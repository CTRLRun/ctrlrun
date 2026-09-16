# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
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
import time
import unicodedata
import weakref
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Protocol, TypeVar

from .action import Action, Principal
from .anchor import Anchor
from .approval import (
    Approval,
    ApprovalRecord,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalStore,
    RequiredRole,
    VerifiedApprover,
    _verified_approver_now,
    check_answerable,
    check_consumable,
    count_grant,
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
from .migrations import migrate
from .receipt import (
    GENESIS_HASH,
    RECEIPT_SCHEMA,
    Event,
    EventType,
    Receipt,
    UnreadableReceipt,
    _document_hash,
    _read_receipt,
)
from .retention import Checkpoint, Hold

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


def _enable_wal(connection: sqlite3.Connection) -> None:
    """Put the database in WAL, tolerating a concurrent starter who is doing the same.

    Switching journal mode takes a **brief exclusive lock**, and SQLite's busy handler does not
    cover it: under contention the pragma returns `database is locked` immediately rather than
    waiting. That was survivable while opening a store was a read. SPEC-v0.6 §3 makes every open
    a potential write, so a fleet restarting after an upgrade meets it -- T149d measured two of
    six opens failing.

    **Another process winning this race is a success, not a failure.** If the mode is already
    WAL there is nothing to do; if the switch is refused we wait and look again, because whoever
    holds the lock is setting the same value we wanted. The loop is bounded by the same
    `busy_timeout` budget every other contended write gets, so a database that genuinely cannot
    be put in WAL still reports it rather than spinning.
    """
    deadline = time.monotonic() + BUSY_TIMEOUT_MS / 1000
    while True:
        mode = connection.execute("PRAGMA journal_mode").fetchone()
        if mode is not None and str(mode[0]).lower() == "wal":
            return
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError:
            if time.monotonic() >= deadline:
                raise
            # Somebody else is setting the same value. Wait a little and look again -- the loop
            # exits the moment the mode is WAL, whoever put it there.
            time.sleep(0.01)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    """A stored timestamp: UTC ISO-8601 at full precision, so it round-trips exactly."""
    return moment.astimezone(UTC).isoformat()


def _at(text: str | None) -> datetime | None:
    return None if text is None else datetime.fromisoformat(text)


#: The oldest SQLite this store works on. `put_receipt` uses `UPDATE ... RETURNING`, which
#: arrived in SQLite 3.35 (March 2021).
#:
#: `requires-python >= 3.11` does not imply it: on Linux CPython links the *system*
#: libsqlite3, and RHEL 8 ships 3.26. Undeclared and unchecked, the first receipt write on
#: such a host raised a bare `sqlite3.OperationalError` about a syntax error near RETURNING,
#: which names neither the real cause nor the remedy.
MIN_SQLITE_VERSION: Final = (3, 35)


def _require_sqlite() -> None:
    """Refuse at open, where the message can name the version, not at the first receipt."""
    if sqlite3.sqlite_version_info >= MIN_SQLITE_VERSION:
        return
    wanted = ".".join(str(part) for part in MIN_SQLITE_VERSION)
    # `InvalidArgument`, on the `:memory:` precedent in this same constructor: the closed
    # error set has no member for "the environment is too old", and `MissingDependency`
    # renders a fixed "it ships in the X extra" sentence that would be false here.
    raise InvalidArgument(
        f"this Python is linked against SQLite {sqlite3.sqlite_version}, and ctrlrun needs "
        f"{wanted} or newer: the receipt chain is written with `UPDATE ... RETURNING`, which "
        f"older SQLite cannot parse. Upgrade the system SQLite, use a Python built against a "
        f"newer one, or run the Postgres backend (pip install 'ctrlrun[postgres]')."
    )


def _storable(text: str | None) -> str | None:
    """`text` in a form SQLite can encode, escaping any lone surrogate.

    Every str SQLite stores is encoded as UTF-8, and a lone surrogate cannot be: writing one
    raises `UnicodeEncodeError` from inside the transaction. That turned the *recording* of an
    outcome into a failure -- `mark_ambiguous` raised out of `Control.execute`, not as a
    `CTRLRunError`, and left the effect in EXECUTING, which is neither outcome and blocks the
    retry until the lease expires. An error message is evidence about an action; it must never
    be able to decide the action's fate.

    Lone surrogates arrive the ordinary way. `json.loads('"\\ud800"')` yields one, and so does
    `os.fsdecode` of any non-UTF-8 filename -- that is what `errors="surrogateescape"` is.

    `backslashreplace` rather than `replace`: `\\ud800` in a stored message says what the byte
    was, where `?` throws it away, and this text is read by a human diagnosing an ambiguous
    effect.
    """
    if text is None:
        return None
    return text.encode("utf-8", "backslashreplace").decode("utf-8")


def _result_json(result: Any) -> str | None:
    """Serialize an executor's return value. Never raises: the effect *did* commit.

    An unserializable result is stored as its `repr`. Losing the shape of a return value is
    a cosmetic loss; failing here would turn a committed effect into an error.
    """
    if result is None:
        return None
    try:
        text = json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=repr)
    except (TypeError, ValueError, RecursionError):
        text = json.dumps({"repr": repr(result)}, ensure_ascii=False, separators=(",", ":"))
    # `json.dumps` is happy to emit a lone surrogate; SQLite is not. The promise above is
    # that this never raises, and it is only kept as far as the value can actually be stored.
    return _storable(text)


def _result_value(text: str | None) -> Any:
    return None if text is None else json.loads(text)


