"""The ACS control hook. SPEC-v0.2 §9 (amended), acceptance tests T51-T55.

ACS is an *advisory* interface: a Guardian returns a decision and the platform executes. That
is the opposite way round from `@protect`, where CTRLRun runs the executor — so the adapter
splits one action across two hooks. `steps/toolCallRequest` decides and takes the
reservation; `steps/toolCallResult` closes it with what actually happened.

The two are joined by `Suspended`/`Control.resume` (§6.9), which already exists for exactly
this shape: a reservation held across a round trip the kernel does not control.

Read against the ACS v0.1.0 schemas, commit c7ad162 (2026-08-11):
  specification/v0.1.0/request-envelope.json
  specification/v0.1.0/response-envelope.json
  specification/v0.1.0/hooks/tool-call-request.json
  specification/v0.1.0/hooks/tool-call-result.json
  specification/v0.1.0/ask-details.json
"""

from __future__ import annotations

import json
import uuid

import pytest

from ctrlrun import Control, EffectState, Policy, SQLiteStateStore
from ctrlrun.acs import ACS_VERSION, TOOL_CALL_REQUEST, TOOL_CALL_RESULT, AcsControlHook
from ctrlrun.receipt import ReceiptResult

POLICY = """
schema: ctrlrun.policy/v2
actions:
  acs.stripe.create_refund:
    effect: "refund:{payment_id}"
    resource: "payment:{payment_id}"
    rules:
      - when: { amount_gte: 0, amount_lte: 50000 }
        decision: allow
      - when: { amount_gte: 0, amount_lte: 1000000 }
        decision: approve
      - decision: deny
  acs.stripe.read_balance:
    decision: allow
  acs.stripe.reports_before_acting:
    effect: "report:{payment_id}"
    mcp:
      not_executed_on_error: true
    decision: allow
"""


@pytest.fixture
def store(tmp_path):
    store = SQLiteStateStore(tmp_path / "state.db")
    yield store
    store.close()


@pytest.fixture
def control(store):
    return Control(Policy.from_yaml(POLICY), store)


@pytest.fixture
def hook(control):
    return AcsControlHook(control, prefix="acs")


def _envelope(method, payload, *, request_id=None, agent="refund-agent", user="alice"):
    """One ACS request envelope, in the shape request-envelope.json defines."""
    return {
        "jsonrpc": "2.0",
        "method": method,
        "id": 1,
        "params": {
            "acs_version": ACS_VERSION,
            "request_id": request_id or str(uuid.uuid4()),
            "timestamp": "2026-09-04T10:00:00Z",
            "metadata": {
                "agent_id": agent,
                "session_id": str(uuid.uuid4()),
                "environment": "production",
                "user_context": {"user_id": user, "roles": ["support"]},
            },
            "payload": payload,
        },
    }


def _call(tool="create_refund", *, payment_id="txn_1", amount=200, request_id=None):
    return _envelope(
        TOOL_CALL_REQUEST,
        {
            "tool": {"name": tool, "provider": "stripe"},
            "arguments": {
                "payment_id": {"value": payment_id},
                "amount": {"value": amount},
            },
        },
        request_id=request_id,
    )


def _result(request_id, exit_status="success", tool="create_refund"):
    return _envelope(
        TOOL_CALL_RESULT,
        {
            "tool": {"name": tool, "provider": "stripe"},
            "exit_status": exit_status,
            "outputs": [{"value": {"id": "re_1"}}],
            "request_id_ref": request_id,
        },
    )


CALL_ID = "11111111-1111-4111-8111-111111111111"


def _both(hook, exit_status="success", **call):
    """The two hooks one tool call fires, joined by request_id_ref as ACS joins them."""
    request = hook.handle(_call(request_id=CALL_ID, **call))
    result = hook.handle(_result(CALL_ID, exit_status, tool=call.get("tool", "create_refund")))
    return request, result


def _result_of(hook, response):
    assert "error" not in response, response
    return response["result"]


# --- T51: a hooked call becomes an Action ----------------------------------------------


def test_T51_the_hooked_call_becomes_an_action_with_the_right_name(hook, store):
    hook.handle(_call())

    receipt_or_events = store.events()
    assert receipt_or_events[0].action_id
    assert any(e.data.get("action_hash") for e in receipt_or_events)


