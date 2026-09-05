"""Concurrency and failure injection against a real Postgres. Item 4; SPEC-v0.6 §4.5, §8.

**What was run, stated first, because §4.5 requires the PR to say so.** These are separate OS
processes against one Postgres, with the connection broken by a proxy this test owns. They are
**not two hosts**. Separate processes give separate connections, no shared memory and no shared
file locks -- which is what `BEGIN IMMEDIATE` was silently relying on and what a second host
removes -- so the *reservation* guarantee is exercised. What is not exercised is a network
partition between two machines, and the PR says so rather than implying otherwise.

The failure injection is better than a container for what §4.5 calls its most important test.
Killing a container reproduces "the connection died during `COMMIT`" only when the timing happens
to land there; a proxy that forwards the `COMMIT` and *then* drops the client reproduces it every
time. A test which "usually" reproduces an interleaving proves nothing about the run where it
did not.

**What an independent review had to fix here, because the list is the useful part.** The first
version of this file passed while exercising almost none of what it named:

- The child processes imported `ctrlrun` from a **hardcoded absolute path**, so gutting the store
  under test left T157 and T158 green. They now import the tree this file lives in and *assert*
  that they did.
- T155b and T155 only ever reached Table A1 **row 1** and Table A2 **row 1** -- the proxy
  forwarded every `COMMIT`, so the write always landed and the "re-issue" and "retry the insert"
  rows the section exists for were unreachable. `drop_before_commit` reaches them, and driving
  A2 row 2 found an **unbounded recursion** in the store (fixed in this PR's `postgres.py`).
- T155 asserted an outcome that a store doing *nothing* also produces. It now asserts which
  branch of §4.3.2 ran, which is what §8 asked for and nothing implemented.
- `clients_killed` triggered on `b"COMMIT" in data`, which a Bind *parameter* satisfies. See
  `failure_injection._Frontend`.
- T158b's partition injected nothing observable and delivered a reset at a deadline the proxy
  owned; its restart half terminated zero backends.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ctrlrun.effect import EffectState
from ctrlrun.errors import AmbiguousEffect, DuplicateEffect, NotExecuted
from failure_injection import Proxy, upstream_of

URL = os.environ.get("CTRLRUN_TEST_POSTGRES")

postgres = pytest.mark.skipif(
    not URL, reason="CTRLRUN_TEST_POSTGRES is not set; no server to run against"
)

CONTENDERS = 8
T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

#: The tree under test, derived from this file. A child that hardcodes a path grades whatever is
#: at that path -- on a release verification run from a fresh clone, the maintainer's working
#: tree. The children insert this and assert `ctrlrun.__file__` is under it, and the parent
#: asserts the same on every result.
REPO_SRC = str(Path(__file__).resolve().parents[1] / "src")

#: Every child preamble. Two lines and one assertion, and the assertion is the point.
CHILD_PREAMBLE = """
    import json, os, sys, time
    from datetime import timedelta
    job = json.loads(sys.stdin.read())
    sys.path.insert(0, job["src"])
    import ctrlrun
    _WHERE = os.path.realpath(ctrlrun.__file__)
    assert _WHERE.startswith(os.path.realpath(job["src"])), (
        "the child imported ctrlrun from %s, not the tree under test at %s"
        % (_WHERE, job["src"]))
    from ctrlrun.postgres import PostgresStateStore
"""


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


def branches(caplog) -> list[str]:
    """Which §4.3.2 rows the store reported taking, in order (SPEC-v0.6 §4.3.4)."""
    return [
        record.branch for record in caplog.records if getattr(record, "branch", None) is not None
    ]


@pytest.fixture
def resolutions(caplog):
    """Capture §4.3.4's branch reports, and nothing else."""
    caplog.set_level(logging.WARNING, logger="ctrlrun.postgres")
    return caplog


# --- the injector's own positive control ----------------------------------------------------


