"""Atomic reservation across processes. Build-list item 6; SPEC-v0.1 §5.3; T3, T12.

The guarantee under test is E1: `reserve_effect` succeeds for at most one caller per effect
key, across threads *and processes*. Threads would pass with a `threading.Lock`; agents run
as separate workers, so T3 uses `multiprocessing` with the "spawn" start method — the same
start method on Linux and macOS — against one on-disk SQLite file.

Nothing here counts executions in a Python object: eight processes share no memory. The fake
remote appends one line to a file per call, and the count is the line count.
"""

import json
import multiprocessing
import os
import sqlite3
import sys
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from ctrlrun import (
    Action,
    AmbiguousEffect,
    ApprovalMismatch,
    ApprovalRequest,
    Control,
    DuplicateEffect,
    InvalidArgument,
    Policy,
    Principal,
    SQLiteStateStore,
    context,
    protect,
)
from ctrlrun.approval import DEFAULT_APPROVAL_TTL, ApprovalStatus
from ctrlrun.effect import DEFAULT_LEASE, EffectState
from ctrlrun.receipt import ReceiptResult

POLICY = """
schema: ctrlrun.policy/v1
actions:
  stripe.refund:
    rules:
      - when: { amount_lte: 5000 }
        decision: allow
      - decision: deny
"""

WORKERS = 8
EFFECT_KEY = "refund:txn_9"

#: Worker exit codes. A worker that raises anything else exits non-zero some other way, so
#: an unexpected failure shows up as an unexpected code rather than a silently missing one.
COMMITTED = 0
BLOCKED_DUPLICATE = 3
BLOCKED_AMBIGUOUS = 4

#: Every wait in this module is bounded, so a broken reservation fails red instead of
#: hanging CI (CLAUDE.md working style).
BARRIER_TIMEOUT_S = 30.0
JOIN_TIMEOUT_S = 120.0


def _jsonl(path):
    return path.read_text(encoding="utf-8").splitlines()


