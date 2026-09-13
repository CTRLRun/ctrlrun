"""T415 to T443: consumption, reconciliation and release (SPEC-v0.9 §4).

§4.2's table has nineteen rows and the implementation has one rule: **released exactly when the
effect reaches `FAILED`, held in every other state.** One test per disposition, because v0.8's
item 4 needed three attempts on the analogous lapsed-row case: its spec had ten rows and its code
met an eleventh.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ctrlrun.action import Action, Principal
from ctrlrun.authority import Authority
from ctrlrun.control import Control
from ctrlrun.effect import EffectState
from ctrlrun.errors import ActionDenied, InvalidArgument, NotExecuted
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
    with pytest.raises(Exception):
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
        control.execute(
            _action(), lambda: (_ for _ in ()).throw(TimeoutError("lost")), "refund:1"
        )
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
    with pytest.raises(Exception):
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
    with pytest.raises(Exception):
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