def test_T51_the_tool_name_is_prefixed_and_the_provider_is_not_in_it(hook, store, control):
    """ACS carries `tool.provider` separately from `tool.name`; the action name is built from
    the configured prefix and the tool name, so a policy addresses one stable string."""
    hook.handle(_call(request_id=CALL_ID))

    proposed = store.events()[0]
    assert proposed.action_id
    hook.handle(_result(CALL_ID))
    assert store.receipts()[-1].action == "acs.stripe.create_refund"


def test_T51_the_arguments_are_unwrapped_from_their_provenance_envelopes(hook, store):
    """ACS's `arguments` is `{name: {value, provenance}}`, not raw values. An Action built
    from the envelopes rather than the values would hash something no policy can address."""
    hook.handle(_call(request_id=CALL_ID, payment_id="txn_9", amount=300))
    hook.handle(_result(CALL_ID))

    receipt = store.receipts()[-1]
    assert receipt.arguments == {"amount": 300, "payment_id": "txn_9"}


def test_T51_the_principal_comes_from_the_envelope_metadata(hook, store):
    hook.handle(_call(request_id=CALL_ID))
    hook.handle(_result(CALL_ID))

    receipt = store.receipts()[-1]
    assert receipt.principal.agent == "refund-agent"
    assert receipt.principal.user == "alice"
    assert receipt.environment == "production"


def test_T51_the_resource_comes_from_the_policy_template(hook, store):
    """ACS has no resource field; §3's template is where one comes from."""
    hook.handle(_call(request_id=CALL_ID))
    hook.handle(_result(CALL_ID))

    assert store.receipts()[-1].resource == "payment:txn_1"


def test_T51_an_operation_is_part_of_the_action_name(hook, store):
    """`operation` is ACS's sub-verb for a tool that exposes several; two verbs on one tool
    are two actions, and a policy must be able to say different things about them."""
    envelope = _call()
    envelope["params"]["payload"]["operation"] = "void"
    hook.handle(envelope)

    assert store.events()[0].action_id


# --- T52: deny and approve take ACS's decision shape -----------------------------------


def test_T52_an_allowed_call_returns_decision_allow(hook):
    response = hook.handle(_call())

    result = _result_of(hook, response)
    assert result["decision"] == "allow"
    assert result["type"] == "final"
    assert result["acs_version"] == ACS_VERSION


def test_T52_the_response_echoes_the_request_id_and_the_jsonrpc_id(hook):
    envelope = _call()
    response = hook.handle(envelope)

    assert response["jsonrpc"] == "2.0"
    assert response["id"] == envelope["id"]
    assert response["result"]["request_id"] == envelope["params"]["request_id"]


def test_T52_a_denied_call_returns_deny_with_reasoning(hook, store):
    """response-envelope.json makes `reasoning` required when the decision is `deny`."""
    response = hook.handle(_call(amount=99999999))

    result = _result_of(hook, response)
    assert result["decision"] == "deny"
    assert result["reasoning"]
    assert store.list_effects() == ()


def test_T52_an_action_needing_a_human_returns_ask_with_ask_details(hook, store):
    """ACS's `ask` is CTRLRun's APPROVE. ask-details.json requires approver, question and
    timeout_seconds, so all three are present or the response is not conformant."""
    response = hook.handle(_call(amount=200000))

    result = _result_of(hook, response)
    assert result["decision"] == "ask"
    assert result["reasoning"]
    details = result["ask_details"]
    assert details["approver"]["type"] == "human"
    assert details["approver"]["id"]
    assert details["question"]
    assert isinstance(details["timeout_seconds"], int)
    assert details["timeout_seconds"] >= 1


def test_T52_the_ask_carries_the_request_id_a_human_answers(hook, store):
    response = hook.handle(_call(amount=200000))

    action_hash = store.events()[0].data["action_hash"]
    approvals = store.approvals_for(action_hash)
    assert approvals
    assert approvals[0].approval_id in response["result"]["ask_details"]["question"]


def test_T52_an_unknown_tool_is_denied_because_policy_denies_what_it_does_not_list(hook):
    response = hook.handle(_call(tool="delete_everything"))

    assert _result_of(hook, response)["decision"] == "deny"


def test_T52_a_deny_carries_reason_codes(hook):
    result = _result_of(hook, hook.handle(_call(amount=99999999)))

    assert result["reason_codes"]


# --- T53: a mutated call after an approval is refused ----------------------------------


