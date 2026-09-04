"""Grants, patterns, containment and evaluation. Build-list item 2; SPEC-v0.3 §4.

Authority is the second axis. Policy answers *how much autonomy does this action have*, and
has never been able to see who is asking (`v0.1 §3.2` refuses `agent_eq` at load, and still
does). Authority answers *may this principal propose it at all*, and sees nothing else: a
grant carries no `decision:`, so how much autonomy an action has is the same for every
principal (§4.2, §4.7).

Three rules shape every function below.

**Opt-in, then fail-closed** (§4.1). A document with no `authority:` key produces no
`Authority` at all — `_optional_from_yaml` returns `None` and `Control` behaves exactly as
v0.2. A document with one governs every action, and no matching grant is `DENY`.

**The grammar is small on purpose** (§4.4). `fnmatch` would make `contains` — the relation
build-list item 3's attenuation rests on — an approximation rather than a decision. So: a
literal, a `prefix*` that cannot cross a separator, and a final `**` whose preceding segments
are all literals. Nothing else parses.

**This module is pure.** It reads a `StateStore` and never writes to one, and it appends no
event. `Control` does both (§4.8), which is what keeps `ARCHITECTURE.md` §6's single composer
single.
"""

from __future__ import annotations

import itertools
import json
import re
import secrets
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from types import MappingProxyType
from typing import Any, Final, Literal

import yaml

from .action import Action, Principal
from .errors import AuthorityEscalation, IdentityError, InvalidArgument, PolicyError
from .policy import SUPPORTED_SCHEMAS, Condition, parse_conditions, require_v3
from .policy import _equal as _type_strict_equal
from .state import DelegationRecord, StateStore

#: SPEC-v0.3 §4.4 — an action name is dotted (`v0.1 §2.1`) and a resource is `type:id`.
ACTION_SEPARATOR: Final = "."
RESOURCE_SEPARATOR: Final = ":"

#: The only spelling for "everything", and one token long so a review can grep for it.
DEEP_WILDCARD: Final = "**"

#: SPEC-v0.3 §4.3 — the closed set of reasons, and the one a *passing* result carries.
AUTHORITY_GRANT: Final = "authority_grant"
AUTHORITY_UNREADABLE: Final = "authority_unreadable"
AUTHORITY_ESCALATION: Final = "authority_escalation"
AUTHORITY_REVOKED: Final = "authority_revoked"
AUTHORITY_EXPIRED: Final = "authority_expired"
AUTHORITY_CONSTRAINT: Final = "authority_constraint"
NO_AUTHORITY: Final = "no_authority"

#: §4.3 — fixed rather than short-circuited, so the evidence for one configuration does not
#: depend on the order grants appear in the document. `authority_expired` outranks
#: `authority_constraint` deliberately: expiry is a property of the grant and a failed
#: constraint a property of the call, and the reason should name what will still be wrong
#: after the caller changes the request.
REASON_PRECEDENCE: Final = (
    AUTHORITY_UNREADABLE,
    AUTHORITY_ESCALATION,
    AUTHORITY_REVOKED,
    AUTHORITY_EXPIRED,
    AUTHORITY_CONSTRAINT,
    NO_AUTHORITY,
)

#: SPEC-v0.3 §5.5 — a root grant is depth 0, a delegation of it depth 1. `0` is valid and
#: means no delegation may be created at all.
DEFAULT_MAX_DELEGATION_DEPTH: Final = 3

#: §4.2 — a delegation names its parent by id, so two grants answering to one name is an
#: ambiguity nobody can resolve later.
_GRANT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

#: §5.2 mints delegation ids in this namespace, and one namespace addresses both kinds.
DELEGATION_ID_PREFIX: Final = "dlg_"

#: §5.2 — enforced on the way *out* of the store as well as on the way in. A row that does not
#: match is a corrupted record, never a grant: without this a hand-inserted row named
#: `head-of-finance` could shadow the root grant of that name.
_DELEGATION_ID = re.compile(r"^dlg_[0-9a-f]{32}$")
_ID_HEX_BYTES: Final = 16  # "dlg_" + 32 hex chars

#: §5.2, §5.7 — `api` for `Control.delegate`, where `by` came from wherever the application's
#: identity came from, and `cli` for `ctrlrun delegate`, where it came from a shell. A reader of
#: the evidence can tell an act from an assertion, which is the whole reason the field exists.
_CREATED_VIA: Final[Mapping[str, Literal["api", "cli"]]] = {"api": "api", "cli": "cli"}

#: SPEC-v0.3 §5.3 — the creation-time vocabulary. Disjoint from the evaluation reasons above,
#: and never used interchangeably with them: a test asserting `authority_escalation` on a
#: creation is asserting a value that creation never produces.
UNKNOWN_PARENT: Final = "unknown_parent"
PARENT_NOT_DELEGABLE: Final = "parent_not_delegable"
PARENT_NOT_VALID: Final = "parent_not_valid"
NOT_THE_SUBJECT: Final = "not_the_subject"
MAX_DEPTH: Final = "max_depth"
CONTAINMENT: Final = "containment"

#: §5.4's rows, in the order they are checked. Each is separately testable and separately
#: mutable; T76 breaks each one alone.
DIMENSIONS: Final = ("subject", "actions", "resources", "constraints", "environments", "expires_at")

#: §5.4 — how a child operand must compare with its parent's, per operator. `neq` is equality
#: rather than the superset a deny-list would need: `v0.1 §3.2` has no deny-list operator and a
#: `when:` mapping holds at most one `neq` per argument, so "the parent's exclusions plus more"
#: is not expressible even in principle.
_STRICTER: Final = {
    "lt": lambda child, parent: child <= parent,
    "lte": lambda child, parent: child <= parent,
    "gt": lambda child, parent: child >= parent,
    "gte": lambda child, parent: child >= parent,
}

_AUTHORITY_KEY: Final = "authority"
_AUTHORITY_KEYS: Final = frozenset({"max_delegation_depth", "grants"})
_GRANT_KEYS: Final = frozenset(
    {
        "id",
        "subject",
        "actions",
        "resources",
        "constraints",
        "environments",
        "expires_at",
        "delegable",
    }
)
_SUBJECT_KEYS: Final = frozenset({"agent", "user"})

#: §4.8 — `standalone=False` tolerates the keys the policy loader owns; `standalone=True` is
#: §8.3's `--authority` document, whose key set is closed at exactly `schema` and `authority`.
_POLICY_OWNED_KEYS: Final = frozenset({"schema", "actions", "mode", "environment"})
_STANDALONE_KEYS: Final = frozenset({"schema", _AUTHORITY_KEY})

#: A grant with no constraints. Shared, because it is immutable — handed out through a
#: `default_factory` for the reason `action.NO_CLAIMS` is (Python 3.11 refuses any unhashable
#: dataclass default, `mappingproxy` included).
NO_CONSTRAINTS: Final[Mapping[str, Condition]] = MappingProxyType({})


def _type_name(value: object) -> str:
    return type(value).__name__


# --- patterns (SPEC-v0.3 §4.4) ---------------------------------------------------------


def _segments(pattern: str, separator: str | None) -> tuple[str, ...]:
    return (pattern,) if separator is None else tuple(pattern.split(separator))


