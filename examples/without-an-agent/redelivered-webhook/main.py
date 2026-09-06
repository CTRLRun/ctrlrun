#!/usr/bin/env python3
"""No model, no agent: a webhook delivered twice, and a payout sent twice.

Every webhook worth receiving is delivered at least once, which is a promise about the
minimum. Stripe, GitHub, Shopify, Slack, SNS and every queue behind them will re-deliver an
event whose acknowledgement they did not see — after a deploy, after a 502, after a handler
that took too long. The handler is called a second time with the same payload, and unless
something outside the handler remembers what the *first* call did, the payout goes twice.

An `Idempotency-Key` header does not help here: the second delivery is a second HTTP request
from a different process, and whatever key the handler generates it generates twice. What is
needed is a name for the consequence that both deliveries compute identically — the effect key
`payout:{payout_id}` — and one atomic reservation of it.

    python examples/without-an-agent/redelivered-webhook/main.py

No network, and a state directory of its own under `.ctrlrun/examples/`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ctrlrun import (
    Control,
    DuplicateEffect,
    Policy,
    SQLiteStateStore,
    context,
    protect,
)

HERE = Path(__file__).resolve().parent
BLOCKED = "   ✗ BLOCKED — "
AMOUNT = 180000  # €1,800.00, in integer minor units (SPEC-v0.1 §2.3)

#: The same event, delivered twice. A re-delivery is byte for byte the first one; that is what
#: makes it a re-delivery and not a second payout somebody actually asked for.
EVENT: dict[str, Any] = {"id": "evt_9f2", "payout_id": "po_412", "amount": AMOUNT}


class FakeBank:
    """Sends the money, successfully. Nothing goes wrong here — that is the point."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, payout_id: str, amount: int) -> dict[str, Any]:
        self.sent.append(payout_id)
        return {"payout_id": payout_id, "amount": amount, "status": "paid"}


def state_dir(root: Path) -> Path:
    """A store of this example's own, emptied so the example repeats.

    An example that reserved effect keys in a live store would block real work. Only the
    files this script writes are removed, and only by name: never a directory.
    """
    evidence = root / ".ctrlrun" / "examples" / "without-an-agent" / HERE.name
    evidence.mkdir(parents=True, exist_ok=True)
    for name in ("state.db", "state.db-wal", "state.db-shm", "receipts.jsonl", "events.jsonl"):
        (evidence / name).unlink(missing_ok=True)
    return evidence


def main(root: Path) -> None:
    store = SQLiteStateStore(state_dir(root) / "state.db")
    bank = FakeBank()
    control = Control(Policy.from_file(HERE / "ctrlrun.yaml"), store)

    @protect("payouts.send", effect="payout:{payout_id}", control=control)
    def handle(payout_id: str, amount: int) -> dict[str, Any]:
        return bank.send(payout_id, amount)

    print("The same webhook, delivered twice, by a provider keeping its promise")
    try:
        with context(agent="webhook-handler"):
            print(f"   delivery 1  {EVENT['id']}  →  payout {EVENT['payout_id']}  €1,800.00")
            handle(payout_id=EVENT["payout_id"], amount=EVENT["amount"])
            print("   the payout is sent, and the acknowledgement never reaches the provider")

            print(f"   delivery 2  {EVENT['id']}  →  the identical payload, a new process")
            try:
                handle(payout_id=EVENT["payout_id"], amount=EVENT["amount"])
            except DuplicateEffect:
                print(f"{BLOCKED}payout:po_412 has already committed; this is one effect")
            else:
                raise SystemExit("the second delivery was permitted; the payout went twice")

        for record in store.list_effects():
            print(f"   effect record:       {record.effect_key}  {record.state}")
        print(f"   bank transfers:      {bank.sent.count('po_412')}")
    finally:
        store.close()


if __name__ == "__main__":
    main(Path.cwd())
