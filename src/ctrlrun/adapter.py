"""The adapter surface. Build-list item 2; SPEC-v0.5 §2, §3.

An adapter exists for exactly one reason: **to route an `APPROVE` through a framework's own
interrupt instead of raising past it**. `@protect` already covers anything running in this
process and the gateway covers anything reaching its tools over MCP, so a framework with no
human-in-the-loop primitive of its own has nothing for an adapter to reuse and does not need
one.

Three things make that one reason safe to hand to somebody else.

**A Protocol, not a base class.** A base class is a channel for the kernel to change an
adapter's behaviour later without its author's consent: a method added with a default runs in
every adapter that inherited it, at the next upgrade, in a distribution the kernel does not
version. A Protocol cannot do that, and this is one of the six contracts v1.0 freezes.

**One place the grant is written, and it is not the adapter.** `InterruptApprovalProvider` calls
the same `grant_approval`/`deny_approval` that `ctrlrun approve` and the webhook call. An
adapter returns an answer; it never records one. That is what makes "never a second approval
path" structural rather than advisory -- no adapter can grow one by accident, by copy-paste, or
by an author who read the rule and forgot it.

**The principal is seen and never supplied.** `needs_approval` is here, in core, rather than in
each adapter, because an adapter that built an `Action` for its framework's pre-invocation
predicate would need a `Principal` -- and a self-asserted one is `--principal-from-client-info`,
which SPEC-v0.3 §8.1 removed from the gateway by name and §8.4 then removed again from the ACS
hook. It reached this document's first draft as well, and an independent review found it; SPEC-v0.5
§9.1 is the record.

This module imports `action.py` and `approval.py` and nothing else from the kernel. `Control`
arrives as a *parameter*, never as a module-scope import, so `adapter.py` stays below
`control.py` in the dependency order of `ARCHITECTURE.md` §6 and `Control` remains the only
module that composes the others. It appends no event and owns no sink.
"""

from __future__ import annotations

import logging
import weakref
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from .action import Action
from .approval import (
    DEFAULT_APPROVAL_TTL,
    HASH_MISMATCH,
    UNKNOWN_APPROVAL,
    Approval,
    ApprovalRecord,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalStore,
    build_request,
)
from .effect import resolve_resource
from .errors import ApprovalMismatch, ApprovalTimeout, InvalidArgument
from .policy import OBSERVE, Decision

if TYPE_CHECKING:  # pragma: no cover - a type-checking-only edge, asserted by T129
    from .control import Control

__all__ = [
    "ApprovalAnswer",
    "FrameworkInterrupt",
    "InterruptApprovalProvider",
    "PendingApproval",
    "banner",
    "needs_approval",
]

_LOG = logging.getLogger("ctrlrun")

#: SPEC-v0.3 §6.5's wording, in one place, so no two adapters say it differently.
OBSERVE_BANNER = "OBSERVE MODE - nothing is enforced"

#: The Controls `banner()` has already spoken for. Weak, so an adapter holding no reference to
#: a Control does not keep it alive, and keyed by identity because a Control is not hashable by
#: value and two deployments' Controls are two deployments.
_ANNOUNCED: weakref.WeakSet[Any] = weakref.WeakSet()


def _utc_now() -> datetime:
    from datetime import UTC

    return datetime.now(UTC)


@dataclass(frozen=True)
class PendingApproval:
    """What the framework's interrupt is handed, and the only thing it is (SPEC-v0.5 §2.3).

    A value: the action's name, its canonical arguments, its resource, its environment, the
    principal's `agent` and `user`, the `action_hash` and the request's expiry. It does **not**
    carry the `Action`, the `Control`, the store or the executor -- nothing an adapter could act
    on rather than display.

    `agent` and `user` are outputs. They are here so a human sees who is asking, and there is
    no constructor, keyword or callback anywhere on this surface that puts one back in (§4.2).
    """

    request_id: str
    action_id: str
    action: str
    action_hash: str
    arguments: Mapping[str, Any]
    resource: str | None
    environment: str
    agent: str
    user: str | None
    created_at: datetime
    expires_at: datetime

    @classmethod
    def of(cls, record: ApprovalRecord) -> PendingApproval:
        """The pending approval a stored request stands for."""
        action = record.request.action
        return cls(
            request_id=record.approval_id,
            action_id=action.action_id,
            action=action.name,
            action_hash=record.action_hash,
            arguments=action.canonical_arguments,
            resource=action.resource,
            environment=action.environment,
            agent=action.principal.agent,
            user=action.principal.user,
            created_at=record.request.created_at,
            expires_at=record.expires_at,
        )

    def to_dict(self) -> dict[str, Any]:
        """A JSON-safe view, for a framework that checkpoints the payload.

        `arguments` are the action's canonical arguments -- plain `dict`/`list` rebuilt from the
        canonical bytes -- and the two timestamps are ISO-8601 strings. A payload that would not
        serialize is one that works until the first restart.
        """
        return {
            "request_id": self.request_id,
            "action_id": self.action_id,
            "action": self.action,
            "action_hash": self.action_hash,
            "arguments": self.arguments,
            "resource": self.resource,
            "environment": self.environment,
            "agent": self.agent,
            "user": self.user,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }


