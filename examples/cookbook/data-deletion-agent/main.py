# Extracted by CTRLRun/ctrlrun-docs tools/docs_audit/render_cookbook.py from
# docs/cookbook/data-deletion-agent.mdx — edit the page, never this file.
from pathlib import Path

import ctrlrun
from ctrlrun import Control, Policy, SQLiteStateStore

HERE = Path(__file__).resolve().parent
STATE = HERE / ".ctrlrun"
STATE.mkdir(exist_ok=True)
for name in ("state.db", "state.db-wal", "state.db-shm"):
    (STATE / name).unlink(missing_ok=True)

purged: list[str] = []


def purge_at_warehouse(record_id: str, lose_reply: bool = False) -> str:
    purged.append(record_id)
    if lose_reply:
        raise TimeoutError("warehouse did not answer in 30s")
    return "purged"


store = SQLiteStateStore(STATE / "state.db")
control = Control(Policy.from_file(HERE / "ctrlrun.yaml"), store)


@ctrlrun.protect(
    "records.purge", effect="purge:{record_id}", resource="record:{record_id}", control=control
)
def purge(record_id: str, age_days: int, legal_hold: bool, lose_reply: bool = False) -> str:
    return purge_at_warehouse(record_id, lose_reply)


with ctrlrun.context(agent="deletion-agent"):
    print("r_100, 900 days old:", purge(record_id="r_100", age_days=900, legal_hold=False))

    try:
        purge(record_id="r_101", age_days=400, legal_hold=False)
    except ctrlrun.ApprovalRequired as pending:
        print("r_101, 400 days old: a human decides:", pending.request_id)
    else:
        raise SystemExit("a record inside retention was purged without a human")

    try:
        purge(record_id="r_102", age_days=900, legal_hold=True)
    except ctrlrun.ActionDenied:
        print("r_102, under legal hold: refused")
    else:
        raise SystemExit("a record under legal hold was purged")

    try:
        purge(record_id="r_103", age_days=900, legal_hold=False, lose_reply=True)
    except TimeoutError:
        print("r_103: reply lost")
    try:
        purge(record_id="r_103", age_days=900, legal_hold=False)
    except ctrlrun.AmbiguousEffect:
        print("r_103 again: refused until someone checks the warehouse")
    else:
        raise SystemExit("a purge of unknown outcome was repeated")

print("purge calls at the warehouse:", purged)
