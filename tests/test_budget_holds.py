# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""T415 to T443: consumption, reconciliation and release (SPEC-v0.9 §4).

§4.2's table has nineteen rows and the implementation has one rule: **released exactly when the
effect reaches `FAILED`, held in every other state.** One test per disposition, because v0.8's
item 4 needed three attempts on the analogous lapsed-row case: its spec had ten rows and its code
met an eleventh.
"""

from __future__ import annotations

import contextvars
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ctrlrun.action import Action, Principal
from ctrlrun.authority import Authority
from ctrlrun.control import Control
from ctrlrun.effect import EffectState
from ctrlrun.errors import (
    ActionDenied,
    AmbiguousEffect,
    DuplicateEffect,
    InvalidArgument,
    NotExecuted,
)
from ctrlrun.policy import Policy
from ctrlrun.receipt import ReceiptResult
from ctrlrun.state import Charge, InMemoryStateStore, SQLiteStateStore

pytestmark = pytest.mark.authority

POSTGRES_URL = os.environ.get("CTRLRUN_TEST_POSTGRES")
NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
DAY = timedelta(hours=24)
LEASE = timedelta(minutes=5)
AGENT = Principal(agent="payer", user="ada")

DOC = """
schema: ctrlrun.policy/v7
environment: prod
actions:
  payments.refund:
    effect: "refund:{id}"
    decision: allow
authority:
  grants:
    - id: payer
      subject: {agent: "payer"}
      actions: ["payments.*"]
      budgets:
        - {metric: amount, limit: 250, window: PT24H}
