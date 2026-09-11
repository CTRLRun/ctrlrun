"""Precondition fingerprints. Build-list item 5; SPEC-v0.7 §6, §7, §8.5 T253-T269.

An approval binds to an `action_hash` and an expiry. It does not bind to the state of the world
it was granted against. A human approves *delete customer C123* when the balance is zero and the
account inactive; thirty minutes later the balance is $50,000 and the account is active. The
action has not changed. The world has.

**The recheck narrows the window between a human's decision and the action's execution; it
does not close it.** It is a network call, so it cannot run inside the atomic reservation write,
and a change that lands after the comparison and before the reservation is not refused. T261b
opens exactly that window and asserts the action is *not* refused, because that is what the
kernel does, and a test that said otherwise would make the documentation false.

Every refusal below asserts its `reason`. A precondition mismatch and an ordinary
`ApprovalMismatch` share a type, and a test that asserted only the type could not tell which
guard fired: the first of the four shapes of a false green in the milestone's plan.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import os
import re
import sqlite3
import subprocess
import textwrap
import uuid
import venv
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import test_migrations as releases
from ctrlrun import (
    Action,
    ActionDenied,
    ApprovalMismatch,
    ApprovalRequired,
    Control,
    InMemoryStateStore,
    InvalidArgument,
    JSONLEventSink,
    NotExecuted,
    Policy,
    Principal,
    SQLiteStateStore,
    Suspended,
    protect,
    with_approval,
)
from ctrlrun.action import canonical_bytes
from ctrlrun.approval import ApprovalStatus
from ctrlrun.receipt import RECEIPT_SCHEMA, EventType, ReceiptResult

POLICY = """
schema: ctrlrun.policy/v1
actions:
  customer.delete:
    decision: approve
  customer.read:
    decision: allow
  customer.purge:
    decision: deny
"""

OBSERVE_POLICY = POLICY.replace("ctrlrun.policy/v1", "ctrlrun.policy/v3") + "mode: observe\n"

KEY = "delete:C123"

#: What the human looked at: a zero balance on an inactive account.
AT_REQUEST = {"balance": 0, "active": False}
#: What the world became while the human deliberated.
MOVED = {"balance": 5_000_000, "active": True}

CHANGED = "precondition_changed"
MISSING = "precondition_missing"
UNAVAILABLE = "precondition_unavailable"


def fingerprint(state: Any) -> str:
    """SPEC-v0.7 §6.2's fingerprint, computed here independently of the library."""
    document = {"schema": "ctrlrun.precondition/v1", "state": state}
    return "sha256:" + hashlib.sha256(canonical_bytes(document)).hexdigest()


class World:
    """A precondition provider standing in for the resource an operator reads.

    It counts its calls, because several rows of §6 and §7 are statements that it is *not*
    called, and an assertion that nothing happened needs a counter that would have moved.
    """

    def __init__(self, state: Any = None) -> None:
        self.state: Any = dict(AT_REQUEST) if state is None else state
        self.calls = 0
        self.fail: BaseException | None = None
        self.seen: list[Action] = []

    def __call__(self, action: Action) -> Any:
        self.calls += 1
        self.seen.append(action)
        if self.fail is not None:
            raise self.fail
        return dict(self.state) if isinstance(self.state, dict) else self.state


class Executor:
    def __init__(self, behaviour: Any = None) -> None:
        self.calls = 0
        self._behaviour = behaviour

    def __call__(self) -> Any:
        self.calls += 1
        if self._behaviour is not None:
            return self._behaviour()
        return "deleted"


def an_action(control: Control, customer_id: str = "C123", name: str = "customer.delete"):
    return Action(
        name=name,
        arguments={"customer_id": customer_id},
        principal=Principal(agent="ops-agent", user="ada"),
        environment=control.environment,
    )


@pytest.fixture
def control(state_store, fake_clock):
    return Control(Policy.from_yaml(POLICY), state_store, clock=fake_clock)


def requested(control: Control, action: Action, world: World | None, key: str | None = KEY) -> str:
    """The request pass: `ApprovalRequired`, and the id of the request it created."""
    with pytest.raises(ApprovalRequired) as pending:
        control.execute(action, Executor(), key, preconditions=world)
    return pending.value.request_id


def granted(control: Control, action: Action, world: World | None, key: str | None = KEY) -> str:
    request_id = requested(control, action, world, key)
    control.store.grant_approval(request_id, "human:alice")
    return request_id


def present(
    control: Control,
    action: Action,
    request_id: str,
    world: World | None,
    executor: Executor | None = None,
    key: str | None = KEY,
    **kwargs: Any,
):
    with with_approval(request_id):
        return control.execute(action, executor or Executor(), key, preconditions=world, **kwargs)


def refused(
    control: Control,
    action: Action,
    request_id: str,
    world: World | None,
    executor: Executor | None = None,
    key: str | None = KEY,
    **kwargs: Any,
) -> ApprovalMismatch:
    with pytest.raises(ApprovalMismatch) as raised:
        present(control, action, request_id, world, executor, key, **kwargs)
    return raised.value


def invalidated(store, request_id: str):
    return [
        event
        for event in store.events()
        if event.type is EventType.APPROVAL_INVALIDATED and event.approval_id == request_id
    ]


def last_receipt(store, action: Action):
    found = [receipt for receipt in store.receipts() if receipt.action_id == action.action_id]
    assert found, f"no receipt for {action.action_id}"
    return found[-1]


# --- §6.2: the keyword, and what it refuses at the door --------------------------------------


def test_a_preconditions_keyword_that_is_not_callable_is_invalid_on_execute(control):
    """§6.2: *"A `preconditions=` that is not callable is `InvalidArgument`."*"""
    action = an_action(control)
    with pytest.raises(InvalidArgument) as refused_:
        control.execute(action, Executor(), KEY, preconditions={"balance": 0})  # type: ignore[arg-type]
    assert "preconditions" in str(refused_.value)
    assert control.store.events() == (), "a wiring bug wrote evidence before it was refused"


def test_a_preconditions_keyword_that_is_not_callable_is_refused_at_decoration_time():
    """§6.2: at decoration time for `@protect`, so the mistake fails on import."""
    with pytest.raises(InvalidArgument) as refused_:
        protect("customer.delete", preconditions="balance")  # type: ignore[arg-type]
    assert "preconditions" in str(refused_.value)


def test_there_is_no_flag_that_skips_the_recheck():
    """SPEC-v0.7 §1.1: no `skip_preconditions`. A flag that relaxes a check is the thing the
    milestone's plan names by name, so its absence is asserted on every signature it could
    have landed on, not assumed."""
    for callable_ in (Control.__init__, Control.execute, protect):
        names = set(inspect.signature(callable_).parameters)
        assert not {name for name in names if "skip" in name or "optimistic" in name}, names
    assert "preconditions" in inspect.signature(Control.execute).parameters
    assert "preconditions" in inspect.signature(protect).parameters
    assert inspect.signature(Control.execute).parameters["preconditions"].default is None


# --- T253 / T254: a moved fingerprint refuses by its own reason, and leaves the grant ---------


def test_T253_a_moved_fingerprint_refuses_by_its_own_reason(control, state_store):
    world = World()
    action = an_action(control)
    request_id = granted(control, action, world)
    world.state = dict(MOVED)
    executor = Executor()

    mismatch = refused(control, action, request_id, world, executor)

    assert mismatch.reason == CHANGED, (
        f"the refusal carried reason {mismatch.reason!r}; a precondition mismatch and an "
        "ordinary one share a type, so only the reason says which guard fired"
    )
    assert executor.calls == 0
    assert state_store.get_effect(KEY) is None, "a refused recheck left an effect record"
    assert not any(event.type is EventType.EFFECT_RESERVED for event in state_store.events())
    events = invalidated(state_store, request_id)
    assert len(events) == 1
    data = events[0].data
    assert data["reason"] == CHANGED
    assert data["precondition_at_request"] == fingerprint(AT_REQUEST)
    assert data["precondition_at_recheck"] == fingerprint(MOVED)
    receipt = last_receipt(state_store, action)
    assert receipt.result is ReceiptResult.BLOCKED
    assert receipt.precondition_at_request == fingerprint(AT_REQUEST)
    assert receipt.precondition_at_recheck == fingerprint(MOVED)


def test_T253_the_negative_precondition_without_a_provider_the_moved_world_would_run(control):
    """The `else` behind T253: the same presentation with nobody asking the world commits, so
    T253's refusal is the recheck's and not something the kernel refuses anyway."""
    action = an_action(control)
    request_id = granted(control, action, None)
    executor = Executor()

    receipt = present(control, action, request_id, None, executor)

    assert receipt.result is ReceiptResult.COMMITTED and executor.calls == 1


def test_T254_the_approval_is_left_granted_and_opens_the_world_the_human_saw(control, state_store):
    world = World()
    action = an_action(control)
    request_id = granted(control, action, world)
    world.state = dict(MOVED)
    refused(control, action, request_id, world)

    record = state_store.get_approval(request_id)
    assert record is not None and record.status is ApprovalStatus.GRANTED, (
        "a refusal by a reported fact spent the human's answer"
    )

    world.state = dict(AT_REQUEST)
    executor = Executor()
    receipt = present(control, action, request_id, world, executor)

    assert receipt.result is ReceiptResult.COMMITTED and executor.calls == 1
    assert state_store.get_approval(request_id).status is ApprovalStatus.CONSUMED
    assert receipt.precondition_at_request == receipt.precondition_at_recheck
    assert receipt.precondition_at_recheck == fingerprint(AT_REQUEST)


def test_a_committed_receipt_records_that_the_world_was_checked(control, state_store):
    """§6.11: on a committed action the two fields are equal, and the receipt is `v4`."""
    world = World()
    action = an_action(control)
    request_id = granted(control, action, world)

    receipt = present(control, action, request_id, world)

    assert receipt.schema == RECEIPT_SCHEMA == "ctrlrun.receipt/v4"
    assert receipt.precondition_at_request == fingerprint(AT_REQUEST)
    assert receipt.precondition_at_recheck == fingerprint(AT_REQUEST)
    document = receipt.to_dict()
    assert document["schema"] == "ctrlrun.receipt/v4"
    assert document["precondition_at_request"] == fingerprint(AT_REQUEST)
    assert document["precondition_at_recheck"] == fingerprint(AT_REQUEST)
    stored = state_store.get_approval(request_id)
    assert stored.request.precondition_fingerprint == fingerprint(AT_REQUEST)


def test_a_receipt_with_no_precondition_carries_two_nulls(control, state_store):
    """Absent means absent: an action nobody asked the world about says so, with `null`."""
    action = an_action(control)
    request_id = granted(control, action, None)

    receipt = present(control, action, request_id, None)

    assert receipt.precondition_at_request is None
    assert receipt.precondition_at_recheck is None
    assert state_store.get_approval(request_id).request.precondition_fingerprint is None


# --- T255 / T256: a provider that fails refuses the action ------------------------------------