def _fake_remote(log_path: str) -> str:
    """The fake remote. One `O_APPEND` write per call: the only cross-process call counter.

    A single small append is atomic on POSIX, so eight processes cannot lose or interleave a
    line. Counting in a Python object would count one process's calls, which is exactly the
    bug T3 exists to catch.
    """
    handle = os.open(log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(handle, b"called\n")
    finally:
        os.close(handle)
    return "re_txn_9"


def _refund_worker(db_path: str, log_path: str, barrier) -> int:
    """One agent process: reserve `refund:txn_9`, and execute only if the reservation won."""
    store = SQLiteStateStore(db_path)
    control = Control(Policy.from_yaml(POLICY), store)

    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def refund(payment_id: str, amount: int) -> str:
        return _fake_remote(log_path)

    barrier.wait(timeout=BARRIER_TIMEOUT_S)
    try:
        with context(agent="refund-agent"):
            refund(payment_id="txn_9", amount=2000)
    except DuplicateEffect:
        return BLOCKED_DUPLICATE
    except AmbiguousEffect:
        return BLOCKED_AMBIGUOUS
    finally:
        store.close()
    return COMMITTED


def _run_worker(db_path: str, log_path: str, barrier) -> None:
    sys.exit(_refund_worker(db_path, log_path, barrier))


def _spawn_agents(db_path, log_path):
    """Start `WORKERS` spawned processes on one barrier and return their exit codes."""
    spawn = multiprocessing.get_context("spawn")
    barrier = spawn.Barrier(WORKERS)
    processes = [
        spawn.Process(target=_run_worker, args=(str(db_path), str(log_path), barrier))
        for _ in range(WORKERS)
    ]
    for process in processes:
        process.start()
    deadline = time.monotonic() + JOIN_TIMEOUT_S
    for process in processes:
        process.join(timeout=max(0.0, deadline - time.monotonic()))
    stuck = [process for process in processes if process.exitcode is None]
    for process in stuck:
        process.kill()
        process.join(timeout=5)
    assert not stuck, f"{len(stuck)} worker(s) never finished: the reservation deadlocked"
    return [process.exitcode for process in processes]


# --- T3: concurrent agents, one reservation (SPEC §5.3 E1) ----------------------------


@pytest.fixture
def race(tmp_path):
    """Eight spawned agents racing for one effect key against one on-disk SQLite file."""
    db_path = tmp_path / "state.db"
    log_path = tmp_path / "remote-calls.log"
    log_path.touch()
    store = SQLiteStateStore(db_path)  # create the schema before the race, not during it
    exit_codes = _spawn_agents(db_path, log_path)
    yield store, log_path, exit_codes
    store.close()


def test_T3_exactly_one_agent_reserves_and_seven_are_blocked(race):
    _, _, exit_codes = race
    assert sorted(exit_codes) == [COMMITTED] + [BLOCKED_DUPLICATE] * (WORKERS - 1)


def test_T3_the_fake_remote_is_called_exactly_once(race):
    _, log_path, _ = race
    assert log_path.read_text(encoding="utf-8").splitlines() == ["called"]


def test_T3_one_committed_receipt_and_seven_blocked_receipts(race):
    store, _, _ = race
    results = [receipt.result for receipt in store.receipts()]
    assert results.count(ReceiptResult.COMMITTED) == 1
    assert results.count(ReceiptResult.BLOCKED) == WORKERS - 1
    assert len(results) == WORKERS


def test_T3_the_effect_is_committed_on_its_first_attempt(race):
    store, _, _ = race
    record = store.get_effect(EFFECT_KEY)
    assert record.state is EffectState.COMMITTED
    assert record.attempt == 1


def test_T3_every_proposal_is_a_distinct_action_for_the_same_effect(race):
    store, _, _ = race
    receipts = store.receipts()
    assert len({receipt.action_id for receipt in receipts}) == WORKERS
    assert {receipt.effect_key for receipt in receipts} == {EFFECT_KEY}


def test_T3_eight_processes_leave_the_jsonl_evidence_intact(race):
    """SPEC-v0.1 §6 — the same file the store mirrors into, written by eight workers.

    One `O_APPEND` write per record, as the fake remote does above: whole lines interleave,
    fragments do not. A torn line would make the evidence unreadable to everything.
    """
    store, _, _ = race
    written = [json.loads(line) for line in _jsonl(store.journal.receipts_path)]

    assert len(written) == WORKERS
    assert {document["receipt_id"] for document in written} == {
        receipt.receipt_id for receipt in store.receipts()
    }
    assert all(json.loads(line) for line in _jsonl(store.journal.events_path))


# --- one approval, eight processes (SPEC §4.2 A2, A4) ---------------------------------

APPROVAL_ID = "apr_000000000001"

#: Exit code for the seven workers that find the approval already spent.
BLOCKED_CONSUMED = 5


def _shared_action():
    """The same action content in every process, and therefore the same `action_hash`."""
    return Action(
        name="stripe.refund",
        arguments={"payment_id": "txn_9", "amount": 2000},
        principal=Principal(agent="refund-agent"),
        action_id="act_000000000001",
    )


def _consume_worker(db_path: str, index: int, barrier) -> int:
    """One agent presenting the shared approval, each for an effect key of its own.

    Distinct keys mean the reservation can never be what refuses a worker: whatever blocks
    the other seven blocked them at the approval, which is A2 across processes.
    """
    store = SQLiteStateStore(db_path)
    action = _shared_action()
    barrier.wait(timeout=BARRIER_TIMEOUT_S)
    try:
        store.consume_approval_and_reserve(
            APPROVAL_ID, action.action_hash, f"refund:txn_{index}", action.action_id
        )
    except ApprovalMismatch:
        return BLOCKED_CONSUMED
    finally:
        store.close()
    return COMMITTED


def _run_consumer(db_path: str, index: int, barrier) -> None:
    sys.exit(_consume_worker(db_path, index, barrier))


def test_only_one_of_eight_processes_consumes_one_approval(tmp_path):
    db_path = tmp_path / "state.db"
    store = SQLiteStateStore(db_path)
    action = _shared_action()
    store.put_approval_request(
        ApprovalRequest(
            request_id=APPROVAL_ID,
            action_hash=action.action_hash,
            action=action,
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + DEFAULT_APPROVAL_TTL,
        )
    )
    store.grant_approval(APPROVAL_ID, "cli:local")

    spawn = multiprocessing.get_context("spawn")
    barrier = spawn.Barrier(WORKERS)
    processes = [
        spawn.Process(target=_run_consumer, args=(str(db_path), index, barrier))
        for index in range(WORKERS)
    ]
    for process in processes:
        process.start()
    deadline = time.monotonic() + JOIN_TIMEOUT_S
    for process in processes:
        process.join(timeout=max(0.0, deadline - time.monotonic()))
    for process in processes:
        if process.exitcode is None:
            process.kill()
            process.join(timeout=5)
            pytest.fail("a worker never finished: approval consumption deadlocked")

    exit_codes = sorted(process.exitcode for process in processes)
    reserved = [
        index for index in range(WORKERS) if store.get_effect(f"refund:txn_{index}") is not None
    ]
    assert exit_codes == [COMMITTED] + [BLOCKED_CONSUMED] * (WORKERS - 1)
    assert len(reserved) == 1
    assert store.get_approval(APPROVAL_ID).status is ApprovalStatus.CONSUMED
    store.close()


#: How long the forced-open window below is held. Well inside `busy_timeout`, so the
#: contender blocks and then proceeds rather than erroring, and the test stays bounded.
HOLD_S = 0.25


def test_a_contending_consumer_waits_for_the_open_transaction(tmp_path):
    """E1 with the window forced open: the decision is taken under the write lock.

    One consumer pauses inside its transaction, after the approval was found granted and
    before it is marked consumed. A second consumer, on its own connection, must not be able
    to read that approval as still grantable in the meantime. `BEGIN IMMEDIATE` is what makes
    it wait — unlike the reservation, nothing like `UNIQUE(effect_key)` stands behind this.
    """
    store = SQLiteStateStore(tmp_path / "state.db")
    action = _shared_action()
    store.put_approval_request(
        ApprovalRequest(
            request_id=APPROVAL_ID,
            action_hash=action.action_hash,
            action=action,
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + DEFAULT_APPROVAL_TTL,
        )
    )
    store.grant_approval(APPROVAL_ID, "cli:local")

    inside = threading.Event()
    consume_locked = store._consume_locked

    def hold_the_transaction_open(connection, approval_id, now):
        inside.set()
        time.sleep(HOLD_S)
        consume_locked(connection, approval_id, now)

    store._consume_locked = hold_the_transaction_open
    outcomes: dict[str, object] = {}

    def first() -> None:
        outcomes["first"] = store.consume_approval_and_reserve(
            APPROVAL_ID, action.action_hash, "refund:txn_a", action.action_id
        )

    def second() -> None:
        inside.wait(timeout=BARRIER_TIMEOUT_S)
        try:
            store.consume_approval_and_reserve(
                APPROVAL_ID, action.action_hash, "refund:txn_b", action.action_id
            )
        except ApprovalMismatch as exc:
            outcomes["second"] = exc

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=BARRIER_TIMEOUT_S)

    assert [thread.is_alive() for thread in threads] == [False, False]
    assert "first" in outcomes, "the first consumer did not finish"
    assert isinstance(outcomes.get("second"), ApprovalMismatch)
    assert outcomes["second"].reason == "consumed"
    assert store.get_effect("refund:txn_b") is None
    store.close()


