# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""T408 to T414: the ledger and the store amendment (SPEC-v0.9 §3).

The charge lands **inside the transaction that writes the reservation**, or it is a check-then-act
race two processes win together. This file is where that is proven rather than asserted, and the
proof is multi-process against Postgres: a counter that is correct in one process is not a claim
about anything an operator runs.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ctrlrun.state import (
    BudgetExhaustedError,
    Charge,
    InMemoryStateStore,
    SQLiteStateStore,
    check_charges,
)

POSTGRES_URL = os.environ.get("CTRLRUN_TEST_POSTGRES")
NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
DAY = timedelta(hours=24)
LEASE = timedelta(minutes=5)


def _charge(amount: int = 100, limit: int = 250, grant: str = "g") -> Charge:
    return Charge(grant_id=grant, metric="amount", amount=amount, limit=limit, window=DAY)


class _Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, by: timedelta) -> None:
        self.now += by


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture(
    params=[
        "in-memory",
        "sqlite",
        pytest.param(
            "postgres",
            marks=pytest.mark.skipif(
                POSTGRES_URL is None, reason="CTRLRUN_TEST_POSTGRES is not set"
            ),
        ),
    ]
)
def store(request, tmp_path, clock):
    if request.param == "in-memory":
        made: Any = InMemoryStateStore(clock=clock)
    elif request.param == "sqlite":
        made = SQLiteStateStore(tmp_path / "state.db", clock=clock)
    else:
        from ctrlrun.postgres import PostgresStateStore

        schema = f"ledger_{uuid.uuid4().hex[:12]}"
        PostgresStateStore.create_schema(POSTGRES_URL, schema)
        made = PostgresStateStore(POSTGRES_URL, schema=schema, clock=clock)
    yield made
    made.close()
    if request.param == "postgres":
        from ctrlrun.postgres import PostgresStateStore

        PostgresStateStore.drop_schema(POSTGRES_URL, schema)


def test_T408_a_charge_and_its_reservation_are_one_transaction(store, clock) -> None:
    """A refused budget writes **neither**: the predicate runs before anything is written."""
    store.reserve_effect("e1", "a", LEASE, (_charge(),))
    store.reserve_effect("e2", "a", LEASE, (_charge(),))
    with pytest.raises(BudgetExhaustedError):
        store.reserve_effect("e3", "a", LEASE, (_charge(),))
    assert store.get_effect("e3") is None, "a refused budget must leave no reservation"
    assert len(store.consumptions(grant_id="g")) == 2, "and no ledger row"


def test_T408a_the_refusal_names_the_grant_the_metric_and_the_window(store, clock) -> None:
    """§4.5. Asserted by field, because three of this milestone's refusals share a type."""
    store.reserve_effect("e1", "a", LEASE, (_charge(amount=250),))
    with pytest.raises(BudgetExhaustedError) as caught:
        store.reserve_effect("e2", "a", LEASE, (_charge(amount=1),))
    assert (caught.value.grant_id, caught.value.metric, caught.value.window) == ("g", "amount", DAY)


def test_T408b_an_unbudgeted_reservation_takes_the_0_8_0_path(store, clock) -> None:
    """R5. `charges=()` is the default, and a grant with no budget passes none."""
    reservation = store.reserve_effect("e1", "a", LEASE)
    assert reservation.attempt == 1
    assert store.consumptions() == (), "an unbudgeted reservation writes no ledger row"


def test_T411_a_replayed_insert_does_not_double_charge(store, clock) -> None:
    """§3.4, and `v0.6 §4.3.2` Table A1 row 2 is why: a lost `COMMIT` retries the insert once."""
    store.reserve_effect("e1", "a", LEASE, (_charge(),))
    charges = (_charge(),)
    if hasattr(store, "_charge_locked"):
        import inspect

        signature = inspect.signature(store._charge_locked)
        if "connection" in signature.parameters:
            connection = store._connection()
            store._charge_locked(connection, charges, "e1", 1, NOW)
            connection.commit()
        else:
            with store._lock:
                store._charge_locked(charges, "e1", 1, NOW)
    rows = store.consumptions(grant_id="g")
    assert len(rows) == 1, f"a replayed insert double-charged: {rows}"
    assert rows[0].amount == 100


