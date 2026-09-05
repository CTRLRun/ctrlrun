"""Recovery on restart. Build-list item 5; SPEC-v0.6 §5, §8 T159-T163.

What a process finds when it comes back, and what it is allowed to conclude. The answer to the
second question is almost nothing: **"the holder is dead" is not knowable from the store**, and no
amount of schema makes it knowable. What ages a reservation out is its lease, and what an expired
lease produces is `AMBIGUOUS` -- which is a refusal, not a reclaim.
"""

from __future__ import annotations

import importlib
import inspect
import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ctrlrun import Control, InMemoryStateStore, Policy, SQLiteStateStore
from ctrlrun.action import Action, Principal
from ctrlrun.effect import (
    RECONCILED_COMMITTED,
    RECONCILED_NOT_EXECUTED,
    RESOLVED_BY_RECONCILE,
    EffectRecord,
    EffectState,
)
from ctrlrun.errors import AmbiguousEffect, DuplicateEffect, InvalidArgument
from ctrlrun.receipt import Event, EventType

#: Where a Postgres run finds its server. `resolved_by` is a **column on both shipped backends**,
#: so a test that covered only SQLite and the in-memory store would be covering the two that share
#: an implementation and skipping the one that does not. A review mutated Postgres's read and write
#: of the column to `None` and the whole suite -- 2262 tests -- stayed green, twice.
POSTGRES_URL = os.environ.get("CTRLRUN_TEST_POSTGRES")

BACKENDS = ["sqlite", "memory"] + (["postgres"] if POSTGRES_URL else [])

#: Backends whose storage outlives the object holding it. The in-memory store is excluded by
#: definition, not by omission: a continuation "outliving its process" is a claim about storage.
SHARED_BACKENDS = ["sqlite"] + (["postgres"] if POSTGRES_URL else [])


T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
LEASE = timedelta(minutes=5)
ALLOW = "schema: ctrlrun.policy/v1\nactions:\n  stripe.refund:\n    decision: allow\n"


def an_action(payment_id: str = "p1") -> Action:
    return Action(
        name="stripe.refund",
        arguments={"payment_id": payment_id, "amount": 2000},
        principal=Principal(agent="recovery-agent"),
    )


# --- T159: AMBIGUOUS survives a restart -----------------------------------------------------


def test_T159_ambiguous_survives_a_restart_and_still_refuses_a_blind_retry(tmp_path):
    """SPEC-v0.6 §5, and `v0.1 §5.2`'s statement that no test drove.

    A process coming back finds the unknown outcome exactly where it was left. The restart is a
    real one: a second store object on the same file, opened after the first is closed.
    """
    database = tmp_path / "state.db"
    store = SQLiteStateStore(database, clock=lambda: T0)
    store.reserve_effect("refund:unknown", "act_a", LEASE)
    store.begin_execution("refund:unknown", "act_a")
    store.mark_ambiguous("refund:unknown", "act_a", "the response was lost")
    store.close()

    restarted = SQLiteStateStore(database, clock=lambda: T0)
    record = restarted.get_effect("refund:unknown")
    assert record is not None
    assert record.state is EffectState.AMBIGUOUS
    assert record.error == "the response was lost"

    try:
        restarted.reserve_effect("refund:unknown", "act_b", LEASE)
    except AmbiguousEffect:
        pass
    else:
        raise AssertionError("a restart let a blind retry through an unknown outcome")
    restarted.close()


def test_T159_a_restart_reads_nothing_and_repairs_nothing(tmp_path):
    """SPEC-v0.6 §5.2: *"a restart does nothing on its own."*

    Opening a store migrates it and reads nothing else. A process that came back does not scan,
    does not repair, and does not report -- it waits to be asked for an effect key.
    """
    database = tmp_path / "state.db"
    store = SQLiteStateStore(database, clock=lambda: T0)
    for key, action_id in (("refund:r1", "act_1"), ("refund:r2", "act_2")):
        store.reserve_effect(key, action_id, LEASE)
        store.begin_execution(key, action_id)
    store.mark_ambiguous("refund:r1", "act_1", "lost")
    store.close()

    before = _effect_rows(database)
    SQLiteStateStore(database, clock=lambda: T0 + timedelta(days=7)).close()
    assert _effect_rows(database) == before, (
        "opening the store changed an effect record. A restart is not a repair, and a store that "
        "swept on open would be a store that decided 'the holder is dead' (§5.1, §5.2)"
    )


def _effect_rows(database: Path) -> list[tuple[object, ...]]:
    connection = sqlite3.connect(database)
    try:
        return connection.execute(
            "SELECT effect_key, state, action_id, attempt, lease_expires_at, error "
            "FROM effects ORDER BY effect_key"
        ).fetchall()
    finally:
        connection.close()