"""


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

        schema = f"holds_{uuid.uuid4().hex[:12]}"
        PostgresStateStore.create_schema(POSTGRES_URL, schema)
        made = PostgresStateStore(POSTGRES_URL, schema=schema, clock=clock)
    yield made
    made.close()
    if request.param == "postgres":
        from ctrlrun.postgres import PostgresStateStore

        PostgresStateStore.drop_schema(POSTGRES_URL, schema)


def _control(store, clock) -> Control:
    return Control(
        policy=Policy.from_yaml(DOC, source="<d>"),
        store=store,
        clock=clock,
        environment="prod",
        authority=Authority.from_yaml(DOC, source="<d>"),
    )


def _action(identifier: str = "1", amount: int = 100) -> Action:
    return Action(
        name="payments.refund",
        arguments={"amount": amount, "id": identifier},
        principal=AGENT,
        environment="prod",
    )


def _held(store) -> int:
    return sum(row.amount for row in store.consumptions() if row.released_at is None)


def test_T415_commit_holds_permanently(store, clock) -> None:
    """§4.2 row 1. A committed spend is a spend."""
    control = _control(store, clock)
    control.execute(_action(), lambda: {"ok": True}, "refund:1")
    assert _held(store) == 100


def test_T416_fail_releases(store, clock) -> None:
    """§4.2 row 2. The executor proved nothing happened (`v0.1 §5.5`)."""
    control = _control(store, clock)
    with pytest.raises(NotExecuted):
        control.execute(
            _action(), lambda: (_ for _ in ()).throw(NotExecuted("nothing happened")), "refund:1"
        )
    assert _held(store) == 0, "a FAILED effect must release its charge"


def test_T417_ambiguity_holds(store, clock) -> None:
    """§4.2 row 3, and **R2: ambiguity is not a refund.**

    The correctness hole that parked budgets for four milestones. If ambiguity released the hold,
    an agent that can generate ambiguity could generate unlimited authority, and generating
    ambiguity is free for any flaky integration.
    """
    control = _control(store, clock)
    with pytest.raises(RuntimeError):
        control.execute(
            _action(), lambda: (_ for _ in ()).throw(RuntimeError("who knows")), "refund:1"
        )
    assert store.get_effect("refund:1").state is EffectState.AMBIGUOUS
    assert _held(store) == 100, "an AMBIGUOUS effect must keep its consumption"


def test_T418_a_lapsed_lease_holds(store, clock) -> None:
    """§4.2 row 4. No transition has occurred, so nothing is released."""
    store.reserve_effect("e1", "a", LEASE, (Charge("payer", "amount", 100, 250, DAY),))
    clock.advance(LEASE * 2)
    assert _held(store) == 100


def test_T419_a_human_resolving_FAILED_releases(store, clock) -> None:
    """§4.2's `resolve_effect(FAILED)` row, and the defect it caught.

    **`resolve_effect` does not go through `_transition`**, so the release had to be written on
    that path as well. Without it the one act meant to free a held charge, a human saying the
    effect did not happen, would have held it for ever, which is the exact opposite of R2's
    intent. Found when G22's scenario tried to free its own hold.
    """
    control = _control(store, clock)
    with pytest.raises(RuntimeError):
        control.execute(
            _action(), lambda: (_ for _ in ()).throw(RuntimeError("who knows")), "refund:1"
        )
    assert _held(store) == 100
    store.resolve_effect("refund:1", EffectState.FAILED, "ada@example.com")
    assert _held(store) == 0, "a human's FAILED must release, and this path bypasses _transition"


def test_T420_a_human_resolving_COMMITTED_holds(store, clock) -> None:
    """The other half of the same row: the human said it happened."""
    control = _control(store, clock)
    with pytest.raises(RuntimeError):
        control.execute(
            _action(), lambda: (_ for _ in ()).throw(RuntimeError("who knows")), "refund:1"
        )
    store.resolve_effect("refund:1", EffectState.COMMITTED, "ada@example.com")
    assert _held(store) == 100


def test_T421_the_release_is_idempotent(store, clock) -> None:
    """§4.4. A compare-and-set on the flag, never a decrement: `v0.6 §4.3.2` Table A2 row 2
    re-issues a lost `UPDATE` once, and a decrement would subtract twice."""
    store.reserve_effect("e1", "a", LEASE, (Charge("payer", "amount", 100, 250, DAY),))
    store.begin_execution("e1", "a")
    store.fail_effect("e1", "a", "nothing happened")
    first = next(row.released_at for row in store.consumptions())
    store.reserve_effect("e1", "a", LEASE, (Charge("payer", "amount", 100, 250, DAY),))
    store.begin_execution("e1", "a")
    store.fail_effect("e1", "a", "again")
    again = next(row.released_at for row in store.consumptions())
    assert again == first, "a re-issued release must not move a timestamp already set"


def test_T426_a_renewal_after_FAILED_charges_again(store, clock) -> None:
    """§4.3. `FAILED` is the only state that re-reserves one effect key, and it **should**
    charge again: the release already happened and the failure proves the first spend did not."""
    control = _control(store, clock)
    with pytest.raises(NotExecuted):
        control.execute(_action(), lambda: (_ for _ in ()).throw(NotExecuted("no")), "refund:1")
    assert _held(store) == 0
    control.execute(_action(), lambda: {"ok": True}, "refund:1")
    rows = store.consumptions()
    assert len(rows) == 2, "the renewal writes its own row"
    assert {row.attempt for row in rows} == {1, 2}, "distinct by attempt, per §3.4's key"
    assert _held(store) == 100


def test_T433_the_refusal_names_the_grant_the_metric_and_the_window(store, clock) -> None:
    """§4.5, and **not the remaining amount**, asserted by word.

    A refusal reporting the balance is an oracle: refused actions cost nothing, so an attacker
    binary-searches the exact limit in a few dozen refusals.
    """
    control = _control(store, clock)
    control.execute(_action("1", 250), lambda: {"ok": True}, "refund:1")
    with pytest.raises(ActionDenied) as caught:
        control.execute(_action("2", 1), lambda: {"ok": True}, "refund:2")
    assert caught.value.reason == "budget_exhausted"
    message = str(caught.value)
    assert "payer" in message and "amount" in message
    assert "250" not in message and "249" not in message, (
        f"the refusal discloses the balance, which is an oracle: {message}"
    )


def test_T437_a_budget_refusal_writes_no_approval_event(store, clock) -> None:
    """§3.3.2's hazard, which item 2 met first with the scope refusal.

    `_secure`'s `ActionDenied` handler appends `APPROVAL_DENIED` unconditionally, so a budget
    refusal routed through it fabricates an approval denial for an action no human ever saw.
    """
    control = _control(store, clock)
    control.execute(_action("1", 250), lambda: {"ok": True}, "refund:1")
    with pytest.raises(ActionDenied):
        control.execute(_action("2", 1), lambda: {"ok": True}, "refund:2")
    kinds = [event.type.value for event in store.events()]
    assert "APPROVAL_DENIED" not in kinds
    denied = [r for r in store.receipts() if r.result is ReceiptResult.DENIED]
    assert len(denied) == 1 and denied[0].decision_reason == "budget_exhausted"


def test_T440_a_negative_metric_value_is_refused(store, clock) -> None:
    """§2.3. A negative amount would reduce the rolling sum and refill the budget, which is the
    compensation §12 forbids. The test that proves it matters alternates `+n` and `-n`."""
    control = _control(store, clock)
    control.execute(_action("1", 250), lambda: {"ok": True}, "refund:1")
    with pytest.raises(InvalidArgument):
        control.execute(_action("2", -250), lambda: {"ok": True}, "refund:2")
    assert _held(store) == 250, "a refused negative must not have moved the sum"


def test_T441_an_action_with_no_metric_argument_is_refused(store, clock) -> None:
    """§2.3: a missing value is refused, never counted as zero. Treating absence as zero turns
    the absence of a field into unlimited authority."""
    control = _control(store, clock)
    action = Action(
        name="payments.refund", arguments={"id": "1"}, principal=AGENT, environment="prod"
    )
    with pytest.raises(InvalidArgument) as caught:
        control.execute(action, lambda: {"ok": True}, "refund:1")
    # **The message, not just the type.** A mutation run found this guard removable: with it
    # gone, `None` falls through to the non-integer check and raises anyway, so the test passed
    # for a reason that was not this rule. That is CONTRIBUTING.md's first pattern, a subsumed
    # guard, and the list allows keeping one for its message on the condition a test asserts it.
    assert "carries no 'amount' argument" in str(caught.value), str(caught.value)


def test_T442_a_budgeted_grant_refuses_an_action_with_no_effect_key(store, clock) -> None:
    """§2.4.1, in the third place this check has lived and the one the probes point at.

    Without it an agent proposes actions carrying no `effect:` template and spends nothing
    against every budget on the chain, for ever.
    """
    control = _control(store, clock)
    with pytest.raises(InvalidArgument) as caught:
        control.execute(_action(), lambda: {"ok": True}, None)
    assert "effect" in str(caught.value)
    assert store.consumptions() == ()


def test_T412b_every_ancestor_is_charged_through_a_real_chain(store, clock) -> None:
    """§2.7, driven end to end rather than at the store.

    **The rule that makes the feature mean anything**, and a mutation run found nothing exercising
    it through `Control`: removing the ancestor walk left 84 tests green. Without it a holder of a
    250-a-day grant delegates children, each correctly contained, and every child spends the
    parent's budget over again.
    """
    from ctrlrun.authority import Grant, Subject

    delegable = DOC.replace(
        "        - {metric: amount, limit: 250, window: PT24H}",
        "        - {metric: amount, limit: 250, window: PT24H}\n"
        "      delegable: true\n"
        '      expires_at: "2027-01-01T00:00:00Z"',
    )
    control = Control(
        policy=Policy.from_yaml(delegable, source="<d>"),
        store=store,
        clock=clock,
        environment="prod",
        authority=Authority.from_yaml(delegable, source="<d>"),
    )
    child = Grant(
        id="",
        subject=Subject(agent="payer", user="ada"),
        actions=("payments.refund",),
        expires_at=datetime(2026, 12, 1, tzinfo=UTC),
        budgets=((control.authority.grants["payer"].budgets or ())[0],),
    )
    delegation = control.delegate("payer", child, by=AGENT)

    control.execute(_action("1", 100), lambda: {"ok": True}, "refund:1")
    charged = {row.grant_id for row in store.consumptions()}
    assert charged == {"payer", delegation.delegation_id}, (
        f"every ancestor must be charged, not only the grant that decided: {charged}"
    )
    # And the parent's budget is what refuses, even though the child is within its own.
    with pytest.raises(ActionDenied) as caught:
        control.execute(_action("2", 200), lambda: {"ok": True}, "refund:2")
    assert caught.value.reason == "budget_exhausted"


def test_T443_a_refused_receipt_records_no_charge(store, clock) -> None:
    """SPEC-v0.9 §10.1, and an independent review found the receipt lying.

    `budget_charges` was stamped where the charges were computed, which is before `_take`
    attempts the transaction that applies them. Every refusal raised later in `_secure`'s loop
    then reached `_record` with them set, so a `denied` receipt claimed the action charged the
    very grant it was refused from spending against.

    **A receipt asserting a spend that never happened is the one thing an evidence trail may not
    do**, and it is worse than an absent field, because a reader has no way to tell it apart from
    a real one.
    """
    control = _control(store, clock)
    control.execute(_action("1", 250), lambda: {"ok": True}, "refund:1")
    with pytest.raises(ActionDenied):
        control.execute(_action("2", 100), lambda: {"ok": True}, "refund:2")

    committed = [r for r in store.receipts() if r.result is ReceiptResult.COMMITTED]
    denied = [r for r in store.receipts() if r.result is ReceiptResult.DENIED]
    assert [dict(c) for c in committed[0].budget_charges] == [
        {"grant_id": "payer", "metric": "amount", "amount": 250}
    ]
    assert denied[0].budget_charges == (), (
        "a refused action charged nothing; its receipt must not say otherwise"
    )


def test_T406a_two_budgets_on_one_metric_is_the_shape_SS2_2_exists_for(store, clock) -> None:
    """§2.2's own motivating shape, which an earlier duplicate guard killed at execute.

    "Two budgets on one metric over two windows is the first thing an operator asks for." It
    arrives as two charges differing only in `limit` and `window`: both predicates run, §3.4's
    key writes **one** row, because it is one spend measured against two windows.

    An independent review found the previous guard refusing any duplicate pair, so the loader
    accepted the document, observe mode reported it clean, `ctrlrun verify` could not grade it,
    and enforce mode died with no receipt and no event.
    """
    text = DOC.replace(
        "        - {metric: amount, limit: 250, window: PT24H}",
        "        - {metric: amount, limit: 250, window: PT24H}\n"
        "        - {metric: amount, limit: 5000, window: P30D}",
    )
    control = Control(
        policy=Policy.from_yaml(text, source="<two>"),
        store=store,
        clock=clock,
        environment="prod",
        authority=Authority.from_yaml(text, source="<two>"),
    )
    control.execute(_action("1", 100), lambda: {"ok": True}, "refund:1")
    control.execute(_action("2", 100), lambda: {"ok": True}, "refund:2")
    assert len(store.consumptions()) == 2, "one row per spend, not one per budget"
    # The daily budget binds first, and its window is the one named.
    with pytest.raises(ActionDenied) as caught:
        control.execute(_action("3", 100), lambda: {"ok": True}, "refund:3")
    assert caught.value.reason == "budget_exhausted"
    # And the monthly one still holds after the daily window rolls.
    clock.advance(DAY + timedelta(seconds=1))
    for index in range(3, 31):
        try:
            control.execute(_action(str(index), 100), lambda: {"ok": True}, f"refund:{index}")
        except ActionDenied:
            break
        clock.advance(DAY + timedelta(seconds=1))
    held = sum(row.amount for row in store.consumptions() if row.released_at is None)
    assert held <= 5000, f"the monthly budget was exceeded: {held}"


def test_T406b_two_charges_on_one_metric_with_different_amounts_are_refused(store, clock) -> None:
    """The hazard the guard is actually for: §3.4's key carries no window, so differing amounts
    would collapse to whichever row landed first and the ledger would under-record the spend."""
    with pytest.raises(InvalidArgument):
        store.reserve_effect(
            "e1",
            "a",
            LEASE,
            (
                Charge("payer", "amount", 100, 250, DAY),
                Charge("payer", "amount", 900, 5000, timedelta(days=30)),
            ),
        )
    assert store.consumptions() == ()


# --- §4.2's rows that had no test, and the mutant that survived without them ------------------


def test_T425_the_release_is_keyed_on_the_state_reached_not_the_call(store, clock) -> None:
    """**§4.2's warning paragraph, and the mutant that survived the whole suite without it.**

    An independent review moved `_release_locked` above the state check, so the release keyed on
    the *call* rather than the state reached, and all 42 tests passed. It is not an equivalent
    mutant: a `fail_effect` that is **refused** because the record moved on then releases the hold
    on an `AMBIGUOUS` record, which is the manufacturable refund this whole item exists to stop.

    "The release is keyed on the record reaching `FAILED`, never on the call that tried to put it
    there."

    The mutant only bites in memory. Both SQL stores run the transition inside one transaction and
    roll it back when the check raises, so there the order is equivalent and the rollback carries
    §4.1. The in-memory store mutates a dict under a lock and has no rollback, so the order **is**
    the atomicity. The test runs on all three anyway: which backend enforces §4.1 by which
    mechanism is an implementation detail, and the guarantee is not.
    """
    from ctrlrun.errors import AmbiguousEffect

    store.reserve_effect("e1", "a", LEASE, (Charge("payer", "amount", 100, 250, DAY),))
    store.begin_execution("e1", "a")
    # The record moves on under the attempt: a human, or another process, declares it ambiguous.
    store.mark_ambiguous("e1", "a", "the outcome is unknown")
    assert _held(store) == 100

    with pytest.raises(AmbiguousEffect):
        store.fail_effect("e1", "a", "the executor says it did not happen")

    assert _held(store) == 100, (
        "a REFUSED fail_effect released the hold: the release is keyed on the call, not the "
        "state reached, and somebody may have committed this effect"
    )


def test_T423_a_suspension_holds_its_charge(store, clock) -> None:
    """§4.2's suspension row. A continuation extends the lease; no transition, so no release.

    A suspension is the one state that can outlive a whole budget window, so "held in every other
    state" is load-bearing here: an elicitation that sits for a day must not let the same grant
    spend its daily limit twice.
    """
    action = _action()
    store.reserve_effect("e1", action.action_id, LEASE, (Charge("payer", "amount", 100, 250, DAY),))
    store.begin_execution("e1", action.action_id)
    store.hold_continuation(action, "e1", "cont-1", clock.now + timedelta(hours=1))
    assert _held(store) == 100
    clock.advance(DAY + timedelta(seconds=1))
    assert _held(store) == 100, "a suspension outliving its window must still hold its charge"


def test_T424_begin_execution_moves_nothing_in_the_ledger(store, clock) -> None:
    """§4.2's `begin_execution` row. Listed because the table claims completeness."""
    store.reserve_effect("e1", "a", LEASE, (Charge("payer", "amount", 100, 250, DAY),))
    before = [(row.effect_key, row.released_at) for row in store.consumptions()]
    store.begin_execution("e1", "a")
    assert [(row.effect_key, row.released_at) for row in store.consumptions()] == before


