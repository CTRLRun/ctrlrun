#!/usr/bin/env python3
"""Two agents, one effect: the second one loses, and loses before the remote is touched.

Both agents are doing the right thing. They resolve the same effect key, `refund:txn_123`,
because it is the same real-world effect — and a reservation is the right to execute it
once. Agent B arrives while A holds the key and is refused with `DuplicateEffect`.

The race is made deterministic rather than raced for: agent B runs from inside A's remote
call, which is exactly the window that matters and needs no sleeping to hit. Across
processes the guarantee is the same one, enforced by `BEGIN IMMEDIATE` and a unique
constraint on the effect key rather than by a lock in this interpreter.

    python examples/agent-race/main.py

No network, and a state directory of its own under `.ctrlrun/examples/`.
"""

from __future__ import annotations

from collections.abc import Callable
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
AMOUNT = 50000  # €500.00, in integer minor units
KEY = "refund:txn_123"


class FakeStripe:
    """A remote that lets a second agent arrive mid-call, which is what makes this a race."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.on_call: dict[str, Callable[[], None]] = {}

    def refund(self, payment_id: str, amount: int) -> dict[str, Any]:
        self.calls.append(payment_id)
        arriving = self.on_call.pop(payment_id, None)
        if arriving is not None:
            arriving()
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

    refused: list[DuplicateEffect] = []

    def agent_b() -> None:
        with context(agent="refund-agent-b"):
            try:
                refund(payment_id="txn_123", amount=AMOUNT)
            except DuplicateEffect as blocked:
                refused.append(blocked)

    print("Concurrent agents, same effect")
    try:
        remote.on_call["txn_123"] = agent_b
        with context(agent="refund-agent-a"):
            refund(payment_id="txn_123", amount=AMOUNT)

        if not refused:
            raise SystemExit("agent B was permitted; this example proved nothing")

        print(f"   Agent A  reserve {KEY}  →  ACQUIRED  →  executes")
        print(f"   Agent B  reserve {KEY}  →")
        print(f"{BLOCKED}already reserved ({refused[0].state})")
        print(f"   remote refund calls: {remote.calls.count('txn_123')}")
        print("   one effect key, one execution — whichever agent asked first")
    finally:
        store.close()


if __name__ == "__main__":
    main(Path.cwd())
