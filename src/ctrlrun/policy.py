"""Policy loading and rule evaluation to a Decision. Build-list item 2; SPEC-v0.1 §3.

SPEC-v0.2 §3 adds per-action `effect:` and `resource:` templates, because the gateway has no
decorator to carry one. That is the only reason this module imports the template grammar from
`effect.py`: it validates the syntax at load time, so a typo fails at startup rather than
raising `EffectKeyError` at the moment an agent tries to move money. It stays ignorant of
effect *state* — records, transitions and reservations are none of policy's business
(ARCHITECTURE §6).
"""

from __future__ import annotations

import hashlib
import logging
import operator
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time
from enum import StrEnum
from functools import cached_property
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, Literal

import yaml

from .action import Action, PlainValue, canonical_bytes
from .effect import template_placeholders
from .errors import InvalidArgument, PolicyError

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

#: All of them, newest last, for the message an unknown schema produces.
SUPPORTED_SCHEMAS: Final = (
    POLICY_SCHEMA,
    POLICY_SCHEMA_V2,
    POLICY_SCHEMA_V3,
    POLICY_SCHEMA_V4,
)

#: SPEC-v0.3 §6.1 — the two values of the top-level `mode:` key, and nothing else. Absent
#: means `enforce`: the fail-closed default, so a document that predates the key enforces.
MODE_KEY: Final = "mode"
OBSERVE: Final = "observe"
ENFORCE: Final = "enforce"
POLICY_MODES: Final = (OBSERVE, ENFORCE)

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

_TOP_LEVEL_KEYS: Final = frozenset(
    {"schema", "actions", "environment", "authority", "mode", "version", "controls"}
)

#: SPEC-v0.6 §7.1 — the top-level keys that need `ctrlrun.policy/v4`, and what an older reader
#: would do with each. `version:` is the mild one: an older reader ignoring it records no
#: declared version, which is exactly what a `v3` document has. It is still gated, because
#: §3.1's key sets are closed and a `version:` an older reader silently dropped would be a typo
#: that never surfaced.
#: The closed key set of one registry entry (§7.3, and §3.1's rule).
_CONTROL_KEYS: Final = frozenset({"title", "source"})

_V4_TOP_LEVEL_KEYS: Final[Mapping[str, str]] = {
    "controls": (
        "an older reader would ignore the registry and load a document whose rules cite ids "
        "nothing defines"
    ),
    "version": (
        "an older reader would refuse the document outright, since its top-level key set is closed"
    ),
}

#: SPEC-v0.3 §12.1 — the top-level keys that need `ctrlrun.policy/v3`, and what an older
#: reader would do with each if it ignored one.
_V3_TOP_LEVEL_KEYS: Final[Mapping[str, str]] = {
    "environment": ("an older reader would ignore it and put every action in the wrong deployment"),
    "authority": (
        "an older reader would ignore it and run every action with no authority check at all"
    ),
    "mode": ("an older reader would enforce a configuration that was deployed to observe"),
}
_RULE_KEYS: Final = frozenset({"when", "decision", "controls"})

#: SPEC-v0.2 §3.1 — the keys `ctrlrun.policy/v2` adds to an action entry. The gateway has no
#: decorator to carry an effect template, so the policy file has to.
_V2_ENTRY_KEYS: Final = frozenset({"effect", "resource", "mcp"})
_ENTRY_KEYS: Final = frozenset({"decision", "rules", "controls"}) | _V2_ENTRY_KEYS

#: And the closed key set of the `mcp` mapping, which is one key wide.
_MCP_KEYS: Final = frozenset({"not_executed_on_error"})

