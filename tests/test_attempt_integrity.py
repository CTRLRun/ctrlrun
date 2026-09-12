"""Attempt numbers never repeat, on Postgres. Item 3a; SPEC-v0.7 §5.6, §8.3a (T246, T246b).

Two properties, and at 0.6.1 neither held on Postgres: **no two reservations of one key carry the
same attempt number**, and **the number a reservation method returns is the number it wrote**.
v0.7 makes the attempt number load-bearing twice, in the idempotency token and in the ceiling, so a
reused number gives two dispatches one token and lets a ceiling of N admit N+1.

**Every window here is opened on purpose** (mutation pattern 4). Two processes renewing
concurrently open none of them: each needs one store stalled at a precise point inside a
reservation method while a second process renews, runs and fails. The proxy the tests own does the
stalling, and it holds one statement on one connection (`failure_injection.Proxy.arm`) or
swallows one `COMMIT` and acts before the re-read (`drop_before_commit`, `on_drop`).

**What was run, stated first, as `test_cross_host.py` does.** The store under test and the store
that interleaves are **separate OS processes**, each with its own connection, against one local
Postgres; the parent owns the proxy and only orchestrates. They are not two hosts. Every wait is
bounded, so a store that never reaches the window fails red rather than hanging.

**SQLite has no row here, and that is not a gap.** Its renewal reads and writes inside one
`BEGIN IMMEDIATE`, so the stale case is unreachable and the same `AND attempt = ?` is an
equivalent mutant there; it has no lost-commit path at all (§5.6).
"""

from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ctrlrun.action import Action, Principal
from ctrlrun.effect import EffectState
from ctrlrun.errors import CTRLRunError
from failure_injection import Proxy, statement_of, upstream_of

#: Every window in this file is opened by the proxy -- the armed hold -- and measured against
#: what a store did inside it. Sharing a machine with seven other pytest workers turns
#: those into races the test loses: T155b reported "the window never opened", which was
#: true. `scripts/check.sh` runs these on their own.
pytestmark = pytest.mark.serial

URL = os.environ.get("CTRLRUN_TEST_POSTGRES")

postgres = pytest.mark.skipif(
    not URL, reason="CTRLRUN_TEST_POSTGRES is not set; no server to run against"
)

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
LEASE = timedelta(minutes=5)

#: Every bound in this file. Generous, because a bound that fires is a red test and not a hang.
BOUND = 60.0

#: The tree under test. The children import it and assert they did, for the reason
#: `test_cross_host.REPO_SRC` gives: a hardcoded path grades a different checkout.
REPO_SRC = str(Path(__file__).resolve().parents[1] / "src")

#: Both reservation methods, because §5.5 says *every* reservation method returns the attempt
#: number it wrote. They share one code path in the store today; the parametrization is what
#: keeps that true if they ever stop sharing it.
METHODS = ["reserve_effect", "consume_approval_and_reserve"]

#: One child: open a store, optionally wait to be told to go, then run a script of store calls,
#: each at its own frozen instant. It stops at the first refusal and reports every step's outcome
#: and every §4.3.4 branch its store took, as JSON on stdout.
CHILD = textwrap.dedent("""
    import json, logging, os, sys, time
    from datetime import datetime, timedelta
    job = json.loads(sys.stdin.read())
    sys.path.insert(0, job["src"])
    import ctrlrun
    _WHERE = os.path.realpath(ctrlrun.__file__)
    assert _WHERE.startswith(os.path.realpath(job["src"])), (
        "the child imported ctrlrun from %s, not the tree under test at %s"
        % (_WHERE, job["src"]))
    from ctrlrun.effect import EffectState
    from ctrlrun.postgres import PostgresStateStore

    branches = []

    class Branches(logging.Handler):
        def emit(self, record):
            branch = getattr(record, "branch", None)
            if branch is not None:
                branches.append(branch)

    log = logging.getLogger("ctrlrun.postgres")
    log.addHandler(Branches())
    log.setLevel(logging.WARNING)

    OPS = ("reserve", "begin", "fail", "commit", "ambiguous", "resolve", "grant")
    for step in job["steps"]:
        if step["op"] not in OPS:
            raise SystemExit("unknown step %r" % step["op"])
    now = [datetime.fromisoformat(job["steps"][0]["now"])]
    store = PostgresStateStore(job["url"], schema=job["schema"], clock=lambda: now[0])
    results = []
    out = {"ctrlrun": _WHERE, "results": results, "branches": branches}
    lease = timedelta(minutes=5)
    try:
        if job["ready"]:
            # Readiness through the filesystem, as test_cross_host's holders do. The store is
            # open, so its migration COMMIT is behind us and the proxy can be armed.
            os.close(os.open(job["ready"], os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            deadline = time.monotonic() + job["bound"]
            while not os.path.exists(job["go"]):
                if time.monotonic() > deadline:
                    raise SystemExit("the child was never told to go")
                time.sleep(0.01)
        for step in job["steps"]:
            now[0] = datetime.fromisoformat(step["now"])
            op, key, actor = step["op"], step["key"], step["action_id"]
            done = {"op": op, "attempt": None, "error": None, "state": None, "message": None}
            results.append(done)
            try:
                if op == "reserve":
                    if step.get("approval_id"):
                        _, reservation = store.consume_approval_and_reserve(
                            step["approval_id"], step["action_hash"], key, actor, lease)
                    else:
                        reservation = store.reserve_effect(key, actor, lease)
                    done["attempt"] = reservation.attempt
                elif op == "begin":
                    store.begin_execution(key, actor)
                elif op == "fail":
                    store.fail_effect(key, actor, "the remote refused before acting")
                elif op == "commit":
                    store.commit_effect(key, actor, {"refund": "re_1"})
                elif op == "ambiguous":
                    store.mark_ambiguous(key, actor, "the outcome was lost")
                elif op == "grant":
                    # SPEC-v0.8 §4.3, T313. The principal is the step's, because the whole
                    # question is whether two *distinct* ones both survive the window.
                    from ctrlrun.action import Principal
                    from ctrlrun.approval import _granting_principal

                    who = Principal(agent=step["agent"], user=step["user"])
                    with _granting_principal(who, entitled=step.get("entitled") or ()):
                        approval = store.grant_approval(step["approval_id"], step["who"])
                    done["state"] = "granted" if approval is not None else "pending"
                    record = store.get_approval(step["approval_id"])
                    done["attempt"] = None if record is None else len(record.approvers)
                else:
                    resolved = store.resolve_effect(key, EffectState(step["to"]), step["resolver"])
                    done["attempt"] = resolved.attempt
            except Exception as refused:
                done["error"] = type(refused).__name__
                done["state"] = getattr(refused, "state", None)
                done["message"] = str(refused)
                break
    finally:
        store.close()
    sys.stdout.write(json.dumps(out))
""")


def step(op: str, key: str, action_id: str, now: datetime, **extra: Any) -> dict[str, Any]:
    """One store call for a child to make, at its own frozen instant."""
    return {"op": op, "key": key, "action_id": action_id, "now": now.isoformat(), **extra}


def reserving(
    key: str,
    action_id: str,
    now: datetime,
    *,
    then_fail: bool = False,
    approval: tuple[str, str] | None = None,
) -> list[dict[str, Any]]:
    """A reservation, through the approval when there is one, and optionally a run that fails."""
    extra = {"approval_id": approval[0], "action_hash": approval[1]} if approval else {}
    script = [step("reserve", key, action_id, now, **extra)]
    if then_fail:
        script += [step("begin", key, action_id, now), step("fail", key, action_id, now)]
    return script