# --- T12: the approval is consumed with the reservation, or not at all (SPEC §4.2 A4) --


def _granted_approval(store, clock, action):
    """Put a granted approval for `action` in the store and return its id."""
    request = ApprovalRequest(
        request_id="apr_000000000001",
        action_hash=action.action_hash,
        action=action,
        created_at=clock(),
        expires_at=clock() + DEFAULT_APPROVAL_TTL,
    )
    store.put_approval_request(request)
    store.grant_approval(request.request_id, "cli:local")
    return request.request_id


def _refund_action(**overrides):
    fields = {
        "name": "stripe.refund",
        "arguments": {"payment_id": "txn_1", "amount": 2000},
        "principal": Principal(agent="refund-agent"),
    }
    fields.update(overrides)
    return Action(**fields)


def test_T12_an_exploding_reservation_leaves_the_approval_granted(
    state_store, fake_clock, monkeypatch
):
    """A4: if the reservation fails, the approval is *not* consumed."""
    action = _refund_action()
    approval_id = _granted_approval(state_store, fake_clock, action)

    def explode(*args, **kwargs):
        raise RuntimeError("the reservation write failed")

    monkeypatch.setattr(state_store, "_reserve_locked", explode)

    with pytest.raises(RuntimeError):
        state_store.consume_approval_and_reserve(
            approval_id, action.action_hash, "refund:txn_1", action.action_id, DEFAULT_LEASE
        )

    assert state_store.get_approval(approval_id).status is ApprovalStatus.GRANTED
    assert state_store.get_effect("refund:txn_1") is None


