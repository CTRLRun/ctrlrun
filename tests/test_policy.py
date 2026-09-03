"""Policy loading and rule evaluation. SPEC-v0.1 §3; acceptance test T6 (policy half)."""

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from ctrlrun import Action, Decision, Policy, PolicyError, Principal
from ctrlrun.policy import Evaluation

REFUND_POLICY = """
schema: ctrlrun.policy/v1
actions:
  customer.read:
    decision: allow
  stripe.refund:
    rules:
      - when: { amount_lte: 500 }
        decision: allow
      - when: { amount_lte: 5000 }
        decision: approve
      - decision: deny
  iam.grant_admin:
    decision: deny
"""


def _action(name: str = "stripe.refund", arguments: dict[str, Any] | None = None) -> Action:
    return Action(
        name=name,
        arguments=arguments if arguments is not None else {},
        principal=Principal(agent="refund-agent"),
    )


def _decide(
    text: str, arguments: dict[str, Any] | None = None, *, name: str = "stripe.refund"
) -> Evaluation:
    return Policy.from_yaml(text).evaluate(_action(name, arguments))


def _one_rule(condition: str, operand: str) -> str:
    """A policy whose single rule allows on one condition; everything else falls to DENY."""
    return f"""
schema: ctrlrun.policy/v1
actions:
  stripe.refund:
    rules:
      - when: {{ {condition}: {operand} }}
        decision: allow
"""


# --- decisions (SPEC §3.3) ------------------------------------------------------------


def test_decision_has_exactly_the_three_specified_members() -> None:
    assert [member.value for member in Decision] == ["allow", "approve", "deny"]
    assert Decision.ALLOW == "allow"
    assert Decision.APPROVE == "approve"
    assert Decision.DENY == "deny"


def test_decision_renders_by_value() -> None:
    """Receipts and CLI output MUST carry `approve`, never `Decision.APPROVE` (SPEC §6.1).

    This is the guard on `Decision` being a `StrEnum` (SPEC §3.3): under `(str, Enum)`
    both renderings below are `Decision.ALLOW`, and a receipt is read by tools that
    never imported CTRLRun.
    """
    for member in Decision:
        assert str(member) == member.value
        assert f"{member}" == member.value
        assert format(member) == member.value
        assert json.dumps(member) == f'"{member.value}"'
    assert str(Decision.ALLOW) == "allow"
    assert f"{Decision.ALLOW}" == "allow"
    assert json.dumps({"decision": Decision.APPROVE}) == '{"decision": "approve"}'


# --- rule evaluation (SPEC §3.2) ------------------------------------------------------


def test_first_matching_rule_wins() -> None:
    # amount=100 satisfies both rule[0] and rule[1]; the first one decides.
    allowed = _decide(REFUND_POLICY, {"amount": 100})
    assert allowed.decision is Decision.ALLOW
    assert allowed.reason == "rule[0]"

    approved = _decide(REFUND_POLICY, {"amount": 2000})
    assert approved.decision is Decision.APPROVE
    assert approved.reason == "rule[1]"

    denied = _decide(REFUND_POLICY, {"amount": 50000})
    assert denied.decision is Decision.DENY
    assert denied.reason == "rule[2]"


def test_first_matching_rule_wins_even_when_a_later_rule_is_more_permissive() -> None:
    policy = """
schema: ctrlrun.policy/v1
actions:
  stripe.refund:
    rules:
      - when: { amount_gte: 1000 }
        decision: deny
      - when: { amount_gte: 1000 }
        decision: allow
"""
    evaluation = _decide(policy, {"amount": 5000})
    assert evaluation.decision is Decision.DENY
    assert evaluation.reason == "rule[0]"


def test_rule_with_no_when_always_matches() -> None:
    assert _decide(REFUND_POLICY, {}).decision is Decision.DENY
    assert _decide(REFUND_POLICY, {"currency": "EUR"}).reason == "rule[2]"


def test_rule_with_no_when_shadows_every_later_rule() -> None:
    policy = """
schema: ctrlrun.policy/v1
actions:
  stripe.refund:
    rules:
      - decision: approve
      - when: { amount_lte: 1 }
        decision: allow
"""
    evaluation = _decide(policy, {"amount": 1})
    assert evaluation.decision is Decision.APPROVE
    assert evaluation.reason == "rule[0]"


def test_no_matching_rule_denies() -> None:
    policy = """
schema: ctrlrun.policy/v1
actions:
  stripe.refund:
    rules:
      - when: { amount_lte: 500 }
        decision: allow
      - when: { amount_lte: 5000 }
        decision: approve
"""
    evaluation = _decide(policy, {"amount": 50000})
    assert evaluation.decision is Decision.DENY
    assert evaluation.reason == "no_matching_rule"


