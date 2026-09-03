"""The gateway in the execution path. Build-list item 6c; SPEC-v0.2 §6.1, §6.3, §6.5-§6.10.

Acceptance tests T19, T21, T22, T23, T25, and the end-to-end halves of T20 and T24 that
item 6b could only do as pure functions.

The upstream is a real HTTP server on a real socket, because the guarantees under test are
about what happens between two processes: that the upstream is never called for a refused
request, that what it receives is byte-for-byte what was hashed, and that a lost response
blocks the retry.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ctrlrun import Control, EffectState, InvalidArgument, Policy, SQLiteStateStore
from ctrlrun.gateway.server import (
    Gateway,
    GatewayConfig,
    build_server,
    httpx_forwarder,
)
from ctrlrun.receipt import ReceiptResult

httpx = pytest.importorskip("httpx", reason="the gateway extra is not installed")

POLICY = """
schema: ctrlrun.policy/v2
actions:
  mcp.acme.create_refund:
    effect: "refund:{payment_id}"
    resource: "payment:{payment_id}"
    rules:
      - when: { amount_gte: 0, amount_lte: 50000 }
        decision: allow
      - when: { amount_gte: 0, amount_lte: 1000000 }
        decision: approve
      - decision: deny
  mcp.acme.read_only:
    decision: allow
  mcp.acme.reports_before_acting:
    effect: "report:{payment_id}"
    mcp:
      not_executed_on_error: true
    decision: allow
"""

CURRENT = "2026-07-28"


def _call(tool="create_refund", arguments=None, request_id=1):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {
            "name": tool,
            "arguments": {"payment_id": "txn_1", "amount": 200} if arguments is None else arguments,
        },
    }


def _headers(body_call=None, **extra):
    call = body_call or _call()
    headers = {
        "MCP-Protocol-Version": CURRENT,
        "Mcp-Method": "tools/call",
        "Mcp-Name": call["params"]["name"],
        "X-Agent": "refund-agent",
    }
    headers.update({key: value for key, value in extra.items() if value is not None})
    return headers


class Upstream:
    """A fake MCP server. It records what it received, and answers however it was told to."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.headers: list[dict] = []
        self.reply: dict | None = None
        self.status = 200
        self.extra_headers: dict[str, str] = {}
        self.drop_after_request = False
        self.body: bytes | None = None

    def respond(self, result=None, *, error=None, status=200, headers=None):
        if error is not None:
            self.reply = {"jsonrpc": "2.0", "id": 1, "error": error}
        elif result is not None:
            self.reply = {"jsonrpc": "2.0", "id": 1, "result": result}
        self.status = status
        self.extra_headers = headers or {}


@pytest.fixture
def upstream():
    state = Upstream()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length)
            state.body = raw
            state.headers.append(dict(self.headers.items()))
            try:
                state.calls.append(json.loads(raw))
            except ValueError:
                state.calls.append({"unparseable": raw.decode(errors="replace")})
            if state.drop_after_request:
                self.close_connection = True
                self.wfile.close()
                return
            payload = json.dumps(state.reply or {}).encode() if state.reply else b"not json"
            self.send_response(state.status)
            for key, value in state.extra_headers.items():
                self.send_header(key, value)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_address[1]}/mcp"
    yield state
    server.shutdown()
    server.server_close()


@pytest.fixture
def store(tmp_path):
    store = SQLiteStateStore(tmp_path / "state.db")
    yield store
    store.close()


@pytest.fixture
def control(store):
    return Control(Policy.from_yaml(POLICY), store)


@pytest.fixture
def gateway(upstream, control):
    config = GatewayConfig(upstream=upstream.url, alias="acme", principal_header="X-Agent", port=0)
    forwarder = httpx_forwarder(config)
    yield Gateway(config, control, forwarder)
    forwarder.close()


@pytest.fixture
def client(gateway):
    """The gateway on a real socket, so a client speaks to it the way an agent would."""
    server = build_server(gateway)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10) as opened:
        yield opened
    server.shutdown()
    server.server_close()


def _post(client, body=None, headers=None, path="/mcp"):
    body = _call() if body is None else body
    return client.post(path, content=json.dumps(body).encode(), headers=headers or _headers(body))


