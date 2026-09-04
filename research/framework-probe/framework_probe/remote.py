"""The fake remote every adapter runs against. SPEC-v0.4 §7.2, §7.3 rule 1.

One remote, three behaviours, the same for every framework:

- **commit-then-drop** — the effect is recorded and the connection is closed without a
  response. This is what a lost reply looks like from the client's side, and it costs no
  wall-clock time, which is why it is the default.
- **commit-then-timeout** — the effect is recorded and the response is withheld until after
  the client has given up.
- **capture-what-was-approved** — the remote records what a human approved and, separately,
  what was executed, so a mutation between the two is visible without asking the framework.

It counts **effects by identity, not by request**: "executed twice" means two effects, not two
HTTP calls. A framework that retried and got a duplicate rejection at the remote did not
execute twice, and a framework that sent one request the remote committed twice did.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

#: The three behaviours of §7.2, by name. Closed: an adapter may not invent a fourth.
COMMIT_THEN_DROP = "commit_then_drop"
COMMIT_THEN_TIMEOUT = "commit_then_timeout"
CAPTURE_APPROVAL = "capture_approval"
BEHAVIOURS = (COMMIT_THEN_DROP, COMMIT_THEN_TIMEOUT, CAPTURE_APPROVAL)

#: How long `commit_then_timeout` withholds a response. Bounded, like every other loop in this
#: repository, so a wedged probe fails rather than hanging.
TIMEOUT_SECONDS = 5.0


@dataclass
class State:
    """What the remote saw. The runner derives every outcome from this and never from what an
    adapter says about itself — an adapter that graded its own run would be the framework
    marking its own homework."""

    effects: list[dict[str, Any]] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)
    approved: dict[str, Any] | None = None

    def snapshot(self) -> State:
        return State(
            effects=[dict(effect) for effect in self.effects],
            requests=[dict(request) for request in self.requests],
            approved=None if self.approved is None else dict(self.approved),
        )


class FakeRemote:
    """A local HTTP server on 127.0.0.1, on a port the OS chose (§7.3 rule 1).

    Same server, same behaviour and the same port discipline for every framework: each run
    gets a fresh instance, so nothing an earlier adapter did is visible to a later one.
    """

    def __init__(self, behaviour: str = COMMIT_THEN_DROP) -> None:
        if behaviour not in BEHAVIOURS:
            raise ValueError(f"unknown behaviour {behaviour!r}; expected one of {BEHAVIOURS}")
        self.behaviour = behaviour
        self.state = State()
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(self))
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self) -> FakeRemote:
        self._thread.start()
        return self

    def __exit__(self, *exception: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=10)

    # --- what the handler calls -----------------------------------------------------------

    def record_request(self, path: str, body: dict[str, Any]) -> int:
        with self._lock:
            self.state.requests.append({"path": path, "body": body})
            return len(self.state.requests)

    def commit(self, arguments: dict[str, Any], request_number: int) -> None:
        """Record one effect, identified by the thing it acts on rather than by the call."""
        with self._lock:
            self.state.effects.append(
                {
                    "identity": arguments.get("payment_id"),
                    "arguments": dict(arguments),
                    "request": request_number,
                }
            )

    def approve(self, arguments: dict[str, Any]) -> None:
        with self._lock:
            self.state.approved = dict(arguments)

    def snapshot(self) -> State:
        with self._lock:
            return self.state.snapshot()


def _handler(remote: FakeRemote) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:
            """Silent. The probe's output is the results file, not a server log."""

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                body = {}
            if not isinstance(body, dict):
                body = {}

            if self.path.rstrip("/").endswith("/approve"):
                remote.approve(body)
                self._respond({"approved": body})
                return

            number = remote.record_request(self.path, body)
            remote.commit(body, number)

            if remote.behaviour == COMMIT_THEN_DROP:
                # The effect happened and the caller will never hear about it. The connection
                # closes with no bytes written, which is what a lost reply looks like from the
                # far side and is the whole scenario. The client sees `RemoteDisconnected`.
                self.close_connection = True
                return
            if remote.behaviour == COMMIT_THEN_TIMEOUT:
                time.sleep(TIMEOUT_SECONDS)
            self._respond({"committed": True, "request": number})

        def do_GET(self) -> None:
            if self.path.rstrip("/").endswith("/_probe/state"):
                snapshot = remote.snapshot()
                self._respond(
                    {
                        "effects": snapshot.effects,
                        "requests": snapshot.requests,
                        "approved": snapshot.approved,
                    }
                )
                return
            self.send_error(404)

        def _respond(self, payload: dict[str, Any]) -> None:
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return Handler
