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
from typing import Any, Final, ParamSpec, TypeVar, cast

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
    RECONCILED_STATES,
    RECONCILED_UNKNOWN,
    RESOLVED_BY_RECONCILE,
    UNRESOLVED_EFFECT,
    ReconcileOutcome,
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
    CTRLRunError,
    DuplicateEffect,
    EffectKeyError,
    InvalidArgument,
    NotExecuted,
)
from .policy import Decision, Evaluation, Policy, discover_policy_path
from .receipt import Event, EventType, Receipt, ReceiptResult, iso_timestamp, new_receipt_id
from .state import SQLiteStateStore, StateStore

_LOG = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")

DEFAULT_ENVIRONMENT: Final = "production"

#: Denial reason when a protected function is called outside `ctrlrun.context()`.
NO_PRINCIPAL: Final = "no_principal"

#: The two moments a `reconcile` hook may run, for `RECONCILIATION_STARTED.data.trigger`
#: (SPEC-v0.2 §2.3). There are no others.
RECONCILE_BLOCKING: Final = "blocking"
RECONCILE_EAGER: Final = "eager"

#: Why an answer was forced to `"unknown"`, for `RECONCILIATION_RESOLVED.data.reason` (§2.5).
RECONCILE_RAISED: Final = "raised"
RECONCILE_INVALID_RETURN: Final = "invalid_return"

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


