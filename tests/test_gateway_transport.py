"""Regression coverage for the September user-impact audit, through real HTTP sockets."""

import json
import socket
import threading
import time
from contextlib import suppress
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from ctrlrun import Control, EffectState, Policy, SQLiteStateStore
from ctrlrun.gateway.server import Gateway, GatewayConfig, build_server, httpx_forwarder

httpx = pytest.importorskip("httpx")

POLICY = """
schema: ctrlrun.policy/v2
actions:
  mcp.audit.refund:
    effect: 'refund:{payment_id}'
    decision: allow
"""
HEADERS = {
    "MCP-Protocol-Version": "2026-07-28",
    "Mcp-Method": "tools/call",
    "Mcp-Name": "refund",
}


def call(rpc_id=42):
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "method": "tools/call",
        "params": {
            "name": "refund",
            "arguments": {"payment_id": "txn_1"},
        },
    }


def success(rpc_id=42):
    return {"jsonrpc": "2.0", "id": rpc_id, "result": {"resultType": "complete", "content": []}}


def reply(handler, document=None, *, status=200, headers=None, body=None):
    payload = json.dumps(document).encode() if body is None else body
    handler.send_response(status)
    for name, value in (headers or {"Content-Type": "application/json"}).items():
        handler.send_header(name, value)
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def stream_headers(handler):
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.close_connection = True


def event(handler, document, *, event_id="upstream-17"):
    handler.wfile.write(
        f"id: {event_id}\nevent: message\ndata: {json.dumps(document)}\n\n".encode()
    )
    handler.wfile.flush()


