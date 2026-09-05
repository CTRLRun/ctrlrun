"""Schema version and forward-only migrations. Build-list item 2; SPEC-v0.6 §3, §8 T147-T152b.

Six places across v0.2, v0.3, v0.4 and v0.5 say *"there is still no migration story -- that is
v0.6."* This is it, and it comes **before** Postgres, because adding a second schema before the
versioning exists means two schemas drifting with no marker to tell you.

The v0.5-database test is the one that matters. A fixture written by hand asserts what its author
believed the old schema was; a database built by v0.5's own code asserts what it actually was.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import textwrap
import venv
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ctrlrun.errors import SchemaMismatch
from ctrlrun.migrations import (
    HEAD,
    MIGRATIONS,
    Classification,
    applied_migrations,
    classify,
)
from ctrlrun.state import SQLiteStateStore

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


# --- building a database with a previous release's own code ---------------------------------


def _release_venv(tmp_path_factory, version: str) -> Path | None:
    """A venv with `ctrlrun==<version>` in it, or `None` if it cannot be installed offline.

    §3.5 item 2 asks a migration to be proved against *the previous release's own code*, not a
    fixture somebody wrote by hand: a hand-written fixture asserts what its author believed the
    old schema was, and a database built by the old code asserts what it actually was.
    """
    root = tmp_path_factory.mktemp(f"rel-{version}")
    env = root / "venv"
    venv.create(env, with_pip=True)
    python = env / "bin" / "python"
    done = subprocess.run(
        [str(python), "-m", "pip", "install", "-q", f"ctrlrun=={version}"],
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        return None
    return python


BUILD_SCRIPT = textwrap.dedent("""
    import json, sys
    from datetime import UTC, datetime, timedelta
    from ctrlrun.state import SQLiteStateStore
    from ctrlrun.action import Action, Principal

    path = sys.argv[1]
    T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    store = SQLiteStateStore(path, clock=lambda: T0)

    action = Action(
        name="stripe.refund",
        arguments={"payment_id": "txn_old", "amount": 2000, "note": "cafe"},
        principal=Principal(agent="legacy-agent", user="ada"),
        resource="payment:txn_old",
    )
    store.reserve_effect("refund:committed", "act_c", timedelta(minutes=5))
    store.begin_execution("refund:committed", "act_c")
    store.commit_effect("refund:committed", "act_c", {"id": "re_1"})

    store.reserve_effect("refund:unknown", "act_u", timedelta(minutes=5))
    store.begin_execution("refund:unknown", "act_u")
    store.mark_ambiguous("refund:unknown", "act_u", "the response was lost")

    store.reserve_effect("refund:failed", "act_f", timedelta(minutes=5))
    store.begin_execution("refund:failed", "act_f")
    store.fail_effect("refund:failed", "act_f", "refused before acting")

    try:
        from ctrlrun.approval import build_request
        request = build_request(action, timedelta(minutes=15), T0)
    except ImportError:
        from ctrlrun.approval import _build_request
        request = _build_request(action, timedelta(minutes=15), T0)
    store.put_approval_request(request)
    store.grant_approval(request.request_id, "cli:legacy")

    out = {
        "action_hash": action.action_hash,
        "approval_id": request.request_id,
        "arguments": dict(action.canonical_arguments),
    }
    store.close()
    print(json.dumps(out))
