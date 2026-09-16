# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
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
import os
import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time
from functools import cached_property, partial
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, Literal

import yaml

from .action import Action, PlainValue, canonical_bytes
from .decision import POLICY_UNAPPROVED as POLICY_UNAPPROVED
from .decision import Decision as Decision
from .effect import template_placeholders
from .errors import InvalidArgument, PolicyError
from .grammar import _NUMERIC_COMPARE as _NUMERIC_COMPARE
from .grammar import _OPERATORS as _OPERATORS
from .grammar import _OPERATORS_BY_LENGTH as _OPERATORS_BY_LENGTH
from .grammar import _V3_TOP_LEVEL_KEYS as _V3_TOP_LEVEL_KEYS
from .grammar import _V7_GRANT_KEYS as _V7_GRANT_KEYS

# Re-exported, not merely used. `SPEC-v0.1.md` §8 freezes `from .policy import Decision, Policy`
# in `__init__.py`, and `adapter.py` imports `Decision` from here too. Both names moved down to
# break the module cycle §6 records, and both still resolve from this module because that block
# is a frozen public surface and a cycle is not a reason to move a published import path.
# Re-exported, not merely used. `SPEC-v0.3.md` §8 freezes
# `from .policy import Condition, parse_conditions`, and `SPEC-v0.1.md` §8 the `__init__` block,
# so every one of these keeps resolving from here. They moved to `grammar.py` to break the last
# cycle §6 carried an exception for; see that module.
from .grammar import DERIVED_SUBJECTS as DERIVED_SUBJECTS
from .grammar import MODE_KEY as MODE_KEY
from .grammar import POLICY_SCHEMA as POLICY_SCHEMA
from .grammar import POLICY_SCHEMA_V2 as POLICY_SCHEMA_V2
from .grammar import POLICY_SCHEMA_V3 as POLICY_SCHEMA_V3
from .grammar import POLICY_SCHEMA_V4 as POLICY_SCHEMA_V4
from .grammar import POLICY_SCHEMA_V5 as POLICY_SCHEMA_V5
from .grammar import POLICY_SCHEMA_V6 as POLICY_SCHEMA_V6
from .grammar import POLICY_SCHEMA_V7 as POLICY_SCHEMA_V7
from .grammar import POLICY_SCHEMA_V8 as POLICY_SCHEMA_V8
from .grammar import RESERVED_ARGUMENTS as RESERVED_ARGUMENTS
from .grammar import SUPPORTED_SCHEMAS as SUPPORTED_SCHEMAS
from .grammar import Condition as Condition
from .grammar import _at_least as _at_least
from .grammar import _checked_operand as _checked_operand
from .grammar import _equal as _equal
from .grammar import _is_container as _is_container
from .grammar import _is_int as _is_int
from .grammar import _parse_condition as _parse_condition
from .grammar import _parse_operand as _parse_operand
from .grammar import _split_condition_key as _split_condition_key
from .grammar import _StrictLoader as _StrictLoader
from .grammar import _type_name as _type_name
from .grammar import parse_conditions as parse_conditions
from .grammar import reject_nested_mode as reject_nested_mode
from .grammar import require_v3 as require_v3
from .grammar import require_v7 as require_v7
from .grammar import strict_load as strict_load

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


_TOP_LEVEL_KEYS: Final = frozenset(
    {"schema", "actions", "environment", "authority", "mode", "version", "controls"}
)

#: SPEC-v0.6 §7.1 — the top-level keys that need `ctrlrun.policy/v4`, and what an older reader
#: would do with each. `version:` is the mild one: an older reader ignoring it records no
#: declared version, which is exactly what a `v3` document has. It is still gated, because
#: §3.1's key sets are closed and a `version:` an older reader silently dropped would be a typo
#: that never surfaced.
#: The closed key set of one registry entry (§7.3, and §3.1's rule).
_CONTROL_KEYS: Final = frozenset({"title", "source", "approver_role"})

_V4_TOP_LEVEL_KEYS: Final[Mapping[str, str]] = {
    "controls": (
        "an older reader would ignore the registry and load a document whose rules cite ids "
        "nothing defines"
    ),
    "version": (
        "an older reader would refuse the document outright, since its top-level key set is closed"
    ),
}

#: §7.4 — the action-entry keys that need `ctrlrun.policy/v4`, and what an older reader would do.
_V4_ENTRY_KEYS: Final[Mapping[str, str]] = {
    "data": (
        "an older reader would ignore the labels and evaluate a `data_scope` condition against "
        "nothing, so a rule written to catch PHI would match no action at all"
    ),
}

#: SPEC-v0.7 §5.3 — the action-entry key that needs `ctrlrun.policy/v5`, and the same sentence:
#: what an older reader would do with the document if it ignored the key.
_V5_ENTRY_KEYS: Final[Mapping[str, str]] = {
    "max_attempts": (
        "an older reader would ignore the ceiling and renew over `FAILED` without bound, which "
        "is the behaviour this key exists to stop"
    ),
}

#: SPEC-v0.8 §4.2 — the M-of-N threshold, gated for `max_attempts`'s reason: an older reader
#: would ignore it and consume on the first grant, which is a deployment believing two humans
#: answered when one did.
#: SPEC-v0.8 §8.2. The one action name the policy-change flow owns, and the only reserved name
#: in this project. **Reserved and declarable**, which a first draft had backwards: `evaluate`
#: answers `DENY unknown_action` for any name a document does not list, so a name no document
#: may declare is a name every proposal is denied for, no committed receipt is ever written,
#: and a deployment with `require_approved_policy=True` denies every action for ever. The two
#: rules were mutually exclusive.
POLICY_CHANGE_ACTION: Final = "ctrlrun.policy.change"

#: SPEC-v0.10 §4.5 — the two upstream refusals, separately observable because "the server
#: changed" and "nobody has checked" are different findings an operator fixes differently.
#: `UPSTREAM_UNVERIFIED` is the fail-closed half and the one to get right: a pin that does
#: nothing when nothing was observed is a pin an upstream can switch off by never being seen.
UPSTREAM_MISMATCH: Final = "upstream_mismatch"
UPSTREAM_UNVERIFIED: Final = "upstream_unverified"

_V6_ENTRY_KEYS: Final[Mapping[str, str]] = {
    "approvals_required": (
        "an older reader would ignore the threshold and consume on the first grant, which is a "
        "deployment believing several humans answered when one did"
    ),
}

