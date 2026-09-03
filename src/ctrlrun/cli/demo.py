"""The four-scenario demo with an in-process fake Stripe. Build-list item 8; SPEC §7 T11.

Four ways an agent action goes wrong at the boundary between intention and effect, and what
CTRLRun does about each. Everything runs in this process: no network, no clock skew, no
sleeping. The fake remote is the only thing pretending — and it pretends in the one way that
matters, by committing before its response goes missing.

The demo keeps its evidence in `.ctrlrun/demo/`, never in the store a real agent is using:
it reserves effect keys and would otherwise collide with — or block — live work.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final, TypeVar

import click

from ..approval import ScriptedApprovalProvider
from ..control import Control, context, protect, with_approval
from ..errors import (
    AmbiguousEffect,
    ApprovalMismatch,
    ApprovalRequired,
    DuplicateEffect,
)
from ..policy import Policy
from ..receipt import EVENTS_FILENAME, RECEIPTS_FILENAME
from ..state import SQLiteStateStore

#: Where the demo keeps its own store and evidence, under `.ctrlrun/`.
DEMO_DIRNAME: Final = "demo"

#: The headings `ctrlrun demo` prints, in order (README, "What `ctrlrun demo` shows").
SCENARIO_HEADINGS: Final = (
    "Duplicate effect after a lost response",
    "Approval mutation",
    "Concurrent agents, same effect",
    "Approval replay",
)

#: What each scenario refunds, in integer minor units (SPEC-v0.1 §2.3). The README's
#: "What `ctrlrun demo` shows" quotes the first three, and
#: `test_the_readme_demo_section_shows_the_amounts_the_demo_uses` keeps the two in step:
#: the demo is the truth, and the README follows it.
LOST_RESPONSE_AMOUNT: Final = 50000  # €500 — autonomous
PROPOSED_AMOUNT: Final = 200000  # €2,000 — the refund a human approves
MUTATED_AMOUNT: Final = 500000  # €5,000 — what the agent tried to run instead
CONCURRENT_AMOUNT: Final = 50000  # €500 — autonomous, raced for by two agents
REPLAY_AMOUNT: Final = 200000  # €2,000 — approved once, presented twice

#: The amounts the README prints, in the order it prints them.
README_AMOUNTS: Final = (LOST_RESPONSE_AMOUNT, PROPOSED_AMOUNT, MUTATED_AMOUNT)

#: Amounts are integer minor units (SPEC-v0.1 §2.3): €1,000 autonomous, €10,000 with a human.
DEMO_POLICY: Final = """
schema: ctrlrun.policy/v1
actions:
  customer.read:
    decision: allow
  stripe.refund:
    rules:
      - when: { amount_lte: 100000 }
        decision: allow
      - when: { amount_lte: 1000000 }
        decision: approve
      - decision: deny
  iam.grant_admin:
    decision: deny
"""

_BLOCKED: Final = "   ✗ BLOCKED — "


class FakeStripe:
    """An in-process payment API. It commits first, then decides how the reply goes wrong.

    That order is the whole point: a remote that fails *before* doing anything is easy, and
    the executor can say so by raising `NotExecuted`. The dangerous remote is this one.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.lose_response: set[str] = set()
        self.on_call: dict[str, Callable[[], None]] = {}

    def refund(self, payment_id: str, amount: int) -> dict[str, Any]:
        self.calls.append(payment_id)  # the money has moved by the time anything else runs
        hook = self.on_call.pop(payment_id, None)
        if hook is not None:
            hook()
        if payment_id in self.lose_response:
            raise TimeoutError("no response from api.stripe.com after 30s")
        return {"id": f"re_{payment_id}", "amount": amount, "status": "succeeded"}


_Refusal = TypeVar("_Refusal", bound=BaseException)


def _euros(minor_units: int) -> str:
    return f"€{minor_units // 100:,}"


def _heading(number: int, text: str) -> None:
    click.echo("")
    click.echo(f"{number}. {text}")
    click.echo("")


def _refused(call: Callable[[], Any], expected: type[_Refusal]) -> _Refusal:
    """Run something the kernel must refuse, and return the refusal.

    A scenario that is not blocked has demonstrated nothing, and would print nothing to say
    so. Raising here makes `ctrlrun demo` fail loudly instead of quietly passing.
    """
    try:
        call()
    except expected as blocked:
        return blocked
    raise AssertionError(f"the demo expected {expected.__name__} and the action went through")


def run_demo(root: Path) -> None:
    """Run the four scenarios under `root`, printing what each one blocks (SPEC §7 T11)."""
    evidence = _fresh_evidence_dir(root)
    store = SQLiteStateStore(evidence / "state.db")
    remote = FakeStripe()
    # Two grants: scenario 2's human, and scenario 4's. A scripted approver never grants
    # more than it was told to, so a scenario that asked twice would fail loudly.
    approvals = ScriptedApprovalProvider(store, ["grant", "grant"], approver="human:demo")
    control = Control(Policy.from_yaml(DEMO_POLICY, source="<demo>"), store, approvals)

    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def refund(payment_id: str, amount: int, currency: str = "EUR") -> dict[str, Any]:
        return remote.refund(payment_id, amount)

    click.echo("CTRLRun demo — four ways an agent action goes wrong, and what stops it.")
    click.echo(
        "Policy: refunds up to €1,000 are autonomous, up to €10,000 need a human, "
        "above that are denied."
    )

    try:
        _lost_response(refund, remote)
        _approval_mutation(refund, approvals)
        _concurrent_agents(refund, remote)
        _approval_replay(refund, approvals)
    finally:
        written = len(store.receipts())
        store.close()

    click.echo("")
    click.echo(f"Receipts ({written}): {evidence / RECEIPTS_FILENAME}")
    click.echo(f"Events:       {evidence / EVENTS_FILENAME}")
    click.echo("")
    click.echo(f"Read them:    {read_them_command(evidence)}")