""")


@pytest.fixture(scope="session")
def v05_database(tmp_path_factory):
    """A real v0.5 database, built by `ctrlrun==0.5.0` in its own interpreter."""
    python = _release_venv(tmp_path_factory, "0.5.0")
    if python is None:
        pytest.skip("ctrlrun==0.5.0 could not be installed; no network")
    work = tmp_path_factory.mktemp("v05db")
    script = work / "build.py"
    script.write_text(BUILD_SCRIPT, encoding="utf-8")
    database = work / "state.db"
    done = subprocess.run([str(python), str(script), str(database)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return database, json.loads(done.stdout)


# --- T147: a v0.5 database migrates, and every row survives ---------------------------------


def _tables(database: Path) -> set[str]:
    connection = sqlite3.connect(database)
    try:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    finally:
        connection.close()
    return {row[0] for row in rows}


def test_T147_a_v05_database_migrates_and_keeps_every_row(v05_database, tmp_path):
    """SPEC-v0.6 §3.5 items 2 and 3. Content, not a count: a migration that dropped and
    recreated a table would pass a count."""
    source, expected = v05_database
    database = tmp_path / "state.db"
    shutil.copy(source, database)

    assert "schema_version" not in _tables(database), "the v0.5 fixture already has the new table"

    store = SQLiteStateStore(database, clock=lambda: T0)

    assert applied_migrations(database) == tuple(m.id for m in MIGRATIONS)

    committed = store.get_effect("refund:committed")
    assert committed is not None
    assert str(committed.state) == "committed"
    assert committed.action_id == "act_c"
    assert committed.result == {"id": "re_1"}

    unknown = store.get_effect("refund:unknown")
    assert unknown is not None and str(unknown.state) == "ambiguous"
    assert unknown.error == "the response was lost"

    failed = store.get_effect("refund:failed")
    assert failed is not None and str(failed.state) == "failed"

    approval = store.get_approval(expected["approval_id"])
    assert approval is not None
    assert str(approval.status) == "granted"
    assert approval.approver == "cli:legacy"
    assert approval.request.action_hash == expected["action_hash"], (
        "the action hash changed across the migration; an approval granted before it would no "
        "longer authorize the action a human approved"
    )
    assert dict(approval.request.action.canonical_arguments) == expected["arguments"]
    store.close()


def test_T147_the_migrated_database_still_works(v05_database, tmp_path):
    """A migration is not done when the rows survive; it is done when the store still works.

    The `else` behind the refusal: an AMBIGUOUS record carried across a migration must still
    refuse a blind retry, or the migration quietly discarded the one guarantee it was carrying.
    """
    source, _ = v05_database
    database = tmp_path / "state.db"
    shutil.copy(source, database)
    store = SQLiteStateStore(database, clock=lambda: T0)

    from ctrlrun.errors import AmbiguousEffect

    try:
        store.reserve_effect("refund:unknown", "act_new", timedelta(minutes=5))
    except AmbiguousEffect:
        pass
    else:
        raise AssertionError("a migrated AMBIGUOUS record let a blind retry through")

    store.reserve_effect("refund:fresh", "act_new", timedelta(minutes=5))
    store.close()


# --- T148: an older binary refuses a newer database ------------------------------------------


def test_T148_an_older_binary_refuses_a_newer_database(tmp_path, monkeypatch):
    """SPEC-v0.6 §3.3, the backward direction -- the one that gets forgotten and the one that
    corrupts."""
    database = tmp_path / "state.db"
    SQLiteStateStore(database, clock=lambda: T0).close()

    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO schema_version(migration_id, applied_at, ctrlrun_version) VALUES(?,?,?)",
        ("9999_from_the_future", "2027-01-01T00:00:00+00:00", "0.9.0"),
    )
    connection.commit()
    connection.close()

    with pytest.raises(SchemaMismatch) as raised:
        SQLiteStateStore(database, clock=lambda: T0)

    message = str(raised.value)
    assert "9999_from_the_future" in message, "the refusal does not name the unknown migration"
    assert "0.9.0" in message, "the refusal does not name the build that wrote it"
    from ctrlrun.migrations import ctrlrun_version

    assert ctrlrun_version() in message, "the refusal does not name the binary that is refusing"


def test_T148_no_other_table_is_read_before_the_refusal(tmp_path):
    """§3.3: refused **before reading any row from any other table**.

    Asserted by taking the other tables away. A store that read `effects` on the way to the
    version check would raise `sqlite3.OperationalError` here instead of `SchemaMismatch`, and
    the refusal-before-reading claim would be prose.
    """
    database = tmp_path / "state.db"
    SQLiteStateStore(database, clock=lambda: T0).close()
    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO schema_version(migration_id, applied_at, ctrlrun_version) VALUES(?,?,?)",
        ("9999_from_the_future", "2027-01-01T00:00:00+00:00", "0.9.0"),
    )
    for table in ("effects", "approvals", "receipts", "events", "delegations", "continuations"):
        connection.execute(f"DROP TABLE IF EXISTS {table}")
    connection.commit()
    connection.close()

    with pytest.raises(SchemaMismatch):
        SQLiteStateStore(database, clock=lambda: T0)


# --- T149: a migration that fails half way leaves the old version ---------------------------


def test_T149_a_half_applied_migration_rolls_back(tmp_path, monkeypatch):
    """SPEC-v0.6 §3.4, proved by execution rather than asserted by argument.

    A deliberately broken migration ships in the test suite: its second statement raises. The
    store refuses to open, the database is still at its previous version, and every row is
    intact.
    """
    import ctrlrun.migrations as migrations

    database = tmp_path / "state.db"
    store = SQLiteStateStore(database, clock=lambda: T0)
    store.reserve_effect("refund:before", "act_b", timedelta(minutes=5))
    store.close()

    before = applied_migrations(database)

    broken = migrations.Migration(
        id="9998_deliberately_broken",
        statements=(
            "CREATE TABLE half_applied(x TEXT)",
            "THIS IS NOT SQL",
        ),
    )
    monkeypatch.setattr(migrations, "MIGRATIONS", (*migrations.MIGRATIONS, broken))

    with pytest.raises(Exception) as raised:
        SQLiteStateStore(database, clock=lambda: T0)
    assert not isinstance(raised.value, SchemaMismatch)

    assert applied_migrations(database) == before, "the broken migration was recorded"
    assert "half_applied" not in _tables(database), (
        "the first statement of a failed migration survived; DDL must be transactional"
    )

    monkeypatch.undo()
    store = SQLiteStateStore(database, clock=lambda: T0)
    assert store.get_effect("refund:before") is not None
    store.close()


# --- T150: re-opening does not re-run a migration --------------------------------------------


def test_T150_reopening_does_not_rerun(tmp_path):
    database = tmp_path / "state.db"
    SQLiteStateStore(database, clock=lambda: T0).close()
    connection = sqlite3.connect(database)
    first = connection.execute(
        "SELECT migration_id, applied_at FROM schema_version ORDER BY migration_id"
    ).fetchall()
    connection.close()

    SQLiteStateStore(database, clock=lambda: T0 + timedelta(days=1)).close()
    connection = sqlite3.connect(database)
    second = connection.execute(
        "SELECT migration_id, applied_at FROM schema_version ORDER BY migration_id"
    ).fetchall()
    connection.close()

    assert first == second, "re-opening re-applied or re-stamped a migration"


# --- T151: an unknown version is refused, not guessed at -------------------------------------


def test_T151_the_divergent_shape_is_refused_naming_both_sides(tmp_path):
    """§3.3's divergent case: missing a known id **and** carrying an unknown one."""
    database = tmp_path / "state.db"
    SQLiteStateStore(database, clock=lambda: T0).close()
    connection = sqlite3.connect(database)
    connection.execute("DELETE FROM schema_version WHERE migration_id = ?", (HEAD,))
    connection.execute(
        "INSERT INTO schema_version(migration_id, applied_at, ctrlrun_version) VALUES(?,?,?)",
        ("9999_elsewhere", "2027-01-01T00:00:00+00:00", "0.9.0"),
    )
    connection.commit()
    connection.close()

    with pytest.raises(SchemaMismatch) as raised:
        SQLiteStateStore(database, clock=lambda: T0)
    message = str(raised.value)
    assert "9999_elsewhere" in message, "the refusal does not name the unknown id"
    assert HEAD in message, "the refusal does not name what is missing"

    connection = sqlite3.connect(database)
    remaining = connection.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
    connection.close()
    assert remaining == len(MIGRATIONS), "the store applied something to a divergent database"


