"""The Postgres backend. Build-list item 3; SPEC-v0.6 §4, §8 T153-T154f.

**It passes item 1's suite. Not a suite written for it -- that one.** A backend graded against a
suite written for that backend has marked its own homework, which is why item 1 comes before item
3 and why the only interesting assertion in this file is a single `run()`.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import uuid
from pathlib import Path

import pytest

from ctrlrun.conformance.report import SuiteStatus
from ctrlrun.conformance.store import run
from ctrlrun.conformance.store.backends import PostgresBackend
from ctrlrun.errors import InvalidArgument, MissingDependency

#: Where the tests find a server. An operator's CI sets it; there is no default that could
#: accidentally point at something real.
URL = os.environ.get("CTRLRUN_TEST_POSTGRES")

postgres = pytest.mark.skipif(
    not URL, reason="CTRLRUN_TEST_POSTGRES is not set; no server to run against"
)


@pytest.fixture
def backend():
    """A backend, and its schemas dropped when the test is done.

    Without the teardown every test that called `open()` left its `_serial = 0` schema behind:
    measured at **+8 per run**, monotonic, in a database CI shares with every other Postgres test
    here. `reset()` drops the schema it is *finished* with and advances the serial, so the last
    one a test used is always still there when the test ends -- which is the half of item 5's
    leak fix that nothing asserted.
    """
    assert URL
    made = PostgresBackend(URL)
    try:
        yield made
    finally:
        made.reset()


# --- T153: the extra says so when it is missing, and core does not import it -----------------


def test_T153_import_ctrlrun_pulls_in_no_psycopg():
    """Beside T30, T92, T125b, T134 and T140f. `import ctrlrun` must not reach an extra."""
    probe = textwrap.dedent("""
        import sys
        import ctrlrun
        leaked = sorted(n for n in sys.modules if n.split(".")[0] in {"psycopg", "psycopg2"})
        print(",".join(leaked))
    """)
    with tempfile.TemporaryDirectory() as work:
        script = Path(work) / "probe.py"
        script.write_text(probe, encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(script)], capture_output=True, text=True, check=True
        )
    assert done.stdout.strip() == "", f"import ctrlrun pulled in {done.stdout.strip()}"


def test_T153_ctrlrun_postgres_is_not_reexported():
    """`ctrlrun.postgres` is `import`ed explicitly by a caller who wants it (§9.1)."""
    import ctrlrun

    assert not hasattr(ctrlrun, "PostgresStateStore")


def test_T153_a_missing_driver_says_how_to_install_it(monkeypatch):
    """`MissingDependency` carrying the install line, not an `ImportError` from the depths.

    The driver is hidden by putting `None` in `sys.modules`, which makes `import psycopg` raise
    `ImportError` without touching `builtins.__import__` -- monkeypatching that breaks every
    other import the call makes on the way, and the test then passes for the wrong reason.
    """
    import ctrlrun.postgres as postgres_module

    monkeypatch.setitem(sys.modules, "psycopg", None)
    with pytest.raises(MissingDependency) as raised:
        postgres_module._psycopg()
    assert "pip install" in str(raised.value)
    assert "ctrlrun[postgres]" in str(raised.value)


# --- T154: Postgres passes item 1's suite ----------------------------------------------------


@postgres
def test_T154_postgres_passes_the_store_conformance_suite(backend):
    """SPEC-v0.6 §4. Every case in §2.3, against a real server.

    Not a suite written for it -- **that** one, which predates this backend and which both
    shipped stores also pass. No N/A is accepted: Postgres has durable, shareable storage.
    """
    report = run(backend)
    assert report.ok, report.to_text()
    assert report.not_applicable_cases == (), report.to_text()


@postgres
def test_T154_every_suite_is_reported(backend):
    """A report with a missing suite would pass `ok` by having nothing to fail."""
    from ctrlrun.conformance.store import SUITES

    report = run(backend)
    assert {suite.name for suite in report.suites} == set(SUITES)
    for suite in report.suites:
        assert suite.status is SuiteStatus.PASS, report.to_text()


# --- T154b: --store-url accepts its second value ---------------------------------------------


def test_T154b_store_url_accepts_two_values_and_refuses_a_third(tmp_path):
    """`v0.4 §3.1` reserved the flag and exited 2 naming v0.6 for anything but SQLite. v0.6 is
    now: the message and its test change here (SPEC-v0.6 §9.6 amendment 2)."""
    from ctrlrun import verify

    policy = tmp_path / "ctrlrun.yaml"
    policy.write_text(
        "schema: ctrlrun.policy/v1\nactions:\n  stripe.refund:\n    decision: allow\n",
        encoding="utf-8",
    )
    with pytest.raises(verify.VerifyRefused) as raised:
        verify.run(policy, store_url="mysql://nope")
    message = str(raised.value)
    assert "postgresql://" in message, "the refusal does not name the second accepted value"
    assert "v0.4" not in message, "the refusal still describes itself as a v0.4 limitation"


@postgres
def test_T154e_verify_touches_no_schema_it_did_not_create(tmp_path):
    """SPEC-v0.6 §4.1, and `v0.4`'s T103 carried to the backend that made it hard.

    Verify creates a `ctrlrun_verify_<hex>` schema per guarantee, migrates only that, and drops
    every one when the run ends. It never migrates or writes `public`.
    """
    import psycopg

    from ctrlrun import verify

    def state_of_public() -> list[tuple[str, ...]]:
        connection = psycopg.connect(URL, autocommit=True)
        try:
            return sorted(
                tuple(str(cell) for cell in row)
                for row in connection.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                ).fetchall()
            )
        finally:
            connection.close()

    def verify_schemas() -> set[str]:
        connection = psycopg.connect(URL, autocommit=True)
        try:
            return {
                str(row[0])
                for row in connection.execute(
                    "SELECT nspname FROM pg_namespace WHERE nspname LIKE 'ctrlrun_verify%'"
                ).fetchall()
            }
        finally:
            connection.close()

    before, schemas_before = state_of_public(), verify_schemas()
    report = verify.run("examples/authority/payments.yaml", store_url=URL)
    assert report.exit_code == 0, report.to_text()

    assert state_of_public() == before, (
        "verify changed the operator's public schema; it must create, migrate and drop only its "
        "own (SPEC-v0.6 §4.1)"
    )
    # Before/after rather than an absolute: a leftover from some other run is not this run's
    # finding, and an absolute would make this test report somebody else's mess as a defect.
    left = verify_schemas() - schemas_before
    assert left == set(), f"verify left scratch schemas behind: {sorted(left)}"


@postgres
def test_T154e_a_url_verify_cannot_make_a_schema_in_is_refused():
    """§4.1: exit 2 naming the reason, and **never** a silent fallback to `public`."""
    from ctrlrun import verify

    broken = URL.rsplit("/", 1)[0] + "/ctrlrun_does_not_exist_" + uuid.uuid4().hex[:8]
    with pytest.raises(verify.VerifyRefused) as raised:
        verify.run("examples/authority/payments.yaml", store_url=broken)
    message = str(raised.value)
    assert "scratch schema" in message, message
    assert "never touches the operator" in message or "operator" in message, message


# --- T154c: the store refuses a non-UTF8 database --------------------------------------------


@postgres
def test_T154c_a_non_utf8_database_is_refused_at_open():
    """SPEC-v0.6 §4.4.

    `v0.1 §2.3` hashes the exact code points it is given and applies no normalization, so an
    effect key that survives a round trip as different bytes is a **different identity** -- two
    attempts at one logical effect would reserve two keys and both execute. That is a double
    execution reached through the storage layer's character set, refused at open rather than
    discovered.
    """
    import psycopg

    from ctrlrun.postgres import PostgresStateStore

    name = f"ctrlrun_ascii_{uuid.uuid4().hex[:10]}"
    admin = psycopg.connect(URL, autocommit=True)
    try:
        try:
            admin.execute(
                f"CREATE DATABASE \"{name}\" ENCODING 'SQL_ASCII' "
                "TEMPLATE template0 LC_COLLATE 'C' LC_CTYPE 'C'"
            )
        except psycopg.Error as refused:
            pytest.skip(f"cannot create a SQL_ASCII database here: {refused}")
        ascii_url = URL.rsplit("/", 1)[0] + "/" + name
        with pytest.raises(InvalidArgument) as raised:
            PostgresStateStore(ascii_url)
        message = str(raised.value)
        assert "SQL_ASCII" in message, "the refusal does not name the encoding it found"
        assert "UTF8" in message, "the refusal does not say what it wanted"
    finally:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


@postgres
def test_T154c_identity_columns_are_byte_compared(backend):
    """§4.4's `COLLATE "C"`, read from the catalogue rather than from the DDL string.

    A non-deterministic collation would merge two distinct effect keys into one -- a refusal, and
    therefore the safe direction -- but the store should not depend on which way a deployment's
    `lc_collate` happens to fail.
    """
    import psycopg

    store = backend.open()
    bare, schema = __import__(
        "ctrlrun.conformance.store.backends", fromlist=["_split_schema"]
    )._split_schema(backend.url())
    connection = psycopg.connect(bare, autocommit=True)
    try:
        rows = connection.execute(
            "SELECT table_name, column_name, collation_name FROM information_schema.columns "
            "WHERE table_schema = %s AND column_name IN "
            "('effect_key','action_id','approval_id','delegation_id','continuation')",
            (schema,),
        ).fetchall()
    finally:
        connection.close()
        store.close()
    assert rows, "no identity columns found; the query is checking nothing"
    wrong = [(t, c, n) for t, c, n in rows if n != "C"]
    assert not wrong, f'identity columns not COLLATE "C": {wrong}'


# --- T154d: Control never maps a store exception through v0.1 §5.5 ---------------------------


def test_T154d_control_never_maps_a_store_exception_to_failed(tmp_path):
    """SPEC-v0.6 §2.5's third bullet, asserted at the `Control` layer.

    `v0.1 §5.5`'s table is about the **executor**. Applying it to the store would let a storage
    failure look like a proven non-execution, which is the shape of a double refund.
    """
    from datetime import UTC, datetime, timedelta

    import ctrlrun
    from ctrlrun import Control, Policy, SQLiteStateStore
    from ctrlrun.action import Action, Principal

    moment = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    store = SQLiteStateStore(tmp_path / "s.db", clock=lambda: moment)

    class Breaks:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def commit_effect(self, *args, **kwargs):
            raise RuntimeError("the store could not confirm that write")

    policy = Policy.from_yaml(
        "schema: ctrlrun.policy/v1\nactions:\n  stripe.refund:\n    decision: allow\n"
    )
    control = Control(policy, Breaks(store), clock=lambda: moment)
    action = Action(
        name="stripe.refund",
        arguments={"payment_id": "p1", "amount": 2000},
        principal=Principal(agent="a"),
    )
    try:
        control.execute(action, lambda: "ok", "refund:p1", lease=timedelta(minutes=5))
    except ctrlrun.NotExecuted:  # pragma: no cover - the defect this forbids
        raise AssertionError("a store failure reached the caller as NotExecuted") from None
    except RuntimeError:
        pass
    else:
        raise AssertionError("a failing commit_effect did not reach the caller")

    record = store.get_effect("refund:p1")
    assert record is not None
    assert str(record.state) != "failed", (
        "a store exception was written as FAILED; only the executor opts into that (v0.1 §5.5)"
    )


# --- T154f: connection discipline -------------------------------------------------------------


@postgres
def test_T154f_one_connection_per_thread(backend):
    """§4.4. `psycopg` connections are not thread-safe, so each thread gets its own -- and one
    thread gets the same one twice."""
    import threading

    store = backend.open()
    seen: dict[int, list[int]] = {}
    lock = threading.Lock()

    def touch() -> None:
        first = id(store._connection())
        second = id(store._connection())
        with lock:
            seen[threading.get_ident()] = [first, second]

    threads = [threading.Thread(target=touch) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()

    for pair in seen.values():
        assert pair[0] == pair[1], "one thread got two connections"
    handles = [pair[0] for pair in seen.values()]
    assert len(set(handles)) == len(handles), "two threads shared one connection"
    store.close()


@postgres
def test_T154f_close_is_not_a_fence(backend):
    """SPEC-v0.6 §2.7, for this backend too: `close()` releases what the store holds open and
    does not make it refuse later calls."""
    from datetime import timedelta

    store = backend.open()
    store.reserve_effect("refund:closed", "act_c", timedelta(minutes=5))
    store.close()
    assert store.get_effect("refund:closed") is not None
    store.begin_execution("refund:closed", "act_c")
    store.commit_effect("refund:closed", "act_c", {"ok": True})
    assert str(store.get_effect("refund:closed").state) == "committed"
    store.close()


@postgres
def test_T154f_the_store_holds_nothing_session_scoped(backend):
    """§4.4's pgbouncer claim, asserted rather than asserted-about.

    The store works behind transaction-mode pooling **because** it holds no advisory lock, no
    temp table, no prepared statement it depends on surviving, and no `SET` other than the
    `search_path` it re-issues on every connection. That is the second reason §4.2.1 rejected
    advisory locks, and a property worth keeping deliberately rather than by luck.
    """
    source = Path(__import__("ctrlrun.postgres", fromlist=["x"]).__file__).read_text()
    for smell in ("pg_advisory_lock(", "CREATE TEMP", "PREPARE "):
        assert smell not in source, f"postgres.py uses {smell!r}, which pins it to a session"
    # A grep cannot see a `SET`, and a review found one contradicting this test's own claim.
    # `SET LOCAL` reverts at the end of its transaction, so it is not session state; a bare `SET`
    # outside `_connect` would be.
    assert "SET LOCAL search_path" in source, "the store no longer scopes search_path per txn"
    assert source.count("SET search_path") == 1, (
        "a second bare `SET search_path` appeared; only the connect-time one is allowed, and "
        "every transaction re-asserts it with SET LOCAL (§4.4)"
    )


# --- §4.3: the two ambiguities, and the checks that make them different ----------------------


@postgres
def test_T155c_the_re_read_identity_check_is_not_an_action_id_match(backend):
    """SPEC-v0.6 §4.3.2 Table A1 row 1, and §4.3.3 -- the double execution a review found.

    `Action.action_id` is caller-supplyable and §5.1 contemplates two attempts sharing one, so
    *"a record carrying our `action_id`"* is satisfied by **another process's live reservation**.
    This drives exactly that: a live reservation under `act_shared`, then a second attempt with
    the same id whose `COMMIT` was lost. It MUST refuse.

    A mutation reducing §4.3.3's comparison to `action_id` alone makes this test report that the
    second attempt holds the key -- which is one logical effect executed twice, through the
    storage layer.
    """
    from datetime import UTC, datetime, timedelta

    from ctrlrun.effect import Reservation
    from ctrlrun.errors import DuplicateEffect

    # A **frozen** clock, which is the whole point. With the wall clock the two attempts'
    # `lease_expires_at` differ by microseconds and the refusal is an accident of timing rather
    # than of the check -- a review found this test passing for exactly that reason, while the
    # store concluded it held a key another attempt held. `verify` and the conformance backend
    # both inject clocks, so the frozen case is the deployed one.
    frozen = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    store = backend.open_with_clock(lambda: frozen)
    shared = "act_shared"
    store.reserve_effect("refund:shared", shared, timedelta(minutes=5))

    # A second attempt under the SAME action_id but a different lease -- the realistic collision,
    # and the one an `action_id` match alone cannot see. §4.3.3 records why the fully-identical
    # case is a caller-side violation rather than something a store can separate.
    attempted = Reservation(
        effect_key="refund:shared",
        action_id=shared,
        attempt=1,
        lease_expires_at=frozen + timedelta(minutes=9),
    )
    try:
        store._resolve_lost_insert(
            "refund:shared",
            attempted,
            frozen,
            approval_id=None,
            action_hash=None,
            lease=timedelta(minutes=5),
        )
    except DuplicateEffect:
        pass
    else:
        raise AssertionError(
            "the re-read concluded we hold a key another attempt holds; §4.3.3 compares every "
            "column written, and fails closed where the rows are indistinguishable"
        )
    store.close()


@postgres
def test_T155c_the_re_read_accepts_only_our_own_row(backend):
    """The other half: a lost `COMMIT` that DID land must be recognised, or every ambiguous
    write would refuse and the re-read would buy nothing."""
    from datetime import UTC, datetime, timedelta

    from ctrlrun.effect import Reservation

    frozen = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    store = backend.open_with_clock(lambda: frozen)
    reservation = store.reserve_effect("refund:mine", "act_mine", timedelta(minutes=5))
    record = store.get_effect("refund:mine")
    assert record is not None
    landed = Reservation(
        effect_key="refund:mine",
        action_id="act_mine",
        attempt=record.attempt,
        lease_expires_at=record.lease_expires_at,
    )
    # No exception: this is our own write, so we hold the key and execution proceeds.
    store._resolve_lost_insert(
        "refund:mine",
        landed,
        frozen,
        approval_id=None,
        action_hash=None,
        lease=timedelta(minutes=5),
    )
    assert reservation.action_id == "act_mine"
    store.close()


@postgres
def test_T155_a_stated_abort_is_failed_and_not_ambiguous(backend):
    """SPEC-v0.6 §4.3.1.

    `40001` and `40P01` are the server stating **in band** that it rolled the transaction back --
    `v0.2 §6.8`'s rule one layer down. They must propagate as themselves so the store write is
    `FAILED` and retryable, and must NOT be turned into an ambiguous write that triggers a
    re-read. `57014` is deliberately not in the set, and is asserted to be ambiguous.
    """
    import psycopg

    from ctrlrun.postgres import AmbiguousWrite

    store = backend.open()

    class Aborted(psycopg.errors.SerializationFailure):
        sqlstate = "40001"

    class Cancelled(psycopg.Error):
        sqlstate = "57014"

    class Commits:
        def __init__(self, error):
            self._error = error

        def commit(self):
            raise self._error

    for error, expected in (
        (Aborted("aborted"), Aborted),
        (Cancelled("cancelled"), AmbiguousWrite),
    ):
        try:
            store._commit(Commits(error))
        except expected:
            pass
        except BaseException as other:
            raise AssertionError(
                f"{error.sqlstate} raised {type(other).__name__}, expected {expected.__name__}"
            ) from None
        else:
            raise AssertionError(f"{error.sqlstate} did not raise")
    store.close()


@postgres
def test_T155_the_transition_row_count_is_checked(backend, monkeypatch):
    """§4.2: every transition is a compare-and-set with **the row count checked**.

    The window has to be opened on purpose. Moving the record out from under the transition the
    obvious way does not reach this check at all: `_checked` reads first and raises on the moved
    record, so the `UPDATE` never runs and the row count is a **subsumed guard** -- a mutation
    deleting it stays green. Found by mutation.

    So the record is changed **between** the read and the write, which is the race the check
    exists for and the only way to reach it: `_checked` sees a healthy record, the `UPDATE`'s
    `WHERE` then matches nothing, and the store must refuse rather than report success.
    """
    from datetime import timedelta

    from ctrlrun.errors import AmbiguousEffect, DuplicateEffect

    store = backend.open()
    other = backend.reopen()
    assert other is not None
    store.reserve_effect("refund:cas", "act_cas", timedelta(minutes=5))
    store.begin_execution("refund:cas", "act_cas")

    real_read = store._read_effect
    moved = False

    def read_then_move(connection, effect_key):
        nonlocal moved
        record = real_read(connection, effect_key)
        if effect_key == "refund:cas" and not moved:
            moved = True
            # Another attempt declares it ambiguous in the instant between our read and our
            # write. This is `v0.1 §5.3 E3`'s lapsed-lease contender, at the exact moment that
            # makes the row count the only thing standing in the way.
            other.mark_ambiguous("refund:cas", "act_cas", "a contender got there first")
        return record

    monkeypatch.setattr(store, "_read_effect", read_then_move)

    try:
        store.commit_effect("refund:cas", "act_cas", {"ok": True})
    except (AmbiguousEffect, DuplicateEffect):
        pass
    else:
        raise AssertionError(
            "a transition whose WHERE clause matched nothing reported success; §4.2 requires "
            "the row count to be checked"
        )
    assert moved, "the window never opened; this test would prove nothing"
    record = other.get_effect("refund:cas")
    assert record is not None and str(record.state) == "ambiguous"
    store.close()
    other.close()


def test_T153_the_module_imports_without_the_driver(monkeypatch):
    """§9.1: `ctrlrun.postgres` is importable without psycopg; only *using* it raises.

    The property `test_T153_import_ctrlrun_pulls_in_no_psycopg` checks is about `import
    ctrlrun`, which never reaches this module -- so an eager `import psycopg` at module scope
    would leave that test green while turning `MissingDependency` into an `ImportError` from
    the depths, which is exactly what `errors.py` says must not happen. Found by mutation:
    the earlier row pointed at a test the change could not affect.
    """
    import subprocess

    probe = textwrap.dedent("""
        import sys
        sys.modules["psycopg"] = None
        import ctrlrun.postgres as m
        from ctrlrun.errors import MissingDependency
        try:
            m.PostgresStateStore("postgresql://nobody@127.0.0.1/nothing")
        except MissingDependency as missing:
            print("MissingDependency:", missing)
        except BaseException as other:
            print("WRONG:", type(other).__name__, other)
    """)
    with tempfile.TemporaryDirectory() as work:
        script = Path(work) / "probe.py"
        script.write_text(probe, encoding="utf-8")
        done = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith("MissingDependency:"), (
        f"importing ctrlrun.postgres without the driver did not work: {done.stdout}{done.stderr}"
    )
    assert "ctrlrun[postgres]" in done.stdout


@postgres
def test_T154f_a_user_without_ddl_rights_is_refused_naming_the_privilege():
    """SPEC-v0.6 §3.6 and T154f's first assertion, which had neither test nor code.

    A role without `CREATE` got a raw `psycopg.errors.InsufficientPrivilege` out of the
    constructor, quoting the failing `CREATE TABLE` verbatim -- uncatchable by any
    `except CTRLRunError` in this codebase. A migration needs DDL rights at least on the first
    start after an upgrade, and an operator who has not granted them deserves to be told which
    grant is missing rather than which statement failed.
    """
    import psycopg

    from ctrlrun.postgres import PostgresStateStore

    role = f"ctrlrun_nodll_{uuid.uuid4().hex[:8]}"
    schema = f"locked_{uuid.uuid4().hex[:8]}"
    admin = psycopg.connect(URL, autocommit=True)
    try:
        try:
            admin.execute(f"CREATE ROLE \"{role}\" LOGIN PASSWORD 'probe'")
            admin.execute(f'CREATE SCHEMA "{schema}"')
            admin.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"')
            admin.execute(f'REVOKE CREATE ON SCHEMA "{schema}" FROM "{role}", PUBLIC')
        except psycopg.Error as refused:
            pytest.skip(f"cannot create a locked-down role here: {refused}")

        host = URL.split("@", 1)[1]
        locked = f"postgresql://{role}:probe@{host}"
        with pytest.raises(InvalidArgument) as raised:
            PostgresStateStore(locked, schema=schema)
        message = str(raised.value)
        assert "CREATE" in message, "the refusal does not name the missing privilege"
        assert schema in message, "the refusal does not name the schema"
        assert "GRANT CREATE ON SCHEMA" in message, "the refusal does not name the grant"
    finally:
        admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        admin.close()


@postgres
def test_T154f_search_path_is_set_per_transaction(backend):
    """§4.4's pgbouncer transaction-mode claim, made true.

    A connect-time `SET search_path` is **session** state, and §4.4 claimed the store holds none.
    Under transaction pooling a later transaction may land on a server connection that never
    received it, resolving to `"$user", public` -- which for a scratch-schema store means reading
    and writing the *operator's* `public` tables, the hazard §4.1 exists to close.

    Modelled by resetting the session's `search_path` behind the store's back and then writing:
    a store that depended on the session would land in `public`.
    """
    from datetime import timedelta

    store = backend.open()
    connection = store._connection()
    connection.execute("SET search_path TO public")

    store.reserve_effect("refund:pooled", "act_pooled", timedelta(minutes=5))

    # The record must be in the store's own schema, not in `public`.
    assert store.get_effect("refund:pooled") is not None
    bare, schema = __import__(
        "ctrlrun.conformance.store.backends", fromlist=["_split_schema"]
    )._split_schema(backend.url())
    import psycopg

    checker = psycopg.connect(bare, autocommit=True)
    try:
        rows = checker.execute(
            f'SELECT effect_key FROM "{schema}".effects WHERE effect_key = %s', ("refund:pooled",)
        ).fetchall()
        assert rows, "the write did not land in the store's own schema"
        stray = checker.execute("SELECT to_regclass('public.effects')").fetchone()
        assert stray is None or stray[0] is None, (
            "the store created tables in `public`; §4.1's whole point is that it never touches "
            "a schema it was not pointed at"
        )
    finally:
        checker.close()
        store.close()
