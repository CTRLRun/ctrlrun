#!/usr/bin/env python3
"""A lost response is not a failure, and a blind retry is a second refund.

The remote commits the refund and *then* the reply goes missing. That order is the whole
point: a remote that fails before doing anything is easy, and an executor can say so by
raising `NotExecuted`. This one is the dangerous kind, so CTRLRun records the outcome as
AMBIGUOUS rather than failed and refuses the retry. Nothing in this process knows whether
the money moved, and the one thing worse than not knowing is guessing.

    python examples/double-refund/main.py

No network, and a state directory of its own under `.ctrlrun/examples/`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ctrlrun import (
    AmbiguousEffect,
    Control,
    Policy,
    SQLiteStateStore,
    context,
    protect,
)

HERE = Path(__file__).resolve().parent
BLOCKED = "   ✗ BLOCKED — "
AMOUNT = 50000  # €500.00, in integer minor units (SPEC-v0.1 §2.3)


class FakeStripe:
    """Commits first, then loses the reply."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def refund(self, payment_id: str, amount: int) -> dict[str, Any]:
        self.calls.append(payment_id)  # the money has moved by the time anything else runs
        raise TimeoutError("no response from api.stripe.com after 30s")


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

    print("Duplicate effect after a lost response")
    print("   refund €500.00  →  remote commits  →  response lost  →  effect: AMBIGUOUS")
    try:
        with context(agent="refund-agent"):
            try:
                refund(payment_id="txn_1", amount=AMOUNT)
            except TimeoutError as lost:
                print(f"   the executor saw:    {lost}")

            print("   agent retries the same refund")
            try:
                refund(payment_id="txn_1", amount=AMOUNT)
            except AmbiguousEffect:
                print(f"{BLOCKED}effect may already have committed; blind retry refused")
            else:
                raise SystemExit("the retry was permitted; this example proved nothing")

        for record in store.list_effects():
            print(f"   effect record:       {record.effect_key}  {record.state}")
        print(f"   remote refund calls: {remote.calls.count('txn_1')}")
        print("   only a human moves it on:  ctrlrun resolve refund:txn_1 --committed|--failed")
    finally:
        store.close()


if __name__ == "__main__":
    main(Path.cwd())
