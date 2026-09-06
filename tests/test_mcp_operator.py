"""The operator MCP server. SPEC-mcp-operator.md; acceptance tests T182-T193.

An approver answers from the assistant they are already talking to, through the same two
store calls `ctrlrun approve` and `ctrlrun deny` make. So most of what is under test here is
what the server *refuses* to do: reads that never touch the identity provider, writes that
refuse without a credential naming a human, a store that is byte-identical after every
refusal, and a source file that composes nothing.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from ctrlrun import (
    Action,
    ApprovalMismatch,
    ApprovalRequired,
    Control,
    EffectState,
    InvalidArgument,
    Policy,
    Principal,
    SQLiteStateStore,
    with_approval,
)
from ctrlrun.approval import ApprovalStatus, LocalApprovalProvider
from ctrlrun.cli.main import main
from ctrlrun.effect import RESOLVED_BY_HUMAN
from ctrlrun.gateway.operator import (
    OperatorConfig,
    OperatorServer,
    build_operator_server,
    operator_identity_provider,
)
from ctrlrun.identity import IdentityProvider
from ctrlrun.receipt import EventType, JSONLEventSink

CURRENT = "2026-07-28"

POLICY = """
schema: ctrlrun.policy/v2
actions:
  stripe.refund:
    effect: "refund:{payment_id}"
    resource: "payment:{payment_id}"
    rules:
      - when: { amount_lte: 500 }
        decision: allow
      - when: { amount_lte: 500000 }
        decision: approve
      - decision: deny