def test_T12_a_refused_reservation_leaves_the_approval_granted(state_store, fake_clock):
    """The same guarantee without a test double: a real committed effect refuses the key."""
    action = _refund_action()
    approval_id = _granted_approval(state_store, fake_clock, action)
    state_store.reserve_effect("refund:txn_1", "act_earlier", DEFAULT_LEASE)
    state_store.begin_execution("refund:txn_1", "act_earlier")
    state_store.commit_effect("refund:txn_1", "act_earlier", {"id": "re_1"})

    with pytest.raises(DuplicateEffect):
        state_store.consume_approval_and_reserve(
            approval_id, action.action_hash, "refund:txn_1", action.action_id, DEFAULT_LEASE
        )

    assert state_store.get_approval(approval_id).status is ApprovalStatus.GRANTED


def test_T12_a_still_granted_approval_can_be_presented_again(state_store, fake_clock):
    """An unconsumed approval is worth having: it still authorizes its action afterwards."""
    action = _refund_action()
    approval_id = _granted_approval(state_store, fake_clock, action)
    state_store.reserve_effect("refund:txn_1", "act_earlier", DEFAULT_LEASE)

    with pytest.raises(DuplicateEffect):
        state_store.consume_approval_and_reserve(
            approval_id, action.action_hash, "refund:txn_1", action.action_id, DEFAULT_LEASE
        )

    approval, reservation = state_store.consume_approval_and_reserve(
        approval_id, action.action_hash, "refund:txn_2", action.action_id, DEFAULT_LEASE
    )
    assert approval.approval_id == approval_id
    assert reservation.effect_key == "refund:txn_2"
    assert state_store.get_approval(approval_id).status is ApprovalStatus.CONSUMED


def test_T12_a_successful_reservation_consumes_the_approval(state_store, fake_clock):
    action = _refund_action()
    approval_id = _granted_approval(state_store, fake_clock, action)

    approval, reservation = state_store.consume_approval_and_reserve(
        approval_id, action.action_hash, "refund:txn_1", action.action_id, DEFAULT_LEASE
    )

    assert approval.action_hash == action.action_hash
    assert reservation.attempt == 1
    assert state_store.get_approval(approval_id).status is ApprovalStatus.CONSUMED
    assert state_store.get_effect("refund:txn_1").state is EffectState.RESERVED


