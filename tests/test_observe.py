"""Observe mode and `ctrlrun stats`. Build-list item 4; SPEC-v0.3 §6.

The acceptance tests are T82 to T87. Four sentences run through all of them:

**Everything is evaluated, nothing about an action is enforced, everything is recorded**
(§6.2). T82 asserts both halves in one test, so "observe executes" cannot pass by the action
having been allowed.

**Observe mode is not a dry run** (§6.6). It executes, effects land at remotes, and the
records of them are real — which is why T83 asserts that a *refused* reservation writes no
effect record, and why T87 asserts the store survives the switch to enforce untouched.

**There is one switch and it governs the process** (§6.1). A `mode:` anywhere but the top
level is a load error naming the path, and T84 walks every nesting the document has.

**A rollout mode that pages a human is a queue that fills up** (§6.2). T82c asserts the
absence of the request, the absence of the event, and the executor having run anyway.
"""

from __future__ import annotations

import json
import socket
from datetime import UTC, datetime, timedelta

import pytest
from click.testing import CliRunner

from ctrlrun import (
    Action,
    ActionDenied,
    AmbiguousEffect,
    ApprovalRequired,
    Control,
    DuplicateEffect,
    EffectKeyError,
    EffectState,
    IdentityError,
    InMemoryStateStore,
    Policy,
    PolicyError,
    Principal,
    SQLiteStateStore,
    context,
    protect,
)
from ctrlrun.authority import Authority, grant_from_yaml
from ctrlrun.cli.main import OBSERVE_BANNER, STATS_SCHEMA, main
from ctrlrun.errors import AuthorityEscalation
from ctrlrun.policy import ENFORCE, OBSERVE
from ctrlrun.receipt import (
    BLOCKED_AMBIGUOUS,
    BLOCKED_APPROVAL_REQUIRED,
    BLOCKED_DUPLICATE,
    BLOCKED_IN_PROGRESS,
    Receipt,
    ReceiptResult,
)

V3 = "schema: ctrlrun.policy/v3\n"

ACTIONS = """
actions:
  stripe.refund:
    rules:
      - when: { amount_lte: 50000 }
        decision: allow
      - when: { amount_lte: 5000000 }
        decision: approve
      - decision: deny
  customer.read:
    decision: allow
"""

DELEGABLE = """
authority:
  grants:
    - id: head-of-support
      subject: { agent: "support-agent", user: "alice@example.com" }
      actions: ["stripe.refund"]
      constraints: { amount_lte: 200000 }
      delegable: true
      expires_at: "2027-01-01T00:00:00Z"
"""