"""


# --- the fixtures -----------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    (tmp_path / "ctrlrun.yaml").write_text(POLICY, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CTRLRUN_CONFIG", raising=False)
    monkeypatch.delenv("CTRLRUN_STATE", raising=False)
    monkeypatch.delenv("CTRLRUN_STORE_URL", raising=False)
    return tmp_path


@pytest.fixture
def store(workspace):
    store = SQLiteStateStore(workspace / ".ctrlrun" / "state.db")
    yield store
    store.close()


@pytest.fixture
def control(workspace, store):
    return Control(
        Policy.from_file(workspace / "ctrlrun.yaml"),
        store,
        LocalApprovalProvider(store),
        sinks=[JSONLEventSink(workspace / ".ctrlrun")],
    )


class Recording:
    """An identity provider that answers, and records that it was asked.

    T182's negative half needs the *record*: a provider that ran on a read and declined would
    satisfy "the read succeeded" while breaking the rule the test is about.
    """

    def __init__(self, *, agent="approver-app", user="alice", expires_at=None):
        self.calls: list[str] = []
        self.agent = agent
        self.user = user
        self.expires_at = expires_at

    def resolve(self, context):
        self.calls.append(context.action)
        header = context.headers.get("x-approver")
        if not header:
            return None
        return Principal(
            agent=self.agent,
            user=self.user,
            issuer="https://proxy.example/",
            expires_at=self.expires_at,
        )


def _config(**overrides) -> OperatorConfig:
    settings = {"principal_header": "x-approver", "user_header": "x-approver-user"}
    settings.update(overrides)
    return OperatorConfig(**settings)


@pytest.fixture
def identity():
    return Recording()


@pytest.fixture
def server(control, identity):
    return OperatorServer(_config(), control, identity)


def _headers(**extra):
    headers = {"MCP-Protocol-Version": CURRENT, "Mcp-Method": "tools/call"}
    headers.update({k.replace("_", "-"): v for k, v in extra.items() if v is not None})
    return headers


def _call(server, tool, arguments=None, *, credential=None, request_id=1):
    """One `tools/call`, returning the parsed JSON-RPC document and the HTTP status."""
    body = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments or {}},
    }
    headers = _headers(Mcp_Name=tool)
    if credential is not None:
        headers["X-Approver"] = credential
    response = server.handle(json.dumps(body).encode(), headers)
    return json.loads(response.body), response.status


def _rpc(server, method, params=None, *, request_id=1):
    body = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        body["params"] = params
    headers = _headers(Mcp_Method=method)
    response = server.handle(json.dumps(body).encode(), headers)
    return json.loads(response.body), response.status


def _result(document):
    assert "error" not in document, document["error"]
    return document["result"]


def _structured(document):
    return _result(document)["structuredContent"]


def _error(document):
    assert "error" in document, document
    return document["error"]


# --- building the store's contents ------------------------------------------------------


def _action(control, payment_id="txn_1", amount=200000):
    return Action(
        name="stripe.refund",
        arguments={"payment_id": payment_id, "amount": amount},
        principal=Principal(agent="refund-agent", user="bob"),
        resource=f"payment:{payment_id}",
        environment=control.environment,
    )


def _pending(control, payment_id="txn_1", amount=200000):
    """Propose an action that needs a human, and return (action, request_id)."""
    action = _action(control, payment_id, amount)
    with pytest.raises(ApprovalRequired) as raised:
        control.execute(action, lambda: "re_1", f"refund:{payment_id}")
    return action, raised.value.request_id


def _ambiguous(control, payment_id="txn_amb"):
    """An effect whose outcome nobody knows, for `resolve` to state an outcome about."""
    action = _action(control, payment_id, amount=100)

    def executor():
        raise TimeoutError("no response from api.stripe.com after 30s")

    with pytest.raises(Exception):
        control.execute(action, executor, f"refund:{payment_id}")
    record = control.store.get_effect(f"refund:{payment_id}")
    assert record is not None and record.state is EffectState.AMBIGUOUS
    return f"refund:{payment_id}"


def _snapshot(store):
    """Everything a refused write must leave untouched (T190).

    Events, receipts, every approval record's status and approver, and every effect record's
    state and resolver. A refused write that appended a single event fails this.
    """
    requested = [
        event.approval_id
        for event in store.events()
        if event.type is EventType.APPROVAL_REQUESTED and event.approval_id
    ]
    approvals = []
    for approval_id in requested:
        record = store.get_approval(approval_id)
        approvals.append(
            (approval_id, None if record is None else (str(record.status), record.approver))
        )
    return (
        len(store.events()),
        len(store.receipts()),
        tuple(approvals),
        tuple(
            (record.effect_key, str(record.state), record.resolved_by)
            for record in store.list_effects()
        ),
    )


# --- T182 — read tools answer with no credential ----------------------------------------


def test_T182_read_tools_answer_without_a_credential(server, control, identity):
    """SPEC-mcp-operator §4.1. Five read tools, no identity header, no bearer token."""
    _pending(control)
    _ambiguous(control)

    for tool, arguments in [
        ("list_pending_approvals", {}),
        ("receipts", {}),
        ("effects", {}),
        ("stats", {}),
    ]:
        document, status = _call(server, tool, arguments)
        assert status == 200, (tool, document)
        _result(document)

    listed = _structured(_call(server, "list_pending_approvals")[0])["pending"]
    assert len(listed) == 1
    document, status = _call(server, "inspect_action", {"action_id": listed[0]["action_id"]})
    assert status == 200
    _result(document)

    document, status = _rpc(server, "tools/list")
    assert status == 200
    assert {tool["name"] for tool in _result(document)["tools"]} == {
        "list_pending_approvals",
        "inspect_action",
        "receipts",
        "effects",
        "stats",
        "approve",
        "deny",
        "resolve",
    }


def test_T182_a_read_never_consults_the_identity_provider(server, control, identity):
    """§4.1's second paragraph, and the half that a passing read alone cannot prove.

    A provider that ran on every read would make an expired credential turn `receipts` into a
    refusal, and would put a JWKS fetch on the cost of reading.
    """
    _pending(control)
    for tool in ("list_pending_approvals", "receipts", "effects", "stats"):
        _call(server, tool, credential="alice")
    _rpc(server, "tools/list")
    _rpc(server, "initialize", {"protocolVersion": CURRENT})

    assert identity.calls == []


# --- T183 — no way to bind a non-loopback address ---------------------------------------


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "10.0.0.4", "example.internal"])
def test_T183_a_non_loopback_listen_is_refused(host):
    """§2.1. The read tools answer without a credential, so this process must not be the one
    that opens a port to a network."""
    with pytest.raises(InvalidArgument) as raised:
        _config(host=host)
    assert "loopback" in str(raised.value)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_T183_loopback_is_accepted(host):
    assert _config(host=host).host == host


def test_T183_there_is_no_flag_that_permits_a_remote_bind():
    """§2.1's two sentences hold together or not at all. A later `--allow-remote` must fail a
    test rather than a review, so the absence is asserted by name."""
    assert not hasattr(OperatorConfig, "allow_remote")
    assert "allow_remote" not in OperatorConfig.__dataclass_fields__

    command = main.commands["mcp-operator"]
    names = {parameter.name for parameter in command.params}
    assert "allow_remote" not in names
    assert "principal" not in names  # §3.1 — StaticIdentityProvider cannot attribute
    flags = {flag for parameter in command.params for flag in parameter.opts}
    assert "--allow-remote" not in flags
    assert "--principal" not in flags


# --- T184 — each write refuses without a principal and succeeds with one, attributed -----


def test_T184_approve_refuses_without_a_principal(server, control, store):
    _, request_id = _pending(control)
    before = _snapshot(store)

    document, status = _call(server, "approve", {"request_id": request_id})

    assert status == 403
    assert _error(document)["code"] == -41007
    assert _error(document)["data"]["error"] == "ctrlrun.no_principal"
    assert store.get_approval(request_id).status is ApprovalStatus.PENDING
    assert _snapshot(store) == before


def test_T184_deny_refuses_without_a_principal(server, control, store):
    _, request_id = _pending(control)
    before = _snapshot(store)

    document, status = _call(server, "deny", {"request_id": request_id})

    assert status == 403
    assert _error(document)["code"] == -41007
    assert store.get_approval(request_id).status is ApprovalStatus.PENDING
    assert _snapshot(store) == before


def test_T184_resolve_refuses_without_a_principal(server, control, store):
    effect_key = _ambiguous(control)
    before = _snapshot(store)

    document, status = _call(
        server, "resolve", {"effect_key": effect_key, "outcome": "committed", "reason": "checked"}
    )

    assert status == 403
    assert _error(document)["code"] == -41007
    assert store.get_effect(effect_key).state is EffectState.AMBIGUOUS
    assert _snapshot(store) == before


def test_T184_approve_succeeds_with_one_and_is_attributed(server, control, store):
    """§5.2 and §5.4: the event names her, the channel is recorded, and — the half that
    matters — the *receipt* the kernel writes afterwards carries the same name."""
    action, request_id = _pending(control)

    document, status = _call(server, "approve", {"request_id": request_id}, credential="alice")

    assert status == 200
    assert _structured(document)["status"] == "granted"
    record = store.get_approval(request_id)
    assert record.status is ApprovalStatus.GRANTED
    assert record.approver == "mcp-operator:alice"

    granted = [e for e in store.events() if e.type is EventType.APPROVAL_GRANTED]
    assert len(granted) == 1
    assert granted[0].data["approver"] == "mcp-operator:alice"
    assert granted[0].data["via"] == "mcp-operator"
    assert granted[0].approval_id == request_id

    with with_approval(request_id):
        control.execute(action, lambda: "re_1", "refund:txn_1")
    receipt = next(r for r in store.receipts() if r.action_id == action.action_id)
    assert receipt.approver == "mcp-operator:alice"


def test_T184_deny_succeeds_with_one_and_is_attributed(server, control, store):
    _, request_id = _pending(control)

    document, status = _call(server, "deny", {"request_id": request_id}, credential="alice")

    assert status == 200
    assert _structured(document)["status"] == "denied"
    assert store.get_approval(request_id).status is ApprovalStatus.DENIED
    denied = [e for e in store.events() if e.type is EventType.APPROVAL_DENIED]
    assert denied[0].data["approver"] == "mcp-operator:alice"
    assert denied[0].data["via"] == "mcp-operator"


def test_T184_resolve_succeeds_with_one_and_is_attributed(server, control, store):
    effect_key = _ambiguous(control)

    document, status = _call(
        server,
        "resolve",
        {"effect_key": effect_key, "outcome": "failed", "reason": "the ledger has no charge"},
        credential="alice",
    )

    assert status == 200
    assert _structured(document)["state"] == "failed"
    record = store.get_effect(effect_key)
    assert record.state is EffectState.FAILED
    # `EffectRecord.resolved_by` is *who* (SPEC-v0.6 §5.3); the event's `resolved_by` is
    # `v0.1 §5.2`'s `human` constant, saying what kind of authority moved it. Two different
    # facts under one spelling, and the test asserts both so neither can drift into the other.
    assert record.resolved_by == "mcp-operator:alice"
    resolved = [e for e in store.events() if e.type is EventType.EFFECT_RESOLVED]
    assert resolved[0].data["resolved_by"] == RESOLVED_BY_HUMAN
    assert resolved[0].data["resolver"] == "mcp-operator:alice"
    assert resolved[0].data["via"] == "mcp-operator"


# --- T185 — a credential that names no human --------------------------------------------


def test_T185_a_principal_with_no_user_is_refused(control, store):
    """§3.2. An agent with no user is a machine credential, and a machine approving an action
    is the auto-approve §1.1 refuses, reached by configuration instead of by a flag."""
    _, request_id = _pending(control)
    server = OperatorServer(_config(), control, Recording(user=None))
    before = _snapshot(store)

    document, status = _call(server, "approve", {"request_id": request_id}, credential="alice")

    assert status == 403
    assert _error(document)["code"] == -41013
    assert _error(document)["data"]["error"] == "ctrlrun.not_a_human"
    assert store.get_approval(request_id).status is ApprovalStatus.PENDING
    assert _snapshot(store) == before


def test_T185_a_static_principal_is_refused_at_startup():
    """§3.1. `StaticIdentityProvider` answers with the same principal for every request, so
    every approval would carry an approver that distinguishes nobody."""
    with pytest.raises(InvalidArgument) as raised:
        OperatorConfig(principal_header=None, user_header=None)
    assert "--principal-header" in str(raised.value)


def test_T185_principal_header_without_user_header_is_refused_at_startup():
    with pytest.raises(InvalidArgument) as raised:
        OperatorConfig(principal_header="x-approver")
    assert "--user-header" in str(raised.value)


def test_T185_identity_jwt_without_a_user_claim_is_refused_at_startup():
    with pytest.raises(InvalidArgument) as raised:
        OperatorConfig(
            identity_jwt=True,
            identity_jwt_public_key="/dev/null",
            identity_jwt_algorithms=("RS256",),
            identity_jwt_issuer="https://issuer.example/",
            identity_jwt_audience="ctrlrun",
            identity_jwt_token_type="at+jwt",
        )
    assert "--identity-jwt-user-claim" in str(raised.value)


def test_T185_the_header_provider_the_config_names_carries_the_user_header(control):
    """The startup check is about a configuration that could never write; this is the other
    half — that the flag it demands is actually wired to the provider."""
    provider = operator_identity_provider(_config())
    assert isinstance(provider, IdentityProvider)
    from ctrlrun.identity import IdentityContext

    resolved = provider.resolve(
        IdentityContext(
            action="mcp-operator.approve",
            environment="production",
            headers={"x-approver": "approver-app", "x-approver-user": "alice"},
        )
    )
    assert resolved is not None and resolved.user == "alice"


# --- T186 — expiry, on both sides of the clock ------------------------------------------


def test_T186_an_expired_request_is_not_listed_and_cannot_be_answered(workspace, fake_clock):
    """§4.4. The store marks a request `expired` only when somebody tries to answer it, so a
    listing that trusted the stored status would offer a request that refuses on contact.

    One clock, moved once, driving the store, the `Control` and the server together — a
    request that expires on one of the three and not the others is not the state a deployment
    reaches by waiting.
    """
    from ctrlrun.approval import DEFAULT_APPROVAL_TTL

    store = SQLiteStateStore(workspace / ".ctrlrun" / "state.db", clock=fake_clock)
    control = Control(
        Policy.from_file(workspace / "ctrlrun.yaml"),
        store,
        LocalApprovalProvider(store, clock=fake_clock),
        clock=fake_clock,
    )
    server = OperatorServer(_config(), control, Recording(), clock=fake_clock)
    _, request_id = _pending(control)
    assert store.get_approval(request_id).status is ApprovalStatus.PENDING

    listed = _structured(_call(server, "list_pending_approvals")[0])["pending"]
    assert [entry["request_id"] for entry in listed] == [request_id]

    fake_clock.advance(DEFAULT_APPROVAL_TTL + timedelta(seconds=1))
    # The stored status is still `pending`: nothing has tried to answer it. A listing that
    # trusted it would offer an approver a request that refuses the moment they answer.
    assert store.get_approval(request_id).status is ApprovalStatus.PENDING

    listed = _structured(_call(server, "list_pending_approvals")[0])["pending"]
    assert listed == []

    document, status = _call(server, "approve", {"request_id": request_id}, credential="alice")
    assert status == 200
    assert _error(document)["code"] == -41003
    assert _error(document)["data"]["reason"] == "expired"
    store.close()


def test_T186_an_expired_credential_cannot_answer(control, store):
    """§3.3. `-41014`, not `-41007`: "your credential expired" and "you presented none" are
    different problems with different fixes."""
    _, request_id = _pending(control)
    stale = Recording(expires_at=datetime.now(UTC) - timedelta(minutes=1))
    server = OperatorServer(_config(), control, stale)
    before = _snapshot(store)

    for tool, arguments in [
        ("approve", {"request_id": request_id}),
        ("deny", {"request_id": request_id}),
        ("resolve", {"effect_key": "refund:x", "outcome": "failed", "reason": "no"}),
    ]:
        document, status = _call(server, tool, arguments, credential="alice")
        assert status == 403, tool
        assert _error(document)["code"] == -41014, tool
        assert _error(document)["data"]["error"] == "ctrlrun.principal_expired"

    assert store.get_approval(request_id).status is ApprovalStatus.PENDING
    assert _snapshot(store) == before


# --- T187 — a mutated action is refused after an MCP-relayed approval --------------------


def test_T187_a_mutated_action_is_refused_after_an_mcp_relayed_approval(server, control, store):
    """v0.1 §7 T3, reached through this server. The answer travelled through a different
    transport and the binding held."""
    action, request_id = _pending(control)
    _call(server, "approve", {"request_id": request_id}, credential="alice")

    mutated = Action(
        name="stripe.refund",
        arguments={"payment_id": "txn_1", "amount": 200001},
        principal=action.principal,
        resource="payment:txn_1",
        environment=control.environment,
    )
    with pytest.raises(ApprovalMismatch), with_approval(request_id):
        control.execute(mutated, lambda: "re_bad", "refund:txn_1")

    receipt = next(r for r in store.receipts() if r.action_id == mutated.action_id)
    assert str(receipt.result) == "blocked"
    invalidated = [e for e in store.events() if e.type is EventType.APPROVAL_INVALIDATED]
    assert [e.data["reason"] for e in invalidated] == ["mismatch"]

    # And the grant still authorizes the action it was given for, so "everything was refused"
    # cannot pass this test.
    with with_approval(request_id):
        control.execute(action, lambda: "re_1", "refund:txn_1")
    assert store.get_approval(request_id).status is ApprovalStatus.CONSUMED


# --- T188 — resolve requires a reason ---------------------------------------------------


@pytest.mark.parametrize("reason", [None, "", "   ", "\t\n"])
def test_T188_resolve_requires_a_reason(server, control, store, reason):
    """§4.5. A resolution arriving through an assistant has a conversation behind it and no
    record of it, so the reason is the record."""
    effect_key = _ambiguous(control)
    before = _snapshot(store)
    arguments = {"effect_key": effect_key, "outcome": "committed"}
    if reason is not None:
        arguments["reason"] = reason

    document, status = _call(server, "resolve", arguments, credential="alice")

    assert status == 200
    assert _error(document)["code"] == -32602
    assert "reason" in _error(document)["message"]
    assert store.get_effect(effect_key).state is EffectState.AMBIGUOUS
    assert _snapshot(store) == before


def test_T188_resolve_records_the_reason_the_resolver_and_resolved_by(server, control, store):
    """Three assertions, separately: a test that only checked the effect moved would pass with
    the reason dropped on the floor."""
    effect_key = _ambiguous(control)

    _call(
        server,
        "resolve",
        {"effect_key": effect_key, "outcome": "committed", "reason": "seen in the dashboard"},
        credential="alice",
    )

    record = store.get_effect(effect_key)
    assert record.state is EffectState.COMMITTED
    assert record.resolved_by == "mcp-operator:alice"
    event = next(e for e in store.events() if e.type is EventType.EFFECT_RESOLVED)
    assert event.data["reason"] == "seen in the dashboard"
    assert event.data["resolver"] == "mcp-operator:alice"
    assert event.data["resolved_by"] == RESOLVED_BY_HUMAN


# --- T189 — the server never executes, never resumes, never composes ---------------------


def test_T189_the_operator_server_composes_nothing():
    """§1.1. `Control` is the only module that composes the others, so the property is a
    property of this source file and is asserted against it."""
    source = Path(__import__("ctrlrun.gateway.operator", fromlist=["x"]).__file__).read_text(
        encoding="utf-8"
    )
    # Everything after the last `--- what this module may not do` marker is the assertion's
    # own vocabulary, and would otherwise match itself.
    code = source.split("FORBIDDEN")[0]
    for forbidden in (
        ".execute(",
        ".resume(",
        ".delegate(",
        ".revoke(",
        ".evaluate(",
        ".reserve_effect(",
        ".commit_effect(",
        ".fail_effect(",
        ".put_approval_request(",
        ".take_approval(",
        "resolve_principal",
    ):
        assert forbidden not in code, forbidden


def test_T189_no_tool_proposes_an_action(server, control, store):
    """The behavioural half: after every tool this server offers, no action was proposed."""
    _pending(control)
    before = len([e for e in store.events() if e.type is EventType.ACTION_PROPOSED])
    for tool, arguments in [
        ("list_pending_approvals", {}),
        ("receipts", {}),
        ("effects", {}),
        ("stats", {}),
    ]:
        _call(server, tool, arguments, credential="alice")
    after = len([e for e in store.events() if e.type is EventType.ACTION_PROPOSED])
    assert after == before


# --- T190 — every refusal shape leaves the store identical -------------------------------


REFUSALS = [
    ("approve", {"request_id": "apr_does_not_exist"}, "alice", 200, -41003),
    ("approve", {}, "alice", 200, -32602),
    ("approve", {"request_id": 7}, "alice", 200, -32602),
    ("deny", {"request_id": "apr_does_not_exist"}, "alice", 200, -41003),
    ("resolve", {"effect_key": "refund:nope", "outcome": "committed", "reason": "x"},
     "alice", 200, -41003),
    ("resolve", {"effect_key": "refund:txn_amb", "outcome": "maybe", "reason": "x"},
     "alice", 200, -32602),
    ("resolve", {"outcome": "committed", "reason": "x"}, "alice", 200, -32602),
    ("no_such_tool", {}, "alice", 200, -32602),
]


@pytest.mark.parametrize("tool,arguments,credential,status,code", REFUSALS)
def test_T190_a_refused_write_writes_nothing(
    server, control, store, tool, arguments, credential, status, code
):
    """§11. Every row of §7 that names a write tool, table-driven, so a refusal shape added
    later without this property fails."""
    _pending(control)
    _ambiguous(control)
    before = _snapshot(store)

    document, http_status = _call(server, tool, arguments, credential=credential)

    assert http_status == status, document
    assert _error(document)["code"] == code, document
    assert _snapshot(store) == before


def test_T190_a_refusal_is_logged_once_and_the_wire_says_less_than_the_log(
    server, control, store, caplog
):
    """§3.3. The caller learns that its credential was rejected and nothing about why; the
    operator's log learns which provider rejected it. Two audiences, one raise site — and one
    log line, because a refusal that logged twice was the first thing a real transcript showed.
    """
    import logging

    _, request_id = _pending(control)
    with caplog.at_level(logging.WARNING, logger="ctrlrun.mcp_operator"):
        document, status = _call(server, "approve", {"request_id": request_id})

    assert status == 403
    assert _error(document)["message"] == "no principal could be derived from the request"
    lines = [record.getMessage() for record in caplog.records]
    assert len(lines) == 1, lines


def test_T190_an_expired_credential_tells_the_log_more_than_the_client(control, caplog):
    import logging

    _, request_id = _pending(control)
    stale = Recording(expires_at=datetime.now(UTC) - timedelta(minutes=1))
    server = OperatorServer(_config(), control, stale)

    with caplog.at_level(logging.WARNING, logger="ctrlrun.mcp_operator"):
        document, _ = _call(server, "approve", {"request_id": request_id}, credential="alice")

    assert _error(document)["message"] == "the credential offered has expired"
    assert "alice" not in _error(document)["message"]
    lines = [record.getMessage() for record in caplog.records]
    assert len(lines) == 1
    assert "alice" in lines[0] and "expired at" in lines[0]


# --- T191 — over a real socket ------------------------------------------------------------


@pytest.fixture
def listening(server):
    httpd = build_operator_server(server)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[0], httpd.server_address[1]
    yield f"http://{host}:{port}/mcp"
    httpd.shutdown()
    httpd.server_close()


def _post(url, body, headers=None, *, raw=None):
    payload = raw if raw is not None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=payload, method="POST")
    request.add_header("Content-Type", "application/json")
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def test_T191_initialize_then_list_then_read_then_write(listening, control, store):
    _, request_id = _pending(control)

    status, body = _post(
        listening,
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"MCP-Protocol-Version": CURRENT, "Mcp-Method": "initialize"},
    )
    assert status == 200
    assert json.loads(body)["result"]["serverInfo"]["name"] == "ctrlrun-mcp-operator"

    status, body = _post(
        listening,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"MCP-Protocol-Version": CURRENT, "Mcp-Method": "tools/list"},
    )
    assert status == 200
    assert len(json.loads(body)["result"]["tools"]) == 8

    status, body = _post(
        listening,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "list_pending_approvals", "arguments": {}},
        },
        {
            "MCP-Protocol-Version": CURRENT,
            "Mcp-Method": "tools/call",
            "Mcp-Name": "list_pending_approvals",
        },
    )
    assert status == 200
    pending = json.loads(body)["result"]["structuredContent"]["pending"]
    assert [entry["request_id"] for entry in pending] == [request_id]

    status, body = _post(
        listening,
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "approve", "arguments": {"request_id": request_id}},
        },
        {
            "MCP-Protocol-Version": CURRENT,
            "Mcp-Method": "tools/call",
            "Mcp-Name": "approve",
            "X-Approver": "approver-app",
        },
    )
    assert status == 200
    assert store.get_approval(request_id).status is ApprovalStatus.GRANTED


def test_T191_the_transport_refusals_are_the_gateways(listening):
    """These are `ctrlrun.gateway.mcp`'s refusals, and the point of asserting them here is
    that this server routes through it rather than reimplementing it."""
    common = {"MCP-Protocol-Version": CURRENT, "Mcp-Method": "tools/list"}

    status, body = _post(listening, [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}], common)
    assert status == 400
    assert json.loads(body)["error"]["code"] == -32600

    status, body = _post(
        listening,
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        {"MCP-Protocol-Version": "2024-11-05", "Mcp-Method": "tools/list"},
    )
    assert status == 400
    assert json.loads(body)["error"]["code"] == -32022

    status, body = _post(
        listening,
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        {"MCP-Protocol-Version": CURRENT, "Mcp-Method": "tools/call"},
    )
    assert status == 400
    assert json.loads(body)["error"]["code"] == -32020


def test_T191_an_oversized_body_is_413_and_is_never_read(server):
    """Asserted through `handle`, not over the socket, and the reason is worth stating.

    The handler answers 413 from the `Content-Length` alone and never reads the body —
    deliberately, exactly as the gateway does: a body it will not read is a body it cannot
    decide about. A client that has already written part of it therefore sees a connection
    reset rather than the status, so the status is asserted where it can be observed.
    """
    response = server.handle(
        b"x" * (1024 * 1024 + 1), {"MCP-Protocol-Version": CURRENT, "Mcp-Method": "tools/list"}
    )
    assert response.status == 413
    assert response.body == b""


def test_T191_an_unlisted_origin_is_refused(listening):
    status, _ = _post(
        listening,
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        {"MCP-Protocol-Version": CURRENT, "Mcp-Method": "tools/list", "Origin": "https://evil"},
    )
    assert status == 403


def test_T191_an_unknown_method_is_method_not_found(server):
    document, status = _rpc(server, "resources/list")
    assert status == 200
    assert _error(document)["code"] == -32601


# --- T192 — import ctrlrun imports none of this -------------------------------------------


def test_T192_import_ctrlrun_does_not_import_the_operator_server():
    """T30, T92, T125b and T134's assertion, extended. `sys.modules` in a subprocess."""
    import subprocess
    import sys

    script = (
        "import sys, json; import ctrlrun; "
        "print(json.dumps(sorted(name for name in sys.modules "
        "if name.startswith('ctrlrun') or name in {'httpx','jwt'} "
        "or name.startswith('opentelemetry'))))"
    )
    output = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    ).stdout
    loaded = set(json.loads(output))
    assert "ctrlrun.gateway.operator" not in loaded
    assert "ctrlrun.gateway" not in loaded
    assert "ctrlrun.reporting" not in loaded
    assert loaded & {"httpx", "jwt"} == set()


