"""`PostgresStateStore`. Build-list item 3; SPEC-v0.6 §4.

The same frozen protocol of `v0.1 §5.3`, extended by nothing, with a different mechanism
underneath. `BEGIN IMMEDIATE` is a whole-database write lock on a local file; take the file away
and put the store on another host and E1 -- *at most one caller per effect key, across threads and
processes* -- has to be re-earned.

**The mechanism.** `UNIQUE(effect_key)` plus `INSERT … ON CONFLICT DO NOTHING`, under `READ
COMMITTED`, which is Postgres's default and which this store does **not** set. The guarantee is
the unique index, not the isolation level. Every later transition is a compare-and-set --
`UPDATE … WHERE effect_key = %s AND state = %s AND action_id = %s` -- with **the row count
checked**.

**The decisions stay where they are.** `plan_reservation`, `plan_lease_extension`,
`check_consumable` and `check_answerable` are pure functions in `effect.py` and `approval.py`, and
both existing backends decide with them and then only write. This one does the same, so
`v0.1 §5.4`'s retry table has one implementation rather than three and a backend cannot drift into
permitting something SQLite refuses. That is the property `ctrlrun.conformance.store` exists to
check and this structure exists to make true.

**Two ambiguities, one word.** An exception raised *before* `COMMIT` is issued means nothing
committed: the store write is `FAILED` and may be retried. An exception *during or after* `COMMIT`
means nobody knows, and the answer is to **re-read the record** -- §4.3.2. Only if the re-read
itself fails does the store refuse to let execution proceed, writing no effect state. An ambiguous
*remote effect* has no such move, which is why `AMBIGUOUS` is terminal there; collapsing the two
would either refuse work a single query could have recovered or retry work nothing can.

`ctrlrun[postgres]`. `import ctrlrun` imports no `psycopg` module (T153).
"""

from __future__ import annotations

import contextlib
import json
import logging
import threading
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, Final

