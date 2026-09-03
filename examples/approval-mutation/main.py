#!/usr/bin/env python3
"""An approval authorizes one exact action, not a category of action.

A human sees "refund €2,000 on txn_2" and says yes. The agent then executes €5,000 with
that same approval — the sort of thing a prompt injection asks for, and the sort of thing a
loop does by accident after re-planning. The approval is bound to the action's hash, which
covers the principal, the arguments, the resource and the environment, so the mutated call
matches nothing and is refused before the remote is touched.

    python examples/approval-mutation/main.py

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
PROPOSED = 200000  # €2,000.00 — what the human approves
MUTATED = 500000  # €5,000.00 — what the agent tries to run instead


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

    print("Approval mutation")
    try:
        with context(agent="refund-agent"):
            # A refund this size needs a human, so the first call raises rather than running.
            try:
                refund(payment_id="txn_2", amount=PROPOSED)
                raise SystemExit("the refund ran without a human; this example proved nothing")
            except ApprovalRequired as pending:
                request_id = pending.request_id

            # What `ctrlrun approve <request_id>` does, in process.
            store.grant_approval(request_id, "human:ops")
            print(f"   agent proposes refund €2,000.00  →  human approves {request_id}")
            print("   agent executes refund €5,000.00  →")

            try:
                with with_approval(request_id):
                    refund(payment_id="txn_2", amount=MUTATED)
            except ApprovalMismatch as blocked:
                print(f"{BLOCKED}approved action ≠ requested action ({blocked.reason})")
            else:
                raise SystemExit("the mutation was permitted; this example proved nothing")

        print(f"   remote refund calls: {len(remote.calls)}")
        print("   the approval is still granted for the action the human actually saw")
    finally:
        store.close()


if __name__ == "__main__":
    main(Path.cwd())
