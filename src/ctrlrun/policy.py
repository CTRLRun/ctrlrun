"""Policy loading and rule evaluation to a Decision. Build-list item 2; SPEC-v0.1 §3.

SPEC-v0.2 §3 adds per-action `effect:` and `resource:` templates, because the gateway has no
decorator to carry one. That is the only reason this module imports the template grammar from
`effect.py`: it validates the syntax at load time, so a typo fails at startup rather than
raising `EffectKeyError` at the moment an agent tries to move money. It stays ignorant of
effect *state* — records, transitions and reservations are none of policy's business
(ARCHITECTURE §6).
"""

from __future__ import annotations

import logging
import operator
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

import yaml

from .action import Action
from .effect import template_placeholders
from .errors import InvalidArgument, PolicyError

#: The schema `ctrlrun init` writes, and the one every v0.1 file declares.
POLICY_SCHEMA: Final = "ctrlrun.policy/v1"

#: SPEC-v0.2 §3.1 — required by any document using `effect:`, `resource:` or `mcp:`. A v2
#: file fails to load on v0.1, correctly: v0.1 would ignore the effect template and execute
#: with no duplicate protection at all. The schema string is the only thing standing between
#: those two outcomes, so it is not optional and not inferred.
POLICY_SCHEMA_V2: Final = "ctrlrun.policy/v2"

#: Both, newest last, for the message an unknown schema produces.
SUPPORTED_SCHEMAS: Final = (POLICY_SCHEMA, POLICY_SCHEMA_V2)

CONFIG_ENV_VAR: Final = "CTRLRUN_CONFIG"
DEFAULT_POLICY_FILENAME: Final = "ctrlrun.yaml"

#: Reasons attached to an Evaluation that no rule produced.
UNKNOWN_ACTION: Final = "unknown_action"
NO_MATCHING_RULE: Final = "no_matching_rule"
BARE_DECISION: Final = "decision"

_LOG = logging.getLogger(__name__)

_NUMERIC_COMPARE: Final[Mapping[str, Callable[[int, int], bool]]] = {
    "lt": operator.lt,
    "lte": operator.le,
    "gt": operator.gt,
    "gte": operator.ge,
}
_OPERATORS: Final = ("eq", "neq", "in", *_NUMERIC_COMPARE)
#: Longest first, so `amount_neq` reads as (amount, neq) and never as (amount_n, eq).
_OPERATORS_BY_LENGTH: Final = tuple(sorted(_OPERATORS, key=len, reverse=True))

_TOP_LEVEL_KEYS: Final = frozenset({"schema", "actions"})
_RULE_KEYS: Final = frozenset({"when", "decision"})

#: SPEC-v0.2 §3.1 — the keys `ctrlrun.policy/v2` adds to an action entry. The gateway has no
#: decorator to carry an effect template, so the policy file has to.
_V2_ENTRY_KEYS: Final = frozenset({"effect", "resource", "mcp"})
_ENTRY_KEYS: Final = frozenset({"decision", "rules"}) | _V2_ENTRY_KEYS

#: And the closed key set of the `mcp` mapping, which is one key wide.
_MCP_KEYS: Final = frozenset({"not_executed_on_error"})

#: Names of `Action` fields (SPEC-v0.1 §2.1), which a condition cannot address: conditions
#: see the action's *arguments* and nothing else (§3.2). Writing one reads like it scopes a
#: rule — `when: { environment_eq: production }` — and matches nothing, so it is refused at
#: load. Same fail-closed reading as `{resource}` in §5.1: two candidate meanings for one
#: name, so make the author rename rather than silently pick one.
RESERVED_ARGUMENTS: Final = frozenset(
    {"action_id", "agent", "environment", "principal", "resource", "user"}
)


class Decision(StrEnum):
    """What may happen to an action: exactly three outcomes in v0.1 (SPEC-v0.1 §3.3).

    `StrEnum`, so a member renders as its value in receipts and CLI output (SPEC-v0.1 §6.1).
    """

    ALLOW = "allow"
    APPROVE = "approve"
    DENY = "deny"


@dataclass(frozen=True)
class Evaluation:
    """A decision and the reason it was reached, e.g. `rule[1]` or `unknown_action`."""

    decision: Decision
    reason: str


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
class _Condition:
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
        if self.op == "eq":
            return _equal(value, self.operand)
        if self.op == "neq":
            return not _equal(value, self.operand)
        if self.op == "in":
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


