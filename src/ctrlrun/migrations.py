"""Schema version and the forward-only migration runner. Build-list item 2; SPEC-v0.6 §3.

Six places across v0.2, v0.3, v0.4 and v0.5 say *"there is still no migration story -- that is
v0.6."* This is it.

**A schema version nobody checks is a schema version that lies.** From v0.6 a store refuses a
database it does not recognise, in **both** directions: a newer binary migrates or refuses, and an
**older** binary against a newer schema refuses immediately rather than reading columns it does
not understand. The second direction is the one that gets forgotten and the one that corrupts --
a binary that reads a table it half understands does not fail, it succeeds, silently dropping the
columns it does not know.

**The backward guard begins with v0.6, and that qualifier is not a detail.** A released v0.5
binary has no version check at all: it will open a v0.6 database and `SELECT json FROM receipts`
straight past `seq`, `prev_hash` and `hash`. So the rule protects a v0.6-or-later reader from a
future schema; it cannot protect a reader that shipped before it existed. `SPEC-v0.6 §9.5` states
the operational consequence -- **upgrade every reader before upgrading any writer** -- and this
paragraph exists so the module does not imply a protection it cannot give retroactively.

**The version is recorded, never inferred.** A store MUST NOT decide what version a database is by
looking for a column: `PRAGMA table_info` answers *what is there*, which is not the same question
as *what has been applied*, and the two diverge the moment a migration does anything a column list
cannot show -- a backfill, a constraint, a data repair.

**Forward-only.** A downgrade that has never been run against a real database is a button that
does not work, and it will be found out on the worst day. The supported way back is the backup
taken before the upgrade.

This module lives beside `state.py` rather than above it: it is what decides whether a store may
open, and a store whose admission check lived somewhere else would be a store with two front
doors.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

from .errors import SchemaMismatch


def ctrlrun_version() -> str:
    """The running distribution's version, for `schema_version.ctrlrun_version`.

    Lives here rather than in `verify/` -- which had the only copy -- because a migration stamps
    it into a durable record an *older* binary later reads back and names in its refusal (§3.3).
    Two implementations of "which build is this" would be two answers in the one place the
    question has to be exact.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("ctrlrun")
    except PackageNotFoundError:  # pragma: no cover - a source tree with no install
        return "0.0.0"


#: `v0.1 §2.3`'s hash shape, with no bytes behind it. The chain's `seq = 0` head carries this so
#: the first chained receipt is `seq = 1` with a `prev_hash` nobody can forge a predecessor for
#: (SPEC-v0.6 §3.7, §6.2).
GENESIS_HASH: Final = "sha256:" + "00" * 32


@dataclass(frozen=True)
class Migration:
    """One forward step, applied and recorded in a single transaction (§3.4).

    `statements` are plain SQL. A migration MUST NOT depend on the binary's Python objects -- one
    that called `Receipt.from_dict` would break the moment `Receipt` changed, which is the same
    afternoon -- and MUST NOT do anything outside the transaction: no file writes, no network, no
    `VACUUM`, no `CREATE INDEX CONCURRENTLY`. A step that cannot be rolled back is not a migration
    step; it is a second migration, applied and recorded separately.
    """

    id: str
    statements: tuple[str, ...]


#: SPEC-v0.6 §3.2. Exactly the schema v0.5 shipped, `CREATE TABLE IF NOT EXISTS` throughout.
#:
#: **Its DDL runs on the baseline path, and skipping it would be a defect.** "Baseline" matches a
#: v0.1, v0.2, v0.3, v0.4 *or* v0.5 database -- all five have an `effects` table and no
#: `schema_version` -- and the tables differ: `continuations` and its unique index arrived in
#: v0.2, `delegations` and its index in v0.3. Both reached databases that already existed
#: precisely because the store ran its whole schema script on every open. Idempotence is the
#: reason this is safe to run, not a reason to skip it.
_BASELINE: Final = (
    """CREATE TABLE IF NOT EXISTS effects(
  effect_key TEXT PRIMARY KEY,
  state TEXT NOT NULL,
  action_id TEXT NOT NULL,
  attempt INTEGER NOT NULL DEFAULT 1,
  lease_expires_at TEXT,
  result_json TEXT,
  error TEXT,
  created_at TEXT,
  updated_at TEXT
)""",
    """CREATE TABLE IF NOT EXISTS continuations(
  effect_key TEXT PRIMARY KEY,
  action_id TEXT NOT NULL,
  action_json TEXT NOT NULL,
  continuation TEXT NOT NULL,
  rounds INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT
)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS continuations_by_value
  ON continuations(continuation) WHERE continuation <> ''""",
    """CREATE TABLE IF NOT EXISTS approvals(
  approval_id TEXT PRIMARY KEY,
  action_hash TEXT NOT NULL,
  status TEXT NOT NULL,
  action_json TEXT NOT NULL,
  approver TEXT,
  created_at TEXT,
  granted_at TEXT,
  expires_at TEXT,
  consumed_at TEXT
)""",
    """CREATE TABLE IF NOT EXISTS receipts(
  receipt_id TEXT PRIMARY KEY,
  action_id TEXT,
  effect_key TEXT,
  result TEXT,
  json TEXT,
  ts TEXT
)""",
    """CREATE TABLE IF NOT EXISTS events(
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT,
  type TEXT,
  action_id TEXT,
  effect_key TEXT,
  approval_id TEXT,
  data_json TEXT
)""",
    """CREATE TABLE IF NOT EXISTS delegations(
  delegation_id TEXT PRIMARY KEY,
  parent_id TEXT NOT NULL,
  depth INTEGER NOT NULL,
  grant_json TEXT NOT NULL,
  created_by_agent TEXT NOT NULL,
  created_by_user TEXT,
  created_via TEXT NOT NULL,
  created_at TEXT NOT NULL,
  revoked_at TEXT,
  revoked_by TEXT
)""",
    "CREATE INDEX IF NOT EXISTS delegations_by_parent ON delegations(parent_id)",
)

