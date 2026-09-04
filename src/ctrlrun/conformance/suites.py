"""The suites, and what an adapter hands the kit. SPEC-v0.5 §5.2, §5.3.

Every case drives **one** protected call through the adapter's own framework and asserts what
the kernel would have done. The kit supplies the policy, the store, the `Control` and the
executor; the adapter supplies the framework and the courier that carries a human's answer
back. That division is the whole test: an adapter's only contribution to an action's fate
should be *where the human answers*, and a case that came out differently through an adapter
than through `@protect` is the finding.

Two things every case does, and both are `v0.4 §1.3`'s positive-control rule one level up:

- **It counts executor calls.** "The second attempt was refused" is satisfied just as well by
  an adapter that never reached the executor at all, and that adapter passes every refusal.
- **It names the exception and its reason**, not merely that something was raised. A guard
  that can only be distinguished from a later guard by reading the source is documentation.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

from ..action import Principal
from ..adapter import (
    ApprovalAnswer,
    FrameworkInterrupt,
    InterruptApprovalProvider,
    PendingApproval,
)
from ..authority import Authority
from ..control import Control
from ..errors import (
    ActionDenied,
    AmbiguousEffect,
    ApprovalMismatch,
    ApprovalTimeout,
    AuthorityDenied,
    DuplicateEffect,
    NotExecuted,
)
from ..identity import StaticIdentityProvider
from ..policy import OBSERVE, Policy
from ..state import InMemoryStateStore
from .report import CaseResult, ConformanceReport, SuiteResult, SuiteStatus

REFUND = "stripe.refund"
AGENT = "conformance-agent"
APPROVER = "conformance:human"

#: A fixed instant, so a case that turns on expiry is exact rather than nearly exact. Every
#: `Control` the kit builds runs on a clock only the kit moves: a waiting test bound by wall
#: time is a test that hangs CI instead of failing.
T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

ALLOW = """
schema: ctrlrun.policy/v1
actions:
  stripe.refund:
    decision: allow
"""

APPROVE = """
schema: ctrlrun.policy/v1
actions:
  stripe.refund:
    decision: approve
"""

EMPTY = """
schema: ctrlrun.policy/v1
actions: {}
"""

NO_GRANT = """
schema: ctrlrun.policy/v3
authority:
  grants:
    - id: somebody-else
      subject: {agent: somebody-else}
      actions: ["stripe.*"]
      resources: ["payment:*"]
      environments: [production]
actions:
  stripe.refund:
    decision: allow
"""

EXPIRED_GRANT = """
schema: ctrlrun.policy/v3
authority:
  grants:
    - id: lapsed
      subject: {agent: conformance-agent}
      actions: ["stripe.*"]
      resources: ["payment:*"]
      environments: [production]
      expires_at: 2025-01-01T00:00:00+00:00
actions:
  stripe.refund:
    decision: allow
"""

APPROVE_WITHOUT_AUTHORITY = """
schema: ctrlrun.policy/v3
authority:
  grants:
    - id: somebody-else
      subject: {agent: somebody-else}
      actions: ["stripe.*"]
      resources: ["payment:*"]
      environments: [production]
actions:
  stripe.refund:
    decision: approve
