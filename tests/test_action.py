"""Action model, canonicalization and action_hash. SPEC-v0.1 §2; acceptance test T7."""

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from ctrlrun import Action, InvalidArgument, Principal, action_hash, canonicalize


def _action(**overrides: Any) -> Action:
    kwargs: dict[str, Any] = {
        "name": "stripe.refund",
        "arguments": {"payment_id": "txn_8231", "amount": 2000},
        "principal": Principal(agent="refund-agent", user="ana@example.com"),
        "resource": "payment:txn_8231",
        "environment": "production",
    }
    kwargs.update(overrides)
    return Action(**kwargs)


# --- canonical form (SPEC §2.2) -------------------------------------------------------


def test_T7_canonical_form_is_exactly_the_specified_serialization() -> None:
    action = Action(
        name="stripe.refund",
        arguments={"payment_id": "txn_8231", "amount": 2000, "meta": {"b": 1, "a": 2}},
        principal=Principal(agent="refund-agent"),
        resource="payment:txn_8231",
    )
    assert canonicalize(action) == (
        b'{"arguments":{"amount":2000,"meta":{"a":2,"b":1},"payment_id":"txn_8231"},'
        b'"environment":"production","name":"stripe.refund",'
        b'"principal":{"agent":"refund-agent","user":null},'
        b'"resource":"payment:txn_8231","schema":"ctrlrun.action/v1"}'
    )


def test_T7_nested_dicts_are_sorted_recursively() -> None:
    unsorted = _action(
        arguments={"z": {"c": {"z": 1, "a": 2}, "a": [{"y": 1, "x": 2}]}, "a": 1},
    )
    canonical = canonicalize(unsorted)
    assert b'"arguments":{"a":1,"z":{"a":[{"x":2,"y":1}],"c":{"a":2,"z":1}}}' in canonical


def test_T7_canonical_form_uses_ensure_ascii_false() -> None:
    action = _action(arguments={"note": "café ☕"}, resource="customer:Zoë")
    canonical = canonicalize(action)
    assert "café ☕".encode() in canonical
    assert "Zoë".encode() in canonical
    assert b"\\u" not in canonical


def test_T7_canonical_form_has_no_insignificant_whitespace() -> None:
    canonical = canonicalize(_action(arguments={"note": "a b", "items": [1, 2]}))
    assert b", " not in canonical
    assert b": " not in canonical


# --- hash stability (SPEC §2.3) -------------------------------------------------------


def test_T7_hash_is_identical_regardless_of_dict_insertion_order() -> None:
    first = _action(arguments={"payment_id": "txn_8231", "amount": 2000, "currency": "EUR"})
    second = _action(arguments={"currency": "EUR", "amount": 2000, "payment_id": "txn_8231"})
    assert first.arguments != {}
    assert action_hash(first) == action_hash(second)


def test_T7_hash_is_identical_regardless_of_nested_dict_insertion_order() -> None:
    first = _action(arguments={"meta": {"b": {"y": 1, "x": 2}, "a": 3}})
    second = _action(arguments={"meta": {"a": 3, "b": {"x": 2, "y": 1}}})
    assert action_hash(first) == action_hash(second)


@pytest.mark.parametrize(
    "override",
    [
        pytest.param({"name": "stripe.charge"}, id="name"),
        pytest.param(
            {"arguments": {"payment_id": "txn_8231", "amount": 2001}}, id="argument-value"
        ),
        pytest.param(
            {"arguments": {"payment_id": "txn_8232", "amount": 2000}}, id="argument-other"
        ),
        pytest.param(
            {"arguments": {"payment_id": "txn_8231", "amount": 2000, "currency": "EUR"}},
            id="argument-added",
        ),
        pytest.param({"arguments": {"payment_id": "txn_8231"}}, id="argument-removed"),
        pytest.param({"resource": "payment:txn_9999"}, id="resource"),
        pytest.param({"resource": None}, id="resource-absent"),
        pytest.param(
            {"principal": Principal(agent="other-agent", user="ana@example.com")},
            id="principal-agent",
        ),
        pytest.param(
            {"principal": Principal(agent="refund-agent", user="bob@example.com")},
            id="principal-user",
        ),
        pytest.param(
            {"principal": Principal(agent="refund-agent", user=None)},
            id="principal-user-absent",
        ),
        pytest.param({"environment": "staging"}, id="environment"),
    ],
)
def test_T7_hash_changes_when_any_single_field_changes(override: dict[str, Any]) -> None:
    assert action_hash(_action(**override)) != action_hash(_action())


def test_T7_hash_distinguishes_bool_from_int() -> None:
    assert action_hash(_action(arguments={"dry_run": True})) != action_hash(
        _action(arguments={"dry_run": 1})
    )


def test_T7_action_id_is_excluded_from_the_hash() -> None:
    first = _action()
    second = _action()
    assert first.action_id != second.action_id
    assert action_hash(first) == action_hash(second)
    assert first.action_id.encode() not in canonicalize(first)


