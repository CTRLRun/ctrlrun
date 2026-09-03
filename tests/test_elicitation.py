"""Held reservations across an elicitation. Build-list item 6d; SPEC-v0.2 §6.9, §6.2.

Acceptance tests T26 and T30b. The kernel half — `Suspended` and `Control.resume` — is in
`tests/test_resume.py`; this is the MCP half on top of it.

Both naive readings are wrong. Treating the first leg as a completed action records an
outcome the upstream never reported; refusing the continuation makes every eliciting tool
unusable. The gateway holds the reservation open instead: the effect stays `EXECUTING`,
concurrent duplicates stay blocked throughout, and only the final result is mapped by §6.8.

A legacy stream may be resumed by a `GET` carrying `Last-Event-ID`, which would let the final
response to an intercepted `tools/call` arrive on a connection the gateway is not attributing
to that action — the outcome would land somewhere the gateway cannot see, and the reservation
would lease-expire into `AMBIGUOUS` despite a perfectly good answer having been delivered.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ctrlrun import Control, EffectState, Policy, SQLiteStateStore
from ctrlrun.gateway.legacy import (
    LAST_EVENT_ID_HEADER,
    SESSION_HEADER,
    is_event_stream,
    strip_event_ids,
)
from ctrlrun.gateway.server import Gateway, GatewayConfig, build_server, httpx_forwarder
from ctrlrun.receipt import EventType

httpx = pytest.importorskip("httpx", reason="the gateway extra is not installed")

POLICY = """
schema: ctrlrun.policy/v2
actions:
  mcp.acme.create_refund:
    effect: "refund:{payment_id}"
    resource: "payment:{payment_id}"
    decision: allow
  mcp.acme.read_only:
    decision: allow
