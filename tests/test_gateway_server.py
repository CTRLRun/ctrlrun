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
from datetime import timedelta
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


# --- T65b/T65c: the removed flag, and a repeated identity header (SPEC-v0.3 §8.1, §3.1) --


def test_T65b_the_removed_client_info_flag_exits_naming_its_replacement():
    """§8.1 — a hard error, not a silent ignore. The flag chose the gateway's principal, so a
    gateway that started anyway would run with a different one than the operator asked for."""
    from click.testing import CliRunner

    from ctrlrun.cli.main import main

    result = CliRunner().invoke(
        main,
        [
            "gateway",
            "--upstream",
            "http://127.0.0.1:1/mcp",
            "--alias",
            "acme",
            # A valid identity source, so the "exactly one of ..." guard cannot fire instead —
            # it also exits non-zero and also names --principal-header, which would let this
            # test pass with the removal guard deleted.
            "--principal-header",
            "X-Agent",
            "--principal-from-client-info",
        ],
    )

    assert result.exit_code != 0
    assert "was removed in 0.3" in result.output, result.output
    assert "--principal-header" in result.output


def test_T65b_a_gateway_without_the_removed_flag_gets_past_that_check():
    """The control: the invocation above must fail *because of the flag*. Without this, a CLI
    that refused every gateway invocation would satisfy it."""
    from click.testing import CliRunner

    from ctrlrun.cli.main import main

    result = CliRunner().invoke(
        main,
        [
            "gateway",
            "--upstream",
            "http://127.0.0.1:1/mcp",
            "--alias",
            "acme",
            "--principal-header",
            "X-Agent",
        ],
    )

    assert "was removed in 0.3" not in result.output


def test_T65c_a_repeated_identity_header_is_refused(client, upstream, store):
    """§3.1 — a `Mapping` holds one value per name, so something must decide what a repeated
    header becomes, and under authority that decision picks the principal."""
    upstream.respond({"resultType": "complete", "content": []})
    body = _call()
    headers = [(k, v) for k, v in _headers(body).items() if k.lower() != "x-agent"]
    headers += [("X-Agent", "refund-agent"), ("X-Agent", "attacker-agent")]

    response = client.post("/mcp", content=json.dumps(body).encode(), headers=headers)

    assert response.status_code == 403
    assert _error(response)["data"]["error"] == "ctrlrun.no_principal"
    assert upstream.calls == []
    assert store.receipts() == ()
    assert store.events() == ()


def test_T65c_the_same_request_with_one_header_is_forwarded(client, upstream):
    """The control: without it, a gateway that refused everything would satisfy the test
    above, and the recorder would never have had a forwarded request to miss."""
    upstream.respond({"resultType": "complete", "content": []})

    response = _post(client)

    assert response.status_code == 200
    assert len(upstream.calls) == 1


def test_T65c_a_repeated_user_header_is_refused_too(upstream, store):
    """§10 T65c — "Repeated for `--user-header`". The principal header being watched does not
    mean the user header is, and a collapsed user is still a principal the client chose."""
    import threading as _threading

    from ctrlrun.gateway.server import build_server

    config = GatewayConfig(
        upstream=upstream.url,
        alias="acme",
        principal_header="X-Agent",
        user_header="X-User",
        port=0,
    )
    forwarder = httpx_forwarder(config)
    gateway = Gateway(config, Control(Policy.from_yaml(POLICY), store), forwarder)
    server = build_server(gateway)
    thread = _threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        upstream.respond({"resultType": "complete", "content": []})
        body = _call()
        # X-Agent exactly once, so the refusal can only come from the repeated X-User. With
        # it twice the principal-header watch fires and this proves nothing about the user one.
        headers = [(k, v) for k, v in _headers(body).items() if k.lower() != "x-agent"]
        headers += [("X-Agent", "refund-agent"), ("X-User", "alice"), ("X-User", "mallory")]
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_address[1]}") as client:
            response = client.post("/mcp", content=json.dumps(body).encode(), headers=headers)
        assert response.status_code == 403
        assert upstream.calls == []
    finally:
        server.shutdown()
        server.server_close()
        forwarder.close()


