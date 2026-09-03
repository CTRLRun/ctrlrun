"""The OpenTelemetry sink. Build-list item 8; SPEC-v0.2 §8, acceptance test T29.

An `EventSink` (§4) and nothing more: it never decides, never blocks, and never raises. What
it exports is a copy of what the store already holds — so a failing exporter is a lost export,
never a failed action.
"""

from __future__ import annotations

import pytest

from ctrlrun import (
    ApprovalRequired,
    Control,
    InMemoryStateStore,
    MissingDependency,
    NotExecuted,
    Policy,
    context,
    protect,
)

pytest.importorskip("opentelemetry.sdk", reason="the otel extra is not installed")

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from ctrlrun.otel import OTelEventSink

POLICY = """
schema: ctrlrun.policy/v1
actions:
  stripe.refund:
    rules:
      - when: { amount_lte: 500 }
        decision: allow
      - when: { amount_lte: 5000 }
        decision: approve
      - decision: deny
"""


@pytest.fixture
def exporter():
    """A real SDK tracer provider with an in-memory exporter, as §8's test asks for."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter, provider


@pytest.fixture
def store():
    store = InMemoryStateStore()
    yield store
    store.close()


def _control(store, provider, **options):
    return Control(
        Policy.from_yaml(POLICY),
        store,
        sinks=[OTelEventSink(tracer_provider=provider, **options)],
    )


def _refund(control, executor=None):
    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def refund(payment_id: str, amount: int) -> str:
        return executor() if executor else "re_1"

    return refund


def _spans(exporter):
    return exporter.get_finished_spans()


def _attrs(span):
    return dict(span.attributes or {})


# --- T29: one span per action ----------------------------------------------------------


def test_T29_one_action_produces_one_span_named_for_the_action(store, exporter):
    export, provider = exporter
    with context(agent="refund-agent", user="alice"):
        _refund(_control(store, provider))(payment_id="txn_1", amount=200)

    spans = _spans(export)
    assert len(spans) == 1
    assert spans[0].name == "stripe.refund"


def test_T29_the_span_carries_every_attribute_the_spec_names(store, exporter):
    export, provider = exporter
    with context(agent="refund-agent", user="alice"):
        _refund(_control(store, provider))(payment_id="txn_1", amount=200)

    attributes = _attrs(_spans(export)[0])
    for name in (
        "ctrlrun.action_id",
        "ctrlrun.action.name",
        "ctrlrun.action_hash",
        "ctrlrun.principal.agent",
        "ctrlrun.principal.user",
        "ctrlrun.environment",
        "ctrlrun.decision",
        "ctrlrun.decision_reason",
        "ctrlrun.effect_key",
        "ctrlrun.attempt",
        "ctrlrun.result",
        "ctrlrun.receipt_id",
    ):
        assert name in attributes, name
    assert attributes["ctrlrun.action.name"] == "stripe.refund"
    assert attributes["ctrlrun.principal.agent"] == "refund-agent"
    assert attributes["ctrlrun.principal.user"] == "alice"
    assert attributes["ctrlrun.effect_key"] == "refund:txn_1"
    assert attributes["ctrlrun.result"] == "committed"
    assert attributes["ctrlrun.attempt"] == 1


def test_T29_every_event_becomes_a_span_event_named_by_its_type(store, exporter):
    export, provider = exporter
    with context(agent="refund-agent"):
        _refund(_control(store, provider))(payment_id="txn_1", amount=200)

    names = [event.name for event in _spans(export)[0].events]
    assert names == [str(event.type) for event in store.events()]


def test_T29_a_span_events_data_is_flattened_to_ctrlrun_attributes(store, exporter):
    export, provider = exporter
    with context(agent="refund-agent"):
        _refund(_control(store, provider))(payment_id="txn_1", amount=200)

    policy_evaluated = next(
        event for event in _spans(export)[0].events if event.name == "POLICY_EVALUATED"
    )
    assert dict(policy_evaluated.attributes)["ctrlrun.decision"] == "allow"


def test_T29_the_approval_attributes_appear_when_there_was_one(store, exporter):
    export, provider = exporter
    control = _control(store, provider)
    refund = _refund(control)
    with context(agent="refund-agent"), pytest.raises(ApprovalRequired) as pending:
        refund(payment_id="txn_2", amount=2000)
    store.grant_approval(pending.value.request_id, "cli:local")
    from ctrlrun import with_approval

    with context(agent="refund-agent"), with_approval(pending.value.request_id):
        refund(payment_id="txn_2", amount=2000)

    attributes = _attrs(_spans(export)[-1])
    assert attributes["ctrlrun.approval_id"] == pending.value.request_id
    assert attributes["ctrlrun.approver"] == "cli:local"


# --- T29: argument values are not exported unless asked for ----------------------------


def test_T29_argument_values_are_not_attributes_by_default(store, exporter):
    """Arguments carry customer identifiers and amounts, and a trace backend is not the
    receipt store. The default is the fail-closed one."""
    export, provider = exporter
    with context(agent="refund-agent"):
        _refund(_control(store, provider))(payment_id="txn_secret", amount=200)

    attributes = _attrs(_spans(export)[0])
    assert not [name for name in attributes if name.startswith("ctrlrun.argument.")]
    assert attributes.get("ctrlrun.argument.payment_id") is None


def test_T29_argument_values_appear_only_when_the_option_is_set(store, exporter):
    export, provider = exporter
    with context(agent="refund-agent"):
        _refund(_control(store, provider, arguments=True))(payment_id="txn_secret", amount=200)

    attributes = _attrs(_spans(export)[0])
    assert attributes["ctrlrun.argument.payment_id"] == "txn_secret"
    assert attributes["ctrlrun.argument.amount"] == 200


def test_the_effect_key_carries_argument_text_even_when_arguments_are_withheld(store, exporter):
    """The one tension in §8, asserted rather than left to be discovered.

    Argument values are withheld by default, and `ctrlrun.effect_key` is exported always —
    but an effect key is *built* from arguments, so `refund:{payment_id}` puts a payment id
    in every backend receiving these spans. That is intended: §8 names the effect key as what
    correlates a retry, and a correlation key nobody can see correlates nothing. It makes the
    choice of effect template a choice about what leaves the building.
    """
    export, provider = exporter
    with context(agent="refund-agent"):
        _refund(_control(store, provider))(payment_id="txn_1", amount=200)

    assert _attrs(_spans(export)[0])["ctrlrun.effect_key"] == "refund:txn_1"


# --- T29: span status ------------------------------------------------------------------


def test_T29_status_is_ok_for_committed(store, exporter):
    export, provider = exporter
    with context(agent="refund-agent"):
        _refund(_control(store, provider))(payment_id="txn_1", amount=200)

    assert _spans(export)[0].status.status_code is trace.StatusCode.OK


def test_T29_status_is_error_for_ambiguous(store, exporter):
    export, provider = exporter
    control = _control(store, provider)

    def lose_the_response():
        raise TimeoutError("response lost")

    with context(agent="refund-agent"), pytest.raises(TimeoutError):
        _refund(control, lose_the_response)(payment_id="txn_1", amount=200)

    assert _spans(export)[0].status.status_code is trace.StatusCode.ERROR


def test_T29_status_is_error_for_failed(store, exporter):
    export, provider = exporter
    control = _control(store, provider)

    def nothing_happened():
        raise NotExecuted("rejected before any side effect")

    with context(agent="refund-agent"), pytest.raises(NotExecuted):
        _refund(control, nothing_happened)(payment_id="txn_1", amount=200)

    assert _spans(export)[0].status.status_code is trace.StatusCode.ERROR


def test_T29_status_is_unset_for_denied(store, exporter):
    """A refusal is the system working as designed. Marking it an error teaches an on-call
    rotation to ignore the signal that matters."""
    export, provider = exporter
    from ctrlrun import ActionDenied

    with context(agent="refund-agent"), pytest.raises(ActionDenied):
        _refund(_control(store, provider))(payment_id="txn_1", amount=999999)

    assert _spans(export)[0].status.status_code is trace.StatusCode.UNSET


def test_T29_status_is_unset_for_blocked(store, exporter):
    export, provider = exporter
    from ctrlrun import AmbiguousEffect

    control = _control(store, provider)

    def lose_the_response():
        raise TimeoutError("response lost")

    with context(agent="refund-agent"), pytest.raises(TimeoutError):
        _refund(control, lose_the_response)(payment_id="txn_1", amount=200)
    with context(agent="refund-agent"), pytest.raises(AmbiguousEffect):
        _refund(control)(payment_id="txn_1", amount=200)

    blocked = _spans(export)[-1]
    assert _attrs(blocked)["ctrlrun.result"] == "blocked"
    assert blocked.status.status_code is trace.StatusCode.UNSET


# --- T29: a failing exporter cannot affect an action -----------------------------------


def test_T29_an_exporter_that_raises_does_not_affect_the_action(store, caplog):
    """T17's rule applied to this sink (§4.2)."""

    class Exploding(InMemorySpanExporter):
        def export(self, spans):
            raise RuntimeError("the collector is down")

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(Exploding()))
    control = _control(store, provider)

    with caplog.at_level("WARNING", logger="ctrlrun"), context(agent="refund-agent"):
        assert _refund(control)(payment_id="txn_1", amount=200) == "re_1"

    assert store.receipts()[-1].result.value == "committed"