@dataclass(frozen=True)
class _Rule:
    decision: Decision
    conditions: tuple[_Condition, ...]

    def matches(self, action_name: str, arguments: Mapping[str, Any]) -> bool:
        # A rule with no `when` has no conditions, so all() holds and it always matches.
        return all(condition.matches(action_name, arguments) for condition in self.conditions)


@dataclass(frozen=True)
class McpOptions:
    """Per-tool assertions an operator makes about their upstream (SPEC-v0.2 §3.1, §6.8).

    `not_executed_on_error` is the `NotExecuted` of v0.1 §5.5 in YAML: the claim that this
    tool reports errors only *before* acting. It defaults to the fail-closed value, so an
    `isError: true` the operator has said nothing about is an `AMBIGUOUS` outcome.
    """

    not_executed_on_error: bool = False


#: The options an action with no `mcp:` mapping gets. Shared, because it is immutable.
_DEFAULT_MCP_OPTIONS: Final = McpOptions()


@dataclass(frozen=True)
class _ActionPolicy:
    decision: Decision | None
    rules: tuple[_Rule, ...]
    effect: str | None = None
    resource: str | None = None
    mcp: McpOptions = _DEFAULT_MCP_OPTIONS

    def evaluate(self, action_name: str, arguments: Mapping[str, Any]) -> Evaluation:
        if self.decision is not None:
            return Evaluation(self.decision, BARE_DECISION)
        for index, rule in enumerate(self.rules):
            if rule.matches(action_name, arguments):
                return Evaluation(rule.decision, f"rule[{index}]")
        return Evaluation(Decision.DENY, NO_MATCHING_RULE)


@dataclass(frozen=True)
class Policy:
    """Action-level autonomy policy: which actions may run, and under which conditions.

    Load with `Policy.from_file()`. A policy that cannot be loaded is an error, never an
    empty permissive policy (SPEC-v0.1 §3.4).
    """

    actions: Mapping[str, _ActionPolicy]
    source: str
    schema: str = POLICY_SCHEMA

    @classmethod
    def from_file(cls, path: str | os.PathLike[str] | None = None) -> Policy:
        """Load a policy from `path`, else `$CTRLRUN_CONFIG`, else `./ctrlrun.yaml`."""
        resolved = Path(path) if path is not None else discover_policy_path()
        try:
            text = resolved.read_text(encoding="utf-8")
        except OSError as exc:
            raise PolicyError(f"policy file {resolved} could not be read: {exc}") from exc
        return cls.from_yaml(text, source=str(resolved))

    @classmethod
    def from_yaml(cls, text: str, *, source: str = "<string>") -> Policy:
        """Parse and validate a policy document. Anything malformed raises `PolicyError`."""
        try:
            document = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise PolicyError(f"{source}: not valid YAML: {exc}") from exc
        return cls._from_document(document, source)

    @classmethod
    def _from_document(cls, document: object, source: str) -> Policy:
        if not isinstance(document, Mapping):
            raise PolicyError(
                f"{source}: policy must be a mapping with 'schema' and 'actions' keys, "
                f"got {_type_name(document)}"
            )
        # SPEC: §3.4 — an absent `schema` key is an unknown schema, never "assume v1".
        schema = document.get("schema")
        if schema not in SUPPORTED_SCHEMAS:
            raise PolicyError(
                f"{source}: unknown policy schema {schema!r}, expected one of "
                f"{', '.join(repr(known) for known in SUPPORTED_SCHEMAS)}"
            )
        # SPEC: §3.1 — key sets are closed, so a typo such as `action:` fails at load
        # instead of silently denying everything at runtime.
        _reject_unknown_keys(document, _TOP_LEVEL_KEYS, f"{source}: top level")

        entries = document.get("actions")
        if not isinstance(entries, Mapping):
            raise PolicyError(
                f"{source}: 'actions' must be a mapping of action name to entry, "
                f"got {_type_name(entries)}"
            )
        actions: dict[str, _ActionPolicy] = {}
        for name, entry in entries.items():
            if not isinstance(name, str) or not name:
                raise PolicyError(f"{source}: action names must be non-empty strings, got {name!r}")
            actions[name] = _parse_entry(entry, f"{source}: action {name!r}", str(schema))
        return cls(actions=MappingProxyType(actions), source=source, schema=str(schema))

    def effect_template(self, action_name: str) -> str | None:
        """This action's `effect:` template, or `None` (SPEC-v0.2 §3.1, §11).

        `None` means the action has no logical effect and gets no reservation — the
        documented escape hatch of v0.1 §5.1, and the right answer for a read.
        """
        entry = self.actions.get(action_name)
        return None if entry is None else entry.effect

    def resource_template(self, action_name: str) -> str | None:
        """This action's `resource:` template, or `None` (SPEC-v0.2 §3.1, §11)."""
        entry = self.actions.get(action_name)
        return None if entry is None else entry.resource

    def mcp_options(self, action_name: str) -> McpOptions:
        """This action's `mcp:` options, defaulting to the fail-closed ones (§3.1, §11)."""
        entry = self.actions.get(action_name)
        return _DEFAULT_MCP_OPTIONS if entry is None else entry.mcp

    def evaluate(self, action: Action) -> Evaluation:
        """Decide an action. No side effects; an unlisted action is denied (SPEC-v0.1 §3.4)."""
        entry = self.actions.get(action.name)
        if entry is None:
            return Evaluation(Decision.DENY, UNKNOWN_ACTION)
        # Conditions see exactly what the executor will receive (SPEC-v0.1 §2.2), which is
        # also why a list argument compares equal to a list operand.
        return entry.evaluate(action.name, action.canonical_arguments)