def test_T12_a_mutated_action_reserves_nothing(state_store, fake_clock):
    """A1 runs before the reservation: a mutated action must not take the effect key either."""
    action = _refund_action()
    approval_id = _granted_approval(state_store, fake_clock, action)
    mutated = _refund_action(arguments={"payment_id": "txn_1", "amount": 5000})

    with pytest.raises(ApprovalMismatch) as excinfo:
        state_store.consume_approval_and_reserve(
            approval_id, mutated.action_hash, "refund:txn_1", mutated.action_id, DEFAULT_LEASE
        )

    assert excinfo.value.reason == "mismatch"
    assert state_store.get_approval(approval_id).status is ApprovalStatus.GRANTED
    assert state_store.get_effect("refund:txn_1") is None


# --- one process, many threads (SPEC §5.3 E1) ------------------------------------------


def test_only_one_of_sixteen_threads_reserves_an_effect_key(state_store):
    workers = 16
    barrier = threading.Barrier(workers)
    lock = threading.Lock()
    reserved = []
    refused = []

    def reserve(index: int) -> None:
        barrier.wait(timeout=BARRIER_TIMEOUT_S)
        try:
            reservation = state_store.reserve_effect(EFFECT_KEY, f"act_{index}", DEFAULT_LEASE)
        except DuplicateEffect as exc:
            with lock:
                refused.append(exc)
        else:
            with lock:
                reserved.append(reservation)

    threads = [threading.Thread(target=reserve, args=(index,)) for index in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=BARRIER_TIMEOUT_S)

    assert [thread.is_alive() for thread in threads] == [False] * workers
    assert len(reserved) == 1
    assert len(refused) == workers - 1
    assert {exc.state for exc in refused} == {"in_progress"}


# --- the SQLite mechanism behind E1 ----------------------------------------------------


def test_the_database_is_in_wal_mode(tmp_path):
    store = SQLiteStateStore(tmp_path / "state.db")
    try:
        (mode,) = store._connection().execute("PRAGMA journal_mode").fetchone()
    finally:
        store.close()
    assert mode.lower() == "wal"


def test_the_busy_timeout_is_five_seconds(tmp_path):
    store = SQLiteStateStore(tmp_path / "state.db")
    try:
        (timeout,) = store._connection().execute("PRAGMA busy_timeout").fetchone()
    finally:
        store.close()
    assert timeout == 5000


def test_the_effect_key_is_unique_in_the_schema(tmp_path):
    store = SQLiteStateStore(tmp_path / "state.db")
    connection = store._connection()
    store.reserve_effect(EFFECT_KEY, "act_1", DEFAULT_LEASE)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO effects(effect_key, state, action_id, attempt) VALUES(?,?,?,?)",
                (EFFECT_KEY, "reserved", "act_2", 1),
            )
    finally:
        store.close()


def test_an_in_memory_sqlite_path_is_refused():
    # A ":memory:" database is private to one connection, so it could never reserve across
    # processes. Fail closed rather than silently losing E1.
    with pytest.raises(InvalidArgument):
        SQLiteStateStore(":memory:")


def test_two_stores_on_one_file_see_the_same_effects(tmp_path):
    first = SQLiteStateStore(tmp_path / "state.db")
    second = SQLiteStateStore(tmp_path / "state.db")
    try:
        first.reserve_effect(EFFECT_KEY, "act_1", DEFAULT_LEASE)
        with pytest.raises(DuplicateEffect):
            second.reserve_effect(EFFECT_KEY, "act_2", DEFAULT_LEASE)
        assert second.get_effect(EFFECT_KEY).action_id == "act_1"
    finally:
        first.close()
        second.close()


def test_a_lease_that_expires_immediately_is_refused(state_store):
    with pytest.raises(InvalidArgument):
        state_store.reserve_effect(EFFECT_KEY, "act_1", timedelta(0))