def test_the_gateway_stamps_the_controls_environment(upstream, store):
    """SPEC-v0.3 §2.5 — the Control's, never a second copy of the gateway's own. Hard-coding
    one here is how `$CTRLRUN_ENVIRONMENT` gets silently outranked."""
    import threading as _threading

    from ctrlrun.gateway.server import build_server

    config = GatewayConfig(upstream=upstream.url, alias="acme", principal_header="X-Agent", port=0)
    forwarder = httpx_forwarder(config)
    control = Control(Policy.from_yaml(POLICY), store, environment="staging")
    server = build_server(Gateway(config, control, forwarder))
    thread = _threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        upstream.respond({"resultType": "complete", "content": []})
        body = _call()
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_address[1]}") as client:
            client.post("/mcp", content=json.dumps(body).encode(), headers=_headers(body))
        assert [r.environment for r in store.receipts()] == ["staging"]
    finally:
        server.shutdown()
        server.server_close()
        forwarder.close()


def test_a_user_header_without_a_principal_header_is_refused():
    """SPEC-v0.3 §8.2 — a flag that cannot take effect is a flag the operator believes took
    effect: with `--principal` there is no request to read a user from."""
    with pytest.raises(InvalidArgument):
        GatewayConfig(upstream="http://x/mcp", alias="acme", principal="a", user_header="X-User")


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


def test_T24_a_second_identical_allowed_call_is_refused_as_a_duplicate(client, upstream, store):
    """The `DuplicateEffect` path, which the approval tests never reach.

    An approved action is caught earlier, by the check that will not send a human a decision
    the effect key would refuse anyway (§6.10). An *allowed* one goes all the way to the
    reservation, which is where v0.1 §5.4 refuses it — and that is the mapping under test.
    """
    upstream.respond({"resultType": "complete", "content": []})
    _post(client)

    again = _post(client)

    assert again.status_code == 409
    assert _error(again)["code"] == -41004
    assert _error(again)["data"]["state"] == "committed"
    assert _error(again)["data"]["effect_key"] == "refund:txn_1"
    assert len(upstream.calls) == 1


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


# --- SPEC-v0.3 §4: authority reaches the gateway's execution path ------------------------

AUTHORITY_ONLY = """
schema: ctrlrun.policy/v3
authority:
  grants:
    - id: refunds-only
      subject: { agent: "refund-agent" }
      actions: ["mcp.acme.create_refund"]
      resources: ["payment:*"]
      constraints: { amount_lte: 20000 }
"""


@pytest.fixture
def authority_gateway(upstream, store):
    from ctrlrun import Authority

    policy = Policy.from_yaml(POLICY.replace("ctrlrun.policy/v2", "ctrlrun.policy/v3"))
    control = Control(policy, store, authority=Authority.from_yaml(AUTHORITY_ONLY, standalone=True))
    config = GatewayConfig(upstream=upstream.url, alias="acme", principal_header="X-Agent", port=0)
    forwarder = httpx_forwarder(config)
    yield Gateway(config, control, forwarder)
    forwarder.close()


@pytest.fixture
def authority_client(authority_gateway):
    server = build_server(authority_gateway)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10) as opened:
        yield opened
    server.shutdown()
    server.server_close()


@pytest.mark.authority
def test_a_call_outside_the_grant_never_reaches_the_upstream(authority_client, upstream, store):
    """§4.3 at the gateway. The `-41012` code and the `--authority` flag are build-list item
    5 (§8.3); what item 2 owns is that a gateway whose Control holds an `Authority` refuses
    before dispatch and writes the evidence, rather than forwarding and finding out later."""
    response = _post(
        authority_client,
        _call(arguments={"amount": 50000, "payment_id": "pi_1"}),
        headers={"X-Agent": "refund-agent"},
    )

    assert upstream.calls == []
    assert response.json()["error"]["data"]["reason"] == "authority_constraint"
    assert [str(event.type) for event in store.events()][1] == "AUTHORITY_DENIED"
    assert store.receipts()[-1].decision_reason == "authority_constraint"


@pytest.mark.authority
def test_a_call_inside_the_grant_is_forwarded(authority_client, upstream):
    """The control for the refusal: without it a gateway that refused everything would pass."""
    upstream.respond({"resultType": "complete", "content": []})

    response = _post(
        authority_client,
        _call(arguments={"amount": 1000, "payment_id": "pi_2"}),
        headers={"X-Agent": "refund-agent"},
    )

    assert len(upstream.calls) == 1
    assert "error" not in response.json()


# --- T91: the gateway enforces authority, distinguishably (SPEC-v0.3 §8.4) --------------