@pytest.mark.skipif(POSTGRES_URL is None, reason="CTRLRUN_TEST_POSTGRES is not set")
@pytest.mark.parametrize("renewal", [False, True])
def test_T411a_an_ambiguous_commit_resolves_the_charge_with_the_reservation(
    tmp_path, renewal: bool
) -> None:
    """**The branch T411 does not reach**, and an independent review found it empty.

    `v0.6 §4.3.2`'s re-issue paths call `_authorize_and_reserve` again. Without `charges`
    forwarded, the retried transaction re-inserts the reservation and **nothing else**: the
    reservation lands, the ledger stays empty, and the effect happens while the budget never sees
    it. That is `reserved=1, charged=0`, the exact state §3.3.0's spike named as disqualifying the
    alternative design, reproduced inside the chosen one. It also falsified §3.3's second and
    stated-stronger bar for touching a frozen protocol.

    Driven through the real branch by making `_commit` lose the write and report itself ambiguous,
    which is `_commit`'s own documented behaviour for a dropped connection. T411 asserts the
    idempotence of a writer this path never called, which is why it was green throughout.

    Both branches: A1 row 2 (`reinsert`) on a first attempt, A2 (`reissue`) on a renewal.
    """
    import uuid as _uuid

    from ctrlrun.postgres import AmbiguousWrite, PostgresStateStore

    schema = f"amb_{_uuid.uuid4().hex[:12]}"
    PostgresStateStore.create_schema(POSTGRES_URL, schema)
    store = PostgresStateStore(POSTGRES_URL, schema=schema)
    try:
        if renewal:
            # A2's premise: a FAILED record, so the next reserve is a renewal.
            store.reserve_effect("e1", "a", LEASE, (_charge(),))
            store.begin_execution("e1", "a")
            store.fail_effect("e1", "a", "provably not executed")
            before = len(store.consumptions(grant_id="g"))
        else:
            before = 0

        fired: list[int] = []
        real = store._commit

        def lose_the_commit(connection: Any) -> None:
            if not fired:
                fired.append(1)
                connection.rollback()
                raise AmbiguousWrite("the commit was lost")
            real(connection)

        store._commit = lose_the_commit  # type: ignore[method-assign]
        store.reserve_effect("e1" if renewal else "e2", "a", LEASE, (_charge(),))

        rows = store.consumptions(grant_id="g")
        assert len(rows) == before + 1, (
            f"the re-issue dropped the charge: reserved, ledger={rows}. "
            "§3.3's second bar says one re-read resolves both"
        )
    finally:
        store.close()
        PostgresStateStore.drop_schema(POSTGRES_URL, schema)


def test_T412_a_three_level_chain_charges_all_three(store, clock) -> None:
    """§2.7, the rule that makes the feature mean anything.

    Without it a holder of a 100,000-a-day grant delegates ten children, each correctly contained,
    and spends 1,000,000. The store's half is that it writes one row per ancestor handed to it.
    """
    chain = (_charge(grant="root"), _charge(grant="mid"), _charge(grant="leaf"))
    store.reserve_effect("e1", "a", LEASE, chain)
    for grant in ("root", "mid", "leaf"):
        rows = store.consumptions(grant_id=grant)
        assert len(rows) == 1 and rows[0].amount == 100, grant


def test_T412a_any_exhausted_ancestor_refuses_the_whole_reservation(store, clock) -> None:
    """And the refusal names **that ancestor**, which may not be the grant that decided."""
    tight = Charge(grant_id="root", metric="amount", amount=100, limit=50, window=DAY)
    with pytest.raises(BudgetExhaustedError) as caught:
        store.reserve_effect("e1", "a", LEASE, (_charge(grant="leaf"), tight))
    assert caught.value.grant_id == "root"
    assert store.get_effect("e1") is None
    assert store.consumptions() == (), "a refusal on one ancestor writes no row for any"