class _Clock:
    """Static: it moves only when a test moves it, so an expiry boundary is exact."""

    def __init__(self) -> None:
        self.now = datetime(2026, 9, 3, 10, 12, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@pytest.fixture
def clock():
    return _Clock()


@pytest.fixture
def store(clock):
    return InMemoryStateStore(clock=clock)


class _Executor:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return "ok"


def _explode():
    raise TimeoutError("the remote never answered")


def _document(mode: str = OBSERVE, actions: str = ACTIONS, authority: str = "") -> str:
    return f"{V3}mode: {mode}\n{authority}{actions}"


def _control(document, store, clock, **kwargs):
    authority = Authority.from_yaml(document) if "authority:" in document else None
    return Control(
        Policy.from_yaml(document),
        store,
        authority=authority,
        clock=clock,
        environment="production",
        **kwargs,
    )


def _action(**overrides):
    fields = {
        "name": "stripe.refund",
        "arguments": {"amount": 1000, "payment_id": "EU-42"},
        "principal": Principal(agent="support-agent", user="alice@example.com"),
        "resource": "payment:EU-42",
        "environment": "production",
    }
    fields.update(overrides)
    return Action(**fields)


def _types(store):
    return [str(event.type) for event in store.events()]


# --- T82: observe executes what enforce would deny (§6.2, §6.3) -------------------------


def test_T82_observe_executes_what_enforce_would_deny(store, clock):
    """Both halves in one test: the same document under `mode: enforce` raises and does not
    run, so "observe executes" cannot pass by the action having been allowed."""
    denied = _action(arguments={"amount": 90000000, "payment_id": "EU-44"})

    observing = _control(_document(OBSERVE), store, clock)
    watched = _Executor()
    receipt = observing.execute(denied, watched, "refund:EU-44")

    assert watched.calls == 1
    assert receipt.result is ReceiptResult.OBSERVED
    assert receipt.execution is ReceiptResult.COMMITTED
    assert receipt.would_have is not None
    assert receipt.would_have.decision.value == "deny"
    assert receipt.would_have.reason == receipt.decision_reason
    assert receipt.would_have.blocked_reason == receipt.decision_reason

    enforcing = _control(_document(ENFORCE), InMemoryStateStore(clock=clock), clock)
    refused = _Executor()
    with pytest.raises(ActionDenied):
        enforcing.execute(denied, refused, "refund:EU-44")

    assert refused.calls == 0


def test_T82_an_observed_receipt_nothing_would_have_blocked_still_says_observed(store, clock):
    """§6.3 — `result` is "observed" for every receipt an observe-mode run observed, so a
    reader never infers from the absence of a field that a receipt describes an unenforced
    run. `blocked_reason` is the field that says nothing intervened."""
    control = _control(_document(OBSERVE), store, clock)

    receipt = control.execute(_action(), _Executor(), "refund:EU-42")

    assert receipt.result is ReceiptResult.OBSERVED
    assert receipt.execution is ReceiptResult.COMMITTED
    assert receipt.would_have is not None
    assert receipt.would_have.decision.value == "allow"
    assert receipt.would_have.blocked_reason is None


def test_T82_the_observed_receipt_round_trips_through_json(store, clock):
    """§12.2 — `observed` is not in v0.1 §6.1's set of results, and `execution` and
    `would_have` are new keys. A receipt that cannot be read back is not evidence."""
    control = _control(_document(OBSERVE), store, clock)

    receipt = control.execute(
        _action(arguments={"amount": 90000000, "payment_id": "EU-44"}), _Executor(), None
    )
    document = json.loads(receipt.to_json())

    assert document["schema"] == "ctrlrun.receipt/v3"
    assert document["result"] == "observed"
    assert document["execution"] == "committed"
    assert document["would_have"]["decision"] == "deny"
    assert Receipt.from_json(receipt.to_json()).to_dict() == document
    assert store.receipts()[-1].to_dict() == document


def test_T82_a_receipt_written_before_v0_3_still_parses(store, clock):
    """`execution` and `would_have` are absent from every receipt 0.2 wrote (§12.2)."""
    control = _control(_document(ENFORCE), store, clock)
    document = control.execute(_action(), _Executor(), None).to_dict()
    del document["execution"]
    del document["would_have"]

    older = Receipt.from_dict(document)

    assert older.execution is None
    assert older.would_have is None


def test_T82_enforce_mode_writes_neither_new_field(store, clock):
    """§6.3 — in enforce mode `execution` is always null: `result` already carries it, and
    duplicating it would give two fields that can disagree."""
    control = _control(_document(ENFORCE), store, clock)

    receipt = control.execute(_action(), _Executor(), "refund:EU-42")

    assert receipt.execution is None
    assert receipt.would_have is None
    assert json.loads(receipt.to_json())["would_have"] is None


def test_T82_an_absent_mode_key_is_enforce(store, clock):
    """§6.1 — the fail-closed default, so a document predating the key enforces."""
    control = _control(V3 + ACTIONS, store, clock)

    assert control.policy.mode == ENFORCE
    with pytest.raises(ActionDenied):
        control.execute(
            _action(arguments={"amount": 90000000, "payment_id": "EU-44"}), _Executor(), None
        )


@pytest.mark.authority
def test_T82_authority_is_evaluated_in_full_and_recorded_not_enforced(store, clock):
    """§6.2 — AUTHORITY_DENIED is appended exactly as in enforce mode, including §4.3's rule
    that a denial by authority skips policy. ACTION_DENIED is not: the action ran."""
    authority = """
authority:
  grants:
    - id: reads-only
      subject: { agent: "support-agent" }
      actions: ["customer.read"]
"""
    control = _control(_document(OBSERVE, authority=authority), store, clock)
    executor = _Executor()

    receipt = control.execute(_action(), executor, "refund:EU-42")

    assert executor.calls == 1
    assert receipt.result is ReceiptResult.OBSERVED
    assert receipt.would_have is not None
    assert receipt.would_have.blocked_reason == "no_authority"
    assert "AUTHORITY_DENIED" in _types(store)
    assert "POLICY_EVALUATED" not in _types(store)
    assert "ACTION_DENIED" not in _types(store)


@pytest.mark.authority
def test_T82_a_grant_that_passes_still_appends_authority_resolved(store, clock):
    control = _control(_document(OBSERVE, authority=DELEGABLE), store, clock)

    receipt = control.execute(_action(), _Executor(), "refund:EU-42")

    assert receipt.would_have is not None
    assert receipt.would_have.blocked_reason is None
    assert "AUTHORITY_RESOLVED" in _types(store)
    assert "POLICY_EVALUATED" in _types(store)


def test_T82_an_expired_principal_is_recorded_and_not_enforced(store, clock):
    """§6.2's first bullet — `principal_expired` is evaluated and recorded, not enforced.
    Enforce mode raises `IdentityError` here and never runs the executor."""
    expired = _action(
        principal=Principal(agent="support-agent", expires_at=clock.now - timedelta(minutes=1))
    )
    executor = _Executor()

    receipt = _control(_document(OBSERVE), store, clock).execute(expired, executor, None)

    assert executor.calls == 1
    assert receipt.would_have is not None
    assert receipt.would_have.blocked_reason == "principal_expired"
    assert receipt.would_have.decision.value == "deny"

    with pytest.raises(IdentityError):
        _control(_document(ENFORCE), InMemoryStateStore(clock=clock), clock).execute(
            expired, _Executor(), None
        )


# --- T82b: what observe mode still refuses (§6.2) --------------------------------------


def test_T82b_no_principal_is_refused_under_observe(store, clock):
    """§6.2 — there is no Action to observe and nothing to attribute a receipt to. The shape
    matches enforce mode's exactly: no receipt, no events."""
    executor = _Executor()

    @protect("stripe.refund", control=_control(_document(OBSERVE), store, clock))
    def refund(amount: int, payment_id: str):
        return executor()

    with pytest.raises(ActionDenied) as refused:
        refund(amount=1000, payment_id="EU-42")

    assert refused.value.reason == "no_principal"
    assert executor.calls == 0
    assert store.events() == ()
    assert store.receipts() == ()


def test_T82b_a_raising_identity_provider_is_refused_under_observe(store, clock):
    """§6.2 — the same row: no principal was produced, so there is nothing to observe."""

    class _Refusing:
        def resolve(self, identity_context):
            raise IdentityError("the credential was rejected")

    executor = _Executor()
    control = _control(_document(OBSERVE), store, clock, identity=_Refusing())

    @protect("stripe.refund", control=control)
    def refund(amount: int, payment_id: str):
        return executor()

    with context("support-agent"), pytest.raises(IdentityError):
        refund(amount=1000, payment_id="EU-42")

    assert executor.calls == 0
    assert store.events() == ()
    assert store.receipts() == ()


def test_T82b_an_unresolvable_effect_key_is_refused_under_observe(store, clock):
    """§6.2 — the action has no identity, so "would it have been a duplicate" has no answer.
    Recorded exactly as enforce mode records it, and the receipt carries no counterfactual
    because the action genuinely was stopped (§6.3)."""
    executor = _Executor()
    control = _control(_document(OBSERVE), store, clock)

    @protect("stripe.refund", effect="refund:{missing}", control=control)
    def refund(amount: int, payment_id: str):
        return executor()

    with context("support-agent"), pytest.raises(EffectKeyError):
        refund(amount=1000, payment_id="EU-42")

    assert executor.calls == 0
    assert _types(store) == ["ACTION_PROPOSED", "ACTION_DENIED"]
    receipt = store.receipts()[-1]
    assert receipt.result is ReceiptResult.DENIED
    assert receipt.execution is None
    assert receipt.would_have is None


def test_T82b_an_unrepresentable_argument_is_refused_under_observe(tmp_path, clock):
    """§6.2 — the Action cannot be constructed, so there is nothing to observe. Asserted
    through the gateway, because that is the only path that reaches v0.2 §6.6."""
    from ctrlrun.gateway.server import Gateway, GatewayConfig

    forwarded: list[bytes] = []

    def forwarder(body, headers, *, fresh):
        forwarded.append(body)
        return None, b"{}", 200, {}

    document = (
        f"{V3}mode: observe\n"
        "actions:\n"
        "  mcp.acme.create_refund:\n"
        "    decision: allow\n"
        '    effect: "refund:{payment_id}"\n'
    )
    store = SQLiteStateStore(tmp_path / "state.db", clock=clock)
    control = Control(Policy.from_yaml(document), store, clock=clock, environment="production")
    gateway = Gateway(
        GatewayConfig(
            upstream="http://127.0.0.1:1/mcp", alias="acme", principal_header="X-Agent", port=0
        ),
        control,
        forwarder,
    )
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "create_refund", "arguments": {"payment_id": "p1", "amount": 20.0}},
        }
    ).encode()

    response = gateway.handle(
        body,
        {"content-type": "application/json", "accept": "application/json", "x-agent": "bot"},
    )

    assert response.status == 400
    assert json.loads(response.body)["error"]["data"]["error"] == "ctrlrun.unrepresentable_argument"
    assert forwarded == []
    receipt = store.receipts()[-1]
    assert receipt.result is ReceiptResult.DENIED
    assert receipt.would_have is None
    store.close()