@pytest.mark.authority
def test_T91_a_call_outside_the_grant_is_41012_and_never_reaches_the_upstream(
    authority_client, upstream, store
):
    """§8.4 — `-41001` means this action is not permitted to anyone in this configuration;
    `-41012` means it is not permitted to *you*. A client answers the two differently."""
    response = _post(
        authority_client,
        _call(arguments={"amount": 200, "payment_id": "pi_1"}),
        headers={"X-Agent": "someone-else"},
    )

    assert response.status_code == 403
    assert _error(response)["code"] == -41012
    assert _error(response)["data"]["error"] == "ctrlrun.unauthorized"
    assert _error(response)["data"]["reason"] == "no_authority"
    assert upstream.calls == []
    assert store.receipts()[-1].result is ReceiptResult.DENIED
    assert store.receipts()[-1].decision_reason == "no_authority"


@pytest.mark.authority
def test_T91_a_policy_denial_under_the_same_authority_section_is_still_41001(upstream, store):
    """The other half, and the one that pins the discrimination: widening the
    `AuthorityDenied` handler to `ActionDenied`, or ordering the two `except` clauses the
    other way, returns a plausible code and fails *this* test rather than the one above."""
    grant = """
schema: ctrlrun.policy/v3
authority:
  grants:
    - id: everything
      subject: { agent: "refund-agent" }
      actions: ["mcp.acme.**"]
"""
    from ctrlrun import Authority

    gateway = _gateway_with(store, upstream, Authority.from_yaml(grant, standalone=True))
    body = _call(arguments={"amount": 90000000, "payment_id": "pi_9"})

    response = gateway.handle(
        json.dumps(body).encode(), _lowered(_headers(body, **{"X-Agent": "refund-agent"}))
    )

    assert response.status == 403
    error = json.loads(response.body)["error"]
    assert error["code"] == -41001
    assert error["data"]["error"] == "ctrlrun.denied"
    assert upstream.calls == []


@pytest.mark.authority
def test_T91_a_call_inside_the_grant_reaches_the_upstream(authority_client, upstream):
    upstream.respond({"resultType": "complete", "content": []})

    response = _post(
        authority_client,
        _call(arguments={"amount": 1000, "payment_id": "pi_2"}),
        headers={"X-Agent": "refund-agent"},
    )

    assert len(upstream.calls) == 1
    assert "error" not in response.json()


def _lowered(headers):
    return {name.lower(): value for name, value in headers.items()}


def _gateway_with(store, upstream, authority, *, mode="enforce", **config_overrides):
    """One `Gateway` over a real forwarder, with the authority section a test wants."""
    document = POLICY.replace("ctrlrun.policy/v2", f"ctrlrun.policy/v3\nmode: {mode}")
    control = Control(Policy.from_yaml(document), store, authority=authority)
    base = {
        "upstream": upstream.url,
        "alias": "acme",
        "principal_header": "X-Agent",
        "port": 0,
    }
    config = GatewayConfig(**{**base, **config_overrides})
    return Gateway(config, control, httpx_forwarder(config))


# --- T91b: the approval gate uses the combined decision (§8.3) --------------------------


@pytest.mark.authority
def test_T91b_the_approval_flow_works_with_an_authority_section_loaded(upstream, store):
    """§8.3 — three places in shipped v0.2 code read `Policy.evaluate` directly, and the
    gateway's `tools/call` path is one. Left alone, this flow still works, so the change is
    pinned by what it *stops*: a call a grant forbids no longer runs the approval flow."""
    from ctrlrun import Authority

    grant = """
schema: ctrlrun.policy/v3
authority:
  grants:
    - id: everything
      subject: { agent: "refund-agent" }
      actions: ["mcp.acme.**"]
"""
    gateway = _gateway_with(store, upstream, Authority.from_yaml(grant, standalone=True))
    upstream.respond({"resultType": "complete", "content": []})
    body = _call(arguments={"amount": 500000, "payment_id": "pi_3"})
    headers = _lowered(_headers(body, **{"X-Agent": "refund-agent"}))

    first = gateway.handle(json.dumps(body).encode(), headers)

    assert json.loads(first.body)["error"]["code"] == -41002
    assert upstream.calls == []

    request_id = json.loads(first.body)["error"]["data"]["request_id"]
    store.grant_approval(request_id, "human:test")
    second = gateway.handle(json.dumps(body).encode(), headers)

    assert "error" not in json.loads(second.body)
    assert len(upstream.calls) == 1


