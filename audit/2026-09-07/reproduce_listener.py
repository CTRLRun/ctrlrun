"""Local-loopback-only listener probes. Requires permission to bind local sockets."""

import http.client
import json
import threading
from collections.abc import Mapping

from ctrlrun import Control, InMemoryStateStore, Policy
from ctrlrun.gateway.outcome import UpstreamStatus
from ctrlrun.gateway.server import Gateway, GatewayConfig, build_server


def gateway(config: GatewayConfig, calls: list[bytes]) -> Gateway:
    def forward(
        body: bytes, headers: Mapping[str, str], *, fresh: bool
    ) -> tuple[UpstreamStatus, bytes, int, dict[str, str]]:
        calls.append(body)
        return UpstreamStatus(202), b"", 202, {}

    def request(
        method: str, body: bytes, headers: Mapping[str, str], *, fresh: bool = False
    ) -> tuple[UpstreamStatus, bytes, int, dict[str, str]]:
        return forward(body, headers, fresh=fresh)

    forward.request = request  # type: ignore[attr-defined]
    control = Control(
        Policy.from_yaml("schema: ctrlrun.policy/v1\nactions: {}"), InMemoryStateStore()
    )
    return Gateway(config, control, forward)


if __name__ == "__main__":
    calls = []
    config = GatewayConfig(
        upstream="http://127.0.0.1:9999/mcp", alias="audit", principal="audit-agent", port=0
    )
    server = build_server(gateway(config, calls))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for method in ("GET", "DELETE"):
            client = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=2)
            try:
                client.request(method, "/mcp", headers={"MCP-Protocol-Version": "2025-11-25"})
                response = client.getresponse()
                response.read()
                assert response.status == 202
                assert len(calls) == (1 if method == "GET" else 2)
                print(
                    json.dumps(
                        {"method": method, "status": response.status, "forwarded_calls": len(calls)}
                    )
                )
            finally:
                client.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    config6 = GatewayConfig(
        upstream="http://127.0.0.1:9999/mcp",
        alias="audit",
        principal="audit-agent",
        host="::1",
        port=0,
    )
    server6 = build_server(gateway(config6, []))
    try:
        assert server6.server_port > 0
        print(json.dumps({"host": "::1", "configuration_accepted": True, "bound": True}))
    finally:
        server6.server_close()