#: SPEC-v0.6 §3.7, §6.2. The columns the receipt chain needs, and the head row it hangs from.
#:
#: The head starts at `seq = 0` carrying the genesis hash, so the first chained receipt is
#: `seq = 1`, whether the database was empty or already held a thousand unchained receipts. A
#: `head_mismatch` MUST NOT fire on a database whose only receipts are `unchained`: a head at
#: `seq = 0` with no chained receipt is a consistent chain of length zero, not a truncated one.
#:
#: Pre-chain receipts keep `seq`, `prev_hash` and `hash` NULL and are **not backfilled**: a chain
#: computed over rows written before the chain existed would assert an integrity property nobody
#: can have (§3.7). §6.5 reports them as `unchained`, and never as a pass.
_RECEIPT_CHAIN: Final = (
    "ALTER TABLE receipts ADD COLUMN seq INTEGER",
    "ALTER TABLE receipts ADD COLUMN prev_hash TEXT",
    "ALTER TABLE receipts ADD COLUMN hash TEXT",
    "CREATE UNIQUE INDEX IF NOT EXISTS receipts_by_seq ON receipts(seq) WHERE seq IS NOT NULL",
    """CREATE TABLE IF NOT EXISTS receipt_chain(
  id INTEGER PRIMARY KEY CHECK (id = 1),
  seq INTEGER NOT NULL,
  hash TEXT NOT NULL
)""",
    f"INSERT OR IGNORE INTO receipt_chain(id, seq, hash) VALUES(1, 0, '{GENESIS_HASH}')",
)

#: The ordered set this binary knows. `NNNN_snake_name`: four digits, zero-padded, so
#: lexicographic order is application order.
MIGRATIONS: Final[tuple[Migration, ...]] = (
    Migration("0001_baseline", _BASELINE),
    Migration("0002_receipt_chain", _RECEIPT_CHAIN),
)

HEAD: Final = MIGRATIONS[-1].id


def _check_migration_ids() -> None:
    """`MIGRATIONS` must be unique, `NNNN_snake_name`-shaped and in lexicographic order.

    `classify` compares a set read `ORDER BY migration_id` against `known[:n]` in *declaration*
    order, so the two agree only while those hold. An out-of-order id would classify a healthy
    database as `gapped` and refuse it; a duplicate would collide at apply time. §3.1 states the
    rule and, until a review asked, nothing enforced it -- and §3.7 already schedules two more
    migrations for later items.
    """
    ids = [migration.id for migration in MIGRATIONS]
    if len(set(ids)) != len(ids):
        raise AssertionError(f"duplicate migration id in MIGRATIONS: {ids}")
    if ids != sorted(ids):
        raise AssertionError(f"MIGRATIONS is not in lexicographic order: {ids}")
    for item in ids:
        if not re.fullmatch(r"\d{4}_[a-z][a-z0-9_]*", item):
            raise AssertionError(f"migration id {item!r} is not NNNN_snake_name")


_check_migration_ids()

_SCHEMA_VERSION_TABLE: Final = """CREATE TABLE IF NOT EXISTS schema_version(
  migration_id TEXT PRIMARY KEY,
  applied_at TEXT NOT NULL,
  ctrlrun_version TEXT NOT NULL
)"""

#: A table that means "this is a CTRLRun database from before v0.6". `effects` is the one table
#: every release since v0.1 has had.
_MARKER_TABLE: Final = "effects"