def test_T7_hash_is_sha256_of_the_canonical_form() -> None:
    action = _action()
    digest = hashlib.sha256(canonicalize(action)).hexdigest()
    assert action_hash(action) == f"sha256:{digest}"
    assert action.action_hash == action_hash(action)


def test_T7_action_id_has_the_specified_shape() -> None:
    action_id = _action().action_id
    assert action_id.startswith("act_")
    suffix = action_id.removeprefix("act_")
    assert len(suffix) == 32
    assert set(suffix) <= set("0123456789abcdef")


def test_T7_golden_hash() -> None:
    """Pin the canonical form of a fully populated Action.

    This value is the wire contract: approvals granted against it must still verify.
    Changing it means changing the canonical form, which requires bumping the schema
    version in SPEC-v0.1 §2.2 (and `ACTION_SCHEMA`). Never re-baseline this literal
    to make a failing test pass.
    """
    action = Action(
        name="stripe.refund",
        arguments={
            "payment_id": "txn_8231",
            "amount": 2000,
            "currency": "EUR",
            "dry_run": False,
            "reason": None,
            "note": "remboursement café ☕",
            "items": [1, "a", True, None, {"k": ["deep"]}],
            "meta": {"z": {"b": 1, "a": 2}, "a": [{"y": 1, "x": 2}]},
        },
        principal=Principal(agent="refund-agent", user="ana@example.com"),
        resource="payment:txn_8231",
        environment="production",
    )
    assert action.action_hash == (
        "sha256:c1587edf8b9f26268211ef7699f8c891164993c07319ee251d85518d4fdcb7fd"
    )


# --- proposal identity: equality and hashing (SPEC §2.1) ------------------------------


def test_T7_actions_with_identical_content_are_different_proposals() -> None:
    first = _action()
    second = _action()
    assert first.action_id != second.action_id
    assert first.action_hash == second.action_hash
    assert first != second


def test_T7_actions_are_equal_when_action_id_is_equal() -> None:
    first = _action()
    assert _action(action_id=first.action_id) == first
    assert _action(action_id=first.action_id, environment="staging") == first


def test_T7_actions_are_hashable_and_usable_in_a_set() -> None:
    first = _action()
    second = _action()
    assert hash(first) == hash(first.action_id)
    assert len({first, second, _action(action_id=first.action_id)}) == 2


def test_T7_an_action_is_not_equal_to_a_non_action() -> None:
    assert _action() != "act_000000000000"


# --- argument types (SPEC §2.3) -------------------------------------------------------


@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param({"amount": 20.0}, id="top-level"),
        pytest.param({"amounts": [1, 20.0]}, id="in-list"),
        pytest.param({"meta": {"amount": 20.0}}, id="in-dict"),
        pytest.param({"meta": {"rows": [{"amount": 20.0}]}}, id="deeply-nested"),
    ],
)
def test_T7_float_argument_raises_invalid_argument(arguments: dict[str, Any]) -> None:
    with pytest.raises(InvalidArgument):
        _action(arguments=arguments)


@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param({"amount": Decimal("20.00")}, id="decimal"),
        pytest.param({"items": {1, 2}}, id="set"),
        pytest.param({"blob": b"bytes"}, id="bytes"),
        pytest.param({"who": object()}, id="object"),
        pytest.param({"at": datetime(2026, 9, 3, tzinfo=UTC)}, id="datetime"),
        pytest.param({"amount": complex(1, 2)}, id="complex"),
        pytest.param({"meta": {"blob": b"bytes"}}, id="nested-bytes"),
        pytest.param({"rows": [{"amount": Decimal("1")}]}, id="deeply-nested-decimal"),
    ],
)
def test_T7_disallowed_argument_types_raise_invalid_argument(arguments: dict[str, Any]) -> None:
    with pytest.raises(InvalidArgument):
        _action(arguments=arguments)


@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param({1: "one"}, id="int-key"),
        pytest.param({None: "none"}, id="none-key"),
        pytest.param({("a", "b"): "tuple"}, id="tuple-key"),
        pytest.param({"meta": {2: "two"}}, id="nested-int-key"),
    ],
)
def test_T7_non_string_argument_key_raises_invalid_argument(arguments: dict[Any, Any]) -> None:
    with pytest.raises(InvalidArgument):
        _action(arguments=arguments)


def test_T7_a_tuple_and_the_equivalent_list_produce_the_same_hash() -> None:
    from_tuple = _action(arguments={"items": (1, "a", {"k": ("deep",)})})
    from_list = _action(arguments={"items": [1, "a", {"k": ["deep"]}]})
    assert from_tuple.action_hash == from_list.action_hash
    assert b'"items":[1,"a",{"k":["deep"]}]' in canonicalize(from_tuple)


