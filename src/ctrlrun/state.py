"""StateStore protocol, SQLite and in-memory stores. Build-list item 6; SPEC-v0.1 §5.3.

`SQLiteStateStore` is the store for anything that matters: it holds approvals, effects and
evidence in one file, and it is where E1 lives — `reserve_effect` succeeds for at most one
caller per effect key, across threads *and processes*. That is `BEGIN IMMEDIATE` plus a
`UNIQUE(effect_key)` constraint, with `busy_timeout` making contenders wait instead of fail.

Both stores decide with the same two pure functions — `plan_reservation` (§5.4) and
`check_consumable` (§4.2) — and then only write. The rules therefore live in one place, and
`InMemoryStateStore` cannot drift into permitting something SQLite refuses.

Approval consumption and effect reservation happen in one transaction (§4.2 A4): nothing is
written until both have been decided, so a refused reservation leaves the approval granted.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import sqlite3
import threading
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Protocol, TypeVar

from .action import Action, Principal
from .approval import (
    Approval,
    ApprovalRecord,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalStore,
    check_answerable,
    check_consumable,
)
from .effect import (
    COMMITTED_EFFECT,
    DEFAULT_LEASE,
    IN_PROGRESS_EFFECT,
    LEASE_EXPIRED,
    EffectRecord,
    EffectState,
    Reservation,
    ReservationPlan,
    plan_lease_extension,
    plan_reservation,
)
from .errors import AmbiguousEffect, DuplicateEffect, InvalidArgument
from .receipt import Event, EventType, Receipt

_LOG = logging.getLogger(__name__)

#: SPEC-v0.1 §5.3 E1 — a contender waits this long for the write lock before giving up.
BUSY_TIMEOUT_MS: Final = 5000

_RESERVED: Final = frozenset({EffectState.RESERVED})
_EXECUTING: Final = frozenset({EffectState.EXECUTING})
#: `mark_ambiguous` accepts a reservation that never began (a crash between the two) and is
#: idempotent, so recording an unknown outcome can never itself fail (SPEC §5.5).
_UNFINISHED: Final = frozenset({EffectState.RESERVED, EffectState.EXECUTING, EffectState.AMBIGUOUS})
#: SPEC-v0.1 §5.2 — `AMBIGUOUS` is the only state a human resolves, and these are the two
#: answers available: what actually happened at the remote was one or the other.
RESOLUTIONS: Final = frozenset({EffectState.COMMITTED, EffectState.FAILED})

_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS effects(
  effect_key TEXT PRIMARY KEY,
  state TEXT NOT NULL,
  action_id TEXT NOT NULL,
  attempt INTEGER NOT NULL DEFAULT 1,
  lease_expires_at TEXT,
  result_json TEXT,
  error TEXT,
  created_at TEXT,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS continuations(
  effect_key TEXT PRIMARY KEY,
  action_id TEXT NOT NULL,
  action_hash TEXT NOT NULL,
  request_state TEXT NOT NULL,
  rounds INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS approvals(
  approval_id TEXT PRIMARY KEY,
  action_hash TEXT NOT NULL,
  status TEXT NOT NULL,
  action_json TEXT NOT NULL,
  approver TEXT,
  created_at TEXT,
  granted_at TEXT,
  expires_at TEXT,
  consumed_at TEXT
);
CREATE TABLE IF NOT EXISTS receipts(
  receipt_id TEXT PRIMARY KEY,
  action_id TEXT,
  effect_key TEXT,
  result TEXT,
  json TEXT,
  ts TEXT
);
CREATE TABLE IF NOT EXISTS events(
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT,
  type TEXT,
  action_id TEXT,
  effect_key TEXT,
  approval_id TEXT,
  data_json TEXT
);
"""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    """A stored timestamp: UTC ISO-8601 at full precision, so it round-trips exactly."""
    return moment.astimezone(UTC).isoformat()


def _at(text: str | None) -> datetime | None:
    return None if text is None else datetime.fromisoformat(text)


def _result_json(result: Any) -> str | None:
    """Serialize an executor's return value. Never raises: the effect *did* commit.

    An unserializable result is stored as its `repr`. Losing the shape of a return value is
    a cosmetic loss; failing here would turn a committed effect into an error.
    """
    if result is None:
        return None
    try:
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=repr)
    except (TypeError, ValueError, RecursionError):
        return json.dumps({"repr": repr(result)}, ensure_ascii=False, separators=(",", ":"))


def _result_value(text: str | None) -> Any:
    return None if text is None else json.loads(text)


