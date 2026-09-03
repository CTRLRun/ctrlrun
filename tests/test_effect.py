"""Effect key templating. Build-list item 5; SPEC-v0.1 §5.1.

The effect key is the identity of a *logical effect*, the thing duplicate protection is
built on (§5.3, item 6). These tests pin how a template becomes one, and what happens when
it cannot: never a silent `None`.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ctrlrun import (
    Action,
    AmbiguousEffect,
    Control,
    CTRLRunError,
    Decision,
    DuplicateEffect,
    EffectKeyError,
    InMemoryStateStore,
    InvalidArgument,
    NotExecuted,
    Policy,
    Principal,
    context,
    protect,
)
from ctrlrun.effect import (
    DEFAULT_LEASE,
    UNRESOLVED_EFFECT,
    EffectState,
    resolve_effect_key,
    resolve_resource,
)
from ctrlrun.effect import template_placeholders as placeholders
from ctrlrun.receipt import EventType, ReceiptResult

POLICY = """
schema: ctrlrun.policy/v1
actions:
  customer.read:
    decision: allow
  stripe.refund:
    decision: allow
"""


class _Clock:
    """A monotonic fake clock, so receipts have deterministic, ordered timestamps."""

    def __init__(self) -> None:
        self.now = datetime(2026, 9, 3, 10, 12, 1, 120000, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(milliseconds=10)
        return self.now


class _ReservationSpy(InMemoryStateStore):
    """A store that records — and refuses — any attempt to reserve an effect.

    Reservation is build-list item 6, so nothing may call it yet; refusing keeps this double
    at least as strict as the real store. Once item 6 lands, the spy still proves that an
    action with no `effect=` reserves nothing (SPEC-v0.1 §5.1).
    """

    def __init__(self) -> None:
        super().__init__()
        self.reservations: list[tuple[str, str]] = []

    def reserve_effect(self, effect_key: str, action_id: str, lease: Any = None) -> Any:
        self.reservations.append((effect_key, action_id))
        raise NotImplementedError("effect reservation is build-list item 6")


@pytest.fixture
def store():
    return InMemoryStateStore()


@pytest.fixture
def control(store):
    return Control(Policy.from_yaml(POLICY), store, clock=_Clock())


def _action(**overrides: Any) -> Action:
    fields: dict[str, Any] = {
        "name": "stripe.refund",
        "arguments": {"payment_id": "txn_8231", "amount": 2000},
        "principal": Principal(agent="refund-agent"),
        "resource": "payment:txn_8231",
    }
    fields.update(overrides)
    return Action(**fields)


def _event_types(store):
    return [event.type for event in store.events()]


# --- the resolver: arguments (SPEC §5.1) ----------------------------------------------


def test_a_template_resolves_a_placeholder_from_the_arguments():
    assert resolve_effect_key("refund:{payment_id}", _action()) == "refund:txn_8231"


def test_a_template_with_no_placeholder_is_its_own_key():
    assert resolve_effect_key("refund:nightly", _action()) == "refund:nightly"


def test_a_template_resolves_every_placeholder():
    key = resolve_effect_key("refund:{payment_id}:{amount}", _action())
    assert key == "refund:txn_8231:2000"


def test_a_placeholder_may_repeat():
    assert resolve_effect_key("{payment_id}/{payment_id}", _action()) == "txn_8231/txn_8231"


def test_an_int_argument_renders_as_digits():
    assert resolve_effect_key("refund:{amount}", _action()) == "refund:2000"


def test_the_same_arguments_in_a_different_order_resolve_to_the_same_key():
    first = _action(arguments={"payment_id": "txn_1", "amount": 2000})
    second = _action(arguments={"amount": 2000, "payment_id": "txn_1"})
    assert resolve_effect_key("refund:{payment_id}:{amount}", first) == resolve_effect_key(
        "refund:{payment_id}:{amount}", second
    )


# --- the resolver: {resource} (SPEC §5.1) ---------------------------------------------


def test_the_resource_placeholder_resolves_from_the_resource_field():
    assert resolve_effect_key("{resource}", _action()) == "payment:txn_8231"


def test_the_resource_placeholder_composes_with_literals_and_arguments():
    key = resolve_effect_key("refund:{resource}:{amount}", _action())
    assert key == "refund:payment:txn_8231:2000"


def test_the_resource_placeholder_without_a_resource_raises_EffectKeyError():
    with pytest.raises(EffectKeyError):
        resolve_effect_key("refund:{resource}", _action(resource=None))


def test_an_argument_named_resource_makes_the_resource_placeholder_ambiguous():
    # SPEC: §5.1 — the fail-closed reading. Two candidate values, one key: refuse rather
    # than silently pick, because duplicate protection depends on which one it is.
    ambiguous = _action(arguments={"resource": "payment:txn_1"})
    with pytest.raises(EffectKeyError):
        resolve_effect_key("refund:{resource}", ambiguous)


def test_an_argument_named_resource_is_harmless_when_the_template_ignores_it():
    action = _action(arguments={"resource": "payment:txn_1", "payment_id": "txn_8231"})
    assert resolve_effect_key("refund:{payment_id}", action) == "refund:txn_8231"


# --- the resolver: refusals (SPEC §5.1) -----------------------------------------------


def test_a_missing_placeholder_raises_EffectKeyError_never_a_silent_none():
    with pytest.raises(EffectKeyError) as excinfo:
        resolve_effect_key("refund:{invoice_id}", _action())

    assert "invoice_id" in str(excinfo.value)


@pytest.mark.parametrize(
    "value",
    [None, True, False, [], ["txn_1"], {"id": "txn_1"}, ""],
    ids=["none", "true", "false", "empty-list", "list", "dict", "empty-string"],
)
def test_a_placeholder_that_is_not_a_non_empty_scalar_raises_EffectKeyError(value):
    # SPEC: §5.1 — an effect key is an identity. None and an empty string identify nothing
    # and would collide across unrelated actions; a container has no stable rendering.
    with pytest.raises(EffectKeyError):
        resolve_effect_key("refund:{payment_id}", _action(arguments={"payment_id": value}))


def test_EffectKeyError_is_a_ctrlrun_error():
    assert issubclass(EffectKeyError, CTRLRunError)


# --- template syntax (SPEC §5.1) ------------------------------------------------------


def test_placeholders_are_reported_in_order():
    assert placeholders("refund:{payment_id}:{amount}") == ("payment_id", "amount")


def test_a_template_without_placeholders_has_none():
    assert placeholders("refund:nightly") == ()


def test_a_placeholder_may_name_any_identifier_a_python_parameter_can_have():
    action = _action(arguments={"pagó_id": "txn_1", "_private": 2})
    assert placeholders("refund:{pagó_id}:{_private}") == ("pagó_id", "_private")
    assert resolve_effect_key("refund:{pagó_id}:{_private}", action) == "refund:txn_1:2"


@pytest.mark.parametrize(
    "template",
    [
        "refund:{",
        "refund:}",
        "refund:{}",
        "refund:{payment_id",
        "refund:{payment.id}",
        "refund:{payment_id[0]}",
        "refund:{payment_id!r}",
        "refund:{payment_id:>10}",
        "refund:{ payment_id }",
        "refund:{{payment_id}}",
        "refund:{1st}",
    ],
)
def test_a_malformed_template_raises_InvalidArgument(template):
    # SPEC: §5.1 — a template is literal text and `{name}` placeholders, nothing else. No
    # escapes, no format specs, no attribute or index access: a brace that is not a
    # placeholder is a typo, and a typo must not become part of an effect identity.
    with pytest.raises(InvalidArgument):
        placeholders(template)


def test_an_empty_template_raises_InvalidArgument():
    with pytest.raises(InvalidArgument):
        placeholders("")


# --- resource templates resolve before the Action exists (SPEC §5.1) ------------------


def test_a_resource_template_resolves_against_the_bound_arguments():
    assert resolve_resource("payment:{payment_id}", {"payment_id": "txn_1"}) == "payment:txn_1"


def test_a_resource_template_with_a_missing_placeholder_raises_InvalidArgument():
    # SPEC: §5.1 — `resource` is part of the canonical form and therefore of the action
    # hash (§2.2), so it fails at construction time, like any other invalid field.
    with pytest.raises(InvalidArgument):
        resolve_resource("payment:{payment_id}", {"amount": 2000})


def test_a_literal_resource_is_its_own_value():
    assert resolve_resource("payment:txn_1", {}) == "payment:txn_1"


# --- @protect wires the template through Control.execute (SPEC §5.1, §8) --------------


def test_the_resolved_effect_key_reaches_the_receipt(control, store):
    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def refund(payment_id: str, amount: int) -> str:
        return "re_1"

    with context(agent="refund-agent"):
        assert refund(payment_id="txn_1", amount=200) == "re_1"

    (receipt,) = store.receipts()
    assert receipt.effect_key == "refund:txn_1"
    assert receipt.result == ReceiptResult.COMMITTED
    assert json.loads(receipt.to_json())["effect_key"] == "refund:txn_1"


def test_the_effect_key_reaches_the_events(control, store):
    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def refund(payment_id: str) -> None: ...

    with context(agent="refund-agent"):
        refund(payment_id="txn_1")

    assert {event.effect_key for event in store.events()} == {"refund:txn_1"}


def test_a_defaulted_argument_resolves_the_template(control, store):
    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def refund(payment_id: str = "txn_default") -> None: ...

    with context(agent="refund-agent"):
        refund()

    (receipt,) = store.receipts()
    assert receipt.effect_key == "refund:txn_default"


def test_a_resource_template_resolves_into_the_action_and_the_effect_key(control, store):
    @protect(
        "stripe.refund",
        resource="payment:{payment_id}",
        effect="refund:{resource}",
        control=control,
    )
    def refund(payment_id: str) -> None: ...

    with context(agent="refund-agent"):
        refund(payment_id="txn_1")

    (receipt,) = store.receipts()
    assert receipt.resource == "payment:txn_1"
    assert receipt.effect_key == "refund:payment:txn_1"


def test_a_resource_template_changes_the_action_hash(control, store):
    @protect("stripe.refund", resource="payment:{payment_id}", control=control)
    def refund(payment_id: str) -> None: ...

    with context(agent="refund-agent"):
        refund(payment_id="txn_1")
        refund(payment_id="txn_2")

    first, second = store.receipts()
    assert first.action_hash != second.action_hash


def test_a_missing_resource_placeholder_records_nothing(control, store):
    @protect("stripe.refund", resource="payment:{invoice_id}", control=control)
    def refund(payment_id: str) -> None:
        raise AssertionError("executor must not run when the resource cannot be resolved")

    with context(agent="refund-agent"), pytest.raises(InvalidArgument):
        refund(payment_id="txn_1")

    assert store.receipts() == ()
    assert store.events() == ()


# --- a missing placeholder denies the action (SPEC §5.1, §6.1) ------------------------


def test_a_missing_placeholder_denies_the_action_and_does_not_execute(control):
    @protect("stripe.refund", effect="refund:{invoice_id}", control=control)
    def refund(payment_id: str) -> None:
        raise AssertionError("executor must not run without an effect key")

    with context(agent="refund-agent"), pytest.raises(EffectKeyError):
        refund(payment_id="txn_1")


def test_a_missing_placeholder_writes_a_denied_receipt(control, store):
    @protect("stripe.refund", effect="refund:{invoice_id}", control=control)
    def refund(payment_id: str) -> None: ...

    with context(agent="refund-agent"), pytest.raises(EffectKeyError):
        refund(payment_id="txn_1")

    (receipt,) = store.receipts()
    assert receipt.result == ReceiptResult.DENIED
    assert receipt.decision == Decision.DENY
    assert receipt.decision_reason == UNRESOLVED_EFFECT
    assert receipt.effect_key is None
    assert receipt.action == "stripe.refund"
    assert receipt.arguments == {"payment_id": "txn_1"}


def test_a_missing_placeholder_denies_before_the_policy_is_consulted(control, store):
    @protect("stripe.refund", effect="refund:{invoice_id}", control=control)
    def refund(payment_id: str) -> None: ...

    with context(agent="refund-agent"), pytest.raises(EffectKeyError):
        refund(payment_id="txn_1")

    # An action whose logical effect cannot be identified cannot be protected against
    # duplication, whatever the policy would have said about it.
    assert _event_types(store) == [EventType.ACTION_PROPOSED, EventType.ACTION_DENIED]
    assert store.events()[-1].data["reason"] == UNRESOLVED_EFFECT


def test_an_unresolvable_effect_key_denies_an_action_the_policy_would_allow(control, store):
    @protect("customer.read", effect="read:{missing}", control=control)
    def read(customer_id: str) -> None:
        raise AssertionError("executor must not run without an effect key")

    with context(agent="support-agent"), pytest.raises(EffectKeyError):
        read(customer_id="cus_1")

    assert store.receipts()[0].result == ReceiptResult.DENIED


# --- no effect= means no effect (SPEC §5.1) -------------------------------------------


def test_no_effect_leaves_the_receipt_effect_key_null():
    store = _ReservationSpy()
    control = Control(Policy.from_yaml(POLICY), store, clock=_Clock())

    @protect("customer.read", control=control)
    def read(customer_id: str) -> None: ...

    with context(agent="support-agent"):
        read(customer_id="cus_1")

    (receipt,) = store.receipts()
    assert receipt.effect_key is None
    assert json.loads(receipt.to_json())["effect_key"] is None


def test_no_effect_attempts_no_reservation():
    store = _ReservationSpy()
    control = Control(Policy.from_yaml(POLICY), store, clock=_Clock())

    @protect("customer.read", control=control)
    def read(customer_id: str) -> None: ...

    with context(agent="support-agent"):
        read(customer_id="cus_1")

    assert store.reservations == []
    assert all(event.effect_key is None for event in store.events())


def test_no_effect_reserves_nothing_even_when_the_executor_is_ambiguous():
    store = _ReservationSpy()
    control = Control(Policy.from_yaml(POLICY), store, clock=_Clock())

    @protect("customer.read", control=control)
    def read(customer_id: str) -> None:
        raise TimeoutError("read timed out")

    with context(agent="support-agent"), pytest.raises(TimeoutError):
        read(customer_id="cus_1")

    assert store.reservations == []
    assert store.receipts()[0].effect_key is None


# --- templates are checked at decoration time (SPEC §5.1) -----------------------------


def test_a_malformed_effect_template_is_rejected_at_decoration_time(control):
    with pytest.raises(InvalidArgument):

        @protect("stripe.refund", effect="refund:{payment.id}", control=control)
        def refund(payment_id: str) -> None: ...


def test_a_malformed_resource_template_is_rejected_at_decoration_time(control):
    with pytest.raises(InvalidArgument):

        @protect("stripe.refund", resource="payment:{", control=control)
        def refund(payment_id: str) -> None: ...


def test_an_empty_effect_template_is_rejected_at_decoration_time(control):
    with pytest.raises(InvalidArgument):

        @protect("stripe.refund", effect="", control=control)
        def refund(payment_id: str) -> None: ...


def test_an_empty_effect_key_is_refused_by_execute(control):
    action = _action()
    with pytest.raises(InvalidArgument):
        control.execute(action, lambda: None, effect_key="")


# --- the executor still runs on canonical arguments (SPEC §2.2) -----------------------


def test_the_executor_receives_canonical_arguments_when_an_effect_is_declared(control):
    seen: dict[str, Any] = {}

    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def refund(payment_id: str, metadata: dict[str, Any]) -> None:
        seen.update(metadata)

    original = {"reason": "duplicate"}
    with context(agent="refund-agent"):
        refund(payment_id="txn_1", metadata=original)

    original["reason"] = "mutated"
    assert seen == {"reason": "duplicate"}


# --- reservation: a fresh key (SPEC §5.3) ---------------------------------------------


def test_reserving_a_fresh_key_returns_a_first_attempt(state_store, fake_clock):
    reservation = state_store.reserve_effect("refund:txn_1", "act_1", DEFAULT_LEASE)

    assert reservation.effect_key == "refund:txn_1"
    assert reservation.action_id == "act_1"
    assert reservation.attempt == 1
    assert reservation.lease_expires_at == fake_clock() + DEFAULT_LEASE


def test_reserving_a_fresh_key_writes_a_reserved_record(state_store, fake_clock):
    state_store.reserve_effect("refund:txn_1", "act_1", DEFAULT_LEASE)

    record = state_store.get_effect("refund:txn_1")
    assert record.state is EffectState.RESERVED
    assert record.action_id == "act_1"
    assert record.attempt == 1
    assert record.error is None
    assert record.created_at == fake_clock()
    assert record.lease_expires_at == fake_clock() + DEFAULT_LEASE


def test_get_effect_is_none_for_a_key_nobody_reserved(state_store):
    assert state_store.get_effect("refund:txn_1") is None


def test_effect_keys_do_not_collide(state_store):
    state_store.reserve_effect("refund:txn_1", "act_1", DEFAULT_LEASE)
    reservation = state_store.reserve_effect("refund:txn_2", "act_2", DEFAULT_LEASE)

    assert reservation.attempt == 1
    assert state_store.get_effect("refund:txn_1").action_id == "act_1"


def test_an_empty_effect_key_cannot_be_reserved(state_store):
    with pytest.raises(InvalidArgument):
        state_store.reserve_effect("", "act_1", DEFAULT_LEASE)


def test_an_empty_action_id_cannot_reserve(state_store):
    with pytest.raises(InvalidArgument):
        state_store.reserve_effect("refund:txn_1", "", DEFAULT_LEASE)


# --- reservation: the retry table (SPEC §5.4) -----------------------------------------


def _reserve_and_execute(store, effect_key="refund:txn_1", action_id="act_1"):
    """Take the key to EXECUTING, where an executor would be running."""
    store.reserve_effect(effect_key, action_id, DEFAULT_LEASE)
    store.begin_execution(effect_key, action_id)


def test_a_committed_effect_refuses_a_second_reservation(state_store):
    _reserve_and_execute(state_store)
    state_store.commit_effect("refund:txn_1", "act_1", {"id": "re_1"})

    with pytest.raises(DuplicateEffect) as excinfo:
        state_store.reserve_effect("refund:txn_1", "act_2", DEFAULT_LEASE)

    assert excinfo.value.state == "committed"
    assert state_store.get_effect("refund:txn_1").action_id == "act_1"


def test_an_ambiguous_effect_refuses_a_second_reservation(state_store):
    _reserve_and_execute(state_store)
    state_store.mark_ambiguous("refund:txn_1", "act_1", "TimeoutError: lost the response")

    with pytest.raises(AmbiguousEffect):
        state_store.reserve_effect("refund:txn_1", "act_2", DEFAULT_LEASE)

    assert state_store.get_effect("refund:txn_1").state is EffectState.AMBIGUOUS


def test_a_live_reserved_lease_refuses_a_second_reservation(state_store):
    state_store.reserve_effect("refund:txn_1", "act_1", DEFAULT_LEASE)

    with pytest.raises(DuplicateEffect) as excinfo:
        state_store.reserve_effect("refund:txn_1", "act_2", DEFAULT_LEASE)

    assert excinfo.value.state == "in_progress"
    assert excinfo.value.effect_key == "refund:txn_1"


def test_a_live_executing_lease_refuses_a_second_reservation(state_store):
    _reserve_and_execute(state_store)

    with pytest.raises(DuplicateEffect) as excinfo:
        state_store.reserve_effect("refund:txn_1", "act_2", DEFAULT_LEASE)

    assert excinfo.value.state == "in_progress"
    assert state_store.get_effect("refund:txn_1").state is EffectState.EXECUTING


def test_a_lease_that_is_still_live_at_its_last_second_refuses(state_store, fake_clock):
    state_store.reserve_effect("refund:txn_1", "act_1", DEFAULT_LEASE)
    fake_clock.advance(DEFAULT_LEASE)

    with pytest.raises(DuplicateEffect):
        state_store.reserve_effect("refund:txn_1", "act_2", DEFAULT_LEASE)


def test_an_expired_reserved_lease_becomes_ambiguous(state_store, fake_clock):
    state_store.reserve_effect("refund:txn_1", "act_1", DEFAULT_LEASE)
    fake_clock.advance(DEFAULT_LEASE + timedelta(seconds=1))

    with pytest.raises(AmbiguousEffect):
        state_store.reserve_effect("refund:txn_1", "act_2", DEFAULT_LEASE)

    record = state_store.get_effect("refund:txn_1")
    assert record.state is EffectState.AMBIGUOUS
    assert record.action_id == "act_1"


def test_an_expired_executing_lease_becomes_ambiguous(state_store, fake_clock):
    _reserve_and_execute(state_store)
    fake_clock.advance(DEFAULT_LEASE + timedelta(seconds=1))

    with pytest.raises(AmbiguousEffect):
        state_store.reserve_effect("refund:txn_1", "act_2", DEFAULT_LEASE)

    assert state_store.get_effect("refund:txn_1").state is EffectState.AMBIGUOUS


def test_an_expired_lease_is_never_silently_released(state_store, fake_clock):
    # SPEC §5.3 E3 — the worker may have died after the remote committed. A second attempt
    # is refused for good, not handed the key.
    state_store.reserve_effect("refund:txn_1", "act_1", DEFAULT_LEASE)
    fake_clock.advance(DEFAULT_LEASE * 10)

    with pytest.raises(AmbiguousEffect):
        state_store.reserve_effect("refund:txn_1", "act_2", DEFAULT_LEASE)
    with pytest.raises(AmbiguousEffect):
        state_store.reserve_effect("refund:txn_1", "act_3", DEFAULT_LEASE)


def test_a_failed_effect_permits_a_new_attempt(state_store):
    _reserve_and_execute(state_store)
    state_store.fail_effect("refund:txn_1", "act_1", "remote rejected the request")

    reservation = state_store.reserve_effect("refund:txn_1", "act_2", DEFAULT_LEASE)

    assert reservation.attempt == 2
    record = state_store.get_effect("refund:txn_1")
    assert record.state is EffectState.RESERVED
    assert record.action_id == "act_2"
    assert record.error is None


def test_repeated_failures_keep_counting_attempts(state_store):
    for attempt, action_id in enumerate(("act_1", "act_2", "act_3"), start=1):
        reservation = state_store.reserve_effect("refund:txn_1", action_id, DEFAULT_LEASE)
        assert reservation.attempt == attempt
        state_store.begin_execution("refund:txn_1", action_id)
        state_store.fail_effect("refund:txn_1", action_id, "no")

    assert state_store.get_effect("refund:txn_1").attempt == 3


def test_a_retry_keeps_the_effects_creation_time(state_store, fake_clock):
    created_at = fake_clock()
    _reserve_and_execute(state_store)
    state_store.fail_effect("refund:txn_1", "act_1", "no")
    fake_clock.advance(timedelta(minutes=1))

    state_store.reserve_effect("refund:txn_1", "act_2", DEFAULT_LEASE)

    record = state_store.get_effect("refund:txn_1")
    assert record.created_at == created_at
    assert record.updated_at == fake_clock()


def test_a_refused_reservation_leaves_the_record_untouched(state_store):
    state_store.reserve_effect("refund:txn_1", "act_1", DEFAULT_LEASE)
    before = state_store.get_effect("refund:txn_1")

    with pytest.raises(DuplicateEffect):
        state_store.reserve_effect("refund:txn_1", "act_2", DEFAULT_LEASE)

    assert state_store.get_effect("refund:txn_1") == before


# --- the outcome transitions (SPEC §5.2, §5.5) ----------------------------------------


def test_begin_execution_moves_a_reservation_to_executing(state_store):
    state_store.reserve_effect("refund:txn_1", "act_1", DEFAULT_LEASE)
    state_store.begin_execution("refund:txn_1", "act_1")

    assert state_store.get_effect("refund:txn_1").state is EffectState.EXECUTING


def test_commit_effect_records_the_result(state_store):
    _reserve_and_execute(state_store)
    state_store.commit_effect("refund:txn_1", "act_1", {"id": "re_1", "amount": 2000})

    record = state_store.get_effect("refund:txn_1")
    assert record.state is EffectState.COMMITTED
    assert record.result == {"id": "re_1", "amount": 2000}
    assert record.error is None
    assert record.lease_expires_at is None


def test_commit_effect_survives_a_result_that_is_not_json(state_store):
    _reserve_and_execute(state_store)
    state_store.commit_effect("refund:txn_1", "act_1", object())

    assert state_store.get_effect("refund:txn_1").state is EffectState.COMMITTED


def test_fail_effect_records_the_error(state_store):
    _reserve_and_execute(state_store)
    state_store.fail_effect("refund:txn_1", "act_1", "remote rejected the request")

    record = state_store.get_effect("refund:txn_1")
    assert record.state is EffectState.FAILED
    assert record.error == "remote rejected the request"


def test_mark_ambiguous_records_the_error(state_store):
    _reserve_and_execute(state_store)
    state_store.mark_ambiguous("refund:txn_1", "act_1", "TimeoutError: lost the response")

    record = state_store.get_effect("refund:txn_1")
    assert record.state is EffectState.AMBIGUOUS
    assert record.error == "TimeoutError: lost the response"


def test_mark_ambiguous_may_be_repeated(state_store):
    _reserve_and_execute(state_store)
    state_store.mark_ambiguous("refund:txn_1", "act_1", "TimeoutError: lost the response")
    state_store.mark_ambiguous("refund:txn_1", "act_1", "TimeoutError: lost the response")

    assert state_store.get_effect("refund:txn_1").state is EffectState.AMBIGUOUS


def test_a_reservation_may_be_marked_ambiguous_without_beginning_execution(state_store):
    state_store.reserve_effect("refund:txn_1", "act_1", DEFAULT_LEASE)
    state_store.mark_ambiguous("refund:txn_1", "act_1", "KeyboardInterrupt: ")

    assert state_store.get_effect("refund:txn_1").state is EffectState.AMBIGUOUS


def test_no_transition_is_possible_without_a_reservation(state_store):
    for transition in (
        lambda: state_store.begin_execution("refund:txn_1", "act_1"),
        lambda: state_store.commit_effect("refund:txn_1", "act_1", None),
        lambda: state_store.fail_effect("refund:txn_1", "act_1", "no"),
        lambda: state_store.mark_ambiguous("refund:txn_1", "act_1", "no"),
    ):
        with pytest.raises(InvalidArgument):
            transition()


def test_another_attempt_may_not_commit_a_live_reservation(state_store):
    _reserve_and_execute(state_store)

    with pytest.raises(DuplicateEffect):
        state_store.commit_effect("refund:txn_1", "act_2", {"id": "re_1"})

    assert state_store.get_effect("refund:txn_1").state is EffectState.EXECUTING


def test_a_committed_effect_cannot_be_committed_twice(state_store):
    _reserve_and_execute(state_store)
    state_store.commit_effect("refund:txn_1", "act_1", {"id": "re_1"})

    with pytest.raises(DuplicateEffect):
        state_store.commit_effect("refund:txn_1", "act_1", {"id": "re_2"})

    assert state_store.get_effect("refund:txn_1").result == {"id": "re_1"}


def test_an_ambiguous_record_refuses_a_late_commit(state_store, fake_clock):
    # The worker that lost its lease comes back to commit; another attempt has already
    # declared the effect AMBIGUOUS. Only a human resolves that (§5.2).
    _reserve_and_execute(state_store)
    fake_clock.advance(DEFAULT_LEASE + timedelta(seconds=1))
    with pytest.raises(AmbiguousEffect):
        state_store.reserve_effect("refund:txn_1", "act_2", DEFAULT_LEASE)

    with pytest.raises(AmbiguousEffect):
        state_store.commit_effect("refund:txn_1", "act_1", {"id": "re_1"})

    assert state_store.get_effect("refund:txn_1").state is EffectState.AMBIGUOUS


def test_a_failed_effect_cannot_be_committed_without_a_new_reservation(state_store):
    _reserve_and_execute(state_store)
    state_store.fail_effect("refund:txn_1", "act_1", "no")

    with pytest.raises(InvalidArgument):
        state_store.commit_effect("refund:txn_1", "act_1", {"id": "re_1"})


def test_begin_execution_is_refused_after_the_record_became_ambiguous(state_store, fake_clock):
    state_store.reserve_effect("refund:txn_1", "act_1", DEFAULT_LEASE)
    fake_clock.advance(DEFAULT_LEASE + timedelta(seconds=1))
    with pytest.raises(AmbiguousEffect):
        state_store.reserve_effect("refund:txn_1", "act_2", DEFAULT_LEASE)

    with pytest.raises(AmbiguousEffect):
        state_store.begin_execution("refund:txn_1", "act_1")


def test_get_effect_never_mutates_an_expired_record(state_store, fake_clock):
    # A read is a read: the AMBIGUOUS transition of §5.3 E3 belongs to the next attempt.
    state_store.reserve_effect("refund:txn_1", "act_1", DEFAULT_LEASE)
    fake_clock.advance(DEFAULT_LEASE * 2)

    record = state_store.get_effect("refund:txn_1")
    assert record.state is EffectState.RESERVED
    assert not record.lease_is_live(fake_clock())
    assert state_store.get_effect("refund:txn_1").state is EffectState.RESERVED


# --- Control reserves what it executes (SPEC §5.3, §6) --------------------------------


@pytest.fixture
def reserving_control(state_store, fake_clock):
    return Control(Policy.from_yaml(POLICY), state_store, clock=fake_clock)


def test_an_executed_action_commits_its_effect(reserving_control, state_store):
    @protect("stripe.refund", effect="refund:{payment_id}", control=reserving_control)
    def refund(payment_id: str, amount: int) -> str:
        return f"re_{payment_id}"

    with context(agent="refund-agent"):
        refund(payment_id="txn_1", amount=2000)

    record = state_store.get_effect("refund:txn_1")
    assert record.state is EffectState.COMMITTED
    assert record.result == "re_txn_1"
    assert _event_types(state_store) == [
        EventType.ACTION_PROPOSED,
        EventType.POLICY_EVALUATED,
        EventType.EFFECT_RESERVED,
        EventType.EXECUTION_STARTED,
        EventType.EXECUTION_COMMITTED,
    ]


def test_a_second_attempt_at_a_committed_effect_is_blocked(reserving_control, state_store):
    calls: list[str] = []

    @protect("stripe.refund", effect="refund:{payment_id}", control=reserving_control)
    def refund(payment_id: str, amount: int) -> str:
        calls.append(payment_id)
        return f"re_{payment_id}"

    with context(agent="refund-agent"):
        refund(payment_id="txn_1", amount=2000)
        with pytest.raises(DuplicateEffect):
            refund(payment_id="txn_1", amount=2000)

    assert calls == ["txn_1"]
    committed, blocked = state_store.receipts()
    assert committed.result is ReceiptResult.COMMITTED
    assert blocked.result is ReceiptResult.BLOCKED
    assert blocked.decision is Decision.ALLOW
    assert blocked.effect_key == "refund:txn_1"
    assert _event_types(state_store)[-1] == EventType.EFFECT_RESERVATION_REFUSED


def test_a_blocked_attempt_is_a_distinct_proposal_for_the_same_effect(
    reserving_control, state_store
):
    @protect("stripe.refund", effect="refund:{payment_id}", control=reserving_control)
    def refund(payment_id: str, amount: int) -> str:
        return f"re_{payment_id}"

    with context(agent="refund-agent"):
        refund(payment_id="txn_1", amount=2000)
        with pytest.raises(DuplicateEffect):
            refund(payment_id="txn_1", amount=2000)

    committed, blocked = state_store.receipts()
    assert committed.action_id != blocked.action_id
    assert committed.action_hash == blocked.action_hash


def test_an_ambiguous_execution_leaves_an_ambiguous_effect(reserving_control, state_store):
    @protect("stripe.refund", effect="refund:{payment_id}", control=reserving_control)
    def refund(payment_id: str, amount: int) -> str:
        raise TimeoutError("lost the response")

    with context(agent="refund-agent"), pytest.raises(TimeoutError):
        refund(payment_id="txn_1", amount=2000)

    record = state_store.get_effect("refund:txn_1")
    assert record.state is EffectState.AMBIGUOUS
    assert "TimeoutError" in record.error
    assert state_store.receipts()[0].result is ReceiptResult.AMBIGUOUS


def test_a_not_executed_execution_leaves_a_failed_effect(reserving_control, state_store):
    @protect("stripe.refund", effect="refund:{payment_id}", control=reserving_control)
    def refund(payment_id: str, amount: int) -> str:
        raise NotExecuted("the remote rejected the request")

    with context(agent="refund-agent"), pytest.raises(NotExecuted):
        refund(payment_id="txn_1", amount=2000)

    assert state_store.get_effect("refund:txn_1").state is EffectState.FAILED


def test_the_receipt_attempt_counts_reservations(reserving_control, state_store):
    attempts: list[int] = []

    @protect("stripe.refund", effect="refund:{payment_id}", control=reserving_control)
    def refund(payment_id: str, amount: int) -> str:
        attempts.append(len(attempts) + 1)
        if len(attempts) == 1:
            raise NotExecuted("the remote rejected the request")
        return f"re_{payment_id}"

    with context(agent="refund-agent"):
        with pytest.raises(NotExecuted):
            refund(payment_id="txn_1", amount=2000)
        refund(payment_id="txn_1", amount=2000)

    failed, committed = state_store.receipts()
    assert failed.attempt == 1
    assert committed.attempt == 2
    assert state_store.get_effect("refund:txn_1").attempt == 2


def test_an_action_with_no_effect_writes_no_effect_record(reserving_control, state_store):
    @protect("customer.read", control=reserving_control)
    def read(customer_id: str) -> str:
        return customer_id

    with context(agent="support-agent"):
        read(customer_id="cus_1")
        read(customer_id="cus_1")

    assert state_store.get_effect("read:cus_1") is None
    assert len(state_store.receipts()) == 2


def test_an_effect_taken_over_before_execution_blocks_the_action(
    reserving_control, state_store, fake_clock
):
    """The reservation is won, then lost: the worker stalls past its lease and is overtaken.

    Everything here is a real store operation — the second attempt is what declares the
    effect AMBIGUOUS (§5.3 E3). The first must not execute on a key it no longer holds.
    """
    began = state_store.begin_execution

    def overtaken(effect_key: str, action_id: str) -> None:
        fake_clock.advance(DEFAULT_LEASE + timedelta(seconds=1))
        with pytest.raises(AmbiguousEffect):
            state_store.reserve_effect(effect_key, "act_overtaking", DEFAULT_LEASE)
        began(effect_key, action_id)

    state_store.begin_execution = overtaken

    @protect("stripe.refund", effect="refund:{payment_id}", control=reserving_control)
    def refund(payment_id: str, amount: int) -> str:
        raise AssertionError("the executor must not run on a lost reservation")

    with context(agent="refund-agent"), pytest.raises(AmbiguousEffect):
        refund(payment_id="txn_1", amount=2000)

    (receipt,) = state_store.receipts()
    assert receipt.result is ReceiptResult.BLOCKED
    assert _event_types(state_store)[-1] == EventType.EFFECT_RESERVATION_REFUSED
    assert state_store.get_effect("refund:txn_1").state is EffectState.AMBIGUOUS