# --- T160: an expired lease frees nothing ----------------------------------------------------


def test_T160_an_expired_lease_frees_nothing_and_no_read_transitions_it(tmp_path):
    """SPEC-v0.6 §5.2, §5.3.

    Nothing sweeps: no background thread, no timer, no cron, no `ctrlrun reap`. An expired lease
    becomes `AMBIGUOUS` **lazily**, at the moment the next contender plans a reservation -- and
    that is a refusal, not a reclaim.

    The reads are the point. `get_effect`, `list_effects` and opening the store all see a lapsed
    lease, and none of them may move it.
    """
    database = tmp_path / "state.db"
    now = T0
    store = SQLiteStateStore(database, clock=lambda: now)
    store.reserve_effect("refund:lapsed", "act_a", timedelta(seconds=30))
    store.begin_execution("refund:lapsed", "act_a")
    now = T0 + timedelta(hours=1)

    for read in (
        lambda: store.get_effect("refund:lapsed"),
        lambda: store.list_effects(),
        lambda: store.list_effects(EffectState.EXECUTING),
    ):
        read()
        record = store.get_effect("refund:lapsed")
        assert record is not None and record.state is EffectState.EXECUTING, (
            f"a read moved the record to {record.state if record else None}; reads never "
            "transition anything (§5.2)"
        )

    SQLiteStateStore(database, clock=lambda: now).close()
    record = store.get_effect("refund:lapsed")
    assert record is not None and record.state is EffectState.EXECUTING

    # Only a contender moves it, and what it gets is a refusal.
    try:
        store.reserve_effect("refund:lapsed", "act_b", LEASE)
    except AmbiguousEffect:
        pass
    else:
        raise AssertionError("an expired lease was handed to the next contender")
    moved = store.get_effect("refund:lapsed")
    assert moved is not None and moved.state is EffectState.AMBIGUOUS
    store.close()


def test_T160_there_is_no_reaper(tmp_path):
    """The absence, asserted by name so adding one is a deliberate act with a failing test in
    front of it (§5.2).

    **This is attribution, not prevention, and a review was right to say so.** It was written as
    though it defended §5.2; it does not. A real reaper -- a `threading.Thread` sweeping expired
    leases to `AMBIGUOUS` in `SQLiteStateStore.__init__` -- was added to prove the point, and the
    three *behavioural* tests in this file failed while this one passed. They are the defence.
    What this adds is a name: a background sweeper arrives with an obvious failing test rather
    than as a diff nobody reads twice.

    Three things it also got wrong, all of them the reason it looked stronger than it is. It read
    `Path("src/ctrlrun")`, relative to the working directory, so anywhere but the repo root it
    *errored* rather than asserted. It computed a `source` variable it never used and asserted
    that it was truthy, which it always was. And it looked at `state.py` alone, so a timer in
    `postgres.py` was invisible to it.
    """
    from ctrlrun.cli import main as cli

    commands = set(cli.main.commands)
    for forbidden in ("reap", "sweep", "expire", "gc", "cleanup", "reclaim"):
        assert forbidden not in commands, f"a `ctrlrun {forbidden}` command appeared (§5.2)"

    # Every module that implements the store protocol, found from the modules themselves rather
    # than from a path relative to wherever pytest was started.
    for module in (SQLiteStateStore.__module__, InMemoryStateStore.__module__, "ctrlrun.postgres"):
        text = Path(inspect.getfile(importlib.import_module(module))).read_text(encoding="utf-8")
        for machinery in ("threading.Timer", "threading.Thread", "sched.scheduler"):
            assert machinery not in text, (
                f"{module} grew a {machinery}; §5.2 says nothing reclaims an expired lease, and "
                "a background sweeper is exactly the thing that would"
            )


# --- T161: what reclaims it, and on whose authority -------------------------------------------


@pytest.mark.parametrize("backend", BACKENDS)
def test_T161_a_human_resolution_records_who(tmp_path, backend):
    """SPEC-v0.6 §5.3. `resolved_by` is queryable, and is not the executor's error text."""
    store = _store(tmp_path, backend)
    _ambiguate(store, "refund:h")

    resolved = store.resolve_effect("refund:h", EffectState.COMMITTED, "cli:ada")
    assert resolved.resolved_by == "cli:ada"
    assert store.get_effect("refund:h").resolved_by == "cli:ada"

    # `error` keeps what `v0.1` put there: a receipt already written must not change meaning.
    assert resolved.error is not None and "cli:ada" in resolved.error
    assert "the response was lost" in resolved.error


