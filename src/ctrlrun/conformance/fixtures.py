"""Adapters broken in one named way each, and one that is not. SPEC-v0.5 §5.4.

**A kit that only ever passes is a kit nothing exercises.** These exist so the kit's own tests
can require each fixture to fail the suite named for it -- which is `v0.4 §1.3`'s
positive-control rule one level up, and the reason item 3 comes before the reference adapters.
"Two adapters pass the suites" means nothing until the suite can fail.

`Reference` is the pair to all of them: no framework at all, just the surface, correct. Without
it, "the kit fails a broken adapter" is satisfied by a kit that fails everything.

None of these is a framework adapter. They implement `ConformanceAdapter` directly, in process,
so that what they break is the *contract* and never a framework's behaviour -- which is
`v0.4 §7.3` rule 6's line, held from the other side: the kit reports on adapters and the probe
reports on frameworks, and neither is allowed to editorialize about the other's subject.
"""

from __future__ import annotations

import contextlib
from typing import Any

from ..action import Action
from ..adapter import ApprovalAnswer, PendingApproval
from ..control import protect, with_approval
from ..errors import ActionDenied, ApprovalRequired, AuthorityDenied, NotExecuted
from .suites import CallRequest


class Courier:
    """The `FrameworkInterrupt` a correct in-process adapter uses.

    It stands in for a framework's primitive and does the one thing the contract asks of one:
    carry the pending approval out and the human's answer back, unchanged. The kit's answer is
    the ground truth, and this hands it over untouched.
    """

    framework = "reference"

    def __init__(self, *, carries: bool = True) -> None:
        self.carries_approved_arguments = carries
        self.answer: ApprovalAnswer | None = None
        self.calls: list[PendingApproval] = []

    def interrupt(self, pending: PendingApproval) -> ApprovalAnswer:
        self.calls.append(pending)
        if self.answer is None:
            # `request.answer is None` means no human was expected. An adapter whose framework
            # interrupted anyway has asked about something the policy allowed outright, and the
            # kit's A1 case is what notices; raising here rather than inventing an answer keeps
            # SPEC-v0.5 §2.4's "a framework error is never an answer" true of the double too.
            raise AssertionError("interrupted when no human was expected")
        return self.answer


class Reference:
    """A correct adapter with no framework behind it. The pair to every fixture below."""

    def __init__(self, *, carries: bool = True, framework: str = "reference") -> None:
        self.framework = framework
        self.interrupt = Courier(carries=carries)
        self.interrupt.framework = framework

    def invoke(self, request: CallRequest) -> Any:
        self.interrupt.answer = request.answer
        return self._call(request)

    def _call(self, request: CallRequest) -> Any:
        """The whole of a correct in-process adapter: `@protect(wait=True)`, and nothing else.

        `wait=True` is what routes an `APPROVE` through the provider -- and therefore through
        the framework's primitive -- instead of raising `ApprovalRequired` past it. That single
        keyword is the entire difference an adapter makes (SPEC-v0.5 §1.1).
        """
        return _protected(request)(**request.arguments)


def _protected(request: CallRequest, *, wait: bool = True) -> Any:
    """The kit's executor, wrapped in `@protect` under the call's own parameter names.

    `@protect` refuses `*args` and `**kwargs` (`v0.1 §8`) -- an Action's arguments are a mapping
    of *named* values and a variadic parameter has no name a policy rule could address -- so the
    tool is built with a signature matching this call. A framework registers its tools once and
    this stands in for that registration; building it per call is the only difference, and it is
    the kit's, not the contract's.
    """
    parameters = ", ".join(request.arguments)
    forwarded = ", ".join(f"{name}={name}" for name in request.arguments)
    namespace: dict[str, Any] = {"_executor": request.executor}
    exec(
        f"def tool({parameters}):\n    return _executor({forwarded})", namespace
    )
    return protect(
        request.action,
        effect=request.effect,
        resource="payment:{payment_id}",
        wait=wait,
        control=request.control,
    )(namespace["tool"])


class NeverExecutes(Reference):
    """Returns a plausible value without calling the kit's executor.

    The fixture every refusal case needs, because "the second attempt was refused" is satisfied
    just as well by an adapter that never ran anything at all.
    """

    def invoke(self, request: CallRequest) -> Any:
        return "committed"