def test_the_kill_trigger_reads_frames_and_not_the_payload():
    """`failure_injection` grepped for `b"COMMIT"` and fired on a Bind *parameter*.

    No server needed, and that is the point: this is a fixture test of the injector, written
    because the guard a review found broken -- `clients_killed >= 1`, added to catch exactly this
    class of false green -- could not tell the difference. An effect key containing the word
    killed the client on the `SELECT`'s Bind, which is §4.3's Table A row 1, a clean `FAILED`
    *before* `COMMIT`, and not the ambiguous window the test claimed to open.
    """
    import struct

    from failure_injection import _Frontend, is_commit

    def untyped(body: bytes) -> bytes:
        return struct.pack("!i", 4 + len(body)) + body

    def typed(kind: bytes, body: bytes) -> bytes:
        return kind + struct.pack("!i", 4 + len(body)) + body

    ssl_request = untyped(struct.pack("!i", 80877103))
    startup = untyped(struct.pack("!i", 196608) + b"user\x00bob\x00\x00")
    bind = typed(b"B", b"refund:COMMIT-me\x00")  # the payload that used to trigger it
    select = typed(b"Q", b"SELECT 1\x00")
    commit = typed(b"Q", b"COMMIT\x00")
    stream = ssl_request + startup + bind + select + commit

    assert b"COMMIT" in bind, "the control is void: this Bind does not contain the word"

    parsed = _Frontend().feed(stream)
    assert [kind for kind, _, _ in parsed] == [b"", b"", b"B", b"Q", b"Q"]
    assert [offset for kind, body, offset in parsed if is_commit(kind, body)] == [
        len(ssl_request) + len(startup) + len(bind) + len(select)
    ], "the COMMIT was not found where it is, or something else was mistaken for it"

    # And it survives arriving one byte at a time, which is the case a length-prefixed parser
    # exists for and a substring search never had to handle.
    split = _Frontend()
    seen = [m for i in range(len(stream)) for m in split.feed(stream[i : i + 1])]
    assert sum(1 for kind, body, _ in seen if is_commit(kind, body)) == 1
    assert len(seen) == 5


# --- T155: the connection dies during COMMIT ------------------------------------------------


@postgres
def test_T155_a_connection_killed_during_commit_is_resolved_by_the_re_read(
    proxy, schema, resolutions
):
    """**The single most important test in this milestone** (SPEC-v0.6 §4.5).

    It is the case where a naive port reports `FAILED` for an effect that committed, and an agent
    acting on `FAILED` retries -- the exact failure this library exists to prevent, arriving
    through its own storage layer.

    The window is opened on purpose: the proxy forwards the `COMMIT` to the server, lets it land,
    and then takes the client's connection away. The store cannot know whether it committed, and
    §4.3.2 says the answer is to re-read.

    **The branch is asserted, not the outcome.** The outcome here -- a reservation that is ours,
    and a blind retry refused -- is *also* what a store that never re-read at all would produce,
    because the write did land. A review demonstrated it: with `_resolve_lost_insert` replaced by
    `return`, every assertion about the record still passed. §8 asks that "the events name which
    branch of §4.3.2 ran", and §4.3.4 is where the store says so.
    """
    store = store_on(proxy.url(URL), schema)
    proxy.reset_counters()
    proxy.kill_on_commit = True

    reservation = store.reserve_effect("refund:killed", "act_k", timedelta(minutes=5))

    assert not proxy.tls_negotiated, "TLS was negotiated; the proxy cannot see a COMMIT at all"
    assert proxy.commits_seen == 1, (
        f"the proxy parsed {proxy.commits_seen} COMMIT frames, expected exactly the one this "
        "reservation issues"
    )
    assert proxy.clients_killed == 1, (
        "the proxy never dropped a client mid-COMMIT, so the window this test exists for never "
        "opened. An earlier version slept before closing, which let the COMMIT's reply get back "
        "-- the store returned normally and this test passed having exercised nothing"
    )
    assert reservation.action_id == "act_k"

    assert branches(resolutions) == ["a1.row1.ours"], (
        f"the store reported {branches(resolutions)}; §4.3.2 Table A1 row 1 is the branch a "
        "landed COMMIT takes, and a store that skipped the re-read entirely reports nothing "
        "while satisfying every other assertion here"
    )

    # The re-read resolved it: the reservation is ours and the record says so.
    proxy.kill_on_commit = False
    checker = store_on(URL, schema)
    record = checker.get_effect("refund:killed")
    assert record is not None, "the commit was lost and the re-read did not retry it"
    assert record.state is EffectState.RESERVED
    assert record.action_id == "act_k"

    # And a blind retry against that key is refused.
    with pytest.raises(DuplicateEffect):
        checker.reserve_effect("refund:killed", "act_other", timedelta(minutes=5))
    checker.close()
    store.close()


