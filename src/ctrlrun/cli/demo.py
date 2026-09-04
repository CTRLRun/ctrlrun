"""The five-scenario demo with an in-process fake Stripe. SPEC-v0.1 §7 T11, SPEC-v0.3 §1.2.

Five ways an agent action goes wrong at the boundary between intention and effect, and what
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

from ..action import Action, Principal
from ..approval import ScriptedApprovalProvider
from ..authority import Authority, Grant, grant_from_yaml
from ..control import Control, context, protect, with_approval
from ..errors import (
    AmbiguousEffect,
    ApprovalMismatch,
    ApprovalRequired,
    AuthorityDenied,
    AuthorityEscalation,
    DuplicateEffect,
)
from ..policy import Policy
from ..receipt import EVENTS_FILENAME, RECEIPTS_FILENAME, JSONLEventSink
from ..state import SQLiteStateStore, StateStore

#: Where the demo keeps its own store and evidence, under `.ctrlrun/`.
DEMO_DIRNAME: Final = "demo"

#: The headings `ctrlrun demo` prints, in order (README, "What `ctrlrun demo` shows").
SCENARIO_HEADINGS: Final = (
    "Duplicate effect after a lost response",
    "Approval mutation",
    "Concurrent agents, same effect",
    "Approval replay",
    # SPEC-v0.3 §1.2 — the fifth answers a different question from the other four. They are
    # about *what* is being done; this one is about *who* is entitled to do it.
    "Authority escalation",
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

#: Scenario 5's chain, in integer minor units. Each link is narrower than the one above it,
#: which is the whole of what "attenuation" means (SPEC-v0.3 §5.4).
HEAD_OF_SUPPORT_CEILING: Final = 10000000  # €100,000 — the human's own grant, delegable
FINANCE_CEILING: Final = 2500000  # €25,000 — delegated to the finance agent
SUPPORT_CEILING: Final = 200000  # €2,000 — delegated on to the support agent
ESCALATION_AMOUNT: Final = 5000000  # €50,000 — what the support agent asks for
WITHIN_GRANT_AMOUNT: Final = 150000  # €1,500 — what it is actually entitled to

#: The amounts the README prints, in the order it prints them.
README_AMOUNTS: Final = (LOST_RESPONSE_AMOUNT, PROPOSED_AMOUNT, MUTATED_AMOUNT)

#: Amounts are integer minor units (SPEC-v0.1 §2.3): €1,000 autonomous, €10,000 with a human.
#:
#: Scenarios 1 to 4 load this one, and it carries **no `authority:` section**: that is v0.2
#: behaviour exactly (SPEC-v0.3 §4.1), and running the first four against it is the demo's own
#: small proof that authority is opt-in. Scenario 5 builds its own document.
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
    """Run the five scenarios under `root`, printing what each one blocks (SPEC §7 T11).

    Four failure scenarios from `v0.2 §1.1` and, since `v0.3 §1.2`, the authority escalation
    that answers a different question from the other four."""
    evidence = _fresh_evidence_dir(root)
    store = SQLiteStateStore(evidence / "state.db")
    remote = FakeStripe()
    # Two grants: scenario 2's human, and scenario 4's. A scripted approver never grants
    # more than it was told to, so a scenario that asked twice would fail loudly.
    approvals = ScriptedApprovalProvider(store, ["grant", "grant"], approver="human:demo")
    # SPEC-v0.2 §4.3 — the JSONL evidence is a Control sink now. The demo prints where
    # both files landed, so it installs one on its own evidence directory.
    control = Control(
        Policy.from_yaml(DEMO_POLICY, source="<demo>"),
        store,
        approvals,
        sinks=[JSONLEventSink(evidence)],
    )

    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def refund(payment_id: str, amount: int, currency: str = "EUR") -> dict[str, Any]:
        return remote.refund(payment_id, amount)

    click.echo("CTRLRun demo — five ways an agent action goes wrong, and what stops it.")
    click.echo(
        "Policy: refunds up to €1,000 are autonomous, up to €10,000 need a human, "
        "above that are denied."
    )

    try:
        _lost_response(refund, remote)
        _approval_mutation(refund, approvals)
        _concurrent_agents(refund, remote)
        _approval_replay(refund, approvals)
        _authority_escalation(control, store, remote)
    finally:
        written = len(store.receipts())
        store.close()

    click.echo("")
    click.echo(f"Receipts ({written}): {written_path(root, evidence / RECEIPTS_FILENAME)}")
    click.echo(f"Events:       {written_path(root, evidence / EVENTS_FILENAME)}")
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
        used = f"approval {request_id} used once"
        click.echo(f"   {used}  →  consumed")
        # The two arrows line up, and the id's width is not a constant to hardcode: it grew
        # once already (48 → 128 bits) and quietly knocked this column out of true.
        click.echo(f"   {'same approval presented again':<{len(used)}}  →")

        def present_it_again() -> None:
            with with_approval(request_id):
                refund(payment_id="txn_4", amount=amount)

        blocked = _refused(present_it_again, ApprovalMismatch)
    click.echo(f"{_BLOCKED}single-use approval already {blocked.reason}")


# --- 5. authority escalation (SPEC-v0.3 §5.6, T80's story) ----------------------------


#: Scenario 5's own document. The `authority:` section is the whole point, so it needs
#: `ctrlrun.policy/v3`; the `actions:` half is the same money policy the other four use, so a
#: reader comparing the two sees exactly one difference.
AUTHORITY_POLICY: Final = f"""
schema: ctrlrun.policy/v3
authority:
  max_delegation_depth: 3
  grants:
    - id: head-of-support
      subject: {{ agent: "head-of-support", user: "dana@example.com" }}
      actions: ["stripe.refund"]
      constraints: {{ amount_lte: {HEAD_OF_SUPPORT_CEILING} }}
      delegable: true
      expires_at: "2027-01-01T00:00:00Z"