def _error(response):
    return response.json()["error"]


# --- T19: the gateway forwards the canonical action ------------------------------------


def test_T19_the_upstream_receives_the_canonical_arguments(client, upstream, store):
    """§6.7 — what a human approved, and what was hashed, reserved and recorded, is
    byte-for-byte what the upstream receives. A gateway relaying the original bytes would be
    binding an approval to one document and executing another."""
    upstream.respond({"resultType": "complete", "content": []})
    scrambled = {"amount": 200, "payment_id": "txn_1"}
    body = _call(arguments=scrambled)
    body["params"]["arguments"] = {"payment_id": "txn_1", "amount": 200}

    _post(client, body)

    received = upstream.calls[0]["params"]["arguments"]
    assert list(received) == ["amount", "payment_id"]  # canonical: sorted keys


def test_T19_the_recorded_hash_is_the_hash_of_what_the_upstream_received(client, upstream, store):
    upstream.respond({"resultType": "complete", "content": []})
    _post(client)

    from ctrlrun import Action, Principal, action_hash

    receipt = store.receipts()[-1]
    rebuilt = Action(
        name="mcp.acme.create_refund",
        arguments=upstream.calls[0]["params"]["arguments"],
        principal=Principal(agent="refund-agent"),
        resource="payment:txn_1",
    )
    assert receipt.action_hash == rebuilt.action_hash == action_hash(rebuilt)


def test_T19_the_action_is_named_for_the_alias_and_the_tool(client, upstream, store):
    upstream.respond({"resultType": "complete", "content": []})
    _post(client)

    assert store.receipts()[-1].action == "mcp.acme.create_refund"


def test_T19_the_response_carries_the_ctrlrun_receipt_meta(client, upstream, store):
    """§6.8 — so a client is not left guessing what CTRLRun recorded."""
    upstream.respond({"resultType": "complete", "content": []})

    response = _post(client)

    meta = response.json()["_meta"]["com.ctrlrun/receipt"]
    assert meta["effect_key"] == "refund:txn_1"
    assert meta["result"] == "committed"
    assert meta["attempt"] == 1
    assert meta["receipt_id"] == store.receipts()[-1].receipt_id


def test_T19_an_allowed_call_commits_the_effect(client, upstream, store):
    upstream.respond({"resultType": "complete", "content": []})
    _post(client)

    assert store.get_effect("refund:txn_1").state is EffectState.COMMITTED
    assert store.receipts()[-1].result is ReceiptResult.COMMITTED


# --- T20 end-to-end: the upstream is never called for a refused request ----------------


@pytest.mark.parametrize(
    ("body", "headers", "status", "code"),
    [
        ("{not json", None, 400, -32700),
        ([_call()], None, 400, -32600),
        (None, {"MCP-Protocol-Version": "1999-01-01"}, 400, -32022),
        (None, {"MCP-Protocol-Version": CURRENT, "Mcp-Name": "create_refund"}, 400, -32020),
        (
            None,
            {"MCP-Protocol-Version": CURRENT, "Mcp-Method": "tools/call", "Mcp-Name": "other"},
            400,
            -32020,
        ),
    ],
)
def test_T20_a_refused_request_never_reaches_the_upstream(
    client, upstream, body, headers, status, code
):
    content = body if isinstance(body, str) else json.dumps(body or _call())

    response = client.post("/mcp", content=content.encode(), headers=headers or _headers())

    assert response.status_code == status
    assert _error(response)["code"] == code
    assert upstream.calls == []


def test_T20_an_unallowlisted_origin_is_refused_before_anything_else(client, upstream):
    response = _post(client, headers={**_headers(), "Origin": "https://evil.example"})

    assert response.status_code == 403
    assert upstream.calls == []


def test_T20_a_body_over_the_limit_is_refused_with_413(client, upstream):
    body = _call(arguments={"payment_id": "x" * (1024 * 1024 + 10), "amount": 200})

    response = client.post("/mcp", content=json.dumps(body).encode(), headers=_headers(body))

    assert response.status_code == 413
    assert upstream.calls == []


# --- T21: no principal, no action ------------------------------------------------------


