import sys

import ctrlrun

from agent import refund, stripe

with open("request_id.txt") as handle:
    request_id = handle.read().strip()

with ctrlrun.context(agent="support-agent"), ctrlrun.with_approval(request_id):
    # Exactly what the human read: €5,000 on txn_2.
    print("€5,000 with the approval ->", refund(payment_id="txn_2", amount=500_000)["status"])

    # The same approval, one digit changed.
    try:
        refund(payment_id="txn_2", amount=900_000)
    except ctrlrun.ApprovalMismatch:
        print("€9,000 on that same approval -> refused")
    else:
        sys.exit("a mutated action ran on a human's approval; that is the bug this exists to stop")

print("calls that reached the provider:", len(stripe.calls), "(the €9,000 never left)")
