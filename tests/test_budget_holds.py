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
