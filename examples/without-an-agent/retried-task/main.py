#!/usr/bin/env python3
"""No model, no agent, no prompt: a task queue with automatic retry, charging twice.

This is the same failure as `examples/double-refund/`, written the way it actually reaches
production — the retry is not a line somebody wrote, it is the queue's `max_retries`. Celery,
RQ, Sidekiq, Temporal's activity retries, `tenacity`, a Lambda that a failed invocation
re-delivers: each of them re-runs the task when it raises, and each of them re-runs it just as
willingly when the exception means *I do not know what happened* as when it means *nothing
happened*.

The provider commits the charge and then the reply is lost. The queue does what it is
configured to do. CTRLRun records the outcome as AMBIGUOUS rather than failed, and the second
attempt is refused before it reaches the provider.

    python examples/without-an-agent/retried-task/main.py

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
AMOUNT = 24900  # €249.00, in integer minor units (SPEC-v0.1 §2.3)
MAX_RETRIES = 3  # what the queue is configured to do, not what this script decides


class FakeProcessor:
    """Commits the charge, then loses the reply. The dangerous order, on purpose."""

    def __init__(self) -> None:
        self.charged: list[str] = []

    def charge(self, invoice_id: str, amount: int) -> dict[str, Any]:
        self.charged.append(invoice_id)  # the money has moved by the time anything else runs
        raise TimeoutError("no response from the payment processor after 30s")


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
    processor = FakeProcessor()
    control = Control(Policy.from_file(HERE / "ctrlrun.yaml"), store)

    @protect("billing.charge", effect="charge:{invoice_id}", control=control)
    def charge(invoice_id: str, amount: int) -> dict[str, Any]:
        return processor.charge(invoice_id, amount)

    print("A retrying task queue, with nothing agentic anywhere in it")
    print(f"   worker picks up charge_invoice(inv_88)  →  max_retries={MAX_RETRIES}")
    try:
        refusals = 0
        # The retry loop is the queue's, not this script's: the task raises and is re-queued.
        with context(agent="billing-worker"):
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    charge(invoice_id="inv_88", amount=AMOUNT)
                except TimeoutError as lost:
                    print(f"   attempt {attempt}: the task raised   {lost}")
                except AmbiguousEffect:
                    refusals += 1
                    print(f"{BLOCKED}attempt {attempt}: effect may already have committed")

        if refusals != MAX_RETRIES - 1:
            raise SystemExit(f"the queue was allowed to retry; {refusals} attempts were refused")

        for record in store.list_effects():
            print(f"   effect record:       {record.effect_key}  {record.state}")
        print(f"   processor charges:   {processor.charged.count('inv_88')}")
        print("   only a human moves it on:  ctrlrun resolve charge:inv_88 --committed|--failed")
    finally:
        store.close()


if __name__ == "__main__":
    main(Path.cwd())
