"""Concurrency and failure injection against a real Postgres. Item 4; SPEC-v0.6 §4.5, §8.

**What was run, stated first, because §4.5 requires the PR to say so.** These are separate OS
processes against one Postgres, with the connection broken by a proxy this test owns. They are
**not two hosts**. Separate processes give separate connections, no shared memory and no shared
file locks -- which is what `BEGIN IMMEDIATE` was silently relying on and what a second host
removes -- so the *reservation* guarantee is exercised. What is not exercised is a network
partition between hosts, and the PR says so rather than implying otherwise.

The failure injection is better than a container for what §4.5 calls its most important test.
Killing a container reproduces "the connection died during `COMMIT`" only when the timing happens
to land there; a proxy that forwards the `COMMIT` and *then* drops the client reproduces it every
time. A test which "usually" reproduces an interleaving proves nothing about the run where it
did not.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ctrlrun.effect import EffectState
from ctrlrun.errors import AmbiguousEffect, DuplicateEffect
from failure_injection import Proxy, upstream_of

URL = os.environ.get("CTRLRUN_TEST_POSTGRES")

postgres = pytest.mark.skipif(
    not URL, reason="CTRLRUN_TEST_POSTGRES is not set; no server to run against"
)

CONTENDERS = 8
T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def schema():
    """A private schema per test, created and dropped."""
    from ctrlrun.postgres import PostgresStateStore

    name = f"xhost_{uuid.uuid4().hex[:12]}"
    PostgresStateStore.create_schema(URL, name)
    try:
        yield name
    finally:
        PostgresStateStore.drop_schema(URL, name)


@pytest.fixture
def proxy():
    host, port = upstream_of(URL)
    made = Proxy(host, port).start()
    try:
        yield made
    finally:
        made.stop()


def store_on(url: str, schema: str, *, clock=None):
    from ctrlrun.postgres import PostgresStateStore

    return PostgresStateStore(url, schema=schema, clock=clock or (lambda: T0))


# --- T155: the connection dies during COMMIT ------------------------------------------------


@postgres
def test_T155_a_connection_killed_during_commit_is_resolved_by_the_re_read(proxy, schema):
    """**The single most important test in this milestone** (SPEC-v0.6 §4.5).

    It is the case where a naive port reports `FAILED` for an effect that committed, and an agent
    acting on `FAILED` retries -- the exact failure this library exists to prevent, arriving
    through its own storage layer.

    The window is opened on purpose: the proxy forwards the `COMMIT` to the server, lets it land,
    and then takes the client's connection away. The store cannot know whether it committed, and
    §4.3.2 says the answer is to re-read.
    """
    store = store_on(proxy.url(URL), schema)
    proxy.kill_on_commit = True

    reservation = store.reserve_effect("refund:killed", "act_k", timedelta(minutes=5))

    assert proxy.clients_killed >= 1, (
        "the proxy never dropped a client mid-COMMIT, so the window this test exists for never "
        "opened. An earlier version slept before closing, which let the COMMIT's reply get back "
        "-- the store returned normally and this test passed having exercised nothing"
    )
    assert reservation.action_id == "act_k"

    # The re-read resolved it: the reservation is ours and the record says so.
    proxy.kill_on_commit = False
    checker = store_on(URL, schema)
    record = checker.get_effect("refund:killed")
    assert record is not None, "the commit was lost and the re-read did not retry it"
    assert record.state is EffectState.RESERVED
    assert record.action_id == "act_k"
    assert str(record.state) != "failed", (
        "a lost COMMIT was recorded as FAILED. FAILED means it definitely did not run; a "
        "connection lost during COMMIT means nobody knows (v0.1 §5.5, SPEC-v0.6 §4.3)"
    )

    # And a blind retry against that key is refused.
    with pytest.raises(DuplicateEffect):
        checker.reserve_effect("refund:killed", "act_other", timedelta(minutes=5))
    checker.close()
    store.close()


@postgres
def test_T155_no_effect_is_ever_recorded_failed_by_a_lost_commit(proxy, schema):
    """The negative that matters, driven across every write a lost `COMMIT` can hit.

    `FAILED` is the one state that permits an automatic retry (`v0.1 §5.4`), so a store that
    guessed it after an unobservable write would be handing an agent permission to act twice.
    """
    store = store_on(proxy.url(URL), schema)
    store.reserve_effect("refund:sweep", "act_s", timedelta(minutes=5))
    store.begin_execution("refund:sweep", "act_s")

    proxy.kill_on_commit = True
    with contextlib.suppress(Exception):
        store.commit_effect("refund:sweep", "act_s", {"ok": True})  # the record is the subject
    proxy.kill_on_commit = False

    checker = store_on(URL, schema)
    record = checker.get_effect("refund:sweep")
    assert record is not None
    assert record.state is not EffectState.FAILED, (
        f"a lost COMMIT left the record {record.state}; FAILED asserts non-execution and "
        "nothing here proved it"
    )
    checker.close()
    store.close()


# --- T155b: Table A2, the compare-and-set paths ---------------------------------------------


@postgres
@pytest.mark.parametrize("outcome", ["commit", "fail"])
def test_T155b_a_lost_commit_on_a_transition_re_issues(proxy, schema, outcome):
    """SPEC-v0.6 §4.3.2 Table A2, the row a single table gets wrong.

    A lost `COMMIT` on an `UPDATE` leaves the record in its **pre-state** -- our `action_id`, a
    state we were not writing. Reading that as *somebody moved it, refuse* would turn a committed
    effect into a human's problem, and a **proven non-execution** into `AMBIGUOUS`, throwing away
    the one automatic retry `v0.1 §5.4` grants. Neither is unsafe; both make the store useless in
    exactly the situation it was built for.
    """
    store = store_on(proxy.url(URL), schema)
    key = f"refund:a2-{outcome}"
    store.reserve_effect(key, "act_a2", timedelta(minutes=5))
    store.begin_execution(key, "act_a2")

    proxy.kill_on_commit = True
    with contextlib.suppress(Exception):
        # Resolution is asserted on the record, not on what this call raised.
        if outcome == "commit":
            store.commit_effect(key, "act_a2", {"ok": True})
        else:
            store.fail_effect(key, "act_a2", "the remote refused before acting")
    proxy.kill_on_commit = False

    assert proxy.clients_killed >= 1, "the window never opened"

    checker = store_on(URL, schema)
    record = checker.get_effect(key)
    assert record is not None
    want = EffectState.COMMITTED if outcome == "commit" else EffectState.FAILED
    assert record.state is want, (
        f"a lost COMMIT on {outcome}_effect left the record {record.state}, expected {want}. "
        "Table A2's row 2 re-issues; routing it through Table A1 refuses instead"
    )
    if outcome == "fail":
        # `v0.1 §5.4`'s one automatic retry is still available, which is the whole point of
        # not letting a proven non-execution become AMBIGUOUS.
        again = checker.reserve_effect(key, "act_retry", timedelta(minutes=5))
        assert again.attempt == 2
    checker.close()
    store.close()


# --- T156: a failed re-read refuses to proceed ----------------------------------------------


@postgres
def test_T156_a_failed_re_read_refuses_to_proceed(proxy, schema):
    """SPEC-v0.6 §4.3.2's last paragraph.

    The `COMMIT` is lost **and** the re-read cannot be made. The store writes no effect state and
    refuses; the caller sees a store exception rather than any outcome. That costs availability --
    a key may be reserved by an attempt that will never execute, and its lease will lapse to
    `AMBIGUOUS` and need a human -- and it never costs a double execution, which is the trade.
    """
    store = store_on(proxy.url(URL), schema)
    proxy.kill_on_commit = True
    proxy.refuse_connections = True

    with pytest.raises(Exception) as raised:
        store.reserve_effect("refund:noread", "act_n", timedelta(minutes=5))

    from ctrlrun.errors import NotExecuted

    assert proxy.clients_killed >= 1, "the window never opened"
    assert not isinstance(raised.value, NotExecuted), (
        "an unresolvable store write reached the caller as NotExecuted, which is the one "
        "exception an agent may read as permission to retry"
    )
    proxy.kill_on_commit = False
    proxy.refuse_connections = False

    # No effect STATE was written by the refusal. The row may exist -- the lost COMMIT very
    # likely landed, which is the whole reason the outcome was unknowable -- and what matters is
    # that the store did not move it anywhere on the strength of a guess.
    checker = store_on(URL, schema)
    record = checker.get_effect("refund:noread")
    if record is not None:
        assert record.state is EffectState.RESERVED, (
            f"the refusal left the record {record.state}; §4.3.2 writes no effect state when the "
            "re-read fails"
        )
        assert record.action_id == "act_n"
    checker.close()
    store.close()


# --- T157: T3's standard, across OS processes ------------------------------------------------


CONTEND = textwrap.dedent("""
    import json, os, sys, time
    from datetime import timedelta
    job = json.loads(sys.stdin.read())
    sys.path.insert(0, "/Users/arpanghoshal/ctrlrun/src")
    from ctrlrun.postgres import PostgresStateStore

    store = PostgresStateStore(job["url"], schema=job["schema"])
    gate = job["gate"]
    os.close(os.open(os.path.join(gate, f"a-{os.getpid()}"), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and len(os.listdir(gate)) < job["n"]:
        time.sleep(0.002)

    out = {"won": False, "error": None, "pid": os.getpid()}
    try:
        store.reserve_effect(job["key"], job["action_id"], timedelta(minutes=5))
        store.begin_execution(job["key"], job["action_id"])
        handle = os.open(os.path.join(job["calls"], "called"),
                         os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(handle)
        if job.get("hang"):
            sys.stdout.write(json.dumps({"won": True, "error": None, "pid": os.getpid()}))
            sys.stdout.flush()
            time.sleep(600)
        store.commit_effect(job["key"], job["action_id"], {"ok": True})
        out["won"] = True
    except BaseException as e:
        out["error"] = type(e).__name__
    finally:
        store.close()
    if not job.get("hang"):
        sys.stdout.write(json.dumps(out))
""")


def _contenders(url: str, schema: str, jobs: list[dict]) -> list[dict]:
    script = Path(tempfile.mkdtemp()) / "contend.py"
    script.write_text(CONTEND, encoding="utf-8")
    with tempfile.TemporaryDirectory() as gate, tempfile.TemporaryDirectory() as calls:
        payloads = [
            json.dumps(
                {**job, "url": url, "schema": schema, "gate": gate, "calls": calls, "n": len(jobs)}
            )
            for job in jobs
        ]
        children = [
            subprocess.Popen(
                [sys.executable, str(script)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in payloads
        ]
        for child, payload in zip(children, payloads, strict=True):
            assert child.stdin is not None
            child.stdin.write(payload)
            child.stdin.close()
        results = []
        for child in children:
            child.wait(timeout=120)
            assert child.stdout is not None
            raw = child.stdout.read()
            results.append(json.loads(raw) if raw.strip() else {"won": False, "error": "silent"})
        return results, sorted(os.listdir(calls))


@postgres
def test_T157_one_winner_across_os_processes(schema):
    """`v0.1 §7` T3's standard, unchanged, against a real Postgres.

    **Separate OS processes, not two hosts.** Separate processes give separate connections, no
    shared memory and no shared file locks -- exactly what `BEGIN IMMEDIATE` was relying on and
    what a second host removes -- so the reservation guarantee is exercised. What is not
    exercised is a partition between hosts, which §4.5 lists separately.
    """
    jobs = [{"key": "refund:t157", "action_id": f"act_{i:032x}"} for i in range(CONTENDERS)]
    results, called = _contenders(URL, schema, jobs)

    winners = [r for r in results if r["won"]]
    assert len(winners) == 1, f"{len(winners)} of {CONTENDERS} won; exactly one may\n{results}"
    assert called == ["called"], f"the fake remote was called {len(called)} times"
    for loser in (r for r in results if not r["won"]):
        assert loser["error"] in ("DuplicateEffect", "AmbiguousEffect"), (
            f"a loser saw {loser['error']}; v0.1 §5.4's refusals are DuplicateEffect and "
            "AmbiguousEffect"
        )

    store = store_on(URL, schema)
    record = store.get_effect("refund:t157")
    assert record is not None and record.state is EffectState.COMMITTED
    store.close()


# --- T158: kill -9 mid-transaction ------------------------------------------------------------


HOLD = textwrap.dedent("""
    import json, os, sys, time
    from datetime import timedelta
    job = json.loads(sys.stdin.read())
    sys.path.insert(0, "/Users/arpanghoshal/ctrlrun/src")
    from ctrlrun.postgres import PostgresStateStore

    from datetime import UTC, datetime
    # The same frozen instant the test uses. Without it the child's lease is stamped from the
    # wall clock and the test's "one hour later" is a year in the past, so the lapse never
    # happens and the assertion measures the calendar rather than the lease.
    frozen = datetime.fromisoformat(job["now"])
    store = PostgresStateStore(job["url"], schema=job["schema"], clock=lambda: frozen)
    store.reserve_effect(job["key"], job["action_id"], timedelta(minutes=5))
    store.begin_execution(job["key"], job["action_id"])
    # Readiness through the filesystem, not stdout: a pipe handshake is one more thing that can
    # be the reason a test hangs, and this test is about a process dying, not about plumbing.
    os.close(os.open(job["ready"], os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    while True:
        time.sleep(0.05)
""")


@postgres
def test_T158_kill_9_mid_transaction_ages_out_by_its_lease_and_nothing_else(schema):
    """SPEC-v0.6 §5.1, §5.3, under a real `SIGKILL`.

    A process is killed while holding a reservation it has begun executing. **Nothing reclaims
    the key**: *"the holder is dead" is not knowable from the store*. The record stays `EXECUTING`
    until its lease lapses, and the next contender then finds `AMBIGUOUS` -- a refusal, not a
    reclaim, and one only a human or a reconcile hook moves on.
    """
    work = Path(tempfile.mkdtemp())
    script = work / "hold.py"
    script.write_text(HOLD, encoding="utf-8")
    ready = work / "ready"

    child = subprocess.Popen(
        [sys.executable, str(script)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert child.stdin is not None
    child.stdin.write(
        json.dumps(
            {
                "url": URL,
                "schema": schema,
                "key": "refund:t158",
                "action_id": "act_doomed",
                "ready": str(ready),
                "now": T0.isoformat(),
            }
        )
    )
    child.stdin.close()

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not ready.exists():
        if child.poll() is not None:
            assert child.stderr is not None
            raise AssertionError(f"the holder exited early: {child.stderr.read()[-400:]}")
        time.sleep(0.05)
    assert ready.exists(), "the holder never took the reservation within its bound"

    os.kill(child.pid, signal.SIGKILL)
    child.wait(timeout=30)
    assert child.returncode != 0, "the holder exited cleanly; it was supposed to be killed"

    # The database is consistent, and the record is exactly where the dead process left it.
    store = store_on(URL, schema)
    record = store.get_effect("refund:t158")
    assert record is not None
    assert record.state is EffectState.EXECUTING, (
        f"the record is {record.state}; a dead holder changes nothing until its lease lapses"
    )

    # Before the lease lapses, a contender is refused as in-progress -- not handed the key.
    with pytest.raises(DuplicateEffect):
        store.reserve_effect("refund:t158", "act_next", timedelta(minutes=5))
    store.close()

    # After it lapses: AMBIGUOUS. A refusal that needs a human, never a reclaim.
    later = store_on(URL, schema, clock=lambda: T0 + timedelta(hours=1))
    with pytest.raises(AmbiguousEffect):
        later.reserve_effect("refund:t158", "act_next", timedelta(minutes=5))
    lapsed = later.get_effect("refund:t158")
    assert lapsed is not None and lapsed.state is EffectState.AMBIGUOUS
    later.close()


# --- T158b: partition, and every backend restarted --------------------------------------------


@postgres
def test_T158b_a_partition_stalls_and_never_reports_failed(proxy, schema):
    """§4.5's partition injection.

    The client sees a stall, not a reset -- which is the shape a partition actually has, and the
    reason `FAILED` must not be inferred from it. Every wait is bounded: a timeout here fails red
    rather than hanging CI.
    """
    store = store_on(proxy.url(URL), schema)
    store.reserve_effect("refund:part", "act_p", timedelta(minutes=5))

    proxy.partition = True
    started = time.monotonic()
    outcome: str
    try:
        store.begin_execution("refund:part", "act_p")
        outcome = "returned"
    except Exception as stalled:
        outcome = type(stalled).__name__
    elapsed = time.monotonic() - started
    proxy.partition = False
    assert elapsed < 90, "the partitioned call did not give up within its bound"

    checker = store_on(URL, schema)
    record = checker.get_effect("refund:part")
    assert record is not None
    assert record.state is not EffectState.FAILED, (
        f"a partition produced a FAILED record ({outcome}); nothing about a stall proves "
        "non-execution"
    )
    checker.close()
    store.close()


@postgres
def test_T158b_every_backend_terminated_under_load_leaves_no_failed_effect(schema):
    """The client-visible half of "restart Postgres under load".

    Every backend is terminated while contenders are mid-flight. From a client that is
    indistinguishable from a restart, and it is what this environment can do without taking the
    server away from everything else on the machine -- which the PR says rather than implying a
    full restart was run.
    """
    import threading

    import psycopg

    store = store_on(URL, schema)
    store.reserve_effect("refund:restart", "act_r", timedelta(minutes=5))
    store.begin_execution("refund:restart", "act_r")

    stop = threading.Event()

    def terminate_everything() -> None:
        admin = psycopg.connect(URL, autocommit=True)
        try:
            deadline = time.monotonic() + 5
            while not stop.is_set() and time.monotonic() < deadline:
                admin.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND pid <> pg_backend_pid()"
                )
                time.sleep(0.1)
        finally:
            admin.close()

    killer = threading.Thread(target=terminate_everything, daemon=True)
    killer.start()
    try:
        for _ in range(20):
            try:
                store.commit_effect("refund:restart", "act_r", {"ok": True})
                break
            except Exception:
                time.sleep(0.05)
    finally:
        stop.set()
        killer.join(timeout=30)

    checker = store_on(URL, schema)
    record = checker.get_effect("refund:restart")
    assert record is not None
    assert record.state is not EffectState.FAILED, (
        "a terminated backend produced a FAILED record; only the executor opts into FAILED"
    )
    checker.close()
    store.close()