def _action_json(action: Action) -> str:
    """An Action as stored JSON. Round-trips to the same `action_hash` (SPEC-v0.1 §2.2)."""
    return json.dumps(
        {
            "action_id": action.action_id,
            "name": action.name,
            "arguments": action.canonical_arguments,
            "principal": {"agent": action.principal.agent, "user": action.principal.user},
            "resource": action.resource,
            "environment": action.environment,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _action_from_json(text: str) -> Action:
    document = json.loads(text)
    principal = document["principal"]
    return Action(
        name=document["name"],
        arguments=document["arguments"],
        principal=Principal(agent=principal["agent"], user=principal["user"]),
        resource=document["resource"],
        environment=document["environment"],
        action_id=document["action_id"],
    )


def _checked(
    record: EffectRecord | None,
    effect_key: str,
    action_id: str,
    expected: frozenset[EffectState],
    now: datetime,
) -> EffectRecord:
    """The record, if this attempt may make this transition. Otherwise refuse, fail-closed.

    A record that moved on belongs to something else now: `AMBIGUOUS` needs a human, a
    committed effect is done, and a live lease belongs to another attempt. Anything else is
    a transition no record could make — a wiring bug, not a race.
    """
    if record is None:
        raise InvalidArgument(f"no reservation for effect {effect_key!r}")
    if record.action_id == action_id and record.state in expected:
        return record
    if record.state is EffectState.AMBIGUOUS:
        raise AmbiguousEffect(
            f"effect {effect_key!r} has an unknown outcome; resolve it with "
            f"'ctrlrun resolve {effect_key}'",
            effect_key=effect_key,
            action_id=record.action_id,
        )
    if record.state is EffectState.COMMITTED:
        raise DuplicateEffect(
            f"effect {effect_key!r} was already committed by {record.action_id}",
            state=COMMITTED_EFFECT,
            effect_key=effect_key,
        )
    if record.action_id != action_id and record.lease_is_live(now):
        raise DuplicateEffect(
            f"effect {effect_key!r} is {record.state} under {record.action_id}, not {action_id}",
            state=IN_PROGRESS_EFFECT,
            effect_key=effect_key,
        )
    raise InvalidArgument(
        f"effect {effect_key!r} is {record.state} under {record.action_id}; this transition "
        f"needs {'|'.join(sorted(expected))} under {action_id}"
    )


def _transitioned(
    record: EffectRecord,
    state: EffectState,
    now: datetime,
    *,
    result: Any = None,
    error: str | None = None,
) -> EffectRecord:
    """The record after one outcome transition (SPEC-v0.1 §5.2).

    The lease survives only into `EXECUTING`; every other state here is terminal for this
    attempt, and a terminal record holding a lease would be a lie about work in flight.
    """
    return replace(
        record,
        state=state,
        updated_at=now,
        lease_expires_at=record.lease_expires_at if state is EffectState.EXECUTING else None,
        result=result,
        error=error,
    )


def _resolvable(record: EffectRecord | None, effect_key: str, state: EffectState) -> EffectRecord:
    """The record, if a human may move it to `state` (SPEC-v0.1 §5.2).

    Only `AMBIGUOUS` is resolvable, and only to `COMMITTED` or `FAILED`. Every other record
    is either terminal or belongs to an attempt in flight; a `resolve` that could overwrite
    one would be a way to release a live reservation, which §5.3 says there is none of.
    """
    if state not in RESOLUTIONS:
        raise InvalidArgument(f"an effect resolves to {'|'.join(sorted(RESOLUTIONS))}, not {state}")
    if record is None:
        raise InvalidArgument(f"no effect {effect_key!r} to resolve")
    if record.state is not EffectState.AMBIGUOUS:
        raise InvalidArgument(
            f"effect {effect_key!r} is {record.state}, not ambiguous; only an effect with an "
            "unknown outcome is resolved by hand"
        )
    return record


def _resolved(
    record: EffectRecord, state: EffectState, resolver: str, now: datetime
) -> EffectRecord:
    """The record after a human answered what the executor could not (SPEC-v0.1 §5.2).

    The unknown that made it ambiguous stays on the record: a resolution is a human's claim
    about what happened, and the evidence should say it was one.
    """
    note = f"resolved {state} by {resolver}"
    return _transitioned(
        record,
        state,
        now,
        result=record.result,
        error=note if record.error is None else f"{note} (was: {record.error})",
    )


def _reserved(
    reservation: Reservation, previous: EffectRecord | None, now: datetime
) -> EffectRecord:
    """The record a won reservation writes. A retry keeps the effect's creation time."""
    return EffectRecord(
        effect_key=reservation.effect_key,
        state=EffectState.RESERVED,
        action_id=reservation.action_id,
        attempt=reservation.attempt,
        created_at=now if previous is None else previous.created_at,
        updated_at=now,
        lease_expires_at=reservation.lease_expires_at,
        result=None,
        error=None,
    )


class StateStore(ApprovalStore, Protocol):
    """Durable state behind a `Control` (SPEC-v0.1 §5.3): approvals, effects, evidence."""

    def reserve_effect(
        self, effect_key: str, action_id: str, lease: timedelta = DEFAULT_LEASE
    ) -> Reservation:
        """Claim an effect key for one attempt. At most one caller wins (§5.3 E1).

        Refuses per the retry table of §5.4: `DuplicateEffect` for a committed effect or a
        live reservation, `AmbiguousEffect` for an unresolved or lease-expired one.
        """
        ...

    def consume_approval_and_reserve(
        self,
        approval_id: str,
        action_hash: str,
        effect_key: str,
        action_id: str,
        lease: timedelta = DEFAULT_LEASE,
    ) -> tuple[Approval, Reservation]:
        """Consume the approval and reserve the effect in one transaction (§4.2 A4).

        The approval is checked first, so its refusal is the one raised when both would
        apply (acceptance test T4). If the reservation is refused, nothing is consumed.
        """
        ...

    def begin_execution(self, effect_key: str, action_id: str) -> None:
        """Move a reservation to `EXECUTING`, just before the executor runs."""
        ...

    def commit_effect(self, effect_key: str, action_id: str, result: Any) -> None:
        """Record that the effect happened."""
        ...

    def fail_effect(self, effect_key: str, action_id: str, error: str) -> None:
        """Record that the effect provably did *not* happen (§5.5); a retry is permitted."""
        ...

    def mark_ambiguous(self, effect_key: str, action_id: str, error: str) -> None:
        """Record that the outcome is unknown. Only a human moves it on (§5.2)."""
        ...

    def resolve_effect(self, effect_key: str, state: EffectState, resolver: str) -> EffectRecord:
        """Move an `AMBIGUOUS` record to `COMMITTED` or `FAILED` (SPEC-v0.1 §5.2).

        The only transition out of `AMBIGUOUS`, and the only one a human drives —
        `ctrlrun resolve`. Anything else raises `InvalidArgument`.
        """
        ...

    def get_effect(self, effect_key: str) -> EffectRecord | None:
        """The record for this key, or `None`. A read: it never transitions anything."""
        ...

    def list_effects(self, state: EffectState | None = None) -> tuple[EffectRecord, ...]:
        """Every effect record, oldest first, optionally narrowed to one state."""
        ...

    def extend_lease(self, effect_key: str, action_id: str, until: datetime) -> None:
        """Extend a live `EXECUTING` reservation this action holds (SPEC-v0.2 §6.9.4).

        Atomic, and refused unless all of: the record is `EXECUTING`, its `action_id` is the
        caller's, and its lease **has not already expired**. An expired lease is extendable
        by nothing, and an expired reservation is still released by nobody.
        """
        ...

    def find_granted_approval(self, action_hash: str) -> Approval | None:
        """The newest granted, unexpired approval for this hash, or `None` (§6.10)."""
        ...

    def find_denied_request(self, action_hash: str) -> ApprovalRequest | None:
        """The newest unexpired *denied* request for this hash, or `None` (§6.10).

        "No" is an answer, and re-asking is not free: without this the gateway would send a
        human a fresh notification every time an agent resent a call it disliked.
        """
        ...

    def hold_continuation(
        self, effect_key: str, action_id: str, request_state: str, *, action_hash: str
    ) -> int:
        """Hold this reservation open across an elicitation round trip (SPEC-v0.2 §6.9.2).

        Refused unless the record is `EXECUTING`, held by this `action_id`, with a live
        lease — the same conditions `extend_lease` requires, for the same reason. Returns the
        round number, which is what `--max-elicitation-rounds` is counted against.

        `action_hash` travels with it because the continuation is *the same action*, and the
        gateway has to be able to prove that before admitting one.
        """
        ...

    def take_continuation(self, effect_key: str, request_state: str) -> EffectRecord:
        """Consume a held continuation, or refuse (SPEC-v0.2 §6.9.2).

        The `request_state` is compared with `hmac.compare_digest` and consumed in the same
        transaction that admits the continuation, so one elicitation admits exactly one. Two
        continuations racing on one held key is the duplicate execution this product exists
        to prevent, wearing an `inputResponses` field as a disguise.
        """
        ...

    def held_action_hash(self, effect_key: str) -> str | None:
        """The `action_hash` of the held continuation for this key, or `None`."""
        ...

    def approvals_for(self, action_hash: str) -> tuple[ApprovalRecord, ...]:
        """Every approval record carrying `action_hash`, oldest first (SPEC-v0.2 §5).

        Every one, not only the consumed one: an invalidated, expired or denied request is
        part of an action's history, and `ctrlrun inspect` exists to show that history.

        On `StateStore` rather than on `ApprovalStore`, which is deliberately the slice an
        approval provider depends on. A provider answers one request; it has no business
        enumerating them.
        """
        ...

    def append_event(self, event: Event) -> Event:
        """Append an event, assigning it the next `event_id`, and return it as stored.

        The stored event is handed back because `Control` fans it out to its `EventSink`s
        with the id this store assigned (SPEC-v0.2 §4.1). A sink that could not join its
        export back to the record would be exporting something else.
        """
        ...

    def put_receipt(self, receipt: Receipt) -> None:
        """Record a receipt for an action that reached a terminal state."""
        ...

    def close(self) -> None:
        """Release whatever this store holds open."""
        ...


class InMemoryStateStore:
    """Everything held in process memory: for tests and `ctrlrun demo`.

    Nothing here survives the process, and nothing here is shared between processes, so this
    store cannot provide the cross-process half of SPEC-v0.1 §5.3 E1 — `SQLiteStateStore` is
    the store for anything that matters. Within one process it refuses exactly what SQLite
    refuses: the same `plan_reservation` and `check_consumable` decide, under one lock that
    covers each whole check-and-write.
    """

    def __init__(self, *, clock: Callable[[], datetime] = _utc_now) -> None:
        self._lock = threading.Lock()
        self._clock = clock
        self._events: list[Event] = []
        self._receipts: list[Receipt] = []
        self._approvals: dict[str, ApprovalRecord] = {}
        self._effects: dict[str, EffectRecord] = {}
        self._continuations: dict[str, tuple[str, str, int]] = {}

    def close(self) -> None:
        """Nothing to release; here so either store can be closed the same way."""

    # --- evidence ---------------------------------------------------------------------

    def append_event(self, event: Event) -> Event:
        with self._lock:
            stored = replace(event, event_id=len(self._events) + 1)
            self._events.append(stored)
        return stored

    def put_receipt(self, receipt: Receipt) -> None:
        with self._lock:
            self._receipts.append(receipt)

    def events(self) -> tuple[Event, ...]:
        """An immutable snapshot of the event log, in append order."""
        with self._lock:
            return tuple(self._events)

    def receipts(self) -> tuple[Receipt, ...]:
        """An immutable snapshot of the receipts, in write order."""
        with self._lock:
            return tuple(self._receipts)

    # --- approvals (SPEC-v0.1 §4.2) ---------------------------------------------------

    def put_approval_request(self, request: ApprovalRequest) -> None:
        with self._lock:
            if request.request_id in self._approvals:
                raise InvalidArgument(f"approval {request.request_id} already exists")
            self._approvals[request.request_id] = ApprovalRecord(
                request=request, status=ApprovalStatus.PENDING
            )

    def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        with self._lock:
            return self._approvals.get(approval_id)

    def approvals_for(self, action_hash: str) -> tuple[ApprovalRecord, ...]:
        with self._lock:
            return tuple(
                record for record in self._approvals.values() if record.action_hash == action_hash
            )

    def find_granted_approval(self, action_hash: str) -> Approval | None:
        now = self._clock()
        with self._lock:
            records = list(self._approvals.values())
        return _newest_granted(records, action_hash, now)

    def find_denied_request(self, action_hash: str) -> ApprovalRequest | None:
        now = self._clock()
        with self._lock:
            records = list(self._approvals.values())
        return _newest_denied(records, action_hash, now)

    def grant_approval(self, approval_id: str, approver: str) -> Approval:
        approver = _approver(approver)
        with self._lock:
            record = self._answerable(approval_id)
            granted = replace(
                record,
                status=ApprovalStatus.GRANTED,
                approver=approver,
                granted_at=self._clock(),
            )
            self._approvals[approval_id] = granted
            return granted.as_approval()

    def deny_approval(self, approval_id: str, approver: str) -> None:
        approver = _approver(approver)
        with self._lock:
            record = self._answerable(approval_id)
            self._approvals[approval_id] = replace(
                record, status=ApprovalStatus.DENIED, approver=approver
            )

    def consume_approval(self, approval_id: str, action_hash: str) -> Approval:
        """Take a granted approval for exactly this action, once (SPEC-v0.1 §4.2)."""
        approval, _ = self._authorize_and_reserve(
            approval_id, action_hash, None, None, DEFAULT_LEASE
        )
        return _only(approval, "approval")

    def _answerable(self, approval_id: str) -> ApprovalRecord:
        """The record for `approval_id`, if it still awaits an answer. Caller holds the lock."""
        verdict = check_answerable(self._approvals.get(approval_id), approval_id, self._clock())
        if verdict.refusal is not None:
            if verdict.expire:
                self._expire_locked(approval_id)
            raise verdict.refusal
        return _only(verdict.record, "approval record")

    def _expire_locked(self, approval_id: str) -> None:
        record = self._approvals[approval_id]
        self._approvals[approval_id] = replace(record, status=ApprovalStatus.EXPIRED)

    def _consume_locked(self, approval_id: str, now: datetime) -> None:
        record = self._approvals[approval_id]
        self._approvals[approval_id] = replace(
            record, status=ApprovalStatus.CONSUMED, consumed_at=now
        )

    # --- effects (SPEC-v0.1 §5.3) -----------------------------------------------------

    def reserve_effect(
        self, effect_key: str, action_id: str, lease: timedelta = DEFAULT_LEASE
    ) -> Reservation:
        _, reservation = self._authorize_and_reserve(None, None, effect_key, action_id, lease)
        return _only(reservation, "reservation")

    def consume_approval_and_reserve(
        self,
        approval_id: str,
        action_hash: str,
        effect_key: str,
        action_id: str,
        lease: timedelta = DEFAULT_LEASE,
    ) -> tuple[Approval, Reservation]:
        approval, reservation = self._authorize_and_reserve(
            approval_id, action_hash, effect_key, action_id, lease
        )
        return _only(approval, "approval"), _only(reservation, "reservation")

    def _authorize_and_reserve(
        self,
        approval_id: str | None,
        action_hash: str | None,
        effect_key: str | None,
        action_id: str | None,
        lease: timedelta,
    ) -> tuple[Approval | None, Reservation | None]:
        """Consume an approval, reserve an effect, or both together (SPEC-v0.1 §4.2 A4).

        Nothing is written until both have been decided, and the reservation is written
        before the consumption, so a store failure part-way cannot leave an approval spent
        on an attempt that never reserved.
        """
        with self._lock:
            now = self._clock()
            approved: ApprovalRecord | None = None
            if approval_id is not None:
                approved = self._consumable(approval_id, _required_hash(action_hash), now)
            plan = ReservationPlan()
            if effect_key is not None:
                plan = self._plan(effect_key, _required_action(action_id), lease, now)
            if plan.reservation is not None:
                self._reserve_locked(plan.reservation, plan.renews, now)
            if approved is not None:
                self._consume_locked(approved.approval_id, now)
        return (approved.as_approval() if approved is not None else None), plan.reservation

    def _consumable(self, approval_id: str, action_hash: str, now: datetime) -> ApprovalRecord:
        verdict = check_consumable(self._approvals.get(approval_id), approval_id, action_hash, now)
        if verdict.refusal is not None:
            if verdict.expire:
                self._expire_locked(approval_id)
            raise verdict.refusal
        return _only(verdict.record, "approval record")

    def _plan(
        self, effect_key: str, action_id: str, lease: timedelta, now: datetime
    ) -> ReservationPlan:
        plan = plan_reservation(self._effects.get(effect_key), effect_key, action_id, lease, now)
        if plan.refusal is not None:
            if plan.ambiguate:
                self._effects[effect_key] = _transitioned(
                    self._effects[effect_key], EffectState.AMBIGUOUS, now, error=LEASE_EXPIRED
                )
            raise plan.refusal
        return plan

    def _reserve_locked(self, reservation: Reservation, renews: bool, now: datetime) -> None:
        previous = self._effects.get(reservation.effect_key)
        self._effects[reservation.effect_key] = _reserved(reservation, previous, now)

    def begin_execution(self, effect_key: str, action_id: str) -> None:
        self._transition(effect_key, action_id, EffectState.EXECUTING, _RESERVED)

    def commit_effect(self, effect_key: str, action_id: str, result: Any) -> None:
        self._transition(
            effect_key,
            action_id,
            EffectState.COMMITTED,
            _EXECUTING,
            result=_result_value(_result_json(result)),
        )

    def fail_effect(self, effect_key: str, action_id: str, error: str) -> None:
        self._transition(effect_key, action_id, EffectState.FAILED, _EXECUTING, error=error)

    def mark_ambiguous(self, effect_key: str, action_id: str, error: str) -> None:
        self._transition(effect_key, action_id, EffectState.AMBIGUOUS, _UNFINISHED, error=error)

    def resolve_effect(self, effect_key: str, state: EffectState, resolver: str) -> EffectRecord:
        resolver = _approver(resolver)
        with self._lock:
            record = _resolvable(self._effects.get(effect_key), effect_key, state)
            resolved = _resolved(record, state, resolver, self._clock())
            self._effects[effect_key] = resolved
            return resolved

    def extend_lease(self, effect_key: str, action_id: str, until: datetime) -> None:
        with self._lock:
            extended = plan_lease_extension(
                self._effects.get(effect_key), effect_key, action_id, until, self._clock()
            )
            self._effects[effect_key] = extended

    def hold_continuation(
        self, effect_key: str, action_id: str, request_state: str, *, action_hash: str
    ) -> int:
        with self._lock:
            record = _holdable(self._effects.get(effect_key), effect_key, action_id, self._clock())
            _, _, rounds = self._continuations.get(effect_key, ("", "", 0))
            self._continuations[effect_key] = (request_state, action_hash, rounds + 1)
            assert record is not None
            return rounds + 1

    def take_continuation(self, effect_key: str, request_state: str) -> EffectRecord:
        with self._lock:
            record = _continuable(
                self._effects.get(effect_key),
                self._continuations.get(effect_key),
                effect_key,
                request_state,
                self._clock(),
            )
            held = self._continuations[effect_key]
            self._continuations[effect_key] = ("", held[1], held[2])
            return record

    def held_action_hash(self, effect_key: str) -> str | None:
        with self._lock:
            held = self._continuations.get(effect_key)
        return None if held is None else held[1]

    def get_effect(self, effect_key: str) -> EffectRecord | None:
        with self._lock:
            return self._effects.get(effect_key)

    def list_effects(self, state: EffectState | None = None) -> tuple[EffectRecord, ...]:
        with self._lock:
            records = sorted(self._effects.values(), key=lambda record: record.created_at)
        return tuple(record for record in records if state is None or record.state is state)

    def _transition(
        self,
        effect_key: str,
        action_id: str,
        state: EffectState,
        expected: frozenset[EffectState],
        *,
        result: Any = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            now = self._clock()
            record = _checked(self._effects.get(effect_key), effect_key, action_id, expected, now)
            self._effects[effect_key] = _transitioned(
                record, state, now, result=result, error=error
            )


class SQLiteStateStore:
    """Approvals, effects and evidence in one SQLite file (ARCHITECTURE §5).

    This is the store that makes reservation atomic across processes (SPEC-v0.1 §5.3 E1):
    every decision is taken inside `BEGIN IMMEDIATE`, which holds the database's write lock,
    and `effects.effect_key` is a primary key — the `UNIQUE(effect_key)` constraint — so an
    insert that races past the lock still fails rather than overwriting a reservation.

    A connection is opened per thread; `sqlite3` connections are not shareable. Separate
    processes simply open the same file, which is the point.
    """

    def __init__(
        self, path: str | os.PathLike[str], *, clock: Callable[[], datetime] = _utc_now
    ) -> None:
        text = os.fspath(path)
        if text == ":memory:" or "mode=memory" in text:
            # An in-memory database is private to one connection, so it could not reserve
            # across threads, let alone processes. Refuse rather than silently lose E1.
            raise InvalidArgument(
                f"{text!r} is per-connection and cannot reserve across processes; "
                "use a file path, or InMemoryStateStore if that is what you meant"
            )
        self._path = Path(text)
        self._clock = clock
        self._local = threading.local()
        self._open: set[sqlite3.Connection] = set()
        self._open_lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._connection().executescript(_SCHEMA)

    @property
    def path(self) -> Path:
        return self._path

    def close(self) -> None:
        """Close every connection this store opened, on whichever thread opened it.

        A shutdown operation, not a per-thread one: a long-lived host that runs agents on a
        thread pool would otherwise accumulate one open file handle per thread that ever
        touched the store, and closing only the caller's would leave them all. A thread that
        uses the store after this simply gets a fresh connection.

        It is not safe to call while another thread is mid-transaction — that is the caller's
        to arrange, as it is with any resource being torn down.
        """
        with self._open_lock:
            connections, self._open = self._open, set()
        for connection in connections:
            connection.close()

    def _connection(self) -> sqlite3.Connection:
        connection: sqlite3.Connection | None = getattr(self._local, "connection", None)
        if connection is not None:
            with self._open_lock:
                if connection in self._open:
                    return connection
            # `close()` tore this one down; open a fresh one rather than hand back a corpse.
        # `check_same_thread=False` because `close()` closes other threads' connections. Each
        # connection is still used by exactly one thread — that is what the thread-local is
        # for — so the guard this drops was never the thing keeping them apart.
        connection = sqlite3.connect(
            self._path,
            isolation_level=None,
            timeout=BUSY_TIMEOUT_MS / 1000,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA synchronous=NORMAL")
        self._local.connection = connection
        with self._open_lock:
            self._open.add(connection)
        return connection

    # --- evidence ---------------------------------------------------------------------

    def append_event(self, event: Event) -> Event:
        cursor = self._connection().execute(
            "INSERT INTO events(ts, type, action_id, effect_key, approval_id, data_json) "
            "VALUES(?,?,?,?,?,?)",
            (
                _iso(event.ts),
                str(event.type),
                event.action_id,
                event.effect_key,
                event.approval_id,
                json.dumps(dict(event.data), ensure_ascii=False, separators=(",", ":")),
            ),
        )
        return replace(event, event_id=cursor.lastrowid)

    def put_receipt(self, receipt: Receipt) -> None:
        self._connection().execute(
            "INSERT INTO receipts(receipt_id, action_id, effect_key, result, json, ts) "
            "VALUES(?,?,?,?,?,?)",
            (
                receipt.receipt_id,
                receipt.action_id,
                receipt.effect_key,
                str(receipt.result),
                receipt.to_json(),
                _iso(receipt.finished_at),
            ),
        )

    def events(self) -> tuple[Event, ...]:
        rows = self._connection().execute("SELECT * FROM events ORDER BY event_id").fetchall()
        return tuple(
            Event(
                type=EventType(row["type"]),
                action_id=row["action_id"],
                ts=datetime.fromisoformat(row["ts"]),
                data=json.loads(row["data_json"]),
                effect_key=row["effect_key"],
                approval_id=row["approval_id"],
                event_id=row["event_id"],
            )
            for row in rows
        )

    def receipts(self) -> tuple[Receipt, ...]:
        rows = self._connection().execute("SELECT json FROM receipts ORDER BY rowid").fetchall()
        return tuple(Receipt.from_json(row["json"]) for row in rows)

    # --- approvals (SPEC-v0.1 §4.2) ---------------------------------------------------

    def put_approval_request(self, request: ApprovalRequest) -> None:
        try:
            self._connection().execute(
                "INSERT INTO approvals(approval_id, action_hash, status, action_json, "
                "created_at, expires_at) VALUES(?,?,?,?,?,?)",
                (
                    request.request_id,
                    request.action_hash,
                    str(ApprovalStatus.PENDING),
                    _action_json(request.action),
                    _iso(request.created_at),
                    _iso(request.expires_at),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise InvalidArgument(f"approval {request.request_id} already exists") from exc

    def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        return self._read_approval(self._connection(), approval_id)

    def hold_continuation(
        self, effect_key: str, action_id: str, request_state: str, *, action_hash: str
    ) -> int:
        connection = self._connection()
        connection.execute("BEGIN IMMEDIATE")
        try:
            _holdable(
                self._read_effect(connection, effect_key), effect_key, action_id, self._clock()
            )
            row = connection.execute(
                "SELECT rounds FROM continuations WHERE effect_key=?", (effect_key,)
            ).fetchone()
            rounds = (row["rounds"] if row else 0) + 1
            connection.execute(
                "INSERT INTO continuations(effect_key, action_id, action_hash, request_state, "
                "rounds, updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(effect_key) DO UPDATE SET "
                "action_id=excluded.action_id, action_hash=excluded.action_hash, "
                "request_state=excluded.request_state, rounds=excluded.rounds, "
                "updated_at=excluded.updated_at",
                (
                    effect_key,
                    action_id,
                    action_hash,
                    request_state,
                    rounds,
                    _iso(self._clock()),
                ),
            )
        except BaseException:
            self._unwind(connection)
            raise
        connection.commit()
        return rounds

    def take_continuation(self, effect_key: str, request_state: str) -> EffectRecord:
        connection = self._connection()
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                "SELECT request_state, action_hash, rounds FROM continuations WHERE effect_key=?",
                (effect_key,),
            ).fetchone()
            held = (
                None if row is None else (row["request_state"], row["action_hash"], row["rounds"])
            )
            record = _continuable(
                self._read_effect(connection, effect_key),
                held,
                effect_key,
                request_state,
                self._clock(),
            )
            # Consumed in the same transaction that admits it (§6.9.2).
            connection.execute(
                "UPDATE continuations SET request_state='' WHERE effect_key=?", (effect_key,)
            )
        except BaseException:
            self._unwind(connection)
            raise
        connection.commit()
        return record

    def held_action_hash(self, effect_key: str) -> str | None:
        row = (
            self._connection()
            .execute("SELECT action_hash FROM continuations WHERE effect_key=?", (effect_key,))
            .fetchone()
        )
        return None if row is None else str(row["action_hash"])

    def find_granted_approval(self, action_hash: str) -> Approval | None:
        return _newest_granted(self.approvals_for(action_hash), action_hash, self._clock())

    def find_denied_request(self, action_hash: str) -> ApprovalRequest | None:
        return _newest_denied(self.approvals_for(action_hash), action_hash, self._clock())

    def approvals_for(self, action_hash: str) -> tuple[ApprovalRecord, ...]:
        connection = self._connection()
        rows = connection.execute(
            "SELECT approval_id FROM approvals WHERE action_hash=? ORDER BY rowid",
            (action_hash,),
        ).fetchall()
        found = (self._read_approval(connection, row["approval_id"]) for row in rows)
        return tuple(record for record in found if record is not None)

    def grant_approval(self, approval_id: str, approver: str) -> Approval:
        approver = _approver(approver)
        connection = self._connection()
        now = self._clock()
        connection.execute("BEGIN IMMEDIATE")
        try:
            record = self._answerable(connection, approval_id, now)
            granted = replace(
                record, status=ApprovalStatus.GRANTED, approver=approver, granted_at=now
            )
            connection.execute(
                "UPDATE approvals SET status=?, approver=?, granted_at=? WHERE approval_id=?",
                (str(ApprovalStatus.GRANTED), approver, _iso(now), approval_id),
            )
        except BaseException:
            self._unwind(connection)
            raise
        connection.commit()
        return granted.as_approval()

    def deny_approval(self, approval_id: str, approver: str) -> None:
        approver = _approver(approver)
        connection = self._connection()
        now = self._clock()
        connection.execute("BEGIN IMMEDIATE")
        try:
            self._answerable(connection, approval_id, now)
            connection.execute(
                "UPDATE approvals SET status=?, approver=? WHERE approval_id=?",
                (str(ApprovalStatus.DENIED), approver, approval_id),
            )
        except BaseException:
            self._unwind(connection)
            raise
        connection.commit()

    def consume_approval(self, approval_id: str, action_hash: str) -> Approval:
        approval, _ = self._authorize_and_reserve(
            approval_id, action_hash, None, None, DEFAULT_LEASE
        )
        return _only(approval, "approval")

    def _answerable(
        self, connection: sqlite3.Connection, approval_id: str, now: datetime
    ) -> ApprovalRecord:
        verdict = check_answerable(self._read_approval(connection, approval_id), approval_id, now)
        if verdict.refusal is not None:
            if verdict.expire:
                self._expire_locked(connection, approval_id)
                connection.commit()  # a lapsed approval is evidence; keep it, then refuse
            raise verdict.refusal
        return _only(verdict.record, "approval record")

    def _read_approval(
        self, connection: sqlite3.Connection, approval_id: str
    ) -> ApprovalRecord | None:
        row = connection.execute(
            "SELECT * FROM approvals WHERE approval_id=?", (approval_id,)
        ).fetchone()
        if row is None:
            return None
        return ApprovalRecord(
            request=ApprovalRequest(
                request_id=row["approval_id"],
                action_hash=row["action_hash"],
                action=_action_from_json(row["action_json"]),
                created_at=datetime.fromisoformat(row["created_at"]),
                expires_at=datetime.fromisoformat(row["expires_at"]),
            ),
            status=ApprovalStatus(row["status"]),
            approver=row["approver"],
            granted_at=_at(row["granted_at"]),
            consumed_at=_at(row["consumed_at"]),
        )

    def _expire_locked(self, connection: sqlite3.Connection, approval_id: str) -> None:
        connection.execute(
            "UPDATE approvals SET status=? WHERE approval_id=?",
            (str(ApprovalStatus.EXPIRED), approval_id),
        )

    def _consume_locked(
        self, connection: sqlite3.Connection, approval_id: str, now: datetime
    ) -> None:
        connection.execute(
            "UPDATE approvals SET status=?, consumed_at=? WHERE approval_id=?",
            (str(ApprovalStatus.CONSUMED), _iso(now), approval_id),
        )

    # --- effects (SPEC-v0.1 §5.3) -----------------------------------------------------

    def reserve_effect(
        self, effect_key: str, action_id: str, lease: timedelta = DEFAULT_LEASE
    ) -> Reservation:
        _, reservation = self._authorize_and_reserve(None, None, effect_key, action_id, lease)
        return _only(reservation, "reservation")

    def consume_approval_and_reserve(
        self,
        approval_id: str,
        action_hash: str,
        effect_key: str,
        action_id: str,
        lease: timedelta = DEFAULT_LEASE,
    ) -> tuple[Approval, Reservation]:
        approval, reservation = self._authorize_and_reserve(
            approval_id, action_hash, effect_key, action_id, lease
        )
        return _only(approval, "approval"), _only(reservation, "reservation")

    def _authorize_and_reserve(
        self,
        approval_id: str | None,
        action_hash: str | None,
        effect_key: str | None,
        action_id: str | None,
        lease: timedelta,
    ) -> tuple[Approval | None, Reservation | None]:
        """Consume an approval, reserve an effect, or both, in one transaction (§4.2 A4).

        `BEGIN IMMEDIATE` takes the write lock before the first read, so the whole
        check-and-write is serialized against every other process on this file (§5.3 E1).
        Nothing is written until both halves have been decided, so a refused reservation
        rolls back to an approval that is still granted (acceptance test T12).
        """
        connection = self._connection()
        now = self._clock()
        connection.execute("BEGIN IMMEDIATE")
        try:
            approved: ApprovalRecord | None = None
            if approval_id is not None:
                approved = self._consumable(
                    connection, approval_id, _required_hash(action_hash), now
                )
            plan = ReservationPlan()
            if effect_key is not None:
                plan = self._plan(connection, effect_key, _required_action(action_id), lease, now)
            if plan.reservation is not None:
                self._reserve_locked(connection, plan.reservation, plan.renews, now)
            if approved is not None:
                self._consume_locked(connection, approved.approval_id, now)
        except BaseException:
            self._unwind(connection)
            raise
        connection.commit()
        return (approved.as_approval() if approved is not None else None), plan.reservation

    def _consumable(
        self, connection: sqlite3.Connection, approval_id: str, action_hash: str, now: datetime
    ) -> ApprovalRecord:
        verdict = check_consumable(
            self._read_approval(connection, approval_id), approval_id, action_hash, now
        )
        if verdict.refusal is not None:
            if verdict.expire:
                self._expire_locked(connection, approval_id)
                connection.commit()  # a lapsed approval is evidence; keep it, then refuse
            raise verdict.refusal
        return _only(verdict.record, "approval record")

    def _plan(
        self,
        connection: sqlite3.Connection,
        effect_key: str,
        action_id: str,
        lease: timedelta,
        now: datetime,
    ) -> ReservationPlan:
        record = self._read_effect(connection, effect_key)
        plan = plan_reservation(record, effect_key, action_id, lease, now)
        if plan.refusal is not None:
            if plan.ambiguate and record is not None:
                # SPEC §5.3 E3 — the expired lease becomes AMBIGUOUS and that write is kept,
                # even though this attempt is refused. The approval, written after, is not.
                self._write_effect(
                    connection,
                    _transitioned(record, EffectState.AMBIGUOUS, now, error=LEASE_EXPIRED),
                )
                connection.commit()
            raise plan.refusal
        return plan

    def _reserve_locked(
        self,
        connection: sqlite3.Connection,
        reservation: Reservation,
        renews: bool,
        now: datetime,
    ) -> None:
        previous = self._read_effect(connection, reservation.effect_key)
        record = _reserved(reservation, previous, now)
        if renews:
            # Only a FAILED record is renewable (§5.4); the WHERE clause says so again, so a
            # record that changed under us refuses instead of overwriting an attempt.
            updated = connection.execute(
                "UPDATE effects SET state=?, action_id=?, attempt=?, lease_expires_at=?, "
                "result_json=NULL, error=NULL, updated_at=? WHERE effect_key=? AND state=?",
                (
                    str(record.state),
                    record.action_id,
                    record.attempt,
                    _iso(record.lease_expires_at) if record.lease_expires_at else None,
                    _iso(now),
                    record.effect_key,
                    str(EffectState.FAILED),
                ),
            ).rowcount
            if updated != 1:
                raise DuplicateEffect(
                    f"effect {record.effect_key!r} was taken by another attempt",
                    state=IN_PROGRESS_EFFECT,
                    effect_key=record.effect_key,
                )
            return
        try:
            connection.execute(
                "INSERT INTO effects(effect_key, state, action_id, attempt, lease_expires_at, "
                "result_json, error, created_at, updated_at) VALUES(?,?,?,?,?,NULL,NULL,?,?)",
                (
                    record.effect_key,
                    str(record.state),
                    record.action_id,
                    record.attempt,
                    _iso(record.lease_expires_at) if record.lease_expires_at else None,
                    _iso(record.created_at),
                    _iso(record.updated_at),
                ),
            )
        except sqlite3.IntegrityError as exc:
            # UNIQUE(effect_key). Unreachable while BEGIN IMMEDIATE holds the write lock,
            # which is exactly why it is worth keeping: the constraint is the last word.
            raise DuplicateEffect(
                f"effect {record.effect_key!r} is already reserved",
                state=IN_PROGRESS_EFFECT,
                effect_key=record.effect_key,
            ) from exc

    def begin_execution(self, effect_key: str, action_id: str) -> None:
        self._transition(effect_key, action_id, EffectState.EXECUTING, _RESERVED)

    def commit_effect(self, effect_key: str, action_id: str, result: Any) -> None:
        self._transition(effect_key, action_id, EffectState.COMMITTED, _EXECUTING, result=result)

    def fail_effect(self, effect_key: str, action_id: str, error: str) -> None:
        self._transition(effect_key, action_id, EffectState.FAILED, _EXECUTING, error=error)

    def mark_ambiguous(self, effect_key: str, action_id: str, error: str) -> None:
        self._transition(effect_key, action_id, EffectState.AMBIGUOUS, _UNFINISHED, error=error)

    def extend_lease(self, effect_key: str, action_id: str, until: datetime) -> None:
        connection = self._connection()
        connection.execute("BEGIN IMMEDIATE")
        try:
            extended = plan_lease_extension(
                self._read_effect(connection, effect_key),
                effect_key,
                action_id,
                until,
                self._clock(),
            )
            self._write_effect(connection, extended)
        except BaseException:
            self._unwind(connection)
            raise
        connection.commit()

    def resolve_effect(self, effect_key: str, state: EffectState, resolver: str) -> EffectRecord:
        resolver = _approver(resolver)
        connection = self._connection()
        connection.execute("BEGIN IMMEDIATE")
        try:
            record = _resolvable(self._read_effect(connection, effect_key), effect_key, state)
            resolved = _resolved(record, state, resolver, self._clock())
            self._write_effect(connection, resolved)
        except BaseException:
            self._unwind(connection)
            raise
        connection.commit()
        return resolved

    def get_effect(self, effect_key: str) -> EffectRecord | None:
        return self._read_effect(self._connection(), effect_key)

    def list_effects(self, state: EffectState | None = None) -> tuple[EffectRecord, ...]:
        rows = (
            self._connection()
            .execute(
                "SELECT effect_key FROM effects"
                + ("" if state is None else " WHERE state=?")
                + " ORDER BY created_at, rowid",
                () if state is None else (str(state),),
            )
            .fetchall()
        )
        records = (self.get_effect(row["effect_key"]) for row in rows)
        return tuple(record for record in records if record is not None)

    def _transition(
        self,
        effect_key: str,
        action_id: str,
        state: EffectState,
        expected: frozenset[EffectState],
        *,
        result: Any = None,
        error: str | None = None,
    ) -> None:
        connection = self._connection()
        now = self._clock()
        connection.execute("BEGIN IMMEDIATE")
        try:
            record = _checked(
                self._read_effect(connection, effect_key), effect_key, action_id, expected, now
            )
            self._write_effect(
                connection, _transitioned(record, state, now, result=result, error=error)
            )
        except BaseException:
            self._unwind(connection)
            raise
        connection.commit()

    def _read_effect(self, connection: sqlite3.Connection, effect_key: str) -> EffectRecord | None:
        row = connection.execute(
            "SELECT * FROM effects WHERE effect_key=?", (effect_key,)
        ).fetchone()
        if row is None:
            return None
        return EffectRecord(
            effect_key=row["effect_key"],
            state=EffectState(row["state"]),
            action_id=row["action_id"],
            attempt=row["attempt"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            lease_expires_at=_at(row["lease_expires_at"]),
            result=_result_value(row["result_json"]),
            error=row["error"],
        )

    def _write_effect(self, connection: sqlite3.Connection, record: EffectRecord) -> None:
        connection.execute(
            "UPDATE effects SET state=?, action_id=?, attempt=?, lease_expires_at=?, "
            "result_json=?, error=?, updated_at=? WHERE effect_key=?",
            (
                str(record.state),
                record.action_id,
                record.attempt,
                _iso(record.lease_expires_at) if record.lease_expires_at else None,
                _result_json(record.result),
                record.error,
                _iso(record.updated_at),
                record.effect_key,
            ),
        )

    @staticmethod
    def _unwind(connection: sqlite3.Connection) -> None:
        """Undo the open transaction, if this failure did not already close one."""
        connection.rollback()


_T = TypeVar("_T")


def _only(value: _T | None, what: str) -> _T:
    """Narrow a result the caller did ask for. A store returning nothing here is broken."""
    if value is None:
        raise InvalidArgument(f"the store produced no {what}")
    return value


def _approver(approver: str) -> str:
    if not approver:
        raise InvalidArgument("approver must be a non-empty string")
    return approver


def _required_hash(action_hash: str | None) -> str:
    if not action_hash:
        raise InvalidArgument("consuming an approval needs the action hash it must authorize")
    return action_hash


def _required_action(action_id: str | None) -> str:
    if not action_id:
        raise InvalidArgument("reserving an effect needs the action_id reserving it")
    return action_id


def _newest_granted(
    records: Iterable[ApprovalRecord], action_hash: str, now: datetime
) -> Approval | None:
    """The newest granted, unexpired approval for `action_hash` (SPEC-v0.2 §6.10).

    Matching on the hash rather than on a presented request id is safe for the reason v0.1
    §4.2 A1 exists: the hash covers the principal, the arguments, the resource and the
    environment, so an approval can only match an identical action from the same principal —
    which is the same action. It is still single-use, and still consumed atomically with the
    reservation, which is where that guarantee actually lives.
    """
    granted = [
        record
        for record in records
        if record.action_hash == action_hash
        and record.status is ApprovalStatus.GRANTED
        and record.expires_at > now
    ]
    if not granted:
        return None
    # `reversed`, because `max` returns the *first* maximal element and `records` arrives
    # oldest-first: two approvals granted in the same clock tick tie, and the later-stored
    # one is the newer of them.
    newest: ApprovalRecord = max(
        reversed(list(granted)), key=lambda record: record.granted_at or record.request.created_at
    )
    return newest.as_approval()


def _newest_denied(
    records: Iterable[ApprovalRecord], action_hash: str, now: datetime
) -> ApprovalRequest | None:
    """The newest unexpired denied request for `action_hash` (SPEC-v0.2 §6.10).

    A denial holds for the life of the request that carried it; once that expires, asking
    again is legitimate.
    """
    denied = [
        record
        for record in records
        if record.action_hash == action_hash
        and record.status is ApprovalStatus.DENIED
        and record.expires_at > now
    ]
    if not denied:
        return None
    newest: ApprovalRecord = max(reversed(denied), key=lambda record: record.request.created_at)
    return newest.request


def _holdable(
    record: EffectRecord | None, effect_key: str, action_id: str, now: datetime
) -> EffectRecord:
    """Whether this reservation may be held open across a round trip (SPEC-v0.2 §6.9.2).

    The same conditions `extend_lease` requires, and for the same reason: a record that is
    not this attempt's, live and executing is not this attempt's to suspend either.
    """
    if record is None or record.state is not EffectState.EXECUTING or not record.lease_is_live(now):
        raise AmbiguousEffect(
            f"effect {effect_key!r} is not executing under a live lease and cannot be held",
            effect_key=effect_key,
            action_id=None if record is None else record.action_id,
        )
    if record.action_id != action_id:
        raise DuplicateEffect(
            f"effect {effect_key!r} is held by {record.action_id}, not {action_id}",
            state=IN_PROGRESS_EFFECT,
            effect_key=effect_key,
        )
    return record


def _continuable(
    record: EffectRecord | None,
    held: tuple[str, str, int] | None,
    effect_key: str,
    request_state: str,
    now: datetime,
) -> EffectRecord:
    """Whether a continuation may be admitted (SPEC-v0.2 §6.9.2).

    The record checks come first, so an expired or resolved reservation is refused as what it
    is rather than as an unknown continuation. `hmac.compare_digest` because the presented
    value is agent-supplied and comparing it byte by byte leaks its prefix.
    """
    if record is None or record.state is not EffectState.EXECUTING or not record.lease_is_live(now):
        raise AmbiguousEffect(
            f"effect {effect_key!r} is no longer executing under a live lease",
            effect_key=effect_key,
            action_id=None if record is None else record.action_id,
        )
    stored = "" if held is None else held[0]
    if not stored or not hmac.compare_digest(stored, request_state):
        raise InvalidArgument(
            f"no held elicitation on {effect_key!r} matches the presented requestState"
        )
    return record