#: SPEC-v0.10 §4.6 — the action-entry key `ctrlrun.policy/v8` adds, gated in the shape
#: `_V4_ENTRY_KEYS` and `_V5_ENTRY_KEYS` use and **not** `require_v7`'s: that one walks
#: `authority.grants`, because `tasks:` and `budgets:` are grant keys, and a standalone
#: `--authority` document carries no action entries at all.
_V8_ENTRY_KEYS: Final[Mapping[str, str]] = {
    "upstream": (
        "an older reader would ignore the pin and authorise the action against any server at "
        "all, which is the whole of what the key restricts"
    ),
}

_RULE_KEYS: Final = frozenset({"when", "decision", "controls"})

#: SPEC-v0.2 §3.1 — the keys `ctrlrun.policy/v2` adds to an action entry. The gateway has no
#: decorator to carry an effect template, so the policy file has to.
_V2_ENTRY_KEYS: Final = frozenset({"effect", "resource", "mcp"})
_ENTRY_KEYS: Final = (
    frozenset({"decision", "rules", "controls", "data"})
    | _V2_ENTRY_KEYS
    | frozenset(_V5_ENTRY_KEYS)
    | frozenset(_V6_ENTRY_KEYS)
    | frozenset(_V8_ENTRY_KEYS)
)

#: And the closed key set of the `mcp` mapping, which is one key wide.
_MCP_KEYS: Final = frozenset({"not_executed_on_error"})

#: SPEC-v0.10 §4.2 — the pin's closed key set, and the shape of a `sha256:` digest.
_UPSTREAM_KEYS: Final = frozenset({"tls_cert_sha256", "tls_cert_file", "tool_schema_sha256"})
_SHA256: Final = re.compile(r"^sha256:[0-9a-f]{64}$")

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
#: `v0.3 §2.5`'s last rank. Here rather than in `control.py` because `_canonical_policy`
#: needs it and dependencies point downward: `policy.py` does not import `control.py`.
DEFAULT_ENVIRONMENT: Final = "production"


def _refuse_reserved(names: Iterable[str], where: str, what: str) -> None:
    """Refuse a **derived** subject used as an argument name, wherever an argument is named.

    `RESERVED_ARGUMENTS` was consulted in exactly one place -- `_split_condition_key` -- and
    `data_scope` is exempted there for policy rules by `DERIVED_SUBJECTS`. So for the one name
    v0.6 added, the set was **inert**: an independent review loaded a `data:` key called
    `data_scope` and an `effect:` template containing `{data_scope}` without a murmur, while
    §7.4's table said both were load errors and `_Rule.matches`'s docstring leaned on it.

    **`DERIVED_SUBJECTS`, and not `RESERVED_ARGUMENTS`, and the narrowing is the finding
    inside the finding.** The first version of this check used the whole reserved set and broke
    shipped code immediately: `user` has been in it since v0.3 and `@protect`-ed functions take
    a `user` parameter throughout this repository's own tests. The two halves of that set are
    not the same rule. `agent`, `user`, `claims`, `issuer` and the rest are refused as
    **condition subjects**, because a rule must not be able to read who is acting (`v0.3 §4.5`)
    -- an *argument* of that name collides with nothing, since policy cannot see the principal
    at all. A derived subject is different in kind: `_ActionPolicy.evaluate` merges
    `{**arguments, **derived}` in one dictionary, so `data_scope` really would be two things at
    one evaluation, decided by merge order in another function.

    §7.4's table row is corrected in the same change to say this rather than the wider claim.
    """
    offending = sorted(name for name in names if name in DERIVED_SUBJECTS)
    if offending:
        raise PolicyError(
            f"{where}: {', '.join(repr(name) for name in offending)} is derived by ctrlrun and "
            f"may not be {what}. §7.4 resolves it at evaluation from the arguments actually "
            "supplied, so an argument of the same name would mean two things in one rule; "
            "rename the argument"
        )


#: §7.4 — the closed key set of one `data:` entry in its mapping form.
#:
#: **`redact:` is not in it.** §7.4 put the key on probation and §7.5's throwaway sector
#: configuration was to earn it; that configuration did not need it, so item 7 cut it and §12
#: records the cut. A `data:` entry is therefore a label, in either shape.
_DATA_KEYS: Final = frozenset({"label"})

#: Keys §7.4 described and item 7 removed, so the refusal can say what happened rather than
#: reading as a typo. **Refused and not ignored**: an operator who writes `redact: true` and gets
#: no error would believe the value is hidden from the evidence, which is worse than not having
#: the feature.
_CUT_DATA_KEYS: Final[Mapping[str, str]] = {
    "redact": (
        "redaction is not in v0.6. §7.5's sector configuration did not need it: a deployment "
        "that may not hold a value in its receipts may not hold it upstream either, so "
        "redacting here would be a weaker second copy of a control that has to live elsewhere "
        "-- and the approval payload carries the real value regardless (§7.4), so it would hide "
        "the value from the auditor and from nobody else"
    ),
}


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


@dataclass(frozen=True)
class _Rule:
    decision: Decision
    conditions: tuple[Condition, ...]
    #: §7.3 — the control ids this rule cites. A rule may narrow or add to the action's.
    controls: tuple[str, ...] = ()

    def matches(
        self,
        action_name: str,
        arguments: Mapping[str, Any],
        derived: Mapping[str, Any] | None = None,
    ) -> bool:
        """Whether every condition holds. A rule with no `when` always matches.

        `derived` carries §7.4's subjects, which are resolved from something other than the
        action's arguments and are **shadowed by nothing**: `data_scope` is a reserved argument
        name, so no real argument can be called it.
        """
        seen = {**arguments, **(derived or {})}
        return all(condition.matches(action_name, seen) for condition in self.conditions)


@dataclass(frozen=True)
class DataLabel:
    """What class of data one argument carries (SPEC-v0.6 §7.4).

    `label` is the operator's own word -- `phi`, `internal`, `pci`. ctrlrun does not know what
    any of them mean; it derives the *set* present in an action's arguments so a rule can see it.

    **There is no `redact`.** §7.4 put it on probation and §7.5's throwaway sector configuration
    was to earn it. That configuration needed a rule that could *see* an argument was PHI and did
    not need the value hidden, so item 7 cut it -- and `_CUT_DATA_KEYS` refuses the key rather
    than ignoring it, because an operator who writes `redact: true` and gets no error believes
    the value is hidden.
    """

    label: str