def test_T21_a_missing_principal_header_is_refused_with_41007(client, upstream, store):
    headers = {key: value for key, value in _headers().items() if key != "X-Agent"}

    response = client.post("/mcp", content=json.dumps(_call()).encode(), headers=headers)

    assert response.status_code == 403
    assert _error(response)["code"] == -41007
    assert _error(response)["data"]["error"] == "ctrlrun.no_principal"
    assert upstream.calls == []


def test_T21_no_receipt_and_no_events_are_written_without_a_principal(client, store):
    """§6.5 — there is no principal to attribute the refusal to, so it does not belong in
    the evidence log. It must not be silent either, which is what the log line is for."""
    headers = {key: value for key, value in _headers().items() if key != "X-Agent"}

    client.post("/mcp", content=json.dumps(_call()).encode(), headers=headers)

    assert store.receipts() == ()
    assert store.events() == ()


def test_T21_the_refusal_is_logged_with_the_action(client, caplog):
    headers = {key: value for key, value in _headers().items() if key != "X-Agent"}

    with caplog.at_level("WARNING", logger="ctrlrun.gateway"):
        client.post("/mcp", content=json.dumps(_call()).encode(), headers=headers)

    assert [r for r in caplog.records if "create_refund" in r.getMessage()]


def test_an_empty_principal_header_is_no_principal(client, upstream):
    response = _post(client, headers={**_headers(), "X-Agent": ""})

    assert _error(response)["code"] == -41007
    assert upstream.calls == []


# --- T22: a float argument is refused, not coerced -------------------------------------


def test_T22_a_float_argument_is_refused_with_41008_naming_the_pointer(client, upstream, store):
    body = _call(arguments={"payment_id": "txn_1", "amount": 20.0})

    response = client.post("/mcp", content=json.dumps(body).encode(), headers=_headers(body))

    assert response.status_code == 400
    assert _error(response)["code"] == -41008
    assert _error(response)["data"]["pointer"] == "/amount"
    assert upstream.calls == []


def test_T22_a_denied_receipt_is_written_with_no_policy_evaluated_event(client, upstream, store):
    """§6.6 — recorded like v0.1 §5.1's effect_key_error: there is a principal, so it belongs
    in the evidence log, and the policy is never consulted."""
    body = _call(arguments={"payment_id": "txn_1", "amount": 20.0})
    client.post("/mcp", content=json.dumps(body).encode(), headers=_headers(body))

    receipt = store.receipts()[-1]
    assert receipt.result is ReceiptResult.DENIED
    assert receipt.decision_reason == "unrepresentable_argument"
    assert [event.type.value for event in store.events()] == [
        "ACTION_PROPOSED",
        "ACTION_DENIED",
    ]


# --- T23: a lost response blocks the retry (the signature test, over HTTP) -------------


def test_T23_a_lost_response_leaves_the_effect_ambiguous(client, upstream, store):
    upstream.drop_after_request = True

    response = _post(client)

    assert response.status_code == 502
    assert _error(response)["code"] == -41010
    assert store.get_effect("refund:txn_1").state is EffectState.AMBIGUOUS


def test_T23_the_meta_says_ambiguous(client, upstream, store):
    upstream.drop_after_request = True

    response = _post(client)

    assert response.json()["_meta"]["com.ctrlrun/receipt"]["result"] == "ambiguous"


def test_T23_the_identical_call_sent_again_is_refused_and_the_upstream_called_once(
    client, upstream, store
):
    upstream.drop_after_request = True
    _post(client)
    upstream.drop_after_request = False
    upstream.respond({"resultType": "complete", "content": []})

    again = _post(client)

    assert again.status_code == 409
    assert _error(again)["code"] == -41005
    assert len(upstream.calls) == 1
    assert store.receipts()[-1].result is ReceiptResult.BLOCKED


# --- T24 end-to-end: the upstream's own answer reaches the client ----------------------