@pytest.mark.parametrize("backend", BACKENDS)
def test_T161_a_retry_after_a_resolution_is_not_attributed_to_the_resolver(tmp_path, backend):
    """`v0.1 §5.4`'s one automatic retry must not inherit the human who permitted it.

    A human resolves an `AMBIGUOUS` effect to `FAILED`, which is precisely the statement *"go
    ahead and retry"*. The agent then retries and commits. That commit is the **agent's**, and a
    record still naming the human says a person decided something they did not.

    v0.5 got this right by accident: the resolver lived inside the free-text `error`, and the
    renewal cleared `error`. Item 5 moved it into a column the same `UPDATE` did not clear, so
    the item that exists to improve attribution regressed it -- and §8.1's soak counts humans
    from this field. Two of the three stores agreed with each other and not with the third,
    which is what the conformance case beside this one is for.
    """
    store = _store(tmp_path, backend)
    store.reserve_effect("refund:retry", "act_a", LEASE)
    store.begin_execution("refund:retry", "act_a")
    store.mark_ambiguous("refund:retry", "act_a", "the response was lost")
    store.resolve_effect("refund:retry", EffectState.FAILED, "cli:ada")

    resolved = store.get_effect("refund:retry")
    assert resolved is not None and resolved.resolved_by == "cli:ada"

    store.reserve_effect("refund:retry", "act_b", LEASE)
    renewed = store.get_effect("refund:retry")
    assert renewed is not None
    assert renewed.attempt == 2
    assert renewed.resolved_by is None, (
        f"the retry carries resolved_by={renewed.resolved_by!r}. A human said this effect may be "
        "retried; they did not commit the retry, and `ctrlrun effects` would print their name "
        "beside an outcome the agent produced"
    )

    store.begin_execution("refund:retry", "act_b")
    store.commit_effect("refund:retry", "act_b", {"ok": True})
    committed = store.get_effect("refund:retry")
    assert committed is not None
    assert committed.state is EffectState.COMMITTED
    assert committed.resolved_by is None, (
        f"a committed retry is attributed to {committed.resolved_by!r}; nothing resolved it"
    )
    store.close()


@pytest.mark.parametrize("backend", BACKENDS)
def test_T161_an_unresolved_record_has_no_resolver(tmp_path, backend):
    """The control. Without it a store that stamped every record would pass the test above."""
    store = _store(tmp_path, backend)
    _ambiguate(store, "refund:none")
    assert store.get_effect("refund:none").resolved_by is None

    store.reserve_effect("refund:live", "act_l", LEASE)
    assert store.get_effect("refund:live").resolved_by is None


def test_T161_a_reconcile_hook_records_itself_and_unknown_changes_nothing(tmp_path):
    """SPEC-v0.6 §5.3's second authority, and `v0.2 §2`'s rule that it moves a record **only
    where its answer points**.

    The two authorities are different, and §5's whole argument is that a reader must be able to
    tell them apart -- item 8's soak cannot say anything about unexplained ambiguity if
    "a human decided" and "a hook decided" are the same free-text string.
    """
    for answer, expected in (
        (RECONCILED_COMMITTED, EffectState.COMMITTED),
        (RECONCILED_NOT_EXECUTED, EffectState.FAILED),
    ):
        store = SQLiteStateStore(tmp_path / f"{answer}.db", clock=lambda: T0)
        control = Control(Policy.from_yaml(ALLOW), store, clock=lambda: T0)
        _ambiguate(store, "refund:rec")

        # A `committed` answer means the effect already happened, so the retry is refused as a
        # duplicate; a `failed` answer is `v0.1 §5.4`'s one automatic retry and the action runs.
        # Either way the record moved, and it is the record this test is about.
        try:
            control.execute(
                an_action("rec"),
                lambda: "ok",
                "refund:rec",
                reconcile=lambda _action, answer=answer: answer,
            )
            ran = True
        except DuplicateEffect:
            ran = False
        assert ran is (answer == RECONCILED_NOT_EXECUTED), (
            f"a {answer!r} answer did not behave as v0.1 §5.4 says"
        )
        record = store.get_effect("refund:rec")
        assert record is not None
        if answer == RECONCILED_NOT_EXECUTED:
            # The retry ran and committed, which is what a proven non-execution permits.
            assert record.state is EffectState.COMMITTED
        else:
            assert record.state is expected
        if answer == RECONCILED_COMMITTED:
            assert record.resolved_by == f"{RESOLVED_BY_RECONCILE}:stripe.refund", (
                f"a reconcile resolution recorded {record.resolved_by!r}; §5.3 requires the two "
                "authorities to be distinguishable, and two hooks from each other"
            )
        store.close()

    # "unknown" changes nothing -- including the resolver.
    store = SQLiteStateStore(tmp_path / "unknown.db", clock=lambda: T0)
    control = Control(Policy.from_yaml(ALLOW), store, clock=lambda: T0)
    _ambiguate(store, "refund:rec")
    try:
        control.execute(
            an_action("rec"), lambda: "ok", "refund:rec", reconcile=lambda _action: "unknown"
        )
    except AmbiguousEffect:
        pass
    else:
        raise AssertionError("an 'unknown' answer let the action through")
    record = store.get_effect("refund:rec")
    assert record is not None and record.state is EffectState.AMBIGUOUS
    assert record.resolved_by is None, "an answer that changed nothing recorded a resolver anyway"
    store.close()