@dataclass(frozen=True)
class ApprovalAnswer:
    """A human's answer, and who gave it (SPEC-v0.5 §2.2, §3.4).

    `approver` names a **channel** wherever the framework's primitive does not identify a
    person -- `"langgraph:interrupt"`, in the same register as v0.1's `"cli:local"`. It is what
    `grant_approval` writes on the record, and from there what reaches `APPROVAL_CONSUMED` and
    the receipt's `approver` field. Authenticating the approver is not in scope (SPEC-v0.3 §13),
    so no document may describe this field as *who* approved.

    `approved_arguments` are the arguments the answer was given against, as the adapter
    recovered them from its framework's own record of it. Required where the interrupt declares
    `carries_approved_arguments`; `None` only where it does not. **Never a copy of
    `PendingApproval.arguments`**: handing back what you were just given makes every comparison
    trivially pass, which is manufacturing the check.
    """

    granted: bool
    approver: str
    approved_arguments: Mapping[str, Any] | None = None


@runtime_checkable
class FrameworkInterrupt(Protocol):
    """One framework's human-in-the-loop primitive, and nothing else (SPEC-v0.5 §2.1).

    `interrupt()` **may exit non-locally**: LangGraph's raises, the graph catches it and
    checkpoints, and the resumed run re-enters here and returns. The provider never catches it.

    An `interrupt()` that raises anything else is that framework's failure and not a decision.
    Nothing is written and it propagates. There is no default, and in particular no default
    `granted=False`: a denial is a human's answer, and manufacturing one from a crashed
    interrupt puts a refusal in the evidence log that nobody made.
    """

    #: The framework's name, as it appears in a conformance report. Non-empty.
    framework: str

    #: Does this framework's resumption carry back what the human answered against (§3.4)?
    #:
    #: A **declaration about the framework**, not a setting. `True` makes the binding check
    #: mandatory, and an answer that omits `approved_arguments` is refused. `False` says the
    #: resumption carries nothing the adapter can inspect, so the binding across the interrupt
    #: is the framework's checkpoint rather than CTRLRun's -- attribution, not prevention -- and
    #: it is not free: the conformance kit reports `binding: not_applicable` with the adapter's
    #: reason, permanently, where a reviewer reads first. A flag hides a weakening; this
    #: publishes one.
    carries_approved_arguments: bool

    def interrupt(self, pending: PendingApproval) -> ApprovalAnswer: ...