@dataclass(frozen=True)
class PolicyControl:
    """One entry in the control registry: an identifier and a citation (SPEC-v0.6 §7.3).

    **`PolicyControl` and not `Control`.** It shipped as `ctrlrun.policy.Control` -- a second
    `Control` in a package whose central object is `Control`, which an independent review flagged
    as a name nobody should have to disambiguate at a call site, and which §9.1 would have frozen
    for a long time. Renamed in the same change that adds it to §9.1.1's list.

    **ctrlrun does not interpret a control.** `source:` is a string the operator wrote. The
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
    #: SPEC-v0.8 §3.2 — which role may answer an approval this control was cited on. An opaque
    #: string: ctrlrun does not know what it means, does not check that such a role exists, and
    #: makes no compliance claim on the strength of one, exactly as it does not interpret
    #: `source`. What it does is decide **who may answer an approval the decision already
    #: required**, which is the first thing a control has ever decided (§3.2).
    #:
    #: `None` means this control gates nothing, which is 0.7.0's behaviour for it and is the
    #: opposite answer to a principal whose claims carry no role (§3.5).
    approver_role: str | None = None


def _in_registry_order(cited: tuple[str, ...], order: tuple[str, ...]) -> tuple[str, ...]:
    """`cited`, sorted into the registry's declaration order (SPEC-v0.6 §7.3).

    An id not in `order` keeps its relative position at the end rather than being dropped. That
    cannot happen through the loader -- `_parse_cited` makes a dangling citation a load error --
    but this function is one line from silently losing a control, and losing one is worse than
    ordering it oddly.
    """
    if not order:
        return cited
    rank = {identifier: index for index, identifier in enumerate(order)}
    known = sorted((item for item in cited if item in rank), key=lambda item: rank[item])
    return tuple(known) + tuple(item for item in cited if item not in rank)


@dataclass(frozen=True)
class UpstreamPin:
    """Which server an action entry authorises itself against (SPEC-v0.10 §4.2).

    **Not folded into `McpOptions`**, which is the closest existing name: that one carries
    claims an operator makes about their upstream's *behaviour* (`not_executed_on_error` is a
    `NotExecuted` hint), and this carries a claim about its *identity*, which is an
    authorization input. Merging them would put a pin inside a structure whose documented job
    is a classifier hint.

    **Two TLS keys, because a digest cannot be a trust anchor.** `certs` feeds §4.3's check 3,
    where the pinned certificates become the connection's only trust anchors and a swapped
    server fails the handshake; `SSLContext.load_verify_locations` takes PEM, and there is no
    way to hand OpenSSL a hash and have it validate a chain. `cert_sha256` feeds checks 1 and 2,
    which compare what was observed. An entry pinning by digest alone gets the first two checks
    and not the third, which §4.2 states as a limit rather than leaving to be discovered.

    The two must agree: every certificate `certs` holds hashes to a digest `cert_sha256` names,
    checked at load. A rotation that moved only one half would fail at the handshake on a day
    an operator believed they had prepared for.
    """

    cert_sha256: tuple[str, ...] = ()
    certs: tuple[str, ...] = ()
    tool_schema_sha256: str | None = None

    def __bool__(self) -> bool:
        return bool(self.cert_sha256 or self.certs or self.tool_schema_sha256)


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
    #: SPEC-v0.10 §4.2 — which upstream this entry authorises itself against, or an empty pin.
    upstream: UpstreamPin = field(default_factory=UpstreamPin)
    #: §7.3 — the control ids this action cites, which govern every rule under it.
    controls: tuple[str, ...] = ()
    #: §7.4 — which of this action's arguments carry which class of data.
    data: Mapping[str, DataLabel] = field(default_factory=dict)
    #: SPEC-v0.7 §5.3 — how many attempts may execute on one effect key, the first included, or
    #: `None` where the entry names no ceiling. `None` is today's behaviour and is not a number.
    max_attempts: int | None = None
    #: SPEC-v0.8 §4.2 — how many distinct verified principals must answer, or `None` where the
    #: entry names none. `None` is one, which is 0.7.0.
    approvals_required: int | None = None

    def decisions(self) -> tuple[Decision, ...]:
        """Every decision this entry can reach (SPEC-v0.8 §8.2.1).

        One for a `decision:` entry, one per rule for a `rules:` entry. `Policy.approving_actions`
        reads it to answer "does this action always go to a human", which is not the same as
        "can it": an entry that approves under one condition and allows under another is a
        policy whose change can be made without one.
        """
        if self.decision is not None:
            return (self.decision,)
        return tuple(rule.decision for rule in self.rules)

    def data_scope(self, arguments: Mapping[str, Any]) -> frozenset[str]:
        """The labels present in **the arguments actually supplied** (SPEC-v0.6 §7.4).

        Not the whole declared map: an action that carries no PHI is not a PHI action because
        some other call of it would be.
        """
        return frozenset(self.data[name].label for name in arguments if name in self.data)

    def evaluate(
        self, action_name: str, arguments: Mapping[str, Any], order: tuple[str, ...] = ()
    ) -> Evaluation:
        """Decide this action. `order` is the registry's declaration order (§7.3)."""
        if self.decision is not None:
            return Evaluation(
                self.decision, BARE_DECISION, _in_registry_order(self.controls, order)
            )
        derived = {"data_scope": sorted(self.data_scope(arguments))}
        for index, rule in enumerate(self.rules):
            if rule.matches(action_name, arguments, derived):
                # The union of the action's and the matched rule's, in registry order (§7.3).
                # The action's alone would drop what the rule narrowed to; the rule's alone
                # would drop a control that governs every rule under the action.
                # **In registry order** (§7.3), which this concatenation did not give: it
                # produced the action's citation order followed by the rule's, and an
                # independent review found all three sentences claiming otherwise -- §7.3, the
                # `Evaluation.controls` docstring, and the comment beside the shipped
                # assertion. Order matters here because a receipt is read by a human against
                # the document, and two receipts citing the same set should list it the same
                # way whichever rule matched.
                union = self.controls + tuple(
                    item for item in rule.controls if item not in self.controls
                )
                return Evaluation(rule.decision, f"rule[{index}]", _in_registry_order(union, order))
        # No rule matched, so no rule's controls apply -- and the action's do: they govern
        # everything under it, including this refusal.
        return Evaluation(Decision.DENY, NO_MATCHING_RULE, _in_registry_order(self.controls, order))


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
    controls: Mapping[str, PolicyControl] = field(default_factory=dict)
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
        return cls._from_document(strict_load(text, source), source, text)

    @classmethod
    def _from_document(cls, document: object, source: str, text: str | None = None) -> Policy:
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
        require_v7(document, str(schema), source)
        version = document.get("version")
        if "version" in document and (not isinstance(version, str) or not version.strip()):
            raise PolicyError(
                f"{source}: 'version' must be a non-empty string, got {_type_name(version)}"
            )
        controls = _parse_controls(document.get("controls"), source, schema)
        actions: dict[str, _ActionPolicy] = {}
        for name, entry in entries.items():
            if not isinstance(name, str) or not name:
                raise PolicyError(f"{source}: action names must be non-empty strings, got {name!r}")
            if name == POLICY_CHANGE_ACTION and not _at_least(str(schema), POLICY_SCHEMA_V6):
                # SPEC-v0.8 §8.2.1. An older reader would treat it as an ordinary action name
                # and decide a policy change by whatever rule it found, with nothing saying the
                # document meant the reserved one.
                raise PolicyError(
                    f"{source}: declaring {name!r} needs 'schema: {POLICY_SCHEMA_V6}'; this "
                    f"document declares {schema!r}"
                )
            actions[name] = _parse_entry(
                entry,
                f"{source}: action {name!r}",
                str(schema),
                frozenset(controls),
                # SPEC-v0.7 §5.3 — the line a refused key sits on, recovered from the document's
                # own marks and only where a refusal is about to name one.
                line_of=partial(_entry_key_line, text, name),
            )
        _reject_reserved_elsewhere(actions, source)
        return cls(
            actions=MappingProxyType(actions),
            source=source,
            schema=str(schema),
            environment=environment if isinstance(environment, str) else None,
            mode=mode,
            version=version if isinstance(version, str) else None,
            controls=MappingProxyType(controls),
            _canonical=_canonical_policy(document, str(schema), mode, environment, source),
        )

    def with_action(self, name: str, entry: Mapping[str, Any]) -> Policy:
        """This policy plus one action entry, reparsed (SPEC-v0.8 §8.4, for `verify`).

        Reparsed rather than mutated, because a `Policy` carries its own canonical form and a
        mutated one would hash as the document it is not. Verify uses it to give G21 a
        document that declares its own change: without that the enforcement's effect branch is
        never reached, and the guarantee passes with the enforcement deleted.

        **Verify's, and nothing else's.** A rule's conditions are dropped, which widens that
        rule, and that is acceptable only because the result is a scratch document graded in a
        scratch store and never anything an operator deploys.
        """
        import yaml

        def rendered(policy: _ActionPolicy) -> dict[str, Any]:
            """One entry, **including a `rules:` one**.

            An earlier build emitted only `decision:` entries, so every rules-based action
            disappeared from the rebuilt document and `evaluate` answered `unknown_action` for
            it -- which G21 then reported as its failure, hiding what it was actually grading.
            Conditions are not re-rendered: a rule keeps its decision and loses its `when`,
            which is a **widening** of that rule and is why this is verify's only caller and
            says so.
            """
            if policy.decision is not None:
                return {"decision": str(policy.decision)}
            return {"rules": [{"decision": str(rule.decision)} for rule in policy.rules]}

        document: dict[str, Any] = {
            "schema": POLICY_SCHEMA_V6,
            "actions": {
                **{action: rendered(policy) for action, policy in self.actions.items()},
                name: dict(entry),
            },
        }
        if self.environment is not None:
            document["environment"] = self.environment
        return Policy.from_yaml(yaml.safe_dump(document), source=f"{self.source} +{name}")

    def approving_actions(self) -> frozenset[str]:
        """The action names whose entry sends them to a human under every rule (§8.2.1).

        Used by `Control` to answer "does this policy declare its own change as an approval",
        which is the rule that stops an administrator writing a change rule of `allow`. An
        entry with `rules:` counts only where **every** rule decides `approve`: one that
        allows under some condition is a policy whose change can be made without a human under
        that condition.
        """
        approving: set[str] = set()
        for name, entry in self.actions.items():
            decisions = entry.decisions()
            if decisions and all(decision is Decision.APPROVE for decision in decisions):
                approving.add(name)
        return frozenset(approving)

    def data_scope(self, action: Action) -> frozenset[str]:
        """The set of data labels this action's supplied arguments carry (SPEC-v0.6 §7.4)."""
        entry = self.actions.get(action.name)
        return frozenset() if entry is None else entry.data_scope(action.canonical_arguments)

    def upstream_pin(self, action_name: str) -> UpstreamPin:
        """This action's upstream pin, or an empty one (SPEC-v0.10 §4.2).

        An empty pin is satisfied by anything, which is every action entry written before v0.10
        and why they all upgrade untouched.
        """
        entry = self.actions.get(action_name)
        return UpstreamPin() if entry is None else entry.upstream

    def tool_name(self, action_name: str) -> str | None:
        """The upstream tool this action routes to, for §4.2's tool-schema pin.

        The action name **is** the tool name at the gateway (`v0.2 §6.6` builds the Action from
        `params.name`), so this is the identity today and exists as a name rather than as an
        inlined assumption: a deployment that ever mapped one to the other would change here and
        nowhere else.
        """
        return action_name if action_name in self.actions else None

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

    def approvals_required(self, action_name: str) -> int:
        """How many distinct verified principals must answer this action (SPEC-v0.8 §4.2).

        One where the entry names none, which is 0.7.0, and one for an action no entry names:
        such an action is denied `unknown_action` before an approval exists, so the number is
        never read.
        """
        entry = self.actions.get(action_name)
        if entry is None or entry.approvals_required is None:
            return 1
        return entry.approvals_required

    def max_attempts(self, action_name: str) -> int | None:
        """This action's attempt ceiling, or `None` (SPEC-v0.7 §5.3).

        `None` means the operator named no ceiling, which is 0.6.1's behaviour exactly: a renewal
        over `FAILED` is admitted without bound (`v0.1 §5.4`). It is not a number and never
        defaults to one, because any default would refuse at 0.7.0 a renewal that succeeded at
        0.6.1, and would be a bound nobody chose (§5.4).
        """
        entry = self.actions.get(action_name)
        return None if entry is None else entry.max_attempts

    def evaluate(self, action: Action) -> Evaluation:
        """Decide an action. No side effects; an unlisted action is denied (SPEC-v0.1 §3.4)."""
        entry = self.actions.get(action.name)
        if entry is None:
            return Evaluation(Decision.DENY, UNKNOWN_ACTION)
        # Conditions see exactly what the executor will receive (SPEC-v0.1 §2.2), which is
        # also why a list argument compares equal to a list operand.
        return entry.evaluate(action.name, action.canonical_arguments, tuple(self.controls))


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
    source: str,
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
        # The **parsed** grants, not the raw section, so that the same authority reaches this
        # hash identically whether it was written inline or handed to `Control` as a separate
        # `--authority` document (§7.1). `Control` substitutes its own effective authority here
        # through `hash_with_authority`, and the two agree only because both go through
        # `canonical_grants`.
        "authority": _canonical_authority(document, source),
        # SPEC-v0.6 §7.3's registry, **in the hash**, and the call is worth stating because it
        # cuts against "decision inputs only": a control changes no decision, so on a strict
        # reading it does not belong here. It is in anyway, because `policy_hash` is an
        # auditor's only handle from a receipt back to a document, and the receipt records
        # control **ids** whose meaning lives entirely in this registry. Without it, two
        # receipts with the same hash and the same `controls: [card-data-handling]` could cite
        # different standards.
        #
        # The cost, accepted: editing a control's `title:` changes every later receipt's
        # provenance. That is the same shape as the argument against hashing comments and comes
        # out the other way, because a title is not formatting -- it is what the id means.
        "controls": _plain(document.get("controls")),
        # **The resolved environment, not the raw key.** A document with no `environment:` runs
        # in `production` (`v0.3 §2.5`'s last rank), so hashing `None` here made
        # `Policy.policy_hash` and the receipt's differ for every ordinary document once
        # `Control` began substituting the effective value. The default is spelled here so the
        # two agree in the common case and the substitution below is reserved for a real
        # override -- `Control(environment=...)` or `$CTRLRUN_ENVIRONMENT`.
        "environment": environment if isinstance(environment, str) else DEFAULT_ENVIRONMENT,
        "mode": mode,
        "schema": schema,
    }