#: Names of `Action` fields (SPEC-v0.1 §2.1), which a condition cannot address: conditions
#: see the action's *arguments* and nothing else (§3.2). Writing one reads like it scopes a
#: rule — `when: { environment_eq: production }` — and matches nothing, so it is refused at
#: load. Same fail-closed reading as `{resource}` in §5.1: two candidate meanings for one
#: name, so make the author rename rather than silently pick one.
#: SPEC-v0.3 §4.5 adds `claims`, `issuer` and `expires_at`: before v0.3 those names meant
#: nothing, and now they name fields of a `Principal`, so v0.1 §3.2's rule applies — a name
#: with two candidate meanings is refused until the author renames. Not gated on the schema
#: version (§12.1): the splitter runs for every condition in every document, and gating it
#: would leave the same name meaning two things in two files.
RESERVED_ARGUMENTS: Final = frozenset(
    {
        "action_id",
        "agent",
        "claims",
        "environment",
        "expires_at",
        "issuer",
        "principal",
        "resource",
        "user",
    }
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
    #: SPEC-v0.6 §7.3 — the control ids the **matched rule** cited, unioned with the action's,
    #: in registry order. Empty where the document declares none, which is every document
    #: before `ctrlrun.policy/v4`.
    #:
    #: **Attribution, not prevention.** These do not participate in the decision: they say which
    #: written expectation the rule that produced it exists to serve. A field that changed a
    #: decision would be a control that enforced something, and §7.3's second rule forbids
    #: exactly that reading.
    controls: tuple[str, ...] = ()


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
    conditions: tuple[Condition, ...]
    #: §7.3 — the control ids this rule cites. A rule may narrow or add to the action's.
    controls: tuple[str, ...] = ()

    def matches(self, action_name: str, arguments: Mapping[str, Any]) -> bool:
        # A rule with no `when` has no conditions, so all() holds and it always matches.
        return all(condition.matches(action_name, arguments) for condition in self.conditions)


@dataclass(frozen=True)
class Control:
    """One entry in the control registry: an identifier and a citation (SPEC-v0.6 §7.3).

    **CTRLRun does not interpret a control.** `source:` is a string the operator wrote. The
    kernel does not know what PCI DSS is, does not check the clause exists, and makes no
    compliance, conformance or alignment claim on the strength of one -- validating a citation
    would be the beginning of interpreting it.

    And a control is **attribution, not prevention**. Citing one on a rule does not cause an
    approval; the rule's `decision:` does. It says which written expectation the rule exists to
    serve, so a receipt can answer "under what" and an operator can go from a control to its
    evidence. Any sentence that implies otherwise is a false green in prose.
    """

    id: str
    title: str
    source: str | None = None


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
    #: §7.3 — the control ids this action cites, which govern every rule under it.
    controls: tuple[str, ...] = ()

    def evaluate(self, action_name: str, arguments: Mapping[str, Any]) -> Evaluation:
        if self.decision is not None:
            return Evaluation(self.decision, BARE_DECISION, self.controls)
        for index, rule in enumerate(self.rules):
            if rule.matches(action_name, arguments):
                # The union of the action's and the matched rule's, in registry order (§7.3).
                # The action's alone would drop what the rule narrowed to; the rule's alone
                # would drop a control that governs every rule under the action.
                return Evaluation(
                    rule.decision,
                    f"rule[{index}]",
                    self.controls
                    + tuple(item for item in rule.controls if item not in self.controls),
                )
        # No rule matched, so no rule's controls apply -- and the action's do: they govern
        # everything under it, including this refusal.
        return Evaluation(Decision.DENY, NO_MATCHING_RULE, self.controls)


@dataclass(frozen=True)
class Policy:
    """Action-level autonomy policy: which actions may run, and under which conditions.

    Load with `Policy.from_file()`. A policy that cannot be loaded is an error, never an
    empty permissive policy (SPEC-v0.1 §3.4).
    """

    actions: Mapping[str, _ActionPolicy]
    source: str
    schema: str = POLICY_SCHEMA
    #: SPEC-v0.3 §2.5 rank 3. `None` where the document names none; `Control` then falls
    #: through to "production". Policy itself never reads it — the environment is not a
    #: condition (`environment` is a reserved argument name) — it is carried for `Control`.
    environment: str | None = None
    #: SPEC-v0.3 §6.1 — `observe` or `enforce`, top-level and nothing else. Policy itself
    #: never reads it either: observe mode changes what `Control` *does* with a decision, not
    #: which decision is reached, and an evaluator that knew the mode would be a second place
    #: for the two to disagree.
    mode: Literal["observe", "enforce"] = ENFORCE
    #: SPEC-v0.6 §7.1 — the operator's own label for this document, or `None`. A free string,
    #: **for humans, and never authoritative**: two documents sharing a `version:` and differing
    #: in content are two different policies, and `policy_hash` is what says so. Recorded on
    #: every receipt beside the hash so an operator can read the evidence in their own terms.
    version: str | None = None
    #: SPEC-v0.6 §7.3 — the control registry, by id, in document order. Empty where the document
    #: declares none, which is every document before `ctrlrun.policy/v4`.
    controls: Mapping[str, Control] = field(default_factory=dict)
    #: The canonical form this policy's hash is computed over (§7.1). Held rather than rebuilt so
    #: `policy_hash` is not recomputed on every action, and private because it is an
    #: implementation detail of the hash and not a second way to read the policy.
    _canonical: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @cached_property
    def policy_hash(self) -> str:
        """`"sha256:" + hex(SHA-256(canonical_form(...)))` over the **parsed** decision inputs.

        SPEC-v0.6 §7.1. Over the parsed rules and not the file's bytes: two documents differing
        only in comments, key order or whitespace hash the same, which is correct, because the
        decision is a function of the rules and not of the formatting. A hash that moved on a
        reformat would make every receipt's provenance field noise -- an operator who ran a
        formatter would find nothing before that commit comparable with anything after it.

        `v0.1 §2.3`'s canonicalizer through `canonical_bytes`, which is the same primitive the
        action hash and the receipt chain use. A second one is what §6.2 forbids by name.

        Authority is **in** it: `v0.3 §4.6` makes authority half of what decided an action, and
        a hash that noticed a changed rule but not a widened grant would answer "what decided
        this" with half the answer.
        """
        return "sha256:" + hashlib.sha256(canonical_bytes(self._canonical)).hexdigest()

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
        require_v3(document, str(schema), source)
        environment = document.get("environment")
        if "environment" in document and (
            not isinstance(environment, str) or not environment.strip()
        ):
            raise PolicyError(
                f"{source}: 'environment' must be a non-empty string, got {_type_name(environment)}"
            )
        mode = _parse_mode(document, source)
        require_v4(document, str(schema), source)
        version = document.get("version")
        if "version" in document and (not isinstance(version, str) or not version.strip()):
            raise PolicyError(
                f"{source}: 'version' must be a non-empty string, got {_type_name(version)}"
            )
        controls = _parse_controls(document.get("controls"), source)
        actions: dict[str, _ActionPolicy] = {}
        for name, entry in entries.items():
            if not isinstance(name, str) or not name:
                raise PolicyError(f"{source}: action names must be non-empty strings, got {name!r}")
            actions[name] = _parse_entry(
                entry, f"{source}: action {name!r}", str(schema), frozenset(controls)
            )
        return cls(
            actions=MappingProxyType(actions),
            source=source,
            schema=str(schema),
            environment=environment if isinstance(environment, str) else None,
            mode=mode,
            version=version if isinstance(version, str) else None,
            controls=MappingProxyType(controls),
            _canonical=_canonical_policy(document, str(schema), mode, environment),
        )

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


def _canonical_policy(
    document: Mapping[Any, Any],
    schema: str,
    mode: str,
    environment: object,
) -> Mapping[str, Any]:
    """The decision inputs of a policy document, in the shape its hash is taken over (§7.1).

    **The parsed inputs, and only those.** `version:` is absent by construction -- it is
    recorded and never authoritative, so a document relabelled and not otherwise touched hashes
    the same. `source` is absent too: the same rules loaded from two paths are one policy.

    Rules keep **document order**, because a policy is ordered and first-match wins (`v0.1
    §3.3`); everything else is a mapping and `canonical_bytes` sorts it. Reordering two rules is
    a different policy and the hash says so; reordering two keys inside one rule is not.
    """
    return {
        "actions": _plain(document.get("actions")),
        "authority": _plain(document.get("authority")),
        "environment": environment if isinstance(environment, str) else None,
        "mode": mode,
        "schema": schema,
    }


def _plain(value: object) -> PlainValue:
    """A YAML fragment as plain JSON-shaped data, so `canonical_bytes` can encode it.

    `yaml.safe_load` already yields dicts, lists, strings, ints, bools and `None`, and
    `canonical_bytes` refuses anything else -- including the `float` a threshold written as
    `50000.0` would produce, which is `v0.1 §2.3`'s rule reaching the policy hash and is the
    right answer: a binary float in a decision input is not portable between two hosts.
    """
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    if value is None or isinstance(value, str | int):  # bool is a subclass of int
        return value
    if isinstance(value, datetime | date | time):
        # `yaml.safe_load` turns an unquoted `expires_at: 2020-01-01T00:00:00Z` into a
        # `datetime`, and such documents load today -- a grant with an expiry is the ordinary
        # case. ISO-8601 is what the same value would have been had it been quoted, and what
        # CTRLRun writes everywhere else, so this loses nothing and invents nothing.
        #
        # An earlier version of this function *refused* here, which broke every authority
        # document with an unquoted expiry. The conformance kit's own `EXPIRED_GRANT` caught it.
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    # A `float`, or anything else. `canonical_bytes` refuses it too, and naming the policy here
    # is what turns "float is not encodable at payload.actions..." into something an operator can
    # act on. A float in a *condition* was already refused before v0.6, by the numeric-operand
    # check; this covers the rest of the document, and the reason is `v0.1 §2.3`'s: provenance
    # that two hosts computed differently is not provenance.
    raise PolicyError(
        f"a policy cannot contain a {_type_name(value)}: the decision inputs are hashed with "
        "v0.1 §2.3's canonicalizer, which encodes JSON and nothing else. Write a threshold as an "
        "integer in minor units."
    )


def require_v3(document: Mapping[Any, Any], schema: str, source: str) -> None:
    """Refuse a `ctrlrun.policy/v3` key in an older document (SPEC-v0.3 §12.1).

    Shared with `authority.py`, which reads the same key from a document the policy loader
    may never see: SPEC-v0.3 §8.3's `--authority` file carries `schema` and `authority` and
    nothing else, so the check has to exist on both paths rather than on whichever runs first.
    """
    # v4 is a superset: a `v4` document may use every `v3` key. Comparing for equality here was
    # right while v3 was the newest and becomes a bug the moment it is not.
    if schema in (POLICY_SCHEMA_V3, POLICY_SCHEMA_V4):
        return
    for key, consequence in _V3_TOP_LEVEL_KEYS.items():
        if key in document:
            raise PolicyError(
                f"{source}: {key!r} needs 'schema: {POLICY_SCHEMA_V3}'; this document "
                f"declares {schema!r}, and {consequence}"
            )


def require_v4(document: Mapping[Any, Any], schema: str, source: str) -> None:
    """Refuse a `ctrlrun.policy/v4` key in an older document (SPEC-v0.6 §7.1).

    `require_v3`'s shape exactly, one version up. Kept separate rather than folded into a
    version-ordering comparison, because the *consequences* differ per key and the message an
    operator reads is the point: "an older reader would run every action with no authority
    check at all" and "an older reader would refuse the document outright" call for different
    reactions.
    """
    if schema == POLICY_SCHEMA_V4:
        return
    for key, consequence in _V4_TOP_LEVEL_KEYS.items():
        if key in document:
            raise PolicyError(
                f"{source}: {key!r} needs 'schema: {POLICY_SCHEMA_V4}'; this document "
                f"declares {schema!r}, and {consequence}"
            )


def _parse_mode(document: Mapping[Any, Any], source: str) -> Literal["observe", "enforce"]:
    """The top-level `mode:`, or the fail-closed default (SPEC-v0.3 §6.1).

    The value MUST be exactly `observe` or `enforce`. `true`, `off` and `0` are not aliases
    for either: a switch that governs whether *anything* is enforced is set deliberately or
    not at all, and YAML would otherwise read `mode: off` as the boolean `False`, which names
    neither mode.
    """
    if MODE_KEY not in document:
        return ENFORCE
    value = document[MODE_KEY]
    if value not in POLICY_MODES:
        raise PolicyError(
            f"{source}: 'mode' must be exactly {OBSERVE!r} or {ENFORCE!r}, got {value!r}. "
            "There is one switch and it governs the process (SPEC-v0.3 §6.1)"
        )
    return OBSERVE if value == OBSERVE else ENFORCE


def reject_nested_mode(mapping: Mapping[Any, Any], where: str) -> None:
    """Refuse a `mode:` anywhere but the top level of the policy document (SPEC-v0.3 §6.1).

    The closed key sets of `v0.1 §3.1` would already refuse it as unknown, wherever they
    reach. This runs first and for its *message*: "unknown key 'mode'" reads as "CTRLRun has
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


def parse_conditions(mapping: Mapping[Any, Any], *, where: str) -> Mapping[str, Condition]:
    """Parse a `when:`-shaped mapping into conditions, keyed by the raw condition key.

    Public since SPEC-v0.3 §11: a grant's `constraints:` is in exactly this syntax and MUST be
    parsed by this code (§4.5). The key is injective given §3.2's longest-suffix split, which
    is what lets `Grant`'s containment check look a dimension up by name.
    """
    conditions: dict[str, Condition] = {}
    for key, operand in mapping.items():
        condition = _parse_condition(key, operand, where)
        conditions[condition.key] = condition
    return conditions


def _reject_unknown_keys(mapping: Mapping[Any, Any], allowed: Iterable[str], where: str) -> None:
    unknown = sorted(repr(key) for key in mapping if key not in set(allowed))
    if unknown:
        raise PolicyError(
            f"{where}: unknown key(s) {', '.join(unknown)}; allowed: {', '.join(sorted(allowed))}"
        )


def _parse_controls(value: object, source: str) -> dict[str, Control]:
    """The top-level `controls:` registry (SPEC-v0.6 §7.3)."""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise PolicyError(
            f"{source}: 'controls' must be a mapping of id to entry, got {_type_name(value)}"
        )
    registry: dict[str, Control] = {}
    for identifier, entry in value.items():
        if not isinstance(identifier, str) or not identifier.strip():
            raise PolicyError(
                f"{source}: control ids must be non-empty strings, got {identifier!r}"
            )
        where = f"{source}: control {identifier!r}"
        if not isinstance(entry, Mapping):
            raise PolicyError(f"{where}: must be a mapping with a 'title', got {_type_name(entry)}")
        _reject_unknown_keys(entry, _CONTROL_KEYS, where)
        title = entry.get("title")
        if not isinstance(title, str) or not title.strip():
            raise PolicyError(
                f"{where}: 'title' must be a non-empty string, got {_type_name(title)}"
            )
        cited = entry.get("source")
        if "source" in entry and (not isinstance(cited, str) or not cited.strip()):
            raise PolicyError(
                f"{where}: 'source' must be a non-empty string, got {_type_name(cited)}"
            )
        registry[identifier] = Control(
            id=identifier, title=title, source=cited if isinstance(cited, str) else None
        )
    return registry


def _parse_cited(value: object, where: str, known: frozenset[str]) -> tuple[str, ...]:
    """A `controls:` citation list, checked against the registry (§7.3).

    **An unknown id is a load error naming it.** A registry whose citations can dangle is a
    registry that quietly stops meaning anything -- an operator reading a receipt would follow a
    control id to a definition that is not there, and conclude the evidence was wrong rather
    than the document.
    """
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, list | tuple):
        raise PolicyError(f"{where}: 'controls' must be a list of ids, got {_type_name(value)}")
    cited: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise PolicyError(f"{where}: control ids must be non-empty strings, got {item!r}")
        if item not in known:
            listed = ", ".join(sorted(known)) or "none"
            raise PolicyError(
                f"{where}: cites control {item!r}, which the registry does not define. "
                f"Declared controls: {listed}"
            )
        if item not in cited:
            cited.append(item)
    return tuple(cited)


def _parse_entry(
    entry: object, where: str, schema: str, known: frozenset[str] = frozenset()
) -> _ActionPolicy:
    if not isinstance(entry, Mapping):
        raise PolicyError(
            f"{where}: entry must be a mapping with 'decision' or 'rules', got {_type_name(entry)}"
        )
    reject_nested_mode(entry, where)
    _reject_unknown_keys(entry, _ENTRY_KEYS, where)
    _reject_v2_keys_under_v1(entry, where, schema)
    has_decision = "decision" in entry
    has_rules = "rules" in entry
    if has_decision == has_rules:
        raise PolicyError(f"{where}: entry must have exactly one of 'decision' or 'rules'")

    effect = _parse_template(entry.get("effect"), "effect", where)
    resource = _parse_template(entry.get("resource"), "resource", where)
    mcp = _parse_mcp(entry.get("mcp"), where)

    cited = _parse_cited(entry.get("controls"), where, known)

    if has_decision:
        return _ActionPolicy(
            decision=_parse_decision(entry["decision"], where),
            rules=(),
            effect=effect,
            resource=resource,
            mcp=mcp,
            controls=cited,
        )

    rules = entry["rules"]
    if not isinstance(rules, list) or not rules:
        raise PolicyError(f"{where}: 'rules' must be a non-empty list, got {_type_name(rules)}")
    return _ActionPolicy(
        decision=None,
        rules=tuple(
            _parse_rule(rule, f"{where} rule[{index}]", known) for index, rule in enumerate(rules)
        ),
        effect=effect,
        resource=resource,
        mcp=mcp,
        controls=cited,
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
        raise PolicyError(f"{where}: {kwarg!r} must be a template string, got {_type_name(value)}")
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


def _parse_rule(rule: object, where: str, known: frozenset[str] = frozenset()) -> _Rule:
    if not isinstance(rule, Mapping):
        raise PolicyError(f"{where}: a rule must be a mapping, got {_type_name(rule)}")
    reject_nested_mode(rule, where)
    _reject_unknown_keys(rule, _RULE_KEYS, where)
    if "decision" not in rule:
        raise PolicyError(f"{where}: a rule must have a 'decision'")
    decision = _parse_decision(rule["decision"], where)
    cited = _parse_cited(rule.get("controls"), where, known)
    if "when" not in rule:
        return _Rule(decision=decision, conditions=(), controls=cited)

    when = rule["when"]
    # SPEC: §3.2 — `when` is either absent (always matches) or a non-empty mapping. An
    # empty mapping is a truncated edit, and the catch-all is already spelled "no `when`".
    if not isinstance(when, Mapping) or not when:
        raise PolicyError(
            f"{where}: 'when' must be a non-empty mapping of conditions, got {_type_name(when)}"
        )
    return _Rule(
        decision=decision,
        conditions=tuple(parse_conditions(when, where=where).values()),
        controls=cited,
    )


def _parse_condition(key: object, operand: object, where: str) -> Condition:
    if not isinstance(key, str):
        raise PolicyError(f"{where}: condition keys must be strings, got {key!r}")
    argument, op = _split_condition_key(key, where)
    return Condition(
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