def test_T255_a_provider_that_raises_on_the_presenting_pass_fails_closed(control, state_store):
    world = World()
    action = an_action(control)
    request_id = granted(control, action, world)
    world.fail = ConnectionError("the CRM is down")
    executor = Executor()

    mismatch = refused(control, action, request_id, world, executor)

    assert mismatch.reason == UNAVAILABLE
    assert mismatch.reason != CHANGED, "an outage and a moved world need different remedies"
    assert executor.calls == 0
    assert state_store.get_effect(KEY) is None, "nothing may be reserved"
    assert state_store.get_approval(request_id).status is ApprovalStatus.GRANTED
    data = invalidated(state_store, request_id)[0].data
    assert data["reason"] == UNAVAILABLE
    assert data["error"] == "ConnectionError", "the exception is recorded by its type name only"
    assert data["precondition_at_request"] == fingerprint(AT_REQUEST)
    assert data["precondition_at_recheck"] is None


def test_T255_a_provider_that_raises_on_the_request_pass_creates_no_request(control, state_store):
    world = World()
    world.fail = TimeoutError("the CRM did not answer")
    action = an_action(control)

    with pytest.raises(ActionDenied) as denied:
        control.execute(action, Executor(), KEY, preconditions=world)

    assert denied.value.reason == UNAVAILABLE
    assert state_store.approvals_for(action.action_hash) == (), "a request exists; a human is asked"
    types = [event.type for event in state_store.events()]
    assert EventType.APPROVAL_REQUESTED not in types
    denial = [event for event in state_store.events() if event.type is EventType.ACTION_DENIED]
    assert denial and denial[-1].data["reason"] == UNAVAILABLE
    receipt = last_receipt(state_store, action)
    assert receipt.result is ReceiptResult.DENIED
    assert str(receipt.decision) == "approve", "the denied receipt must keep `decision: approve`"


class _Unencodable:
    """Nothing `canonical_bytes` could ever encode."""


CANONICALIZER_REFUSES = {
    "a float at depth three": {"account": {"ledger": {"balance": 0.5}}},
    "a mapping with an integer key": {"account": {7: "active"}},
    "a string holding a lone surrogate": {"name": "caf\ud800"},
    "an object json cannot encode": {"account": _Unencodable()},
}


@pytest.mark.parametrize("label", sorted(CANONICALIZER_REFUSES))
def test_T256_what_the_canonicalizer_refuses_is_the_canonicalizers_refusal(label):
    """The negative precondition for T256: each of these is refused by `canonical_bytes`
    itself, so the fail-closed below is the canonicalizer's refusal inherited and not a check
    this item added and could get wrong."""
    with pytest.raises(Exception):  # noqa: B017 - which exception is the canonicalizer's business
        canonical_bytes(
            {"schema": "ctrlrun.precondition/v1", "state": CANONICALIZER_REFUSES[label]}
        )


#: A list of pairs: the one non-mapping that `dict()` would happily turn into one, and that the
#: canonicalizer accepts inside the envelope. Only the `Mapping` check refuses it.
PAIRS = [["balance", 0], ["active", False]]


def test_T256_a_list_is_refused_by_the_mapping_check_and_by_nothing_else():
    """The negative precondition for the list case: the canonicalizer accepts it inside the
    envelope and `dict()` would convert it, so a refusal proves the `Mapping` check is live
    rather than subsumed by either."""
    canonical_bytes({"schema": "ctrlrun.precondition/v1", "state": PAIRS})
    assert dict(PAIRS) == {"balance": 0, "active": False}


RETURNS = {**CANONICALIZER_REFUSES, "a list of pairs": PAIRS, "None": None}


@pytest.mark.parametrize("label", sorted(RETURNS))
def test_T256_on_the_presenting_pass_it_is_unavailable(control, state_store, label):
    world = World()
    action = an_action(control)
    request_id = granted(control, action, world)
    world.state = RETURNS[label]
    executor = Executor()

    mismatch = refused(control, action, request_id, world, executor)

    assert mismatch.reason == UNAVAILABLE, label
    assert executor.calls == 0
    assert state_store.get_effect(KEY) is None
    assert state_store.get_approval(request_id).status is ApprovalStatus.GRANTED


@pytest.mark.parametrize("label", sorted(RETURNS))
def test_T256_on_the_request_pass_it_is_denied_with_no_request(control, state_store, label):
    world = World()
    world.state = RETURNS[label]
    action = an_action(control)

    with pytest.raises(ActionDenied) as denied:
        control.execute(action, Executor(), KEY, preconditions=world)

    assert denied.value.reason == UNAVAILABLE, label
    assert state_store.approvals_for(action.action_hash) == ()


# --- T257: a fingerprint on one side only is a refusal, never a skip ---------------------------


def test_T257_an_approval_with_a_fingerprint_presented_without_a_provider(control, state_store):
    world = World()
    action = an_action(control)
    request_id = granted(control, action, world)
    executor = Executor()

    mismatch = refused(control, action, request_id, None, executor)

    assert mismatch.reason == MISSING
    assert executor.calls == 0
    assert state_store.get_effect(KEY) is None
    assert state_store.get_approval(request_id).status is ApprovalStatus.GRANTED
    data = invalidated(state_store, request_id)[0].data
    assert data["precondition_at_request"] == fingerprint(AT_REQUEST)
    assert data["precondition_at_recheck"] is None


def test_T257_a_provider_presenting_an_approval_with_no_fingerprint(control, state_store):
    """The request was created by a call that named no provider; this one names one."""
    action = an_action(control)
    request_id = granted(control, action, None)
    world = World()
    executor = Executor()

    mismatch = refused(control, action, request_id, world, executor)

    assert mismatch.reason == MISSING
    assert executor.calls == 0
    assert state_store.get_approval(request_id).status is ApprovalStatus.GRANTED
    data = invalidated(state_store, request_id)[0].data
    assert data["precondition_at_request"] is None
    assert data["precondition_at_recheck"] == fingerprint(AT_REQUEST), (
        "the two precondition_missing cases are told apart by which field is null"
    )


def _null_the_column_sqlite(store: SQLiteStateStore, request_id: str) -> None:
    connection = sqlite3.connect(store.path)
    try:
        changed = connection.execute(
            "UPDATE approvals SET precondition_fingerprint = NULL WHERE approval_id = ?",
            (request_id,),
        ).rowcount
        connection.commit()
    finally:
        connection.close()
    assert changed == 1


def test_T257_a_store_that_lost_the_column_is_a_refusal_not_a_skip(tmp_path, fake_clock):
    """§6.4: *"a store that drops the column turns the check off"* is what "skip" would mean.
    The request is created with a fingerprint, the column is then set to `NULL` underneath the
    store, and the approval is presented with the provider."""
    store = SQLiteStateStore(tmp_path / "state.db", clock=fake_clock)
    control = Control(Policy.from_yaml(POLICY), store, clock=fake_clock)
    world = World()
    action = an_action(control)
    request_id = granted(control, action, world)
    assert store.get_approval(request_id).request.precondition_fingerprint is not None
    _null_the_column_sqlite(store, request_id)
    assert store.get_approval(request_id).request.precondition_fingerprint is None
    executor = Executor()

    mismatch = refused(control, action, request_id, world, executor)

    assert mismatch.reason == MISSING
    assert executor.calls == 0
    assert store.get_effect(KEY) is None
    store.close()


# --- T258 / T259: ALLOW, DENY and resume never call the provider ------------------------------


def test_T258_allow_never_calls_the_provider(control, state_store):
    world = World()
    action = an_action(control, name="customer.read")

    receipt = control.execute(action, Executor(), "read:C123", preconditions=world)

    assert receipt.result is ReceiptResult.COMMITTED
    assert world.calls == 0
    assert receipt.precondition_at_request is None and receipt.precondition_at_recheck is None


def test_T258_allow_with_a_presented_approval_never_calls_the_provider(control, state_store):
    """A presented approval on an `ALLOW` action is spent by `v0.6 §7.2.2`'s path, without a
    recheck: nothing the world could say would change what runs."""
    world = World()
    approved = an_action(control)
    request_id = granted(control, approved, world)
    before = world.calls
    allowed = an_action(control, name="customer.read")

    receipt = present(control, allowed, request_id, world, key="read:C123")

    assert receipt.result is ReceiptResult.COMMITTED
    assert world.calls == before


def test_T258_deny_never_calls_the_provider(control, state_store):
    world = World()
    action = an_action(control, name="customer.purge")

    with pytest.raises(ActionDenied):
        control.execute(action, Executor(), "purge:C123", preconditions=world)

    assert world.calls == 0


def test_T259_resume_never_calls_the_provider(control, state_store):
    world = World()
    action = an_action(control)
    request_id = granted(control, action, world)

    def suspend() -> Any:
        raise Suspended("continuation-C123")

    with pytest.raises(Suspended):
        present(control, action, request_id, world, Executor(suspend))
    before = world.calls
    assert before == 2, "the request pass and the presenting pass each fetch once"

    receipt = control.resume("continuation-C123", lambda: "deleted")

    assert receipt.result is ReceiptResult.COMMITTED
    assert world.calls == before, "Control.resume called the provider"
    assert receipt.precondition_at_recheck is None, "a resumed leg did not recheck"


# --- T260: raw provider output reaches no evidence --------------------------------------------

SENTINEL = "SENTINEL-balance-50000-PHI-7f3a"


def _every_row(database) -> str:
    connection = sqlite3.connect(database)
    try:
        tables = [
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ]
        text = []
        for table in tables:
            for row in connection.execute(f"SELECT * FROM {table}"):
                text.append(repr(tuple(row)))
    finally:
        connection.close()
    return "\n".join(text)


def test_T260_raw_provider_output_reaches_no_evidence(tmp_path, fake_clock, caplog):
    """§6.10. The provider returns a sentinel; after a committed action, a refused one, an
    unavailable one (whose exception message carries the sentinel, the one field nobody
    thought to check) and a request-pass denial, the sentinel is in no row of any table, no
    JSONL line, no event, no receipt and no captured log record."""
    caplog.set_level(logging.DEBUG, logger="ctrlrun")
    store = SQLiteStateStore(tmp_path / "state.db", clock=fake_clock)
    sink = JSONLEventSink(tmp_path / "jsonl")
    control = Control(Policy.from_yaml(POLICY), store, clock=fake_clock, sinks=[sink])
    world = World({"balance": SENTINEL, "active": False})

    committed = an_action(control, "C1")
    request_id = granted(control, committed, world, "delete:C1")
    present(control, committed, request_id, world, key="delete:C1")

    moved = an_action(control, "C2")
    request_id = granted(control, moved, world, "delete:C2")
    world.state = {"balance": SENTINEL + "-moved", "active": True}
    refused(control, moved, request_id, world, key="delete:C2")

    unavailable = an_action(control, "C3")
    world.state = {"balance": SENTINEL, "active": False}
    request_id = granted(control, unavailable, world, "delete:C3")
    world.fail = RuntimeError(f"could not read balance {SENTINEL}")
    refused(control, unavailable, request_id, world, key="delete:C3")

    denied = an_action(control, "C4")
    with pytest.raises(ActionDenied):
        control.execute(denied, Executor(), "delete:C4", preconditions=world)

    observe = Control(Policy.from_yaml(OBSERVE_POLICY), store, clock=fake_clock, sinks=[sink])
    observed = an_action(observe, "C5")
    world.fail = None
    world.state = {"balance": SENTINEL, "active": False}
    request_id = granted(control, observed, world, "delete:C5")
    world.state = {"balance": SENTINEL + "-observed", "active": True}
    present(observe, observed, request_id, world, key="delete:C5")

    store.close()
    assert world.calls >= 8, "the provider was not exercised on every path"
    assert SENTINEL not in _every_row(tmp_path / "state.db"), "raw state reached a table"
    for path in (sink.receipts_path, sink.events_path):
        assert SENTINEL not in path.read_text(encoding="utf-8"), f"raw state reached {path.name}"
    for record in caplog.records:
        assert SENTINEL not in record.getMessage(), f"raw state reached a log line: {record}"


