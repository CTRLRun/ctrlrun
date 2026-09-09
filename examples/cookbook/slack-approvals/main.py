# Extracted by CTRLRun/ctrlrun-docs tools/docs_audit/render_cookbook.py from
# docs/cookbook/slack-approvals.mdx — edit the page, never this file.
import json
from pathlib import Path

import ctrlrun
from ctrlrun import Control, Policy, SQLiteStateStore
from ctrlrun.webhook import handle_inbound, sign

HERE = Path(__file__).resolve().parent
STATE = HERE / ".ctrlrun"
STATE.mkdir(exist_ok=True)
for name in ("state.db", "state.db-wal", "state.db-shm"):
    (STATE / name).unlink(missing_ok=True)

SECRET = "shared-with-the-slack-service"  # from $CTRLRUN_WEBHOOK_SECRET in production
store = SQLiteStateStore(STATE / "state.db")
control = Control(Policy.from_file(HERE / "ctrlrun.yaml"), store)
refunds: list[int] = []


@ctrlrun.protect("stripe.refund", effect="refund:{payment_id}", control=control)
def refund(payment_id: str, amount: int) -> str:
    refunds.append(amount)
    return "refunded"


with ctrlrun.context(agent="support-agent"):
    try:
        refund(payment_id="txn_5", amount=250000)
    except ctrlrun.ApprovalRequired as pending:
        request_id = pending.request_id
        print("€2,500 refund: request", request_id[:4] + "…", "goes to Slack")
    else:
        raise SystemExit("a €2,500 refund ran without a human")

    # What the Slack service POSTs to /ctrlrun/approvals/<request_id> when the lead clicks. It
    # echoes the action hash it showed the human: an answer for a different hash is refused.
    shown = store.get_approval(request_id).request.action_hash
    answer = json.dumps(
        {
            "request_id": request_id,
            "action_hash": shown,
            "decision": "grant",
            "approver": "slack:dana",
        }
    ).encode()
    status, message = handle_inbound(store, request_id, answer, sign(answer, SECRET), secret=SECRET)
    print("the lead approves in Slack:", status, message)

    # A forged answer, signed with the wrong secret, changes nothing.
    forged = json.dumps(
        {
            "request_id": request_id,
            "action_hash": shown,
            "decision": "grant",
            "approver": "slack:nobody",
        }
    ).encode()
    status, message = handle_inbound(
        store, request_id, forged, sign(forged, "guess"), secret=SECRET
    )
    print("a forged answer:", status, message)
    if status == 200:
        raise SystemExit("a forged answer was accepted")

    with ctrlrun.with_approval(request_id):
        print("€2,500 refund, approved in Slack:", refund(payment_id="txn_5", amount=250000))

print("refunds:", refunds)