class Child:
    """One store in its own OS process. Started at once; `result()` waits for it, bounded."""

    def __init__(
        self,
        home: Path,
        name: str,
        *,
        url: str,
        schema: str,
        gated: bool,
        key: str = "",
        action_id: str = "",
        now: datetime = T0,
        then_fail: bool = False,
        approval: tuple[str, str] | None = None,
        steps: list[dict[str, Any]] | None = None,
    ) -> None:
        if steps is None:
            steps = reserving(key, action_id, now, then_fail=then_fail, approval=approval)
        script = home / "child.py"
        if not script.exists():
            script.write_text(CHILD, encoding="utf-8")
        self.name = name
        self.ready = home / f"{name}.ready"
        self.go_marker = home / f"{name}.go"
        self._process = subprocess.Popen(
            [sys.executable, str(script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert self._process.stdin is not None
        self._process.stdin.write(
            json.dumps(
                {
                    "src": REPO_SRC,
                    "url": url,
                    "schema": schema,
                    "steps": steps,
                    "ready": str(self.ready) if gated else None,
                    "go": str(self.go_marker),
                    "bound": BOUND,
                }
            )
        )
        self._process.stdin.close()

    def running(self) -> bool:
        return self._process.poll() is None

    def wait_ready(self) -> None:
        deadline = time.monotonic() + BOUND
        while not self.ready.exists():
            if not self.running():
                raise AssertionError(f"{self.name} exited before it was ready: {self._stderr()}")
            if time.monotonic() > deadline:
                raise AssertionError(f"{self.name} did not open its store within {BOUND}s")
            time.sleep(0.01)

    def go(self) -> None:
        os.close(os.open(self.go_marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY))

    def result(self) -> dict[str, Any]:
        try:
            self._process.wait(timeout=BOUND)
        except subprocess.TimeoutExpired:
            self.kill()
            raise AssertionError(f"{self.name} did not finish within {BOUND}s") from None
        assert self._process.stdout is not None
        raw = self._process.stdout.read()
        if not raw.strip():
            raise AssertionError(f"{self.name} reported nothing: {self._stderr()}")
        out: dict[str, Any] = json.loads(raw)
        assert out["ctrlrun"].startswith(REPO_SRC), (
            f"{self.name} imported ctrlrun from {out['ctrlrun']!r}, not the tree under test"
        )
        # The first step's number, and the first refusal, which is where the script stopped.
        results = out["results"]
        refused = next((done for done in results if done["error"]), None)
        out["attempt"] = results[0]["attempt"] if results else None
        for field in ("error", "state", "message"):
            out[field] = refused[field] if refused else None
        return out

    def kill(self) -> None:
        if self.running():
            self._process.kill()
            self._process.wait(timeout=BOUND)

    def _stderr(self) -> str:
        assert self._process.stderr is not None
        return self._process.stderr.read()[-800:]


@pytest.fixture
def schema():
    from ctrlrun.postgres import PostgresStateStore

    name = f"attempts_{uuid.uuid4().hex[:12]}"
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
        made.release()  # a test that failed while a statement was held must not strand it
        made.stop()


@pytest.fixture
def home():
    with tempfile.TemporaryDirectory() as made:
        yield Path(made)


def direct(schema: str, now: datetime = T0):
    """A store on the server itself, never through the proxy: setup and the final read."""
    from ctrlrun.postgres import PostgresStateStore

    return PostgresStateStore(URL, schema=schema, clock=lambda: now)


def failed_once(schema: str, key: str) -> None:
    """The pre-state of every renewal here: attempt 1 ran and proved nothing happened."""
    store = direct(schema)
    try:
        store.reserve_effect(key, "act_first", LEASE)
        store.begin_execution(key, "act_first")
        store.fail_effect(key, "act_first", "the remote refused before acting")
    finally:
        store.close()


def granted_for(schema: str, method: str, key: str) -> tuple[str, str] | None:
    """A granted approval for the held store to spend, when the method under test takes one."""
    if method != "consume_approval_and_reserve":
        return None
    from ctrlrun.action import Action, Principal
    from ctrlrun.approval import ApprovalRequest

    action = Action(
        name="stripe.refund",
        arguments={"payment_id": key, "amount": 2000},
        principal=Principal(agent="a"),
    )
    approval_id = f"apr_{uuid.uuid4().hex[:12]}"
    store = direct(schema)
    try:
        store.put_approval_request(
            ApprovalRequest(
                request_id=approval_id,
                action_hash=action.action_hash,
                action=action,
                created_at=T0,
                expires_at=T0 + timedelta(hours=1),
            )
        )
        store.grant_approval(approval_id, "cli:local")
    finally:
        store.close()
    return approval_id, action.action_hash


def approval_status(schema: str, approval: tuple[str, str] | None) -> str | None:
    if approval is None:
        return None
    store = direct(schema)
    try:
        record = store.get_approval(approval[0])
        assert record is not None
        return str(record.status)
    finally:
        store.close()


def stored(schema: str, key: str):
    store = direct(schema)
    try:
        return store.get_effect(key)
    finally:
        store.close()


def table_statement(verb: bytes, schema: str, table_name: str):
    """A `Proxy.arm` predicate: a statement on one of this schema's tables, beginning `verb`."""
    table = f'"{schema}".{table_name}'.encode()

    def matches(kind: bytes, body: bytes) -> bool:
        text = statement_of(kind, body)
        return text is not None and text.lstrip().upper().startswith(verb) and table in text

    return matches


def effects_statement(verb: bytes, schema: str):
    return table_statement(verb, schema, "effects")


def approvals_statement(verb: bytes, schema: str):
    return table_statement(verb, schema, "approvals")


# --- the injector's own control -------------------------------------------------------------


def test_the_hold_trigger_reads_the_parse_and_not_a_bind_parameter():
    """The hold sees SQL text only where the protocol puts it.

    No server needed, and the reason is `test_cross_host`'s first control: a trigger that grepped
    the byte stream fired on a Bind parameter. An effect key containing `UPDATE` travels in a Bind
    and must not look like a statement; the statement itself travels in a Parse.
    """

    def typed(kind: bytes, body: bytes) -> tuple[bytes, bytes]:
        framed = kind + struct.pack("!i", 4 + len(body)) + body
        return framed[0:1], framed[5:]

    parse = typed(b"P", b'\x00UPDATE "s".effects SET state=$1 WHERE effect_key=$2\x00\x00\x00')
    bind = typed(b"B", b"\x00\x00\x00\x00\x00\x01\x00\x00\x00\x0frefund:UPDATE-me")
    query = typed(b"Q", b"SELECT 1\x00")

    assert b"UPDATE" in bind[1], "the control is void: this Bind does not contain the word"
    assert statement_of(*parse) == b'UPDATE "s".effects SET state=$1 WHERE effect_key=$2'
    assert statement_of(*bind) is None
    assert statement_of(*query) == b"SELECT 1"

    matches = effects_statement(b"UPDATE", "s")
    assert matches(*parse)
    assert not matches(*bind)
    assert not effects_statement(b"UPDATE", "other")(*parse), "another schema's table matched"


def test_the_hold_can_be_armed_twice_and_refuses_to_arm_over_a_pending_hold():
    """`Proxy.arm` is reusable, which items 3 and 4 need, and it says no rather than lie.

    No server: a loopback listener stands in for Postgres and records what it receives, over real
    sockets, so "held" means the bytes did not arrive and "released" means they did. The first
    version was one-shot: its `holding` and release events were never cleared, so a second hold
    reported itself held at once and forwarded its statement straight through, with `holds`
    counting a window that never opened.
    """
    upstream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    upstream.bind(("127.0.0.1", 0))
    upstream.listen(1)
    received = bytearray()
    lock = threading.Lock()
    stop = threading.Event()

    def serve() -> None:
        connection, _ = upstream.accept()
        connection.settimeout(0.1)
        with connection:
            while not stop.is_set():
                try:
                    data = connection.recv(65536)
                except TimeoutError:
                    continue
                if not data:
                    return
                with lock:
                    received.extend(data)

    def arrived(marker: bytes, within: float) -> bool:
        deadline = time.monotonic() + within
        while time.monotonic() < deadline:
            with lock:
                if marker in received:
                    return True
            time.sleep(0.01)
        return False

    def framed(kind: bytes, body: bytes) -> bytes:
        return kind + struct.pack("!i", 4 + len(body)) + body

    def statement(text: bytes):
        return lambda kind, body: statement_of(kind, body) == text

    server = threading.Thread(target=serve, daemon=True)
    server.start()
    proxy = Proxy("127.0.0.1", upstream.getsockname()[1]).start()
    client = socket.create_connection(("127.0.0.1", proxy.port), timeout=BOUND)
    try:
        startup = struct.pack("!i", 196608) + b"user\x00bob\x00\x00"
        client.sendall(struct.pack("!i", 4 + len(startup)) + startup)
        assert arrived(b"user\x00bob", BOUND), "the relay is not forwarding at all"

        proxy.arm(statement(b"UPDATE one"))
        with pytest.raises(RuntimeError):
            proxy.arm(statement(b"UPDATE other"))  # armed and not yet fired
        client.sendall(framed(b"P", b"\x00UPDATE one\x00\x00\x00"))
        assert proxy.holding.wait(BOUND), "the first hold never fired"
        assert not arrived(b"UPDATE one", 0.5), "the first held statement reached the server"
        with pytest.raises(RuntimeError):
            proxy.arm(statement(b"UPDATE other"))  # fired and not yet released
        proxy.release()
        assert arrived(b"UPDATE one", BOUND), "the release did not forward the held statement"

        proxy.arm(statement(b"UPDATE two"))
        assert not proxy.holding.is_set(), "the second hold reported itself held before firing"
        proxy.release()  # nothing is held yet, so this must not pre-release the armed hold
        client.sendall(framed(b"P", b"\x00UPDATE two\x00\x00\x00"))
        assert proxy.holding.wait(BOUND), "the second hold never fired"
        assert not arrived(b"UPDATE two", 0.5), (
            "the second hold forwarded its statement at once: it inherited the first hold's "
            "release, so a test using it would open no window and pass"
        )
        assert proxy.holds == 2
        proxy.release()
        assert arrived(b"UPDATE two", BOUND)
    finally:
        stop.set()
        proxy.release()
        client.close()
        proxy.stop()
        upstream.close()


# --- T246: a stale renewal never lands ------------------------------------------------------


def held_at(window: str, schema: str):
    """Where T246 stalls the renewal, between the plan's `SELECT` and the `UPDATE`.

    `update` holds the `UPDATE` itself, which is §8.3a's window. `second-read` holds the store's
    **second** read of the record, which `_reserve_locked` makes (`previous`, for `created_at`)
    after `_plan`'s and before the `UPDATE`. The fix takes the planned-from attempt from the plan;
    a fix that took it from that second read would pass the `update` window, because both reads
    precede it and see the same record, and fail this one, where the second read sees the rival's.
    """
    if window == "update":
        return effects_statement(b"UPDATE", schema)
    select = effects_statement(b"SELECT", schema)
    seen = [0]

    def second_read(kind: bytes, body: bytes) -> bool:
        if not select(kind, body):
            return False
        seen[0] += 1
        return seen[0] == 2

    return second_read


@postgres
@pytest.mark.parametrize("window", ["update", "second-read"])
@pytest.mark.parametrize("method", METHODS)
def test_T246_a_stale_renewal_on_postgres_never_lands(proxy, schema, home, method, window):
    """SPEC-v0.7 §5.6 and §8.3a T246: the renewal `UPDATE` is conditioned on the planned-from
    attempt, so a renewal planned against *k* cannot land after another process moved *k*.

    The window: the held process has read the record `FAILED` at 1 and planned a renewal to 2;
    the proxy holds it before its `UPDATE` reaches the server (`held_at` says at which of two
    statements). Meanwhile a second process renews to 2, runs and fails, leaving the record
    `FAILED` again. 0.6.1's `WHERE effect_key AND state = 'failed'` then matches, and attempt 2 is
    written a second time: two dispatches, one number.
    """
    key = f"refund:stale-{method}-{window}"
    failed_once(schema, key)
    approval = granted_for(schema, method, key)

    held = Child(
        home,
        "held",
        url=proxy.url(URL),
        schema=schema,
        key=key,
        action_id="act_held",
        now=T0 + timedelta(seconds=1),
        gated=True,
        approval=approval,
    )
    try:
        held.wait_ready()
        proxy.reset_counters()
        proxy.arm(held_at(window, schema))
        held.go()

        assert proxy.holding.wait(BOUND), (
            f"the renewal never reached the {window} statement through the proxy, so the window "
            "this test is about was never opened"
        )
        planned_from = stored(schema, key)
        assert planned_from is not None
        assert (planned_from.state, planned_from.attempt) == (EffectState.FAILED, 1), (
            f"the held process planned against {planned_from.state} at {planned_from.attempt}; "
            "the window needs it to have read FAILED at 1"
        )

        rival = Child(
            home,
            "rival",
            url=URL,
            schema=schema,
            key=key,
            action_id="act_rival",
            now=T0 + timedelta(seconds=2),
            gated=False,
            then_fail=True,
        ).result()
        assert rival["error"] is None, f"the rival did not renew, run and fail: {rival}"
        assert rival["attempt"] == 2
        assert held.running(), "the held process finished while its UPDATE was being held"

        proxy.release()
        outcome = held.result()
    finally:
        proxy.release()
        held.kill()

    assert proxy.holds == 1
    handed = [r["attempt"] for r in (rival, outcome) if r["attempt"] is not None]
    assert handed == [2], (
        f"reservations were handed attempts {handed}: the stale renewal landed, so attempt 2 was "
        f"written twice and two dispatches share one number. Held process: {outcome}"
    )
    assert outcome["error"] == "DuplicateEffect", outcome
    assert outcome["state"] == "in_progress", (
        "the stale renewal is refused exactly as a lost renewal race is refused (§5.6)"
    )

    record = stored(schema, key)
    assert record is not None
    found = (record.state, record.attempt, record.action_id)
    assert found == (EffectState.FAILED, 2, "act_rival"), (
        f"the record is {record.state} at {record.attempt} under {record.action_id}; the rival's "
        "failed attempt 2 must be what the store says"
    )
    if approval is not None:
        assert approval_status(schema, approval) == "granted", (
            "the refused reservation spent its approval; v0.1 §4.2 A4 rolls both back together"
        )


# --- T246b: a lost COMMIT returns the attempt it wrote --------------------------------------


@postgres
@pytest.mark.parametrize("method", METHODS)
def test_T246b_renewal_a_lost_commit_returns_the_attempt_the_re_issue_wrote(
    proxy, schema, home, method
):
    """§8.3a T246b, the renewal variant: Table A2 row 2 re-issues, and returns what it wrote.

    The window: the held process's renewal to 2 loses its `COMMIT` (the proxy swallows it, so the
    server rolls it back). Before the store re-reads, another process renews to 2 and fails. The
    re-read finds `FAILED` at 2 and re-issues, and the re-issue writes 3. 0.6.1 then returned the
    original plan's reservation, attempt 2, which the rival had already been handed.
    """
    key = f"refund:lost-renewal-{method}"
    failed_once(schema, key)
    approval = granted_for(schema, method, key)
    rivals: list[dict[str, Any]] = []

    def another_process_renews_and_fails() -> None:
        rivals.append(
            Child(
                home,
                "rival",
                url=URL,
                schema=schema,
                key=key,
                action_id="act_rival",
                now=T0 + timedelta(seconds=2),
                gated=False,
                then_fail=True,
            ).result()
        )

    held = Child(
        home,
        "held",
        url=proxy.url(URL),
        schema=schema,
        key=key,
        action_id="act_held",
        now=T0 + timedelta(seconds=1),
        gated=True,
        approval=approval,
    )
    try:
        held.wait_ready()
        proxy.reset_counters()
        proxy.on_drop = another_process_renews_and_fails
        proxy.drop_before_commit = 1
        held.go()
        outcome = held.result()
    finally:
        held.kill()

    assert proxy.commits_dropped == 1, "no COMMIT was swallowed; the window never opened"
    assert len(rivals) == 1, "the rival never ran between the lost COMMIT and the re-read"
    rival = rivals[0]
    assert rival["error"] is None and rival["attempt"] == 2, rival
    assert outcome["branches"] == ["a2.row2.reissue"], (
        f"the held store reported {outcome['branches']}; the window needs the re-read to find "
        "FAILED and re-issue"
    )
    assert outcome["error"] is None, outcome

    record = stored(schema, key)
    assert record is not None
    assert (record.state, record.action_id) == (EffectState.RESERVED, "act_held")
    assert record.attempt == 3, f"the re-issue renewed from 2, so it wrote 3, not {record.attempt}"
    assert outcome["attempt"] == record.attempt, (
        f"the reservation method returned attempt {outcome['attempt']} and the store holds "
        f"{record.attempt}. The rival was handed {rival['attempt']}: the lost COMMIT's re-issue "
        "was discarded and the original plan's number returned"
    )
    if approval is not None:
        assert approval_status(schema, approval) == "consumed"


@postgres
@pytest.mark.parametrize("method", METHODS)
def test_T246b_insert_a_lost_commit_returns_the_attempt_the_re_issue_wrote(
    proxy, schema, home, method
):
    """§8.3a T246b, the insert variant: Table A1 row 2 re-issues, and returns what it wrote.

    The window is later than the renewal's, and has to be. The held process's `INSERT` of attempt 1
    loses its `COMMIT`. Its re-read finds no record and takes `a1.row2.reinsert`. The proxy then
    holds the re-issue's **own planning `SELECT`** while another process inserts attempt 1 and
    fails, and releases it, so the re-issue plans a renewal and writes 2. 0.6.1 returned the
    original plan's attempt 1, the number the rival had just run under.

    Interleaving before the re-read instead would send the insert down `a1.row3.refuse` and never
    reach the re-issue, which is a test of a window nobody opened. So the hold is armed only when
    the `COMMIT` is swallowed, and it fires on the second `SELECT` of the effects table after
    that: the first is the re-read, on its own fresh connection; the second is the re-issue's plan.
    """
    key = f"refund:lost-insert-{method}"
    direct(schema).close()  # migrated, and no record: attempt 1 is an INSERT
    approval = granted_for(schema, method, key)
    select = effects_statement(b"SELECT", schema)
    seen = [0]

    def the_re_issues_planning_read(kind: bytes, body: bytes) -> bool:
        if not select(kind, body):
            return False
        seen[0] += 1
        return seen[0] == 2

    def arm_the_hold() -> None:
        proxy.arm(the_re_issues_planning_read)

    held = Child(
        home,
        "held",
        url=proxy.url(URL),
        schema=schema,
        key=key,
        action_id="act_held",
        now=T0 + timedelta(seconds=1),
        gated=True,
        approval=approval,
    )
    try:
        held.wait_ready()
        proxy.reset_counters()
        proxy.on_drop = arm_the_hold
        proxy.drop_before_commit = 1
        held.go()

        assert proxy.holding.wait(BOUND), (
            "the re-issue's planning SELECT never reached the proxy, so the re-issue was never "
            "entered or never planned"
        )
        assert stored(schema, key) is None, (
            "a record exists while the re-issue's planning read is held; the re-read cannot have "
            "found none, and this is not the a1.row2 window"
        )

        rival = Child(
            home,
            "rival",
            url=URL,
            schema=schema,
            key=key,
            action_id="act_rival",
            now=T0 + timedelta(seconds=2),
            gated=False,
            then_fail=True,
        ).result()
        assert rival["error"] is None and rival["attempt"] == 1, rival
        assert held.running(), "the held process finished while its planning read was held"

        proxy.release()
        outcome = held.result()
    finally:
        proxy.release()
        held.kill()

    assert proxy.commits_dropped == 1, "no COMMIT was swallowed; the window never opened"
    assert proxy.holds == 1 and seen[0] == 2
    assert outcome["branches"] == ["a1.row2.reinsert"], (
        f"the held store reported {outcome['branches']}; the window is the re-issue of a1.row2"
    )
    assert outcome["error"] is None, outcome

    record = stored(schema, key)
    assert record is not None
    assert (record.state, record.action_id) == (EffectState.RESERVED, "act_held")
    assert record.attempt == 2, (
        f"the re-issue planned over the rival's FAILED attempt 1, so it wrote 2, not "
        f"{record.attempt}"
    )
    assert outcome["attempt"] == record.attempt, (
        f"the reservation method returned attempt {outcome['attempt']} and the store holds "
        f"{record.attempt}. The rival ran under {rival['attempt']}: the lost COMMIT's re-issue "
        "was discarded and the original plan's number returned"
    )
    if approval is not None:
        assert approval_status(schema, approval) == "consumed"


@postgres
@pytest.mark.parametrize("method", METHODS)
def test_T246b_landed_a_renewal_re_read_that_finds_our_action_id_is_not_proof_it_landed(
    proxy, schema, home, method
):
    """§5.5's MUST on the third branch of `_resolve_lost_renewal`, which §8.3a does not name.

    Found while building item 3a (§12.3a). Table A2 row 1 on a renewal concluded *"the commit
    landed"* from `action_id` and `RESERVED` alone. v0.6 §4.3.3 says why that is not enough on the
    insert path, and the renewal path never applied it: `action_id` is caller-supplyable, and a
    caller that rebuilt the same `Action` after a restart renews under the same one. So the held
    process's renewal loses its `COMMIT`, a second process renews the key under the **same**
    `action_id` at a later instant, and the re-read finds a record that carries our id and is not
    our write. 0.6.1 returned attempt 2 to both: two dispatches holding one attempt, and the
    number returned was one the method never wrote.

    What separates them is §4.3.3's row identity: the rival's lease and `updated_at` are its own.
    """
    key = f"refund:lost-landed-{method}"
    failed_once(schema, key)
    approval = granted_for(schema, method, key)
    rivals: list[dict[str, Any]] = []

    def the_same_action_id_renews_elsewhere() -> None:
        rivals.append(
            Child(
                home,
                "rival",
                url=URL,
                schema=schema,
                key=key,
                action_id="act_shared",
                now=T0 + timedelta(seconds=2),
                gated=False,
            ).result()
        )

    held = Child(
        home,
        "held",
        url=proxy.url(URL),
        schema=schema,
        key=key,
        action_id="act_shared",
        now=T0 + timedelta(seconds=1),
        gated=True,
        approval=approval,
    )
    try:
        held.wait_ready()
        proxy.reset_counters()
        proxy.on_drop = the_same_action_id_renews_elsewhere
        proxy.drop_before_commit = 1
        held.go()
        outcome = held.result()
    finally:
        held.kill()

    assert proxy.commits_dropped == 1, "no COMMIT was swallowed; the window never opened"
    assert len(rivals) == 1, "the rival never ran between the lost COMMIT and the re-read"
    rival = rivals[0]
    assert rival["error"] is None and rival["attempt"] == 2, rival

    record = stored(schema, key)
    assert record is not None
    assert (record.state, record.attempt) == (EffectState.RESERVED, 2)
    assert record.lease_expires_at == T0 + timedelta(seconds=2) + LEASE, (
        "the record is not the rival's reservation; the window this test needs did not open"
    )

    assert outcome["attempt"] is None, (
        f"the held process was handed attempt {outcome['attempt']} for a renewal whose COMMIT "
        f"never landed, and the rival holds attempt {rival['attempt']} on the same key: one "
        f"attempt, two dispatches. It reported {outcome['branches']}"
    )
    assert outcome["error"] == "DuplicateEffect" and outcome["state"] == "in_progress", outcome
    assert outcome["branches"] == ["a2.row3.refuse"], (
        f"the held store reported {outcome['branches']}; a record that is not our own write is "
        "Table A2's third row"
    )
    if approval is not None:
        assert approval_status(schema, approval) == "granted", (
            "the held process was refused, and its own consumption was rolled back with the lost "
            "COMMIT; the approval must still be there for the attempt that runs"
        )


@postgres
@pytest.mark.parametrize("method", METHODS)
def test_T246b_insert_refuse_a_rival_that_failed_before_the_re_read_is_not_ours(
    proxy, schema, home, method
):
    """Table A1 row 3 when the rival's record is one the planner would renew over.

    A review found this row's last line untested. The held process's `INSERT` loses its `COMMIT`;
    before its re-read, another process inserts attempt 1, runs it and fails it. The re-read finds
    `FAILED` at 1 under the rival: not our write, and a record `plan_reservation` *grants* over,
    so the refusal is not the planner's but the line after it. Replacing that line with `return
    reservation` handed the held process attempt 1, the rival's number, on a record it never wrote,
    and no test failed.
    """
    key = f"refund:lost-insert-refuse-{method}"
    direct(schema).close()  # migrated, and no record: attempt 1 is an INSERT
    approval = granted_for(schema, method, key)
    rivals: list[dict[str, Any]] = []

    def another_process_inserts_and_fails() -> None:
        rivals.append(
            Child(
                home,
                "rival",
                url=URL,
                schema=schema,
                gated=False,
                steps=reserving(key, "act_rival", T0 + timedelta(seconds=2), then_fail=True),
            ).result()
        )

    held = Child(
        home,
        "held",
        url=proxy.url(URL),
        schema=schema,
        gated=True,
        steps=reserving(key, "act_held", T0 + timedelta(seconds=1), approval=approval),
    )
    try:
        held.wait_ready()
        proxy.reset_counters()
        proxy.on_drop = another_process_inserts_and_fails
        proxy.drop_before_commit = 1
        held.go()
        outcome = held.result()
    finally:
        held.kill()

    assert proxy.commits_dropped == 1, "no COMMIT was swallowed; the window never opened"
    assert len(rivals) == 1, "the rival never ran between the lost COMMIT and the re-read"
    assert rivals[0]["error"] is None and rivals[0]["attempt"] == 1, rivals[0]
    assert outcome["branches"] == ["a1.row3.refuse"], outcome["branches"]
    assert outcome["attempt"] is None, (
        f"the held process was handed attempt {outcome['attempt']}, the number the rival ran and "
        "failed under, for an INSERT that never landed"
    )
    assert outcome["error"] == "DuplicateEffect" and outcome["state"] == "in_progress", outcome

    record = stored(schema, key)
    assert record is not None
    found = (record.state, record.attempt, record.action_id)
    assert found == (EffectState.FAILED, 1, "act_rival"), found
    if approval is not None:
        assert approval_status(schema, approval) == "granted"


# --- T246c: no stale write moves an attempt number backwards ---------------------------------


def stale_write(proxy, home, schema: str, key: str, held_steps, rival_steps):
    """Hold `held_steps`' first `UPDATE` on the effects table while `rival_steps` run, then
    release it. Returns (what the held process reported, what the rival reported, the record the
    held process read). The shape of T246, for every write that is not a renewal."""
    held = Child(home, "held", url=proxy.url(URL), schema=schema, gated=True, steps=held_steps)
    try:
        held.wait_ready()
        proxy.reset_counters()
        proxy.arm(effects_statement(b"UPDATE", schema))
        held.go()
        assert proxy.holding.wait(BOUND), (
            "the held write's UPDATE never reached the proxy, so the window was never opened"
        )
        read = stored(schema, key)
        rival = Child(
            home, "rival", url=URL, schema=schema, gated=False, steps=rival_steps
        ).result()
        assert rival["error"] is None, f"the rival did not complete its steps: {rival}"
        assert held.running(), "the held process finished while its UPDATE was being held"
        proxy.release()
        outcome = held.result()
    finally:
        proxy.release()
        held.kill()
    assert proxy.holds == 1
    return outcome, rival, read


# --- T313: the M-of-N count, in the window between its read and its write ---------------------


@postgres
def test_T313_two_processes_granting_in_the_window_produce_two_approvers(proxy, schema, home):
    """SPEC-v0.8 §4.3. The window is between the read of `approvers` and the update of it.

    Two OS processes, two distinct verified principals, one request needing two yeses. The proxy
    holds ALICE's `UPDATE` after her connection has already read the row — an empty approver
    list — and BOB's grant lands in that window. When ALICE's update is released it must not
    write the list she read over the one BOB wrote.

    **It fails against a compare-and-set on `status`,** which is the shape the store had and the
    shape a reviewer found: `status` is still `pending` when ALICE's held update lands, so the
    condition holds, her write succeeds, and the row ends with one approver and a threshold of
    two that a third yes would have to fill. Nobody would ever know: two humans answered and the
    record says one did.

    `CONTRIBUTING.md`'s fourth mutation shape is the reason this test exists at all. Item 4's serial
    tests pass against a store with no compare-and-set whatever, because a read and a write with
    nothing in between is correct right up until something *is* in between.
    """
    from ctrlrun.approval import RequiredRole, _required_roles, build_request

    request_id = None
    setup = direct(schema)
    try:
        action = Action(
            name="payments.refund",
            arguments={"amount": 100, "payment_id": "EU-42"},
            principal=Principal(agent="ops-agent", user="ada"),
            environment="production",
        )
        with _required_roles((RequiredRole(control="c1", role="payments-owner"),), 2):
            request = build_request(action, timedelta(minutes=15), T0)
        setup.put_approval_request(request)
        request_id = request.request_id
    finally:
        setup.close()

    def grant(name: str, agent: str, at: datetime) -> dict:
        return step(
            "grant",
            "",
            "",
            at,
            approval_id=request_id,
            who=f"mcp-operator:{name}",
            agent=agent,
            user=f"{name}@example.com",
            entitled=["c1"],
        )

    held = Child(
        home,
        "alice",
        url=proxy.url(URL),
        schema=schema,
        gated=True,
        steps=[grant("alice", "human:alice", T0 + timedelta(seconds=1))],
    )
    try:
        held.wait_ready()
        proxy.reset_counters()
        proxy.arm(approvals_statement(b"UPDATE", schema))
        held.go()
        assert proxy.holding.wait(BOUND), (
            "alice's UPDATE never reached the proxy, so the window between the count's read and "
            "its write was never opened and this test has proved nothing"
        )
        rival = Child(
            home,
            "bob",
            url=URL,
            schema=schema,
            gated=False,
            steps=[grant("bob", "human:bob", T0 + timedelta(seconds=2))],
        ).result()
        assert rival["error"] is None, f"bob did not complete: {rival}"
        assert rival["results"][0]["attempt"] == 1, (
            f"bob's grant recorded {rival['results'][0]['attempt']} approvers, not 1: the window "
            "was opened somewhere other than where this test believes"
        )
        assert held.running(), "alice finished while her UPDATE was being held"
        proxy.release()
        outcome = held.result()
    finally:
        proxy.release()
        held.kill()

    assert proxy.holds == 1
    assert outcome["error"] is None, f"alice's grant was refused: {outcome}"

    after = direct(schema)
    try:
        record = after.get_approval(request_id)
    finally:
        after.close()
    assert record is not None
    agents = sorted(approver.agent for approver in record.approvers)
    assert agents == ["human:alice", "human:bob"], (
        f"the row ended with {agents}: one human's yes was written over the other's inside the "
        "window, and the request still needs a third answer that two people already gave"
    )
    assert str(record.status) == "granted", (
        f"two distinct principals answered and the request is {record.status}"
    )


REUSED = "act_reused"


@postgres
def test_T246c_a_stale_resolve_never_rewinds_a_newer_attempt(proxy, schema, home):
    """A review's reproduction, made a test: `resolve_effect` wrote the attempt it had read.

    Attempt 1 is `AMBIGUOUS` under an `action_id` the caller reuses, which is what retrying one
    `Action` object does. Human H1 reads it to resolve it `FAILED`, and the proxy holds H1's
    `UPDATE`. Meanwhile H2 resolves it `FAILED`, the owner retries the same `Action`, is handed
    attempt 2, dispatches and times out: `AMBIGUOUS` at 2 under the same id. 0.6.1's `WHERE
    effect_key AND action_id AND state` matched that, and H1's write put the record back to
    `FAILED` at **1**: the next renewal would hand out 2 again, and attempt 2's unknown outcome
    was settled by a human who had looked at attempt 1.
    """
    key = "refund:stale-resolve"
    setup = direct(schema)
    try:
        setup.reserve_effect(key, REUSED, LEASE)
        setup.begin_execution(key, REUSED)
        setup.mark_ambiguous(key, REUSED, "attempt 1 timed out")
    finally:
        setup.close()
    later = T0 + timedelta(seconds=3)
    outcome, rival, read = stale_write(
        proxy,
        home,
        schema,
        key,
        [step("resolve", key, REUSED, T0 + timedelta(seconds=1), to="failed", resolver="h1")],
        [
            step("resolve", key, REUSED, T0 + timedelta(seconds=2), to="failed", resolver="h2"),
            step("reserve", key, REUSED, later),
            step("begin", key, REUSED, later),
            step("ambiguous", key, REUSED, later),
        ],
    )
    assert read is not None and (read.state, read.attempt) == (EffectState.AMBIGUOUS, 1), read
    assert rival["results"][1]["attempt"] == 2

    record = stored(schema, key)
    assert record is not None
    assert (record.state, record.attempt) == (EffectState.AMBIGUOUS, 2), (
        f"H1's resolution, decided on attempt 1, left the record {record.state} at "
        f"{record.attempt}: attempt 2's unknown outcome was overwritten, and a renewal from here "
        "hands out an attempt number that has already been dispatched"
    )
    assert outcome["error"] == "AmbiguousEffect", (
        f"the stale resolve was refused as {outcome['error']}; the record it found is AMBIGUOUS at "
        "the newer attempt, which is nobody's live reservation, so in_progress would be false"
    )
    assert "moved from attempt 1" in outcome["message"], outcome["message"]


@postgres
def test_T246c_a_stale_ambiguate_never_rewinds_a_newer_attempt(proxy, schema, home):
    """§4.2.2's kept write, made by a contender that is not the owner, from a stale read.

    Attempt 1 is `EXECUTING` under a reused `action_id`. A contender whose clock is past the lease
    reads it, plans the `AMBIGUOUS` write, and the proxy holds that `UPDATE`. Meanwhile the owner
    fails attempt 1, retries the same `Action` and begins attempt 2, live. 0.6.1's condition matched
    `EXECUTING` under that id and wrote `AMBIGUOUS` at **1** over attempt 2 in flight.
    """
    key = "refund:stale-ambiguate"
    setup = direct(schema)
    try:
        setup.reserve_effect(key, REUSED, LEASE)
        setup.begin_execution(key, REUSED)
    finally:
        setup.close()
    owner = T0 + timedelta(minutes=8)  # attempt 2's lease runs to 12:13, past the contender's now
    outcome, rival, read = stale_write(
        proxy,
        home,
        schema,
        key,
        [step("reserve", key, "act_contender", T0 + timedelta(minutes=10))],
        [
            step("fail", key, REUSED, owner),
            step("reserve", key, REUSED, owner),
            step("begin", key, REUSED, owner),
        ],
    )
    assert read is not None and (read.state, read.attempt) == (EffectState.EXECUTING, 1), read
    assert rival["results"][1]["attempt"] == 2

    record = stored(schema, key)
    assert record is not None
    found = (record.state, record.attempt, record.action_id)
    assert found == (EffectState.EXECUTING, 2, REUSED), (
        f"the contender's stale AMBIGUOUS write left the record {found}: attempt 2, in flight, was "
        "rewound to attempt 1"
    )
    assert outcome["error"] == "DuplicateEffect" and outcome["state"] == "in_progress", outcome
    assert "moved from attempt 1" in outcome["message"], outcome["message"]


@postgres
@pytest.mark.parametrize(
    ("op", "pre", "lands", "tail"),
    [
        ("ambiguous", "begin", EffectState.AMBIGUOUS, "AmbiguousEffect"),
        ("commit", "begin", EffectState.COMMITTED, "DuplicateEffect"),
        ("fail", "begin", EffectState.EXECUTING, "DuplicateEffect"),
        ("begin", "", EffectState.RESERVED, "DuplicateEffect"),
    ],
)
def test_T246c_a_stale_transition_lands_its_outcome_or_is_refused(
    proxy, schema, home, op, pre, lands, tail
):
    """`_transition` under a reused `action_id`, held after its read while the record moves on.

    The record cannot be rewound: the `UPDATE` carries the attempt it read. What is left is what
    the stale write should do instead, and it is not the same answer for all four transitions.

    **An outcome is never dropped.** `commit_effect` and `mark_ambiguous` carry what the executor
    did, or the fact that nobody knows. Refusing them writes nothing, and then the record, which is
    what gates the next renewal, carries nothing: a review measured a renewal to attempt 3 with
    attempt 1's outcome recorded on no record at all, and in the `commit` row attempt 1 had
    committed at the remote, so attempt 3 would have been a second refund. When the re-read says
    the record is still ours and still in a state this transition may be made from, the transition
    is re-issued once against that re-read, so the outcome lands on the newer attempt, exactly
    where the same call a moment later would have landed it. Attributing it to the newer attempt is
    the residual (§12.3a); losing it is not a residual, it is a lost outcome.

    **A claim about a *running* attempt is refused.** `fail_effect` asserts that nothing happened,
    and re-issuing attempt 1's `FAILED` over attempt 2 in flight would permit a retry beside a
    dispatch that is still running. `begin_execution` claims a reservation this attempt no longer
    holds. Both are refused, and the record is left as the newer attempt wrote it.
    """
    key = f"refund:stale-transition-{op}"
    setup = direct(schema)
    try:
        setup.reserve_effect(key, REUSED, LEASE)
        if pre:
            setup.begin_execution(key, REUSED)
    finally:
        setup.close()
    read_state = EffectState.EXECUTING if pre else EffectState.RESERVED
    later = T0 + timedelta(seconds=2)
    rival_steps = (
        [step("fail", key, REUSED, later)]
        if pre
        else [
            step("begin", key, REUSED, later),
            step("fail", key, REUSED, later),
        ]
    )
    rival_steps += [step("reserve", key, REUSED, later)]
    if pre:
        rival_steps += [step("begin", key, REUSED, later)]
    outcome, rival, read = stale_write(
        proxy,
        home,
        schema,
        key,
        [step(op, key, REUSED, T0 + timedelta(seconds=1))],
        rival_steps,
    )
    assert read is not None and (read.state, read.attempt) == (read_state, 1), read
    assert rival["results"][-2 if pre else -1]["attempt"] == 2, rival

    record = stored(schema, key)
    assert record is not None
    found = (record.state, record.attempt, record.action_id)
    assert found == (lands, 2, REUSED), (
        f"attempt 1's stale {op} left the record {found}, expected {lands} at attempt 2: an "
        "outcome was dropped, or a newer attempt was overwritten"
    )
    if op in ("ambiguous", "commit"):
        assert outcome["error"] is None, (
            f"the {op} was refused ({outcome['error']}) and wrote nothing, so attempt 1's outcome "
            "is recorded on no effect record and the next renewal is not gated by it"
        )
    else:
        assert outcome["error"] == "DuplicateEffect" and outcome["state"] == "in_progress", outcome
        assert "moved from attempt 1" in outcome["message"], outcome["message"]

    # And whatever the record now says, it is not a record another attempt may take.
    after = direct(schema, T0 + timedelta(seconds=3))
    try:
        with pytest.raises(CTRLRunError) as refused:
            after.reserve_effect(key, "act_next", LEASE)
    finally:
        after.close()
    assert type(refused.value).__name__ == tail, (
        f"after a stale {op}, a further attempt was refused with {type(refused.value).__name__}, "
        f"expected {tail}"
    )


@postgres
def test_a_stale_expire_never_overwrites_a_consumption(proxy, schema, home):
    """The same shape on `approvals`, found by the round-2 review's survey of every write.

    `_expire` writes `status = expired` from a read that saw `granted` past `expires_at`, on
    `approval_id` alone. A consumption that commits in between is overwritten, and an approval that
    authorised a real effect reads `expired`: evidence corruption, and the one write on that table
    that was not a compare-and-set (`_consume_locked`, `grant_approval` and `deny_approval` all
    are). Here the expiring store's `UPDATE` is held while another process, whose clock is still
    inside the approval's life, consumes it and reserves the key.
    """
    key = "refund:stale-expire"
    approval = granted_for(schema, "consume_approval_and_reserve", key)
    assert approval is not None
    expired = T0 + timedelta(hours=2)  # past the approval's expires_at

    late = Child(
        home,
        "late",
        url=proxy.url(URL),
        schema=schema,
        gated=True,
        steps=reserving(key, "act_late", expired, approval=approval),
    )
    try:
        late.wait_ready()
        proxy.reset_counters()
        proxy.arm(approvals_statement(b"UPDATE", schema))
        late.go()
        assert proxy.holding.wait(BOUND), "the expiring UPDATE never reached the proxy"
        rival = Child(
            home,
            "rival",
            url=URL,
            schema=schema,
            gated=False,
            steps=reserving(key, "act_rival", T0 + timedelta(seconds=2), approval=approval),
        ).result()
        assert rival["error"] is None and rival["attempt"] == 1, rival
        proxy.release()
        outcome = late.result()
    finally:
        proxy.release()
        late.kill()

    assert outcome["error"] == "ApprovalMismatch", outcome
    assert approval_status(schema, approval) == "consumed", (
        "the stale expiry overwrote a consumption: the approval that authorised the rival's "
        "effect now reads expired, and the evidence says a human's yes was never spent"
    )
    record = stored(schema, key)
    assert record is not None and record.action_id == "act_rival"


# --- the outcome itself: never lost, whatever the store answers ------------------------------


@pytest.mark.parametrize("did", ["raised TimeoutError", "returned", "raised NotExecuted"])
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_an_unknown_outcome_the_store_refuses_to_record_is_never_lost(backend, did, tmp_path):
    """`v0.1 §5.5` through `Control`, when the outcome write itself is refused.

    The round-2 review drove this through the public API on SQLite, so the store is not the
    variable: the executor runs past its lease, a contender finds the lapsed record and makes it
    `AMBIGUOUS`, a human resolves it `FAILED` while the attempt is *still running*, and only then
    does the executor raise `TimeoutError`. `mark_ambiguous` is then refused, because `FAILED` is
    not a state this attempt may move from, and that refusal used to escape `Control`: no receipt,
    no `EXECUTION_AMBIGUOUS` event, and the caller handed an `InvalidArgument` about its own effect
    key instead of the exception its executor raised. An unknown outcome vanished, on both
    backends, and this has nothing to do with the attempt number.

    The store's answer may be a refusal; the evidence may not. The receipt and the event are
    written whatever the store says, they name the refusal, and the caller gets its own exception
    back. What the effect record says afterwards is the human's claim and not this attempt's, which
    is the residual §12.3a states and the reason the receipt has to carry the truth.

    **And the receipt says what the executor did**, which is the fact a human resolving the effect
    needs most. All three outcomes end here: a timeout, an executor that returned normally (so the
    remote very likely acted), and one that raised `NotExecuted` (so it very likely did not). A
    review found the last two producing byte-identical receipts, because the refusal was recorded
    and the outcome was not. A human reading *"DuplicateEffect: ..."* cannot tell a refund that
    probably landed from one that certainly did not.
    """
    from ctrlrun.action import Action, Principal
    from ctrlrun.control import Control
    from ctrlrun.errors import NotExecuted
    from ctrlrun.policy import Policy
    from ctrlrun.receipt import EventType, ReceiptResult
    from ctrlrun.state import SQLiteStateStore

    if backend == "postgres" and not URL:
        pytest.skip("CTRLRUN_TEST_POSTGRES is not set; no server to run against")
    now = [T0]
    name = f"unknown_{uuid.uuid4().hex[:10]}"
    if backend == "sqlite":
        path = str(tmp_path / "store.db")

        def open_store():
            return SQLiteStateStore(path, clock=lambda: now[0])
    else:
        from ctrlrun.postgres import PostgresStateStore

        PostgresStateStore.create_schema(URL, name)

        def open_store():
            return PostgresStateStore(URL, schema=name, clock=lambda: now[0])

    store = open_store()
    policy = Policy.from_yaml(
        "schema: ctrlrun.policy/v2\nactions:\n  stripe.refund:\n"
        '    effect: "refund:{id}"\n    rules:\n      - decision: allow\n'
    )
    control = Control(policy, store, clock=lambda: now[0], lease=LEASE)
    action = Action(
        name="stripe.refund", arguments={"id": "re_1"}, principal=Principal(agent="agent:a")
    )
    ran = []

    def executor():
        now[0] = T0 + timedelta(minutes=10)  # the lease lapsed while the remote was thinking
        other = open_store()
        try:
            with pytest.raises(CTRLRunError):
                other.reserve_effect("refund:re_1", "act_contender", LEASE)
            other.resolve_effect("refund:re_1", EffectState.FAILED, "cli:human")
        finally:
            other.close()
        ran.append("the executor ran, and nobody knows what the remote did")
        if did == "returned":
            return {"refund": "re_1"}
        if did == "raised NotExecuted":
            raise NotExecuted("the gateway refused the request before dispatching it")
        raise TimeoutError("the refund response never arrived")

    try:
        before = len(store.receipts())
        expected = TimeoutError if did == "raised TimeoutError" else CTRLRunError
        with pytest.raises(expected):
            control.execute(action, executor, "refund:re_1")
        assert ran, "the executor never ran; this test is about what happens after it does"

        written = store.receipts()[before:]
        assert len(written) == 1, (
            f"{len(written)} receipts for an attempt whose outcome is unknown: the store refused "
            "the outcome write and the evidence went with it"
        )
        assert written[0].result is ReceiptResult.AMBIGUOUS, written[0].result
        assert "refused" in (written[0].error or ""), (
            f"the receipt says {written[0].error!r}; it must say the outcome could not be written, "
            "because the effect record does not say it either"
        )
        says = {
            "raised TimeoutError": "TimeoutError",
            "returned": "returned",
            "raised NotExecuted": "NotExecuted",
        }[did]
        assert says in (written[0].error or ""), (
            f"the executor {did} and the receipt says {written[0].error!r}, which does not. An "
            "executor that returned and one that proved it did not act must not leave the same "
            "receipt: for the first the remote very likely acted, and a human resolving this "
            "effect has nothing else to go on"
        )
        assert EventType.EXECUTION_AMBIGUOUS in [event.type for event in store.events()], (
            "no EXECUTION_AMBIGUOUS event: the one unknown outcome here is recorded nowhere"
        )
        record = store.get_effect("refund:re_1")
        assert record is not None and record.attempt == 1
    finally:
        store.close()
        if backend == "postgres":
            from ctrlrun.postgres import PostgresStateStore

            PostgresStateStore.drop_schema(URL, name)


@postgres
def test_the_outcome_re_issue_is_bounded_at_one(schema, caplog):
    """*Every loop in this project is bounded*, and the re-issue above is a loop.

    `T155f` is the precedent: the lost-commit re-issue had no bound, and driven with a proxy that
    swallowed every `COMMIT` the store recursed 96 deep and escaped as `RecursionError`, outside
    the error taxonomy entirely. The stale re-issue is bounded the same way, by a flag, and this
    drives it the same way: a record that has moved **again** by the time the re-issue reads it.

    No proxy can open that window deterministically, because it would have to interleave a rival
    inside each re-issue, and a hold fires once. So the seam is the store's own read, subclassed to
    report the record one attempt further on each time it is consulted while the flag is set: a
    rival that never stops moving. It refuses nothing the real store refuses and invents no outcome
    the real store cannot produce, it only makes every conditional `UPDATE` miss, which is what a
    rival moving the row does. With the bound the call refuses; without it, it recurses until
    Python stops it, which is a failure mode no caller can classify.
    """
    from dataclasses import replace

    from ctrlrun.postgres import PostgresStateStore

    class KeepsMoving(PostgresStateStore):
        moving = False
        reads = 0

        def _read_effect(self, connection, effect_key):  # type: ignore[no-untyped-def]
            record = super()._read_effect(connection, effect_key)
            if record is None or not self.moving:
                return record
            self.reads += 1
            return replace(record, attempt=record.attempt + self.reads)

    caplog.set_level("WARNING", logger="ctrlrun.postgres")
    key = "refund:bounded-reissue"
    store = KeepsMoving(URL, schema=schema, clock=lambda: T0)
    try:
        store.reserve_effect(key, "act_moving", LEASE)
        store.begin_execution(key, "act_moving")
        store.moving = True
        with pytest.raises(CTRLRunError) as refused:
            store.mark_ambiguous(key, "act_moving", "the outcome was lost")
        assert not isinstance(refused.value, RecursionError), (
            "the re-issue chased a record that kept moving until Python stopped it"
        )
        assert "moved from attempt" in str(refused.value), refused.value
        assert store.reads >= 3, (
            f"the record was read {store.reads} times: the re-issue never ran, so this test is "
            "not about the bound"
        )
        # And the restage that then refused is in the log. It used to be written only after the
        # re-issue returned, so the one case an operator would go looking for left no line.
        restages = [r for r in caplog.records if getattr(r, "restage", None)]
        assert len(restages) == 1, (
            f"{len(restages)} restage lines for a re-issue that ran and then refused; §4.3.4's "
            "rule is that which branch ran is observable, and this is the branch that wrote "
            "nothing"
        )
    finally:
        store.moving = False
        store.close()

    # The record is exactly as attempt 1 left it: a refused re-issue writes nothing.
    record = stored(schema, key)
    assert record is not None
    assert (record.state, record.attempt) == (EffectState.EXECUTING, 1), record


@postgres
@pytest.mark.parametrize("moves", [(0, 0, 1, 0), (1, 0, 0, 0)])
def test_the_two_re_issue_bounds_compose(proxy, schema, moves):
    """Two bounds that reset each other are one unbounded loop, and this drives both at once.

    `restaged` bounds the stale re-issue; `retrying` bounds the lost-commit re-issue of `v0.6`
    §4.3.2 Table A2. Each was tested alone. A review composed them: a `COMMIT` lost *inside* a
    restage re-entered the lost-commit path, which re-issued without `restaged`, which restaged
    again without `retrying`, and the two alternated to `RecursionError` at 113 deep. That is
    T155f's failure mode exactly, and T155f's comment says why it matters: `RecursionError` is
    outside this library's closed set of errors, so no caller can classify it and the record is
    left stranded.

    Both halves are driven the way each is driven alone. The lost `COMMIT` is the **real** proxy at
    `drop_before_commit = 1000`, which is T155f's injection; the record that keeps moving is the
    bound test's seam, which is what a same-`action_id` renewal leaves behind. Neither invents a
    store answer: one is a connection dying mid-`COMMIT`, the other is a row a rival advanced.

    **The assertion is the nesting, not the absence of a `RecursionError`, and the reason is what a
    search of these interleavings shows.** Dropping *both* flags recurses, on some patterns and not
    others. Dropping *one* does not recurse at all on any pattern of four reads or fewer: it
    permits exactly one re-issue more than the bound allows, which happens to terminate. So a test
    that asked only "did this blow the stack" would be green for half the ways this can break, and
    would have been green for the very mutants that say each flag is load-bearing. Each bound
    permits one re-issue, so the deepest nesting any interleaving may reach is three; a fourth
    level means a bound was cleared, whether or not that particular pattern went on forever. The
    patterns here are the two that separate every case, measured through this proxy rather than
    guessed: both recurse with both flags gone; `(0, 0, 1, 0)` reaches four with the restage's
    `retrying` dropped, and `(1, 0, 0, 0)` reaches four with the lost-commit re-issue's `restaged`
    dropped. Either one alone would leave one of the two flags untested.
    """
    from dataclasses import replace

    from ctrlrun.postgres import PostgresStateStore

    class LosesCommitsAndKeepsMoving(PostgresStateStore):
        breaking = False
        reads = 0
        depth = 0
        deepest = 0

        def _read_effect(self, connection, effect_key):  # type: ignore[no-untyped-def]
            record = super()._read_effect(connection, effect_key)
            if record is None or not self.breaking:
                return record
            # Where in the cycle the rival moves the row. Whether the bounds compose is a
            # property of the interleaving: the obvious every-other-read pattern ends bounded
            # even when they do not compose, which is mutation pattern 4 one layer up from the
            # store, so the patterns come from a search rather than from intuition.
            moved = moves[self.reads % len(moves)]
            self.reads += 1
            return replace(record, attempt=record.attempt + 1) if moved else record

        def _transition(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            self.depth += 1
            self.deepest = max(self.deepest, self.depth)
            try:
                return super()._transition(*args, **kwargs)
            finally:
                self.depth -= 1

    key = "refund:composed-bounds"
    setup = direct(schema)
    try:
        setup.reserve_effect(key, REUSED, LEASE)
        setup.begin_execution(key, REUSED)
    finally:
        setup.close()

    store = LosesCommitsAndKeepsMoving(proxy.url(URL), schema=schema, clock=lambda: T0)
    raised: Exception | None = None
    try:
        proxy.reset_counters()
        proxy.drop_before_commit = 1000  # every COMMIT from here on, as T155f does
        store.breaking = True
        try:
            store.mark_ambiguous(key, REUSED, "nobody knows what the remote did")
        except Exception as broke:  # RecursionError is an Exception; the unbounded case raised it
            raised = broke
    finally:
        store.breaking = False
        proxy.drop_before_commit = 0
        store.close()

    assert proxy.commits_dropped >= 1, "no COMMIT was swallowed; the lost-commit half never ran"
    assert store.reads >= 2, "the record never moved; the restage half never ran"
    assert store.deepest >= 2, (
        "the transition never nested, so neither re-issue ran and this test is about nothing"
    )
    assert not isinstance(raised, RecursionError), (
        f"the two bounds reset each other and the re-issues alternated to a RecursionError at "
        f"{store.deepest} deep: outside the closed error set, so no caller can classify it"
    )
    assert isinstance(raised, CTRLRunError), (
        f"the composition ended as {type(raised).__name__ if raised else 'a clean return'}; a "
        "write nobody could land must refuse inside the taxonomy"
    )
    assert store.deepest <= 3, (
        f"the transition nested {store.deepest} deep. Each bound permits one re-issue, so three "
        "is the deepest any interleaving may reach; a fourth level is one of the two bounds "
        "cleared by the other, whether or not this pattern went on to recurse"
    )

    record = stored(schema, key)
    assert record is not None
    assert (record.state, record.attempt) == (EffectState.EXECUTING, 1), (
        f"the record is {record.state} at {record.attempt}; no COMMIT was allowed to land"
    )
