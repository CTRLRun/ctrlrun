# Extracted by tools/docs_audit/render_cookbook.py from
# docs/docs/cookbook/crm-update-agent.mdx — edit the page, never this file.
from pathlib import Path

import ctrlrun
from ctrlrun import Control, Policy, SQLiteStateStore

HERE = Path(__file__).resolve().parent
STATE = HERE / ".ctrlrun"
STATE.mkdir(exist_ok=True)
for name in ("state.db", "state.db-wal", "state.db-shm"):
    (STATE / name).unlink(missing_ok=True)

records: dict[str, dict[str, str]] = {"c_1": {"phone": "+353 1 555 0100"}, "c_2": {}}
writes = 0


def crm_update(record_id: str, field: str, value: str) -> str:
    global writes
    writes += 1
    records[record_id][field] = value
    return "updated"


store = SQLiteStateStore(STATE / "state.db")
control = Control(Policy.from_file(HERE / "ctrlrun.yaml"), store)


@ctrlrun.protect(
    "crm.update_record",
    effect="crm:{record_id}:{field}:{revision}",
    resource="record:{record_id}",
    control=control,
)
def update(record_id: str, field: str, value: str, revision: int) -> str:
    return crm_update(record_id, field, value)


@ctrlrun.protect("crm.merge_records", effect="merge:{keep}:{drop}", control=control)
def merge(keep: str, drop: str) -> str:
    return "merged"


@ctrlrun.protect("crm.delete_record", control=control)
def delete(record_id: str) -> str:
    return "deleted"


with ctrlrun.context(agent="support-agent"):
    print(
        "update phone:", update(record_id="c_1", field="phone", value="+353 1 555 0199", revision=7)
    )

    # A second worker handling the same conversation proposes the same revision.
    try:
        update(record_id="c_1", field="phone", value="+353 1 555 0199", revision=7)
    except ctrlrun.DuplicateEffect:
        print("same update from a second worker: refused, already applied")
    else:
        raise SystemExit("one revision was written twice")

    # A later revision is a new effect and runs.
    print(
        "update phone, revision 8:",
        update(record_id="c_1", field="phone", value="+353 1 555 0200", revision=8),
    )

    try:
        merge(keep="c_1", drop="c_2")
    except ctrlrun.ApprovalRequired as pending:
        print("merge c_2 into c_1: a human decides:", pending.request_id)
    else:
        raise SystemExit("a merge ran without a human")

    try:
        delete(record_id="c_2")
    except ctrlrun.ActionDenied:
        print("delete c_2: refused")
    else:
        raise SystemExit("a record was deleted")

print("CRM writes:", writes)
