"""Verify duplicate prevention through real loopback HTTP and a temporary SQLite store."""

import http.client
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import ClassVar

from reproduce_findings import CALL, HEADERS, POLICY

from ctrlrun import Control, Policy, SQLiteStateStore
from ctrlrun.gateway.server import Gateway, GatewayConfig, build_server, httpx_forwarder


class Upstream(BaseHTTPRequestHandler):
    effects: ClassVar[list[object]] = []

    def do_POST(self) -> None:
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.effects.append(request["params"]["arguments"])
        body = json.dumps(
            {"jsonrpc": "2.0", "id": 999, "error": {"code": -32602, "message": "unrelated request"}}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


if __name__ == "__main__":
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    try:
        with TemporaryDirectory(prefix="ctrlrun-audit-") as directory:
            store = SQLiteStateStore(Path(directory) / "state.db")
            config = GatewayConfig(
                upstream=f"http://127.0.0.1:{upstream.server_port}/mcp",
                alias="audit",
                principal="audit-agent",
                port=0,
            )
            forwarder = httpx_forwarder(config)
            server = build_server(
                Gateway(config, Control(Policy.from_yaml(POLICY), store), forwarder)
            )
            gateway_thread = threading.Thread(target=server.serve_forever, daemon=True)
            gateway_thread.start()
            try:
                responses = []
                for _ in range(2):
                    client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                    try:
                        client.request("POST", "/mcp", body=json.dumps(CALL), headers=HEADERS)
                        response = client.getresponse()
                        responses.append(json.loads(response.read()))
                    finally:
                        client.close()
                record = store.get_effect("refund:txn_1")
                assert len(Upstream.effects) == 1
                assert (
                    record is not None and record.state.value == "ambiguous" and record.attempt == 1
                )
                print(
                    json.dumps(
                        {
                            "transport": "real loopback HTTP",
                            "store": "SQLite",
                            "remote_effects": len(Upstream.effects),
                            "effect_state": str(record.state),
                            "attempts": record.attempt,
                            "response_ids": [reply["id"] for reply in responses],
                        }
                    )
                )
            finally:
                server.shutdown()
                server.server_close()
                gateway_thread.join(timeout=2)
                forwarder.close()
                store.close()
    finally:
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=2)