# --- T261 / T261b: the window, before the compare and after it ---------------------------------


def test_T261_a_change_before_the_compare_is_refused(control, state_store):
    """The world moves after the request and before the presenting pass calls the provider:
    the provider reads the moved state, the comparison sees it, and the action is refused."""
    world = World()
    action = an_action(control)
    request_id = granted(control, action, world)
    world.state = dict(MOVED)  # before the presenting pass has fetched anything

    mismatch = refused(control, action, request_id, world)

    assert mismatch.reason == CHANGED


def test_T261b_the_residual_window_a_change_after_the_compare_and_before_the_reservation_is_not_refused(  # noqa: E501
    control, state_store
):
    """SPEC-v0.7 §6.7, segment 2: **the residual window this item narrows and does not close.**

    The comparison has passed and `consume_approval_and_reserve` has not yet run. The test
    changes the resource inside that interval by wrapping the store call so the change lands
    ahead of the real call; nothing is added to the library for the test's sake. **The action
    is not refused, and it commits against a world the human never saw.** That is the kernel's
    behaviour, the reason every sentence about the recheck says *narrows*, and the reason a
    resource that can refuse a stale write itself (a conditional request, a compare-and-swap)
    is the executor's business and not something this recheck substitutes for.
    """
    world = World()
    action = an_action(control)
    request_id = granted(control, action, world)
    order: list[str] = []
    real = state_store.consume_approval_and_reserve

    def the_world_moves_first(*args: Any, **kwargs: Any):
        order.append(f"reserve after {world.calls} fetches")
        world.state = dict(MOVED)
        return real(*args, **kwargs)

    state_store.consume_approval_and_reserve = the_world_moves_first  # type: ignore[method-assign]
    executor = Executor()
    fetched_before = world.calls

    receipt = present(control, action, request_id, world, executor)

    assert order == [f"reserve after {fetched_before + 1} fetches"], (
        "the change did not land between the compare and the reservation, so this test did "
        "not open the window it is named for"
    )
    assert world.state == MOVED, "the world did not move inside the window"
    assert receipt.result is ReceiptResult.COMMITTED, (
        "the residual window was refused; the recheck cannot run inside the reservation, so "
        "either this test is not opening the window or the documentation is now wrong"
    )
    assert executor.calls == 1
    assert receipt.precondition_at_recheck == fingerprint(AT_REQUEST), (
        "the receipt records what the recheck compared, which is the world before it moved"
    )
    assert receipt.precondition_at_recheck != fingerprint(MOVED)


# --- T262: an approval that would be refused anyway never reaches the provider ------------------


def test_T262_a_consumed_approval_never_reaches_the_provider(control, state_store):
    world = World()
    action = an_action(control)
    request_id = granted(control, action, world)
    present(control, action, request_id, world)
    world.state = dict(MOVED)
    before = world.calls

    mismatch = refused(control, action, request_id, world)

    assert mismatch.reason == "consumed"
    assert world.calls == before


def test_T262_an_expired_approval_never_reaches_the_provider(control, state_store, fake_clock):
    world = World()
    action = an_action(control)
    request_id = granted(control, action, world)
    fake_clock.advance(timedelta(hours=1))
    world.state = dict(MOVED)
    before = world.calls

    mismatch = refused(control, action, request_id, world)

    assert mismatch.reason == "expired"
    assert world.calls == before
    assert state_store.get_approval(request_id).status is ApprovalStatus.EXPIRED, (
        "0.6.1 records the lapse, and so must this path"
    )


def test_T262_a_hash_mismatched_approval_never_reaches_the_provider(control, state_store):
    world = World()
    action = an_action(control)
    request_id = granted(control, action, world)
    mutated = an_action(control, "C999")
    world.state = dict(MOVED)
    before = world.calls

    mismatch = refused(control, mutated, request_id, world, key="delete:C999")

    assert mismatch.reason == "mismatch"
    assert world.calls == before


def test_T262_a_denied_approval_never_reaches_the_provider(control, state_store):
    world = World()
    action = an_action(control)
    request_id = requested(control, action, world)
    state_store.deny_approval(request_id, "human:alice")
    world.state = dict(MOVED)
    before = world.calls

    with pytest.raises(ActionDenied) as denied:
        present(control, action, request_id, world)

    assert denied.value.reason == "approval_denied"
    assert world.calls == before


def test_T262_a_pending_approval_never_reaches_the_provider(control, state_store):
    world = World()
    action = an_action(control)
    request_id = requested(control, action, world)
    before = world.calls

    mismatch = refused(control, action, request_id, world)

    assert mismatch.reason == "pending"
    assert world.calls == before


def test_T262_a_grant_that_lands_after_the_read_is_not_consumed_without_a_recheck(
    control, state_store
):
    """The window §6.6's read opens, closed in the fail-closed direction.

    `Control` reads the record, finds it `pending`, and does not call the provider. If a human
    grants it between that read and the store call, a store call made anyway would consume a
    fingerprinted approval that no recheck ever looked at, which is a skip. So a refusal the
    read found is raised from the read, and the store is not asked to consume at all.
    """
    world = World()
    action = an_action(control)
    request_id = requested(control, action, world)
    real = state_store.get_approval
    answered: list[str] = []

    def a_human_answers_right_after_the_read(approval_id: str):
        record = real(approval_id)
        if not answered and record is not None and record.status is ApprovalStatus.PENDING:
            answered.append(approval_id)
            state_store.grant_approval(approval_id, "human:alice")
        return record

    state_store.get_approval = a_human_answers_right_after_the_read  # type: ignore[method-assign]
    executor = Executor()

    mismatch = refused(control, action, request_id, world, executor)

    assert answered == [request_id], "the grant did not land inside the window"
    assert mismatch.reason == "pending"
    assert executor.calls == 0
    assert real(request_id).status is ApprovalStatus.GRANTED, "the grant was spent unchecked"
    assert state_store.get_effect(KEY) is None


# --- T263: the recheck runs before each take ----------------------------------------------------


def _ambiguous_record(store, key: str) -> None:
    store.reserve_effect(key, "act_earlier", timedelta(minutes=5))
    store.begin_execution(key, "act_earlier")
    store.mark_ambiguous(key, "act_earlier", "the response was lost")


def test_T263_a_reconciled_record_is_rechecked_before_the_second_take(control, state_store):
    """`_secure` may take twice: once more after a `reconcile` hook moves an `AMBIGUOUS`
    record. The hook is a network call, and a world that moves during it is refused on the
    second take."""
    world = World()
    action = an_action(control)
    request_id = granted(control, action, world)
    _ambiguous_record(state_store, KEY)
    before = world.calls

    def reconcile(effect_key: str) -> str:
        world.state = dict(MOVED)
        return "not_executed"

    executor = Executor()
    mismatch = refused(control, action, request_id, world, executor, reconcile=reconcile)

    assert world.calls - before == 2, "the provider was not called before each take"
    assert mismatch.reason == CHANGED
    assert executor.calls == 0
    assert state_store.get_approval(request_id).status is ApprovalStatus.GRANTED


def test_T263_the_positive_control_a_world_that_holds_still_commits_after_two_fetches(
    control, state_store
):
    world = World()
    action = an_action(control)
    request_id = granted(control, action, world)
    _ambiguous_record(state_store, KEY)
    before = world.calls
    executor = Executor()

    receipt = present(
        control, action, request_id, world, executor, reconcile=lambda _: "not_executed"
    )

    assert receipt.result is ReceiptResult.COMMITTED and executor.calls == 1
    assert world.calls - before == 2


# --- §6.5: what propagates, and what the provider is handed -------------------------------------


def test_a_base_exception_from_the_provider_propagates_with_nothing_reserved(control, state_store):
    world = World()
    action = an_action(control)
    request_id = granted(control, action, world)
    world.fail = KeyboardInterrupt()
    receipts = len(state_store.receipts())

    with pytest.raises(KeyboardInterrupt):
        present(control, action, request_id, world)

    assert state_store.get_effect(KEY) is None
    assert len(state_store.receipts()) == receipts, "a receipt was written for an interrupt"
    assert state_store.get_approval(request_id).status is ApprovalStatus.GRANTED


def test_the_provider_is_handed_the_whole_action(control):
    """§6.9: the hook is general, and it is given the principal and the resource as well."""
    world = World()
    action = an_action(control)
    requested(control, action, world)

    assert world.seen and world.seen[0].action_hash == action.action_hash
    assert world.seen[0].principal == action.principal


def test_protect_wait_true_captures_and_rechecks(tmp_path, fake_clock):
    """The `@protect` row of §7: the decorator names the provider, the request pass captures
    it, `wait=True` blocks on a scripted human, and the presenting pass rechecks."""
    from ctrlrun import ScriptedApprovalProvider, context

    store = SQLiteStateStore(tmp_path / "state.db", clock=fake_clock)
    human = ScriptedApprovalProvider(store, ["grant"], clock=fake_clock)
    control = Control(Policy.from_yaml(POLICY), store, human, clock=fake_clock)
    world = World()
    ran: list[str] = []

    @protect(
        "customer.delete",
        effect="delete:{customer_id}",
        wait=True,
        control=control,
        preconditions=world,
    )
    def delete(customer_id: str) -> str:
        ran.append(customer_id)
        return "deleted"

    with context("ops-agent", "ada"):
        assert delete("C123") == "deleted"

    assert ran == ["C123"]
    assert world.calls == 2
    receipt = store.receipts()[-1]
    assert receipt.precondition_at_request == receipt.precondition_at_recheck
    assert receipt.precondition_at_recheck == fingerprint(AT_REQUEST)
    store.close()


def test_protect_wait_true_refuses_a_world_that_moved_while_the_human_answered(
    tmp_path, fake_clock
):
    from ctrlrun import ScriptedApprovalProvider, context

    store = SQLiteStateStore(tmp_path / "state.db", clock=fake_clock)
    world = World()

    class MovesTheWorld(ScriptedApprovalProvider):
        def wait(self, request_id, timeout=None):
            answer = super().wait(request_id, timeout)
            world.state = dict(MOVED)
            return answer

    human = MovesTheWorld(store, ["grant"], clock=fake_clock)
    control = Control(Policy.from_yaml(POLICY), store, human, clock=fake_clock)
    ran: list[str] = []

    @protect(
        "customer.delete",
        effect="delete:{customer_id}",
        wait=True,
        control=control,
        preconditions=world,
    )
    def delete(customer_id: str) -> str:
        ran.append(customer_id)
        return "deleted"

    with context("ops-agent", "ada"), pytest.raises(ApprovalMismatch) as raised:
        delete("C123")

    assert raised.value.reason == CHANGED
    assert ran == []
    store.close()


