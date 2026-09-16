# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""T379 to T387: task-bound authority (SPEC-v0.9 §6).

One more containment dimension, attenuated by the same `child ⊆ parent` rule as every other.
Parametrised over the three stores like every authority test, because a delegation is a row and
a store that loses the parent link loses the containment the dimension exists to impose.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from ctrlrun.action import Action, Principal
from ctrlrun.authority import Authority, canonical_grants
from ctrlrun.control import Control
from ctrlrun.errors import AuthorityEscalation, PolicyError
from ctrlrun.policy import Decision, Policy
from ctrlrun.receipt import ReceiptResult
from ctrlrun.state import InMemoryStateStore, SQLiteStateStore

pytestmark = pytest.mark.authority

POSTGRES_URL = os.environ.get("CTRLRUN_TEST_POSTGRES")
NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
AGENT = Principal(agent="payer", user="ada")

TASK_BOUND = """
schema: ctrlrun.policy/v7
environment: prod
actions:
  payments.refund:
    decision: allow
authority:
  grants:
    - id: payments-agent
      subject: {agent: "payer"}
      actions: ["payments.*"]
      tasks: ["invoice-run-*"]
      delegable: true
      expires_at: "2027-01-01T00:00:00Z"
"""

UNBOUND = TASK_BOUND.replace('      tasks: ["invoice-run-*"]\n', "")


class _Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


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
        made = SQLiteStateStore(tmp_path / "s.db", clock=clock)
    else:
        from ctrlrun.postgres import PostgresStateStore

        schema = f"task_{uuid.uuid4().hex[:12]}"
        PostgresStateStore.create_schema(POSTGRES_URL, schema)
        made = PostgresStateStore(POSTGRES_URL, schema=schema, clock=clock)
    yield made
    made.close()
    if request.param == "postgres":
        from ctrlrun.postgres import PostgresStateStore

        PostgresStateStore.drop_schema(POSTGRES_URL, schema)


def _control(text: str, store, clock) -> Control:
    policy = Policy.from_yaml(text, source="<test>")
    return Control(
        policy=policy,
        store=store,
        clock=clock,
        environment="prod",
        authority=Authority.from_yaml(text, source="<test>"),
    )


def _action(name: str = "payments.refund") -> Action:
    return Action(name=name, arguments={"amount": 10}, principal=AGENT, environment="prod")


def test_T379_a_task_bound_grant_permits_a_task_it_names(store, clock) -> None:
    control = _control(TASK_BOUND, store, clock)
    receipt = control.execute(_action(), lambda: {"ok": True}, "refund:1", task="invoice-run-7")
    assert receipt.result is ReceiptResult.COMMITTED


def test_T380_a_task_bound_grant_refuses_a_task_it_does_not_name(store, clock) -> None:
    control = _control(TASK_BOUND, store, clock)
    evaluation = control.evaluate(_action(), task="payroll-3")
    # The refusal names the dimension by value, not merely the type: SPEC-v0.9 §6.2, and G24
    # is "refused off its task, by name".
    assert evaluation.reason == "authority_task"


def test_T381_a_task_bound_grant_refuses_an_action_carrying_no_task(store, clock) -> None:
    control = _control(TASK_BOUND, store, clock)
    evaluation = control.evaluate(_action())
    assert evaluation.reason == "authority_task"


def test_T382_a_grant_naming_no_task_permits_any_task(store, clock) -> None:
    """SPEC-v0.9 §6.5, the one decision that could have broken every existing deployment."""
    control = _control(UNBOUND, store, clock)
    with_task = control.execute(_action(), lambda: {"ok": True}, "refund:a", task="anything")
    without = control.execute(_action(), lambda: {"ok": True}, "refund:b")
    assert with_task.result is ReceiptResult.COMMITTED
    assert without.result is ReceiptResult.COMMITTED
    # Field for field what 0.8.0 wrote, but for the schema tag and the v6 keys (§9.1). `task` is
    # always present and nullable, like `authority_grant_id` beside it.
    assert with_task.to_dict()["task"] == "anything"
    assert without.to_dict()["task"] is None


def test_T383_a_child_naming_a_task_its_parent_does_not_is_rejected(store, clock) -> None:
    control = _control(TASK_BOUND, store, clock)
    from ctrlrun.authority import Grant, Subject

    # Contained on every dimension but the one under test: `contained_dimension` returns the
    # FIRST row violated, so a child that also drops `expires_at` would report that instead and
    # the test would pass while proving nothing about tasks.
    child = Grant(
        id="",
        subject=Subject(agent="payer", user="ada"),
        actions=["payments.refund"],
        expires_at=datetime(2026, 12, 1, tzinfo=UTC),
        tasks=("payroll-*",),
    )
    with pytest.raises(AuthorityEscalation) as caught:
        control.delegate("payments-agent", child, by=AGENT)
    assert caught.value.dimension == "tasks"


def test_T384_a_child_omitting_tasks_under_a_parent_that_names_them_is_rejected(
    store, clock
) -> None:
    control = _control(TASK_BOUND, store, clock)
    from ctrlrun.authority import Grant, Subject

    child = Grant(
        id="",
        subject=Subject(agent="payer", user="ada"),
        actions=["payments.refund"],
        expires_at=datetime(2026, 12, 1, tzinfo=UTC),
    )
    with pytest.raises(AuthorityEscalation) as caught:
        control.delegate("payments-agent", child, by=AGENT)
    assert caught.value.dimension == "tasks"