def _gateway(tmp_path, clock, mode, actions, observations):
    """One `Gateway` over an in-process forwarder, so `handle()` is the whole harness."""
    from ctrlrun.gateway.server import Gateway, GatewayConfig

    forwarded: list[bytes] = []

    def forwarder(body, headers, *, fresh):
        forwarded.append(body)
        return observations.pop(0), b'{"jsonrpc":"2.0","id":1,"result":{"content":[]}}', 200, {}

    document = f"{V3}mode: {mode}\n{actions}"
    store = SQLiteStateStore(tmp_path / "state.db", clock=clock)
    control = Control(Policy.from_yaml(document), store, clock=clock, environment="production")
    config = GatewayConfig(
        upstream="http://127.0.0.1:1/mcp", alias="acme", principal_header="X-Agent", port=0
    )
    return Gateway(config, control, forwarder), store, forwarded


def _tools_call(payment_id="p1", amount=200000):
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "create_refund",
                "arguments": {"payment_id": payment_id, "amount": amount},
            },
        }
    ).encode()


GATEWAY_ACTIONS = """
actions:
  mcp.acme.create_refund:
    decision: approve
    effect: "refund:{payment_id}"
"""


@pytest.mark.parametrize("mode", [OBSERVE, ENFORCE])
def test_T82_the_gateway_enforces_nothing_in_observe_mode(tmp_path, clock, mode):
    """v0.2 §6.10's pre-check refuses a `tools/call` before `Control.execute` is reached — a
    denied request, a committed key, an ambiguous one. Three of §9's ⚠ rows, enforced by the
    gateway rather than by the kernel, so §6.1's one switch has to reach them too."""
    from ctrlrun.gateway.outcome import UpstreamResult

    observed = [UpstreamResult(result_type="content"), UpstreamResult(result_type="content")]
    gateway, store, forwarded = _gateway(tmp_path, clock, mode, GATEWAY_ACTIONS, observed)
    store.reserve_effect("refund:p1", "act_earlier", timedelta(minutes=5))
    store.begin_execution("refund:p1", "act_earlier")
    store.commit_effect("refund:p1", "act_earlier", None)
    headers = {"content-type": "application/json", "accept": "application/json", "x-agent": "bot"}

    response = gateway.handle(_tools_call(), headers)

    if mode == OBSERVE:
        assert forwarded, "the gateway refused a call in observe mode"
        assert response.status == 200
        assert store.receipts()[-1].result is ReceiptResult.OBSERVED
        # The approval gate is the first thing enforce mode would have stopped at, and
        # `blocked_reason` keeps the first reason: a later refusal is one enforce mode would
        # never have reached. The committed key is why the pre-check would have refused.
        assert store.receipts()[-1].would_have.blocked_reason == BLOCKED_APPROVAL_REQUIRED
        assert store.get_effect("refund:p1").action_id == "act_earlier"
    else:
        assert forwarded == []
        assert json.loads(response.body)["error"]["code"] == -41004
    store.close()


