"""`EventSink` and `JSONLEventSink`. Build-list item 4; SPEC-v0.2 §4, acceptance test T17.

The store is authoritative and everything else is a convenience export of what it already
holds. That is the whole shape of this item: sinks run *after* the store write succeeded,
they see the `event_id` the store assigned, and one of them failing cannot reach the caller
— because by then the effect has committed at the remote, and an exception on a successful
action is read by an agent as a failure, and retried.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from ctrlrun import (
    ActionDenied,
    Control,
    EffectState,
    Event,
    EventSink,
    InMemoryStateStore,
    JSONLEventSink,
    Policy,
    Receipt,
    SQLiteStateStore,
    context,
    protect,
)
from ctrlrun.receipt import EVENTS_FILENAME, RECEIPTS_FILENAME, ReceiptResult

POLICY = """
schema: ctrlrun.policy/v1
actions:
  stripe.refund:
    rules:
      - when: { amount_lte: 500 }
        decision: allow
      - decision: deny
"""


class Recorder:
    """A sink that remembers what it was handed, in the order it was handed it."""

    def __init__(self) -> None:
        self.events: list[Event] = []
        self.receipts: list[Receipt] = []

    def on_event(self, event: Event) -> None:
        self.events.append(event)

    def on_receipt(self, receipt: Receipt) -> None:
        self.receipts.append(receipt)


class Exploding:
    """A sink that fails every way it can. Nothing it does may reach the caller (§4.2)."""

    def __init__(self, error: type[BaseException] = RuntimeError) -> None:
        self._error = error
        self.calls = 0

    def on_event(self, event: Event) -> None:
        self.calls += 1
        raise self._error("the sink is down")

    def on_receipt(self, receipt: Receipt) -> None:
        self.calls += 1
        raise self._error("the sink is down")


@pytest.fixture
def store():
    store = InMemoryStateStore()
    yield store
    store.close()


def _control(store, *sinks: EventSink) -> Control:
    return Control(Policy.from_yaml(POLICY), store, sinks=sinks)


def _refund(control: Control):
    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def refund(payment_id: str, amount: int) -> str:
        return f"re_{payment_id}"

    return refund


# --- T17: a failing sink cannot affect an action ---------------------------------------


def test_T17_a_failing_sink_does_not_stop_the_action_committing(store, caplog):
    broken = Exploding()
    control = _control(store, broken)
    refund = _refund(control)

    with caplog.at_level(logging.WARNING, logger="ctrlrun"), context(agent="refund-agent"):
        assert refund(payment_id="txn_1", amount=200) == "re_txn_1"

    assert store.get_effect("refund:txn_1").state is EffectState.COMMITTED
    assert store.receipts()[-1].result is ReceiptResult.COMMITTED
    assert broken.calls == len(store.events()) + 1  # every event, and the receipt


def test_T17_the_warning_names_the_sink_class_and_the_record(store, caplog):
    control = _control(store, Exploding())
    refund = _refund(control)

    with caplog.at_level(logging.WARNING, logger="ctrlrun"), context(agent="refund-agent"):
        refund(payment_id="txn_1", amount=200)

    warnings = [
        record.getMessage() for record in caplog.records if "Exploding" in record.getMessage()
    ]
    assert warnings
    assert any("ACTION_PROPOSED" in message for message in warnings)
    assert any("ctr_" in message for message in warnings)


def test_T17_a_working_sink_registered_after_a_failing_one_still_sees_everything(store):
    broken, working = Exploding(), Recorder()
    control = _control(store, broken, working)
    refund = _refund(control)

    with context(agent="refund-agent"):
        refund(payment_id="txn_1", amount=200)

    assert [event.type for event in working.events] == [event.type for event in store.events()]
    assert [receipt.receipt_id for receipt in working.receipts] == [
        receipt.receipt_id for receipt in store.receipts()
    ]


def test_T17_a_failing_sink_does_not_change_the_exception_the_caller_sees(store):
    """§4.2 — not a decision, not an effect state, not a receipt, and not this either."""
    control = _control(store, Exploding())

    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def refund(payment_id: str, amount: int) -> str:
        raise AssertionError("a denied action never executes")

    with context(agent="refund-agent"), pytest.raises(ActionDenied):
        refund(payment_id="txn_1", amount=999999)

    assert store.receipts()[-1].result is ReceiptResult.DENIED
    assert store.get_effect("refund:txn_1") is None


def test_T17_a_base_exception_from_a_sink_propagates(store):
    """Swallowing a KeyboardInterrupt while exporting telemetry is worse than losing it."""
    control = _control(store, Exploding(KeyboardInterrupt))
    refund = _refund(control)

    with context(agent="refund-agent"), pytest.raises(KeyboardInterrupt):
        refund(payment_id="txn_1", amount=200)


# --- §4.1: when sinks run, in what order, and with what -------------------------------


def test_sinks_run_in_registration_order(store):
    order: list[str] = []

    class Named(Recorder):
        def __init__(self, label: str) -> None:
            super().__init__()
            self.label = label

        def on_event(self, event: Event) -> None:
            order.append(self.label)

    control = _control(store, Named("first"), Named("second"))
    refund = _refund(control)

    with context(agent="refund-agent"):
        refund(payment_id="txn_1", amount=200)

    assert order[:2] == ["first", "second"]
    assert order == ["first", "second"] * (len(order) // 2)


def test_a_sink_sees_the_event_id_the_store_assigned(state_store):
    """§4.1 — the id is the store's, so an export can be joined back to the record.

    Parametrized over both stores: the id comes from `lastrowid` in one and from a list
    length in the other, and a sink cannot tell which store it is behind.
    """
    recorder = Recorder()
    control = _control(state_store, recorder)
    refund = _refund(control)

    with context(agent="refund-agent"):
        refund(payment_id="txn_1", amount=200)

    assert [event.event_id for event in recorder.events] == [
        event.event_id for event in state_store.events()
    ]
    assert [event.event_id for event in recorder.events] == [1, 2, 3, 4, 5]


def test_a_sink_runs_only_after_the_store_accepted_the_record(store):
    """A sink that reads the store must find the record it was just handed (§4.1)."""
    seen: list[bool] = []

    class ReadsBack:
        def on_event(self, event: Event) -> None:
            seen.append(any(held.event_id == event.event_id for held in store.events()))

        def on_receipt(self, receipt: Receipt) -> None:
            seen.append(receipt.receipt_id in {held.receipt_id for held in store.receipts()})

    control = _control(store, ReadsBack())
    refund = _refund(control)

    with context(agent="refund-agent"):
        refund(payment_id="txn_1", amount=200)

    assert seen and all(seen)


def test_no_sinks_is_the_default(store):
    """A Control built without sinks writes no files and calls nothing (§4.3)."""
    control = Control(Policy.from_yaml(POLICY), store)
    refund = _refund(control)

    with context(agent="refund-agent"):
        refund(payment_id="txn_1", amount=200)

    assert store.receipts()[-1].result is ReceiptResult.COMMITTED


# --- §4.3: JSONLEventSink, and the store that no longer writes files -------------------


def test_the_store_no_longer_writes_jsonl(tmp_path):
    """§11 — `SQLiteStateStore.journal` is removed; `Control` owns the files now."""
    store = SQLiteStateStore(tmp_path / ".ctrlrun" / "state.db")
    try:
        assert not hasattr(store, "journal")
        control = Control(Policy.from_yaml(POLICY), store)
        refund = _refund(control)
        with context(agent="refund-agent"):
            refund(payment_id="txn_1", amount=200)

        assert not (tmp_path / ".ctrlrun" / RECEIPTS_FILENAME).exists()
        assert not (tmp_path / ".ctrlrun" / EVENTS_FILENAME).exists()
    finally:
        store.close()


def test_the_jsonl_sink_writes_the_two_files_of_v0_1_section_6(store, tmp_path):
    sink = JSONLEventSink(tmp_path)
    control = _control(store, sink)
    refund = _refund(control)

    with context(agent="refund-agent"):
        refund(payment_id="txn_1", amount=200)

    assert sink.receipts_path == tmp_path / RECEIPTS_FILENAME
    assert sink.events_path == tmp_path / EVENTS_FILENAME
    receipts = sink.receipts_path.read_text(encoding="utf-8").strip().splitlines()
    events = sink.events_path.read_text(encoding="utf-8").strip().splitlines()
    assert json.loads(receipts[-1])["result"] == "committed"
    assert [json.loads(line)["event_id"] for line in events] == [1, 2, 3, 4, 5]


def test_the_jsonl_sink_round_trips_the_receipt_the_store_holds(store, tmp_path):
    """The file form is lossless to the millisecond `iso_timestamp` records (v0.1 §6.1), so
    the clock is pinned rather than the assertion loosened."""
    from datetime import UTC, datetime

    pinned = datetime(2026, 9, 3, 10, 12, 1, tzinfo=UTC)
    sink = JSONLEventSink(tmp_path)
    control = Control(Policy.from_yaml(POLICY), store, sinks=[sink], clock=lambda: pinned)
    refund = _refund(control)

    with context(agent="refund-agent"):
        refund(payment_id="txn_1", amount=200)

    (line,) = sink.receipts_path.read_text(encoding="utf-8").strip().splitlines()
    assert Receipt.from_json(line) == store.receipts()[0]


def test_control_from_file_installs_the_jsonl_sink_where_v0_1_put_it(tmp_path, monkeypatch):
    """§4.3 — existing evidence directories are unchanged by this refactor."""
    (tmp_path / "ctrlrun.yaml").write_text(POLICY, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CTRLRUN_CONFIG", raising=False)
    monkeypatch.delenv("CTRLRUN_STATE", raising=False)

    control = Control.from_file()
    try:
        refund = _refund(control)
        with context(agent="refund-agent"):
            refund(payment_id="txn_1", amount=200)
    finally:
        control.store.close()

    assert (tmp_path / ".ctrlrun" / RECEIPTS_FILENAME).is_file()
    assert (tmp_path / ".ctrlrun" / EVENTS_FILENAME).is_file()


def test_an_unwritable_jsonl_sink_never_fails_an_action(store, tmp_path, caplog):
    """v0.1 §6's rule, now enforced by §4.2's general one rather than by the store."""
    sink = JSONLEventSink(tmp_path / "nowhere")

    def refuse(path: Path, line: str) -> None:
        raise OSError("read-only file system")

    sink._append = refuse  # type: ignore[method-assign]
    control = _control(store, sink)
    refund = _refund(control)

    with caplog.at_level(logging.WARNING, logger="ctrlrun"), context(agent="refund-agent"):
        assert refund(payment_id="txn_1", amount=200) == "re_txn_1"

    assert store.get_effect("refund:txn_1").state is EffectState.COMMITTED
    assert [record for record in caplog.records if "JSONLEventSink" in record.getMessage()]


def test_a_second_sink_on_the_same_directory_appends_rather_than_truncates(store, tmp_path):
    first, second = JSONLEventSink(tmp_path), JSONLEventSink(tmp_path)
    refund_one = _refund(_control(store, first))
    with context(agent="refund-agent"):
        refund_one(payment_id="txn_1", amount=200)

    refund_two = _refund(_control(store, second))
    with context(agent="refund-agent"):
        refund_two(payment_id="txn_2", amount=200)

    assert len(second.receipts_path.read_text(encoding="utf-8").strip().splitlines()) == 2


def test_the_event_sink_protocol_is_structural(store):
    """`EventSink` is a Protocol: anything with the two methods is one (§4.1)."""

    class Anything:
        def __init__(self) -> None:
            self.seen = 0

        def on_event(self, event: Event) -> None:
            self.seen += 1

        def on_receipt(self, receipt: Receipt) -> None:
            self.seen += 1

    sink: EventSink = Anything()
    control = _control(store, sink)
    refund = _refund(control)

    with context(agent="refund-agent"):
        refund(payment_id="txn_1", amount=200)

    assert isinstance(sink, Anything)
    assert sink.seen == len(store.events()) + 1


def test_the_store_returns_the_event_it_stored(state_store):
    """§4.1 needs the assigned id, so `append_event` hands the stored event back.

    Against both stores, because this is the half of the contract `Control` depends on and
    a double that returned the caller's own event would hide every id bug behind it.
    """
    from datetime import UTC, datetime

    from ctrlrun.receipt import EventType

    first: Any = state_store.append_event(
        Event(type=EventType.ACTION_PROPOSED, action_id="act_1", ts=datetime.now(UTC))
    )
    second: Any = state_store.append_event(
        Event(type=EventType.ACTION_PROPOSED, action_id="act_2", ts=datetime.now(UTC))
    )

    assert (first.event_id, second.event_id) == (1, 2)
    assert [event.event_id for event in state_store.events()] == [1, 2]