def test_a_bare_decision_entry_needs_no_arguments() -> None:
    allowed = _decide(REFUND_POLICY, {"customer_id": "cus_1"}, name="customer.read")
    assert allowed.decision is Decision.ALLOW
    assert allowed.reason == "decision"
    assert _decide(REFUND_POLICY, {}, name="iam.grant_admin").decision is Decision.DENY


def test_all_conditions_in_a_when_must_hold() -> None:
    policy = """
schema: ctrlrun.policy/v1
actions:
  stripe.refund:
    rules:
      - when: { amount_lte: 500, currency_eq: EUR }
        decision: allow
"""
    assert _decide(policy, {"amount": 100, "currency": "EUR"}).decision is Decision.ALLOW
    assert _decide(policy, {"amount": 100, "currency": "USD"}).decision is Decision.DENY
    assert _decide(policy, {"amount": 900, "currency": "EUR"}).decision is Decision.DENY
    assert _decide(policy, {"currency": "EUR"}).decision is Decision.DENY


def test_evaluation_is_side_effect_free_and_repeatable() -> None:
    policy = Policy.from_yaml(REFUND_POLICY)
    action = _action(arguments={"amount": 2000})
    first = policy.evaluate(action)
    second = policy.evaluate(action)
    assert first == second
    assert action.arguments == {"amount": 2000}


# --- condition operators (SPEC §3.2) --------------------------------------------------


@pytest.mark.parametrize(
    ("condition", "operand", "arguments", "expected"),
    [
        pytest.param("value_eq", "5", {"value": 5}, Decision.ALLOW, id="eq-int-match"),
        pytest.param("value_eq", "5", {"value": 6}, Decision.DENY, id="eq-int-miss"),
        pytest.param("value_eq", "EUR", {"value": "EUR"}, Decision.ALLOW, id="eq-str-match"),
        pytest.param("value_eq", "EUR", {"value": "USD"}, Decision.DENY, id="eq-str-miss"),
        pytest.param("value_eq", "EUR", {"value": "eur"}, Decision.DENY, id="eq-str-case"),
        pytest.param("value_eq", "null", {"value": None}, Decision.ALLOW, id="eq-none-match"),
        pytest.param("value_eq", "null", {"value": 0}, Decision.DENY, id="eq-none-miss"),
        pytest.param("value_eq", "true", {"value": True}, Decision.ALLOW, id="eq-bool-match"),
        pytest.param("value_eq", "true", {"value": False}, Decision.DENY, id="eq-bool-miss"),
        pytest.param("value_eq", "[1, 2]", {"value": [1, 2]}, Decision.ALLOW, id="eq-list-match"),
        pytest.param("value_eq", "[1, 2]", {"value": [2, 1]}, Decision.DENY, id="eq-list-order"),
        pytest.param("value_eq", "[1, 2]", {"value": [1]}, Decision.DENY, id="eq-list-length"),
        pytest.param(
            "value_eq", "{a: 1}", {"value": {"a": 1}}, Decision.ALLOW, id="eq-mapping-match"
        ),
        pytest.param(
            "value_eq", "{a: 1}", {"value": {"a": 2}}, Decision.DENY, id="eq-mapping-miss"
        ),
        pytest.param(
            "value_eq", "{a: 1}", {"value": {"a": 1, "b": 2}}, Decision.DENY, id="eq-mapping-extra"
        ),
        pytest.param("value_neq", "5", {"value": 6}, Decision.ALLOW, id="neq-match"),
        pytest.param("value_neq", "5", {"value": 5}, Decision.DENY, id="neq-miss"),
        pytest.param("value_neq", "EUR", {"value": "USD"}, Decision.ALLOW, id="neq-str"),
        pytest.param("value_lt", "5", {"value": 4}, Decision.ALLOW, id="lt-below"),
        pytest.param("value_lt", "5", {"value": 5}, Decision.DENY, id="lt-equal"),
        pytest.param("value_lt", "5", {"value": 6}, Decision.DENY, id="lt-above"),
        pytest.param("value_lte", "5", {"value": 4}, Decision.ALLOW, id="lte-below"),
        pytest.param("value_lte", "5", {"value": 5}, Decision.ALLOW, id="lte-equal"),
        pytest.param("value_lte", "5", {"value": 6}, Decision.DENY, id="lte-above"),
        pytest.param("value_gt", "5", {"value": 6}, Decision.ALLOW, id="gt-above"),
        pytest.param("value_gt", "5", {"value": 5}, Decision.DENY, id="gt-equal"),
        pytest.param("value_gt", "5", {"value": 4}, Decision.DENY, id="gt-below"),
        pytest.param("value_gte", "5", {"value": 6}, Decision.ALLOW, id="gte-above"),
        pytest.param("value_gte", "5", {"value": 5}, Decision.ALLOW, id="gte-equal"),
        pytest.param("value_gte", "5", {"value": 4}, Decision.DENY, id="gte-below"),
        pytest.param("value_lt", "-1", {"value": -2}, Decision.ALLOW, id="lt-negative"),
        pytest.param("value_in", '["a", "b"]', {"value": "a"}, Decision.ALLOW, id="in-match"),
        pytest.param("value_in", '["a", "b"]', {"value": "c"}, Decision.DENY, id="in-miss"),
        pytest.param("value_in", "[1, 2]", {"value": 2}, Decision.ALLOW, id="in-int"),
        pytest.param("value_in", "[]", {"value": 1}, Decision.DENY, id="in-empty"),
        pytest.param("value_in", "[[1, 2]]", {"value": [1, 2]}, Decision.ALLOW, id="in-list-item"),
    ],
)
def test_condition_operators(
    condition: str, operand: str, arguments: dict[str, Any], expected: Decision
) -> None:
    assert _decide(_one_rule(condition, operand), arguments).decision is expected