def _canonical_authority(document: Mapping[Any, Any], source: str) -> PlainValue:
    """The document's own `authority:` section, parsed and canonicalized, or `None`.

    **`source` is threaded through and not invented.** A first version passed a synthetic
    `"<policy …>"` string and two shipped tests went red: `T84` asserts that a `mode:` nested
    inside a grant is refused with the *file name* in the message, and an error naming a
    placeholder instead of the file an operator has to edit is a worse error.
    """
    from .authority import _from_section, canonical_grants

    section = document.get("authority")
    if section is None:
        return None
    return canonical_grants(_from_section(section, source))


def hash_with_authority(policy: Policy, authority: object, environment: str | None = None) -> str:
    """`policy`'s hash, with `authority` standing in for whatever the document declared (§7.1).

    This exists because `Policy` cannot see a separately-loaded `Authority` and `Control` can.
    An independent review found the gap: §7.1 promises both are folded into one canonical
    structure, and the gateway's shape -- policy from one file, `Authority.from_yaml` from
    another -- folded in nothing, so every receipt in such a deployment carried provenance that
    was silently missing half of what decided the action.

    Where the authority came from the policy document itself -- which is what
    `Control.from_file` does, reading the same file again for its section -- the substitution is
    a no-op and the hash is `policy.policy_hash` exactly, because `_canonical_policy` already
    put the same `canonical_grants` output there. That identity is asserted rather than assumed.

    **And the substitution is a substitution, not a merge.** A `Control` handed a policy with an
    inline `authority:` section and no `authority=` argument does not enforce that section at
    all -- `_authority_result` reads `self._authority` and nothing else -- so the hash records
    the authority that actually decided, which is `None`. That is the honest answer to *"what
    decided this action"* and not a gap; a merge would have recorded grants that governed
    nothing.
    """
    from .authority import Authority, canonical_grants

    if not isinstance(authority, Authority) and authority is not None:
        raise InvalidArgument("hash_with_authority takes an Authority or None")
    folded = dict(policy._canonical)
    folded["authority"] = canonical_grants(authority)
    # SPEC-v0.6 §7.1 lists `environment` among the hashed decision inputs, and `Policy` only
    # knows the **document's**. `Control(environment=...)` and `$CTRLRUN_ENVIRONMENT` outrank it
    # (`_resolve_environment`), so a review found the hash naming an environment that did not
    # decide the action. The receipt carries the effective one in its own field, which made the
    # mismatch detectable and not attributable; substituting it here makes the hash mean what
    # §7.1 says it means.
    folded["environment"] = environment
    return "sha256:" + hashlib.sha256(canonical_bytes(folded)).hexdigest()