def test_a_sink_with_no_configured_provider_is_free(store):
    """With no tracer provider the API's no-op one makes this cost nothing (§8)."""
    control = Control(Policy.from_yaml(POLICY), store, sinks=[OTelEventSink()])

    with context(agent="refund-agent"):
        assert _refund(control)(payment_id="txn_1", amount=200) == "re_1"


# --- §8: a retry is a new span, correlated by the effect key ---------------------------


def test_a_retry_is_a_new_span_correlated_by_the_effect_key(store, exporter):
    export, provider = exporter
    control = _control(store, provider)
    attempts = {"n": 0}

    def fails_then_works():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise NotExecuted("nothing ran")
        return "re_1"

    refund = _refund(control, fails_then_works)
    with context(agent="refund-agent"), pytest.raises(NotExecuted):
        refund(payment_id="txn_1", amount=200)
    with context(agent="refund-agent"):
        refund(payment_id="txn_1", amount=200)

    spans = _spans(export)
    assert len(spans) == 2
    assert {_attrs(span)["ctrlrun.effect_key"] for span in spans} == {"refund:txn_1"}
    assert _attrs(spans[0])["ctrlrun.action_id"] != _attrs(spans[1])["ctrlrun.action_id"]
    assert [_attrs(span)["ctrlrun.attempt"] for span in spans] == [1, 2]