"""


@dataclass(frozen=True)
class CallRequest:
    """One protected call the kit wants driven through the framework (SPEC-v0.5 §5.2)."""

    control: Control
    action: str
    arguments: Mapping[str, Any]
    effect: str | None
    executor: Callable[..., Any]
    #: What the human says if the framework interrupts. `None` means **no human is expected**:
    #: an adapter whose framework interrupts anyway has asked about something the policy allowed
    #: outright, and the case fails.
    answer: ApprovalAnswer | None = None


@runtime_checkable
class ConformanceAdapter(Protocol):
    """What an adapter implements so the suites can drive it (SPEC-v0.5 §5.2).

    `interrupt` is the same `FrameworkInterrupt` the operator would wire into a
    `InterruptApprovalProvider`, and the kit wires it into the `Control` it hands back in
    `CallRequest.control` -- the kit plays operator here, because §2.3 says an adapter never
    constructs a `Control` and a kit that let it would be testing a shape that does not ship.

    `invoke` runs **one** protected call end to end through the framework and returns the
    executor's value. Every CTRLRun exception propagates: the kit asserts on them by type and by
    `reason`, so an adapter that swallowed one fails rather than passing quietly.

    The adapter is a **courier** for `request.answer`: it carries the kit's answer to its
    framework's primitive and the primitive's reply back, unchanged. Substituting one is
    `fixtures.EchoesThePayload`, and the binding suite is what catches it.
    """

    framework: str
    interrupt: FrameworkInterrupt

    def invoke(self, request: CallRequest) -> Any: ...


class Clock:
    """A clock only the kit moves: a waiting path bound by wall time hangs CI instead of failing."""

    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


class Executor:
    """The kit's function, and the count that makes every refusal mean something."""

    def __init__(self, *behaviour: BaseException | None) -> None:
        self._behaviour = list(behaviour) or [None]
        self.calls = 0

    def __call__(self, **arguments: Any) -> str:
        self.calls += 1
        step = self._behaviour[min(self.calls - 1, len(self._behaviour) - 1)]
        if step is not None:
            raise step
        return "committed"


def unwrapped(interrupt: FrameworkInterrupt) -> FrameworkInterrupt:
    """The adapter's own interrupt, however many proxies are around it.

    A case builds more than one `World`, and each would otherwise wrap the previous wrapper --
    so a count would be of the innermost proxy's calls rather than of the framework's.
    """
    while isinstance(interrupt, Watched):
        interrupt = interrupt._inner
    return interrupt


