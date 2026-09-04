#!/usr/bin/env python3
"""A delegated grant cannot be wider than the one it came from, and it is re-checked.

Three links: a human who holds €100,000 and may delegate, a finance agent given €25,000, a
support agent given €2,000. Then the two ways of stepping outside the chain, which fail at
two different moments and for two different reasons:

- the support agent **asks for €50,000** — refused at evaluation, `authority_constraint`;
- the finance agent **tries to mint €50,000** under its own €25,000 — refused at creation,
  `containment`, naming the dimension it widened.

Asserting only one would hide the other. The second is the one worth sitting with: an agent
that could delegate more authority than it holds could reach the first amount by minting its
own permission, and every check at evaluation would still pass.

The last part is not an escalation at all. The support agent asks for €1,500, which its grant
permits — and the policy still stops to ask a human, because authority and policy are separate
axes and an action needs both.

    python examples/authority-escalation/main.py

No network, and a state directory of its own under `.ctrlrun/examples/`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ctrlrun import (
    Action,
    ApprovalRequired,
    Authority,
    AuthorityDenied,
    AuthorityEscalation,
    Control,
    Grant,
    Policy,
    Principal,
    SQLiteStateStore,
)
from ctrlrun.authority import grant_from_yaml

HERE = Path(__file__).resolve().parent
BLOCKED = "   ✗ BLOCKED — "

#: Integer minor units (SPEC-v0.1 §2.3). Each link is narrower than the one above it.
FINANCE_CEILING = 2500000  # €25,000.00
SUPPORT_CEILING = 200000  # €2,000.00
ESCALATION = 5000000  # €50,000.00 — outside the support agent's grant
WITHIN_GRANT = 150000  # €1,500.00 — inside it, and still not autonomous

HEAD = Principal(agent="head-of-support", user="dana@example.com")
FINANCE = Principal(agent="finance-agent", user="dana@example.com")
SUPPORT = Principal(agent="support-agent", user="dana@example.com")


class FakeStripe:
    """Records every call, so "the remote was never reached" is a number and not a claim."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def refund(self, payment_id: str, amount: int) -> dict[str, Any]:
        self.calls.append(payment_id)
        return {"id": f"re_{payment_id}", "amount": amount, "status": "succeeded"}


def narrower(agent: str, ceiling: int) -> Grant:
    """One delegated grant, in the shape `ctrlrun delegate --file` takes.

    **Every dimension is stated.** Omitting one the parent constrains is rejected, not
    inherited and certainly not treated as unlimited — which is the opposite of how almost
    every permission system behaves, and the reason this file spells out `actions`,
    `resources`, `constraints`, `environments` and `expires_at` on every link.
    """
    return grant_from_yaml(
        f"""
subject: {{ agent: "{agent}", user: "dana@example.com" }}
actions: ["stripe.refund"]
resources: ["payment:*"]
constraints: {{ amount_lte: {ceiling} }}
environments: ["production"]
delegable: true
expires_at: "2026-12-01T00:00:00Z"
"""
    )


def refund_action(principal: Principal, payment_id: str, amount: int) -> Action:
    """The Action a decorator or a gateway would build, built by hand.

    Three principals share one `Control` here, and `ctrlrun.context()` names a principal
    rather than resolving one — so the Action is constructed directly, which is the same
    public path the gateway and the ACS hook take.
    """
    return Action(
        name="stripe.refund",
        arguments={"payment_id": payment_id, "amount": amount},
        principal=principal,
        resource=f"payment:{payment_id}",
    )


def state_dir(root: Path) -> Path:
    """A store of this example's own, emptied so the example repeats.

    An example that reserved effect keys in a live store would block real work. Only the
    files this script writes are removed, and only by name: never a directory.
    """
    evidence = root / ".ctrlrun" / "examples" / HERE.name
    evidence.mkdir(parents=True, exist_ok=True)
    for name in ("state.db", "state.db-wal", "state.db-shm", "receipts.jsonl", "events.jsonl"):
        (evidence / name).unlink(missing_ok=True)
    return evidence


def main(root: Path) -> None:
    document = (HERE / "ctrlrun.yaml").read_text(encoding="utf-8")
    store = SQLiteStateStore(state_dir(root) / "state.db")
    remote = FakeStripe()
    control = Control(
        Policy.from_yaml(document, source=str(HERE / "ctrlrun.yaml")),
        store,
        authority=Authority.from_yaml(document, source=str(HERE / "ctrlrun.yaml")),
        environment="production",
    )

    try:
        print("Authority escalation")
        finance = control.delegate(
            "head-of-support", narrower("finance-agent", FINANCE_CEILING), by=HEAD
        )
        support = control.delegate(
            finance.delegation_id, narrower("support-agent", SUPPORT_CEILING), by=FINANCE
        )
        print("   human €100,000 delegable  →  finance agent €25,000  →  support agent €2,000")
        print(f"   the support agent's grant: {support.delegation_id}")

        print("   support agent requests €50,000  →")
        try:
            control.execute(
                refund_action(SUPPORT, "txn_1", ESCALATION),
                lambda: remote.refund("txn_1", ESCALATION),
                "refund:txn_1",
            )
        except AuthorityDenied as refused:
            print(f"{BLOCKED}outside the delegated grant ({refused.reason})")
        else:
            raise SystemExit("the escalation was permitted; this example proved nothing")
        print(f"   remote refund calls: {remote.calls.count('txn_1')}")

        print("   finance agent tries to delegate €50,000 under its own €25,000  →")
        try:
            control.delegate(
                finance.delegation_id, narrower("support-agent", ESCALATION), by=FINANCE
            )
        except AuthorityEscalation as refused:
            print(f"{BLOCKED}not contained in its parent ({refused.reason}: {refused.dimension})")
        else:
            raise SystemExit("a widening delegation was created; this example proved nothing")
        print(f"   delegations on record: {len(store.delegations())}")

        # Not an escalation: the amount is inside the grant. Authority permits it and the
        # policy still asks a human, because the two axes combine as the stricter of the pair.
        print("   support agent requests €1,500  →")
        try:
            control.execute(
                refund_action(SUPPORT, "txn_2", WITHIN_GRANT),
                lambda: remote.refund("txn_2", WITHIN_GRANT),
                "refund:txn_2",
            )
        except ApprovalRequired as pending:
            print(f"   authority permits it, and the policy asks a human ({pending.request_id})")
        except AuthorityDenied as refused:  # pragma: no cover - the assertion is the failure
            raise SystemExit(
                f"an amount inside the grant was refused by authority ({refused.reason}); "
                "this example's chain authorizes nothing, so its refusals prove nothing"
            ) from refused
        else:
            raise SystemExit("the €1,500 refund ran without a human; check the policy")
        print(f"   remote refund calls: {remote.calls.count('txn_2')}")
        print("   two axes, and an action needs both: the stricter of the pair wins")
    finally:
        store.close()


if __name__ == "__main__":
    main(Path.cwd())