class InterruptApprovalProvider:
    """An `ApprovalProvider` whose `wait()` routes through a framework's own primitive.

    That is the whole mechanism of SPEC-v0.5 §3: `@protect(wait=True)` raises `ApprovalRequired`,
    catches it, calls `wait()`, and re-presents the same proposal under `with_approval`. The
    reservation is taken only on that second pass, which is why an approval gate needs none of
    `Suspended`'s machinery -- a human deliberating for an hour pins nothing.

    **The operator constructs it**, on the line where they choose the policy, the store and the
    identity provider:

        control = Control(policy, store,
                          approvals=InterruptApprovalProvider(store, MyInterrupt()),
                          identity=..., authority=...)

    An adapter never constructs a `Control` (SPEC-v0.5 §2.3). Everything it must not choose --
    the identity provider, the authority document, the clock, the environment, the mode -- is
    chosen there, by the person who deployed it.
    """

    def __init__(
        self,
        store: ApprovalStore,
        interrupt: FrameworkInterrupt,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        _check_interrupt(interrupt)
        self._store = store
        self._interrupt = interrupt
        self._clock = clock

    @property
    def framework(self) -> str:
        return self._interrupt.framework

    @property
    def carries_approved_arguments(self) -> bool:
        return self._interrupt.carries_approved_arguments

    def request(self, action: Action, ttl: timedelta = DEFAULT_APPROVAL_TTL) -> ApprovalRequest:
        request = build_request(action, ttl, self._clock())
        self._store.put_approval_request(request)
        return request

    def wait(self, request_id: str, timeout: timedelta | None = None) -> Approval | None:
        """Put the request to a human through the framework, and record what they said.

        The six steps of SPEC-v0.5 §2.4, in order. The order of the last two is the whole of
        the fifth: `grant_approval` refuses an expired record on its own (v0.1 §4.2 A3), so
        writing first would reach the same decision -- and would leave the record `expired`
        rather than `pending`, and raise `ApprovalMismatch` rather than `ApprovalTimeout`.

        **No event is appended here, and none is claimed.** An `ApprovalProvider` is constructed
        with an `ApprovalStore` and has no sink; giving one to this module would make it a
        second thing fanning out evidence (`ARCHITECTURE.md` §6). So a refusal at step 4 raises,
        writes nothing, and leaves `APPROVAL_REQUESTED` standing as the only record that a human
        was asked. `WebhookApprovalProvider` is in exactly this position and appends nothing
        either.
        """
        record = self._store.get_approval(request_id)
        if record is None:
            raise ApprovalMismatch(
                f"no approval request {request_id}",
                reason=UNKNOWN_APPROVAL,
                approval_id=request_id,
            )
        if record.status is ApprovalStatus.GRANTED:
            return record.as_approval()
        if record.status is not ApprovalStatus.PENDING:
            # Denied, consumed or already expired: this request will never be granted, and
            # `None` means "answered, no" exactly as it does for every other provider (§4.3).
            return None

        deadline = self._deadline(record, timeout)
        if self._clock() > deadline:
            raise ApprovalTimeout(
                f"approval request {request_id} expired before it could be put to a human",
                request_id=request_id,
            )

        pending = PendingApproval.of(record)
        answer = self._interrupt.interrupt(pending)
        self._check_answer(answer, record, pending)

        if self._clock() > deadline:
            # The human deliberated past the request's expiry: they have answered a dead
            # request. Nothing is written, and the record is left `pending` rather than moved
            # to `expired` by a `grant_approval` that would have refused it anyway.
            raise ApprovalTimeout(
                f"approval request {request_id} was not answered in time",
                request_id=request_id,
            )

        if answer.granted:
            return self._store.grant_approval(request_id, answer.approver)
        self._store.deny_approval(request_id, answer.approver)
        return None

    def _deadline(self, record: ApprovalRecord, timeout: timedelta | None) -> datetime:
        """The request's own expiry, or `timeout` from now, whichever is sooner (§2.4).

        `@protect(wait=True)` passes `None`, so the request's expiry is the bound -- which is
        `LocalApprovalProvider`'s rule unchanged. Where `interrupt()` blocks, the provider
        cannot interrupt it: the framework owns that call, and the adapter's README says how its
        primitive is bounded.
        """
        if timeout is None:
            return record.expires_at
        return min(record.expires_at, self._clock() + timeout)

    def _check_answer(
        self, answer: ApprovalAnswer, record: ApprovalRecord, pending: PendingApproval
    ) -> None:
        """Step 4: the answer is well formed, and it was given against this proposal (§3.4)."""
        if not isinstance(answer, ApprovalAnswer):
            raise InvalidArgument(
                f"{self._interrupt.framework}: interrupt() must return an ApprovalAnswer, "
                f"got {type(answer).__name__}"
            )
        if not isinstance(answer.approver, str) or not answer.approver.strip():
            raise InvalidArgument(
                f"{self._interrupt.framework}: an ApprovalAnswer must name an approver; "
                "an approval with no approver is evidence that answers the wrong question"
            )
        if not self._interrupt.carries_approved_arguments:
            # SPEC-v0.5 §3.4's second bullet. The binding across the interrupt is the
            # framework's checkpoint, not this hash -- attribution, declared, and reported as
            # `binding: not_applicable` by the conformance kit rather than as a pass.
            return
        if answer.approved_arguments is None:
            raise ApprovalMismatch(
                f"{self._interrupt.framework} declares it carries the approved arguments and "
                f"the answer to {record.approval_id} omitted them",
                reason=HASH_MISMATCH,
                approval_id=record.approval_id,
            )
        # v0.1 §4.2 A1's comparison, applied to what the human answered against. Rebuilding the
        # proposal with those arguments rather than comparing the mappings directly: the hash
        # excludes `action_id` (§2.2) so the replica differs in exactly the dimension under
        # test, the comparison is over canonical bytes so `True` cannot equal `1`, and
        # `Action.__post_init__` refuses a float or a set arriving from a framework's JSON at
        # the gate rather than as a silent inequality. A second equality implementation here
        # would be a second place for the binding to drift.
        replica = replace(record.request.action, arguments=answer.approved_arguments)
        if replica.action_hash != pending.action_hash:
            raise ApprovalMismatch(
                f"the answer to {record.approval_id} was given against different arguments than "
                f"the action it would authorize",
                reason=HASH_MISMATCH,
                approval_id=record.approval_id,
            )


def _check_interrupt(interrupt: FrameworkInterrupt) -> None:
    """Both declarations are required, and both are checked where the operator wires it up.

    A missing `carries_approved_arguments` would default to whatever a reader assumed, and the
    fail-open assumption is the one somebody makes. There is no default.
    """
    framework = getattr(interrupt, "framework", None)
    if not isinstance(framework, str) or not framework.strip():
        raise InvalidArgument(
            "a FrameworkInterrupt must declare a non-empty `framework` name: it is what a "
            "conformance report and a receipt's approver are read against"
        )
    carries = getattr(interrupt, "carries_approved_arguments", None)
    if not isinstance(carries, bool):
        raise InvalidArgument(
            f"{framework}: a FrameworkInterrupt must declare `carries_approved_arguments` as a "
            "bool (SPEC-v0.5 §3.4). It states whether the framework's resumption carries back "
            "what the human answered against, and therefore whether the binding check is "
            "prevention or attribution; there is no default, because the default somebody "
            "assumes is the one that does not check"
        )
    if not callable(getattr(interrupt, "interrupt", None)):
        raise InvalidArgument(f"{framework}: a FrameworkInterrupt must implement interrupt()")


def needs_approval(
    control: Control,
    action: str,
    arguments: Mapping[str, Any],
    *,
    resource: str | None = None,
) -> bool:
    """Does this call need a human? For a framework that asks before it invokes (SPEC-v0.5 §3.5).

    The OpenAI Agents SDK's shape: the framework asks whether a tool call needs approval
    *before* it invokes the tool, surfaces its own approval item, and invokes only after a human
    answers. This answers that question and nothing else -- `True` iff the combined
    SPEC-v0.3 §4.6 decision is `APPROVE`.

    It is core's rather than each adapter's because the only way to write it in an adapter was
    to build an `Action`, and `Action.principal` has no default: the principal would have come
    from the framework's session, which is the one thing §4.2 forbids. Here it comes from
    `Control.resolve_principal`, exactly as it does at every other entry point.

    **It writes nothing**: no event, no receipt, no request, no reservation. A framework may
    call its predicate more than once, and a predicate that left evidence behind would put a
    proposal in the log for every time the framework wondered.

    A `DENY` returns `False`, so the tool is invoked and `Control.execute` denies it with a
    receipt, an `ACTION_DENIED` and the exception the caller catches. Refusing here would refuse
    without evidence, and SPEC-v0.3 §4.3 is explicit that a denial with a principal to attribute
    it to belongs in the evidence log.

    `resource` is a template over `arguments`, as `@protect`'s is (v0.1 §5.1), and the policy's
    `resource:` is used where none is given -- the same precedence `@protect` applies. It
    matters: authority matches on resource patterns (SPEC-v0.3 §4.2), so a predicate that
    skipped it would evaluate a different action from the one that runs.
    """
    principal = control.resolve_principal(action)
    template = resource if resource is not None else control.policy.resource_template(action)
    proposed = Action(
        name=action,
        arguments=arguments,
        principal=principal,
        resource=None if template is None else resolve_resource(template, arguments),
        environment=control.environment,
    )
    return control.evaluate(proposed).decision is Decision.APPROVE


def banner(control: Control) -> None:
    """Log SPEC-v0.3 §6.5's observe banner, once per `Control`. An adapter MUST call it (§3.6).

    Logged and never printed. §6.5 puts the banner on stderr for every CLI command that loads
    the operator's policy, and an adapter is not a CLI command: it is inside somebody else's
    loop and may have no stream that reaches a human at all. Printing into a framework's channel
    is at best noise and at worst a corrupted stream.

    Once per `Control`, because a warning that repeats per call is a warning nobody reads -- and
    at all, because a deployment that has been observing for six months is exactly the one that
    line is for, and an adapter that said nothing would be the quietest place in the system to
    forget. A no-op under `mode: enforce`.
    """
    if control.policy.mode != OBSERVE:
        return
    if control in _ANNOUNCED:
        return
    _ANNOUNCED.add(control)
    _LOG.warning(
        "%s: this Control was built from a policy in observe mode, so every decision below is "
        "recorded and none is enforced (SPEC-v0.3 §6). An adapter's interrupt is never reached "
        "here, because an action awaiting approval is not one observe mode produces",
        OBSERVE_BANNER,
    )
