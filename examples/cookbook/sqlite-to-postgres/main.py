# Extracted by tools/docs_audit/render_cookbook.py from
# docs/docs/cookbook/sqlite-to-postgres.mdx — edit the page, never this file.
import os
from pathlib import Path

import ctrlrun
from ctrlrun import Control, Policy, SQLiteStateStore, StateStore

HERE = Path(__file__).resolve().parent
STATE = HERE / ".ctrlrun"
STATE.mkdir(exist_ok=True)
for name in ("state.db", "state.db-wal", "state.db-shm"):
    (STATE / name).unlink(missing_ok=True)


def open_store() -> StateStore:
    url = os.environ.get("CTRLRUN_STORE_URL")
    if url and url.startswith(("postgresql://", "postgres://")):
        from ctrlrun.postgres import PostgresStateStore  # pip install "ctrlrun[postgres]"

        return PostgresStateStore(url, schema="ctrlrun")  # migrates at open, forward only
    return SQLiteStateStore(STATE / "state.db")


store = open_store()
control = Control(Policy.from_file(HERE / "ctrlrun.yaml"), store)
refunds: list[str] = []


@ctrlrun.protect("stripe.refund", effect="refund:{payment_id}", control=control)
def refund(payment_id: str, amount: int) -> str:
    refunds.append(payment_id)
    return "refunded"


with ctrlrun.context(agent="refund-agent"):
    print("store:", type(store).__name__)
    print("refund txn_1:", refund(payment_id="txn_1", amount=12000))
    try:
        refund(payment_id="txn_1", amount=12000)  # a second host, same effect
    except ctrlrun.DuplicateEffect:
        print("refund txn_1 from another host: refused")
    else:
        raise SystemExit("one effect committed twice")

print("remote refund calls:", len(refunds))
store.close()