def test_T422_a_lapsed_lease_another_planner_ambiguates_still_holds(store, clock) -> None:
    """§4.2's row 5. The record is `AMBIGUOUS` now, and R2 applies: the charge stays."""
    store.reserve_effect("e1", "a", LEASE, (Charge("payer", "amount", 100, 250, DAY),))
    clock.advance(LEASE * 2)
    with pytest.raises(AmbiguousEffect):
        store.reserve_effect("e1", "b", LEASE, (Charge("payer", "amount", 100, 250, DAY),))
    assert store.get_effect("e1").state is EffectState.AMBIGUOUS
    assert _held(store) == 100


def test_T427_a_refused_commit_releases_nothing(store, clock) -> None:
    """§4.2's `commit_effect` refused row: §4.1 over the state actually reached."""
    from ctrlrun.errors import AmbiguousEffect

    store.reserve_effect("e1", "a", LEASE, (Charge("payer", "amount", 100, 250, DAY),))
    store.begin_execution("e1", "a")
    store.mark_ambiguous("e1", "a", "unknown")
    with pytest.raises(AmbiguousEffect):
        store.commit_effect("e1", "a", {"ok": True})
    assert _held(store) == 100


def test_T428_a_human_resolving_FAILED_mid_flight_releases_and_the_call_does_not(
    store, clock
) -> None:
    """The sub-case the review named: a human resolves `FAILED` while an attempt runs, so the
    charge is **already released** and the refused call releases nothing further."""
    from ctrlrun.errors import CTRLRunError

    store.reserve_effect("e1", "a", LEASE, (Charge("payer", "amount", 100, 250, DAY),))
    store.begin_execution("e1", "a")
    store.mark_ambiguous("e1", "a", "unknown")
    store.resolve_effect("e1", EffectState.FAILED, "ada@example.com")
    assert _held(store) == 0
    released = [row.released_at for row in store.consumptions()]
    with pytest.raises(CTRLRunError):
        store.fail_effect("e1", "a", "the executor says so too")
    assert [row.released_at for row in store.consumptions()] == released


# --- §4.2's rows that only the Control route can reach ---------------------------------------

CEILING_DOC = DOC.replace(
    "    decision: allow", "    decision: allow\n    max_attempts: 3"
).replace("limit: 250", "limit: 1000")


def _ceiling_control(store, clock) -> Control:
    return Control(
        policy=Policy.from_yaml(CEILING_DOC, source="<c>"),
        store=store,
        clock=clock,
        environment="prod",
        authority=Authority.from_yaml(CEILING_DOC, source="<c>"),
    )


def _boom() -> Any:
    raise NotExecuted("the remote rejected it before doing anything")


def test_T429_the_ceiling_refusing_after_the_reservation_was_won_releases(store, clock) -> None:
    """§4.2's ceiling row, and three others on the way to it.

    **This is the shape v0.8's item 4 missed.** The kernel wins the reservation, charges for it,
    then refuses on its own attempt ceiling and drives `begin_execution` + `fail_effect` itself.
    The executor never ran, so by §4.1 the charge is released, and the release is driven by the
    kernel rather than by any outcome.

    The route also covers the `reconcile` hook row (the hook moves the record to `FAILED`, which
    releases the first charge like a human's `resolve_effect(FAILED)`) and the second-`_take` row
    (the renewal takes a **fresh** charge, per §4.3).
    """
    control = _ceiling_control(store, clock)
    for _ in range(2):
        with pytest.raises(NotExecuted):
            control.execute(_action(), _boom, "refund:1")
    with pytest.raises(TimeoutError):
        control.execute(_action(), lambda: (_ for _ in ()).throw(TimeoutError("lost")), "refund:1")
    assert store.get_effect("refund:1").state is EffectState.AMBIGUOUS
    assert _held(store) == 100, "R2: the ambiguous attempt's charge is held"

    with pytest.raises(ActionDenied) as refused:
        control.execute(_action(), _boom, "refund:1", reconcile=lambda key: "not_executed")
    assert refused.value.reason == "attempt_ceiling"
    assert store.get_effect("refund:1").state is EffectState.FAILED
    # The hook released the ambiguous charge; the renewal took a fresh one; the kernel's own
    # fail_effect released that one too. Every row is charged, and every row is released.
    assert _held(store) == 0
    assert len(store.consumptions()) == 4, "three attempts plus the renewal, each charged once"


