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
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, NoReturn, ParamSpec, TypeVar, cast

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
    IdentityError,
    InvalidArgument,
    NotExecuted,
    Suspended,
)
from .identity import IdentityContext, IdentityProvider
from .policy import Decision, Evaluation, Policy, discover_policy_path
from .receipt import (
    Event,
    EventSink,
    EventType,
    JSONLEventSink,
    Receipt,
    ReceiptResult,
    iso_timestamp,
    new_receipt_id,
)
from .state import SQLiteStateStore, StateStore

_LOG = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")

DEFAULT_ENVIRONMENT: Final = "production"

#: SPEC-v0.2 §6.9.2 — how long a reservation is held open across one round trip. A client
#: that never returns lets this lapse, and the record becomes AMBIGUOUS by v0.1 §5.3 E3.
DEFAULT_SUSPEND_TIMEOUT: Final = timedelta(minutes=5)

#: Denial reason when a protected function is called outside `ctrlrun.context()`.
NO_PRINCIPAL: Final = "no_principal"

#: SPEC-v0.3 §2.3 — the reason an expired credential refuses, on the receipt and the event.
PRINCIPAL_EXPIRED: Final = "principal_expired"

#: SPEC-v0.3 §2.5 rank 2.
ENVIRONMENT_ENV_VAR: Final = "CTRLRUN_ENVIRONMENT"

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


_CONTEXT: ContextVar[_Invocation] = ContextVar("ctrlrun_context")
_PRESENTED_APPROVAL: ContextVar[str] = ContextVar("ctrlrun_approval")