def test_T161_ctrlrun_inspect_shows_the_resolver_in_both_of_its_outputs(tmp_path, monkeypatch):
    """§5.3 names two read surfaces: *"`ctrlrun effects` and `ctrlrun inspect` are where a reader
    finds it"*. Only one of them had it.

    `inspect` printed `effect  {key}  {state}  attempt {n}` and `--json` built `_effect_dict`
    without the field, so on the command §5.3 points an operator at, the answer was not there in
    either form. The event line carried `resolved_by=human` -- the *kind*, not the resolver --
    which is the other half of the vocabulary this milestone separated.
    """
    from click.testing import CliRunner

    from ctrlrun.cli import main as cli

    database = tmp_path / ".ctrlrun" / "state.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteStateStore(database, clock=lambda: T0)
    action = an_action("insp")
    store.append_event(
        Event(
            event_id=None,
            ts=T0,
            type=EventType.ACTION_PROPOSED,
            action_id=action.action_id,
            effect_key="refund:insp",
            data={"action": action.name},
        )
    )
    store.reserve_effect("refund:insp", action.action_id, LEASE)
    store.begin_execution("refund:insp", action.action_id)
    store.mark_ambiguous("refund:insp", action.action_id, "the response was lost")
    store.resolve_effect("refund:insp", EffectState.COMMITTED, "cli:ada")
    store.close()

    monkeypatch.setenv("CTRLRUN_STATE", str(database))
    text = CliRunner().invoke(cli.main, ["inspect", action.action_id])
    assert text.exit_code == 0, text.output
    effect_line = next(line for line in text.output.splitlines() if line.startswith("effect "))
    assert "resolved by cli:ada" in effect_line, (
        f"§5.3 says inspect is where a reader finds the resolver:\n{effect_line}"
    )

    as_json = CliRunner().invoke(cli.main, ["inspect", action.action_id, "--json"])
    assert as_json.exit_code == 0, as_json.output
    document = json.loads(as_json.output)
    assert document["effect"]["resolved_by"] == "cli:ada", (
        f"--json has no resolver: {document['effect']}"
    )

    # The control: an unresolved record says nothing in either form.
    plain = SQLiteStateStore(database, clock=lambda: T0)
    other = an_action("plain")
    plain.append_event(
        Event(
            event_id=None,
            ts=T0,
            type=EventType.ACTION_PROPOSED,
            action_id=other.action_id,
            effect_key="refund:plain",
            data={"action": other.name},
        )
    )
    plain.reserve_effect("refund:plain", other.action_id, LEASE)
    plain.close()
    quiet = CliRunner().invoke(cli.main, ["inspect", other.action_id])
    assert "resolved by" not in quiet.output, (
        f"an unresolved record names a resolver:\n{quiet.output}"
    )
    quiet_json = CliRunner().invoke(cli.main, ["inspect", other.action_id, "--json"])
    assert json.loads(quiet_json.output)["effect"]["resolved_by"] is None


def test_T161_a_reader_can_join_events_to_records_on_one_field(tmp_path, monkeypatch):
    """SPEC-v0.6 §5.3, and the reason item 8's soak can count these at all.

    Two authorities, two paths, and until now two *vocabularies* under one field name. The CLI
    stamped `cli:local` on the record and put `resolved_by="human"` in the event; the reconcile
    hook stamped `reconcile` on the record and put `resolved_by="reconcile"` in the event. They
    coincided for the hook, which is exactly what hid that they did not for the human -- anything
    joining events to records on the field name got a mismatch on the one authority §5.3 says
    matters most.

    So the two names are separated and each means one thing everywhere:

    - `event["resolver"]` is **the string on the record**, on both paths.
    - `event["resolved_by"]` is the **kind** -- `human` or `reconcile` -- on both paths.
    """
    from click.testing import CliRunner

    from ctrlrun.cli import main as cli
    from ctrlrun.effect import RESOLVED_BY_HUMAN, RESOLVED_BY_RECONCILE

    database = tmp_path / ".ctrlrun" / "state.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteStateStore(database, clock=lambda: T0)
    _ambiguate(store, "refund:join")
    store.close()

    monkeypatch.setenv("CTRLRUN_STATE", str(database))
    result = CliRunner().invoke(cli.main, ["resolve", "refund:join", "--committed"])
    assert result.exit_code == 0, result.output

    after = SQLiteStateStore(database, clock=lambda: T0)
    record = after.get_effect("refund:join")
    assert record is not None
    resolved = [e for e in after.events() if e.type is EventType.EFFECT_RESOLVED]
    assert len(resolved) == 1
    data = resolved[0].data
    assert data["resolver"] == record.resolved_by, (
        f"the event says resolver={data['resolver']!r} and the record says "
        f"{record.resolved_by!r}; a reader joining the two on this field gets nothing"
    )
    assert data["resolved_by"] == RESOLVED_BY_HUMAN
    assert RESOLVED_BY_RECONCILE != RESOLVED_BY_HUMAN
    after.close()