# --- T193 — one producer per document -----------------------------------------------------


def test_T193_inspect_action_returns_the_cli_document(server, control, workspace):
    """§1.1's "not a second composer" is worth nothing if the two producers drift, so the
    assertion is equality and not shape."""
    action, request_id = _pending(control)
    _call(server, "approve", {"request_id": request_id}, credential="alice")
    with with_approval(request_id):
        control.execute(action, lambda: "re_1", "refund:txn_1")

    result = CliRunner().invoke(main, ["inspect", action.action_id, "--json"])
    assert result.exit_code == 0, result.output
    from_cli = json.loads(result.stdout)

    from_server = _structured(_call(server, "inspect_action", {"action_id": action.action_id})[0])
    assert from_server == from_cli


def test_T193_stats_returns_the_cli_document(server, control, workspace):
    action, request_id = _pending(control)
    _call(server, "deny", {"request_id": request_id}, credential="alice")
    control.execute(_action(control, "txn_2", amount=100), lambda: "re_2", "refund:txn_2")

    result = CliRunner().invoke(main, ["stats", "--json"])
    assert result.exit_code == 0, result.output
    from_cli = json.loads(result.stdout)

    from_server = _structured(_call(server, "stats")[0])
    assert from_server == from_cli


def test_T193_the_inspection_schema_is_unchanged(server, control):
    """§9.2 — no new schema string. The document this server returns is `ctrlrun.inspection/v2`
    and the one the CLI returns is too."""
    action, _ = _pending(control)
    document = _structured(_call(server, "inspect_action", {"action_id": action.action_id})[0])
    assert document["schema"] == "ctrlrun.inspection/v2"
    assert _structured(_call(server, "stats")[0])["schema"] == "ctrlrun.stats/v1"


def test_T193_an_unknown_action_id_is_an_error_not_an_empty_document(server, control):
    document, status = _call(server, "inspect_action", {"action_id": "act_nope"})
    assert status == 200
    assert _error(document)["code"] == -32602