@pytest.mark.authority
def test_T91b_an_action_a_grant_forbids_never_reaches_the_approval_flow(upstream, store):
    """The half that only the combined decision gets right: with `Policy.evaluate` alone the
    pre-check would run the approval flow for a call the grant forbids outright, and a human
    would be paged about an action that could never run."""
    from ctrlrun import Authority

    grant = """
schema: ctrlrun.policy/v3
authority:
  grants:
    - id: small-refunds-only
      subject: { agent: "refund-agent" }
      actions: ["mcp.acme.create_refund"]
      constraints: { amount_lte: 20000 }
"""
    gateway = _gateway_with(store, upstream, Authority.from_yaml(grant, standalone=True))
    body = _call(arguments={"amount": 500000, "payment_id": "pi_4"})

    response = gateway.handle(
        json.dumps(body).encode(), _lowered(_headers(body, **{"X-Agent": "refund-agent"}))
    )

    assert json.loads(response.body)["error"]["code"] == -41012
    assert store.approvals_for(store.receipts()[-1].action_hash) == ()
    assert "APPROVAL_REQUESTED" not in [str(event.type) for event in store.events()]


# --- T91c: in observe mode the gateway forwards everything (§8.4) ----------------------


@pytest.mark.authority
def test_T91c_in_observe_mode_a_call_outside_the_grant_is_forwarded(upstream, store):
    """§8.4 — no `-41001`, no `-41012`, no `-41002`; the client sees the upstream's response
    and the would-have decision lives in the receipt. A gateway that returned an error while
    claiming not to enforce would be enforcing."""
    from ctrlrun import Authority

    grant = """
schema: ctrlrun.policy/v3
authority:
  grants:
    - id: someone-else-entirely
      subject: { agent: "other-agent" }
      actions: ["mcp.acme.**"]
"""
    gateway = _gateway_with(
        store, upstream, Authority.from_yaml(grant, standalone=True), mode="observe"
    )
    upstream.respond({"resultType": "complete", "content": []})
    body = _call(arguments={"amount": 200, "payment_id": "pi_5"})

    response = gateway.handle(
        json.dumps(body).encode(), _lowered(_headers(body, **{"X-Agent": "refund-agent"}))
    )

    assert "error" not in json.loads(response.body)
    assert len(upstream.calls) == 1
    receipt = store.receipts()[-1]
    assert receipt.result is ReceiptResult.OBSERVED
    assert receipt.would_have.blocked_reason == "no_authority"


@pytest.mark.authority
def test_T91c_the_same_call_under_enforce_is_refused(upstream, store):
    """The control: without it, an upstream that was never reachable would satisfy the test
    above by returning an error for a different reason."""
    from ctrlrun import Authority

    grant = """
schema: ctrlrun.policy/v3
authority:
  grants:
    - id: someone-else-entirely
      subject: { agent: "other-agent" }
      actions: ["mcp.acme.**"]
"""
    gateway = _gateway_with(store, upstream, Authority.from_yaml(grant, standalone=True))
    body = _call(arguments={"amount": 200, "payment_id": "pi_5"})

    response = gateway.handle(
        json.dumps(body).encode(), _lowered(_headers(body, **{"X-Agent": "refund-agent"}))
    )

    assert json.loads(response.body)["error"]["code"] == -41012
    assert upstream.calls == []


# --- SPEC-v0.3 §8.2: exactly one identity source, and no flag that cannot take effect ----


IDENTITY_BASE = {"upstream": "http://127.0.0.1:1/mcp", "alias": "acme"}
JWT_MINIMUM = {
    "identity_jwt": True,
    "identity_jwt_secret_file": None,
    "identity_jwt_public_key": None,
    "identity_jwt_jwks_url": "https://issuer.example/jwks",
    "identity_jwt_algorithms": ("RS256",),
    "identity_jwt_issuer": "https://issuer.example",
    "identity_jwt_audience": "https://ctrlrun.example",
    "identity_jwt_token_type": "at+jwt",
}


@pytest.mark.parametrize(
    "sources",
    [
        {},
        {"principal": "bot", "principal_header": "X-Agent"},
        {"principal": "bot", **JWT_MINIMUM},
        {"principal_header": "X-Agent", **JWT_MINIMUM},
        {"principal": "bot", "principal_header": "X-Agent", **JWT_MINIMUM},
    ],
    ids=["none", "two-classic", "static-and-jwt", "header-and-jwt", "all-three"],
)
def test_exactly_one_identity_source_is_required_and_the_three_are_named(sources):
    """§8.2 — giving two, or none, exits non-zero naming the three. There is still no
    default, because an Action cannot exist without a principal."""
    with pytest.raises(InvalidArgument) as refused:
        GatewayConfig(**IDENTITY_BASE, **sources)

    message = str(refused.value)
    assert "--principal" in message
    assert "--principal-header" in message
    assert "--identity-jwt" in message