@pytest.mark.authority
def test_T82b_an_escalating_delegation_is_refused_under_observe(store, clock):
    """§6.2 — a delegation is a durable authority record, not a decision about an action.
    No row is written, and T87 makes records created under observe survive the switch."""
    control = _control(_document(OBSERVE, authority=DELEGABLE), store, clock)
    escalating = grant_from_yaml(
        """
subject: { agent: "support-agent", user: "alice@example.com" }
actions: ["stripe.refund"]
constraints: { amount_lte: 900000 }
expires_at: "2026-12-01T00:00:00Z"
"""
    )

    with pytest.raises(AuthorityEscalation) as refused:
        control.delegate(
            "head-of-support",
            escalating,
            by=Principal(agent="support-agent", user="alice@example.com"),
        )

    assert refused.value.reason == "containment"
    assert store.delegations(include_revoked=True) == ()
    assert "DELEGATION_REJECTED" in _types(store)
    assert "DELEGATION_CREATED" not in _types(store)


# --- T82c: observe mode asks no human (§6.2) -------------------------------------------


def test_T82c_observe_mode_asks_no_human(store, clock):
    needs_a_human = _action(arguments={"amount": 100000, "payment_id": "EU-43"})
    control = _control(_document(OBSERVE), store, clock)
    executor = _Executor()

    try:
        receipt = control.execute(needs_a_human, executor, "refund:EU-43")
    except ApprovalRequired as pending:  # pragma: no cover - the raise is the failure
        raise AssertionError(
            f"observe mode created approval request {pending.request_id}; SPEC-v0.3 §6.2 "
            "forbids paging a human about an action it is about to execute regardless"
        ) from pending

    assert executor.calls == 1
    assert receipt.result is ReceiptResult.OBSERVED
    assert receipt.would_have is not None
    assert receipt.would_have.decision.value == "approve"
    assert receipt.would_have.blocked_reason == BLOCKED_APPROVAL_REQUIRED
    assert store.approvals_for(needs_a_human.action_hash) == ()
    assert "APPROVAL_REQUESTED" not in _types(store)


def test_T82c_enforce_mode_still_asks(store, clock):
    """The control: without it, an `execute` that never reached the approval gate at all
    would satisfy the test above."""
    control = _control(_document(ENFORCE), store, clock)

    with pytest.raises(ApprovalRequired):
        control.execute(
            _action(arguments={"amount": 100000, "payment_id": "EU-43"}), _Executor(), None
        )

    assert "APPROVAL_REQUESTED" in _types(store)


# --- T83: a duplicate is recorded and still runs (§6.2, §6.3) --------------------------


def test_T83_a_duplicate_is_recorded_and_still_runs(store, clock):
    control = _control(_document(OBSERVE), store, clock)
    executor = _Executor()

    control.execute(_action(), executor, "refund:EU-42")
    before = store.get_effect("refund:EU-42")
    second = control.execute(_action(), executor, "refund:EU-42")
    after = store.get_effect("refund:EU-42")

    assert executor.calls == 2
    assert second.would_have is not None
    assert second.would_have.blocked_reason == BLOCKED_DUPLICATE
    # §6.3 — `execution` describes the executor, which ran and returned. It is not a claim
    # about the effect record, which this attempt never held.
    assert second.execution is ReceiptResult.COMMITTED
    assert (after.state, after.attempt, after.action_id) == (
        before.state,
        before.attempt,
        before.action_id,
    )


def test_T83_a_refused_reservation_never_touches_the_store_record(store, clock, monkeypatch):
    """§6.2's first reason, isolated. `commit_effect` and its siblings admit only the holder
    (v0.1 §5.3 E1), so an attempt that wrote here would be refused by the store — which means
    a test asserting only the record's final state cannot tell the two defences apart. This
    one asserts the store was never asked."""
    asked: list[tuple[str, str]] = []

    for name in ("begin_execution", "commit_effect", "fail_effect", "mark_ambiguous"):
        original = getattr(InMemoryStateStore, name)

        def recording(self, effect_key, action_id, *args, _name=name, _original=original, **kw):
            asked.append((_name, action_id))
            return _original(self, effect_key, action_id, *args, **kw)

        monkeypatch.setattr(InMemoryStateStore, name, recording)

    control = _control(_document(OBSERVE), store, clock)
    executor = _Executor()
    control.execute(_action(), executor, "refund:EU-42")
    first = {action_id for _, action_id in asked}
    second = control.execute(_action(), executor, "refund:EU-42")

    assert executor.calls == 2
    assert second.would_have.blocked_reason == BLOCKED_DUPLICATE
    assert {action_id for _, action_id in asked} == first, (
        "observe mode asked the store to move a record its attempt never reserved"
    )


def test_T83_an_ambiguous_record_is_recorded_and_left_ambiguous(store, clock):
    """§6.2 — a record already in AMBIGUOUS would be moved to COMMITTED by a machine, and
    v0.1 §5.2 reserves that for a human. The `else` branch fails the test."""
    control = _control(_document(OBSERVE), store, clock)

    with pytest.raises(TimeoutError):
        control.execute(_action(), _explode, "refund:EU-42")
    assert store.get_effect("refund:EU-42").state is EffectState.AMBIGUOUS

    executor = _Executor()
    second = control.execute(_action(), executor, "refund:EU-42")

    assert executor.calls == 1
    assert second.would_have is not None
    assert second.would_have.blocked_reason == BLOCKED_AMBIGUOUS
    record = store.get_effect("refund:EU-42")
    if record.state is not EffectState.AMBIGUOUS:
        raise AssertionError(
            f"observe mode moved an AMBIGUOUS record to {record.state}; SPEC-v0.3 §6.2 "
            "reserves that for a human and for the reconcile hook, and observe mode is neither"
        )


