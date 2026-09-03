"""Control, @protect and context(). SPEC-v0.1 §2.2, §6, §8; acceptance test T6."""

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ctrlrun import (
    Action,
    ActionDenied,
    ApprovalRequired,
    Control,
    Decision,
    DuplicateEffect,
    Event,
    InMemoryStateStore,
    InvalidArgument,
    NotExecuted,
    Policy,
    PolicyError,
    Principal,
    Receipt,
    context,
    protect,
)
from ctrlrun import control as control_module
from ctrlrun.receipt import RECEIPT_SCHEMA, EventType, ReceiptResult

POLICY = """
schema: ctrlrun.policy/v1
actions:
  customer.read:
    decision: allow
  iam.grant_admin:
    decision: deny
  stripe.refund:
    rules:
      - when: { amount_lte: 500 }
        decision: allow
      - when: { amount_lte: 5000 }
        decision: approve
      - decision: deny
"""


class _Clock:
    """A monotonic fake clock, so receipts have deterministic, ordered timestamps."""

    def __init__(self) -> None:
        self.now = datetime(2026, 9, 3, 10, 12, 1, 120000, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(milliseconds=10)
        return self.now


@pytest.fixture
def store():
    return InMemoryStateStore()


@pytest.fixture
def control(store):
    return Control(Policy.from_yaml(POLICY), store, clock=_Clock())


def _event_types(store):
    return [event.type for event in store.events()]


# --- T6: unknown action fails closed (SPEC §3.4, §6.1) --------------------------------


def test_T6_unknown_action_raises_ActionDenied_with_reason_unknown_action(control):
    @protect("foo.bar", control=control)
    def foo(x: int) -> str:
        raise AssertionError("executor must not run for a denied action")

    with context(agent="refund-agent"), pytest.raises(ActionDenied) as excinfo:
        foo(1)

    assert excinfo.value.reason == "unknown_action"


def test_T6_unknown_action_writes_a_denied_receipt(control, store):
    @protect("foo.bar", control=control)
    def foo(x: int) -> str:
        raise AssertionError("executor must not run for a denied action")

    with context(agent="refund-agent"), pytest.raises(ActionDenied):
        foo(1)

    (receipt,) = store.receipts()
    assert receipt.action == "foo.bar"
    assert receipt.decision == Decision.DENY
    assert receipt.decision_reason == "unknown_action"
    assert receipt.result == ReceiptResult.DENIED
    assert receipt.arguments == {"x": 1}


def test_T6_unknown_action_appends_an_ACTION_DENIED_event(control, store):
    @protect("foo.bar", control=control)
    def foo() -> None: ...

    with context(agent="refund-agent"), pytest.raises(ActionDenied):
        foo()

    assert _event_types(store) == [
        EventType.ACTION_PROPOSED,
        EventType.POLICY_EVALUATED,
        EventType.ACTION_DENIED,
    ]


def test_a_policy_deny_is_denied_the_same_way_as_an_unknown_action(control, store):
    @protect("iam.grant_admin", control=control)
    def grant(user: str) -> None:
        raise AssertionError("executor must not run for a denied action")

    with context(agent="refund-agent"), pytest.raises(ActionDenied) as excinfo:
        grant(user="mallory")

    assert excinfo.value.reason == "decision"
    (receipt,) = store.receipts()
    assert receipt.result == ReceiptResult.DENIED


# --- the ALLOW path (SPEC §2.2, §6.1) -------------------------------------------------


def test_allow_executes_the_wrapped_function_exactly_once_and_returns_its_value(control):
    calls: list[str] = []

    @protect("customer.read", control=control)
    def read(customer_id: str) -> str:
        calls.append(customer_id)
        return f"customer:{customer_id}"

    with context(agent="support-agent"):
        result = read("cus_1")

    assert result == "customer:cus_1"
    assert calls == ["cus_1"]


def test_allow_writes_a_committed_receipt_and_the_expected_events(control, store):
    @protect("customer.read", control=control)
    def read(customer_id: str) -> str:
        return customer_id

    with context(agent="support-agent"):
        read("cus_1")

    (receipt,) = store.receipts()
    assert receipt.decision == Decision.ALLOW
    assert receipt.result == ReceiptResult.COMMITTED
    assert receipt.error is None
    assert _event_types(store) == [
        EventType.ACTION_PROPOSED,
        EventType.POLICY_EVALUATED,
        EventType.EXECUTION_STARTED,
        EventType.EXECUTION_COMMITTED,
    ]


def test_events_carry_the_action_id_and_a_monotonic_event_id(control, store):
    @protect("customer.read", control=control)
    def read() -> None: ...

    with context(agent="support-agent"):
        read()

    (receipt,) = store.receipts()
    assert [event.action_id for event in store.events()] == [receipt.action_id] * 4
    assert [event.event_id for event in store.events()] == [1, 2, 3, 4]


def test_a_returning_executor_never_reaches_the_ambiguous_path(control, store):
    @protect("customer.read", control=control)
    def read() -> None:
        return None

    with context(agent="support-agent"):
        read()

    assert EventType.EXECUTION_AMBIGUOUS not in _event_types(store)


# --- signature binding (SPEC §2.1) ----------------------------------------------------


def test_positional_and_keyword_arguments_map_to_the_same_action_arguments(control, store):
    @protect("customer.read", control=control)
    def read(customer_id: str, include_archived: bool = False) -> None: ...

    with context(agent="support-agent"):
        read("cus_1", True)
        read(customer_id="cus_1", include_archived=True)

    positional, keyword = store.receipts()
    assert positional.arguments == {"customer_id": "cus_1", "include_archived": True}
    assert positional.arguments == keyword.arguments
    assert positional.action_hash == keyword.action_hash


def test_default_arguments_are_part_of_the_action(control, store):
    @protect("customer.read", control=control)
    def read(customer_id: str, include_archived: bool = False) -> None: ...

    with context(agent="support-agent"):
        read("cus_1")

    (receipt,) = store.receipts()
    assert receipt.arguments == {"customer_id": "cus_1", "include_archived": False}


def test_keyword_only_and_positional_only_parameters_are_bound_and_called(control, store):
    seen: dict[str, Any] = {}

    @protect("customer.read", control=control)
    def read(customer_id: str, /, *, include_archived: bool = False) -> None:
        seen["customer_id"] = customer_id
        seen["include_archived"] = include_archived

    with context(agent="support-agent"):
        read("cus_1", include_archived=True)

    assert seen == {"customer_id": "cus_1", "include_archived": True}
    (receipt,) = store.receipts()
    assert receipt.arguments == {"customer_id": "cus_1", "include_archived": True}


def test_a_protected_function_may_not_take_var_positional_arguments():
    with pytest.raises(InvalidArgument):

        @protect("customer.read")
        def read(*args: str) -> None: ...


def test_a_protected_function_may_not_take_var_keyword_arguments():
    with pytest.raises(InvalidArgument):

        @protect("customer.read")
        def read(**kwargs: str) -> None: ...


def test_protect_requires_a_non_empty_action_name():
    with pytest.raises(InvalidArgument):
        protect("")


def test_a_float_argument_is_rejected_before_anything_executes(control, store):
    @protect("stripe.refund", control=control)
    def refund(amount: float) -> None:
        raise AssertionError("executor must not run for an uncanonicalizable action")

    with context(agent="refund-agent"), pytest.raises(InvalidArgument):
        refund(20.0)

    assert store.receipts() == ()


# --- the executor sees canonical arguments (SPEC §2.2) --------------------------------


def test_the_executor_is_called_with_canonical_arguments_not_the_callers_objects(control):
    seen: dict[str, Any] = {}

    @protect("customer.read", control=control)
    def read(filters: dict, tags: list) -> None:
        seen["filters"] = filters
        seen["tags"] = tags
        filters["mutated"] = True
        tags.append("mutated")

    original_filters = {"active": True}
    original_tags = ["vip"]
    with context(agent="support-agent"):
        read(filters=original_filters, tags=original_tags)

    assert type(seen["filters"]) is dict
    assert type(seen["tags"]) is list
    assert original_filters == {"active": True}
    assert original_tags == ["vip"]


def test_an_executor_mutating_its_arguments_cannot_change_the_action_hash(control, store):
    @protect("customer.read", control=control)
    def read(filters: dict) -> None:
        filters["mutated"] = True

    with context(agent="support-agent"):
        read(filters={"active": True})

    (receipt,) = store.receipts()
    assert receipt.arguments == {"filters": {"active": True}}


# --- context() (SPEC §2.1, §8) --------------------------------------------------------


def test_context_sets_the_principal(control, store):
    @protect("customer.read", control=control)
    def read() -> None: ...

    with context(agent="refund-agent", user="ops@example.com"):
        read()

    (receipt,) = store.receipts()
    assert receipt.principal == Principal(agent="refund-agent", user="ops@example.com")


def test_context_defaults_the_user_to_none_and_the_environment_to_production(control, store):
    @protect("customer.read", control=control)
    def read() -> None: ...

    with context(agent="refund-agent"):
        read()

    (receipt,) = store.receipts()
    assert receipt.principal == Principal(agent="refund-agent")
    assert receipt.environment == "production"


def test_context_sets_the_environment(control, store):
    @protect("customer.read", control=control)
    def read() -> None: ...

    with context(agent="refund-agent", environment="staging"):
        read()

    (receipt,) = store.receipts()
    assert receipt.environment == "staging"


def test_a_nested_context_inherits_the_enclosing_environment(control, store):
    @protect("customer.read", control=control)
    def read() -> None: ...

    with context(agent="outer", environment="staging"), context(agent="inner"):
        read()

    (receipt,) = store.receipts()
    assert receipt.principal == Principal(agent="inner")
    assert receipt.environment == "staging"


def test_context_is_restored_on_exit(control, store):
    @protect("customer.read", control=control)
    def read() -> None: ...

    with context(agent="outer"):
        with context(agent="inner"):
            read()
        read()

    inner, outer = store.receipts()
    assert inner.principal == Principal(agent="inner")
    assert outer.principal == Principal(agent="outer")


def test_context_rejects_an_empty_agent():
    with pytest.raises(InvalidArgument):
        context(agent="").__enter__()


def test_context_rejects_an_empty_environment():
    with pytest.raises(InvalidArgument):
        context(agent="refund-agent", environment="").__enter__()


def test_missing_context_denies_the_action(control, store):
    @protect("customer.read", control=control)
    def read() -> None:
        raise AssertionError("executor must not run without a principal")

    with pytest.raises(ActionDenied) as excinfo:
        read()

    assert excinfo.value.reason == "no_principal"
    assert store.receipts() == ()
    assert store.events() == ()


def test_missing_context_warns_on_the_ctrlrun_logger(control, caplog):
    @protect("customer.read", control=control)
    def read() -> None: ...

    with caplog.at_level(logging.WARNING, logger="ctrlrun"), pytest.raises(ActionDenied):
        read()

    (record,) = caplog.records
    assert record.name.startswith("ctrlrun")
    assert "customer.read" in record.getMessage()


# --- Control.evaluate has no side effects (SPEC §8) -----------------------------------


def test_evaluate_returns_the_decision_and_reason(control):
    action = Action(
        name="stripe.refund",
        arguments={"amount": 100},
        principal=Principal(agent="refund-agent"),
    )
    evaluation = control.evaluate(action)
    assert evaluation.decision is Decision.ALLOW
    assert evaluation.reason == "rule[0]"


def test_evaluate_has_no_side_effects(control, store):
    action = Action(
        name="stripe.refund",
        arguments={"amount": 100},
        principal=Principal(agent="refund-agent"),
    )
    control.evaluate(action)
    control.evaluate(action)
    assert store.events() == ()
    assert store.receipts() == ()


def test_evaluate_of_an_unknown_action_denies_without_raising(control, store):
    action = Action(name="foo.bar", arguments={}, principal=Principal(agent="refund-agent"))
    evaluation = control.evaluate(action)
    assert evaluation.decision is Decision.DENY
    assert evaluation.reason == "unknown_action"
    assert store.receipts() == ()


# --- Control.from_file (SPEC §3.4, §8) ------------------------------------------------


def test_from_file_loads_the_policy(tmp_path):
    path = tmp_path / "ctrlrun.yaml"
    path.write_text(POLICY, encoding="utf-8")

    loaded = Control.from_file(path)
    action = Action(name="customer.read", arguments={}, principal=Principal(agent="a"))
    assert loaded.evaluate(action).decision is Decision.ALLOW


def test_a_missing_policy_file_means_no_Control_can_be_constructed(tmp_path):
    with pytest.raises(PolicyError):
        Control.from_file(tmp_path / "absent.yaml")


def test_a_malformed_policy_file_means_no_Control_can_be_constructed(tmp_path):
    path = tmp_path / "ctrlrun.yaml"
    path.write_text("actions: {}\n", encoding="utf-8")  # no schema key

    with pytest.raises(PolicyError):
        Control.from_file(path)


# --- where the state lives (SPEC §8, §5.3 E1) -----------------------------------------


def _policy_file(tmp_path):
    path = tmp_path / "ctrlrun.yaml"
    path.write_text(POLICY, encoding="utf-8")
    return path


def test_state_lands_beside_the_policy_file(tmp_path):
    # Beside the policy, not beside the process: two workers sharing a policy must share
    # one store, or reservation is atomic within each of them and meaningless between them.
    loaded = Control.from_file(_policy_file(tmp_path))

    assert loaded.store.path == tmp_path / ".ctrlrun" / "state.db"
    assert loaded.store.path.exists()


def test_CTRLRUN_STATE_overrides_the_state_path(tmp_path, monkeypatch):
    elsewhere = tmp_path / "shared" / "ctrlrun.db"
    monkeypatch.setenv("CTRLRUN_STATE", str(elsewhere))

    loaded = Control.from_file(_policy_file(tmp_path))

    assert loaded.store.path == elsewhere
    assert elsewhere.exists()
    assert not (tmp_path / ".ctrlrun").exists()


def test_two_controls_sharing_CTRLRUN_STATE_share_their_effects(tmp_path, monkeypatch):
    monkeypatch.setenv("CTRLRUN_STATE", str(tmp_path / "shared.db"))
    first = Control.from_file(_policy_file(tmp_path))
    second = Control.from_file(tmp_path / "ctrlrun.yaml")

    first.store.reserve_effect("refund:txn_1", "act_1")

    with pytest.raises(DuplicateEffect):
        second.store.reserve_effect("refund:txn_1", "act_2")


def test_an_empty_CTRLRUN_STATE_is_refused(tmp_path, monkeypatch):
    # As CTRLRUN_CONFIG does in §3.4: a blank path is a misconfiguration, and falling back
    # would put an agent's effects in a store nobody is watching.
    monkeypatch.setenv("CTRLRUN_STATE", "   ")

    with pytest.raises(InvalidArgument):
        Control.from_file(_policy_file(tmp_path))


def test_an_in_memory_CTRLRUN_STATE_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("CTRLRUN_STATE", ":memory:")

    with pytest.raises(InvalidArgument):
        Control.from_file(_policy_file(tmp_path))


def test_an_in_memory_uri_CTRLRUN_STATE_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("CTRLRUN_STATE", "file:state?mode=memory&cache=shared")

    with pytest.raises(InvalidArgument):
        Control.from_file(_policy_file(tmp_path))


def test_an_undeclared_control_is_discovered_from_the_policy_file(tmp_path, monkeypatch):
    path = tmp_path / "ctrlrun.yaml"
    path.write_text(POLICY, encoding="utf-8")
    monkeypatch.setenv("CTRLRUN_CONFIG", str(path))
    monkeypatch.setattr(control_module, "_DEFAULT_CONTROL", None)

    @protect("customer.read")
    def read(customer_id: str) -> str:
        return customer_id

    with context(agent="support-agent"):
        assert read("cus_1") == "cus_1"


# --- executor outcome mapping (SPEC §5.5) ---------------------------------------------


def test_NotExecuted_records_a_failed_receipt_and_propagates(control, store):
    @protect("customer.read", control=control)
    def read() -> None:
        raise NotExecuted("remote rejected the request before any side effect")

    with context(agent="support-agent"), pytest.raises(NotExecuted):
        read()

    (receipt,) = store.receipts()
    assert receipt.result == ReceiptResult.FAILED
    assert "remote rejected" in receipt.error
    assert _event_types(store)[-1] == EventType.EXECUTION_FAILED


def test_any_other_exception_records_an_ambiguous_receipt_and_propagates(control, store):
    @protect("customer.read", control=control)
    def read() -> None:
        raise TimeoutError("read timed out")

    with context(agent="support-agent"), pytest.raises(TimeoutError):
        read()

    (receipt,) = store.receipts()
    assert receipt.result == ReceiptResult.AMBIGUOUS
    assert "TimeoutError" in receipt.error
    assert _event_types(store)[-1] == EventType.EXECUTION_AMBIGUOUS


def test_an_unknown_exception_is_never_recorded_as_failed(control, store):
    @protect("customer.read", control=control)
    def read() -> None:
        raise ConnectionError("connection reset")

    with context(agent="support-agent"), pytest.raises(ConnectionError):
        read()

    (receipt,) = store.receipts()
    assert receipt.result != ReceiptResult.FAILED


# --- the APPROVE path (SPEC §4.3; covered in depth by tests/test_approval.py) ---------


def test_an_APPROVE_decision_suspends_the_call_with_ApprovalRequired(control, store):
    @protect("stripe.refund", control=control)
    def refund(amount: int) -> None:
        raise AssertionError("executor must not run without an approval")

    with context(agent="refund-agent"), pytest.raises(ApprovalRequired) as excinfo:
        refund(amount=2000)

    assert excinfo.value.request_id.startswith("apr_")
    assert _event_types(store)[-1] == EventType.APPROVAL_REQUESTED


# --- effect and resource templates (SPEC §5.1; in depth in tests/test_effect.py) -------


def test_an_effect_template_reaches_the_receipt(control, store):
    @protect("customer.read", effect="read:{customer_id}", control=control)
    def read(customer_id: str) -> None: ...

    with context(agent="support-agent"):
        read("cus_1")

    assert store.receipts()[0].effect_key == "read:cus_1"


def test_an_effect_key_passed_to_execute_reaches_the_receipt(control):
    action = Action(name="customer.read", arguments={}, principal=Principal(agent="a"))
    receipt = control.execute(action, lambda: None, effect_key="read:cus_1")
    assert receipt.effect_key == "read:cus_1"


def test_a_templated_resource_reaches_the_receipt(control, store):
    @protect("customer.read", resource="customer:{customer_id}", control=control)
    def read(customer_id: str) -> None: ...

    with context(agent="support-agent"):
        read("cus_1")

    assert store.receipts()[0].resource == "customer:cus_1"


def test_a_literal_resource_reaches_the_receipt(control, store):
    @protect("customer.read", resource="customer:cus_1", control=control)
    def read() -> None: ...

    with context(agent="support-agent"):
        read()

    (receipt,) = store.receipts()
    assert receipt.resource == "customer:cus_1"


# --- receipts and events are portable JSON (SPEC §6.1, §6.2) --------------------------


def test_receipt_json_has_exactly_the_specified_fields(control, store):
    @protect("customer.read", resource="customer:cus_1", control=control)
    def read(customer_id: str) -> None: ...

    with context(agent="support-agent", user="ops@example.com"):
        read("cus_1")

    (receipt,) = store.receipts()
    document = json.loads(receipt.to_json())
    assert set(document) == {
        "schema",
        "receipt_id",
        "action_id",
        "action",
        "action_hash",
        "principal",
        "resource",
        "arguments",
        "environment",
        "decision",
        "decision_reason",
        "approval_id",
        "approver",
        "effect_key",
        "attempt",
        "result",
        "error",
        "started_at",
        "finished_at",
    }
    assert document["schema"] == RECEIPT_SCHEMA
    assert document["receipt_id"].startswith("ctr_")
    assert document["principal"] == {"agent": "support-agent", "user": "ops@example.com"}
    assert document["arguments"] == {"customer_id": "cus_1"}
    assert document["attempt"] == 1
    assert document["effect_key"] is None
    assert document["approval_id"] is None
    assert document["approver"] is None


def test_receipt_json_renders_enums_by_value(control, store):
    @protect("customer.read", control=control)
    def read() -> None: ...

    with context(agent="support-agent"):
        read()

    (receipt,) = store.receipts()
    document = json.loads(receipt.to_json())
    assert document["decision"] == "allow"
    assert document["result"] == "committed"


def test_receipt_timestamps_are_utc_iso_8601(control, store):
    @protect("customer.read", control=control)
    def read() -> None: ...

    with context(agent="support-agent"):
        read()

    (receipt,) = store.receipts()
    document = json.loads(receipt.to_json())
    assert document["started_at"] == "2026-09-03T10:12:01.130Z"
    assert document["finished_at"].endswith("Z")
    assert document["finished_at"] > document["started_at"]


def test_event_json_has_the_specified_fields(control, store):
    @protect("customer.read", control=control)
    def read() -> None: ...

    with context(agent="support-agent"):
        read()

    document = json.loads(store.events()[1].to_json())
    assert set(document) == {
        "event_id",
        "ts",
        "type",
        "action_id",
        "effect_key",
        "approval_id",
        "data",
    }
    assert document["type"] == "POLICY_EVALUATED"
    assert document["data"] == {"decision": "allow", "reason": "decision"}


# --- the store (SPEC §5.3, evidence half) ---------------------------------------------


def test_in_memory_store_returns_immutable_snapshots(store):
    event = Event(type=EventType.ACTION_PROPOSED, action_id="act_1", ts=datetime.now(UTC))
    store.append_event(event)
    assert isinstance(store.events(), tuple)
    assert isinstance(store.receipts(), tuple)


def test_in_memory_store_assigns_event_ids(store):
    for _ in range(3):
        store.append_event(
            Event(type=EventType.ACTION_PROPOSED, action_id="act_1", ts=datetime.now(UTC))
        )
    assert [event.event_id for event in store.events()] == [1, 2, 3]


def test_receipt_is_exported_from_the_package():
    assert Receipt.__module__ == "ctrlrun.receipt"


# --- T16: the decorator wins a template mismatch (SPEC-v0.2 §3.2) ----------------------

TEMPLATED_POLICY = """
schema: ctrlrun.policy/v2
actions:
  stripe.refund:
    effect: "refund:{payment_id}"
    resource: "payment:{payment_id}"
    decision: allow
"""


def _templated_control(store=None):
    return Control(Policy.from_yaml(TEMPLATED_POLICY), store or InMemoryStateStore())


def test_T16_the_policy_supplies_the_templates_the_decorator_omits():
    control = _templated_control()

    @protect("stripe.refund", control=control)
    def refund(payment_id: str, amount: int) -> str:
        return f"re_{payment_id}"

    with context(agent="refund-agent"):
        refund(payment_id="txn_1", amount=200)

    receipt = control.store.receipts()[-1]
    assert receipt.effect_key == "refund:txn_1"
    assert receipt.resource == "payment:txn_1"


def test_T16_a_decorator_and_a_policy_template_produce_the_same_action_hash():
    """The gateway has no decorator (§3.2), so the two paths have to agree exactly."""
    from_policy = _templated_control()
    from_decorator = Control(
        Policy.from_yaml(
            TEMPLATED_POLICY.replace("ctrlrun.policy/v2", "ctrlrun.policy/v1")
            .replace('    effect: "refund:{payment_id}"\n', "")
            .replace('    resource: "payment:{payment_id}"\n', "")
        ),
        InMemoryStateStore(),
    )

    @protect("stripe.refund", control=from_policy)
    def policy_refund(payment_id: str, amount: int) -> str:
        return f"re_{payment_id}"

    @protect(
        "stripe.refund",
        effect="refund:{payment_id}",
        resource="payment:{payment_id}",
        control=from_decorator,
    )
    def decorated_refund(payment_id: str, amount: int) -> str:
        return f"re_{payment_id}"

    with context(agent="refund-agent"):
        policy_refund(payment_id="txn_1", amount=200)
        decorated_refund(payment_id="txn_1", amount=200)

    left = from_policy.store.receipts()[-1]
    right = from_decorator.store.receipts()[-1]
    assert left.action_hash == right.action_hash
    assert left.effect_key == right.effect_key == "refund:txn_1"
    assert left.resource == right.resource == "payment:txn_1"


def test_T16_a_gateway_built_action_hashes_the_same_as_the_decorator_call():
    """§6.6 builds its Action from the policy's templates; this is that construction."""
    from ctrlrun.effect import resolve_effect_key, resolve_resource

    control = _templated_control()
    policy = control.policy
    arguments = {"payment_id": "txn_1", "amount": 200}

    gateway_action = Action(
        name="stripe.refund",
        arguments=arguments,
        principal=Principal(agent="refund-agent"),
        resource=resolve_resource(policy.resource_template("stripe.refund"), arguments),
    )
    gateway_key = resolve_effect_key(policy.effect_template("stripe.refund"), gateway_action)

    @protect("stripe.refund", control=control)
    def refund(payment_id: str, amount: int) -> str:
        return f"re_{payment_id}"

    with context(agent="refund-agent"):
        refund(payment_id="txn_1", amount=200)

    receipt = control.store.receipts()[-1]
    assert receipt.action_hash == gateway_action.action_hash
    assert receipt.effect_key == gateway_key


@pytest.mark.parametrize(
    ("kwarg", "decorated", "expected_key", "expected_resource"),
    [
        ("effect", "rf:{payment_id}", "rf:txn_1", "payment:txn_1"),
        ("resource", "card:{payment_id}", "refund:txn_1", "card:txn_1"),
    ],
)
def test_T16_the_decorator_wins_a_mismatch(kwarg, decorated, expected_key, expected_resource):
    """The code that will run is what executes; substituting the policy's template would
    change an effect identity without changing a line of the program (§3.2)."""
    control = _templated_control()

    @protect("stripe.refund", control=control, **{kwarg: decorated})
    def refund(payment_id: str, amount: int) -> str:
        return f"re_{payment_id}"

    with context(agent="refund-agent"):
        refund(payment_id="txn_1", amount=200)

    receipt = control.store.receipts()[-1]
    assert receipt.effect_key == expected_key
    assert receipt.resource == expected_resource


def test_T16_a_mismatch_warns_once_naming_both_templates(caplog):
    control = _templated_control()

    with caplog.at_level(logging.WARNING, logger="ctrlrun"):

        @protect("stripe.refund", effect="rf:{payment_id}", control=control)
        def refund(payment_id: str, amount: int) -> str:
            return f"re_{payment_id}"

        with context(agent="refund-agent"):
            refund(payment_id="txn_1", amount=200)
            refund(payment_id="txn_2", amount=200)

    warnings = [
        record.getMessage() for record in caplog.records if "rf:{payment_id}" in record.getMessage()
    ]
    assert len(warnings) == 1
    assert "refund:{payment_id}" in warnings[0]
    assert "stripe.refund" in warnings[0]


def test_T16_the_mismatch_warning_is_emitted_at_decoration_time_with_an_explicit_control(caplog):
    """§3.2 — when `control=` is passed the Control resolves at decoration, so the warning
    lands at import rather than waiting for the first agent run to surface it."""
    control = _templated_control()

    with caplog.at_level(logging.WARNING, logger="ctrlrun"):

        @protect("stripe.refund", effect="rf:{payment_id}", control=control)
        def refund(payment_id: str, amount: int) -> str:
            return f"re_{payment_id}"

    assert [record for record in caplog.records if "rf:{payment_id}" in record.getMessage()]


def test_T16_matching_templates_do_not_warn(caplog):
    control = _templated_control()

    with caplog.at_level(logging.WARNING, logger="ctrlrun"):

        @protect(
            "stripe.refund",
            effect="refund:{payment_id}",
            resource="payment:{payment_id}",
            control=control,
        )
        def refund(payment_id: str, amount: int) -> str:
            return f"re_{payment_id}"

        with context(agent="refund-agent"):
            refund(payment_id="txn_1", amount=200)

    assert not [record for record in caplog.records if "in force" in record.getMessage()]


def test_T16_an_action_with_no_effect_in_policy_gets_no_reservation():
    """§3.2's escape hatch, and it is the right answer for a read."""
    control = _templated_control()

    @protect("customer.read", control=control)
    def read(customer_id: str) -> str:
        return "ok"

    document = TEMPLATED_POLICY + "  customer.read:\n    decision: allow\n"
    control = Control(Policy.from_yaml(document), InMemoryStateStore())

    @protect("customer.read", control=control)
    def read_again(customer_id: str) -> str:
        return "ok"

    with context(agent="support-agent"):
        read_again(customer_id="cus_1")

    assert control.store.list_effects() == ()
    assert control.store.receipts()[-1].effect_key is None