@postgres
def test_T155_the_window_opens_on_the_COMMIT_even_when_the_key_contains_the_word(
    proxy, schema, resolutions
):
    """The end-to-end half of the control above, against a real server.

    Same reservation, same injection, one difference: the effect key contains `COMMIT`, so it
    travels as a Bind parameter on the `SELECT` that precedes the write. Under the substring
    trigger the client died there instead -- before `COMMIT` was ever issued, which is a clean
    `FAILED` and no ambiguity at all -- and every assertion about `clients_killed` still passed.
    """
    store = store_on(proxy.url(URL), schema)
    proxy.reset_counters()
    proxy.kill_on_commit = True

    store.reserve_effect("refund:COMMIT-me", "act_c", timedelta(minutes=5))

    assert proxy.commits_seen == 1, (
        f"{proxy.commits_seen} COMMIT frames on a reservation that issues one; the key's payload "
        "is being read as a command"
    )
    assert branches(resolutions) == ["a1.row1.ours"], (
        f"the store reported {branches(resolutions)}; the kill landed somewhere other than the "
        "COMMIT, so this is not the ambiguous window"
    )
    proxy.kill_on_commit = False

    checker = store_on(URL, schema)
    record = checker.get_effect("refund:COMMIT-me")
    assert record is not None and record.state is EffectState.RESERVED
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

    proxy.reset_counters()
    proxy.kill_on_commit = True
    raised: BaseException | None = None
    try:
        store.commit_effect("refund:sweep", "act_s", {"ok": True})  # the record is the subject
    except BaseException as broke:
        raised = broke
    proxy.kill_on_commit = False

    assert proxy.clients_killed >= 1, "the window never opened"
    assert not isinstance(raised, NotExecuted), (
        "a lost COMMIT reached the caller as NotExecuted, the one exception that means "
        "'definitely did not happen'"
    )

    checker = store_on(URL, schema)
    record = checker.get_effect("refund:sweep")
    assert record is not None
    assert record.state is not EffectState.FAILED, (
        f"a lost COMMIT left the record {record.state}; FAILED asserts non-execution and "
        "nothing here proved it"
    )
    checker.close()
    store.close()


# --- T155b: Table A2 row 1, and T155d/e/f: the rows that re-issue ---------------------------


@postgres
@pytest.mark.parametrize("outcome", ["commit", "fail"])
def test_T155b_a_landed_commit_on_a_transition_is_seen_as_landed(
    proxy, schema, outcome, resolutions
):
    """SPEC-v0.6 §4.3.2 Table A2 **row 1**: the `UPDATE` landed and the re-read says so.

    Named for what it does. The re-issue is `test_T155e`, below, which needs a `COMMIT` the
    server never receives -- and until this PR the proxy had no mode that could produce one, so
    §8's "the store **re-issues** the `UPDATE`" was asserted by a test that never reached the
    branch. Two mutants proved it: deleting the re-issue arm, and deleting the whole Table A2
    re-read, both left this test green.
    """
    store = store_on(proxy.url(URL), schema)
    key = f"refund:a2-{outcome}"
    store.reserve_effect(key, "act_a2", timedelta(minutes=5))
    store.begin_execution(key, "act_a2")
    resolutions.clear()
    proxy.reset_counters()

    proxy.kill_on_commit = True
    raised: BaseException | None = None
    try:
        if outcome == "commit":
            store.commit_effect(key, "act_a2", {"ok": True})
        else:
            store.fail_effect(key, "act_a2", "the remote refused before acting")
    except BaseException as broke:
        raised = broke
    proxy.kill_on_commit = False

    assert proxy.clients_killed == 1, "the window never opened"
    assert not isinstance(raised, NotExecuted)
    assert branches(resolutions) == ["a2.row1.landed"]

    checker = store_on(URL, schema)
    record = checker.get_effect(key)
    assert record is not None
    want = EffectState.COMMITTED if outcome == "commit" else EffectState.FAILED
    assert record.state is want, (
        f"a lost COMMIT on {outcome}_effect left the record {record.state}, expected {want}. "
        "Routing Table A2 through Table A1 refuses instead"
    )
    if outcome == "fail":
        # `v0.1 §5.4`'s one automatic retry is still available, which is the whole point of
        # not letting a proven non-execution become AMBIGUOUS.
        again = checker.reserve_effect(key, "act_retry", timedelta(minutes=5))
        assert again.attempt == 2
    checker.close()
    store.close()


@postgres
def test_T155d_a_commit_the_server_never_received_retries_the_insert(proxy, schema, resolutions):
    """§4.3.2 Table A1 **row 2**: no record, so re-issue the same reservation once.

    Unreachable before this PR. The proxy drops the client *without* forwarding the `COMMIT`, so
    the server rolls the transaction back and the re-read genuinely finds nothing -- which is the
    only way this row is entered. The count is 1 so the retry's own `COMMIT` gets through;
    swallowing that one too is `test_T155f`.
    """
    store = store_on(proxy.url(URL), schema)
    proxy.reset_counters()
    proxy.drop_before_commit = 1

    reservation = store.reserve_effect("refund:lost", "act_l", timedelta(minutes=5))

    assert proxy.commits_dropped == 1, "no COMMIT was swallowed; the row was never entered"
    assert branches(resolutions) == ["a1.row2.reinsert"]
    assert reservation.action_id == "act_l"

    checker = store_on(URL, schema)
    record = checker.get_effect("refund:lost")
    assert record is not None, "the retry did not land the reservation"
    assert record.state is EffectState.RESERVED
    assert record.action_id == "act_l"
    assert record.attempt == 1, "the retry of one lost COMMIT is the same attempt, not a second"
    checker.close()
    store.close()