def test_T161_a_reconcile_hook_names_the_action_it_reconciled(tmp_path):
    """§5.3's table says `reconcile:<action name>`, and it says it for a reason.

    Two hooks reconciling two different actions were indistinguishable in the record: both wrote
    the bare string `reconcile`. That is the finer distinction §5.3 promised and §8.1's soak was
    told it would have, and the code shipped without it.
    """
    store = SQLiteStateStore(tmp_path / "state.db", clock=lambda: T0)
    control = Control(Policy.from_yaml(ALLOW), store, clock=lambda: T0)
    _ambiguate(store, "refund:named")
    action = an_action("named")
    try:
        control.execute(
            action, lambda: "ok", "refund:named", reconcile=lambda _a: RECONCILED_COMMITTED
        )
    except DuplicateEffect:
        pass  # a committed answer means the effect already happened; the retry is refused
    else:
        raise AssertionError("a committed reconcile answer let the action run again")

    record = store.get_effect("refund:named")
    assert record is not None
    assert record.resolved_by == f"{RESOLVED_BY_RECONCILE}:{action.name}", (
        f"the hook recorded {record.resolved_by!r}; two hooks reconciling two different actions "
        "must not be one string"
    )
    resolved = [e for e in store.events() if e.type is EventType.EFFECT_RESOLVED]
    assert resolved and resolved[-1].data["resolver"] == record.resolved_by
    assert resolved[-1].data["resolved_by"] == RESOLVED_BY_RECONCILE
    store.close()


@pytest.mark.parametrize("backend", BACKENDS)
def test_T161_a_resolver_cannot_forge_a_row_in_the_evidence(tmp_path, backend):
    """`ctrlrun effects` prints one record per line, so a newline in a resolver is a second row.

    A review demonstrated it: `resolve_effect(key, COMMITTED, "\\n  resolved by god")` stored the
    string verbatim, and the listing then showed an effect that does not exist. Not reachable
    through the shipped CLI, which writes a constant -- and `resolve_effect` is on the frozen
    `StateStore` protocol, so any caller holding a store could do it, on every backend.

    A refusal rather than an escape: a record of who decided, that is not what anybody typed, is
    worse than a rejected write.
    """
    store = _store(tmp_path, backend)
    _ambiguate(store, "refund:forge")
    for forged in ("\n  refund:fake  committed", "cli:eve\r\nrefund:fake", "cli:\x00eve"):
        try:
            store.resolve_effect("refund:forge", EffectState.COMMITTED, forged)
        except InvalidArgument:
            pass
        else:
            raise AssertionError(f"a resolver containing {forged!r} was accepted into the record")
    record = store.get_effect("refund:forge")
    assert record is not None
    assert record.state is EffectState.AMBIGUOUS, "a refused resolve moved the record anyway"
    assert record.resolved_by is None

    # The control: an ordinary resolver still works, so this is a refusal and not a wall.
    store.resolve_effect("refund:forge", EffectState.COMMITTED, "cli:ada")
    resolved = store.get_effect("refund:forge")
    assert resolved is not None and resolved.resolved_by == "cli:ada"
    store.close()


def test_T161_resolved_by_survives_a_restart(tmp_path):
    """A resolution is durable, or it is not a record of who overrode the kernel."""
    database = tmp_path / "state.db"
    store = SQLiteStateStore(database, clock=lambda: T0)
    _ambiguate(store, "refund:d")
    store.resolve_effect("refund:d", EffectState.FAILED, "cli:ada")
    store.close()

    restarted = SQLiteStateStore(database, clock=lambda: T0)
    record = restarted.get_effect("refund:d")
    assert record is not None and record.resolved_by == "cli:ada"
    restarted.close()


def test_T161_no_receipt_carries_a_resolver(tmp_path):
    """SPEC-v0.6 §5.3, stated as structural rather than an omission.

    A receipt is written when an action reaches a terminal state; a resolution happens
    **afterwards**, often days later and by somebody who was not the actor. Putting it on a
    receipt would mean mutating one that has already been written and hashed -- which §6.5 would
    correctly report as `content_altered`, the chain detecting its own kernel.
    """
    from ctrlrun.receipt import Receipt

    assert "resolved_by" not in {field.name for field in Receipt.__dataclass_fields__.values()}