def test_T24_connection_refused_is_FAILED_and_a_retry_is_permitted(control, store, upstream):
    """Nothing was written, so the record is FAILED and the next attempt may run."""
    config = GatewayConfig(
        upstream="http://127.0.0.1:1/mcp", alias="acme", principal_header="X-Agent", port=0
    )
    forwarder = httpx_forwarder(config)
    gateway = Gateway(config, control, forwarder)
    try:
        response = gateway.handle(json.dumps(_call()).encode(), _headers())
    finally:
        forwarder.close()

    assert json.loads(response.body)["error"]["code"] == -41011
    assert store.get_effect("refund:txn_1").state is EffectState.FAILED


def test_T24_is_error_true_is_AMBIGUOUS_and_relayed_unchanged(client, upstream, store):
    upstream.respond({"resultType": "complete", "isError": True, "content": [{"x": 1}]})

    response = _post(client)

    assert store.get_effect("refund:txn_1").state is EffectState.AMBIGUOUS
    assert response.json()["result"]["content"] == [{"x": 1}]


def test_T24_is_error_true_is_FAILED_where_the_policy_says_so(client, upstream, store):
    body = _call(tool="reports_before_acting")
    upstream.respond({"resultType": "complete", "isError": True, "content": []})

    client.post("/mcp", content=json.dumps(body).encode(), headers=_headers(body))

    assert store.get_effect("report:txn_1").state is EffectState.FAILED


@pytest.mark.parametrize(("code", "state"), [(-32602, "failed"), (-32603, "ambiguous")])
def test_T24_jsonrpc_error_codes_map_as_specified(client, upstream, store, code, state):
    upstream.respond(error={"code": code, "message": "no"})

    response = _post(client)

    assert store.get_effect("refund:txn_1").state.value == state
    assert response.json()["error"]["code"] == code  # the upstream's own error, unchanged


def test_T24_a_401_is_FAILED_with_the_challenge_relayed_verbatim(client, upstream, store):
    upstream.respond(
        result={"resultType": "complete"},
        status=401,
        headers={"WWW-Authenticate": 'Bearer realm="acme"'},
    )

    response = _post(client)

    assert store.get_effect("refund:txn_1").state is EffectState.FAILED
    assert response.headers["WWW-Authenticate"] == 'Bearer realm="acme"'


def test_T24_a_retry_is_permitted_after_a_401(client, upstream, store):
    upstream.respond(result={"resultType": "complete"}, status=401)
    _post(client)
    upstream.respond(result={"resultType": "complete", "content": []}, status=200)

    _post(client)

    assert store.get_effect("refund:txn_1").state is EffectState.COMMITTED
    assert store.get_effect("refund:txn_1").attempt == 2


def test_T24_an_html_error_page_is_AMBIGUOUS(client, upstream, store):
    upstream.reply = None  # the handler writes b"not json"
    upstream.status = 502

    response = _post(client)

    assert store.get_effect("refund:txn_1").state is EffectState.AMBIGUOUS
    assert response.json()["error"]["code"] == -41010


@pytest.mark.parametrize("result_type", [None, "who-knows"])
def test_T24_an_unrecognized_or_absent_result_type_is_AMBIGUOUS(
    client, upstream, store, result_type
):
    result = {"content": []} if result_type is None else {"resultType": result_type}
    upstream.respond(result)

    _post(client)

    assert store.get_effect("refund:txn_1").state is EffectState.AMBIGUOUS


def test_T24_a_committed_response_reaches_the_client_otherwise_unchanged(client, upstream):
    upstream.respond({"resultType": "complete", "content": [{"text": "ok"}], "extra": 7})

    document = _post(client).json()

    assert document["result"] == {
        "resultType": "complete",
        "content": [{"text": "ok"}],
        "extra": 7,
    }
    assert document["jsonrpc"] == "2.0"


# --- T25: approval over the gateway ----------------------------------------------------


def _needs_approval():
    return _call(arguments={"payment_id": "txn_2", "amount": 200000})


def test_T25_an_action_needing_a_human_returns_41002_and_writes_no_receipt(client, upstream, store):
    body = _needs_approval()

    response = client.post("/mcp", content=json.dumps(body).encode(), headers=_headers(body))

    assert response.status_code == 403
    assert _error(response)["code"] == -41002
    assert _error(response)["data"]["request_id"].startswith("apr_")
    assert store.receipts() == ()
    assert upstream.calls == []