class Watched:
    """The adapter's own interrupt, with a call count the kit can read.

    It exists because of a fixture. `GrantsForItself` wrote the grant before `@protect` ever
    reached `wait()`, so the provider found the record already `granted`, returned it, and the
    framework's primitive was never called -- and every suite passed. The provider is right to
    return an already-granted record (`ctrlrun approve` grants out of band, and so does a
    webhook, and `v0.1 §4.3` says a provider never invents an answer), so the hole is not
    there: it is that a suite asserting a *refusal* cannot tell "the human said no through the
    framework" from "the framework was never asked".

    So the kit counts. A case that expects a human asserts the count is 1, and one that expects
    none asserts it is 0. `v0.4 §1.3` again: a refusal proves nothing unless the thing it
    forbids would otherwise have been visible.
    """

    #: The proxy's own attributes. Everything else belongs to the adapter's interrupt and is
    #: forwarded, because the kit swaps this object in for that one: an adapter that sets state
    #: on `self.interrupt` before invoking -- which the reference does, to hand the kit's answer
    #: to its primitive -- must reach the real object and not the wrapper.
    _OWN = frozenset({"_inner", "calls", "before"})

    #: Declared so the type checker sees them: `__init__` sets them past `__setattr__`, and
    #: everything else on this object is forwarded.
    _inner: FrameworkInterrupt
    calls: int
    #: Run just before the adapter's primitive, where a case needs the world to change while a
    #: human deliberates. The kit's only way into that interval, and T5 is what it is for.
    before: Callable[[], None] | None

    def __init__(self, inner: FrameworkInterrupt) -> None:
        object.__setattr__(self, "_inner", unwrapped(inner))
        object.__setattr__(self, "calls", 0)
        object.__setattr__(self, "before", None)
        # `framework` and `carries_approved_arguments` are **forwarded**, not copied. A copy
        # would diverge from the adapter's own declaration the moment anything changed it, and
        # the provider validates what it is handed -- so forwarding keeps one truth.

    def __getattr__(self, name: str) -> Any:
        # Reached only for names not found on the instance, so `_OWN` never lands here.
        return getattr(self._inner, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in self._OWN:
            object.__setattr__(self, name, value)
        else:
            setattr(self._inner, name, value)

    def interrupt(self, pending: PendingApproval) -> ApprovalAnswer:
        self.calls += 1
        if self.before is not None:
            self.before()
        return self._inner.interrupt(pending)


class World:
    """One scratch deployment: a store, a clock and a `Control` wired to the adapter.

    Scratch, always. The kit never touches an operator's store, for the reason `v0.4 §3.5`
    gives about verify: a kit that reserved a real effect key would be a defect of exactly the
    class it exists to find.
    """

    def __init__(
        self, adapter: ConformanceAdapter, document: str, *, principal: str = AGENT
    ) -> None:
        self.clock = Clock()
        self.watched = Watched(adapter.interrupt)
        # **Swapped in place**, not merely wired. Wiring the proxy into the provider counts
        # only the calls that arrive through `wait()` -- and `wait()` is reached only on
        # `ApprovalRequired`, which an `ALLOW` policy never raises. So an adapter that put an
        # allowed call to a human by reaching its *own* primitive out of band was invisible,
        # and the A1 case that forbids it was a negative test against behaviour the path
        # already prevented. Replacing the attribute puts every route through the count.
        # `run()` restores the original.
        adapter.interrupt = self.watched
        self.store = InMemoryStateStore(clock=self.clock)
        policy = Policy.from_yaml(document, source="<conformance>")
        authority = (
            Authority.from_yaml(document, source="<conformance>")
            if "authority:" in document
            else None
        )
        self.control = Control(
            policy,
            self.store,
            InterruptApprovalProvider(self.store, self.watched, clock=self.clock),
            clock=self.clock,
            authority=authority,
            identity=StaticIdentityProvider(principal),
            environment="production",
        )

    def receipts(self) -> tuple[Any, ...]:
        return self.store.receipts()

    def events(self) -> list[str]:
        return [str(event.type) for event in self.store.events()]

    def pending(self) -> list[Any]:
        """Every request still awaiting an answer, found through the events the kernel writes.

        The `StateStore` protocol has no "list every approval", and it should not grow one for
        a test: `APPROVAL_REQUESTED` is the evidence an action awaiting a human leaves
        (`v0.1 §6.1`), and reading it is reading the same trail an operator would.
        """
        records = []
        for event in self.store.events():
            if str(event.type) == "APPROVAL_REQUESTED" and event.approval_id:
                record = self.store.get_approval(event.approval_id)
                if record is not None and str(record.status) == "pending":
                    records.append(record)
        return records


def granted(arguments: Mapping[str, Any] | None = None) -> ApprovalAnswer:
    return ApprovalAnswer(granted=True, approver=APPROVER, approved_arguments=arguments)


@dataclass(frozen=True)
class Case:
    """One acceptance test, and where it came from."""

    id: str
    title: str
    body: Callable[[ConformanceAdapter], CaseResult]


def case(identifier: str, title: str) -> Callable[..., Case]:
    def wrap(body: Callable[[ConformanceAdapter], CaseResult]) -> Case:
        return Case(identifier, title, body)

    return wrap


def failed(identifier: str, title: str, reason: str, **detail: Any) -> CaseResult:
    return CaseResult(identifier, title, SuiteStatus.FAIL, reason, detail)


def passed(identifier: str, title: str) -> CaseResult:
    return CaseResult(identifier, title, SuiteStatus.PASS)


def na(identifier: str, title: str, reason: str) -> CaseResult:
    return CaseResult(identifier, title, SuiteStatus.NOT_APPLICABLE, reason)


def expect(
    identifier: str,
    title: str,
    call: Callable[[], Any],
    error: type[BaseException],
    reason: str | None = None,
) -> CaseResult | None:
    """Run `call`, requiring it to raise `error` (and, where given, with that `reason`).

    Returns `None` when it did, and the failing `CaseResult` when it did not -- so a case body
    reads as a sequence of requirements rather than as nested try/except.
    """
    try:
        call()
    except error as raised:
        got = getattr(raised, "reason", None)
        if reason is not None and got != reason:
            return failed(
                identifier,
                title,
                f"raised {type(raised).__name__} with reason {got!r}, expected {reason!r}",
            )
        return None
    except BaseException as other:
        return failed(
            identifier,
            title,
            f"raised {type(other).__name__}: {other}; expected {error.__name__}",
        )
    return failed(identifier, title, f"did not raise {error.__name__}")


# --- kernel (v0.1 §7) -------------------------------------------------------------------


@case("T1", "a lost response blocks a blind retry")
def kernel_lost_response(adapter: ConformanceAdapter) -> CaseResult:
    world = World(adapter, ALLOW)
    executor = Executor(TimeoutError("the response was lost"), None)
    request = CallRequest(world.control, REFUND, {"payment_id": "t1", "amount": 2000},
                          "refund:t1", executor)

    problem = expect("T1", kernel_lost_response.title,
                     lambda: adapter.invoke(request), TimeoutError)
    if problem:
        return problem
    if executor.calls != 1:
        return failed("T1", kernel_lost_response.title,
                      f"the executor ran {executor.calls} times on the first attempt, expected 1")

    problem = expect("T1", kernel_lost_response.title, lambda: adapter.invoke(request),
                     AmbiguousEffect)
    if problem:
        return problem
    if executor.calls != 1:
        return failed("T1", kernel_lost_response.title,
                      f"the retry reached the executor: {executor.calls} calls, expected 1")
    record = world.store.get_effect("refund:t1")
    if record is None or str(record.state) != "ambiguous":
        return failed("T1", kernel_lost_response.title,
                      f"the effect is {record and record.state}, expected ambiguous")
    return passed("T1", kernel_lost_response.title)


@case("T4", "a committed effect refuses a second attempt, approval or not")
def kernel_duplicate_after_approval(adapter: ConformanceAdapter) -> CaseResult:
    world = World(adapter, APPROVE)
    executor = Executor()
    arguments = {"payment_id": "t4", "amount": 2000}
    request = CallRequest(world.control, REFUND, arguments, "refund:t4", executor,
                          answer=granted(arguments))

    adapter.invoke(request)
    if executor.calls != 1:
        return failed("T4", kernel_duplicate_after_approval.title,
                      f"the approved call ran {executor.calls} times, expected 1")
    if world.watched.calls != 1:
        return failed("T4", kernel_duplicate_after_approval.title,
                      f"the framework's primitive was reached {world.watched.calls} times, "
                      "expected 1: an approval nobody was asked for is not an approval")

    problem = expect("T4", kernel_duplicate_after_approval.title,
                     lambda: adapter.invoke(request), DuplicateEffect)
    if problem:
        return problem
    if executor.calls != 1:
        return failed("T4", kernel_duplicate_after_approval.title,
                      f"the second attempt reached the executor: {executor.calls} calls")
    return passed("T4", kernel_duplicate_after_approval.title)


@case("T6", "an unknown action fails closed")
def kernel_unknown_action(adapter: ConformanceAdapter) -> CaseResult:
    world = World(adapter, EMPTY)
    executor = Executor()
    request = CallRequest(world.control, REFUND, {"payment_id": "t6", "amount": 1},
                          "refund:t6", executor)

    problem = expect("T6", kernel_unknown_action.title, lambda: adapter.invoke(request),
                     ActionDenied, "unknown_action")
    if problem:
        return problem
    if executor.calls:
        return failed("T6", kernel_unknown_action.title,
                      f"a denied action reached the executor {executor.calls} times")
    if [receipt.result for receipt in world.receipts()] != ["denied"]:
        return failed("T6", kernel_unknown_action.title,
                      f"receipts are {[r.result for r in world.receipts()]}, expected ['denied']")
    return passed("T6", kernel_unknown_action.title)


@case("T8", "NotExecuted permits a retry, and nothing else does")
def kernel_failed_permits_retry(adapter: ConformanceAdapter) -> CaseResult:
    world = World(adapter, ALLOW)
    executor = Executor(NotExecuted("the remote rejected it before acting"), None)
    request = CallRequest(world.control, REFUND, {"payment_id": "t8", "amount": 2000},
                          "refund:t8", executor)

    problem = expect("T8", kernel_failed_permits_retry.title,
                     lambda: adapter.invoke(request), NotExecuted)
    if problem:
        return problem

    try:
        adapter.invoke(request)
    except BaseException as refused:
        return failed("T8", kernel_failed_permits_retry.title,
                      f"a FAILED effect refused the retry with {type(refused).__name__}: {refused}")
    if executor.calls != 2:
        return failed("T8", kernel_failed_permits_retry.title,
                      f"the executor ran {executor.calls} times, expected 2")
    record = world.store.get_effect("refund:t8")
    if record is None or record.attempt != 2:
        return failed("T8", kernel_failed_permits_retry.title,
                      f"attempt is {record and record.attempt}, expected 2")
    return passed("T8", kernel_failed_permits_retry.title)


@case("T5", "an approval answered after its expiry authorizes nothing")
def kernel_expired_approval(adapter: ConformanceAdapter) -> CaseResult:
    """`v0.1 §7` T5 driven through an adapter, which an earlier draft of §12.5 said was not
    reachable through a single `invoke`. It is: the counting proxy the kit already wraps around
    the adapter's primitive is the hook, and moving the kit's clock inside it reproduces
    SPEC-v0.5 §2.4 step 5 exactly -- a human who deliberated past the request's own expiry.

    It is an adapter case and not only a provider one, because what it asks is whether the
    adapter lets `ApprovalTimeout` propagate or swallows it the way `swallows-denial` swallows
    `ActionDenied`. An adapter that returned a value here would have executed nothing and told
    its framework the refund went through.
    """
    world = World(adapter, APPROVE)
    executor = Executor()
    arguments = {"payment_id": "t5", "amount": 2000}
    request = CallRequest(world.control, REFUND, arguments, "refund:t5", executor,
                          answer=granted(arguments))
    world.watched.before = lambda: world.clock.advance(timedelta(hours=1))

    problem = expect("T5", kernel_expired_approval.title, lambda: adapter.invoke(request),
                     ApprovalTimeout)
    if problem:
        return problem
    if world.watched.calls != 1:
        return failed("T5", kernel_expired_approval.title,
                      f"the framework's primitive was reached {world.watched.calls} times, "
                      "expected 1")
    if executor.calls:
        return failed("T5", kernel_expired_approval.title,
                      f"an expired approval reached the executor {executor.calls} times")
    if not world.pending():
        return failed("T5", kernel_expired_approval.title,
                      "the request was not left pending: an answer that arrived too late must "
                      "not move the record a human could still be shown")
    return passed("T5", kernel_expired_approval.title)


@case("A1", "an allowed action is never put to a human")
def kernel_no_interrupt_on_allow(adapter: ConformanceAdapter) -> CaseResult:
    """`request.answer is None` means no human is expected. The positive control for every
    approval case: without it, an adapter that interrupts on everything passes them all."""
    world = World(adapter, ALLOW)
    executor = Executor()
    request = CallRequest(world.control, REFUND, {"payment_id": "a1", "amount": 2000},
                          "refund:a1", executor)

    try:
        adapter.invoke(request)
    except BaseException as raised:
        return failed("A1", kernel_no_interrupt_on_allow.title,
                      f"an allowed action raised {type(raised).__name__}: {raised}")
    if executor.calls != 1:
        return failed("A1", kernel_no_interrupt_on_allow.title,
                      f"the executor ran {executor.calls} times, expected 1")
    if "APPROVAL_REQUESTED" in world.events() or world.watched.calls:
        return failed("A1", kernel_no_interrupt_on_allow.title,
                      f"the adapter put an action the policy allowed outright to a human "
                      f"({world.watched.calls} interrupts, "
                      f"{world.events().count('APPROVAL_REQUESTED')} requests). A human asked "
                      "about something nobody needed to approve is a human who stops reading "
                      "the queue")
    return passed("A1", kernel_no_interrupt_on_allow.title)


# --- binding (SPEC-v0.5 §3.4) -----------------------------------------------------------


@case("B1", "an answer given against different arguments authorizes nothing")
def binding_mutated_answer(adapter: ConformanceAdapter) -> CaseResult:
    if not adapter.interrupt.carries_approved_arguments:
        return na(
            "B1",
            binding_mutated_answer.title,
            f"{adapter.framework} declares carries_approved_arguments=False: its resumption "
            "carries nothing the adapter can inspect, so the binding across the interrupt is "
            "the framework's checkpoint and not CTRLRun's hash. Attribution, not prevention",
        )
    world = World(adapter, APPROVE)
    executor = Executor()
    request = CallRequest(
        world.control, REFUND, {"payment_id": "b1", "amount": 2000}, "refund:b1", executor,
        # The human answered about 500. The action proposes 2000.
        answer=granted({"payment_id": "b1", "amount": 500}),
    )

    problem = expect("B1", binding_mutated_answer.title, lambda: adapter.invoke(request),
                     ApprovalMismatch, "mismatch")
    if problem:
        return problem
    if executor.calls:
        return failed("B1", binding_mutated_answer.title,
                      f"a mutated answer reached the executor {executor.calls} times")
    if not world.pending():
        return failed("B1", binding_mutated_answer.title,
                      "the refused request was not left pending: a mutated answer must leave the "
                      "approval the human actually saw still grantable")
    return passed("B1", binding_mutated_answer.title)


@case("B2", "an answer given against the proposal does authorize it")
def binding_matching_answer(adapter: ConformanceAdapter) -> CaseResult:
    """B1's control. Without it, B1 passes against an adapter that refuses every answer."""
    world = World(adapter, APPROVE)
    executor = Executor()
    arguments = {"payment_id": "b2", "amount": 2000}
    request = CallRequest(world.control, REFUND, arguments, "refund:b2", executor,
                          answer=granted(arguments))

    try:
        adapter.invoke(request)
    except BaseException as raised:
        return failed("B2", binding_matching_answer.title,
                      f"a matching answer was refused with {type(raised).__name__}: {raised}")
    if executor.calls != 1:
        return failed("B2", binding_matching_answer.title,
                      f"the executor ran {executor.calls} times, expected 1")
    if world.watched.calls != 1:
        return failed("B2", binding_matching_answer.title,
                      f"the framework's primitive was reached {world.watched.calls} times, "
                      "expected 1: an adapter that granted for itself never asked anybody")
    approvers = [receipt.approver for receipt in world.receipts()]
    if approvers != [APPROVER]:
        return failed("B2", binding_matching_answer.title,
                      f"the receipt names {approvers}, expected [{APPROVER!r}]")
    return passed("B2", binding_matching_answer.title)


@case("B3", "a refused answer is a denial and not an error")
def binding_denial(adapter: ConformanceAdapter) -> CaseResult:
    world = World(adapter, APPROVE)
    executor = Executor()
    request = CallRequest(
        world.control, REFUND, {"payment_id": "b3", "amount": 2000}, "refund:b3", executor,
        answer=ApprovalAnswer(granted=False, approver=APPROVER),
    )

    problem = expect("B3", binding_denial.title, lambda: adapter.invoke(request), ActionDenied)
    if problem:
        return problem
    if executor.calls:
        return failed("B3", binding_denial.title,
                      f"a denied action reached the executor {executor.calls} times")
    if world.watched.calls != 1:
        return failed("B3", binding_denial.title,
                      f"the framework's primitive was reached {world.watched.calls} times, "
                      "expected 1")
    if "APPROVAL_DENIED" not in world.events():
        return failed("B3", binding_denial.title,
                      "no APPROVAL_DENIED: a human's no is an answer, and it belongs in the log")
    return passed("B3", binding_denial.title)


# --- duplicate (v0.1 §7 T3, in one process) ---------------------------------------------


@case("D1", "two attempts on one effect key, one commit")
def duplicate_one_commit(adapter: ConformanceAdapter) -> CaseResult:
    """**Not** `v0.1 §7` T3. That one uses `multiprocessing` and is a statement about SQLite's
    `BEGIN IMMEDIATE` across OS processes; a framework runtime driven across spawned processes
    measures the framework's process model and not the adapter. What this catches is an adapter
    that cached a decision, reused a request id, or memoized a receipt."""
    world = World(adapter, ALLOW)
    executor = Executor()
    first = CallRequest(world.control, REFUND, {"payment_id": "d1", "amount": 2000},
                        "refund:d1", executor)
    second = CallRequest(world.control, REFUND, {"payment_id": "d1", "amount": 2000},
                         "refund:d1", executor)

    adapter.invoke(first)
    problem = expect("D1", duplicate_one_commit.title, lambda: adapter.invoke(second),
                     DuplicateEffect)
    if problem:
        return problem
    if executor.calls != 1:
        return failed("D1", duplicate_one_commit.title,
                      f"the executor ran {executor.calls} times, expected 1")
    committed = [receipt for receipt in world.receipts() if receipt.result == "committed"]
    if len(committed) != 1:
        return failed("D1", duplicate_one_commit.title,
                      f"{len(committed)} committed receipts, expected 1")
    return passed("D1", duplicate_one_commit.title)


# --- authority (v0.3 §10) ---------------------------------------------------------------


@case("T66", "no grant, no action")
def authority_no_grant(adapter: ConformanceAdapter) -> CaseResult:
    world = World(adapter, NO_GRANT)
    executor = Executor()
    request = CallRequest(world.control, REFUND, {"payment_id": "a66", "amount": 2000},
                          "refund:a66", executor)

    problem = expect("T66", authority_no_grant.title, lambda: adapter.invoke(request),
                     AuthorityDenied, "no_authority")
    if problem:
        return problem
    if executor.calls:
        return failed("T66", authority_no_grant.title,
                      f"an unauthorized action reached the executor {executor.calls} times")
    if "POLICY_EVALUATED" in world.events():
        return failed("T66", authority_no_grant.title,
                      "POLICY_EVALUATED after an authority denial: authority runs first and a "
                      "denial there skips policy (v0.3 §4.3)")
    return passed("T66", authority_no_grant.title)


@case("T70", "an expired grant is not a grant")
def authority_expired(adapter: ConformanceAdapter) -> CaseResult:
    world = World(adapter, EXPIRED_GRANT)
    executor = Executor()
    request = CallRequest(world.control, REFUND, {"payment_id": "a70", "amount": 2000},
                          "refund:a70", executor)

    problem = expect("T70", authority_expired.title, lambda: adapter.invoke(request),
                     AuthorityDenied, "authority_expired")
    if problem:
        return problem
    if executor.calls:
        return failed("T70", authority_expired.title,
                      f"an expired grant reached the executor {executor.calls} times")
    return passed("T70", authority_expired.title)


@case("T74", "an authority denial leaves no pending approval")
def authority_leaves_no_request(adapter: ConformanceAdapter) -> CaseResult:
    world = World(adapter, APPROVE_WITHOUT_AUTHORITY)
    executor = Executor()
    request = CallRequest(world.control, REFUND, {"payment_id": "a74", "amount": 2000},
                          "refund:a74", executor, answer=granted({"payment_id": "a74",
                                                                  "amount": 2000}))

    problem = expect("T74", authority_leaves_no_request.title, lambda: adapter.invoke(request),
                     AuthorityDenied)
    if problem:
        return problem
    if world.pending():
        return failed("T74", authority_leaves_no_request.title,
                      "a human was left a request for an action that could never run")
    if "APPROVAL_REQUESTED" in world.events():
        return failed("T74", authority_leaves_no_request.title,
                      "APPROVAL_REQUESTED after an authority denial")
    return passed("T74", authority_leaves_no_request.title)


# --- identity (v0.3 §10) ----------------------------------------------------------------


@case("T63", "the provider's principal wins, whatever the framework's session says")
def identity_provider_wins(adapter: ConformanceAdapter) -> CaseResult:
    world = World(adapter, ALLOW, principal="the-provider-said-this")
    executor = Executor()
    request = CallRequest(world.control, REFUND, {"payment_id": "i63", "amount": 2000},
                          "refund:i63", executor)

    adapter.invoke(request)
    agents = [receipt.principal.agent for receipt in world.receipts()]
    if agents != ["the-provider-said-this"]:
        return failed("T63", identity_provider_wins.title,
                      f"receipts name {agents}, expected ['the-provider-said-this']: an adapter "
                      "sees the principal and never supplies one (SPEC-v0.5 §4.2)")
    return passed("T63", identity_provider_wins.title)


@case("T60", "no principal, no action")
def identity_no_principal(adapter: ConformanceAdapter) -> CaseResult:
    world = World(adapter, ALLOW)
    # A Control with no identity provider and no ambient context() has nobody to attribute an
    # action to. v0.1 §2.1: that is a wiring bug, not an agent action.
    bare = Control(
        world.control.policy,
        world.store,
        InterruptApprovalProvider(world.store, world.watched, clock=world.clock),
        clock=world.clock,
        environment="production",
    )
    executor = Executor()
    request = CallRequest(bare, REFUND, {"payment_id": "i60", "amount": 2000},
                          "refund:i60", executor)

    problem = expect("T60", identity_no_principal.title, lambda: adapter.invoke(request),
                     ActionDenied, "no_principal")
    if problem:
        return problem
    if executor.calls:
        return failed("T60", identity_no_principal.title,
                      f"an unattributed action reached the executor {executor.calls} times")
    return passed("T60", identity_no_principal.title)


#: The suites, in report order. A name here is a name in an adapter's README (§7 item 7).
SUITES: Mapping[str, tuple[Case, ...]] = {
    #: `v0.1 §7` T5 -- an approval answered after its expiry -- is **not** here, and the
    #: absence is deliberate rather than an omission. Through `@protect(wait=True)` the request
    #: is created and consumed inside one `invoke`, so there is no interval for it to expire in
    #: that the kit can reach without a hook into the adapter's own primitive. Expiry is
    #: exercised where it lives: at the provider (SPEC-v0.5 T129d, both halves) and in the
    #: kernel (`v0.1 §7` T5). Same treatment T2 and T3 get in §5.3.
    "kernel": (kernel_lost_response, kernel_duplicate_after_approval, kernel_unknown_action,
               kernel_failed_permits_retry, kernel_expired_approval,
               kernel_no_interrupt_on_allow, binding_matching_answer, binding_denial),
    #: `binding` is the mutation check **alone**, and that is why it is its own suite. Its
    #: control (B2) and the denial case (B3) live in `kernel`, because both run whatever the
    #: framework carries back -- and a suite containing them would report `pass` for an adapter
    #: that cannot perform the one check the suite is named for. That is `8/8` with an N/A
    #: folded in, one level down.
    "binding": (binding_mutated_answer,),
    "duplicate": (duplicate_one_commit,),
    "authority": (authority_no_grant, authority_expired, authority_leaves_no_request),
    "identity": (identity_provider_wins, identity_no_principal),
}


def run(adapter: ConformanceAdapter, deployment: Control | None = None) -> ConformanceReport:
    """Drive every suite through `adapter` and report what each came to (SPEC-v0.5 §5).

    `deployment` is the operator's `Control`, where the kit is being run inside one. **The
    suites do not run against it.** They run against scratch `Control`s the kit builds, for the
    reason `v0.4 §3.5` gives about verify: a kit that reserved a real effect key would be a
    defect of exactly the class it exists to find. The one thing read from it is the mode, and
    an observing one is **refused** -- no suites, no denominator, `ok` is `False` -- because
    running the suites in a synthetic enforce mode would report guarantees about a configuration
    nobody deployed, and running them as-is would report a wall of failures that are true and
    useless (§3.6).

    A case that raises out of its own body is reported as a failed case naming the exception,
    never as a crashed run: one broken suite must not be able to hide the eight that worked.
    """
    if deployment is not None and deployment.policy.mode == OBSERVE:
        return ConformanceReport(
            framework=adapter.framework,
            status="refused",
            reason="mode: observe - nothing is enforced, so these suites would report "
            "guarantees about a configuration nobody deployed (SPEC-v0.5 §3.6)",
        )

    own = unwrapped(adapter.interrupt)
    results = []
    try:
        for name, cases in SUITES.items():
            outcomes = []
            for item in cases:
                try:
                    outcomes.append(item.body(adapter))
                except BaseException as broke:
                    outcomes.append(
                        failed(item.id, item.title,
                               f"the case raised {type(broke).__name__}: {broke}")
                    )
            results.append(SuiteResult.of(name, tuple(outcomes)))
    finally:
        # Each `World` swaps the counting proxy in for the run; the kit put it there, so the
        # kit takes it out, whatever a case did on the way through. Leaving a test double
        # bolted to an object the caller still holds is how a kit changes what it measured.
        adapter.interrupt = own
    return ConformanceReport(framework=adapter.framework, suites=tuple(results))


def principal_named(agent: str) -> Principal:
    """A principal an adapter must never build. Exported for `fixtures.SelfAssertsPrincipal`,
    which is a **broken** adapter, and for nothing else."""
    return Principal(agent=agent)