def _plain(value: object) -> PlainValue:
    """A YAML fragment as plain JSON-shaped data, so `canonical_bytes` can encode it.

    `yaml.safe_load` already yields dicts, lists, strings, ints, bools and `None`, and
    `canonical_bytes` refuses anything else -- including the `float` a threshold written as
    `50000.0` would produce, which is `v0.1 §2.3`'s rule reaching the policy hash and is the
    right answer: a binary float in a decision input is not portable between two hosts.
    """
    if isinstance(value, Mapping):
        # **A non-string key is refused, not coerced.** `str(key)` made `{1: 2}` and
        # `{"1": 2}` hash identically, and an independent review found the collision reachable:
        # `Policy._from_document` never validates the `authority:` section itself (that is
        # `_optional_from_yaml`'s job, on a different call path), so both documents load as a
        # `Policy` and share a hash. Not exploitable today -- every such document is refused
        # when the section is actually parsed -- but `policy_hash` is supposed to be injective
        # over its inputs, which is the whole property `canonical_bytes` was promoted to give,
        # and "safe because some other loader happens to refuse it" is not that property.
        for key in value:
            if not isinstance(key, str):
                raise PolicyError(
                    f"policy keys must be strings; {key!r} is a {_type_name(key)}. Coercing it "
                    "would make two different documents hash the same (SPEC-v0.6 §7.1)"
                )
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    if value is None or isinstance(value, str | int):  # bool is a subclass of int
        return value
    if isinstance(value, datetime | date | time):
        # `yaml.safe_load` turns an unquoted `expires_at: 2020-01-01T00:00:00Z` into a
        # `datetime`, and such documents load today -- a grant with an expiry is the ordinary
        # case. ISO-8601 is what the same value would have been had it been quoted, and what
        # ctrlrun writes everywhere else, so this loses nothing and invents nothing.
        #
        # An earlier version of this function *refused* here, which broke every authority
        # document with an unquoted expiry. The conformance kit's own `EXPIRED_GRANT` caught it.
        return value.isoformat()
    if isinstance(value, bytes):
        # `errors="replace"` mapped two distinct `!!binary` values onto one string and one
        # hash. Same argument as the key above: a lossy decode inside a hash is a collision,
        # and `canonical_bytes` refuses `bytes` one layer down for exactly this reason.
        raise PolicyError(
            "a policy may not carry binary data; a lossy decode would make two different "
            "documents hash the same (SPEC-v0.6 §7.1)"
        )
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