# --- §6.8: observe mode rechecks and records, and spends nothing --------------------------------


def test_observe_mode_rechecks_records_and_runs_spending_no_grant(tmp_path, fake_clock):
    store = SQLiteStateStore(tmp_path / "state.db", clock=fake_clock)
    enforce = Control(Policy.from_yaml(POLICY), store, clock=fake_clock)
    observe = Control(Policy.from_yaml(OBSERVE_POLICY), store, clock=fake_clock)
    world = World()
    action = an_action(enforce)
    request_id = granted(enforce, action, world)
    world.state = dict(MOVED)
    before = world.calls
    executor = Executor()

    receipt = present(observe, action, request_id, world, executor)

    assert world.calls == before + 1, "observe mode did not call the provider where enforce would"
    assert executor.calls == 1, "observe mode refused something"
    assert receipt.result is ReceiptResult.OBSERVED
    assert receipt.would_have is not None
    assert receipt.would_have.blocked_reason == "approval_mismatch"
    data = invalidated(store, request_id)[0].data
    assert data["reason"] == CHANGED
    assert data["precondition_at_recheck"] == fingerprint(MOVED)
    assert store.get_approval(request_id).status is ApprovalStatus.GRANTED
    store.close()


def test_observe_mode_request_pass_never_fetches(tmp_path, fake_clock):
    """`v0.3 §6.2`: observe mode creates no request, so its request pass never fetches."""
    store = SQLiteStateStore(tmp_path / "state.db", clock=fake_clock)
    observe = Control(Policy.from_yaml(OBSERVE_POLICY), store, clock=fake_clock)
    world = World()
    action = an_action(observe)

    receipt = observe.execute(action, Executor(), KEY, preconditions=world)

    assert receipt.result is ReceiptResult.OBSERVED
    assert world.calls == 0
    assert store.approvals_for(action.action_hash) == ()
    store.close()


def test_in_memory_and_sqlite_stores_both_carry_the_fingerprint(fake_clock, tmp_path):
    for store in (
        InMemoryStateStore(clock=fake_clock),
        SQLiteStateStore(tmp_path / "state.db", clock=fake_clock),
    ):
        control = Control(Policy.from_yaml(POLICY), store, clock=fake_clock)
        world = World()
        request_id = requested(control, an_action(control), world)
        record = store.get_approval(request_id)
        assert record.request.precondition_fingerprint == fingerprint(AT_REQUEST), store
        approvals = store.approvals_for(record.action_hash)
        assert approvals[0].request.precondition_fingerprint == fingerprint(AT_REQUEST)
        store.close()


def test_the_unavailable_refusal_is_not_an_executor_outcome(control, state_store):
    """`v0.7 §1.1`: a precondition check never writes `FAILED`. The refusal is before the
    reservation, so there is no record to write anything to, and `NotExecuted` is never
    raised by it."""
    world = World()
    action = an_action(control)
    request_id = granted(control, action, world)
    world.fail = NotExecuted("a provider borrowing the executor's vocabulary")

    mismatch = refused(control, action, request_id, world)

    assert mismatch.reason == UNAVAILABLE
    assert state_store.get_effect(KEY) is None
    assert not any(event.type is EventType.EXECUTION_FAILED for event in state_store.events())


def test_the_approval_invalidated_data_carries_hashes_only(control, state_store):
    world = World()
    action = an_action(control)
    request_id = granted(control, action, world)
    world.state = dict(MOVED)
    refused(control, action, request_id, world)

    data = invalidated(state_store, request_id)[0].data
    for name in ("precondition_at_request", "precondition_at_recheck"):
        assert str(data[name]).startswith("sha256:") and len(data[name]) == 71
    assert "balance" not in json.dumps(dict(data))


# --- T257 through the gateway and the ACS hook: they name no provider, and refuse ---------------

GATEWAY_POLICY = """
schema: ctrlrun.policy/v2
actions:
  mcp.acme.delete_customer:
    effect: "delete:{customer_id}"
    decision: approve
  acs.crm.delete_customer:
    effect: "delete:{customer_id}"
    decision: approve
"""


def _gateway(store, fake_clock):
    pytest.importorskip("httpx", reason="the gateway extra is not installed")
    from ctrlrun.gateway.server import Gateway, GatewayConfig

    forwarded: list[bytes] = []

    def forwarder(body, headers, *, fresh):
        forwarded.append(body)
        raise AssertionError("a refused call reached the upstream")

    control = Control(Policy.from_yaml(GATEWAY_POLICY), store, clock=fake_clock)
    config = GatewayConfig(
        upstream="http://127.0.0.1:9/mcp", alias="acme", principal_header="X-Agent", port=0
    )
    return Gateway(config, control, forwarder), control, forwarded


def _tools_call() -> tuple[bytes, dict[str, str]]:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "delete_customer", "arguments": {"customer_id": "C123"}},
    }
    headers = {
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": "tools/call",
        "Mcp-Name": "delete_customer",
        "X-Agent": "ops-agent",
    }
    return json.dumps(body).encode(), headers


def _fingerprinted_approval_for(control: Control, action: Action, world: World) -> str:
    """Request and grant an approval for exactly `action`'s hash, through the path that names
    a provider, which is the only one that can create a fingerprint."""
    request_id = requested(control, action, world, "delete:C123")
    control.store.grant_approval(request_id, "human:alice")
    return request_id


def test_T257_the_gateway_refuses_a_fingerprinted_approval_and_stays_refused_until_it_expires(
    tmp_path, fake_clock
):
    """§6.4 with its bound. The gateway presents the newest granted approval for the action's
    hash and names no provider; an approval requested with a fingerprint was granted against a
    world this path cannot recheck. It is refused `-41006` with the reason, the second and
    third identical calls are refused the same way with no fresh request, and once the
    approval expires the next call creates one."""
    store = SQLiteStateStore(tmp_path / "state.db", clock=fake_clock)
    gateway, control, forwarded = _gateway(store, fake_clock)
    body, headers = _tools_call()

    first = json.loads(gateway.handle(body, headers).body)
    assert first["error"]["code"] == -41002, first
    unfingerprinted = first["error"]["data"]["request_id"]
    action = store.get_approval(unfingerprinted).request.action
    fingerprinted = _fingerprinted_approval_for(control, action, World())
    assert store.find_granted_approval(action.action_hash).approval_id == fingerprinted

    for attempt in range(3):
        response = gateway.handle(body, headers)
        document = json.loads(response.body)
        assert response.status == 409, (attempt, document)
        assert document["error"]["code"] == -41006, (attempt, document)
        assert document["error"]["data"]["reason"] == MISSING, (attempt, document)
        assert len(store.approvals_for(action.action_hash)) == 2, "a fresh request was created"
        assert store.get_approval(fingerprinted).status is ApprovalStatus.GRANTED
    assert forwarded == [] and store.get_effect("delete:C123") is None

    fake_clock.advance(timedelta(minutes=16))
    after = json.loads(gateway.handle(body, headers).body)

    assert after["error"]["code"] == -41002, after
    assert len(store.approvals_for(action.action_hash)) == 3, "the bound did not release"
    assert forwarded == []
    store.close()


def test_T257_the_acs_hook_refuses_a_fingerprinted_approval(tmp_path, fake_clock):
    pytest.importorskip("httpx", reason="the gateway extra is not installed")
    from ctrlrun.acs import ACS_VERSION, TOOL_CALL_REQUEST, AcsControlHook

    store = SQLiteStateStore(tmp_path / "state.db", clock=fake_clock)
    control = Control(Policy.from_yaml(GATEWAY_POLICY), store, clock=fake_clock)
    hook = AcsControlHook(control, prefix="acs")

    def envelope() -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "method": TOOL_CALL_REQUEST,
            "id": 1,
            "params": {
                "acs_version": ACS_VERSION,
                "request_id": "11111111-1111-4111-8111-111111111111",
                "timestamp": "2026-09-04T10:00:00Z",
                "metadata": {
                    "agent_id": "ops-agent",
                    "session_id": "22222222-2222-4222-8222-222222222222",
                    "environment": "production",
                    "user_context": {"user_id": "ada", "roles": ["support"]},
                },
                "payload": {
                    "tool": {"name": "delete_customer", "provider": "crm"},
                    "arguments": {"customer_id": {"value": "C123"}},
                },
            },
        }

    asked = hook.handle(envelope())["result"]
    assert asked["decision"] == "ask", asked
    requested_ids = [
        event.approval_id for event in store.events() if event.type is EventType.APPROVAL_REQUESTED
    ]
    action = store.get_approval(requested_ids[-1]).request.action
    fingerprinted = _fingerprinted_approval_for(control, action, World())

    answer = hook.handle(envelope())["result"]

    assert answer["decision"] == "deny", answer
    assert answer["reason_codes"] == ["ctrlrun.blocked", MISSING], answer
    assert store.get_approval(fingerprinted).status is ApprovalStatus.GRANTED
    assert store.get_effect("delete:C123") is None
    store.close()


# --- T264 / T265: a database built by 0.6.1's own code ------------------------------------------


POSTGRES_URL = os.environ.get("CTRLRUN_TEST_POSTGRES")
postgres = pytest.mark.skipif(
    not POSTGRES_URL, reason="CTRLRUN_TEST_POSTGRES is not set; no server to run against"
)
BACKENDS = ["sqlite", pytest.param("postgres", marks=postgres)]

RELEASE = "0.6.1"

#: The instant 0.6.1's build script writes at, and a clock a few minutes after it, so the
#: approvals it granted are still unexpired when 0.7 presents them.
BUILT_AT = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def after_the_build() -> datetime:
    return BUILT_AT + timedelta(minutes=5)


@pytest.fixture(scope="session")
def release_061(tmp_path_factory):
    """`ctrlrun[postgres]==0.6.1` in an interpreter of its own (SPEC-v0.7 §8.5 T264).

    **The previous release's own code, never a hand-written fixture** (`v0.6 §3.5` item 2).
    With the `postgres` extra, because T264 is on both backends and 0.6.1's Postgres store is
    the thing whose schema the forward migration has to accept.
    """
    root = tmp_path_factory.mktemp("rel-0.6.1-postgres")
    env = root / "venv"
    venv.create(env, with_pip=True, symlinks=os.name != "nt")
    python = env / "bin" / "python"
    done = subprocess.run(
        [str(python), "-m", "pip", "install", "-q", f"ctrlrun[postgres]=={RELEASE}"],
        capture_output=True,
        text=True,
        env=releases._clean_env(),
    )
    if done.returncode != 0:
        if releases.REQUIRE_RELEASES:
            raise AssertionError(
                f"ctrlrun[postgres]=={RELEASE} could not be installed, and "
                "CTRLRUN_REQUIRE_RELEASE_FIXTURES=1. SPEC-v0.7 T264 asks for 0.6.1's own code; "
                f"skipping is not that.\n{done.stderr}"
            )
        pytest.skip(f"ctrlrun=={RELEASE} could not be installed; no network")
    probe = subprocess.run(
        [str(python), "-c", "import ctrlrun, importlib.metadata as m; print(m.version('ctrlrun'))"],
        capture_output=True,
        text=True,
        env=releases._clean_env(),
        cwd=root,
    )
    assert probe.stdout.strip() == RELEASE, (
        f"the release interpreter imports {probe.stdout.strip()!r}, not {RELEASE}: the fixture "
        f"would be this tree's database and prove the opposite of what it says.\n{probe.stderr}"
    )
    return python


