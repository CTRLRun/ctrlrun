"""The adapter surface. SPEC-v0.5 §2, §3, §4; T126-T129h.

Every test here that asserts a refusal counts the interrupt double's calls and asserts the
count, because a refusal is satisfied just as well by a run in which the adapter was never
reached at all -- and that run passes against a surface with the guard deleted (SPEC-v0.4 §1.3).
Where the count alone would not distinguish two guards, the test carries a **control**: the same
configuration with the guard's precondition removed, reaching the next refusal instead.
"""

from __future__ import annotations

import inspect
import json
import logging
import re
from dataclasses import fields, is_dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

import ctrlrun.adapter as adapter_module
from ctrlrun import (
    Action,
    ActionDenied,
    ApprovalMismatch,
    ApprovalTimeout,
    AuthorityDenied,
    Control,
    IdentityError,
    InvalidArgument,
    Principal,
    StaticIdentityProvider,
    context,
    protect,
)
from ctrlrun.adapter import (
    ApprovalAnswer,
    FrameworkInterrupt,
    InterruptApprovalProvider,
    PendingApproval,
    banner,
    needs_approval,
)
from ctrlrun.approval import ApprovalStatus, build_request
from ctrlrun.authority import Authority
from ctrlrun.policy import Policy
from ctrlrun.receipt import EventType
from ctrlrun.state import InMemoryStateStore, SQLiteStateStore

REFUND = "stripe.refund"

POLICY_APPROVE = """
schema: ctrlrun.policy/v1
actions:
  stripe.refund:
    rules:
      - when: {amount_lte: 100}
        decision: allow
      - when: {amount_lte: 500000}
        decision: approve
      - decision: deny
"""

POLICY_ALLOW = """
schema: ctrlrun.policy/v1
actions:
  stripe.refund:
    decision: allow
"""

AUTHORITY_NONE = """
schema: ctrlrun.policy/v3
mode: enforce
authority:
  grants:
    - id: nobody
      subject: {agent: somebody-else}
      actions: ["stripe.*"]
      resources: ["payment:*"]
      environments: [production]
actions:
  stripe.refund:
    rules:
      - when: {amount_lte: 500000}
        decision: approve
      - decision: deny
"""


class CountingInterrupt:
    """A `FrameworkInterrupt` double that records every call it was given.

    It is the observer every negative test here needs: without a count, "the action was
    refused" is equally true of a run that never reached the adapter, and that run passes
    against a kernel with the guard deleted.
    """

    framework = "double"

    def __init__(
        self,
        answer: ApprovalAnswer | None = None,
        *,
        carries: bool = True,
        raises: BaseException | None = None,
        before: Any = None,
    ) -> None:
        self.carries_approved_arguments = carries
        self._answer = answer
        self._raises = raises
        self._before = before
        self.calls: list[PendingApproval] = []

    def interrupt(self, pending: PendingApproval) -> ApprovalAnswer:
        self.calls.append(pending)
        if self._before is not None:
            self._before()
        if self._raises is not None:
            raise self._raises
        # Whatever it was handed, unchanged: a faithful courier hands its framework's reply
        # through, and a double that quietly coerced one would be a double that refuses less
        # than the real thing.
        if isinstance(self._answer, ApprovalAnswer) and self._answer.approved_arguments is _ECHO:
            return replace(self._answer, approved_arguments=dict(pending.arguments))
        return self._answer  # type: ignore[return-value]


#: Sentinel for "hand back whatever you were given" -- SPEC-v0.5 §3.4's manufactured check.
_ECHO: Any = object()


def granted(**overrides: Any) -> ApprovalAnswer:
    base = ApprovalAnswer(granted=True, approver="double:interrupt", approved_arguments=_ECHO)
    return replace(base, **overrides)


