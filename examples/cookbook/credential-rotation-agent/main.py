# Extracted by tools/docs_audit/render_cookbook.py from
# docs/docs/cookbook/credential-rotation-agent.mdx — edit the page, never this file.
from pathlib import Path

import ctrlrun
from ctrlrun import Control, Policy, SQLiteStateStore

HERE = Path(__file__).resolve().parent
STATE = HERE / ".ctrlrun"
STATE.mkdir(exist_ok=True)
for name in ("state.db", "state.db-wal", "state.db-shm"):
    (STATE / name).unlink(missing_ok=True)

live_keys: list[str] = ["key_old"]


def mint(service: str, rotation_id: str, lose_reply: bool = False) -> str:
    key_id = f"key_{rotation_id}"
    live_keys.append(key_id)  # the provider has it from here on
    if lose_reply:
        raise TimeoutError("no response from the secrets manager after 30s")
    return key_id


store = SQLiteStateStore(STATE / "state.db")
control = Control(Policy.from_file(HERE / "ctrlrun.yaml"), store)


@ctrlrun.protect("secrets.create_key", effect="key:{service}:{rotation_id}", control=control)
def create_key(service: str, rotation_id: str, lose_reply: bool = False) -> str:
    return mint(service, rotation_id, lose_reply)


@ctrlrun.protect("secrets.revoke_key", effect="revoke:{service}:{key_id}", control=control)
def revoke_key(service: str, key_id: str) -> str:
    live_keys.remove(key_id)
    return "revoked"


with ctrlrun.context(agent="rotation-agent"):
    print("mint 2026-09:", create_key(service="billing-api", rotation_id="2026-09"))

    try:
        revoke_key(service="billing-api", key_id="key_old")
    except ctrlrun.ApprovalRequired as pending:
        print("revoke key_old: a human decides:", pending.request_id)
    else:
        raise SystemExit("a key was revoked without a human")

    try:
        create_key(service="billing-api", rotation_id="2026-10", lose_reply=True)
    except TimeoutError:
        print("mint 2026-10: reply lost")
    try:
        create_key(service="billing-api", rotation_id="2026-10")
    except ctrlrun.AmbiguousEffect:
        print("mint 2026-10 again: refused; a key may already exist")
    else:
        raise SystemExit("a second key was minted for one rotation")

print("live keys at the provider:", live_keys)
