# Extracted by CTRLRun/ctrlrun-docs tools/docs_audit/render_cookbook.py from
# docs/cookbook/customer-notification-agent.mdx — edit the page, never this file.
from pathlib import Path

import ctrlrun
from ctrlrun import Control, Policy, SQLiteStateStore

HERE = Path(__file__).resolve().parent
STATE = HERE / ".ctrlrun"
STATE.mkdir(exist_ok=True)
for name in ("state.db", "state.db-wal", "state.db-shm"):
    (STATE / name).unlink(missing_ok=True)

delivered: list[str] = []
store = SQLiteStateStore(STATE / "state.db")
control = Control(Policy.from_file(HERE / "ctrlrun.yaml"), store)


@ctrlrun.protect("notify.batch", effect="batch:{incident_id}", control=control)
def start_batch(incident_id: str, size: int) -> str:
    return "started"


@ctrlrun.protect("notify.customer", effect="notify:{incident_id}:{customer_id}", control=control)
def notify(incident_id: str, customer_id: str) -> str:
    delivered.append(customer_id)
    return "delivered"


customers = ["c_1", "c_2", "c_3"]

with ctrlrun.context(agent="incident-agent"):
    print("batch of 3:", start_batch(incident_id="inc_7", size=3))
    for customer in customers:
        notify(incident_id="inc_7", customer_id=customer)
    print("first pass delivered:", len(delivered))

    # The batch is retried after a crash, and a second worker runs it at the same time.
    skipped = 0
    for customer in customers + customers:
        try:
            notify(incident_id="inc_7", customer_id=customer)
        except ctrlrun.DuplicateEffect:
            skipped += 1
    print("retry and second worker: skipped", skipped, "already-notified customers")

    try:
        start_batch(incident_id="inc_8", size=12000)
    except ctrlrun.ApprovalRequired as pending:
        print("batch of 12,000: a human decides:", pending.request_id)
    else:
        raise SystemExit("a large batch started without a human")

print("messages delivered:", len(delivered))