def test_T7_an_action_can_be_rebuilt_from_another_actions_stored_arguments() -> None:
    original = _action(arguments={"items": [1, 2], "meta": {"a": 1}})
    rebuilt = _action(arguments=original.arguments)
    assert rebuilt.action_hash == original.action_hash
    assert rebuilt.arguments == original.arguments


def test_T7_an_action_can_be_rebuilt_from_canonical_arguments() -> None:
    original = _action(arguments={"items": [1, 2], "meta": {"a": 1}})
    rebuilt = _action(arguments=original.canonical_arguments)
    assert rebuilt.action_hash == original.action_hash


def test_T7_allowed_argument_types_are_accepted() -> None:
    action = _action(
        arguments={
            "text": "txn_8231",
            "count": 2000,
            "flag": False,
            "missing": None,
            "items": [1, "a", True, None, {"k": [2]}],
            "meta": {"nested": {"deep": "ok"}},
        }
    )
    assert action.action_hash.startswith("sha256:")


def test_T7_arguments_are_snapshotted_at_construction() -> None:
    arguments: dict[str, Any] = {"payment_id": "txn_8231", "amount": 2000}
    action = _action(arguments=arguments)
    before = action.action_hash
    arguments["amount"] = 999999
    assert action.action_hash == before
    with pytest.raises(TypeError):
        action.arguments["amount"] = 999999  # type: ignore[index]


# --- deep freeze and canonical execution arguments (SPEC §2.2) ------------------------


def test_T7_stored_arguments_are_deep_frozen() -> None:
    action = _action(arguments={"meta": {"tags": ["a", "b"], "nested": {"k": "v"}}})
    meta = action.arguments["meta"]
    assert isinstance(meta, Mapping)
    assert meta["tags"] == ("a", "b")
    assert isinstance(meta["nested"], Mapping)
    with pytest.raises(TypeError):
        meta["nested"] = {}
    with pytest.raises(TypeError):
        meta["nested"]["k"] = "mutated"
    with pytest.raises(TypeError):
        meta["tags"][0] = "mutated"  # type: ignore[index]


def test_T7_lists_inside_lists_are_frozen_to_tuples() -> None:
    action = _action(arguments={"rows": [[1, 2], [{"k": ["deep"]}]]})
    rows = action.arguments["rows"]
    assert rows == ((1, 2), ({"k": ("deep",)},))
    with pytest.raises(TypeError):
        rows[0][0] = 99


def test_T7_mutating_a_nested_list_after_construction_changes_nothing() -> None:
    nested: list[Any] = [1, {"deep": ["x"]}]
    arguments: dict[str, Any] = {"payment_id": "txn_8231", "items": nested}
    action = _action(arguments=arguments)

    hash_before = action.action_hash
    canonical_before = canonicalize(action)
    executed_before = action.canonical_arguments

    nested.append("appended")
    nested[1]["deep"].append("also appended")
    arguments["items"] = ["replaced"]

    assert action.action_hash == hash_before
    assert canonicalize(action) == canonical_before
    assert action.canonical_arguments == executed_before
    assert action.canonical_arguments["items"] == [1, {"deep": ["x"]}]


def test_T7_canonical_arguments_are_parsed_back_from_the_canonical_form() -> None:
    action = _action(arguments={"items": [1, {"k": ["deep"]}], "meta": {"a": 1}})
    assert action.canonical_arguments == json.loads(canonicalize(action))["arguments"]


def test_T7_canonical_arguments_are_plain_mutable_containers() -> None:
    action = _action(arguments={"items": [1, 2], "meta": {"a": 1}})
    executed = action.canonical_arguments
    assert type(executed) is dict
    assert type(executed["items"]) is list
    assert type(executed["meta"]) is dict


def test_T7_canonical_arguments_are_fresh_on_every_access() -> None:
    action = _action(arguments={"items": [1, 2], "meta": {"a": 1}})
    executed = action.canonical_arguments
    executed["items"].append(3)
    executed["meta"]["a"] = 999
    executed["injected"] = True

    assert action.canonical_arguments == {"items": [1, 2], "meta": {"a": 1}}
    assert action.arguments["items"] == (1, 2)
    assert action.action_hash == _action(arguments={"items": [1, 2], "meta": {"a": 1}}).action_hash


@pytest.mark.parametrize(
    "override",
    [
        pytest.param({"name": ""}, id="empty-name"),
        pytest.param({"environment": ""}, id="empty-environment"),
        pytest.param({"resource": ""}, id="empty-resource"),
    ],
)
def test_T7_empty_identity_fields_raise_invalid_argument(override: dict[str, Any]) -> None:
    with pytest.raises(InvalidArgument):
        _action(**override)


@pytest.mark.parametrize(
    "principal",
    [
        pytest.param({"agent": ""}, id="empty-agent"),
        pytest.param({"agent": "refund-agent", "user": ""}, id="empty-user"),
    ],
)
def test_T7_empty_principal_fields_raise_invalid_argument(principal: dict[str, Any]) -> None:
    with pytest.raises(InvalidArgument):
        Principal(**principal)