class SwallowsNotExecuted(Reference):
    """Catches `NotExecuted` and returns. The whole of `v0.1 §5.5`'s asymmetry, undone: the one
    exception an agent may read as permission to retry stops reaching it."""

    def invoke(self, request: CallRequest) -> Any:
        try:
            return super().invoke(request)
        except NotExecuted:
            return None


class SwallowsDenial(Reference):
    """Catches `ActionDenied` and returns a value. A policy that said no, reported as a yes."""

    def invoke(self, request: CallRequest) -> Any:
        try:
            return super().invoke(request)
        except ActionDenied:
            return "committed"


class FrameworkRejectedError(Exception):
    """A framework's own "the user rejected this" error, for `DenialIsAnError` to raise."""


class _RaisesOnRefusal(Courier):
    """A `Courier` that carries a grant back and raises on a refusal."""

    def interrupt(self, pending: PendingApproval) -> ApprovalAnswer:
        answer = super().interrupt(pending)
        if not answer.granted:
            raise FrameworkRejectedError("the user rejected this tool call")
        return answer


class DenialIsAnError(Reference):
    """Raises its framework's rejection error instead of carrying the human's `no` back.

    The `denial` suite's own fixture, and it is aimed rather than incidental. `SwallowsDenial`
    would fail `denial` too -- it catches the `ActionDenied` that a refusal produces -- but it
    is aimed at `kernel`, where it swallows T6's denial of an unknown action, and §5.4's rule
    is that a suite whose only fixture fails it by accident is a suite nothing is aimed at.
    That is why `IgnoresAuthority` exists beside it, and this is the same argument one suite
    further along.

    The mistake is an authentic one, and it is the opposite of `SwallowsDenial`'s: not a no
    reported as a yes, but a no reported as a *fault*. An author whose framework raises when a
    human declines -- most do -- lets that exception stand as the outcome, reasoning that the
    call did not happen and the framework already said why. It did not happen, and CTRLRun's
    evidence log is where that has to be visible: `APPROVAL_DENIED` then `ACTION_DENIED`
    (`v0.1 §6.2`, SPEC-v0.5 §2.4). Under this adapter a human's answer is indistinguishable
    from the primitive having crashed -- §10's row for that says the request stays `pending`
    with no grant, no denial and no receipt, which is right for a crash and a silent loss of
    the record for an answer.

    It fails B3 on the **first** of that case's two checks -- what propagates is not
    `ActionDenied` -- and `DeniesForItself` is the pair that fails it on the second, because
    `expect` returns as soon as one is unmet and a single fixture therefore exercises exactly
    one. Two checks with one fixture between them is a subsumed guard: remove either and the
    other still fails the suite, and a test asserting only that the suite failed cannot tell
    which ran.

    It reaches the primitive, which is the point. The count cannot tell this from a correct
    adapter -- a human *was* asked and did answer -- so only the case's own checks can.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.interrupt = _RaisesOnRefusal(carries=self.interrupt.carries_approved_arguments)
        self.interrupt.framework = self.framework


class _DeniesByRaising(Courier):
    """A `Courier` that translates a refusal into `ActionDenied` itself."""

    def interrupt(self, pending: PendingApproval) -> ApprovalAnswer:
        answer = super().interrupt(pending)
        if not answer.granted:
            # The reason it picks is its own guess, and that is part of the mistake: the
            # kernel's would have come from the record `deny_approval` wrote.
            raise ActionDenied("the user rejected this tool call", reason="approval_denied")
        return answer


class DeniesForItself(Reference):
    """Raises `ActionDenied` itself instead of returning the human's `no` to the provider.

    `GrantsForItself` at the other end of the answer. That one writes the grant and never asks;
    this one asks, gets an answer, and then writes the *consequence* of the answer rather than
    handing the answer over -- which §2.4 forbids in one sentence covering both: an adapter
    returns an `ApprovalAnswer` and `InterruptApprovalProvider` records it. An author writes
    this because the caller sees exactly what a correct adapter produces: `ActionDenied`, no
    execution, the primitive reached once. Every check but one is satisfied.

    The one is the evidence. `deny_approval` was never called, so the request is still `pending`
    and the log has no `APPROVAL_DENIED` -- a human said no and CTRLRun cannot show it. That is
    B3's second check, and this fixture is the only thing that reaches it: `DenialIsAnError`
    fails the first and `expect` returns there.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.interrupt = _DeniesByRaising(carries=self.interrupt.carries_approved_arguments)
        self.interrupt.framework = self.framework