def _cli_jwt_flags():
    """Every `--identity-jwt-*` option `ctrlrun gateway` actually declares.

    Read off the command rather than written out here, and deliberately **not** off the
    implementation's own `given` map: a list copied from the code under test mirrors it, and
    cannot fail for the flag the code forgot. Two were forgotten, and the earlier version of
    this test — parametrized from that map — was structurally unable to notice.
    """
    from ctrlrun.cli.main import gateway as gateway_command

    return sorted(
        parameter.name
        for parameter in gateway_command.params
        if parameter.name is not None
        and parameter.name.startswith("identity_jwt_")
        and parameter.name != "identity_jwt"
    )


#: A value for each that is different from its default, so "was it given" is unambiguous.
_STRAY_VALUES = {
    "identity_jwt_jwks_url": "https://x/jwks",
    "identity_jwt_public_key": "/tmp/k.pem",
    "identity_jwt_secret_file": "/tmp/s",
    "identity_jwt_algorithms": ("RS256",),
    "identity_jwt_issuer": "https://issuer.example",
    "identity_jwt_audience": "https://ctrlrun.example",
    "identity_jwt_token_type": "at+jwt",
    "identity_jwt_user_claim": "email",
    "identity_jwt_claims": ("scope",),
    "identity_jwt_header": "x-token",
    "identity_jwt_agent_claim": "client_id",
    "identity_jwt_leeway": 99999.0,
    "identity_jwt_jwks_min_refresh": 99999.0,
    "identity_jwt_http_timeout": 99999.0,
}


def test_the_stray_value_table_covers_every_cli_flag():
    """The guard on the guard: a new `--identity-jwt-*` option with no value here would make
    the test below silently skip it."""
    assert set(_cli_jwt_flags()) == set(_STRAY_VALUES), set(_cli_jwt_flags()) ^ set(_STRAY_VALUES)


@pytest.mark.parametrize("flag", _cli_jwt_flags())
def test_every_identity_jwt_flag_needs_identity_jwt(flag):
    """§8.2 — a flag that cannot take effect is a flag the operator believes took effect."""
    with pytest.raises(InvalidArgument, match="--identity-jwt"):
        GatewayConfig(**IDENTITY_BASE, principal="bot", **{flag: _STRAY_VALUES[flag]})


@pytest.mark.parametrize(
    "missing",
    [
        "identity_jwt_algorithms",
        "identity_jwt_issuer",
        "identity_jwt_audience",
        "identity_jwt_token_type",
    ],
)
def test_identity_jwt_refuses_the_four_settings_with_no_safe_default(missing):
    """An unpinned algorithm, issuer or audience is a token from somewhere else, and an
    unpinned type is an ID token. Refused before the extra is even imported, so an operator
    who has not installed it still learns what they got wrong."""
    options = dict(JWT_MINIMUM)
    options[missing] = () if missing.endswith("algorithms") else None

    with pytest.raises(InvalidArgument) as refused:
        GatewayConfig(**IDENTITY_BASE, **options)

    assert missing.replace("_", "-").replace("identity-jwt-", "--identity-jwt-") in str(
        refused.value
    )


def test_an_empty_token_type_is_the_explicit_no_typ_and_is_accepted():
    """§8.2 — `""` means "this issuer sets no typ", and it must stay distinguishable from
    "not passed": a default would make RFC 8725 §3.11's defence something nobody ever saw."""
    config = GatewayConfig(**IDENTITY_BASE, **{**JWT_MINIMUM, "identity_jwt_token_type": ""})

    assert config.identity_jwt_token_type == ""


def test_user_header_is_refused_with_identity_jwt():
    with pytest.raises(InvalidArgument, match="--identity-jwt-user-claim"):
        GatewayConfig(**IDENTITY_BASE, **JWT_MINIMUM, user_header="X-User")


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({"principal": "bot"}, "StaticIdentityProvider"),
        ({"principal_header": "X-Agent"}, "HeaderIdentityProvider"),
        (JWT_MINIMUM, "JWTIdentityProvider"),
    ],
    ids=["static", "header", "jwt"],
)
def test_each_flag_constructs_the_provider_it_names(config, expected):
    """§8.2 — `--principal` and `--principal-header` are understood as constructors now, not
    as a second way of naming a principal."""
    from ctrlrun.gateway.server import identity_provider

    provider = identity_provider(GatewayConfig(**IDENTITY_BASE, **config))

    assert type(provider).__name__ == expected