@postgres
@pytest.mark.parametrize("outcome", ["commit", "fail"])
def test_T155e_a_commit_the_server_never_received_re_issues_the_update(
    proxy, schema, outcome, resolutions
):
    """§4.3.2 Table A2 **row 2**: unchanged and still ours, so re-issue the `UPDATE`.

    The row §8 names T155b for, reached here for the first time. Re-issuing is safe because the
    `UPDATE` is conditional on the pre-state: had the first one landed, the second matches nothing
    and the re-read sees row 1 instead.
    """
    store = store_on(proxy.url(URL), schema)
    key = f"refund:reissue-{outcome}"
    store.reserve_effect(key, "act_r2", timedelta(minutes=5))
    store.begin_execution(key, "act_r2")
    resolutions.clear()
    proxy.reset_counters()

    proxy.drop_before_commit = 1
    if outcome == "commit":
        store.commit_effect(key, "act_r2", {"ok": True})
    else:
        store.fail_effect(key, "act_r2", "the remote refused before acting")

    assert proxy.commits_dropped == 1
    assert branches(resolutions) == ["a2.row2.reissue"], (
        f"the store reported {branches(resolutions)}; a lost COMMIT on an UPDATE leaves the "
        "record in its PRE-state, and reading that as 'somebody moved it, refuse' is the "
        "collapse §4.3.2 exists to forbid"
    )

    checker = store_on(URL, schema)
    record = checker.get_effect(key)
    assert record is not None
    want = EffectState.COMMITTED if outcome == "commit" else EffectState.FAILED
    assert record.state is want
    checker.close()
    store.close()


@postgres
def test_T155f_a_second_lost_commit_on_the_same_operation_fails_closed(proxy, schema):
    """§4.2's bound, on the re-issue path: *every loop in this project is bounded*.

    `_authorize_and_reserve` carries a `retrying` flag for exactly this and says so in a comment.
    `_transition` did not, so Table A2's re-issue called back into a `_transition` that could lose
    its `COMMIT` again and re-enter the same resolution -- unbounded. Driven with a proxy that
    swallows every `COMMIT`, the store recursed 96 deep and escaped as `RecursionError`, which is
    outside the error taxonomy entirely, leaving the record stranded. Found by review; the bound
    is in this PR.
    """
    store = store_on(proxy.url(URL), schema)
    store.reserve_effect("refund:bounded", "act_b", timedelta(minutes=5))
    store.begin_execution("refund:bounded", "act_b")

    proxy.reset_counters()
    proxy.drop_before_commit = 1000  # every COMMIT from here on
    raised: BaseException | None = None
    try:
        store.commit_effect("refund:bounded", "act_b", {"ok": True})
    except BaseException as broke:
        raised = broke
    proxy.drop_before_commit = 0

    assert raised is not None, "every COMMIT was swallowed and the call still returned"
    assert not isinstance(raised, RecursionError), (
        "the re-issue path recursed until Python stopped it. A store that runs out of stack "
        "raises outside this library's closed set of errors, and no caller can classify it"
    )
    assert not isinstance(raised, NotExecuted), "a lost COMMIT is never a proven non-execution"
    assert proxy.commits_dropped >= 2, (
        f"only {proxy.commits_dropped} COMMIT was swallowed; the second one is the bound this "
        "test is about"
    )

    checker = store_on(URL, schema)
    record = checker.get_effect("refund:bounded")
    assert record is not None
    assert record.state is not EffectState.FAILED
    checker.close()
    store.close()


# T155c is not here, and is not missing. §4.3.3's identity check -- the double execution this
# milestone's review found, where "a record carrying our `action_id`" was satisfied by another
# process's live reservation -- is `test_T155c_the_re_read_identity_check_is_not_an_action_id_match`
# and `test_T155c_the_re_read_accepts_only_our_own_row` in `tests/test_postgres.py`, which run
# against every backend. Reducing §4.3.3's comparison to `action_id` alone fails them. It needs no
# proxy: the window is opened by a held transaction rather than by a broken connection, so it
# belongs with the store's own tests.


# --- T156: a failed re-read refuses to proceed ----------------------------------------------