def test_T151_a_gapped_history_is_refused_not_filled(tmp_path):
    """§3.3's gapped case: every recorded id known, but not a prefix.

    An implementer's natural move is to apply the missing one. That runs a migration out of
    order against a schema it was never written for, which is why the shape is named and
    refused rather than left undefined.
    """
    if len(MIGRATIONS) < 2:
        pytest.skip("a gap needs at least two migrations")
    database = tmp_path / "state.db"
    SQLiteStateStore(database, clock=lambda: T0).close()
    connection = sqlite3.connect(database)
    connection.execute("DELETE FROM schema_version WHERE migration_id = ?", (MIGRATIONS[0].id,))
    connection.commit()
    connection.close()

    with pytest.raises(SchemaMismatch) as raised:
        SQLiteStateStore(database, clock=lambda: T0)
    assert MIGRATIONS[0].id in str(raised.value)


# --- T152: adoption, every classification, from every release that has one -------------------


def test_T152_an_empty_database_is_migrated_to_head(tmp_path):
    database = tmp_path / "state.db"
    SQLiteStateStore(database, clock=lambda: T0).close()
    assert applied_migrations(database) == tuple(m.id for m in MIGRATIONS)


def test_T152_a_database_at_head_applies_nothing(tmp_path):
    database = tmp_path / "state.db"
    SQLiteStateStore(database, clock=lambda: T0).close()
    assert classify(applied_migrations(database)) is Classification.UP_TO_DATE


def test_T152_a_foreign_database_is_refused_naming_what_it_found(tmp_path):
    """§3.2: neither `schema_version` nor `effects`, but other tables present.

    It is somebody else's database, and creating CTRLRun's tables in it is not a recovery.
    """
    database = tmp_path / "someone-elses.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE customers(id TEXT)")
    connection.commit()
    connection.close()

    with pytest.raises(SchemaMismatch) as raised:
        SQLiteStateStore(database, clock=lambda: T0)
    assert "customers" in str(raised.value), "the refusal does not name what it found"