def require_v4(document: Mapping[Any, Any], schema: str, source: str) -> None:
    """Refuse a `ctrlrun.policy/v4` key in an older document (SPEC-v0.6 §7.1).

    `require_v3`'s shape exactly, one version up. Kept separate rather than folded into a
    version-ordering comparison, because the *consequences* differ per key and the message an
    operator reads is the point: "an older reader would run every action with no authority
    check at all" and "an older reader would refuse the document outright" call for different
    reactions.
    """
    if _at_least(schema, POLICY_SCHEMA_V4):
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


def _reject_unknown_keys(mapping: Mapping[Any, Any], allowed: Iterable[str], where: str) -> None:
    unknown = sorted(repr(key) for key in mapping if key not in set(allowed))
    if unknown:
        raise PolicyError(
            f"{where}: unknown key(s) {', '.join(unknown)}; allowed: {', '.join(sorted(allowed))}"
        )


#: §7.3 — a control id is printed in a per-line CLI table and carried on every receipt that
#: cites it, so it is bounded and free of control characters for `state.py`'s `_approver`
#: reasons: a newline forges a whole row in a listing an operator reads to decide what happened.
#: 200 is generous for an identifier and short enough that a table stays a table.
MAX_CONTROL_ID = 200


def _checked_control_id(identifier: str, source: str) -> None:
    """Refuse a control id that would corrupt the evidence it is printed in (§7.3).

    An independent review found ids accepting newlines and unbounded length. JSON escapes them,
    so a receipt is safe -- but `ctrlrun receipts --control` and every per-line CLI table are
    not, and the id reaches both. A refusal and not an escape, on `_approver`'s reasoning: a
    stored id that differs from the one the operator wrote is worse than a rejected document.
    """
    if len(identifier) > MAX_CONTROL_ID:
        raise PolicyError(
            f"{source}: control id is {len(identifier)} characters; the limit is "
            f"{MAX_CONTROL_ID}. An id is printed in a table and carried on every receipt that "
            "cites it"
        )
    if identifier.splitlines() != [identifier] or any(
        unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"} for character in identifier
    ):
        raise PolicyError(
            f"{source}: control id {identifier!r} contains a control character. `ctrlrun "
            "receipts` prints one record per line, so a line break here forges a row in the "
            "evidence an operator reads"
        )


def _parse_controls(value: object, source: str, schema: str) -> dict[str, PolicyControl]:
    """The top-level `controls:` registry (SPEC-v0.6 §7.3)."""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise PolicyError(
            f"{source}: 'controls' must be a mapping of id to entry, got {_type_name(value)}"
        )
    registry: dict[str, PolicyControl] = {}
    for identifier, entry in value.items():
        if not isinstance(identifier, str) or not identifier.strip():
            raise PolicyError(
                f"{source}: control ids must be non-empty strings, got {identifier!r}"
            )
        _checked_control_id(identifier, source)
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
        role = entry.get("approver_role")
        if "approver_role" in entry:
            if not _at_least(schema, POLICY_SCHEMA_V6):
                raise PolicyError(
                    f"{where}: 'approver_role' needs 'schema: {POLICY_SCHEMA_V6}'; this document "
                    f"declares {schema!r}, and an older reader would load it, gate nobody, and "
                    "report a deployment as checking entitlement when it is not"
                )
            if not isinstance(role, str) or not role.strip():
                raise PolicyError(
                    f"{where}: 'approver_role' must be a non-empty string, got {_type_name(role)}"
                )
            if role != role.strip():
                # Refused rather than trimmed, because §3.4 matches a role against a claim byte
                # for byte: trimming here would make the document and the comparison disagree,
                # and accepting it as written means a role nobody's credential can ever carry,
                # which refuses every approval the control gates and says nothing about why.
                raise PolicyError(
                    f"{where}: 'approver_role' has leading or trailing whitespace ({role!r}); "
                    "roles are matched byte for byte against a claim, so this one would match "
                    "nothing and refuse every approval this control gates"
                )
        registry[identifier] = PolicyControl(
            id=identifier,
            title=title,
            source=cited if isinstance(cited, str) else None,
            approver_role=role if isinstance(role, str) else None,
        )
    return registry


def _parse_data(value: object, where: str) -> dict[str, DataLabel]:
    """An action's `data:` map (SPEC-v0.6 §7.4).

    Two shapes, and both are just a label:

        data:
          patient_id: phi
          diagnosis: {label: phi}

    The mapping form existed for `redact:`, which §7.5's throwaway configuration did not need
    and item 7 cut (`_CUT_DATA_KEYS`). It is kept because it is the shape a key would grow into
    if one is ever earned, and because refusing it now would be a second edit for no gain --
    but a document using it says nothing the short form does not.
    """
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise PolicyError(
            f"{where}: 'data' must be a mapping of argument name to label, got {_type_name(value)}"
        )
    labels: dict[str, DataLabel] = {}
    _refuse_reserved(
        [name for name in value if isinstance(name, str)], f"{where}: 'data'", "an argument name"
    )
    for name, entry in value.items():
        if not isinstance(name, str) or not name.strip():
            raise PolicyError(f"{where}: argument names in 'data' must be non-empty strings")
        at = f"{where} data {name!r}"
        if isinstance(entry, str):
            if not entry.strip():
                raise PolicyError(f"{at}: a label must be a non-empty string")
            labels[name] = DataLabel(label=entry)
            continue
        if not isinstance(entry, Mapping):
            raise PolicyError(
                f"{at}: must be a label or a mapping with 'label', got {_type_name(entry)}"
            )
        for cut, why in _CUT_DATA_KEYS.items():
            if cut in entry:
                raise PolicyError(f"{at}: {cut!r} is not a data key: {why}")
        _reject_unknown_keys(entry, _DATA_KEYS, at)
        label = entry.get("label")
        if not isinstance(label, str) or not label.strip():
            raise PolicyError(f"{at}: 'label' must be a non-empty string, got {_type_name(label)}")
        labels[name] = DataLabel(label=label)
    return labels


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