BUILD_061 = textwrap.dedent("""
    import json, sys
    from datetime import UTC, datetime, timedelta
    from ctrlrun import (Action, ActionDenied, ApprovalRequired, Control, Policy, Principal,
                         with_approval)
    from ctrlrun.state import DelegationRecord, SQLiteStateStore

    backend, target = sys.argv[1], sys.argv[2]
    T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    clock = lambda: T0
    if backend == "sqlite":
        store = SQLiteStateStore(target, clock=clock)
    else:
        from ctrlrun.postgres import PostgresStateStore
        url, schema = target.rsplit("#", 1)
        store = PostgresStateStore(url, schema=schema, clock=clock)
    control = Control(Policy.from_yaml(sys.stdin.read()), store, clock=clock)

    def act(customer, name="customer.delete"):
        return Action(name=name, arguments={"customer_id": customer},
                      principal=Principal(agent="ops-agent", user="ada"))

    def request(action, key):
        try:
            control.execute(action, lambda: "x", key)
        except ApprovalRequired as pending:
            return pending.request_id
        raise SystemExit("0.6.1 did not ask for an approval")

    control.execute(act("C1", "customer.read"), lambda: "read", "read:C1")
    consumed = request(act("C2"), "delete:C2")
    store.grant_approval(consumed, "human:alice")
    with with_approval(consumed):
        control.execute(act("C2"), lambda: "deleted", "delete:C2")
    granted = request(act("C3"), "delete:C3")
    store.grant_approval(granted, "human:alice")
    pending = request(act("C4"), "delete:C4")
    try:
        control.execute(act("C5", "customer.purge"), lambda: "x", None)
    except ActionDenied:
        pass
    else:
        raise SystemExit("0.6.1 did not deny a purge")
    def lost():
        raise TimeoutError("the response was lost")
    try:
        control.execute(act("C6", "customer.read"), lost, "read:C6")
    except TimeoutError:
        pass
    store.put_delegation(DelegationRecord(
        delegation_id="dlg_" + "b" * 32, parent_id="ops", depth=1,
        grant_json='{"actions":["customer.*"],"delegable":false,"expires_at":null,"resources":null}',
        created_by_agent="ops-agent", created_by_user="ada", created_via="api", created_at=T0))
    receipts = store.receipts()
    print(json.dumps({
        "consumed": consumed, "granted": granted, "pending": pending,
        "policy_hash": store.get_approval(granted).request.policy_hash,
        "receipts": [[r.receipt_id, r.seq, r.hash] for r in receipts],
        "events": [e.event_id for e in store.events()],
        "delegation": "dlg_" + "b" * 32,
    }))
    store.close()
""")

OPEN_061 = textwrap.dedent("""
    import sys
    from ctrlrun.errors import SchemaMismatch
    backend, target = sys.argv[1], sys.argv[2]
    try:
        if backend == "sqlite":
            from ctrlrun.state import SQLiteStateStore
            SQLiteStateStore(target)
        else:
            from ctrlrun.postgres import PostgresStateStore
            url, schema = target.rsplit("#", 1)
            PostgresStateStore(url, schema=schema)
    except SchemaMismatch as refused:
        print("REFUSED", refused)
    else:
        raise SystemExit("0.6.1 opened a database 0.7 migrated")
""")


