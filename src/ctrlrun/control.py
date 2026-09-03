"""The @protect decorator, Control and the ambient action context. Build-list item 3.

`Control` is the only place the other modules are composed (ARCHITECTURE §6): it turns a
function call into an Action, decides it against the policy, runs the executor, and records
what happened. SPEC-v0.1 §8 freezes the names here.
"""

from __future__ import annotations

import functools
import inspect
import logging
import os
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, ParamSpec, TypeVar

from .action import Action, Principal
from .approval import (
    DEFAULT_APPROVAL_TTL,
    Approval,
    ApprovalProvider,
    ApprovalStatus,
    LocalApprovalProvider,
)
from .effect import (
    DEFAULT_LEASE,
    UNRESOLVED_EFFECT,
    Reservation,
    resolve_effect_key,
    resolve_resource,
    template_placeholders,
)
from .errors import (
    ActionDenied,
    AmbiguousEffect,
    ApprovalMismatch,
    ApprovalRequired,
    DuplicateEffect,
    EffectKeyError,
    InvalidArgument,
    NotExecuted,
)
from .policy import Decision, Evaluation, Policy
from .receipt import Event, EventType, Receipt, ReceiptResult, iso_timestamp, new_receipt_id
from .state import SQLiteStateStore, StateStore

_LOG = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")

DEFAULT_ENVIRONMENT: Final = "production"

#: Denial reason when a protected function is called outside `ctrlrun.context()`.
NO_PRINCIPAL: Final = "no_principal"

#: Where `Control.from_file` keeps its store, and the env var that overrides it (SPEC §8).
STATE_ENV_VAR: Final = "CTRLRUN_STATE"
DEFAULT_STATE_DIR: Final = ".ctrlrun"
DEFAULT_STATE_FILENAME: Final = "state.db"


def _utc_now() -> datetime:
    return datetime.now(UTC)


# --- the ambient context ---------------------------------------------------------------


@dataclass(frozen=True)
class _Invocation:
    principal: Principal
    environment: str


_CONTEXT: ContextVar[_Invocation] = ContextVar("ctrlrun_context")
_PRESENTED_APPROVAL: ContextVar[str] = ContextVar("ctrlrun_approval")


@contextmanager
def context(agent: str, user: str | None = None, environment: str | None = None) -> Iterator[None]:
    """Bind the principal (and optionally the environment) for calls made inside the block.

    A protected function called outside any `context()` has no principal and is denied
    (SPEC-v0.1 §2.1). A nested `context()` that omits `environment` inherits the enclosing
    one, so entering a context to change agent cannot silently move a call to production.
    """
    if environment is not None and not environment:
        raise InvalidArgument("context(environment=...) must be a non-empty string or None")
    enclosing = _CONTEXT.get(None)
    if environment is not None:
        resolved = environment
    elif enclosing is not None:
        resolved = enclosing.environment
    else:
        resolved = DEFAULT_ENVIRONMENT
    token = _CONTEXT.set(_Invocation(Principal(agent=agent, user=user), resolved))
    try:
        yield
    finally:
        _CONTEXT.reset(token)


@contextmanager
def with_approval(request_id: str) -> Iterator[None]:
    """Present a granted approval to the calls made inside the block (SPEC-v0.1 §4.3).

    The approval still has to match: it authorizes the exact action a human saw, once, and
    only until it expires. Presenting it is an offer, not a decision.
    """
    if not request_id:
        raise InvalidArgument("with_approval(request_id) must be a non-empty request id")
    token = _PRESENTED_APPROVAL.set(request_id)
    try:
        yield
    finally:
        _PRESENTED_APPROVAL.reset(token)


# --- Control ---------------------------------------------------------------------------