@contextmanager
def context(agent: str, user: str | None = None) -> Iterator[None]:
    """Bind the principal for calls made inside the block.

    A protected function called outside any `context()` has no principal and is denied
    (SPEC-v0.1 §2.1).

    **`environment` was a parameter here until v0.3** and is gone (SPEC-v0.3 §2.5). A grant may
    scope to an environment, which makes it an authorization input, and an authorization
    dimension the subject sets is not one — the same argument that removes
    `--principal-from-client-info` from the gateway. It is set once on the `Control` now, so
    every Action a deployment proposes carries the deployment's own answer.

    Where an `IdentityProvider` is installed, the principal named here is a **hint** rather than
    an identity: the provider wins where it answers (§3.2).
    """
    token = _CONTEXT.set(_Invocation(Principal(agent=agent, user=user)))
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
        sinks: Sequence[EventSink] = (),
        suspend_timeout: timedelta = DEFAULT_SUSPEND_TIMEOUT,
        identity: IdentityProvider | None = None,
        environment: str | None = None,
    ) -> None:
        self._policy = policy
        self._store = store
        self._approvals: ApprovalProvider = (
            approvals if approvals is not None else LocalApprovalProvider(store, clock=clock)
        )
        self._clock = clock
        self._approval_ttl = approval_ttl
        self._lease = _checked_lease(lease, "Control(lease=...)")
        self._sinks = tuple(sinks)
        self._suspend_timeout = _checked_lease(suspend_timeout, "Control(suspend_timeout=...)")
        self._identity = identity
        self._environment = _resolve_environment(environment, policy)
        #: SPEC-v0.3 §3.2 — the provider-versus-context warning is emitted at most once per
        #: Control. A warning that repeats per call is a warning nobody reads.
        self._warned_about_principal = False

    @classmethod
    def from_file(
        cls, path: str | os.PathLike[str] | None = None, *, environment: str | None = None
    ) -> Control:
        """Build a Control from a policy file (`$CTRLRUN_CONFIG`, else `./ctrlrun.yaml`).

        A missing or malformed policy raises `PolicyError`: a Control cannot be constructed
        without one (SPEC-v0.1 §3.4). State lands in `.ctrlrun/state.db` beside the policy —
        or wherever `$CTRLRUN_STATE` says — so approvals and effects outlive the process and
        are shared by every worker that loaded the same policy (§5.3 E1, §8).
        """
        policy = Policy.from_file(path)
        state = state_path(policy.source)
        store = SQLiteStateStore(state)
        # SPEC-v0.2 §4.3 — the two files of v0.1 §6 land exactly where v0.1 put them,
        # so an existing evidence directory is unchanged by the move out of the store.
        return cls(
            policy,
            store,
            LocalApprovalProvider(store),
            sinks=[JSONLEventSink(state.parent)],
            environment=environment,
        )

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
    def sinks(self) -> tuple[EventSink, ...]:
        """The sinks this Control fans out to, in registration order (SPEC-v0.2 §4.1)."""
        return self._sinks

    @property
    def lease(self) -> timedelta:
        """How long a reservation this Control takes is held for (SPEC-v0.1 §5.3 E3)."""
        return self._lease

    @property
    def identity(self) -> IdentityProvider | None:
        """The provider this Control resolves principals from, if any (SPEC-v0.3 §3.1)."""
        return self._identity

    @property
    def environment(self) -> str:
        """The environment every Action this Control decides carries (SPEC-v0.3 §2.5).

        Resolved once at construction and fixed for the life of the object, because a grant may
        scope to it (§4.2) and an authorization dimension the subject sets is not one.
        """
        return self._environment

    def evaluate(self, action: Action) -> Evaluation:
        """Decide an action. No side effects: nothing is recorded (SPEC-v0.1 §8).

        An expired principal is a `DENY` here rather than the refusal `execute` raises
        (SPEC-v0.3 §2.3): this method may not write, and a check defined only as side effects
        would otherwise have no meaning on the one path that may not have them. The gateway's
        approval pre-check reads this, so leaving it undefined would give three different
        gateway behaviours.
        """
        if self._is_expired(action.principal):
            return Evaluation(Decision.DENY, PRINCIPAL_EXPIRED)
        return self._policy.evaluate(action)

    def _is_expired(self, principal: Principal) -> bool:
        """SPEC-v0.3 §2.3. `expires_at is None` is the absence of a check, not one that passes."""
        return principal.expires_at is not None and self._clock() > principal.expires_at

    def _resolve_principal(self, action_name: str) -> Principal:
        """The principal for this action, per SPEC-v0.3 §3.2's table.

        The provider wins where it answers, because §4 makes the principal an authorization
        input and a self-asserted name cannot be one. A `None` is a *decline* and leaves the
        v0.1 `context()` path intact; a raise is a *refusal* and is never backfilled, since
        falling back there would turn a rejected credential into a successful action.
        """
        invocation = _CONTEXT.get(None)
        from_context = invocation.principal if invocation is not None else None
        if self._identity is None:
            if from_context is None:
                _refuse_no_principal(action_name)
            return from_context

        resolved = self._ask_provider(action_name, from_context)
        if resolved is None:
            # A decline. SPEC-v0.3 §3.2 adds one more row here in build-list item 2: once an
            # `authority:` section is loaded a decline is refused rather than backfilled,
            # because omitting a credential would otherwise be an easier route than forging
            # one. `Authority` does not exist yet, so that row lands with the code it needs.
            if from_context is None:
                _refuse_no_principal(action_name)
            return from_context

        if from_context is not None and not self._warned_about_principal:
            same = (from_context.agent, from_context.user) == (resolved.agent, resolved.user)
            if not same:
                self._warned_about_principal = True
                _LOG.warning(
                    "%s: the identity provider resolved %r and an active context() says %r; "
                    "the provider is in force (SPEC-v0.3 §3.2)",
                    action_name,
                    _named(resolved),
                    _named(from_context),
                )
        return resolved

    def _ask_provider(self, action_name: str, hint: Principal | None) -> Principal | None:
        assert self._identity is not None
        identity_context = IdentityContext(
            action=action_name,
            environment=self._environment,
            agent=hint.agent if hint is not None else None,
            user=hint.user if hint is not None else None,
        )
        try:
            return self._identity.resolve(identity_context)
        except IdentityError:
            # Already the exception a caller catches; re-wrapping would lose its message.
            raise
        except Exception as exc:
            # SPEC-v0.3 §3.2 — one exception type for a caller to catch, with the cause kept.
            # A `BaseException` that is not an `Exception` propagates untouched (v0.1 §5.5).
            _LOG.warning(
                "%s: identity provider %s raised %s: %s",
                action_name,
                type(self._identity).__name__,
                type(exc).__name__,
                exc,
            )
            raise IdentityError(f"{action_name}: the identity provider rejected this call") from exc

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
        self._check_environment(action)
        held = self._lease if lease is None else _checked_lease(lease, "execute(lease=...)")
        reconciler = _reconciler(reconcile, reconcile_eagerly, "execute")

        started_at = self._clock()
        self._append(
            EventType.ACTION_PROPOSED, action, {"action_hash": action.action_hash}, effect_key
        )
        if self._is_expired(action.principal):
            # SPEC-v0.3 §2.3 — before authority and before policy. There is a principal to
            # attribute the refusal to, so unlike a call outside context() it is recorded; and
            # it is refused before the approval gate, because a decision that can never be
            # honoured must not spend a human's attention.
            self._append(EventType.ACTION_DENIED, action, {"reason": PRINCIPAL_EXPIRED}, effect_key)
            self._record(
                action,
                Evaluation(Decision.DENY, PRINCIPAL_EXPIRED),
                ReceiptResult.DENIED,
                started_at,
                effect_key=effect_key,
            )
            raise IdentityError(
                f"{action.name}: the principal's credential expired at "
                f"{action.principal.expires_at}"
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
        return self._outcome(
            action, evaluation, executor, effect_key, attempt, approval, started_at, reconciler
        )

    def resume(self, continuation: str, executor: Callable[[], Any]) -> Receipt:
        """Finish an action a `Suspended` executor left open (SPEC-v0.2 §6.9).

        The continuation is taken and consumed atomically, so one suspension admits exactly
        one resumption; the action and its effect key are rehydrated from the store, which is
        what lets a process that restarted mid-round still finish one. The record must still
        be `EXECUTING` under a live lease — a lease that lapsed while the client was
        answering is `AMBIGUOUS` by the ordinary path of v0.1 §5.3 E3.

        The executor then runs through the **same** outcome mapping `execute` uses. There is
        one implementation of v0.1 §5.5 in this codebase, and a resumed call gets that one:
        a resumption that decided outcomes differently would be a second answer to the only
        question this library exists to answer.
        """
        held = self._store.take_continuation(continuation)
        started_at = self._clock()
        action = held.action
        # SPEC-v0.3 §2.5 — a continuation is a store-wide token, so a Control in another
        # environment can reach one. Evaluating a staging action inside a production
        # deployment is the fail-open §2.5 exists to close.
        self._check_environment(action)
        self._append(
            EventType.EXECUTION_RESUMED,
            action,
            {"round": held.rounds},
            held.effect_key,
        )
        # SPEC: §6.9.2 — the policy is evaluated again for the *receipt*, not to re-decide.
        # The action was decided and reserved on the first leg; refusing here would strand a
        # reservation the remote may already be acting on, which is the one thing a held
        # reservation exists to avoid.
        evaluation = self._policy.evaluate(action)
        return self._outcome(
            action,
            evaluation,
            executor,
            held.effect_key,
            held.record.attempt,
            None,
            started_at,
            _Reconciler(None, False),
        )

    def _outcome(
        self,
        action: Action,
        evaluation: Evaluation,
        executor: Callable[[], Any],
        effect_key: str | None,
        attempt: int,
        approval: Approval | None,
        started_at: datetime,
        reconciler: _Reconciler,
    ) -> Receipt:
        """Run the executor and record what happened (SPEC-v0.1 §5.5).

        The single implementation of the asymmetry: `NotExecuted` is the only outcome that
        maps to `FAILED`, `Suspended` records no outcome at all, and everything else is
        `AMBIGUOUS`. `execute` and `resume` both come through here, so the two cannot drift.
        """
        try:
            result = executor()
        except Suspended as suspension:
            # SPEC-v0.2 §6.9 — no outcome, no receipt: the remote has not said what happened
            # and this attempt is not finished. Handled above the generic branch precisely so
            # it cannot be mistaken for one.
            self._suspend(action, effect_key, suspension, approval)
            raise
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

    def _suspend(
        self,
        action: Action,
        effect_key: str | None,
        suspension: Suspended,
        approval: Approval | None,
    ) -> None:
        """Hold the reservation open and record that it is held (SPEC-v0.2 §6.9.2).

        With no effect key there is nothing to hold, and the event says so. Suspension
        without an effect key gives no protection at all — a second caller can start the same
        work while the first is waiting — exactly as retries without one give none (v0.1
        §5.1). It is permitted, logged, and recorded as unheld rather than silently pretended.
        """
        if effect_key is None:
            _LOG.warning(
                "%s: suspended with no effect key, so nothing is held; a concurrent caller "
                "can start the same work. Declare effect= to make a suspension mean something",
                action.name,
            )
            self._append(
                EventType.EXECUTION_SUSPENDED,
                action,
                {"held": False, "round": 1},
                None,
                approval=approval,
            )
            return
        rounds = self._store.hold_continuation(
            action, effect_key, suspension.continuation, self._clock() + self._suspend_timeout
        )
        self._append(
            EventType.EXECUTION_SUSPENDED,
            action,
            {"held": True, "round": rounds},
            effect_key,
            approval=approval,
        )

    # --- the effect key (SPEC-v0.1 §5.1) ------------------------------------------------

    def _check_environment(self, action: Action) -> None:
        """Refuse an Action built for another deployment (SPEC-v0.3 §2.5).

        `Action(...)` is public and `execute` is frozen API, so without this a caller sets the
        dimension a grant scopes to. `InvalidArgument` rather than a denial: it is a wiring
        bug, not a policy saying no, and `Action` is frozen so there is nothing to re-stamp.
        """
        if action.environment != self._environment:
            raise InvalidArgument(
                f"{action.name}: the action is for environment {action.environment!r} and this "
                f"Control is {self._environment!r}. The environment belongs to the deployment "
                "(SPEC-v0.3 §2.5); build the Action from this Control's environment, or use a "
                "Control for that one"
            )

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
        stored = self._store.append_event(
            Event(
                type=type_,
                action_id=action.action_id,
                ts=self._clock(),
                data=data,
                effect_key=effect_key,
                approval_id=approval_id if approval is None else approval.approval_id,
            )
        )
        self._fan_out("on_event", stored, str(stored.type))

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
        self._fan_out("on_receipt", receipt, receipt.receipt_id)
        return receipt

    def _fan_out(self, method: str, record: Event | Receipt, described: str) -> None:
        """Hand one record to every sink, in registration order (SPEC-v0.2 §4.1, §4.2).

        Called only after the authoritative store write for that record succeeded, so a sink
        that reads the store finds what it was just handed.

        A sink that raises is logged and skipped, and the remaining sinks still run: by the
        time this executes the effect has committed at the remote and the record is durable,
        and raising here would reach the caller as an exception on a successful action —
        which an agent reads as a failure, and retries. That is the one mistake this library
        exists to prevent, so it is not going to be introduced by a telemetry exporter.

        A `BaseException` that is not an `Exception` propagates, for the reason in v0.1 §5.5:
        swallowing a `KeyboardInterrupt` while exporting is worse than losing the export.
        """
        for sink in self._sinks:
            try:
                getattr(sink, method)(record)
            except Exception as exc:
                _LOG.warning(
                    "%s.%s(%s) failed and was skipped: %s",
                    type(sink).__name__,
                    method,
                    described,
                    exc,
                )


def _refuse_no_principal(action_name: str) -> NoReturn:
    """SPEC-v0.1 §2.1, unchanged by v0.3 §3.2.

    A call with no principal is a wiring bug, not an agent action: it is denied and warned
    about, but never enters the evidence log, which records actions and has no principal to
    attribute this one to.
    """
    _LOG.warning("%s: denied: no principal is available", action_name)
    raise ActionDenied(
        f"{action_name}: no principal is available; wrap the call in "
        "'with ctrlrun.context(agent=...)', or install an identity provider that answers",
        reason=NO_PRINCIPAL,
    )


def _named(principal: Principal) -> str:
    return principal.agent if principal.user is None else f"{principal.agent}/{principal.user}"


def _resolve_environment(argument: str | None, policy: Policy) -> str:
    """SPEC-v0.3 §2.5: the argument, else $CTRLRUN_ENVIRONMENT, else the document, else production.

    The environment-variable check runs whenever the variable is *present*, including when the
    argument won, so the refusal does not depend on how the Control happened to be constructed.
    """
    configured = os.environ.get(ENVIRONMENT_ENV_VAR)
    if configured is not None and not configured.strip():
        raise InvalidArgument(
            f"{ENVIRONMENT_ENV_VAR} is set but empty; unset it or name the deployment. Falling "
            "back would put actions in an environment nobody chose (SPEC-v0.3 §2.5)"
        )
    if argument is not None:
        if not argument:
            raise InvalidArgument("Control(environment=...) must be a non-empty string or None")
        return argument
    if configured is not None:
        return configured
    return policy.environment or DEFAULT_ENVIRONMENT


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
        compared: list[bool] = []
        if control is not None:
            # SPEC-v0.2 §3.2 — the warning lands when the function first resolves its
            # Control, which is here when one was passed: an operator who added templates to
            # a policy hears about the disagreement at import, not on the first agent run.
            _templates_in_force(control, name, effect, resource, compared)

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            # SPEC-v0.2 §3.2 — the Control resolves before the Action, because the policy
            # may be the only place a template is declared and `resource` is part of the
            # canonical form. The decorator's value wins where both exist. SPEC-v0.3 §3.2
            # adds a second reason: only the Control knows whether an identity provider is
            # installed, and the principal comes from it where one is.
            resolved = control if control is not None else _default_control()
            principal = resolved._resolve_principal(name)
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            effect_template, resource_template = _templates_in_force(
                resolved, name, effect, resource, compared
            )
            action = Action(
                name=name,
                arguments=dict(bound.arguments),
                principal=principal,
                # SPEC-v0.1 §5.1 — `resource` is part of the canonical form, so it resolves
                # before the Action exists; the effect template resolves against the Action.
                resource=(
                    None
                    if resource_template is None
                    else resolve_resource(resource_template, bound.arguments)
                ),
                # SPEC-v0.3 §2.5 — the deployment's, never the call's.
                environment=resolved.environment,
            )
            effect_key = resolved._resolve_effect(action, effect_template)
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


def _templates_in_force(
    resolved: Control,
    name: str,
    effect: str | None,
    resource: str | None,
    compared: list[bool],
) -> tuple[str | None, str | None]:
    """The `effect` and `resource` templates this action will use (SPEC-v0.2 §3.2).

    The decorator's value wins where both it and the policy declare one. The policy is a
    deployment artefact and the decorator is a statement in the code that will run; when
    they disagree, the code is what executes, and silently substituting the policy's
    template would change an effect identity without changing a line of the program.

    `compared` is the once-per-decorated-function latch for the warning, because a warning
    that repeats per call is a warning nobody reads.
    """
    from_policy_effect = resolved.policy.effect_template(name)
    from_policy_resource = resolved.policy.resource_template(name)
    if not compared:
        compared.append(True)
        _warn_template_mismatch(name, "effect", effect, from_policy_effect)
        _warn_template_mismatch(name, "resource", resource, from_policy_resource)
    return (
        effect if effect is not None else from_policy_effect,
        resource if resource is not None else from_policy_resource,
    )


def _warn_template_mismatch(
    name: str, kwarg: str, decorated: str | None, from_policy: str | None
) -> None:
    """Name the action, both templates, and which one is in force (SPEC-v0.2 §3.2).

    A warning and not an error: an operator adding templates to a policy for the gateway's
    sake must not break a running decorator-based deployment.
    """
    if decorated is None or from_policy is None or decorated == from_policy:
        return
    _LOG.warning(
        "%s: %s= is %r in the decorator and %r in the policy; the decorator's is in force, "
        "because that is the code that will run. Remove one of them.",
        name,
        kwarg,
        decorated,
        from_policy,
    )


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
