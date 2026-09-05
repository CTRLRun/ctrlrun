"""The broken-store fixtures. SPEC-v0.6 §2.6.

**A suite that only ever passes is a suite nothing exercises.** Each store here is broken in one
named way, and T140 requires each to fail the suite named for it -- by **case** and by **reason**,
because a test asserting only that `reservation` failed cannot tell you which check ran. That is
`v0.5 §5.4`'s finding one layer down.

Every fixture wraps a real backend and subverts one method, so what is being tested is the
suite's ability to notice, not a hand-built store's ability to be wrong in many ways at once.

Two of them exist because of the review of `SPEC-v0.6.md` rather than by foresight:
`falsely-declares-no-url`, because §2.4's two N/As are the only declarations on this surface and
a declaration that turns a suite off is a setting that relaxes a check; and the pairing of
`guesses-failed` with `raises-not-executed`, because `outcome` has two checks and one fixture
would have left the other subsumed.
"""

from __future__ import annotations

import atexit
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ...action import Action
from ...effect import DEFAULT_LEASE, EffectRecord, EffectState, Reservation
from ...errors import NotExecuted
from ...receipt import Event
from ...state import DelegationRecord, StateStore
from .backends import SQLiteBackend, StoreBackend


def _clock_of(store: StateStore) -> Callable[[], datetime]:
    """A broken store reaching into a real one. Only fixtures do this."""
    clock: Callable[[], datetime] = store._clock  # type: ignore[attr-defined]
    return clock


class _Wrapped:
    """A store that delegates everything. Each fixture overrides exactly one method.

    Delegation rather than subclassing `SQLiteStateStore`: a subclass would inherit the
    behaviour it is meant to be missing, and the fixture would then be testing a patch rather
    than a broken store.
    """

    def __init__(self, inner: StateStore) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _Backend:
    """A backend whose stores are wrapped. `name` is the fixture's, so a report says which."""

    def __init__(self, name: str, root: Path, wrap: Callable[[StateStore], StateStore]) -> None:
        self.name = name
        self._inner = SQLiteBackend(root)
        self._wrap = wrap

    def open(self) -> StateStore:
        return self._wrap(self._inner.open())

    def reopen(self) -> StateStore | None:
        inner = self._inner.reopen()
        return None if inner is None else self._wrap(inner)

    def open_with_clock(self, clock: Callable[[], datetime]) -> StateStore:
        return self._wrap(self._inner.open_with_clock(clock))

    def url(self) -> str | None:
        return self._inner.url()

    def reset(self) -> None:
        self._inner.reset()


# --- reservation -----------------------------------------------------------------------------


class _TwoWinners(_Wrapped):
    """Drops the uniqueness check: every contender reserves."""

    def reserve_effect(
        self, effect_key: str, action_id: str, lease: timedelta = DEFAULT_LEASE
    ) -> Reservation:
        now = datetime.now(tz=None).astimezone()
        return Reservation(
            effect_key=effect_key, action_id=action_id, attempt=1, lease_expires_at=now + lease
        )


class _ReleasesAnExpiredLease(_Wrapped):
    """Treats a lease-expired record as free and re-reserves it, rather than moving it to
    `AMBIGUOUS` and refusing (`v0.1 §5.3 E3`)."""

    def reserve_effect(
        self, effect_key: str, action_id: str, lease: timedelta = DEFAULT_LEASE
    ) -> Reservation:
        record = self._inner.get_effect(effect_key)
        if record is not None and record.lease_expires_at is not None:
            live = record.lease_is_live(_clock_of(self._inner)())
            if not live and record.state in (EffectState.RESERVED, EffectState.EXECUTING):
                now = _clock_of(self._inner)()
                return Reservation(
                    effect_key=effect_key,
                    action_id=action_id,
                    attempt=record.attempt + 1,
                    lease_expires_at=now + lease,
                )
        return self._inner.reserve_effect(effect_key, action_id, lease)


class _RefusesWithTheWrongError(_Wrapped):
    """Refuses a duplicate reservation with a plain `RuntimeError`.

    `v0.1 §5.4`'s refusals are `DuplicateEffect` and `AmbiguousEffect`, and an agent loop's
    `except DuplicateEffect` -- written to handle exactly this -- would not catch a
    `RuntimeError`. The refusal happens; the *taxonomy* does not, and a case asserting only
    "something was raised" cannot tell the difference. Mutation found the check unfixtured.
    """

    def reserve_effect(
        self, effect_key: str, action_id: str, lease: timedelta = DEFAULT_LEASE
    ) -> Reservation:
        try:
            return self._inner.reserve_effect(effect_key, action_id, lease)
        except Exception as refused:
            raise RuntimeError(f"could not reserve: {refused}") from refused


# --- approval --------------------------------------------------------------------------------


