# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""The policy document grammar: schemas, strict loading, and the condition evaluator.

**Below `policy.py` and `authority.py`, and owned by neither.** `docs/ARCHITECTURE.md` §6 records
that `authority.py` imports the condition parser and evaluator from `policy.py` deliberately,
because a grant's `constraints:` is a rule's `when:` syntax and `SPEC-v0.3.md` §4.5 says the two
axes MUST share one evaluator: a second one would be a second place for `True` to start comparing
equal to `1`. That requirement is unchanged. What changed is where the one evaluator lives.

It lived in `policy.py`, so `authority.py` imported upward, while `policy.py` reached back into
`authority.py` from `_canonical_authority` and `hash_with_authority`. That was a cycle, and the
last one §6 had to carry an exception for. Neither direction could be removed on its own:
`_from_section` constructs an `Authority` and `canonical_grants` consumes one, so neither moves
below `policy.py`, and moving their two callers up to `control.py` would change what
`policy_hash` is taken over, which is evidence in every receipt rather than an implementation
detail.

Moving the *shared* half down removes the cycle without touching either. The evaluator is now
owned by neither axis, which is what §4.5 asks for more literally than the old arrangement did.

**Nothing here changed but its address.** Every block was moved verbatim, comments included, and
`policy.py` re-exports all of it, so `from ctrlrun.policy import Condition, parse_conditions`
still resolves and `SPEC-v0.3.md` §8's frozen line stays literally true.
"""

from __future__ import annotations

import logging
import operator
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final

import yaml

from .errors import PolicyError

#: Pinned to `ctrlrun.policy` rather than taken from `__name__`. These functions logged there
#: before the move, and a refactor that silently re-routes a log line is a refactor that loses
#: whatever handler or filter an operator pointed at it.
_LOG = logging.getLogger("ctrlrun.policy")


#: The schema `ctrlrun init` writes, and the one every v0.1 file declares.
POLICY_SCHEMA: Final = "ctrlrun.policy/v1"
#: SPEC-v0.2 §3.1 — required by any document using `effect:`, `resource:` or `mcp:`. A v2
#: file fails to load on v0.1, correctly: v0.1 would ignore the effect template and execute
#: with no duplicate protection at all. The schema string is the only thing standing between
#: those two outcomes, so it is not optional and not inferred.
POLICY_SCHEMA_V2: Final = "ctrlrun.policy/v2"
#: SPEC-v0.3 §12.1 — required by any document using `environment:`, and later by `authority:`
#: and `mode:`. A v3 key in an older document is a load error naming the key and the schema,
#: for the reason v0.2 gives: a reader that ignored it would run with a guarantee switched off.
POLICY_SCHEMA_V3: Final = "ctrlrun.policy/v3"
#: SPEC-v0.6 §7.1, §9.5 — required by any document using `version:`, `controls:` or `data:`.
#: `v1`, `v2` and `v3` documents load unchanged and get a `policy_hash` like any other; only
#: those three keys need `v4`.
POLICY_SCHEMA_V4: Final = "ctrlrun.policy/v4"
#: SPEC-v0.7 §5.3 — required by any document using `max_attempts:`. An 0.6.1 reader refuses a
#: `v5` document outright, which is the fail-closed direction: a reader that ignored the key
#: would renew without a ceiling, which is the behaviour the key exists to bound.
POLICY_SCHEMA_V5: Final = "ctrlrun.policy/v5"
#: SPEC-v0.8 §11.3: `v6` adds `approver_role` on a control entry, and items 4 and 5 add
#: their keys under it. The version moves once, here, for the reason §11.4 gives: an older
#: reader must refuse a document whose keys it would otherwise ignore, and a reader that
#: ignored `approver_role` would run a deployment believing nobody was gated.
POLICY_SCHEMA_V6: Final = "ctrlrun.policy/v6"
#: SPEC-v0.9 §10.1: `v7` adds `tasks` on a grant (item 1) and `budgets` on one (item 3). The
#: version moves once, here, with item 1, and item 3 fills it under the version already in
#: place: two branches racing a schema bump is how a catalogue ends up with a stub row.
POLICY_SCHEMA_V7: Final = "ctrlrun.policy/v7"
#: SPEC-v0.10 §4.6 — `v8` adds one action-entry key, `upstream:`. Bumped once, by item 3.
POLICY_SCHEMA_V8: Final = "ctrlrun.policy/v8"
#: All of them, newest last, for the message an unknown schema produces. **In version order**,
#: which `_at_least` reads: a version added out of order would make every gate below lie.
SUPPORTED_SCHEMAS: Final = (
    POLICY_SCHEMA,
    POLICY_SCHEMA_V2,
    POLICY_SCHEMA_V3,
    POLICY_SCHEMA_V4,
    POLICY_SCHEMA_V5,
    POLICY_SCHEMA_V6,
    POLICY_SCHEMA_V7,
    POLICY_SCHEMA_V8,
)


def _at_least(schema: str, minimum: str) -> bool:
    """Whether `schema` is `minimum` or a later version (SPEC-v0.7 §5.3).

    Each version is a **superset** of the one before: a `v5` document may use every key any
    earlier version allows. Three gates in this module compared for equality instead, which was
    right while `v4` was the newest and became wrong the moment it was not; `require_v3`'s own
    comment predicted it. An unknown schema is refused before any of them runs, so a name that
    is not in `SUPPORTED_SCHEMAS` cannot reach here from `Policy._from_document`; where one does,
    from `authority.py`'s standalone path, it is treated as too old, which is fail-closed.
    """
    if schema not in SUPPORTED_SCHEMAS or minimum not in SUPPORTED_SCHEMAS:
        return False
    return SUPPORTED_SCHEMAS.index(schema) >= SUPPORTED_SCHEMAS.index(minimum)


#: SPEC-v0.3 §6.1 — the two values of the top-level `mode:` key, and nothing else. Absent
#: means `enforce`: the fail-closed default, so a document that predates the key enforces.
MODE_KEY: Final = "mode"
_NUMERIC_COMPARE: Final[Mapping[str, Callable[[int, int], bool]]] = {
    "lt": operator.lt,
    "lte": operator.le,
    "gt": operator.gt,
    "gte": operator.ge,
}
_OPERATORS: Final = ("eq", "neq", "in", *_NUMERIC_COMPARE)
#: Longest first, so `amount_neq` reads as (amount, neq) and never as (amount_n, eq).
_OPERATORS_BY_LENGTH: Final = tuple(sorted(_OPERATORS, key=len, reverse=True))
#: SPEC-v0.3 §12.1 — the top-level keys that need `ctrlrun.policy/v3`, and what an older
#: reader would do with each if it ignored one.
_V3_TOP_LEVEL_KEYS: Final[Mapping[str, str]] = {
    "environment": ("an older reader would ignore it and put every action in the wrong deployment"),
    "authority": (
        "an older reader would ignore it and run every action with no authority check at all"
    ),
    "mode": ("an older reader would enforce a configuration that was deployed to observe"),
}
RESERVED_ARGUMENTS: Final = frozenset(
    {
        "action_id",
        "agent",
        "claims",
        "data_scope",
        "environment",
        "expires_at",
        "issuer",
        "principal",
        "resource",
        "user",
    }
)
#: SPEC-v0.6 §7.4 — names refused as **arguments** and permitted as **condition subjects**,
#: resolved at evaluation from something other than `action.canonical_arguments`.
#:
#: The distinction is what makes `data_scope` implementable at all. Today one check does both
#: jobs: the splitter refuses a condition whose subject is in `RESERVED_ARGUMENTS`, which is how
#: `claims_eq:` becomes a load error. Adding `data_scope` to that set unchanged would have made
#: `data_scope_in:` a load error too -- the very condition §7.4 asks operators to write.
#:
#: **A name here is still refused as an argument**, so one name never means two things in one
#: document. And this list is the *policy evaluator's*: authority `constraints:` do not consult
#: it, so a grant naming `data_scope` is refused exactly as it always was (§11 puts matching a
#: grant on a data label out of scope).
DERIVED_SUBJECTS: Final = frozenset({"data_scope"})


def _is_int(value: object) -> bool:
    """True for a real int. `bool` subclasses int in Python; SPEC-v0.1 §3.2 excludes it."""
    return isinstance(value, int) and not isinstance(value, bool)


def _type_name(value: object) -> str:
    return type(value).__name__


def _equal(value: object, operand: object) -> bool:
    """Type-strict equality: `True` never equals `1`, and a list never equals a scalar.

    SPEC: §3.2 — equality is type-strict and applies recursively inside containers.
    Canonical arguments distinguish bool from int (§2.3), so conditions must too, or a
    policy written for `1` would match `True`.
    """
    if isinstance(value, bool) or isinstance(operand, bool):
        return value is operand
    if isinstance(value, Mapping) and isinstance(operand, Mapping):
        return value.keys() == operand.keys() and all(
            _equal(value[key], operand[key]) for key in value
        )
    if isinstance(value, list | tuple) and isinstance(operand, list | tuple):
        return len(value) == len(operand) and all(
            _equal(item, other) for item, other in zip(value, operand, strict=True)
        )
    if _is_container(value) or _is_container(operand):
        return False
    return bool(value == operand)


def _is_container(value: object) -> bool:
    return isinstance(value, Mapping | list | tuple)


@dataclass(frozen=True)
class Condition:
    """One `<argument>_<op>: operand` test against an action's arguments (SPEC-v0.1 §3.2).

    Public since SPEC-v0.3 §11, because a `Grant`'s constraints are made of them and the two
    axes share one evaluator: a second implementation would be a second place for `True` to
    start comparing equal to `1`. `key` is the raw condition key the author wrote.
    """

    key: str
    argument: str
    op: str
    operand: Any

    def matches(self, action_name: str, arguments: Mapping[str, Any]) -> bool:
        if self.argument not in arguments:
            # SPEC §3.2 — still false, never an error, but never silent either. Defaults are
            # applied when a call is bound (§8), so an argument is either always present or
            # never: an absent one is a typo, and silence let a mistyped rule disappear into
            # a catch-all below it.
            _LOG.warning(
                "%s: condition %s ignored: the action has no argument %r (it has: %s)",
                action_name,
                self.key,
                self.argument,
                ", ".join(sorted(arguments)) or "none",
            )
            return False
        value = arguments[self.argument]
        if self.op in {"eq", "neq"} and self.argument in DERIVED_SUBJECTS:
            # SPEC-v0.6 §7.4: *"`_eq` and `_neq` compare the whole set."* **The whole set, and
            # a set has no order.** The derived value is a `sorted(...)` list, and `_equal` on
            # lists is order-sensitive -- so an independent review found `data_scope_eq: [phi,
            # internal]` never matching, silently, while `[internal, phi]` did. An operator
            # writing the labels in the order their own `data:` map declares them gets a rule
            # that never fires, with no warning: the key splits, the subject is present, and
            # `matches` simply returns `False` and falls through to whatever is below. Where
            # the rule was the `deny` or `approve`, that is fail-open.
            #
            # Narrowed to `DERIVED_SUBJECTS` for exactly the reason `_in` below is: an ordinary
            # list-valued argument means *this list*, and `value_eq: [1, 2]` against `[2, 1]`
            # must stay false. This branch is one line away from the one that regressed when it
            # was written too wide, and it is written narrow for the same reason.
            if not isinstance(value, list | tuple) or not isinstance(self.operand, list | tuple):
                return (
                    _equal(value, self.operand)
                    if self.op == "eq"
                    else not _equal(value, self.operand)
                )
            same = frozenset(value) == frozenset(self.operand)
            return same if self.op == "eq" else not same
        if self.op == "eq":
            return _equal(value, self.operand)
        if self.op == "neq":
            return not _equal(value, self.operand)
        if self.op == "in":
            if self.argument in DERIVED_SUBJECTS and isinstance(value, list | tuple):
                # SPEC-v0.6 §7.4 — a **derived, set-valued** subject intersects the list, which
                # is the membership `_in` already expresses one element at a time. §7.4 adds no
                # operator for it: `contains` and `not_in` would land in `_OPERATORS`, which
                # authority `constraints:` share (`v0.3 §4.5` -- one implementation, not two),
                # and §11 puts matching a grant on a data label out of scope.
                #
                # **Narrowed to derived subjects, and the first version was not.** Testing every
                # list-valued subject changed `_in` for ordinary arguments: `value_in: [[1, 2]]`
                # against `value = [1, 2]` means *this exact list is one of the operands* and has
                # since v0.1, and intersecting broke it. A rule about a new subject may not
                # quietly re-mean an operator for the old ones.
                return any(_equal(item, candidate) for item in value for candidate in self.operand)
            return any(_equal(value, item) for item in self.operand)
        if not _is_int(value):
            _LOG.warning(
                "%s: condition %s ignored: argument %r is %s, not int",
                action_name,
                self.key,
                self.argument,
                _type_name(value),
            )
            return False
        return _NUMERIC_COMPARE[self.op](value, self.operand)


class _StrictLoader(yaml.SafeLoader):  # type: ignore[misc]  # PyYAML ships no stubs
    """`yaml.SafeLoader` that refuses a repeated mapping key instead of resolving it.

    YAML says a duplicated key is an error and PyYAML resolves it to the last one anyway,
    silently. That is a fail-**open** in the authority document: a grant written as

        actions: ["payments.refund"]
        actions: ["**"]

    -- the shape of a half-finished narrowing edit -- loads as `("**",)` with no warning, and
    `ctrlrun verify` reads this same loader, so nothing downstream catches it either. Every
    other mistake in these documents is refused, the key sets being closed at every level, so
    a clean load reads as "the document is what I meant".

    The node carries the line, which is exactly what the message needs.
    """

    def construct_mapping(
        self,
        node: Any,  # noqa: ANN401 - PyYAML's node type, and PyYAML ships no stubs
        deep: bool = False,
    ) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=True)
            try:
                duplicate = key in seen
            except TypeError:  # an unhashable key; the base class refuses it below
                continue
            if duplicate:
                mark = key_node.start_mark
                raise yaml.constructor.ConstructorError(
                    None,
                    None,
                    f"duplicate key {key!r} on line {mark.line + 1}, column {mark.column + 1}",
                    mark,
                )
            seen.add(key)
        return super().construct_mapping(node, deep)  # type: ignore[no-any-return]


def strict_load(text: str, source: str) -> Any:  # noqa: ANN401 - any YAML scalar or node
    """`yaml.safe_load`, refusing a repeated key. The one loader for every ctrlrun document.

    **`yaml.YAMLError` is not the whole contract.** PyYAML converts a scalar before it has
    decided the document is well formed, and three conversions raise the interpreter's own
    exception rather than a `YAMLError`:

    - `"\\U0001f600"` with too many digits overflows converting the codepoint to a C int,
      which is `OverflowError`. The fuzzer found this one after 174,380 executions;
    - `"\\U00110000"` is a legal-looking escape above the Unicode maximum, and `chr()` says
      `ValueError`;
    - `2026-99-99` is a `ValueError` from `datetime`, and is the one that matters: a mistyped
      date is a thing a person writes in a real policy, not a thing a fuzzer invents.

    Each was a crash where the caller was promised a refusal, and `Policy.from_yaml` says
    "anything malformed raises `PolicyError`" without qualification. The document is refused
    either way, so nothing unsafe was ever admitted; what leaked was the exception type, and a
    caller that catches `PolicyError` around a policy load would not have caught these.

    `ValueError` and `OverflowError` are therefore refusals too. The catch is deliberately not
    `Exception`: this function calls one thing, so a `MemoryError` or a `KeyboardInterrupt`
    here is not the document's fault and must not be reported as one. `RecursionError` is left
    uncaught for the same reason, and `fuzz/properties.py` says so where it excludes it.
    """
    try:
        # `_StrictLoader` derives from `SafeLoader`, so this constructs no arbitrary object.
        return yaml.load(text, Loader=_StrictLoader)
    except yaml.YAMLError as exc:
        raise PolicyError(f"{source}: not valid YAML: {exc}") from exc
    except (ValueError, OverflowError) as exc:
        raise PolicyError(f"{source}: not valid YAML: {type(exc).__name__}: {exc}") from exc


def require_v3(document: Mapping[Any, Any], schema: str, source: str) -> None:
    """Refuse a `ctrlrun.policy/v3` key in an older document (SPEC-v0.3 §12.1).

    Shared with `authority.py`, which reads the same key from a document the policy loader
    may never see: SPEC-v0.3 §8.3's `--authority` file carries `schema` and `authority` and
    nothing else, so the check has to exist on both paths rather than on whichever runs first.
    """
    # v4 is a superset: a `v4` document may use every `v3` key. Comparing for equality here was
    # right while v3 was the newest and becomes a bug the moment it is not. SPEC-v0.7 §5.3: the
    # membership test that replaced it had the same shape, so `v5` reads it through `_at_least`.
    if _at_least(schema, POLICY_SCHEMA_V3):
        return
    for key, consequence in _V3_TOP_LEVEL_KEYS.items():
        if key in document:
            raise PolicyError(
                f"{source}: {key!r} needs 'schema: {POLICY_SCHEMA_V3}'; this document "
                f"declares {schema!r}, and {consequence}"
            )


#: SPEC-v0.9 §10.1 — the grant-entry keys that need `ctrlrun.policy/v7`, and what an older
#: reader would do with each. Both are authorization dimensions, so both fail the same way: an
#: older reader ignores the key and grants more than the document says.
_V7_GRANT_KEYS: Final[Mapping[str, str]] = {
    "tasks": (
        "an older reader would ignore the binding and authorise the grant on every task, which "
        "is the whole of what the key restricts"
    ),
    "budgets": (
        "an older reader would ignore the limit and let the grant spend without bound, which is "
        "the whole of what the key restricts"
    ),
}


def require_v7(document: Mapping[Any, Any], schema: str, source: str) -> None:
    """Refuse a `ctrlrun.policy/v7` grant key in an older document (SPEC-v0.9 §10.1).

    `require_v3`'s shape, two versions up, and shared with `authority.py` for the same reason:
    `SPEC-v0.3 §8.3`'s `--authority` file carries `schema` and `authority` and nothing else, so
    a gate that lived only in the policy loader would not run on that path at all. The keys are
    one level deeper than v3's and v4's, hence the walk rather than a membership test.
    """
    if _at_least(schema, POLICY_SCHEMA_V7):
        return
    section = document.get("authority")
    if not isinstance(section, Mapping):
        return
    entries: list[Any] = []
    grants = section.get("grants")
    if isinstance(grants, list):
        entries.extend(grants)
    elif isinstance(grants, Mapping):
        entries.extend(grants.values())
    envelopes = section.get("break_glass")
    if isinstance(envelopes, Mapping):
        entries.extend(envelopes.values())
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        for key, consequence in _V7_GRANT_KEYS.items():
            if key in entry:
                raise PolicyError(
                    f"{source}: {key!r} on a grant needs 'schema: {POLICY_SCHEMA_V7}'; this "
                    f"document declares {schema!r}, and {consequence}"
                )


def reject_nested_mode(mapping: Mapping[Any, Any], where: str) -> None:
    """Refuse a `mode:` anywhere but the top level of the policy document (SPEC-v0.3 §6.1).

    The closed key sets of `v0.1 §3.1` would already refuse it as unknown, wherever they
    reach. This runs first and for its *message*: "unknown key 'mode'" reads as "ctrlrun has
    no such setting", and the author who wrote it here believes they have observed one action
    while enforcing the rest. A partially-enforced configuration is the failure mode the
    top-level-only rule exists to prevent, so the error says which rule was broken.

    Shared with `authority.py`, which owns the two nestings inside an `authority:` section and
    parses documents the policy loader never reads (§4.8).
    """
    if MODE_KEY in mapping:
        raise PolicyError(
            f"{where}: 'mode' is top level and nothing else (SPEC-v0.3 §6.1). A configuration "
            "where some actions are observed and some are enforced is one where nobody can say "
            "whether an action was permitted or merely watched; move it beside 'schema:'"
        )


def parse_conditions(
    mapping: Mapping[Any, Any], *, where: str, allow_derived: bool = False
) -> Mapping[str, Condition]:
    """Parse a `when:`-shaped mapping into conditions, keyed by the raw condition key.

    Public since SPEC-v0.3 §11: a grant's `constraints:` is in exactly this syntax and MUST be
    parsed by this code (§4.5). The key is injective given §3.2's longest-suffix split, which
    is what lets `Grant`'s containment check look a dimension up by name.

    `allow_derived` admits §7.4's derived subjects and **defaults to off**, so `authority.py` --
    which calls this without it — sees exactly the surface it saw in v0.3. A grant naming
    `data_scope` is refused as it always was, which is what keeps §11's *"matching a grant on a
    data label"* out of v0.6 rather than letting it in through a shared parser.
    """
    conditions: dict[str, Condition] = {}
    for key, operand in mapping.items():
        condition = _parse_condition(key, operand, where, allow_derived)
        conditions[condition.key] = condition
    return conditions


def _parse_condition(
    key: object, operand: object, where: str, allow_derived: bool = False
) -> Condition:
    if not isinstance(key, str):
        raise PolicyError(f"{where}: condition keys must be strings, got {key!r}")
    argument, op = _split_condition_key(key, where, allow_derived)
    return Condition(
        key=key,
        argument=argument,
        op=op,
        operand=_parse_operand(op, operand, where, key),
    )


def _split_condition_key(key: str, where: str, allow_derived: bool = False) -> tuple[str, str]:
    for op in _OPERATORS_BY_LENGTH:
        suffix = f"_{op}"
        if key.endswith(suffix) and len(key) > len(suffix):
            argument = key[: -len(suffix)]
            if argument in DERIVED_SUBJECTS and allow_derived:
                return argument, op
            if argument in DERIVED_SUBJECTS:
                # Reached only where derived subjects are not admitted -- an authority
                # `constraints:` mapping. §11 puts *"matching a grant on a data label"* out of
                # v0.6, and the message says which surface refused it rather than claiming the
                # name is an `Action` field, which `data_scope` is not.
                raise PolicyError(
                    f"{where}: condition {key!r} addresses {argument!r}, which a policy rule may "
                    f"see and a grant may not. Matching a grant on {argument!r} is not in v0.6; "
                    "write the rule in the policy instead."
                )
            if argument in RESERVED_ARGUMENTS:
                raise PolicyError(
                    f"{where}: condition {key!r} names the Action field {argument!r}, not an "
                    "argument; a v0.1 condition can only address the action's arguments, so "
                    "this rule would never match. If the protected function really does take "
                    f"an argument called {argument!r}, rename it."
                )
            return argument, op
    raise PolicyError(
        f"{where}: condition {key!r} must be '<argument>_<op>' where op is one of "
        f"{', '.join(sorted(_OPERATORS))}"
    )


def _parse_operand(op: str, operand: object, where: str, key: str) -> object:
    if op in _NUMERIC_COMPARE:
        if not _is_int(operand):
            # SPEC-v0.3 §4.5 — the message names the representation rule, because the operator
            # who wrote `amount_lte: "2000.00"` has hit a real limit and not a typo: only
            # integer arguments can be bounded, so a deployment representing money as decimal
            # strings cannot express an amount ceiling in a grant at all.
            raise PolicyError(
                f"{where}: condition {key!r}: a numeric operator needs an int operand, "
                f"got {_type_name(operand)}. Only integers can be bounded, so an amount that "
                "a rule or a grant compares is written in integer minor units "
                "(amount_lte: 200000), never as a decimal string"
            )
        return operand
    if op == "in":
        if not isinstance(operand, list):
            raise PolicyError(
                f"{where}: condition {key!r}: '_in' needs a list operand, got {_type_name(operand)}"
            )
        return tuple(_checked_operand(item, where, key) for item in operand)
    checked = _checked_operand(operand, where, key)
    if op in {"eq", "neq"} and isinstance(checked, list | tuple):
        # A derived, set-valued subject is compared with `frozenset(...)`, so every element
        # has to be hashable. `data_scope_eq: [[phi]]` used to load clean and then raise
        # `TypeError: unhashable type: 'list'` on every evaluation of the action -- out of
        # `Control.execute`, and not as a `CTRLRunError`, so an application catching the
        # kernel's own errors did not catch it. Refuse here, where the message can name the
        # condition and the operator can find the line.
        for item in checked:
            if isinstance(item, list | tuple | Mapping):
                raise PolicyError(
                    f"{where}: condition {key!r}: a set-valued operand holds strings, "
                    f"got {_type_name(item)}. Write the labels as a flat list "
                    "(data_scope_eq: [phi, pci]), not nested"
                )
    return checked


def _checked_operand(operand: object, where: str, key: str) -> object:
    """Validate an operand against the argument types allowed by SPEC-v0.1 §2.3."""
    if isinstance(operand, float):
        raise PolicyError(
            f"{where}: condition {key!r}: float operands are not allowed; use integer minor "
            "units (amount_lte: 50000) or a decimal string"
        )
    if operand is None or isinstance(operand, str | int):  # bool is a subclass of int
        return operand
    if isinstance(operand, Mapping):
        for name in operand:
            if not isinstance(name, str):
                raise PolicyError(
                    f"{where}: condition {key!r}: operand keys must be strings, got {name!r}"
                )
        return {name: _checked_operand(value, where, key) for name, value in operand.items()}
    if isinstance(operand, list):
        return [_checked_operand(item, where, key) for item in operand]
    raise PolicyError(
        f"{where}: condition {key!r}: {_type_name(operand)} is not an allowed operand type"
    )