def validate_pattern(pattern: object, *, separator: str | None, where: str) -> str:
    """Refuse anything outside §4.4's grammar, and return the pattern.

    `separator=None` is a subject pattern: one segment, no separator, and therefore no deep
    wildcard — `agent: "**"` would give "any agent" a spelling a review grep for `"*"` misses
    (§4.2).
    """
    if not isinstance(pattern, str) or not pattern:
        raise InvalidArgument(f"{where}: a pattern must be a non-empty string, got {pattern!r}")
    segments = _segments(pattern, separator)
    last = len(segments) - 1
    for index, segment in enumerate(segments):
        if not segment:
            raise InvalidArgument(
                f"{where}: {pattern!r} has an empty segment; a pattern is a non-empty sequence "
                f"of segments joined by {separator!r}"
            )
        if "?" in segment or "[" in segment or "]" in segment:
            raise InvalidArgument(
                f"{where}: {pattern!r} is not a pattern; there is no '?' and there are no "
                "character classes. A pattern is a literal, a trailing '*', or a final '**'"
            )
        if segment == DEEP_WILDCARD:
            if separator is None:
                raise InvalidArgument(
                    f"{where}: {pattern!r} may not carry a deep wildcard; a subject has no "
                    "separator, so '**' means nothing there that '*' does not. Write '*'"
                )
            if index != last:
                raise InvalidArgument(f"{where}: {pattern!r} may use '**' only as its last segment")
            for earlier in segments[:index]:
                if "*" in earlier:
                    raise InvalidArgument(
                        f"{where}: {pattern!r} puts a wildcard before a '**'; every segment "
                        "before a deep wildcard must be a literal, so containment is decided "
                        "by comparison rather than by reasoning about two kinds of wildcard"
                    )
            continue
        stars = segment.count("*")
        if stars > 1 or (stars == 1 and not segment.endswith("*")):
            raise InvalidArgument(
                f"{where}: {pattern!r} has a segment {segment!r} that is neither a literal nor "
                "a literal followed by one trailing '*'. There is no leading or infix '*', and "
                "'**' is never part of a larger segment"
            )
    return pattern


def matches(pattern: str, value: str, *, separator: str | None) -> bool:
    """Does `value` match `pattern`? Segment-wise, case-sensitive, no normalization (§4.4)."""
    parts = _segments(pattern, separator)
    actual = _segments(value, separator)
    if parts[-1] == DEEP_WILDCARD:
        prefix = parts[:-1]
        return len(actual) > len(prefix) and actual[: len(prefix)] == prefix
    if len(parts) != len(actual):
        return False
    return all(_segment_matches(part, item) for part, item in zip(parts, actual, strict=True))


def _segment_matches(pattern: str, value: str) -> bool:
    if pattern.endswith("*"):
        return value.startswith(pattern[:-1])
    return pattern == value


def contains(parent: str, child: str, *, separator: str | None) -> bool:
    """Is every value matching `child` also matched by `parent`? (SPEC-v0.3 §5.5.)

    A different relation from `matches`, with counterintuitive clauses — `Q.**` does not
    contain `Q` — and an implementation of it as `fnmatch` or `startswith` passes an
    evaluation test while breaking attenuation. `**` counts as one segment everywhere below,
    which is what makes every well-formed pattern contain itself.
    """
    outer = _segments(parent, separator)
    inner = _segments(child, separator)
    if outer == (DEEP_WILDCARD,):
        return True
    if outer[-1] == DEEP_WILDCARD:
        prefix = outer[:-1]
        return len(inner) > len(prefix) and inner[: len(prefix)] == prefix
    if inner[-1] == DEEP_WILDCARD or len(inner) != len(outer):
        return False
    return all(_segment_contains(out, item) for out, item in zip(outer, inner, strict=True))


def _segment_contains(parent: str, child: str) -> bool:
    parent_wild = parent.endswith("*")
    child_wild = child.endswith("*")
    if not parent_wild:
        # literal ⊆ literal iff equal; a prefix wildcard is never inside a literal.
        return not child_wild and parent == child
    return child[: -1 if child_wild else None].startswith(parent[:-1])


# --- the model (SPEC-v0.3 §4.8) --------------------------------------------------------


@dataclass(frozen=True)
class Subject:
    """Who a grant is addressed to: an agent pattern, a user pattern, or both (§4.2).

    Both `None` is refused. Without that, a holder of a narrow grant could mint a child
    addressed to every principal in the deployment — widening the population rather than the
    powers. "Any agent" is spelled `Subject(agent="*")`, which is greppable.
    """

    agent: str | None = None
    user: str | None = None

    def __post_init__(self) -> None:
        if self.agent is None and self.user is None:
            raise InvalidArgument(
                "a subject must declare at least one of 'agent' and 'user'; a subject matching "
                "every principal is one nobody means to write by accident (SPEC-v0.3 §4.2)"
            )
        if self.agent is not None:
            validate_pattern(self.agent, separator=None, where="subject.agent")
        if self.user is not None:
            validate_pattern(self.user, separator=None, where="subject.user")

    def matches(self, principal: Principal) -> bool:
        """§4.2 — a grant scoped to a human is not satisfied by an agent acting alone."""
        if self.agent is not None and not matches(self.agent, principal.agent, separator=None):
            return False
        if self.user is not None:
            if principal.user is None:
                return False
            if not matches(self.user, principal.user, separator=None):
                return False
        return True