class _GrantsTwice(_Wrapped):
    """`consume_approval` returns the `Approval` and leaves the record `granted`."""

    def consume_approval(self, approval_id: str, action_hash: str) -> Any:
        record = self._inner.get_approval(approval_id)
        if record is not None and str(record.status) == "granted":
            return record.as_approval()
        return self._inner.consume_approval(approval_id, action_hash)


class _ConsumesBeforeReserving(_Wrapped):
    """Sequences `consume_approval` then `reserve_effect` in two transactions, so a refused
    reservation has already spent the approval -- the exact hole `v0.1 §4.2 A4` closes."""

    def consume_approval_and_reserve(
        self,
        approval_id: str,
        action_hash: str,
        effect_key: str,
        action_id: str,
        lease: timedelta = DEFAULT_LEASE,
    ) -> Any:
        approval = self._inner.consume_approval(approval_id, action_hash)
        reservation = self._inner.reserve_effect(effect_key, action_id, lease)
        return approval, reservation


# --- resolution ------------------------------------------------------------------------------


class _ResolvesAnything(_Wrapped):
    """`resolve_effect` moves any record, not only an `AMBIGUOUS` one."""

    def resolve_effect(self, effect_key: str, state: EffectState, resolver: str) -> EffectRecord:
        record = self._inner.get_effect(effect_key)
        if record is None:
            return self._inner.resolve_effect(effect_key, state, resolver)
        moved = EffectRecord(
            effect_key=record.effect_key,
            state=state,
            action_id=record.action_id,
            attempt=record.attempt,
            created_at=record.created_at,
            updated_at=record.updated_at,
            lease_expires_at=None,
            result=record.result,
            error=f"resolved {state} by {resolver}",
        )
        connection = self._inner._connection()  # type: ignore[attr-defined]
        connection.execute(
            "UPDATE effects SET state=?, error=?, lease_expires_at=NULL WHERE effect_key=?",
            (str(state), moved.error, effect_key),
        )
        connection.commit()
        return moved


# --- outcome ---------------------------------------------------------------------------------


class _GuessesFailed(_Wrapped):
    """Writes `FAILED` when a transition refuses, rather than leaving the record alone.

    This is the storage-layer version of the mistake this library exists to prevent: "I could
    not make that write" becoming "it definitely did not happen".
    """

    def begin_execution(self, effect_key: str, action_id: str) -> None:
        try:
            self._inner.begin_execution(effect_key, action_id)
        except BaseException:
            # Written straight to the row, because the honest path refuses it -- which is the
            # point: a store that "helpfully" recorded a refusal as FAILED would be turning
            # "I could not make that write" into "it definitely did not happen".
            connection = self._inner._connection()  # type: ignore[attr-defined]
            connection.execute(
                "UPDATE effects SET state='failed', error='guessed' WHERE effect_key=?",
                (effect_key,),
            )
            connection.commit()
            raise


class _RaisesNotExecuted(_Wrapped):
    """Raises `NotExecuted` from a refused transition -- the one exception an agent may read as
    permission to retry, asserted by a store that has never spoken to the remote."""

    def begin_execution(self, effect_key: str, action_id: str) -> None:
        try:
            self._inner.begin_execution(effect_key, action_id)
        except BaseException as refused:
            raise NotExecuted(f"the store could not begin execution: {refused}") from refused


# --- durability ------------------------------------------------------------------------------


class _ForgetsTheUnknown(_Wrapped):
    """Holds `AMBIGUOUS` in memory and never writes it, so it is lost across a reopen."""

    def mark_ambiguous(self, effect_key: str, action_id: str, error: str) -> None:
        return None


class _ForgetsTheResolver(_Wrapped):
    """Resolves correctly and forgets **who** did it (SPEC-v0.6 §5.3).

    Written because a review mutated `PostgresStateStore`'s read and write of `resolved_by` to
    `None` and the whole suite -- 2262 tests -- stayed green, twice. `resolved_by` is a column on
    both shipped backends, and the suite that exists to make two backends agree had no case for
    the one field the milestone added to them.
    """

    def resolve_effect(self, effect_key: str, state: EffectState, resolver: str) -> EffectRecord:
        return replace(self._inner.resolve_effect(effect_key, state, resolver), resolved_by=None)

    def get_effect(self, effect_key: str) -> EffectRecord | None:
        record = self._inner.get_effect(effect_key)
        return None if record is None else replace(record, resolved_by=None)


