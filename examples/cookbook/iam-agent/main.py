# Extracted by tools/docs_audit/render_cookbook.py from
# docs/cookbook/iam-agent.mdx — edit the page, never this file.
from pathlib import Path

import ctrlrun
from ctrlrun import Control, Policy, SQLiteStateStore

HERE = Path(__file__).resolve().parent
STATE = HERE / ".ctrlrun"
STATE.mkdir(exist_ok=True)
for name in ("state.db", "state.db-wal", "state.db-shm"):
    (STATE / name).unlink(missing_ok=True)

bindings: list[tuple[str, str, str]] = []


def bind(project: str, principal: str, role: str) -> str:
    bindings.append((project, principal, role))
    return "bound"


store = SQLiteStateStore(STATE / "state.db")
control = Control(Policy.from_file(HERE / "ctrlrun.yaml"), store)


@ctrlrun.protect(
    "iam.grant_role",
    effect="grant:{principal}:{role}",
    resource="project:{project}",
    control=control,
)
def grant(project: str, principal: str, role: str) -> str:
    return bind(project, principal, role)


with ctrlrun.context(agent="access-agent"):
    print(
        "reader on billing:", grant(project="billing", principal="ana@example.com", role="reader")
    )

    try:
        grant(project="billing", principal="ana@example.com", role="editor")
    except ctrlrun.ApprovalRequired as pending:
        print("editor on billing: a human decides:", pending.request_id)
        request_id = pending.request_id
    else:
        raise SystemExit("editor was granted without a human")

    store.grant_approval(request_id, "human:security")
    with ctrlrun.with_approval(request_id):
        try:
            grant(project="billing", principal="ana@example.com", role="admin")
        except ctrlrun.ActionDenied:
            print("admin with the editor approval: refused; admin is never an agent action")
        else:
            raise SystemExit("admin was granted on an approval for editor")
        print(
            "editor with the editor approval:",
            grant(project="billing", principal="ana@example.com", role="editor"),
        )

print("bindings:", bindings)