def test_T385_a_task_changed_in_the_document_moves_the_policy_hash() -> None:
    one = canonical_grants(Authority.from_yaml(TASK_BOUND, source="<a>"))
    widened = TASK_BOUND.replace('["invoice-run-*"]', '["invoice-run-*", "payroll-*"]')
    two = canonical_grants(Authority.from_yaml(widened, source="<b>"))
    assert one != two, "a task outside the canonical render is a dimension outside the hash"


def test_T387_a_v7_tasks_key_in_a_v6_document_is_refused() -> None:
    older = TASK_BOUND.replace("ctrlrun.policy/v7", "ctrlrun.policy/v6")
    with pytest.raises(PolicyError) as caught:
        Policy.from_yaml(older, source="<old>")
    assert "tasks" in str(caught.value)


BREAK_GLASS = """
schema: ctrlrun.policy/v7
environment: prod
controls:
  incident-response:
    title: Only an incident commander opens break-glass
    approver_role: incident-commander
actions:
  payments.refund:
    decision: allow
authority:
  grants:
    - id: everyday
      subject: {agent: "ops-agent"}
      actions: ["payments.read"]
  break_glass:
    incident-payments:
      subject: {agent: "oncall-*"}
      actions: ["payments.*"]
      environments: ["prod"]
      tasks: ["incident-*"]
      max_ttl: PT4H
      controls: [incident-response]
"""


def test_T386_a_break_glass_grant_attenuates_on_tasks_at_the_second_level(store, clock) -> None:
    """SPEC-v0.9 §6.6, on T337's pattern.

    v0.8 §5 shaped the envelope as an ordinary `Grant` plus `max_ttl` **so that** v0.9 would
    attenuate it without a second path. This asserts that rather than building one: the
    dimension has to survive all three sites `SPEC-v0.8 §5.2` names as reading `delegable`,
    and the second level is where a rule applied only at the root would show.
    """
    from ctrlrun.authority import Authority, Grant, Subject

    authority = Authority.from_yaml(BREAK_GLASS, source="<bg>")
    envelope = authority.envelopes["incident-payments"]
    # The envelope is a Grant carrying the dimension: no second kind of authority, which is the
    # whole of what §6.6 asks item 1 to confirm.
    assert envelope.grant.tasks == ("incident-*",)
    child = Grant(
        id="",
        subject=Subject(agent="oncall-agent", user="ada"),
        actions=["payments.refund"],
        environments=("prod",),
        tasks=("payroll-*",),
    )
    from ctrlrun.authority import contained_dimension

    assert contained_dimension(envelope.grant, child) == "tasks"


def test_T386a_resume_does_not_evaluate_the_task_dimension(store, clock) -> None:
    """SPEC-v0.9 §6.3.2's third mode, and the reason it is not `v0.3 §5.6.1`'s.

    A resumed leg carries no task: §6.3.1 keeps it off the `Action`, and the action is
    rehydrated from the store. Evaluating the dimension there would deny **every** resumed leg
    under a task-bound grant, on what `control.py` calls the only receipt an MCP multi
    round-trip ever gets.
    """
    control = _control(TASK_BOUND, store, clock)
    action = _action()
    # The first leg, on a task the grant names.
    assert control.evaluate(action, task="invoice-run-7").decision is not Decision.DENY
    # The resumed leg's shape: authority evaluated with the dimension skipped. Asserted through
    # the public evaluator rather than through a private flag, so the test is about the rule.
    from ctrlrun.authority import Authority

    authority = Authority.from_yaml(TASK_BOUND, source="<t>")
    skipped = authority.evaluate(action, now=clock(), store=store, task=None, evaluate_task=False)
    assert skipped.passed, "a resumed leg must not be denied for carrying no task"
    # And the control: with the dimension evaluated, the same call IS denied. Without this the
    # test passes against a kernel that never checks tasks at all.
    checked = authority.evaluate(action, now=clock(), store=store, task=None)
    assert not checked.passed and checked.reason == "authority_task"


def test_T387a_an_unresolvable_task_template_is_recorded_before_it_raises(store, clock) -> None:
    """An independent review found this: it raised past every recording path.

    `@protect(task="{run_id}")` with no such argument must deny and record like an unresolvable
    `effect` template does. Without it a caller is handed an `EffectKeyError` about an action
    with no `ACTION_PROPOSED`, no `ACTION_DENIED` and no receipt, which is the same escape
    `control.py`'s own round-two comment records finding once before on a store refusal.
    """
    from ctrlrun.control import context, protect
    from ctrlrun.errors import EffectKeyError

    control = _control(UNBOUND, store, clock)

    @protect("payments.refund", control=control, task="{run_id}")
    def refund(amount: int) -> dict[str, bool]:
        return {"ok": True}

    with context(agent="payer", user="ada"), pytest.raises(EffectKeyError):
        refund(amount=1)

    assert [event.type.value for event in store.events()] == [
        "ACTION_PROPOSED",
        "ACTION_DENIED",
    ]
    receipts = store.receipts()
    assert len(receipts) == 1
    assert receipts[0].result is ReceiptResult.DENIED
    # The reason is the template's, not a rule's: the policy never rendered a decision.
    assert receipts[0].decision_reason == "effect_key_error"