def discover_policy_path() -> Path:
    """The policy file this process would load: `$CTRLRUN_CONFIG`, else `./ctrlrun.yaml`."""
    configured = os.environ.get(CONFIG_ENV_VAR)
    if configured is None:
        return Path.cwd() / DEFAULT_POLICY_FILENAME
    if not configured.strip():
        raise PolicyError(f"{CONFIG_ENV_VAR} is set but empty; unset it or point it at a policy")
    return Path(configured)


def _reject_unknown_keys(mapping: Mapping[Any, Any], allowed: Iterable[str], where: str) -> None:
    unknown = sorted(repr(key) for key in mapping if key not in set(allowed))
    if unknown:
        raise PolicyError(
            f"{where}: unknown key(s) {', '.join(unknown)}; allowed: {', '.join(sorted(allowed))}"
        )


def _parse_entry(entry: object, where: str, schema: str) -> _ActionPolicy:
    if not isinstance(entry, Mapping):
        raise PolicyError(
            f"{where}: entry must be a mapping with 'decision' or 'rules', got {_type_name(entry)}"
        )
    _reject_unknown_keys(entry, _ENTRY_KEYS, where)
    _reject_v2_keys_under_v1(entry, where, schema)
    has_decision = "decision" in entry
    has_rules = "rules" in entry
    if has_decision == has_rules:
        raise PolicyError(f"{where}: entry must have exactly one of 'decision' or 'rules'")

    effect = _parse_template(entry.get("effect"), "effect", where)
    resource = _parse_template(entry.get("resource"), "resource", where)
    mcp = _parse_mcp(entry.get("mcp"), where)

    if has_decision:
        return _ActionPolicy(
            decision=_parse_decision(entry["decision"], where),
            rules=(),
            effect=effect,
            resource=resource,
            mcp=mcp,
        )

    rules = entry["rules"]
    if not isinstance(rules, list) or not rules:
        raise PolicyError(f"{where}: 'rules' must be a non-empty list, got {_type_name(rules)}")
    return _ActionPolicy(
        decision=None,
        rules=tuple(
            _parse_rule(rule, f"{where} rule[{index}]") for index, rule in enumerate(rules)
        ),
        effect=effect,
        resource=resource,
        mcp=mcp,
    )


def _reject_v2_keys_under_v1(entry: Mapping[Any, Any], where: str, schema: str) -> None:
    """A v2 key in a v1 document is a load error naming the key and the schema (§3.1).

    Not a warning, and not silently honoured. A v0.1 kernel reading this file would ignore
    the effect template and execute with no duplicate protection at all, so a document that
    depends on one has to say so in a way v0.1 refuses.
    """
    if schema != POLICY_SCHEMA:
        return
    used = sorted(key for key in entry if key in _V2_ENTRY_KEYS)
    if used:
        raise PolicyError(
            f"{where}: {', '.join(repr(key) for key in used)} needs "
            f"'schema: {POLICY_SCHEMA_V2}'; this document declares {POLICY_SCHEMA!r}, and "
            "a v0.1 reader would ignore the template and execute with no duplicate protection"
        )