# --- 1. the signature scenario (SPEC §7 T1) -------------------------------------------


def _lost_response(refund: Callable[..., Any], remote: FakeStripe) -> None:
    amount = LOST_RESPONSE_AMOUNT
    remote.lose_response.add("txn_1")
    _heading(1, SCENARIO_HEADINGS[0])
    click.echo(
        f"   refund {_euros(amount)}  →  remote commits  →  response lost  →  effect: AMBIGUOUS"
    )
    with context(agent="refund-agent"):
        _refused(lambda: refund(payment_id="txn_1", amount=amount), TimeoutError)
        click.echo("   agent retries the same refund")
        _refused(lambda: refund(payment_id="txn_1", amount=amount), AmbiguousEffect)
        click.echo(f"{_BLOCKED}effect may already have committed; blind retry refused")
    click.echo(f"   remote refund calls: {remote.calls.count('txn_1')}")
    click.echo("   only a human moves it on:  ctrlrun resolve refund:txn_1 --committed|--failed")


# --- 2. the approval is for one exact action (SPEC §7 T2) ------------------------------


def _approval_mutation(refund: Callable[..., Any], approvals: ScriptedApprovalProvider) -> None:
    proposed, mutated = PROPOSED_AMOUNT, MUTATED_AMOUNT
    _heading(2, SCENARIO_HEADINGS[1])
    with context(agent="refund-agent"):
        request_id = _propose(refund, payment_id="txn_2", amount=proposed)
        approvals.wait(request_id, None)  # the scripted human says yes
        click.echo(
            f"   agent proposes refund {_euros(proposed)}  →  human approves {request_id} "
            "(bound to the action hash)"
        )
        click.echo(f"   agent executes refund {_euros(mutated)}  →")

        def execute_the_mutation() -> None:
            with with_approval(request_id):
                refund(payment_id="txn_2", amount=mutated)

        blocked = _refused(execute_the_mutation, ApprovalMismatch)
    click.echo(f"{_BLOCKED}approved action ≠ requested action ({blocked.reason})")


# --- 3. two agents, one effect (SPEC §7 T3, in one process) ----------------------------


def _concurrent_agents(refund: Callable[..., Any], remote: FakeStripe) -> None:
    amount, key = CONCURRENT_AMOUNT, "refund:txn_123"
    _heading(3, SCENARIO_HEADINGS[2])
    refused: list[DuplicateEffect] = []

    def agent_b() -> None:
        """Agent B arrives while A holds the key, which is what makes this a race."""
        with context(agent="refund-agent-b"):
            refused.append(
                _refused(lambda: refund(payment_id="txn_123", amount=amount), DuplicateEffect)
            )

    remote.on_call["txn_123"] = agent_b
    with context(agent="refund-agent-a"):
        refund(payment_id="txn_123", amount=amount)
    click.echo(f"   Agent A  reserve {key}  →  ACQUIRED  →  executes")
    click.echo(f"   Agent B  reserve {key}  →")
    click.echo(f"{_BLOCKED}already reserved ({refused[0].state})")


# --- 4. an approval is worth exactly one execution (SPEC §7 T4) ------------------------


def _approval_replay(refund: Callable[..., Any], approvals: ScriptedApprovalProvider) -> None:
    amount = REPLAY_AMOUNT
    _heading(4, SCENARIO_HEADINGS[3])
    with context(agent="refund-agent"):
        request_id = _propose(refund, payment_id="txn_4", amount=amount)
        approvals.wait(request_id, None)
        with with_approval(request_id):
            refund(payment_id="txn_4", amount=amount)
        click.echo(f"   approval {request_id} used once  →  consumed")
        click.echo(f"   {'same approval presented again':<35}  →")

        def present_it_again() -> None:
            with with_approval(request_id):
                refund(payment_id="txn_4", amount=amount)

        blocked = _refused(present_it_again, ApprovalMismatch)
    click.echo(f"{_BLOCKED}single-use approval already {blocked.reason}")


def _propose(refund: Callable[..., Any], payment_id: str, amount: int) -> str:
    """Make the proposal that asks a human, and return the request it raised (SPEC §4.3)."""
    pending = _refused(lambda: refund(payment_id=payment_id, amount=amount), ApprovalRequired)
    return pending.request_id


def read_them_command(evidence: Path) -> str:
    """The command that reads the demo's receipts, ready to paste (SPEC-v0.1 §8).

    The demo keeps its own store, so `ctrlrun receipts` on its own would read the operator's.
    `$CTRLRUN_STATE` is the documented way to point a command at another store, and this is
    the one place a new user needs it.
    """
    return f"CTRLRUN_STATE={shlex.quote(str(evidence / 'state.db'))} ctrlrun receipts"


def _fresh_evidence_dir(root: Path) -> Path:
    """`.ctrlrun/demo/`, emptied of a previous run's evidence so the demo repeats.

    Only the files this demo writes are removed, by name: a demo that deleted a directory
    would eventually delete somebody's real evidence.
    """
    evidence = root / ".ctrlrun" / DEMO_DIRNAME
    evidence.mkdir(parents=True, exist_ok=True)
    for name in (
        "state.db",
        "state.db-wal",
        "state.db-shm",
        RECEIPTS_FILENAME,
        EVENTS_FILENAME,
    ):
        (evidence / name).unlink(missing_ok=True)
    return evidence