def test_condition_key_splits_on_the_longest_operator_suffix() -> None:
    # "payment_id_eq" is the argument "payment_id", not "payment" with a bad op.
    assert _decide(_one_rule("payment_id_eq", "txn_1"), {"payment_id": "txn_1"}).decision is (
        Decision.ALLOW
    )
    # "amount_neq" is "amount" with `_neq`, not "amount_n" with `_eq`.
    assert _decide(_one_rule("amount_neq", "5"), {"amount": 6}).decision is Decision.ALLOW
    assert _decide(_one_rule("amount_neq", "5"), {"amount_n": 6}).decision is Decision.DENY
    # "amount_lte" is "amount" with `_lte`, not "amount_l" with `_te`.
    assert _decide(_one_rule("amount_lte", "5"), {"amount": 5}).decision is Decision.ALLOW


def test_equality_distinguishes_bool_from_int() -> None:
    assert _decide(_one_rule("value_eq", "1"), {"value": True}).decision is Decision.DENY
    assert _decide(_one_rule("value_eq", "true"), {"value": 1}).decision is Decision.DENY
    assert _decide(_one_rule("value_in", "[1]"), {"value": True}).decision is Decision.DENY
    assert _decide(_one_rule("value_neq", "1"), {"value": True}).decision is Decision.ALLOW


def test_equality_distinguishes_containers_from_scalars() -> None:
    assert _decide(_one_rule("value_eq", "5"), {"value": [5]}).decision is Decision.DENY
    assert _decide(_one_rule("value_eq", "[5]"), {"value": 5}).decision is Decision.DENY
    assert _decide(_one_rule("value_eq", "{a: 1}"), {"value": [1]}).decision is Decision.DENY


def test_a_list_argument_matches_a_list_operand_despite_being_stored_frozen() -> None:
    # Action.arguments holds tuples; conditions are evaluated against the canonical form.
    action = _action(arguments={"tags": ["a", "b"]})
    assert action.arguments["tags"] == ("a", "b")
    policy = Policy.from_yaml(_one_rule("tags_eq", '["a", "b"]'))
    assert policy.evaluate(action).decision is Decision.ALLOW


# --- absent arguments and type mismatches (SPEC §3.2, §3.4) ---------------------------


@pytest.mark.parametrize(
    ("condition", "operand"),
    [
        pytest.param("amount_eq", "5", id="eq"),
        pytest.param("amount_neq", "5", id="neq"),
        pytest.param("amount_lt", "5", id="lt"),
        pytest.param("amount_lte", "5", id="lte"),
        pytest.param("amount_gt", "5", id="gt"),
        pytest.param("amount_gte", "5", id="gte"),
        pytest.param("amount_in", "[5]", id="in"),
    ],
)
def test_absent_argument_makes_the_condition_false_for_every_operator(
    condition: str, operand: str
) -> None:
    evaluation = _decide(_one_rule(condition, operand), {"currency": "EUR"})
    assert evaluation.decision is Decision.DENY
    assert evaluation.reason == "no_matching_rule"


