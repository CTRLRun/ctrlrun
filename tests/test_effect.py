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
    Control,
    CTRLRunError,
    Decision,
    EffectKeyError,
    InMemoryStateStore,
    InvalidArgument,
    Policy,
    Principal,
    context,
    protect,
)
from ctrlrun.effect import UNRESOLVED_EFFECT, resolve_effect_key, resolve_resource
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
