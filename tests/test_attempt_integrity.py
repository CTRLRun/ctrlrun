"""Attempt numbers never repeat, on Postgres. Item 3a; SPEC-v0.7 §5.6, §8.3a (T246, T246b).

Two properties, and at 0.6.1 neither held on Postgres: **no two reservations of one key carry the
same attempt number**, and **the number a reservation method returns is the number it wrote**.
v0.7 makes the attempt number load-bearing twice, in the idempotency token and in the ceiling, so a
reused number gives two dispatches one token and lets a ceiling of N admit N+1.

**Every window here is opened on purpose** (mutation pattern 4). Two processes renewing
concurrently open none of them: each needs one store stalled at a precise point inside a
reservation method while a second process renews, runs and fails. The proxy the tests own does the
stalling, and it holds one statement on one connection (`failure_injection.Proxy.hold_when`) or
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
import struct
import subprocess
import sys
import tempfile
import textwrap
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ctrlrun.effect import EffectState
from failure_injection import Proxy, statement_of, upstream_of

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

#: One child: open a store, optionally wait to be told to go, take one reservation, and optionally
#: run and fail it. It reports the reservation it was handed, or the refusal, and every §4.3.4
#: branch its store took, as JSON on stdout.
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

    frozen = datetime.fromisoformat(job["now"])
    store = PostgresStateStore(job["url"], schema=job["schema"], clock=lambda: frozen)
    out = {"ctrlrun": _WHERE, "attempt": None, "action_id": None, "error": None,
           "state": None, "message": None, "branches": branches}
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
        lease = timedelta(minutes=5)
        if job["approval_id"]:
            _, reservation = store.consume_approval_and_reserve(
                job["approval_id"], job["action_hash"], job["key"], job["action_id"], lease)
        else:
            reservation = store.reserve_effect(job["key"], job["action_id"], lease)
        out["attempt"] = reservation.attempt
        out["action_id"] = reservation.action_id
        if job["then_fail"]:
            store.begin_execution(job["key"], job["action_id"])
            store.fail_effect(job["key"], job["action_id"], "the remote refused before acting")
    except Exception as refused:
        out["error"] = type(refused).__name__
        out["state"] = getattr(refused, "state", None)
        out["message"] = str(refused)
    finally:
        store.close()
    sys.stdout.write(json.dumps(out))
""")


class Child:
    """One store in its own OS process. Started at once; `result()` waits for it, bounded."""

    def __init__(
        self,
        home: Path,
        name: str,
        *,
        url: str,
        schema: str,
        key: str,
        action_id: str,
        now: datetime,
        gated: bool,
        then_fail: bool = False,
        approval: tuple[str, str] | None = None,
    ) -> None:
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
                    "key": key,
                    "action_id": action_id,
                    "now": now.isoformat(),
                    "ready": str(self.ready) if gated else None,
                    "go": str(self.go_marker),
                    "bound": BOUND,
                    "then_fail": then_fail,
                    "approval_id": approval[0] if approval else None,
                    "action_hash": approval[1] if approval else None,
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


def effects_statement(verb: bytes, schema: str):
    """A `hold_when` predicate: a statement on this schema's `effects` table beginning `verb`."""
    table = f'"{schema}".effects'.encode()

    def matches(kind: bytes, body: bytes) -> bool:
        text = statement_of(kind, body)
        return text is not None and text.lstrip().upper().startswith(verb) and table in text

    return matches


# --- the injector's own control -------------------------------------------------------------


def test_the_hold_trigger_reads_the_parse_and_not_a_bind_parameter():
    """`hold_when` sees SQL text only where the protocol puts it.

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
        proxy.hold_when = held_at(window, schema)
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
        proxy.hold_when = the_re_issues_planning_read

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