@dataclass(frozen=True)
class Grant:
    """One permission: this subject may propose these actions, under these limits (§4.2).

    `__post_init__` validates everything the YAML loader validates, so the constructor refuses
    exactly what the loader refuses. That is not decoration: `Control.delegate` takes a `Grant`
    built in Python, and §5.5's segment relation is undefined on a segment like `a**`, so
    without it item 3's containment check would be discharging a proof about a value nothing
    validated.
    """

    id: str
    subject: Subject
    actions: tuple[str, ...] = ()
    resources: tuple[str, ...] | None = None
    constraints: Mapping[str, Condition] = field(default_factory=lambda: NO_CONSTRAINTS)
    environments: tuple[str, ...] | None = None
    expires_at: datetime | None = None
    delegable: bool = False

    def __post_init__(self) -> None:
        # An empty id is legal only on the `Control.delegate` path and only until the call
        # returns (§4.2); a `dlg_`-prefixed one is what §5.2 mints and what a delegation
        # record is parsed back into, so the model accepts it and the loader refuses it.
        if self.id and not _GRANT_ID.match(self.id):
            raise InvalidArgument(f"grant id {self.id!r} must match {_GRANT_ID.pattern}")
        object.__setattr__(self, "actions", tuple(self.actions))
        if not self.actions:
            raise InvalidArgument(f"grant {self.id!r}: 'actions' must be a non-empty list")
        for pattern in self.actions:
            validate_pattern(pattern, separator=ACTION_SEPARATOR, where=f"grant {self.id!r} action")
        if self.resources is not None:
            object.__setattr__(self, "resources", tuple(self.resources))
            if not self.resources:
                raise InvalidArgument(
                    f"grant {self.id!r}: 'resources' must be a non-empty list, or absent — "
                    "an absent 'resources' is what grants any resource (SPEC-v0.3 §4.2)"
                )
            for pattern in self.resources:
                validate_pattern(
                    pattern, separator=RESOURCE_SEPARATOR, where=f"grant {self.id!r} resource"
                )
        if self.environments is not None:
            object.__setattr__(self, "environments", tuple(self.environments))
            if not self.environments:
                raise InvalidArgument(
                    f"grant {self.id!r}: 'environments' must be a non-empty list, or absent"
                )
            for name in self.environments:
                if not isinstance(name, str) or not name:
                    raise InvalidArgument(
                        f"grant {self.id!r}: an environment must be a non-empty string, "
                        f"got {name!r}"
                    )
        for key, condition in self.constraints.items():
            if not isinstance(condition, Condition):
                raise InvalidArgument(
                    f"grant {self.id!r}: constraint {key!r} must be a policy.Condition, "
                    f"got {_type_name(condition)}"
                )
            if key != f"{condition.argument}_{condition.op}" or key != condition.key:
                # The sharpest case of all: containment would compare one thing and §5.2 would
                # store another.
                raise InvalidArgument(
                    f"grant {self.id!r}: constraint key {key!r} disagrees with the condition it "
                    f"holds ({condition.argument!r}, {condition.op!r})"
                )
        object.__setattr__(self, "constraints", MappingProxyType(dict(self.constraints)))
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise InvalidArgument(
                f"grant {self.id!r}: 'expires_at' must carry an offset; an expiry in an "
                "unstated timezone cannot be checked (SPEC-v0.3 §4.2)"
            )
        if self.delegable and self.expires_at is None:
            # Nothing else bounds the *population* a delegable grant can reach: creation is
            # non-idempotent and nothing caps children per parent, so a compromised holder can
            # mint one full-width child per agent name it can think of. An expiry makes §5.4's
            # `expires_at` row bound every descendant in time by construction.
            raise InvalidArgument(
                f"grant {self.id!r}: a grant with 'delegable: true' must declare 'expires_at' "
                "(SPEC-v0.3 §4.2)"
            )

    def is_expired(self, now: datetime) -> bool:
        """`now > expires_at`; a grant is valid up to and including its expiry instant."""
        return self.expires_at is not None and now > self.expires_at

    def matches_shape(self, action: Action) -> bool:
        """Subject, action name, resource and environment — all four, or no match (§4.3)."""
        if not self.subject.matches(action.principal):
            return False
        if not any(
            matches(pattern, action.name, separator=ACTION_SEPARATOR) for pattern in self.actions
        ):
            return False
        if self.resources is not None:
            # §4.4 — an action whose resource is None does not match a grant that declares
            # `resources:`. To grant an action that carries no resource, omit the key.
            if action.resource is None:
                return False
            if not any(
                matches(pattern, action.resource, separator=RESOURCE_SEPARATOR)
                for pattern in self.resources
            ):
                return False
        # §4.2 — matched by exact string, not by pattern: an environment name is a short
        # closed list, and a glob over it buys nothing but a way to typo `prod*` into
        # matching `production-canary`.
        return self.environments is None or action.environment in self.environments

    def constraints_hold(self, action: Action) -> bool:
        """All constraints, ANDed. An absent argument makes one false and logs (§4.5)."""
        arguments = action.canonical_arguments
        return all(
            condition.matches(action.name, arguments) for condition in self.constraints.values()
        )


@dataclass(frozen=True)
class Delegation:
    """A grant created at runtime by a principal who already holds one (SPEC-v0.3 §5.1).

    The parsed form of a `DelegationRecord`: `state.py` persists rows with `grant_json` as a
    string, and this module is what parses a `Grant` out of one. `grant.id` **is** the
    `delegation_id` — one namespace addresses root grants and delegations, and `--parent`
    takes either (§5.2).
    """

    delegation_id: str
    parent_id: str
    depth: int
    grant: Grant
    created_by: Principal
    created_via: Literal["api", "cli"]
    created_at: datetime
    revoked_at: datetime | None = None
    revoked_by: str | None = None

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def to_record(self) -> DelegationRecord:
        """The row `StateStore.put_delegation` writes (§5.2)."""
        return DelegationRecord(
            delegation_id=self.delegation_id,
            parent_id=self.parent_id,
            depth=self.depth,
            grant_json=grant_to_json(self.grant),
            created_by_agent=self.created_by.agent,
            created_by_user=self.created_by.user,
            created_via=self.created_via,
            created_at=self.created_at,
            revoked_at=self.revoked_at,
            revoked_by=self.revoked_by,
        )


@dataclass(frozen=True)
class AuthorityResult:
    """What the authority axis decided, and which grant it decided on (§4.8).

    The four trailing fields carry §7's `AUTHORITY_DENIED` evidence: the `dimension` a §5.6
    rule-4 step failed on, the `missing_parent_id` of rule 1, the `expired_parent_id` of rule
    3, and rule 5's `depth_exceeded` or `cycle_at`. They exist because rules 1, 3, 4, 5 and 6
    all report `authority_escalation`, so without them five guards would be indistinguishable
    in evidence and no test could tell which had run.
    """

    passed: bool
    reason: str
    grant_id: str | None = None
    delegation_id: str | None = None
    depth: int = 0
    dimension: str | None = None
    missing_parent_id: str | None = None
    expired_parent_id: str | None = None
    depth_exceeded: int | None = None
    cycle_at: str | None = None


# --- serialization and containment (SPEC-v0.3 §5.2, §5.4, §5.5) -------------------------


class _UnreadableError(Exception):
    """A stored delegation that could not be read, parsed or addressed (§5.2).

    Internal: `evaluate` turns it into `authority_unreadable`, which is first in §4.3's
    precedence order and therefore denies the action outright. It is never skipped in favour
    of another matching grant — that is how a principal holding both a broad root grant and a
    narrow delegation ends up authorized by the broad one because the narrow one was
    corrupted (§4.6).
    """

    def __init__(self, delegation_id: str, detail: str) -> None:
        super().__init__(f"delegation {delegation_id!r} is unreadable: {detail}")
        self.delegation_id = delegation_id


def new_delegation_id() -> str:
    """`"dlg_" + 32 hex`, the shape of `act_` and `ctr_` (SPEC-v0.3 §5.2)."""
    return f"{DELEGATION_ID_PREFIX}{secrets.token_hex(_ID_HEX_BYTES)}"