def test_the_jwt_secret_is_read_from_a_file_and_never_from_a_flag(tmp_path):
    """§8.2 — a shared secret on a command line is in every process listing on the host."""
    from ctrlrun.gateway.server import identity_provider

    secret_file = tmp_path / "secret"
    secret_file.write_text("  hunter2  \n")
    config = GatewayConfig(
        **IDENTITY_BASE,
        identity_jwt=True,
        identity_jwt_secret_file=str(secret_file),
        identity_jwt_algorithms=("HS256",),
        identity_jwt_issuer="https://issuer.example",
        identity_jwt_audience="https://ctrlrun.example",
        identity_jwt_token_type="at+jwt",
    )

    provider = identity_provider(config)

    assert type(provider).__name__ == "JWTIdentityProvider"
    assert not any("hunter2" in str(value) for value in vars(config).values())


def test_the_jwt_identity_header_is_watched_for_repetition(tmp_path):
    """§3.1's rule has to reach the header that carries the *credential*, not only the weaker
    sources. Without this it applies to `--principal-header` and not to `--identity-jwt`."""
    from ctrlrun.gateway.server import _repeated_identity_header

    config = GatewayConfig(**IDENTITY_BASE, **{**JWT_MINIMUM, "identity_jwt_header": "x-token"})
    pairs = [("Content-Type", "application/json"), ("X-Token", "a"), ("x-token", "b")]

    assert _repeated_identity_header(config, pairs) == "x-token"


# --- SPEC-v0.3 §8.3: --authority loads the section from a separate document --------------


AUTHORITY_DOCUMENT = """
schema: ctrlrun.policy/v3
authority:
  grants:
    - id: refunds-only
      subject: { agent: "refund-agent" }
      actions: ["mcp.acme.create_refund"]
      constraints: { amount_lte: 20000 }
"""


@pytest.mark.authority
def test_the_authority_flag_loads_the_section_from_its_own_document(tmp_path, store):
    """§8.3 — the person who writes grants and the person who writes per-action autonomy are
    often not the same person, and a gateway is where that separation shows up first."""
    from ctrlrun.gateway import _authority

    path = tmp_path / "authority.yaml"
    path.write_text(AUTHORITY_DOCUMENT)
    control = Control(Policy.from_yaml(POLICY), store)

    loaded = _authority(control, str(path))

    assert set(loaded.grants) == {"refunds-only"}


@pytest.mark.authority
def test_declaring_authority_in_both_places_refuses_to_start(tmp_path, store):
    """§8.3 — two sources for one section is the ambiguity `v0.1 §5.1` refuses for
    `{resource}`. Silently preferring either would mean an operator who edited the wrong file
    saw no effect and no error."""
    from ctrlrun import Authority
    from ctrlrun.gateway import _authority

    path = tmp_path / "authority.yaml"
    path.write_text(AUTHORITY_DOCUMENT)
    control = Control(
        Policy.from_yaml(POLICY.replace("ctrlrun.policy/v2", "ctrlrun.policy/v3")),
        store,
        authority=Authority.from_yaml(AUTHORITY_ONLY, standalone=True),
    )

    with pytest.raises(InvalidArgument) as refused:
        _authority(control, str(path))

    assert str(path) in str(refused.value)
    assert control.policy.source in str(refused.value)


@pytest.mark.parametrize("key", ["actions", "mode"])
def test_an_authority_document_is_not_a_policy_document(tmp_path, store, key):
    """§8.3 — its top-level key set is closed at exactly `schema` and `authority`. Two sources
    for `mode:` would be two switches, which is the one thing §6.1 says there is not."""
    from ctrlrun.errors import PolicyError
    from ctrlrun.gateway import _authority

    path = tmp_path / "authority.yaml"
    extra = "actions:\n  x:\n    decision: allow\n" if key == "actions" else "mode: observe\n"
    path.write_text(AUTHORITY_DOCUMENT + extra)

    with pytest.raises(PolicyError) as refused:
        _authority(Control(Policy.from_yaml(POLICY), store), str(path))

    assert str(path) in str(refused.value)
    assert key in str(refused.value)


# --- SPEC-v0.3 §8.4: the startup block ---------------------------------------------------


@pytest.mark.authority
def test_the_startup_block_names_the_environment_identity_and_authority(capsys, store, upstream):
    """§8.4 — everything here is something an operator can get wrong in a way that is
    invisible until an incident. It goes on the line that starts the process."""
    from ctrlrun import Authority
    from ctrlrun.gateway import _announce

    gateway = _gateway_with(store, upstream, Authority.from_yaml(AUTHORITY_ONLY, standalone=True))
    _announce(gateway._control, gateway.config, gateway.identity, None)
    printed = capsys.readouterr().out

    assert "environment  production" in printed
    assert "HeaderIdentityProvider" in printed
    assert "trusts the header 'X-Agent'" in printed
    assert "1 grant(s)" in printed
    assert "mcp.acme.read_only" in printed  # the action with no effect: template