from .action import Action
from .approval import (
    Approval,
    ApprovalRecord,
    ApprovalRequest,
    ApprovalStatus,
    check_answerable,
    check_consumable,
)
from .effect import (
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
from .errors import (
    ApprovalMismatch,
    CTRLRunError,
    DuplicateEffect,
    InvalidArgument,
    MissingDependency,
)
from .migrations import migrate
from .receipt import Event, EventType, Receipt
from .state import (
    DelegationRecord,
    HeldContinuation,
    _action_from_json,
    _action_json,
    _approver,
    _at,
    _checked,
    _iso,
    _only,
    _required_action,
    _required_hash,
    _reserved,
    _resolvable,
    _resolved,
    _result_json,
    _result_value,
    _transitioned,
    _utc_now,
)

_RESERVED: Final = frozenset({EffectState.RESERVED})
_EXECUTING: Final = frozenset({EffectState.EXECUTING})
#: `mark_ambiguous` accepts a reservation that never began (a crash between the two) and is
#: idempotent, so recording an unknown outcome can never itself fail (`v0.1 §5.5`).
_UNFINISHED: Final = frozenset({EffectState.RESERVED, EffectState.EXECUTING, EffectState.AMBIGUOUS})

#: SPEC-v0.6 §4.3.1. SQLSTATEs where the server states, in band, that it rolled the transaction
#: back. That is the closest thing Postgres offers to an executor raising `NotExecuted`, and it is
#: `v0.2 §6.8`'s rule one layer down: a protocol-level error the specification defines as emitted
#: before dispatch is a statement of non-execution; everything a peer says after dispatch is an
#: outcome.
#:
#: The set is closed and small, and adding to it is a specification change. `57014`
#: (`query_canceled`) is deliberately **not** in it: a statement timeout on `COMMIT` is exactly the
#: ambiguous case, and a cancellation that arrived while the server was committing may or may not
#: have prevented it.
STATED_ABORTS: Final = frozenset({"40001", "40P01"})

_LOG = logging.getLogger(__name__)

#: SPEC-v0.6 §4.3.4. The closed set of §4.3.2 branches, reported on the `ctrlrun.postgres` logger
#: as `branch=` so a test -- and an operator reading a log after an incident -- can tell **which
#: row** of Table A a lost `COMMIT` took.
#:
#: This exists because §8's T155 asks that "the events name which branch of §4.3.2 ran" and
#: nothing implemented it, which made the test unfalsifiable: the outcome T155 asserted (a
#: reservation that is ours, a blind retry refused) is *also* what a store that never re-read
#: produces, because in that scenario the write did land. A review replaced
#: `_resolve_lost_insert` with `return` and T155 still passed.
#:
#: Reported at WARNING because reaching any of these means a store write's outcome was
#: unobservable, which is worth a line in an operator's log whether or not it resolved cleanly.
A1_OURS: Final = "a1.row1.ours"
A1_REINSERT: Final = "a1.row2.reinsert"
A1_REFUSE: Final = "a1.row3.refuse"
A2_LANDED: Final = "a2.row1.landed"
A2_REISSUE: Final = "a2.row2.reissue"
A2_REFUSE: Final = "a2.row3.refuse"


def _took(branch: str, effect_key: str) -> None:
    """Say which row of §4.3.2's tables resolved an ambiguous store write."""
    _LOG.warning(
        "ambiguous store write on effect %r resolved by %s",
        effect_key,
        branch,
        extra={"branch": branch, "effect_key": effect_key},
    )


def _psycopg() -> Any:
    """The driver, imported lazily so `import ctrlrun` never reaches it (T153)."""
    try:
        import psycopg
    except ImportError as missing:
        raise MissingDependency("psycopg", "postgres") from missing
    return psycopg


# `errors.py`'s convention: this codebase's exception names carry no suffix.
class AmbiguousWrite(CTRLRunError):  # noqa: N818
    """A `COMMIT` whose outcome the store could not observe (SPEC-v0.6 §4.3 Table A).

    It exists so the two ambiguities of §1.3 cannot be confused in the code the way they must not
    be confused in the prose: a *store write* nobody could observe is this, and a *remote effect*
    nobody could observe is `AmbiguousEffect`.

    **It subclasses `CTRLRunError`, and that is a correction.** It was documented as "internal to
    this module and never raised to a caller", and a review found it escaping from eleven methods
    -- as a bare `Exception` that no `except CTRLRunError` in this codebase catches: not the
    CLI's, not the gateway's, not the webhook's. The reservation and transition paths resolve it
    by re-reading (§4.3.2); everywhere else it is a refusal a caller can catch, which is what an
    unobservable write **is**. Documenting it as unreachable did not make it so.
    """


def _is_stated_abort(error: BaseException) -> bool:
    return str(getattr(error, "sqlstate", "")) in STATED_ABORTS


def _is_our_own_write(found: EffectRecord, expected: EffectRecord) -> bool:
    """Is this record byte-for-byte the row we attempted to write (SPEC-v0.6 §4.3.3)?

    **Every column, not a match on `action_id`.** `Action.action_id` is caller-supplyable and
    §5.1 contemplates two attempts sharing one, so *"a record carrying our `action_id`"* is
    satisfied by another process's live reservation -- one logical effect executed twice, through
    the storage layer.

    `created_at` and `updated_at` are here and were missing. §4.3.3 lists them, and without them a
    **frozen injected clock** -- which `verify` and the conformance backend both use -- made two
    attempts indistinguishable, and the store concluded it held a key another attempt held. A
    review reproduced exactly that, and the test meant to catch it passed only because the wall
    clock happened to move between the two.

    **And a residual §4.3.3 could not close, stated rather than implied.** Where two attempts are
    identical in *every* column -- same `action_id`, same `attempt`, and a clock that gave them
    the same `created_at`, `updated_at` and `lease_expires_at` -- no comparison can separate
    "our commit landed" from "somebody else's identical commit landed", because the record is the
    same record. §4.3.3 asked the store to fail closed there, and that is not implementable: the
    identical case *is* the indistinguishable case, so failing closed on it would refuse every
    ambiguous commit that actually landed and make the re-read pointless.

    What closes it is upstream: **`action_id` identifies one attempt**, `Action` generates a fresh
    one per attempt, and two concurrent attempts sharing one is a caller-side violation no store
    can defend against -- `action_id` is the store's whole notion of who holds a key. The columns
    here are what make the *realistic* collision detectable: a second attempt under the same id
    at any other instant, with any other lease, or at a different `attempt` number, differs in a
    column and is refused. SPEC-v0.6 §4.3.3 carries the argument.
    """
    return (
        found.effect_key == expected.effect_key
        and found.action_id == expected.action_id
        and found.state is expected.state
        and found.attempt == expected.attempt
        and found.lease_expires_at == expected.lease_expires_at
        and found.created_at == expected.created_at
        and found.updated_at == expected.updated_at
    )


class PostgresStateStore:
    """Approvals, effects and evidence in a Postgres schema (SPEC-v0.6 §4).

    One connection per thread, as `SQLiteStateStore` does: `psycopg` connections are not
    thread-safe, and the thread-local shape is the one `close()` is already specified against
    (§2.7).

    **A connection outlives the thread that opened it, and that is a real limit worth stating.**
    A host running agents on a *bounded, recycled* thread pool is fine -- the same threads keep
    reusing the same connections. A host that starts a fresh thread per unit of work accumulates
    one connection per thread that ever touched the store, and Postgres connections are far
    scarcer than SQLite file handles: `max_connections` defaults to 100. `close()` releases every
    one, so the mitigation is to close a store you are done with. The conformance suite met this
    for real -- ninety-six connections across twelve rounds of eight threads -- and the failure
    surfaced in a case that had nothing to do with it.

    No pool ships. An operator may put pgbouncer in front in **transaction** mode, and it works
    because this store holds nothing session-scoped: no advisory lock, no temp table, no prepared
    statement it depends on surviving, no `SET`. That is the second reason §4.2.1 rejected
    advisory locks, and it is a property worth keeping deliberately rather than by luck.
    """

    def __init__(
        self,
        url: str,
        *,
        clock: Callable[[], datetime] = _utc_now,
        schema: str = "public",
    ) -> None:
        if not url:
            raise InvalidArgument("a Postgres store needs a connection URL")
        if not schema or not schema.replace("_", "").isalnum():
            raise InvalidArgument(f"schema must be a plain identifier, got {schema!r}")
        self._url = url
        self._schema = schema
        self._clock = clock
        self._local = threading.local()
        self._open: set[Any] = set()
        self._open_lock = threading.Lock()
        connection = self._connection()
        self._refuse_without_ddl_rights(connection)
        migrate(connection, self._clock(), dialect="postgres")

    @staticmethod
    def create_schema(url: str, schema: str) -> None:
        """Create a schema for a store to live in, if it is not there.

        Used by the conformance backend and by `ctrlrun verify`'s scratch store (§4.1), which
        needs a schema of its own precisely so that verify never migrates or writes to the
        operator's. A plain identifier only: this is the one place a name reaches SQL.
        """
        if not schema or not schema.replace("_", "").isalnum():
            raise InvalidArgument(f"schema must be a plain identifier, got {schema!r}")
        psycopg = _psycopg()
        connection = psycopg.connect(url, autocommit=True)
        try:
            connection.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        finally:
            connection.close()

    @staticmethod
    def drop_schema(url: str, schema: str) -> None:
        """Remove a schema and everything in it. Verify's scratch store is dropped when the run
        ends, including when it ends by exception (§4.1)."""
        if not schema or not schema.replace("_", "").isalnum():
            raise InvalidArgument(f"schema must be a plain identifier, got {schema!r}")
        psycopg = _psycopg()
        connection = psycopg.connect(url, autocommit=True)
        try:
            connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        finally:
            connection.close()

    @property
    def _q(self) -> str:
        """This store's schema, quoted, for every table reference (§4.4).

        **Every statement names its schema, so nothing depends on `search_path` at all.** A
        connect-time `SET` is *session* state, and a review found it contradicting §4.4's claim
        that the store holds none: under pgbouncer transaction pooling a later transaction may
        land on a server connection that never received it, resolving to `"$user", public` --
        which for a scratch-schema store (verify, the conformance backend) means reading and
        writing the **operator's** `public` tables, the precise hazard §4.1 exists to close.
        Reads made it worse, because they run outside any transaction and so could not even be
        fixed with `SET LOCAL`.

        The schema is validated as a plain identifier at construction, which is what makes it
        safe to interpolate: it is the one name in this module that reaches SQL, and it never
        comes from an action, an argument or a header.
        """
        return f'"{self._schema}"'

    def _refuse_without_ddl_rights(self, connection: Any) -> None:
        """Refuse at open, naming the missing privilege, rather than reporting the SQL (§3.6).

        SPEC-v0.6 T154f asks for exactly this and it did not exist: a role without `CREATE` on
        the schema got a raw `psycopg.errors.InsufficientPrivilege` out of the constructor, with
        the failing `CREATE TABLE` quoted verbatim, uncatchable by any `except CTRLRunError` in
        this codebase. A migration needs DDL rights at least on the first start after an upgrade,
        and `docs/postgres.md` says so; an operator who has not granted them deserves to be told
        which grant is missing, not which statement failed.
        """
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_schema(), current_user")
            row = cursor.fetchone()
            schema, user = (str(row[0]) if row and row[0] else None), str(row[1]) if row else "?"
            if schema is None:
                raise InvalidArgument(
                    f"schema {self._schema!r} does not exist, or {user} cannot see it. CTRLRun "
                    "creates no schema for an operator: create it, or point --store-url at one "
                    "that exists (SPEC-v0.6 §4.1)"
                )
            cursor.execute("SELECT has_schema_privilege(%s, 'CREATE')", (schema,))
            allowed = cursor.fetchone()
        if not (allowed and allowed[0]):
            raise InvalidArgument(
                f"the database user {user!r} has no CREATE privilege on schema {schema!r}, so "
                "CTRLRun cannot apply its migrations. A store is opened un-migrated by nothing "
                "(SPEC-v0.6 §3.6), so this is refused at open rather than discovered at the "
                f'first write. Grant it with: GRANT CREATE ON SCHEMA "{schema}" TO "{user}"'
            )

    # --- connections ------------------------------------------------------------------

    def _connect(self) -> Any:
        psycopg = _psycopg()
        # **Autocommit, with every write taking an explicit `BEGIN`.** With `autocommit=False`
        # every `SELECT` began a transaction that nothing ended: a review measured the store
        # sitting `idle in transaction` after a plain `get_effect`, which blocks `DROP SCHEMA`
        # forever (verify hung whenever a guarantee failed), holds `xmin` against vacuum, holds
        # a transaction open across the executor's run -- the cost §4.2.1 rejected
        # `SELECT … FOR UPDATE` to avoid -- and makes §4.4's pgbouncer transaction-mode claim
        # impossible, since a transaction that never ends is never returned to the pool. It also
        # let one failing statement poison the connection permanently, so every later call
        # raised `InFailedSqlTransaction` until some write path's handler happened to roll back.
        connection = psycopg.connect(self._url, autocommit=True)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_encoding_to_char(encoding) FROM pg_database "
                "WHERE datname = current_database()"
            )
            row = cursor.fetchone()
            encoding = str(row[0]) if row else "?"
            if encoding.upper() != "UTF8":
                connection.close()
                # SPEC-v0.6 §4.4. `v0.1 §2.3` hashes the exact code points it is given and applies
                # no normalization, so an effect key that survives a round trip as different bytes
                # is a DIFFERENT IDENTITY -- two attempts at one logical effect would reserve two
                # keys and both execute. That is a double execution reached through the storage
                # layer's character set, so it is refused at open rather than discovered.
                raise InvalidArgument(
                    f"this database's server_encoding is {encoding}, not UTF8. CTRLRun hashes "
                    "the exact code points it is given (v0.1 §2.3), so a lossy encoding makes "
                    "one logical effect into two identities and both would execute"
                )
            # `search_path` is set **per transaction**, not once per session, and §4.4's claim
            # that this store holds nothing session-scoped is now true. A review found the `SET`
            # here contradicting it: under pgbouncer transaction pooling a later transaction may
            # land on a server connection that never received it, resolving to `"$user", public`
            # -- which for a scratch-schema store (verify, the conformance backend) means reading
            # and writing the **operator's** `public` tables, the precise hazard §4.1 exists to
            # close.
            cursor.execute(f'SET search_path TO "{self._schema}"')
        return connection

    def _use_schema(self, connection: Any) -> None:
        """Re-assert `search_path` inside the current transaction (§4.4).

        `SET LOCAL` reverts at the end of the transaction, so it cannot leak into a pooled
        connection's next tenant, and it is re-issued by every write. Reads run outside a
        transaction and rely on the connect-time `SET`, which is correct for a dedicated
        connection and is why the pooling claim is stated as transaction-mode only.
        """
        connection.execute(f'SET LOCAL search_path TO "{self._schema}"')

    def _connection(self) -> Any:
        connection = getattr(self._local, "connection", None)
        if connection is not None and not connection.closed:
            with self._open_lock:
                if connection in self._open:
                    return connection
        connection = self._connect()
        self._local.connection = connection
        with self._open_lock:
            self._open.add(connection)
        return connection

    def close(self) -> None:
        """Release every connection this store opened.

        `close()` is a release of resources and **not a fence** (SPEC-v0.6 §2.7): a caller that
        uses the store afterwards gets a fresh connection. Making it a fence would have made it
        the fault-injection hook §2.5 refuses to add.
        """
        with self._open_lock:
            connections, self._open = self._open, set()
        for connection in connections:
            # Closing a connection that is already broken is not an error.
            with contextlib.suppress(Exception):
                connection.close()
        self._local = threading.local()

    # --- the two ambiguities ------------------------------------------------------------

    def _commit(self, connection: Any) -> None:
        """`COMMIT`, mapping what happened onto §4.3's Table A.

        A stated abort (`40001`, `40P01`) is the server telling us in band that it rolled back:
        nothing committed, the store write is `FAILED`, and it may be retried. Anything else
        raised by `COMMIT` means nobody knows.
        """
        try:
            connection.commit()
        except BaseException as broke:
            # `BaseException`, like every other handler here: a `KeyboardInterrupt` arriving
            # during `COMMIT` is the ambiguous case by definition, and letting it escape
            # unclassified would skip the re-read.
            if isinstance(broke, Exception) and _is_stated_abort(broke):
                raise
            raise AmbiguousWrite(str(broke)) from broke

    def _fresh_read_effect(self, effect_key: str) -> EffectRecord | None:
        """Re-read on a **fresh** connection: the old one is not trustworthy and may be unusable.

        A failure here is not caught. §4.3.2: only if the re-read itself fails does the store
        refuse to let execution proceed, writing no effect state and failing closed. That costs
        availability -- an effect key may be reserved by an attempt that will never execute, and
        its lease will lapse to `AMBIGUOUS` and need a human -- and it never costs a double
        execution, which is the trade this library exists to make.
        """
        connection = self._connect()
        try:
            return self._read_effect(connection, effect_key)
        finally:
            with contextlib.suppress(Exception):
                connection.close()

    def _rollback(self, connection: Any) -> None:
        # A broken connection cannot roll back; the server has already discarded the
        # transaction, which is the outcome the rollback was for.
        with contextlib.suppress(Exception):
            connection.rollback()

    # --- rows -------------------------------------------------------------------------

    def _read_effect(self, connection: Any, effect_key: str) -> EffectRecord | None:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT effect_key, state, action_id, attempt, lease_expires_at, result_json, "
                "error, created_at, updated_at, resolved_by "
                f"FROM {self._q}.effects WHERE effect_key = %s",
                (effect_key,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return EffectRecord(
            effect_key=str(row[0]),
            state=EffectState(row[1]),
            action_id=str(row[2]),
            attempt=int(row[3]),
            lease_expires_at=_at(row[4]),
            result=_result_value(row[5]),
            error=row[6],
            created_at=datetime.fromisoformat(str(row[7])),
            updated_at=datetime.fromisoformat(str(row[8])),
            resolved_by=row[9],
        )

    def _read_approval(self, connection: Any, approval_id: str) -> ApprovalRecord | None:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT approval_id, action_hash, status, action_json, approver, created_at, "
                f"granted_at, expires_at, consumed_at FROM {self._q}.approvals WHERE "
                f"approval_id = %s",
                (approval_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return ApprovalRecord(
            request=ApprovalRequest(
                request_id=str(row[0]),
                action_hash=str(row[1]),
                action=_action_from_json(str(row[3])),
                created_at=datetime.fromisoformat(str(row[5])),
                expires_at=datetime.fromisoformat(str(row[7])),
            ),
            status=ApprovalStatus(row[2]),
            approver=row[4],
            granted_at=_at(row[6]),
            consumed_at=_at(row[8]),
        )

    # --- reservation (SPEC-v0.6 §4.2) ---------------------------------------------------

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

    def consume_approval(self, approval_id: str, action_hash: str) -> Approval:
        approval, _ = self._authorize_and_reserve(
            approval_id, action_hash, None, None, DEFAULT_LEASE
        )
        return _only(approval, "approval")

    def _authorize_and_reserve(
        self,
        approval_id: str | None,
        action_hash: str | None,
        effect_key: str | None,
        action_id: str | None,
        lease: timedelta,
        *,
        retrying: bool = False,
    ) -> tuple[Approval | None, Reservation | None]:
        """Consume an approval, reserve an effect, or both, in ONE transaction.

        §4.2, and `v0.1 §4.2 A4`.

        The approval is checked first, so its refusal is the one raised when a replayed approval
        and a duplicate effect both apply (T4). Nothing is written until both have been decided,
        so a refused reservation rolls back to an approval that is still granted (T12).
        """
        connection = self._connection()
        now = self._clock()
        connection.execute("BEGIN")
        self._use_schema(connection)
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
        except AmbiguousWrite:
            raise
        except BaseException:
            self._rollback(connection)
            raise
        try:
            self._commit(connection)
        except AmbiguousWrite:
            # §4.3.2. Re-read on a fresh connection and obey what it says.
            if retrying:
                # §4.2's bound, and it is one: a second ambiguous commit on the same operation is
                # not a race this protocol produces, so it fails closed rather than recursing.
                # *Every loop in this project is bounded*, and a re-read path is a loop.
                raise
            if effect_key is None or plan.reservation is None:
                raise
            if plan.renews:
                self._resolve_lost_renewal(effect_key, plan.reservation, now, lease)
            else:
                self._resolve_lost_insert(
                    effect_key,
                    plan.reservation,
                    now,
                    approval_id=approval_id,
                    action_hash=action_hash,
                    lease=lease,
                )
        return (approved.as_approval() if approved is not None else None), plan.reservation

    def _resolve_lost_renewal(
        self, effect_key: str, reservation: Reservation, now: datetime, lease: timedelta
    ) -> None:
        """§4.3.2 Table **A2**, for the one reservation that is an `UPDATE`.

        A renewal's pre-state is `FAILED` (`v0.1 §5.4`'s one automatic retry). If the commit
        landed the record is ours and `RESERVED`; if it did not, the record is still `FAILED` and
        the renewal simply re-issues -- safe because that `UPDATE` is conditional on
        `state = 'failed'`.

        Routing a renewal through Table A1 turned a **proven non-execution** into a refusal
        carrying a `state` that misdescribed the record: the collapse §4.3.2 exists to forbid,
        found by review.
        """
        found = self._fresh_read_effect(effect_key)
        if found is None:
            raise InvalidArgument(f"no reservation for effect {effect_key!r}")
        if found.action_id == reservation.action_id and found.state is EffectState.RESERVED:
            _took(A2_LANDED, effect_key)
            return  # the commit landed
        if found.state is EffectState.FAILED:
            _took(A2_REISSUE, effect_key)
            self._authorize_and_reserve(
                None, None, effect_key, reservation.action_id, lease, retrying=True
            )
            return
        _took(A2_REFUSE, effect_key)
        plan = plan_reservation(found, effect_key, reservation.action_id, lease, now)
        if plan.refusal is not None:
            raise plan.refusal

    def _resolve_lost_insert(
        self,
        effect_key: str,
        reservation: Reservation,
        now: datetime,
        *,
        approval_id: str | None,
        action_hash: str | None,
        lease: timedelta,
    ) -> None:
        """§4.3.2 Table A1: what a lost `COMMIT` on the reservation `INSERT` means.

        The first row is an **identity check on the whole row we attempted to write**, not a match
        on `action_id` -- and the difference is a double execution. `Action.action_id` is
        caller-supplyable and §5.1 contemplates two attempts sharing one, so "a record carrying
        our `action_id`" is satisfied by *another process's live reservation*. Anything that is
        not byte-for-byte our own write goes back through `plan_reservation`, which is the
        function §4.1 promises every backend decides with.
        """
        found = self._fresh_read_effect(effect_key)
        if found is None:
            # The commit did not land. Retry the SAME operation, once (§4.2 step 5) -- with the
            # approval if there was one, and with the caller's lease. Retrying a narrower
            # operation was a double-spend: the effect was reserved, the caller was handed an
            # `Approval`, and the approval row was still `granted`, so the same approval then
            # authorised a second effect key. Found by review.
            _took(A1_REINSERT, effect_key)
            self._authorize_and_reserve(
                approval_id, action_hash, effect_key, reservation.action_id, lease, retrying=True
            )
            return
        expected = _reserved(reservation, None, now)
        if _is_our_own_write(found, expected):
            _took(A1_OURS, effect_key)
            return  # the commit landed; we hold it
        _took(A1_REFUSE, effect_key)
        plan = plan_reservation(found, effect_key, reservation.action_id, lease, now)
        if plan.refusal is not None:
            raise plan.refusal
        raise DuplicateEffect(
            f"effect {effect_key!r} could not be resolved after a lost commit",
            state=IN_PROGRESS_EFFECT,
            effect_key=effect_key,
        )

    def _consumable(
        self, connection: Any, approval_id: str, action_hash: str, now: datetime
    ) -> ApprovalRecord:
        verdict = check_consumable(
            self._read_approval(connection, approval_id), approval_id, action_hash, now
        )
        if verdict.refusal is not None:
            if verdict.expire:
                # §4.2.2's second kept write: a lapsed approval is evidence. Its own transaction,
                # ordered before the refusing one.
                self._expire(approval_id)
            raise verdict.refusal
        return _only(verdict.record, "approval record")

    def _expire(self, approval_id: str) -> None:
        connection = self._connect()
        connection.execute("BEGIN")
        self._use_schema(connection)
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"UPDATE {self._q}.approvals SET status = %s WHERE approval_id = %s",
                    (str(ApprovalStatus.EXPIRED), approval_id),
                )
            self._commit(connection)
        finally:
            with contextlib.suppress(Exception):
                connection.close()

    def _plan(
        self, connection: Any, effect_key: str, action_id: str, lease: timedelta, now: datetime
    ) -> ReservationPlan:
        record = self._read_effect(connection, effect_key)
        plan = plan_reservation(record, effect_key, action_id, lease, now)
        if plan.refusal is not None:
            if plan.ambiguate and record is not None:
                # §4.2.2's first kept write, in its own transaction: the expired lease becomes
                # AMBIGUOUS and that write is kept even though this attempt is refused.
                self._ambiguate(record, now)
            raise plan.refusal
        return plan

    def _ambiguate(self, record: EffectRecord, now: datetime) -> None:
        connection = self._connect()
        connection.execute("BEGIN")
        self._use_schema(connection)
        try:
            self._write_effect(
                connection,
                _transitioned(record, EffectState.AMBIGUOUS, now, error=LEASE_EXPIRED),
                record,
            )
            self._commit(connection)
        finally:
            with contextlib.suppress(Exception):
                connection.close()

    def _reserve_locked(
        self, connection: Any, reservation: Reservation, renews: bool, now: datetime
    ) -> None:
        previous = self._read_effect(connection, reservation.effect_key)
        record = _reserved(reservation, previous, now)
        if renews:
            # Only a FAILED record is renewable (§5.4); the WHERE clause says so again, so a
            # record that changed under us refuses instead of overwriting an attempt.
            with connection.cursor() as cursor:
                cursor.execute(
                    f"UPDATE {self._q}.effects SET "
                    f"state=%s, action_id=%s, attempt=%s, lease_expires_at=%s, "
                    "result_json=NULL, error=NULL, updated_at=%s "
                    "WHERE effect_key=%s AND state=%s",
                    (
                        str(record.state),
                        record.action_id,
                        record.attempt,
                        _iso(record.lease_expires_at) if record.lease_expires_at else None,
                        _iso(now),
                        record.effect_key,
                        str(EffectState.FAILED),
                    ),
                )
                updated = cursor.rowcount
            if updated != 1:
                raise DuplicateEffect(
                    f"effect {record.effect_key!r} was taken by another attempt",
                    state=IN_PROGRESS_EFFECT,
                    effect_key=record.effect_key,
                )
            return
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {self._q}.effects("
                "effect_key, state, action_id, attempt, lease_expires_at, "
                "result_json, error, created_at, updated_at) "
                "VALUES(%s,%s,%s,%s,%s,NULL,NULL,%s,%s) ON CONFLICT (effect_key) DO NOTHING",
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
            inserted = cursor.rowcount
        if inserted == 1:
            return
        # §4.2 step 5: another transaction inserted between our read and our insert. Re-read and
        # re-plan, ONCE. A second zero would mean the winner's record vanished, which nothing in
        # this protocol can do -- there is no DELETE on `effects` anywhere -- so it is a corrupted
        # database or somebody at a psql prompt, and the fail-closed answer is to raise.
        again = self._read_effect(connection, record.effect_key)
        plan = plan_reservation(again, record.effect_key, record.action_id, DEFAULT_LEASE, now)
        if plan.refusal is not None:
            raise plan.refusal
        raise DuplicateEffect(
            f"effect {record.effect_key!r} is already reserved",
            state=IN_PROGRESS_EFFECT,
            effect_key=record.effect_key,
        )

    def _consume_locked(self, connection: Any, approval_id: str, now: datetime) -> None:
        """`granted -> consumed`, as a compare-and-set (`v0.1 §4.2 A2`, §4.2).

        **Unconditional, this was a double-spend.** `_consumable` reads with a plain `SELECT`
        under `READ COMMITTED`, so N transactions all saw `GRANTED`, all wrote `CONSUMED`, all
        committed, and each reserved its own effect key: a review measured **8 of 8** contenders
        consuming one approval against Postgres, where SQLite consumes 1 of 8. One human's *yes*
        authorised eight refunds. SQLite is safe only because `BEGIN IMMEDIATE` wraps its read
        and its write together; with no such lock the condition has to be in the statement.
        """
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {self._q}.approvals SET status=%s, consumed_at=%s WHERE "
                f"approval_id=%s AND status=%s",
                (
                    str(ApprovalStatus.CONSUMED),
                    _iso(now),
                    approval_id,
                    str(ApprovalStatus.GRANTED),
                ),
            )
            if cursor.rowcount != 1:
                raise ApprovalMismatch(
                    f"approval {approval_id} was consumed by another attempt", reason="consumed"
                )

    def _write_effect(
        self, connection: Any, record: EffectRecord, was: EffectRecord | None = None
    ) -> None:
        """Write a record, conditional on the one we read still being there (§4.2).

        **Unconditional, this erased an unknown outcome.** §4.2 names `resolve_effect`,
        `extend_lease` and `hold_continuation` among the transitions that become a conditional
        `UPDATE` with the row count checked; they went through this method, which had neither. A
        review opened the window deliberately and watched a `mark_ambiguous` committed by another
        process get overwritten by `extend_lease` -- the record ended `EXECUTING`, lease extended
        an hour, `error` cleared, so **the unknown outcome that needed a human was gone** and the
        effect could then be committed normally. SQLite cannot reach that state, because its read
        and its write are inside one `BEGIN IMMEDIATE`.

        `was` is the record this write was planned against. It is optional only so the one caller
        that has already established the pre-state under a row lock need not repeat it.
        """
        expected = was if was is not None else record
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {self._q}.effects SET "
                f"state=%s, action_id=%s, attempt=%s, lease_expires_at=%s, "
                "result_json=%s, error=%s, updated_at=%s, resolved_by=%s "
                "WHERE effect_key=%s AND action_id=%s AND state=%s",
                (
                    str(record.state),
                    record.action_id,
                    record.attempt,
                    _iso(record.lease_expires_at) if record.lease_expires_at else None,
                    _result_json(record.result),
                    record.error,
                    _iso(record.updated_at),
                    record.resolved_by,
                    record.effect_key,
                    expected.action_id,
                    str(expected.state),
                ),
            )
            if cursor.rowcount != 1:
                found = self._read_effect(connection, record.effect_key)
                if found is None:
                    raise InvalidArgument(f"no reservation for effect {record.effect_key!r}")
                # Let the predicate the caller used say what it says now, so the exception
                # taxonomy is the one every test written for SQLite already asserts.
                _checked(
                    found,
                    record.effect_key,
                    expected.action_id,
                    frozenset({expected.state}),
                    self._clock(),
                )
                raise DuplicateEffect(
                    f"effect {record.effect_key!r} was taken by another attempt",
                    state=IN_PROGRESS_EFFECT,
                    effect_key=record.effect_key,
                )

    # --- transitions (SPEC-v0.6 §4.2, §4.3.2 Table A2) ----------------------------------

    def begin_execution(self, effect_key: str, action_id: str) -> None:
        self._transition(effect_key, action_id, EffectState.EXECUTING, _RESERVED)

    def commit_effect(self, effect_key: str, action_id: str, result: Any) -> None:
        self._transition(effect_key, action_id, EffectState.COMMITTED, _EXECUTING, result=result)

    def fail_effect(self, effect_key: str, action_id: str, error: str) -> None:
        self._transition(effect_key, action_id, EffectState.FAILED, _EXECUTING, error=error)

    def mark_ambiguous(self, effect_key: str, action_id: str, error: str) -> None:
        self._transition(effect_key, action_id, EffectState.AMBIGUOUS, _UNFINISHED, error=error)

    def _transition(
        self,
        effect_key: str,
        action_id: str,
        state: EffectState,
        expected: frozenset[EffectState],
        *,
        result: Any = None,
        error: str | None = None,
        retrying: bool = False,
    ) -> None:
        """One compare-and-set, with the row count checked (§4.2).

        A `rowcount` of zero re-reads and runs the same `_checked` predicate SQLite runs, so the
        exception taxonomy is preserved: `AmbiguousEffect` where the record went ambiguous,
        `DuplicateEffect` where another attempt holds it, `InvalidArgument` where the transition
        was never possible. That taxonomy is asserted by tests written for SQLite which now run
        against both backends, which is why they had to be written first.
        """
        connection = self._connection()
        now = self._clock()
        connection.execute("BEGIN")
        self._use_schema(connection)
        try:
            record = _checked(
                self._read_effect(connection, effect_key), effect_key, action_id, expected, now
            )
            moved = _transitioned(record, state, now, result=result, error=error)
            with connection.cursor() as cursor:
                cursor.execute(
                    f"UPDATE {self._q}.effects SET "
                    f"state=%s, action_id=%s, attempt=%s, lease_expires_at=%s, "
                    "result_json=%s, error=%s, updated_at=%s "
                    "WHERE effect_key=%s AND action_id=%s AND state=%s",
                    (
                        str(moved.state),
                        moved.action_id,
                        moved.attempt,
                        _iso(moved.lease_expires_at) if moved.lease_expires_at else None,
                        _result_json(moved.result),
                        moved.error,
                        _iso(moved.updated_at),
                        effect_key,
                        action_id,
                        str(record.state),
                    ),
                )
                updated = cursor.rowcount
            if updated != 1:
                # The record changed between the read and the write. Re-plan through the same
                # predicate rather than guessing.
                _checked(
                    self._read_effect(connection, effect_key), effect_key, action_id, expected, now
                )
                raise DuplicateEffect(
                    f"effect {effect_key!r} was taken by another attempt",
                    state=IN_PROGRESS_EFFECT,
                    effect_key=effect_key,
                )
        except AmbiguousWrite:
            raise
        except BaseException:
            self._rollback(connection)
            raise
        try:
            self._commit(connection)
        except AmbiguousWrite:
            if retrying:
                # §4.2's bound, the one `_authorize_and_reserve` already had and this path did
                # not. Table A2's re-issue calls back into `_transition`, whose `COMMIT` can be
                # lost again, which re-entered the same resolution with nothing to stop it.
                # Driven by a proxy that swallows every `COMMIT`, it recursed 96 deep and escaped
                # as `RecursionError` -- outside this library's closed set of errors, so no caller
                # could classify it -- leaving the record stranded `EXECUTING`. Found by review.
                # *Every loop in this project is bounded*, and a re-read path is a loop.
                raise
            self._resolve_lost_update(effect_key, action_id, state, expected, result, error)

    def _resolve_lost_update(
        self,
        effect_key: str,
        action_id: str,
        state: EffectState,
        expected: frozenset[EffectState],
        result: Any,
        error: str | None,
    ) -> None:
        """§4.3.2 Table A2: a lost `COMMIT` on a compare-and-set.

        The record **always exists** here, so Table A1's "no record" row is unreachable and its
        "our id, a different state" row is exactly wrong: a lost `COMMIT` on an `UPDATE` leaves the
        record in its **pre-state**, which is our `action_id` in a state we were not writing.
        Reading that as *somebody moved it, refuse* would turn a committed effect into a human's
        problem and a **proven** non-execution into `AMBIGUOUS`, throwing away the one automatic
        retry `v0.1 §5.4` grants. Neither is unsafe; both would make the store useless in exactly
        the situation it was built for.

        Re-issuing is safe because the `UPDATE` is conditional on the pre-state: if the first one
        did land, the second matches nothing and the next re-read sees row 1.
        """
        found = self._fresh_read_effect(effect_key)
        if found is None:
            raise InvalidArgument(f"no reservation for effect {effect_key!r}")
        if found.state is state and found.action_id == action_id:
            _took(A2_LANDED, effect_key)
            return  # the commit landed
        if found.action_id == action_id and found.state in expected:
            _took(A2_REISSUE, effect_key)
            self._transition(
                effect_key, action_id, state, expected, result=result, error=error, retrying=True
            )
            return
        _took(A2_REFUSE, effect_key)
        _checked(found, effect_key, action_id, expected, self._clock())

    def resolve_effect(self, effect_key: str, state: EffectState, resolver: str) -> EffectRecord:
        resolver = _approver(resolver)
        connection = self._connection()
        now = self._clock()
        connection.execute("BEGIN")
        self._use_schema(connection)
        try:
            record = _resolvable(self._read_effect(connection, effect_key), effect_key, state)
            resolved = _resolved(record, state, resolver, now)
            self._write_effect(connection, resolved, record)
        except BaseException:
            self._rollback(connection)
            raise
        self._commit(connection)
        return resolved

    def extend_lease(self, effect_key: str, action_id: str, until: datetime) -> None:
        connection = self._connection()
        now = self._clock()
        connection.execute("BEGIN")
        self._use_schema(connection)
        try:
            record = self._read_effect(connection, effect_key)
            # `plan_lease_extension` returns the record to write, or raises. It is not a plan
            # object; treating it as one is how the conformance suite -- written before this
            # backend existed -- caught this on its first run.
            extended = plan_lease_extension(record, effect_key, action_id, until, now)
            self._write_effect(connection, extended, _only(record, "effect record"))
        except BaseException:
            self._rollback(connection)
            raise
        self._commit(connection)

    def get_effect(self, effect_key: str) -> EffectRecord | None:
        return self._read_effect(self._connection(), effect_key)

    def list_effects(self, state: EffectState | None = None) -> tuple[EffectRecord, ...]:
        connection = self._connection()
        with connection.cursor() as cursor:
            if state is None:
                cursor.execute(
                    f"SELECT effect_key FROM {self._q}.effects ORDER BY created_at, effect_key"
                )
            else:
                cursor.execute(
                    f"SELECT effect_key FROM {self._q}.effects WHERE state = %s "
                    "ORDER BY created_at, effect_key",
                    (str(state),),
                )
            keys = [str(row[0]) for row in cursor.fetchall()]
        found = (self._read_effect(connection, key) for key in keys)
        return tuple(record for record in found if record is not None)

    # --- approvals (v0.1 §4.2) -----------------------------------------------------------

    def put_approval_request(self, request: ApprovalRequest) -> None:
        connection = self._connection()
        connection.execute("BEGIN")
        self._use_schema(connection)
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"INSERT INTO {self._q}.approvals("
                    "approval_id, action_hash, status, action_json, "
                    "approver, created_at, granted_at, expires_at, consumed_at) "
                    "VALUES(%s,%s,%s,%s,NULL,%s,NULL,%s,NULL)",
                    (
                        request.request_id,
                        request.action_hash,
                        str(ApprovalStatus.PENDING),
                        _action_json(request.action),
                        _iso(request.created_at),
                        _iso(request.expires_at),
                    ),
                )
        except Exception as duplicate:
            self._rollback(connection)
            raise InvalidArgument(
                f"approval request {request.request_id!r} already exists"
            ) from duplicate
        self._commit(connection)

    def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        return self._read_approval(self._connection(), approval_id)

    def approvals_for(self, action_hash: str) -> tuple[ApprovalRecord, ...]:
        connection = self._connection()
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT approval_id FROM {self._q}.approvals WHERE "
                f"action_hash = %s ORDER BY created_at, "
                "approval_id",
                (action_hash,),
            )
            ids = [str(row[0]) for row in cursor.fetchall()]
        found = (self._read_approval(connection, item) for item in ids)
        return tuple(record for record in found if record is not None)

    def find_granted_approval(self, action_hash: str) -> Approval | None:
        from .state import _newest_granted

        return _newest_granted(self.approvals_for(action_hash), action_hash, self._clock())

    def find_denied_request(self, action_hash: str) -> ApprovalRequest | None:
        from .state import _newest_denied

        return _newest_denied(self.approvals_for(action_hash), action_hash, self._clock())

    def grant_approval(self, approval_id: str, approver: str) -> Approval:
        approver = _approver(approver)
        connection = self._connection()
        now = self._clock()
        connection.execute("BEGIN")
        self._use_schema(connection)
        try:
            record = self._answerable(connection, approval_id, now)
            granted = replace(
                record, status=ApprovalStatus.GRANTED, approver=approver, granted_at=now
            )
            with connection.cursor() as cursor:
                # Conditional on what `check_answerable` saw. Unconditional, a concurrent
                # `deny_approval` was silently overwritten and `find_granted_approval` then
                # returned an approval a human had refused.
                cursor.execute(
                    f"UPDATE {self._q}.approvals SET status=%s, approver=%s, granted_at=%s "
                    "WHERE approval_id=%s AND status=%s",
                    (
                        str(ApprovalStatus.GRANTED),
                        approver,
                        _iso(now),
                        approval_id,
                        str(record.status),
                    ),
                )
                if cursor.rowcount != 1:
                    raise ApprovalMismatch(
                        f"approval {approval_id} was answered by somebody else first",
                        reason="answered",
                    )
        except BaseException:
            self._rollback(connection)
            raise
        self._commit(connection)
        return granted.as_approval()

    def deny_approval(self, approval_id: str, approver: str) -> None:
        approver = _approver(approver)
        connection = self._connection()
        now = self._clock()
        connection.execute("BEGIN")
        self._use_schema(connection)
        try:
            record = self._answerable(connection, approval_id, now)
            with connection.cursor() as cursor:
                cursor.execute(
                    f"UPDATE {self._q}.approvals SET status=%s, approver=%s "
                    "WHERE approval_id=%s AND status=%s",
                    (str(ApprovalStatus.DENIED), approver, approval_id, str(record.status)),
                )
                if cursor.rowcount != 1:
                    raise ApprovalMismatch(
                        f"approval {approval_id} was answered by somebody else first",
                        reason="answered",
                    )
        except BaseException:
            self._rollback(connection)
            raise
        self._commit(connection)

    def _answerable(self, connection: Any, approval_id: str, now: datetime) -> ApprovalRecord:
        verdict = check_answerable(self._read_approval(connection, approval_id), approval_id, now)
        if verdict.refusal is not None:
            if verdict.expire:
                self._expire(approval_id)
            raise verdict.refusal
        return _only(verdict.record, "approval record")

    # --- continuations (v0.2 §6.9.2) -----------------------------------------------------

    def hold_continuation(
        self, action: Action, effect_key: str, continuation: str, until: datetime
    ) -> int:
        connection = self._connection()
        now = self._clock()
        connection.execute("BEGIN")
        self._use_schema(connection)
        try:
            record = self._read_effect(connection, effect_key)
            extended = plan_lease_extension(record, effect_key, action.action_id, until, now)
            self._write_effect(connection, extended, _only(record, "effect record"))
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT rounds FROM {self._q}.continuations WHERE effect_key=%s", (effect_key,)
                )
                row = cursor.fetchone()
                rounds = (int(row[0]) if row else 0) + 1
                cursor.execute(
                    f"INSERT INTO {self._q}.continuations("
                    "effect_key, action_id, action_json, continuation, "
                    "rounds, updated_at) VALUES(%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (effect_key) DO UPDATE SET action_id=EXCLUDED.action_id, "
                    "action_json=EXCLUDED.action_json, continuation=EXCLUDED.continuation, "
                    "rounds=EXCLUDED.rounds, updated_at=EXCLUDED.updated_at",
                    (
                        effect_key,
                        action.action_id,
                        _action_json(action),
                        continuation,
                        rounds,
                        _iso(now),
                    ),
                )
        except BaseException:
            self._rollback(connection)
            raise
        self._commit(connection)
        return rounds

    def take_continuation(self, continuation: str) -> HeldContinuation:
        import hmac

        from .state import _NO_SUCH_CONTINUATION, _continuable

        connection = self._connection()
        connection.execute("BEGIN")
        self._use_schema(connection)
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT effect_key, action_json, continuation, rounds FROM "
                    f"{self._q}.continuations "
                    "WHERE continuation <> ''"
                )
                rows = cursor.fetchall()
            found = next(
                (row for row in rows if hmac.compare_digest(str(row[2]), continuation)), None
            )
            if found is None:
                raise InvalidArgument(_NO_SUCH_CONTINUATION)
            effect_key = str(found[0])
            record = _continuable(
                self._read_effect(connection, effect_key), effect_key, self._clock()
            )
            with connection.cursor() as cursor:
                # Consumed in the transaction that admits it, and **conditionally**. Without the
                # `continuation` guard and the row count this was a plain double execution: a
                # review measured 8 of 8 concurrent callers taking one continuation against
                # Postgres (1 of 8 on SQLite), and four concurrent `Control.resume()` calls
                # running the executor four times. SQLite's identical statement is safe only
                # because `BEGIN IMMEDIATE` holds the write lock around it.
                cursor.execute(
                    f"UPDATE {self._q}.continuations SET continuation='' "
                    "WHERE effect_key=%s AND continuation=%s",
                    (effect_key, continuation),
                )
                if cursor.rowcount != 1:
                    raise InvalidArgument(_NO_SUCH_CONTINUATION)
            held = HeldContinuation(
                _action_from_json(str(found[1])), effect_key, record, int(found[3])
            )
        except BaseException:
            self._rollback(connection)
            raise
        self._commit(connection)
        return held

    def continuation_rounds(self, effect_key: str) -> int:
        with self._connection().cursor() as cursor:
            cursor.execute(
                f"SELECT rounds FROM {self._q}.continuations WHERE effect_key=%s", (effect_key,)
            )
            row = cursor.fetchone()
        return 0 if row is None else int(row[0])

    # --- delegations (v0.3 §5.2) ---------------------------------------------------------

    def put_delegation(self, record: DelegationRecord) -> None:
        """A plain `INSERT` that raises on a duplicate id, and **never an upsert**: an upsert on
        an existing id would clear `revoked_at`, which is `unrevoke` by another door in a release
        that says there is no such thing (`v0.3 §5.7`)."""
        connection = self._connection()
        connection.execute("BEGIN")
        self._use_schema(connection)
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"INSERT INTO {self._q}.delegations("
                    "delegation_id, parent_id, depth, grant_json, "
                    "created_by_agent, created_by_user, created_via, created_at, revoked_at, "
                    "revoked_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        record.delegation_id,
                        record.parent_id,
                        record.depth,
                        record.grant_json,
                        record.created_by_agent,
                        record.created_by_user,
                        record.created_via,
                        _iso(record.created_at),
                        _iso(record.revoked_at) if record.revoked_at else None,
                        record.revoked_by,
                    ),
                )
        except Exception as duplicate:
            self._rollback(connection)
            raise InvalidArgument(
                f"delegation {record.delegation_id!r} already exists"
            ) from duplicate
        self._commit(connection)

    def _read_delegations(self, where: str, args: tuple[Any, ...]) -> tuple[DelegationRecord, ...]:
        with self._connection().cursor() as cursor:
            cursor.execute(
                "SELECT delegation_id, parent_id, depth, grant_json, created_by_agent, "
                "created_by_user, created_via, created_at, revoked_at, revoked_by "
                f"FROM {self._q}.delegations {where}",
                args,
            )
            rows = cursor.fetchall()
        return tuple(
            DelegationRecord(
                delegation_id=str(row[0]),
                parent_id=str(row[1]),
                depth=int(row[2]),
                grant_json=str(row[3]),
                created_by_agent=str(row[4]),
                created_by_user=row[5],
                created_via=str(row[6]),
                created_at=datetime.fromisoformat(str(row[7])),
                revoked_at=_at(row[8]),
                revoked_by=row[9],
            )
            for row in rows
        )

    def get_delegation(self, delegation_id: str) -> DelegationRecord | None:
        found = self._read_delegations("WHERE delegation_id = %s", (delegation_id,))
        return found[0] if found else None

    def delegations_for(self, parent_id: str) -> tuple[DelegationRecord, ...]:
        return self._read_delegations(
            "WHERE parent_id = %s ORDER BY created_at, delegation_id", (parent_id,)
        )

    def delegations(self, *, include_revoked: bool = False) -> tuple[DelegationRecord, ...]:
        where = "ORDER BY created_at, delegation_id"
        if not include_revoked:
            where = "WHERE revoked_at IS NULL " + where
        return self._read_delegations(where, ())

    def revoke_delegation(self, delegation_id: str, *, by: str | None, at: datetime) -> bool:
        """Revoke one delegation, atomically. `False` if it was already revoked (`v0.3 §5.7`).

        The read-then-write happens in one transaction, so two concurrent revokes append one
        `DELEGATION_REVOKED` between them rather than one each. An unknown id is an
        `InvalidArgument`: there is nothing to revoke, and returning `False` would make that
        indistinguishable from the idempotent case.
        """
        connection = self._connection()
        connection.execute("BEGIN")
        self._use_schema(connection)
        try:
            existing = self.get_delegation(delegation_id)
            if existing is None:
                raise InvalidArgument(f"no delegation {delegation_id}")
            if existing.revoked_at is not None:
                self._commit(connection)
                return False
            with connection.cursor() as cursor:
                cursor.execute(
                    f"UPDATE {self._q}.delegations SET revoked_at=%s, revoked_by=%s "
                    "WHERE delegation_id=%s AND revoked_at IS NULL",
                    (_iso(at), by, delegation_id),
                )
                updated = cursor.rowcount
        except BaseException:
            self._rollback(connection)
            raise
        self._commit(connection)
        return bool(updated == 1)

    # --- evidence (v0.1 §6, v0.2 §4.1) ---------------------------------------------------

    def append_event(self, event: Event) -> Event:
        connection = self._connection()
        connection.execute("BEGIN")
        self._use_schema(connection)
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"INSERT INTO {self._q}.events("
                    "ts, type, action_id, effect_key, approval_id, data_json) "
                    "VALUES(%s,%s,%s,%s,%s,%s) RETURNING event_id",
                    (
                        _iso(event.ts),
                        str(event.type),
                        event.action_id,
                        event.effect_key,
                        event.approval_id,
                        json.dumps(dict(event.data), sort_keys=True),
                    ),
                )
                row = cursor.fetchone()
            stored = replace(event, event_id=int(_only(row, "event id")[0]))
        except BaseException:
            self._rollback(connection)
            raise
        self._commit(connection)
        return stored

    def events(self) -> tuple[Event, ...]:
        with self._connection().cursor() as cursor:
            cursor.execute(
                "SELECT event_id, ts, type, action_id, effect_key, approval_id, data_json "
                f"FROM {self._q}.events ORDER BY event_id"
            )
            rows = cursor.fetchall()
        return tuple(
            Event(
                event_id=int(row[0]),
                ts=datetime.fromisoformat(str(row[1])),
                type=EventType(row[2]),
                action_id=str(row[3]),
                effect_key=row[4],
                approval_id=row[5],
                data=json.loads(str(row[6])),
            )
            for row in rows
        )

    def put_receipt(self, receipt: Receipt) -> None:
        connection = self._connection()
        connection.execute("BEGIN")
        self._use_schema(connection)
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"INSERT INTO {self._q}.receipts("
                    "receipt_id, action_id, effect_key, result, json, ts) "
                    "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT (receipt_id) DO NOTHING",
                    (
                        receipt.receipt_id,
                        receipt.action_id,
                        receipt.effect_key,
                        str(receipt.result),
                        json.dumps(receipt.to_dict(), sort_keys=True),
                        _iso(receipt.finished_at),
                    ),
                )
        except BaseException:
            self._rollback(connection)
            raise
        self._commit(connection)

    def receipts(self) -> tuple[Receipt, ...]:
        with self._connection().cursor() as cursor:
            cursor.execute(f"SELECT json FROM {self._q}.receipts ORDER BY ts, receipt_id")
            rows = cursor.fetchall()
        return tuple(Receipt.from_dict(json.loads(str(row[0]))) for row in rows)