def test_T430_begin_execution_refused_after_the_reservation_was_won_holds(store, clock) -> None:
    """§4.2's `begin_execution`-refused row: the reservation is won and charged, then taken away.

    Mechanically the lapsed-lease row, but a distinct call path: the kernel holds a reservation it
    can no longer execute against. The charge is **held**, by the ambiguity rule, because nobody
    can say the effect did not happen.
    """
    control = _control(store, clock)
    taken: list[str] = []

    def steal() -> Any:  # pragma: no cover - never reached
        raise AssertionError("the executor must not run")

    original = store.begin_execution

    def refuse(effect_key: str, action_id: str) -> Any:
        taken.append(effect_key)
        store.mark_ambiguous(effect_key, action_id, "another process got there first")
        return original(effect_key, action_id)

    store.begin_execution = refuse  # type: ignore[method-assign]
    with pytest.raises(AmbiguousEffect):
        control.execute(_action(), steal, "refund:1")
    assert taken == ["refund:1"]
    assert _held(store) == 100, "a reservation taken away is ambiguous, and R2 holds the charge"


def test_T431_mark_ambiguous_refused_moves_nothing(store, clock) -> None:
    """§4.2's `mark_ambiguous`-refused row. It folds the refusal into the error text rather than
    calling `_unrecorded`, so §4.1 applies over the state the record actually reached."""
    store.reserve_effect("e1", "a", LEASE, (Charge("payer", "amount", 100, 250, DAY),))
    store.begin_execution("e1", "a")
    store.commit_effect("e1", "a", {"ok": True})
    released = [row.released_at for row in store.consumptions()]
    with pytest.raises(DuplicateEffect):
        store.mark_ambiguous("e1", "a", "too late")
    assert [row.released_at for row in store.consumptions()] == released
    assert _held(store) == 100, "the record reached COMMITTED, and a committed spend is a spend"


def test_T432_a_refused_retry_charges_nothing(store, clock) -> None:
    """§4.2's last row. The refusal happens in `plan_reservation`, **before** any reservation is
    won, so there is nothing to charge and nothing to release.

    The retry is refused rather than answered from the record: `DuplicateEffect` is the kernel
    telling the caller the effect already happened, which is the point. What matters to §4.2 is
    that the second call leaves the ledger exactly as the first left it.
    """
    from ctrlrun.errors import DuplicateEffect

    control = _control(store, clock)
    control.execute(_action(), lambda: {"ok": True}, "refund:1")
    before = [(row.effect_key, row.amount, row.released_at) for row in store.consumptions()]
    with pytest.raises(DuplicateEffect):
        control.execute(_action(), lambda: {"ok": True}, "refund:1")
    after = [(row.effect_key, row.amount, row.released_at) for row in store.consumptions()]
    assert after == before, "a refused retry is not a second spend"
    assert _held(store) == 100


# --- §2.3 and §2.4.1 refuse, and a refusal is a thing the operator can see --------------------

APPROVE_DOC = DOC.replace("decision: allow", "decision: approve")


def _approving_control(store, clock):
    from ctrlrun.approval import LocalApprovalProvider

    return Control(
        policy=Policy.from_yaml(APPROVE_DOC, source="<a>"),
        store=store,
        approvals=LocalApprovalProvider(store, clock=clock, poll_interval=timedelta(0)),
        clock=clock,
        environment="prod",
        authority=Authority.from_yaml(APPROVE_DOC, source="<a>"),
    )


def test_T444_an_unmeasurable_action_is_refused_with_an_event_and_a_receipt(store, clock) -> None:
    """§2.3 and §2.4.1 refuse. **A refusal nobody can see is not a refusal.**

    A review found these three escaping as bare `InvalidArgument` with no `ACTION_DENIED` and no
    receipt, which leaves the one record an operator has of a refused action empty. The exception
    type stays what §2.3 says it is: this is an argument the kernel cannot measure, not a budget
    that ran out.
    """
    control = _control(store, clock)
    for arguments, fragment in (
        ({"amount": -250, "id": "1"}, "-250"),
        ({"id": "1"}, "carries no 'amount' argument"),
    ):
        before = len(store.events())
        with pytest.raises(InvalidArgument) as caught:
            control.execute(
                Action(
                    name="payments.refund",
                    arguments=arguments,
                    principal=AGENT,
                    environment="prod",
                ),
                lambda: {"ok": True},
                "refund:1",
            )
        assert fragment in str(caught.value)
        written = [str(event.type) for event in store.events()][before:]
        assert "ACTION_DENIED" in written, written
        receipt = store.receipts()[-1]
        assert receipt.result is ReceiptResult.DENIED
        assert receipt.decision_reason == "budget_unmeasurable", receipt.decision_reason
    assert store.consumptions() == (), "nothing was charged for any of them"


def test_T445_a_keyless_budgeted_action_is_refused_with_an_event_and_a_receipt(
    store, clock
) -> None:
    """§2.4.1's refusal, given the same treatment. Its reason is distinct from §2.3's because an
    operator who declared a budget on an action with no `effect:` template has a different thing
    to fix than one whose agent proposed a negative amount."""
    control = _control(store, clock)
    with pytest.raises(InvalidArgument):
        control.execute(_action(), lambda: {"ok": True}, None)
    assert "ACTION_DENIED" in [str(event.type) for event in store.events()]
    receipt = store.receipts()[-1]
    assert receipt.result is ReceiptResult.DENIED
    assert receipt.decision_reason == "budget_unkeyed", receipt.decision_reason


def test_T446_no_human_is_asked_to_approve_an_action_the_kernel_will_refuse(store, clock) -> None:
    """**The ordering half.** §2.3's and §2.4.1's refusals do not depend on anything the approval
    gate produces, and they are unconditional: the action can never run, whatever a human says.

    Running them after the gate asks a human to sit and approve a refund the kernel has already
    decided to refuse, and leaves a granted approval in the store for an action nothing can
    execute. A probe found `APPROVAL_REQUESTED` written for exactly that shape.
    """
    control = _approving_control(store, clock)
    action = Action(
        name="payments.refund",
        arguments={"amount": -250, "id": "1"},
        principal=AGENT,
        environment="prod",
    )
    with pytest.raises(InvalidArgument):
        control.execute(action, lambda: {"ok": True}, "refund:1")
    written = [str(event.type) for event in store.events()]
    assert "APPROVAL_REQUESTED" not in written, written
    assert "ACTION_DENIED" in written, written


# --- §4.2's resumed leg: the only receipt an MCP or ACS action ever gets ----------------------