@pytest.fixture
def gateway_http(tmp_path):
    state = SimpleNamespace(calls=[], release=threading.Event())
    state.respond = lambda handler, document: reply(handler, success(document.get("id")))

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _dispatch(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            document = json.loads(raw) if raw else {}
            state.calls.append((self.command, dict(self.headers.items()), document))
            with suppress(BrokenPipeError, ConnectionResetError):
                state.respond(self, document)

        def do_POST(self):
            self._dispatch()

        def do_GET(self):
            self._dispatch()

        def do_DELETE(self):
            self._dispatch()

        def log_message(self, *args):
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    upstream.daemon_threads = True
    worker = threading.Thread(target=upstream.serve_forever, daemon=True)
    worker.start()
    store = SQLiteStateStore(tmp_path / "state.db")
    config = GatewayConfig(
        upstream=f"http://127.0.0.1:{upstream.server_port}/mcp",
        alias="audit",
        principal="audit-agent",
        port=0,
        upstream_timeout=2,
    )
    forwarder = httpx_forwarder(config)
    gateway = Gateway(config, Control(Policy.from_yaml(POLICY), store), forwarder)
    listener = build_server(gateway)
    serving = threading.Thread(target=listener.serve_forever, daemon=True)
    serving.start()
    client = httpx.Client(
        base_url=f"http://127.0.0.1:{listener.server_port}", timeout=4, trust_env=False
    )
    state.client, state.store, state.gateway = client, store, gateway
    yield state
    state.release.set()
    client.close()
    listener.shutdown()
    listener.server_close()
    serving.join(timeout=2)
    forwarder.close()
    upstream.shutdown()
    upstream.server_close()
    worker.join(timeout=2)
    store.close()


@pytest.mark.parametrize("response_id", [999, "42", 42.0, True, None, "missing"])
@pytest.mark.parametrize("result_kind", ["result", "error"])
def test_wrong_response_id_never_releases_an_executed_effect(
    gateway_http, response_id, result_kind
):
    h = gateway_http
    remote_effects = []

    def respond(handler, document):
        remote_effects.append(document["params"]["arguments"])
        response = {
            "jsonrpc": "2.0",
            "id": response_id,
            result_kind: (
                {"code": -32602, "message": "another request failed validation"}
                if result_kind == "error"
                else {"resultType": "complete", "content": []}
            ),
        }
        if response_id == "missing":
            response.pop("id")
        reply(handler, response)

    h.respond = respond
    first = h.client.post("/mcp", json=call(), headers=HEADERS)
    assert first.status_code == 502
    assert first.json()["id"] == 42
    assert first.json()["error"]["code"] == -41010
    assert h.store.get_effect("refund:txn_1").state is EffectState.AMBIGUOUS
    second = h.client.post("/mcp", json=call(), headers=HEADERS)
    assert second.status_code == 409
    assert len(remote_effects) == 1


@pytest.mark.parametrize("revision", ["2025-03-26", "2025-06-18", "2025-11-25"])
def test_legacy_success_is_committed(gateway_http, revision):
    h = gateway_http
    h.respond = lambda handler, document: reply(
        handler,
        {
            "jsonrpc": "2.0",
            "id": document["id"],
            "result": {"content": [], "isError": False},
        },
    )
    response = h.client.post("/mcp", json=call(), headers={"MCP-Protocol-Version": revision})
    assert response.status_code == 200
    assert "resultType" not in response.json()["result"]
    assert h.store.get_effect("refund:txn_1").state is EffectState.COMMITTED


def test_current_revision_without_result_type_stays_ambiguous(gateway_http):
    h = gateway_http
    h.respond = lambda handler, document: reply(
        handler,
        {
            "jsonrpc": "2.0",
            "id": document["id"],
            "result": {"content": []},
        },
    )
    h.client.post("/mcp", json=call(), headers=HEADERS)
    assert h.store.get_effect("refund:txn_1").state is EffectState.AMBIGUOUS


@pytest.mark.parametrize(
    "document",
    [
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": "server-request", "result": {"action": "accept"}},
    ],
)
def test_empty_acknowledgement_is_relayed(gateway_http, document):
    h = gateway_http
    h.respond = lambda handler, document: reply(
        handler,
        status=202,
        body=b"",
        headers={
            "Mcp-Session-Id": "session-1",
        },
    )
    response = h.client.post("/mcp", json=document, headers={"MCP-Protocol-Version": "2025-11-25"})
    assert response.status_code == 202 and response.content == b""
    assert response.headers["Mcp-Session-Id"] == "session-1"
    assert h.store.receipts() == ()


@pytest.mark.parametrize("method", ["GET", "DELETE"])
def test_transport_methods_preserve_upstream_response_and_session_headers(gateway_http, method):
    h = gateway_http
    h.respond = lambda handler, document: reply(
        handler,
        status=204,
        body=b"",
        headers={
            "Mcp-Session-Id": "session-1",
        },
    )
    response = h.client.request(
        method,
        "/mcp",
        headers={
            "MCP-Protocol-Version": "2025-11-25",
            "Mcp-Session-Id": "session-1",
            "Last-Event-ID": "7",
        },
    )
    assert response.status_code == 204 and response.content == b""
    received_method, headers, _ = h.calls[0]
    assert received_method == method
    assert headers["Mcp-Session-Id"] == "session-1" and headers["Last-Event-ID"] == "7"
    assert response.headers["Mcp-Session-Id"] == "session-1"
    assert h.store.events() == ()


@pytest.mark.parametrize("method", ["GET", "DELETE"])
@pytest.mark.parametrize(
    "path,headers,status",
    [
        ("/other", {}, 404),
        ("/mcp", {"Origin": "https://untrusted.invalid"}, 403),
        ("/mcp", {"MCP-Protocol-Version": "unsupported"}, 400),
    ],
)
def test_transport_methods_keep_request_boundary_checks(
    gateway_http, method, path, headers, status
):
    h = gateway_http
    assert h.client.request(method, path, headers=headers).status_code == status
    assert not h.calls


def test_stream_delivers_progress_before_commit_and_blocks_a_retry(gateway_http):
    h = gateway_http
    progress = {"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progress": 1}}

    def respond(handler, document):
        stream_headers(handler)
        event(handler, progress)
        if h.release.wait(3):
            event(handler, success(document["id"]))

    h.respond = respond
    with h.client.stream("POST", "/mcp", json=call("stream-call"), headers=HEADERS) as response:
        assert response.headers["content-type"] == "text/event-stream"
        lines = response.iter_lines()
        first = next(lines)
        assert first == "event: message"  # The upstream SSE id has been stripped.
        assert json.loads(next(lines).removeprefix("data: ")) == progress
        assert h.store.get_effect("refund:txn_1").state is EffectState.EXECUTING
        assert h.store.receipts() == ()
        h.release.set()
        rest = list(lines)
    assert not any(line.startswith("id:") for line in rest)
    final = json.loads(next(line[6:] for line in rest if line.startswith("data: ")))
    assert final["id"] == "stream-call"
    assert final["_meta"]["com.ctrlrun/receipt"]["result"] == "committed"
    assert h.store.get_effect("refund:txn_1").state is EffectState.COMMITTED
    assert h.client.post("/mcp", json=call(), headers=HEADERS).status_code == 409
    assert len(h.calls) == 1


@pytest.mark.parametrize("wrong_final", [False, True])
def test_unfinished_or_wrong_id_stream_stays_ambiguous(gateway_http, wrong_final):
    h = gateway_http

    def respond(handler, document):
        stream_headers(handler)
        event(handler, {"jsonrpc": "2.0", "method": "notifications/progress"})
        if wrong_final:
            event(
                handler,
                {"jsonrpc": "2.0", "id": "wrong", "error": {"code": -32602, "message": "bad"}},
            )

    h.respond = respond
    response = h.client.post("/mcp", json=call(), headers=HEADERS)
    frames = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    assert frames[-1]["error"]["code"] == -41010 and frames[-1]["id"] == 42
    assert h.store.get_effect("refund:txn_1").state is EffectState.AMBIGUOUS
    assert h.client.post("/mcp", json=call(), headers=HEADERS).status_code == 409
    assert len(h.calls) == 1