class ReplaysApproval(Reference):
    """Keeps a `request_id` and re-presents it on the next call, reaching past `@protect`.

    An author who thought they had to carry the approval themselves writes this. It is a second
    approval path in miniature: the id is minted by the kernel and then held by the adapter.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._held: str | None = None

    def invoke(self, request: CallRequest) -> Any:
        if self._held is not None:
            with with_approval(self._held):
                return super().invoke(request)
        result = super().invoke(request)
        if self.interrupt.calls:
            self._held = self.interrupt.calls[-1].request_id
        return result


class SelfAssertsPrincipal(Reference):
    """Builds a `Principal` from its framework's session and reaches a `Control` with it.

    `--principal-from-client-info` (`v0.3 §8.1`) in an adapter's clothes, which is the third
    place it has turned up. §4.2 forbids the surface from having a name that takes one; this is
    what an author does anyway, and the identity suite is what catches it.
    """

    SESSION = "whoever-the-framework-said"

    def invoke(self, request: CallRequest) -> Any:
        from .suites import principal_named

        action = Action(
            name=request.action,
            arguments=request.arguments,
            principal=principal_named(self.SESSION),
            resource=f"payment:{request.arguments.get('payment_id')}",
            environment=request.control.environment,
        )
        return request.control.execute(
            action, lambda: request.executor(**request.arguments), request.effect
        )


class IgnoresAuthority(Reference):
    """Catches `AuthorityDenied` and returns a value.

    Written by an author who read `AuthorityDenied(ActionDenied)` and concluded that authority
    is advisory. It is not: a denial there is the action being denied, and it happens *before*
    policy runs and before any human is asked (`v0.3 §4.3`). Distinct from `SwallowsDenial`
    because the two catch different exceptions for different reasons, and a suite whose only
    fixture failed it incidentally is a suite nothing is aimed at.
    """

    def invoke(self, request: CallRequest) -> Any:
        try:
            return super().invoke(request)
        except AuthorityDenied:
            return "committed"


class InterruptsOnAllow(Reference):
    """Routes every call through the interrupt, `APPROVE` or not: a human asked about an action
    the policy allowed outright is a human who stops reading the queue."""

    def invoke(self, request: CallRequest) -> Any:
        self.interrupt.answer = request.answer
        # A real framework's primitive does not raise when nobody was expected: it prompts,
        # somebody answers, and the tool runs. So the kit double's complaint is suppressed and
        # this carries on -- because a fixture that failed *because the double raised* would be
        # caught by the double rather than by the kit, and an adapter with a real primitive
        # would sail through. The interrupt **count** is what catches this, and suppressing
        # here is what makes the count the only thing that can.
        with contextlib.suppress(AssertionError):
            self.interrupt.interrupt(_invented(request))
        return self._call(request)


def _invented(request: CallRequest) -> PendingApproval:
    """A pending approval this adapter made up, to put in front of somebody who was not asked
    about anything: the shape of an adapter that routes every call through its framework's
    approval UI whether the policy asked for one or not."""
    now = request.control._clock()
    return PendingApproval(
        request_id="apr_invented", action_id="act_invented", action=request.action,
        action_hash="sha256:invented", arguments=dict(request.arguments), resource=None,
        environment=request.control.environment, agent="?", user=None,
        created_at=now, expires_at=now,
    )


class InterruptsOnlyWhenUnasked(Reference):
    """Correct on every call the policy sends to a human, and wrong on exactly the ones it does
    not: it puts an `ALLOW` through its framework's approval UI and nothing else.

    It exists because a mutation table found A1's interrupt count **subsumed**.
    `InterruptsOnAllow` interrupts on every call, so it bumps the count on T4, B2 and B3 as
    well, and deleting A1's check left the kit still failing that adapter -- for a reason that
    has nothing to do with the case A1 is. This one is invisible to every other case, so A1's
    count is the only thing that can catch it, which is what makes A1 a check rather than a
    restatement.
    """

    def invoke(self, request: CallRequest) -> Any:
        self.interrupt.answer = request.answer
        if request.answer is None:
            with contextlib.suppress(AssertionError):
                self.interrupt.interrupt(_invented(request))
        return self._call(request)


class GrantsForItself(Reference):
    """Writes the grant rather than returning an answer.

    The fixture for the document's **second rule**. "Never a second approval path" is the
    sentence a security reviewer reads first, and until this existed it was the one claim in
    SPEC-v0.5 with no broken adapter behind it.
    """

    def invoke(self, request: CallRequest) -> Any:
        self.interrupt.answer = request.answer
        try:
            return _unattended(request)(**request.arguments)
        except ApprovalRequired as pending:
            # The whole defect, in three lines: the adapter decides the human said yes, writes
            # the grant with `ctrlrun approve`'s own call, and re-presents. The framework's
            # primitive is never reached, so nobody was ever asked -- and every refusal the kit
            # asserts is still refused, because the *kernel* is intact. Only the interrupt count
            # tells them apart.
            request.control.store.grant_approval(pending.request_id, "the-adapter")
            with with_approval(pending.request_id):
                return _unattended(request)(**request.arguments)


class EchoesThePayload(Reference):
    """Declares `carries_approved_arguments = True` and hands back `pending.arguments`.

    The fixture for the document's **mutation check**. Handing back what you were just given
    makes every comparison trivially pass: it is manufacturing the check, and it is the one way
    to turn attribution into a lie about prevention. Until this existed, §3.4's claim had no
    broken adapter behind it either.
    """

    def invoke(self, request: CallRequest) -> Any:
        self.interrupt.answer = request.answer
        self.interrupt.__class__ = _EchoingCourier
        return self._call(request)


class _EchoingCourier(Courier):
    def interrupt(self, pending: PendingApproval) -> ApprovalAnswer:
        self.calls.append(pending)
        if self.answer is None:
            raise AssertionError("interrupted when no human was expected")
        return ApprovalAnswer(
            granted=self.answer.granted,
            approver=self.answer.approver,
            approved_arguments=dict(pending.arguments),
        )


class CachesTheDecision(Reference):
    """Memoizes the first call's result and returns it for the second, so a duplicate effect
    never reaches the kernel that would have refused it."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._seen: dict[str, Any] = {}

    def invoke(self, request: CallRequest) -> Any:
        key = f"{request.action}:{request.effect}"
        if key in self._seen:
            return self._seen[key]
        result = super().invoke(request)
        self._seen[key] = result
        return result