@postgres
def test_T156_a_failed_re_read_refuses_to_proceed(proxy, schema, resolutions):
    """SPEC-v0.6 §4.3.2's last paragraph.

    The `COMMIT` is lost **and** the re-read cannot be made. The store writes no effect state and
    refuses; the caller sees a store exception rather than any outcome. That costs availability --
    a key may be reserved by an attempt that will never execute, and its lease will lapse to
    `AMBIGUOUS` and need a human -- and it never costs a double execution, which is the trade.

    The exception type is asserted. `pytest.raises(Exception)` pins nothing: an `AttributeError`
    from a bug in the store would have satisfied it.

    **And the type alone is still not enough**, which a mutation run found after the first fix.
    Make `_fresh_read_effect` swallow its connect failure and return `None` -- turning §4.3.2's
    *"the re-read failed, refuse and write nothing"* into Table A1's *"no record, retry the
    insert"* -- and the caller still sees `OperationalError`, because the retry's own connect
    fails too. The two are indistinguishable by exception. They are distinguishable by §4.3.4:
    refusing reports **no branch at all**, because the re-read raised before any row of Table A
    was chosen, while failing open reports `a1.row2.reinsert`. If the connection had come back
    between the two calls, the fail-open store would have silently taken the retry and this test
    could not have told.
    """
    import psycopg

    executed: list[str] = []
    store = store_on(proxy.url(URL), schema)
    proxy.reset_counters()
    proxy.kill_on_commit = True
    proxy.refuse_connections = True

    with pytest.raises(psycopg.OperationalError) as raised:
        store.reserve_effect("refund:noread", "act_n", timedelta(minutes=5))
        executed.append("the executor would run here")

    assert proxy.clients_killed == 1, "the window never opened"
    assert executed == [], "§8: the executor is never reached"
    assert branches(resolutions) == [], (
        f"the store reported {branches(resolutions)}; §4.3.2 chooses no row of Table A when the "
        "re-read itself fails, and a store that reported one resolved an unobservable write on "
        "the strength of a read it never made"
    )
    assert not isinstance(raised.value, NotExecuted), (
        "an unresolvable store write reached the caller as NotExecuted, which is the one "
        "exception an agent may read as permission to retry"
    )
    proxy.kill_on_commit = False
    proxy.refuse_connections = False

    # No effect STATE was written by the refusal. The row is there -- the lost COMMIT landed,
    # which is the whole reason the outcome was unknowable -- and it is exactly as it was. The
    # first version hedged this behind `if record is not None`, which made §8's "the record is
    # left exactly as it was" optional and would have hidden a regression that dropped the row.
    checker = store_on(URL, schema)
    record = checker.get_effect("refund:noread")
    assert record is not None, "the refusal lost the row it refused to interpret"
    assert record.state is EffectState.RESERVED, (
        f"the refusal left the record {record.state}; §4.3.2 writes no effect state when the "
        "re-read fails"
    )
    assert record.action_id == "act_n"
    checker.close()
    store.close()


# --- T157: T3's standard, across OS processes ------------------------------------------------


CONTEND = textwrap.dedent(f"""{CHILD_PREAMBLE}
    store = PostgresStateStore(job["url"], schema=job["schema"])
    gate = job["gate"]
    marker = os.path.join(gate, f"a-{{os.getpid()}}")
    os.close(os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and len(os.listdir(gate)) < job["n"]:
        time.sleep(0.002)
    late = len(os.listdir(gate)) < job["n"]

    out = {{"won": False, "error": None, "pid": os.getpid(),
           "ctrlrun": _WHERE, "barrier_timed_out": late}}
    try:
        store.reserve_effect(job["key"], job["action_id"], timedelta(minutes=5))
        store.begin_execution(job["key"], job["action_id"])
        # One marker per caller. A shared name could only ever be created once, so a second
        # execution raised FileExistsError in its own child and was caught by the loser-error
        # assertion -- a different assertion from the one whose message said "called N times".
        handle = os.open(os.path.join(job["calls"], f"called-{{os.getpid()}}"),
                         os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(handle)
        store.commit_effect(job["key"], job["action_id"], {{"ok": True}})
        out["won"] = True
    except BaseException as e:
        out["error"] = type(e).__name__
    finally:
        store.close()
    sys.stdout.write(json.dumps(out))
""")