class Control:
    """Policy, state and evidence composed around a single action (SPEC-v0.1 §8).

    `evaluate()` decides an action and touches nothing. `execute()` decides it, runs the
    executor, and records a receipt and events for whatever happened.
    """

    def __init__(
        self,
        policy: Policy,
        store: StateStore,
        approvals: ApprovalProvider | None = None,
        *,
        clock: Callable[[], datetime] = _utc_now,
        approval_ttl: timedelta = DEFAULT_APPROVAL_TTL,
    ) -> None:
        self._policy = policy
        self._store = store
        self._approvals: ApprovalProvider = (
            approvals if approvals is not None else LocalApprovalProvider(store, clock=clock)
        )
        self._clock = clock
        self._approval_ttl = approval_ttl

    @classmethod
    def from_file(cls, path: str | os.PathLike[str] | None = None) -> Control:
        """Build a Control from a policy file (`$CTRLRUN_CONFIG`, else `./ctrlrun.yaml`).

        A missing or malformed policy raises `PolicyError`: a Control cannot be constructed
        without one (SPEC-v0.1 §3.4). State lands in `.ctrlrun/state.db` beside the policy —
        or wherever `$CTRLRUN_STATE` says — so approvals and effects outlive the process and
        are shared by every worker that loaded the same policy (§5.3 E1, §8).
        """
        policy = Policy.from_file(path)
        store = SQLiteStateStore(_state_path(policy.source))
        return cls(policy, store, LocalApprovalProvider(store))

    @property
    def policy(self) -> Policy:
        return self._policy

    @property
    def store(self) -> StateStore:
        return self._store

    @property
    def approvals(self) -> ApprovalProvider:
        return self._approvals

    def evaluate(self, action: Action) -> Evaluation:
        """Decide an action. No side effects: nothing is recorded (SPEC-v0.1 §8)."""
        return self._policy.evaluate(action)

    def execute(
        self,
        action: Action,
        executor: Callable[[], Any],
        effect_key: str | None = None,
    ) -> Receipt:
        """Decide, run and record one action. Returns the receipt for its terminal state.

        The executor's own exceptions propagate: `NotExecuted` after a `failed` receipt,
        anything else after an `ambiguous` one (SPEC-v0.1 §5.5).

        `effect_key` is the already-resolved logical effect identity (§5.1); it is recorded
        on the receipt and on every event for this action.
        """
        if effect_key is not None and not effect_key:
            raise InvalidArgument("effect_key must be a non-empty string or None")

        started_at = self._clock()
        self._append(
            EventType.ACTION_PROPOSED, action, {"action_hash": action.action_hash}, effect_key
        )
        evaluation = self._policy.evaluate(action)
        self._append(
            EventType.POLICY_EVALUATED,
            action,
            {"decision": str(evaluation.decision), "reason": evaluation.reason},
            effect_key,
        )

        if evaluation.decision is Decision.DENY:
            self._append(EventType.ACTION_DENIED, action, {"reason": evaluation.reason}, effect_key)
            self._record(
                action, evaluation, ReceiptResult.DENIED, started_at, effect_key=effect_key
            )
            raise ActionDenied(
                f"{action.name} denied: {evaluation.reason}",
                reason=evaluation.reason,
                action_id=action.action_id,
            )
        approval, reservation = self._secure(action, evaluation, started_at, effect_key)
        attempt = 1 if reservation is None else reservation.attempt

        if effect_key is not None:
            try:
                self._store.begin_execution(effect_key, action.action_id)
            except (DuplicateEffect, AmbiguousEffect) as refused:
                # The key was taken from under this attempt between winning it and starting:
                # its lease expired and another attempt declared the effect AMBIGUOUS. The
                # refusal is terminal for this proposal, so it gets a receipt like any other.
                self._refused(
                    action, evaluation, started_at, effect_key, refused, approval=approval
                )
                raise
        self._append(EventType.EXECUTION_STARTED, action, {}, effect_key, approval=approval)
        try:
            result = executor()
        except NotExecuted as exc:
            if effect_key is not None:
                # SPEC §5.5 — the executor asserted the remote side did nothing, so this is
                # the one outcome that leaves the key retryable (§5.4).
                self._store.fail_effect(effect_key, action.action_id, str(exc))
            self._append(
                EventType.EXECUTION_FAILED,
                action,
                {"error": str(exc)},
                effect_key,
                approval=approval,
            )
            self._record(
                action,
                evaluation,
                ReceiptResult.FAILED,
                started_at,
                error=str(exc),
                approval=approval,
                effect_key=effect_key,
                attempt=attempt,
            )
            raise
        except BaseException as exc:
            # SPEC: §5.5 — anything that is not NotExecuted is an AMBIGUOUS outcome, never
            # FAILED, and "anything" means BaseException: a KeyboardInterrupt mid-request
            # leaves the same unknown outcome a timeout does. Narrowing this to Exception
            # is a regression, not a cleanup.
            error = f"{type(exc).__name__}: {exc}"
            if effect_key is not None:
                self._store.mark_ambiguous(effect_key, action.action_id, error)
            self._append(
                EventType.EXECUTION_AMBIGUOUS,
                action,
                {"error": error},
                effect_key,
                approval=approval,
            )
            self._record(
                action,
                evaluation,
                ReceiptResult.AMBIGUOUS,
                started_at,
                error=error,
                approval=approval,
                effect_key=effect_key,
                attempt=attempt,
            )
            raise
        if effect_key is not None:
            self._store.commit_effect(effect_key, action.action_id, result)
        self._append(EventType.EXECUTION_COMMITTED, action, {}, effect_key, approval=approval)
        return self._record(
            action,
            evaluation,
            ReceiptResult.COMMITTED,
            started_at,
            approval=approval,
            effect_key=effect_key,
            attempt=attempt,
        )

    # --- the effect key (SPEC-v0.1 §5.1) ------------------------------------------------

    def _resolve_effect(self, action: Action, template: str | None) -> str | None:
        """Resolve an effect template against a constructed action, or deny the action.

        A missing placeholder is never a silent `None` (§5.1): the action is refused and
        recorded as denied, before the policy is consulted. An action whose logical effect
        cannot be identified cannot be protected against duplication, whatever the policy
        would have said about it — and unlike a call outside `context()` (§2.1) there is a
        principal here, so the refusal belongs in the evidence log.
        """
        if template is None:
            return None
        started_at = self._clock()
        try:
            return resolve_effect_key(template, action)
        except EffectKeyError as exc:
            self._append(EventType.ACTION_PROPOSED, action, {"action_hash": action.action_hash})
            self._append(
                EventType.ACTION_DENIED,
                action,
                {"reason": UNRESOLVED_EFFECT, "effect": template, "error": str(exc)},
            )
            # SPEC: §6.1 — a receipt needs a decision and the policy never rendered one, so
            # the fail-closed value is recorded: denied, for a reason that is not a rule.
            self._record(
                action,
                Evaluation(Decision.DENY, UNRESOLVED_EFFECT),
                ReceiptResult.DENIED,
                started_at,
                error=str(exc),
            )
            raise

    # --- authority and the effect key (SPEC-v0.1 §4.2, §5.3) --------------------------

    def _secure(
        self,
        action: Action,
        evaluation: Evaluation,
        started_at: datetime,
        effect_key: str | None,
    ) -> tuple[Approval | None, Reservation | None]:
        """Take everything this action needs before it may run: the grant, and the key.

        Both are taken in one store transaction (SPEC-v0.1 §4.2 A4). The approval is checked
        first, so a replayed approval is what gets raised when a duplicate effect would also
        apply (T4), and a refused reservation leaves the approval granted for the action the
        human actually saw (T12).
        """
        approval_id = (
            self._presented(action, effect_key) if evaluation.decision is Decision.APPROVE else None
        )
        if approval_id is None and effect_key is None:
            return None, None

        try:
            approval, reservation = self._take(action, approval_id, effect_key)
        except ActionDenied as denied:
            # The store raises this when the record says a human refused. §4.2 makes that a
            # denial of the action, not a mismatch: nothing about the action was wrong.
            approver = self._approver_of(approval_id)
            self._append(
                EventType.APPROVAL_DENIED,
                action,
                {"approver": approver},
                effect_key,
                approval_id=approval_id,
            )
            self._append(EventType.ACTION_DENIED, action, {"reason": denied.reason}, effect_key)
            self._record(
                action,
                evaluation,
                ReceiptResult.DENIED,
                started_at,
                error=str(denied),
                approval_id=approval_id,
                approver=approver,
                effect_key=effect_key,
            )
            raise ActionDenied(
                str(denied), reason=denied.reason, action_id=action.action_id
            ) from denied
        except ApprovalMismatch as mismatch:
            if mismatch.reason == ApprovalStatus.EXPIRED:
                self._append(
                    EventType.APPROVAL_EXPIRED, action, {}, effect_key, approval_id=approval_id
                )
            self._append(
                EventType.APPROVAL_INVALIDATED,
                action,
                {"reason": mismatch.reason, "action_hash": action.action_hash},
                effect_key,
                approval_id=approval_id,
            )
            self._record(
                action,
                evaluation,
                ReceiptResult.BLOCKED,
                started_at,
                error=str(mismatch),
                approval_id=approval_id,
                approver=self._approver_of(approval_id),
                effect_key=effect_key,
            )
            raise
        except (DuplicateEffect, AmbiguousEffect) as refused:
            # SPEC §5.4 — this effect already happened, is happening, or ended unknown. The
            # approval, if one was presented, was not consumed: it is still worth something.
            self._refused(
                action, evaluation, started_at, effect_key, refused, approval_id=approval_id
            )
            raise

        if approval is not None:
            self._append(
                EventType.APPROVAL_CONSUMED,
                action,
                {"approver": approval.approver},
                effect_key,
                approval_id=approval.approval_id,
            )
        if reservation is not None:
            self._append(
                EventType.EFFECT_RESERVED,
                action,
                {
                    "attempt": reservation.attempt,
                    "lease_expires_at": iso_timestamp(reservation.lease_expires_at),
                },
                effect_key,
                approval=approval,
            )
        return approval, reservation

    def _refused(
        self,
        action: Action,
        evaluation: Evaluation,
        started_at: datetime,
        effect_key: str | None,
        refused: DuplicateEffect | AmbiguousEffect,
        *,
        approval: Approval | None = None,
        approval_id: str | None = None,
    ) -> None:
        """Record a refusal by the effect key: the event, and a `blocked` receipt (§6.1)."""
        presented = approval.approval_id if approval is not None else approval_id
        self._append(
            EventType.EFFECT_RESERVATION_REFUSED,
            action,
            _refusal_data(refused),
            effect_key,
            approval_id=presented,
        )
        self._record(
            action,
            evaluation,
            ReceiptResult.BLOCKED,
            started_at,
            error=str(refused),
            approval_id=presented,
            approver=self._approver_of(presented),
            effect_key=effect_key,
        )

    def _presented(self, action: Action, effect_key: str | None) -> str:
        """The approval this call presents, or record a request and suspend the action.

        With nothing presented, `ApprovalRequired` is raised so the caller can come back with
        `with_approval(request_id)` (SPEC-v0.1 §4.3). No receipt is written for that: the
        action is suspended awaiting a human, which is not a terminal state (§6.1). The
        `APPROVAL_REQUESTED` event is the evidence.
        """
        presented = _PRESENTED_APPROVAL.get(None)
        if presented is not None:
            return presented
        request = self._approvals.request(action, self._approval_ttl)
        self._append(
            EventType.APPROVAL_REQUESTED,
            action,
            {"action_hash": action.action_hash, "expires_at": iso_timestamp(request.expires_at)},
            effect_key,
            approval_id=request.request_id,
        )
        raise ApprovalRequired(
            f"{action.name} requires approval: run 'ctrlrun approve {request.request_id}', "
            f"then retry inside ctrlrun.with_approval({request.request_id!r})",
            request_id=request.request_id,
            action_id=action.action_id,
        )

    def _take(
        self, action: Action, approval_id: str | None, effect_key: str | None
    ) -> tuple[Approval | None, Reservation | None]:
        """Consume the approval, reserve the effect, or both at once (SPEC-v0.1 §4.2 A4)."""
        if approval_id is not None and effect_key is not None:
            return self._store.consume_approval_and_reserve(
                approval_id, action.action_hash, effect_key, action.action_id, DEFAULT_LEASE
            )
        if approval_id is not None:
            return self._store.consume_approval(approval_id, action.action_hash), None
        if effect_key is not None:
            return None, self._store.reserve_effect(effect_key, action.action_id, DEFAULT_LEASE)
        return None, None

    def _approver_of(self, approval_id: str | None) -> str | None:
        """Who answered, for the evidence trail. `None` if there is no such record."""
        if approval_id is None:
            return None
        record = self._store.get_approval(approval_id)
        return None if record is None else record.approver

    # --- evidence ---------------------------------------------------------------------

    def _append(
        self,
        type_: EventType,
        action: Action,
        data: Mapping[str, Any],
        effect_key: str | None = None,
        *,
        approval: Approval | None = None,
        approval_id: str | None = None,
    ) -> None:
        self._store.append_event(
            Event(
                type=type_,
                action_id=action.action_id,
                ts=self._clock(),
                data=data,
                effect_key=effect_key,
                approval_id=approval_id if approval is None else approval.approval_id,
            )
        )

    def _record(
        self,
        action: Action,
        evaluation: Evaluation,
        result: ReceiptResult,
        started_at: datetime,
        error: str | None = None,
        *,
        approval: Approval | None = None,
        approval_id: str | None = None,
        approver: str | None = None,
        effect_key: str | None = None,
        attempt: int = 1,
    ) -> Receipt:
        receipt = Receipt(
            receipt_id=new_receipt_id(),
            action_id=action.action_id,
            action=action.name,
            action_hash=action.action_hash,
            principal=action.principal,
            resource=action.resource,
            arguments=action.canonical_arguments,
            environment=action.environment,
            decision=evaluation.decision,
            decision_reason=evaluation.reason,
            approval_id=approval.approval_id if approval is not None else approval_id,
            approver=approval.approver if approval is not None else approver,
            effect_key=effect_key,
            attempt=attempt,
            result=result,
            started_at=started_at,
            finished_at=self._clock(),
            error=error,
        )
        self._store.put_receipt(receipt)
        return receipt