# --- T162: no process identity is inferable ---------------------------------------------------


def test_T162_no_process_identity_is_inferable_from_a_record():
    """SPEC-v0.6 §5.1, asserted **by name** so adding one is a deliberate act.

    A `holder_host` or `holder_pid` column would be the first thing somebody wrote a reclaimer
    against -- *if the holder is not alive, free the key* -- and that reclaimer is wrong on the
    day it matters, because a container that stopped answering a health check may still have an
    open socket to a payment API. Worse, the column would look authoritative: it names a host,
    and a host can be pinged. A field that invites a false inference is worse than no field.
    """
    fields = set(EffectRecord.__dataclass_fields__)
    for forbidden in ("holder", "holder_pid", "holder_host", "pid", "host", "hostname", "owner"):
        assert forbidden not in fields, (
            f"EffectRecord grew {forbidden!r}; §5.1 refuses process identity on a record"
        )
    assert fields == {
        "effect_key",
        "state",
        "action_id",
        "attempt",
        "created_at",
        "updated_at",
        "lease_expires_at",
        "result",
        "error",
        "resolved_by",
    }, f"EffectRecord's shape changed: {sorted(fields)}"


def test_T162_action_id_identifies_an_attempt_not_a_process():
    """The reason §5.1's refusal is not merely stylistic: there is nothing else to key on.

    Two attempts by one process have two ids, and one attempt survives a process restart with the
    same id if the caller rebuilt the same `Action`. So a record's `action_id` says which attempt,
    never which process.
    """
    first, second = an_action(), an_action()
    assert first.action_id != second.action_id, "two attempts shared an action_id"
    assert first.action_hash == second.action_hash, "the same action hashed differently"

    signature = inspect.signature(Action.__init__)
    assert "action_id" in signature.parameters, (
        "action_id is no longer caller-supplyable; §4.3.3's argument depends on it being so"
    )


# --- T163: a continuation held by a dead process ----------------------------------------------


@pytest.mark.parametrize("backend", SHARED_BACKENDS)
def test_T163_a_continuation_outlives_its_process_but_not_its_lease(tmp_path, backend):
    """SPEC-v0.6 §5.4, all four assertions, and the two refusals **by exception type**.

    `take_continuation`'s message is identical whether the value was forged, already consumed, or
    belongs to a suspension that has moved on -- telling an agent which of those it hit is telling
    it how to search. So the two facts an operator needs travel in the type and in the record's
    own state, and a more helpful string here would reopen a disclosure the store closed on
    purpose.
    """
    action = an_action("cont")

    holder = _store(tmp_path, backend)
    holder.reserve_effect("refund:cont", action.action_id, LEASE)
    holder.begin_execution("refund:cont", action.action_id)
    holder.hold_continuation(action, "refund:cont", "tok-1", T0 + timedelta(hours=1))
    holder.close()  # the process is gone

    # 1. The row survives, and 2. a different process may finish it -- new in v0.6, and only
    #    because a shared store makes it possible. §5.4's claim is specifically about Postgres
    #    ("two gateways on two hosts now share continuations"), which is why this runs against
    #    every backend whose storage outlives the object holding it and not only SQLite.
    other = _second(holder, tmp_path, backend)
    held = other.take_continuation("tok-1")
    assert held.effect_key == "refund:cont"
    assert held.action.action_hash == action.action_hash, (
        "the rehydrated action is not the one that was suspended; a resumption is the SAME action"
    )

    # 3. A second take is refused.
    try:
        other.take_continuation("tok-1")
    except InvalidArgument:
        pass
    else:
        raise AssertionError("one suspension admitted two resumptions")
    other.close()

    # 4. Where the lease lapsed first, the take is refused with the lease's own exception -- and
    #    the effect becomes AMBIGUOUS by the ordinary path.
    lapsed_home = tmp_path / "lapsed"
    lapsed_home.mkdir(exist_ok=True)
    first = _store(lapsed_home, backend)
    first.reserve_effect("refund:late", action.action_id, timedelta(seconds=30))
    first.begin_execution("refund:late", action.action_id)
    first.hold_continuation(action, "refund:late", "tok-2", T0 + timedelta(minutes=1))
    first.close()

    later = T0 + timedelta(hours=1)
    after = _second(first, lapsed_home, backend, clock=lambda: later)
    try:
        after.take_continuation("tok-2")
    except AmbiguousEffect:
        pass
    else:
        raise AssertionError("a continuation whose lease had lapsed was still resumable")

    try:
        after.reserve_effect("refund:late", "act_next", LEASE)
    except AmbiguousEffect:
        pass
    else:
        raise AssertionError("the lapsed effect did not become AMBIGUOUS by the ordinary path")
    record = after.get_effect("refund:late")
    assert record is not None and record.state is EffectState.AMBIGUOUS
    after.close()