#: Every fixture, and the suite each must fail (§5.4). Read by the kit's own tests, which is
#: why it lives here rather than in them: a fixture added without a suite to fail is a fixture
#: that proves nothing, and this mapping is what makes that a type error rather than an
#: oversight.
def _unattended(request: CallRequest) -> Any:
    """`_protected`, but with `wait=False`: the adapter catches `ApprovalRequired` itself."""
    return _protected(request, wait=False)


BROKEN: dict[str, tuple[type[Reference], str]] = {
    "never-executes": (NeverExecutes, "kernel"),
    "swallows-not-executed": (SwallowsNotExecuted, "kernel"),
    "swallows-denial": (SwallowsDenial, "kernel"),
    "replays-approval": (ReplaysApproval, "kernel"),
    "interrupts-on-allow": (InterruptsOnAllow, "kernel"),
    "interrupts-only-when-unasked": (InterruptsOnlyWhenUnasked, "kernel"),
    "grants-for-itself": (GrantsForItself, "kernel"),
    "denial-as-error": (DenialIsAnError, "denial"),
    "denies-for-itself": (DeniesForItself, "denial"),
    "ignores-authority": (IgnoresAuthority, "authority"),
    "self-asserts-principal": (SelfAssertsPrincipal, "identity"),
    "echoes-the-payload": (EchoesThePayload, "binding"),
    "caches-the-decision": (CachesTheDecision, "duplicate"),
}