def test_an_action_awaiting_a_human_leaves_its_span_open(store, exporter):
    """Stated, not solved (§8): the action has not reached a terminal state, so there is no
    receipt and nothing to end the span with."""
    export, provider = exporter
    with context(agent="refund-agent"), pytest.raises(ApprovalRequired):
        _refund(_control(store, provider))(payment_id="txn_2", amount=2000)

    assert _spans(export) == ()


def test_the_open_span_map_is_bounded(store, exporter):
    """A long-running gateway must not leak a span per abandoned approval."""
    _, provider = exporter
    sink = OTelEventSink(tracer_provider=provider, max_open_spans=4)
    control = Control(Policy.from_yaml(POLICY), store, sinks=[sink])
    refund = _refund(control)

    for index in range(10):
        with context(agent="refund-agent"), pytest.raises(ApprovalRequired):
            refund(payment_id=f"txn_{index}", amount=2000)

    assert sink.open_spans <= 4


def test_an_event_for_an_action_this_sink_never_saw_start_opens_no_span(store, exporter):
    """Every action opens with ACTION_PROPOSED (v0.1 §6.2). An event for one this sink never
    saw belongs to a span somebody else owns — a process that restarted, or a `ctrlrun
    resolve` run days later — and inventing one would export a span with no beginning."""
    from datetime import UTC, datetime

    from ctrlrun.receipt import Event, EventType

    export, provider = exporter
    sink = OTelEventSink(tracer_provider=provider)

    sink.on_event(
        Event(
            type=EventType.EFFECT_RESOLVED,
            action_id="act_from_another_process",
            ts=datetime.now(UTC),
            data={"state": "failed", "resolved_by": "human"},
        )
    )

    assert sink.open_spans == 0
    assert _spans(export) == ()


