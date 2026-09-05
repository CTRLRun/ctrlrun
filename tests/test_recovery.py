"""Recovery on restart. Build-list item 5; SPEC-v0.6 §5, §8 T159-T163.

What a process finds when it comes back, and what it is allowed to conclude. The answer to the
second question is almost nothing: **"the holder is dead" is not knowable from the store**, and no
amount of schema makes it knowable. What ages a reservation out is its lease, and what an expired
lease produces is `AMBIGUOUS` -- which is a refusal, not a reclaim.
"""

from __future__ import annotations

import inspect
import sqlite3
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
    front of it (§5.2)."""
    from ctrlrun.cli import main as cli

    commands = set(cli.main.commands)
    for forbidden in ("reap", "sweep", "expire", "gc", "cleanup", "reclaim"):
        assert forbidden not in commands, f"a `ctrlrun {forbidden}` command appeared (§5.2)"

    source = Path(SQLiteStateStore.__module__.replace(".", "/")).name
    text = (Path("src/ctrlrun") / "state.py").read_text(encoding="utf-8")
    assert "threading.Timer" not in text, "the store grew a background timer"
    assert source


# --- T161: what reclaims it, and on whose authority -------------------------------------------


@pytest.mark.parametrize("backend", ["sqlite", "memory"])
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


@pytest.mark.parametrize("backend", ["sqlite", "memory"])
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
    from ctrlrun.errors import NotExecuted

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
            assert record.resolved_by == RESOLVED_BY_RECONCILE, (
                f"a reconcile resolution recorded {record.resolved_by!r}; §5.3 requires the two "
                "authorities to be distinguishable"
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
    assert NotExecuted


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


def test_T163_a_continuation_outlives_its_process_but_not_its_lease(tmp_path):
    """SPEC-v0.6 §5.4, all four assertions, and the two refusals **by exception type**.

    `take_continuation`'s message is identical whether the value was forged, already consumed, or
    belongs to a suspension that has moved on -- telling an agent which of those it hit is telling
    it how to search. So the two facts an operator needs travel in the type and in the record's
    own state, and a more helpful string here would reopen a disclosure the store closed on
    purpose.
    """
    database = tmp_path / "state.db"
    now = T0
    action = an_action("cont")

    holder = SQLiteStateStore(database, clock=lambda: now)
    holder.reserve_effect("refund:cont", action.action_id, LEASE)
    holder.begin_execution("refund:cont", action.action_id)
    holder.hold_continuation(action, "refund:cont", "tok-1", T0 + timedelta(hours=1))
    holder.close()  # the process is gone

    # 1. The row survives, and 2. a different process may finish it -- new in v0.6, and only
    #    because a shared store makes it possible.
    other = SQLiteStateStore(database, clock=lambda: now)
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
    lapsed_db = tmp_path / "lapsed.db"
    when = T0
    first = SQLiteStateStore(lapsed_db, clock=lambda: when)
    first.reserve_effect("refund:late", action.action_id, timedelta(seconds=30))
    first.begin_execution("refund:late", action.action_id)
    first.hold_continuation(action, "refund:late", "tok-2", T0 + timedelta(minutes=1))
    first.close()

    when = T0 + timedelta(hours=1)
    after = SQLiteStateStore(lapsed_db, clock=lambda: when)
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


# --- helpers ----------------------------------------------------------------------------------


def _store(tmp_path: Path, backend: str):
    if backend == "memory":
        return InMemoryStateStore(clock=lambda: T0)
    return SQLiteStateStore(tmp_path / "state.db", clock=lambda: T0)


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