def _refusal_data(refused: DuplicateEffect | AmbiguousEffect) -> dict[str, str]:
    """Why a reservation was refused, for `EFFECT_RESERVATION_REFUSED` (SPEC §5.4, §6.2)."""
    if isinstance(refused, DuplicateEffect):
        return {"reason": "duplicate", "state": refused.state}
    return {"reason": "ambiguous"}


def _state_path(source: str) -> Path:
    """Where `Control.from_file` keeps its state (SPEC-v0.1 §8).

    `.ctrlrun/state.db` beside the policy file, unless `$CTRLRUN_STATE` names somewhere else.
    Beside the policy, not beside the process: workers started from different directories but
    sharing a policy must share one store, or reservation is atomic within each of them and
    meaningless between them (§5.3 E1). Set but empty is a misconfiguration, not a licence to
    fall back to the default — an agent's effects would land in a store nobody is watching.
    """
    configured = os.environ.get(STATE_ENV_VAR)
    if configured is None:
        return Path(source).parent / DEFAULT_STATE_DIR / DEFAULT_STATE_FILENAME
    if not configured.strip():
        raise InvalidArgument(
            f"{STATE_ENV_VAR} is set but empty; unset it or point it at a state database"
        )
    return Path(configured)


_DEFAULT_CONTROL: Control | None = None