class _KeepsTheResolverAcrossARetry(_Wrapped):
    """Never clears `resolved_by`, so `v0.1 §5.4`'s retry is attributed to whoever permitted it.

    The direction that matters: a human resolving to `FAILED` says *"this may be retried"*, and
    the agent's next outcome is the agent's. This is the shape the shipped stores had -- the
    resolver moved from free text into a column, and the renewal `UPDATE` cleared the text and
    not the column.
    """

    _resolvers: dict[str, str]

    def __init__(self, inner: StateStore) -> None:
        _Wrapped.__init__(self, inner)
        self._resolvers = {}

    def reserve_effect(
        self, effect_key: str, action_id: str, lease: timedelta = DEFAULT_LEASE
    ) -> Reservation:
        before = self._inner.get_effect(effect_key)
        reservation = self._inner.reserve_effect(effect_key, action_id, lease)
        if before is not None and before.resolved_by is not None:
            self._resolvers[effect_key] = before.resolved_by
        return reservation

    def get_effect(self, effect_key: str) -> EffectRecord | None:
        record = self._inner.get_effect(effect_key)
        if record is None:
            return None
        kept = self._resolvers.get(effect_key)
        return record if kept is None else replace(record, resolved_by=kept)


# --- evidence --------------------------------------------------------------------------------


class _CoercesAnArgument(_Wrapped):
    """Truncates a string argument on the way back out of the store.

    The stored `Action` then no longer hashes to what a human approved, which is `v0.1 §4.2 A1`
    defeated through storage rather than through the agent -- the failure mode a backend with a
    narrow column, a lossy encoding or an over-eager normalizer produces by accident.
    """

    def get_approval(self, approval_id: str) -> Any:
        record = self._inner.get_approval(approval_id)
        if record is None:
            return None
        truncated = {
            name: value[:8] if isinstance(value, str) else value
            for name, value in record.request.action.canonical_arguments.items()
        }
        action = Action(
            name=record.request.action.name,
            arguments=truncated,
            principal=record.request.action.principal,
            resource=record.request.action.resource,
            environment=record.request.action.environment,
        )
        return replace(record, request=replace(record.request, action=action))


class _RenumbersEvents(_Wrapped):
    """`append_event` returns an event carrying an id other than the one it stored."""

    def append_event(self, event: Event) -> Event:
        stored = self._inner.append_event(event)
        return replace(stored, event_id=(stored.event_id or 0) + 1000)


# --- continuation ----------------------------------------------------------------------------


class _AdmitsTwoResumptions(_Wrapped):
    """`take_continuation` does not consume the token in the transaction that admits it."""

    def take_continuation(self, continuation: str) -> Any:
        held = self._inner.take_continuation(continuation)
        self._inner.hold_continuation(
            held.action,
            held.effect_key,
            continuation,
            held.record.lease_expires_at or _clock_of(self._inner)(),
        )
        return held


# --- delegation ------------------------------------------------------------------------------


class _UpsertsADelegation(_Wrapped):
    """`put_delegation` upserts on a duplicate id, clearing `revoked_at` -- unrevoking by
    another door, in a release that says there is no such thing (`v0.3 §5.2`)."""

    def put_delegation(self, record: DelegationRecord) -> None:
        existing = self._inner.get_delegation(record.delegation_id)
        if existing is not None:
            connection = self._inner._connection()  # type: ignore[attr-defined]
            connection.execute(
                "UPDATE delegations SET revoked_at=NULL, revoked_by=NULL WHERE delegation_id=?",
                (record.delegation_id,),
            )
            connection.commit()
            return None
        self._inner.put_delegation(record)


# --- the declarations ------------------------------------------------------------------------


class _FalselyDeclaresNoUrl:
    """Durable, shareable storage that returns `None` from `url()` and `reopen()`.

    §2.4's two N/As are the only declarations on this surface, and a declaration that turns a
    suite off is a setting that relaxes a check -- which §1.1's last bullet forbids by name.
    This is `v0.5 §5.4`'s `falsely-refuses-before-invoking` one layer down: the kit does not take
    the declaration on trust, it tries the thing the declaration says is impossible.
    """

    name = "falsely-declares-no-url"

    def __init__(self, root: Path) -> None:
        self._inner = SQLiteBackend(root)

    def open(self) -> StateStore:
        return self._inner.open()

    def open_with_clock(self, clock: Callable[[], datetime]) -> StateStore:
        return self._inner.open_with_clock(clock)

    def reopen(self) -> StateStore | None:
        return None

    def url(self) -> str | None:
        return None

    def reset(self) -> None:
        self._inner.reset()