def test_T408c_the_rolling_window_forgets(store, clock) -> None:
    """§2.5. A row older than the window cannot affect the sum, so the budget refills."""
    store.reserve_effect("e1", "a", LEASE, (_charge(amount=250),))
    with pytest.raises(BudgetExhaustedError):
        store.reserve_effect("e2", "a", LEASE, (_charge(amount=1),))
    clock.advance(DAY + timedelta(seconds=1))
    store.reserve_effect("e3", "a", LEASE, (_charge(amount=250),))
    assert len(store.consumptions(grant_id="g")) == 2


def test_the_predicate_is_inclusive_and_a_zero_limit_admits_a_zero_amount() -> None:
    """§3.3.1: `sum + amount <= limit`, and what a `limit: 0` grant actually does.

    It does **not** stop a grant: it refuses every action with a nonzero metric and permits
    unboundedly many zero-valued ones. Pinned because §3.3.1 records an earlier draft claiming
    the opposite.
    """
    check_charges((Charge("g", "amount", 100, 100, DAY),), lambda _c: 0)
    with pytest.raises(BudgetExhaustedError):
        check_charges((Charge("g", "amount", 101, 100, DAY),), lambda _c: 0)
    check_charges((Charge("g", "amount", 0, 0, DAY),), lambda _c: 0)


# --- T409: the race, multi-process, against Postgres (SPEC-v0.9 §3.6) -------------------------

_RACE_LIMIT, _RACE_AMOUNT, _RACE_WORKERS = 1000, 100, 24


def _race_worker(args):
    index, schema, barrier = args
    from ctrlrun.postgres import PostgresStateStore

    made = PostgresStateStore(POSTGRES_URL, schema=schema)
    try:
        # **The barrier is what makes this a test.** Built before it, so connection setup,
        # `search_path` and the migration check do not spread the workers out in time. Without
        # one the unlocked implementation "held" in four runs of four: the window was never
        # opened, which is CONTRIBUTING.md's fourth mutation pattern exactly.
        barrier.wait()
        made.reserve_effect(
            f"k{index}",
            "a",
            timedelta(minutes=5),
            (Charge("g", "amount", _RACE_AMOUNT, _RACE_LIMIT, timedelta(hours=24)),),
        )
        return "spent"
    except BudgetExhaustedError:
        return "refused"
    except Exception as exc:
        return f"error:{type(exc).__name__}"
    finally:
        made.close()


def _sqlite_race_worker(args):
    index, path, barrier = args
    made = SQLiteStateStore(path)
    try:
        barrier.wait()
        made.reserve_effect(
            f"k{index}",
            "a",
            timedelta(minutes=5),
            (Charge("g", "amount", _RACE_AMOUNT, _RACE_LIMIT, timedelta(hours=24)),),
        )
        return "spent"
    except BudgetExhaustedError:
        return "refused"
    except Exception as exc:
        return f"error:{type(exc).__name__}"
    finally:
        made.close()


@pytest.mark.serial
@pytest.mark.skipif(POSTGRES_URL is None, reason="CTRLRUN_TEST_POSTGRES is not set")
@pytest.mark.parametrize("run", range(3))
def test_T409_N_processes_racing_one_budget_spend_at_most_the_limit(run: int) -> None:
    """**Run repeatedly** (§3.6.2), because a broken implementation is occasionally right.

    The spike measured the unlocked version holding the limit in one run of four. A concurrency
    test run once against it would report `PASS` about a quarter of the time, which is how this
    milestone's central guarantee could ship green and broken.

    Each process takes a **distinct effect key**, so the effects table's own uniqueness does not
    serialise them and hide the defect.
    """
    from ctrlrun.postgres import PostgresStateStore

    schema = f"race_{uuid.uuid4().hex[:12]}"
    PostgresStateStore.create_schema(POSTGRES_URL, schema)
    try:
        with mp.Manager() as manager:
            barrier = manager.Barrier(_RACE_WORKERS)
            with mp.Pool(_RACE_WORKERS) as pool:
                results = pool.map(
                    _race_worker, [(i, schema, barrier) for i in range(_RACE_WORKERS)]
                )
        reader = PostgresStateStore(POSTGRES_URL, schema=schema)
        try:
            spent = sum(
                row.amount for row in reader.consumptions(grant_id="g") if row.released_at is None
            )
        finally:
            reader.close()
    finally:
        PostgresStateStore.drop_schema(POSTGRES_URL, schema)

    errors = [result for result in results if result.startswith("error")]
    assert not errors, f"a worker failed for a reason that is not the budget: {errors[:3]}"
    assert spent <= _RACE_LIMIT, (
        f"{_RACE_WORKERS} processes spent {spent} against a limit of {_RACE_LIMIT}"
    )
    assert results.count("spent") == _RACE_LIMIT // _RACE_AMOUNT, (
        "the limit must be spent exactly, not under-spent: an implementation that refused "
        "everything would satisfy the bound above and grade green"
    )
    assert results.count("refused") == _RACE_WORKERS - results.count("spent")