def test_absent_argument_falls_through_to_the_next_rule() -> None:
    policy = """
schema: ctrlrun.policy/v1
actions:
  stripe.refund:
    rules:
      - when: { amount_lte: 500 }
        decision: allow
      - decision: approve
"""
    evaluation = _decide(policy, {"currency": "EUR"})
    assert evaluation.decision is Decision.APPROVE
    assert evaluation.reason == "rule[1]"


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("2000", id="str"),
        pytest.param(True, id="bool"),
        pytest.param(None, id="none"),
        pytest.param([1], id="list"),
        pytest.param({"a": 1}, id="mapping"),
    ],
)
@pytest.mark.parametrize("op", ["lt", "lte", "gt", "gte"])
def test_numeric_operator_on_a_non_int_argument_is_false_and_warns(
    op: str, value: Any, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="ctrlrun"):
        evaluation = _decide(_one_rule(f"amount_{op}", "5000"), {"amount": value})

    assert evaluation.decision is Decision.DENY
    assert evaluation.reason == "no_matching_rule"
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert warnings[0].name.startswith("ctrlrun")
    assert "amount" in warnings[0].getMessage()


def test_a_type_mismatch_falls_through_to_the_next_rule() -> None:
    policy = """
schema: ctrlrun.policy/v1
actions:
  stripe.refund:
    rules:
      - when: { amount_lte: 500 }
        decision: allow
      - decision: deny
"""
    evaluation = _decide(policy, {"amount": "500"})
    assert evaluation.decision is Decision.DENY
    assert evaluation.reason == "rule[1]"


def test_no_warning_is_logged_for_a_well_typed_condition(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="ctrlrun"):
        _decide(REFUND_POLICY, {"amount": 2000})
        _decide(_one_rule("value_eq", "EUR"), {"value": ["not", "a", "string"]})
    assert [record for record in caplog.records if record.levelno == logging.WARNING] == []


# --- a condition that can never match says so (SPEC §3.2) -----------------------------


def test_a_condition_on_an_absent_argument_is_false_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """SPEC §3.2 — still false, never an error, but no longer silent.

    Defaults are applied when a call is bound (§8), so an argument is either always present
    or never: an absent one is a typo or a field name, not a value that happens to be
    missing this time. Silence there let a mistyped rule fall through to a catch-all allow.
    """
    with caplog.at_level(logging.WARNING, logger="ctrlrun"):
        evaluation = _decide(_one_rule("amont_lte", "5000"), {"amount": 100})

    assert evaluation.decision is Decision.DENY
    assert evaluation.reason == "no_matching_rule"
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "amont" in warnings[0].getMessage()
    assert "amount" in warnings[0].getMessage()  # names what the action actually carries