class _Reconciler:
    """One attempt's right to ask a `reconcile` hook, spent at most once (SPEC-v0.2 §2.3).

    Both trigger points can arise inside a single `Control.execute`: a blocking answer of
    `not_executed` unblocks the reservation, the attempt then runs and ends `AMBIGUOUS`, and
    eager reconciliation would fire on the same attempt. The budget is counted here rather
    than inferred from control flow, so a wrong or slow hook costs one call per attempt and a
    retry loop cannot amplify it.
    """

    __slots__ = ("_eagerly", "_hook", "_used")

    def __init__(self, hook: Callable[[str], ReconcileOutcome] | None, eagerly: bool) -> None:
        self._hook = hook
        self._eagerly = eagerly
        self._used = False

    @property
    def eager(self) -> bool:
        return self._eagerly

    def available(self, effect_key: str | None) -> bool:
        return self._hook is not None and not self._used and effect_key is not None

    def ask(self, effect_key: str) -> tuple[str, str | None, str | None]:
        """Run the hook once. Returns `(outcome, reason, error)`; never raises `Exception`.

        Anything the hook raises, and any return value that is not one of the three literals,
        is `"unknown"` — the answer that changes nothing (SPEC-v0.2 §2.4). A `BaseException`
        that is not an `Exception` propagates, for the reason in §2.3.
        """
        assert self._hook is not None
        self._used = True
        try:
            answer = self._hook(effect_key)
        except Exception as exc:
            return RECONCILED_UNKNOWN, RECONCILE_RAISED, f"{type(exc).__name__}: {exc}"
        if answer == RECONCILED_UNKNOWN:
            return RECONCILED_UNKNOWN, None, None
        if answer in RECONCILED_STATES:
            return str(answer), None, None
        return RECONCILED_UNKNOWN, RECONCILE_INVALID_RETURN, repr(answer)


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
        lease: timedelta = DEFAULT_LEASE,
    ) -> None:
        self._policy = policy
        self._store = store
        self._approvals: ApprovalProvider = (
            approvals if approvals is not None else LocalApprovalProvider(store, clock=clock)
        )
        self._clock = clock
        self._approval_ttl = approval_ttl
        self._lease = _checked_lease(lease, "Control(lease=...)")

    @classmethod
    def from_file(cls, path: str | os.PathLike[str] | None = None) -> Control:
        """Build a Control from a policy file (`$CTRLRUN_CONFIG`, else `./ctrlrun.yaml`).

        A missing or malformed policy raises `PolicyError`: a Control cannot be constructed
        without one (SPEC-v0.1 §3.4). State lands in `.ctrlrun/state.db` beside the policy —
        or wherever `$CTRLRUN_STATE` says — so approvals and effects outlive the process and
        are shared by every worker that loaded the same policy (§5.3 E1, §8).
        """
        policy = Policy.from_file(path)
        store = SQLiteStateStore(state_path(policy.source))
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

    @property
    def lease(self) -> timedelta:
        """How long a reservation this Control takes is held for (SPEC-v0.1 §5.3 E3)."""
        return self._lease

    def evaluate(self, action: Action) -> Evaluation:
        """Decide an action. No side effects: nothing is recorded (SPEC-v0.1 §8)."""
        return self._policy.evaluate(action)

    def execute(
        self,
        action: Action,
        executor: Callable[[], Any],
        effect_key: str | None = None,
        *,
        lease: timedelta | None = None,
        reconcile: Callable[[str], ReconcileOutcome] | None = None,
        reconcile_eagerly: bool = False,
    ) -> Receipt:
        """Decide, run and record one action. Returns the receipt for its terminal state.

        The executor's own exceptions propagate: `NotExecuted` after a `failed` receipt,
        anything else after an `ambiguous` one (SPEC-v0.1 §5.5). The exception is an effect
        record that moved on while the executor ran — the lease lapsed and a human owns it
        now — where the store's refusal propagates instead, after an `ambiguous` receipt.

        `effect_key` is the already-resolved logical effect identity (§5.1); it is recorded
        on the receipt and on every event for this action. `lease` is how long this action's
        reservation is held; `None` means this Control's lease (§5.3 E3).

        `reconcile` asks the remote what happened to an effect whose outcome is unknown, and
        is the only authority besides a human that may move a record out of `AMBIGUOUS`
        (SPEC-v0.2 §2.2). It runs at most once per call.
        """
        if effect_key is not None and not effect_key:
            raise InvalidArgument("effect_key must be a non-empty string or None")
        held = self._lease if lease is None else _checked_lease(lease, "execute(lease=...)")
        reconciler = _reconciler(reconcile, reconcile_eagerly, "execute")

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
        approval, reservation = self._secure(
            action, evaluation, started_at, effect_key, held, reconciler
        )
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
                try:
                    self._store.fail_effect(effect_key, action.action_id, str(exc))
                except (DuplicateEffect, AmbiguousEffect) as refused:
                    # SPEC: §5.2 — the record moved on while the executor ran: this attempt's
                    # lease lapsed and another declared the effect AMBIGUOUS, which only a
                    # human moves it out of. The store's refusal is what propagates, not the
                    # NotExecuted: an agent that caught that would retry a key it lost.
                    self._unrecorded(
                        action, evaluation, started_at, effect_key, attempt, refused, approval
                    )
                    raise
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
            recorded = effect_key is None
            if effect_key is not None:
                try:
                    self._store.mark_ambiguous(effect_key, action.action_id, error)
                    recorded = True
                except (DuplicateEffect, AmbiguousEffect) as refused:
                    # Recording an unknown outcome must never mask the exception that caused
                    # it. A store that refuses here has the record in a state a human already
                    # owns — where this attempt was trying to put it — and the receipt below
                    # says `ambiguous` either way.
                    _LOG.warning("%s: effect %s: %s", action.name, effect_key, refused)
            self._append(
                EventType.EXECUTION_AMBIGUOUS,
                action,
                {"error": error},
                effect_key,
                approval=approval,
            )
            # SPEC-v0.2 §2.3 — eager reconciliation, and never while a KeyboardInterrupt or
            # SystemExit unwinds: v0.1 §5.5 already refuses to let tidying up swallow an
            # interrupt, and calling arbitrary user code during one is the same mistake with
            # a longer stack. `recorded` gates it too: a record this attempt could not mark
            # is not this attempt's to resolve.
            if reconciler.eager and recorded and isinstance(exc, Exception):
                self._reconciled(reconciler, action, effect_key, RECONCILE_EAGER)
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
            try:
                self._store.commit_effect(effect_key, action.action_id, result)
            except (DuplicateEffect, AmbiguousEffect) as refused:
                # SPEC: §5.2 — the executor returned, but the key is no longer this attempt's
                # to commit: the lease lapsed and the record is AMBIGUOUS until a human says
                # otherwise. What happened at the remote is now as unknown as a timeout, so
                # the attempt is recorded `ambiguous` rather than committed.
                self._unrecorded(
                    action, evaluation, started_at, effect_key, attempt, refused, approval
                )
                raise
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
        lease: timedelta,
        reconciler: _Reconciler,
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

        # At most two passes: an `AMBIGUOUS` refusal may be reconciled once (SPEC-v0.2 §2.3),
        # and whatever the second attempt meets is final.
        for reconciled in (False, True):
            try:
                approval, reservation = self._take(action, approval_id, effect_key, lease)
                break
            except AmbiguousEffect as refused:
                # SPEC-v0.2 §2.3 — the record is `AMBIGUOUS` now, whether it already was or
                # was just moved there by an expired lease (v0.1 §5.4). Either way it is in
                # the state reconciliation asks about, which is why the order is this way
                # round.
                if reconciled or not self._reconciled(
                    reconciler, action, effect_key, RECONCILE_BLOCKING
                ):
                    self._refused(
                        action,
                        evaluation,
                        started_at,
                        effect_key,
                        refused,
                        approval_id=approval_id,
                    )
                    raise
            except ActionDenied as denied:
                # The store raises this when the record says a human refused. §4.2 makes that
                # a denial of the action, not a mismatch: nothing about the action was wrong.
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
                        EventType.APPROVAL_EXPIRED,
                        action,
                        {},
                        effect_key,
                        approval_id=approval_id,
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
            except DuplicateEffect as refused:
                # SPEC §5.4 — this effect already happened or is happening now. The approval,
                # if one was presented, was not consumed: it is still worth something.
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

    def _reconciled(
        self,
        reconciler: _Reconciler,
        action: Action,
        effect_key: str | None,
        trigger: str,
    ) -> bool:
        """Ask the hook once, and move the record only where its answer points (§2.2, §2.4).

        Returns whether the record moved. `"unknown"` — which is also what an exception and a
        nonsense return value become — moves nothing and returns `False`, so every caller's
        fail-closed path is the one that was already there.
        """
        if not reconciler.available(effect_key):
            return False
        assert effect_key is not None
        self._append(
            EventType.RECONCILIATION_STARTED,
            action,
            {"effect_key": effect_key, "trigger": trigger},
            effect_key,
        )
        outcome, reason, error = reconciler.ask(effect_key)
        data: dict[str, Any] = {"outcome": outcome}
        if reason is not None:
            data["reason"] = reason
            data["error"] = error
        self._append(EventType.RECONCILIATION_RESOLVED, action, data, effect_key)
        if reason is not None:
            _LOG.warning(
                "%s: reconcile(%s) gave no answer (%s): %s", action.name, effect_key, reason, error
            )
        state = RECONCILED_STATES.get(outcome)
        if state is None:
            return False
        try:
            self._store.resolve_effect(effect_key, state, RESOLVED_BY_RECONCILE)
        except CTRLRunError as refused:
            # The record moved out of `AMBIGUOUS` between the refusal and the answer — a
            # human, or another process, got there first. Their resolution stands; this one
            # is dropped, which is the same nothing `"unknown"` would have done.
            _LOG.warning("%s: reconcile(%s) not applied: %s", action.name, effect_key, refused)
            return False
        self._append(
            EventType.EFFECT_RESOLVED,
            action,
            {"state": str(state), "resolved_by": RESOLVED_BY_RECONCILE},
            effect_key,
        )
        return True

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

    def _unrecorded(
        self,
        action: Action,
        evaluation: Evaluation,
        started_at: datetime,
        effect_key: str,
        attempt: int,
        refused: DuplicateEffect | AmbiguousEffect,
        approval: Approval | None,
    ) -> None:
        """Record an outcome the store refused to write (SPEC-v0.1 §5.2, §5.5).

        The executor finished, but the effect record moved on while it ran, so what happened
        at the remote is exactly as unknown as a timeout: `ambiguous`, whatever the executor
        returned or raised. A terminal action still gets its receipt (§6.1).
        """
        error = f"{type(refused).__name__}: {refused}"
        self._append(
            EventType.EXECUTION_AMBIGUOUS, action, {"error": error}, effect_key, approval=approval
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
        self, action: Action, approval_id: str | None, effect_key: str | None, lease: timedelta
    ) -> tuple[Approval | None, Reservation | None]:
        """Consume the approval, reserve the effect, or both at once (SPEC-v0.1 §4.2 A4)."""
        if approval_id is not None and effect_key is not None:
            return self._store.consume_approval_and_reserve(
                approval_id, action.action_hash, effect_key, action.action_id, lease
            )
        if approval_id is not None:
            return self._store.consume_approval(approval_id, action.action_hash), None
        if effect_key is not None:
            return None, self._store.reserve_effect(effect_key, action.action_id, lease)
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