def test_T83_the_reconcile_hook_is_never_called_in_observe_mode(store, clock):
    """§6.2 — its blocking trigger fires when an AMBIGUOUS record refuses a reservation; in
    observe mode that refusal is ignored, so there is nothing being unblocked."""
    control = _control(_document(OBSERVE), store, clock)
    asked: list[str] = []

    def reconcile(effect_key):
        asked.append(effect_key)
        return "committed"

    with pytest.raises(TimeoutError):
        control.execute(_action(), _explode, "refund:EU-42")

    executor = _Executor()
    second = control.execute(_action(), executor, "refund:EU-42", reconcile=reconcile)

    assert asked == []
    assert executor.calls == 1
    assert second.would_have is not None
    assert second.would_have.blocked_reason == BLOCKED_AMBIGUOUS
    assert store.get_effect("refund:EU-42").state is EffectState.AMBIGUOUS
    assert "RECONCILIATION_STARTED" not in _types(store)


def test_T83_the_same_hook_is_called_in_enforce_mode(store, clock):
    """The control for the test above: the hook has to be one the real code would call."""
    control = _control(_document(ENFORCE), store, clock)
    asked: list[str] = []

    def reconcile(effect_key):
        asked.append(effect_key)
        return "unknown"

    with pytest.raises(TimeoutError):
        control.execute(_action(), _explode, "refund:EU-42")
    with pytest.raises(AmbiguousEffect):
        control.execute(_action(), _Executor(), "refund:EU-42", reconcile=reconcile)

    assert asked == ["refund:EU-42"]


def test_T83_enforce_mode_still_refuses_the_duplicate(store, clock):
    """The control: without it, a `_secure` that never reserved anything would satisfy the
    two observe tests above."""
    control = _control(_document(ENFORCE), store, clock)
    executor = _Executor()

    control.execute(_action(), executor, "refund:EU-42")
    with pytest.raises(DuplicateEffect):
        control.execute(_action(), executor, "refund:EU-42")

    assert executor.calls == 1


def test_T83_a_key_another_attempt_holds_is_recorded_as_in_progress(store, clock):
    """§6.3's vocabulary keeps `duplicate` and `in_progress` apart, because one says the
    effect happened and the other says another attempt holds the key right now."""
    control = _control(_document(OBSERVE), store, clock)
    store.reserve_effect("refund:EU-42", "act_someone_else", timedelta(minutes=5))
    executor = _Executor()

    receipt = control.execute(_action(), executor, "refund:EU-42")

    assert executor.calls == 1
    assert receipt.would_have is not None
    assert receipt.would_have.blocked_reason == BLOCKED_IN_PROGRESS
    record = store.get_effect("refund:EU-42")
    assert (record.state, record.action_id) == (EffectState.RESERVED, "act_someone_else")


def test_T83_an_executor_that_fails_on_a_held_key_still_writes_the_record(store, clock):
    """§6.2 — where the reservation *succeeded*, the outcome maps through v0.1 §5.5 unchanged
    and this attempt's effect record is written. Observe mode records what happened."""
    control = _control(_document(OBSERVE), store, clock)

    with pytest.raises(TimeoutError):
        control.execute(_action(), _explode, "refund:EU-42")

    assert store.get_effect("refund:EU-42").state is EffectState.AMBIGUOUS
    receipt = store.receipts()[-1]
    assert receipt.result is ReceiptResult.OBSERVED
    assert receipt.execution is ReceiptResult.AMBIGUOUS


# --- T84: `mode` is top-level only (§6.1) ----------------------------------------------


NESTED = {
    "action entry": V3
    + """
actions:
  stripe.refund:
    mode: observe
    decision: allow
""",
    "rule": V3
    + """
actions:
  stripe.refund:
    rules:
      - when: { amount_lte: 100 }
        mode: observe
        decision: allow
""",
    "grant": V3
    + """
authority:
  grants:
    - id: g
      mode: observe
      subject: { agent: "a" }
      actions: ["stripe.refund"]
"""
    + ACTIONS,
    "authority section": V3
    + """
authority:
  mode: observe
  grants: []
"""
    + ACTIONS,
}


@pytest.mark.parametrize("where", sorted(NESTED))
def test_T84_mode_is_refused_anywhere_but_the_top_level(where, tmp_path):
    """`Control.from_file` is the load: it runs both loaders over the same document, which is
    the only place all four nestings are read (§4.8)."""
    (tmp_path / "ctrlrun.yaml").write_text(NESTED[where])

    with pytest.raises(PolicyError) as refused:
        Control.from_file(tmp_path / "ctrlrun.yaml")

    assert "ctrlrun.yaml" in str(refused.value)
    assert "'mode'" in str(refused.value)
    assert "top level" in str(refused.value)


@pytest.mark.parametrize("where", ["action entry", "rule"])
def test_T84_the_policy_loader_owns_the_two_nestings_inside_actions(where):
    with pytest.raises(PolicyError) as refused:
        Policy.from_yaml(NESTED[where], source="policy.yaml")

    assert "policy.yaml" in str(refused.value)
    assert "top level" in str(refused.value)