@pytest.mark.skipif(not POSTGRES_URL, reason="CTRLRUN_TEST_POSTGRES is not set")
def test_T161_the_operator_commands_reach_a_postgres_store(tmp_path, monkeypatch):
    """§5's two authorities need a route to the store the operator actually runs.

    `_store()` returned `SQLiteStateStore(state_path())` unconditionally, so on the backend this
    whole milestone exists for, `ctrlrun resolve` -- §5.3's human authority -- could not reach the
    record, `ctrlrun effects` could not show a lapsed lease, and `resolved_by` had no read surface
    at all outside a Python session. A review found it; §9.4 gains the flag.

    It **never creates a schema.** `PostgresStateStore` creates nothing, and an operator command
    that quietly made one would be a reader with a side effect on the thing it reads.
    """
    from click.testing import CliRunner

    from ctrlrun.cli import main as cli
    from ctrlrun.postgres import PostgresStateStore

    schema = f"rec_{uuid.uuid4().hex[:12]}"
    PostgresStateStore.create_schema(POSTGRES_URL, schema)
    _SCHEMAS.append(schema)
    url = f"{POSTGRES_URL}?ctrlrun_schema={schema}"

    store = PostgresStateStore(POSTGRES_URL, schema=schema, clock=lambda: T0)
    _ambiguate(store, "refund:pg")
    store.close()

    runner = CliRunner()
    listed = runner.invoke(cli.main, ["effects", "--store-url", url])
    assert listed.exit_code == 0, listed.output
    assert "refund:pg" in listed.output, listed.output

    resolved = runner.invoke(cli.main, ["resolve", "refund:pg", "--committed", "--store-url", url])
    assert resolved.exit_code == 0, resolved.output

    again = PostgresStateStore(POSTGRES_URL, schema=schema, clock=lambda: T0)
    record = again.get_effect("refund:pg")
    assert record is not None
    assert record.state is EffectState.COMMITTED
    assert record.resolved_by == "cli:local", (
        f"the CLI resolved a Postgres record and recorded {record.resolved_by!r}"
    )
    again.close()

    after = runner.invoke(cli.main, ["effects", "--store-url", url])
    assert "resolved by cli:local" in after.output, after.output

    # A schema that does not exist is refused, not created.
    absent = f"{POSTGRES_URL}?ctrlrun_schema=rec_{uuid.uuid4().hex[:12]}"
    missing = runner.invoke(cli.main, ["effects", "--store-url", absent])
    assert missing.exit_code != 0, (
        "an operator command created a schema in the operator's database; a reader must not "
        f"have a side effect on what it reads:\n{missing.output}"
    )


# --- helpers ----------------------------------------------------------------------------------


def _second(store, tmp_path: Path, backend: str, *, clock=None):
    """A second, independent store on the SAME storage as `store`.

    Two handles rather than one object, because §5.4's claim -- *"two gateways on two hosts now
    share continuations"* -- is a claim about the storage and not about a Python reference.
    """
    if backend == "postgres":
        from ctrlrun.postgres import PostgresStateStore

        assert POSTGRES_URL
        return PostgresStateStore(
            POSTGRES_URL, schema=_SCHEMA_OF[id(store)], clock=clock or (lambda: T0)
        )
    return SQLiteStateStore(tmp_path / "state.db", clock=clock or (lambda: T0))


def _store(tmp_path: Path, backend: str):
    if backend == "memory":
        return InMemoryStateStore(clock=lambda: T0)
    if backend == "postgres":
        from ctrlrun.postgres import PostgresStateStore

        assert POSTGRES_URL
        name = f"rec_{uuid.uuid4().hex[:12]}"
        PostgresStateStore.create_schema(POSTGRES_URL, name)
        _SCHEMAS.append(name)
        made = PostgresStateStore(POSTGRES_URL, schema=name, clock=lambda: T0)
        # `PostgresStateStore` exposes no `schema`, and §9.1 is a closed list of public names, so
        # the test remembers it rather than the library growing one for a test's convenience.
        _SCHEMA_OF[id(made)] = name
        return made
    return SQLiteStateStore(tmp_path / "state.db", clock=lambda: T0)


#: Schemas created by `_store`, dropped by the autouse fixture below. A test that leaks one leaks
#: it into a database CI shares with every other Postgres test in this repository.
_SCHEMAS: list[str] = []

#: Which schema a store made by `_store` is using, keyed by object identity.
_SCHEMA_OF: dict[int, str] = {}