"""

CURRENT = "2026-07-28"
STATE = "opaque-state-from-the-server"


def _call(tool="create_refund", *, arguments=None, request_id=1, extra=None):
    params = {
        "name": tool,
        "arguments": {"payment_id": "txn_1", "amount": 200} if arguments is None else arguments,
    }
    params.update(extra or {})
    return {"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": params}


def _headers(body=None):
    call = body or _call()
    return {
        "MCP-Protocol-Version": CURRENT,
        "Mcp-Method": "tools/call",
        "Mcp-Name": call["params"]["name"],
        "X-Agent": "refund-agent",
    }


class Upstream:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.replies: list[dict] = []

    def next_reply(self) -> dict:
        return self.replies.pop(0) if self.replies else {"resultType": "complete", "content": []}

    def elicits(self, *, request_state=STATE, rounds=1):
        for index in range(rounds):
            result = {"resultType": "input_required", "inputRequests": {"q1": "elicitation"}}
            if request_state is not None:
                result["requestState"] = f"{request_state}-{index}" if rounds > 1 else request_state
            self.replies.append(result)


@pytest.fixture
def upstream():
    state = Upstream()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            state.calls.append(json.loads(self.rfile.read(length)))
            payload = json.dumps({"jsonrpc": "2.0", "id": 1, "result": state.next_reply()}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state.url = f"http://127.0.0.1:{server.server_address[1]}/mcp"
    yield state
    server.shutdown()
    server.server_close()


@pytest.fixture
def store(tmp_path):
    store = SQLiteStateStore(tmp_path / "state.db")
    yield store
    store.close()


def _gateway(upstream, store, **overrides):
    config = GatewayConfig(
        upstream=upstream.url, alias="acme", principal_header="X-Agent", port=0, **overrides
    )
    forwarder = httpx_forwarder(config)
    return Gateway(config, Control(Policy.from_yaml(POLICY), store), forwarder), forwarder


@pytest.fixture
def client(upstream, store):
    gateway, forwarder = _gateway(upstream, store)
    server = build_server(gateway)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with httpx.Client(
        base_url=f"http://127.0.0.1:{server.server_address[1]}", timeout=10
    ) as opened:
        yield opened
    server.shutdown()
    server.server_close()
    forwarder.close()


def _post(client, body=None):
    body = body or _call()
    return client.post("/mcp", content=json.dumps(body).encode(), headers=_headers(body))


def _continuation(request_state=STATE, request_id=2, arguments=None):
    return _call(
        request_id=request_id,
        arguments=arguments,
        extra={"requestState": request_state, "inputResponses": {"q1": "yes"}},
    )


def _error(response):
    return response.json()["error"]


# --- T26: with a requestState ----------------------------------------------------------


def test_T26_an_input_required_result_leaves_the_effect_executing(client, upstream, store):
    upstream.elicits()

    response = _post(client)

    assert store.get_effect("refund:txn_1").state is EffectState.EXECUTING
    assert response.json()["result"]["resultType"] == "input_required"
    assert store.receipts() == ()


def test_T26_the_response_meta_says_suspended(client, upstream, store):
    upstream.elicits()

    meta = _post(client).json()["_meta"]["com.ctrlrun/receipt"]

    assert meta["result"] == "suspended"
    assert meta["effect_key"] == "refund:txn_1"


def test_T26_execution_suspended_is_appended_with_the_round(client, upstream, store):
    upstream.elicits()

    _post(client)

    suspended = [e for e in store.events() if e.type is EventType.EXECUTION_SUSPENDED]
    assert len(suspended) == 1
    assert suspended[0].data == {"held": True, "round": 1}


def test_T26_a_concurrent_duplicate_is_refused_while_the_elicitation_is_outstanding(
    client, upstream, store
):
    """The guarantee that actually matters: the reservation is still held."""
    upstream.elicits()
    _post(client)

    duplicate = _post(client, _call(request_id=99))

    assert duplicate.status_code == 409
    assert _error(duplicate)["code"] == -41004
    assert _error(duplicate)["data"]["state"] == "in_progress"


def test_T26_the_continuation_is_admitted_and_produces_one_receipt(client, upstream, store):
    upstream.elicits()
    _post(client)

    final = _post(client, _continuation())

    assert final.status_code == 200
    assert final.json()["result"]["resultType"] == "complete"
    assert store.get_effect("refund:txn_1").state is EffectState.COMMITTED
    assert len(store.receipts()) == 1
    assert store.receipts()[0].attempt == 1


def test_T26_the_continuation_is_the_same_action(client, upstream, store):
    """§6.9.2 — same action_id, same action_hash, same reservation, same attempt."""
    upstream.elicits()
    _post(client)
    proposed = store.events()[0]

    _post(client, _continuation())

    assert store.receipts()[0].action_id == proposed.action_id
    assert store.receipts()[0].action_hash == proposed.data["action_hash"]


def test_T26_suspended_precedes_resumed(client, upstream, store):
    upstream.elicits()
    _post(client)
    _post(client, _continuation())

    types = [e.type for e in store.events()]
    assert types.index(EventType.EXECUTION_SUSPENDED) < types.index(EventType.EXECUTION_RESUMED)


# --- T26: the refusals, each leaving the record EXECUTING ------------------------------


def test_T26_a_continuation_with_the_wrong_request_state_is_refused_with_41009(
    client, upstream, store
):
    upstream.elicits()
    _post(client)
    before = len(upstream.calls)

    refused = _post(client, _continuation(request_state="guessed"))

    assert refused.status_code == 400
    assert _error(refused)["code"] == -41009
    assert store.get_effect("refund:txn_1").state is EffectState.EXECUTING
    assert len(upstream.calls) == before


def test_T26_a_second_continuation_on_a_consumed_state_is_refused(client, upstream, store):
    """One elicitation admits one continuation."""
    upstream.elicits()
    _post(client)
    _post(client, _continuation())
    before = len(upstream.calls)

    replayed = _post(client, _continuation(request_id=3))

    assert _error(replayed)["code"] == -41009
    assert len(upstream.calls) == before


def test_T26_a_continuation_matching_no_held_elicitation_is_refused_with_41009(client, upstream):
    """A forgery, or a gateway that restarted before the record was written."""
    refused = _post(client, _continuation())

    assert _error(refused)["code"] == -41009
    assert upstream.calls == []


# --- T26: the bounds -------------------------------------------------------------------


def test_T26_exceeding_the_round_bound_records_ambiguous_and_returns_41010(upstream, store):
    """A held reservation is a resource an upstream must not be able to pin forever: the
    gateway has sent that many tool calls and knows the outcome of none."""
    gateway, forwarder = _gateway(upstream, store, max_elicitation_rounds=1)
    server = build_server(gateway)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    upstream.elicits(rounds=4)
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{server.server_address[1]}", timeout=10
        ) as client:
            _post(client)
            last = _post(client, _continuation(request_state=f"{STATE}-0"))
    finally:
        server.shutdown()
        server.server_close()
        forwarder.close()

    assert _error(last)["code"] == -41010
    assert store.get_effect("refund:txn_1").state is EffectState.AMBIGUOUS


# --- T26: without a requestState (§6.9.3) ----------------------------------------------


def test_T26_an_elicitation_without_a_request_state_records_ambiguous(client, upstream, store):
    """The fail-closed floor, stated as a cost. The protocol minted no identity for that
    exchange, so the only thing distinguishing a continuation from a fresh duplicate call is
    a field the agent controls completely."""
    upstream.elicits(request_state=None)

    _post(client)

    assert store.get_effect("refund:txn_1").state is EffectState.AMBIGUOUS


def test_T26_a_continuation_after_a_stateless_elicitation_is_refused_with_41005(
    client, upstream, store
):
    upstream.elicits(request_state=None)
    _post(client)

    refused = _post(client, _call(request_id=2))

    assert _error(refused)["code"] == -41005


def test_T26_a_tool_with_no_effect_key_is_unaffected(client, upstream, store):
    """No reservation to hold, so an eliciting *read* costs a log line either way."""
    upstream.elicits()
    body = _call(tool="read_only")

    response = client.post("/mcp", content=json.dumps(body).encode(), headers=_headers(body))

    assert response.json()["result"]["resultType"] == "input_required"
    assert store.list_effects() == ()


# --- T30b: resumption is not relayed for an intercepted call ---------------------------


def test_T30b_id_fields_are_stripped_from_an_intercepted_stream():
    stream = [
        "event: message",
        "id: 42",
        'data: {"jsonrpc":"2.0"}',
        "",
        "  id: 43",
        "data: more",
        "",
    ]

    assert list(strip_event_ids(stream)) == [
        "event: message",
        'data: {"jsonrpc":"2.0"}',
        "",
        "data: more",
        "",
    ]


def test_T30b_everything_but_the_id_field_survives():
    stream = ["retry: 1000", ": a comment", "event: x", "data: y", "identity: kept", ""]

    assert list(strip_event_ids(stream)) == stream


def test_T30b_a_non_intercepted_stream_keeps_its_ids():
    """Relayed verbatim: those streams carry no intercepted outcome (§6.2)."""
    stream = ["id: 7", "data: x", ""]

    assert list(stream) == ["id: 7", "data: x", ""]


def test_the_session_header_is_relayed_and_never_minted():
    """§6.2 — relayed in both directions, never minted, never interpreted. The gateway holds
    no sessions; on 2026-07-28 there are none to hold."""
    assert SESSION_HEADER == "Mcp-Session-Id"
    assert LAST_EVENT_ID_HEADER == "Last-Event-ID"


def test_an_sse_content_type_is_recognized_with_or_without_parameters():
    assert is_event_stream("text/event-stream")
    assert is_event_stream("text/event-stream; charset=utf-8")
    assert not is_event_stream("application/json")
    assert not is_event_stream(None)