def grant_to_json(grant: Grant) -> str:
    """A grant as the `grant_json` column of §5.2.

    A **storage encoding**, deliberately not called canonical: nothing hashes it, and
    `v0.1 §2.3`'s canonical form is a versioned security primitive that a change here must not
    be read as touching. `expires_at` keeps the offset it was written with rather than being
    normalized to UTC, so a grant round-trips to an equal `Grant`.
    """
    document = {
        "subject": {"agent": grant.subject.agent, "user": grant.subject.user},
        "actions": list(grant.actions),
        "resources": None if grant.resources is None else list(grant.resources),
        "constraints": {key: condition.operand for key, condition in grant.constraints.items()},
        "environments": None if grant.environments is None else list(grant.environments),
        "expires_at": None if grant.expires_at is None else grant.expires_at.isoformat(),
        "delegable": grant.delegable,
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def grant_from_json(text: str, *, delegation_id: str) -> Grant:
    """Parse a stored grant, validating it exactly as the YAML loader would (§5.2).

    A store is not more trusted than a file. A delegation whose stored form no longer parses
    is a refusal naming it, never a grant with a field quietly defaulted.
    """
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise _UnreadableError(delegation_id, f"grant_json is not JSON: {exc}") from exc
    if not isinstance(document, Mapping):
        raise _UnreadableError(
            delegation_id, f"grant_json is {_type_name(document)}, not an object"
        )
    subject = document.get("subject")
    if not isinstance(subject, Mapping):
        raise _UnreadableError(delegation_id, "grant_json has no 'subject' object")
    expires_at = document.get("expires_at")
    constraints = document.get("constraints")
    if not isinstance(constraints, Mapping):
        raise _UnreadableError(delegation_id, "grant_json has no 'constraints' object")
    try:
        return Grant(
            id=delegation_id,
            subject=Subject(agent=subject.get("agent"), user=subject.get("user")),
            # A list or nothing, never a coercion: `"actions": "stripe.refund"` would
            # otherwise become thirteen single-character patterns, every one of them
            # grammar-valid. §5.2 requires reading a grant back to validate exactly what
            # loading one from YAML validates, and `_parse_patterns` refuses a non-list.
            actions=_optional_tuple(document.get("actions"), delegation_id) or (),
            resources=_optional_tuple(document.get("resources"), delegation_id),
            constraints=(
                parse_conditions(constraints, where=f"delegation {delegation_id}")
                if constraints
                else NO_CONSTRAINTS
            ),
            environments=_optional_tuple(document.get("environments"), delegation_id),
            expires_at=None if expires_at is None else datetime.fromisoformat(str(expires_at)),
            delegable=bool(document.get("delegable", False)),
        )
    except (InvalidArgument, PolicyError, TypeError, ValueError) as exc:
        raise _UnreadableError(delegation_id, str(exc)) from exc


def _optional_tuple(value: object, delegation_id: str) -> tuple[str, ...] | None:
    """A list of strings, or `None`. Takes the row's id because it raises past
    `grant_from_json`'s `except` clause, and evidence that cannot name the corrupted row is
    evidence an operator cannot act on — §13 ships no way to list delegations."""
    if value is None:
        return None
    if not isinstance(value, list):
        raise _UnreadableError(delegation_id, f"expected a list or null, got {_type_name(value)}")
    return tuple(str(item) for item in value)


def _delegation_from_record(record: DelegationRecord) -> Delegation:
    """Parse a row into a `Delegation`, refusing an id outside the namespace (§5.2)."""
    if not _DELEGATION_ID.match(record.delegation_id):
        # Enforcing the namespace on the way in only would leave the other side open: a
        # hand-inserted row named `head-of-finance` would otherwise shadow the root grant of
        # that name, and quietly disconnect the one lever §5.6 offers against a compromised
        # root grant.
        raise _UnreadableError(record.delegation_id, f"an id must match {_DELEGATION_ID.pattern}")
    via = _CREATED_VIA.get(record.created_via)
    if via is None:
        # §5.2 — the column tells an act from an assertion, so a value outside the two is a
        # record whose provenance cannot be read.
        raise _UnreadableError(record.delegation_id, f"unknown created_via {record.created_via!r}")
    return Delegation(
        delegation_id=record.delegation_id,
        parent_id=record.parent_id,
        depth=record.depth,
        grant=grant_from_json(record.grant_json, delegation_id=record.delegation_id),
        created_by=Principal(agent=record.created_by_agent, user=record.created_by_user),
        created_via=via,
        created_at=record.created_at,
        revoked_at=record.revoked_at,
        revoked_by=record.revoked_by,
    )


def contained_dimension(parent: Grant, child: Grant) -> str | None:
    """The first §5.4 row `child` violates, or `None` where it is contained on every one.

    Checked at creation *and* again at every evaluation (§5.6). **Omission is never
    "unlimited"**: a child that drops a dimension its parent constrains is rejected, not
    treated as inheriting the parent's limit and certainly not as unconstrained. Inheritance
    would be worse than rejection, because a child that silently inherits looks, in the file
    and in the receipt, like a child that was authorized for what it says.
    """
    if not _subject_contained(parent.subject, child.subject):
        return "subject"
    if not _patterns_contained(parent.actions, child.actions, separator=ACTION_SEPARATOR):
        return "actions"
    if parent.resources is not None and (
        child.resources is None
        or not _patterns_contained(parent.resources, child.resources, separator=RESOURCE_SEPARATOR)
    ):
        return "resources"
    if not _constraints_contained(parent.constraints, child.constraints):
        return "constraints"
    if parent.environments is not None and (
        child.environments is None or not set(child.environments) <= set(parent.environments)
    ):
        return "environments"
    if parent.expires_at is not None and (
        child.expires_at is None or child.expires_at > parent.expires_at
    ):
        return "expires_at"
    return None


def _subject_contained(parent: Subject, child: Subject) -> bool:
    """§5.4's `subject` row: two things that look like naming the grantee are widening.

    *Dropping the parent's `user`* turns authority to act **for alice** into authority for the
    agent acting alone. *An unbounded grantee* — `agent: "*"`, or an omitted `agent`, which
    §4.2 makes match any agent — hands the parent's authority to every agent in the
    deployment, and with it the right to delegate onward. Which concrete agent the child names
    is otherwise unconstrained; that is what delegation is for.
    """
    if child.agent is None or "*" in child.agent:
        return False
    if parent.user is not None:
        if child.user is None or "*" in child.user:
            return False
        return matches(parent.user, child.user, separator=None)
    # SPEC: §5.4's row conditions the `user` rule on the parent declaring one, and its second
    # bullet argues only about `agent`. A wildcard `user` under a parent that declares none is
    # the same unbounded grantee spelled on the other field, so the fail-closed reading — the
    # design note's "a delegation's subject may carry no wildcard" — applies to both.
    return child.user is None or "*" not in child.user


def _patterns_contained(
    parent: Sequence[str], child: Sequence[str], *, separator: str | None
) -> bool:
    """Every child pattern is contained in **some** parent pattern (§5.4)."""
    return all(
        any(contains(outer, inner, separator=separator) for outer in parent) for inner in child
    )


def _constraints_contained(parent: Mapping[str, Condition], child: Mapping[str, Condition]) -> bool:
    """For every `(argument, op)` the parent declares, the child declares it at least as strict.

    The child may add constraints the parent does not have. It may **not** discharge the
    parent's by omitting it, nor by constraining a different operator: a child bounding
    `amount_lt` where the parent bounds `amount_lte` may hold that *as well*, but the parent's
    key must be present. Requiring the same operator keeps containment decidable; a solver
    reasoning across operators would be a second policy engine.
    """
    for key, outer in parent.items():
        inner = child.get(key)
        if inner is None or not _operand_at_least_as_strict(outer, inner):
            return False
    return True


def _operand_at_least_as_strict(parent: Condition, child: Condition) -> bool:
    """§5.4's operand table. The key is `f"{argument}_{op}"`, so the ops are already equal."""
    compare = _STRICTER.get(parent.op)
    if compare is not None:
        return bool(compare(child.operand, parent.operand))
    if parent.op == "in":
        return all(
            any(_type_strict_equal(item, other) for other in parent.operand)
            for item in child.operand
        )
    # `eq` and `neq`: equal to the parent's, type-strictly. See §5.4 on why `neq` is not
    # inverted here — `v0.1 §3.2` has no deny-list operator, so "the parent's exclusions plus
    # more" is not expressible and equality is the only rule there is.
    return _type_strict_equal(child.operand, parent.operand)


@dataclass(frozen=True)
class _Walk:
    """What walking a delegation to its root found (SPEC-v0.3 §5.5).

    Depth is **recomputed, never trusted**: the `depth` column is recorded for reading, so a
    row edited directly in the database cannot assert its way to a shorter chain.
    """

    nodes: tuple[Delegation, ...]  # leaf first
    root: Grant | None = None
    root_id: str | None = None
    missing_parent_id: str | None = None
    cycle_at: str | None = None
    depth_exceeded: int | None = None

    @property
    def complete(self) -> bool:
        return self.root is not None

    @property
    def ancestors(self) -> tuple[Grant, ...]:
        """Every grant above the leaf, root first — the root grant and the delegations."""
        above: list[Grant] = [] if self.root is None else [self.root]
        above.extend(node.grant for node in reversed(self.nodes[1:]))
        return tuple(above)

    @property
    def ancestor_ids(self) -> tuple[str, ...]:
        above: list[str] = [] if self.root_id is None else [self.root_id]
        above.extend(node.delegation_id for node in reversed(self.nodes[1:]))
        return tuple(above)

    @property
    def steps(self) -> tuple[tuple[Grant, Grant], ...]:
        """Every parent→child pair, root first, for §5.4's containment re-check."""
        chain = [*self.ancestors, self.nodes[0].grant]
        return tuple(itertools.pairwise(chain))


@dataclass(frozen=True)
class _ChainCheck:
    """The walked depth, and the §5.6 rule that failed — or `None` where none did."""

    depth: int
    failure: AuthorityResult | None


def _by_grant_id(result: AuthorityResult) -> str:
    """§4.3, §4.6 — ties break on simple codepoint order, a property of the set of matching
    grants rather than of the document. Reordering the file changes neither the decision nor
    the id."""
    return result.grant_id or ""


class Authority:
    """The `authority:` section, loaded and evaluable (SPEC-v0.3 §4).

    Pure: `evaluate` reads the store to resolve delegations and writes nothing to it, appends
    no event, and has no other side effect. `Control` performs every write (§4.8).
    """

    def __init__(
        self,
        grants: Mapping[str, Grant],
        *,
        max_delegation_depth: int = DEFAULT_MAX_DELEGATION_DEPTH,
        source: str = "<string>",
    ) -> None:
        if max_delegation_depth < 0:
            raise InvalidArgument("max_delegation_depth must be a non-negative int")
        self._grants = MappingProxyType(dict(grants))
        self._max_delegation_depth = max_delegation_depth
        self._source = source

    @property
    def grants(self) -> Mapping[str, Grant]:
        return self._grants

    @property
    def max_delegation_depth(self) -> int:
        return self._max_delegation_depth

    @property
    def source(self) -> str:
        return self._source

    @classmethod
    def from_yaml(
        cls, text: str, *, source: str = "<string>", standalone: bool = False
    ) -> Authority:
        """Parse an `authority:` section. A document with none is a `PolicyError` (§4.8).

        `standalone=True` is §8.3's `--authority` document, whose top-level key set is closed
        at exactly `schema` and `authority`; `standalone=False` is the combined `ctrlrun.yaml`,
        where the policy loader owns `actions:` and `mode:` and this one ignores them. One
        constructor cannot enforce both, so it is told which.

        Deciding that a document *has* no section is `Control`'s job, not this one's: a loader
        that invented an empty `Authority` would deny every action in a v0.2 configuration
        (§4.1).
        """
        authority = _optional_from_yaml(text, source=source, standalone=standalone)
        if authority is None:
            raise PolicyError(
                f"{source}: no 'authority:' section. A document loaded as authority must carry "
                "one; a section that governs every action must state what it permits"
            )
        return authority

    def evaluate(self, action: Action, *, now: datetime, store: StateStore) -> AuthorityResult:
        """Does any grant cover this action? (SPEC-v0.3 §4.3.)

        Passes iff at least one grant matches subject, action name, resource and environment,
        has all its constraints hold, and is unexpired. Every reason a grant failed for is
        collected rather than short-circuited, and the reported one is the first in §4.3's
        fixed precedence order — so the evidence for one configuration does not depend on the
        order grants appear in the document.

        `store` is required, not optional: §5.6 re-checks a delegation's whole chain on every
        evaluation and that walk reads the store. An optional one would give an implementation
        a fail-open reading in which a revoked delegation evaluates as valid, and "a chain of
        any depth is cut by one write" would stop being true.
        """
        passed: list[AuthorityResult] = []
        failed: dict[str, list[AuthorityResult]] = {}
        try:
            candidates = self._candidates(store)
        except _UnreadableError as unreadable:
            return AuthorityResult(
                False, AUTHORITY_UNREADABLE, delegation_id=unreadable.delegation_id
            )
        for grant_id, grant, delegation in candidates:
            if not grant.matches_shape(action):
                continue
            outcomes: list[AuthorityResult] = []
            depth = 0
            if delegation is not None:
                try:
                    chain = self._check_chain(delegation, store=store, now=now)
                except _UnreadableError as unreadable:
                    return AuthorityResult(
                        False, AUTHORITY_UNREADABLE, delegation_id=unreadable.delegation_id
                    )
                depth = chain.depth
                if chain.failure is not None:
                    outcomes.append(chain.failure)
            if grant.is_expired(now):
                outcomes.append(AuthorityResult(False, AUTHORITY_EXPIRED))
            if not grant.constraints_hold(action):
                outcomes.append(AuthorityResult(False, AUTHORITY_CONSTRAINT))
            named = None if delegation is None else grant_id
            if outcomes:
                # §4.3 — every reason a grant failed for, collected rather than
                # short-circuited, so the reported one is a property of the configuration and
                # not of the order an implementation happened to check things in.
                for outcome in outcomes:
                    failed.setdefault(outcome.reason, []).append(
                        replace(outcome, grant_id=grant_id, delegation_id=named, depth=depth)
                    )
            else:
                passed.append(
                    AuthorityResult(
                        True,
                        AUTHORITY_GRANT,
                        grant_id=grant_id,
                        delegation_id=named,
                        depth=depth,
                    )
                )
        if passed:
            # §4.6 — holding two permissions is never worse than holding one, and the grant
            # named is a property of the set rather than of the document.
            return min(passed, key=_by_grant_id)
        for reason in REASON_PRECEDENCE:
            if reason in failed:
                return min(failed[reason], key=_by_grant_id)
        return AuthorityResult(False, NO_AUTHORITY)

    # --- delegation (SPEC-v0.3 §5) -----------------------------------------------------

    def plan_delegation(
        self, parent_id: str, grant: Grant, *, by: Principal, store: StateStore, now: datetime
    ) -> Delegation:
        """The delegation `Control.delegate` would write, or a refusal (SPEC-v0.3 §5.3).

        Pure: it reads the store, writes nothing, and appends no event. `Control` performs the
        write and the §7 events, which is what keeps `ARCHITECTURE.md` §6's single composer
        single (§4.8).

        The six checks run **in the order §5.3 lists** and the first that fails is the
        refusal's reason — fixed, so the evidence for one attempted creation does not depend on
        the order an implementation happens to check things in.

        `# SPEC:` §5.2 says `Control.delegate` mints the id. §11 freezes this signature without
        one, and a `Delegation` is frozen and complete, so the mint happens here, where the
        record is constructed. `Control` is still the only thing that writes it.
        """
        # Rule 0. Scoped to `Control.execute`, §2.3's expiry check would leave this path open:
        # an agent whose token lapsed five minutes ago, and whose every proposed action is now
        # refused, could still write a permanent, re-delegable grant for an agent of its
        # choosing. A delegation is the most durable thing a principal can create.
        if by.expires_at is not None and now > by.expires_at:
            raise IdentityError(
                f"the delegating principal's credential expired at {by.expires_at}; an expired "
                "credential may not create authority (SPEC-v0.3 §5.3 rule 0)"
            )
        if grant.id:
            raise InvalidArgument(
                f"the grant passed to delegate() carries id {grant.id!r}; a delegation's id is "
                "assigned, not chosen (SPEC-v0.3 §5.2)"
            )
        parent, parent_depth = self._parent_for_creation(parent_id, store=store, now=now)
        # Rule 4. You may only delegate authority you hold; a process that could delegate from
        # a grant addressed to somebody else would make the subject field decorative.
        if not parent.subject.matches(by):
            raise AuthorityEscalation(
                f"{by.agent!r} does not match the subject of {parent_id!r}",
                reason=NOT_THE_SUBJECT,
                parent_id=parent_id,
            )
        depth = parent_depth + 1
        if depth > self._max_delegation_depth:
            raise AuthorityEscalation(
                f"a delegation of {parent_id!r} would be at depth {depth}, beyond "
                f"max_delegation_depth {self._max_delegation_depth}",
                reason=MAX_DEPTH,
                parent_id=parent_id,
            )
        dimension = contained_dimension(parent, grant)
        if dimension is not None:
            raise AuthorityEscalation(
                f"the child grant is not contained in {parent_id!r} on {dimension!r}; "
                "omission never means unlimited (SPEC-v0.3 §5.4)",
                reason=CONTAINMENT,
                parent_id=parent_id,
                dimension=dimension,
            )
        delegation_id = new_delegation_id()
        return Delegation(
            delegation_id=delegation_id,
            parent_id=parent_id,
            depth=depth,
            grant=replace(grant, id=delegation_id),
            created_by=by,
            created_via="api",
            created_at=now,
        )

    def plan_revocation(
        self, delegation_id: str, *, by: str | None, store: StateStore, now: datetime
    ) -> Delegation:
        """The delegation `Control.revoke` would revoke (SPEC-v0.3 §5.7). Reads only."""
        record = store.get_delegation(delegation_id)
        if record is None:
            raise InvalidArgument(f"no delegation {delegation_id}")
        try:
            return _delegation_from_record(record)
        except _UnreadableError as unreadable:
            raise InvalidArgument(str(unreadable)) from unreadable

    def _parent_for_creation(
        self, parent_id: str, *, store: StateStore, now: datetime
    ) -> tuple[Grant, int]:
        """§5.3 rules 1 to 3, and the walked depth rule 5 needs."""
        # §5.2 — a root grant wins any collision: an id is resolved against the document first
        # and the store second.
        root = self._grants.get(parent_id)
        if root is not None:
            if not root.delegable:
                raise AuthorityEscalation(
                    f"{parent_id!r} is not delegable",
                    reason=PARENT_NOT_DELEGABLE,
                    parent_id=parent_id,
                )
            if root.is_expired(now):
                raise AuthorityEscalation(
                    f"{parent_id!r} expired at {root.expires_at}",
                    reason=PARENT_NOT_VALID,
                    parent_id=parent_id,
                )
            return root, 0
        record = store.get_delegation(parent_id)
        if record is None or record.is_revoked:
            # Rule 1 — "a root grant or a *live* delegation". A revoked one is neither, and
            # saying so here rather than in rule 3 keeps rule 3 about the *chain above* it.
            raise AuthorityEscalation(
                f"no live grant or delegation {parent_id!r}",
                reason=UNKNOWN_PARENT,
                parent_id=parent_id,
            )
        try:
            parent = _delegation_from_record(record)
        except _UnreadableError as unreadable:
            raise AuthorityEscalation(
                str(unreadable), reason=PARENT_NOT_VALID, parent_id=parent_id
            ) from unreadable
        if not parent.grant.delegable:
            raise AuthorityEscalation(
                f"{parent_id!r} is not delegable",
                reason=PARENT_NOT_DELEGABLE,
                parent_id=parent_id,
            )
        if parent.grant.is_expired(now):
            raise AuthorityEscalation(
                f"{parent_id!r} expired at {parent.grant.expires_at}",
                reason=PARENT_NOT_VALID,
                parent_id=parent_id,
            )
        try:
            walk = self._walk(parent, store=store)
        except _UnreadableError as unreadable:
            raise AuthorityEscalation(
                str(unreadable), reason=PARENT_NOT_VALID, parent_id=parent_id
            ) from unreadable
        # Rule 3 covers `delegable` and revocation across the whole chain, not just the
        # immediate parent, so an operator who sets `delegable: false` on a root grant stops
        # new delegations appearing anywhere beneath it rather than only one level down.
        invalid = walk.missing_parent_id is not None or walk.cycle_at is not None
        if not invalid:
            invalid = any(node.is_revoked for node in walk.nodes[1:]) or any(
                ancestor.is_expired(now) or not ancestor.delegable for ancestor in walk.ancestors
            )
        if invalid:
            raise AuthorityEscalation(
                f"the chain above {parent_id!r} is no longer valid",
                reason=PARENT_NOT_VALID,
                parent_id=parent_id,
            )
        if walk.depth_exceeded is not None:
            return parent.grant, walk.depth_exceeded
        return parent.grant, len(walk.nodes)

    def _candidates(self, store: StateStore) -> list[tuple[str, Grant, Delegation | None]]:
        """Every grant this principal could hold: the document's, then the store's (§4.3)."""
        found: list[tuple[str, Grant, Delegation | None]] = [
            (grant_id, grant, None) for grant_id, grant in self._grants.items()
        ]
        for record in store.delegations(include_revoked=True):
            delegation = _delegation_from_record(record)
            found.append((delegation.delegation_id, delegation.grant, delegation))
        return found

    def _walk(self, leaf: Delegation, *, store: StateStore) -> _Walk:
        """Walk a delegation to its root, bounded and cycle-checked (SPEC-v0.3 §5.5).

        Bounded at `max_delegation_depth + 1` steps and refusing a chain that revisits an id.
        A cycle is not reachable through `Control.delegate` — a parent exists before its child
        — but it is reachable with `sqlite3` and a text editor.
        """
        nodes = [leaf]
        seen = {leaf.delegation_id}
        while True:
            parent_id = nodes[-1].parent_id
            root = self._grants.get(parent_id)
            if root is not None:
                return _Walk(tuple(nodes), root=root, root_id=parent_id)
            if parent_id in seen:
                return _Walk(tuple(nodes), cycle_at=parent_id)
            record = store.get_delegation(parent_id)
            if record is None:
                return _Walk(tuple(nodes), missing_parent_id=parent_id)
            if len(nodes) >= self._max_delegation_depth:
                # One more node would put the chain past the bound, and the bound is what
                # stops an edited store hanging the process.
                return _Walk(tuple(nodes), depth_exceeded=len(nodes) + 1)
            seen.add(parent_id)
            nodes.append(_delegation_from_record(record))

    def _check_chain(self, leaf: Delegation, *, store: StateStore, now: datetime) -> _ChainCheck:
        """§5.6's six rules, evaluated 1 → 6, the first failure naming the refusal.

        Fixed order, because rules 1, 3, 4, 5 and 6 all yield `authority_escalation` while
        carrying different `data` — so a chain that is both orphaned above and non-contained
        below would otherwise record implementation-defined evidence for one configuration.

        Re-checking is not belt-and-braces. It is what makes revocation transitive, what makes
        an expiring parent stop authorizing without anyone finding its children, and what makes
        a narrowed root grant narrow everything beneath it.
        """
        walk = self._walk(leaf, store=store)
        depth = walk.depth_exceeded if walk.depth_exceeded is not None else len(walk.nodes)
        if walk.missing_parent_id is not None:
            return _ChainCheck(
                depth,
                AuthorityResult(
                    False, AUTHORITY_ESCALATION, missing_parent_id=walk.missing_parent_id
                ),
            )
        if any(node.is_revoked for node in walk.nodes):
            return _ChainCheck(depth, AuthorityResult(False, AUTHORITY_REVOKED))
        for ancestor_id, ancestor in zip(walk.ancestor_ids, walk.ancestors, strict=True):
            if ancestor.is_expired(now):
                # Attribution rather than prevention (§9): §5.4's `expires_at` row already
                # makes the leaf expired, so §4.3 would refuse it either way. What this adds is
                # the name of the ancestor that lapsed, which is the thing an operator fixes.
                return _ChainCheck(
                    depth,
                    AuthorityResult(False, AUTHORITY_ESCALATION, expired_parent_id=ancestor_id),
                )
        for parent, child in walk.steps:
            dimension = contained_dimension(parent, child)
            if dimension is not None:
                return _ChainCheck(
                    depth, AuthorityResult(False, AUTHORITY_ESCALATION, dimension=dimension)
                )
        if walk.cycle_at is not None:
            # A chain that loops is a store somebody has edited by hand, and it belongs with
            # the other unreadable-record cases rather than with the ordinary escalations.
            return _ChainCheck(
                depth, AuthorityResult(False, AUTHORITY_UNREADABLE, cycle_at=walk.cycle_at)
            )
        if depth > self._max_delegation_depth:
            # Compared here rather than inferred from `_walk` truncating. The walk returns as
            # soon as it reaches a root grant, *before* it consults the bound, so a chain that
            # completes in one step is never measured against anything — and
            # `max_delegation_depth: 0`, which §5.5 offers as the legible way to switch
            # delegation off, would leave every existing depth-1 delegation authorizing while
            # appearing to work, because deeper chains truncate and deny. A guard that is a
            # side effect of a bound is not a guard.
            return _ChainCheck(
                depth, AuthorityResult(False, AUTHORITY_ESCALATION, depth_exceeded=depth)
            )
        if any(not ancestor.delegable for ancestor in walk.ancestors):
            # Rule 6 exists because `delegable` is not a §5.4 row and rule 4 would therefore
            # never see it. An operator setting `delegable: false` on a root grant is shutting
            # down a chain they believe is compromised.
            return _ChainCheck(depth, AuthorityResult(False, AUTHORITY_ESCALATION))
        return _ChainCheck(depth, None)


# --- loading (SPEC-v0.3 §4.1, §4.2) ----------------------------------------------------


def _optional_from_yaml(
    text: str, *, source: str = "<string>", standalone: bool = False
) -> Authority | None:
    """The `authority:` section of this document, or `None` where it has none.

    `# SPEC:` §11 freezes `Authority.from_yaml`, which answers a document that *has* a section.
    "No section at all" is a different answer and the one §4.1's opt-in rule turns on, so the
    reader that can give it is private to the package: `Control.from_file` calls it, and a
    public name for it would be an addition to a frozen surface.
    """
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PolicyError(f"{source}: not valid YAML: {exc}") from exc
    if not isinstance(document, Mapping):
        raise PolicyError(
            f"{source}: an authority document must be a mapping, got {_type_name(document)}"
        )
    schema = document.get("schema")
    if schema not in SUPPORTED_SCHEMAS:
        raise PolicyError(
            f"{source}: unknown policy schema {schema!r}, expected one of "
            f"{', '.join(repr(known) for known in SUPPORTED_SCHEMAS)}"
        )
    if standalone:
        for owned in sorted(_POLICY_OWNED_KEYS - _STANDALONE_KEYS):
            if owned in document:
                raise PolicyError(
                    f"{source}: an authority document carries 'schema' and 'authority' and "
                    f"nothing else; {owned!r} belongs in the policy file. Two sources for one "
                    "key is an ambiguity nobody can resolve later (SPEC-v0.3 §8.3)"
                )
        _reject_unknown_keys(document, _STANDALONE_KEYS, f"{source}: top level")
    if _AUTHORITY_KEY not in document:
        return None
    # §12.1 — the schema string is the only thing standing between a reader that enforces
    # authority and one that ignores the section and runs every action unchecked. Checked here
    # as well as in `Policy`, because §8.3's `--authority` document is never read by the
    # policy loader at all.
    require_v3(document, str(schema), source)
    return _from_section(document[_AUTHORITY_KEY], source)


def _from_section(section: object, source: str) -> Authority:
    where = f"{source}: authority"
    if not isinstance(section, Mapping):
        raise PolicyError(
            f"{where}: must be a mapping with a 'grants' key, got {_type_name(section)}"
        )
    _reject_unknown_keys(section, _AUTHORITY_KEYS, where)
    depth = section.get("max_delegation_depth", DEFAULT_MAX_DELEGATION_DEPTH)
    if not isinstance(depth, int) or isinstance(depth, bool) or depth < 0:
        raise PolicyError(
            f"{where}: 'max_delegation_depth' must be a non-negative int, got {depth!r}"
        )
    entries = section.get("grants")
    if not isinstance(entries, list):
        # §4.1 — inferring "nothing" from a missing key would make a truncated edit look
        # deliberate. An empty list is valid and denies every action; an absent one is not.
        raise PolicyError(
            f"{where}: 'grants' must be a list, got {_type_name(entries)}. A section that "
            "governs every action must state what it permits, so 'grants' is required — write "
            "'grants: []' to permit nothing"
        )
    grants: dict[str, Grant] = {}
    for index, entry in enumerate(entries):
        grant = _parse_grant(entry, f"{where} grants[{index}]")
        if grant.id in grants:
            raise PolicyError(
                f"{where} grants[{index}]: duplicate grant id {grant.id!r}; a delegation names "
                "its parent by id, and two grants answering to one name is an ambiguity nobody "
                "can resolve later"
            )
        grants[grant.id] = grant
    return Authority(grants, max_delegation_depth=depth, source=source)


def grant_from_yaml(text: str, *, source: str = "<string>") -> Grant:
    """One grant with the keys of §4.2 **minus `id`** — `ctrlrun delegate --file` (§5.7).

    An `id:` key is a `PolicyError` naming the rule: a delegation's id is assigned, not
    chosen (§5.2). The returned grant carries `id=""`, which is legal only on the
    `Control.delegate` path and only until the call returns.
    """
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PolicyError(f"{source}: not valid YAML: {exc}") from exc
    if not isinstance(document, Mapping):
        raise PolicyError(
            f"{source}: a delegated grant must be a mapping of the keys of SPEC-v0.3 §4.2 "
            f"minus 'id', got {_type_name(document)}"
        )
    if "id" in document:
        raise PolicyError(
            f"{source}: a delegated grant may not carry 'id'; the id is assigned, not chosen "
            "(SPEC-v0.3 §5.2)"
        )
    return _parse_grant({**document, "id": _UNASSIGNED}, source, unassigned=True)


#: A placeholder `id` for the `--file` path: `_parse_grant` requires a non-empty string, and
#: `Grant` accepts `""` only on the `Control.delegate` path. Never stored, never compared.
_UNASSIGNED: Final = "unassigned"


def _parse_grant(entry: object, where: str, *, unassigned: bool = False) -> Grant:
    if not isinstance(entry, Mapping):
        raise PolicyError(f"{where}: a grant must be a mapping, got {_type_name(entry)}")
    _reject_unknown_keys(entry, _GRANT_KEYS, where)
    grant_id = entry.get("id")
    if not isinstance(grant_id, str) or not grant_id:
        raise PolicyError(f"{where}: 'id' must be a non-empty string, got {grant_id!r}")
    if grant_id.startswith(DELEGATION_ID_PREFIX):
        raise PolicyError(
            f"{where}: a grant id may not begin {DELEGATION_ID_PREFIX!r}; that namespace is "
            "minted for delegations (SPEC-v0.3 §5.2), and one namespace addresses both kinds"
        )
    delegable = entry.get("delegable", False)
    if not isinstance(delegable, bool):
        raise PolicyError(f"{where}: 'delegable' must be true or false, got {delegable!r}")

    try:
        return Grant(
            id="" if unassigned else grant_id,
            subject=_parse_subject(entry.get("subject"), where),
            actions=_parse_patterns(entry.get("actions"), "actions", where),
            resources=(
                _parse_patterns(entry["resources"], "resources", where)
                if "resources" in entry
                else None
            ),
            constraints=_parse_constraints(entry, where),
            environments=(
                _parse_environments(entry["environments"], where)
                if "environments" in entry
                else None
            ),
            expires_at=_parse_expires_at(entry, where),
            delegable=delegable,
        )
    except InvalidArgument as exc:
        # The model refuses what the loader refuses (§4.8), so the loader delegates the
        # grammar to it rather than keeping a second copy that can drift.
        raise PolicyError(f"{where}: {exc}") from exc


def _parse_subject(value: object, where: str) -> Subject:
    if not isinstance(value, Mapping):
        raise PolicyError(
            f"{where}: 'subject' must be a mapping with 'agent' and/or 'user', "
            f"got {_type_name(value)}"
        )
    _reject_unknown_keys(value, _SUBJECT_KEYS, f"{where}: subject")
    for key in sorted(_SUBJECT_KEYS):
        if key in value and not isinstance(value[key], str):
            raise PolicyError(
                f"{where}: subject: {key!r} must be a pattern string, got {_type_name(value[key])}"
            )
    try:
        return Subject(agent=value.get("agent"), user=value.get("user"))
    except InvalidArgument as exc:
        raise PolicyError(f"{where}: subject: {exc}") from exc


def _parse_patterns(value: object, key: str, where: str) -> tuple[str, ...]:
    """A present key must be a list. An *absent* one is what §4.2 makes optional — a key
    written with no value is a truncated edit, and reading it as "any resource" would widen
    the grant in the direction §4.1 exists to refuse."""
    if not isinstance(value, list):
        raise PolicyError(
            f"{where}: {key!r} must be a non-empty list of patterns, got {_type_name(value)}"
        )
    for pattern in value:
        if not isinstance(pattern, str):
            raise PolicyError(f"{where}: {key!r}: a pattern must be a string, got {pattern!r}")
    return tuple(value)


def _parse_environments(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise PolicyError(
            f"{where}: 'environments' must be a non-empty list of names, got {_type_name(value)}"
        )
    return tuple(value)


def _parse_constraints(entry: Mapping[Any, Any], where: str) -> Mapping[str, Condition]:
    if "constraints" not in entry:
        return NO_CONSTRAINTS
    value = entry["constraints"]
    # §4.5 — as `when: {}` is. An empty mapping is a truncated edit, and "no constraints" is
    # already spelled by leaving the key out.
    if not isinstance(value, Mapping) or not value:
        raise PolicyError(
            f"{where}: 'constraints' must be a non-empty mapping of conditions, or absent, "
            f"got {_type_name(value)}"
        )
    return parse_conditions(value, where=f"{where}: constraints")


def _parse_expires_at(entry: Mapping[Any, Any], where: str) -> datetime | None:
    if "expires_at" not in entry:
        return None
    value = entry["expires_at"]
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as exc:
            raise PolicyError(
                f"{where}: 'expires_at' must be an ISO-8601 timestamp with an offset, got {value!r}"
            ) from exc
    if not isinstance(value, datetime):
        raise PolicyError(
            f"{where}: 'expires_at' must be an ISO-8601 timestamp with an offset, "
            f"got {_type_name(value)}"
        )
    # A naive timestamp is refused by `Grant.__post_init__`, not here. A second check would be
    # a subsumed guard — it can only fire where the model's would also fire, with the same
    # observable result — and the loader already wraps the model's `InvalidArgument` in a
    # `PolicyError` naming this location. What is *not* subsumed is the isinstance check
    # above: without it the model would be handed a `str` and raise `AttributeError`.
    return value


def _reject_unknown_keys(mapping: Mapping[Any, Any], allowed: Iterable[str], where: str) -> None:
    """Key sets are closed at every level (§4.2), so `action:` for `actions:` fails loudly."""
    known = set(allowed)
    unknown = sorted(repr(key) for key in mapping if key not in known)
    if unknown:
        raise PolicyError(
            f"{where}: unknown key(s) {', '.join(unknown)}; allowed: {', '.join(sorted(known))}"
        )


__all__ = [
    "ACTION_SEPARATOR",
    "AUTHORITY_CONSTRAINT",
    "AUTHORITY_ESCALATION",
    "AUTHORITY_EXPIRED",
    "AUTHORITY_GRANT",
    "AUTHORITY_REVOKED",
    "AUTHORITY_UNREADABLE",
    "CONTAINMENT",
    "DEFAULT_MAX_DELEGATION_DEPTH",
    "DELEGATION_ID_PREFIX",
    "DIMENSIONS",
    "MAX_DEPTH",
    "NOT_THE_SUBJECT",
    "NO_AUTHORITY",
    "PARENT_NOT_DELEGABLE",
    "PARENT_NOT_VALID",
    "RESOURCE_SEPARATOR",
    "UNKNOWN_PARENT",
    "Authority",
    "AuthorityResult",
    "Delegation",
    "Grant",
    "Subject",
    "contained_dimension",
    "contains",
    "grant_from_json",
    "grant_from_yaml",
    "grant_to_json",
    "matches",
    "new_delegation_id",
    "validate_pattern",
]