@pytest.mark.parametrize("where", ["grant", "authority section"])
def test_T84_the_authority_loader_owns_the_two_inside_the_section(where):
    """§4.8 — the `authority:` section is parsed by `authority.py` on the `--authority` path,
    which the policy loader never reads. The guard exists on both, not on whichever runs
    first."""
    with pytest.raises(PolicyError) as refused:
        Authority.from_yaml(NESTED[where], source="policy.yaml")

    assert "top level" in str(refused.value)


def test_T84_mode_is_refused_in_a_standalone_authority_document():
    """§8.3 — an `--authority` document carries `schema` and `authority` and nothing else."""
    document = V3 + "mode: observe\nauthority:\n  grants: []\n"

    with pytest.raises(PolicyError) as refused:
        Authority.from_yaml(document, source="authority.yaml", standalone=True)

    assert "authority.yaml" in str(refused.value)
    assert "mode" in str(refused.value)


@pytest.mark.parametrize("value", ["true", '"off"', "0", '"Observe"', '"enforced"', "null", "[]"])
def test_T84_mode_must_be_exactly_observe_or_enforce(value):
    with pytest.raises(PolicyError) as refused:
        Policy.from_yaml(f"{V3}mode: {value}\n{ACTIONS}", source="policy.yaml")

    assert "observe" in str(refused.value) and "enforce" in str(refused.value)


@pytest.mark.parametrize("mode", [OBSERVE, ENFORCE])
def test_T84_both_accepted_values_load(mode):
    assert Policy.from_yaml(f"{V3}mode: {mode}\n{ACTIONS}").mode == mode


def test_T84_mode_needs_schema_v3():
    """§12.1 — a reader that ignored `mode: observe` would enforce a configuration that was
    deployed to observe."""
    with pytest.raises(PolicyError) as refused:
        Policy.from_yaml("schema: ctrlrun.policy/v2\nmode: observe\n" + ACTIONS)

    assert "ctrlrun.policy/v3" in str(refused.value)


def test_T84_a_mistyped_mode_key_is_caught_by_the_closed_top_level_key_set():
    with pytest.raises(PolicyError, match="Mode"):
        Policy.from_yaml(f"{V3}Mode: observe\n{ACTIONS}")


# --- T85: the banner, and `ctrlrun verify` (§6.5) --------------------------------------

#: §6.5's two columns, by name. v0.3 moves no command across the line.
LOADS_THE_POLICY = ("gateway", "stats", "delegate", "revoke", "verify")
DOES_NOT = (
    "init",
    # `ctrlrun demo` builds an `authority:` section of its own for scenario 5 (SPEC-v0.3
    # §1.2), so it may legitimately append the §7 event types. Marked here rather than on the
    # whole test, so T66's guard still covers the other seven commands: a leak from
    # `receipts` or `inspect` must still fail.
    pytest.param("demo", marks=pytest.mark.authority),
    "approve",
    "deny",
    "receipts",
    "effects",
    "resolve",
    "inspect",
)

#: Enough arguments for each command to reach its body. Several exit non-zero against an
#: empty store, which is the point: the banner is printed before anything else.
#: Keyed by command name; `DOES_NOT` carries a `pytest.param` for one of them, so the ids the
#: parametrization produces are the plain names either way.
ARGUMENTS = {
    "gateway": ("--upstream", "http://127.0.0.1:1/mcp", "--alias", "acme", "--principal", "bot"),
    "stats": (),
    "delegate": ("--parent", "head-of-support", "--file", "grant.yaml", "--as", "bot"),
    "revoke": ("dlg_00000000000000000000000000000000",),
    "verify": (),
    "init": (),
    "demo": (),
    "approve": ("apr_0",),
    "deny": ("apr_0",),
    "receipts": (),
    "effects": (),
    "resolve": ("k", "--committed"),
    "inspect": ("act_0",),
}


@pytest.fixture
def cli(tmp_path, monkeypatch):
    """A working directory, and a `serve()` that records instead of listening."""
    import ctrlrun.gateway as gateway_module

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CTRLRUN_STATE", str(tmp_path / "state.db"))
    monkeypatch.setattr(gateway_module, "serve", lambda **kwargs: None)
    (tmp_path / "grant.yaml").write_text('subject: { agent: "bot" }\nactions: ["customer.read"]\n')
    return tmp_path


def _write_policy(directory, mode):
    (directory / "ctrlrun.yaml").write_text(_document(mode))


def _run(*arguments):
    return CliRunner().invoke(main, list(arguments))


@pytest.fixture
def loads(monkeypatch):
    """Records every `Policy.from_file`, so "does this command load the policy" is answered
    by the call and not by string-matching output a command happened not to print."""
    calls: list[str] = []
    original = Policy.from_file

    def recording(*args, **kwargs):
        calls.append("loaded")
        return original(*args, **kwargs)

    monkeypatch.setattr(Policy, "from_file", recording)
    return calls


@pytest.mark.parametrize("command", LOADS_THE_POLICY)
def test_T85_every_command_that_loads_the_policy_prints_the_banner(cli, command, loads):
    _write_policy(cli, OBSERVE)
    observed = _run(command, *ARGUMENTS[command])

    _write_policy(cli, ENFORCE)
    enforced = _run(command, *ARGUMENTS[command])

    assert loads, f"{command} never loaded the policy at all"
    assert OBSERVE_BANNER in observed.stderr, observed.output
    assert OBSERVE_BANNER not in observed.stdout
    assert OBSERVE_BANNER not in enforced.output