def test_an_evicted_span_is_dropped_rather_than_ended(store, exporter):
    """Ending one would export a span asserting an outcome that never happened."""
    export, provider = exporter
    sink = OTelEventSink(tracer_provider=provider, max_open_spans=2)
    control = Control(Policy.from_yaml(POLICY), store, sinks=[sink])
    refund = _refund(control)

    for index in range(6):
        with context(agent="refund-agent"), pytest.raises(ApprovalRequired):
            refund(payment_id=f"txn_{index}", amount=2000)

    assert sink.open_spans <= 2
    assert _spans(export) == (), "an evicted span must never reach the exporter"


def test_a_receipt_ends_only_its_own_action_span(store, exporter):
    """One action commits while another is suspended awaiting a human. The receipt must end
    the span it names and leave the other open, not the nearest one to hand."""
    export, provider = exporter
    control = _control(store, provider)
    refund = _refund(control)

    with context(agent="refund-agent"), pytest.raises(ApprovalRequired):
        refund(payment_id="txn_waiting", amount=2000)
    with context(agent="refund-agent"):
        refund(payment_id="txn_done", amount=200)

    spans = _spans(export)
    assert len(spans) == 1
    assert _attrs(spans[0])["ctrlrun.effect_key"] == "refund:txn_done"


def test_a_receipt_whose_span_was_evicted_ends_nobody_elses(store, exporter):
    """The window eviction opens, and the one that matters.

    A suspended action is the only way a receipt arrives long after its `ACTION_PROPOSED`
    under the *same* action_id (§6.9): `Control.resume` writes one for the action that
    suspended. If its span was evicted meanwhile, that receipt has no span to end — and
    ending "the nearest span to hand" would close a live action's span and stamp it with a
    different action's outcome. A wrong trace is worse than a missing one.
    """
    from ctrlrun import Suspended

    export, provider = exporter
    sink = OTelEventSink(tracer_provider=provider, max_open_spans=1)
    control = Control(Policy.from_yaml(POLICY), store, sinks=[sink])

    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def suspending(payment_id: str, amount: int) -> str:
        raise Suspended("held-continuation")

    with context(agent="refund-agent"), pytest.raises(Suspended):
        suspending(payment_id="txn_suspended", amount=200)

    # A second action's ACTION_PROPOSED evicts the suspended action's span.
    with context(agent="refund-agent"), pytest.raises(ApprovalRequired):
        _refund(control)(payment_id="txn_holds_the_slot", amount=2000)

    # The suspended action now finishes, and its receipt carries the original action_id.
    control.resume("held-continuation", lambda: "re_1")

    assert _spans(export) == (), "the evicted action had no span, and took nobody else's"
    assert sink.open_spans == 1


def test_an_argument_value_otel_cannot_carry_is_dropped_not_stringified(store, exporter):
    """An attribute whose type changed on the way out is worse than one that is absent: a
    query written against it silently stops matching."""
    export, provider = exporter
    control = Control(
        Policy.from_yaml(POLICY),
        store,
        sinks=[OTelEventSink(tracer_provider=provider, arguments=True)],
    )

    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def refund(payment_id: str, amount: int, tags: list) -> str:
        return "re_1"

    with context(agent="refund-agent"):
        refund(payment_id="txn_1", amount=200, tags=["a", "b"])

    attributes = _attrs(_spans(export)[0])
    assert attributes["ctrlrun.argument.payment_id"] == "txn_1"
    assert "ctrlrun.argument.tags" not in attributes


# --- T30: the extra says so when it is absent ------------------------------------------


def test_T30_a_missing_otel_extra_raises_MissingDependency(monkeypatch):
    import ctrlrun.otel as otel

    monkeypatch.setattr(otel, "_OTEL_MODULE", "no_such_module_at_all")

    with pytest.raises(MissingDependency) as raised:
        OTelEventSink()

    assert "pip install 'ctrlrun[otel]'" in str(raised.value)
    assert not isinstance(raised.value, ImportError)