def test_a_mistyped_condition_no_longer_falls_through_to_a_catch_all_allow(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The regression this exists for: a typo made a deny rule vanish, silently.

    The decision is unchanged — SPEC §3.2 keeps an absent argument false — so the rule below
    still allows. What changed is that it can no longer happen quietly.
    """
    policy = """
schema: ctrlrun.policy/v1
actions:
  stripe.refund:
    rules:
      - when: { amont_gte: 100000 }
        decision: deny
      - decision: allow
"""
    with caplog.at_level(logging.WARNING, logger="ctrlrun"):
        evaluation = _decide(policy, {"amount": 999999})

    assert evaluation.decision is Decision.ALLOW
    assert [record for record in caplog.records if record.levelno == logging.WARNING]


@pytest.mark.parametrize(
    "field", ["environment", "resource", "principal", "agent", "user", "action_id"]
)
@pytest.mark.parametrize("op", ["eq", "neq", "lte", "in"])
def test_a_condition_naming_an_action_field_is_refused_at_load(field: str, op: str) -> None:
    """SPEC §3.2 — conditions address arguments; an Action field in one is refused.

    `when: { environment_eq: production }` reads like it scopes a rule to production and
    does nothing at all, because `environment` is an Action field and conditions see only
    arguments. Same fail-closed reading as `{resource}` in §5.1: two candidate meanings for
    one name, so refuse and make the author rename, rather than pick one silently.
    """
    operand = "[production]" if op == "in" else "1" if op == "lte" else "production"
    with pytest.raises(PolicyError) as refused:
        Policy.from_yaml(_one_rule(f"{field}_{op}", operand))

    assert field in str(refused.value)
    assert "argument" in str(refused.value)


def test_an_argument_that_merely_contains_a_field_name_still_loads() -> None:
    """Only the whole argument name is reserved: `resource_id` is an ordinary argument."""
    policy = Policy.from_yaml(_one_rule("resource_id_eq", "srv-1"))
    assert policy.evaluate(_action(arguments={"resource_id": "srv-1"})).decision is Decision.ALLOW


# --- fail-closed defaults (SPEC §3.4) -------------------------------------------------


def test_T6_unknown_action_is_denied_with_reason_unknown_action() -> None:
    evaluation = _decide(REFUND_POLICY, {"amount": 1}, name="foo.bar")
    assert evaluation.decision is Decision.DENY
    assert evaluation.reason == "unknown_action"


def test_T6_an_action_name_is_matched_exactly() -> None:
    for name in ["stripe.refund ", "STRIPE.REFUND", "stripe", "stripe.refunds", "refund"]:
        assert _decide(REFUND_POLICY, {"amount": 1}, name=name).reason == "unknown_action"


def test_an_empty_actions_map_denies_everything() -> None:
    policy = "schema: ctrlrun.policy/v1\nactions: {}\n"
    assert _decide(policy, {"amount": 1}).reason == "unknown_action"


# --- load-time validation (SPEC §3.1, §3.4) -------------------------------------------


def test_entry_with_both_decision_and_rules_is_a_policy_error() -> None:
    policy = """
schema: ctrlrun.policy/v1
actions:
  stripe.refund:
    decision: allow
    rules:
      - decision: deny
"""
    with pytest.raises(PolicyError, match=r"stripe\.refund"):
        Policy.from_yaml(policy)


def test_entry_with_neither_decision_nor_rules_is_a_policy_error() -> None:
    with pytest.raises(PolicyError, match=r"stripe\.refund"):
        Policy.from_yaml("schema: ctrlrun.policy/v1\nactions:\n  stripe.refund: {}\n")


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("schema: ctrlrun.policy/v1\nactions:\n  stripe.refund:\n", id="null-entry"),
        pytest.param(
            "schema: ctrlrun.policy/v1\nactions:\n  stripe.refund: allow\n", id="scalar-entry"
        ),
        pytest.param(
            "schema: ctrlrun.policy/v1\nactions:\n  stripe.refund:\n    rules: []\n",
            id="empty-rules",
        ),
        pytest.param(
            "schema: ctrlrun.policy/v1\nactions:\n  stripe.refund:\n    rules:\n"
            "      decision: deny\n",
            id="rules-not-a-list",
        ),
        pytest.param(
            "schema: ctrlrun.policy/v1\nactions:\n  stripe.refund:\n    rules:\n      - allow\n",
            id="rule-not-a-mapping",
        ),
        pytest.param(
            "schema: ctrlrun.policy/v1\nactions:\n  stripe.refund:\n    rules:\n"
            "      - when: { amount_lte: 5 }\n",
            id="rule-without-decision",
        ),
        pytest.param(
            "schema: ctrlrun.policy/v1\nactions:\n  stripe.refund:\n    decision: allow\n"
            "    unknown: 1\n",
            id="unknown-entry-key",
        ),
        pytest.param(
            "schema: ctrlrun.policy/v1\nactions:\n  stripe.refund:\n    rules:\n"
            "      - decision: allow\n        unless: { amount_lte: 5 }\n",
            id="unknown-rule-key",
        ),
        pytest.param(
            "schema: ctrlrun.policy/v1\nactions:\n  stripe.refund:\n    decision: maybe\n",
            id="unknown-decision",
        ),
        pytest.param(
            "schema: ctrlrun.policy/v1\nactions:\n  stripe.refund:\n    decision: ALLOW\n",
            id="miscased-decision",
        ),
        pytest.param(
            "schema: ctrlrun.policy/v1\nactions:\n  stripe.refund:\n    decision: 1\n",
            id="non-string-decision",
        ),
        pytest.param(
            "schema: ctrlrun.policy/v1\nactions:\n  stripe.refund:\n    rules:\n"
            "      - when: amount\n        decision: allow\n",
            id="when-not-a-mapping",
        ),
        pytest.param(
            "schema: ctrlrun.policy/v1\nactions:\n  stripe.refund:\n    rules:\n"
            "      - when: {}\n        decision: allow\n",
            id="empty-when",
        ),
        pytest.param(
            "schema: ctrlrun.policy/v1\nactions:\n  stripe.refund:\n    rules:\n"
            "      - when: { 5: 5 }\n        decision: allow\n",
            id="non-string-condition-key",
        ),
    ],
)
def test_malformed_action_entry_is_a_policy_error(text: str) -> None:
    with pytest.raises(PolicyError):
        Policy.from_yaml(text)


@pytest.mark.parametrize(
    "condition",
    [
        pytest.param("amount", id="no-operator"),
        pytest.param("amount_lteq", id="misspelled-operator"),
        pytest.param("amount_contains", id="unsupported-operator"),
        pytest.param("amount_ne", id="near-miss-operator"),
        pytest.param("_eq", id="empty-argument-name"),
    ],
)
def test_unknown_condition_operator_is_a_policy_error(condition: str) -> None:
    with pytest.raises(PolicyError, match=condition):
        Policy.from_yaml(_one_rule(condition, "5"))


@pytest.mark.parametrize(
    ("condition", "operand"),
    [
        pytest.param("amount_lt", '"5"', id="lt-str"),
        pytest.param("amount_lte", "5.0", id="lte-float"),
        pytest.param("amount_gt", "true", id="gt-bool"),
        pytest.param("amount_gte", "null", id="gte-null"),
        pytest.param("amount_lte", "[5]", id="lte-list"),
    ],
)
def test_non_int_operand_for_a_numeric_operator_is_a_policy_error(
    condition: str, operand: str
) -> None:
    with pytest.raises(PolicyError, match="int"):
        Policy.from_yaml(_one_rule(condition, operand))


@pytest.mark.parametrize(
    "operand",
    [
        pytest.param("5", id="int"),
        pytest.param('"a"', id="str"),
        pytest.param("{a: 1}", id="mapping"),
        pytest.param("null", id="null"),
    ],
)
def test_non_list_operand_for_in_is_a_policy_error(operand: str) -> None:
    with pytest.raises(PolicyError, match="list"):
        Policy.from_yaml(_one_rule("value_in", operand))


@pytest.mark.parametrize(
    ("condition", "operand"),
    [
        pytest.param("amount_eq", "20.0", id="eq-float"),
        pytest.param("amount_neq", "20.0", id="neq-float"),
        pytest.param("amount_in", "[1, 20.0]", id="in-float"),
        pytest.param("meta_eq", "{amount: 20.0}", id="nested-float"),
        pytest.param("at_eq", "2026-09-03", id="date"),
        pytest.param("at_eq", "2026-09-03T10:12:01Z", id="datetime"),
    ],
)
def test_disallowed_operand_type_is_a_policy_error(condition: str, operand: str) -> None:
    with pytest.raises(PolicyError):
        Policy.from_yaml(_one_rule(condition, operand))


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("actions:\n  customer.read:\n    decision: allow\n", id="missing-schema"),
        # `ctrlrun.policy/v2` stood here until SPEC-v0.2 §3.1 made it a schema v0.2 loads.
        # The rule under test is unchanged — an unsupported schema is refused — so the
        # example moves forward to one that is still unsupported.
        pytest.param(
            "schema: ctrlrun.policy/v3\nactions:\n  customer.read:\n    decision: allow\n",
            id="future-schema",
        ),
        pytest.param(
            "schema: ctrlrun.action/v1\nactions:\n  customer.read:\n    decision: allow\n",
            id="wrong-schema",
        ),
        pytest.param(
            "schema: null\nactions:\n  customer.read:\n    decision: allow\n", id="null-schema"
        ),
    ],
)
def test_wrong_schema_is_a_policy_error(text: str) -> None:
    with pytest.raises(PolicyError, match="schema"):
        Policy.from_yaml(text)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("", id="empty-file"),
        pytest.param("# only a comment\n", id="comment-only"),
        pytest.param("schema: ctrlrun.policy/v1\n", id="missing-actions"),
        pytest.param("schema: ctrlrun.policy/v1\nactions: null\n", id="null-actions"),
        pytest.param("schema: ctrlrun.policy/v1\nactions:\n  - customer.read\n", id="actions-list"),
        pytest.param(
            "schema: ctrlrun.policy/v1\nactions:\n  1:\n    decision: allow\n", id="int-name"
        ),
        pytest.param("- schema: ctrlrun.policy/v1\n", id="top-level-list"),
        pytest.param("just a string\n", id="top-level-scalar"),
        pytest.param("schema: ctrlrun.policy/v1\nactions: {\n", id="unparseable-yaml"),
        pytest.param("schema: [ctrlrun.policy/v1\nactions: {}\n", id="broken-flow-sequence"),
    ],
)
def test_malformed_policy_document_is_a_policy_error(text: str) -> None:
    with pytest.raises(PolicyError):
        Policy.from_yaml(text)


def test_yaml_is_loaded_safely() -> None:
    # A policy file is untrusted input; it must not be able to construct Python objects.
    with pytest.raises(PolicyError):
        Policy.from_yaml("!!python/object/apply:os.system ['echo pwned']\n")


# --- file discovery (SPEC §3.1, §3.4) -------------------------------------------------


def test_from_file_loads_an_explicit_path(tmp_path: Path) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text(REFUND_POLICY, encoding="utf-8")
    policy = Policy.from_file(path)
    assert policy.evaluate(_action(arguments={"amount": 100})).decision is Decision.ALLOW
    assert policy.source == str(path)


def test_from_file_prefers_the_ctrlrun_config_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = tmp_path / "from-env.yaml"
    configured.write_text(REFUND_POLICY, encoding="utf-8")
    (tmp_path / "ctrlrun.yaml").write_text(
        "schema: ctrlrun.policy/v1\nactions: {}\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CTRLRUN_CONFIG", str(configured))

    policy = Policy.from_file()
    assert policy.source == str(configured)
    assert policy.evaluate(_action(arguments={"amount": 100})).decision is Decision.ALLOW


def test_from_file_falls_back_to_ctrlrun_yaml_in_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "ctrlrun.yaml").write_text(REFUND_POLICY, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CTRLRUN_CONFIG", raising=False)

    policy = Policy.from_file()
    assert policy.evaluate(_action(arguments={"amount": 100})).decision is Decision.ALLOW


def test_missing_policy_file_is_a_policy_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CTRLRUN_CONFIG", raising=False)
    with pytest.raises(PolicyError, match=r"ctrlrun\.yaml"):
        Policy.from_file()


def test_missing_explicit_policy_file_is_a_policy_error(tmp_path: Path) -> None:
    with pytest.raises(PolicyError, match=r"nope\.yaml"):
        Policy.from_file(tmp_path / "nope.yaml")


def test_missing_file_named_by_the_env_var_is_a_policy_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CTRLRUN_CONFIG", str(tmp_path / "nope.yaml"))
    with pytest.raises(PolicyError, match=r"nope\.yaml"):
        Policy.from_file()


def test_empty_env_var_is_a_policy_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "ctrlrun.yaml").write_text(REFUND_POLICY, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CTRLRUN_CONFIG", "")
    with pytest.raises(PolicyError, match="CTRLRUN_CONFIG"):
        Policy.from_file()


def test_a_directory_in_place_of_a_policy_file_is_a_policy_error(tmp_path: Path) -> None:
    with pytest.raises(PolicyError):
        Policy.from_file(tmp_path)


# --- the shipped example must stay loadable -------------------------------------------


def test_the_shipped_example_policy_loads_and_evaluates() -> None:
    policy = Policy.from_file(Path(__file__).parent.parent / "ctrlrun.example.yaml")
    assert policy.evaluate(_action(arguments={"amount": 1000})).decision is Decision.ALLOW
    assert policy.evaluate(_action(arguments={"amount": 200000})).decision is Decision.APPROVE
    assert policy.evaluate(_action(arguments={"amount": 900000})).decision is Decision.DENY
    assert policy.evaluate(_action(name="customer.read")).decision is Decision.ALLOW
    assert policy.evaluate(_action(name="iam.grant_admin")).decision is Decision.DENY
    assert policy.evaluate(_action(name="k8s.delete_namespace")).decision is Decision.APPROVE


# --- T16: per-action effect: and resource: templates (SPEC-v0.2 §3) --------------------

V2_POLICY = """
schema: ctrlrun.policy/v2
actions:
  stripe.refund:
    effect: "refund:{payment_id}"
    resource: "payment:{payment_id}"
    mcp:
      not_executed_on_error: true
    rules:
      - when: { amount_gte: 0, amount_lte: 50000 }
        decision: allow
      - decision: approve
  customer.read:
    decision: allow
"""


def test_T16_a_v2_document_loads_and_exposes_its_templates():
    policy = Policy.from_yaml(V2_POLICY)

    assert policy.effect_template("stripe.refund") == "refund:{payment_id}"
    assert policy.resource_template("stripe.refund") == "payment:{payment_id}"
    assert policy.mcp_options("stripe.refund").not_executed_on_error is True


def test_T16_an_action_without_the_new_keys_has_no_templates():
    """§3.2's documented escape hatch: no `effect:` means no logical effect, and no
    reservation. Right for reads, and the reason it is `None` rather than an error."""
    policy = Policy.from_yaml(V2_POLICY)

    assert policy.effect_template("customer.read") is None
    assert policy.resource_template("customer.read") is None
    assert policy.mcp_options("customer.read").not_executed_on_error is False


def test_T16_an_unknown_action_has_no_templates_either():
    policy = Policy.from_yaml(V2_POLICY)

    assert policy.effect_template("nothing.declared") is None
    assert policy.resource_template("nothing.declared") is None
    assert policy.mcp_options("nothing.declared").not_executed_on_error is False


def test_T16_a_v1_document_still_loads_unchanged():
    """A v1 file keeps loading exactly as it did, which is the compatibility half of §3.1."""
    policy = Policy.from_yaml(REFUND_POLICY)

    assert policy.evaluate(_action(arguments={"amount": 400})).decision is Decision.ALLOW
    assert policy.effect_template("stripe.refund") is None


@pytest.mark.parametrize("key", ["effect", "resource", "mcp"])
def test_T16_a_key_added_by_v2_is_a_PolicyError_in_a_v1_document(key):
    """v0.1 would ignore the template and execute with no duplicate protection at all, so
    the schema string is the only thing standing between those outcomes (§3.1)."""
    value = '"refund:{payment_id}"' if key != "mcp" else "{ not_executed_on_error: true }"
    document = f"""
schema: ctrlrun.policy/v1
actions:
  stripe.refund:
    {key}: {value}
    decision: allow
"""
    with pytest.raises(PolicyError) as raised:
        Policy.from_yaml(document)

    assert key in str(raised.value)
    assert "ctrlrun.policy/v2" in str(raised.value)


@pytest.mark.parametrize("key", ["effect", "resource"])
@pytest.mark.parametrize("template", ["refund:{payment_id", "refund:{}", "refund:{1st}", ""])
def test_T16_a_malformed_template_is_a_PolicyError_at_load(key, template):
    """At load, not an `EffectKeyError` at runtime (§3.1): a typo must not wait for traffic."""
    document = f"""
schema: ctrlrun.policy/v2
actions:
  stripe.refund:
    {key}: "{template}"
    decision: allow
"""
    with pytest.raises(PolicyError):
        Policy.from_yaml(document)


@pytest.mark.parametrize("key", ["effect", "resource"])
def test_T16_a_template_that_is_not_a_string_is_a_PolicyError(key):
    document = f"""
schema: ctrlrun.policy/v2
actions:
  stripe.refund:
    {key}: 17
    decision: allow
"""
    with pytest.raises(PolicyError):
        Policy.from_yaml(document)


@pytest.mark.parametrize("value", ["yes-please", "1", "null", "[]", "{}"])
def test_T16_not_executed_on_error_must_be_a_bool(value):
    """An operator's claim about their upstream, so it is a claim or it is nothing (§3.1)."""
    document = f"""
schema: ctrlrun.policy/v2
actions:
  stripe.refund:
    mcp:
      not_executed_on_error: {value}
    decision: allow
"""
    with pytest.raises(PolicyError):
        Policy.from_yaml(document)


def test_T16_not_executed_on_error_defaults_to_the_fail_closed_value():
    document = """
schema: ctrlrun.policy/v2
actions:
  stripe.refund:
    mcp: {}
    decision: allow
"""
    assert Policy.from_yaml(document).mcp_options("stripe.refund").not_executed_on_error is False


def test_T16_an_unknown_key_under_mcp_is_a_PolicyError():
    document = """
schema: ctrlrun.policy/v2
actions:
  stripe.refund:
    mcp:
      not_executed_on_errors: true
    decision: allow
"""
    with pytest.raises(PolicyError) as raised:
        Policy.from_yaml(document)

    assert "not_executed_on_errors" in str(raised.value)


def test_T16_mcp_must_be_a_mapping():
    document = """
schema: ctrlrun.policy/v2
actions:
  stripe.refund:
    mcp: true
    decision: allow
"""
    with pytest.raises(PolicyError):
        Policy.from_yaml(document)


def test_T16_an_unknown_key_in_an_entry_is_still_a_PolicyError():
    document = """
schema: ctrlrun.policy/v2
actions:
  stripe.refund:
    effekt: "refund:{payment_id}"
    decision: allow
"""
    with pytest.raises(PolicyError):
        Policy.from_yaml(document)


def test_T16_an_unknown_schema_names_both_supported_ones():
    with pytest.raises(PolicyError) as raised:
        Policy.from_yaml("schema: ctrlrun.policy/v3\nactions: {}\n")

    assert "ctrlrun.policy/v1" in str(raised.value)
    assert "ctrlrun.policy/v2" in str(raised.value)
