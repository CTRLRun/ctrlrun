"""A plain MCP client, with no framework at all. SPEC-v0.4 §7.4.

The control row of the whole table. It is the same scenario with nothing between the model's
intent and the remote — no graph, no crew, no runner, no retry policy — so a reader can tell
what a framework contributed from what the protocol does on its own.

It speaks JSON-RPC 2.0 over HTTP to the fake remote's tool endpoint, which is the shape
`tools/call` takes on the Streamable HTTP transport. It is stdlib only, so it runs wherever
the harness does.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from ..scenarios import APPROVAL_MUTATION, Scenario
from .base import Attempt, approve_endpoint, read_version, tool_endpoint

REQUEST_TIMEOUT = 5.0

#: This client does not retry. Whether the *protocol* leaves a retry to the caller is exactly
#: what the row is for.
LOST = (urllib.error.URLError, OSError, TimeoutError)


@dataclass
class MCPClient:
    name: str = "mcp-client"
    distribution: str = "ctrlrun"
    config_deviation: str | None = None
    _calls: list[dict[str, object]] = field(default_factory=list)

    def available(self) -> bool:
        return True

    def version(self) -> str:
        return read_version(self.distribution)

    def run(self, scenario: Scenario, url: str) -> Attempt:
        if scenario.name == APPROVAL_MUTATION:
            assert scenario.approved is not None
            self._post(approve_endpoint(url), scenario.approved)
        envelope = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": scenario.tool_name, "arguments": dict(scenario.arguments)},
        }
        try:
            self._post(tool_endpoint(url), envelope["params"]["arguments"])  # type: ignore[index]
        except LOST as lost:
            return Attempt(
                error=f"{type(lost).__name__}: {lost}",
                notes="no retry: a bare client leaves that to its caller",
            )
        return Attempt()

    def _post(self, target: str, payload: dict[str, object]) -> None:
        request = urllib.request.Request(
            target,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            response.read()


ADAPTER = MCPClient()