def state_path(source: str | os.PathLike[str] | None = None) -> Path:
    """Where the state database lives for a given policy file (SPEC-v0.1 §8).

    `.ctrlrun/state.db` beside the policy file, unless `$CTRLRUN_STATE` names somewhere else.
    Beside the policy, not beside the process: workers started from different directories but
    sharing a policy must share one store, or reservation is atomic within each of them and
    meaningless between them (§5.3 E1). Set but empty is a misconfiguration, not a licence to
    fall back to the default — an agent's effects would land in a store nobody is watching.

    `source=None` discovers the policy the way `Control.from_file` does, which is how the CLI
    finds the store an agent is using without needing to load the policy itself.
    """
    configured = os.environ.get(STATE_ENV_VAR)
    if configured is not None:
        if not configured.strip():
            raise InvalidArgument(
                f"{STATE_ENV_VAR} is set but empty; unset it or point it at a state database"
            )
        return Path(configured)
    resolved = discover_policy_path() if source is None else Path(source)
    return resolved.parent / DEFAULT_STATE_DIR / DEFAULT_STATE_FILENAME


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
    lease: timedelta | None = None,
    reconcile: Callable[[str], ReconcileOutcome] | None = None,
    reconcile_eagerly: bool = False,
    control: Control | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Bind a function to an action name: every call becomes a decided, recorded Action.

    The wrapped function is the executor (SPEC-v0.1 §5.5), and it is invoked with the
    action's canonical arguments, never with the caller's own objects (§2.2).

    `effect` and `resource` are templates over the call's arguments (§5.1). Their syntax is
    checked here, at decoration time, so a typo fails at import rather than mid-agent-run.

    `lease` is how long this action's reservation is held (§5.3 E3), for work that takes
    longer than the Control's default; it overrides that default and nothing else. Expiry
    means what it always meant: past the lease the effect is `AMBIGUOUS`, never released.

    `reconcile` asks the remote what happened to an effect whose outcome is unknown
    (SPEC-v0.2 §2). With `reconcile_eagerly`, it also runs immediately after this call
    produces an `AMBIGUOUS` outcome, rather than only when one blocks a later attempt.
    """
    if not name:
        raise InvalidArgument("protect(name=...) must be a non-empty action name")
    _check_template(name, "effect", effect)
    _check_template(name, "resource", resource)
    held = None if lease is None else _checked_lease(lease, f"protect({name!r}, lease=...)")
    _reconciler(reconcile, reconcile_eagerly, f"protect({name!r}")

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        signature = inspect.signature(func)
        _reject_variadic(signature, name)
        dangling: list[bool] = []

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
            if reconcile is not None and effect_key is None and not dangling:
                # SPEC-v0.2 §2.1 — not a decoration-time error, because the effect template
                # may come from the policy and that is not loaded yet. A hook with no key to
                # reconcile does nothing, unlike a mistyped template, which changes an
                # identity: §5.1 refuses that, and this only says so. Once per function.
                dangling.append(True)
                _LOG.warning(
                    "%s: reconcile= is set but the action has no effect key, so it will "
                    "never be called; declare effect= to give the action an identity",
                    name,
                )
            returned: list[R] = []

            def executor() -> R:
                value = _invoke(func, signature, action.canonical_arguments)
                returned.append(value)
                return value

            try:
                resolved.execute(
                    action,
                    executor,
                    effect_key,
                    lease=held,
                    reconcile=reconcile,
                    reconcile_eagerly=reconcile_eagerly,
                )
            except ApprovalRequired as pending:
                if not wait:
                    raise
                # SPEC-v0.1 §4.3 — with wait=True the decorator blocks on the provider and
                # then re-presents the same proposal. It re-presents a *denied* answer too:
                # the refusal belongs in the receipt Control writes, not in the decorator.
                # The new proposal gets its own reconciliation budget: it is a new attempt.
                resolved.approvals.wait(pending.request_id, None)
                with with_approval(pending.request_id):
                    resolved.execute(
                        action,
                        executor,
                        effect_key,
                        lease=held,
                        reconcile=reconcile,
                        reconcile_eagerly=reconcile_eagerly,
                    )
            return returned[0]

        return wrapper

    return decorator


def _reconciler(reconcile: object, reconcile_eagerly: object, where: str) -> _Reconciler:
    """Validate the pair, wherever one is offered, and hand back this attempt's budget.

    `reconcile_eagerly` without a hook asks for a second trigger point on a hook that does
    not exist. It is a wiring bug with no safe reading, so it is refused rather than ignored
    — at decoration time for `protect`, alongside the template and lease checks (§5.1, §5.3).
    """
    if reconcile is not None and not callable(reconcile):
        raise InvalidArgument(
            f"{where}, reconcile=...): must be callable, not {type(reconcile).__name__}"
        )
    if not isinstance(reconcile_eagerly, bool):
        raise InvalidArgument(f"{where}, reconcile_eagerly=...): must be a bool")
    if reconcile_eagerly and reconcile is None:
        raise InvalidArgument(
            f"{where}, reconcile_eagerly=True): there is no reconcile= hook to run eagerly"
        )
    hook = cast("Callable[[str], ReconcileOutcome] | None", reconcile)
    return _Reconciler(hook, reconcile_eagerly)


def _checked_lease(lease: object, where: str) -> timedelta:
    """A lease must be a positive `timedelta` (SPEC-v0.1 §5.3 E3).

    Checked wherever one is offered — `Control`, `execute`, `protect` — because each is a
    separate way in, and a lease that has already expired reserves nothing: the first
    contender would find the record expired and declare a perfectly healthy effect ambiguous.
    """
    if not isinstance(lease, timedelta):
        raise InvalidArgument(f"{where}: lease must be a timedelta, not {type(lease).__name__}")
    if lease <= timedelta(0):
        raise InvalidArgument(f"{where}: lease must be positive, got {lease!r}")
    return lease


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