@pytest.mark.parametrize("command", DOES_NOT)
def test_T85_no_other_command_prints_the_banner_or_loads_the_policy(cli, command, loads):
    """The right-hand column is not an oversight: reading evidence must not depend on the
    configuration that produced it, so these work with no policy file at all. Asserted on the
    load itself — the test above proves the recorder sees one when it happens."""
    _write_policy(cli, OBSERVE)
    with_policy = _run(command, *ARGUMENTS[command])

    (cli / "ctrlrun.yaml").unlink()
    without = _run(command, *ARGUMENTS[command])

    assert loads == [], f"{command} loaded the operator's policy"
    assert OBSERVE_BANNER not in with_policy.output
    assert OBSERVE_BANNER not in without.output


def test_T85_the_banner_keeps_json_stdout_parseable(cli):
    _write_policy(cli, OBSERVE)

    result = _run("stats", "--json")

    assert OBSERVE_BANNER in result.stderr
    assert json.loads(result.stdout)["schema"] == STATS_SCHEMA


def test_T85_verify_exits_2_under_observe_mode_naming_the_mode(cli):
    """Amended by SPEC-v0.4 §9.4, item 2, in the commit that made `ctrlrun verify` real.

    The frozen sentence was *"`ctrlrun verify` exits 2 in both modes with a message naming
    v0.4"*, and its subject was explicitly temporary. It now exits 2 under `mode: observe`
    only, with a message naming the mode: observe mode enforces nothing, so every refusal the
    guarantees assert would be recorded rather than made, and there is nothing to verify
    (SPEC-v0.4 §3.8). The banner assertions for every other command are untouched.
    """
    _write_policy(cli, OBSERVE)

    result = _run("verify")

    assert result.exit_code == 2
    assert OBSERVE_BANNER in result.stderr
    assert "observe" in result.stderr
    assert result.stdout == ""


def test_T85_verify_runs_under_enforce_mode(cli):
    """The other half of the same amendment: under `mode: enforce` it runs, and reports."""
    _write_policy(cli, ENFORCE)

    result = _run("verify")

    assert result.exit_code == 0, result.output
    assert OBSERVE_BANNER not in result.output
    assert "declared guarantees pass" in result.stdout


def test_T85_a_policy_that_cannot_be_loaded_exits_non_zero_without_a_banner(cli):
    """§6.5 — the banner is not printed, because there is no mode to report."""
    (cli / "ctrlrun.yaml").write_text("schema: nope\n")

    result = _run("stats")

    assert result.exit_code != 0
    assert OBSERVE_BANNER not in result.output


# --- T86: `ctrlrun stats` (§6.4) -------------------------------------------------------


def _seed(store, clock, *, observe=True):
    """Receipts with known shapes, one per line of §6.4's report."""
    control = Control(
        Policy.from_yaml(_document(OBSERVE if observe else ENFORCE)),
        store,
        clock=clock,
        environment="production",
    )
    executor = _Executor()

    def run(action, effect_key, refusal):
        if observe:
            control.execute(action, executor, effect_key)
            return
        with pytest.raises(refusal):
            control.execute(action, executor, effect_key)

    control.execute(_action(arguments={"amount": 10, "payment_id": "A"}), executor, "k:A")
    for suffix in ("B", "C"):
        run(
            _action(arguments={"amount": 90000000, "payment_id": suffix}),
            f"k:{suffix}",
            ActionDenied,
        )
    run(
        _action(name="stripe.chargeback", arguments={"amount": 1, "payment_id": "D"}),
        "k:D",
        ActionDenied,
    )
    run(_action(arguments={"amount": 100000, "payment_id": "E"}), "k:E", ApprovalRequired)
    run(_action(arguments={"amount": 10, "payment_id": "A"}), "k:A", DuplicateEffect)
    with pytest.raises(TimeoutError):
        control.execute(_action(arguments={"amount": 10, "payment_id": "F"}), _explode, "k:F")


@pytest.fixture
def seeded(cli, clock):
    _write_policy(cli, OBSERVE)
    store = SQLiteStateStore(cli / "state.db", clock=clock)
    _seed(store, clock)
    store.close()
    return cli


def test_T86_stats_counts_what_observe_mode_recorded(seeded):
    result = _run("stats")
    document = json.loads(_run("stats", "--json").stdout)

    assert result.exit_code == 0, result.output
    assert document["mode"] == "observe"
    assert document["actions"] == 7
    assert document["would_have_been_denied"] == 3
    assert document["denied_by_reason"]["unknown_action"] == 1
    assert sum(document["denied_by_reason"].values()) == 3
    assert document["would_have_needed_approval"] == 1
    assert document["would_have_been_blocked"] == 1
    assert document["blocked_by_reason"] == {"duplicate": 1}
    assert document["ambiguous_outcomes"] == 1
    assert "would have been denied" in result.stdout
    assert "unknown_action" in result.stdout
    assert "(observe mode)" in result.stdout


def test_T86_the_json_matches_the_human_output_number_for_number(seeded):
    human = _run("stats").stdout
    document = json.loads(_run("stats", "--json").stdout)

    for key in (
        "actions",
        "would_have_been_denied",
        "would_have_needed_approval",
        "would_have_been_blocked",
        "ambiguous_outcomes",
    ):
        assert str(document[key]) in human, key
    for reason, count in document["denied_by_reason"].items():
        assert reason in human
        assert str(count) in human


def test_T86_enforce_mode_reports_less_and_says_so(cli, clock):
    _write_policy(cli, ENFORCE)
    store = SQLiteStateStore(cli / "state.db", clock=clock)
    _seed(store, clock, observe=False)
    store.close()

    result = _run("stats")
    document = json.loads(_run("stats", "--json").stdout)

    assert document["mode"] == "enforce"
    assert "would_have_been_blocked" not in document
    assert "blocked_by_reason" not in document
    assert document["denied"] == 3
    assert document["ambiguous_outcomes"] == 1
    assert "would have been" not in result.stdout
    assert "blocked_reason" in result.stdout


