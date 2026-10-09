# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
# Extracted by CTRLRun/ctrlrun-docs tools/docs_audit/render_cookbook.py from
# docs/cookbook/github-merge-agent.mdx — edit the page, never this file.
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

REVIEWED = "a1b2c3d" + "0" * 33
PUSHED = "e4f5a6b" + "0" * 33
PULL = {"owner": "acme", "repo": "checkout", "pullNumber": 4471, "merge_method": "squash"}
pull_request = {"head": REVIEWED, "merged_at": None}
upstream_calls: list[dict] = []

STALE = (
    "failed to merge pull request: 409 Head branch was modified. Review and try the merge again."
)


def github_mcp_server(
    body: bytes, headers: Mapping[str, str], *, fresh: bool
) -> tuple[Any, bytes, int, dict[str, str]]:
    """merge_pull_request as the gateway sees it: GitHub's `sha` check, reported the way the
    server reports any GitHub error, as a tool result with isError."""
    request = json.loads(body)
    upstream_calls.append(request)
    expected = request["params"]["arguments"].get("expectedHeadSha")
    if expected is not None and expected != pull_request["head"]:
        result = {"content": [{"type": "text", "text": STALE}], "isError": True}
    else:
        pull_request["merged_at"] = pull_request["head"]
        merged = json.dumps({"merged": True, "sha": pull_request["head"]})
        result = {"content": [{"type": "text", "text": merged}]}
    reply = {"jsonrpc": "2.0", "id": request["id"], "result": result}
    return (
        UpstreamResult(result_type=COMPLETE, is_error=result.get("isError", False)),
        json.dumps(reply).encode(),
        200,
        {"content-type": "application/json"},
    )


store = SQLiteStateStore(STATE / "state.db")
control = Control(Policy.from_file(HERE / "ctrlrun.yaml"), store)
config = GatewayConfig(
    upstream="http://127.0.0.1:8082/mcp", alias="github", principal="merge-agent"
)
gateway = Gateway(config, control, github_mcp_server)


def merge(rpc_id: int, **arguments: str) -> dict:
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": "tools/call",
            "params": {"name": "merge_pull_request", "arguments": {**PULL, **arguments}},
        }
    ).encode()
    headers = {
        "MCP-Protocol-Version": CURRENT_REVISION,
        "Mcp-Method": "tools/call",
        "Mcp-Name": "merge_pull_request",
        "Content-Type": "application/json",
    }
    response = gateway.handle(body, headers)
    return {"status": response.status, **json.loads(response.body)}


def refused(answer: dict) -> str:
    return f"{answer['status']} {answer['error']['code']} {answer['error']['data']['error']}"


unpinned = merge(1)
if "error" not in unpinned:
    raise SystemExit("a merge with no expectedHeadSha reached the server")
print("merge, no expectedHeadSha:", refused(unpinned))

asked = merge(2, expectedHeadSha=REVIEWED)
if "error" not in asked:
    raise SystemExit("a merge reached the server without a human")
request_id = asked["error"]["data"]["request_id"]
print("merge at a1b2c3d:", refused(asked))
store.grant_approval(request_id, "human:ada@example.com")
print("a human approves the merge at a1b2c3d")

pull_request["head"] = PUSHED
print("a push lands; the head is now e4f5a6b")

swapped = merge(3, expectedHeadSha=PUSHED)
if "error" not in swapped:
    raise SystemExit("a merge at a head nobody approved reached the server")
print("merge at e4f5a6b instead:", refused(swapped))

approved = merge(4, expectedHeadSha=REVIEWED)
if pull_request["merged_at"] is not None:
    raise SystemExit(f"merged at {pull_request['merged_at'][:7]}, which nobody approved")
print("the approved call:", approved["result"]["content"][0]["text"].split(": ", 1)[1])

again = merge(5, expectedHeadSha=REVIEWED)
if "error" not in again:
    raise SystemExit("the call after an unknown outcome reached the server")
print("the approved call again:", refused(again))

print("merges:", 0 if pull_request["merged_at"] is None else 1)
print("calls that reached the server:", len(upstream_calls))
store.close()