def test_T447_a_resumed_leg_reports_the_charges_its_first_leg_took(store, clock) -> None:
    """§8.3. **The resumed leg's receipt is the whole evidence for that action**, so it has to
    say what the action spent.

    `resume` never touched `_BUDGET_CHARGES`, so the field came from whatever the contextvar
    happened to hold. A resumption in a fresh context reported no charges at all for an action
    that had spent 100; a resumption after another `execute` in the same context reported *that
    action's* spend. Both put a false number on the only receipt there is.
    """
    from ctrlrun import Suspended

    control = _control(store, clock)

    def suspends() -> Any:
        raise Suspended("round-1")

    with pytest.raises(Suspended):
        control.execute(_action("1", 100), suspends, "refund:1")
    assert _held(store) == 100

    # **In a fresh context**, which is the shape a continuation exists for: `hold_continuation`
    # carries the whole `Action` "because a resumption is *the same action*, and rehydrating it
    # from the store is the only way a gateway that restarted mid-round can still finish one."
    # A restarted gateway has no contextvar left, so reading one is reading nothing.
    receipt = contextvars.Context().run(control.resume, "round-1", lambda: {"ok": True})
    assert receipt.budget_charges == ({"grant_id": "payer", "metric": "amount", "amount": 100},), (
        receipt.budget_charges
    )


def test_T448_a_resumed_leg_never_reports_another_actions_charges(store, clock) -> None:
    """The stale half, which is the one that puts a *wrong* number on a receipt rather than a
    missing one. One `Control`, one thread, two actions: the second must not inherit the first."""
    from ctrlrun import Suspended

    control = _control(store, clock)

    def suspends() -> Any:
        raise Suspended("round-1")

    with pytest.raises(Suspended):
        control.execute(_action("1", 100), suspends, "refund:1")
    # An unbudgeted action runs to completion in the same context, leaving its own charges set.
    control.execute(_action("2", 25), lambda: {"ok": True}, "refund:2")

    receipt = control.resume("round-1", lambda: {"ok": True})
    amounts = [charge["amount"] for charge in receipt.budget_charges]
    assert amounts == [100], f"the resumed leg inherited the other action's spend: {amounts}"


def test_T449_an_unbudgeted_resumed_leg_reports_no_charges(store, clock) -> None:
    """The other direction: a resumption must not manufacture charges either. A store with a
    ledger and an action with no budget on its grant reports an empty tuple, not the last thing
    the contextvar saw."""
    from ctrlrun import Suspended

    unbudgeted = DOC.replace(
        "      budgets:\n        - {metric: amount, limit: 250, window: PT24H}\n", ""
    )
    control = Control(
        policy=Policy.from_yaml(unbudgeted, source="<u>"),
        store=store,
        clock=clock,
        environment="prod",
        authority=Authority.from_yaml(unbudgeted, source="<u>"),
    )

    def suspends() -> Any:
        raise Suspended("round-1")

    with pytest.raises(Suspended):
        control.execute(_action("1", 100), suspends, "refund:1")
    receipt = control.resume("round-1", lambda: {"ok": True})
    assert receipt.budget_charges == ()


def test_T450_a_resumed_leg_after_a_renewal_reports_only_its_own_attempts_charges(
    store, clock
) -> None:
    """§4.3 gives a renewal a **new** charge, so an effect that failed and renewed has two rows
    in the ledger. The resumed receipt reports the attempt it is actually on.

    Without the attempt filter the receipt sums a spend that was already released with the one
    the action is holding, and claims the action spent twice what it did.
    """
    from ctrlrun import Suspended

    control = _control(store, clock)
    with pytest.raises(NotExecuted):
        control.execute(_action("1", 100), _boom, "refund:1")
    assert _held(store) == 0, "§4.2: the failed attempt released its charge"

    def suspends() -> Any:
        raise Suspended("round-1")

    with pytest.raises(Suspended):
        control.execute(_action("1", 100), suspends, "refund:1")
    assert len(store.consumptions()) == 2, "one row per attempt, per §4.3"

    receipt = contextvars.Context().run(control.resume, "round-1", lambda: {"ok": True})
    assert receipt.budget_charges == ({"grant_id": "payer", "metric": "amount", "amount": 100},), (
        receipt.budget_charges
    )
    assert receipt.attempt == 2, receipt.attempt


# --- §4.2.1: observe mode charges nothing, and says what would have been refused --------------

OBSERVE_DOC = DOC.replace("environment: prod", "environment: prod\nmode: observe")


def _observing_control(store, clock) -> Control:
    return Control(
        policy=Policy.from_yaml(OBSERVE_DOC, source="<o>"),
        store=store,
        clock=clock,
        environment="prod",
        authority=Authority.from_yaml(OBSERVE_DOC, source="<o>"),
    )


def test_T439_observe_mode_charges_nothing(store, clock) -> None:
    """§4.2.1, first half. `v0.3 §6.2`'s observe mode enforces nothing and records what it would
    have done. A budget consumed there would be the one check in the kernel that enforced under
    observation: the run would refuse at the limit while claiming to be observing, and the
    counterfactual an operator adopts observe mode to get would be wrong.
    """
    control = _observing_control(store, clock)
    for index in range(1, 6):
        receipt = control.execute(_action(str(index), 100), lambda: {"ok": True}, f"refund:{index}")
        assert receipt.result is ReceiptResult.OBSERVED
    assert store.consumptions() == (), "observe mode wrote to the ledger"


def test_T439a_observe_mode_reports_the_budget_that_would_have_refused(store, clock) -> None:
    """§4.2.1, second half, and the half that did not exist. The report says the action *would
    have been* refused on a budget, naming the grant and the metric, exactly as it reports what a
    policy would have decided.

    The shape that reaches it is a **mixed** deployment: the ledger carries enforced spend, and a
    new action is being piloted in observe mode against the same grant. §4.2.1a says why a
    deployment observing everything reports nothing here.
    """
    enforcing = _control(store, clock)
    enforcing.execute(_action("1", 250), lambda: {"ok": True}, "refund:1")

    observing = _observing_control(store, clock)
    receipt = observing.execute(_action("2", 100), lambda: {"ok": True}, "refund:2")

    assert receipt.result is ReceiptResult.OBSERVED, "it ran: observe mode refuses nothing"
    assert receipt.would_have is not None
    assert receipt.would_have.blocked_reason == "budget_exhausted", receipt.would_have
    assert store.consumptions() == store.consumptions(grant_id="payer")
    assert len(store.consumptions()) == 1, "the observed run charged nothing of its own"


def test_T439b_observe_mode_reports_nothing_when_the_budget_has_room(store, clock) -> None:
    """The negative. Without it T439a passes for a `block` that fires unconditionally."""
    enforcing = _control(store, clock)
    enforcing.execute(_action("1", 100), lambda: {"ok": True}, "refund:1")

    observing = _observing_control(store, clock)
    receipt = observing.execute(_action("2", 100), lambda: {"ok": True}, "refund:2")
    blocked = receipt.would_have.blocked_reason if receipt.would_have else None
    assert blocked != "budget_exhausted", blocked


def test_T439c_an_unmeasurable_action_under_observation_reports_and_denies_nothing(
    store, clock
) -> None:
    """§2.3 and §2.4.1 under `v0.3 §6.2`. Enforce mode refuses these, so observe mode's job is to
    say so, and its job is equally to write no `denied` receipt while doing it.

    Routing them through `_refuse_unmeasurable` unguarded would have observe mode record a
    refusal it did not make, on top of the `observed` receipt for the run that went ahead: two
    receipts for one action, disagreeing.
    """
    control = _observing_control(store, clock)
    receipt = control.execute(
        Action(
            name="payments.refund",
            arguments={"amount": -250, "id": "1"},
            principal=AGENT,
            environment="prod",
        ),
        lambda: {"ok": True},
        "refund:1",
    )
    assert receipt.result is ReceiptResult.OBSERVED, "observe mode refuses nothing"
    assert receipt.would_have is not None
    assert receipt.would_have.blocked_reason == "budget_unmeasurable", receipt.would_have
    assert [r.result for r in store.receipts()] == [ReceiptResult.OBSERVED], store.receipts()
    assert store.consumptions() == ()