class Classification(StrEnum):
    """What shape a database's recorded history is in (SPEC-v0.6 §3.2, §3.3).

    The five *versioned* shapes are exhaustive, and a store MUST classify into exactly one.
    Anything an implementation cannot place is refused rather than opened: a sixth shape nobody
    thought of is a database nobody has reasoned about.
    """

    EMPTY = "empty"
    BASELINE = "baseline"
    FOREIGN = "foreign"
    UP_TO_DATE = "up_to_date"
    FORWARD = "forward"
    GAPPED = "gapped"
    BACKWARD = "backward"
    DIVERGENT = "divergent"


def classify(applied: tuple[str, ...]) -> Classification:
    """Place a versioned database's recorded history in exactly one of §3.3's five shapes."""
    known = tuple(migration.id for migration in MIGRATIONS)
    unknown = tuple(item for item in applied if item not in known)
    missing = tuple(item for item in known if item not in applied)

    if unknown and missing:
        return Classification.DIVERGENT
    if unknown:
        return Classification.BACKWARD
    if not missing:
        return Classification.UP_TO_DATE
    # Every recorded id is known. It is forward only if they are exactly the first n, in order.
    if applied == known[: len(applied)]:
        return Classification.FORWARD
    return Classification.GAPPED


#: What an `effects` table must have for a database to be CTRLRun's. Not the whole schema: a
#: v0.1 database has fewer tables than a v0.5 one, and the point is to tell *ours* from
#: *somebody else's*, not to re-derive the version from the columns (§3.1).
_EFFECTS_COLUMNS: Final = frozenset({"effect_key", "state", "action_id", "attempt"})


def _refuse_unless_ours(connection: sqlite3.Connection, tables: set[str]) -> None:
    """Adopt a pre-v0.6 database only if its `effects` table is actually CTRLRun's (§3.2).

    `effects` is a plausible name in somebody else's schema, and a `$CTRLRUN_STATE` typo is a
    plausible way to arrive at one. Keying adoption on the name alone meant CTRLRun created
    `schema_version`, `approvals`, `receipts`, `events`, `delegations`, `continuations` and
    `receipt_chain` **inside the operator's other database**, recorded both migrations, opened
    cleanly, and failed at first use with `no such column: effect_key` -- which is after
    `Control` was constructed, and §3.3 says every refusal is at open. A review built one.
    """
    columns = {
        str(row[1]) for row in connection.execute(f"PRAGMA table_info({_MARKER_TABLE})").fetchall()
    }
    missing = _EFFECTS_COLUMNS - columns
    if missing:
        raise _refuse(
            f"this database has a table named {_MARKER_TABLE!r} that is not CTRLRun's: it is "
            f"missing {', '.join(sorted(missing))}. It holds {', '.join(sorted(tables))}. "
            "Creating CTRLRun's tables in somebody else's database is not a recovery.",
            (),
        )


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {str(row[0]) for row in rows}


def _read_applied(connection: sqlite3.Connection) -> tuple[str, ...]:
    rows = connection.execute(
        "SELECT migration_id FROM schema_version ORDER BY migration_id"
    ).fetchall()
    return tuple(str(row[0]) for row in rows)


def applied_migrations(path: str) -> tuple[str, ...]:
    """The migration ids recorded in the database at `path`, in order. A read, for tests."""
    connection = sqlite3.connect(path)
    try:
        if "schema_version" not in _table_names(connection):
            return ()
        return _read_applied(connection)
    finally:
        connection.close()


def _recorded_versions(connection: sqlite3.Connection) -> dict[str, str]:
    rows = connection.execute("SELECT migration_id, ctrlrun_version FROM schema_version").fetchall()
    return {str(row[0]): str(row[1]) for row in rows}


def _refuse(
    reason: str, applied: tuple[str, ...], versions: dict[str, str] | None = None
) -> SchemaMismatch:
    known = tuple(migration.id for migration in MIGRATIONS)
    running = ctrlrun_version()
    written_by = ""
    if versions:
        unknown = [item for item in applied if item not in known]
        if unknown:
            stamps = ", ".join(
                f"{item} (written by ctrlrun {versions.get(item, '?')})" for item in unknown
            )
            written_by = f" Unrecognised: {stamps}."
    return SchemaMismatch(
        f"{reason} This build is ctrlrun {running}, which knows {', '.join(known)}. "
        f"The database records {', '.join(applied) or '(nothing)'}.{written_by} "
        "Migrations are forward-only; there is no downgrade. Restore the backup taken before "
        "the upgrade, or run a build that knows this schema.",
        applied=applied,
        known=known,
        running=running,
    )


