"""Effect keys and effect records. Build-list items 5 and 6; SPEC-v0.1 §5.

The effect key is what duplicate protection is built on (§5.3): two attempts that resolve to
the same key are the same real-world effect, whatever their `action_id`s are. Resolution is
therefore strict — an unresolvable key is never a silent `None`, and a placeholder that
resolves to nothing identifiable is refused rather than rendered.

`plan_reservation` is the retry table of §5.4 as one pure function. Both StateStores decide
with it and then only write, so the rule that refuses a duplicate lives in exactly one place
and the in-memory store cannot drift into permitting what SQLite refuses.

The transitions of §5.2 are in `state.py`, where the records live. `ctrlrun resolve` — the
only way out of `AMBIGUOUS`, because it is the only one a human drives — arrives with item 8.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Final

from .action import Action
from .errors import (
    AmbiguousEffect,
    CTRLRunError,
    DuplicateEffect,
    EffectKeyError,
    InvalidArgument,
)

#: The decision reason recorded when an effect template cannot be resolved (SPEC §5.1, §6.1).
UNRESOLVED_EFFECT: Final = "effect_key_error"

#: SPEC-v0.1 §5.3 E3 — a reservation is held for five minutes unless told otherwise.
DEFAULT_LEASE: Final = timedelta(minutes=5)

#: `DuplicateEffect.state` values (SPEC-v0.1 §5.4).
COMMITTED_EFFECT: Final = "committed"
IN_PROGRESS_EFFECT: Final = "in_progress"

#: Recorded on a record whose holder disappeared with its lease still held (§5.3 E3).
LEASE_EXPIRED: Final = "lease expired: the worker holding this effect never finished"

#: In an effect template, `{resource}` names the action's `resource` field (SPEC §5.1).
RESOURCE_PLACEHOLDER: Final = "resource"

#: A template is literal text and `{name}` placeholders. Anything else is a typo. `name` is
#: an identifier — a letter or underscore, then letters, digits or underscores — because it
#: names an argument, and an argument name is a Python parameter name.
_TOKEN: Final = re.compile(r"\{(?P<name>[^\W\d]\w*)\}|(?P<text>[^{}]+)|(?P<bad>.)")


class EffectState(StrEnum):
    """Where a logical effect stands (SPEC-v0.1 §5.2).

    `NEW` is the state of a key nobody has reserved: it is never written to a store, which
    reports it as no record at all. `AMBIGUOUS` never collapses to `FAILED`; only a human
    moves a record out of it.
    """

    NEW = "new"
    RESERVED = "reserved"
    EXECUTING = "executing"
    COMMITTED = "committed"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class EffectRecord:
    """What a StateStore holds for one effect key (ARCHITECTURE §5)."""

    effect_key: str
    state: EffectState
    action_id: str
    attempt: int
    created_at: datetime
    updated_at: datetime
    lease_expires_at: datetime | None = None
    result: Any = None
    error: str | None = None

    def lease_is_live(self, now: datetime) -> bool:
        """Whether an attempt is still holding this effect (SPEC-v0.1 §5.3 E3).

        A `RESERVED` or `EXECUTING` record with no lease at all counts as expired: the
        conservative reading, since an expired lease is refused more firmly than a live one.
        """
        if self.state not in (EffectState.RESERVED, EffectState.EXECUTING):
            return False
        return self.lease_expires_at is not None and now <= self.lease_expires_at


@dataclass(frozen=True)
class Reservation:
    """The right to execute one effect once, until `lease_expires_at` (SPEC-v0.1 §5.3)."""

    effect_key: str
    action_id: str
    attempt: int
    lease_expires_at: datetime


@dataclass(frozen=True)
class ReservationPlan:
    """What a store must do about one reservation attempt (SPEC-v0.1 §5.4).

    Exactly one of `reservation` and `refusal` is set. `renews` means an existing `FAILED`
    record is being retried, so the store updates rather than inserts — an insert that hits
    the `UNIQUE(effect_key)` constraint is then a real violation, not an expected one.
    `ambiguate` means the existing record must first be moved to `AMBIGUOUS`, and that write
    kept even though the attempt is refused: an expired lease is evidence (§5.3 E3).
    """

    reservation: Reservation | None = None
    refusal: CTRLRunError | None = None
    renews: bool = False
    ambiguate: bool = False


def plan_reservation(
    record: EffectRecord | None,
    effect_key: str,
    action_id: str,
    lease: timedelta,
    now: datetime,
) -> ReservationPlan:
    """Apply the retry table of SPEC-v0.1 §5.4 to one reservation attempt.

    Pure: it reads a record and returns what to do. Every store decides here, so `FAILED` is
    the only state that lets a second attempt through, in one place rather than in each.
    """
    if not effect_key:
        raise InvalidArgument("effect_key must be a non-empty string")
    if not action_id:
        raise InvalidArgument("action_id must be a non-empty string")
    if lease <= timedelta(0):
        raise InvalidArgument(f"a reservation lease must be positive, got {lease!r}")

    granted = Reservation(
        effect_key=effect_key,
        action_id=action_id,
        attempt=1,
        lease_expires_at=now + lease,
    )
    if record is None or record.state is EffectState.NEW:
        return ReservationPlan(reservation=granted)
    if record.state is EffectState.COMMITTED:
        return ReservationPlan(
            refusal=DuplicateEffect(
                f"effect {effect_key!r} was already committed by {record.action_id}",
                state=COMMITTED_EFFECT,
                effect_key=effect_key,
            )
        )
    if record.state is EffectState.AMBIGUOUS:
        return ReservationPlan(
            refusal=AmbiguousEffect(
                f"effect {effect_key!r} has an unknown outcome from {record.action_id}; "
                f"resolve it with 'ctrlrun resolve {effect_key}' before retrying",
                effect_key=effect_key,
                action_id=record.action_id,
            )
        )
    if record.state is EffectState.FAILED:
        # SPEC §5.4 — the only automatic retry: the executor proved nothing happened (§5.5).
        return ReservationPlan(
            reservation=replace(granted, attempt=record.attempt + 1), renews=True
        )
    if record.lease_is_live(now):
        return ReservationPlan(
            refusal=DuplicateEffect(
                f"effect {effect_key!r} is {record.state} under {record.action_id}",
                state=IN_PROGRESS_EFFECT,
                effect_key=effect_key,
            )
        )
    # SPEC §5.3 E3 — the lease expired mid-flight. The remote may have committed, so the
    # record becomes AMBIGUOUS; it is never silently released to the next caller.
    return ReservationPlan(
        refusal=AmbiguousEffect(
            f"effect {effect_key!r} was left {record.state} by {record.action_id} and its "
            f"lease expired; resolve it with 'ctrlrun resolve {effect_key}'",
            effect_key=effect_key,
            action_id=record.action_id,
        ),
        ambiguate=True,
    )


def template_placeholders(template: str) -> tuple[str, ...]:
    """Return the placeholder names in `template`, in order. Malformed → `InvalidArgument`.

    The grammar is deliberately smaller than `str.format`: no `{{` escapes, no format specs,
    no attribute or index access. An effect key is an identity, not a formatted string, so a
    brace that is not part of a `{name}` placeholder is a typo — and a typo must not become
    part of an effect identity.
    """
    if not template:
        raise InvalidArgument("a template must be a non-empty string")
    names: list[str] = []
    for token in _TOKEN.finditer(template):
        if token.group("bad") is not None:
            raise InvalidArgument(
                f"{template!r} is not a valid template: expected literal text and "
                f"'{{name}}' placeholders, found {token.group('bad')!r} at position "
                f"{token.start()}"
            )
        name = token.group("name")
        if name is not None:
            names.append(name)
    return tuple(names)


def resolve_effect_key(template: str, action: Action) -> str:
    """Resolve an effect template against a constructed action (SPEC-v0.1 §5.1).

    Placeholders name the action's arguments; `{resource}` names its `resource` field. A
    placeholder with no value raises `EffectKeyError`, and the action is refused: an action
    whose logical effect cannot be identified cannot be protected against duplication.
    """
    names = template_placeholders(template)
    values: dict[str, Any] = action.canonical_arguments
    if RESOURCE_PLACEHOLDER in names:
        if RESOURCE_PLACEHOLDER in values:
            # SPEC: §5.1 — the spec gives `{resource}` to the resource field but does not say
            # what an argument of the same name does. Two candidate values for one key is the
            # fail-closed case: refuse, rather than silently pick the one the author did not
            # mean, because duplicate protection depends on which one it is.
            raise EffectKeyError(
                f"{template!r}: '{{resource}}' is ambiguous — {action.name} has both a "
                "resource field and an argument named 'resource'; rename the argument"
            )
        if action.resource is None:
            raise EffectKeyError(
                f"{template!r}: '{{resource}}' needs a resource on the action, and "
                f"{action.name} has none"
            )
        values[RESOURCE_PLACEHOLDER] = action.resource
    return _render(template, values, EffectKeyError)


def resolve_resource(template: str, arguments: Mapping[str, Any]) -> str:
    """Resolve a `resource=` template against the bound call arguments (SPEC-v0.1 §5.1).

    Same syntax and same resolver as an effect template, but resolved *before* the Action
    exists: `resource` is part of the canonical form and therefore of the action hash (§2.2),
    so a missing placeholder is an `InvalidArgument` at construction time, as in §2.
    """
    return _render(template, arguments, InvalidArgument)


def _render(template: str, values: Mapping[str, Any], error: type[CTRLRunError]) -> str:
    """Substitute `values` into `template`, raising `error` for anything unresolvable."""
    parts: list[str] = []
    for token in _TOKEN.finditer(template):
        text = token.group("text")
        if text is not None:
            parts.append(text)
            continue
        name = token.group("name")
        if name is None:
            raise InvalidArgument(
                f"{template!r} is not a valid template: found {token.group('bad')!r} at "
                f"position {token.start()}"
            )
        if name not in values:
            raise error(
                f"{template!r}: no value for '{{{name}}}' (have: "
                f"{', '.join(sorted(values)) or 'nothing'})"
            )
        parts.append(_rendered(name, values[name], template, error))
    return "".join(parts)


def _rendered(name: str, value: object, template: str, error: type[CTRLRunError]) -> str:
    """Render one placeholder value, or raise: only a non-empty `str` or an `int` will do.

    SPEC: §5.1 — the spec does not restrict placeholder types. This is the fail-closed
    reading: `None` and `""` identify nothing and would collide across unrelated actions,
    `bool` identifies nothing either, and a container has no stable rendering. An effect key
    must be an identity a human can read and two attempts can agree on.
    """
    if isinstance(value, str):
        if not value:
            raise error(f"{template!r}: '{{{name}}}' is empty; an effect key must identify")
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    raise error(
        f"{template!r}: '{{{name}}}' is {type(value).__name__}; a placeholder must resolve "
        "to a non-empty string or an int"
    )