def test_T25_the_identical_call_executes_once_the_approval_is_granted(client, upstream, store):
    """§6.10 — a client comes back by re-sending the identical `tools/call`, and the gateway
    finds the granted approval by the action's hash."""
    upstream.respond({"resultType": "complete", "content": []})
    body = _needs_approval()
    first = client.post("/mcp", content=json.dumps(body).encode(), headers=_headers(body))
    request_id = _error(first)["data"]["request_id"]
    store.grant_approval(request_id, "cli:local")

    second = client.post("/mcp", content=json.dumps(body).encode(), headers=_headers(body))

    assert second.status_code == 200
    assert store.get_effect("refund:txn_2").state is EffectState.COMMITTED
    assert store.get_approval(request_id).status.value == "consumed"
    assert len(upstream.calls) == 1


def test_T25_a_third_identical_call_is_refused_and_the_upstream_called_once(
    client, upstream, store
):
    upstream.respond({"resultType": "complete", "content": []})
    body = _needs_approval()
    first = client.post("/mcp", content=json.dumps(body).encode(), headers=_headers(body))
    store.grant_approval(_error(first)["data"]["request_id"], "cli:local")
    client.post("/mcp", content=json.dumps(body).encode(), headers=_headers(body))

    third = client.post("/mcp", content=json.dumps(body).encode(), headers=_headers(body))

    assert _error(third)["code"] in (-41004, -41006)
    assert len(upstream.calls) == 1


def test_T25_a_denied_request_is_not_re_asked_until_it_expires(client, upstream, store):
    """§6.10 — "no" is an answer. Without this an agent that dislikes a denial resends the
    call and gets a fresh notification to a human, until one of them clicks the wrong
    button."""
    body = _needs_approval()
    first = client.post("/mcp", content=json.dumps(body).encode(), headers=_headers(body))
    request_id = _error(first)["data"]["request_id"]
    store.deny_approval(request_id, "cli:local")

    second = client.post("/mcp", content=json.dumps(body).encode(), headers=_headers(body))

    assert _error(second)["code"] == -41003
    assert len(store.approvals_for(store.get_approval(request_id).action_hash)) == 1
    assert upstream.calls == []


# --- §6.3: everything else is relayed --------------------------------------------------


def test_a_non_intercepted_method_is_relayed_with_no_ctrlrun_outcome(client, upstream, store):
    """`tools/list` is not an action; only calling a tool is."""
    upstream.respond({"tools": []})
    body = {"jsonrpc": "2.0", "id": 9, "method": "tools/list"}

    response = client.post(
        "/mcp",
        content=json.dumps(body).encode(),
        headers={"MCP-Protocol-Version": CURRENT, "Mcp-Method": "tools/list"},
    )

    assert response.status_code == 200
    assert len(upstream.calls) == 1
    assert store.receipts() == ()
    assert store.events() == ()
    assert "_meta" not in response.json()


def test_a_tool_with_no_effect_key_in_policy_gets_no_reservation(client, upstream, store):
    body = _call(tool="read_only")
    upstream.respond({"resultType": "complete", "content": []})

    client.post("/mcp", content=json.dumps(body).encode(), headers=_headers(body))

    assert store.list_effects() == ()
    assert store.receipts()[-1].effect_key is None


# --- §6.1, §6.5, §6.6: what the config refuses at startup ------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"alias": "ac.me"},
        {"alias": "ACME"},
        {"alias": ""},
        {"path": "/ctrlrun/approvals"},
        {"path": "mcp"},
        {"principal": None, "principal_header": None},
        {"principal": "a", "principal_header": "X-Agent"},
        {"host": "0.0.0.0"},
    ],
)
def test_the_config_refuses_what_it_cannot_run_safely(overrides):
    base = {
        "upstream": "http://localhost:8000",
        "alias": "acme",
        "principal_header": "X-Agent",
    }
    with pytest.raises(InvalidArgument):
        GatewayConfig(**{**base, **overrides})


def test_a_remote_bind_is_permitted_only_when_it_is_meant():
    config = GatewayConfig(
        upstream="http://localhost:8000",
        alias="acme",
        principal_header="X-Agent",
        host="0.0.0.0",
        allow_remote=True,
    )

    assert config.host == "0.0.0.0"