def test_the_startup_block_says_so_when_there_is_no_authority_section(capsys, store, upstream):
    """§8.4 — so an operator who believes they configured authority finds out on the line that
    starts the process, rather than from a receipt three weeks later."""
    from ctrlrun.gateway import _announce

    gateway = _gateway_with(store, upstream, None)
    _announce(gateway._control, gateway.config, gateway.identity, None)

    assert "no authority: section — every principal is unrestricted" in capsys.readouterr().out


# --- No refusal reaches a client as a dropped connection (SPEC-v0.3 §8.2, §8.4) ---------
#
# `do_POST` has no top-level `try`, and `socketserver` answers an escaping exception by
# logging a traceback and closing the socket with **nothing written**. A client reads that as
# a transport failure — which is the one signal it retries on, and the one thing this library
# exists to stop. Three v0.3 paths landed there; each has a clause and each has a test.


class _Raising:
    """An identity provider that raises whatever it was told to.

    It refuses everything the real providers refuse — it never *names* anybody — so it cannot
    let a call through that a real provider would have stopped.
    """

    def __init__(self, error) -> None:
        self._error = error

    def resolve(self, context):
        raise self._error


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(ValueError("the token service was unreachable"), id="ValueError"),
        pytest.param(RuntimeError("boom"), id="RuntimeError"),
        pytest.param(KeyError("kid"), id="KeyError"),
    ],
)
def test_a_provider_raising_anything_answers_41007_rather_than_dropping_the_connection(
    upstream, store, error, monkeypatch
):
    """§3.2 — "anything else is logged and re-raised as `IdentityError`". `Control` applies
    that rule in `_ask_provider`; the gateway does not go through it, so it needs its own.
    `IdentityProvider` is public API, so this is a deployment's own provider on a bad day."""
    gateway = _gateway_with(store, upstream, None)
    monkeypatch.setattr(gateway, "_identity", _Raising(error))
    body = _call()

    response = gateway.handle(json.dumps(body).encode(), _lowered(_headers(body)))

    assert response.status == 403
    assert json.loads(response.body)["error"]["code"] == -41007
    assert upstream.calls == []
    assert store.receipts() == ()
    assert store.events() == ()


def test_an_expired_principal_answers_41007_rather_than_dropping_the_connection(
    upstream, store, monkeypatch
):
    """§2.3 — `Control.execute` refuses an expired principal with `IdentityError`, which is
    deliberately not an `ActionDenied`, so it needs its own clause in `_through_control`.

    Routinely reachable rather than adversarial: `JWTIdentityProvider` admits a token up to
    `leeway` (60 s by default) past its `exp` and then stamps `Principal.expires_at` with that
    exact `exp` — so every token presented inside that window is admitted by the provider and
    refused by the kernel. Before this clause, that dropped the connection.
    """
    from datetime import UTC, datetime, timedelta

    from ctrlrun import StaticIdentityProvider

    gateway = _gateway_with(store, upstream, None)
    lapsed = datetime.now(UTC) - timedelta(minutes=1)
    monkeypatch.setattr(
        gateway, "_identity", StaticIdentityProvider(agent="refund-agent", expires_at=lapsed)
    )
    body = _call()

    response = gateway.handle(json.dumps(body).encode(), _lowered(_headers(body)))

    assert response.status == 403
    assert json.loads(response.body)["error"]["code"] == -41007
    assert upstream.calls == []
    # The receipt is written — there *is* a principal to attribute it to — and it says why.
    assert store.receipts()[-1].decision_reason == "principal_expired"


@pytest.mark.authority
def test_a_continuation_whose_authority_lapsed_answers_41012_not_a_dropped_connection(
    upstream, store, monkeypatch
):
    """§5.6.1 — an operator runs `ctrlrun revoke` while a human is answering an elicitation.
    `Control.resume` refuses the lease extension with `AuthorityDenied`, and `_continue`
    caught neither that nor `IdentityError`."""
    from ctrlrun import Authority
    from ctrlrun.errors import AuthorityDenied

    gateway = _gateway_with(store, upstream, Authority.from_yaml(AUTHORITY_ONLY, standalone=True))

    def refuse(*args, **kwargs):
        raise AuthorityDenied("authority no longer covers this action", reason="authority_revoked")

    monkeypatch.setattr(gateway._control, "resume", refuse)
    body = _call(arguments={"amount": 1000, "payment_id": "pi_7"})
    body["params"]["requestState"] = "continuation-token"

    response = gateway.handle(
        json.dumps(body).encode(), _lowered(_headers(body, **{"X-Agent": "refund-agent"}))
    )

    assert response.status == 403
    error = json.loads(response.body)["error"]
    assert error["code"] == -41012
    assert error["data"]["reason"] == "authority_revoked"