# --- T414: the migration, in both directions (SPEC-v0.9 §3.5.1) -------------------------------


def test_T414_the_migration_runs_and_a_0_8_0_store_upgrades(tmp_path) -> None:
    """`0007_budget_ledger` is additive and forward-only (`v0.6 §3`)."""
    from ctrlrun.migrations import HEAD, MIGRATIONS

    # `HEAD` moves with every milestone that adds a migration; what T414 is about is that
    # `0007_budget_ledger` is additive and forward-only, which is asserted below against the
    # migration itself rather than against whichever id happens to be last.
    assert HEAD == "0008_anchor_checkpoint_hold"
    assert any(migration.id == "0007_budget_ledger" for migration in MIGRATIONS)
    assert [migration.id for migration in MIGRATIONS][-1] == HEAD
    store = SQLiteStateStore(tmp_path / "state.db")
    try:
        # The table exists and is empty, which is what a fresh upgrade looks like.
        assert store.consumptions() == ()
        store.reserve_effect("e1", "a", LEASE, (_charge(),))
        assert len(store.consumptions()) == 1
    finally:
        store.close()


def test_T414a_a_0_8_0_binary_refuses_the_migrated_database(tmp_path) -> None:
    """The reverse direction, which `v0.6 §3.5` requires and which gets forgotten.

    A migration that only runs forwards turns a rollback into silent corruption: an older binary
    opening a newer database must refuse at open, naming the migration it does not know.
    `v0.7`'s T264 is the precedent.
    """
    from ctrlrun.errors import SchemaMismatch
    from ctrlrun.migrations import MIGRATIONS

    path = tmp_path / "state.db"
    store = SQLiteStateStore(path)
    store.close()

    older = tuple(migration for migration in MIGRATIONS if migration.id != "0007_budget_ledger")
    import ctrlrun.migrations as migrations_module

    real = migrations_module.MIGRATIONS
    migrations_module.MIGRATIONS = older
    try:
        with pytest.raises(SchemaMismatch) as caught:
            SQLiteStateStore(path).close()
    finally:
        migrations_module.MIGRATIONS = real
    assert "0007_budget_ledger" in str(caught.value)


def test_T408d_the_window_is_closed_at_the_floor(store, clock) -> None:
    """§2.5's `[now - window, now]`, and an independent review found all three half-open.

    A row at exactly `now - window` was dropping out. One comparison wide, in the permissive
    direction, and inconsistent with `consumptions(since=)`, which is closed: `inspect --since`
    would have shown a row the predicate had excluded.
    """
    store.reserve_effect("e1", "a", LEASE, (_charge(amount=250),))
    clock.advance(DAY)  # the first row is now EXACTLY at now - window
    with pytest.raises(BudgetExhaustedError):
        store.reserve_effect("e2", "a", LEASE, (_charge(amount=1),))
    clock.advance(timedelta(microseconds=1))
    store.reserve_effect("e3", "a", LEASE, (_charge(amount=250),))