def _reject_reserved_elsewhere(actions: Mapping[str, _ActionPolicy], source: str) -> None:
    """SPEC-v0.8 §8.2.1: nothing else may name the reserved action as a resource or an effect.

    The name is what gates the exemption in §8.4 and the marker in §8.2.1, and a document that
    could make an ordinary action expand to `policy:<hash>` -- or render `ctrlrun.policy.change`
    as its resource -- would be a document that could mint the marker of an approved policy from
    an action nobody reviewed as one. Matched as a substring of the template, because a
    template is expanded later and a placeholder could otherwise carry the name in.
    """
    for name, entry in actions.items():
        if name == POLICY_CHANGE_ACTION:
            continue
        for label, template in (("resource", entry.resource), ("effect", entry.effect)):
            if template is not None and POLICY_CHANGE_ACTION in template:
                raise PolicyError(
                    f"{source}: action {name!r} names {POLICY_CHANGE_ACTION!r} in its "
                    f"{label!r} template. That name is reserved for the policy-change flow "
                    "(SPEC-v0.8 §8.2.1), and an action that could expand to it would be a way "
                    "to mark a policy approved without proposing one"
                )
        if entry.effect is not None and entry.effect.startswith("policy:"):
            raise PolicyError(
                f"{source}: action {name!r} declares an 'effect:' template beginning 'policy:', "
                "which is the effect key a policy approval is recorded under (SPEC-v0.8 §8.4). "
                "An action reserving that key could mark a policy approved"
            )


def _parse_upstream(value: object, where: str) -> UpstreamPin:
    """SPEC-v0.10 §4.2. Refuse what the pin cannot mean, at load, where an operator is present.

    A malformed pin is a `PolicyError` and never a pin that quietly matches nothing: a key whose
    typo turns it off is the fail-open direction, and §4.5's whole point is that an unverified
    upstream refuses rather than passes.
    """
    if value is None:
        return UpstreamPin()
    if not isinstance(value, Mapping):
        raise PolicyError(f"{where}: 'upstream' must be a mapping")
    unknown = set(value) - _UPSTREAM_KEYS
    if unknown:
        raise PolicyError(
            f"{where}: unknown 'upstream' key(s) {sorted(unknown)!r}; the pin's keys are "
            f"{sorted(_UPSTREAM_KEYS)!r} (SPEC-v0.10 §4.2)"
        )
    digests = value.get("tls_cert_sha256", [])
    if isinstance(digests, str):
        digests = [digests]
    if not isinstance(digests, list) or not all(isinstance(item, str) for item in digests):
        raise PolicyError(
            f"{where}: 'upstream.tls_cert_sha256' must be a list of 'sha256:…' strings; it is a "
            "LIST so an operator can carry the current and the next certificate across a "
            "rotation without an outage (SPEC-v0.10 §4.2)"
        )
    for digest in digests:
        if not _SHA256.match(digest):
            raise PolicyError(
                f"{where}: 'upstream.tls_cert_sha256' entry {digest!r} is not 'sha256:' "
                "followed by 64 hex characters"
            )
    files = value.get("tls_cert_file", [])
    if isinstance(files, str):
        files = [files]
    if not isinstance(files, list) or not all(isinstance(item, str) for item in files):
        raise PolicyError(f"{where}: 'upstream.tls_cert_file' must be a path or a list of paths")
    schema_hash = value.get("tool_schema_sha256")
    if schema_hash is not None and (
        not isinstance(schema_hash, str) or not _SHA256.match(schema_hash)
    ):
        raise PolicyError(
            f"{where}: 'upstream.tool_schema_sha256' must be 'sha256:' followed by 64 hex chars"
        )
    pin = UpstreamPin(
        cert_sha256=tuple(digests), certs=tuple(files), tool_schema_sha256=schema_hash
    )
    _check_pin_correspondence(pin, where)
    return pin


def _check_pin_correspondence(pin: UpstreamPin, where: str) -> None:
    """The two TLS halves must agree, checked at load (SPEC-v0.10 §4.2).

    §4.2 measured what a half-moved rotation costs and then this check was not written, which an
    independent review found: a document whose `tls_cert_file` still held only the old certificate
    while `tls_cert_sha256` had both loaded cleanly and failed at the **handshake**, on the day an
    operator believed they had prepared for. That is the outage §4.2 says the list prevents,
    arriving one layer down.

    So: every certificate `tls_cert_file` holds hashes to a digest `tls_cert_sha256` names, and a
    path that does not exist is a load error rather than an empty trust store discovered at the
    first connection.
    """
    if not pin.certs:
        return
    import hashlib
    import ssl

    for path in pin.certs:
        try:
            der_list = [
                ssl.PEM_cert_to_DER_cert(block + "-----END CERTIFICATE-----")
                for block in Path(path).read_text().split("-----END CERTIFICATE-----")
                if "BEGIN CERTIFICATE" in block
            ]
        except OSError as unreadable:
            raise PolicyError(
                f"{where}: 'upstream.tls_cert_file' names {path!r}, which could not be read "
                f"({unreadable.strerror}); a pin whose certificate is missing builds an empty "
                "trust store and refuses every connection (SPEC-v0.10 §4.2)"
            ) from unreadable
        except ValueError as malformed:
            raise PolicyError(
                f"{where}: 'upstream.tls_cert_file' names {path!r}, which is not PEM: {malformed}"
            ) from malformed
        if not der_list:
            raise PolicyError(
                f"{where}: 'upstream.tls_cert_file' names {path!r}, which holds no certificate"
            )
        if not pin.cert_sha256:
            continue
        digests = {"sha256:" + hashlib.sha256(der).hexdigest() for der in der_list}
        if not digests & set(pin.cert_sha256):
            raise PolicyError(
                f"{where}: 'upstream.tls_cert_file' {path!r} hashes to "
                f"{sorted(digests)[0]}, which 'upstream.tls_cert_sha256' does not name. The two "
                "halves pin the same certificates or a rotation that moves one fails at the "
                "handshake (SPEC-v0.10 §4.2)"
            )