class _Built:
    """A database 0.6.1 built, on one backend, and what 0.6.1 said it put there."""

    def __init__(self, backend: str, target: str, facts: dict[str, Any], schema: str | None):
        self.backend = backend
        self.target = target
        self.facts = facts
        self.schema = schema

    def open(self, clock):
        if self.backend == "sqlite":
            return SQLiteStateStore(self.target, clock=clock)
        from ctrlrun.postgres import PostgresStateStore

        return PostgresStateStore(POSTGRES_URL, schema=self.schema, clock=clock)

    def sql(self, statement: str, parameters: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        if self.backend == "sqlite":
            connection = sqlite3.connect(self.target)
            try:
                rows = connection.execute(statement, parameters).fetchall()
                connection.commit()
            finally:
                connection.close()
            return [tuple(row) for row in rows]
        import psycopg

        with psycopg.connect(POSTGRES_URL, autocommit=True) as connection:
            connection.execute(f'SET search_path TO "{self.schema}"')
            cursor = connection.execute(statement.replace("?", "%s"), parameters)
            return [tuple(row) for row in cursor.fetchall()] if cursor.description else []


@pytest.fixture
def built_by_061(request, release_061, tmp_path):
    backend = request.param
    schema = None
    if backend == "sqlite":
        target = str(tmp_path / "state.db")
    else:
        from ctrlrun.postgres import PostgresStateStore

        schema = f"pre_{uuid.uuid4().hex[:12]}"
        PostgresStateStore.create_schema(POSTGRES_URL, schema)
        target = f"{POSTGRES_URL}#{schema}"
    done = subprocess.run(
        [str(release_061), "-c", BUILD_061, backend, target],
        input=POLICY,
        capture_output=True,
        text=True,
        env=releases._clean_env(),
        cwd=tmp_path,
    )
    assert done.returncode == 0, done.stderr
    built = _Built(backend, target, json.loads(done.stdout), schema)
    try:
        yield built
    finally:
        if schema is not None:
            from ctrlrun.postgres import PostgresStateStore

            PostgresStateStore.drop_schema(POSTGRES_URL, schema)


def _applied(built: _Built) -> list[str]:
    return [row[0] for row in built.sql("SELECT migration_id FROM schema_version ORDER BY 1")]


@pytest.mark.parametrize("built_by_061", BACKENDS, indirect=True)
def test_T264_a_database_built_by_061_migrates_and_keeps_every_row(built_by_061):
    built = built_by_061
    fake_clock = after_the_build
    facts = built.facts
    assert "0005_precondition_fingerprint" not in _applied(built), "0.6.1 knows 0005?"
    columns_before = built.sql("SELECT * FROM approvals WHERE approval_id = ?", (facts["granted"],))
    assert columns_before, "0.6.1 did not write the approval"

    store = built.open(fake_clock)

    assert _applied(built)[-1] == "0005_precondition_fingerprint"
    for approval_id, status in (
        (facts["consumed"], ApprovalStatus.CONSUMED),
        (facts["granted"], ApprovalStatus.GRANTED),
        (facts["pending"], ApprovalStatus.PENDING),
    ):
        record = store.get_approval(approval_id)
        assert record is not None and record.status is status, approval_id
        assert record.request.precondition_fingerprint is None, approval_id
    assert store.get_approval(facts["granted"]).approver == "human:alice"
    assert store.get_approval(facts["granted"]).policy_hash_at_approval == facts["policy_hash"]
    nulls = built.sql("SELECT COUNT(*) FROM approvals WHERE precondition_fingerprint IS NULL")
    total = built.sql("SELECT COUNT(*) FROM approvals")
    assert nulls == total == [(3,)], (nulls, total)

    assert str(store.get_effect("read:C1").state) == "committed"
    assert str(store.get_effect("delete:C2").state) == "committed"
    assert str(store.get_effect("read:C6").state) == "ambiguous"
    kept = {receipt.receipt_id: receipt for receipt in store.receipts()}
    for receipt_id, seq, stored_hash in facts["receipts"]:
        assert receipt_id in kept, f"receipt {receipt_id} was lost"
        assert kept[receipt_id].seq == seq and kept[receipt_id].hash == stored_hash
        assert kept[receipt_id].schema == "ctrlrun.receipt/v3"
        assert kept[receipt_id].chain_hash() == stored_hash, (
            f"a receipt 0.6.1 wrote no longer rehashes to its stored hash at seq {seq}"
        )
    assert set(facts["events"]) <= {event.event_id for event in store.events()}
    assert store.get_delegation(facts["delegation"]) is not None

    # The migrated store still works, and a 0.6.1 approval meets the recheck the way §6.4
    # says: with no provider it is 0.6.1's path, with one it is a refusal and never a skip.
    control = Control(Policy.from_yaml(POLICY), store, clock=fake_clock)
    c3 = store.get_approval(facts["granted"]).request.action
    mismatch = refused(control, c3, facts["granted"], World(), key="delete:C3")
    assert mismatch.reason == MISSING
    receipt = present(control, c3, facts["granted"], None, key="delete:C3")
    assert receipt.result is ReceiptResult.COMMITTED
    store.close()


@pytest.mark.parametrize("built_by_061", BACKENDS, indirect=True)
def test_T264_061_refuses_the_migrated_database_naming_0005_and_both_versions(
    built_by_061, release_061, fake_clock, tmp_path
):
    """The backward direction, **before any other table is read**: proved by taking the other
    tables away first, as `v0.6` T148 does. A 0.6.1 that read `approvals` on its way to the
    version check would raise a driver error here instead of `SchemaMismatch`."""
    from ctrlrun.migrations import ctrlrun_version

    built = built_by_061
    built.open(fake_clock).close()
    for table in (
        "effects",
        "approvals",
        "receipts",
        "events",
        "delegations",
        "continuations",
        "receipt_chain",
    ):
        built.sql(f"DROP TABLE IF EXISTS {table}")

    done = subprocess.run(
        [str(release_061), "-c", OPEN_061, built.backend, built.target],
        capture_output=True,
        text=True,
        env=releases._clean_env(),
        cwd=tmp_path,
    )

    assert done.returncode == 0, done.stderr
    message = done.stdout
    assert message.startswith("REFUSED"), message
    assert "0005_precondition_fingerprint" in message, message
    # Both versions, each by the phrase 0.6.1 puts it in: the build that is refusing, and the
    # build that wrote the migration it does not know. Until item 6 bumps this tree's version
    # the two strings are the same, so each is asserted in its own slot.
    assert f"This build is ctrlrun {RELEASE}" in message, message
    assert f"(written by ctrlrun {ctrlrun_version()})" in message, message


def _chain_continued_by_07(built: _Built, fake_clock):
    store = built.open(fake_clock)
    control = Control(Policy.from_yaml(POLICY), store, clock=fake_clock)
    world = World()
    for customer in ("C7", "C8"):
        action = an_action(control, customer)
        request_id = granted(control, action, world, f"delete:{customer}")
        present(control, action, request_id, world, key=f"delete:{customer}")
    return store


def _stored_json(built: _Built, seq: int) -> str:
    return built.sql("SELECT json FROM receipts WHERE seq = ?", (seq,))[0][0]


def _rewrite(built: _Built, seq: int, text: str) -> None:
    built.sql("UPDATE receipts SET json = ? WHERE seq = ?", (text, seq))


FABRICATED = "sha256:" + "f" * 64


def _add_a_recheck(document: dict[str, Any]) -> dict[str, Any]:
    return {**document, "precondition_at_recheck": FABRICATED}


def _relabel(schema: str):
    def tamper(document: dict[str, Any]) -> dict[str, Any]:
        return {**document, "schema": schema}

    return tamper


def _unlabel(document: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in document.items() if key != "schema"}


TAMPERS = {
    "a v3 row given a precondition_at_recheck key": _add_a_recheck,
    "a chained v3 row relabelled v1": _relabel("ctrlrun.receipt/v1"),
    "a chained v3 row relabelled v2": _relabel("ctrlrun.receipt/v2"),
    "a v3 row with its schema key removed": _unlabel,
    "a v3 row saying ctrlrun.receipt/v9": _relabel("ctrlrun.receipt/v9"),
}


@pytest.mark.parametrize("built_by_061", BACKENDS, indirect=True)
def test_T265_a_chain_written_by_061_and_continued_by_07_verifies_end_to_end(
    built_by_061, fake_clock, tmp_path, monkeypatch
):
    from ctrlrun.receipt import verify_chain
    from ctrlrun.verify.scenarios import _AlteredChain

    built = built_by_061
    store = _chain_continued_by_07(built, fake_clock)
    receipts = store.receipts()
    old = [receipt for receipt in receipts if receipt.schema == "ctrlrun.receipt/v3"]
    new = [receipt for receipt in receipts if receipt.schema == "ctrlrun.receipt/v4"]
    assert len(old) == len(built.facts["receipts"]) >= 4
    assert len(new) == 2 and len(receipts) == len(old) + len(new)
    for receipt in old:
        assert receipt.chain_hash() == receipt.hash, f"v3 seq {receipt.seq} rehashes differently"
    for receipt in new:
        document = json.loads(_stored_json(built, receipt.seq))
        assert document["schema"] == "ctrlrun.receipt/v4"
        assert document["precondition_at_recheck"] == fingerprint(AT_REQUEST)

    report = verify_chain(store)
    assert report.ok, report.breaks
    assert report.verified == len(receipts)
    g11 = verify_chain(_AlteredChain(tuple(store.receipts()), store.chain_head()))
    assert g11.ok, g11.breaks
    store.close()

    if built.backend == "sqlite":
        from click.testing import CliRunner

        from ctrlrun.cli import main as cli

        result = CliRunner().invoke(
            cli.main, ["receipts", "--verify-chain", "--store-url", f"sqlite://{built.target}"]
        )
        assert result.exit_code == 0, result.output
        assert f"{len(receipts)} of {len(receipts)}" in result.output, result.output


@pytest.mark.parametrize("label", sorted(TAMPERS))
@pytest.mark.parametrize("built_by_061", BACKENDS, indirect=True)
def test_T265_every_tamper_the_schema_bump_could_hide_is_content_altered(
    built_by_061, fake_clock, tmp_path, monkeypatch, label
):
    """§6.11: *hash what was stored.* Each tamper changes the stored document, so each is
    `content_altered` at its `seq`, with no rule about key sets for the reader to get wrong.

    **The mutation this catches**: `chain_hash()` hashing `to_dict()` for a stored receipt
    instead of its stored document. That leaves the untouched `v3` rows verifying (a `v3`
    receipt renders its document byte for byte) and the relabelled rows failing (each renders
    under its own label), and it is caught by exactly one of these five: the added
    `precondition_at_recheck` key, which then verifies cleanly.
    """
    from ctrlrun.receipt import verify_chain

    built = built_by_061
    _chain_continued_by_07(built, fake_clock).close()
    seq = 2
    original = _stored_json(built, seq)
    document = json.loads(original)
    assert document["schema"] == "ctrlrun.receipt/v3", "the target is not a row 0.6.1 wrote"
    _rewrite(built, seq, json.dumps(TAMPERS[label](document)))

    store = built.open(fake_clock)
    receipts = store.receipts()  # MUST NOT raise: a raise here blinds every reader at once
    report = verify_chain(store)
    assert not report.ok, f"{label}: the chain verified"
    assert ("content_altered", seq) in [(b.name, b.seq) for b in report.breaks], (
        label,
        report.breaks,
    )
    tampered = next(receipt for receipt in receipts if receipt.seq == seq)
    assert tampered.precondition_at_recheck is None, "an undeclared key's value was surfaced"
    assert FABRICATED not in tampered.to_json(), "an undeclared key's value was rendered"
    assert len(receipts) == len(built.facts["receipts"]) + 2
    store.close()

    if built.backend == "sqlite":
        from click.testing import CliRunner

        from ctrlrun.cli import main as cli

        url = f"sqlite://{built.target}"
        listed = CliRunner().invoke(cli.main, ["receipts", "--store-url", url])
        assert listed.exit_code == 0, listed.output
        for receipt in receipts:
            assert receipt.receipt_id in listed.output, f"{receipt.receipt_id} was not listed"
        as_json = CliRunner().invoke(cli.main, ["receipts", "--json", "--store-url", url])
        assert FABRICATED not in as_json.output
        policy = tmp_path / "ctrlrun.yaml"
        policy.write_text(POLICY, encoding="utf-8")
        monkeypatch.setenv("CTRLRUN_CONFIG", str(policy))
        stats = CliRunner().invoke(cli.main, ["stats", "--json", "--store-url", url])
        assert stats.exit_code == 0, stats.output
        assert json.loads(stats.output)["actions"] == len(receipts)

    _rewrite(built, seq, original)
    restored = built.open(fake_clock)
    assert verify_chain(restored).ok, "restoring the row did not restore the chain"
    restored.close()


def test_T265_a_read_back_receipt_altered_with_replace_is_content_altered(tmp_path, fake_clock):
    """The sixth case, in memory rather than on a row: rule (a) of §6.11. G11's own tamper is
    `replace(target, decision_reason=...)` on a read-back receipt handed to `verify_chain`; if
    the stored document survived `replace()`, the altered receipt would hash the untouched
    document and verify. And the same receipt written again reads back clean."""
    from dataclasses import replace

    from ctrlrun.receipt import new_receipt_id, verify_chain
    from ctrlrun.verify.scenarios import _AlteredChain

    store = SQLiteStateStore(tmp_path / "state.db", clock=fake_clock)
    control = Control(Policy.from_yaml(POLICY), store, clock=fake_clock)
    for customer in ("C1", "C2", "C3"):
        control.execute(an_action(control, customer, "customer.read"), Executor(), None)
    receipts = store.receipts()
    target = receipts[1]
    altered = replace(target, decision_reason=f"{target.decision_reason}-altered")

    report = verify_chain(
        _AlteredChain(
            tuple(altered if item.seq == target.seq else item for item in receipts),
            store.chain_head(),
        )
    )
    assert ("content_altered", target.seq) in [(b.name, b.seq) for b in report.breaks], (
        "a receipt altered with replace() still hashed its stored document"
    )

    written = store.put_receipt(replace(altered, receipt_id=new_receipt_id()))
    back = next(item for item in store.receipts() if item.receipt_id == written.receipt_id)
    assert back.chain_hash() == back.hash == written.hash, "the written-again receipt is altered"
    assert verify_chain(store).ok
    store.close()


# --- T266: the store conformance suite covers the column -----------------------------------------


def test_T266_the_store_conformance_suite_covers_the_column():
    from ctrlrun.conformance.report import SuiteStatus
    from ctrlrun.conformance.store import SUITES, run
    from ctrlrun.conformance.store.backends import InMemoryBackend, SQLiteBackend
    from ctrlrun.conformance.store.fixtures import FIXTURES

    assert "precondition-fingerprint" in {case.id for case in SUITES["approval"]}
    fixture = next(item for item in FIXTURES if item.name == "drops-the-precondition-fingerprint")
    assert fixture.cases == {"approval": "precondition-fingerprint"}

    broken = run(fixture.backend(), only=("precondition-fingerprint",))
    case = next(c for s in broken.suites for c in s.cases if c.id == "precondition-fingerprint")
    assert case.status is SuiteStatus.FAIL and fixture.because in (case.reason or "")

    import tempfile

    for backend in (
        InMemoryBackend(),
        SQLiteBackend(__import__("pathlib").Path(tempfile.mkdtemp())),
    ):
        passed = run(backend, only=("precondition-fingerprint",))
        case = next(c for s in passed.suites for c in s.cases if c.id == "precondition-fingerprint")
        assert case.status is SuiteStatus.PASS, case.reason


@postgres
def test_T266_postgres_persists_the_column():
    from ctrlrun.conformance.report import SuiteStatus
    from ctrlrun.conformance.store import run
    from ctrlrun.conformance.store.backends import PostgresBackend

    backend = PostgresBackend(POSTGRES_URL)
    try:
        report = run(backend, only=("precondition-fingerprint",))
        case = next(c for s in report.suites for c in s.cases if c.id == "precondition-fingerprint")
        assert case.status is SuiteStatus.PASS, case.reason
    finally:
        backend.reset()


@postgres
def test_T257_a_postgres_store_that_lost_the_column_is_a_refusal(fake_clock):
    from ctrlrun.postgres import PostgresStateStore

    schema = f"pre_{uuid.uuid4().hex[:12]}"
    PostgresStateStore.create_schema(POSTGRES_URL, schema)
    try:
        store = PostgresStateStore(POSTGRES_URL, schema=schema, clock=fake_clock)
        control = Control(Policy.from_yaml(POLICY), store, clock=fake_clock)
        world = World()
        action = an_action(control)
        request_id = granted(control, action, world)
        built = _Built("postgres", "", {}, schema)
        built.sql(
            "UPDATE approvals SET precondition_fingerprint = NULL WHERE approval_id = ?",
            (request_id,),
        )
        assert store.get_approval(request_id).request.precondition_fingerprint is None

        mismatch = refused(control, action, request_id, world)

        assert mismatch.reason == MISSING
        assert store.get_effect(KEY) is None
        store.close()
    finally:
        PostgresStateStore.drop_schema(POSTGRES_URL, schema)


# --- T267: §7's column, row by row -------------------------------------------------------------


def test_T267_control_evaluate_never_calls_the_provider(control):
    """`Control.evaluate` takes no provider at all: the row's "no" is structural, and the
    count proves nothing else reached one."""
    world = World()
    action = an_action(control)
    request_id = granted(control, action, world)
    before = world.calls

    with with_approval(request_id):
        assert str(control.evaluate(action).decision) == "approve"

    assert "preconditions" not in inspect.signature(Control.evaluate).parameters
    assert world.calls == before


def test_T267_needs_approval_never_calls_the_provider(control):
    from ctrlrun import context, needs_approval

    world = World()
    granted(control, an_action(control), world)
    before = world.calls

    with context("ops-agent", "ada"):
        assert needs_approval(control, "customer.delete", {"customer_id": "C123"}) is True

    assert world.calls == before


def test_T267_an_interrupt_provider_records_the_fingerprint_and_its_wait_never_fetches(
    tmp_path, fake_clock
):
    """`InterruptApprovalProvider` builds its requests through `build_request`, so the
    fingerprint is recorded; its `wait` records an answer and never calls the provider."""
    from ctrlrun import ApprovalAnswer, InterruptApprovalProvider

    class Human:
        framework = "test-framework"
        carries_approved_arguments = False

        def interrupt(self, pending):
            return ApprovalAnswer(granted=True, approver="human:alice")

    store = SQLiteStateStore(tmp_path / "state.db", clock=fake_clock)
    provider = InterruptApprovalProvider(store, Human(), clock=fake_clock)
    control = Control(Policy.from_yaml(POLICY), store, provider, clock=fake_clock)
    world = World()
    action = an_action(control)
    request_id = requested(control, action, world)
    assert store.get_approval(request_id).request.precondition_fingerprint == fingerprint(
        AT_REQUEST
    )
    before = world.calls

    provider.wait(request_id, None)

    assert world.calls == before
    assert store.get_approval(request_id).status is ApprovalStatus.GRANTED
    receipt = present(control, action, request_id, world)
    assert receipt.result is ReceiptResult.COMMITTED
    assert world.calls == before + 1, "Control.execute rechecks in full before consuming"
    store.close()


def test_T267_the_operator_servers_write_tools_never_call_the_provider(
    tmp_path, fake_clock, monkeypatch
):
    pytest.importorskip("httpx", reason="the gateway extra is not installed")
    from ctrlrun.gateway.operator import OperatorConfig, OperatorServer

    store = SQLiteStateStore(tmp_path / "state.db", clock=fake_clock)
    control = Control(Policy.from_yaml(POLICY), store, clock=fake_clock)

    class Approver:
        def resolve(self, context):
            if not context.headers.get("x-approver"):
                return None
            return Principal(agent="approver-app", user="alice")

    server = OperatorServer(
        OperatorConfig(principal_header="x-approver", user_header="x-approver-user"),
        control,
        Approver(),
    )
    world = World()
    to_grant = requested(control, an_action(control, "C1"), world, "delete:C1")
    to_deny = requested(control, an_action(control, "C2"), world, "delete:C2")
    before = world.calls

    def call(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }
        headers = {
            "MCP-Protocol-Version": "2026-07-28",
            "Mcp-Method": "tools/call",
            "Mcp-Name": tool,
            "X-Approver": "alice",
        }
        return json.loads(server.handle(json.dumps(body).encode(), headers).body)

    assert "error" not in call("approve", {"request_id": to_grant})
    assert "error" not in call("deny", {"request_id": to_deny})
    assert store.get_approval(to_grant).status is ApprovalStatus.GRANTED
    assert store.get_approval(to_deny).status is ApprovalStatus.DENIED
    assert world.calls == before
    store.close()


# --- T268: the documentation says narrows --------------------------------------------------------


REPO_ROOT = Path(__file__).resolve().parents[1]

#: T268's words, on word boundaries. Stems for the three that inflect into the same claim
#: (prevents, prevention; guarantees, guaranteed; ensures, ensured), exact forms for the rest.
CLAIM = re.compile(
    r"\b(prevent\w*|close|closes|closed|closing|guarantee\w*|ensur\w*|opens nothing|cannot|"
    r"blocks)\b",
    re.IGNORECASE,
)
#: "Mentions a precondition or a fingerprint", and the recheck that compares them: §8's own
#: positive control, *"the recheck prevents a stale approval"*, names neither of the first two.
SUBJECT = re.compile(r"precondition|fingerprint|recheck", re.IGNORECASE)

#: Where a Markdown block ends: a blank line, a list item, a table row or a heading. A sentence
#: never runs across one, so a bullet's claim is not joined to the sentence before it.
_BLOCK = re.compile(r"\n\s*\n|\n(?=\s*(?:[-*] |\d+\. |\||#))")


def _sentences(text: str) -> list[str]:
    """Sentences, with Markdown line wraps undone: a claim wrapped across two lines is one."""
    found: list[str] = []
    for block in _BLOCK.split(text):
        flowing = re.sub(r"\s+", " ", block).strip()
        found += [
            part.strip()
            for part in re.split(r"(?<=[.!?])\s+(?=[A-Z*`(\[_\"|])", flowing)
            if part.strip()
        ]
    return found


def _flagged(text: str) -> list[str]:
    return [
        sentence
        for sentence in _sentences(text)
        if CLAIM.search(sentence) and SUBJECT.search(sentence)
    ]


def _spec_section_six() -> str:
    spec = (REPO_ROOT / "docs" / "SPEC-v0.7.md").read_text(encoding="utf-8")
    start = spec.index("\n## 6. Precondition fingerprints")
    end = spec.index("\n## 7. ")
    return spec[start:end]


def _spec_section_twelve_five() -> str:
    spec = (REPO_ROOT / "docs" / "SPEC-v0.7.md").read_text(encoding="utf-8")
    start = spec.index("\n### 12.5 Item 5")
    end = spec.index("\n### 12.6 ")
    return spec[start:end]


def _docstrings() -> str:
    """The docstrings of every public name §6 adds or amends (§9.2)."""
    from ctrlrun.approval import ApprovalRequest
    from ctrlrun.receipt import Receipt

    return "\n\n".join(
        inspect.getdoc(item) or "" for item in (protect, Control.execute, ApprovalRequest, Receipt)
    )


SCANNED = {
    "README.md": lambda: (REPO_ROOT / "README.md").read_text(encoding="utf-8"),
    "CHANGELOG.md": lambda: (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8"),
    "SPEC-v0.7 §6": _spec_section_six,
    "SPEC-v0.7 §12.5": _spec_section_twelve_five,
    "docstrings": _docstrings,
}

#: **Every sentence below is allow-listed because somebody wrote it here on purpose**, on
#: `v0.6` T180's design: a new occurrence fails, and whoever adds one says which kind it is.
#: Matched by a fragment unique to the sentence, so a rewrap does not break the list and a
#: reworded claim does.
#:
#: Sentences that **disclaim**: they say the recheck narrows, and name what it does not do.
DISCLAIMS: dict[str, tuple[str, ...]] = {
    "CHANGELOG.md": (
        "A precondition fingerprint **narrows** the window between a human's approval and the "
        "action's execution; it does not close it.",
        "and precondition fingerprints, which **narrow** the window between a human's approval "
        "and the action's execution and do not close it",
    ),
    "SPEC-v0.7 §6": (
        "**A precondition fingerprint narrows the window between a human's decision and the "
        "action's execution; it does not close one.**",
    ),
    "docstrings": (
        "The recheck narrows the window a human's approval leaves open; it does not close it",
    ),
}

#: Sentences where the word is about something else: a catalogue's name, a path that has no
#: provider to recheck with, a provider that produced nothing to compare, and a 0.6 process the
#: new one has no way to see.
ANOTHER_SUBJECT: dict[str, tuple[str, ...]] = {
    "CHANGELOG.md": (
        '"a moved precondition is refused" before the reservation, under `ctrlrun.guarantees/v3`.',
    ),
    "SPEC-v0.7 §6": (
        "the alternative is a path that cannot recheck spending an approval that was granted "
        "conditional on a recheck.",
        "so the comparison cannot be made, and **a check that cannot be made is not a check that "
        "passed**",
        "The kernel cannot detect that process from the new one, so the rule is operational",
    ),
}


def test_T268_the_pattern_fires_on_a_prevention_claim():
    """The positive control: a scan that never fires is a scan nothing exercises."""
    assert _flagged("Some prose. The recheck prevents a stale approval. More prose.") == [
        "The recheck prevents a stale approval."
    ]
    assert _flagged("The precondition check blocks a stale world.")
    assert not _flagged("The fingerprint narrows a window.")


@pytest.mark.parametrize("name", sorted(SCANNED))
def test_T268_the_documentation_says_narrows(name):
    allowed = DISCLAIMS.get(name, ()) + ANOTHER_SUBJECT.get(name, ())
    unexplained = [
        sentence
        for sentence in _flagged(SCANNED[name]())
        if not any(fragment in sentence for fragment in allowed)
    ]
    assert not unexplained, (
        f"{name} has a sentence about a precondition or a fingerprint that uses a word of "
        "prevention, and it is not on the allow-list. The recheck narrows a window and does not "
        "close one; rewrite it, or add it to DISCLAIMS or ANOTHER_SUBJECT saying which it is:\n"
        + "\n".join(f"  - {sentence}" for sentence in unexplained)
    )


def test_T268_every_allow_listed_fragment_still_matches_a_flagged_sentence():
    """An allow-list entry that matches no flagged sentence is a stale exemption waiting to
    cover a new claim that happens to share its words."""
    for name, fragments in {**DISCLAIMS, **ANOTHER_SUBJECT}.items():
        flagged = _flagged(SCANNED[name]())
        for fragment in fragments:
            assert any(fragment in sentence for sentence in flagged), (
                f"{name}: allow-listed fragment matches no flagged sentence: {fragment!r}"
            )


# --- T269: G16 in verify -------------------------------------------------------------------------

AUTHORITY_PAYMENTS = REPO_ROOT / "examples" / "authority" / "payments.yaml"
V1_PAYMENTS = REPO_ROOT / "examples" / "policies" / "payments.yaml"


def _g16(path, **kwargs):
    from ctrlrun.verify import run

    report = run(path, only=("G16",), **kwargs)
    return report, next(result for result in report.guarantees if result.id == "G16")


def test_T269_G16_is_in_the_v3_catalogue():
    from ctrlrun.verify import guarantees as reg

    assert reg.CATALOGUE == "ctrlrun.guarantees/v3"
    assert "G16" in reg.BY_ID
    assert reg.BY_ID["G16"].descends_from


@pytest.mark.authority
@pytest.mark.parametrize("path", [AUTHORITY_PAYMENTS, V1_PAYMENTS], ids=["authority", "v1"])
def test_T269_G16_passes_on_the_shipped_examples(path):
    from ctrlrun.verify import Status
    from ctrlrun.verify import guarantees as reg

    report, result = _g16(path)

    assert result.status is Status.PASS, (result.reason, result.counterexample)
    assert result.detail["note"] == reg.PRECONDITION_NOTE
    assert "verify supplies its own precondition provider" in report.to_text()


def test_T269_G16_is_not_applicable_where_nothing_requires_approval(tmp_path):
    from ctrlrun.verify import Status
    from ctrlrun.verify import guarantees as reg

    path = tmp_path / "ctrlrun.yaml"
    path.write_text("schema: ctrlrun.policy/v1\nactions:\n  a.read:\n    decision: allow\n")

    _, result = _g16(path)

    assert result.status is Status.NOT_APPLICABLE
    assert result.reason == reg.NO_APPROVE_RULE


def test_T269_G16_fails_where_the_recheck_is_gone(monkeypatch):
    """The guarantee is the test and not the mechanism: delete the recheck and G16 is `fail`
    on the refusal half, not `control failed` and not `pass`."""
    from ctrlrun.verify import Status

    monkeypatch.setattr(Control, "_recheck", lambda self, *args, **kwargs: None)

    report, result = _g16(V1_PAYMENTS)

    if result.status is not Status.FAIL:
        raise AssertionError(f"G16 reported {result.status} with the recheck deleted")
    assert result.reason != "control failed"
    assert report.exit_code == 1


def test_T269_G16_fails_where_the_capture_is_gone(monkeypatch):
    """A kernel that never records the fingerprint at request time: the presenting pass then
    meets a provider and an approval without one, which is `precondition_missing` -- and G16's
    assertion on the reason, not on the type, is what tells the two apart."""
    from dataclasses import replace

    import ctrlrun.approval as approval
    from ctrlrun.verify import Status

    original = approval.build_request
    monkeypatch.setattr(
        approval,
        "build_request",
        lambda *args, **kwargs: replace(original(*args, **kwargs), precondition_fingerprint=None),
    )

    _, result = _g16(V1_PAYMENTS)

    assert result.status is Status.FAIL, result.status
    assert "precondition_missing" in (result.reason or ""), result.reason


def test_T269_G16_with_a_broken_control_is_control_failed(monkeypatch):
    from ctrlrun.verify import Status, scenarios

    monkeypatch.setattr(scenarios._Executor, "__call__", lambda self: None)

    _, result = _g16(V1_PAYMENTS)

    assert result.status is Status.FAIL
    assert result.reason == "control failed"


def test_T269_G16_is_deterministic():
    """`v0.4 §3.7`: two runs against one configuration produce the same guarantee JSON."""
    first = _g16(V1_PAYMENTS)[1].to_dict()
    second = _g16(V1_PAYMENTS)[1].to_dict()
    assert first == second


def test_the_fingerprint_is_not_shown_to_anybody_deciding(tmp_path, fake_clock):
    """§6.10: a hash tells a human nothing about the world they are approving, so the
    fingerprint is not added to the webhook document, to `ctrlrun inspect`'s approval entries,
    or to the operator server's pending listing. The receipt and `APPROVAL_INVALIDATED` carry
    it, and those are evidence, not the question put to a human."""
    from ctrlrun.reporting import approval_document, inspection_for
    from ctrlrun.webhook import _payload

    store = SQLiteStateStore(tmp_path / "state.db", clock=fake_clock)
    control = Control(Policy.from_yaml(POLICY), store, clock=fake_clock)
    action = an_action(control)
    request_id = requested(control, action, World())
    record = store.get_approval(request_id)
    assert record.request.precondition_fingerprint == fingerprint(AT_REQUEST)

    webhook = json.loads(_payload(record.request, None))
    assert "precondition" not in json.dumps(webhook)
    assert "precondition" not in json.dumps(approval_document(record))
    document = inspection_for(store, action.action_id)
    assert document is not None
    assert all("precondition" not in json.dumps(entry) for entry in document["approvals"])
    assert fingerprint(AT_REQUEST) not in json.dumps(webhook)
    store.close()


def test_the_operator_servers_pending_listing_carries_no_fingerprint(tmp_path, fake_clock):
    pytest.importorskip("httpx", reason="the gateway extra is not installed")
    from ctrlrun.gateway.operator import OperatorConfig, OperatorServer

    store = SQLiteStateStore(tmp_path / "state.db", clock=fake_clock)
    control = Control(Policy.from_yaml(POLICY), store, clock=fake_clock)
    server = OperatorServer(
        OperatorConfig(principal_header="x-approver", user_header="x-approver-user"),
        control,
        None,
    )
    requested(control, an_action(control), World())
    record = store.approvals_for(an_action(control).action_hash)[0]

    entry = server._pending_entry(record, fake_clock())

    assert record.request.precondition_fingerprint is not None
    assert "precondition" not in json.dumps(entry)
    store.close()


def test_T267_an_adapters_protected_tool_rechecks_through_the_interrupt_seam(tmp_path, fake_clock):
    """§7's adapter row: an adapter's protected tool is `@protect` reached through a framework
    (`v0.5 §4.1`), so the decorator's provider is captured on the request pass, the framework's
    interrupt answers, and the re-presentation compares before it consumes. Driven through
    `InterruptApprovalProvider`, the seam both reference adapters use; neither adapter builds
    `@protect` itself, so there is nothing adapter-side to forward."""
    from ctrlrun import ApprovalAnswer, InterruptApprovalProvider, context

    world = World()

    class Framework:
        framework = "test-framework"
        carries_approved_arguments = False
        moves_the_world = False

        def interrupt(self, pending):
            if self.moves_the_world:
                world.state = dict(MOVED)
            return ApprovalAnswer(granted=True, approver="human:alice")

    store = SQLiteStateStore(tmp_path / "state.db", clock=fake_clock)
    framework = Framework()
    provider = InterruptApprovalProvider(store, framework, clock=fake_clock)
    control = Control(Policy.from_yaml(POLICY), store, provider, clock=fake_clock)
    ran: list[str] = []

    @protect(
        "customer.delete",
        effect="delete:{customer_id}",
        wait=True,
        control=control,
        preconditions=world,
    )
    def delete(customer_id: str) -> str:
        ran.append(customer_id)
        return "deleted"

    with context("ops-agent", "ada"):
        assert delete("C1") == "deleted"
    assert world.calls == 2 and ran == ["C1"]

    framework.moves_the_world = True
    with context("ops-agent", "ada"), pytest.raises(ApprovalMismatch) as raised:
        delete("C2")
    assert raised.value.reason == CHANGED
    assert ran == ["C1"]
    store.close()


def test_T267_delegate_and_revoke_take_no_provider():
    """§7: they create and remove authority and consume no approval, so the "no" is the
    signature's, and it is written down rather than assumed."""
    for method in (Control.delegate, Control.revoke, Control.resume, Control.evaluate):
        assert "preconditions" not in inspect.signature(method).parameters, method.__name__


def test_put_receipt_writes_v4_whatever_schema_the_receipt_was_read_under(tmp_path, fake_clock):
    """§12.5: a `v1` or `v2` key set has no `seq`, so a receipt written into the chain under
    its old label would carry no position in its own document and read back `unchained`. A
    store writes the schema this binary writes."""
    from dataclasses import replace

    from ctrlrun.receipt import new_receipt_id, verify_chain

    store = SQLiteStateStore(tmp_path / "state.db", clock=fake_clock)
    control = Control(Policy.from_yaml(POLICY), store, clock=fake_clock)
    control.execute(an_action(control, "C1", "customer.read"), Executor(), None)
    template = store.receipts()[0]
    for label in ("ctrlrun.receipt/v1", "ctrlrun.receipt/v2", "ctrlrun.receipt/v3"):
        written = store.put_receipt(replace(template, receipt_id=new_receipt_id(), schema=label))
        assert written.schema == RECEIPT_SCHEMA, label
    back = store.receipts()
    assert all(receipt.seq is not None for receipt in back), "a re-put receipt lost its seq"
    assert {receipt.schema for receipt in back} == {RECEIPT_SCHEMA}
    assert verify_chain(store).ok
    store.close()


def test_every_schema_renders_under_its_own_label_and_key_set():
    """§6.11's key sets, counted: `v1` 19, `v2` 21, `v3` 26, `v4` 28; an unknown label renders
    `v3`'s keys under its own label, and an absent one renders no `schema` key."""
    from dataclasses import replace

    from ctrlrun.receipt import Receipt

    base = Receipt(
        receipt_id="ctr_" + "1" * 32,
        action_id="act_1",
        action="customer.read",
        action_hash="sha256:" + "0" * 64,
        principal=Principal(agent="ops-agent", user="ada"),
        resource=None,
        arguments={},
        environment="production",
        decision="allow",  # type: ignore[arg-type]
        decision_reason="decision",
        result=ReceiptResult.COMMITTED,
        started_at=datetime(2026, 9, 1, tzinfo=UTC),
        finished_at=datetime(2026, 9, 1, tzinfo=UTC),
        precondition_at_request=FABRICATED,
    )
    counts = {
        "ctrlrun.receipt/v1": 19,
        "ctrlrun.receipt/v2": 21,
        "ctrlrun.receipt/v3": 26,
        "ctrlrun.receipt/v4": 28,
        "ctrlrun.receipt/v9": 26,
        "": 25,
    }
    for label, count in counts.items():
        document = replace(base, schema=label).to_dict()
        assert len(document) == count, (label, sorted(document))
        assert document.get("schema") == (label or None)
        if label != "ctrlrun.receipt/v4":
            assert FABRICATED not in json.dumps(document), label
    assert replace(base, schema="ctrlrun.receipt/v1").to_dict()["principal"] == {
        "agent": "ops-agent",
        "user": "ada",
    }


def test_T269_G16s_note_is_printed_beneath_G3s_on_the_full_catalogue():
    """The window the note rule is about: a full run on a document with no `effect:` template
    puts G3's note first, and G16's is a different sentence. A report that printed only the
    first note it met would drop G16's here, and a run of G16 alone never opens that window."""
    from ctrlrun.verify import guarantees as reg
    from ctrlrun.verify import run

    text = re.sub(r"\s+", " ", run(V1_PAYMENTS).to_text())

    assert reg.EFFECT_TEMPLATE_NOTE in text, "G3's note is not in this report"
    assert reg.PRECONDITION_NOTE in text, "G16's note was dropped beneath G3's"
    assert text.index(reg.EFFECT_TEMPLATE_NOTE) < text.index(reg.PRECONDITION_NOTE)


class _Ahead:
    """`Control`'s clock, running ahead of the store's by a fixed amount."""

    def __init__(self, behind, by: timedelta) -> None:
        self._behind = behind
        self.by = by

    def __call__(self) -> datetime:
        return self._behind() + self.by


def _divergent(state_store, fake_clock) -> tuple[Control, _Ahead]:
    ahead = _Ahead(fake_clock, timedelta(0))
    return Control(Policy.from_yaml(POLICY), state_store, clock=ahead), ahead


def test_where_neither_side_has_a_fingerprint_the_store_call_is_061s_exactly(
    state_store, fake_clock
):
    """§6.2's first row: *unchanged from 0.6.1*. The read §6.6 adds must not decide anything
    where the precondition question does not arise, and the one place that is visible is a
    `Control` whose clock disagrees with its store's: 0.6.1 let the store's clock decide expiry
    at consumption, and so does this, rather than `Control` refusing from its own read."""
    control, ahead = _divergent(state_store, fake_clock)
    action = an_action(control)
    request_id = granted(control, action, None)
    ahead.by = timedelta(minutes=20)  # past expiry by Control's clock, not by the store's
    executor = Executor()

    receipt = present(control, action, request_id, None, executor)

    assert receipt.result is ReceiptResult.COMMITTED and executor.calls == 1


def test_where_a_precondition_is_in_play_a_divergent_clock_spends_nothing_that_runs(
    state_store, fake_clock
):
    """§12.5's stated caveat, pinned: with a precondition in play `Control` raises the refusal
    its own read found, and an expired grant goes to the store through `consume_approval` only.
    A store whose clock disagrees then spends a grant `Control` calls expired, and that is all:
    nothing reserved, nothing run, and the provider never called."""
    control, ahead = _divergent(state_store, fake_clock)
    world = World()
    action = an_action(control)
    request_id = granted(control, action, world)
    ahead.by = timedelta(minutes=20)
    before = world.calls
    executor = Executor()

    mismatch = refused(control, action, request_id, world, executor)

    assert mismatch.reason == "expired"
    assert executor.calls == 0 and world.calls == before
    assert state_store.get_effect(KEY) is None