def test_T439d_an_observed_receipt_carries_the_charge_it_would_have_taken(store, clock) -> None:
    """§4.2.1a. The counterfactual spend, which is the number a budget is sized from.

    A probe found observed receipts carrying an empty tuple, which made §4.2.1a's sizing path
    impossible: the ledger is empty under observation, so if the receipts do not carry what the
    action would have been charged, nothing anywhere records it.

    It is not a claim that anything was spent. The receipt says `observed`, the ledger is empty,
    and `v0.3 §6.2` makes every number on an observed receipt a counterfactual.
    """
    control = _observing_control(store, clock)
    receipt = control.execute(_action("1", 100), lambda: {"ok": True}, "refund:1")
    assert receipt.result is ReceiptResult.OBSERVED
    assert receipt.budget_charges == ({"grant_id": "payer", "metric": "amount", "amount": 100},), (
        receipt.budget_charges
    )
    assert store.consumptions() == (), "the counterfactual is on the receipt, not in the ledger"


def test_T439e_an_observed_receipt_for_an_unbudgeted_grant_carries_none(store, clock) -> None:
    """The negative, so T439d cannot pass for a field that is always populated."""
    unbudgeted = OBSERVE_DOC.replace(
        "      budgets:\n        - {metric: amount, limit: 250, window: PT24H}\n", ""
    )
    control = Control(
        policy=Policy.from_yaml(unbudgeted, source="<u>"),
        store=store,
        clock=clock,
        environment="prod",
        authority=Authority.from_yaml(unbudgeted, source="<u>"),
    )
    receipt = control.execute(_action("1", 100), lambda: {"ok": True}, "refund:1")
    assert receipt.budget_charges == ()


# --- §4.2.1: the report and the enforcement may not drift on *which* refusal ------------------


def _both_modes(store, clock, document):
    """One document, two Controls over separate stores: what enforce did, what observe said."""
    enforcing = Control(
        policy=Policy.from_yaml(document, source="<e>"),
        store=store,
        clock=clock,
        environment="prod",
        authority=Authority.from_yaml(document, source="<e>"),
    )
    observed = document.replace("environment: prod", "environment: prod\nmode: observe")
    watcher = InMemoryStateStore(clock=clock)
    observing = Control(
        policy=Policy.from_yaml(observed, source="<o>"),
        store=watcher,
        clock=clock,
        environment="prod",
        authority=Authority.from_yaml(observed, source="<o>"),
    )
    return enforcing, observing, watcher


def test_T451_observe_reports_the_refusal_enforce_makes_when_a_human_would_be_asked(
    store, clock
) -> None:
    """§4.2.1: "the report and the enforcement cannot drift."

    T446 moved §2.3's refusal above the approval gate in enforce mode, because the action cannot
    run whatever a human says. Observe mode's copy stayed below it, so a pilot was told a human
    would have been asked about an action enforce refuses before anybody is asked. That is the
    exact defect T446 fixed, surviving on the other side of the mode switch, and an independent
    review found it.
    """
    document = DOC.replace("decision: allow", "decision: approve")
    enforcing, observing, watcher = _both_modes(store, clock, document)
    bad = Action(
        name="payments.refund",
        arguments={"amount": -250, "id": "1"},
        principal=AGENT,
        environment="prod",
    )

    with pytest.raises(InvalidArgument):
        enforcing.execute(bad, lambda: {"ok": True}, "refund:1")
    enforced = store.receipts()[-1].decision_reason

    receipt = observing.execute(bad, lambda: {"ok": True}, "refund:1")

    assert enforced == "budget_unmeasurable"
    assert receipt.would_have is not None
    assert receipt.would_have.blocked_reason == enforced, (
        f"enforce refused {enforced!r} and the pilot was told {receipt.would_have.blocked_reason!r}"
    )
    assert "APPROVAL_REQUESTED" not in [str(e.type) for e in watcher.events()]


def test_T452_observe_reports_the_duplicate_enforce_raises_rather_than_the_budget(
    store, clock
) -> None:
    """The other direction of the same rule, and the one that says where the check belongs.

    The store decides `plan_reservation` **before** `check_charges` (§3.3), so an effect that is
    already committed raises `DuplicateEffect` and the budget is never consulted. Observe mode
    evaluated the budget first and reported `budget_exhausted` for an action enforce mode refuses
    as a duplicate: the operator is told to raise a limit when the real answer is that the effect
    already happened.
    """
    from ctrlrun.errors import DuplicateEffect

    enforcing, observing, watcher = _both_modes(store, clock, DOC)
    # Fill the budget and commit the key, in both stores, so both refusals are live at once.
    for control, into in ((enforcing, store), (observing, watcher)):
        control.execute(_action("1", 250), lambda: {"ok": True}, "refund:1")
        assert into.get_effect("refund:1").state is EffectState.COMMITTED

    with pytest.raises(DuplicateEffect):
        enforcing.execute(_action("1", 250), lambda: {"ok": True}, "refund:1")

    receipt = observing.execute(_action("1", 250), lambda: {"ok": True}, "refund:1")

    assert receipt.would_have is not None
    assert receipt.would_have.blocked_reason == "duplicate", (
        "the pilot was told the budget refused an action enforce mode refuses as a duplicate: "
        f"{receipt.would_have.blocked_reason!r}"
    )


def test_T453_the_observed_sum_uses_the_same_window_the_kernel_decides_on(store, clock) -> None:
    """§2.5 through the observe report. A mutation run found `since=now - charge.window` removable
    with the whole suite green: the report summed the entire ledger and nothing noticed.

    A pilot whose report counts spend the kernel has already forgotten says a budget would refuse
    an action the kernel permits, which is the drift §4.2.1 exists to prevent, pointing the other
    way.
    """
    enforcing = _control(store, clock)
    enforcing.execute(_action("1", 250), lambda: {"ok": True}, "refund:1")
    clock.advance(DAY + timedelta(seconds=1))

    observing = _observing_control(store, clock)
    receipt = observing.execute(_action("2", 250), lambda: {"ok": True}, "refund:2")

    blocked = receipt.would_have.blocked_reason if receipt.would_have else None
    assert blocked != "budget_exhausted", (
        "the report counted a spend that has rolled out of the window, which the kernel would "
        "not have counted"
    )


def test_T454_the_observed_sum_ignores_released_rows_as_the_stores_do(store, clock) -> None:
    """§4.4 through the observe report, and the second mutation that survived: dropping
    `released_at is None` left every test green.

    All three stores' `_spent` excludes released rows, so a report that counts them diverges from
    the decision by exactly the amount a human has already cleared. An operator who resolves an
    effect `FAILED` and watches the pilot still claim the budget is exhausted has been told the
    resolution did nothing.
    """
    enforcing = _control(store, clock)
    enforcing.execute(_action("1", 100), lambda: {"ok": True}, "refund:1")
    with pytest.raises(NotExecuted):
        enforcing.execute(_action("2", 100), _boom, "refund:2")
    assert _held(store) == 100, "the failed attempt released its charge"
    assert len(store.consumptions()) == 2, "and the released row is still in the ledger"

    observing = _observing_control(store, clock)
    receipt = observing.execute(_action("3", 100), lambda: {"ok": True}, "refund:3")

    # Counting the released row makes the sum 300 against a limit of 250, and the report would
    # block. Excluding it, as every store's `_spent` does, makes it 200 and it does not.
    blocked = receipt.would_have.blocked_reason if receipt.would_have else None
    assert blocked != "budget_exhausted", "the report counted a released charge"