def _default_control() -> Control:
    global _DEFAULT_CONTROL
    if _DEFAULT_CONTROL is None:
        _DEFAULT_CONTROL = Control.from_file()
    return _DEFAULT_CONTROL


# --- the decorator ----------------------------------------------------------------------


def protect(
    name: str,
    *,
    effect: str | None = None,
    resource: str | None = None,
    wait: bool = False,
    control: Control | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Bind a function to an action name: every call becomes a decided, recorded Action.

    The wrapped function is the executor (SPEC-v0.1 §5.5), and it is invoked with the
    action's canonical arguments, never with the caller's own objects (§2.2).

    `effect` and `resource` are templates over the call's arguments (§5.1). Their syntax is
    checked here, at decoration time, so a typo fails at import rather than mid-agent-run.
    """
    if not name:
        raise InvalidArgument("protect(name=...) must be a non-empty action name")
    _check_template(name, "effect", effect)
    _check_template(name, "resource", resource)

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        signature = inspect.signature(func)
        _reject_variadic(signature, name)

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            invocation = _CONTEXT.get(None)
            if invocation is None:
                # SPEC-v0.1 §2.1 — a call outside context() is a wiring bug, not an agent
                # action: it is denied and warned about, but never enters the evidence log,
                # which records actions and has no principal to attribute this one to.
                _LOG.warning("%s: denied: no ctrlrun.context() is active", name)
                raise ActionDenied(
                    f"{name}: no ctrlrun.context() is active; wrap the call in "
                    "'with ctrlrun.context(agent=...)'",
                    reason=NO_PRINCIPAL,
                )
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            action = Action(
                name=name,
                arguments=dict(bound.arguments),
                principal=invocation.principal,
                # SPEC-v0.1 §5.1 — `resource` is part of the canonical form, so it resolves
                # before the Action exists; the effect template resolves against the Action.
                resource=None if resource is None else resolve_resource(resource, bound.arguments),
                environment=invocation.environment,
            )
            resolved = control if control is not None else _default_control()
            effect_key = resolved._resolve_effect(action, effect)
            returned: list[R] = []

            def executor() -> R:
                value = _invoke(func, signature, action.canonical_arguments)
                returned.append(value)
                return value

            try:
                resolved.execute(action, executor, effect_key)
            except ApprovalRequired as pending:
                if not wait:
                    raise
                # SPEC-v0.1 §4.3 — with wait=True the decorator blocks on the provider and
                # then re-presents the same proposal. It re-presents a *denied* answer too:
                # the refusal belongs in the receipt Control writes, not in the decorator.
                resolved.approvals.wait(pending.request_id, None)
                with with_approval(pending.request_id):
                    resolved.execute(action, executor, effect_key)
            return returned[0]

        return wrapper

    return decorator


def _check_template(name: str, kwarg: str, template: str | None) -> None:
    """Reject a malformed `effect=` / `resource=` template at decoration time (SPEC §5.1)."""
    if template is None:
        return
    try:
        template_placeholders(template)
    except InvalidArgument as exc:
        raise InvalidArgument(f"protect({name!r}, {kwarg}=...): {exc}") from exc


def _reject_variadic(signature: inspect.Signature, name: str) -> None:
    """Refuse `*args` / `**kwargs` on a protected function.

    SPEC: §8 — an Action's arguments are a mapping of named values (§2.1); policy conditions
    and templates address them by name. A variadic parameter has no such name, so it could
    never be written into a rule. Positional-only and keyword-only parameters are fine.
    """
    variadic = inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD
    offending = [
        parameter.name for parameter in signature.parameters.values() if parameter.kind in variadic
    ]
    if offending:
        raise InvalidArgument(
            f"protect({name!r}): a protected function cannot take *args or **kwargs "
            f"({', '.join(offending)}); every argument must be nameable in policy"
        )


def _invoke(
    func: Callable[..., R], signature: inspect.Signature, arguments: Mapping[str, Any]
) -> R:
    """Call `func` with `arguments`, respecting positional-only parameters."""
    positional = [
        parameter.name
        for parameter in signature.parameters.values()
        if parameter.kind is inspect.Parameter.POSITIONAL_ONLY
    ]
    keyword = {key: value for key, value in arguments.items() if key not in positional}
    return func(*(arguments[key] for key in positional), **keyword)