def test_client_cancellation_marks_an_idle_stream_ambiguous(gateway_http):
    h = gateway_http
    upstream_closed = threading.Event()

    def respond(handler, document):
        stream_headers(handler)
        event(handler, {"jsonrpc": "2.0", "method": "notifications/progress"})
        handler.connection.settimeout(1.5)
        try:
            if handler.connection.recv(1) == b"":
                upstream_closed.set()
        except TimeoutError:
            # Not a swallowed refusal: a timeout *is* the negative observation here -- the
            # upstream connection is still open. The assertion is `upstream_closed` below,
            # which stays unset in that case and fails the test.
            pass

    h.respond = respond
    with h.client.stream("POST", "/mcp", json=call(), headers=HEADERS) as response:
        assert next(response.iter_lines()) == "event: message"
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
        if h.store.get_effect("refund:txn_1").state is EffectState.AMBIGUOUS:
            break
        time.sleep(0.01)
    assert h.store.get_effect("refund:txn_1").state is EffectState.AMBIGUOUS
    assert h.client.post("/mcp", json=call(), headers=HEADERS).status_code == 409
    assert len(h.calls) == 1
    assert upstream_closed.wait(0.1), "cancellation must close the upstream stream promptly"


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_nonintercepted_stream_is_relayed_incrementally_and_verbatim(gateway_http, method):
    h = gateway_http
    first = b'id: 17\ndata: {"jsonrpc":"2.0","method":"ping","id":9}\n\n'
    last = b": finished\n\n"

    def respond(handler, document):
        stream_headers(handler)
        handler.wfile.write(first)
        handler.wfile.flush()
        if h.release.wait(3):
            handler.wfile.write(last)

    h.respond = respond
    kwargs = (
        {"json": {"jsonrpc": "2.0", "id": 7, "method": "tools/list"}} if method == "POST" else {}
    )
    with h.client.stream(method, "/mcp", **kwargs) as response:
        chunks = response.iter_bytes()
        received = next(chunks)
        assert first.startswith(received)
        assert h.store.receipts() == ()
        h.release.set()
        received += b"".join(chunks)
    assert received == first + last


@pytest.mark.parametrize("rpc_id", [42, "request-id"])
def test_transport_error_keeps_request_id(gateway_http, rpc_id):
    h = gateway_http

    def drop(handler, document):
        handler.close_connection = True
        handler.connection.shutdown(socket.SHUT_RDWR)

    h.respond = drop
    response = h.client.post("/mcp", json=call(rpc_id), headers=HEADERS)
    assert response.status_code == 502
    assert response.json()["id"] == rpc_id
    assert response.json()["error"]["code"] == -41010


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_gateway_binds_every_accepted_loopback(gateway_http, host):
    original = gateway_http.gateway
    server = build_server(
        Gateway(
            replace(original.config, host=host),
            original._control,
            original._forward,
        )
    )
    try:
        assert server.server_port > 0
        assert server.address_family == (socket.AF_INET6 if host == "::1" else socket.AF_INET)
    finally:
        server.server_close()


@pytest.mark.parametrize("ending", ["\n", "\r", "\r\n"])
@pytest.mark.parametrize("chunk_size", [1, 2, 17, 1024])
def test_sse_frames_survive_split_unicode_and_multiline_data(ending, chunk_size):
    from ctrlrun.gateway.transport import _event_document, _events

    text = ending.join(
        [
            "\ufeffid: 1",
            'data: {"jsonrpc":"2.0",',
            'data: "id":42,"result":{"text":"café"}}',
            "",
            "",
        ]
    ).encode()
    chunks = (text[start : start + chunk_size] for start in range(0, len(text), chunk_size))
    documents = [_event_document(lines) for lines in _events(chunks)]
    assert documents == [{"jsonrpc": "2.0", "id": 42, "result": {"text": "café"}}]


@pytest.mark.parametrize(
    "response",
    [
        {"jsonrpc": "2.0", "id": 42, "result": {"resultType": "complete"}, "_meta": None},
        {"jsonrpc": "2.0", "id": 42, "error": {"code": -32602, "message": "bad"}, "result": {}},
        {"jsonrpc": "2.0", "id": 42, "error": {"code": -32602}},
        {"jsonrpc": "2.0", "id": 42, "result": {"resultType": "complete", "isError": "false"}},
    ],
)
def test_malformed_response_envelopes_cannot_prove_an_outcome(gateway_http, response):
    h = gateway_http
    h.respond = lambda handler, document: reply(handler, response)
    received = h.client.post("/mcp", json=call(), headers=HEADERS)
    assert received.status_code == 502 and received.json()["id"] == 42
    assert h.store.get_effect("refund:txn_1").state is EffectState.AMBIGUOUS
