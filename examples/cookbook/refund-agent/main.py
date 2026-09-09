# Extracted by CTRLRun/ctrlrun-docs tools/docs_audit/render_cookbook.py from
# docs/cookbook/refund-agent.mdx — edit the page, never this file.
from pathlib import Path

import ctrlrun
from ctrlrun import Control, Policy, SQLiteStateStore

HERE = Path(__file__).resolve().parent
STATE = HERE / ".ctrlrun"
STATE.mkdir(exist_ok=True)
for name in ("state.db", "state.db-wal", "state.db-shm"):  # so the recipe repeats
    (STATE / name).unlink(missing_ok=True)


calls: list[tuple[str, int]] = []


class FakeStripe:
    def refund(self, payment_id: str, amount: int) -> dict:
        calls.append((payment_id, amount))
        return {"id": f"re_{payment_id}", "status": "succeeded"}


stripe = FakeStripe()
store = SQLiteStateStore(STATE / "state.db")
control = Control(Policy.from_file(HERE / "ctrlrun.yaml"), store)


@ctrlrun.protect("stripe.refund", effect="refund:{payment_id}", control=control)
def refund(payment_id: str, amount: int) -> dict:
    return stripe.refund(payment_id, amount)


with ctrlrun.context(agent="support-agent"):
    print("€120 refund:", refund(payment_id="txn_1", amount=12000)["status"])

    try:
        refund(payment_id="txn_2", amount=250000)
    except ctrlrun.ApprovalRequired as pending:
        print("€2,500 refund: a human decides:", pending.request_id)
        store.grant_approval(pending.request_id, "human:ops")  # what `ctrlrun approve` does
        with ctrlrun.with_approval(pending.request_id):
            print("€2,500 refund, approved:", refund(payment_id="txn_2", amount=250000)["status"])
    else:
        raise SystemExit("a €2,500 refund ran without a human")

    try:
        refund(payment_id="txn_3", amount=2000000)
    except ctrlrun.ActionDenied as refused:
        print("€20,000 refund: refused,", refused.reason)
    else:
        raise SystemExit("a €20,000 refund ran")

    try:
        refund(payment_id="txn_1", amount=12000)
    except ctrlrun.DuplicateEffect:
        print("€120 refund again: refused as a duplicate")
    else:
        raise SystemExit("the same refund ran twice")

print("remote refund calls:", len(calls))