def _parse_entry(
    entry: object,
    where: str,
    schema: str,
    known: frozenset[str] = frozenset(),
    *,
    line_of: Callable[[str], int | None] = lambda key: None,
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
    for key, consequence in _V4_ENTRY_KEYS.items():
        if key in entry and not _at_least(schema, POLICY_SCHEMA_V4):
            raise PolicyError(
                f"{where}: {key!r} needs 'schema: {POLICY_SCHEMA_V4}'; this document declares "
                f"{schema!r}, and {consequence}"
            )
    for key, consequence in _V5_ENTRY_KEYS.items():
        if key in entry and not _at_least(schema, POLICY_SCHEMA_V5):
            raise PolicyError(
                f"{where}: {key!r}{_at_line(line_of(key))} needs 'schema: {POLICY_SCHEMA_V5}'; "
                f"this document declares {schema!r}, and {consequence}"
            )
    for key, consequence in _V6_ENTRY_KEYS.items():
        if key in entry and not _at_least(schema, POLICY_SCHEMA_V6):
            raise PolicyError(
                f"{where}: {key!r}{_at_line(line_of(key))} needs 'schema: {POLICY_SCHEMA_V6}'; "
                f"this document declares {schema!r}, and {consequence}"
            )
    for key, consequence in _V8_ENTRY_KEYS.items():
        if key in entry and not _at_least(schema, POLICY_SCHEMA_V8):
            raise PolicyError(
                f"{where}: {key!r}{_at_line(line_of(key))} needs 'schema: {POLICY_SCHEMA_V8}'; "
                f"this document declares {schema!r}, and {consequence}"
            )
    labels = _parse_data(entry.get("data"), where)
    pin = _parse_upstream(entry.get("upstream"), where)
    ceiling = _parse_max_attempts(entry, where, line_of)
    required = _parse_approvals_required(entry, where, line_of)

    if has_decision:
        return _ActionPolicy(
            decision=_parse_decision(entry["decision"], where),
            rules=(),
            effect=effect,
            resource=resource,
            mcp=mcp,
            upstream=pin,
            controls=cited,
            data=MappingProxyType(labels),
            max_attempts=ceiling,
            approvals_required=required,
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
        upstream=pin,
        controls=cited,
        data=MappingProxyType(labels),
        max_attempts=ceiling,
        approvals_required=required,
    )


def _at_line(line: int | None) -> str:
    """` on line N`, or nothing where the document's marks could not be recovered."""
    return "" if line is None else f" on line {line}"


def _parse_approvals_required(
    entry: Mapping[Any, Any], where: str, line_of: Callable[[str], int | None]
) -> int | None:
    """The M-of-N threshold, validated at load (SPEC-v0.8 §4.2).

    `v0.7 §5.3`'s rules for `max_attempts`, for the same reason: a document that cannot say how
    many approvals it requires is a document nobody should deploy, and finding out when the first
    grant consumes is finding out late.

    `bool` is refused although Python makes it an `int` (`v0.1 §3.2`). `0` is refused rather than
    read as "no approval needed", which is what `decision: allow` says, or as "never", which is
    `decision: deny`. Absent means 1, which is 0.7.0.
    """
    if "approvals_required" not in entry:
        return None
    value = entry["approvals_required"]
    at = _at_line(line_of("approvals_required"))
    if isinstance(value, bool) or not isinstance(value, int):
        raise PolicyError(
            f"{where}: 'approvals_required'{at} must be an integer of at least 1, got "
            f"{_type_name(value)} {value!r}. It counts the distinct verified principals that "
            "must answer; remove the key for one"
        )
    if value < 1:
        raise PolicyError(
            f"{where}: 'approvals_required'{at} must be at least 1, got {value}. Zero approvals "
            "is 'decision: allow', and an action nobody may approve is 'decision: deny'"
        )
    return value


def _parse_max_attempts(
    entry: Mapping[Any, Any], where: str, line_of: Callable[[str], int | None]
) -> int | None:
    """The attempt ceiling, validated at load (SPEC-v0.7 §5.3).

    **At load, and naming the key, the action and the line**, so a malformed ceiling fails the
    policy rather than the execution: a document that cannot say how many attempts it permits is
    a document nobody should deploy, and finding out at the fourth dispatch is finding out late.

    `bool` is refused although Python makes it an `int` (`v0.1 §3.2`): `max_attempts: true` is a
    typo for a number and not a ceiling of one. `0` is refused rather than read as "unlimited"
    (`v0.7 §1.1`: no value of this key relaxes it) or as "never run" (`max_attempts` counts what
    executes, and an action that may never execute is a `decision: deny`). There is no upper
    bound: a very large ceiling is the operator's statement that they meant it.
    """
    if "max_attempts" not in entry:
        return None
    value = entry["max_attempts"]
    at = _at_line(line_of("max_attempts"))
    if isinstance(value, bool) or not isinstance(value, int):
        raise PolicyError(
            f"{where}: 'max_attempts'{at} must be an integer of at least 1, got "
            f"{_type_name(value)} {value!r}. It counts the attempts that may execute on one "
            "effect key, the first included; remove the key for no ceiling"
        )
    if value < 1:
        raise PolicyError(
            f"{where}: 'max_attempts'{at} must be an integer of at least 1, got {value!r}. "
            "It counts the attempts that may execute on one effect key, the first included, so "
            "there is no ceiling below 1; remove the key for no ceiling"
        )
    return value


def _entry_key_line(text: str | None, action: str, key: str) -> int | None:
    """The 1-based line `actions: <action>: <key>:` sits on, or `None`.

    Composed on demand, on the refusal path only, rather than carried through the parse: the
    loader hands `_parse_entry` a plain document, and PyYAML drops a node's marks the moment it
    constructs one. Composing again reads the same text with the same loader and asks it for the
    one mark the message needs, at the cost of a second parse of a document that is about to be
    refused. `_StrictLoader.construct_mapping` never runs here, so the duplicate-key refusal is
    unaffected either way: that one already fired, in `strict_load`, before this could.
    """
    if text is None:
        return None
    try:
        root = yaml.compose(text, Loader=_StrictLoader)
    except (yaml.YAMLError, ValueError, OverflowError):  # pragma: no cover - strict_load ran first
        return None
    node = _child(_child(root, "actions"), action)
    found = None if node is None else _key_node(node, key)
    return None if found is None else int(found.start_mark.line) + 1


def _child(node: Any, key: str) -> Any:  # noqa: ANN401 - PyYAML ships no stubs
    found = _key_node(node, key)
    if found is None:
        return None
    return next(value for name, value in node.value if name is found)


def _key_node(node: Any, key: str) -> Any:  # noqa: ANN401 - PyYAML ships no stubs
    if not isinstance(node, yaml.MappingNode):
        return None
    for name, _ in node.value:
        if isinstance(name, yaml.ScalarNode) and name.value == key:
            return name
    return None


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
        named = template_placeholders(value)
    except InvalidArgument as exc:
        raise PolicyError(f"{where}: {kwarg!r}: {exc}") from exc
    _refuse_reserved(named, f"{where}: {kwarg!r}", "a template placeholder")
    return value


def _parse_mcp(value: object, where: str) -> McpOptions:
    if value is None:
        return _DEFAULT_MCP_OPTIONS
    if not isinstance(value, Mapping):
        raise PolicyError(f"{where}: 'mcp' must be a mapping, got {_type_name(value)}")
    _reject_unknown_keys(value, _MCP_KEYS, f"{where}: mcp")
    claimed = value.get("not_executed_on_error", False)
    # SPEC-v0.2 §3.1 — a bool, and `1` is not one. This is an assertion about a remote that
    # ctrlrun cannot check (§6.8), so it is made deliberately or not at all.
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
        conditions=tuple(parse_conditions(when, where=where, allow_derived=True).values()),
        controls=cited,
    )
