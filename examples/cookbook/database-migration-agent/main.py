# Extracted by tools/docs_audit/render_cookbook.py from
# docs/docs/cookbook/database-migration-agent.mdx — edit the page, never this file.
from pathlib import Path

import ctrlrun
from ctrlrun import Control, Policy, SQLiteStateStore

HERE = Path(__file__).resolve().parent
STATE = HERE / ".ctrlrun"
STATE.mkdir(exist_ok=True)
for name in ("state.db", "state.db-wal", "state.db-shm"):
    (STATE / name).unlink(missing_ok=True)

applied: dict[str, list[str]] = {"staging": [], "production": []}


def run_migration(database: str, version: str, drop_connection: bool = False) -> str:
    applied[database].append(version)  # the DDL ran
    if drop_connection:
        raise ConnectionResetError("connection reset by peer")  # ...and the reply was lost
    return "applied"


store = SQLiteStateStore(STATE / "state.db")
control = Control(Policy.from_file(HERE / "ctrlrun.yaml"), store)


@ctrlrun.protect("db.migrate", effect="migration:{database}:{version}", control=control)
def migrate(database: str, version: str, drop_connection: bool = False) -> str:
    return run_migration(database, version, drop_connection)


@ctrlrun.protect("db.rollback", control=control)
def rollback(database: str, version: str) -> str:
    return "rolled back"


with ctrlrun.context(agent="migration-agent"):
    print("0042 on staging:", migrate(database="staging", version="0042"))

    try:
        migrate(database="production", version="0042")
    except ctrlrun.ApprovalRequired as pending:
        print("0042 on production: a human decides:", pending.request_id)
    else:
        raise SystemExit("a production migration ran without a human")

    try:
        rollback(database="production", version="0041")
    except ctrlrun.ActionDenied:
        print("rollback on production: refused")
    else:
        raise SystemExit("a rollback ran")

    try:
        migrate(database="staging", version="0043", drop_connection=True)
    except ConnectionResetError:
        print("0043 on staging: the connection dropped; outcome unknown")
    try:
        migrate(database="staging", version="0043")
    except ctrlrun.AmbiguousEffect:
        print("0043 on staging again: refused; a human checks the schema first")
    else:
        raise SystemExit("a migration of unknown outcome was re-run")

print("migrations applied on staging:", applied["staging"])
