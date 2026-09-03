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
        _decide(_one_rule("amount_lte", "5"), {"currency": "EUR"})
    assert [record for record in caplog.records if record.levelno == logging.WARNING] == []


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
        pytest.param(
            "schema: ctrlrun.policy/v2\nactions:\n  customer.read:\n    decision: allow\n",
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
