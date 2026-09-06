# Extracted by tools/docs_audit/render_cookbook.py from
# docs/cookbook/observe-then-enforce.mdx — edit the page, never this file.
from pathlib import Path

import ctrlrun
from ctrlrun import Control, Policy, SQLiteStateStore

HERE = Path(__file__).resolve().parent
STATE = HERE / ".ctrlrun"
STATE.mkdir(exist_ok=True)
for name in ("state.db", "state.db-wal", "state.db-shm"):
    (STATE / name).unlink(missing_ok=True)

executed: list[str] = []
store = SQLiteStateStore(STATE / "state.db")
control = Control(Policy.from_file(HERE / "ctrlrun.yaml"), store)


@ctrlrun.protect("crm.update_record", effect="crm:{record_id}:{field}:{revision}", control=control)
def update(record_id: str, field: str, value: str, revision: int) -> str:
    executed.append("update")
    return "updated"


@ctrlrun.protect("email.send", effect="email:{message_id}", control=control)
def send(message_id: str, to_domain: str) -> str:
    executed.append("send")
    return "sent"


@ctrlrun.protect("stripe.refund", effect="refund:{payment_id}", control=control)
def refund(payment_id: str, amount: int) -> str:
    executed.append("refund")
    return "refunded"


# A week of traffic, compressed.
with ctrlrun.context(agent="support-agent"):
    update(record_id="c_1", field="phone", value="+353 1 555 0100", revision=3)
    send(message_id="m_1", to_domain="example.com")
    send(message_id="m_2", to_domain="gmail.com")  # would have needed a human
    refund(payment_id="txn_1", amount=12000)
    refund(payment_id="txn_2", amount=250000)  # would have needed a human
    refund(payment_id="txn_3", amount=900000)  # would have been denied
    refund(payment_id="txn_1", amount=12000)  # would have been a duplicate

print("actions executed:", len(executed))
if len(executed) != 7:
    raise SystemExit("observe mode blocked something; it must execute everything")

for receipt in store.receipts():
    if receipt.would_have is not None and receipt.would_have.blocked_reason:
        print(f"{receipt.action} would have been blocked: {receipt.would_have.blocked_reason}")