@pytest.mark.parametrize("version", ["0.1.0", "0.2.0", "0.4.0", "0.5.0"])
def test_T152_the_baseline_path_from_every_release_that_has_one(tmp_path_factory, version):
    """SPEC-v0.6 §3.2, and the defect this replaces.

    "Baseline" matches a v0.1 **or** v0.2 database, and `continuations` arrived in v0.2 while
    `delegations` arrived in v0.3. An adoption that recorded `0001_baseline` without running its
    DDL would leave a v0.1 database with neither table, and every continuation and delegation
    call would die on `no such table`.

    So this drives a continuation and a delegation against the migrated database rather than
    listing table names: a schema-name assertion is one an implementer can satisfy without the
    store working.
    """
    python = _release_venv(tmp_path_factory, version)
    if python is None:
        pytest.skip(f"ctrlrun=={version} could not be installed; no network")
    work = tmp_path_factory.mktemp(f"db-{version}")
    script = work / "build.py"
    script.write_text(
        textwrap.dedent("""
            import sys
            from datetime import UTC, datetime, timedelta
            from ctrlrun.state import SQLiteStateStore
            store = SQLiteStateStore(sys.argv[1], clock=lambda: datetime(2026,1,1,12,0,tzinfo=UTC))
            store.reserve_effect("refund:old", "act_o", timedelta(minutes=5))
            store.close()
        """),
        encoding="utf-8",
    )
    database = work / "state.db"
    done = subprocess.run([str(python), str(script), str(database)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert "schema_version" not in _tables(database)

    store = SQLiteStateStore(database, clock=lambda: T0)
    assert applied_migrations(database) == tuple(m.id for m in MIGRATIONS)
    assert store.get_effect("refund:old") is not None

    # The tables that arrived after v0.1, driven rather than named.
    from ctrlrun.action import Action, Principal
    from ctrlrun.state import DelegationRecord

    action = Action(
        name="stripe.refund",
        arguments={"payment_id": "txn_new"},
        principal=Principal(agent="a"),
    )
    store.reserve_effect("refund:cont", action.action_id, timedelta(minutes=5))
    store.begin_execution("refund:cont", action.action_id)
    store.hold_continuation(action, "refund:cont", "tok-1", T0 + timedelta(hours=1))
    assert store.take_continuation("tok-1").effect_key == "refund:cont"

    record = DelegationRecord(
        delegation_id="dlg_" + "a" * 32,
        parent_id="head-of-finance",
        depth=1,
        grant_json="{}",
        created_by_agent="a",
        created_by_user=None,
        created_via="api",
        created_at=T0,
    )
    store.put_delegation(record)
    assert store.get_delegation(record.delegation_id) is not None
    store.close()


# --- T152b: there is no flag that opens a database without migrating -------------------------


def test_T152b_no_flag_opens_a_database_without_migrating(tmp_path, monkeypatch):
    """SPEC-v0.6 §3.6 and `v0.4 §3.9`'s rule: the thing being verified must be the thing that
    ships. A store that could run un-migrated is a second configuration nobody tested.

    Asserted three ways, because "there is no flag" is a claim about an absence and an absence
    is only checked by looking everywhere one could hide.
    """
    import inspect

    signature = inspect.signature(SQLiteStateStore.__init__)
    accepted = set(signature.parameters) - {"self", "path", "clock"}
    assert not accepted, f"SQLiteStateStore grew a constructor argument: {sorted(accepted)}"

    database = tmp_path / "state.db"
    for name in ("CTRLRUN_SKIP_MIGRATION", "CTRLRUN_NO_MIGRATE", "CTRLRUN_SCHEMA"):
        monkeypatch.setenv(name, "1")
    SQLiteStateStore(database, clock=lambda: T0).close()
    assert applied_migrations(database) == tuple(m.id for m in MIGRATIONS), (
        "an environment variable changed the migration behaviour"
    )

    # Identifiers, not prose: the module's own docstring argues at length about why skipping
    # the baseline DDL *would be* a defect, and a substring search would flag the sentence that
    # exists to prevent the thing it is searching for.
    import ast

    from ctrlrun import migrations

    tree = ast.parse(Path(migrations.__file__).read_text(encoding="utf-8"))
    names = {
        node.id if isinstance(node, ast.Name) else node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Name | ast.Attribute)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(node.name)
            names.update(argument.arg for argument in node.args.args)
            names.update(argument.arg for argument in node.args.kwonlyargs)
    forbidden = {"skip", "skip_migration", "no_migrate", "dry_run", "auto_migrate", "force"}
    assert not (names & forbidden), (
        f"migrations.py has a relaxation knob: {sorted(names & forbidden)}"
    )