# --- the registry ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Fixture:
    """One broken store, and the case in each suite that must carry its failure.

    `cases` maps a suite name to the case id, rather than carrying one id for all of them: a
    fixture aimed at a **declaration** breaks two suites in two different places, and pinning
    both to one id would pin one of them to nothing.
    """

    name: str
    cases: Mapping[str, str]
    build: Callable[[Path], StoreBackend]
    #: A fragment of the reason the case must fail with. Without it a fixture pins only *that*
    #: the case failed, and a case with two checks is then a subsumed guard: delete the first
    #: and the second still fails it, so the mutation table reads green for a check nothing
    #: reached. Found by mutation on `single-use`, which is why this field exists.
    because: str = ""

    @property
    def fails(self) -> tuple[str, ...]:
        return tuple(self.cases)

    def case_in(self, suite: str) -> str:
        return self.cases[suite]

    def backend(self, root: Path | None = None) -> StoreBackend:
        """A backend for this fixture. `root` is the caller's to clean up.

        Where none is given a temporary directory is made and **registered for removal at
        interpreter exit** rather than leaked: T140 builds one backend per fixture per test, and
        the first version left about twenty-six directories behind on every run.
        """
        if root is not None:
            return self.build(Path(root))
        made = tempfile.mkdtemp(prefix="ctrlrun-fixture-")
        atexit.register(shutil.rmtree, made, True)
        return self.build(Path(made))


def _wrapping(
    name: str, wrap: Callable[[StateStore], StateStore]
) -> Callable[[Path], StoreBackend]:
    def build(root: Path) -> StoreBackend:
        return _Backend(name, root, wrap)

    return build


def two_winners(root: Path) -> StoreBackend:
    return _Backend("two-winners", root, _TwoWinners)


def guesses_failed(root: Path) -> StoreBackend:
    return _Backend("guesses-failed", root, _GuessesFailed)


def raises_not_executed(root: Path) -> StoreBackend:
    return _Backend("raises-not-executed", root, _RaisesNotExecuted)


FIXTURES: Sequence[Fixture] = (
    Fixture(
        "two-winners",
        {"reservation": "e1-in-process"},
        _wrapping("two-winners", _TwoWinners),
        because="contenders reserved one effect key",
    ),
    Fixture(
        "refuses-with-the-wrong-error",
        {"reservation": "e1-in-process"},
        _wrapping("refuses-with-the-wrong-error", _RefusesWithTheWrongError),
        because="v0.1 §5.4's refusals are",
    ),
    Fixture(
        "releases-an-expired-lease",
        {"reservation": "lease-expiry"},
        _wrapping("releases-an-expired-lease", _ReleasesAnExpiredLease),
        because="did not raise AmbiguousEffect",
    ),
    Fixture(
        "forgets-the-resolver",
        {"durability": "resolver-survives"},
        _wrapping("forgets-the-resolver", _ForgetsTheResolver),
        because="resolved_by=None",
    ),
    Fixture(
        "keeps-the-resolver-across-a-retry",
        {"resolution": "resolution-attribution"},
        _wrapping("keeps-the-resolver-across-a-retry", _KeepsTheResolverAcrossARetry),
        because="the retry carries resolved_by",
    ),
    Fixture(
        "grants-twice",
        {"approval": "single-use"},
        _wrapping("grants-twice", _GrantsTwice),
        because="after being consumed",
    ),
    Fixture(
        "consumes-before-reserving",
        {"approval": "atomic-with-reservation"},
        _wrapping("consumes-before-reserving", _ConsumesBeforeReserving),
        because="a refused reservation",
    ),
    Fixture(
        "resolves-anything",
        {"resolution": "only-ambiguous"},
        _wrapping("resolves-anything", _ResolvesAnything),
        because="did not raise InvalidArgument",
    ),
    Fixture(
        "guesses-failed",
        {"outcome": "no-failed-on-refusal"},
        _wrapping("guesses-failed", _GuessesFailed),
        because="to FAILED",
    ),
    Fixture(
        "raises-not-executed",
        {"outcome": "no-not-executed"},
        _wrapping("raises-not-executed", _RaisesNotExecuted),
        because="raised NotExecuted",
    ),
    Fixture(
        "forgets-the-unknown",
        {"durability": "ambiguous-survives"},
        _wrapping("forgets-the-unknown", _ForgetsTheUnknown),
        because="came back executing",
    ),
    Fixture(
        "coerces-an-argument",
        {"evidence": "action-round-trip"},
        _wrapping("coerces-an-argument", _CoercesAnArgument),
        because="the action hash changed across the store",
    ),
    Fixture(
        "renumbers-events",
        {"evidence": "event-ids"},
        _wrapping("renumbers-events", _RenumbersEvents),
        because="the store does not hold",
    ),
    Fixture(
        "admits-two-resumptions",
        {"continuation": "one-resumption"},
        _wrapping("admits-two-resumptions", _AdmitsTwoResumptions),
        because="did not raise InvalidArgument",
    ),
    Fixture(
        "upserts-a-delegation",
        {"delegation": "insert-not-upsert"},
        _wrapping("upserts-a-delegation", _UpsertsADelegation),
        because="upserted on a duplicate id",
    ),
    Fixture(
        "falsely-declares-no-url",
        {"reservation": "e1-cross-process", "durability": "ambiguous-survives"},
        _FalselyDeclaresNoUrl,
        because="two independent handles",
    ),
)