actions:
  stripe.refund:
    rules:
      - when: {{ amount_lte: 100000 }}
        decision: allow
      - when: {{ amount_lte: 1000000 }}
        decision: approve
      - decision: deny
"""


def _delegated(ceiling: int, agent: str, user: str) -> Grant:
    """One narrower grant, in the shape `ctrlrun delegate --file` takes (SPEC-v0.3 §5.7).

    Every dimension is stated. **Omitting one its parent constrains would be rejected, not
    inherited** (§5.4) — which is the sentence this scenario exists to make concrete, and the
    opposite of how almost every permission system behaves.
    """
    return grant_from_yaml(
        f"""
subject: {{ agent: "{agent}", user: "{user}" }}
actions: ["stripe.refund"]
constraints: {{ amount_lte: {ceiling} }}
delegable: true
expires_at: "2026-12-01T00:00:00Z"
"""
    )


def _authority_escalation(base: Control, store: StateStore, remote: FakeStripe) -> None:
    """A delegation chain, and the two ways of stepping outside it (SPEC-v0.3 §5.6).

    Scenarios 1 to 4 ask "is this action safe to run". This one asks "is this principal
    entitled to propose it at all" — a separate axis, evaluated first, that a policy cannot
    see (§4.7). A grant carries no `decision:`: how much autonomy an action has is the same
    for every principal, and what differs is whether they may ask.
    """
    _heading(5, SCENARIO_HEADINGS[4])
    # The same store and the **same sinks** as the other four. A second Control that quietly
    # dropped the JSONL sink would leave the demo printing a receipt count from the store that
    # the file it points the reader at does not match — which is the shape of a false green,
    # in a transcript people paste into issues.
    control = Control(
        Policy.from_yaml(AUTHORITY_POLICY, source="<demo>"),
        store,
        base.approvals,
        sinks=base.sinks,
        authority=Authority.from_yaml(AUTHORITY_POLICY, source="<demo>"),
    )
    head = Principal(agent="head-of-support", user="dana@example.com")
    finance = Principal(agent="finance-agent", user="dana@example.com")
    support = Principal(agent="support-agent", user="dana@example.com")

    finance_grant = control.delegate(
        "head-of-support", _delegated(FINANCE_CEILING, "finance-agent", "dana@example.com"), by=head
    )
    support_grant = control.delegate(
        finance_grant.delegation_id,
        _delegated(SUPPORT_CEILING, "support-agent", "dana@example.com"),
        by=finance,
    )
    click.echo(
        f"   human {_euros(HEAD_OF_SUPPORT_CEILING)} delegable  →  "
        f"finance agent {_euros(FINANCE_CEILING)}  →  support agent {_euros(SUPPORT_CEILING)}"
    )
    click.echo(f"   support agent's grant: {support_grant.delegation_id}")

    def escalate() -> None:
        control.execute(
            _refund_action(support, "txn_5", ESCALATION_AMOUNT),
            lambda: remote.refund("txn_5", ESCALATION_AMOUNT),
            "refund:txn_5",
        )

    click.echo(f"   support agent requests {_euros(ESCALATION_AMOUNT)}  →")
    blocked = _refused(escalate, AuthorityDenied)
    click.echo(f"{_BLOCKED}outside the delegated grant ({blocked.reason})")
    click.echo(f"   remote refund calls: {remote.calls.count('txn_5')}")

    # And the delegation that would have widened it is refused at creation, on the dimension
    # it widened — so an agent cannot reach the amount above by minting its own authority.
    def over_delegate() -> None:
        control.delegate(
            finance_grant.delegation_id,
            _delegated(ESCALATION_AMOUNT, "support-agent", "dana@example.com"),
            by=finance,
        )

    escalation = _refused(over_delegate, AuthorityEscalation)
    click.echo(
        f"   finance agent tries to delegate {_euros(ESCALATION_AMOUNT)} under its own "
        f"{_euros(FINANCE_CEILING)}  →  refused ({escalation.reason}: {escalation.dimension})"
    )

    # The control, and the point of §4.6 in one line: the same agent, the same chain, an
    # amount it *is* entitled to. Authority permits it — and the policy still asks a human,
    # because the two axes combine as the stricter of the pair. Without this the scenario
    # would be consistent with a chain that authorized nothing at all.
    def within_the_grant() -> None:
        control.execute(
            _refund_action(support, "txn_6", WITHIN_GRANT_AMOUNT),
            lambda: remote.refund("txn_6", WITHIN_GRANT_AMOUNT),
            "refund:txn_6",
        )

    pending = _refused(within_the_grant, ApprovalRequired)
    click.echo(
        f"   support agent requests {_euros(WITHIN_GRANT_AMOUNT)}  →  authority permits it, "
        f"and the policy asks a human ({pending.request_id})"
    )
    click.echo("   two axes, and an action needs both: the stricter of the pair wins")


def _refund_action(principal: Principal, payment_id: str, amount: int) -> Action:
    """The Action `@protect` would build, built by hand.

    Scenario 5 needs three different principals against one `Control`, and `context()` names a
    principal rather than resolving one — so the Action is constructed directly, which is the
    same public path a gateway or an adapter takes.
    """
    return Action(
        name="stripe.refund",
        arguments={"payment_id": payment_id, "amount": amount, "currency": "EUR"},
        principal=principal,
        resource=f"payment:{payment_id}",
    )


def _propose(refund: Callable[..., Any], payment_id: str, amount: int) -> str:
    """Make the proposal that asks a human, and return the request it raised (SPEC §4.3)."""
    pending = _refused(lambda: refund(payment_id=payment_id, amount=amount), ApprovalRequired)
    return pending.request_id


def written_path(root: Path, path: Path) -> str:
    """`path` as the reader can retype it: relative to the directory the demo ran in.

    The demo's transcript is meant to be pasted — into an issue, a post, the README — and an
    absolute path carries the operator's username and directory layout along with it. A path
    outside `root` has no relative form and is printed whole; the demo never writes one.
    """
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def read_them_command(evidence: Path) -> str:
    """The command that reads the demo's receipts, ready to paste (SPEC-v0.1 §8).

    The demo keeps its own store, so `ctrlrun receipts` on its own would read the operator's.
    `$CTRLRUN_STATE` is the documented way to point a command at another store, and this is
    the one place a new user needs it.

    The path is relative to where the demo ran, which is also where the reader is standing
    when they paste this, so `evidence.parents[1]` is that directory: `<root>/.ctrlrun/demo`.
    """
    state = written_path(evidence.parents[1], evidence / "state.db")
    return f"CTRLRUN_STATE={shlex.quote(state)} ctrlrun receipts"


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