def test_T455_the_observed_sum_is_per_grant(store, clock) -> None:
    """The third: `grant_id=charge.grant_id` was removable because every test had one grant."""
    from ctrlrun.state import Charge as _Charge

    store.reserve_effect(
        "other:1", "act_other", LEASE, (_Charge("somebody-else", "amount", 250, 250, DAY),)
    )

    observing = _observing_control(store, clock)
    receipt = observing.execute(_action("1", 250), lambda: {"ok": True}, "refund:1")

    blocked = receipt.would_have.blocked_reason if receipt.would_have else None
    assert blocked != "budget_exhausted", "another grant's spend was counted against this one"


def test_T456_the_observed_sum_does_report_a_budget_this_grant_really_exhausted(
    store, clock
) -> None:
    """The positive control for the three above. Without it each of them passes against a report
    that never blocks at all, which is CONTRIBUTING.md's third pattern."""
    enforcing = _control(store, clock)
    enforcing.execute(_action("1", 250), lambda: {"ok": True}, "refund:1")

    observing = _observing_control(store, clock)
    receipt = observing.execute(_action("2", 250), lambda: {"ok": True}, "refund:2")

    assert receipt.would_have is not None
    assert receipt.would_have.blocked_reason == "budget_exhausted", receipt.would_have


def test_T457_an_observed_resumed_leg_carries_the_counterfactual_too(store, clock) -> None:
    """§4.2.1a and §8.3 together, and they contradicted each other.

    §4.2.1a says every `observed` receipt carries `budget_charges`, and makes summing them the
    way an operator sizes a budget. §8.3 says the resumed receipt is the **only** receipt an MCP
    multi round-trip or ACS action ever gets. But `_resumed_charges` reads the ledger, and under
    observation the ledger is empty by design, so exactly the deployments §8.3 is about
    contributed nothing to the sum. An independent review found the two texts disagreeing.

    Enforce mode still reads the ledger, because there the ledger is the record of a real spend.
    """
    from ctrlrun import Suspended

    observing = _observing_control(store, clock)

    def suspends() -> Any:
        raise Suspended("round-1")

    with pytest.raises(Suspended):
        observing.execute(_action("1", 100), suspends, "refund:1")
    assert store.consumptions() == (), "observe mode charged nothing, as it must not"

    receipt = contextvars.Context().run(observing.resume, "round-1", lambda: {"ok": True})

    assert receipt.result is ReceiptResult.OBSERVED
    assert receipt.budget_charges == ({"grant_id": "payer", "metric": "amount", "amount": 100},), (
        receipt.budget_charges
    )
    assert store.consumptions() == (), "and still charged nothing"


def test_T457a_an_enforced_resumed_leg_still_reads_the_ledger(store, clock) -> None:
    """The control. Observe mode computing its counterfactual must not make enforce mode compute
    one too: there the ledger is the record of a spend that really happened, and a recomputed
    number would be a claim rather than evidence."""
    from ctrlrun import Suspended

    control = _control(store, clock)

    def suspends() -> Any:
        raise Suspended("round-1")

    with pytest.raises(Suspended):
        control.execute(_action("1", 100), suspends, "refund:1")

    receipt = contextvars.Context().run(control.resume, "round-1", lambda: {"ok": True})

    assert receipt.budget_charges == ({"grant_id": "payer", "metric": "amount", "amount": 100},)
    assert len(store.consumptions()) == 1, "and the row it read is the one the first leg wrote"


def test_T458_observe_and_enforce_agree_when_an_action_is_both_out_of_scope_and_unmeasurable(
    store, clock
) -> None:
    """The third ordering drift, and the one a review suspected without demonstrating.

    `_secure` runs `_charges_for` **before** `_in_scope`; `_observe_secure` ran the scope check
    first. So an action that is both out of scope and unmeasurable was refused
    `budget_unmeasurable` by enforce mode and reported `out_of_scope` by the pilot. Whichever
    order is right, one of them has to follow the other, and the enforcing one is the one that
    decides.
    """
    document = DOC.replace(
        '    effect: "refund:{id}"',
        '    effect: "refund:{id}"\n    resource: "payment:{id}"',
    )
    enforcing, observing, _ = _both_modes(store, clock, document)
    bad = Action(
        name="payments.refund",
        arguments={"id": "1"},  # no `amount`, so §2.3 cannot measure it
        principal=AGENT,
        resource="payment:1",
        environment="prod",
    )
    somebody_else = lambda _action: {"resources": ["payment:999"]}  # noqa: E731

    with pytest.raises(InvalidArgument):
        enforcing.execute(bad, lambda: {"ok": True}, "refund:1", scope=somebody_else)
    enforced = store.receipts()[-1].decision_reason

    receipt = observing.execute(bad, lambda: {"ok": True}, "refund:1", scope=somebody_else)

    assert enforced == "budget_unmeasurable"
    assert receipt.would_have is not None
    assert receipt.would_have.blocked_reason == enforced, (
        f"enforce refused {enforced!r} and the pilot was told {receipt.would_have.blocked_reason!r}"
    )


# --- the second review round: four regressions on the observe and resume paths ---------------


def test_T459_an_observed_resumed_receipt_never_carries_another_actions_charges(
    store, clock
) -> None:
    """**T448's defect, reintroduced on the observe path**, on the one receipt §8.3 makes the
    whole evidence for an MCP multi round-trip.

    `execute` clears `_BUDGET_CHARGES` at its own top; `resume` has no such line and relied on
    `_resumed_charges` to do it, and an earlier fix skipped that call in observe mode. An
    independent review demonstrated the result: a resumed `observed` receipt for an action whose
    own metric cannot be measured reported a charge of 700 belonging to a different effect.

    The action here is unmeasurable on purpose, because that is the path that returns without
    computing anything and so leaves whatever the contextvar held.
    """
    from ctrlrun import Suspended

    control = _observing_control(store, clock)
    unmeasurable = Action(
        name="payments.refund",
        arguments={"amount": -250, "id": "1"},
        principal=AGENT,
        environment="prod",
    )

    def suspends() -> Any:
        raise Suspended("round-1")

    with pytest.raises(Suspended):
        control.execute(unmeasurable, suspends, "refund:1")
    # A different action runs in the same context, leaving its own charges behind.
    control.execute(_action("2", 100), lambda: {"ok": True}, "refund:2")

    receipt = control.resume("round-1", lambda: {"ok": True})

    assert receipt.budget_charges == (), (
        f"the resumed receipt for {dict(receipt.arguments)} carried {receipt.budget_charges}"
    )


def test_T460_the_resumed_leg_records_one_denial_and_the_receipt_agrees_with_it(
    store, clock
) -> None:
    """**An answer and the evidence may not disagree about the same action**, which is the rule
    `acs.py`'s own clause states one boundary lower.

    The resumed recompute was handed a throwaway `_Observation()`, so the block was discarded and
    the event written a second time: the receipt said `decision=ALLOW, blocked_reason=None` while
    the two `ACTION_DENIED` events beside it said the action was refused twice.
    """
    from ctrlrun import Suspended

    control = _observing_control(store, clock)
    unmeasurable = Action(
        name="payments.refund",
        arguments={"amount": -250, "id": "1"},
        principal=AGENT,
        environment="prod",
    )

    def suspends() -> Any:
        raise Suspended("round-1")

    with pytest.raises(Suspended):
        control.execute(unmeasurable, suspends, "refund:1")
    receipt = control.resume("round-1", lambda: {"ok": True})

    denials = [e for e in store.events() if str(e.type) == "ACTION_DENIED"]
    assert len(denials) == 1, [e.data.get("reason") for e in denials]
    assert receipt.would_have is not None
    assert receipt.would_have.blocked_reason == "budget_unmeasurable", receipt.would_have


