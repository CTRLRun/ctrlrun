"""Approval binding: request, grant, consume. SPEC-v0.1 §4; acceptance tests T2, T4, T5."""

import json
import threading
from datetime import UTC, datetime, timedelta

import pytest

import ctrlrun
from ctrlrun import (
    Action,
    ActionDenied,
    Approval,
    ApprovalMismatch,
    ApprovalProvider,
    ApprovalRequest,
    ApprovalRequired,
    ApprovalTimeout,
    Control,
    Decision,
    InMemoryStateStore,
    InvalidArgument,
    LocalApprovalProvider,
    Policy,
    Principal,
    ScriptedApprovalProvider,
    context,
    protect,
    with_approval,
)
from ctrlrun.approval import DEFAULT_APPROVAL_TTL, ApprovalStatus, ScriptedOutcome
from ctrlrun.receipt import EventType, ReceiptResult

POLICY = """
schema: ctrlrun.policy/v1
actions:
  customer.read:
    decision: allow
  iam.grant_admin:
    decision: deny
  stripe.refund:
    rules:
      - when: { amount_lte: 500 }
        decision: allow
      - when: { amount_lte: 5000 }
        decision: approve
      - decision: deny
"""


class FakeClock:
    """A clock that only moves when a test moves it, so expiry is exact."""

    def __init__(self) -> None:
        self.now = datetime(2026, 9, 3, 10, 12, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def store(clock):
    return InMemoryStateStore(clock=clock)


@pytest.fixture
def approvals(store, clock):
    return LocalApprovalProvider(store, clock=clock, poll_interval=timedelta(0))


@pytest.fixture
def control(store, approvals, clock):
    return Control(Policy.from_yaml(POLICY), store, approvals, clock=clock)


def _event_types(store):
    return [event.type for event in store.events()]


def _refund(control, calls):
    @protect("stripe.refund", control=control)
    def refund(payment_id: str, amount: int) -> str:
        calls.append((payment_id, amount))
        return f"re_{payment_id}"

    return refund


def _request_approval(refund, **arguments) -> str:
    """Propose an action that needs approval and return the request id it produced."""
    with pytest.raises(ApprovalRequired) as pending:
        refund(**arguments)
    return pending.value.request_id


# --- T2: approval mutation blocked (SPEC §4.2 A1) -------------------------------------


def test_T2_a_mutated_action_presenting_the_approval_raises_ApprovalMismatch(control, store):
    calls: list[tuple[str, int]] = []
    refund = _refund(control, calls)

    with context(agent="refund-agent"):
        request_id = _request_approval(refund, payment_id="txn_1", amount=2000)
        store.grant_approval(request_id, "cli:local")
        with with_approval(request_id), pytest.raises(ApprovalMismatch) as excinfo:
            refund(payment_id="txn_1", amount=5000)

    assert excinfo.value.reason == "mismatch"
    assert excinfo.value.approval_id == request_id


def test_T2_a_mutated_action_does_not_execute(control, store):
    calls: list[tuple[str, int]] = []
    refund = _refund(control, calls)

    with context(agent="refund-agent"):
        request_id = _request_approval(refund, payment_id="txn_1", amount=2000)
        store.grant_approval(request_id, "cli:local")
        with with_approval(request_id), pytest.raises(ApprovalMismatch):
            refund(payment_id="txn_1", amount=5000)

    assert calls == []


def test_T2_a_mutated_action_leaves_the_approval_granted(control, store):
    refund = _refund(control, [])

    with context(agent="refund-agent"):
        request_id = _request_approval(refund, payment_id="txn_1", amount=2000)
        store.grant_approval(request_id, "cli:local")
        with with_approval(request_id), pytest.raises(ApprovalMismatch):
            refund(payment_id="txn_1", amount=5000)

    record = store.get_approval(request_id)
    assert record.status is ApprovalStatus.GRANTED
    assert record.consumed_at is None


def test_T2_a_mutated_action_appends_APPROVAL_INVALIDATED(control, store):
    refund = _refund(control, [])

    with context(agent="refund-agent"):
        request_id = _request_approval(refund, payment_id="txn_1", amount=2000)
        store.grant_approval(request_id, "cli:local")
        with with_approval(request_id), pytest.raises(ApprovalMismatch):
            refund(payment_id="txn_1", amount=5000)

    assert _event_types(store) == [
        EventType.ACTION_PROPOSED,
        EventType.POLICY_EVALUATED,
        EventType.APPROVAL_REQUESTED,
        EventType.ACTION_PROPOSED,
        EventType.POLICY_EVALUATED,
        EventType.APPROVAL_INVALIDATED,
    ]
    invalidated = store.events()[-1]
    assert invalidated.approval_id == request_id
    assert invalidated.data["reason"] == "mismatch"


def test_T2_a_mutated_action_writes_a_blocked_receipt(control, store):
    refund = _refund(control, [])

    with context(agent="refund-agent"):
        request_id = _request_approval(refund, payment_id="txn_1", amount=2000)
        store.grant_approval(request_id, "cli:local")
        with with_approval(request_id), pytest.raises(ApprovalMismatch):
            refund(payment_id="txn_1", amount=5000)

    (receipt,) = store.receipts()
    assert receipt.result is ReceiptResult.BLOCKED
    assert receipt.decision is Decision.APPROVE
    assert receipt.approval_id == request_id
    assert receipt.arguments == {"payment_id": "txn_1", "amount": 5000}


def test_T2_any_material_change_invalidates_the_approval(control, store):
    """The binding is to the action hash, so a changed principal voids it too (§4.2 A1)."""
    refund = _refund(control, [])

    with context(agent="refund-agent"):
        request_id = _request_approval(refund, payment_id="txn_1", amount=2000)
        store.grant_approval(request_id, "cli:local")
    with (
        context(agent="other-agent"),
        with_approval(request_id),
        pytest.raises(ApprovalMismatch) as excinfo,
    ):
        refund(payment_id="txn_1", amount=2000)

    assert excinfo.value.reason == "mismatch"


# --- T4: approval replay blocked (SPEC §4.2 A2) ---------------------------------------


def test_T4_the_first_presentation_of_an_approval_commits(control, store):
    calls: list[tuple[str, int]] = []
    refund = _refund(control, calls)

    with context(agent="refund-agent"):
        request_id = _request_approval(refund, payment_id="txn_1", amount=2000)
        store.grant_approval(request_id, "cli:local")
        with with_approval(request_id):
            assert refund(payment_id="txn_1", amount=2000) == "re_txn_1"

    assert calls == [("txn_1", 2000)]
    (receipt,) = store.receipts()
    assert receipt.result is ReceiptResult.COMMITTED
    assert receipt.approval_id == request_id
    assert receipt.approver == "cli:local"


def test_T4_replaying_the_approval_raises_ApprovalMismatch_with_reason_consumed(control, store):
    calls: list[tuple[str, int]] = []
    refund = _refund(control, calls)

    with context(agent="refund-agent"):
        request_id = _request_approval(refund, payment_id="txn_1", amount=2000)
        store.grant_approval(request_id, "cli:local")
        with with_approval(request_id):
            refund(payment_id="txn_1", amount=2000)
        with with_approval(request_id), pytest.raises(ApprovalMismatch) as excinfo:
            refund(payment_id="txn_1", amount=2000)

    assert excinfo.value.reason == "consumed"
    assert calls == [("txn_1", 2000)]


def test_T4_a_consumed_approval_stays_consumed_and_the_replay_is_blocked(control, store):
    refund = _refund(control, [])

    with context(agent="refund-agent"):
        request_id = _request_approval(refund, payment_id="txn_1", amount=2000)
        store.grant_approval(request_id, "cli:local")
        with with_approval(request_id):
            refund(payment_id="txn_1", amount=2000)
        with with_approval(request_id), pytest.raises(ApprovalMismatch):
            refund(payment_id="txn_1", amount=2000)

    assert store.get_approval(request_id).status is ApprovalStatus.CONSUMED
    committed, blocked = store.receipts()
    assert committed.result is ReceiptResult.COMMITTED
    assert blocked.result is ReceiptResult.BLOCKED


def test_T4_consumption_is_atomic_under_concurrency(store, clock):
    """A2: at most one caller may take a granted approval, whatever the interleaving."""
    action = Action(
        name="stripe.refund",
        arguments={"payment_id": "txn_1", "amount": 2000},
        principal=Principal(agent="refund-agent"),
    )
    request = ApprovalRequest(
        request_id="apr_000000000001",
        action_hash=action.action_hash,
        action=action,
        created_at=clock(),
        expires_at=clock() + DEFAULT_APPROVAL_TTL,
    )
    store.put_approval_request(request)
    store.grant_approval(request.request_id, "cli:local")

    workers = 16
    barrier = threading.Barrier(workers)
    granted: list[Approval] = []
    refused: list[ApprovalMismatch] = []
    lock = threading.Lock()

    def consume() -> None:
        barrier.wait()
        try:
            approval = store.consume_approval(request.request_id, action.action_hash)
        except ApprovalMismatch as exc:
            with lock:
                refused.append(exc)
        else:
            with lock:
                granted.append(approval)

    threads = [threading.Thread(target=consume) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(granted) == 1
    assert len(refused) == workers - 1
    assert {exc.reason for exc in refused} == {"consumed"}


# --- T5: approval expiry (SPEC §4.2 A3) -----------------------------------------------


def test_T5_an_expired_approval_raises_ApprovalMismatch_with_reason_expired(control, store, clock):
    calls: list[tuple[str, int]] = []
    refund = _refund(control, calls)

    with context(agent="refund-agent"):
        request_id = _request_approval(refund, payment_id="txn_1", amount=2000)
        store.grant_approval(request_id, "cli:local")
        clock.advance(DEFAULT_APPROVAL_TTL + timedelta(seconds=1))
        with with_approval(request_id), pytest.raises(ApprovalMismatch) as excinfo:
            refund(payment_id="txn_1", amount=2000)

    assert excinfo.value.reason == "expired"
    assert calls == []


def test_T5_expiry_is_checked_at_consumption_not_only_at_grant(control, store, clock):
    """A3: the grant was valid when it was made; time passing is what invalidates it."""
    refund = _refund(control, [])

    with context(agent="refund-agent"):
        request_id = _request_approval(refund, payment_id="txn_1", amount=2000)
        store.grant_approval(request_id, "cli:local")
        assert store.get_approval(request_id).status is ApprovalStatus.GRANTED
        clock.advance(DEFAULT_APPROVAL_TTL + timedelta(seconds=1))
        with with_approval(request_id), pytest.raises(ApprovalMismatch):
            refund(payment_id="txn_1", amount=2000)

    assert store.get_approval(request_id).status is ApprovalStatus.EXPIRED


def test_T5_an_expired_approval_appends_APPROVAL_EXPIRED_and_APPROVAL_INVALIDATED(
    control, store, clock
):
    refund = _refund(control, [])

    with context(agent="refund-agent"):
        request_id = _request_approval(refund, payment_id="txn_1", amount=2000)
        store.grant_approval(request_id, "cli:local")
        clock.advance(DEFAULT_APPROVAL_TTL + timedelta(seconds=1))
        with with_approval(request_id), pytest.raises(ApprovalMismatch):
            refund(payment_id="txn_1", amount=2000)

    assert _event_types(store)[-2:] == [
        EventType.APPROVAL_EXPIRED,
        EventType.APPROVAL_INVALIDATED,
    ]
    (receipt,) = store.receipts()
    assert receipt.result is ReceiptResult.BLOCKED


def test_an_approval_that_expires_before_it_is_granted_cannot_be_granted(store, clock):
    action = Action(
        name="stripe.refund", arguments={"amount": 2000}, principal=Principal(agent="a")
    )
    request = ApprovalRequest(
        request_id="apr_00000000000e",
        action_hash=action.action_hash,
        action=action,
        created_at=clock(),
        expires_at=clock() + timedelta(minutes=1),
    )
    store.put_approval_request(request)
    clock.advance(timedelta(minutes=2))

    with pytest.raises(ApprovalMismatch) as excinfo:
        store.grant_approval(request.request_id, "cli:local")

    assert excinfo.value.reason == "expired"
    assert store.get_approval(request.request_id).status is ApprovalStatus.EXPIRED


# --- wait=False: the agent gets a request id back (SPEC §4.3) -------------------------


def test_wait_false_raises_ApprovalRequired_carrying_the_request_id(control, store):
    calls: list[tuple[str, int]] = []
    refund = _refund(control, calls)

    with context(agent="refund-agent"), pytest.raises(ApprovalRequired) as excinfo:
        refund(payment_id="txn_1", amount=2000)

    request_id = excinfo.value.request_id
    assert request_id.startswith("apr_")
    assert len(request_id) == len("apr_") + 12
    assert calls == []


def test_wait_false_records_the_request_in_the_store_as_pending(control, store):
    refund = _refund(control, [])

    with context(agent="refund-agent"), pytest.raises(ApprovalRequired) as excinfo:
        refund(payment_id="txn_1", amount=2000)

    record = store.get_approval(excinfo.value.request_id)
    assert record.status is ApprovalStatus.PENDING
    assert record.request.action.arguments["amount"] == 2000


def test_wait_false_binds_the_request_to_the_action_hash(control, store):
    refund = _refund(control, [])

    with context(agent="refund-agent"), pytest.raises(ApprovalRequired) as excinfo:
        refund(payment_id="txn_1", amount=2000)

    record = store.get_approval(excinfo.value.request_id)
    assert record.action_hash == record.request.action.action_hash
    assert record.action_hash.startswith("sha256:")


def test_wait_false_appends_APPROVAL_REQUESTED_and_writes_no_receipt(control, store):
    refund = _refund(control, [])

    with context(agent="refund-agent"), pytest.raises(ApprovalRequired) as excinfo:
        refund(payment_id="txn_1", amount=2000)

    assert _event_types(store) == [
        EventType.ACTION_PROPOSED,
        EventType.POLICY_EVALUATED,
        EventType.APPROVAL_REQUESTED,
    ]
    assert store.events()[-1].approval_id == excinfo.value.request_id
    # The action is suspended awaiting a human, not terminal: no receipt yet (SPEC §6.1).
    assert store.receipts() == ()


def test_ApprovalRequired_carries_the_action_id(control, store):
    refund = _refund(control, [])

    with context(agent="refund-agent"), pytest.raises(ApprovalRequired) as excinfo:
        refund(payment_id="txn_1", amount=2000)

    assert excinfo.value.action_id == store.events()[0].action_id


def test_the_default_request_ttl_is_fifteen_minutes(control, store, clock):
    refund = _refund(control, [])

    with context(agent="refund-agent"), pytest.raises(ApprovalRequired) as excinfo:
        refund(payment_id="txn_1", amount=2000)

    record = store.get_approval(excinfo.value.request_id)
    assert record.request.expires_at - record.request.created_at == timedelta(minutes=15)


# --- wait=True: the decorator blocks on the provider (SPEC §4.3) ----------------------


def test_wait_true_blocks_until_the_provider_grants(store, clock):
    provider = ScriptedApprovalProvider(store, ["pending", "pending", "grant"], clock=clock)
    control = Control(Policy.from_yaml(POLICY), store, provider, clock=clock)
    calls: list[tuple[str, int]] = []

    @protect("stripe.refund", wait=True, control=control)
    def refund(payment_id: str, amount: int) -> str:
        calls.append((payment_id, amount))
        return f"re_{payment_id}"

    with context(agent="refund-agent"):
        assert refund(payment_id="txn_1", amount=2000) == "re_txn_1"

    assert calls == [("txn_1", 2000)]
    assert provider.polls == 3


def test_wait_true_consumes_the_approval_it_waited_for(store, clock):
    provider = ScriptedApprovalProvider(store, ["grant"], approver="cli:scripted", clock=clock)
    control = Control(Policy.from_yaml(POLICY), store, provider, clock=clock)

    @protect("stripe.refund", wait=True, control=control)
    def refund(payment_id: str, amount: int) -> str:
        return "re_1"

    with context(agent="refund-agent"):
        refund(payment_id="txn_1", amount=2000)

    (receipt,) = store.receipts()
    assert receipt.result is ReceiptResult.COMMITTED
    assert receipt.approver == "cli:scripted"
    assert store.get_approval(receipt.approval_id).status is ApprovalStatus.CONSUMED
    assert _event_types(store) == [
        EventType.ACTION_PROPOSED,
        EventType.POLICY_EVALUATED,
        EventType.APPROVAL_REQUESTED,
        EventType.ACTION_PROPOSED,
        EventType.POLICY_EVALUATED,
        EventType.APPROVAL_CONSUMED,
        EventType.EXECUTION_STARTED,
        EventType.EXECUTION_COMMITTED,
    ]


def test_a_scripted_grant_arriving_after_the_request_expired_is_a_timeout(store, clock):
    """§4.3 — the request's expiry bounds every provider's wait, script or not."""
    provider = ScriptedApprovalProvider(store, ["pending", "grant"], clock=clock)
    action = Action(
        name="stripe.refund", arguments={"amount": 2000}, principal=Principal(agent="a")
    )
    request = provider.request(action, DEFAULT_APPROVAL_TTL)
    clock.advance(DEFAULT_APPROVAL_TTL + timedelta(seconds=1))

    with pytest.raises(ApprovalTimeout):
        provider.wait(request.request_id, None)

    assert store.get_approval(request.request_id).status is ApprovalStatus.PENDING
    assert provider.polls == 0


def test_wait_true_raises_ApprovalTimeout_when_nobody_answers(store, clock):
    provider = ScriptedApprovalProvider(store, ["pending"], clock=clock)
    control = Control(Policy.from_yaml(POLICY), store, provider, clock=clock)
    calls: list[int] = []

    @protect("stripe.refund", wait=True, control=control)
    def refund(payment_id: str, amount: int) -> None:
        calls.append(amount)

    with context(agent="refund-agent"), pytest.raises(ApprovalTimeout):
        refund(payment_id="txn_1", amount=2000)

    assert calls == []


def test_a_local_provider_stops_waiting_when_the_request_expires(store, clock):
    class ExpiringClock:
        """A minute per call, and a hard stop: a provider that ignores expiry must not hang."""

        def __init__(self) -> None:
            self.calls = 0

        def __call__(self) -> datetime:
            self.calls += 1
            if self.calls > 20:
                raise AssertionError("wait() did not stop at the request's expires_at")
            return datetime(2026, 9, 3, 10, 12, 1, tzinfo=UTC) + timedelta(minutes=self.calls)

    provider = LocalApprovalProvider(store, clock=ExpiringClock(), poll_interval=timedelta(0))
    action = Action(
        name="stripe.refund", arguments={"amount": 2000}, principal=Principal(agent="a")
    )
    request = provider.request(action, timedelta(minutes=3))

    with pytest.raises(ApprovalTimeout) as excinfo:
        provider.wait(request.request_id, None)

    assert excinfo.value.request_id == request.request_id


def test_a_local_provider_returns_the_approval_once_it_is_granted(store, clock, approvals):
    action = Action(
        name="stripe.refund", arguments={"amount": 2000}, principal=Principal(agent="a")
    )
    request = approvals.request(action, DEFAULT_APPROVAL_TTL)
    store.grant_approval(request.request_id, "cli:local")

    approval = approvals.wait(request.request_id, None)

    assert approval.approval_id == request.request_id
    assert approval.action_hash == action.action_hash
    assert approval.approver == "cli:local"


def test_a_local_provider_returns_none_once_the_request_is_denied(store, approvals):
    action = Action(
        name="stripe.refund", arguments={"amount": 2000}, principal=Principal(agent="a")
    )
    request = approvals.request(action, DEFAULT_APPROVAL_TTL)
    store.deny_approval(request.request_id, "cli:local")

    assert approvals.wait(request.request_id, None) is None


def test_waiting_on_an_unknown_request_is_a_mismatch(approvals):
    with pytest.raises(ApprovalMismatch) as excinfo:
        approvals.wait("apr_00000000dead", None)

    assert excinfo.value.reason == "unknown"


# --- denial (SPEC §4.3, §6.2) ---------------------------------------------------------


def test_a_denied_approval_raises_ActionDenied(control, store):
    calls: list[int] = []
    refund = _refund(control, calls)

    with context(agent="refund-agent"):
        request_id = _request_approval(refund, payment_id="txn_1", amount=2000)
        store.deny_approval(request_id, "cli:local")
        with with_approval(request_id), pytest.raises(ActionDenied) as excinfo:
            refund(payment_id="txn_1", amount=2000)

    assert excinfo.value.reason == "approval_denied"
    assert calls == []


def test_a_denied_approval_writes_a_denied_receipt_and_the_denial_events(control, store):
    refund = _refund(control, [])

    with context(agent="refund-agent"):
        request_id = _request_approval(refund, payment_id="txn_1", amount=2000)
        store.deny_approval(request_id, "cli:local")
        with with_approval(request_id), pytest.raises(ActionDenied):
            refund(payment_id="txn_1", amount=2000)

    assert _event_types(store)[-2:] == [EventType.APPROVAL_DENIED, EventType.ACTION_DENIED]
    (receipt,) = store.receipts()
    assert receipt.result is ReceiptResult.DENIED
    assert receipt.decision is Decision.APPROVE
    assert receipt.approval_id == request_id
    assert receipt.approver == "cli:local"


def test_wait_true_turns_a_scripted_denial_into_ActionDenied(store, clock):
    provider = ScriptedApprovalProvider(store, ["deny"], clock=clock)
    control = Control(Policy.from_yaml(POLICY), store, provider, clock=clock)
    calls: list[int] = []

    @protect("stripe.refund", wait=True, control=control)
    def refund(payment_id: str, amount: int) -> None:
        calls.append(amount)

    with context(agent="refund-agent"), pytest.raises(ActionDenied) as excinfo:
        refund(payment_id="txn_1", amount=2000)

    assert excinfo.value.reason == "approval_denied"
    assert calls == []


def test_a_denied_approval_cannot_later_be_granted(store, approvals):
    action = Action(
        name="stripe.refund", arguments={"amount": 2000}, principal=Principal(agent="a")
    )
    request = approvals.request(action, DEFAULT_APPROVAL_TTL)
    store.deny_approval(request.request_id, "cli:local")

    with pytest.raises(ApprovalMismatch) as excinfo:
        store.grant_approval(request.request_id, "cli:local")

    assert excinfo.value.reason == "denied"


# --- what may not be presented (SPEC §4.2, §3.4) --------------------------------------


def test_an_ungranted_approval_may_not_be_presented(control, store):
    calls: list[int] = []
    refund = _refund(control, calls)

    with context(agent="refund-agent"):
        request_id = _request_approval(refund, payment_id="txn_1", amount=2000)
        with with_approval(request_id), pytest.raises(ApprovalMismatch) as excinfo:
            refund(payment_id="txn_1", amount=2000)

    assert excinfo.value.reason == "pending"
    assert calls == []


def test_an_unknown_approval_id_may_not_be_presented(control, store):
    calls: list[int] = []
    refund = _refund(control, calls)

    with (
        context(agent="refund-agent"),
        with_approval("apr_00000000dead"),
        pytest.raises(ApprovalMismatch) as excinfo,
    ):
        refund(payment_id="txn_1", amount=2000)

    assert excinfo.value.reason == "unknown"
    assert calls == []
    (receipt,) = store.receipts()
    assert receipt.result is ReceiptResult.BLOCKED


def test_a_presented_approval_cannot_rescue_a_denied_action(control, store):
    """Policy DENY is not overridable by a human approval of some other action (§3.4)."""
    calls: list[str] = []

    @protect("iam.grant_admin", control=control)
    def grant(user: str) -> None:
        calls.append(user)

    with context(agent="refund-agent"):
        request_id = _request_approval(_refund(control, []), payment_id="txn_1", amount=2000)
        store.grant_approval(request_id, "cli:local")
        with with_approval(request_id), pytest.raises(ActionDenied) as excinfo:
            grant(user="mallory")

    assert excinfo.value.reason == "decision"
    assert calls == []
    assert store.get_approval(request_id).status is ApprovalStatus.GRANTED


def test_an_allowed_action_ignores_a_presented_approval(control, store):
    @protect("customer.read", control=control)
    def read(customer_id: str) -> str:
        return customer_id

    with context(agent="refund-agent"):
        request_id = _request_approval(_refund(control, []), payment_id="txn_1", amount=2000)
        store.grant_approval(request_id, "cli:local")
        with with_approval(request_id):
            assert read("cus_1") == "cus_1"

    assert store.get_approval(request_id).status is ApprovalStatus.GRANTED
    read_receipt = store.receipts()[-1]
    assert read_receipt.result is ReceiptResult.COMMITTED
    assert read_receipt.approval_id is None


# --- the provider protocol (SPEC §4.3, §8) --------------------------------------------


def test_both_shipped_providers_satisfy_the_ApprovalProvider_protocol(store):
    assert isinstance(LocalApprovalProvider(store), ApprovalProvider)
    assert isinstance(ScriptedApprovalProvider(store, []), ApprovalProvider)


def test_a_local_provider_polls_until_another_worker_grants(store, clock):
    """§4.3 — `wait()` polls the store, so a grant from another shell ends the wait."""
    action = Action(
        name="stripe.refund", arguments={"amount": 2000}, principal=Principal(agent="a")
    )
    provider = LocalApprovalProvider(store, clock=clock, poll_interval=timedelta(milliseconds=1))
    request = provider.request(action, DEFAULT_APPROVAL_TTL)
    approver = threading.Timer(0.02, store.grant_approval, (request.request_id, "cli:local"))
    approver.start()
    try:
        approval = provider.wait(request.request_id, None)
    finally:
        approver.join()

    assert approval is not None
    assert approval.action_hash == action.action_hash


def test_the_approval_names_frozen_in_spec_section_8_are_importable_from_the_package():
    frozen = {
        "Approval",
        "ApprovalRequest",
        "ApprovalProvider",
        "LocalApprovalProvider",
        "ScriptedApprovalProvider",
        "ApprovalRequired",
        "ApprovalTimeout",
        "ApprovalMismatch",
        "with_approval",
    }
    assert frozen <= set(ctrlrun.__all__)
    assert all(hasattr(ctrlrun, name) for name in frozen)


# --- evidence renders by value (SPEC §6.1) --------------------------------------------


def test_approval_events_and_receipts_render_by_value(control, store):
    refund = _refund(control, [])

    with context(agent="refund-agent"):
        request_id = _request_approval(refund, payment_id="txn_1", amount=2000)
        store.grant_approval(request_id, "cli:local")
        with with_approval(request_id), pytest.raises(ApprovalMismatch):
            refund(payment_id="txn_1", amount=5000)

    event = json.loads(store.events()[-1].to_json())
    assert event["type"] == "APPROVAL_INVALIDATED"
    assert event["approval_id"] == request_id
    assert event["data"]["reason"] == "mismatch"

    receipt = json.loads(store.receipts()[0].to_json())
    assert receipt["decision"] == "approve"
    assert receipt["result"] == "blocked"
    assert receipt["approval_id"] == request_id


# --- with_approval() (SPEC §8) --------------------------------------------------------


def test_with_approval_rejects_an_empty_request_id():
    with pytest.raises(InvalidArgument):
        with_approval("").__enter__()


def test_with_approval_is_restored_on_exit(control, store):
    refund = _refund(control, [])

    with context(agent="refund-agent"):
        request_id = _request_approval(refund, payment_id="txn_1", amount=2000)
        store.grant_approval(request_id, "cli:local")
        with with_approval(request_id):
            refund(payment_id="txn_1", amount=2000)
        # Outside the block the approval is no longer presented: a new request is made.
        with pytest.raises(ApprovalRequired) as excinfo:
            refund(payment_id="txn_1", amount=2000)

    assert excinfo.value.request_id != request_id


# --- models (SPEC §4.1) ---------------------------------------------------------------


def test_an_approval_request_rejects_a_naive_datetime(clock):
    action = Action(name="stripe.refund", arguments={}, principal=Principal(agent="a"))
    with pytest.raises(InvalidArgument):
        ApprovalRequest(
            request_id="apr_000000000002",
            action_hash=action.action_hash,
            action=action,
            created_at=datetime(2026, 9, 3, 10, 12, 1),  # naive: the point of the test
            expires_at=clock() + DEFAULT_APPROVAL_TTL,
        )


def test_an_approval_request_must_expire_after_it_was_created(clock):
    action = Action(name="stripe.refund", arguments={}, principal=Principal(agent="a"))
    with pytest.raises(InvalidArgument):
        ApprovalRequest(
            request_id="apr_000000000003",
            action_hash=action.action_hash,
            action=action,
            created_at=clock(),
            expires_at=clock(),
        )


def test_an_approval_request_needs_a_non_empty_action_hash(clock):
    action = Action(name="stripe.refund", arguments={}, principal=Principal(agent="a"))
    with pytest.raises(InvalidArgument):
        ApprovalRequest(
            request_id="apr_000000000004",
            action_hash="",
            action=action,
            created_at=clock(),
            expires_at=clock() + DEFAULT_APPROVAL_TTL,
        )


def test_an_approval_needs_a_non_empty_approver(clock):
    with pytest.raises(InvalidArgument):
        Approval(
            approval_id="apr_000000000005",
            action_hash="sha256:00",
            approver="",
            granted_at=clock(),
            expires_at=clock() + DEFAULT_APPROVAL_TTL,
        )


def test_the_approval_id_is_the_request_id(control, store):
    refund = _refund(control, [])

    with context(agent="refund-agent"):
        request_id = _request_approval(refund, payment_id="txn_1", amount=2000)
        approval = store.grant_approval(request_id, "cli:local")

    assert approval.approval_id == request_id


def test_a_provider_rejects_a_non_positive_ttl(approvals):
    action = Action(name="stripe.refund", arguments={}, principal=Principal(agent="a"))
    with pytest.raises(InvalidArgument):
        approvals.request(action, timedelta(0))


def test_the_scripted_provider_rejects_an_unknown_outcome(store):
    with pytest.raises(InvalidArgument):
        ScriptedApprovalProvider(store, ["maybe"])


def test_the_scripted_outcomes_render_by_value():
    assert str(ScriptedOutcome.GRANT) == "grant"


# --- the store (SPEC §5.3, approval half) ---------------------------------------------


def test_a_request_id_may_not_be_reused(store, approvals, clock):
    action = Action(name="stripe.refund", arguments={}, principal=Principal(agent="a"))
    request = approvals.request(action, DEFAULT_APPROVAL_TTL)

    with pytest.raises(InvalidArgument):
        store.put_approval_request(request)


def test_get_approval_returns_none_for_an_unknown_id(store):
    assert store.get_approval("apr_00000000beef") is None


def test_granting_an_unknown_approval_is_a_mismatch(store):
    with pytest.raises(ApprovalMismatch) as excinfo:
        store.grant_approval("apr_00000000beef", "cli:local")

    assert excinfo.value.reason == "unknown"


def test_denying_an_unknown_approval_is_a_mismatch(store):
    with pytest.raises(ApprovalMismatch) as excinfo:
        store.deny_approval("apr_00000000beef", "cli:local")

    assert excinfo.value.reason == "unknown"