@pytest.mark.parametrize("since", ["24h", "7d", "30m", "2026-09-03T00:00:00+00:00"])
def test_T86_an_accepted_since_window_parses(seeded, since):
    assert _run("stats", "--since", since).exit_code == 0, since


@pytest.mark.parametrize(
    "since",
    # `2026-09-03T00:00:00` parses perfectly well and carries no offset. Comparing it with a
    # timezone-aware `finished_at` raises, so §6.4 requires one and this asserts the refusal.
    ["1w", "2mo", "5", "yesterday", "24 h", "-1h", "0h", "", "2026-09-03T00:00:00"],
)
def test_T86_an_unaccepted_since_exits_non_zero_naming_the_forms(seeded, since):
    result = _run("stats", "--since", since)

    assert result.exit_code != 0, since
    assert "ISO-8601" in result.output
    assert "30m" in result.output


def test_T86_since_filters_on_finished_at_and_the_boundary_is_included(seeded):
    store = SQLiteStateStore(seeded / "state.db")
    boundary = max(receipt.finished_at for receipt in store.receipts())
    store.close()

    everything = json.loads(_run("stats", "--json").stdout)
    on_it = json.loads(_run("stats", "--since", boundary.isoformat(), "--json").stdout)
    after = json.loads(
        _run("stats", "--since", (boundary + timedelta(seconds=1)).isoformat(), "--json").stdout
    )

    assert everything["actions"] == 7
    assert on_it["actions"] == 7
    assert after["actions"] == 0


def test_T86_an_empty_store_prints_zeros_and_exits_zero(cli):
    _write_policy(cli, OBSERVE)

    result = _run("stats")
    document = json.loads(_run("stats", "--json").stdout)

    assert result.exit_code == 0, result.output
    assert document["actions"] == 0
    assert "0" in result.stdout


def test_T86_stats_says_that_actions_awaiting_a_human_are_not_counted(seeded):
    assert "awaiting a human" in _run("stats").stdout


def test_T86_stats_reaches_no_network(seeded, monkeypatch):
    """§6.4 — from the local store and nothing else. A claim about the environment is a claim
    until something takes the network away."""

    def refuse(*args, **kwargs):
        raise AssertionError("ctrlrun stats opened a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)

    result = _run("stats")

    assert result.exit_code == 0, result.output


# --- T87: observe → enforce needs no migration (§6.6) ----------------------------------


@pytest.mark.authority
def test_T87_a_store_written_under_observe_is_read_by_an_enforcing_control(tmp_path, clock):
    """One store, one file, one edited line. Every table opens, the effect committed under
    observe is refused as a duplicate under enforce, and the delegation still evaluates."""
    policy = tmp_path / "ctrlrun.yaml"
    store = SQLiteStateStore(tmp_path / "state.db", clock=clock)

    def control_for(mode):
        policy.write_text(_document(mode, authority=DELEGABLE))
        text = policy.read_text()
        return Control(
            Policy.from_yaml(text),
            store,
            authority=Authority.from_yaml(text),
            clock=clock,
            environment="production",
        )

    observing = control_for(OBSERVE)
    delegated = observing.delegate(
        "head-of-support",
        grant_from_yaml(
            """
subject: { agent: "support-agent", user: "alice@example.com" }
actions: ["stripe.refund"]
constraints: { amount_lte: 1000 }
expires_at: "2026-12-01T00:00:00Z"
"""
        ),
        by=Principal(agent="support-agent", user="alice@example.com"),
    )
    observing.execute(_action(), _Executor(), "refund:EU-42")

    enforcing = control_for(ENFORCE)

    assert store.get_delegation(delegated.delegation_id) is not None
    assert enforcing.evaluate(_action()).decision.value == "allow"
    assert store.receipts()[0].result is ReceiptResult.OBSERVED
    with pytest.raises(DuplicateEffect):
        enforcing.execute(_action(), _Executor(), "refund:EU-42")
    store.close()


@pytest.mark.authority
def test_T87_a_delegation_created_under_observe_is_enforced_after_the_switch(tmp_path, clock):
    """The other half: the record survives, and it also still *binds*. A delegated grant
    capped at 1000 refuses 2000 the moment the switch is thrown."""
    policy = tmp_path / "ctrlrun.yaml"
    store = SQLiteStateStore(tmp_path / "state.db", clock=clock)

    def control_for(mode):
        policy.write_text(_document(mode, authority=DELEGABLE))
        text = policy.read_text()
        return Control(
            Policy.from_yaml(text),
            store,
            authority=Authority.from_yaml(text),
            clock=clock,
            environment="production",
        )

    control_for(OBSERVE).delegate(
        "head-of-support",
        grant_from_yaml(
            """
subject: { agent: "junior-agent", user: "alice@example.com" }
actions: ["stripe.refund"]
constraints: { amount_lte: 1000 }
expires_at: "2026-12-01T00:00:00Z"
"""
        ),
        by=Principal(agent="support-agent", user="alice@example.com"),
    )
    junior = Principal(agent="junior-agent", user="alice@example.com")
    enforcing = control_for(ENFORCE)

    assert enforcing.evaluate(_action(principal=junior)).decision.value == "allow"
    assert (
        enforcing.evaluate(
            _action(principal=junior, arguments={"amount": 2000, "payment_id": "EU-42"})
        ).decision.value
        == "deny"
    )
    store.close()