def test_T461_the_observed_budget_refusal_names_the_effect_it_refused(store, clock) -> None:
    """Splitting the observe check dropped `effect_key` from the one event that says which effect
    a budget would have refused. Nothing noticed, which is why this exists."""
    enforcing = _control(store, clock)
    enforcing.execute(_action("1", 250), lambda: {"ok": True}, "refund:1")

    observing = _observing_control(store, clock)
    observing.execute(_action("2", 250), lambda: {"ok": True}, "refund:2")

    exhausted = [
        event
        for event in store.events()
        if str(event.type) == "ACTION_DENIED" and event.data.get("reason") == "budget_exhausted"
    ]
    assert exhausted, "no observed budget refusal was recorded"
    assert exhausted[-1].effect_key == "refund:2", exhausted[-1].effect_key


def test_T462_the_unmeasurable_refusal_survives_a_pickle_as_InvalidArgument_did() -> None:
    """`InvalidArgument` round-trips; a subclass with a keyword-only `reason` did not, because
    the default reconstruction is `(cls, self.args)`.

    Nothing in this repository pickles it, so no test would have caught it by running: verify's
    children speak JSON over stdin. A caller fanning `Control.execute` across a
    `ProcessPoolExecutor` lost the pool instead of catching the refusal.
    """
    import pickle

    from ctrlrun.control import _UnmeasurableError

    back = pickle.loads(pickle.dumps(_UnmeasurableError("nope", reason="budget_unkeyed")))

    assert isinstance(back, InvalidArgument)
    assert back.reason == "budget_unkeyed"
    assert str(back) == "nope"


def test_T463_observe_reports_one_refusal_and_not_every_check_that_would_have_failed(
    store, clock
) -> None:
    """Enforce mode raises at the first refusal and never reaches the budget. Observe mode runs
    every check, so it wrote a `budget_exhausted` event for an action enforce mode refuses out of
    scope, and an operator reading the log saw a refusal that would never have happened.

    `_observe_spend`'s own docstring used to claim the earlier clauses had returned by then. The
    scope block and the approval gate do not return; they record and carry on.
    """
    document = DOC.replace(
        '    effect: "refund:{id}"', '    effect: "refund:{id}"\n    resource: "payment:{id}"'
    )
    enforcing, observing, watcher = _both_modes(store, clock, document)
    # Fill the budget in both stores, so the budget really would refuse if it were reached.
    for control in (enforcing, observing):
        control.execute(_action("1", 250), lambda: {"ok": True}, "refund:1")

    somebody_else = lambda _action: {"resources": ["payment:999"]}  # noqa: E731
    later = Action(
        name="payments.refund",
        arguments={"amount": 250, "id": "2"},
        principal=AGENT,
        resource="payment:2",
        environment="prod",
    )
    with pytest.raises(ActionDenied) as refused:
        enforcing.execute(later, lambda: {"ok": True}, "refund:2", scope=somebody_else)
    assert refused.value.reason == "out_of_scope"

    observing.execute(later, lambda: {"ok": True}, "refund:2", scope=somebody_else)

    reasons = [
        event.data.get("reason") for event in watcher.events() if str(event.type) == "ACTION_DENIED"
    ]
    assert reasons == ["out_of_scope"], (
        f"the pilot recorded a refusal enforce mode never reached: {reasons}"
    )


# --- T502: the four v0.9 regressions, as regression tests (SPEC-v0.10 §5.4) --------------------


def test_T502a_a_resumed_observed_leg_carries_the_counterfactual_its_first_leg_computed(
    store, clock
) -> None:
    """T502, row one. `T439` proves observe mode charges nothing; `T447` proves a resumed leg
    reports what its first leg charged. **Neither covers the two together**, and the two together
    are the case that broke.

    The first version of this test asserted the resumed observed receipt reports **no** spend,
    reasoning that the ledger is empty. That is wrong, and `§4.2.1a` says why: under observation
    every number on a receipt is a counterfactual, and if the receipt does not carry what the
    action *would* have been charged then nothing anywhere records it and **a budget cannot be
    sized from an observed run** -- which is the entire reason to run one. `T439d` pins that for
    a single leg.

    So the property here is that a **resumed** leg keeps it. The resumed receipt is the whole
    evidence an MCP or ACS action ever gets, and `_resumed_charges` reads the ledger, which is
    empty under observation. A resumed observed leg that reported `()` would silently drop the
    counterfactual for exactly the actions that take more than one round trip.
    """
    from ctrlrun import Suspended

    observing = _observing_control(store, clock)

    def suspends() -> Any:
        raise Suspended("observed-round")

    with pytest.raises(Suspended):
        observing.execute(_action("1", 100), suspends, "refund:1")
    assert store.consumptions() == (), "observe mode wrote to the ledger on the first leg"

    receipt = contextvars.Context().run(observing.resume, "observed-round", lambda: {"ok": True})

    assert receipt.result is ReceiptResult.OBSERVED
    assert receipt.budget_charges == ({"grant_id": "payer", "metric": "amount", "amount": 100},), (
        f"the resumed observed leg dropped the counterfactual spend: {receipt.budget_charges}"
    )
    assert store.consumptions() == (), (
        "the resumed observed leg wrote to the ledger; the number on the receipt is a "
        "counterfactual and must stay one"
    )


def test_T502b_a_resumed_observed_leg_does_not_announce_the_refusal_twice(store, clock) -> None:
    """T502, row two, and the only one of the four with no test under any name.

    Row three is `test_T461...` and row four is `test_T462...`, both above and both under their
    own numbers -- which is why §8 calls T502 owed while the tree already had most of it.

    The regression: `_refuse_unmeasurable` announces `ACTION_DENIED` under observation, and a
    resumed leg announced it **again** for the same action. The evidence then said the action was
    denied twice while the `observed` receipt beside it said it ran. An answer and its evidence
    disagreeing about one action is what `acs.py`'s clause forbids one boundary lower, and it is
    why `announce=False` exists on the resumed path.

    **Counted, not merely present.** A test asserting the event appears passes whether it appears
    once or twice, which is how this shipped in the first place. `announce` guards the
    *unmeasurable* refusal (§2.3, §2.4.1), so the action carries a metric the budget cannot read
    -- a negative amount -- rather than one that exhausts it.
    """
    from ctrlrun import Suspended

    observing = _observing_control(store, clock)
    unmeasurable = Action(
        name="payments.refund",
        arguments={"amount": -250, "id": "1"},
        principal=AGENT,
        environment="prod",
    )

    def suspends() -> Any:
        raise Suspended("observed-round")

    with pytest.raises(Suspended):
        observing.execute(unmeasurable, suspends, "refund:1")

    def denials() -> list:
        return [
            event
            for event in store.events()
            if str(event.type) == "ACTION_DENIED"
            and event.data.get("reason") == "budget_unmeasurable"
        ]

    assert len(denials()) == 1, (
        f"the first leg did not announce the observed refusal exactly once: {len(denials())}"
    )

    receipt = contextvars.Context().run(observing.resume, "observed-round", lambda: {"ok": True})

    assert receipt.result is ReceiptResult.OBSERVED, "observe mode refused a resumed leg"
    assert len(denials()) == 1, (
        f"the resumed leg announced the refusal again: {len(denials())} ACTION_DENIED rows for "
        "one action, beside an `observed` receipt saying it ran"
    )
