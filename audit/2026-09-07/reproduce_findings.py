"""Offline regression probes for the user-impact audit; no real external actions.

Run: .venv/bin/python audit/2026-09-07/reproduce_findings.py
Updated after remediation: the assertions now require the corrected behavior.
HTTPX's real forwarding/decoding path uses MockTransport to avoid external traffic.
"""

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import NoReturn
from unittest.mock import patch

import httpx

from ctrlrun import (
    Action,
    ApprovalRequired,
    Control,
    InMemoryStateStore,
    Policy,
    Principal,
    Suspended,
    with_approval,
)
from ctrlrun.gateway.server import Gateway, GatewayConfig, _Response, httpx_forwarder

POLICY = """
schema: ctrlrun.policy/v2
actions:
  mcp.audit.refund:
    effect: 'refund:{payment_id}'
    decision: allow
"""
CALL = {
    "jsonrpc": "2.0",
    "id": 42,
    "method": "tools/call",
    "params": {"name": "refund", "arguments": {"payment_id": "txn_1"}},
}
HEADERS = {
    "MCP-Protocol-Version": "2026-07-28",
    "Mcp-Method": "tools/call",
    "Mcp-Name": "refund",
}


def gateway_probe(
    handler: Callable[[httpx.Request], httpx.Response],
    body: Mapping[str, object] = CALL,
    headers: Mapping[str, str] = HEADERS,
    count: int = 1,
) -> tuple[InMemoryStateStore, list[_Response]]:
    real_client = httpx.Client
    transport = httpx.MockTransport(handler)
    store = InMemoryStateStore()
    config = GatewayConfig(
        upstream="http://127.0.0.1:9999/mcp", alias="audit", principal="audit-agent"
    )

    def client(**kwargs: object) -> httpx.Client:
        return real_client(transport=transport, **kwargs)

    with patch("httpx.Client", side_effect=client):
        forwarder = httpx_forwarder(config)
        try:
            gateway = Gateway(config, Control(Policy.from_yaml(POLICY), store), forwarder)
            responses = [gateway.handle(json.dumps(body).encode(), headers) for _ in range(count)]
        finally:
            forwarder.close()
    return store, responses


def mismatched_response_blocks_duplicate() -> dict[str, object]:
    remote_effects = []

    def upstream(request: httpx.Request) -> httpx.Response:
        # The requested action commits, but a defective upstream/proxy returns an
        # unrelated request's validation error. This is NOT evidence of non-execution.
        remote_effects.append(json.loads(request.content)["params"]["arguments"])
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 999, "error": {"code": -32602, "message": "bad params"}},
        )

    store, replies = gateway_probe(upstream, count=2)
    assert len(remote_effects) == 1
    assert store.get_effect("refund:txn_1").state.value == "ambiguous"
    return {
        "remote_effects": len(remote_effects),
        "state": "ambiguous",
        "response_id": json.loads(replies[0].body)["id"],
    }


def successful_sse_commits() -> dict[str, object]:
    final = {"jsonrpc": "2.0", "id": 42, "result": {"resultType": "complete", "content": []}}
    store, replies = gateway_probe(
        lambda request: httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=("event: message\ndata: " + json.dumps(final) + "\n\n").encode(),
        )
    )
    assert replies[0].status == 200
    assert store.get_effect("refund:txn_1").state.value == "committed"
    return {"http_status": replies[0].status, "effect_state": "committed"}


def accepted_notification_is_relayed() -> dict[str, object]:
    store, replies = gateway_probe(
        lambda request: httpx.Response(202, content=b""),
        body={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"MCP-Protocol-Version": "2025-11-25"},
    )
    assert replies[0].status == 202
    assert not store.receipts()
    return {"upstream_status": 202, "client_status": replies[0].status}


def legacy_success_commits() -> dict[str, object]:
    store, replies = gateway_probe(
        lambda request: httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 42,
                "result": {"content": [{"type": "text", "text": "refunded"}], "isError": False},
            },
        ),
        headers={"MCP-Protocol-Version": "2025-11-25"},
    )
    assert replies[0].status == 200
    assert store.get_effect("refund:txn_1").state.value == "committed"
    return {"client_status": replies[0].status, "effect_state": "committed"}


def synthesized_error_keeps_request_id() -> dict[str, object]:
    def timeout(request: httpx.Request) -> NoReturn:
        raise httpx.ReadTimeout("simulated lost reply", request=request)

    _store, replies = gateway_probe(timeout)
    document = json.loads(replies[0].body)
    assert document["id"] == CALL["id"]
    assert document["error"]["code"] == -41010
    return {
        "request_id": CALL["id"],
        "response_id": document["id"],
        "code": document["error"]["code"],
    }


def resumed_receipt_keeps_approval() -> dict[str, object]:
    now = [datetime(2026, 9, 7, tzinfo=UTC)]
    store = InMemoryStateStore(clock=lambda: now[0])
    control = Control(
        Policy.from_yaml("schema: ctrlrun.policy/v1\nactions:\n  refund:\n    decision: approve\n"),
        store,
        clock=lambda: now[0],
    )
    action = Action("refund", {"amount": 2000}, Principal("audit-agent"))

    def suspend() -> NoReturn:
        raise Suspended("audit-continuation")

    # Each expected refusal has an `else` that fails. Without them `request_id` is unbound
    # when approval was *not* required, so the probe dies with `UnboundLocalError` several
    # lines later instead of saying which precondition did not hold -- and the second block
    # would carry on silently past an action that never suspended, quietly proving nothing.
    try:
        control.execute(action, suspend, "refund:txn_1")
    except ApprovalRequired as pending:
        request_id = pending.request_id
    else:
        raise SystemExit("expected ApprovalRequired: the policy did not ask for approval")
    store.grant_approval(request_id, "human:alice")
    try:
        with with_approval(request_id):
            control.execute(action, suspend, "refund:txn_1")
    except Suspended:
        pass
    else:
        raise SystemExit("expected Suspended: the executor did not suspend")
    now[0] += timedelta(minutes=2)
    receipt = control.resume("audit-continuation", lambda: "refunded")
    assert store.get_approval(request_id).status.value == "consumed"
    assert receipt.approval_id == request_id and receipt.approver == "human:alice"
    assert (receipt.finished_at - receipt.started_at).total_seconds() == 120
    return {
        "approval_status": "consumed",
        "receipt_result": str(receipt.result),
        "receipt_approval_id": receipt.approval_id,
        "receipt_approver": receipt.approver,
        "receipt_duration_seconds": (receipt.finished_at - receipt.started_at).total_seconds(),
    }


if __name__ == "__main__":
    probes = [
        mismatched_response_blocks_duplicate,
        successful_sse_commits,
        accepted_notification_is_relayed,
        legacy_success_commits,
        synthesized_error_keeps_request_id,
        resumed_receipt_keeps_approval,
    ]
    for probe in probes:
        print(json.dumps({"probe": probe.__name__, "observed": probe()}, sort_keys=True))
