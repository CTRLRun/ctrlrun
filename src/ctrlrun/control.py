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
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Literal, NoReturn, ParamSpec, TypeVar, cast

from .action import Action, Principal
from .approval import (
    DEFAULT_APPROVAL_TTL,
    Approval,
    ApprovalProvider,
    ApprovalStatus,
    LocalApprovalProvider,
)
from .authority import Authority, AuthorityResult, Delegation, Grant, _optional_from_yaml
from .effect import (
    COMMITTED_EFFECT,
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
    AuthorityDenied,
    AuthorityEscalation,
    CTRLRunError,
    DuplicateEffect,
    EffectKeyError,
    IdentityError,
    InvalidArgument,
    NotExecuted,
    PolicyError,
    Suspended,
)
from .identity import IdentityContext, IdentityProvider
from .policy import OBSERVE, Decision, Evaluation, Policy, discover_policy_path
from .receipt import (
    BLOCKED_AMBIGUOUS,
    BLOCKED_APPROVAL_MISMATCH,
    BLOCKED_APPROVAL_REQUIRED,
    BLOCKED_DUPLICATE,
    BLOCKED_IN_PROGRESS,
    Event,
    EventSink,
    EventType,
    JSONLEventSink,
    Receipt,
    ReceiptResult,
    _WouldHave,
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


class _Observation:
    """One observe-mode action's counterfactual, assembled as it is evaluated (§6.3).

    Mutable and short-lived: `Control.execute` makes one per call, every check that would
    have stopped the action writes to it, and it is frozen into a `_WouldHave` on the receipt.
    A holder rather than a return value because the checks are spread across `execute`,
    `_observe_secure` and `_outcome`, and threading four extra values through all three would
    put the same fact in three signatures.

    `block()` keeps the **first** reason, because the checks run in the order enforce mode
    runs them and enforce mode stops at the first: a later refusal is one enforce mode would
    never have reached.
    """

    __slots__ = ("blocked_reason", "decision", "reason")

    def __init__(self) -> None:
        self.decision = Decision.ALLOW
        self.reason = ""
        self.blocked_reason: str | None = None

    def decided(self, evaluation: Evaluation) -> None:
        self.decision = evaluation.decision
        self.reason = evaluation.reason

    def block(self, reason: str) -> None:
        if self.blocked_reason is None:
            self.blocked_reason = reason

    def frozen(self) -> _WouldHave:
        return _WouldHave(
            decision=self.decision, reason=self.reason, blocked_reason=self.blocked_reason
        )


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
        authority: Authority | None = None,
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
        self._authority = authority
        self._environment = _resolve_environment(environment, policy)
        #: SPEC-v0.3 §6.1 — one switch, read once, governing the process. Not a parameter:
        #: the mode belongs to the configuration an operator deployed, and a Control that
        #: could be handed a different one would be a per-caller opt-out of enforcement.
        self._observing = policy.mode == OBSERVE
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
        # SPEC-v0.3 §4.1 — the same document, read again for its `authority:` section, and
        # `None` where it has none. Opt-in is the whole rule: a loader that returned an empty
        # `Authority` here would deny every action in a v0.2 configuration.
        authority = _optional_authority(policy.source)
        state = state_path(policy.source)
        store = SQLiteStateStore(state)
        # SPEC-v0.2 §4.3 — the two files of v0.1 §6 land exactly where v0.1 put them,
        # so an existing evidence directory is unchanged by the move out of the store.
        return cls(
            policy,
            store,
            LocalApprovalProvider(store),
            sinks=[JSONLEventSink(state.parent)],
            authority=authority,
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
    def authority(self) -> Authority | None:
        """The grants this Control evaluates against, if any (SPEC-v0.3 §4.1).

        `None` is v0.2 behaviour, exactly: no authority evaluation, no `AUTHORITY_*` event,
        no change to any decision. Not `None` means every principal needs a grant.
        """
        return self._authority

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
        # SPEC-v0.3 §4.3.1 — the environment obeys §2.5 on *every* row of that table, and
        # `evaluate` is one. Read-only, so this refuses rather than denies: an Action from
        # another deployment is a wiring bug, and answering "what would happen to this" for a
        # Control that would never run it is answering a different question.
        self._check_environment(action)
        if self._is_expired(action.principal):
            return Evaluation(Decision.DENY, PRINCIPAL_EXPIRED)
        # SPEC-v0.3 §4.6 — the combined decision, not the policy axis alone. This method is
        # the public "what will happen to this action" query and it would stop answering that
        # question if it reported one axis while `execute` acted on both. It reads the store
        # to resolve delegations and still writes nothing.
        result = self._authority_result(action)
        if result is not None and not result.passed:
            return Evaluation(Decision.DENY, result.reason)
        return self._policy.evaluate(action)

    def _authority_result(self, action: Action) -> AuthorityResult | None:
        """The authority axis for this action, or `None` where there is no section (§4.1)."""
        if self._authority is None:
            return None
        return self._authority.evaluate(action, now=self._clock(), store=self._store)

    def _authority_data(self, result: AuthorityResult) -> dict[str, Any]:
        """SPEC-v0.3 §7 — the ids travel here, and never in `decision_reason`.

        A grant may legally be named `no_authority`, so evidence that could be spoofed by
        naming a grant is not evidence. Absent keys are omitted rather than written `null`:
        `grant_id` is present "where one grant was implicated", and `no_authority` implicates
        none.
        """
        data: dict[str, Any] = {"reason": result.reason}
        if result.grant_id is not None:
            data["grant_id"] = result.grant_id
        if result.delegation_id is not None:
            data["delegation_id"] = result.delegation_id
            data["depth"] = result.depth
        # SPEC-v0.3 §7 — the keys that tell §5.6's rules apart. Rules 1, 3, 4, 5 and 6 all
        # report `authority_escalation`, so without these five guards would be one guard as
        # far as any reader or test could tell.
        for key in ("dimension", "missing_parent_id", "expired_parent_id", "cycle_at"):
            value = getattr(result, key)
            if value is not None:
                data[key] = value
        if result.depth_exceeded is not None:
            data["depth_exceeded"] = result.depth_exceeded
        return data

    def _refuse_authority(
        self, action: Action, result: AuthorityResult, started_at: datetime, effect_key: str | None
    ) -> NoReturn:
        """Record an authority denial and raise (SPEC-v0.3 §4.3).

        `AUTHORITY_DENIED` then `ACTION_DENIED`, and **no** `POLICY_EVALUATED`: policy is never
        evaluated, so no approval request is created and no human is left staring at a request
        for an action that could never run.
        """
        self._append(EventType.AUTHORITY_DENIED, action, self._authority_data(result), effect_key)
        self._append(EventType.ACTION_DENIED, action, {"reason": result.reason}, effect_key)
        self._record(
            action,
            Evaluation(Decision.DENY, result.reason),
            ReceiptResult.DENIED,
            started_at,
            effect_key=effect_key,
        )
        raise AuthorityDenied(
            f"{action.name} denied: {result.reason}",
            reason=result.reason,
            action_id=action.action_id,
            grant_id=result.grant_id,
            delegation_id=result.delegation_id,
        )

    def _is_expired(self, principal: Principal) -> bool:
        """SPEC-v0.3 §2.3. `expires_at is None` is the absence of a check, not one that passes."""
        return principal.expires_at is not None and self._clock() > principal.expires_at

    def resolve_principal(self, action_name: str) -> Principal:
        """The principal for this action, per SPEC-v0.3 §3.2's table.

        The provider wins where it answers, because §4 makes the principal an authorization
        input and a self-asserted name cannot be one. A `None` is a *decline* and leaves the
        v0.1 `context()` path intact; a raise is a *refusal* and is never backfilled, since
        falling back there would turn a rejected credential into a successful action.

        Public since SPEC-v0.5 §9, promoted for `ctrlrun.adapter.needs_approval`: a framework
        that asks whether a tool call needs approval *before* it invokes the tool needs an
        `Action`, an `Action` needs a `Principal`, and the only other place one could come from
        is the framework's own session -- which is `--principal-from-client-info` (SPEC-v0.3
        §8.1) in a third costume. This is the seam an adapter may **read** and may not supply.
        It adds no capability: `@protect` has resolved principals this way since v0.3, and this
        method writes nothing, appends nothing and creates no request.
        """
        invocation = _CONTEXT.get(None)
        from_context = invocation.principal if invocation is not None else None
        if self._identity is None:
            if from_context is None:
                _refuse_no_principal(action_name)
            return from_context

        resolved = self._ask_provider(action_name, from_context)
        if resolved is None:
            # A decline, and SPEC-v0.3 §3.2's last row: once an `authority:` section is loaded
            # a decline is refused rather than backfilled. Otherwise agent code already running
            # inside `with context("finance-agent")` executes every un-credentialed call as
            # finance-agent, with finance-agent's grants — omitting a credential reaches the
            # same destination as forging one, by an easier route. This is not a flag and there
            # is nothing to configure: it follows from §4.1's opt-in rule.
            if from_context is None or self._authority is not None:
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
        # SPEC-v0.3 §6.2 — the counterfactual for an observed run, or `None` in enforce mode.
        # Every branch below reads it to choose between refusing and recording.
        observation = _Observation() if self._observing else None
        self._append(
            EventType.ACTION_PROPOSED, action, {"action_hash": action.action_hash}, effect_key
        )
        if self._is_expired(action.principal):
            # SPEC-v0.3 §2.3 — before authority and before policy. There is a principal to
            # attribute the refusal to, so unlike a call outside context() it is recorded; and
            # it is refused before the approval gate, because a decision that can never be
            # honoured must not spend a human's attention.
            expired = Evaluation(Decision.DENY, PRINCIPAL_EXPIRED)
            if observation is not None:
                # §6.2's first bullet — evaluated and recorded, not enforced. Authority and
                # policy stay unevaluated, because enforce mode stops here and observe mode
                # records what enforce mode *would have reached*, not a deeper answer it
                # never gets to.
                observation.decided(expired)
                observation.block(PRINCIPAL_EXPIRED)
                return self._observed(
                    action, expired, executor, effect_key, started_at, observation, reconciler, held
                )
            self._append(EventType.ACTION_DENIED, action, {"reason": PRINCIPAL_EXPIRED}, effect_key)
            self._record(
                action,
                expired,
                ReceiptResult.DENIED,
                started_at,
                effect_key=effect_key,
            )
            raise IdentityError(
                f"{action.name}: the principal's credential expired at "
                f"{action.principal.expires_at}"
            )
        # SPEC-v0.3 §4.3.1 — the order, stated once so it can be tested: principal_expired →
        # authority → policy → approval → reservation → execution.
        result = self._authority_result(action)
        if result is not None:
            if not result.passed:
                if observation is not None:
                    # §6.2 — `AUTHORITY_DENIED` is appended exactly as in enforce mode,
                    # including §4.3's rule that a denial by authority skips policy. What is
                    # *not* appended is `ACTION_DENIED`: the action ran.
                    denial = Evaluation(Decision.DENY, result.reason)
                    self._append(
                        EventType.AUTHORITY_DENIED,
                        action,
                        self._authority_data(result),
                        effect_key,
                    )
                    observation.decided(denial)
                    observation.block(result.reason)
                    return self._observed(
                        action,
                        denial,
                        executor,
                        effect_key,
                        started_at,
                        observation,
                        reconciler,
                        held,
                    )
                self._refuse_authority(action, result, started_at, effect_key)
            self._append(
                EventType.AUTHORITY_RESOLVED, action, self._authority_data(result), effect_key
            )
        evaluation = self._policy.evaluate(action)
        self._append(
            EventType.POLICY_EVALUATED,
            action,
            {"decision": str(evaluation.decision), "reason": evaluation.reason},
            effect_key,
        )
        if observation is not None:
            observation.decided(evaluation)

        if evaluation.decision is Decision.DENY:
            if observation is not None:
                # SPEC: §6.3 lists `unknown_action` and `no_matching_rule` among
                # `blocked_reason`'s values and stops there, but a policy also denies by a
                # `rule[N]` and by a bare `decision`. The reading taken is the uniform one —
                # a denial's `blocked_reason` **is** its `decision_reason` — because that is
                # what makes §6.3's two named values right, and inventing a `policy_denied`
                # bucket for the other two would leave `ctrlrun stats` unable to say which
                # rule an operator should look at. The vocabulary stays closed per document:
                # every value is a reason some check actually produced.
                observation.block(evaluation.reason)
                return self._observed(
                    action,
                    evaluation,
                    executor,
                    effect_key,
                    started_at,
                    observation,
                    reconciler,
                    held,
                )
            self._append(EventType.ACTION_DENIED, action, {"reason": evaluation.reason}, effect_key)
            self._record(
                action, evaluation, ReceiptResult.DENIED, started_at, effect_key=effect_key
            )
            raise ActionDenied(
                f"{action.name} denied: {evaluation.reason}",
                reason=evaluation.reason,
                action_id=action.action_id,
            )
        if observation is not None:
            return self._observed(
                action, evaluation, executor, effect_key, started_at, observation, reconciler, held
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
            action,
            evaluation,
            executor,
            effect_key,
            attempt,
            approval,
            started_at,
            reconciler,
            held_key=effect_key,
        )

    # --- observe mode (SPEC-v0.3 §6) ----------------------------------------------------

    def _observed(
        self,
        action: Action,
        evaluation: Evaluation,
        executor: Callable[[], Any],
        effect_key: str | None,
        started_at: datetime,
        observation: _Observation,
        reconciler: _Reconciler,
        lease: timedelta,
    ) -> Receipt:
        """Run an action observe mode has finished deciding about (SPEC-v0.3 §6.2).

        Reached from four places in `execute` — an expired principal, an authority denial, a
        policy denial, and a decision that would have let the action through — because all
        four execute in observe mode and only the counterfactual differs. The reservation is
        attempted from every one of them: observe mode *executes*, and an attempt that runs
        an effect must own its key where it can.
        """
        approval, reservation = self._observe_secure(
            action, evaluation, effect_key, lease, observation
        )
        held_key = None if reservation is None else effect_key
        attempt = 1 if reservation is None else reservation.attempt
        if held_key is not None:
            try:
                self._store.begin_execution(held_key, action.action_id)
            except (DuplicateEffect, AmbiguousEffect) as refused:
                # The key was taken from under this attempt between winning it and starting.
                # Nothing is refused here, so the attempt goes on to run holding nothing —
                # and §6.2's rule applies: the record belongs to whoever holds the key.
                self._append(
                    EventType.EFFECT_RESERVATION_REFUSED,
                    action,
                    _refusal_data(refused),
                    effect_key,
                )
                observation.block(_blocked_by(refused))
                held_key = None
        self._append(EventType.EXECUTION_STARTED, action, {}, effect_key, approval=approval)
        return self._outcome(
            action,
            evaluation,
            executor,
            effect_key,
            attempt,
            approval,
            started_at,
            reconciler,
            held_key=held_key,
            observation=observation,
        )

    def _observe_secure(
        self,
        action: Action,
        evaluation: Evaluation,
        effect_key: str | None,
        lease: timedelta,
        observation: _Observation,
    ) -> tuple[Approval | None, Reservation | None]:
        """Attempt what `_secure` takes, record every refusal, and hold nothing it lost.

        A separate implementation from `_secure` rather than a flag through it, because the
        two differ in almost every branch: this one writes no receipt, raises nothing, and
        never reconciles. It can only ever end up holding **less** than `_secure` would, so
        the fail-closed direction is preserved by construction.

        Two things it deliberately does not do (SPEC-v0.3 §6.2):

        - **It creates no approval request.** A policy reaching `approve` with nothing
          presented is recorded and the action runs. Paging a human about an action that is
          about to execute regardless of the answer produces a queue, not evidence.
        - **It never calls the `reconcile` hook.** The blocking trigger of `v0.2 §2.3` fires
          when an `AMBIGUOUS` record refuses a reservation; here that refusal is ignored, so
          there is nothing being unblocked — and the hook's `"committed"` answer would move a
          record a human may still be adjudicating.
        """
        approval_id = None
        if evaluation.decision is Decision.APPROVE:
            approval_id = _PRESENTED_APPROVAL.get(None)
            if approval_id is None:
                observation.block(BLOCKED_APPROVAL_REQUIRED)
        if approval_id is None and effect_key is None:
            return None, None
        try:
            approval, reservation = self._take(action, approval_id, effect_key, lease)
        except (DuplicateEffect, AmbiguousEffect) as refused:
            observation.block(_blocked_by(refused))
            self._append(
                EventType.EFFECT_RESERVATION_REFUSED,
                action,
                _refusal_data(refused),
                effect_key,
                approval_id=approval_id,
            )
            return None, None
        except ApprovalMismatch as mismatch:
            # A *presented* approval that does not authorize this action. §6.2 lists
            # `ApprovalMismatch` among the exceptions observe mode does not raise, so it is
            # recorded and the action runs; the approval is left unconsumed either way.
            observation.block(BLOCKED_APPROVAL_MISMATCH)
            self._append(
                EventType.APPROVAL_INVALIDATED,
                action,
                {"reason": mismatch.reason, "action_hash": action.action_hash},
                effect_key,
                approval_id=approval_id,
            )
            return None, None
        except ActionDenied as denied:
            # The store raises this where the record says a human refused the approval that
            # was presented. §4.2 makes that a denial of the action, and the reason is the
            # one `_secure` records; observe mode records it and runs.
            observation.block(denied.reason)
            self._append(
                EventType.APPROVAL_DENIED,
                action,
                {"approver": self._approver_of(approval_id)},
                effect_key,
                approval_id=approval_id,
            )
            return None, None
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
        #
        # SPEC-v0.3 §5.6.1 gives authority the same treatment, and for a sharper reason: this
        # is the *only* receipt an MCP multi round-trip or ACS action ever gets (§8.3), so a
        # receipt reporting a bare policy reason would be the whole evidence for that action.
        result = self._authority_result(action)
        if result is None:
            evaluation = self._policy.evaluate(action)
        elif result.passed:
            self._append(
                EventType.AUTHORITY_RESOLVED, action, self._authority_data(result), held.effect_key
            )
            evaluation = self._policy.evaluate(action)
        else:
            self._append(
                EventType.AUTHORITY_DENIED, action, self._authority_data(result), held.effect_key
            )
            evaluation = Evaluation(Decision.DENY, result.reason)
        # SPEC-v0.3 §6.3 — a resumption in observe mode gets the same `observed` receipt its
        # first leg did. It is the *only* receipt an MCP multi round-trip ever gets (§8.3), so
        # a resumed leg reporting `committed` under a mode that enforces nothing would put the
        # one piece of evidence for that action in the wrong vocabulary. The reservation was
        # taken on the first leg and is still held, so nothing is blocked here.
        observation: _Observation | None = None
        if self._observing:
            observation = _Observation()
            observation.decided(evaluation)
            if evaluation.decision is Decision.DENY:
                observation.block(evaluation.reason)
        return self._outcome(
            action,
            evaluation,
            executor,
            held.effect_key,
            held.record.attempt,
            None,
            started_at,
            _Reconciler(None, False),
            held_key=held.effect_key,
            observation=observation,
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
        *,
        held_key: str | None,
        observation: _Observation | None = None,
    ) -> Receipt:
        """Run the executor and record what happened (SPEC-v0.1 §5.5).

        The single implementation of the asymmetry: `NotExecuted` is the only outcome that
        maps to `FAILED`, `Suspended` records no outcome at all, and everything else is
        `AMBIGUOUS`. `execute` and `resume` both come through here, so the two cannot drift.

        `effect_key` names the key on every event and on the receipt; `held_key` is the key
        this attempt actually **reserved**, and the only one it may write an outcome to. They
        differ in exactly one case: an observe-mode attempt whose reservation was refused
        (SPEC-v0.3 §6.2). The record belongs to the attempt that holds the key, and observe
        mode does not make a second attempt its owner — `commit_effect` and its siblings admit
        only the holder (v0.1 §5.3 E1), and a record in `AMBIGUOUS` is a human's to move.

        `observation` turns every terminal receipt below into an `observed` one carrying what
        the executor did and what enforce mode would have done (§6.3).
        """
        try:
            result = executor()
        except Suspended as suspension:
            # SPEC-v0.2 §6.9 — no outcome, no receipt: the remote has not said what happened
            # and this attempt is not finished. Handled above the generic branch precisely so
            # it cannot be mistaken for one.
            #
            # SPEC-v0.3 §6.2 — with no reservation held there is no lease to extend and no
            # continuation to hold, which is §6.9.3's no-effect-key path reached for a
            # different reason. `held_key` says so.
            self._suspend(action, held_key, suspension, approval)
            raise
        except NotExecuted as exc:
            if held_key is not None:
                # SPEC §5.5 — the executor asserted the remote side did nothing, so this is
                # the one outcome that leaves the key retryable (§5.4).
                try:
                    self._store.fail_effect(held_key, action.action_id, str(exc))
                except (DuplicateEffect, AmbiguousEffect) as refused:
                    # SPEC: §5.2 — the record moved on while the executor ran: this attempt's
                    # lease lapsed and another declared the effect AMBIGUOUS, which only a
                    # human moves it out of. The store's refusal is what propagates, not the
                    # NotExecuted: an agent that caught that would retry a key it lost.
                    self._unrecorded(
                        action, evaluation, started_at, held_key, attempt, refused, approval
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
                observation=observation,
            )
            raise
        except BaseException as exc:
            # SPEC: §5.5 — anything that is not NotExecuted is an AMBIGUOUS outcome, never
            # FAILED, and "anything" means BaseException: a KeyboardInterrupt mid-request
            # leaves the same unknown outcome a timeout does. Narrowing this to Exception
            # is a regression, not a cleanup.
            error = f"{type(exc).__name__}: {exc}"
            recorded = effect_key is None
            if held_key is not None:
                try:
                    self._store.mark_ambiguous(held_key, action.action_id, error)
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
                observation=observation,
            )
            raise
        if held_key is not None:
            try:
                self._store.commit_effect(held_key, action.action_id, result)
            except (DuplicateEffect, AmbiguousEffect) as refused:
                # SPEC: §5.2 — the executor returned, but the key is no longer this attempt's
                # to commit: the lease lapsed and the record is AMBIGUOUS until a human says
                # otherwise. What happened at the remote is now as unknown as a timeout, so
                # the attempt is recorded `ambiguous` rather than committed.
                self._unrecorded(
                    action, evaluation, started_at, held_key, attempt, refused, approval
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
            observation=observation,
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
        if self._is_expired(action.principal):
            # SPEC-v0.3 §2.3.1 — holding a continuation extends a lease, and an extension is a
            # request to keep holding authority the credential no longer carries. Refusing lets
            # the lease lapse, and a lapsed lease is AMBIGUOUS (v0.1 §5.3 E3): the safe state,
            # reached by the ordinary path. The effect record is deliberately not moved here —
            # the expiring lease does that.
            self._append(EventType.ACTION_DENIED, action, {"reason": PRINCIPAL_EXPIRED}, effect_key)
            raise IdentityError(
                f"{action.name}: the principal's credential expired at "
                f"{action.principal.expires_at}, so the reservation is not held across the "
                "round trip; the lease will lapse and the record becomes AMBIGUOUS"
            )
        result = self._authority_result(action)
        if result is not None and not result.passed:
            # SPEC-v0.3 §5.6.1 — an extension asks to keep holding a reservation the grant no
            # longer authorizes. Refused, and the record is deliberately not moved: the lease
            # lapses and it becomes AMBIGUOUS by the ordinary path of v0.1 §5.3 E3, which is
            # the safe state reached without inventing one. Without this, §5.7's "a chain of
            # any depth is cut by one write" is false for exactly the actions in flight when
            # an operator hits the switch.
            self._append(EventType.ACTION_DENIED, action, self._authority_data(result), effect_key)
            raise AuthorityDenied(
                f"{action.name}: authority no longer covers this action ({result.reason}), so "
                "the reservation is not held across the round trip; the lease will lapse and "
                "the record becomes AMBIGUOUS",
                reason=result.reason,
                action_id=action.action_id,
                grant_id=result.grant_id,
                delegation_id=result.delegation_id,
            )
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
        # §5.3's table: `reconcile:<action name>`. The bare kind would make two hooks
        # reconciling two different actions one indistinguishable string, which is the finer
        # distinction the section promises and item 8's soak is told it has.
        resolver = f"{RESOLVED_BY_RECONCILE}:{action.name}"
        try:
            self._store.resolve_effect(effect_key, state, resolver)
        except CTRLRunError as refused:
            # The record moved out of `AMBIGUOUS` between the refusal and the answer — a
            # human, or another process, got there first. Their resolution stands; this one
            # is dropped, which is the same nothing `"unknown"` would have done.
            _LOG.warning("%s: reconcile(%s) not applied: %s", action.name, effect_key, refused)
            return False
        self._append(
            EventType.EFFECT_RESOLVED,
            action,
            # Two keys, each meaning one thing on **both** paths (§5.3): `resolver` is the
            # string on the record, and `resolved_by` is the authority's kind. They used to be
            # the same value here and different values in the CLI, which coincided for the hook
            # and so hid that a reader could not join events to records on either one.
            {
                "state": str(state),
                "resolver": resolver,
                "resolved_by": RESOLVED_BY_RECONCILE,
            },
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

    # --- delegation (SPEC-v0.3 §5) ------------------------------------------------------

    def delegate(self, parent_id: str, grant: Grant, *, by: Principal) -> Delegation:
        """Create a delegated grant beneath `parent_id`, or refuse (SPEC-v0.3 §5.3).

        `by` is the principal creating it, and §5.3 rule 4 checks it against the parent grant's
        subject: you may only delegate authority you hold. It is checked, not authenticated —
        on the `ctrlrun delegate` path it came from a shell, which is why the record carries
        `created_via` (§5.7).

        Every refusal writes no record and appends `DELEGATION_REJECTED`; a success writes the
        row and appends `DELEGATION_CREATED`, which fans out to every registered sink. Creation
        is deliberately **not** idempotent: two identical calls make two delegations, each
        revocable on its own, because a delegation is an act and collapsing two acts into one
        record would lose which one a receipt refers to (§5.2).
        """
        return self._delegate(parent_id, grant, by=by, via="api")

    def revoke(self, delegation_id: str, *, by: str | None = None) -> None:
        """Revoke one delegation (SPEC-v0.3 §5.7).

        Transitive **by structure**: nothing is rewritten and no children are visited, because
        §5.6 rule 2 walks to the root on every evaluation. A chain of any depth is cut by one
        write. Not reversible — there is no `unrevoke` — and idempotent: revoking an
        already-revoked delegation logs and appends no second event.
        """
        authority = self._require_authority("revoke")
        now = self._clock()
        delegation = authority.plan_revocation(delegation_id, by=by, store=self._store, now=now)
        if not self._store.revoke_delegation(delegation_id, by=by, at=now):
            _LOG.info(
                "delegation %s was already revoked at %s",
                delegation_id,
                delegation.revoked_at,
            )
            return
        self._append_delegation(
            EventType.DELEGATION_REVOKED,
            {"delegation_id": delegation_id, "revoked_by": by},
        )

    def _delegate(
        self, parent_id: str, grant: Grant, *, by: Principal, via: Literal["api", "cli"]
    ) -> Delegation:
        """The one implementation behind `Control.delegate` and `ctrlrun delegate`.

        `via` is not on the public signature `SPEC-v0.3 §11` freezes; it is the one thing the
        CLI path needs to say that the API path does not, and §5.7 requires the record to keep
        the two apart so a reader of the evidence can tell an act from an assertion.
        """
        authority = self._require_authority("delegate")
        try:
            planned = authority.plan_delegation(
                parent_id, grant, by=by, store=self._store, now=self._clock()
            )
        except IdentityError:
            # §5.3 rule 0 — recorded with the reason `Control.execute` uses for the same fact.
            self._append_delegation(
                EventType.DELEGATION_REJECTED,
                {"reason": PRINCIPAL_EXPIRED, "parent_id": parent_id},
            )
            raise
        except AuthorityEscalation as escalation:
            data: dict[str, Any] = {"reason": escalation.reason, "parent_id": parent_id}
            if escalation.dimension is not None:
                # §7 — present *only* for a rule-6 containment refusal. The other five name no
                # §5.4 row, and inventing a dimension for them would make two distinguishable
                # guards report the same shape.
                data["dimension"] = escalation.dimension
            self._append_delegation(EventType.DELEGATION_REJECTED, data)
            raise
        delegation = replace(planned, created_via=via)
        self._store.put_delegation(delegation.to_record())
        self._append_delegation(
            EventType.DELEGATION_CREATED,
            {
                "delegation_id": delegation.delegation_id,
                "parent_id": delegation.parent_id,
                "depth": delegation.depth,
                "created_by_agent": by.agent,
                "created_by_user": by.user,
                "created_via": via,
            },
        )
        return delegation

    def _require_authority(self, what: str) -> Authority:
        if self._authority is None:
            raise InvalidArgument(
                f"this Control has no authority section, so there is nothing to {what}; "
                "load a document with an 'authority:' key (SPEC-v0.3 §4.1)"
            )
        return self._authority

    def _append_delegation(self, type_: EventType, data: Mapping[str, Any]) -> None:
        """Append one of §7's three action-less events and fan it out.

        `action_id` is `None`: these are about an authority record, created and revoked outside
        any action's life. `Control` appends them and calls every sink, for `v0.2 §4.1`'s
        reason — the highest-privilege operations in the release must not be the only ones
        missing from the export path.
        """
        stored = self._store.append_event(
            Event(type=type_, action_id=None, ts=self._clock(), data=data)
        )
        self._fan_out("on_event", stored, str(stored.type))

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
        observation: _Observation | None = None,
    ) -> Receipt:
        # SPEC-v0.3 §6.3 — one place turns a terminal outcome into an observed receipt, so
        # `result`, `execution` and `would_have` cannot disagree about the same action. The
        # still-refuses rows of §6.2 pass no observation and keep their `denied` receipt with
        # both new fields null, which is what makes "would_have present on every observed run
        # and absent on every refused one" true in both directions.
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
            result=ReceiptResult.OBSERVED if observation is not None else result,
            execution=None if observation is None else result,
            would_have=None if observation is None else observation.frozen(),
            started_at=started_at,
            finished_at=self._clock(),
            error=error,
            # SPEC-v0.6 §7.1 — what decided this action, on every receipt. The hash is the
            # policy's content and the version is the operator's label for it; §7.1 makes the
            # first authoritative and says the second never is.
            policy_hash=self._policy.policy_hash,
            policy_version=self._policy.version,
            controls=evaluation.controls,
        )
        # The store assigns `seq`, `prev_hash` and `hash` (SPEC-v0.6 §6.2, §6.3), so what goes
        # to the sinks and back to the caller is the **chained** receipt. Handing the unchained
        # one to a sink would put a document with no `seq` in the JSONL export, and §6.4's claim
        # that the export is verifiable by recomputation rests on every document carrying its
        # own place in the chain. `append_event` has returned its stored record since v0.2 for
        # the same reason.
        chained = self._store.put_receipt(receipt)
        self._fan_out("on_receipt", chained, chained.receipt_id)
        return chained

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
        if not argument.strip():
            raise InvalidArgument("Control(environment=...) must be a non-empty string or None")
        return argument.strip()
    if configured is not None:
        # Stripped, and blank-after-strip refused above, so all three ranks answer the same
        # question the same way. An unstripped value would also never match a grant scoped to
        # `["staging"]`, and the mismatch would be invisible.
        return configured.strip()
    return (policy.environment or DEFAULT_ENVIRONMENT).strip()


def _refusal_data(refused: DuplicateEffect | AmbiguousEffect) -> dict[str, str]:
    """Why a reservation was refused, for `EFFECT_RESERVATION_REFUSED` (SPEC §5.4, §6.2)."""
    if isinstance(refused, DuplicateEffect):
        return {"reason": "duplicate", "state": refused.state}
    return {"reason": "ambiguous"}


def _blocked_by(refused: DuplicateEffect | AmbiguousEffect) -> str:
    """The same refusal in `would_have.blocked_reason`'s vocabulary (SPEC-v0.3 §6.3).

    `duplicate` and `in_progress` are kept apart because one says the effect happened and the
    other says another attempt holds the key right now — a rollout report that merged them
    would count a contended key as a repeated payment.
    """
    if isinstance(refused, AmbiguousEffect):
        return BLOCKED_AMBIGUOUS
    return BLOCKED_DUPLICATE if refused.state == COMMITTED_EFFECT else BLOCKED_IN_PROGRESS


def _optional_authority(source: str) -> Authority | None:
    """The `authority:` section of the policy document, or `None` (SPEC-v0.3 §4.1).

    Reads the file a second time rather than threading the parsed document through `Policy`:
    `policy.py` must not learn what an `authority:` section means (`ARCHITECTURE.md` §6), and
    a configuration file is read once at startup. Called only from `Control.from_file`, which
    has just loaded a `Policy` from this path — so a read that fails here is a `PolicyError`
    and never a silent `None`. "The file went away, so run every action unchecked" is the
    fail-open direction, and §4.1 has no half-way.
    """
    try:
        text = Path(source).read_text(encoding="utf-8")
    except OSError as exc:
        raise PolicyError(
            f"the policy at {source} loaded but could not be read again for its 'authority:' "
            f"section: {exc}"
        ) from exc
    return _optional_from_yaml(text, source=source)


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
            if control is None and _CONTEXT.get(None) is None:
                # SPEC-v0.1 §2.1's ordering, kept. Only a Control can hold an identity
                # provider, and `_default_control()` builds one from a file, which never has
                # one — so with no Control passed and no context there is nothing that could
                # answer, and the refusal must not be preceded by a PolicyError about a file
                # this call never needed.
                _refuse_no_principal(name)
            resolved = control if control is not None else _default_control()
            principal = resolved.resolve_principal(name)
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
