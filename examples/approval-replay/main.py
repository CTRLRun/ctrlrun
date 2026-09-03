#!/usr/bin/env python3
"""An approval is worth exactly one execution.

The human's yes is spent the moment it authorizes a run. An agent that keeps the request id
around — in a scratchpad, in a retry loop, in a replayed transcript — and presents it again
is presenting something that no longer exists. The record says `consumed`, and consumption
happens in the same store transaction as the reservation, so two processes racing on one
approval cannot both win it.

    python examples/approval-replay/main.py

No network, and a state directory of its own under `.ctrlrun/examples/`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ctrlrun import (
    ApprovalMismatch,
    ApprovalRequired,
    Control,
    Policy,
    SQLiteStateStore,
    context,
    protect,
    with_approval,
)

HERE = Path(__file__).resolve().parent
BLOCKED = "   ✗ BLOCKED — "
AMOUNT = 200000  # €2,000.00, in integer minor units — needs a human


class FakeStripe:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def refund(self, payment_id: str, amount: int) -> dict[str, Any]:
        self.calls.append(payment_id)
        return {"id": f"re_{payment_id}", "amount": amount, "status": "succeeded"}


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
    store = SQLiteStateStore(state_dir(root) / "state.db")
    remote = FakeStripe()
    control = Control(Policy.from_file(HERE / "ctrlrun.yaml"), store)

    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def refund(payment_id: str, amount: int) -> dict[str, Any]:
        return remote.refund(payment_id, amount)

    print("Approval replay")
    try:
        with context(agent="refund-agent"):
            try:
                refund(payment_id="txn_4", amount=AMOUNT)
                raise SystemExit("the refund ran without a human; this example proved nothing")
            except ApprovalRequired as pending:
                request_id = pending.request_id

            # What `ctrlrun approve <request_id>` does, in process.
            store.grant_approval(request_id, "human:ops")
            with with_approval(request_id):
                refund(payment_id="txn_4", amount=AMOUNT)

            used = f"approval {request_id} used once"
            print(f"   {used}  →  consumed")
            print(f"   {'same approval presented again':<{len(used)}}  →")

            try:
                with with_approval(request_id):
                    refund(payment_id="txn_4", amount=AMOUNT)
            except ApprovalMismatch as blocked:
                print(f"{BLOCKED}single-use approval already {blocked.reason}")
            else:
                raise SystemExit("the replay was permitted; this example proved nothing")

        print(f"   remote refund calls: {remote.calls.count('txn_4')}")
        print("   the approval is checked before the effect key, so this is why it was refused")
    finally:
        store.close()


if __name__ == "__main__":
    main(Path.cwd())
