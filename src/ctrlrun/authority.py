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

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Final

import yaml

from .action import Action, Principal
from .errors import InvalidArgument, PolicyError
from .policy import SUPPORTED_SCHEMAS, Condition, parse_conditions, require_v3
from .state import StateStore

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
class AuthorityResult:
    """What the authority axis decided, and which grant it decided on (§4.8)."""

    passed: bool
    reason: str
    grant_id: str | None = None
    delegation_id: str | None = None
    depth: int = 0


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

        `store` is required and unused while every grant is a root grant: build-list item 3
        walks a delegation's chain through it on every evaluation, and an optional store would
        give an implementation a fail-open reading in which a revoked delegation stays valid.
        """
        passed: list[str] = []
        failed: dict[str, list[str]] = {}
        for grant_id, grant in self._grants.items():
            if not grant.matches_shape(action):
                continue
            reasons: list[str] = []
            if grant.is_expired(now):
                reasons.append(AUTHORITY_EXPIRED)
            if not grant.constraints_hold(action):
                reasons.append(AUTHORITY_CONSTRAINT)
            if reasons:
                for reason in reasons:
                    failed.setdefault(reason, []).append(grant_id)
            else:
                passed.append(grant_id)
        if passed:
            # §4.6 — holding two permissions is never worse than holding one, and the grant
            # named is a property of the set rather than of the document.
            return AuthorityResult(True, AUTHORITY_GRANT, grant_id=min(passed))
        for reason in REASON_PRECEDENCE:
            if reason in failed:
                return AuthorityResult(False, reason, grant_id=min(failed[reason]))
        return AuthorityResult(False, NO_AUTHORITY)


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


def _parse_grant(entry: object, where: str) -> Grant:
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
            id=grant_id,
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
    "DEFAULT_MAX_DELEGATION_DEPTH",
    "DELEGATION_ID_PREFIX",
    "NO_AUTHORITY",
    "RESOURCE_SEPARATOR",
    "Authority",
    "AuthorityResult",
    "Grant",
    "Subject",
    "contains",
    "matches",
    "validate_pattern",
]
