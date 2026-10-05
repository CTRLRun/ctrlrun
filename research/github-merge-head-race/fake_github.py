# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""A fake GitHub REST API: one pull request, and the merge endpoint's documented semantics.

`PUT /api/v3/repos/{owner}/{repo}/pulls/{number}/merge` answers as GitHub documents it: with a
`sha` that is not the pull request's current head it is `409 Head branch was modified` and
nothing is merged; without a `sha` it merges whatever the head is now; on a pull request that is
already merged it is `405 Pull Request is not mergeable`.

Every merge request is a **provider call**. Every merge that happened is a **mutation**, recorded
with the head it landed at. Nothing is inferred from what the MCP server or the gateway says
about itself.

The `/_test/` endpoints move the head, arm a lost response and read the counters. A request for
any other path is recorded under `unknown`, so a stack that quietly starts calling something
this fake does not model fails the run instead of being answered with a 404 nobody reads.
"""

from __future__ import annotations

import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MERGE = re.compile(r"^/api/v3/repos/([^/]+)/([^/]+)/pulls/(\d+)/merge$")
INITIAL_HEAD = "a" * 40

_LOCK = threading.Lock()


def _fresh() -> dict[str, Any]:
    return {
        "head": INITIAL_HEAD,
        "merged": False,
        "provider_calls": 0,
        "mutations": [],
        "drop_next_response": False,
        "unknown": [],
    }


STATE: dict[str, Any] = _fresh()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _send(self, status: int, body: dict[str, Any]) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        return json.loads(raw) if raw else {}

    def _unknown(self, method: str) -> None:
        with _LOCK:
            STATE["unknown"].append(f"{method} {self.path}")
        self._send(404, {"message": "Not Found"})

    def do_GET(self) -> None:
        if self.path != "/_test/state":
            return self._unknown("GET")
        with _LOCK:
            return self._send(200, STATE)

    def do_POST(self) -> None:
        body = self._body()
        with _LOCK:
            if self.path == "/_test/reset":
                STATE.clear()
                STATE.update(_fresh())
                return self._send(200, STATE)
            if self.path == "/_test/push":
                STATE["head"] = body["sha"]
                return self._send(200, STATE)
            if self.path == "/_test/drop_next_response":
                STATE["drop_next_response"] = True
                return self._send(200, STATE)
        return self._unknown("POST")

    def do_PUT(self) -> None:
        body = self._body()
        if not MERGE.match(self.path):
            return self._unknown("PUT")
        with _LOCK:
            STATE["provider_calls"] += 1
            if STATE["merged"]:
                return self._send(405, {"message": "Pull Request is not mergeable"})
            expected = body.get("sha")
            if expected is not None and expected != STATE["head"]:
                return self._send(
                    409, {"message": "Head branch was modified. Review and try the merge again."}
                )
            STATE["merged"] = True
            STATE["mutations"].append(STATE["head"])
            drop = STATE["drop_next_response"]
            STATE["drop_next_response"] = False
            landed = STATE["head"]
        if drop:
            # The merge has landed. The reply never leaves.
            self.close_connection = True
            self.connection.shutdown(2)
            return None
        merged = {"sha": "m" + landed[1:], "merged": True}
        return self._send(200, {**merged, "message": "Pull Request successfully merged"})


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