def test_T408e_a_released_row_no_longer_counts(store, clock) -> None:
    """The `released_at IS NULL` filter, tested **here** rather than deferred to item 5.

    Item 4 ships the column, the migration and the filter; only the caller is item 5's. Reported
    as green-but-uncaught in this item's first mutation table, and an independent review showed
    the mutation is catchable now by setting `released_at` directly, which is the same white-box
    reach T411 already makes into `_charge_locked`.
    """
    store.reserve_effect("e1", "a", LEASE, (_charge(amount=250),))
    with pytest.raises(BudgetExhaustedError):
        store.reserve_effect("e2", "a", LEASE, (_charge(amount=1),))

    released = clock.now
    if isinstance(store, InMemoryStateStore):
        store._ledger = [
            row
            if row.effect_key != "e1"
            else type(row)(**{**row.__dict__, "released_at": released})
            for row in store._ledger
        ]
    elif isinstance(store, SQLiteStateStore):
        connection = store._connection()
        connection.execute(
            "UPDATE budget_ledger SET released_at = ? WHERE effect_key = ?",
            (released.isoformat(), "e1"),
        )
        connection.commit()
    else:
        connection = store._connection()
        connection.execute("BEGIN")
        store._use_schema(connection)
        connection.execute(
            f"UPDATE {store._q}.budget_ledger SET released_at = %s WHERE effect_key = %s",
            (released, "e1"),
        )
        connection.execute("COMMIT")

    store.reserve_effect("e3", "a", LEASE, (_charge(amount=250),))
    rows = store.consumptions(grant_id="g")
    assert len(rows) == 2
    assert sum(row.amount for row in rows if row.released_at is None) == 250


@pytest.mark.serial
@pytest.mark.skipif(POSTGRES_URL is None, reason="CTRLRUN_TEST_POSTGRES is not set")
def test_T410_the_same_race_against_SQLite(tmp_path) -> None:
    """§9.4's T410, which was named and not written. `BEGIN IMMEDIATE` takes the write lock
    before the first read, so nothing further is required; this is the assertion of that."""
    path = tmp_path / "race.db"
    SQLiteStateStore(path).close()  # migrate once, so the children only contend on the reserve

    with mp.Manager() as manager:
        barrier = manager.Barrier(12)
        with mp.Pool(12) as pool:
            results = pool.map(_sqlite_race_worker, [(i, str(path), barrier) for i in range(12)])

    reader = SQLiteStateStore(path)
    try:
        spent = sum(row.amount for row in reader.consumptions(grant_id="g"))
    finally:
        reader.close()
    errors = [result for result in results if result.startswith("error")]
    assert not errors, errors[:3]
    assert spent <= _RACE_LIMIT, f"12 processes spent {spent} against {_RACE_LIMIT}"
    assert results.count("spent") == _RACE_LIMIT // _RACE_AMOUNT


@pytest.mark.parametrize(
    ("amount", "limit", "window", "why"),
    [
        (-100, 250, DAY, "a negative amount REFUNDS the budget and the grant spends again"),
        (100, -1, DAY, "a negative limit"),
        (True, 250, DAY, "a bool amount, which is an int in Python"),
        (1.5, 250, DAY, "a float amount, which would drift"),
        (100, 250, timedelta(0), "a zero window"),
        (100, 250, -DAY, "a negative window"),
    ],
)
def test_T408f_a_charge_refuses_what_a_budget_refuses(amount, limit, window, why) -> None:
    """§2.2's rule applied to the value object an independent review found validating nothing.

    `Budget` beside it refuses all of these. A `StateStore` is reachable directly by a third-party
    caller, and §3.3.1 makes the store the holder of the MUST, so this is the defence in depth the
    loader cannot give.
    """
    from ctrlrun.errors import InvalidArgument

    with pytest.raises(InvalidArgument):
        Charge(grant_id="g", metric="amount", amount=amount, limit=limit, window=window)


def test_T408g_two_charges_on_one_grant_and_metric_are_refused(store, clock) -> None:
    """The silent-drop an independent review found: the predicate cannot see a sibling's amount,
    and §3.4's key carries no window, so the second row vanished on conflict. A spend the ledger
    never recorded."""
    from ctrlrun.errors import InvalidArgument

    with pytest.raises(InvalidArgument):
        store.reserve_effect("e1", "a", LEASE, (_charge(amount=100), _charge(amount=900)))
    assert store.get_effect("e1") is None
    assert store.consumptions() == ()