def _contenders(url: str, schema: str, jobs: list[dict]) -> tuple[list[dict], list[str]]:
    with (
        tempfile.TemporaryDirectory() as home,
        tempfile.TemporaryDirectory() as gate,
        tempfile.TemporaryDirectory() as calls,
    ):
        script = Path(home) / "contend.py"
        script.write_text(CONTEND, encoding="utf-8")
        payloads = [
            json.dumps(
                {
                    **job,
                    "url": url,
                    "schema": schema,
                    "src": REPO_SRC,
                    "gate": gate,
                    "calls": calls,
                    "n": len(jobs),
                }
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
        try:
            # Every stdin first: writing and reading one child at a time serialises them, and a
            # contention test whose contenders run in sequence measures nothing.
            for child, payload in zip(children, payloads, strict=True):
                assert child.stdin is not None
                child.stdin.write(payload)
                child.stdin.close()
            results = []
            for child in children:
                child.wait(timeout=120)
                assert child.stdout is not None and child.stderr is not None
                raw = child.stdout.read()
                results.append(
                    json.loads(raw)
                    if raw.strip()
                    else {"won": False, "error": "silent", "stderr": child.stderr.read()[-400:]}
                )
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
        return results, sorted(os.listdir(calls))


@postgres
def test_T157_one_winner_across_os_processes(schema):
    """`v0.1 §7` T3's standard, unchanged, against a real Postgres.

    **Separate OS processes, not two hosts.** Separate processes give separate connections, no
    shared memory and no shared file locks -- exactly what `BEGIN IMMEDIATE` was relying on and
    what a second host removes -- so the reservation guarantee is exercised. What is not
    exercised is a partition between hosts, which §4.5 lists separately.

    **§8 also asks for "one committed receipt and N-1 blocked", and this test does not check it.
    Not applicable is not a pass, so the reason:** receipts are written by `Control`, not by a
    store, and no `Control` participates here. `v0.1 §7` T3 covers the receipt half against the
    SQLite store and is unchanged by this milestone; what a Postgres backend can get wrong, and
    what this test is for, is the reservation underneath it.
    """
    jobs = [{"key": "refund:t157", "action_id": f"act_{i:032x}"} for i in range(CONTENDERS)]
    results, called = _contenders(URL, schema, jobs)

    for result in results:
        assert result.get("ctrlrun", "").startswith(REPO_SRC), (
            f"a child imported ctrlrun from {result.get('ctrlrun')!r}, not the tree under test. "
            "A hardcoded path here graded a different checkout entirely, and gutting the store "
            "left this test green"
        )
        assert not result.get("barrier_timed_out"), (
            "a contender gave up on the barrier and reserved alone; the contention this test "
            "measures did not happen"
        )

    winners = [r for r in results if r["won"]]
    assert len(winners) == 1, f"{len(winners)} of {CONTENDERS} won; exactly one may\n{results}"
    assert called == [f"called-{winners[0]['pid']}"], (
        f"the fake remote was called by {called}; exactly one caller, the winner, may reach it"
    )
    for loser in (r for r in results if not r["won"]):
        assert loser["error"] in ("DuplicateEffect", "AmbiguousEffect"), (
            f"a loser saw {loser['error']}; v0.1 §5.4's refusals are DuplicateEffect and "
            "AmbiguousEffect"
        )

    store = store_on(URL, schema)
    record = store.get_effect("refund:t157")
    assert record is not None and record.state is EffectState.COMMITTED
    store.close()


# --- T158: kill -9, idle and mid-transaction --------------------------------------------------


HOLD = textwrap.dedent(f"""{CHILD_PREAMBLE}
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

#: The genuinely mid-transaction holder. `HOLD` signals ready *after* `begin_execution` has
#: committed, so its child is killed while idle between transactions -- deterministic, and a
#: perfectly good test of "the holder went away", but not of what "mid-transaction" adds: that
#: Postgres discards a half-written transaction when its backend dies. This one holds an open
#: transaction with an uncommitted `UPDATE` in it and signals ready from inside.
HOLD_MID = textwrap.dedent(f"""{CHILD_PREAMBLE}
    import psycopg
    from datetime import UTC, datetime
    frozen = datetime.fromisoformat(job["now"])
    store = PostgresStateStore(job["url"], schema=job["schema"], clock=lambda: frozen)
    store.reserve_effect(job["key"], job["action_id"], timedelta(minutes=5))

    raw = psycopg.connect(job["url"], autocommit=False)
    with raw.cursor() as cursor:
        cursor.execute('SET LOCAL search_path TO "%s"' % job["schema"])
        cursor.execute(
            "UPDATE effects SET state = 'executing' WHERE effect_key = %s AND state = 'reserved'",
            (job["key"],),
        )
        assert cursor.rowcount == 1, "the child did not write the row it is about to abandon"
    # Written, not committed. The transaction and its row lock are open right now.
    os.close(os.open(job["ready"], os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    while True:
        time.sleep(0.05)
""")


def _hold(script_source: str, schema: str, key: str, action_id: str):
    """Start a holder, wait for its marker, `SIGKILL` it. Returns nothing; it is dead."""
    with tempfile.TemporaryDirectory() as home:
        script = Path(home) / "hold.py"
        script.write_text(script_source, encoding="utf-8")
        ready = Path(home) / "ready"
        child = subprocess.Popen(
            [sys.executable, str(script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert child.stdin is not None
            child.stdin.write(
                json.dumps(
                    {
                        "url": URL,
                        "schema": schema,
                        "src": REPO_SRC,
                        "key": key,
                        "action_id": action_id,
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
                    raise AssertionError(f"the holder exited early: {child.stderr.read()[-600:]}")
                time.sleep(0.05)
            assert ready.exists(), "the holder never took the reservation within its bound"

            os.kill(child.pid, signal.SIGKILL)
            child.wait(timeout=30)
            assert child.returncode != 0, "the holder exited cleanly; it was supposed to be killed"
        finally:
            # An assertion above leaves a `while True` child running for the rest of the session.
            if child.poll() is None:
                child.kill()
                child.wait(timeout=30)


@postgres
def test_T158_a_killed_holder_ages_out_by_its_lease_and_nothing_else(schema):
    """SPEC-v0.6 §5.1, §5.3, under a real `SIGKILL`.

    A process is killed while holding a reservation it has begun executing. **Nothing reclaims
    the key**: *"the holder is dead" is not knowable from the store*. The record stays `EXECUTING`
    until its lease lapses, and the next contender then finds `AMBIGUOUS` -- a refusal, not a
    reclaim, and one only a human or a reconcile hook moves on.

    Named for what it does. The kill lands while the child is **idle between transactions**,
    which is deterministic and is the case §5.1 is about. The mid-transaction kill §8's T158 also
    names is the test below; the two are different claims and were one test before.
    """
    _hold(HOLD, schema, "refund:t158", "act_doomed")

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


@postgres
def test_T158_a_kill_mid_transaction_discards_the_half_written_transaction(schema):
    """§8's T158, the half the idle kill above cannot show.

    The child is killed with an **open transaction** holding an uncommitted `UPDATE` and the row
    lock that goes with it. Postgres discards it: the record is still `reserved`, the write is
    gone, and the lock is released -- so the store can read and plan against the row rather than
    blocking on a lock nobody will ever release. That is the whole of what "mid-transaction"
    adds, and nothing checked it.
    """
    _hold(HOLD_MID, schema, "refund:t158mid", "act_half")

    store = store_on(URL, schema)
    record = store.get_effect("refund:t158mid")
    assert record is not None
    assert record.state is EffectState.RESERVED, (
        f"the record is {record.state}; the killed backend's uncommitted UPDATE survived, which "
        "would mean a half-written transaction became state"
    )

    # The row lock died with the backend: a contender gets an answer rather than blocking.
    with pytest.raises(DuplicateEffect):
        store.reserve_effect("refund:t158mid", "act_next", timedelta(minutes=5))
    store.close()


# --- T158b: partition, and every backend restarted --------------------------------------------


@postgres
def test_T158b_a_partition_stalls_the_call_and_the_restore_completes_it(proxy, schema):
    """§4.5's partition injection, **and its restore**.

    A partition is a stall, not a reset -- which is why `FAILED` must not be inferred from one --
    and this asserts the stall as an observable: with the partition on, the call has not returned;
    with it lifted, the same call completes and the record is `EXECUTING`. Deleting the injection
    makes the first assertion fail, which is the property the first version lacked: it captured
    the outcome and asserted nothing about it, so removing the partition entirely left it green
    (in 0.28 s instead of 40 s, which is the other half of the story).

    The 40 seconds are gone with the injection's deadline. The proxy **holds** traffic rather than
    discarding it, so the restore genuinely delivers the query the server never saw; the first
    version discarded, and then tore the sockets down from under its own relay threads after
    `TIMEOUT * 2` -- so the client got "server closed the connection unexpectedly", the docstring
    said "a stall, not a reset", and the bound the test called a network property was a constant
    in the proxy.
    """
    store = store_on(proxy.url(URL), schema)
    store.reserve_effect("refund:part", "act_p", timedelta(minutes=5))

    proxy.partition = True
    done = threading.Event()
    outcome: list[str] = []

    def call() -> None:
        try:
            store.begin_execution("refund:part", "act_p")
            outcome.append("returned")
        except BaseException as stalled:
            outcome.append(type(stalled).__name__)
        finally:
            done.set()

    worker = threading.Thread(target=call, daemon=True)
    worker.start()

    assert not done.wait(2.0), (
        f"the partitioned call returned {outcome} while the partition was up. A partition that "
        "nothing observes is an injection that is not happening"
    )

    proxy.partition = False
    assert done.wait(30.0), "the restored call never completed within its bound"
    worker.join(timeout=5.0)
    assert not worker.is_alive()
    assert outcome == ["returned"], (
        f"the restored call ended as {outcome}; the proxy holds traffic across a partition, so "
        "the query the server never received arrives when it is lifted"
    )

    checker = store_on(URL, schema)
    record = checker.get_effect("refund:part")
    assert record is not None
    assert record.state is EffectState.EXECUTING
    checker.close()
    store.close()


@postgres
def test_T158b_a_partition_that_never_lifts_never_reports_failed(proxy, schema):
    """The other end of the same injection: the partition outlives the client.

    Nothing about a stall proves non-execution, so whatever the caller sees when its connection
    is finally torn down, the record must not be `FAILED`.
    """
    store = store_on(proxy.url(URL), schema)
    store.reserve_effect("refund:part2", "act_p2", timedelta(minutes=5))

    proxy.partition = True
    done = threading.Event()
    outcome: list[str] = []

    def call() -> None:
        try:
            store.begin_execution("refund:part2", "act_p2")
            outcome.append("returned")
        except BaseException as stalled:
            outcome.append(type(stalled).__name__)
        finally:
            done.set()

    worker = threading.Thread(target=call, daemon=True)
    worker.start()
    assert not done.wait(2.0), f"the partitioned call returned {outcome}; nothing was injected"

    proxy.stop()  # the partition never lifts; the connection goes away underneath the client
    assert done.wait(30.0), "the call never gave up within its bound"
    worker.join(timeout=5.0)
    assert not worker.is_alive()
    assert outcome != ["returned"], "the connection was taken away and the call returned anyway"
    assert outcome[0] != "NotExecuted", (
        "a partition reached the caller as NotExecuted, which asserts the write did not happen"
    )

    checker = store_on(URL, schema)
    record = checker.get_effect("refund:part2")
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

    Every backend **this test opened** is terminated while a commit is in flight. From a client
    that is indistinguishable from a restart, and it is what this environment can do without
    taking the server away from everything else on the machine -- which the PR says rather than
    implying a full restart was run.

    Two things the first version got wrong, both of which made it a test of nothing. It started
    its killer and then immediately committed on an already-open connection, so the commit landed
    before the killer's own `connect` returned and the `finally` ended the thread before its first
    iteration: **zero** backends were ever terminated, and replacing the killer's body with
    `return` left the test green. And it terminated every backend on the database, including
    other tests' and any `psql` the maintainer had open. It now waits for the first successful
    termination, asserts that at least one commit attempt was actually broken, and scopes the kill
    to its own `application_name`.
    """
    import psycopg

    tag = f"xhost_restart_{uuid.uuid4().hex[:8]}"
    separator = "&" if "?" in URL else "?"
    store = store_on(f"{URL}{separator}application_name={tag}", schema)
    store.reserve_effect("refund:restart", "act_r", timedelta(minutes=5))
    store.begin_execution("refund:restart", "act_r")

    stop = threading.Event()
    killing = threading.Event()
    terminated = [0]

    def terminate_ours() -> None:
        admin = psycopg.connect(URL, autocommit=True)
        try:
            deadline = time.monotonic() + 20
            while not stop.is_set() and time.monotonic() < deadline:
                rows = admin.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND application_name = %s "
                    "AND pid <> pg_backend_pid()",
                    (tag,),
                ).fetchall()
                terminated[0] += sum(1 for row in rows if row[0])
                if terminated[0]:
                    killing.set()
                time.sleep(0.02)
        finally:
            admin.close()

    killer = threading.Thread(target=terminate_ours, daemon=True)
    killer.start()
    attempts = 0
    broken = 0
    try:
        assert killing.wait(20.0), (
            f"no backend tagged {tag} was ever terminated, so nothing was injected. The first "
            "version asserted on the record without checking this and terminated nothing at all"
        )
        for _ in range(20):
            attempts += 1
            try:
                store.commit_effect("refund:restart", "act_r", {"ok": True})
                break
            except Exception:
                broken += 1
                time.sleep(0.05)
    finally:
        stop.set()
        killer.join(timeout=30)

    assert terminated[0] >= 1, "the killer reported no terminations"
    assert broken >= 1, (
        f"{attempts} commit attempts and none was broken by a terminated backend; the window "
        "this test names never opened"
    )

    checker = store_on(URL, schema)
    record = checker.get_effect("refund:restart")
    assert record is not None
    assert record.state is not EffectState.FAILED, (
        "a terminated backend produced a FAILED record; only the executor opts into FAILED"
    )
    checker.close()
    store.close()