def _parse_template(value: object, kwarg: str, where: str) -> str | None:
    """Validate an `effect:` / `resource:` template at load time (SPEC-v0.2 §3.1).

    A malformed template is a `PolicyError` here, not an `EffectKeyError` when an agent
    first reaches the action: the same reason `@protect` checks its templates at decoration
    time (v0.1 §5.1). The grammar is `effect.py`'s, so the two paths cannot diverge.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise PolicyError(
            f"{where}: {kwarg!r} must be a template string, got {_type_name(value)}"
        )
    try:
        template_placeholders(value)
    except InvalidArgument as exc:
        raise PolicyError(f"{where}: {kwarg!r}: {exc}") from exc
    return value


def _parse_mcp(value: object, where: str) -> McpOptions:
    if value is None:
        return _DEFAULT_MCP_OPTIONS
    if not isinstance(value, Mapping):
        raise PolicyError(f"{where}: 'mcp' must be a mapping, got {_type_name(value)}")
    _reject_unknown_keys(value, _MCP_KEYS, f"{where}: mcp")
    claimed = value.get("not_executed_on_error", False)
    # SPEC-v0.2 §3.1 — a bool, and `1` is not one. This is an assertion about a remote that
    # CTRLRun cannot check (§6.8), so it is made deliberately or not at all.
    if not isinstance(claimed, bool):
        raise PolicyError(
            f"{where}: mcp: 'not_executed_on_error' must be true or false, "
            f"got {_type_name(claimed)}"
        )
    return McpOptions(not_executed_on_error=claimed)


def _parse_decision(value: object, where: str) -> Decision:
    allowed = ", ".join(member.value for member in Decision)
    if not isinstance(value, str):
        raise PolicyError(f"{where}: decision must be one of {allowed}, got {_type_name(value)}")
    try:
        return Decision(value)
    except ValueError as exc:
        raise PolicyError(
            f"{where}: unknown decision {value!r}, expected one of {allowed}"
        ) from exc


def _parse_rule(rule: object, where: str) -> _Rule:
    if not isinstance(rule, Mapping):
        raise PolicyError(f"{where}: a rule must be a mapping, got {_type_name(rule)}")
    _reject_unknown_keys(rule, _RULE_KEYS, where)
    if "decision" not in rule:
        raise PolicyError(f"{where}: a rule must have a 'decision'")
    decision = _parse_decision(rule["decision"], where)
    if "when" not in rule:
        return _Rule(decision=decision, conditions=())

    when = rule["when"]
    # SPEC: §3.2 — `when` is either absent (always matches) or a non-empty mapping. An
    # empty mapping is a truncated edit, and the catch-all is already spelled "no `when`".
    if not isinstance(when, Mapping) or not when:
        raise PolicyError(
            f"{where}: 'when' must be a non-empty mapping of conditions, got {_type_name(when)}"
        )
    return _Rule(
        decision=decision,
        conditions=tuple(_parse_condition(key, operand, where) for key, operand in when.items()),
    )


def _parse_condition(key: object, operand: object, where: str) -> _Condition:
    if not isinstance(key, str):
        raise PolicyError(f"{where}: condition keys must be strings, got {key!r}")
    argument, op = _split_condition_key(key, where)
    return _Condition(
        key=key,
        argument=argument,
        op=op,
        operand=_parse_operand(op, operand, where, key),
    )


def _split_condition_key(key: str, where: str) -> tuple[str, str]:
    for op in _OPERATORS_BY_LENGTH:
        suffix = f"_{op}"
        if key.endswith(suffix) and len(key) > len(suffix):
            argument = key[: -len(suffix)]
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
            raise PolicyError(
                f"{where}: condition {key!r}: a numeric operator needs an int operand, "
                f"got {_type_name(operand)}"
            )
        return operand
    if op == "in":
        if not isinstance(operand, list):
            raise PolicyError(
                f"{where}: condition {key!r}: '_in' needs a list operand, got {_type_name(operand)}"
            )
        return tuple(_checked_operand(item, where, key) for item in operand)
    return _checked_operand(operand, where, key)


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