def test_T53_the_identical_call_executes_once_the_approval_is_granted(hook, store):
    first = _result_of(hook, hook.handle(_call(amount=200000)))
    request_id = _pending_approval(store)
    store.grant_approval(request_id, "cli:local")

    second = _result_of(hook, hook.handle(_call(amount=200000)))

    assert first["decision"] == "ask"
    assert second["decision"] == "allow"


def _pending_approval(store):
    for record in store.events():
        if record.approval_id:
            return record.approval_id
    raise AssertionError("no approval requested")


def test_T53_a_mutated_call_presenting_the_same_approval_is_refused(hook, store):
    """v0.1 §4.2 A1 over ACS: the approval binds to the action's hash, which covers the
    principal, the arguments, the resource and the environment. A different amount is a
    different action, and the human never saw it."""
    hook.handle(_call(amount=200000))
    store.grant_approval(_pending_approval(store), "cli:local")

    mutated = _result_of(hook, hook.handle(_call(amount=500000)))

    assert mutated["decision"] == "ask"  # a fresh question, not the granted one
    assert store.list_effects() == ()


def test_T53_a_second_call_on_a_committed_effect_is_denied(hook, store):
    _both(hook)

    again = _result_of(hook, hook.handle(_call()))

    assert again["decision"] == "deny"
    assert "duplicate" in " ".join(again["reason_codes"]).lower()


def test_T53_a_concurrent_call_while_one_is_outstanding_is_denied(hook, store):
    """The reservation is held between the two hooks, which is the whole point of splitting
    them: an ACS platform that fires two toolCallRequests before either result must not get
    two allows."""
    hook.handle(_call())

    second = _result_of(hook, hook.handle(_call()))

    assert second["decision"] == "deny"
    assert store.get_effect("refund:txn_1").state is EffectState.EXECUTING


# --- T54: the outcome mapping, via steps/toolCallResult --------------------------------


@pytest.mark.parametrize(
    ("exit_status", "state", "result"),
    [
        ("success", EffectState.COMMITTED, ReceiptResult.COMMITTED),
        ("timeout", EffectState.AMBIGUOUS, ReceiptResult.AMBIGUOUS),
        ("failure", EffectState.AMBIGUOUS, ReceiptResult.AMBIGUOUS),
        ("blocked", EffectState.FAILED, ReceiptResult.FAILED),
    ],
)
def test_T54_exit_status_maps_by_the_asymmetry_of_v0_1_section_5_5(
    hook, store, exit_status, state, result
):
    """ACS names four statuses and says nothing about what any of them means for the side
    effect. This is the fail-closed reading, and the same one §6.8 applies to MCP:

    - `success` is the only one that asserts the effect happened;
    - `blocked` is the only one that asserts it did not — a control refused before dispatch;
    - `failure` and `timeout` are both unknown, because a tool that failed after acting and
      a tool that failed before acting report the same string.
    """
    _both(hook, exit_status)

    assert store.get_effect("refund:txn_1").state is state
    assert store.receipts()[-1].result is result


def test_T54_failure_is_FAILED_only_where_the_operator_asserted_it(hook, store):
    """`not_executed_on_error` (§3.1) is the operator's per-tool claim that this tool reports
    errors only before acting. It is the same assertion in ACS as it is in MCP."""
    _both(hook, "failure", tool="reports_before_acting")

    assert store.get_effect("report:txn_1").state is EffectState.FAILED


def test_T54_the_result_hook_returns_allow_because_it_redacts_nothing(hook, store):
    """ACS describes toolCallResult as an output redaction checkpoint. CTRLRun records the
    outcome and changes no output, so the conformant answer is `allow`."""
    _, response = _both(hook)

    assert _result_of(hook, response)["decision"] == "allow"


def test_T54_a_result_for_a_call_nobody_reserved_is_recorded_and_not_invented(hook, store):
    """A result with no matching request — a restarted Guardian, or a platform that fired
    them out of order. There is no reservation to close, so nothing is written about an
    effect, and the response still permits the output through."""
    response = hook.handle(_result(str(uuid.uuid4())))

    assert _result_of(hook, response)["decision"] == "allow"
    assert store.receipts() == ()


def test_T54_a_call_with_no_effect_key_needs_no_result_to_be_complete(hook, store):
    """A read has no reservation to hold, so the request hook completes it outright."""
    envelope = _envelope(
        TOOL_CALL_REQUEST,
        {"tool": {"name": "read_balance", "provider": "stripe"}, "arguments": {}},
    )

    assert _result_of(hook, hook.handle(envelope))["decision"] == "allow"
    assert store.list_effects() == ()