def _action_json(action: Action) -> str:
    """An Action as stored JSON. Round-trips to the same `action_hash` (SPEC-v0.1 §2.2)."""
    return json.dumps(
        {
            "action_id": action.action_id,
            "name": action.name,
            "arguments": action.canonical_arguments,
            # SPEC-v0.3 §2.4 — the whole principal, not just the two v0.1 fields. Without
            # this every receipt written by `Control.resume` reports no claims and no expiry,
            # on the only receipt an MCP multi-round-trip action ever gets. A value change
            # inside an existing TEXT column, not a schema migration.
            "principal": {
                "agent": action.principal.agent,
                "user": action.principal.user,
                "claims": dict(action.principal.claims),
                "issuer": action.principal.issuer,
                "expires_at": (
                    None
                    if action.principal.expires_at is None
                    else action.principal.expires_at.isoformat()
                ),
            },
            "resource": action.resource,
            "environment": action.environment,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _action_from_json(text: str) -> Action:
    document = json.loads(text)
    principal = document["principal"]
    expires_at = principal.get("expires_at")
    return Action(
        name=document["name"],
        arguments=document["arguments"],
        # `.get` for the three v0.3 fields: a row written by 0.2 carries only the two older
        # keys and must parse back with v0.1 §2.1's defaults rather than raising (§2.4).
        principal=Principal(
            agent=principal["agent"],
            user=principal["user"],
            claims=principal.get("claims") or {},
            issuer=principal.get("issuer"),
            expires_at=None if expires_at is None else datetime.fromisoformat(expires_at),
        ),
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
    return replace(
        _transitioned(
            record,
            state,
            now,
            result=record.result,
            error=note if record.error is None else f"{note} (was: {record.error})",
        ),
        # SPEC-v0.6 §5.3: queryable, and not buried in the executor's error text.
        resolved_by=resolver,
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


@dataclass(frozen=True)
class HeldContinuation:
    """What `take_continuation` hands back: the same action, still reserved (§6.9.2)."""

    action: Action
    effect_key: str
    record: EffectRecord
    rounds: int


@dataclass(frozen=True)
class DelegationRecord:
    """One row of the `delegations` table (SPEC-v0.3 §5.2).

    **The store persists rows, not grants.** `grant_json` stays a string here and
    `authority.py` is what parses a `Grant` out of it, so `state.py` does not import
    `authority.py` and therefore does not transitively acquire `policy.py` —
    `ARCHITECTURE.md` §6's dependency direction. `Delegation`, the parsed form, lives there.

    `depth` is recorded for reading and never trusted: evaluation derives it by walking to the
    root (§5.5), so a row edited directly in the database cannot assert its way to a shorter
    chain.
    """

    delegation_id: str
    parent_id: str
    depth: int
    grant_json: str
    created_by_agent: str
    created_by_user: str | None
    created_via: str
    created_at: datetime
    revoked_at: datetime | None = None
    revoked_by: str | None = None

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None


#: SPEC-v0.7 §3.5. What caused a measurement: the store opening, or an expired lease being
#: declared `AMBIGUOUS` (`v0.1 §5.3 E3`). A closed set, like every vocabulary a reader parses.
_CLOCK_SKEW_TRIGGERS: Final = frozenset({"open", "lease_expired"})


@dataclass(frozen=True)
class ClockSkew:
    """One measurement of a store's clock against the application's (SPEC-v0.7 §3.4, §3.6).

    `skew` is the application's time at the midpoint of one round trip minus the store's reading,
    so a positive value means the application clock is **ahead**. `bound` is half that round
    trip: the store read its clock somewhere inside it, so the true offset lies within
    `skew ± bound`. `measured_at` is the application's midpoint and `trigger` is `"open"` or
    `"lease_expired"`.

    **It observes and decides nothing.** No lease is evaluated against it (§3.2): it is what a
    store with its own clock retains, as the optional `clock_skew` attribute, for `Control` to
    report as `CLOCK_SKEW_DETECTED`. `Control` reports only an instance of this class, so a store
    that exposes one constructs it; a look-alike with the same fields is ignored.

    Fields are checked at construction, so a measurement that is malformed fails where it was
    made rather than inside the `Control` that would report it.
    """

    skew: timedelta
    bound: timedelta
    threshold: timedelta
    measured_at: datetime
    trigger: str

    def __post_init__(self) -> None:
        for name in ("skew", "bound", "threshold"):
            if not isinstance(getattr(self, name), timedelta):
                raise InvalidArgument(f"ClockSkew.{name} must be a timedelta")
        if self.bound < timedelta(0):
            raise InvalidArgument("ClockSkew.bound is half a round trip and cannot be negative")
        if self.threshold <= timedelta(0):
            raise InvalidArgument("ClockSkew.threshold must be positive")
        if not isinstance(self.measured_at, datetime) or self.measured_at.utcoffset() is None:
            raise InvalidArgument("ClockSkew.measured_at must be a timezone-aware datetime")
        if self.trigger not in _CLOCK_SKEW_TRIGGERS:
            raise InvalidArgument(
                f"ClockSkew.trigger must be one of {sorted(_CLOCK_SKEW_TRIGGERS)}, "
                f"got {self.trigger!r}"
            )

    @property
    def exceeded(self) -> bool:
        """Past the threshold by more than the measurement's own uncertainty (§3.4).

        A slow link widens `bound` and raises the bar exactly as far as the doubt it added, so
        latency alone can never produce a report.
        """
        return abs(self.skew) > self.threshold + self.bound


# --- grading a measurement: G13 and the store conformance suite's clock case ------------------
#
# Both inject a skew and ask whether it was reported. A conforming store reports only past
# `threshold + bound`, so an injection sized without looking at the bound grades the link and
# not the store: a round trip slow enough that half of it exceeds the margin makes a correct
# store stay silent, and a fixed margin then reports that silence as a defect. One definition,
# so verify and the suite cannot come to disagree about when a silence is a finding.


def _decisive(injected: timedelta, measured: ClockSkew, alignment: timedelta) -> bool:
    """Must a store honest within `measured.bound` report a skew of `injected`?

    The clock was aligned by a first measurement whose own doubt is `alignment`, so the true
    skew is `injected` within `alignment`, and the store may read it anywhere within its bound
    of that. It reports only past `threshold + bound`. So only an injection past
    `threshold + 2 * bound + alignment` leaves a conforming store no room to stay silent.
    """
    return abs(injected) > measured.threshold + 2 * measured.bound + alignment


def _wider_margin(measured: ClockSkew, alignment: timedelta, base: timedelta) -> timedelta:
    """The margin past the threshold that would have been decisive against `measured`."""
    return 2 * measured.bound + alignment + base


def _explained_by_alignment(measured: ClockSkew, alignment: timedelta) -> bool:
    """Could this report on a clock meant to be aligned be the aligning measurement's error?

    Only where the measurement is past the threshold by more than its own bound, and by no more
    than the alignment's doubt beyond that. A report the store's own rule does not allow is a
    detector firing when it must not, and one past both bounds contradicts the measurement the
    alignment came from: both are findings, and neither is excused here.

    The rule is recomputed from the fields rather than read from `exceeded`, so a store whose
    `exceeded` always answers true is caught by the first branch instead of being excused by
    this one.
    """
    past = abs(measured.skew) - measured.threshold
    return measured.bound < past <= measured.bound + alignment


@dataclass(frozen=True)
class Charge:
    """What one reservation spends against one grant's budget (SPEC-v0.9 §3.3.1).

    **It carries the whole predicate**, not just the amount, and that is the decision §3.3.1
    argues: `limit` and `window` travel with the charge rather than being looked up, because a
    store that resolved a grant's budgets would be reading the policy, and `ARCHITECTURE.md` §6
    has `state.py` not knowing about `policy.py`. The store evaluates one arithmetic predicate it
    was handed, over rows it owns.
    """

    grant_id: str
    metric: str
    amount: int
    limit: int
    window: timedelta

    def __post_init__(self) -> None:
        """Refuse what the loader refuses, on `Budget.__post_init__`'s rule (§2.2).

        **A negative `amount` refunds the budget**: it unwinds the sum and the grant spends again,
        which is the compensation §12 puts out of scope, reachable by anyone who can call the
        store. §2.3 assigns the loader-side refusal to the metric value, and this is the
        defence in depth that rule cannot give a third-party caller reaching the `StateStore`
        directly. An independent review found the value object validating nothing while `Budget`
        beside it validates everything.
        """
        for name, value in (("amount", self.amount), ("limit", self.limit)):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise InvalidArgument(
                    f"charge {self.metric!r} on {self.grant_id!r}: {name!r} must be a "
                    f"non-negative integer, got {value!r} (SPEC-v0.9 §2.3)"
                )
        if not isinstance(self.window, timedelta) or self.window <= timedelta(0):
            raise InvalidArgument(
                f"charge {self.metric!r} on {self.grant_id!r}: 'window' must be positive, "
                f"got {self.window!r}"
            )


@dataclass(frozen=True)
class Consumption:
    """One ledger row, as `consumptions()` hands it back (SPEC-v0.9 §3.3.3, §10).

    `released_at` is `None` while the charge is held. Whether it is held **and why** is §7.2's
    question, and the answer is a join through `get_effect` on `effect_key` rather than a column
    here: the ledger deliberately has no state machine of its own (§4.1).
    """

    grant_id: str
    metric: str
    amount: int
    effect_key: str
    attempt: int
    consumed_at: datetime
    released_at: datetime | None = None


def check_charges(
    charges: tuple[Charge, ...],
    spent: Callable[[Charge], int],
) -> None:
    """SPEC-v0.9 §3.3.1's predicate, in one place so three backends cannot drift on it.

    `spent` is the store's own sum of un-released rows for this charge's `(grant_id, metric)`
    over `[now - window, now]`. The comparison is **inclusive**, matching §2.2's "what the sum may
    reach": a `limit: 0` grant therefore permits an action only if its metric value is 0, and
    §3.3.1 records that a zero limit does not stop a grant, since it permits unboundedly many
    zero-valued actions.

    Pure, like `plan_reservation` and for the same reason: every store decides here rather than
    each deciding for itself, so the arithmetic is one function a test can reach directly.

    **Every charge is evaluated, including several on one `(grant_id, metric)`.** That is §2.2's
    own motivating shape: a grant with two budgets on `amount`, 100,000 a day and 500,000 a month,
    is "the first thing an operator asks for", and it arrives here as two charges differing only
    in `limit` and `window`. Both predicates run; §3.4's key then writes **one** row, which is
    right, because it is one spend measured against two windows.

    **What is refused is two charges on one `(grant_id, metric)` carrying different amounts.**
    A charge is invisible to its sibling here (each is compared against the *stored* sum), and
    §3.4's key carries no window, so differing amounts would collapse to whichever row landed
    first and the ledger would under-record the spend. Nothing legitimate produces that: the
    amount comes from the action's own metric value, so two budgets on one metric always agree,
    and §2.7's per-ancestor charges are distinct grants.

    An earlier version refused **any** duplicate pair, which made §2.2's shape die at execute
    with no receipt: the loader accepted the document, observe mode reported it clean, and
    `ctrlrun verify` could not grade it. An independent review found it.
    """
    amounts: dict[tuple[str, str], int] = {}
    for charge in charges:
        key = (charge.grant_id, charge.metric)
        seen = amounts.setdefault(key, charge.amount)
        if seen != charge.amount:
            raise InvalidArgument(
                f"two charges on {charge.grant_id!r}/{charge.metric!r} in one reservation carry "
                f"different amounts ({seen} and {charge.amount}); §3.4's key would keep one row "
                "and the ledger would under-record the spend"
            )
    for charge in charges:
        if spent(charge) + charge.amount > charge.limit:
            raise BudgetExhaustedError(charge.grant_id, charge.metric, charge.window)


class BudgetExhaustedError(Exception):
    """The store's refusal when §3.3.1's predicate fails. Package-internal, never public.

    **Not a `CTRLRunError`, and §3.3.2 is why.** `Control` converts it to `ActionDenied` with the
    events and the receipt that refusal owes; a store raising `ActionDenied` itself would be
    minting evidence, which is `control.py`'s job. And it must not be an `ActionDenied` subclass:
    `_secure`'s handler for that type appends `APPROVAL_DENIED` unconditionally, which would
    fabricate an approval denial for an action no human ever saw. Item 2 met that hazard first
    with the scope refusal; this is the same handler.
    """

    def __init__(self, grant_id: str, metric: str, window: timedelta) -> None:
        super().__init__(f"budget {metric!r} on grant {grant_id!r} over {window} is exhausted")
        self.grant_id = grant_id
        self.metric = metric
        self.window = window


class StateStore(ApprovalStore, Protocol):
    """Durable state behind a `Control` (SPEC-v0.1 §5.3): approvals, effects, evidence."""

    def reserve_effect(
        self,
        effect_key: str,
        action_id: str,
        lease: timedelta = DEFAULT_LEASE,
        charges: tuple[Charge, ...] = (),
    ) -> Reservation:
        """Claim an effect key for one attempt. At most one caller wins (§5.3 E1).

        Refuses per the retry table of §5.4: `DuplicateEffect` for a committed effect or a
        live reservation, `AmbiguousEffect` for an unresolved or lease-expired one.

        **`charges` is SPEC-v0.9's amendment to this frozen protocol** (§3.3), and it is a MUST
        rather than a courtesy: a store that accepts charges **MUST** evaluate §3.3.1's predicate
        inside this same transaction and **MUST** raise `BudgetExhaustedError` when it fails.
        A store that cannot says so by refusing charges, never by accepting and ignoring them.

        The direction matters and is the reason this is a contract and not a convention. A store
        that wrote the rows and skipped the predicate would **silently disable every budget on the
        deployment**, and nothing downstream could tell. `ctrlrun.conformance`'s store suite has
        the case, and `Control` probes it at construction (§3.3): a store that does not refuse a
        `limit: 0` charge is one an operator cannot accidentally deploy with budgets configured.
        """
        ...

    def consume_approval_and_reserve(
        self,
        approval_id: str,
        action_hash: str,
        effect_key: str,
        action_id: str,
        lease: timedelta = DEFAULT_LEASE,
        charges: tuple[Charge, ...] = (),
    ) -> tuple[Approval, Reservation]:
        """Consume the approval and reserve the effect in one transaction (§4.2 A4).

        The approval is checked first, so its refusal is the one raised when both would
        apply (acceptance test T4). If the reservation is refused, nothing is consumed.

        `charges` carries the same MUST as `reserve_effect`'s, and the same transaction.
        """
        ...

    def consumptions(
        self,
        *,
        grant_id: str | None = None,
        metric: str | None = None,
        since: datetime | None = None,
        effect_key: str | None = None,
    ) -> tuple[Consumption, ...]:
        """Ledger rows, **in insertion order** (SPEC-v0.9 §3.3.3).

        Not "newest last": both durable backends order by the autoincrement id, and two hosts
        with ordinary clock skew, which `v0.7 §3` models and this store warns about at open,
        invert `consumed_at` against it. Deterministic and identical across the three backends,
        which is what a reader needs; it is simply not a time ordering.

        The **read half** of §3.3's amendment, and it clears `v0.6 §9.2`'s bar the way that
        section's own example did: a second backend implementing `charges=` and nothing else would
        satisfy every declared method and break `ctrlrun inspect` and `ctrlrun verify`. `v0.6
        §2.7.2` records exactly that finding for `events()` and `receipts()`.

        **`grant_id` is optional**, because §7.3 has `stats` report the ledger's row count, and a
        required one would make that enumerate every grant id that ever existed, runtime
        delegations and revoked grants included, one call each.

        **`effect_key` is what a resumed leg reads by.** §8.3 makes the resumed receipt the only
        receipt an MCP multi round-trip or ACS action ever gets, so it has to report what that
        action spent, and a gateway that restarted mid-round has nothing in memory to report it
        from. Without this filter that read is a scan of the whole ledger per resumption.
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
        self, action: Action, effect_key: str, continuation: str, until: datetime
    ) -> int:
        """Hold this reservation open across a round trip (SPEC-v0.2 §6.9.2).

        One transaction: the lease is extended to `until`, the continuation is stored, and
        the round counter is incremented. Refused unless the record is `EXECUTING`, held by
        this action, with a live lease — the conditions `extend_lease` requires, for the same
        reason. Returns the round number.

        The whole `Action` travels with it because a resumption is *the same action*, and
        rehydrating it from the store is the only way a gateway that restarted mid-round can
        still finish one.
        """
        ...

    def take_continuation(self, continuation: str) -> HeldContinuation:
        """Consume a held continuation, or refuse (SPEC-v0.2 §6.9.2).

        Compared with `hmac.compare_digest` and consumed in the same transaction that admits
        it, so one suspension admits exactly one resumption.
        """
        ...

    def continuation_rounds(self, effect_key: str) -> int:
        """How many times this effect has been suspended, or 0 (SPEC-v0.2 §6.9.2).

        A read, so a caller can enforce its own round bound: a held reservation is a resource
        an upstream must not be able to pin forever, and the lease bound alone only stops a
        client that stops answering, not an upstream that elicits forever.
        """
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

    def put_delegation(self, record: DelegationRecord) -> None:
        """Insert one delegation row (SPEC-v0.3 §5.2).

        A plain `INSERT` that raises on a duplicate id, and **never an upsert**: an upsert on
        an existing id would clear `revoked_at`, which is `unrevoke` by another door in a
        release that says there is no such thing (§5.7).
        """
        ...

    def get_delegation(self, delegation_id: str) -> DelegationRecord | None:
        """The row with this id, revoked or not, or `None`."""
        ...

    def delegations_for(self, parent_id: str) -> tuple[DelegationRecord, ...]:
        """Every row naming this parent, oldest first."""
        ...

    def delegations(self, *, include_revoked: bool = False) -> tuple[DelegationRecord, ...]:
        """Every delegation row, oldest first (SPEC-v0.3 §11).

        Evaluation enumerates these because §5.6 walks **upward** from a delegation to its
        root: a chain whose root grant has been deleted from the document cannot be found by
        walking downward from the ids that are still in it.
        """
        ...

    def revoke_delegation(self, delegation_id: str, *, by: str | None, at: datetime) -> bool:
        """Revoke one delegation, atomically. `False` if it was already revoked (§5.7).

        The read-then-write happens inside a `BEGIN IMMEDIATE`, as every other read-then-write
        in this store does (`v0.1 §5.3 E1`), so two concurrent revokes append one
        `DELEGATION_REVOKED` between them rather than one each. An unknown id is an
        `InvalidArgument`: there is nothing to revoke, and returning `False` would make that
        indistinguishable from the idempotent case.
        """
        ...

    def append_event(self, event: Event) -> Event:
        """Append an event, assigning it the next `event_id`, and return it as stored.

        The stored event is handed back because `Control` fans it out to its `EventSink`s
        with the id this store assigned (SPEC-v0.2 §4.1). A sink that could not join its
        export back to the record would be exporting something else.
        """
        ...

    def put_receipt(self, receipt: Receipt) -> Receipt:
        """Record a receipt, and return it with its place in the chain (SPEC-v0.6 §6.2, §6.3).

        The returned receipt carries `seq`, `prev_hash` and `hash`, which the store assigns in
        the transaction that writes the row. **The return value is the signature change v0.6
        makes here**, and it is `append_event`'s exactly: that has returned its stored record
        with the `event_id` the store assigned since v0.2, for the same reason. Without it the
        caller hands sinks a document with no `seq`, and §6.4's claim that the JSONL export is
        verifiable by recomputation rests on every document carrying its own place.
        """
        ...

    def chain_head(self) -> tuple[int, str] | None:
        """The receipt chain's head: `(seq, hash)`, or `None` where there is no head row.

        SPEC-v0.6 §6.3, and **the one store method this milestone adds**. §9.1's earlier
        "no new store method" did not survive §6.3: the head exists precisely to detect a
        truncation at the *end*, where deleting the last N receipts leaves a chain that is
        internally consistent, and only a head that still names a `seq` and a `hash` no row
        carries catches it. A reader that cannot see the head cannot report `head_mismatch`,
        so §6.5's fourth name would be unimplementable and the guarantee would be one the
        document claims and the code cannot make. §9.2 records the amendment.

        It adds no capability: the head is a row this same store already writes in
        `put_receipt`, and nothing else may change it.
        """
        ...

    def put_anchor(self, anchor: Anchor) -> None:
        """Cache one anchor (SPEC-v0.11 §3.3). **Amends SPEC-v0.6 §9.2's frozen protocol.**

        §9.2's bar for a new method is *a second backend could not be written without it*, and it
        is cleared: an anchor's local cache cannot be reconstructed from the tables that exist.
        No receipt carries a token, and the point of the cache is to hold what the provider
        answered, which nothing else in this store has ever seen.
        """
        ...

    def anchors(self) -> tuple[Anchor, ...]:
        """Every cached anchor, oldest `seq` first (SPEC-v0.11 §3.3).

        **A cache and not a record.** `verify_anchors` asks the provider what it holds before it
        reads this, so a store whose anchors table was emptied verifies exactly as one that never
        anchored: `anchor_missing`, which is a break.
        """
        ...

    def checkpoint(self) -> tuple[int, str] | None:
        """The `seq` a prune pruned through and the hash at it, or `None` (SPEC-v0.11 §4.2).

        The **read** ships with item 2 because §4.6's supersession rule is part of what
        `anchor_broken` means; `put_checkpoint` ships with item 3, which is what writes one.
        """
        ...

    def put_checkpoint(self, checkpoint: Checkpoint) -> None:
        """Record what a prune pruned through (SPEC-v0.11 §4.2). **Forward only** (§4.5).

        **Amends SPEC-v0.6 §9.2's frozen protocol**, and clears its bar: §4.2 is why a checkpoint
        the walk trusts cannot live in a receipt document, so a second backend could not
        implement retention without a table of its own.
        """
        ...

    def put_hold(self, hold: Hold) -> None:
        """Place a hold over a range of receipts (SPEC-v0.11 §4.3)."""
        ...

    def holds(self) -> tuple[Hold, ...]:
        """Every hold, live or released, oldest range first."""
        ...

    def release_hold(self, hold_id: str, *, by: str, at: datetime) -> None:
        """End a hold. **A person ends it, never a timer** (§4.3).

        `SPEC-v0.9 §4`'s rule that an automatic expiry on a hold is the refund rule in a costume
        applies unchanged. There is no sweeper and there is not going to be one.
        """
        ...

    def pruning(self) -> AbstractContextManager[None]:
        """Hold the receipt-write lock for the whole of a prune (SPEC-v0.11 §4.5).

        **Across the validation and the delete.** Two prunes that both validated and then both
        acted would each be individually valid under §10 and together break rule 2, which is the
        case §4.5 measured.
        """
        ...

    def delete_prefix(self, through: int, effect_keys: Sequence[str]) -> tuple[int, int]:
        """Delete receipts through `seq` and the ledger rows named, inside `pruning()`.

        **Every refusal has already run.** This is the half that destroys and it decides nothing:
        `retention.prune` is where rule 2, the holds and §4.4's table are checked.
        """
        ...

    def events(self) -> tuple[Event, ...]:
        """Every event, oldest first (SPEC-v0.6 §9.2).

        Declared on the protocol in v0.6, having been *called* since v0.2 and *implemented* by
        both shipped stores since v0.1. The store conformance suite found it: a third backend
        written against this protocol alone would omit it, and `ctrlrun inspect`,
        `ctrlrun verify` and the adapter conformance kit would each break on a store that
        satisfied every declared method.

        It adds no behaviour -- both stores already return exactly this -- and it clears
        SPEC-v0.6 §9.2's bar in the plainest way available: a second backend genuinely could
        not be written without it.
        """
        ...

    def receipts(self) -> tuple[Receipt | UnreadableReceipt, ...]:
        """Every receipt, oldest first (SPEC-v0.6 §9.2).

        Declared on the protocol in v0.6, for `events()`'s reason and one of its own: the
        receipt chain reader (SPEC-v0.6 §6.5) enumerates receipts to verify it, and
        `ctrlrun receipts --verify-chain` runs against whatever backend the operator has.

        **SPEC-v0.11 §5.2 amends this signature**, and it is an amendment to SPEC-v0.6 §9.2's
        frozen protocol rather than an addition. A row this store cannot construct comes back as
        an `UnreadableReceipt` naming its `seq`; it does not raise. Before v0.11 it raised, and
        because both backends build every row before any caller sees one, a single malformed
        value took out five readers together (SPEC-v0.11 §2.3).

        SPEC-v0.6 §9.2's bar for touching this protocol is *a second backend could not be
        written without it*, and it is cleared: a backend that raised on one bad row could not
        implement §5 at all.
        """
        ...

    def close(self) -> None:
        """Release whatever this store holds open."""
        ...


def _roles_json(roles: tuple[RequiredRole, ...]) -> str | None:
    """The roles a request pinned, as canonical JSON, or `None` where it pinned none."""
    if not roles:
        return None
    return json.dumps([role.to_dict() for role in roles], sort_keys=True)


def _roles_from_json(text: str | None) -> tuple[RequiredRole, ...]:
    """What the column holds, or `()`. A corrupted column raises, for `_approvers_from_json`'s
    reason: this is authority about to be spent, not evidence a reader walks past."""
    if not text:
        return ()
    try:
        return tuple(RequiredRole.from_dict(item) for item in json.loads(text))
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise InvalidArgument(
            f"the approvals row carries an unreadable 'required_roles' column: {exc}"
        ) from exc


def _approvers_json(approvers: tuple[VerifiedApprover, ...]) -> str | None:
    """The verified approvers as one canonical JSON array, or `None` where there are none.

    `None` and not `"[]"`: a row granted by a surface that resolved nobody and a row granted
    before the column existed are the same thing to a reader, and both are what `NULL` means
    (SPEC-v0.8 §2.5).
    """
    if not approvers:
        return None
    return json.dumps([approver.to_dict() for approver in approvers], sort_keys=True)


def _approvers_from_json(text: str | None) -> tuple[VerifiedApprover, ...]:
    """What the column holds, or `()`. A column a store dropped reads as no approver at all,
    which `Control` refuses at consumption rather than skipping (SPEC-v0.8 §2.5).

    **A corrupted column is a `CTRLRunError` and not a `JSONDecodeError`.** This read sits under
    `get_approval`, which sits under `_recheck`, which sits under `execute`: a raw decoding error
    from a tampered row would reach an agent as an exception no caller catches and no receipt
    records. Fail closed, named, and traceable to the row.

    It does **not** get `Receipt.from_dict`'s never-raises treatment, and the difference is
    deliberate: a receipt is evidence a reader walks past, so one bad row must not blind every
    reader, while an approval is authority about to be spent, so one bad row must stop this
    action rather than be read as "no approver" (§2.5, `v0.7 §6.11`).
    """
    if not text:
        return ()
    try:
        return tuple(VerifiedApprover.from_dict(item) for item in json.loads(text))
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise InvalidArgument(
            f"the approvals row carries an unreadable 'approvers' column: {exc}"
        ) from exc


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
        #: SPEC-v0.9 §3.2's table, in a list. Append-only until a release sets `released_at`,
        #: which is a replacement here and a compare-and-set on the durable stores (§4.4).
        self._ledger: list[Consumption] = []
        self._events: list[Event] = []
        self._receipts: list[Receipt] = []
        #: SPEC-v0.11 §3.3's cache, in memory. This backend's `reopen()` is `None`: it declares
        #: that its storage does not outlive the object, so an anchor cached here is gone with
        #: the process, exactly as every other row in it is.
        self._anchors: list[Anchor] = []
        self._checkpoint: tuple[int, str] | None = None
        self._holds: list[Hold] = []
        #: The chain head (§6.3), starting where `0002_receipt_chain` starts it: seq 0
        #: carrying the genesis hash, so an empty store is a chain of length zero rather
        #: than a truncated one.
        self._chain_seq = 0
        self._chain_hash = GENESIS_HASH
        self._approvals: dict[str, ApprovalRecord] = {}
        self._effects: dict[str, EffectRecord] = {}
        self._continuations: dict[str, tuple[str, Action, int]] = {}
        self._delegations: dict[str, DelegationRecord] = {}

    def close(self) -> None:
        """Nothing to release; here so either store can be closed the same way."""

    # --- evidence ---------------------------------------------------------------------

    def append_event(self, event: Event) -> Event:
        with self._lock:
            stored = replace(event, event_id=len(self._events) + 1)
            self._events.append(stored)
        return stored

    def put_receipt(self, receipt: Receipt) -> Receipt:
        with self._lock:
            # The same protocol as SQLite's (§6.3): take the head, number the receipt, link it,
            # write the head back. The lock here is the object's, which is all a store confined
            # to one process can offer -- and `reopen()` returning `None` is how this backend
            # declares that (§2.4).
            seq = self._chain_seq + 1
            # SPEC-v0.7 §6.11: written under the schema this binary writes, and hashed as the
            # dictionary that says so. The same rule as SQLite's and Postgres's, below.
            chained = replace(receipt, schema=RECEIPT_SCHEMA, seq=seq, prev_hash=self._chain_hash)
            digest = _document_hash(chained.to_dict())
            stored = replace(chained, hash=digest)
            self._receipts.append(stored)
            self._chain_seq = seq
            self._chain_hash = digest
            return stored

    def chain_head(self) -> tuple[int, str] | None:
        with self._lock:
            return (self._chain_seq, self._chain_hash)

    def put_anchor(self, anchor: Anchor) -> None:
        with self._lock:
            if all(held.token != anchor.token for held in self._anchors):
                self._anchors.append(anchor)

    def anchors(self) -> tuple[Anchor, ...]:
        with self._lock:
            return tuple(sorted(self._anchors, key=lambda item: (item.seq, item.kind, item.token)))

    def checkpoint(self) -> tuple[int, str] | None:
        with self._lock:
            return self._checkpoint

    def put_checkpoint(self, checkpoint: Checkpoint) -> None:
        with self._lock:
            if self._checkpoint is None or checkpoint.seq > self._checkpoint[0]:
                self._checkpoint = (checkpoint.seq, checkpoint.hash)

    def put_hold(self, hold: Hold) -> None:
        with self._lock:
            if any(held.hold_id == hold.hold_id for held in self._holds):
                raise InvalidArgument(f"hold {hold.hold_id!r} already exists")
            self._holds.append(hold)

    def holds(self) -> tuple[Hold, ...]:
        with self._lock:
            return tuple(sorted(self._holds, key=lambda item: (item.from_seq, item.hold_id)))

    def release_hold(self, hold_id: str, *, by: str, at: datetime) -> None:
        with self._lock:
            for index, held in enumerate(self._holds):
                if held.hold_id == hold_id and held.live:
                    self._holds[index] = replace(held, released_at=at, released_by=by)
                    return
        raise InvalidArgument(f"no live hold {hold_id!r} in this store")

    @contextmanager
    def pruning(self) -> Iterator[None]:
        """One lock is all a store confined to one process can offer, and `reopen()` returning
        `None` is how this backend declares that (§2.4)."""
        with self._lock:
            yield

    def delete_prefix(self, through: int, effect_keys: Sequence[str]) -> tuple[int, int]:
        """The in-memory half. The caller already holds `pruning()`'s lock."""
        if True:
            kept = [item for item in self._receipts if item.seq is None or item.seq > through]
            receipts = len(self._receipts) - len(kept)
            self._receipts = kept
            wanted = set(effect_keys)
            rows = sum(1 for row in self._ledger if row.effect_key in wanted)
            self._ledger = [row for row in self._ledger if row.effect_key not in wanted]
        return (receipts, rows)

    def events(self) -> tuple[Event, ...]:
        """An immutable snapshot of the event log, in append order."""
        with self._lock:
            return tuple(self._events)

    def receipts(self) -> tuple[Receipt, ...]:
        """An immutable snapshot of the receipts, in write order."""
        with self._lock:
            return tuple(self._receipts)

    # --- delegations (SPEC-v0.3 §5.2) -------------------------------------------------

    def put_delegation(self, record: DelegationRecord) -> None:
        with self._lock:
            if record.delegation_id in self._delegations:
                raise InvalidArgument(f"delegation {record.delegation_id} already exists")
            self._delegations[record.delegation_id] = record

    def get_delegation(self, delegation_id: str) -> DelegationRecord | None:
        with self._lock:
            return self._delegations.get(delegation_id)

    def delegations_for(self, parent_id: str) -> tuple[DelegationRecord, ...]:
        with self._lock:
            return tuple(
                record for record in self._delegations.values() if record.parent_id == parent_id
            )

    def delegations(self, *, include_revoked: bool = False) -> tuple[DelegationRecord, ...]:
        with self._lock:
            return tuple(
                record
                for record in self._delegations.values()
                if include_revoked or not record.is_revoked
            )

    def revoke_delegation(self, delegation_id: str, *, by: str | None, at: datetime) -> bool:
        with self._lock:
            record = self._delegations.get(delegation_id)
            if record is None:
                raise InvalidArgument(f"no delegation {delegation_id}")
            if record.is_revoked:
                return False
            self._delegations[delegation_id] = replace(record, revoked_at=at, revoked_by=by)
            return True

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

    def grant_approval(self, approval_id: str, approver: str) -> Approval | None:
        approver = _approver(approver)
        with self._lock:
            record = self._answerable(approval_id)
            now = self._clock()
            # SPEC-v0.8 §2.5: whatever the granting surface verified, or nothing where it
            # verified nobody. A store that did not read this records no approver, and that is
            # refused at consumption rather than skipped.
            #
            # SPEC-v0.8 §4.2: and `count_grant` decides whether this grant reaches the
            # threshold, in the one implementation all three stores apply.
            verified = _verified_approver_now(now)
            approvers, reached = count_grant(record, verified, now)
            granted = replace(
                record,
                status=ApprovalStatus.GRANTED if reached else record.status,
                approver=approver if reached else record.approver,
                granted_at=now if reached else record.granted_at,
                approvers=approvers,
            )
            self._approvals[approval_id] = granted
            return granted.as_approval() if reached else None

    def deny_approval(self, approval_id: str, approver: str) -> None:
        approver = _approver(approver)
        with self._lock:
            record = self._answerable(approval_id)
            now = self._clock()
            verified = _verified_approver_now(now)
            self._approvals[approval_id] = replace(
                record,
                status=ApprovalStatus.DENIED,
                approver=approver,
                approvers=(*record.approvers, verified) if verified else record.approvers,
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
        self,
        effect_key: str,
        action_id: str,
        lease: timedelta = DEFAULT_LEASE,
        charges: tuple[Charge, ...] = (),
    ) -> Reservation:
        _, reservation = self._authorize_and_reserve(
            None, None, effect_key, action_id, lease, charges=charges
        )
        return _only(reservation, "reservation")

    def consume_approval_and_reserve(
        self,
        approval_id: str,
        action_hash: str,
        effect_key: str,
        action_id: str,
        lease: timedelta = DEFAULT_LEASE,
        charges: tuple[Charge, ...] = (),
    ) -> tuple[Approval, Reservation]:
        approval, reservation = self._authorize_and_reserve(
            approval_id, action_hash, effect_key, action_id, lease, charges=charges
        )
        return _only(approval, "approval"), _only(reservation, "reservation")

    def _authorize_and_reserve(
        self,
        approval_id: str | None,
        action_hash: str | None,
        effect_key: str | None,
        action_id: str | None,
        lease: timedelta,
        charges: tuple[Charge, ...] = (),
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
            # SPEC-v0.9 §3.3.1 — **decided before anything is written, inside the same lock**.
            # `check_charges` raises `BudgetExhaustedError` and nothing above has been written,
            # so a refused budget leaves the approval granted and the effect unreserved, which is
            # the same shape §4.2 A4 already gives a refused reservation.
            if charges:
                check_charges(charges, lambda charge: self._spent(charge, now))
            if plan.reservation is not None:
                self._reserve_locked(plan.reservation, plan.renews, now)
            if approved is not None:
                self._consume_locked(approved.approval_id, now)
            if charges and plan.reservation is not None:
                self._charge_locked(charges, effect_key, plan.reservation.attempt, now)
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

    def _spent(self, charge: Charge, now: datetime) -> int:
        """The un-released sum for this charge's grant and metric, over its rolling window."""
        floor = now - charge.window
        return sum(
            row.amount
            for row in self._ledger
            if row.grant_id == charge.grant_id
            and row.metric == charge.metric
            and row.released_at is None
            # SPEC-v0.9 §2.5 — `[now - window, now]`, **closed at the floor**. An independent
            # review found all three backends half-open here while `consumptions(since=)` was
            # closed, so `inspect --since` would have shown a row the predicate excluded.
            and row.consumed_at >= floor
        )

    def _charge_locked(
        self, charges: tuple[Charge, ...], effect_key: str | None, attempt: int, now: datetime
    ) -> None:
        """Write one row per charge. Idempotent on `(effect_key, attempt, grant_id, metric)`.

        SPEC-v0.9 §3.4: `v0.6 §4.3.2` Table A1 row 2 retries a lost insert once, and an
        unconstrained append would double-charge there. The in-memory store has no unique index,
        so it enforces the same key by hand rather than being the one backend that does not.
        """
        for charge in charges:
            key = (effect_key, attempt, charge.grant_id, charge.metric)
            if any(
                (row.effect_key, row.attempt, row.grant_id, row.metric) == key
                for row in self._ledger
            ):
                continue
            self._ledger.append(
                Consumption(
                    grant_id=charge.grant_id,
                    metric=charge.metric,
                    amount=charge.amount,
                    effect_key=str(effect_key),
                    attempt=attempt,
                    consumed_at=now,
                )
            )

    def _release_locked(self, effect_key: str, state: EffectState, now: datetime) -> None:
        """SPEC-v0.9 §4.1: **released exactly when the effect reaches `FAILED`**, held otherwise.

        The ledger has no state machine of its own. `effect.py`'s `plan_reservation` is already
        the complete table of exits from a reservation, and this one rule covers every row of
        §4.2's nineteen: `COMMITTED` holds permanently, `AMBIGUOUS` holds until a human or a hook
        moves it, a lapsed lease holds because no transition has occurred, and only `FAILED`
        releases, because that is the one state in which the executor proved nothing happened.

        **Keyed on the state reached, never on the call that reached it** (§4.2's warning): a
        `fail_effect` that is *refused* because the record moved on releases nothing, and a
        `resolve_effect(FAILED)` by a human releases even though no `fail_effect` ran.

        A compare-and-set on `released_at`, never a decrement (§4.4): `v0.6 §4.3.2` Table A2 row 2
        re-issues a lost `UPDATE` once, and a decrement would subtract twice.
        """
        if state is not EffectState.FAILED:
            return
        self._ledger = [
            replace(row, released_at=now)
            if row.effect_key == effect_key and row.released_at is None
            else row
            for row in self._ledger
        ]

    def consumptions(
        self,
        *,
        grant_id: str | None = None,
        metric: str | None = None,
        since: datetime | None = None,
        effect_key: str | None = None,
    ) -> tuple[Consumption, ...]:
        with self._lock:
            return tuple(
                row
                for row in self._ledger
                if (grant_id is None or row.grant_id == grant_id)
                and (metric is None or row.metric == metric)
                and (since is None or row.consumed_at >= since)
                and (effect_key is None or row.effect_key == effect_key)
            )

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
            now = self._clock()
            record = _resolvable(self._effects.get(effect_key), effect_key, state)
            resolved = _resolved(record, state, resolver, now)
            self._effects[effect_key] = resolved
            # SPEC-v0.9 §4.1, §4.2's `resolve_effect(FAILED)` row. **This path does not go
            # through `_transition`**, so the release has to be here too: a human resolving an
            # `AMBIGUOUS` record `FAILED` is exactly the authority R2 says releases a hold, and
            # without this the charge would be held for ever by the one act meant to free it.
            self._release_locked(effect_key, state, now)
            return resolved

    def extend_lease(self, effect_key: str, action_id: str, until: datetime) -> None:
        with self._lock:
            extended = plan_lease_extension(
                self._effects.get(effect_key), effect_key, action_id, until, self._clock()
            )
            self._effects[effect_key] = extended

    def hold_continuation(
        self, action: Action, effect_key: str, continuation: str, until: datetime
    ) -> int:
        with self._lock:
            extended = plan_lease_extension(
                self._effects.get(effect_key), effect_key, action.action_id, until, self._clock()
            )
            self._reject_duplicate_continuation(effect_key, continuation)
            _, _, rounds = self._continuations.get(effect_key, ("", action, 0))
            self._effects[effect_key] = extended
            self._continuations[effect_key] = (continuation, action, rounds + 1)
            return rounds + 1

    def take_continuation(self, continuation: str) -> HeldContinuation:
        with self._lock:
            for effect_key, (held, action, rounds) in self._continuations.items():
                if held and hmac.compare_digest(held, continuation):
                    record = _continuable(self._effects.get(effect_key), effect_key, self._clock())
                    self._continuations[effect_key] = ("", action, rounds)
                    return HeldContinuation(action, effect_key, record, rounds)
        raise InvalidArgument(_NO_SUCH_CONTINUATION)

    def continuation_rounds(self, effect_key: str) -> int:
        with self._lock:
            held = self._continuations.get(effect_key)
        return 0 if held is None else held[2]

    def _reject_duplicate_continuation(self, effect_key: str, continuation: str) -> None:
        for other, (held, _, _) in self._continuations.items():
            if other != effect_key and held and hmac.compare_digest(held, continuation):
                raise InvalidArgument(
                    f"continuation is already held for {other!r}; two effects cannot share one"
                )

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
            # SPEC-v0.9 §4.1. **This order is the atomicity**, and unlike the SQL stores there is
            # no rollback to fall back on: `_checked` raising is what must leave the ledger
            # untouched. Moving the release above it keys it on the *call* rather than the state
            # reached, and a refused `fail_effect` then releases the hold on an `AMBIGUOUS`
            # record, which is a manufacturable refund. T425 pins it.
            self._release_locked(effect_key, state, now)


class _HeldConnection:
    """One thread's SQLite connection, owned by an object that thread's locals hold.

    `SQLiteStateStore` keeps only a **weak** reference to this, so when the owning thread ends
    and CPython drops its thread-local values, the holder is collected and the finalizer closes
    the connection. Before this, the registry held connections strongly and only `close()` --
    a documented whole-store shutdown -- ever released one, so a host whose threads come and go
    accumulated two file descriptors per thread for the life of the process, and eventually
    failed every store access with "unable to open database file" until it was restarted.
    `ctrlrun gateway` is that host: a `ThreadingHTTPServer` with one thread per TCP connection.

    The connection is reached through the holder rather than kept directly in the thread-local
    because it is the *holder's* lifetime the registry has to observe: a weak reference to a
    `sqlite3.Connection` is not supported, and the thread-local is what the interpreter clears
    on thread exit.
    """

    __slots__ = ("__weakref__", "_finalizer", "connection")

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self._finalizer = weakref.finalize(self, connection.close)

    def close(self) -> None:
        """Close the connection now. Idempotent: `weakref.finalize` runs at most once."""
        self._finalizer()


class SQLiteStateStore:
    """Approvals, effects and evidence in one SQLite file (ARCHITECTURE §5).

    This is the store that makes reservation atomic across processes (SPEC-v0.1 §5.3 E1):
    every decision is taken inside `BEGIN IMMEDIATE`, which holds the database's write lock,
    and `effects.effect_key` is a primary key — the `UNIQUE(effect_key)` constraint — so an
    insert that races past the lock still fails rather than overwriting a reservation.

    A connection is opened per thread; `sqlite3` connections are not shareable. Separate
    processes open the same file, which is the point.
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
        _require_sqlite()
        self._path = Path(text)
        self._clock = clock
        self._local = threading.local()
        self._open: weakref.WeakSet[_HeldConnection] = weakref.WeakSet()
        self._open_lock = threading.Lock()
        #: True while `pruning()` holds `BEGIN IMMEDIATE`. Inner writes must not commit through
        #: it: `with connection:` commits, and committing there releases the receipt-write lock
        #: in the middle of a prune (SPEC-v0.11 §4.5).
        self._pruning = False
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # SPEC-v0.6 §3. The store's admission check: classify, then migrate or refuse. It runs
        # before any other table is read, and there is no argument, keyword or environment
        # variable that suppresses it (§3.6).
        try:
            migrate(self._connection(), self._clock())
        except sqlite3.DatabaseError as exc:
            # `sqlite3.DatabaseError: file is not a database` names neither the path nor the
            # remedy, and is not a `CTRLRunError` -- so neither the CLI's error contract nor an
            # application catching the kernel's own errors caught it. `$CTRLRUN_STATE` pointing
            # at the wrong file is an ordinary misconfiguration and deserves an ordinary
            # refusal.
            raise InvalidArgument(
                f"{str(self._path)!r} is not a ctrlrun state database: {exc}. Point "
                "$CTRLRUN_STATE or --store-url at the database your agents write, or let "
                "the agent process create one."
            ) from exc

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
            held, self._open = list(self._open), weakref.WeakSet()
        for holder in held:
            holder.close()

    def _connection(self) -> sqlite3.Connection:
        held: _HeldConnection | None = getattr(self._local, "held", None)
        if held is not None:
            with self._open_lock:
                if held in self._open:
                    return held.connection
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
        # `busy_timeout` before the WAL switch, and this ordering is **subsumed** by
        # `_enable_wal`'s own bounded retry: a mutation that swaps the two lines stays green,
        # because the retry covers what the ordering was for. It is kept because it is free and
        # because the *first* pragma read wants a timeout too, and it is recorded as subsumed
        # rather than reported as a load-bearing guard -- a check nothing exercises is
        # documentation, and a mutation table that called this one closed would be lying.
        connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        _enable_wal(connection)
        connection.execute("PRAGMA synchronous=NORMAL")
        held = _HeldConnection(connection)
        # The thread-local is the only strong reference: the registry below is weak, so the
        # interpreter dropping this on thread exit is what closes the connection.
        self._local.held = held
        with self._open_lock:
            self._open.add(held)
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
                # An event carries the same executor text the effect row does, so it needs
                # the same guard: evidence about an action must never decide its fate.
                _storable(json.dumps(dict(event.data), ensure_ascii=False, separators=(",", ":"))),
            ),
        )
        return replace(event, event_id=cursor.lastrowid)

    def put_receipt(self, receipt: Receipt) -> Receipt:
        """Insert the receipt and advance the chain head, in one transaction (SPEC-v0.6 §6.3).

        **The head row is taken as a lock, not compare-and-set**, and the difference is dropped
        receipts. The `UPDATE` is unconditional, so a concurrent writer *blocks* on the row rather
        than losing a race. A draft used `WHERE seq = ?` retried once on zero -- a bound imported
        from §4.2, where its justification is that a second zero would mean a record vanished,
        *"which nothing in this protocol can do"*. That argument does not transfer to a row
        **every** writer updates: a second zero there means a third concurrent writer, which is
        ordinary. Combined with `put_receipt` raising rather than swallowing, three concurrent
        actions would have silently dropped a receipt in the one place §6.3 identifies as the
        first in the kernel where two unrelated actions contend.

        The cost is stated rather than hidden: **every receipt write now serializes on one row**,
        and the lock is held across the insert.
        """
        connection = self._connection()
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                "UPDATE receipt_chain SET seq = seq + 1 WHERE id = 1 RETURNING seq, hash"
            ).fetchone()
            if row is None:
                raise InvalidArgument(
                    "the receipt chain has no head row; this database predates "
                    "0002_receipt_chain and was not migrated"
                )
            # SPEC-v0.7 §6.11 rule (b): **hash the exact dictionary that is serialized**, and
            # never a stored document. The column and the JSON beside it come from one
            # dictionary, so the read-time hash of that JSON is this write-time hash for every
            # receipt nobody touched. Written under `RECEIPT_SCHEMA` whatever schema the receipt
            # was read under: the chain fields exist only from `v3`, and a `v1` document written
            # into the chain would carry no `seq` of its own.
            chained = replace(
                receipt,
                schema=RECEIPT_SCHEMA,
                seq=int(row["seq"]),
                prev_hash=str(row["hash"]),
            )
            document = chained.to_dict()
            digest = _document_hash(document)
            connection.execute(
                "INSERT INTO receipts(receipt_id, action_id, effect_key, result, json, ts, "
                "seq, prev_hash, hash) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    chained.receipt_id,
                    chained.action_id,
                    chained.effect_key,
                    str(chained.result),
                    json.dumps(document, ensure_ascii=False, separators=(",", ":")),
                    _iso(chained.finished_at),
                    chained.seq,
                    chained.prev_hash,
                    digest,
                ),
            )
            connection.execute("UPDATE receipt_chain SET hash = ? WHERE id = 1", (digest,))
        except BaseException:
            connection.rollback()
            raise
        connection.commit()
        return replace(chained, hash=digest)

    def chain_head(self) -> tuple[int, str] | None:
        row = (
            self._connection()
            .execute("SELECT seq, hash FROM receipt_chain WHERE id = 1")
            .fetchone()
        )
        return None if row is None else (int(row["seq"]), str(row["hash"]))

    # --- anchors (SPEC-v0.11 §3.3) ----------------------------------------------------

    @contextmanager
    def _writing(self) -> Iterator[Any]:
        """The connection, committed on exit **unless a prune holds the transaction** (§4.5).

        `with connection:` commits, which is right for a standalone write and wrong for one
        inside `pruning()`: committing there releases the receipt-write lock in the middle of a
        prune. Every write that a prune calls goes through here instead.
        """
        connection = self._connection()
        if self._pruning:
            yield connection
            return
        with connection:
            yield connection

    def put_anchor(self, anchor: Anchor) -> None:
        """Cache one anchor the provider made. **A cache, never the record** (§3.3).

        The record is the operator's provider, outside this store, and that is the whole of what
        makes an anchor worth anything: `verify_anchors` asks the provider what it holds *before*
        it reads this table, so a row deleted from here is checked anyway.

        Keyed on `token`: a `seq` can carry both an `interval` and a `checkpoint` anchor, because
        §3.2 orders the two kinds separately, and the token is the one value a provider promises
        to recognise again.
        """
        with self._writing() as connection:
            connection.execute(
                "INSERT INTO anchors (token, seq, hash, kind, at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(token) DO NOTHING",
                (anchor.token, anchor.seq, anchor.hash, anchor.kind, anchor.at.isoformat()),
            )

    def anchors(self) -> tuple[Anchor, ...]:
        rows = (
            self._connection()
            .execute("SELECT token, seq, hash, kind, at FROM anchors ORDER BY seq, kind, token")
            .fetchall()
        )
        return tuple(
            Anchor(
                seq=int(row["seq"]),
                hash=str(row["hash"]),
                token=str(row["token"]),
                kind=str(row["kind"]),
                at=datetime.fromisoformat(row["at"]),
            )
            for row in rows
        )

    def checkpoint(self) -> tuple[int, str] | None:
        """The `seq` a prune pruned through and the chain hash at it (SPEC-v0.11 §4.2).

        **Read here in item 2 and written by item 3.** §4.6's rule is part of what
        `anchor_broken` *means*, not an addition to it: an anchored `seq` below a checkpoint that
        is itself anchored is **superseded**, not broken. An anchor shipped without that clause
        would report every anchor older than the retention window as tampering, forever, on any
        deployment that ever prunes, and §3.4's definition would be wider than its code.
        """
        row = (
            self._connection()
            .execute("SELECT seq, hash FROM prune_checkpoint WHERE id = 1")
            .fetchone()
        )
        return None if row is None else (int(row["seq"]), str(row["hash"]))

    # --- retention (SPEC-v0.11 §4) ----------------------------------------------------

    def put_checkpoint(self, checkpoint: Checkpoint) -> None:
        """Record what a prune pruned through. **Forward only** (§4.5).

        The `WHERE` clause is the refusal, in SQL rather than only in `retention.prune`: two
        racing prunes are each individually valid under §10, and the second overwriting the
        first's row is what a review measured leaving `[('missing', 4), ('link_broken', 6)]`.
        """
        with self._writing() as connection:
            connection.execute(
                "INSERT INTO prune_checkpoint (id, seq, hash, schema, at) VALUES (1, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET seq = excluded.seq, hash = excluded.hash, "
                "schema = excluded.schema, at = excluded.at WHERE excluded.seq > seq",
                (checkpoint.seq, checkpoint.hash, checkpoint.schema, checkpoint.at.isoformat()),
            )

    def put_hold(self, hold: Hold) -> None:
        """Place a hold over a range of receipts (SPEC-v0.11 §4.3)."""
        connection = self._connection()
        with connection:
            connection.execute(
                "INSERT INTO holds (hold_id, from_seq, to_seq, reason, placed_by, placed_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    hold.hold_id,
                    hold.from_seq,
                    hold.to_seq,
                    hold.reason,
                    hold.placed_by,
                    hold.placed_at.isoformat(),
                ),
            )

    def holds(self) -> tuple[Hold, ...]:
        rows = (
            self._connection()
            .execute(
                "SELECT hold_id, from_seq, to_seq, reason, placed_by, placed_at, released_at, "
                "released_by FROM holds ORDER BY from_seq, hold_id"
            )
            .fetchall()
        )
        return tuple(
            Hold(
                hold_id=str(row["hold_id"]),
                from_seq=int(row["from_seq"]),
                to_seq=None if row["to_seq"] is None else int(row["to_seq"]),
                reason=str(row["reason"]),
                placed_by=str(row["placed_by"]),
                placed_at=datetime.fromisoformat(row["placed_at"]),
                released_at=(
                    None
                    if row["released_at"] is None
                    else datetime.fromisoformat(row["released_at"])
                ),
                released_by=row["released_by"],
            )
            for row in rows
        )

    def release_hold(self, hold_id: str, *, by: str, at: datetime) -> None:
        """End a hold. **A person ends it, never a timer** (§4.3).

        `SPEC-v0.9 §4`'s rule that an automatic expiry on a hold is the refund rule in a costume
        applies unchanged: a hold that lapsed on a schedule would release evidence on a schedule
        nobody reviewed. There is no sweeper here and there is not going to be one.
        """
        connection = self._connection()
        with connection:
            changed = connection.execute(
                "UPDATE holds SET released_at = ?, released_by = ? "
                "WHERE hold_id = ? AND released_at IS NULL",
                (at.isoformat(), by, hold_id),
            ).rowcount
        if changed != 1:
            raise InvalidArgument(f"no live hold {hold_id!r} in this store")

    @contextmanager
    def pruning(self) -> Iterator[None]:
        """Hold the receipt-write lock for the whole of a prune (SPEC-v0.11 §4.5).

        **Across the validation and the delete, not only the delete.** A first implementation
        took the lock inside `delete_prefix`, so two prunes could both validate and then both
        act, and a probe against a real server caught it: the pair happened to be serialized by
        the *anchor* ordering instead, which is shared state but is not the rule §4.5 states and
        is not a lock.

        `BEGIN IMMEDIATE` is the same statement `put_receipt` opens with, and SQLite admits one
        writer, so a prune and a receipt write exclude each other here without anything further.
        That is also exactly why the prune's own receipt is written **before** this is entered:
        `put_receipt` would open a second transaction on this connection and get
        `cannot start a transaction within a transaction`.
        """
        connection = self._connection()
        connection.execute("BEGIN IMMEDIATE")
        # **Inner writes must not commit through this, and an independent review found they
        # did.** `put_anchor` and `put_checkpoint` use `with connection:`, whose `__exit__` calls
        # `commit()`, and a prune calls both -- so the transaction opened above ended at the
        # first of them and the whole destructive half ran with no lock at all. Probed from a
        # second OS process at each step:
        #
        #     before put_anchor    in_transaction=True   CHILD blocked
        #     after  put_anchor    in_transaction=False  CHILD took BEGIN IMMEDIATE
        #     before delete_prefix in_transaction=False  CHILD took BEGIN IMMEDIATE
        #
        # Worse than the missing exclusion: `pruning()`'s own `commit()` and its `rollback()`
        # were then no-ops on a connection with no open transaction, so a prune that failed
        # after writing the checkpoint left the row behind and the store reported
        # `[('missing', 4), ('link_broken', 1)]` on a chain that was completely intact.
        #
        # Postgres had this guard in `_commit` from the start (`postgres.py`). SQLite did not,
        # because the defect was found on Postgres and the fix was applied where it was found.
        # SQLite is the **default** backend.
        self._pruning = True
        try:
            yield
        except BaseException:
            self._pruning = False
            connection.rollback()
            raise
        self._pruning = False
        connection.commit()

    def delete_prefix(self, through: int, effect_keys: Sequence[str]) -> tuple[int, int]:
        """Delete receipts through `seq` and the ledger rows named.

        **Every refusal has already run**, and the lock is already held by `pruning()`. This is
        the half that destroys, and it decides nothing: `retention.prune` is where rule 2, the
        holds and §4.4's table are checked, and a caller reaching here has passed all of them.
        """
        connection = self._connection()
        receipts = connection.execute(
            "DELETE FROM receipts WHERE seq IS NOT NULL AND seq <= ?", (through,)
        ).rowcount
        rows = 0
        for key in effect_keys:
            rows += connection.execute(
                "DELETE FROM budget_ledger WHERE effect_key = ?", (key,)
            ).rowcount
        return (receipts, rows)

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

    def receipts(self) -> tuple[Receipt | UnreadableReceipt, ...]:
        # By `seq`, not by `rowid`: §6.5's reader takes a receipt's *position* from this column
        # and its *content* from the document, and a reader ordering by physical row order would
        # report a gap, or fail to, according to how the rows happen to sit on disk. SQLite sorts
        # NULLs first, which puts pre-chain rows before the chain rather than inside it.
        #
        # SPEC-v0.11 §5.2: `seq` is **selected** and not only ordered by. It was ordered by and
        # never read through five releases, so the position every reader worked from came out of
        # the document, which is the half a tamperer controls.
        rows = (
            self._connection()
            .execute("SELECT seq, json, hash FROM receipts ORDER BY seq, rowid")
            .fetchall()
        )
        # `hash` comes off the column, because a document cannot contain its own hash (§6.2).
        # And the parsed document stays with the receipt (SPEC-v0.7 §6.11), so `chain_hash()`
        # hashes what was stored rather than what this binary would render.
        #
        # `_read_receipt` and not `_stored_receipt`: a row this binary cannot construct comes
        # back named at its `seq` rather than raising through every caller at once (§5.2).
        # The stored **text**, not a parsed document: parsing is one of the ways a row refuses,
        # and a `json.loads` out here would raise through every caller (§5.2).
        return tuple(_read_receipt(row["json"], row["hash"], row["seq"]) for row in rows)

    # --- delegations (SPEC-v0.3 §5.2) -------------------------------------------------

    def put_delegation(self, record: DelegationRecord) -> None:
        try:
            self._connection().execute(
                "INSERT INTO delegations(delegation_id, parent_id, depth, grant_json, "
                "created_by_agent, created_by_user, created_via, created_at, revoked_at, "
                "revoked_by) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    record.delegation_id,
                    record.parent_id,
                    record.depth,
                    record.grant_json,
                    record.created_by_agent,
                    record.created_by_user,
                    record.created_via,
                    _iso(record.created_at),
                    None if record.revoked_at is None else _iso(record.revoked_at),
                    record.revoked_by,
                ),
            )
        except sqlite3.IntegrityError as exc:
            # Never `ON CONFLICT DO UPDATE`: an upsert on an existing id would clear
            # `revoked_at`, which is `unrevoke` by another door (§5.7).
            raise InvalidArgument(f"delegation {record.delegation_id} already exists") from exc

    def get_delegation(self, delegation_id: str) -> DelegationRecord | None:
        row = (
            self._connection()
            .execute("SELECT * FROM delegations WHERE delegation_id=?", (delegation_id,))
            .fetchone()
        )
        return None if row is None else _delegation_record(row)

    def delegations_for(self, parent_id: str) -> tuple[DelegationRecord, ...]:
        rows = (
            self._connection()
            .execute("SELECT * FROM delegations WHERE parent_id=? ORDER BY rowid", (parent_id,))
            .fetchall()
        )
        return tuple(_delegation_record(row) for row in rows)

    def delegations(self, *, include_revoked: bool = False) -> tuple[DelegationRecord, ...]:
        where = "" if include_revoked else " WHERE revoked_at IS NULL"
        rows = (
            self._connection()
            .execute(f"SELECT * FROM delegations{where} ORDER BY rowid")
            .fetchall()
        )
        return tuple(_delegation_record(row) for row in rows)

    def revoke_delegation(self, delegation_id: str, *, by: str | None, at: datetime) -> bool:
        connection = self._connection()
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                "SELECT revoked_at FROM delegations WHERE delegation_id=?", (delegation_id,)
            ).fetchone()
            if row is None:
                raise InvalidArgument(f"no delegation {delegation_id}")
            if row["revoked_at"] is not None:
                revoked = False
            else:
                connection.execute(
                    "UPDATE delegations SET revoked_at=?, revoked_by=? WHERE delegation_id=?",
                    (_iso(at), by, delegation_id),
                )
                revoked = True
        except BaseException:
            self._unwind(connection)
            raise
        connection.commit()
        return revoked

    # --- approvals (SPEC-v0.1 §4.2) ---------------------------------------------------

    def put_approval_request(self, request: ApprovalRequest) -> None:
        try:
            self._connection().execute(
                "INSERT INTO approvals(approval_id, action_hash, status, action_json, "
                "created_at, expires_at, policy_hash_at_approval, precondition_fingerprint, "
                "required_roles, approvals_required) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    request.request_id,
                    request.action_hash,
                    str(ApprovalStatus.PENDING),
                    _action_json(request.action),
                    _iso(request.created_at),
                    _iso(request.expires_at),
                    request.policy_hash,
                    request.precondition_fingerprint,
                    _roles_json(request.required_roles),
                    request.approvals_required,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise InvalidArgument(f"approval {request.request_id} already exists") from exc

    def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        return self._read_approval(self._connection(), approval_id)

    def hold_continuation(
        self, action: Action, effect_key: str, continuation: str, until: datetime
    ) -> int:
        connection = self._connection()
        connection.execute("BEGIN IMMEDIATE")
        try:
            extended = plan_lease_extension(
                self._read_effect(connection, effect_key),
                effect_key,
                action.action_id,
                until,
                self._clock(),
            )
            row = connection.execute(
                "SELECT rounds FROM continuations WHERE effect_key=?", (effect_key,)
            ).fetchone()
            rounds = (row["rounds"] if row else 0) + 1
            self._write_effect(connection, extended)
            connection.execute(
                "INSERT INTO continuations(effect_key, action_id, action_json, continuation, "
                "rounds, updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(effect_key) DO UPDATE SET "
                "action_id=excluded.action_id, action_json=excluded.action_json, "
                "continuation=excluded.continuation, rounds=excluded.rounds, "
                "updated_at=excluded.updated_at",
                (
                    effect_key,
                    action.action_id,
                    _action_json(action),
                    continuation,
                    rounds,
                    _iso(self._clock()),
                ),
            )
        except sqlite3.IntegrityError as exc:
            self._unwind(connection)
            raise InvalidArgument(
                "continuation is already held for another effect; two effects cannot share one"
            ) from exc
        except BaseException:
            self._unwind(connection)
            raise
        connection.commit()
        return rounds

    def take_continuation(self, continuation: str) -> HeldContinuation:
        connection = self._connection()
        connection.execute("BEGIN IMMEDIATE")
        try:
            rows = connection.execute(
                "SELECT effect_key, action_json, continuation, rounds FROM continuations "
                "WHERE continuation <> ''"
            ).fetchall()
            found = next(
                (
                    row
                    for row in rows
                    if hmac.compare_digest(str(row["continuation"]), continuation)
                ),
                None,
            )
            if found is None:
                raise InvalidArgument(_NO_SUCH_CONTINUATION)
            effect_key = str(found["effect_key"])
            record = _continuable(
                self._read_effect(connection, effect_key), effect_key, self._clock()
            )
            # Consumed in the same transaction that admits it (§6.9.2).
            connection.execute(
                "UPDATE continuations SET continuation='' WHERE effect_key=?", (effect_key,)
            )
            held = HeldContinuation(
                _action_from_json(str(found["action_json"])),
                effect_key,
                record,
                int(found["rounds"]),
            )
        except BaseException:
            self._unwind(connection)
            raise
        connection.commit()
        return held

    def continuation_rounds(self, effect_key: str) -> int:
        row = (
            self._connection()
            .execute("SELECT rounds FROM continuations WHERE effect_key=?", (effect_key,))
            .fetchone()
        )
        return 0 if row is None else int(row["rounds"])

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

    def grant_approval(self, approval_id: str, approver: str) -> Approval | None:
        approver = _approver(approver)
        connection = self._connection()
        now = self._clock()
        connection.execute("BEGIN IMMEDIATE")
        try:
            record = self._answerable(connection, approval_id, now)
            # SPEC-v0.8 §2.5, §4.2: the verified approver and the count, inside the same
            # `BEGIN IMMEDIATE` that serialises the read and the write. That serialisation is
            # what makes the count a property of the store's write rather than of a read
            # followed by one (§4.3).
            verified = _verified_approver_now(now)
            approvers, reached = count_grant(record, verified, now)
            status = ApprovalStatus.GRANTED if reached else record.status
            granted = replace(
                record,
                status=status,
                approver=approver if reached else record.approver,
                granted_at=now if reached else record.granted_at,
                approvers=approvers,
            )
            connection.execute(
                "UPDATE approvals SET status=?, approver=?, granted_at=?, approvers=? "
                "WHERE approval_id=?",
                (
                    str(status),
                    granted.approver,
                    _iso(granted.granted_at) if granted.granted_at else None,
                    _approvers_json(approvers),
                    approval_id,
                ),
            )
        except BaseException:
            self._unwind(connection)
            raise
        connection.commit()
        return granted.as_approval() if reached else None

    def deny_approval(self, approval_id: str, approver: str) -> None:
        approver = _approver(approver)
        connection = self._connection()
        now = self._clock()
        connection.execute("BEGIN IMMEDIATE")
        try:
            record = self._answerable(connection, approval_id, now)
            verified = _verified_approver_now(now)
            approvers = (*record.approvers, verified) if verified else record.approvers
            connection.execute(
                "UPDATE approvals SET status=?, approver=?, approvers=? WHERE approval_id=?",
                (
                    str(ApprovalStatus.DENIED),
                    approver,
                    _approvers_json(approvers),
                    approval_id,
                ),
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
                policy_hash=row["policy_hash_at_approval"],
                precondition_fingerprint=row["precondition_fingerprint"],
                required_roles=_roles_from_json(row["required_roles"]),
                # `if ... is None else` and never `or 1`: `or` swallows a tampered `0`,
                # which `ApprovalRequest.__post_init__` exists to refuse (§4.2, §12). `None`
                # is the honest absent value, written by every row predating the column.
                approvals_required=(
                    1 if row["approvals_required"] is None else row["approvals_required"]
                ),
            ),
            status=ApprovalStatus(row["status"]),
            approver=row["approver"],
            granted_at=_at(row["granted_at"]),
            consumed_at=_at(row["consumed_at"]),
            approvers=_approvers_from_json(row["approvers"]),
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
        self,
        effect_key: str,
        action_id: str,
        lease: timedelta = DEFAULT_LEASE,
        charges: tuple[Charge, ...] = (),
    ) -> Reservation:
        _, reservation = self._authorize_and_reserve(
            None, None, effect_key, action_id, lease, charges=charges
        )
        return _only(reservation, "reservation")

    def consume_approval_and_reserve(
        self,
        approval_id: str,
        action_hash: str,
        effect_key: str,
        action_id: str,
        lease: timedelta = DEFAULT_LEASE,
        charges: tuple[Charge, ...] = (),
    ) -> tuple[Approval, Reservation]:
        approval, reservation = self._authorize_and_reserve(
            approval_id, action_hash, effect_key, action_id, lease, charges=charges
        )
        return _only(approval, "approval"), _only(reservation, "reservation")

    def _authorize_and_reserve(
        self,
        approval_id: str | None,
        action_hash: str | None,
        effect_key: str | None,
        action_id: str | None,
        lease: timedelta,
        charges: tuple[Charge, ...] = (),
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
            # SPEC-v0.9 §3.3.1, **inside the `BEGIN IMMEDIATE` this method already holds**. That
            # is the whole of the amendment's value on this backend: the write lock was taken
            # before the first read, so the sum and the insert are serialised against every other
            # process on this file (§3.6), and nothing further is required.
            if charges:
                check_charges(charges, lambda charge: self._spent(connection, charge, now))
            if plan.reservation is not None:
                self._reserve_locked(connection, plan.reservation, plan.renews, now)
            if approved is not None:
                self._consume_locked(connection, approved.approval_id, now)
            if charges and plan.reservation is not None:
                self._charge_locked(
                    connection, charges, str(effect_key), plan.reservation.attempt, now
                )
        except BaseException:
            self._unwind(connection)
            raise
        connection.commit()
        return (approved.as_approval() if approved is not None else None), plan.reservation

    def _release_locked(
        self, connection: sqlite3.Connection, effect_key: str, state: EffectState, now: datetime
    ) -> None:
        """SPEC-v0.9 §4.1, §4.4. Released exactly on `FAILED`, by compare-and-set on the flag.

        `WHERE released_at IS NULL` is the compare half, so a re-issued `UPDATE` (`v0.6 §4.3.2`
        Table A2 row 2) is a no-op rather than a second subtraction.
        """
        if state is not EffectState.FAILED:
            return
        connection.execute(
            "UPDATE budget_ledger SET released_at = ? WHERE effect_key = ? AND released_at IS NULL",
            (_iso(now), effect_key),
        )

    def _spent(self, connection: sqlite3.Connection, charge: Charge, now: datetime) -> int:
        """The un-released sum for this charge, over its rolling window (SPEC-v0.9 §2.5)."""
        row = connection.execute(
            """
            SELECT COALESCE(SUM(amount), 0) FROM budget_ledger
             WHERE grant_id = ? AND metric = ? AND released_at IS NULL AND consumed_at >= ?
            """,
            (charge.grant_id, charge.metric, _iso(now - charge.window)),
        ).fetchone()
        return int(row[0])

    def _charge_locked(
        self,
        connection: sqlite3.Connection,
        charges: tuple[Charge, ...],
        effect_key: str,
        attempt: int,
        now: datetime,
    ) -> None:
        """One row per charge, idempotent on the unique key (SPEC-v0.9 §3.4).

        `INSERT OR IGNORE` against `UNIQUE (effect_key, attempt, grant_id, metric)`, because
        `v0.6 §4.3.2` Table A1 row 2 retries a lost insert once and an unconstrained append would
        double-charge exactly when an operator's network is already misbehaving.
        """
        connection.executemany(
            """
            INSERT OR IGNORE INTO budget_ledger
                (grant_id, metric, amount, effect_key, attempt, consumed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    charge.grant_id,
                    charge.metric,
                    charge.amount,
                    effect_key,
                    attempt,
                    _iso(now),
                )
                for charge in charges
            ],
        )

    def consumptions(
        self,
        *,
        grant_id: str | None = None,
        metric: str | None = None,
        since: datetime | None = None,
        effect_key: str | None = None,
    ) -> tuple[Consumption, ...]:
        clauses: list[str] = []
        values: list[Any] = []
        if grant_id is not None:
            clauses.append("grant_id = ?")
            values.append(grant_id)
        if metric is not None:
            clauses.append("metric = ?")
            values.append(metric)
        if since is not None:
            clauses.append("consumed_at >= ?")
            values.append(_iso(since))
        if effect_key is not None:
            clauses.append("effect_key = ?")
            values.append(effect_key)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = (
            self._connection()
            .execute(
                "SELECT grant_id, metric, amount, effect_key, attempt, consumed_at, released_at"
                f" FROM budget_ledger{where} ORDER BY id",
                values,
            )
            .fetchall()
        )
        return tuple(
            Consumption(
                grant_id=row[0],
                metric=row[1],
                amount=int(row[2]),
                effect_key=row[3],
                attempt=int(row[4]),
                consumed_at=datetime.fromisoformat(row[5]),
                released_at=_at(row[6]),
            )
            for row in rows
        )

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
            #
            # `AND attempt=?`, the attempt the plan renewed from, is SPEC-v0.7 §5.6's condition
            # for Postgres, kept here for defence in depth and stated as what it is: BEGIN
            # IMMEDIATE already holds the write lock across the read and this write, so the
            # stale case cannot happen on this backend and removing the clause fails no test.
            updated = connection.execute(
                # `resolved_by` is cleared with them, and its own line says why: a human
                # resolving to FAILED is saying *"this may be retried"*, not committing the
                # retry. Leaving the column set attributes the agent's next outcome to the
                # person who merely permitted it -- and v0.5 got this right only by accident,
                # because the resolver used to live inside the `error` this UPDATE clears.
                "UPDATE effects SET state=?, action_id=?, attempt=?, lease_expires_at=?, "
                "result_json=NULL, error=NULL, resolved_by=NULL, updated_at=? "
                "WHERE effect_key=? AND state=? AND attempt=?",
                (
                    str(record.state),
                    record.action_id,
                    record.attempt,
                    _iso(record.lease_expires_at) if record.lease_expires_at else None,
                    _iso(now),
                    record.effect_key,
                    str(EffectState.FAILED),
                    reservation.attempt - 1,
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
            now = self._clock()
            record = _resolvable(self._read_effect(connection, effect_key), effect_key, state)
            resolved = _resolved(record, state, resolver, now)
            self._write_effect(connection, resolved)
            # SPEC-v0.9 §4.1, §4.2's `resolve_effect(FAILED)` row: this path does not go through
            # `_transition`, so the release is here too, inside the same `BEGIN IMMEDIATE`.
            self._release_locked(connection, effect_key, state, now)
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
            # SPEC-v0.9 §4.1, inside the same `BEGIN IMMEDIATE`: the ledger moves with the record
            # or neither moves. A release in a second transaction could leave a `FAILED` effect
            # holding its charge for ever if the process died between them.
            self._release_locked(connection, effect_key, state, now)
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
            resolved_by=row["resolved_by"],
        )

    def _write_effect(self, connection: sqlite3.Connection, record: EffectRecord) -> None:
        connection.execute(
            "UPDATE effects SET state=?, action_id=?, attempt=?, lease_expires_at=?, "
            "result_json=?, error=?, updated_at=?, resolved_by=? WHERE effect_key=?",
            (
                str(record.state),
                record.action_id,
                record.attempt,
                _iso(record.lease_expires_at) if record.lease_expires_at else None,
                _result_json(record.result),
                _storable(record.error),
                _iso(record.updated_at),
                _storable(record.resolved_by),
                record.effect_key,
            ),
        )

    @staticmethod
    def _unwind(connection: sqlite3.Connection) -> None:
        """Undo the open transaction, if this failure did not already close one."""
        connection.rollback()


def _delegation_record(row: sqlite3.Row) -> DelegationRecord:
    """One `delegations` row, with the columns of SPEC-v0.3 §5.2 and nothing derived."""
    return DelegationRecord(
        delegation_id=str(row["delegation_id"]),
        parent_id=str(row["parent_id"]),
        depth=int(row["depth"]),
        grant_json=str(row["grant_json"]),
        created_by_agent=str(row["created_by_agent"]),
        created_by_user=row["created_by_user"],
        created_via=str(row["created_via"]),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        revoked_at=_at(row["revoked_at"]),
        revoked_by=row["revoked_by"],
    )


_T = TypeVar("_T")


def _only(value: _T | None, what: str) -> _T:
    """Narrow a result the caller did ask for. A store returning nothing here is broken."""
    if value is None:
        raise InvalidArgument(f"the store produced no {what}")
    return value


#: Unicode general categories that have no place in a name printed to a terminal: control
#: (`Cc`), format (`Cf` -- the bidi overrides), and the two separators (`Zl`, `Zp`) that
#: `str.splitlines()` treats as line breaks.
_UNPRINTABLE: Final = frozenset({"Cc", "Cf", "Zl", "Zp"})


def _approver(approver: str) -> str:
    """The one place a resolver's or approver's name is checked before it becomes evidence.

    Non-empty, and **no control characters**. `ctrlrun effects` and `ctrlrun approvals` print one
    record per line, so a newline in this string forges a whole row in the evidence output:

        refund:z     committed  attempt 1  act_a  2026-01-01T12:00:00.000Z  resolved by cli:eve
        refund:fake  committed  attempt 1  act_x  2026-01-01T12:00:00.000Z

    -- a second effect that does not exist, in a listing an operator reads to decide what
    happened. Not reachable through the shipped CLI, which writes a constant, and reachable by
    any caller holding the store; `resolve_effect` and `grant_approval` are on the frozen
    `StateStore` protocol, so every backend gets this by going through here.

    It is a refusal, not an escape. Escaping would make the stored value differ from the one the
    caller passed, and a record of who decided that is not what anybody typed is worse than a
    rejected write. §5.3's "a reader must be able to tell them apart" needs the strings to be
    what they say they are.

    **The property is checked directly, because a proxy for it missed three characters.** The
    first version refused anything below `U+0020` plus `U+007F`, which is C0 and DEL -- and
    `str.splitlines()`, which every Python reader of this output uses, also splits on `U+0085`,
    `U+2028` and `U+2029`. A review drove `cli:eve\u2028refund:fake  committed` through the
    shipped listing and got two lines out, the second an effect that does not exist. So the check
    is now the sentence itself: this string occupies exactly one line.
    """
    if not approver:
        raise InvalidArgument("approver must be a non-empty string")
    if approver.splitlines() != [approver]:
        raise InvalidArgument(
            f"approver must occupy exactly one line, got {approver!r}: `ctrlrun effects` prints "
            "one record per line, and a string that splits forges a row in the evidence"
        )
    if any(unicodedata.category(character) in _UNPRINTABLE for character in approver):
        raise InvalidArgument(
            f"approver must not contain control or formatting characters, got {approver!r}: "
            "U+202E and its neighbours rewrite the rest of a terminal line without adding one"
        )
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


def _continuable(record: EffectRecord | None, effect_key: str, now: datetime) -> EffectRecord:
    """Whether a resumption may be admitted (SPEC-v0.2 §6.9.2).

    The record checks are the ones a lease extension makes, and for the same reason: a record
    that has been resolved, or whose lease lapsed while the client was answering, is not this
    attempt's to finish. An expired one is `AMBIGUOUS` by the ordinary path of v0.1 §5.3 E3 —
    the executor may have died mid-flight, and that is exactly what has happened.
    """
    if record is None:
        raise AmbiguousEffect(
            f"effect {effect_key!r} has no record to resume", effect_key=effect_key
        )
    if not (record.state is EffectState.EXECUTING and record.lease_is_live(now)):
        # One branch, not two: a resolved record and a lapsed lease both refuse with the same
        # exception and write nothing, so separate guards for each would be one defence
        # wearing two hats — indistinguishable to a caller and to a test. The message names
        # the state instead.
        raise AmbiguousEffect(
            f"effect {effect_key!r} is {record.state} under {record.action_id} with no live "
            "lease and cannot be resumed",
            effect_key=effect_key,
            action_id=record.action_id,
        )
    return record


#: What a continuation nobody holds is refused with. Deliberately identical whether the value
#: was forged, already consumed, or belongs to a suspension that has since moved on: telling
#: an agent which of those it hit is telling it how to search.
_NO_SUCH_CONTINUATION: Final = "no held suspension matches the presented continuation"