def _apply(connection: sqlite3.Connection, migration: Migration, now: datetime) -> None:
    """Apply one migration and record it, in one transaction (§3.4).

    SQLite has transactional DDL, so a migration that raises part way **rolls back to the old
    version by construction** -- including the `INSERT` into `schema_version`, which is inside
    the same transaction as the DDL it records. T149 proves the DDL half by execution, and
    T149b proves the recording half by making the recording itself fail.

    **The applied set is re-read after the write lock is taken, and that is the whole of
    §3.6's concurrency claim.** `migrate()` reads it before the lock, so between that read and
    this transaction another process may have applied the very migration this one is about to.
    Without the re-check the second process runs the DDL again -- `ALTER TABLE … ADD COLUMN` is
    not idempotent -- and then collides on `schema_version`'s primary key. An independent review
    measured it: six concurrent opens of an existing v0.5 database, ten trials, **20 of 60
    failed** with `UNIQUE constraint failed` or `duplicate column name`, and five of six failed
    on every trial against a fresh one. It failed closed, and every worker but one refused to
    start, which is not what "a second process either finds the work done or waits for it"
    means.
    """
    connection.execute("BEGIN IMMEDIATE")
    try:
        if migration.id in _read_applied(connection):
            # Applied by somebody else while we waited for the lock. Nothing to do, and
            # nothing to record.
            connection.rollback()
            return
        for statement in migration.statements:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO schema_version(migration_id, applied_at, ctrlrun_version) VALUES(?,?,?)",
            (migration.id, now.astimezone(UTC).isoformat(), ctrlrun_version()),
        )
    except BaseException:
        # Load-bearing and, until a review measured it, exercised by nothing: an abandoned
        # connection holds an *uncommitted* transaction, WAL lets later reads through, and the
        # next writer meets `database is locked`. T149c opens that window on purpose.
        connection.rollback()
        raise
    connection.commit()


def migrate(connection: sqlite3.Connection, now: datetime | None = None) -> Classification:
    """Bring a database to `HEAD`, or refuse. Returns what it found (SPEC-v0.6 §3).

    Every refusal is at **open**. A store that classified a database and then served reads while
    refusing writes would be a store in a state no caller can reason about.

    There is no argument, keyword or environment variable that suppresses this. A store that
    could run un-migrated is a second configuration nobody tested, which is `v0.4 §3.9`'s rule
    with a storage shape (§3.6).
    """
    stamp = now or datetime.now(UTC)
    tables = _table_names(connection)

    if "schema_version" not in tables:
        if _MARKER_TABLE in tables:
            _refuse_unless_ours(connection, tables)
            found = Classification.BASELINE
        elif tables:
            raise _refuse(
                f"This database has no CTRLRun schema and is not empty: it holds "
                f"{', '.join(sorted(tables))}. Creating CTRLRun's tables in somebody else's "
                "database is not a recovery.",
                (),
            )
        else:
            found = Classification.EMPTY
        connection.execute(_SCHEMA_VERSION_TABLE)
        connection.commit()
    else:
        found = classify(_read_applied(connection))

    applied = _read_applied(connection)

    if found in (Classification.BACKWARD, Classification.DIVERGENT, Classification.GAPPED):
        versions = _recorded_versions(connection)
        known = tuple(migration.id for migration in MIGRATIONS)
        missing = tuple(item for item in known if item not in applied)
        reasons = {
            Classification.BACKWARD: (
                "This database was written by a newer build of CTRLRun and records a migration "
                "this one does not know."
            ),
            Classification.DIVERGENT: (
                f"This database's history has diverged: it is missing {', '.join(missing)} and "
                "carries a migration this build does not know. Two build lineages have written "
                "to it and neither is authoritative."
            ),
            Classification.GAPPED: (
                f"This database's history has a gap: {', '.join(missing)} was never applied, but "
                "a later migration was. Applying it now would run a migration out of order, "
                "against a schema it was never written for."
            ),
        }
        raise _refuse(reasons[found], applied, versions)

    for migration in MIGRATIONS:
        if migration.id not in applied:
            try:
                _apply(connection, migration, stamp)
            except sqlite3.Error as broke:
                # §9.3: an operator's process refusing to start needs a distinguishable
                # exception. A raw `sqlite3.OperationalError` out of a constructor is a bare
                # traceback that no `except CTRLRunError` in this codebase catches -- not the
                # CLI's, not the gateway's, not the webhook's.
                raise SchemaMismatch(
                    f"migration {migration.id!r} could not be applied: {broke}. The database is "
                    f"unchanged -- the migration ran in one transaction and rolled back. It is "
                    f"still at {', '.join(applied) or '(nothing)'}.",
                    applied=applied,
                    known=tuple(item.id for item in MIGRATIONS),
                    running=ctrlrun_version(),
                ) from broke
    return found