@pytest.fixture(autouse=True)
def _drop_schemas():
    yield
    from contextlib import suppress

    while _SCHEMAS:
        name = _SCHEMAS.pop()
        with suppress(Exception):
            from ctrlrun.postgres import PostgresStateStore

            PostgresStateStore.drop_schema(POSTGRES_URL, name)


def _ambiguate(store, key: str) -> None:
    store.reserve_effect(key, "act_x", LEASE)
    store.begin_execution(key, "act_x")
    store.mark_ambiguous(key, "act_x", "the response was lost")


def test_T160_ctrlrun_effects_shows_an_expired_lease_as_expired(tmp_path, monkeypatch):
    """SPEC-v0.6 §5.2: an expired lease that is never contended stays `EXECUTING` in the table,
    and `ctrlrun effects` shows it **as expired** -- because printing the state alone hides it.

    A display change, and not a transition: the record is asserted unchanged afterwards.
    """
    from click.testing import CliRunner

    from ctrlrun.cli import main as cli

    database = tmp_path / ".ctrlrun" / "state.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    now = T0
    store = SQLiteStateStore(database, clock=lambda: now)
    store.reserve_effect("refund:stale", "act_s", timedelta(seconds=30))
    store.begin_execution("refund:stale", "act_s")
    store.close()

    monkeypatch.setenv("CTRLRUN_STATE", str(database))
    result = CliRunner().invoke(cli.main, ["effects"])
    assert result.exit_code == 0, result.output
    assert "refund:stale" in result.output
    assert "lease expired" in result.output, (
        f"an expired lease is not shown as expired, so an operator reading `ctrlrun effects` "
        f"cannot tell it from live work:\n{result.output}"
    )

    after = SQLiteStateStore(database, clock=lambda: now)
    record = after.get_effect("refund:stale")
    assert record is not None and record.state is EffectState.EXECUTING, (
        "listing the effects transitioned one; reading is not a transition (§5.2)"
    )
    after.close()


def test_T160_ctrlrun_effects_marks_only_what_is_expired_and_only_what_is_resolved(
    tmp_path, monkeypatch
):
    """The controls for the line above, and for `resolved by X` -- which had none at all.

    A review ran four mutants against the previous version and every one stayed green across the
    whole suite: marking **every** record expired, dropping the `lease_is_live` half of the guard
    so a live reservation printed as expired, narrowing `_LEASED` to `EXECUTING` so an expired
    `RESERVED` record printed clean, and deleting the `resolved by X` branch entirely. A test that
    asserts a string is present, with nothing asserting where it is absent, cannot fail for any
    of them.

    So: four records in one listing, and each line is checked for what it must say **and** what it
    must not.
    """
    from click.testing import CliRunner

    from ctrlrun.cli import main as cli

    database = tmp_path / ".ctrlrun" / "state.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteStateStore(database, clock=lambda: T0)

    # Expired, in each of the two states that hold a lease.
    store.reserve_effect("refund:expired-reserved", "act_er", timedelta(seconds=30))
    store.reserve_effect("refund:expired-executing", "act_ee", timedelta(seconds=30))
    store.begin_execution("refund:expired-executing", "act_ee")
    # Live, and therefore not expired -- the control for the guard's other half.
    store.reserve_effect("refund:live", "act_lv", timedelta(days=3650))
    # Resolved, and one that is not.
    _ambiguate(store, "refund:resolved")
    store.resolve_effect("refund:resolved", EffectState.COMMITTED, "cli:ada")
    store.close()

    monkeypatch.setenv("CTRLRUN_STATE", str(database))
    result = CliRunner().invoke(cli.main, ["effects"])
    assert result.exit_code == 0, result.output
    lines = {
        line.split()[0]: line for line in result.output.splitlines() if line.startswith("refund:")
    }
    assert set(lines) == {
        "refund:expired-reserved",
        "refund:expired-executing",
        "refund:live",
        "refund:resolved",
    }, f"the listing is not what this test set up:\n{result.output}"

    for key in ("refund:expired-reserved", "refund:expired-executing"):
        assert "lease expired" in lines[key], (
            f"{key} holds a lapsed lease and the line does not say so:\n{lines[key]}"
        )
    assert "lease expired" not in lines["refund:live"], (
        "a live reservation is shown as expired, so the marker says nothing:\n"
        + lines["refund:live"]
    )
    assert "lease expired" not in lines["refund:resolved"], (
        "a resolved record holds no lease and must not be marked as having lapsed one"
    )

    assert "resolved by cli:ada" in lines["refund:resolved"], (
        f"a resolved record does not say who resolved it:\n{lines['refund:resolved']}"
    )
    for key in ("refund:expired-reserved", "refund:expired-executing", "refund:live"):
        assert "resolved by" not in lines[key], (
            f"{key} was never resolved and the line names a resolver:\n{lines[key]}"
        )
