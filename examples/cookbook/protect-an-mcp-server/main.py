# Extracted by tools/docs_audit/render_cookbook.py from
# docs/docs/cookbook/protect-an-mcp-server.mdx — edit the page, never this file.
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ctrlrun import Control, Policy, SQLiteStateStore
from ctrlrun.gateway.mcp import CURRENT_REVISION
from ctrlrun.gateway.outcome import COMPLETE, UpstreamResult
from ctrlrun.gateway.server import Gateway, GatewayConfig

HERE = Path(__file__).resolve().parent
STATE = HERE / ".ctrlrun"
STATE.mkdir(exist_ok=True)
for name in ("state.db", "state.db-wal", "state.db-shm"):
    (STATE / name).unlink(missing_ok=True)

upstream_calls: list[dict] = []


def upstream(
    body: bytes, headers: Mapping[str, str], *, fresh: bool
) -> tuple[Any, bytes, int, dict[str, str]]:
    """The MCP server, as the gateway sees it: a stand-in that answers every tools/call."""
    request = json.loads(body)
    upstream_calls.append(request)
    reply = {
        "jsonrpc": "2.0",
        "id": request["id"],
        "result": {"content": [{"type": "text", "text": "ok"}]},
    }
    return (
        UpstreamResult(result_type=COMPLETE),
        json.dumps(reply).encode(),
        200,
        {"content-type": "application/json"},
    )


store = SQLiteStateStore(STATE / "state.db")
control = Control(Policy.from_file(HERE / "ctrlrun.yaml"), store)
config = GatewayConfig(upstream="http://localhost:8000/mcp", alias="ops", principal="oncall-agent")
gateway = Gateway(config, control, upstream)


def call(tool: str, arguments: dict, rpc_id: int) -> dict:
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }
    ).encode()
    headers = {
        "MCP-Protocol-Version": CURRENT_REVISION,
        "Mcp-Method": "tools/call",
        "Mcp-Name": tool,
        "Content-Type": "application/json",
    }
    response = gateway.handle(body, headers)
    return {"status": response.status, **json.loads(response.body)}


first = call("restart_deployment", {"cluster": "prod-eu", "name": "checkout"}, 1)
print("restart_deployment:", first["status"], first["result"]["content"][0]["text"])

again = call("restart_deployment", {"cluster": "prod-eu", "name": "checkout"}, 2)
print(
    "the same restart again:",
    again["status"],
    again["error"]["code"],
    again["error"]["data"]["error"],
)
if "error" not in again:
    raise SystemExit("a duplicate restart reached the upstream")

held = call("delete_namespace", {"cluster": "prod-eu", "name": "checkout"}, 3)
print(
    "delete_namespace:",
    held["status"],
    held["error"]["code"],
    held["error"]["data"]["error"],
    "request",
    held["error"]["data"]["request_id"][:4] + "…",
)
if "error" not in held:
    raise SystemExit("a namespace delete reached the upstream without a human")

unknown = call("drop_database", {"name": "prod"}, 4)
print(
    "drop_database (not in the policy):",
    unknown["status"],
    unknown["error"]["code"],
    unknown["error"]["data"]["error"],
)

print("calls that reached the upstream:", len(upstream_calls))
store.close()