def test_a_continuation_whose_credential_expired_answers_41007(upstream, store, monkeypatch):
    """§2.3.1 — the other half of the same hole: holding a continuation extends a lease, and
    an extension is a request to keep authority the credential no longer carries."""
    from ctrlrun.errors import IdentityError

    gateway = _gateway_with(store, upstream, None)

    def refuse(*args, **kwargs):
        raise IdentityError("the principal's credential expired at 2026-01-01T00:00:00Z")

    monkeypatch.setattr(gateway._control, "resume", refuse)
    body = _call()
    body["params"]["requestState"] = "continuation-token"

    response = gateway.handle(json.dumps(body).encode(), _lowered(_headers(body)))

    assert response.status == 403
    assert json.loads(response.body)["error"]["code"] == -41007


def test_the_gateway_identity_refusal_message_says_nothing_about_why(upstream, store, monkeypatch):
    """§8.2 — "a distinct message; the client learns that its credential was rejected and
    learns nothing about why, which is the correct amount to tell an unauthenticated caller".
    This handler had no test at all, which is how the three holes above went unnoticed."""
    from ctrlrun.errors import IdentityError

    gateway = _gateway_with(store, upstream, None)
    monkeypatch.setattr(
        gateway, "_identity", _Raising(IdentityError("kid 'rotated' is unknown after a refresh"))
    )
    body = _call()

    response = gateway.handle(json.dumps(body).encode(), _lowered(_headers(body)))
    message = json.loads(response.body)["error"]["message"]

    assert message == "the credential offered was rejected"
    assert "kid" not in message and "rotated" not in message


def test_a_declining_provider_is_a_different_message_from_a_refusing_one(
    upstream, store, monkeypatch
):
    """The two are different things (§3.2) and a client that saw one message could not tell
    "you sent no credential" from "the one you sent was rejected"."""

    class _Declines:
        def resolve(self, context):
            return None

    gateway = _gateway_with(store, upstream, None)
    monkeypatch.setattr(gateway, "_identity", _Declines())
    body = _call()

    response = gateway.handle(json.dumps(body).encode(), _lowered(_headers(body)))

    assert json.loads(response.body)["error"]["code"] == -41007
    assert json.loads(response.body)["error"]["message"] == "no principal could be derived"


@pytest.mark.authority
def test_T91b_the_pre_check_must_not_answer_for_an_action_the_grant_forbids(upstream, store):
    """§8.3, and the half that `Policy.evaluate` gets **observably** wrong.

    With the policy axis alone, an action the grant forbids still reaches
    `_before_asking_a_human` — which refuses on the effect record before authority is ever
    consulted. The client then gets `-41004 duplicate_effect`: the wrong reason, and a
    disclosure that this effect exists to a principal holding no grant over it. The combined
    decision means the pre-check is never entered, so the answer is `-41012`.

    Without this test, reverting to `Policy.evaluate` changes nothing any assertion can see:
    the earlier version reached the same `-41012` by a longer route.
    """
    from ctrlrun import Authority

    grant = """
schema: ctrlrun.policy/v3
authority:
  grants:
    - id: small-refunds-only
      subject: { agent: "refund-agent" }
      actions: ["mcp.acme.create_refund"]
      constraints: { amount_lte: 20000 }
"""
    gateway = _gateway_with(store, upstream, Authority.from_yaml(grant, standalone=True))
    # The key this call would use is already committed by somebody else.
    store.reserve_effect("refund:pi_8", "act_earlier", timedelta(minutes=5))
    store.begin_execution("refund:pi_8", "act_earlier")
    store.commit_effect("refund:pi_8", "act_earlier", None)
    # Policy says `approve` for this amount; the grant forbids it outright.
    body = _call(arguments={"amount": 500000, "payment_id": "pi_8"})

    response = gateway.handle(
        json.dumps(body).encode(), _lowered(_headers(body, **{"X-Agent": "refund-agent"}))
    )

    error = json.loads(response.body)["error"]
    assert error["code"] == -41012, "the effect pre-check answered before authority did"
    assert error["data"]["reason"] == "authority_constraint"
    assert upstream.calls == []