class Clock:
    """A fake clock that only moves when a test moves it (no wall-clock in a waiting test)."""

    def __init__(self, now: datetime | None = None) -> None:
        self.now = now or datetime(2026, 9, 4, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


def build(
    document: str,
    interrupt: CountingInterrupt | None = None,
    *,
    clock: Clock | None = None,
    identity: Any = None,
    tmp_path: Any = None,
) -> tuple[Control, InMemoryStateStore, Clock]:
    ticking = clock or Clock()
    store = InMemoryStateStore(clock=ticking)
    policy = Policy.from_yaml(document, source="<test>")
    has_authority = "authority:" in document
    authority = Authority.from_yaml(document, source="<test>") if has_authority else None
    approvals = (
        InterruptApprovalProvider(store, interrupt, clock=ticking)
        if interrupt is not None
        else None
    )
    control = Control(
        policy,
        store,
        approvals,
        clock=ticking,
        authority=authority,
        identity=identity,
    )
    return control, store, ticking


def refund_tool(control: Control):
    calls: list[dict[str, Any]] = []

    @protect(REFUND, effect="refund:{payment_id}", resource="payment:{payment_id}", wait=True,
             control=control)
    def issue_refund(payment_id: str, amount: int) -> str:
        calls.append({"payment_id": payment_id, "amount": amount})
        return "ok"

    issue_refund.calls = calls  # type: ignore[attr-defined]
    return issue_refund


def events(store: InMemoryStateStore) -> list[str]:
    return [str(event.type) for event in store.events()]


# --- T126: the order, by observation --------------------------------------------------------------


EXPIRED_PRINCIPAL = Principal(
    agent="finance-agent", expires_at=datetime(2026, 9, 4, 11, 0, tzinfo=UTC)
)
LIVE_PRINCIPAL = Principal(
    agent="finance-agent", expires_at=datetime(2026, 9, 4, 13, 0, tzinfo=UTC)
)


@pytest.mark.authority
def test_T126_an_expired_principal_refuses_before_authority_and_before_policy():
    """SPEC-v0.3 §4.3.1's order, observed through an adapter rather than read from the source."""
    double = CountingInterrupt(granted())
    control, store, _ = build(AUTHORITY_NONE, double, identity=StaticIdentityProvider("x"))
    control = replace_identity(control, EXPIRED_PRINCIPAL)

    with pytest.raises(IdentityError):
        refund_tool(control)(payment_id="txn_1", amount=2000)

    # The event sequence, and the reason on the event -- not a substring of a message. A
    # disjunction over "expired" is satisfied by an *approval* expiring, which is a different
    # guard at a different point in the order.
    seen = events(store)
    assert seen == [str(EventType.ACTION_PROPOSED), str(EventType.ACTION_DENIED)], seen
    denied = [e for e in store.events() if e.type is EventType.ACTION_DENIED]
    assert [e.data["reason"] for e in denied] == ["principal_expired"]
    assert [r.decision_reason for r in store.receipts()] == ["principal_expired"]
    assert double.calls == []


@pytest.mark.authority
def test_T126_its_control_the_same_config_with_a_live_principal_reaches_authority():
    """Without this, T126 passes on a kernel that refuses everything for any reason at all."""
    double = CountingInterrupt(granted())
    control, store, _ = build(AUTHORITY_NONE, double, identity=StaticIdentityProvider("x"))
    control = replace_identity(control, LIVE_PRINCIPAL)

    with pytest.raises(AuthorityDenied):
        refund_tool(control)(payment_id="txn_1", amount=2000)

    seen = events(store)
    assert str(EventType.AUTHORITY_DENIED) in seen
    assert str(EventType.POLICY_EVALUATED) not in seen
    assert double.calls == []


def test_T126_and_the_double_can_be_called_when_nothing_refuses():
    """The mutant for both tests above: the observer is able to see a call when there is one."""
    double = CountingInterrupt(granted())
    control, _, _ = build(POLICY_APPROVE, double)

    with context("finance-agent"):
        refund_tool(control)(payment_id="txn_1", amount=2000)

    assert len(double.calls) == 1


def replace_identity(control: Control, principal: Principal) -> Control:
    """A Control whose provider resolves `principal`. Built here rather than in `build` so the
    two T126 tests differ in exactly one value."""
    provider = StaticIdentityProvider(
        agent=principal.agent, user=principal.user, expires_at=principal.expires_at
    )
    return Control(
        control.policy,
        control.store,
        control.approvals,
        clock=control._clock,
        authority=control.authority,
        identity=provider,
    )


# --- T127: an adapter cannot skip authority -------------------------------------------------------


@pytest.mark.authority
def test_T127_an_adapter_that_reaches_a_control_with_authority_is_refused():
    double = CountingInterrupt(granted())
    control, store, _ = build(AUTHORITY_NONE, double)
    tool = refund_tool(control)

    with context("finance-agent"), pytest.raises(AuthorityDenied) as raised:
        tool(payment_id="txn_1", amount=2000)

    assert raised.value.reason == "no_authority"
    assert str(EventType.POLICY_EVALUATED) not in events(store)
    assert tool.calls == []
    assert double.calls == []


@pytest.mark.authority
def test_T127_the_executor_is_never_reached_without_control_execute():
    """The adapter-specific half. Without it this is a v0.3 regression wearing a new label.

    The mutant is an "adapter" that calls the function directly: it reaches the executor, which
    is what the surface makes impossible and what this asserts about.
    """
    double = CountingInterrupt(granted())
    control, _, _ = build(AUTHORITY_NONE, double)
    tool = refund_tool(control)

    with context("finance-agent"), pytest.raises(AuthorityDenied):
        tool(payment_id="txn_1", amount=2000)
    assert tool.calls == []

    # The mutant, stated rather than implied: bypassing the surface does reach the executor.
    assert tool.__wrapped__(payment_id="txn_1", amount=2000) == "ok"
    assert tool.calls == [{"payment_id": "txn_1", "amount": 2000}]


# --- T128: a denial by authority leaves no pending approval ---------------------------------------


@pytest.mark.authority
def test_T128_an_authority_denial_through_an_adapter_leaves_no_request():
    double = CountingInterrupt(granted())
    control, store, _ = build(AUTHORITY_NONE, double)

    with context("finance-agent"), pytest.raises(AuthorityDenied):
        refund_tool(control)(payment_id="txn_1", amount=2000)

    action = Action(
        name=REFUND,
        arguments={"payment_id": "txn_1", "amount": 2000},
        principal=Principal(agent="finance-agent"),
        resource="payment:txn_1",
    )
    assert store.approvals_for(action.action_hash) == ()
    assert str(EventType.APPROVAL_REQUESTED) not in events(store)
    assert double.calls == []


# --- T128b: needs_approval ------------------------------------------------------------------------


def test_T128b_needs_approval_is_true_only_for_approve():
    control, _, _ = build(POLICY_APPROVE, CountingInterrupt(granted()))

    with context("finance-agent"):
        assert needs_approval(control, REFUND, {"payment_id": "t", "amount": 2000}) is True
        assert needs_approval(control, REFUND, {"payment_id": "t", "amount": 50}) is False
        assert needs_approval(control, REFUND, {"payment_id": "t", "amount": 900000}) is False


@pytest.mark.authority
def test_T128b_a_denial_by_authority_returns_false_so_the_tool_is_invoked_and_denied():
    """SPEC-v0.5 §3.5. Refusing inside the predicate would refuse without evidence."""
    double = CountingInterrupt(granted())
    control, store, _ = build(AUTHORITY_NONE, double)

    with context("finance-agent"):
        assert needs_approval(control, REFUND, {"payment_id": "t", "amount": 2000}) is False

        with pytest.raises(AuthorityDenied):
            refund_tool(control)(payment_id="t", amount=2000)

    assert str(EventType.ACTION_DENIED) in events(store)
    assert [receipt.result for receipt in store.receipts()] == ["denied"]


def test_T128b_needs_approval_writes_nothing():
    """A predicate a framework may call more than once must leave nothing behind."""
    control, store, _ = build(POLICY_APPROVE, CountingInterrupt(granted()))

    with context("finance-agent"):
        before = (len(store.events()), len(store.receipts()))
        for amount in (50, 2000, 900000):
            needs_approval(control, REFUND, {"payment_id": "t", "amount": amount})
        assert (len(store.events()), len(store.receipts())) == before
        assert store.get_effect("refund:t") is None


def test_T128b_the_principal_is_the_controls_and_not_the_contexts():
    """SPEC-v0.5 §4.2 -- `needs_approval` resolves identity the way every entry point does."""
    seen: list[Action] = []
    control, _, _ = build(POLICY_APPROVE, CountingInterrupt(granted()),
                          identity=StaticIdentityProvider("provider-agent"))
    original = control.policy.evaluate

    def spy(action: Action):
        seen.append(action)
        return original(action)

    object.__setattr__(control.policy, "evaluate", spy)
    with context("context-agent"):
        needs_approval(control, REFUND, {"payment_id": "t", "amount": 2000})

    assert [action.principal.agent for action in seen] == ["provider-agent"]


def test_T128b_the_resource_template_is_resolved_the_way_protect_resolves_it():
    """Authority matches on resource (v0.3 §4.2), so a predicate that skipped it would evaluate
    a different action from the one that runs."""
    control, _, _ = build(POLICY_APPROVE, CountingInterrupt(granted()))
    seen: list[Action] = []
    original = control.policy.evaluate

    def spy(action: Action):
        seen.append(action)
        return original(action)

    object.__setattr__(control.policy, "evaluate", spy)
    with context("finance-agent"):
        needs_approval(
            control, REFUND, {"payment_id": "txn_9", "amount": 2000},
            resource="payment:{payment_id}",
        )

    assert [action.resource for action in seen] == ["payment:txn_9"]


# --- T129: an adapter cannot supply a principal ---------------------------------------------------


PRINCIPAL_NAMES = {"principal", "agent", "user", "claims"}
CONTROL_NAMES = {"identity", "authority", "environment", "control_class"}


def public_callables() -> list[tuple[str, Any]]:
    found = []
    for name in adapter_module.__all__:
        value = getattr(adapter_module, name)
        if inspect.isfunction(value):
            found.append((name, value))
        elif inspect.isclass(value) and not is_dataclass(value):
            found.append((f"{name}.__init__", value.__init__))
    return found


@pytest.mark.parametrize(
    "name,func", public_callables(), ids=lambda item: getattr(item, "__name__", item)
)
def test_T129_no_public_callable_takes_a_principal(name, func):
    """Scoped to callables on purpose: `PendingApproval` has fields named `agent` and `user`,
    and they are read-only fields of a frozen dataclass -- outputs, not inputs (§4.2)."""
    signature = inspect.signature(func)
    for parameter in signature.parameters.values():
        assert parameter.name not in PRINCIPAL_NAMES, f"{name}({parameter.name}=...)"
        annotation = str(parameter.annotation)
        assert "Principal" not in annotation, f"{name}({parameter.name}: {annotation})"


def test_T129_the_module_exposes_no_way_to_construct_a_control():
    """The route a `Principal` would actually take, and the one a scan of parameter names alone
    would miss: `Control(identity=...)` is §4.2 defeated in a constructor keyword."""
    assert "Control" not in adapter_module.__all__
    # By the module's own code objects rather than by a regex over its text: a source scan for
    # `Control(` misses `Control.from_file()`, `Control (`, `type(control)(...)` and every
    # indirection, and a test that can be evaded by whitespace is a test nobody has to satisfy.
    # The `Control` import is under `TYPE_CHECKING`, so no runtime name in this module resolves
    # to the class -- and that is what is asserted.
    assert "Control" not in vars(adapter_module)
    for name in _referenced_names(adapter_module):
        assert name not in {"Control", "from_file"}, f"adapter.py reaches {name}"
    for name, func in public_callables():
        for parameter in inspect.signature(func).parameters.values():
            assert parameter.name not in CONTROL_NAMES, f"{name}({parameter.name}=...)"


def _referenced_names(module) -> set[str]:
    """Every global and attribute name the module's compiled code can reach."""
    import types

    seen: set[str] = set()
    stack = [obj.__code__ for obj in vars(module).values() if isinstance(obj, types.FunctionType)]
    stack += [
        method.__code__
        for value in vars(module).values()
        if inspect.isclass(value)
        for method in vars(value).values()
        if isinstance(method, types.FunctionType)
    ]
    while stack:
        code = stack.pop()
        seen |= set(code.co_names)
        stack += [const for const in code.co_consts if isinstance(const, types.CodeType)]
    return seen


def test_T129_pending_approval_carries_the_principal_as_read_only_strings():
    assert is_dataclass(PendingApproval)
    assert PendingApproval.__dataclass_params__.frozen
    by_name = {field.name: field for field in fields(PendingApproval)}
    assert by_name["agent"].type in ("str", str)
    assert by_name["user"].type in ("str | None", "str|None")


def test_T129_the_receipt_names_the_provider_and_not_the_framework_session():
    """Behaviourally: a `Control` whose provider resolves A, driven by an adapter whose framework
    session says B, produces receipts naming A."""
    double = CountingInterrupt(granted())
    control, store, _ = build(
        POLICY_ALLOW, double, identity=StaticIdentityProvider("provider-agent")
    )

    with context("framework-session-agent"):
        refund_tool(control)(payment_id="txn_1", amount=2000)

    assert [receipt.principal.agent for receipt in store.receipts()] == ["provider-agent"]


# --- T129b: the provider is the only thing that grants --------------------------------------------


def test_T129b_the_grant_is_written_by_the_provider_and_not_by_the_interrupt():
    """§2.4 step 6. An adapter that granted for itself is §5.4's `grants-for-itself` fixture; the
    surface gives it no way to, and this is what says so."""
    call = re.compile(r"\.(grant_approval|deny_approval)\s*\(")
    module_calls = call.findall(inspect.getsource(adapter_module))
    assert module_calls, "nothing in adapter.py writes a grant, so the provider does not work"

    inside = call.findall(inspect.getsource(InterruptApprovalProvider.wait))
    assert sorted(module_calls) == sorted(inside), (
        "adapter.py writes a grant somewhere other than InterruptApprovalProvider.wait"
    )


def test_T129b_its_control_a_correct_answer_does_grant():
    """Without the pair, the assertion above is satisfied by a provider that never grants."""
    double = CountingInterrupt(granted())
    control, store, _ = build(POLICY_APPROVE, double)

    with context("finance-agent"):
        refund_tool(control)(payment_id="txn_1", amount=2000)

    consumed = [
        event for event in store.events() if event.type is EventType.APPROVAL_CONSUMED
    ]
    assert [event.data["approver"] for event in consumed] == ["double:interrupt"]
    assert [receipt.approver for receipt in store.receipts()] == ["double:interrupt"]


# --- T129c: a framework error is never an answer --------------------------------------------------


class FrameworkControlFlow(BaseException):
    """The shape LangGraph's `GraphInterrupt` has: a non-local exit through `wait()`.

    A `BaseException` subclass rather than the real thing, so this test does not depend on a
    framework being installed -- and so the assertion is about what the provider does with an
    exception it does not recognise, which is the actual contract.
    """


@pytest.mark.parametrize("failure", [RuntimeError("boom"), FrameworkControlFlow()])
def test_T129c_an_interrupt_that_raises_writes_nothing_and_propagates(failure):
    double = CountingInterrupt(raises=failure)
    control, store, _ = build(POLICY_APPROVE, double)

    with context("finance-agent"), pytest.raises(type(failure)):
        refund_tool(control)(payment_id="txn_1", amount=2000)

    assert len(double.calls) == 1
    seen = events(store)
    assert str(EventType.APPROVAL_GRANTED) not in seen
    assert str(EventType.APPROVAL_DENIED) not in seen
    assert store.receipts() == ()
    pending = store.approvals_for(double.calls[0].action_hash)
    assert [record.status for record in pending] == [ApprovalStatus.PENDING]


def test_T129c_a_denial_is_a_humans_answer_and_not_a_default():
    """The control for the test above: a *stated* refusal is recorded, so the absence of a
    record after a crash is the provider declining to invent one rather than a provider that
    records nothing."""
    double = CountingInterrupt(ApprovalAnswer(granted=False, approver="double:interrupt"),
                               carries=False)
    control, store, _ = build(POLICY_APPROVE, double)

    with context("finance-agent"), pytest.raises(ActionDenied):
        refund_tool(control)(payment_id="txn_1", amount=2000)

    assert str(EventType.APPROVAL_DENIED) in events(store)


# --- T129d: expiry, before and during -------------------------------------------------------------


def test_T129d_an_expired_request_is_not_put_to_a_human():
    """§2.4 step 2: putting a dead request to a human spends the one scarce resource on nothing.

    The count is the whole assertion. `grant_approval` would refuse an expired record anyway
    (v0.1 §4.2 A3), so a test that only checked the exception would pass with step 2 deleted.
    """
    clock = Clock()
    store = InMemoryStateStore(clock=clock)
    double = CountingInterrupt(granted())
    provider = InterruptApprovalProvider(store, double, clock=clock)
    request = provider.request(_action(), timedelta(minutes=15))
    clock.advance(timedelta(hours=1))

    with pytest.raises(ApprovalTimeout):
        provider.wait(request.request_id, None)

    assert double.calls == []
    record = store.get_approval(request.request_id)
    assert record is not None and record.status is ApprovalStatus.PENDING


def test_T129d_an_answer_that_arrives_after_the_expiry_is_not_recorded():
    """The half that pins §2.4's step order. Recording first would reach the same *decision* --
    `grant_approval` refuses an expired record -- and would leave it `expired` rather than
    `pending`, and raise `ApprovalMismatch` rather than `ApprovalTimeout`. Both are asserted."""
    clock = Clock()
    store = InMemoryStateStore(clock=clock)
    double = CountingInterrupt(granted(), before=lambda: clock.advance(timedelta(hours=1)))
    provider = InterruptApprovalProvider(store, double, clock=clock)
    action = Action(
        name=REFUND,
        arguments={"payment_id": "txn_1", "amount": 2000},
        principal=Principal(agent="finance-agent"),
    )
    request = provider.request(action, timedelta(minutes=15))

    with pytest.raises(ApprovalTimeout):
        provider.wait(request.request_id, None)

    assert len(double.calls) == 1
    record = store.get_approval(request.request_id)
    assert record is not None
    assert record.status is ApprovalStatus.PENDING
    assert record.approver is None


def test_T129d_timeout_bounds_the_wait_where_it_is_sooner_than_the_expiry():
    """§2.4 -- `timeout` is a deadline the provider holds across the interrupt.

    The interrupt here takes five minutes of fake clock: inside the request's own fifteen, and
    outside the one minute the caller allowed. Without this the parameter is in a frozen
    signature and in no step, which is how two implementations of it come to exist -- and the
    request's expiry alone would satisfy any test that only moved the clock past *that*.
    """
    clock = Clock()
    store = InMemoryStateStore(clock=clock)
    double = CountingInterrupt(granted(), before=lambda: clock.advance(timedelta(minutes=5)))
    provider = InterruptApprovalProvider(store, double, clock=clock)
    request = provider.request(_action(), timedelta(minutes=15))

    with pytest.raises(ApprovalTimeout):
        provider.wait(request.request_id, timedelta(minutes=1))

    assert len(double.calls) == 1
    record = store.get_approval(request.request_id)
    assert record is not None and record.status is ApprovalStatus.PENDING


def test_T129d_its_control_the_same_interrupt_inside_the_timeout_grants():
    """Without it, the test above passes against a provider that refuses every timeout."""
    clock = Clock()
    store = InMemoryStateStore(clock=clock)
    double = CountingInterrupt(granted(), before=lambda: clock.advance(timedelta(minutes=5)))
    provider = InterruptApprovalProvider(store, double, clock=clock)
    request = provider.request(_action(), timedelta(minutes=15))

    approval = provider.wait(request.request_id, timedelta(minutes=10))

    assert approval is not None and approval.approver == "double:interrupt"


def test_T129d_a_timeout_longer_than_the_request_does_not_extend_it():
    """The deadline is the *sooner* of the two: a caller cannot outlive the request by asking."""
    clock = Clock()
    store = InMemoryStateStore(clock=clock)
    double = CountingInterrupt(granted(), before=lambda: clock.advance(timedelta(minutes=20)))
    provider = InterruptApprovalProvider(store, double, clock=clock)
    request = provider.request(_action(), timedelta(minutes=15))

    with pytest.raises(ApprovalTimeout):
        provider.wait(request.request_id, timedelta(hours=3))

    record = store.get_approval(request.request_id)
    assert record is not None and record.status is ApprovalStatus.PENDING


# --- T129e: an answer with no approver ------------------------------------------------------------


@pytest.mark.parametrize("approver", ["", "   ", "\t\n"])
def test_T129e_an_answer_with_no_approver_is_refused(approver):
    """§2.2 -- and the assertion is on **which** guard refused, not merely that one did.

    `store.grant_approval` already refuses `""` with `InvalidArgument`, so a test asserting only
    the type would pass with this check deleted for that one value: a guard that can only fire
    where a later guard fires with the same observable result is documentation rather than
    defence. Two things separate them and both are asserted. The message names the framework, so
    an operator reading a traceback knows which adapter handed back a nameless answer. And the
    store accepts `"   "` -- a whitespace-only approver reaches the record and lands on a
    receipt -- so for every value but the empty string this check is the only one there is.
    """
    store = InMemoryStateStore()  # real clock: nothing here depends on time
    double = CountingInterrupt(
        ApprovalAnswer(granted=True, approver=approver, approved_arguments=_ECHO)
    )
    provider = InterruptApprovalProvider(store, double)
    request = provider.request(_action(), timedelta(minutes=15))

    with pytest.raises(InvalidArgument) as raised:
        provider.wait(request.request_id, None)

    assert "double" in str(raised.value), "the refusal does not name the framework that answered"
    assert "must name an approver" in str(raised.value)
    record = store.get_approval(request.request_id)
    assert record is not None and record.status is ApprovalStatus.PENDING


def test_T129e_the_store_alone_would_let_a_whitespace_approver_through():
    """The other half of the subsumption argument, stated as a test rather than as a claim.

    Without it, "this check is not subsumed" is an assertion about code somebody would have to
    re-derive; with it, a change to `grant_approval` that started refusing whitespace would show
    up here as the reason this guard became documentation.
    """
    store = InMemoryStateStore()
    request = build_request(_action(), timedelta(minutes=15), datetime.now(UTC))
    store.put_approval_request(request)

    granted_by_the_store = store.grant_approval(request.request_id, "   ")

    assert granted_by_the_store.approver == "   "


# --- T129f: PendingApproval survives a checkpoint -------------------------------------------------


def test_T129f_pending_approval_round_trips_through_json():
    store = InMemoryStateStore()
    double = CountingInterrupt(granted())
    provider = InterruptApprovalProvider(store, double)
    action = Action(
        name=REFUND,
        arguments={
            "payment_id": "txn_1",
            "amount": 2000,
            "dry_run": False,
            "note": None,
            "tags": ["a", "b"],
            "meta": {"nested": {"deep": [1, 2, {"x": True}]}},
        },
        principal=Principal(agent="finance-agent", user="ada"),
        resource="payment:txn_1",
    )
    request = provider.request(action, timedelta(minutes=15))
    provider.wait(request.request_id, None)
    pending = double.calls[0]

    document = pending.to_dict()
    assert json.loads(json.dumps(document)) == document
    assert document["arguments"] == action.canonical_arguments
    assert document["expires_at"] == request.expires_at.isoformat()


# --- T129g: the banner ----------------------------------------------------------------------------


OBSERVE = """
schema: ctrlrun.policy/v3
mode: observe
actions:
  stripe.refund:
    decision: allow
"""


def test_T129g_the_banner_is_logged_once_per_control_and_printed_never(caplog, capsys):
    control, _, _ = build(OBSERVE)
    other, _, _ = build(OBSERVE)

    with caplog.at_level(logging.WARNING, logger="ctrlrun"):
        for _ in range(10):
            banner(control)
        banner(other)

    records = [record for record in caplog.records if "OBSERVE MODE" in record.getMessage()]
    assert len(records) == 2, records
    # The logger and the level, both of which §3.6 states: a banner on a different logger would
    # still reach caplog through root propagation and pass a message-only assertion.
    assert {record.name for record in records} == {"ctrlrun"}
    assert {record.levelno for record in records} == {logging.WARNING}
    assert all("nothing is enforced" in record.getMessage() for record in records)
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_T129g_the_banner_is_a_no_op_under_enforce(caplog):
    control, _, _ = build(POLICY_ALLOW)

    with caplog.at_level(logging.WARNING, logger="ctrlrun"):
        banner(control)

    assert [r for r in caplog.records if "OBSERVE MODE" in r.message] == []


def test_T129g_an_adapter_never_interrupts_in_observe_mode():
    """§3.6 -- `ApprovalRequired` is never raised there, so the primitive is never reached."""
    double = CountingInterrupt(granted())
    document = OBSERVE.replace("decision: allow", "rules:\n      - decision: approve")
    control, _, _ = build(document, double)

    with context("finance-agent"):
        refund_tool(control)(payment_id="txn_1", amount=2000)

    assert double.calls == []


# --- T129h: no flag relaxes a check ---------------------------------------------------------------


RELAXING = re.compile(r"auto_approve|skip|bypass|dry_run|force|insecure|allow_|disable_")


@pytest.mark.parametrize(
    "name,func", public_callables(), ids=lambda item: getattr(item, "__name__", item)
)
def test_T129h_no_public_callable_takes_a_relaxing_flag(name, func):
    for parameter in inspect.signature(func).parameters.values():
        assert not RELAXING.search(parameter.name), f"{name}({parameter.name}=...)"


def test_T129h_the_module_reads_no_environment_variable():
    source = inspect.getsource(adapter_module)
    assert "os.environ" not in source
    assert "getenv" not in source


# --- The binding check (§3.4) ---------------------------------------------------------------------


def test_a_declared_carrier_that_omits_the_arguments_is_refused():
    """§3.4 -- a declaration is not a hint."""
    store = InMemoryStateStore()
    double = CountingInterrupt(
        ApprovalAnswer(granted=True, approver="double"), carries=True
    )
    provider = InterruptApprovalProvider(store, double)
    request = provider.request(_action(), timedelta(minutes=15))

    with pytest.raises(ApprovalMismatch) as raised:
        provider.wait(request.request_id, None)

    assert raised.value.reason == "mismatch"
    record = store.get_approval(request.request_id)
    assert record is not None and record.status is ApprovalStatus.PENDING


def test_an_answer_given_against_different_arguments_is_refused():
    store = InMemoryStateStore()
    double = CountingInterrupt(
        ApprovalAnswer(
            granted=True,
            approver="double",
            approved_arguments={"payment_id": "txn_1", "amount": 500},
        )
    )
    provider = InterruptApprovalProvider(store, double)
    request = provider.request(_action(), timedelta(minutes=15))

    with pytest.raises(ApprovalMismatch) as raised:
        provider.wait(request.request_id, None)

    assert raised.value.reason == "mismatch"


def test_the_binding_check_is_type_strict():
    """`True` must not compare equal to `1`: the canonical form distinguishes them (v0.1 §2.3)."""
    store = InMemoryStateStore()
    action = Action(
        name=REFUND,
        arguments={"payment_id": "txn_1", "amount": 1},
        principal=Principal(agent="finance-agent"),
    )
    double = CountingInterrupt(
        ApprovalAnswer(
            granted=True, approver="double",
            approved_arguments={"payment_id": "txn_1", "amount": True},
        )
    )
    provider = InterruptApprovalProvider(store, double)
    request = provider.request(action, timedelta(minutes=15))

    with pytest.raises(ApprovalMismatch):
        provider.wait(request.request_id, None)


def test_an_unrepresentable_answer_is_refused_at_the_gate():
    """A float arriving from a framework's JSON is `InvalidArgument`, not a silent inequality."""
    store = InMemoryStateStore()
    double = CountingInterrupt(
        ApprovalAnswer(
            granted=True, approver="double",
            approved_arguments={"payment_id": "txn_1", "amount": 20.0},
        )
    )
    provider = InterruptApprovalProvider(store, double)
    request = provider.request(_action(), timedelta(minutes=15))

    with pytest.raises(InvalidArgument):
        provider.wait(request.request_id, None)


def test_a_refusal_is_never_refused_for_carrying_no_arguments():
    """SPEC-v0.5 §3.4, §12.2. An answer of `granted=False` authorizes nothing, so there is
    nothing to bind it to -- and requiring `approved_arguments` on one turned a human's *no*
    into an `ApprovalMismatch`, when §2.4 is explicit that a denial is an answer and gets
    `APPROVAL_DENIED` then `ACTION_DENIED`.

    The conformance kit found this by driving a denial through a declared carrier. It has a
    test here too, because a defect in `adapter.py` whose only coverage is `conformance/` is a
    defect that comes back the moment somebody edits one without running the other.
    """
    store = InMemoryStateStore()
    double = CountingInterrupt(
        ApprovalAnswer(granted=False, approver="double:interrupt"), carries=True
    )
    provider = InterruptApprovalProvider(store, double)
    request = provider.request(_action(), timedelta(minutes=15))

    assert provider.wait(request.request_id, None) is None

    record = store.get_approval(request.request_id)
    assert record is not None
    assert str(record.status) == "denied"
    assert record.approver == "double:interrupt"


def test_a_non_carrier_is_not_checked_and_says_so():
    """§3.4's second bullet: attribution, declared. The kit turns this into `not_applicable`."""
    store = InMemoryStateStore()
    double = CountingInterrupt(
        ApprovalAnswer(granted=True, approver="double"), carries=False
    )
    provider = InterruptApprovalProvider(store, double)
    request = provider.request(_action(), timedelta(minutes=15))

    approval = provider.wait(request.request_id, None)

    assert approval is not None and approval.approver == "double"


def _action() -> Action:
    return Action(
        name=REFUND,
        arguments={"payment_id": "txn_1", "amount": 2000},
        principal=Principal(agent="finance-agent"),
    )


# --- The Protocol itself --------------------------------------------------------------------------


def test_the_surface_is_a_protocol_and_not_a_base_class():
    """§2.1 -- a base class is a channel for the kernel to change an adapter's behaviour later
    without its author's consent, and this is what stops one appearing."""
    assert getattr(FrameworkInterrupt, "_is_protocol", False)
    assert isinstance(CountingInterrupt(granted()), FrameworkInterrupt)


def test_an_interrupt_missing_its_declaration_is_refused_at_construction():
    class Incomplete:
        framework = "incomplete"

        def interrupt(self, pending: PendingApproval) -> ApprovalAnswer:  # pragma: no cover
            raise AssertionError

    with pytest.raises(InvalidArgument):
        InterruptApprovalProvider(InMemoryStateStore(), Incomplete())  # type: ignore[arg-type]


def test_an_interrupt_with_no_framework_name_is_refused_at_construction():
    class Nameless:
        framework = ""
        carries_approved_arguments = True

        def interrupt(self, pending: PendingApproval) -> ApprovalAnswer:  # pragma: no cover
            raise AssertionError

    with pytest.raises(InvalidArgument):
        InterruptApprovalProvider(InMemoryStateStore(), Nameless())


# --- What the independent review found untested (SPEC-v0.5 §2.4 step 1, §2.2) -----------

# Five live guards had no test at all, and the mutation table did not contain them: deleting
# each left all 49 green. Three of them are the whole of §2.4's **step 1** -- the step with the
# most explicit language in the section -- and §10's row "the request is not `pending`" had no
# test either. A guard nothing exercises is a guard nobody can tell you about.


def test_an_unknown_request_is_refused_and_no_human_is_asked():
    """§2.4 step 1, first branch. `LocalApprovalProvider` does exactly this, and so must a
    provider that would otherwise put an invented request in front of somebody."""
    double = CountingInterrupt(granted())
    provider = InterruptApprovalProvider(InMemoryStateStore(), double)

    with pytest.raises(ApprovalMismatch) as raised:
        provider.wait("apr_no_such_request", None)

    assert raised.value.reason == "unknown"
    assert double.calls == []


def test_an_already_granted_request_is_returned_without_asking_again():
    """§2.4 step 1, second branch.

    Correct, and worth a test precisely because it looks like a hole: a request granted out of
    band -- by `ctrlrun approve`, or by a webhook -- is a real answer from a real human, and a
    provider never invents one and never re-asks about one (v0.1 §4.3). What it *does* mean is
    that an adapter which granted for itself would never reach its own interrupt, which is why
    the conformance kit counts interrupt calls rather than trusting this branch to notice.
    """
    store = InMemoryStateStore()
    double = CountingInterrupt(granted())
    provider = InterruptApprovalProvider(store, double)
    request = provider.request(_action(), timedelta(minutes=15))
    store.grant_approval(request.request_id, "cli:local")

    approval = provider.wait(request.request_id, None)

    assert approval is not None
    assert approval.approver == "cli:local"
    assert double.calls == [], "a request a human already answered was put to them again"


@pytest.mark.parametrize("answered", ["deny", "consume"])
def test_a_request_that_will_never_be_granted_returns_none(answered):
    """§2.4 step 1, third branch, and §10's "the request is not `pending`" row.

    `None` means *answered, no* and never *still waiting* (v0.1 §4.3). Returning the record, or
    interrupting again, would ask a human to reconsider a decision the store has already made.
    """
    store = InMemoryStateStore()
    double = CountingInterrupt(granted())
    provider = InterruptApprovalProvider(store, double)
    action = _action()
    request = provider.request(action, timedelta(minutes=15))
    if answered == "deny":
        store.deny_approval(request.request_id, "cli:local")
    else:
        store.grant_approval(request.request_id, "cli:local")
        store.consume_approval(request.request_id, action.action_hash)

    assert provider.wait(request.request_id, None) is None
    assert double.calls == []


def test_an_interrupt_that_returns_something_other_than_an_answer_is_refused():
    """§2.4 step 4. A framework whose resume value is a dict, or `True`, or `None` reaches here
    unchanged if the adapter is a faithful courier -- and none of those is a verdict."""
    for returned in ({"approved": True}, True, None, "yes"):
        store = InMemoryStateStore()
        double = CountingInterrupt(returned)  # type: ignore[arg-type]
        provider = InterruptApprovalProvider(store, double)
        request = provider.request(_action(), timedelta(minutes=15))

        with pytest.raises(InvalidArgument) as raised:
            provider.wait(request.request_id, None)

        assert "ApprovalAnswer" in str(raised.value), returned
        record = store.get_approval(request.request_id)
        assert record is not None and record.status is ApprovalStatus.PENDING


@pytest.mark.parametrize("granted_value", ["no", "yes", 1, 0, None, [], object()])
def test_granted_must_be_a_bool_and_a_truthy_value_is_not_a_yes(granted_value):
    """The hole an independent review found. `granted` is the one field carrying the decision
    and it was the only one on this surface not type-checked, so an adapter handing a
    framework's raw resume value through -- `granted="no"`, which is what LangGraph's
    `interrupt()` returns -- produced a real `Approval`. A human's *no* became a grant."""
    store = InMemoryStateStore()
    double = CountingInterrupt(
        ApprovalAnswer(granted=granted_value, approver="double", approved_arguments=_ECHO)  # type: ignore[arg-type]
    )
    provider = InterruptApprovalProvider(store, double)
    request = provider.request(_action(), timedelta(minutes=15))

    with pytest.raises(InvalidArgument) as raised:
        provider.wait(request.request_id, None)

    assert "granted" in str(raised.value)
    record = store.get_approval(request.request_id)
    assert record is not None and record.status is ApprovalStatus.PENDING


@pytest.mark.parametrize("verdict", [True, False])
def test_its_control_a_real_bool_is_recorded_either_way(verdict):
    """Without the pair, the test above passes against a provider that refuses every answer."""
    store = InMemoryStateStore()
    answer = (
        granted() if verdict else ApprovalAnswer(granted=False, approver="double:interrupt")
    )
    double = CountingInterrupt(answer, carries=verdict)
    provider = InterruptApprovalProvider(store, double)
    request = provider.request(_action(), timedelta(minutes=15))

    result = provider.wait(request.request_id, None)

    record = store.get_approval(request.request_id)
    assert record is not None
    assert (result is not None) is verdict
    assert str(record.status) == ("granted" if verdict else "denied")


def test_an_interrupt_with_no_interrupt_method_is_refused_at_construction():
    class Mute:
        framework = "mute"
        carries_approved_arguments = True

    with pytest.raises(InvalidArgument) as raised:
        InterruptApprovalProvider(InMemoryStateStore(), Mute())  # type: ignore[arg-type]

    assert "interrupt()" in str(raised.value)


def test_the_binding_check_survives_a_round_trip_through_sqlite(tmp_path):
    """The provider is only ever driven against the in-memory store elsewhere, and the binding
    check compares a hash rebuilt from an Action the store rehydrated. A regression in
    `_action_from_json` would break every grant in production and no test would see it."""
    store = SQLiteStateStore(tmp_path / "state.db")
    try:
        action = Action(
            name=REFUND,
            arguments={"payment_id": "txn_1", "amount": 2000, "flag": True, "tags": ["a"]},
            principal=Principal(agent="finance-agent", user="ada"),
            resource="payment:txn_1",
        )
        matching = InterruptApprovalProvider(store, CountingInterrupt(granted()))
        request = matching.request(action, timedelta(minutes=15))
        assert matching.wait(request.request_id, None) is not None

        mutated = InterruptApprovalProvider(
            store,
            CountingInterrupt(
                ApprovalAnswer(
                    granted=True,
                    approver="double",
                    approved_arguments={"payment_id": "txn_1", "amount": 1, "flag": True,
                                        "tags": ["a"]},
                )
            ),
        )
        second = mutated.request(action, timedelta(minutes=15))
        with pytest.raises(ApprovalMismatch) as raised:
            mutated.wait(second.request_id, None)
        assert raised.value.reason == "mismatch"
    finally:
        store.close()


def test_needs_approval_and_protect_can_disagree_and_both_directions_are_safe():
    """SPEC-v0.5 §3.5's stated divergence, driven rather than asserted in prose.

    `@protect` applies the wrapped function's defaults; `needs_approval` gets the framework's
    raw arguments. So a framework that omits a defaulted argument gets `False` from the
    predicate for an action the policy will `APPROVE` -- and the safety is that
    `Control.execute` then raises `ApprovalRequired` and the interrupt is reached anyway.
    """
    document = """
schema: ctrlrun.policy/v1
actions:
  stripe.refund:
    rules:
      - when: {mode_eq: live}
        decision: approve
      - decision: allow
"""
    double = CountingInterrupt(granted())
    control, _, _ = build(document, double)

    with context("finance-agent"):
        # The predicate, given what a framework would pass: no `mode`, so the rule misses.
        assert needs_approval(control, REFUND, {"payment_id": "t", "amount": 1}) is False

        @protect(REFUND, effect="refund:{payment_id}", resource="payment:{payment_id}",
                 wait=True, control=control)
        def issue_refund(payment_id: str, amount: int, mode: str = "live") -> str:
            return "ok"

        # And the call itself, where the default is applied and the rule matches. The human is
        # asked here instead of before invocation: the other framework shape, reached from this
        # one, and nothing executed that would not have.
        assert issue_refund(payment_id="t", amount=1) == "ok"

    assert len(double.calls) == 1