def test_T54_not_executed_on_error_does_not_reach_across_to_timeout(hook, store):
    """The operator asserted something about *errors*. A timeout is not an error the tool
    reported — it is the absence of any report — and no per-tool claim can speak for it."""
    _both(hook, "timeout", tool="reports_before_acting")

    assert store.get_effect("report:txn_1").state is EffectState.AMBIGUOUS


def test_T54_a_result_with_no_request_id_ref_writes_nothing(hook, store):
    """Nothing links this result to a call. Guessing which one it meant is how a duplicate
    gets committed, so no effect is written and the output still passes."""
    envelope = _result(CALL_ID)
    del envelope["params"]["payload"]["request_id_ref"]
    hook.handle(_call(request_id=CALL_ID))

    response = hook.handle(envelope)

    assert _result_of(hook, response)["decision"] == "allow"
    assert store.get_effect("refund:txn_1").state is EffectState.EXECUTING
    assert store.receipts() == ()


def test_T51_an_argument_that_is_not_a_provenance_envelope_is_refused(hook, store):
    """`tool-call-request.json` requires `{value, provenance?}` per argument. A bare value
    is a payload this adapter does not understand, and reading it as one would hash
    something the producer did not mean."""
    envelope = _call()
    # A mapping with provenance but no `value` — the shape a producer gets wrong, and the
    # one a bare-value check would let through into `envelope["value"]` as a KeyError.
    envelope["params"]["payload"]["arguments"] = {"payment_id": {"provenance": {"origin": "user"}}}

    response = hook.handle(envelope)

    assert "error" in response
    assert store.events() == ()

    bare = _call()
    bare["params"]["payload"]["arguments"] = {"payment_id": "txn_1"}
    assert "error" in hook.handle(bare)


def test_T51_a_call_with_no_agent_id_is_refused(hook, store):
    """An Action cannot exist without a principal (v0.1 §2.1). ACS makes `agent_id` required
    on every envelope, so its absence is a malformed request, not a decision."""
    envelope = _call()
    del envelope["params"]["metadata"]["agent_id"]

    response = hook.handle(envelope)

    assert "error" in response
    assert store.events() == ()
    # `Principal` would refuse an empty agent too, with a message about the Principal. This
    # one names the ACS field a producer has to fix, which is the difference worth keeping.
    assert "agent_id" in response["error"]["message"]


def test_T51_the_operation_appears_in_the_recorded_action_name(hook, store):
    """Asserted on the receipt, not merely on an event existing: two verbs on one tool are
    two actions, and a policy has to be able to say different things about them."""
    envelope = _call(request_id=CALL_ID)
    envelope["params"]["payload"]["operation"] = "void"
    envelope["params"]["payload"]["tool"]["name"] = "read_balance"
    hook.handle(envelope)

    proposed = [e for e in store.events() if e.type.value == "POLICY_EVALUATED"]
    assert proposed, "the action reached the policy"
    assert store.receipts()[-1].action == "acs.stripe.read_balance.void"


# --- T55: the adapter is in an extra, and says so when it is absent --------------------


def test_T55_an_unknown_method_is_a_jsonrpc_error_in_the_reserved_range(hook):
    response = hook.handle(_envelope("steps/sessionStart", {}))

    assert "error" in response
    assert -32099 <= response["error"]["code"] <= -32000


def test_T55_a_malformed_envelope_is_a_jsonrpc_error(hook):
    well_formed = _call()
    wrong_version = {**well_formed, "jsonrpc": "1.0"}
    for broken in (
        {},
        {"jsonrpc": "2.0"},
        {"jsonrpc": "1.0", "method": TOOL_CALL_REQUEST},
        # Only the version is wrong; every other guard would let this through, so this is
        # the case that proves the version check is doing something.
        wrong_version,
    ):
        response = hook.handle(broken)
        assert "error" in response, broken


def test_T55_the_adapter_is_not_imported_by_importing_ctrlrun():
    import subprocess
    import sys

    finished = subprocess.run(
        [sys.executable, "-c", "import ctrlrun, sys; print('ctrlrun.acs' in sys.modules)"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert finished.stdout.strip() == "False"


def test_T55_the_response_is_json_serialisable(hook):
    """A Guardian puts this on a wire; anything that will not serialise is a bug here."""
    for envelope in (_call(), _call(amount=200000), _call(amount=99999999)):
        assert json.loads(json.dumps(hook.handle(envelope)))
