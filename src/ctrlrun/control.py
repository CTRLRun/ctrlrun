# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""The @protect decorator, Control and the ambient action context. Build-list item 3.

`Control` is the only place the other modules are composed (ARCHITECTURE §6): it turns a
function call into an Action, decides it against the policy, runs the executor, and records
what happened. SPEC-v0.1 §8 freezes the names here.
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import logging
import os
import threading
import weakref
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import IntEnum
from pathlib import Path
from typing import Any, Final, NoReturn, ParamSpec, TypeVar, cast

from .action import Action, Principal, canonical_bytes
from .anchor import AnchorProvider
from .approval import (
    APPROVAL_UNRECORDED,
    APPROVALS_UNVERIFIABLE,
    APPROVER_IS_REQUESTER,
    APPROVER_UNENTITLED,
    APPROVER_UNVERIFIED,
    DEFAULT_APPROVAL_TTL,
    Approval,
    ApprovalProvider,
    ApprovalRecord,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalVerdict,
    ApproverIdentity,
    LocalApprovalProvider,
    RequiredRole,
    VerifiedApprover,
    _precondition_at_request,
    _precondition_fingerprint,
    _required_roles,
    check_consumable,
    entitled_controls,
    policy_in_force,
    roles_held,
    unsatisfied,
)
from .authority import (
    _DELEGATION_ID,
    RESOURCE_SEPARATOR,
    Authority,
    AuthorityResult,
    BreakGlassEnvelope,
    CreatedVia,
    Delegation,
    Grant,
    _optional_from_yaml,
    matches,
)
from .effect import (
    _EXECUTOR_RUN,
    COMMITTED_EFFECT,
    DEFAULT_LEASE,
    RECONCILED_STATES,
    RECONCILED_UNKNOWN,
    RESOLVED_BY_RECONCILE,
    UNRESOLVED_EFFECT,
    EffectState,
    ReconcileOutcome,
    Reservation,
    _closed,
    _ExecutorRun,
    _opened,
    idempotency_token_for,
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
from .policy import (
    DEFAULT_ENVIRONMENT,
    OBSERVE,
    POLICY_CHANGE_ACTION,
    POLICY_UNAPPROVED,
    UPSTREAM_UNVERIFIED,
    Decision,
    Evaluation,
    Policy,
    discover_policy_path,
    hash_with_authority,
)
from .receipt import (
    BLOCKED_AMBIGUOUS,
    BLOCKED_APPROVAL_REQUIRED,
    BLOCKED_ATTEMPT_CEILING,
    BLOCKED_DUPLICATE,
    BLOCKED_IN_PROGRESS,
    KNOWN_RECEIPT_SCHEMAS,
    Event,
    EventSink,
    EventType,
    JSONLEventSink,
    Receipt,
    ReceiptResult,
    UnreadableReceipt,
    _WouldHave,
    iso_timestamp,
    new_receipt_id,
)
from .state import (
    BudgetExhaustedError,
    Charge,
    ClockSkew,
    SQLiteStateStore,
    StateStore,
    check_charges,
)

_LOG = logging.getLogger(__name__)

#: SPEC-v0.7 §3.6. Which kinds of unusable `clock_skew` value each store has already been logged
#: for: once per store per kind, not once per action, which would flood a log exactly as the
#: event's own rate limit exists to avoid. Keyed weakly so a store's entry goes with the store;
#: a store that cannot be weakly referenced falls back to the reading `Control`'s own set.
_SKEW_WARNED: weakref.WeakKeyDictionary[Any, set[str]] = weakref.WeakKeyDictionary()
_SKEW_WARNED_LOCK = threading.Lock()
_SKEW_NOT_A_MEASUREMENT: Final = "not a ClockSkew"
_SKEW_READ_RAISED: Final = "read raised"
_SKEW_APPEND_FAILED: Final = "append failed"
#: The event's numbers are integer microseconds, exact, so a reader sees what the decision used.
_MICROSECOND: Final = timedelta(microseconds=1)

P = ParamSpec("P")
R = TypeVar("R")


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

#: SPEC-v0.7 §6.2: the three reasons a precondition refuses, each a value of an existing field
#: (`ApprovalMismatch.reason`, `APPROVAL_INVALIDATED.data.reason`, and on the request pass
#: `ActionDenied.reason`). Distinct because a precondition refusal and an ordinary
#: `ApprovalMismatch` share a type, and a test asserting only the type could not tell which
#: guard fired. Private: §9.2 adds no public name for them, and every test asserts the string.
_PRECONDITION_CHANGED: Final = "precondition_changed"
_PRECONDITION_MISSING: Final = "precondition_missing"
_PRECONDITION_UNAVAILABLE: Final = "precondition_unavailable"
_PRECONDITION_REASONS: Final = frozenset(
    {_PRECONDITION_CHANGED, _PRECONDITION_MISSING, _PRECONDITION_UNAVAILABLE}
)

#: SPEC-v0.7 §6.4 — who a request withdrawn by the kernel was answered by. Not a human and not
#: a policy: the fingerprint the request pass computed was not recorded, so the request is made
#: unanswerable through `deny_approval`, and the approver says which of the two it was.
_WITHDRAWN_BY: Final = "ctrlrun:precondition-not-recorded"

#: The two outcomes of `_withdraw` that are this call's own writes. Anything else happened to
#: the request rather than to it, and the refusal says so rather than claiming a withdrawal.
_WITHDRAWALS: Final = frozenset({"denied", "spent"})

#: Where `Control.from_file` keeps its store, and the env var that overrides it (SPEC §8).
STATE_ENV_VAR: Final = "CTRLRUN_STATE"
DEFAULT_STATE_DIR: Final = ".ctrlrun"
DEFAULT_STATE_FILENAME: Final = "state.db"


@dataclass
class _Bound:
    """The task and hop a suspended leg was running under (SPEC-v0.10 §3.4.2).

    `recorded` is the discriminator and it is **key presence**, not value: an event this build
    wrote always carries both keys, so `recorded` is `True` even when both values are `None`, and
    an event 0.9.0 wrote carries `{}` and leaves it `False`. That is what tells "the caller named
    no task" from "this leg predates the fields", and conflating them denies every action in
    flight across the upgrade.
    """

    task: str | None = None
    hop: str | None = None
    recorded: bool = False


def _started_data() -> dict[str, Any]:
    """What `EXECUTION_STARTED` carries so a resumed leg can be decided (SPEC-v0.10 §3.4.2).

    `v0.9 §6.3.2` named this change and the milestone that would want it: recovering the first
    leg's task means stamping it here so `_resumed_context` can read it back. v0.10 wants it for
    the hop too, since a hop crossing a boundary is exactly what a continuation must still be
    bound by.

    **Both keys are always present, and `None` is a value.** §3.4.2's discriminator is the presence
    of the KEY, never the value: a 0.10 build running with a hop and no task writes
    `{"hop": "dlg_…", "task": None}`, and a reader keying on the value would conclude "0.9.0 wrote
    this" and drop a hop that is right there in the event. An event 0.9.0 wrote carries `{}`, which
    is the only shape meaning "this predates the fields".
    """
    return {"task": _TASK.get(None), "hop": _HOP.get(None)}


def _utc_now() -> datetime:
    return datetime.now(UTC)


# --- the ambient context ---------------------------------------------------------------


class DecisionPoint(IntEnum):
    """**Where** enforce mode decides, declared once, in order (SPEC-v0.10 §5).

    An earlier version of this ranked the **reason**, which cannot work, and an independent
    review demonstrated three live regressions from it before anything else did. Two reasons
    make a reason-ranking impossible rather than merely wrong:

    - **The same reason is produced at two different points.** `precondition_changed`,
      `approval_denied`, `consumed`, `expired` and `mismatch` all belong to
      `BLOCKED_APPROVAL_REASONS`, and enforce mode raises `approval_required` at the gate
      (`_presented`, above `_in_scope`) while raising the rest inside the loop (`_recheck` and
      `_take`, below `_in_scope`). No single rank for `precondition_changed` is both above and
      below `out_of_scope`.
    - **Some checks are not in `_secure` at all.** The attempt-ceiling fast path runs in
      `execute` before `_secure` is called, so its reason outranks everything `_secure` decides,
      including the upstream pin and the budget.

    So the call site declares where it is, and the reason travels as evidence rather than as an
    ordering key. That is what `control.py`'s own comment has always described:
    `principal_expired -> authority -> policy -> approval -> reservation -> execution`.

    **Adding a refusal means giving its call site a point.** One that forgets still reports as a
    refusal and still loses to every point declared, which is the fail-safe direction, and §5.3's
    end-to-end property test is what turns the omission red.
    """

    PRINCIPAL = 0
    #: `v0.8 §8.4` — a policy nobody approved decides nothing, checked before anything else is
    #: decided because what follows would be decided *by* it.
    POLICY_UNAPPROVED = 1
    #: `v0.3 §4.3` — authority before policy, so a denial leaves no pending approval behind.
    AUTHORITY = 2
    POLICY = 3
    #: `v0.7 §5.5`'s fast path, which runs in `execute` **before** `_secure`.
    CEILING = 4
    #: SPEC-v0.10 §4.3's check 2, first inside `_secure`.
    UPSTREAM = 5
    #: `v0.9 §2.3`, §2.4.1 — above the gate on T446's argument: unconditional, so a human's
    #: answer cannot change them.
    BUDGET = 6
    #: `_presented`, above `_in_scope`.
    APPROVAL_GATE = 7
    #: `v0.9 §5.3` — inside the loop, before every `_take`.
    SCOPE = 8
    #: `v0.7 §6.2`'s recheck, and the approval refusals `_take` raises. **Below scope**, which is
    #: why these cannot share a rank with the gate above.
    APPROVAL_TAKE = 9
    RESERVATION = 10
    #: Anything that forgot to declare a point. Last, which is fail-safe: the report stays a
    #: refusal and names something that did declare one.
    UNDECLARED = 99


@dataclass(frozen=True)
class _Invocation:
    principal: Principal


_CONTEXT: ContextVar[_Invocation] = ContextVar("ctrlrun_context")
#: SPEC-v0.8 §5.4 — the grant that decided the action being recorded, for the receipt. A
#: context variable rather than an argument threaded through `_record`'s dozen call sites,
#: on the precedent of `_PRESENTED_APPROVAL` beneath it: `Control` is shared across calls and
#: holds no per-call state, and a context variable is per-call by construction.
_AUTHORITY_GRANT_ID: ContextVar[str | None] = ContextVar("ctrlrun_authority_grant_id")
#: SPEC-v0.9 §6.3.1 — the task reaches the receipt the way `authority_grant_id` does, and for
#: the same reason: set at the one place that knows it rather than at each receipt site, because
#: a site that forgot would stamp the **previous** action's task onto this one's evidence.
_TASK: ContextVar[str | None] = ContextVar("ctrlrun_task")
#: SPEC-v0.10 §2.3.2 — the hop this action is running under, set where the task is and read for
#: the **evidence**, never for a decision. `_TASK`'s twin, and deliberately not `_suspend`'s
#: source: `_outcome`'s own docstring says `execute` and `resume` both come through it, so on
#: every round after the first this variable belongs to the resuming process rather than to the
#: action. The decision path threads the hop as a parameter; a review round found what reading it
#: here would have decided, which is an extension checked against somebody else's envelope.
_HOP: ContextVar[str | None] = ContextVar("ctrlrun_hop")
#: SPEC-v0.9 §5.5 — the scope hash reaches the receipt the way the task does, and is reset
#: beside it: a refusal whose receipt carried the previous action's scope would be the same
#: stale-evidence defect on a new field.
_SCOPE_HASH: ContextVar[str | None] = ContextVar("ctrlrun_scope_hash")
#: SPEC-v0.9 §2.7 — the authority decision this action was allowed by, so `_secure` can assemble
#: the charges without re-walking the chain. Set beside `_AUTHORITY_GRANT_ID` and reset with it,
#: for the stale-evidence reason that field's own comment gives.
_AUTHORITY_RESULT: ContextVar[AuthorityResult | None] = ContextVar("ctrlrun_authority_result")
#: SPEC-v0.9 §10.1 — what this action charged, for the receipt. Set where the charges are
#: assembled and reset beside the other two, for the stale-evidence reason `_AUTHORITY_GRANT_ID`'s
#: own comment gives.
_BUDGET_CHARGES: ContextVar[tuple[Mapping[str, Any], ...]] = ContextVar("ctrlrun_budget_charges")

#: SPEC-v0.8 §8.2.1, §11.2. How the policy-change flow says that this `ctrlrun.policy.change`
#: is one it built. **Package-internal on purpose**, beside `_granting_principal`, and it
#: carries the same residual §2.5.1 concedes: an application inside the process can call a
#: private function, so the claim is not "nothing else can propose one". It is **"nothing
#: outside the flow proposes one by accident, and the shipped surfaces are its only setters"**.
#: §8.6's property does not rest on this gate, which is why narrowing it costs nothing.
#: SPEC-v0.8 §8.4 — the effect key a policy approval is recorded under. Reserved on the
#: **resolved** key in `execute`, not only on the template at load: the key is expanded from
#: arguments an agent supplies, and a check on the template alone is defeated by one
#: placeholder.
_POLICY_EFFECT_PREFIX: Final = "policy:"

_POLICY_CHANGE_IN_FLIGHT: ContextVar[bool] = ContextVar(
    "ctrlrun_policy_change_in_flight", default=False
)


@contextmanager
def _policy_change_in_flight() -> Iterator[None]:
    """Mark the `ctrlrun.policy.change` built inside this block as the flow's own (§8.2.1)."""
    token = _POLICY_CHANGE_IN_FLIGHT.set(True)
    try:
        yield
    finally:
        _POLICY_CHANGE_IN_FLIGHT.reset(token)


_PRESENTED_APPROVAL: ContextVar[str] = ContextVar("ctrlrun_approval")

#: SPEC-v0.7 §4.3. The token of the attempt whose executor is running, and nothing else. Set
#: around `executor()` in `_outcome`, and only where the attempt holds its reservation.
_IDEMPOTENCY_TOKEN: ContextVar[str | None] = ContextVar("ctrlrun_idempotency_token", default=None)


@contextmanager
def _attempt_token(held_key: str | None, attempt: int) -> Iterator[None]:
    """Bind this attempt's token for exactly the executor's run (SPEC-v0.7 §4.3).

    Nothing is bound where the attempt holds no reservation: `held_key` is `None` for an action
    with no effect key and for an observe-mode attempt whose reservation was refused, and handing
    the second one attempt 1's token would name the real holder's attempt (§4.3).
    """
    if held_key is None:
        yield
        return
    token = _IDEMPOTENCY_TOKEN.set(idempotency_token_for(held_key, attempt))
    try:
        yield
    finally:
        _IDEMPOTENCY_TOKEN.reset(token)


def idempotency_token() -> str:
    """The provider idempotency token for the attempt this executor is running (SPEC-v0.7 §4).

    Send it to the provider as its idempotency key. It is
    `ctrlrun.effect.idempotency_token_for(effect_key, attempt)` for the attempt that holds the
    reservation, so it is stable across a `Control.resume` of the same attempt and **different
    after a renewal**: a token stable across v0.1 §5.4's renewal would have the provider answer
    the one retry the kernel permits with the cached failure of the attempt that failed (§4.1).

    What it is for is reconciliation: a deterministic handle to ask the provider what became of
    an attempt whose outcome is unknown, by a key the provider already indexes (§4.7). It does
    not make a retry safe, and after an `AMBIGUOUS` outcome the kernel still refuses one.

    Nothing is stored: the token is a pure function of two fields every receipt of an attempt
    that ran already carries, so a receipt re-derives it with `idempotency_token_for` and a
    `reconcile` hook reads the attempt off the record (§4.5).

    **Outside an executor this raises `InvalidArgument`**, because a token invented outside an
    attempt identifies nothing. So does an executor whose action has no effect key, an
    observe-mode attempt whose reservation was refused, and a thread the executor started
    without copying its context: a missing value is refused rather than guessed (§4.3).
    """
    token = _IDEMPOTENCY_TOKEN.get()
    if token is None:
        raise InvalidArgument(
            "idempotency_token() is defined only inside an executor running an attempt that "
            "holds its effect key. There is no attempt here to name, so there is no token "
            "(SPEC-v0.7 §4.3)"
        )
    return token


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

    **`block()` keeps the reason earliest in `DECISION_ORDER`, not the first one it is handed**
    (SPEC-v0.10 §5). The docstring here used to say the opposite, and say it for a reason that was
    not true: "the checks run in the order enforce mode runs them". They do not, which is what
    `v0.9 §4.2.1b` records, and keeping the first is how observe mode came to name a refusal
    enforce mode would not raise.

    **What this does not do is move a check**, and that is the point. v0.9 aligned three cases by
    reordering and the three reorderings produced four regressions between them (`v0.9 §13.8`).
    The information was never missing: a probe over §4.2.1b's own second case shows observe mode
    is handed `['no_authority', 'policy_unapproved']` and reports the first, while enforce mode
    raises the second. Ordering the **selection** is enough, and it cannot regress a check's
    position because it changes none.
    """

    __slots__ = ("blocked_at", "blocked_reason", "decision", "reason")

    def __init__(self) -> None:
        self.decision = Decision.ALLOW
        self.reason = ""
        self.blocked_reason: str | None = None
        self.blocked_at: DecisionPoint = DecisionPoint.UNDECLARED

    def decided(self, evaluation: Evaluation) -> None:
        self.decision = evaluation.decision
        self.reason = evaluation.reason

    def block(self, reason: str, at: DecisionPoint = DecisionPoint.UNDECLARED) -> None:
        """Record a refusal enforce mode would have raised, keeping the one it raises FIRST.

        `at` is **where** enforce mode decides this, and the caller declares it because only the
        caller knows: the same reason is produced at two different points (§5), so the reason
        cannot carry the ordering. A call site that declares none sorts last and still loses to
        every one that does, which is fail-safe.
        """
        if self.blocked_reason is None or at < self.blocked_at:
            self.blocked_reason = reason
            self.blocked_at = at

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


class _Compared:
    """What one presenting pass compared, for the event and the receipt (SPEC-v0.7 §6.2).

    Mutable and short-lived, as `_Observation` is: `execute` makes one per call, each recheck
    resets it and fills it, and the receipt reads it. Hashes only. `error` is the provider's
    failure by its type name, never its message: a provider that put the balance it read into
    its exception would otherwise carry raw state into the evidence through the one field
    nobody thought to check (§6.5).

    SPEC-v0.8 §2.5 adds `approvers` to it, because it is already the per-call scratch the
    presenting pass fills and the receipt reads: the alternative was a second `get_approval`
    per receipt, on a path that has just read the record.
    """

    __slots__ = ("approvers", "at_recheck", "at_request", "error", "unentitled")

    def __init__(self, at_request: str | None = None) -> None:
        self.at_request = at_request
        self.at_recheck: str | None = None
        self.error: str | None = None
        self.approvers: tuple[VerifiedApprover, ...] = ()
        #: SPEC-v0.8 §3.7 — the required role an approver did not hold, so the event can name the
        #: control as well as the reason.
        self.unentitled: RequiredRole | None = None

    def reset(self) -> None:
        self.at_request = None
        self.at_recheck = None
        self.error = None
        self.approvers = ()
        self.unentitled = None

    def data(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "precondition_at_request": self.at_request,
            "precondition_at_recheck": self.at_recheck,
        }
        if self.error is not None:
            data["error"] = self.error
        return data

    def spent(self) -> dict[str, Any]:
        """The two fields for `APPROVAL_CONSUMED`, and nothing at all where nothing was
        compared: absent means absent on an event as much as on a receipt (§6.11)."""
        if self.at_request is None and self.at_recheck is None:
            return {}
        return {
            "precondition_at_request": self.at_request,
            "precondition_at_recheck": self.at_recheck,
        }


_Preconditions = Callable[[Action], Mapping[str, Any]]


def _fetched(provider: _Preconditions, action: Action) -> tuple[str | None, str | None]:
    """Ask the operator's provider and fingerprint the answer: `(fingerprint, None)` or
    `(None, what went wrong)`, never raising an `Exception` (SPEC-v0.7 §6.5).

    A provider that raises, returns something that is not a `Mapping`, or returns something
    `canonical_bytes` refuses has produced no fingerprint, and a comparison that was never made
    is not a comparison that passed (`v0.4 §3.8`). What went wrong is named by **type only**. The
    return value exists here for as long as it takes to hash it and goes nowhere else.

    A `BaseException` that is not an `Exception` propagates untouched, as it does from every
    other hook in this file: nothing has been reserved when this runs, so an interrupt leaves
    nothing to tidy.
    """
    try:
        # `object`, not the annotation's `Mapping`: the annotation is what the operator
        # promised, and the check below is what happens when the promise is not kept.
        #
        # **Every line that touches what the provider handed back is inside this `try`**, the
        # `isinstance` included: `isinstance` reads `__class__`, and an object whose `__class__`
        # raises used to carry its own message out of `Control` as a raw exception, with no
        # refusal reason and no receipt. A provider's return value is the operator's data, and
        # nothing about it may escape as anything but `precondition_unavailable`.
        state: object = provider(action)
        if not isinstance(state, Mapping):
            return None, f"returned {type(state).__name__}, not a mapping"
        return _precondition_fingerprint(state), None
    except Exception as exc:
        return None, type(exc).__name__


def _hash_or_none(value: object) -> str | None:
    """A fingerprint read back out of an event's data, or `None` for anything else.

    Events are JSON, and a row-writer can put anything in one. A fingerprint is a string or it
    is nothing, and a resumed leg's receipt says `null` rather than whatever was found.
    """
    return value if isinstance(value, str) else None


class _ScopeRefusedError(Exception):
    """SPEC-v0.9 §5.6's refusal, carried out of `_secure`'s loop without meeting its handlers.

    **Not an `ActionDenied` subclass, and that is the whole point.** `_secure`'s `except
    ActionDenied` appends `APPROVAL_DENIED` unconditionally, so a scope refusal raised as one
    fabricates an approval denial for an action no human ever saw, and records `ACTION_DENIED`
    twice. `SPEC-v0.9 §3.3.2` names this hazard for the budget refusal a later item adds; it is
    the same handler and the same defect, found here first by reading the events a refusal wrote.

    `_refuse_scope` has already written the events and the receipt, so this carries only the
    public error the caller should see.
    """

    def __init__(self, denial: ActionDenied) -> None:
        super().__init__(str(denial))
        self.denial = denial


class _UnmeasurableError(InvalidArgument):
    """SPEC-v0.9 §2.3 and §2.4.1's refusal, **carrying its reason to a protocol boundary**.

    An `InvalidArgument` subclass and not a new type, because §2.3 pins that exception and a
    caller's `except InvalidArgument` must keep working. What it adds is `reason`, which the
    boundaries need and could not get from a message.

    An independent review found why that matters. `gateway/server.py`'s `_through_control`
    catches eight exception types and not `InvalidArgument`, so this refusal raised out of the
    request handler and the **socket closed with no response** -- the one failure that file's own
    comment calls the thing this library exists to prevent. And `acs.py` answered
    `-32002 malformed envelope`, whose comment reads "there is no action", for an action it had
    just written an `ACTION_DENIED` and a `denied` receipt for; the `IdentityError` clause
    directly below it states the rule that breaks, that an answer and the evidence may not
    disagree about the same action.

    `_refuse_unmeasurable` has already written the events and the receipt, so this carries only
    what a boundary needs to answer with.
    """

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason

    def __reduce__(self) -> tuple[Any, tuple[Any, ...]]:
        """Keep this picklable, because `InvalidArgument` is.

        The default reconstruction is `(cls, self.args)`, and this `__init__` takes `reason`
        keyword-only, so unpickling raised `TypeError` and a caller fanning `Control.execute`
        across a `ProcessPoolExecutor` lost the pool instead of catching the refusal. Nothing
        in this repository pickles it -- verify's children speak JSON over stdin -- so an
        independent review found it by probing the type rather than by a failing run.
        """
        return (_rebuild_unmeasurable, (str(self), self.reason))


def _rebuild_unmeasurable(message: str, reason: str) -> _UnmeasurableError:
    """Module-level so `pickle` can find it by name."""
    return _UnmeasurableError(message, reason=reason)


class _ObservedRefusalError(Exception):
    """SPEC-v0.9 §5.2.2 — observe mode's would-have-refused, which escapes `_in_scope` and is
    swallowed by `_observe_secure`. Package-internal and never public: it is control flow, not a
    refusal, and a caller that could catch it could mistake an observed run for an enforced one.

    **It carries the reason**, and an independent review is why it does. Without it
    `_observe_secure` had one hardcoded `out_of_scope` for both refusals, so a deployment whose
    scope *source was down* read a counterfactual saying the record was not theirs. Observe mode
    exists to tell an operator what enforce mode would do; reporting the wrong category is the one
    way it can be worse than useless.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


#: SPEC-v0.9 §5.6 — the two refusal reasons, distinct because a test asserting only the exception
#: type cannot tell which guard fired. `scope_unavailable` is "the provider could not answer";
#: `out_of_scope` is "it answered, and the record is not this principal's". G23 is the first.
#: SPEC-v0.9 §4.5 — its own reason, because an exhausted budget, an out-of-scope record and a
#: failing scope provider all deny the same action with the same exception type.
BUDGET_EXHAUSTED: Final = "budget_exhausted"

#: SPEC-v0.9 §2.3 and §2.4.1 refuse before anything is charged, and an independent review found
#: both escaping as a bare `InvalidArgument`: no `ACTION_DENIED`, no receipt, nothing in the one
#: record an operator has of a refused action. They keep that exception type, because neither is
#: a budget running out, but they are refusals and they are recorded as refusals. Two reasons
#: rather than one: an operator who declared a budget on an action with no `effect:` template has
#: a different thing to fix than one whose agent proposed a negative amount.
BUDGET_UNMEASURABLE: Final = "budget_unmeasurable"
BUDGET_UNKEYED: Final = "budget_unkeyed"
SCOPE_UNAVAILABLE: Final = "scope_unavailable"
OUT_OF_SCOPE: Final = "out_of_scope"


def _where_to_look(result: AuthorityResult) -> str:
    """SPEC-v0.10 §6.3 — the command, with its argument filled in, never a placeholder.

    **The argument is always the PRESENTED hop**, with whatever the refusal knows about the chain
    named in the prose beside it. An earlier draft of §6.3 had `missing_parent_id` print the id it
    names; that id is by construction the record the store could **not** read, so
    `inspect --hop <it>` is the unknown-id path and exits non-zero. A refusal whose one suggested
    command is guaranteed to fail is worse than no suggestion: it sends an operator to a dead end
    and teaches them the line is noise.

    `authority_revoked` gets the same treatment for the same reason, measured: `_check_chain`
    returns no id for the revoked node, so the only id in hand is the leaf.

    Nothing is suggested where there is no id, which is a principal that presented no hop and
    holds no delegation: `inspect --hop` has no argument there and the operator's question is a
    different one.
    """
    hop = result.hop or result.delegation_id
    if hop is None:
        return ""
    detail = ""
    if result.missing_parent_id is not None:
        detail = f"; {result.missing_parent_id} in its chain could not be read"
    elif result.expired_parent_id is not None:
        detail = f"; {result.expired_parent_id} above it has expired"
    return f"{detail}. ctrlrun inspect --hop {hop}"


#: SPEC-v0.9 §5.5 — its own domain tag, so a scope hash can never equal a precondition
#: fingerprint over the same mapping. That matters precisely because §5.7 permits both.
_SCOPE_SCHEMA: Final = "ctrlrun.scope/v1"

#: §5.4 — the key the provider answers under. A scope is a set of resource patterns, matched
#: with the relation `authority.py` already uses for a grant's `resources:`.
_SCOPE_RESOURCES: Final = "resources"


def _checked_scope(scope: object, where: str) -> _Preconditions | None:
    """SPEC-v0.9 §5.6's third row, and `v0.7 §6.2`'s rule for a non-callable `preconditions=`."""
    if scope is not None and not callable(scope):
        raise InvalidArgument(
            f"{where}: scope must be a callable taking the Action and returning a mapping "
            f"with {_SCOPE_RESOURCES!r}, not {type(scope).__name__}"
        )
    return cast("_Preconditions | None", scope)


def _scope_hash(scope: Mapping[str, Any]) -> str:
    """`"sha256:" + hex(SHA-256(canonical_bytes({schema, scope})))` (SPEC-v0.9 §5.5).

    Through `canonical_bytes` and nothing else, so `v0.1 §2.3`'s float rejection and the
    non-string-key refusal are inherited rather than re-argued: a scope hashed over a float
    would drift. Whatever it raises is the caller's to turn into `scope_unavailable`.
    """
    document = {"schema": _SCOPE_SCHEMA, "scope": dict(scope)}
    return "sha256:" + hashlib.sha256(canonical_bytes(document)).hexdigest()


def _checked_preconditions(preconditions: object, where: str) -> _Preconditions | None:
    """`None`, or a callable (SPEC-v0.7 §6.2). Anything else is a wiring bug, refused at the
    door: at decoration time for `@protect`, before any evidence for `execute`."""
    if preconditions is not None and not callable(preconditions):
        raise InvalidArgument(
            f"{where}: preconditions must be a callable taking the Action and returning a "
            f"mapping, not {type(preconditions).__name__}"
        )
    return cast("_Preconditions | None", preconditions)


# --- Control ---------------------------------------------------------------------------


def _storable(text: str) -> str:
    """`text` with any lone surrogate escaped, so a store and a hash can both take it."""
    return text.encode("utf-8", "backslashreplace").decode("utf-8")


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
        approver_identity: ApproverIdentity | None = None,
        require_approved_policy: bool = False,
        upstream: str | None = None,
        anchor: AnchorProvider | None = None,
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
        #: SPEC-v0.8 §2.3: opt in, then fail closed. `None` is 0.7.0 exactly; anything else
        #: makes an approval consumable only where the row carries a verified approver (§2.7).
        #: `Control` never *resolves* one: it never grants an approval, so what it does with
        #: this is check what the granting surface recorded (§1.4 item 1).
        self._approver_identity = approver_identity
        # SPEC-v0.8 §8.4. **In code and not in the file it governs**, or the file would switch
        # off its own governance. Default false: opt in, then fail closed.
        self._require_approved_policy = require_approved_policy
        # SPEC-v0.11 §9 — **a property of the deployment, not of an action**, which is why it is
        # here and not on `execute`. A deployment anchors its receipt chain or it does not; no
        # single action decides that, and a per-action parameter would invite one caller to
        # anchor and another not to, in the same store.
        #
        # `Control` never *makes* an anchor on an action's path. Anchoring is an operator's act
        # on a schedule (`ctrlrun anchor`), and putting it on the write path would mean an
        # unreachable timestamp authority could block an agent from acting, which is a
        # availability cost this milestone has no reason to impose: the anchor is about reading
        # the record later, not about deciding now.
        self._anchor = anchor
        # SPEC-v0.10 §4.3 — the upstream this deployment fronts, which only a surface holding the
        # connection can name. The gateway passes `GatewayConfig.upstream`; in-process it is
        # `None`, and §4.4 makes a pinned action refuse `upstream_unverified` there.
        self._upstream = upstream
        #: Cached **only when the answer is yes** (§8.4). A negative answer is re-asked on every
        #: decision, so a long-lived process that started before the approval landed begins
        #: working the moment it lands, with no restart; the cost is one keyed read per decision
        #: while a deployment is unapproved, which is the state where nothing is running anyway.
        #: A positive answer is cached for this `Control`'s life, because a COMMITTED effect
        #: does not become uncommitted through any path this kernel offers -- which is also
        #: §8.6's residual: deleting the row underneath a running process does not stop it.
        self._policy_approved = False
        #: SPEC-v0.6 §7.1's *"both are folded into the one canonical structure before hashing"*.
        #: `Policy` cannot see a separately-loaded `Authority` and this can, so the hash every
        #: receipt and every approval request carries is composed here. Where the authority came
        #: from the policy document, this equals `policy.policy_hash` exactly.
        self._environment = _resolve_environment(environment, policy)
        self._policy_hash = hash_with_authority(policy, authority, self._environment)
        #: SPEC-v0.3 §6.1 — one switch, read once, governing the process. Not a parameter:
        #: the mode belongs to the configuration an operator deployed, and a Control that
        #: could be handed a different one would be a per-caller opt-out of enforcement.
        self._observing = policy.mode == OBSERVE
        #: SPEC-v0.3 §3.2 — the provider-versus-context warning is emitted at most once per
        #: Control. A warning that repeats per call is a warning nobody reads.
        self._warned_about_principal = False
        #: SPEC-v0.7 §3.6: the last clock measurement this Control reported, so the same one is
        #: appended once however many actions read it.
        self._skew_reported: ClockSkew | None = None
        self._skew_warned: set[str] = set()
        #: SPEC-v0.7 §5.3 — the actions already warned about for a ceiling that can count
        #: nothing, so the warning is one per action per Control and not one per call.
        self._ceiling_warned: set[str] = set()

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
    def require_approved_policy(self) -> bool:
        """Does this deployment refuse to decide under a policy nobody approved? (§8.4.)"""
        return self._require_approved_policy

    @property
    def approver_identity(self) -> ApproverIdentity | None:
        """How this deployment verifies who answered an approval, or `None` (SPEC-v0.8 §2.3)."""
        return self._approver_identity

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

    def evaluate(
        self, action: Action, *, task: str | None = None, hop: str | None = None
    ) -> Evaluation:
        """Decide an action. No side effects: nothing is recorded (SPEC-v0.1 §8).

        An expired principal is a `DENY` here rather than the refusal `execute` raises
        (SPEC-v0.3 §2.3): this method may not write, and a check defined only as side effects
        would otherwise have no meaning on the one path that may not have them. The gateway's
        approval pre-check reads this, so leaving it undefined would give three different
        gateway behaviours.

        **It does not see the attempt ceiling** (SPEC-v0.7 §5.5, §7). `max_attempts` is decided
        against an effect record, and this method takes an `Action` rather than an effect key and
        may not read the store to resolve one. So an adapter asking `ctrlrun.adapter.needs_approval`
        can put an approval in front of a human for an attempt `execute` will then refuse, and a
        gateway pre-check can report `approve` for the same attempt. The cost is a wasted answer,
        never an execution: nothing here writes, and every ceiling refusal happens in `execute`.

        **`task` is SPEC-v0.9 §6**, and it is here as well as on `execute` because §6.3.2 requires
        the two to agree: `ctrlrun.adapter.needs_approval` routes through this method, and an
        `evaluate` blind to the task would report `ALLOW` for an action `execute` refuses.
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
        result = self._authority_result(action, task=task, hop=hop)
        if result is not None and not result.passed:
            return Evaluation(Decision.DENY, result.reason)
        return self._policy.evaluate(action)

    def _require_approved(
        self,
        action: Action,
        *,
        evaluation: Evaluation | None,
        started_at: datetime,
        effect_key: str | None,
    ) -> None:
        """Refuse every decision under a policy nobody approved (SPEC-v0.8 §8.4).

        **A keyed read, not a scan.** `get_effect(f"policy:{hash}")` is on the frozen protocol
        and is O(1) on both shipped stores; `receipts()` could only answer this by returning
        every receipt in the store, parsed, on the first decision of every process. It is the
        same key the proposal reserved, so the two cannot drift.

        **Two rules, and the second is what closes §8.6's obvious escape.** There must be a
        committed effect at that key, *and* the policy in force must declare
        `ctrlrun.policy.change` with `decision: approve`. Without the second an administrator
        writes a policy whose change rule is `allow`; installing it still needs an approval
        under the old policy, and the moment it is installed the deployment stops deciding
        anything, with the refusal naming the key.
        """
        if not self._require_approved_policy:
            return
        if self._observing:
            # SPEC-v0.3 §6.2, and §11.1's own reason table, which lists `policy_unapproved` as
            # a `would_have.blocked_reason` value this code could never produce. Observe mode
            # **records** what enforce mode would have refused and does not refuse; raising
            # here made an observe-mode deployment stop, which is the one thing observe mode
            # exists not to do. The caller records it: `_observe_secure` reads what this
            # returns rather than catching an exception, because an exception out of here
            # would have to be caught in four places.
            return
        # **SPEC-v0.8 §8.2.1, and an independent review found this exemption too wide.** The
        # policy change is exempt from the *effect* check, because the proposal is how a policy
        # becomes approved and gating it on an approved policy is a bootstrap that cannot
        # complete. It is **not** exempt from the declaration check: an earlier build returned
        # here before that ran, so a policy whose change rule was `allow` proposed a *different*
        # policy with no approval at all, committed the marker, and the successor then decided
        # normally -- approved by nobody. §8.6's property was false in exactly the case §8.2.1
        # exists to close.
        #
        # Exactly one action, matched by name and never by prefix: `ctrlrun.policy.changes` is
        # a different action and is refused with everything else (T360).
        exempt_from_marker = action.name == POLICY_CHANGE_ACTION
        if self._policy_approved and not exempt_from_marker:
            return
        reason, detail = self._policy_approval_state(skip_marker=exempt_from_marker)
        if reason is None:
            if not exempt_from_marker:
                # Never cached from the exempt path: that answer did not read the marker, and
                # caching it would mark the deployment approved because a proposal ran.
                self._policy_approved = True
            return
        _LOG.warning("%s: %s", action.name, detail)
        self._append(EventType.ACTION_DENIED, action, {"reason": reason}, effect_key)
        self._record(
            action,
            evaluation if evaluation is not None else Evaluation(Decision.DENY, reason),
            ReceiptResult.DENIED,
            started_at,
            error=detail,
            effect_key=effect_key,
        )
        raise ActionDenied(detail, reason=reason, action_id=action.action_id)

    def _policy_approval_state(self, *, skip_marker: bool = False) -> tuple[str | None, str]:
        """`(None, "")` where this policy is approved, else the reason and what to say.

        `skip_marker` is the policy change's own exemption and covers **only** the committed
        effect: the declaration rule applies to it like everything else (§8.2.1).
        """
        entry = self._policy.actions.get(POLICY_CHANGE_ACTION)
        if entry is None or POLICY_CHANGE_ACTION not in self._policy.approving_actions():
            return (
                POLICY_UNAPPROVED,
                f"this deployment requires an approved policy, and the policy in force does "
                f"not declare {POLICY_CHANGE_ACTION!r} with 'decision: approve'. A policy that "
                "cannot send its own change to a human decides nothing (SPEC-v0.8 §8.2.1)",
            )
        if skip_marker:
            return (None, "")
        record = self._store.get_effect(f"policy:{self._policy_hash}")
        if record is None or record.state is not EffectState.COMMITTED:
            return (
                POLICY_UNAPPROVED,
                f"this deployment requires an approved policy and nobody approved "
                f"{self._policy_hash}. Propose it with 'ctrlrun policy propose --file "
                "<this policy>' and have it approved (SPEC-v0.8 §8.4)",
            )
        return (None, "")

    def _authority_result(
        self,
        action: Action,
        *,
        task: str | None = None,
        evaluate_task: bool = True,
        hop: str | None = None,
    ) -> AuthorityResult | None:
        """The authority axis for this action, or `None` where there is no section (§4.1).

        SPEC-v0.8 §5.4: it also remembers which grant decided, for the receipt. Here rather
        than at the four call sites, because a site that forgot would produce a receipt whose
        `authority_grant_id` was the **previous** action's, and a stale id on the evidence is
        worse than none. Set on every call, including to `None`, for the same reason.

        **SPEC-v0.9 §6.3.2: `evaluate_task=False` on two of the four call sites.** `resume` and
        a lease extension both rehydrate an action from the store, which carries no task (§6.3.1
        keeps it off `Action`), so evaluating the dimension there would deny every resumed leg
        under a task-bound grant, on what the comment above `resume`'s own call calls the only
        receipt an MCP multi round-trip ever gets. It is not `v0.3 §5.6.1`'s evaluated-and-
        recorded, which would still put `AUTHORITY_DENIED` on that receipt; it is a third mode,
        and §6.3.2 names it as one.
        """
        _TASK.set(task)
        _HOP.set(hop)
        if self._authority is None:
            _AUTHORITY_GRANT_ID.set(None)
            _AUTHORITY_RESULT.set(None)
            return None
        result = self._authority.evaluate(
            action,
            now=self._clock(),
            store=self._store,
            task=task,
            evaluate_task=evaluate_task,
            hop=hop,
        )
        # Only where one passed: `grant_id` is also set on a refusal, and a committed receipt
        # is the only thing this field is read on. §4.6's `min` already picked which grant of
        # several decided, so this is that decision and not a guess about it.
        _AUTHORITY_GRANT_ID.set(result.grant_id if result.passed else None)
        _AUTHORITY_RESULT.set(result if result.passed else None)
        return result

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
        # SPEC-v0.10 §2.3.2 — present on every refusal of an action proposed under a hop, which
        # is what lets §6.3 print `ctrlrun inspect --hop <id>` with the argument filled in. The id
        # and nothing else: §3.3 refuses to describe the envelope a refusal would otherwise leak.
        if result.hop is not None:
            # SPEC-v0.10 §3.3 — the presented id, **bounded**. A hop reaches the kernel from the
            # caller (through `@protect`'s template, so from the action's arguments), and a
            # refusal writes one `AUTHORITY_DENIED` row per refused action into an append-only
            # log an operator reads on a terminal. An independent review drove a megabyte of
            # `A`, then NULs, newlines and ANSI escapes, straight through to `data.hop`.
            #
            # An id that is not one this kernel could have minted is recorded as its length and
            # nothing else: enough to tell a typo from a flood, and no more. `new_delegation_id`
            # mints `dlg_` plus 32 hex, so a well-formed id is 36 characters and unharmed.
            data["hop"] = (
                result.hop
                if _DELEGATION_ID.match(result.hop)
                else f"<malformed, {len(result.hop)} characters>"
            )
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
            f"{action.name} denied: {result.reason}{_where_to_look(result)}",
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
        preconditions: Callable[[Action], Mapping[str, Any]] | None = None,
        task: str | None = None,
        hop: str | None = None,
        scope: Callable[[Action], Mapping[str, Any]] | None = None,
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

        `task` is the unit of work this call is part of (SPEC-v0.9 §6). It is the **resolved**
        task id, like `effect_key` beside it and unlike `@protect`'s `task=`, which is a template.
        A grant naming `tasks` refuses a call that names none (§6.4); a grant naming none accepts
        any (§6.5), which is why every 0.8.0 caller is unchanged. It reaches the authority
        decision and **never the action hash**: §6.3.1 argues that at length, and the short form
        is that a field on `Action` would move every action hash in existence.

        `scope` answers whether this action's resource is in the calling principal's assigned
        scope (SPEC-v0.9 §5). It is called with the `Action` and returns a mapping carrying a
        `resources` list; **the kernel matches**, with the relation a grant's `resources:` uses.
        It runs **strictly before the reservation** and before the precondition recheck, so a
        provider that hangs can only fail closed. A provider that raises, answers with the wrong
        shape, or answers something the canonicalizer refuses denies the action
        `scope_unavailable`; a resource the scope does not cover denies it `out_of_scope`. Only
        the hash of what it returned reaches the receipt, never the scope itself.

        `preconditions` reads the state an approval depends on (SPEC-v0.7 §6). It is called
        with the `Action` and returns a mapping, which is hashed through `canonical_bytes` and
        kept only as a fingerprint. Under `APPROVE` it is called when the approval is requested,
        and the fingerprint is stored with the request; and on the presenting pass it is called
        again **strictly before** the store call that consumes the approval, before each such
        call, and the action is refused `ApprovalMismatch(reason="precondition_changed")` where
        the two differ, `"precondition_missing"` where only one side has one, and
        `"precondition_unavailable"` where the provider raises, returns something that is not a
        mapping, or returns one `canonical_bytes` refuses. A refusal reserves nothing and leaves
        the approval granted. **It
        narrows the window between a human's decision and the effect; it does not close it.**
        A change landing after the comparison and before the reservation is not refused, and
        the world can move again before the executor's request lands (§6.7). `ALLOW`, `DENY`
        and `Control.resume` never call it.
        """
        self._report_clock_skew()
        if effect_key is not None and not effect_key:
            raise InvalidArgument("effect_key must be a non-empty string or None")
        if (
            effect_key is not None
            and effect_key.startswith(_POLICY_EFFECT_PREFIX)
            and not _POLICY_CHANGE_IN_FLIGHT.get()
        ):
            # **SPEC-v0.8 §8.2.1, on the RESOLVED key, which is where the guard belongs.**
            # The loader refuses a template that literally begins `policy:`, and an
            # independent review showed that closes almost nothing: the key that reaches
            # `commit_effect` is expanded from arguments an agent supplies, so
            # `effect: "{scheme}:{ref}"` with `scheme="policy"` mints the marker of an
            # approved policy from an ordinary action, `"{p}olicy:{ref}"` evades the
            # literal check outright, and `execute(action, executor, "policy:<hash>")`
            # needs no template at all because `effect_key` is a public parameter.
            #
            # One rule on the resolved key closes all three. The loader check stays as a
            # convenience that names the mistake at load time; **this** is the guard.
            raise InvalidArgument(
                f"an effect key beginning {_POLICY_EFFECT_PREFIX!r} is reserved: it is "
                "what records that a policy hash was approved, so an action able to "
                "commit one could approve a policy nobody reviewed (SPEC-v0.8 §8.2.1). "
                f"This call asked for {effect_key!r}"
            )
        provider = _checked_preconditions(preconditions, "execute(preconditions=...)")
        scoper = _checked_scope(scope, "execute(scope=...)")
        scoped: list[str | None] = []
        self._check_environment(action)
        held = self._lease if lease is None else _checked_lease(lease, "execute(lease=...)")
        reconciler = _reconciler(reconcile, reconcile_eagerly, "execute")

        started_at = self._clock()
        if action.name == POLICY_CHANGE_ACTION and not _POLICY_CHANGE_IN_FLIGHT.get():
            # SPEC-v0.8 §8.2.1. The reserved name is the flow's, and an ordinary caller
            # reaching it would be proposing a policy change without one. Refused before
            # anything is evaluated or recorded, because there is nothing here to decide: the
            # action is well-formed and simply is not this caller's to build.
            raise InvalidArgument(
                f"{POLICY_CHANGE_ACTION!r} is reserved for the policy-change flow "
                "(SPEC-v0.8 §8.2.1). Propose a policy with 'ctrlrun policy propose --file "
                "<new.yaml>', which builds this action and marks it as the flow's own"
            )
        # SPEC-v0.8 §5.4. **Cleared at the top of the call, not only set at the authority
        # gate.** §4.3.1 puts `principal_expired` first, so `execute` records a denied receipt
        # *before* `_authority_result` runs; with only the gate setting this, that receipt
        # carried the **previous** action's grant id. An independent review demonstrated it: a
        # committed action under a break-glass grant, then a refusal for a lapsed credential,
        # and the refusal's receipt named the grant that never decided it. Async tasks inherit
        # a copy of the context at creation, so a task started after a break-glass action
        # carried that id into an unrelated refusal too.
        _AUTHORITY_GRANT_ID.set(None)
        _AUTHORITY_RESULT.set(None)
        # SPEC-v0.9 §6.3.1 — reset beside it, for the reason the comment above gives about a
        # stale grant id: a refusal whose receipt carried the previous action's task would be the
        # same defect on a new field.
        _TASK.set(None)
        # SPEC-v0.10 §3.4 — **and the hop**, for the reason the block above gives for the other
        # five. `v0.3 §4.3.1` puts `principal_expired` above authority, so a denied receipt is
        # written before `_authority_result` ever runs; without this reset it names the previous
        # action's hop. A review demonstrated exactly that, which is the same defect this block's
        # own comment was written for, reproduced on the field v0.10 adds.
        _HOP.set(None)
        _SCOPE_HASH.set(None)
        _BUDGET_CHARGES.set(())
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
                observation.block(PRINCIPAL_EXPIRED, DecisionPoint.PRINCIPAL)
                return self._observed(
                    action,
                    expired,
                    executor,
                    effect_key,
                    started_at,
                    observation,
                    reconciler,
                    held,
                    provider,
                    scoper,
                    scoped,
                    hop=hop,
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
        # SPEC-v0.8 §8.4: **a policy nobody approved decides nothing**, and that is checked
        # before anything else is decided, because what follows would be decided *by* it. The
        # policy-change action itself is exempt by name: the proposal is how a policy becomes
        # approved, so gating it on an approved policy is the bootstrap that cannot complete.
        self._require_approved(
            action, evaluation=None, started_at=started_at, effect_key=effect_key
        )
        # SPEC-v0.3 §4.3.1 — the order, stated once so it can be tested: principal_expired →
        # authority → policy → approval → reservation → execution.
        result = self._authority_result(action, task=task, hop=hop)
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
                    observation.block(result.reason, DecisionPoint.AUTHORITY)
                    return self._observed(
                        action,
                        denial,
                        executor,
                        effect_key,
                        started_at,
                        observation,
                        reconciler,
                        held,
                        provider,
                        scoper,
                        scoped,
                        hop=hop,
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
                observation.block(evaluation.reason, DecisionPoint.POLICY)
                return self._observed(
                    action,
                    evaluation,
                    executor,
                    effect_key,
                    started_at,
                    observation,
                    reconciler,
                    held,
                    provider,
                    scoper,
                    scoped,
                    hop=hop,
                )
            # SPEC-v0.6 §7.2.1's third bullet: *"the refusal is recorded against the approval
            # so the history shows a grant that met a denial."* It was not. An independent
            # review found `ACTION_DENIED` appended with `approval_id=None` and the receipt
            # carrying neither the id nor the approver, so nothing in the store or the log
            # connected a **live** granted approval to the refusal it met -- and that bullet is
            # one of the three arguments §7.2.1 offers for the *worse* half of the asymmetry,
            # a live bearer token for an action the policy currently forbids.
            #
            # Recording it changes nothing about the approval, which is the whole point of the
            # `DENY` row: it stays `granted`, unspent, for the action a human really did answer.
            presented = _PRESENTED_APPROVAL.get(None)
            self._append(
                EventType.ACTION_DENIED,
                action,
                {"reason": evaluation.reason},
                effect_key,
                approval_id=presented,
            )
            self._record(
                action,
                evaluation,
                ReceiptResult.DENIED,
                started_at,
                effect_key=effect_key,
                approval_id=presented,
                approver=self._approver_of(presented),
            )
            raise ActionDenied(
                f"{action.name} denied: {evaluation.reason}",
                reason=evaluation.reason,
                action_id=action.action_id,
            )
        # SPEC-v0.7 §5.5 — the ceiling's fast path, between policy and the approval gate. It
        # saves a write, it saves a presented approval from being spent, and it saves a human
        # from being asked about an attempt that could never run (`v0.3 §4.3`). It is never the
        # guarantee: the check on the number the store assigned is, below.
        ceiling = self._ceiling(action, effect_key)
        refused_early = self._ceiling_fast_path(effect_key, ceiling)
        if refused_early is not None:
            if observation is not None:
                # §5.7 — recorded and not enforced. Here rather than inside `_observed`, because
                # enforce mode decides this before the approval gate and `_Observation.block`
                # keeps the first reason: recording it later would let `approval_required` win a
                # race that enforce mode does not have.
                observation.block(BLOCKED_ATTEMPT_CEILING, DecisionPoint.CEILING)
            else:
                # SPEC-v0.6 §7.2.1, applied here as the `DENY` branch above applies it: a
                # presented approval is recorded against the refusal it met, so the history
                # connects a live granted approval to what stopped it. It is **not** consumed,
                # which is the fast path's point, and a review found this same omission on the
                # `DENY` path before it was fixed there.
                self._refuse_ceiling(
                    action,
                    evaluation,
                    started_at,
                    effect_key,
                    attempt=refused_early,
                    ceiling=ceiling,
                    reserved=False,
                    approval_id=_PRESENTED_APPROVAL.get(None),
                )
        if observation is not None:
            return self._observed(
                action,
                evaluation,
                executor,
                effect_key,
                started_at,
                observation,
                reconciler,
                held,
                provider,
                scoper,
                scoped,
                hop=hop,
            )
        compared = _Compared()
        approval, reservation = self._secure(
            action,
            evaluation,
            started_at,
            effect_key,
            held,
            reconciler,
            provider,
            compared,
            scoper,
            scoped,
        )
        attempt = 1 if reservation is None else reservation.attempt
        # SPEC-v0.7 §5.5 — the check, on the attempt number the store **assigned**, after the
        # reservation and before the executor. Two callers who both read N-1 both pass the fast
        # path above; only this one stops the second, because the reservation is the only write
        # that assigns the number atomically.
        if reservation is not None and self._over_the_ceiling(ceiling, reservation.attempt):
            self._refuse_ceiling(
                action,
                evaluation,
                started_at,
                effect_key,
                attempt=reservation.attempt,
                ceiling=ceiling,
                reserved=True,
                approval=approval,
            )

        if effect_key is not None:
            try:
                self._store.begin_execution(effect_key, action.action_id)
            except (DuplicateEffect, AmbiguousEffect) as refused:
                # The key was taken from under this attempt between winning it and starting:
                # its lease expired and another attempt declared the effect AMBIGUOUS. The
                # refusal is terminal for this proposal, so it gets a receipt like any other.
                self._refused(
                    action,
                    evaluation,
                    started_at,
                    effect_key,
                    refused,
                    approval=approval,
                    compared=compared,
                )
                raise
        self._append(
            EventType.EXECUTION_STARTED, action, _started_data(), effect_key, approval=approval
        )
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
            compared=compared,
            hop=hop,
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
        preconditions: _Preconditions | None = None,
        scope: _Preconditions | None = None,
        scoped: list[str | None] | None = None,
        hop: str | None = None,
    ) -> Receipt:
        """Run an action observe mode has finished deciding about (SPEC-v0.3 §6.2).

        Reached from four places in `execute` — an expired principal, an authority denial, a
        policy denial, and a decision that would have let the action through — because all
        four execute in observe mode and only the counterfactual differs. The reservation is
        attempted from every one of them: observe mode *executes*, and an attempt that runs
        an effect must own its key where it can.
        """
        compared = _Compared()
        approval, reservation = self._observe_secure(
            action,
            evaluation,
            effect_key,
            lease,
            observation,
            preconditions,
            compared,
            scope,
            scoped,
        )
        held_key = None if reservation is None else effect_key
        attempt = 1 if reservation is None else reservation.attempt
        # SPEC-v0.7 §5.7 — the check, in observe mode: recorded and not enforced, because
        # observe mode suppresses ctrlrun's decisions and not the record of an effect that
        # happened. The fast path's half is recorded in `execute`, before this is reached.
        if reservation is not None and self._over_the_ceiling(
            self._policy.max_attempts(action.name), reservation.attempt
        ):
            observation.block(BLOCKED_ATTEMPT_CEILING, DecisionPoint.CEILING)
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
                observation.block(_blocked_by(refused), DecisionPoint.RESERVATION)
                held_key = None
        self._append(
            EventType.EXECUTION_STARTED, action, _started_data(), effect_key, approval=approval
        )
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
            compared=compared,
            hop=hop,
        )

    def _observe_secure(
        self,
        action: Action,
        evaluation: Evaluation,
        effect_key: str | None,
        lease: timedelta,
        observation: _Observation,
        preconditions: _Preconditions | None,
        compared: _Compared,
        scope: _Preconditions | None = None,
        scoped: list[str | None] | None = None,
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
        # SPEC-v0.8 §8.4 in observe mode: **recorded, not refused.** `_require_approved` stands
        # aside while observing, and this is where an operator piloting the requirement learns
        # that enforce mode would deny every action under a policy nobody approved -- which is
        # the single thing they are running observe mode to find out.
        if self._require_approved_policy and action.name != POLICY_CHANGE_ACTION:
            reason, _ = self._policy_approval_state()
            if reason is not None:
                observation.block(reason, DecisionPoint.POLICY_UNAPPROVED)
        # SPEC-v0.9 §5.2.2's observe row. The provider **runs**, so its hash reaches the receipt
        # and an operator sizing a scope before turning it on sees what would have happened; the
        # refusal is recorded and not raised. `v0.3 §6.2`: observe mode records rather than
        # enforces, and a check that enforced here would refuse during the phase whose entire
        # purpose is to refuse nothing.
        # SPEC-v0.9 §4.2.1 — **above the scope check, because `_secure` computes charges before
        # calling `_in_scope`.** An action that is both out of scope and unmeasurable was refused
        # `budget_unmeasurable` by enforce mode and reported `out_of_scope` by the pilot. T458.
        # SPEC-v0.10 §4.3, the observe-mode row: **recorded, not refused** (`v0.3 §6.2`). At the
        # same point in the declared order as `_secure`'s, which is what §5 is about.
        observed_upstream = self._upstream_reason(action)
        if observed_upstream is not None:
            observation.block(observed_upstream, DecisionPoint.UPSTREAM)
        charges = self._observe_charges(action, effect_key, observation)
        try:
            self._in_scope(action, scope, scoped, enforcing=False)
        except _ObservedRefusalError as would:
            observation.block(would.reason, DecisionPoint.SCOPE)
        # The charges above were computed before the scope check, where `_secure` computes them:
        # §2.3's and §2.4.1's refusals do not depend on anything the approval gate produces and
        # are unconditional, so T446 moved them above it in enforce mode; observe mode's copy
        # stayed below and told a pilot a human would have been asked about an action enforce
        # refuses before anybody is asked. That is T446's own defect on the other side of the
        # mode switch, and an independent review found it. T451.
        approval_id = None
        if evaluation.decision is Decision.APPROVE:
            approval_id = _PRESENTED_APPROVAL.get(None)
            if approval_id is None:
                # SPEC-v0.8 §4.2, §11.1. **The threshold refusal reaches observe mode too**,
                # and it reached it nowhere: `_refuse_unverifiable` lives in `_presented`,
                # which observe mode never calls, so a deployment piloting a two-approver
                # policy with no approver identity was told `approval_required` -- that a human
                # would have been asked. Enforce mode denies every one of those actions before
                # anybody is asked, and reporting which is the one thing observe mode is for.
                required = self._policy.approvals_required(action.name)
                observation.block(
                    APPROVALS_UNVERIFIABLE
                    if required > 1 and self._approver_identity is None
                    else BLOCKED_APPROVAL_REQUIRED,
                    DecisionPoint.APPROVAL_GATE,
                )
        if approval_id is None and effect_key is None:
            return None, None
        try:
            approval, reservation = self._observe_take(
                action, approval_id, effect_key, lease, preconditions, compared
            )
        except (DuplicateEffect, AmbiguousEffect) as refused:
            if isinstance(refused, AmbiguousEffect):
                # SPEC-v0.7 §3.6, as in `_secure`: observe mode reserves, so it meets E3 too.
                self._report_clock_skew(action, effect_key)
            observation.block(_blocked_by(refused), DecisionPoint.RESERVATION)
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
            #
            # **The reason and not the constant, which is a behaviour change SPEC-v0.8 §4.1
            # argues for rather than a side effect of item 2.** This recorded
            # `approval_mismatch` for every mismatch, so an operator reading an observe-mode
            # report could not tell a moved precondition from an approver who may not answer.
            # Recording the specific reason for the approver refusals alone would leave a
            # vocabulary nobody can explain, so every mismatch now records its own reason. The
            # values are the ones `_secure` raises, and §11.1's table lists them.
            observation.block(mismatch.reason, DecisionPoint.APPROVAL_TAKE)
            self._append(
                EventType.APPROVAL_INVALIDATED,
                action,
                self._invalidated(action, mismatch, compared),
                effect_key,
                approval_id=approval_id,
            )
            return None, None
        except ActionDenied as denied:
            # The store raises this where the record says a human refused the approval that
            # was presented. §4.2 makes that a denial of the action, and the reason is the
            # one `_secure` records; observe mode records it and runs.
            observation.block(denied.reason, DecisionPoint.APPROVAL_TAKE)
            self._append(
                EventType.APPROVAL_DENIED,
                action,
                {"approver": self._approver_of(approval_id)},
                effect_key,
                approval_id=approval_id,
            )
            return None, None
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
        # SPEC-v0.9 §4.2.1 — **below the take, because the store decides in that order.**
        # `plan_reservation` runs before `check_charges` (§3.3), so an effect that is already
        # committed raises `DuplicateEffect` and the budget is never consulted. Reporting the
        # budget first told an operator to raise a limit when the real answer was that the effect
        # had already happened. T452.
        #
        # Not every earlier refusal returns before this: the scope block and the approval gate
        # record and carry on. `_observe_spend` skips itself once anything has blocked, which is
        # what keeps the report to the one refusal enforce mode would have raised.
        self._observe_spend(action, charges, observation, effect_key)
        return approval, reservation

    def _observe_take(
        self,
        action: Action,
        approval_id: str | None,
        effect_key: str | None,
        lease: timedelta,
        preconditions: _Preconditions | None,
        compared: _Compared,
        scope: _Preconditions | None = None,
        scoped: list[str | None] | None = None,
    ) -> tuple[Approval | None, Reservation | None]:
        """Observe mode's `_take`: **check the grant, never spend it** (SPEC-v0.6 §7.2.3).

        `_observe_secure` used to call `_take`, which consumes. An independent review found it,
        and §7.2.3's own sentence is the argument -- *a counterfactual is not a place to spend a
        real grant*. Observe mode blocks nothing: the action runs whatever the approval says, so
        consuming it destroyed a single-use answer that authorized nothing, and an operator
        evaluating a policy in observe mode silently burned their humans' grants.

        The **reservation is still taken**, and that asymmetry is deliberate. In observe mode
        the action genuinely executes, so the effect record has to exist or `v0.1 §5.4`'s
        duplicate refusal has nothing to refuse with. What observe mode suppresses is ctrlrun's
        *decisions*; it does not suppress the record of an effect that really happened.

        The verdict is computed with the same pure `check_consumable` every store applies, so
        the four refusals observe mode records are the four `_secure` would have raised, from
        one implementation rather than two.

        SPEC-v0.7 §6.8: **observe mode rechecks and records.** Where the grant is one enforce
        mode would consume, the precondition is compared exactly as `_recheck` compares it, and
        a refusal is raised here for `_observe_secure` to record; the action still runs and no
        grant is spent.
        """
        if approval_id is not None:
            found = self._store.get_approval(approval_id)
            verdict = check_consumable(
                found,
                approval_id,
                action.action_hash,
                self._clock(),
            )
            # SPEC-v0.8 §4.1: observe mode records what enforce mode would have done, and
            # enforce mode refuses a self-approval. This path never calls `_recheck`, so the
            # check is made here too or the row §4.1 describes does not exist. Before the
            # verdict's own refusal, on the same order `_recheck` uses.
            self._check_approver(action, approval_id, found, compared, verdict)
            if verdict.refusal is not None:
                raise verdict.refusal
            # `as_approval()` and not a hand-built `Approval`: one construction, so observe
            # mode cannot drift from what a store returns.
            record = verdict.record
            if record is not None:
                self._compare(action, record, preconditions, compared)
            approval = None if record is None else record.as_approval()
            # **And no event, which is the change worth naming.** `_observe_secure` used to
            # append `APPROVAL_CONSUMED` here. Nothing is consumed now, and there is no
            # `APPROVAL_PRESENTED` type to append instead -- §9 freezes the event vocabulary and
            # v0.6 adds none, deliberately. An event naming a write that did not happen is worse
            # than no event: it is the false-green shape, in the evidence log. What observe mode
            # found is on the observation and on the receipt's `approver`, which is where a
            # counterfactual belongs. The refusal paths keep their events, because there the
            # approval really was found unusable and that is a fact about the record.
            if effect_key is None:
                return approval, None
            return approval, self._store.reserve_effect(effect_key, action.action_id, lease)
        if effect_key is not None:
            return None, self._store.reserve_effect(effect_key, action.action_id, lease)
        return None, None

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

        **With one difference, and it is a narrowing: a continuation leg can never record
        `FAILED`** (SPEC-v0.7 §12.2.12). A continuation exists only because the remote answered
        once already and is holding the exchange, so nothing on this leg can say the remote did
        nothing: `ctrlrun.transport` will not claim `NotExecuted` here, and the gateway records
        an unknown outcome for everything it could otherwise call `FAILED` on a continuation.
        An executor that raises `NotExecuted` itself is still believed, as `v0.1 §5.5` says it
        is; what changed is that nothing in this library will hand it one.
        """
        self._report_clock_skew()
        held = self._store.take_continuation(continuation)
        action = held.action
        started_at, approval, compared, bound = self._resumed_context(
            action, held.record.created_at
        )
        # SPEC-v0.9 §10.1, read from the **ledger** rather than from the contextvar. §8.3 makes
        # this the only receipt an MCP multi round-trip or ACS action ever gets, so it has to
        # report what the action spent, and the two ways to get that wrong are both live: a
        # gateway that restarted mid-round has an empty contextvar and would report no charges
        # for an action that spent, and a gateway that ran another action in this context since
        # the suspension would report *that* action's spend. The ledger is the record; the first
        # leg wrote it inside the reservation's own transaction. T447, T448.
        # **Unconditional, because this call is also the reset.** `execute` clears
        # `_BUDGET_CHARGES` at its own top (§6.3.1's reason: a refusal carrying the previous
        # action's numbers); `resume` has no such line, so skipping this in observe mode left the
        # contextvar holding whatever the last action in this context had put there. An
        # independent review demonstrated the consequence: a resumed `observed` receipt for an
        # action whose own metric could not be measured reported a charge of 700 belonging to a
        # different effect. That is T448's defect on the observe path, on the one receipt §8.3
        # makes the whole evidence for an MCP multi round-trip. In observe mode the ledger is
        # empty by design, so this sets `()` and §4.2.1a's counterfactual is computed below.
        self._resumed_charges(held.effect_key, held.record.attempt)
        # SPEC-v0.10 §3.4, and `v0.9 §13.8`'s defect on the field beside the one it was found on.
        # `execute` clears five context variables at its top so a receipt cannot carry the
        # previous action's evidence; `resume` re-establishes four of them (the task, the hop and
        # the two authority values through `_authority_result`, the charges through the line
        # above) and left this one alone. A resumed receipt then reported an unrelated action's
        # scope hash, on the one receipt an MCP multi round-trip ever gets. A resumed leg fetches
        # no scope of its own (`v0.9 §5.3` runs the provider before the reservation, which this
        # leg already holds), so the honest value is none.
        _SCOPE_HASH.set(None)
        # SPEC-v0.3 §2.5 — a continuation is a store-wide token, so a Control in another
        # environment can reach one. Evaluating a staging action inside a production
        # deployment is the fail-open §2.5 exists to close.
        self._check_environment(action)
        self._append(
            EventType.EXECUTION_RESUMED,
            action,
            {"round": held.rounds},
            held.effect_key,
            approval=approval,
        )
        # SPEC: §6.9.2 — the policy is evaluated again for the *receipt*, not to re-decide.
        # The action was decided and reserved on the first leg; refusing here would strand a
        # reservation the remote may already be acting on, which is the one thing a held
        # reservation exists to avoid.
        #
        # SPEC-v0.3 §5.6.1 gives authority the same treatment, and for a sharper reason: this
        # is the *only* receipt an MCP multi round-trip or ACS action ever gets (§8.3), so a
        # receipt reporting a bare policy reason would be the whole evidence for that action.
        # SPEC-v0.10 §3.4.2 — evaluated on **both** dimensions where this build suspended the
        # action, because the event carries them. `evaluate_task=False` survives for exactly two
        # cases now: a lease extension, which genuinely has no task, and a leg **0.9.0**
        # suspended, whose `EXECUTION_STARTED` carries `{}` and for which evaluating the task
        # would deny every in-flight action across the upgrade (`v0.9 §6.4`).
        result = self._authority_result(
            action,
            task=bound.task,
            evaluate_task=bound.recorded,
            hop=bound.hop,
        )
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
        if self._observing:
            # SPEC-v0.9 §4.2.1a — **the counterfactual, recomputed.** Observe mode charges
            # nothing, so the ledger read above has nothing to find, and §8.3 makes this the only
            # receipt an MCP multi round-trip or ACS action ever gets. Without this, §4.2.1a's
            # sizing sum silently under-counts exactly the deployments §8.3 is about, which is an
            # independent review's finding and a contradiction between two sections of the spec.
            #
            # Enforce mode keeps the ledger read: there the row is evidence of a spend that
            # happened, and a recomputed number would be a claim about it instead.
            #
            # Computed below, once the **real** observation exists. It used to be handed a
            # throwaway `_Observation()`, which swallowed the block: the resumed receipt then
            # said `decision=ALLOW, blocked_reason=None` for an action enforce mode refuses,
            # while the two `ACTION_DENIED` events beside it said otherwise.
            pass
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
                observation.block(evaluation.reason, DecisionPoint.POLICY)
            # §4.2.1a's counterfactual, on the real observation and announcing nothing: the
            # first leg already wrote this action's `ACTION_DENIED`.
            self._observe_charges(action, held.effect_key, observation, announce=False)
        # SPEC-v0.7 §6.8: **no recheck on a resumed leg**, for `v0.6 §7.2.3`'s reason. The
        # approval was consumed on the first leg, after that leg's recheck, and refusing here
        # would strand a reservation the remote may already be acting on. The receipt says so:
        # the fingerprint the approval was requested with, and no recheck.
        return self._outcome(
            action,
            evaluation,
            executor,
            held.effect_key,
            held.record.attempt,
            approval,
            started_at,
            _Reconciler(None, False),
            held_key=held.effect_key,
            observation=observation,
            resumed=True,
            compared=compared,
            # SPEC-v0.10 §3.4.3 — **the hop the first leg held, into the lease extension.**
            # `_outcome` passes it to `_suspend`, which re-decides authority on every round after
            # the first. Without it that decision falls back to the receiver's whole candidate
            # set, so a receiver holding any grant of its own keeps its reservation across the
            # round trip after the hop is cut: `v0.3 §5.7`'s "a chain of any depth is cut by one
            # write" inverted, for exactly the actions the check at `:2325` exists to cut.
            #
            # §3.4.3 named this as item 2's residual to close. An independent review found it
            # open, with a control: without the receiver's own grant the same revocation does cut
            # round 2, which is the fallback §2.3 forbids.
            hop=bound.hop,
        )

    def _resumed_context(
        self, action: Action, fallback: datetime
    ) -> tuple[datetime, Approval | None, _Compared, _Bound]:
        """Recover the original attempt's evidence, including after a process restart.

        EXECUTION_STARTED durably binds the consumed approval to this action ID. Looking
        up a grant by action hash instead could attribute a later, unrelated approval.
        These events already exist in every supported store, including older databases;
        no continuation schema change or in-process cache is needed.

        SPEC-v0.7 §6.11 — and the first leg's comparison, off its `APPROVAL_CONSUMED`. This leg
        rechecks nothing (§6.8), and its receipt is the only one the action gets: recording what
        the leg that consumed the approval compared is what makes *on a committed action they
        are equal* true of a resumed one too.
        """
        proposed = fallback
        started = fallback
        approval_id = None
        compared = _Compared()
        # SPEC-v0.10 §3.4.2 — the task and the hop the first leg held, read back rather than
        # inferred. `v0.9 §6.3.2` skipped the task dimension entirely on this path because the
        # rehydrated action carries none; with the event carrying it there is nothing left to
        # skip, except on a leg 0.9.0 suspended.
        bound = _Bound()
        for event in self._store.events():
            if event.action_id != action.action_id:
                continue
            if event.type is EventType.ACTION_PROPOSED:
                proposed = event.ts
            elif event.type is EventType.APPROVAL_CONSUMED:
                compared.at_request = _hash_or_none(event.data.get("precondition_at_request"))
                compared.at_recheck = _hash_or_none(event.data.get("precondition_at_recheck"))
            elif event.type is EventType.EXECUTION_STARTED:
                started = proposed
                approval_id = event.approval_id
                # **Key presence, never the value** (§3.4.2). A 0.10 build running with a hop and
                # no task writes `{"hop": "dlg_…", "task": None}`; keying on the value would read
                # that as 0.9.0's `{}` and drop a hop the event is carrying.
                if "task" in event.data or "hop" in event.data:
                    task = event.data.get("task")
                    hop = event.data.get("hop")
                    bound = _Bound(
                        task=task if isinstance(task, str) else None,
                        hop=hop if isinstance(hop, str) else None,
                        recorded=True,
                    )
        record = None if approval_id is None else self._store.get_approval(approval_id)
        approval = None if record is None else record.as_approval()
        if compared.at_request is None and record is not None:
            # An approval consumed before this event carried the comparison, or by a path that
            # compared nothing: the record still says what it was requested with.
            compared.at_request = record.request.precondition_fingerprint
        if record is not None:
            # SPEC-v0.8 §2.5: who answered, recovered from the row for the same reason. **The
            # resumed leg's receipt is the only receipt an MCP multi round-trip or an ACS action
            # ever gets** (`SPEC-mcp-operator.md` §8.3), so without this the approvers reach the
            # evidence on every action except the ones that get exactly one receipt.
            compared.approvers = record.approvers
        return started, approval, compared, bound

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
        resumed: bool = False,
        hop: str | None = None,
        compared: _Compared | None = None,
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

        SPEC-v0.7 §4.3. The idempotency token is bound around `executor()` and nowhere else,
        for the attempt named by the key this attempt holds and the number the store assigned
        it. `execute` and `resume` both arrive here, which is why a resumed leg reads the token
        of the attempt it is resuming: `resume` passes `held.record.attempt` unchanged (§4.4).
        """
        # SPEC-v0.7 §2.3, §12.2.9 — the register of what this run offered, for exactly the
        # executor's call. A classifier claims `NotExecuted` only inside it, and only while it is
        # unmarked: a connection refused after another in the same run delivered proves nothing.
        # A resumed leg starts **marked** (§12.2.12). A continuation exists only because the
        # remote spoke, and the remote is holding the exchange, so nothing on that leg may say the
        # remote did nothing: the same sentence the lease extension above makes, that the remote
        # may already be acting on this reservation.
        opened = _ExecutorRun(_EXECUTOR_RUN.get(), offered=resumed)
        _opened(opened)
        run = _EXECUTOR_RUN.set(opened)
        try:
            try:
                # Both bindings are for exactly the executor's call, and the token's is item 3's
                # (§4.3): the register says what this run offered, the token names the attempt.
                with _attempt_token(held_key, attempt):
                    result = executor()
            finally:
                # Its own `finally`, and first: a run left in `_OPEN_RUNS` would go on being
                # marked by every context-less send for the life of the process, and one left
                # current in this context would be read by the next call on this thread.
                try:
                    _closed(opened)
                finally:
                    _EXECUTOR_RUN.reset(run)
        except Suspended as suspension:
            # SPEC-v0.2 §6.9 — no outcome, no receipt: the remote has not said what happened
            # and this attempt is not finished. Handled above the generic branch precisely so
            # it cannot be mistaken for one.
            #
            # SPEC-v0.3 §6.2 — with no reservation held there is no lease to extend and no
            # continuation to hold, which is §6.9.3's no-effect-key path reached for a
            # different reason. `held_key` says so.
            self._suspend(action, held_key, suspension, approval, hop)
            raise
        except NotExecuted as exc:
            if held_key is not None:
                # SPEC §5.5 — the executor asserted the remote side did nothing, so this is
                # the one outcome that leaves the key retryable (§5.4).
                try:
                    self._store.fail_effect(held_key, action.action_id, str(exc))
                except CTRLRunError as refused:
                    # SPEC: §5.2 — the record moved on while the executor ran: this attempt's
                    # lease lapsed and another declared the effect AMBIGUOUS, which only a
                    # human moves it out of. The store's refusal is what propagates, not the
                    # NotExecuted: an agent that caught that would retry a key it lost.
                    #
                    # **Every refusal the store can answer with, not two of them.** A record a
                    # human resolved while this attempt was still running answers
                    # `InvalidArgument`, which escaped: no receipt, no event, and the caller
                    # handed a store error about its own effect key. Found by review, round 2.
                    self._unrecorded(
                        action,
                        evaluation,
                        started_at,
                        held_key,
                        attempt,
                        refused,
                        approval,
                        compared,
                        did=f"the executor raised NotExecuted: {exc}",
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
                compared=compared,
            )
            raise
        except BaseException as exc:
            # SPEC: §5.5 — anything that is not NotExecuted is an AMBIGUOUS outcome, never
            # FAILED, and "anything" means BaseException: a KeyboardInterrupt mid-request
            # leaves the same unknown outcome a timeout does. Narrowing this to Exception
            # is a regression, not a cleanup.
            # The one place an executor's own text enters the system, so the one place to
            # make it storable. SQLite encodes every str it stores as UTF-8 and the receipt
            # chain canonicalizes what it hashes; a lone surrogate can do neither, so an
            # exception message carrying one used to raise `UnicodeEncodeError` out of
            # `mark_ambiguous` and strand the effect in EXECUTING -- neither outcome, and
            # blocked until the lease expired. Evidence about an action must never be able to
            # decide the action's fate. Lone surrogates arrive the ordinary way: from
            # `json.loads('"\\ud800"')`, and from `os.fsdecode` of a non-UTF-8 filename.
            error = _storable(f"{type(exc).__name__}: {exc}")
            recorded = effect_key is None
            if held_key is not None:
                try:
                    self._store.mark_ambiguous(held_key, action.action_id, error)
                    recorded = True
                except CTRLRunError as refused:
                    # Recording an unknown outcome must never mask the exception that caused
                    # it, and must never be lost with the refusal either. The receipt and the
                    # event below are written whatever the store answered, and they carry the
                    # refusal, because where the store would not take the outcome the effect
                    # record does not carry it: the evidence is then the only place it exists.
                    #
                    # **Every refusal, not two of them, and it is named rather than logged
                    # away.** `DuplicateEffect` and `AmbiguousEffect` were caught and the
                    # comment here said the record was in a state "a human already owns"; a
                    # record a human resolved `FAILED` while this attempt was still running
                    # answers `InvalidArgument` instead, which escaped this handler entirely,
                    # so an unknown outcome reached no receipt and no event and the caller was
                    # handed a store error in place of its executor's exception. Found by
                    # review, round 2 (SPEC-v0.7 §12.3a).
                    _LOG.warning("%s: effect %s: %s", action.name, effect_key, refused)
                    error = _storable(
                        f"{error} (the effect record does not carry this outcome: the store "
                        f"refused the write with {type(refused).__name__}: {refused})"
                    )
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
                compared=compared,
            )
            raise
        if held_key is not None:
            try:
                self._store.commit_effect(held_key, action.action_id, result)
            except CTRLRunError as refused:
                # SPEC: §5.2 — the executor returned, but the key is no longer this attempt's
                # to commit: the lease lapsed and the record is AMBIGUOUS until a human says
                # otherwise. What happened at the remote is now as unknown as a timeout, so
                # the attempt is recorded `ambiguous` rather than committed.
                self._unrecorded(
                    action,
                    evaluation,
                    started_at,
                    held_key,
                    attempt,
                    refused,
                    approval,
                    compared,
                    did="the executor returned, so the remote may well have acted",
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
            compared=compared,
        )

    def _suspend(
        self,
        action: Action,
        effect_key: str | None,
        suspension: Suspended,
        approval: Approval | None,
        hop: str | None = None,
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
        # SPEC-v0.10 §3.4.3 — **the hop is passed in, never read from the context.** This is the
        # one re-decision point inside an action, so without it a receiver holding any grant of
        # its own keeps its reservation across the round trip after the hop is cut, and the
        # sentence below about `v0.3 §5.7` becomes false for exactly the actions this check
        # exists to cut.
        #
        # An earlier draft read `_HOP` here. `_outcome`'s own docstring says "`execute` and
        # `resume` both come through here", so on every round after the first this is reached
        # from `resume`, whose ambient context belongs to the resuming process: unset, or
        # holding some unrelated hop that would then decide this action's extension. A review
        # round found it, and the repair is that the caller who knows says so.
        result = self._authority_result(action, evaluate_task=False, hop=hop)
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
        return self._resolve_template(action, template, "effect")

    def _resolve_template(self, action: Action, template: str | None, what: str) -> str | None:
        """`_resolve_effect`'s body, over either template `@protect` resolves.

        **SPEC-v0.9 §6.3.1's task template goes through here, and an independent review is why.**
        `@protect(task="{run_id}")` with no such argument raised `EffectKeyError` past every
        recording path: no `ACTION_PROPOSED`, no `ACTION_DENIED`, no denied receipt, and a caller
        handed a template error about an action nothing recorded. That is the shape
        `control.py`'s own round-two comment records finding once before, on a store refusal.

        One function rather than two, so the effect template and the task template cannot drift
        into recording different things for the same class of mistake.
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
                {"reason": UNRESOLVED_EFFECT, what: template, "error": str(exc)},
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
        preconditions: _Preconditions | None,
        compared: _Compared,
        scope: _Preconditions | None = None,
        scoped: list[str | None] | None = None,
    ) -> tuple[Approval | None, Reservation | None]:
        """Take everything this action needs before it may run: the grant, and the key.

        Both are taken in one store transaction (SPEC-v0.1 §4.2 A4). The approval is checked
        first, so a replayed approval is what gets raised when a duplicate effect would also
        apply (T4), and a refused reservation leaves the approval granted for the action the
        human actually saw (T12).

        SPEC-v0.7 §6.2: under `APPROVE`, the precondition is rechecked **strictly before each
        `_take`**, so the provider can never run after a reservation exists. "Each" because this
        may take twice, once more after a `reconcile` hook moves an `AMBIGUOUS` record, and the
        hook is a network call whose duration would otherwise sit inside the window.
        """
        # SPEC-v0.9 §2.7 — every ancestor charged, assembled once and passed to both passes so a
        # reconcile between them cannot change what this action spends.
        #
        # **Before the keyless early return below**, because §2.4.1's refusal is exactly about an
        # action that reaches it: a budgeted grant whose action resolved no effect key spends
        # nothing against every budget on the chain, for ever, and returning early would be the
        # kernel declining to notice.
        #
        # **And before the approval gate**, because §2.3's and §2.4.1's refusals depend on nothing
        # the gate produces and are unconditional: the action cannot run whatever a human says.
        # Assembling after the gate asks a human to approve a refund the kernel has already
        # decided to refuse, and leaves a granted approval behind for an action nothing can
        # execute. A probe found `APPROVAL_REQUESTED` written for exactly that shape. T446.
        # SPEC-v0.10 §4.3's check 2. **Above the approval gate**, on T446's argument: the pin
        # depends on nothing a human says, so asking one about an action pinned to a server this
        # process has not verified leaves a granted approval behind for a call that cannot run.
        refused_upstream = self._upstream_reason(action)
        if refused_upstream is not None:
            raise self._refuse_upstream(action, refused_upstream, effect_key)
        charges = self._charges_for(action, effect_key)
        approval_id = (
            self._presented(action, effect_key, evaluation, started_at, preconditions)
            if evaluation.decision is Decision.APPROVE
            else None
        )
        if approval_id is None and effect_key is None:
            # SPEC-v0.6 §7.2's `ALLOW` row, which §7.2.2 step 1 quietly assumed a reservation
            # for. There is nothing to take here -- no grant to check, no key to hold -- but a
            # presented approval is still a live bearer token this policy says is not needed,
            # and an independent review found the row silently not firing for exactly the class
            # of action `v0.1 §5.1` documents as the escape hatch: one with no `effect:`
            # template. Item 7's own throwaway configuration contains one.
            #
            # It is reachable by the ordinary route, because the **`APPROVE`** path consumes
            # without a reservation too -- `_take(action, approval_id, None, lease)`. So grant,
            # relax the rule to `allow`, present: the grant outlived the answer for its full
            # TTL, which is the precise hazard §7.2 exists to close.
            return self._spend_unneeded_approval(action, None), None

        # At most two passes: an `AMBIGUOUS` refusal may be reconciled once (SPEC-v0.2 §2.3),
        # and whatever the second attempt meets is final.
        for reconciled in (False, True):
            try:
                # SPEC-v0.9 §5.3, §5.7 — **before the recheck and before every `_take`**. It is
                # in the loop and not above it because the `reconcile` hook between the two
                # passes is a network call, and a scope fetched before it would be compared
                # against a world that moved while it ran. §5.8 states what this costs the
                # precondition's own window, which is that it now contains this call.
                self._in_scope(action, scope, scoped)
                if approval_id is not None:
                    # **Immediately before `_take`, and nothing between them.** The window this
                    # narrows is the time from the provider's fetch to the store call; anything
                    # inserted here widens it, and a later item adding a check on this path
                    # (the attempt ceiling, §5.5) belongs before this line or after `_take`.
                    self._recheck(action, approval_id, preconditions, compared)
                approval, reservation = self._take(action, approval_id, effect_key, lease, charges)
                # SPEC-v0.9 §10.1 — **after the store call took**, and an independent review is
                # why. Set where the charges were computed, a refusal raised later in this loop
                # still reached `_record` with them stamped, so a `denied` receipt claimed the
                # action charged the very grant it was refused from spending against. A receipt
                # asserting a spend that never happened is the one thing an evidence trail may
                # not do.
                _BUDGET_CHARGES.set(
                    tuple(
                        {
                            "grant_id": charge.grant_id,
                            "metric": charge.metric,
                            "amount": charge.amount,
                        }
                        for charge in charges
                    )
                )
                break
            except BudgetExhaustedError as exhausted:
                # SPEC-v0.9 §3.3.2 — **its own clause, before the `ActionDenied` one**, for the
                # reason item 2 met first with the scope refusal: that handler appends
                # `APPROVAL_DENIED` unconditionally, which would fabricate an approval denial for
                # an action no human ever saw. An exception raised inside an `except` clause
                # leaves the whole `try` rather than meeting its siblings.
                raise self._refuse_budget(action, exhausted) from None
            except _ScopeRefusedError as refused:
                # SPEC-v0.9 §5.6. Its own clause, **before** the `ActionDenied` one:
                # `_refuse_scope` has already written the events and the receipt, and an
                # exception raised inside an `except` clause leaves the whole `try` rather than
                # meeting its siblings. Routed through `except ActionDenied` instead, this would
                # append `APPROVAL_DENIED` for an action no human saw and a second
                # `ACTION_DENIED` (§3.3.2's hazard, the same handler).
                raise refused.denial from None
            except AmbiguousEffect as refused:
                # SPEC-v0.7 §3.6, before anything else: a store with its own clock re-measures
                # when an expired lease is declared AMBIGUOUS, and the report belongs beside this
                # refusal, naming this attempt. It changes nothing about the refusal.
                self._report_clock_skew(action, effect_key)
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
                        compared=compared,
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
                    self._invalidated(action, mismatch, compared),
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
                    compared=compared,
                )
                raise
            except DuplicateEffect as refused:
                # SPEC §5.4 — this effect already happened or is happening now. The approval,
                # if one was presented, was not consumed: it is still worth something.
                self._refused(
                    action,
                    evaluation,
                    started_at,
                    effect_key,
                    refused,
                    approval_id=approval_id,
                    compared=compared,
                )
                raise

        if approval is not None:
            # SPEC-v0.7 §6.11 — what was compared, on the event that says the grant was spent.
            # A suspended action writes no receipt on this leg, and the resumed leg's is the
            # only receipt it ever gets, so without this the one comparison that happened would
            # leave no trace at all. Hashes only, and absent where nothing was compared.
            self._append(
                EventType.APPROVAL_CONSUMED,
                action,
                {"approver": approval.approver, **compared.spent()},
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
        if approval is None and evaluation.decision is not Decision.APPROVE:
            approval = self._spend_unneeded_approval(action, effect_key)
        return approval, reservation

    def _spend_unneeded_approval(self, action: Action, effect_key: str | None) -> Approval | None:
        """SPEC-v0.6 §7.2's `ALLOW` row: close a token the action no longer needs.

        The policy said `APPROVE` when a human was asked and says something permissive now. Left
        alone, the presented approval stays `granted` for its full TTL, bound to a hash a later
        policy edit could make `APPROVE`-requiring again -- a live bearer token for an action a
        human already answered, and `v0.1 §4.1` calls a request id a bearer token in as many
        words. **This is a change to shipped behaviour and is the reason §7.2 exists.**

        **After the reservation, and in a write of its own** (§7.2.2). An earlier draft said
        *consumed anyway, in the same transaction*, which would have added a refusal path to the
        permissive decision: `consume_approval_and_reserve` checks the approval **first**
        (`v0.1 §4.2 A4`), so an approval that is expired, already consumed or denied raises
        `ApprovalMismatch` -- and an action the policy allows would be refused because of an
        approval it did not need. The reachable case is ordinary: an agent retries inside
        `with_approval(id)` after the operator relaxed the rule, the grant having been spent on
        the first attempt.

        So nothing here can refuse the action. A failure is logged and the action proceeds,
        because the policy permits it outright and there is nothing to protect.
        """
        approval_id = _PRESENTED_APPROVAL.get(None)
        if approval_id is None:
            return None
        try:
            spent = self._store.consume_approval(approval_id, action.action_hash)
        except Exception as refused:
            # **`Exception`, not `CTRLRunError`, and the width is the point.** An independent
            # review found this catching only `CTRLRunError`, so a `sqlite3.OperationalError`
            # ("database is locked"), a dropped `psycopg` connection or any other store failure
            # propagated out of `_secure` -- **after** the reservation was taken and **before**
            # `begin_execution`. The action the policy allows was refused, no receipt was
            # written at all (`execute` raises before `_record`), and the effect key was left
            # `RESERVED` with nothing holding it until the lease lapsed: an ambiguity
            # manufactured by the *permissive* decision path, and the case T154d is about
            # arriving from a new direction.
            #
            # Catching everything is safe here for the reason the docstring gives and for no
            # other: **there is nothing to protect.** The policy permits this action outright,
            # this call closes a token it does not need, and a token that failed to close stays
            # bounded by its own `expires_at`. No other handler in this file may widen on that
            # argument -- it holds because the decision was already permissive, not because a
            # store error is unimportant.
            _LOG.info(
                "%s: the presented approval %s was not consumable (%s); the policy allows this "
                "action outright, so it proceeds",
                action.name,
                approval_id,
                refused,
            )
            return None
        self._append(
            EventType.APPROVAL_CONSUMED,
            action,
            {"approver": spent.approver, "required": False},
            effect_key,
            approval_id=spent.approval_id,
        )
        return spent

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
        compared: _Compared | None = None,
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
            compared=compared,
        )

    # --- the attempt ceiling (SPEC-v0.7 §5) ---------------------------------------------

    def _ceiling(self, action: Action, effect_key: str | None) -> int | None:
        """This action's `max_attempts`, or `None` where the operator named none (§5.3).

        **A ceiling on an action that resolves no effect key counts nothing**, and is warned
        about once per action rather than refused at load: the `effect:` template may come from
        the decorator, which the policy cannot see (`v0.2 §3.2`), so the loader cannot tell a
        ceiling that will count from one that cannot. Same treatment as a `reconcile` hook with
        no key, and for the same reason.
        """
        ceiling = self._policy.max_attempts(action.name)
        if ceiling is not None and effect_key is None and action.name not in self._ceiling_warned:
            self._ceiling_warned.add(action.name)
            _LOG.warning(
                "%s: max_attempts is %d but this call resolves no effect key, so there is no "
                "record to count attempts on and nothing is bounded. Declare effect= on "
                "@protect, or an 'effect:' template in the policy",
                action.name,
                ceiling,
            )
        return ceiling

    @staticmethod
    def _over_the_ceiling(ceiling: int | None, attempt: int) -> bool:
        """Whether this attempt number is past the operator's ceiling (SPEC-v0.7 §5.5).

        One comparison for both defences, so there is one definition of "past the ceiling" and
        not two that can drift. `None` is no ceiling, which is `v0.1 §5.4` exactly.
        """
        return ceiling is not None and attempt > ceiling

    def _ceiling_fast_path(self, effect_key: str | None, ceiling: int | None) -> int | None:
        """The attempt number a read before the approval gate refuses, or `None` (§5.5).

        **It refuses only a `FAILED` record**, normatively. A record in any other state,
        `AMBIGUOUS` above all, passes untouched to the reservation, which refuses or reconciles
        it exactly as at 0.6.1; T245's route and G15 both depend on that, and a fast path that
        answered for an `AMBIGUOUS` record would take the check's only test away from it.

        It is a fast path and **never the guarantee**: two callers who both read attempt N-1
        both pass it. What it buys is that the ordinary sequential case never writes, never
        spends a presented approval and never asks a human.
        """
        if ceiling is None or effect_key is None:
            return None
        record = self._store.get_effect(effect_key)
        if record is None or record.state is not EffectState.FAILED:
            return None
        attempt = record.attempt + 1
        return attempt if self._over_the_ceiling(ceiling, attempt) else None

    def _refuse_ceiling(
        self,
        action: Action,
        evaluation: Evaluation,
        started_at: datetime,
        effect_key: str | None,
        *,
        attempt: int,
        ceiling: int | None,
        reserved: bool,
        approval: Approval | None = None,
        approval_id: str | None = None,
    ) -> NoReturn:
        """Refuse one attempt for the operator's ceiling, and say so in the history (§5.5).

        Above the ceiling the executor is not called. Where a reservation was taken, the record
        is released as `FAILED` through `begin_execution` and `fail_effect`, with an error naming
        the ceiling: `FAILED` is true here, because nothing ran. `EXECUTION_STARTED` is **not**
        appended, because that event is the claim that something started. The refused number is
        **spent** either way (§5.5): the reservation assigned it, so an operator who later raises
        the ceiling buys the difference minus the numbers refusals already consumed.

        `ActionDenied` and not `DuplicateEffect`, `AmbiguousEffect` or `NotExecuted`: it is the
        only type in the closed set whose meaning is true, "the action may not run, and `reason`
        says why". `max_attempts` is a policy saying no, and an agent loop's `except ActionDenied`
        is written for exactly that. The receipt is `blocked` rather than `denied` because it
        describes what stopped the attempt, which is the effect's own history, and it keeps the
        decision the policy actually reached (`v0.1 §6.1`, `§4.2 A1`).

        **The evidence is written before the release is attempted**, and a refusal of any type is
        caught. A review found `InvalidArgument` escaping here: `_checked` raises it where the
        record moved under a different `action_id` with a dead lease (`state.py`), and on the old
        ordering that left the record `RESERVED` *and* the refusal with no event and no receipt.
        The store's own exception still propagates, as `v0.1 §5.5` has it; what changed is that it
        can no longer take the evidence with it.

        **On the fast path nothing was reserved**, so `attempt` is the number this call *would*
        have been given. Two calls refused in a row therefore carry the same number, which is not
        a defect: no number was assigned to either, and the alternative is a receipt that names an
        attempt the store never wrote.
        """
        error = f"attempt {attempt} refused: max_attempts is {ceiling} (SPEC-v0.7 §5)"
        presented = approval.approval_id if approval is not None else approval_id
        self._append(
            EventType.EFFECT_RESERVATION_REFUSED,
            action,
            {
                "reason": BLOCKED_ATTEMPT_CEILING,
                "attempt": attempt,
                "max_attempts": ceiling,
            },
            effect_key,
            approval=approval,
            approval_id=approval_id,
        )
        self._record(
            action,
            evaluation,
            ReceiptResult.BLOCKED,
            started_at,
            error=error,
            approval=approval,
            approval_id=approval_id,
            approver=None if approval is not None else self._approver_of(presented),
            effect_key=effect_key,
            attempt=attempt,
        )
        if reserved and effect_key is not None:
            # §5.7 — where the record moved on while the ceiling was deciding, one of these
            # refuses and **that refusal propagates**, in place of the `ActionDenied` below, as
            # `v0.1 §5.5` has a store's refusal propagate: `AMBIGUOUS` is not something this
            # path may collapse to `FAILED`. It propagates by not being caught. A round of
            # review found the `try`/`except CTRLRunError: raise` that used to stand here to be
            # an equivalent mutant, green with the whole clause deleted, because it caught only
            # to re-raise the same object. What is load-bearing is that the evidence above is
            # written **first**, which T249 grades and which a reordering mutant kills.
            self._store.begin_execution(effect_key, action.action_id)
            self._store.fail_effect(effect_key, action.action_id, error)
        raise ActionDenied(
            f"{action.name} denied: {error}",
            reason=BLOCKED_ATTEMPT_CEILING,
            action_id=action.action_id,
        )

    def _unrecorded(
        self,
        action: Action,
        evaluation: Evaluation,
        started_at: datetime,
        effect_key: str,
        attempt: int,
        refused: CTRLRunError,
        approval: Approval | None,
        compared: _Compared | None = None,
        *,
        did: str,
    ) -> None:
        """Record an outcome the store refused to write (SPEC-v0.1 §5.2, §5.5).

        The executor finished, but the effect record moved on while it ran, so what happened
        at the remote is exactly as unknown as a timeout: `ambiguous`, whatever the executor
        returned or raised. A terminal action still gets its receipt (§6.1).

        **`did` is what the executor did, and it belongs in the receipt beside the refusal.**
        Recording only the refusal made two receipts identical that a human must be able to tell
        apart: an executor that *returned* leaves a remote that very likely acted, and one that
        raised `NotExecuted` leaves a remote that very likely did not. With the effect record
        carrying neither, the receipt is the only place that fact exists, and whoever runs
        `ctrlrun resolve` on this key has nothing else to go on. Found by review, round 3.
        """
        error = _storable(
            f"{did}, and the outcome write was refused: {type(refused).__name__}: {refused}"
        )
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
            compared=compared,
        )

    def _presented(
        self,
        action: Action,
        effect_key: str | None,
        evaluation: Evaluation,
        started_at: datetime,
        preconditions: _Preconditions | None,
    ) -> str:
        """The approval this call presents, or record a request and suspend the action.

        With nothing presented, `ApprovalRequired` is raised so the caller can come back with
        `with_approval(request_id)` (SPEC-v0.1 §4.3). No receipt is written for that: the
        action is suspended awaiting a human, which is not a terminal state (§6.1). The
        `APPROVAL_REQUESTED` event is the evidence.
        """
        # SPEC-v0.8 §4.2: a threshold above one in a deployment that verifies nobody has no
        # referent for "distinct principals": the count could never move, or distinctness would
        # fall back to the string §4.1 forbids. Denied here, before a human is asked, because
        # asking somebody to answer a request that can never be completed spends their attention
        # on nothing (`v0.3 §4.3`'s argument for refusing before the approval gate).
        required = self._policy.approvals_required(action.name)
        if required > 1 and self._approver_identity is None:
            self._refuse_unverifiable(action, evaluation, started_at, effect_key, required)
        presented = _PRESENTED_APPROVAL.get(None)
        if presented is not None:
            return presented
        # SPEC-v0.7 §6.2: the fingerprint is captured here, before the request exists, and a
        # provider that produces none refuses the action before any human is asked (§6.5).
        fingerprint = None
        if preconditions is not None:
            fingerprint, error = _fetched(preconditions, action)
            if fingerprint is None:
                self._refuse_unfetched_request(action, evaluation, started_at, effect_key, error)
        # SPEC-v0.6 §7.1 — the request records which policy was in force while it was built.
        # `Control` is the only object holding both a policy and a provider, and the provider
        # protocol takes neither, so it travels the way a presented approval does. The
        # fingerprint travels beside it, by the same route and for the same reason.
        # SPEC-v0.8 §3.3: which controls the evaluation that sent this action to approval cited,
        # and what role each demands. Built here rather than on `Policy`, because `Control` is
        # the only object that holds both the evaluation and the registry, and §11.1 freezes no
        # accessor for it.
        roles = tuple(
            RequiredRole(control=identifier, role=control.approver_role)
            for identifier, control in (
                (identifier, self._policy.controls.get(identifier))
                for identifier in evaluation.controls
            )
            if control is not None and control.approver_role
        )
        try:
            with (
                policy_in_force(self._policy_hash),
                _precondition_at_request(fingerprint),
                _required_roles(roles, self._policy.approvals_required(action.name)),
            ):
                request = self._approvals.request(action, self._approval_ttl)
        except Exception:
            if fingerprint is not None:
                # SPEC-v0.7 §6.4's residual, the half with no race in it: a provider that
                # recorded a request and *then* raised leaves a row `Control` never learns the
                # id of, so there is nothing to withdraw. The exception is the provider's and
                # propagates; what the kernel owes is a line saying a fingerprint was computed,
                # so an operator knows an unfingerprinted request may be sitting in the store.
                _LOG.warning(
                    "%s: the approval provider raised after a precondition fingerprint was "
                    "computed; if it recorded a request before raising, that request carries no "
                    "fingerprint and no presentation of it can compare anything (SPEC-v0.7 §6.4)",
                    action.name,
                )
            raise
        # The request exists in the store from here, whatever happens next, so it is recorded
        # before anything is decided about it: a row with no `APPROVAL_REQUESTED` behind it is
        # evidence nobody can read.
        self._append(
            EventType.APPROVAL_REQUESTED,
            action,
            {"action_hash": action.action_hash, "expires_at": iso_timestamp(request.expires_at)},
            effect_key,
            approval_id=request.request_id,
        )
        # **Asked whenever anything was pinned**, not only for a fingerprint. The earlier
        # spelling was `if fingerprint is not None`, which meant a deployment with no
        # preconditions at all -- the common one -- never read the row back, and so never saw a
        # threshold or a role list that failed to reach it.
        missing = (
            self._unpinned(request, fingerprint, roles, required)
            if fingerprint is not None or roles or required > 1
            else None
        )
        if missing is not None:
            # SPEC-v0.7 §6.4: **never a skip**, and without this it was one. A provider that
            # builds its own `ApprovalRequest` (`build_request` is package-internal) and a store
            # that does not persist the column both leave an approval that was requested with a
            # fingerprint carrying none -- and an approval with none, presented by a call that
            # names no provider, is 0.6.1's path: consumed with nothing compared. The request
            # pass is where that is visible, so it is where it is refused.
            self._refuse_unrecorded_request(
                action, evaluation, started_at, effect_key, request, fingerprint, missing
            )
        raise ApprovalRequired(
            f"{action.name} requires approval: run 'ctrlrun approve {request.request_id}', "
            f"then retry inside ctrlrun.with_approval({request.request_id!r})",
            request_id=request.request_id,
            action_id=action.action_id,
        )

    def _refuse_unverifiable(
        self,
        action: Action,
        evaluation: Evaluation,
        started_at: datetime,
        effect_key: str | None,
        required: int,
    ) -> None:
        """§4.2's refusal: `approvals_required` above one, and nobody to count (§12).

        Not a load error: the policy is loadable and correct, and what is missing is the
        `Control` it was deployed in, which the loader cannot see.
        """
        message = (
            f"{action.name}: 'approvals_required: {required}' needs an approver identity, and "
            "this deployment names none; distinct principals cannot be counted where nobody is "
            "verified (SPEC-v0.8 §4.2)"
        )
        self._append(
            EventType.ACTION_DENIED, action, {"reason": APPROVALS_UNVERIFIABLE}, effect_key
        )
        self._record(
            action,
            Evaluation(Decision.DENY, APPROVALS_UNVERIFIABLE, evaluation.controls),
            ReceiptResult.DENIED,
            started_at,
            error=message,
            effect_key=effect_key,
        )
        raise ActionDenied(message, reason=APPROVALS_UNVERIFIABLE, action_id=action.action_id)

    def _refuse_unfetched_request(
        self,
        action: Action,
        evaluation: Evaluation,
        started_at: datetime,
        effect_key: str | None,
        error: str | None,
    ) -> NoReturn:
        """SPEC-v0.7 §6.2's request-pass row: no fingerprint, so no request and no human.

        `ActionDenied(reason="precondition_unavailable")`, `ACTION_DENIED` with the reason, and a
        `denied` receipt keeping `decision: approve`, because the policy did decide `approve` and
        what refused the action was a provider that could not be read. The error is by type
        name only (§6.5).
        """
        _LOG.warning(
            "%s: the precondition provider produced no fingerprint (%s), so no approval is "
            "requested (SPEC-v0.7 §6.5)",
            action.name,
            error,
        )
        self._append(
            EventType.ACTION_DENIED,
            action,
            {"reason": _PRECONDITION_UNAVAILABLE, "error": error},
            effect_key,
        )
        message = (
            f"{action.name}: the precondition provider produced no fingerprint ({error}), so no "
            "approval was requested"
        )
        self._record(
            action,
            evaluation,
            ReceiptResult.DENIED,
            started_at,
            error=message,
            effect_key=effect_key,
        )
        raise ActionDenied(message, reason=_PRECONDITION_UNAVAILABLE, action_id=action.action_id)

    def _unpinned(
        self,
        request: ApprovalRequest,
        fingerprint: str | None,
        roles: tuple[RequiredRole, ...],
        required: int,
    ) -> str | None:
        """What the kernel pinned and the stored row does not carry, or `None` (§6.4, §4.5).

        **The read-back, and only the read-back.** An earlier build also compared the returned
        `ApprovalRequest`, and the review found that guard subsumed: a presentation reads the
        store, so a returned object that differs from the row changes nothing a later pass sees,
        and every way of losing the fingerprint that a presentation could meet -- a store
        without the column, a provider that builds its own request -- is visible here. A guard
        that can only fire where a later one would, with the same result, is documentation
        rather than defence (`CONTRIBUTING.md`, the first of the four shapes of a false green).

        One `get_approval`, on the request pass only.

        **Three fields, not one, and the two new ones are v0.8's.** `v0.7 §6.4` wrote this for
        the precondition fingerprint and named both ways of losing it: a provider that builds
        its own `ApprovalRequest`, and a store that does not persist the column. Items 3 and 4
        pin the required roles and the threshold by exactly the same route, and an independent
        review found that both were lost in exactly the same two ways, with no refusal anywhere:
        a row pinning `required_roles=()` satisfies `unsatisfied` trivially, and a row pinning
        `approvals_required=1` grants on one yes. An action then executed under a policy
        demanding two approvals from a named role, approved once by somebody holding no role.

        So the read-back covers every field this method pins. A row missing any of them cannot
        be compared against what was in force, and `v0.7 §6.4`'s rule is that such a request is
        refused where it is visible rather than skipped.
        """
        record = self._store.get_approval(request.request_id)
        if record is None:
            return "the request was not recorded at all"
        stored = record.request
        if fingerprint is not None and stored.precondition_fingerprint != fingerprint:
            return "the precondition fingerprint"
        if stored.required_roles != roles:
            return (
                f"the roles the cited controls require ({[role.control for role in roles]}); "
                f"the row carries {[role.control for role in stored.required_roles]}"
            )
        if stored.approvals_required != required:
            return (
                f"the threshold of {required} approvals; the row carries "
                f"{stored.approvals_required}"
            )
        return None

    def _refuse_unrecorded_request(
        self,
        action: Action,
        evaluation: Evaluation,
        started_at: datetime,
        effect_key: str | None,
        request: ApprovalRequest,
        fingerprint: str | None,
        missing: str,
    ) -> NoReturn:
        """Refuse, and leave nothing behind that another path could spend (SPEC-v0.7 §6.4).

        Refusing this call alone would not be enough: the request the provider already recorded
        is answerable, and a human granting it would leave a grant any call naming no provider
        could spend with nothing compared. It is withdrawn through `deny_approval`, an existing
        store method, so `check_consumable` refuses it for ever with a denial's own reason; a
        grant that landed inside the window is withdrawn by being spent instead, on nothing.

        The residual is stated rather than hidden: `Control` learns the request exists only when
        the provider returns, so an approval granted **and presented** before that is spent
        before there is anything to withdraw. Closing that needs a store call that records the
        request and its fingerprint together, and `StateStore` is frozen (`v0.6 §9.2`).
        """
        withdrawn = self._withdraw(request)
        compared = _Compared()
        if fingerprint is not None:
            compared.at_recheck = fingerprint
        # The fingerprint keeps its own reason, because `precondition_missing` is in the
        # vocabulary `v0.7` froze and every reader of an older receipt reads it that way. What
        # items 3 and 4 pin gets its own, for the reason every refusal here does: a test that
        # asserts a status cannot tell which guard ran.
        reason = _PRECONDITION_MISSING if fingerprint is not None else APPROVAL_UNRECORDED
        outcome = (
            f"the request is withdrawn ({withdrawn})"
            if withdrawn in _WITHDRAWALS
            else f"the request could not be withdrawn ({withdrawn})"
        )
        _LOG.warning(
            "%s: %s was not recorded with approval request %s, so %s and the action is refused "
            "(SPEC-v0.7 §6.4, SPEC-v0.8 §4.5)",
            action.name,
            missing,
            request.request_id,
            outcome,
        )
        self._append(
            EventType.APPROVAL_INVALIDATED,
            action,
            {
                "reason": reason,
                "missing": missing,
                "action_hash": action.action_hash,
                "withdrawn": withdrawn,
                **compared.data(),
            },
            effect_key,
            approval_id=request.request_id,
        )
        self._append(
            EventType.ACTION_DENIED,
            action,
            {"reason": reason},
            effect_key,
            approval_id=request.request_id,
        )
        message = (
            f"{action.name}: {missing} was not recorded with approval request "
            f"{request.request_id}, so no presentation of it could compare anything; {outcome}"
        )
        self._record(
            action,
            evaluation,
            ReceiptResult.DENIED,
            started_at,
            error=message,
            approval_id=request.request_id,
            effect_key=effect_key,
            compared=compared,
        )
        raise ActionDenied(message, reason=reason, action_id=action.action_id)

    def _withdraw(self, request: ApprovalRequest) -> str:
        """Make a request nobody may answer, with the methods a store already has (§6.4).

        `deny_approval` for a request still pending, which is the ordinary case and leaves a
        record `check_consumable` refuses by `approval_denied`. A record that is no longer
        pending refuses that, so a grant that landed inside the window is spent instead: a
        consumed approval authorizes nothing either, and nothing was reserved or run for it.

        **What it returns is what happened, and it reads the row back to find out.** An earlier
        build reported the status it had read *before* its own failed `consume_approval`, and
        said `consumed` whether this call had spent the grant or another caller had: a
        presentation that won the race ran the action while the evidence said the request had
        been withdrawn `granted`. The answers are distinct now -- `denied` and `spent` for this
        call's own writes, `already_consumed` where somebody else got there first, and
        `not_withdrawn:<what the row says>` where nothing was withdrawn -- and only the first
        two let the refusal call itself a withdrawal.

        **Every exception is caught, and the width is the point**, as in
        `_spend_unneeded_approval` for the opposite reason. There the action proceeds because
        there is nothing to protect; here it is refused whatever the store does, so catching a
        driver error can only add a refusal and its evidence. Letting one out left no
        `ACTION_DENIED`, no receipt, and an answerable request carrying no fingerprint, which is
        the hole this method exists to close.
        """
        try:
            self._store.deny_approval(request.request_id, _WITHDRAWN_BY)
            return "denied"
        except Exception as refused:
            _LOG.info("%s could not be denied (%s); it is not pending", request.request_id, refused)
        record = self._read_back(request)
        if record is not None and record.status is ApprovalStatus.GRANTED:
            try:
                self._store.consume_approval(request.request_id, record.action_hash)
                return "spent"
            except Exception as refused:
                _LOG.warning(
                    "%s was granted inside the window and could not be spent (%s)",
                    request.request_id,
                    refused,
                )
        found = self._read_back(request)
        if found is None:
            return "not_withdrawn:absent"
        if found.status is ApprovalStatus.DENIED:
            # Denied while this call was looking, by a human or by another withdrawal: the
            # request is unanswerable, which is what this method is for.
            return "denied"
        if found.status is ApprovalStatus.CONSUMED:
            return "already_consumed"
        return f"not_withdrawn:{found.status}"

    def _read_back(self, request: ApprovalRequest) -> ApprovalRecord | None:
        """The record as it stands now, or `None` where there is none or it cannot be read.

        A store that raises here leaves the caller saying `not_withdrawn:absent`, which is the
        honest answer when nothing can be read: it claims no write.
        """
        try:
            return self._store.get_approval(request.request_id)
        except Exception as refused:
            _LOG.warning(
                "%s: the approval record could not be read back (%s)", request.request_id, refused
            )
            return None

    def _in_scope(
        self,
        action: Action,
        scope: _Preconditions | None,
        seen: list[str | None] | None,
        *,
        enforcing: bool = True,
    ) -> None:
        """SPEC-v0.9 §5: fetch the principal's scope and match this action's resource into it.

        **Strictly before the reservation, before every `_take`** (§5.3), and before `_recheck`
        (§5.7): `out_of_scope` says the principal never had the right to the record and
        `precondition_changed` says the record moved, and an operator handed the second when the
        first is true goes looking for a race that is not there.

        The ordering is the safety argument and not a preference. After the reservation, a
        provider that hangs leaves a lease to lapse and an `AMBIGUOUS` record nobody can resolve:
        a *scope check* would have manufactured the state it exists to prevent (`v0.7 §6.2`).

        Called twice where `_secure` takes twice, for `v0.7 §6.2`'s reason: the `reconcile` hook
        between them is a network call whose duration would otherwise sit inside the window.
        Idempotent from the caller's side, and `seen` keeps the hash the first call computed so
        the receipt records one answer rather than the last.
        """
        if scope is None:
            return
        # SPEC-v0.9 §5.2.2's observe row: the provider **runs**, so its hash reaches the receipt
        # and an operator sizing a scope before turning it on sees what would have happened, and
        # it refuses nothing. `v0.3 §6.2` is the rule: observe mode records rather than enforces,
        # and a check that enforced under observation would refuse during the phase whose whole
        # purpose is to refuse nothing.
        refuse = self._refuse_scope if enforcing else self._would_refuse_scope
        try:
            answered = scope(action)
            if not isinstance(answered, Mapping):
                raise TypeError(
                    f"a scope provider returns a mapping, not {type(answered).__name__}"
                )
            digest = _scope_hash(answered)
        except Exception as exc:
            # **Every failure the provider can produce, not two of them** (§5.6): it raised, it
            # answered with the wrong shape, or it answered something the canonicalizer refuses.
            # All three are "the scope could not be read", which is fail-closed, and none of them
            # is `out_of_scope`, which is a statement that it *was* read.
            raise refuse(action, SCOPE_UNAVAILABLE, str(exc)) from exc
        if seen is not None:
            seen.append(digest)
        _SCOPE_HASH.set(digest)
        patterns = answered.get(_SCOPE_RESOURCES)
        if not isinstance(patterns, (list, tuple)) or not all(
            isinstance(one, str) for one in patterns
        ):
            raise refuse(
                action,
                SCOPE_UNAVAILABLE,
                f"the scope carries no usable {_SCOPE_RESOURCES!r} list",
            )
        # §4.4's rule for a grant that declares `resources:`, applied here for the same reason:
        # an action carrying no resource does not match a scope that names them, and treating it
        # as in-scope would make the check optional for any caller who omitted the field.
        if action.resource is None or not any(
            matches(pattern, action.resource, separator=RESOURCE_SEPARATOR) for pattern in patterns
        ):
            raise refuse(action, OUT_OF_SCOPE, f"resource {action.resource!r} is not in this scope")

    def _charges_for(
        self,
        action: Action,
        effect_key: str | None,
        observation: _Observation | None = None,
        announce: bool = True,
    ) -> tuple[Charge, ...]:
        """What this action spends, one `Charge` per ancestor (SPEC-v0.9 §2.7).

        **And §2.4.1's refusal, here, because this is where the effect key is finally known.**
        A budgeted grant that reaches an action whose key resolved to `None` is refused: without
        it an agent proposes actions carrying no `effect:` template and spends nothing against
        every budget on the chain, for ever, which is the feature's own sharp case answered by
        declining to play. §2.4.1 records the two probes that moved this out of the loader: a
        loader cannot see a decorator-supplied `effect=`, and cannot run at all on the
        standalone-authority path.
        """
        if self._authority is None:
            return ()
        result = _AUTHORITY_RESULT.get(None)
        if result is None:
            return ()
        try:
            charges = self._authority._charges_for(action, result, store=self._store)
        except InvalidArgument as unmeasurable:
            # §2.3. The kernel cannot measure what this action spends, so it cannot hold the
            # grant to its budget, so it declines to run it. Recorded before it is re-raised.
            raise self._refuse_unmeasurable(
                action, BUDGET_UNMEASURABLE, unmeasurable, observation, announce
            ) from None
        if charges and effect_key is None:
            raise self._refuse_unmeasurable(
                action,
                BUDGET_UNKEYED,
                InvalidArgument(
                    f"{action.name}: grant {charges[0].grant_id!r} carries a budget and this "
                    "action resolved no effect key, so nothing could be charged against it. "
                    "Declare an `effect:` template for the action, or take the budget off the "
                    "grant (SPEC-v0.9 §2.4.1)"
                ),
                observation,
                announce,
            ) from None
        return charges

    def _observe_charges(
        self,
        action: Action,
        effect_key: str | None,
        observation: _Observation,
        announce: bool = True,
    ) -> tuple[Charge, ...]:
        """§4.2.1's first half: what this action *would have* been charged, charging nothing.

        §2.3's and §2.4.1's refusals are **reported** here rather than raised: enforce mode
        refuses those actions, so saying so is exactly what observe mode is for. They get their
        own reasons rather than `budget_exhausted`, because an operator whose pilot says "this
        would have been refused" needs to know whether the budget is too small or the action
        cannot be measured at all.

        Called above the approval gate, where `_secure` computes the same thing, so the two modes
        agree about which refusal comes first (T451).
        """
        # **Cleared first, on every path.** Returning early without touching it left the
        # previous action's charges on this action's receipt, which is the defect above.
        _BUDGET_CHARGES.set(())
        try:
            charges = self._charges_for(action, effect_key, observation, announce)
        except InvalidArgument:
            # Already reported by `_refuse_unmeasurable`, which blocked rather than denying.
            return ()
        if not charges:
            return ()
        # §4.2.1a — **the counterfactual spend, on the receipt.** The ledger is empty under
        # observation, so if the receipt does not carry what this action would have been charged,
        # nothing anywhere records it and a budget cannot be sized from an observed run. It
        # asserts no spend: the receipt says `observed`, and `v0.3 §6.2` makes every number on an
        # observed receipt a counterfactual. T439d.
        _BUDGET_CHARGES.set(
            tuple(
                {"grant_id": charge.grant_id, "metric": charge.metric, "amount": charge.amount}
                for charge in charges
            )
        )
        return charges

    def _observe_spend(
        self,
        action: Action,
        charges: tuple[Charge, ...],
        observation: _Observation,
        effect_key: str | None = None,
    ) -> None:
        """§4.2.1's second half: whether the budget would have refused, writing nothing.

        The predicate is `check_charges`, the same function all three stores decide with, so the
        report and the enforcement cannot drift **on the arithmetic** (§4.2.1b states what is not
        promised about *which* refusal is named): a pilot that said "this would have been fine"
        about an action enforce mode refuses is worse than no pilot.

        **The sum is a lock-free read** off the public `consumptions()` rather than a store's
        private `_spent`, because this runs outside any reservation and must take no lock and
        write nothing. It is therefore stale under concurrency, which is correct for a
        counterfactual and would not be for a decision.

        **Skipped once something else has blocked.** Enforce mode raises at the first refusal and
        never reaches the budget; observe mode runs every check, so without this it wrote a
        `budget_exhausted` event for an action enforce mode refuses out of scope, and an operator
        reading the log saw a refusal that would never have happened. An earlier version of this
        docstring claimed the clauses above had already returned by then, which was not true of
        the scope block or the approval gate.

        `check_charges` can also raise `InvalidArgument` for two charges on one grant and metric
        carrying different amounts (§3.3.1). Nothing reachable produces that shape -- §2.7's
        ancestors are distinct grants, and one grant's two budgets on one metric always agree --
        and observe mode is not the place to raise about it if something ever does.
        """
        if not charges or observation.blocked_reason is not None:
            return
        now = self._clock()

        def spent(charge: Charge) -> int:
            return sum(
                row.amount
                for row in self._store.consumptions(
                    grant_id=charge.grant_id,
                    metric=charge.metric,
                    since=now - charge.window,
                )
                if row.released_at is None
            )

        try:
            check_charges(charges, spent)
        except BudgetExhaustedError as exhausted:
            observation.block(BUDGET_EXHAUSTED, DecisionPoint.BUDGET)
            self._append(
                EventType.ACTION_DENIED,
                action,
                {
                    "reason": BUDGET_EXHAUSTED,
                    "grant_id": exhausted.grant_id,
                    "metric": exhausted.metric,
                    "window": int(exhausted.window.total_seconds()),
                    "observed": True,
                },
                # Splitting this method dropped the key, so the one event that names which
                # effect the budget refused stopped naming it. Nothing noticed.
                effect_key,
            )
        except InvalidArgument:
            return

    def _resumed_charges(self, effect_key: str | None, attempt: int) -> None:
        """Stamp the resumed leg's receipt with what its **first** leg charged (§10.1, §8.3).

        One row per grant and metric at this attempt, in the ledger's insertion order, which
        §3.3.3 makes identical across the three backends. A released row still counts: it says
        what this action spent, and `released_at` is a later fact about the same spend.
        """
        if effect_key is None:
            _BUDGET_CHARGES.set(())
            return
        _BUDGET_CHARGES.set(
            tuple(
                {"grant_id": row.grant_id, "metric": row.metric, "amount": row.amount}
                for row in self._store.consumptions(effect_key=effect_key)
                if row.attempt == attempt
            )
        )

    def _refuse_unmeasurable(
        self,
        action: Action,
        reason: str,
        error: InvalidArgument,
        observation: _Observation | None = None,
        announce: bool = True,
    ) -> InvalidArgument:
        """§2.3 and §2.4.1's refusals, with the events and the receipt they were missing.

        Returns the error for the caller to `raise`, like `_refuse_scope`, so a reader can see
        the control flow leaves at the call site. The message is the one the guard already wrote:
        it names the grant, the metric and the offending value, and an operator reading the
        receipt needs exactly that.
        """
        if observation is not None:
            # `v0.3 §6.2`: observe mode records what enforce mode would have done and refuses
            # nothing. Writing the `denied` receipt below would put a refusal it did not make in
            # the store, alongside the `observed` receipt for the run that went ahead: two
            # receipts for one action, disagreeing. T439c.
            observation.block(reason, DecisionPoint.BUDGET)
            if announce:
                # **Not on a resumed leg**: its first leg already wrote this event for this
                # action, and a second one made the evidence say the action was denied twice
                # while the receipt beside it said `ALLOW`. An independent review found the two
                # disagreeing, which is what `acs.py`'s clause forbids one boundary lower.
                self._append(
                    EventType.ACTION_DENIED,
                    action,
                    {"reason": reason, "error": str(error), "observed": True},
                )
            return _UnmeasurableError(str(error), reason=reason)
        self._append(EventType.ACTION_DENIED, action, {"reason": reason, "error": str(error)})
        self._record(
            action,
            Evaluation(Decision.DENY, reason),
            ReceiptResult.DENIED,
            self._clock(),
            error=str(error),
        )
        return _UnmeasurableError(str(error), reason=reason)

    def _upstream_reason(self, action: Action) -> str | None:
        """§4.3's check 2: is this action pinned to an upstream this process has verified?

        `None` where the entry pins nothing, which is every action written before v0.10.

        **In-process there is no upstream to observe, so a pinned action is refused**
        `upstream_unverified` on every call (§4.4). That is loud, correct, and exactly what the
        pin says the operator asked for: a pin is a claim about a server ctrlrun connects to, and
        in-process the executor is the operator's own code holding its own connection.
        """
        from . import upstream as _upstream

        pin = self._policy.upstream_pin(action.name)
        if not pin:
            return None
        if self._upstream is None:
            return UPSTREAM_UNVERIFIED
        return _upstream.check(pin, self._upstream, self._policy.tool_name(action.name))

    def _refuse_upstream(self, action: Action, reason: str, effect_key: str | None) -> ActionDenied:
        """The refusal §4.5 names, recorded the way every other `ActionDenied` is."""
        self._append(EventType.ACTION_DENIED, action, {"reason": reason}, effect_key)
        detail = (
            "this process has verified no upstream for it"
            if reason == UPSTREAM_UNVERIFIED
            else "what this process observed is in no pinned list"
        )
        return ActionDenied(
            f"{action.name}: the policy pins the upstream it authorises, and {detail} "
            "(SPEC-v0.10 §4.3)",
            reason=reason,
            action_id=action.action_id,
        )

    def _refuse_budget(self, action: Action, exhausted: BudgetExhaustedError) -> ActionDenied:
        """SPEC-v0.9 §4.5. Names the grant, the metric and the window; **never the balance**.

        A refusal that reported how much was left would be an oracle: refused actions cost
        nothing, so an attacker binary-searches the exact limit in a few dozen refusals and then
        knows precisely how much authority to use without tripping it. An operator debugging at
        3am gets the number from `inspect`, which needs the store rather than the ability to be
        refused.

        The grant named is **the one that refused**, which under §2.7 may be an ancestor rather
        than the grant that decided: an operator whose child grant is well within its own budget
        needs to be told the parent is not.
        """
        error = (
            f"budget {exhausted.metric!r} on grant {exhausted.grant_id!r} over "
            f"{exhausted.window} is exhausted"
        )
        self._append(
            EventType.ACTION_DENIED,
            action,
            {
                "reason": BUDGET_EXHAUSTED,
                "grant_id": exhausted.grant_id,
                "metric": exhausted.metric,
                "window": int(exhausted.window.total_seconds()),
            },
        )
        self._record(
            action,
            Evaluation(Decision.DENY, BUDGET_EXHAUSTED),
            ReceiptResult.DENIED,
            self._clock(),
            error=error,
        )
        return ActionDenied(f"{action.name} denied: {error}", reason=BUDGET_EXHAUSTED)

    def _refuse_scope(self, action: Action, reason: str, error: str) -> _ScopeRefusedError:
        """The refusal, with its events and its receipt. Returns it for the caller to raise.

        Returned rather than raised so the call site reads `raise self._refuse_scope(...)` and a
        reader can see the control flow leaves there: an exception raised inside a helper is a
        `return` a linter cannot see.
        """
        self._append(EventType.ACTION_DENIED, action, {"reason": reason, "error": error})
        self._record(
            action,
            Evaluation(Decision.DENY, reason),
            ReceiptResult.DENIED,
            self._clock(),
            error=error,
        )
        return _ScopeRefusedError(ActionDenied(f"{action.name} denied: {reason}", reason=reason))

    def _would_refuse_scope(self, action: Action, reason: str, error: str) -> _ObservedRefusalError:
        """Observe mode's counterpart: record what would have happened, and refuse nothing.

        Returns a sentinel the caller raises, which `_observe_secure` catches. A `None` return
        would make `_in_scope`'s `raise` a type error and a separate code path in `_in_scope`
        would be the flag through it that `_observe_secure`'s own docstring argues against.
        """
        self._append(
            EventType.ACTION_DENIED,
            action,
            {"reason": reason, "error": error, "observed": True},
        )
        return _ObservedRefusalError(reason)

    def _recheck(
        self,
        action: Action,
        approval_id: str,
        preconditions: _Preconditions | None,
        compared: _Compared,
    ) -> None:
        """SPEC-v0.7 §6.2: the precondition, compared **strictly before** the store call that
        consumes the approval. Raises the refusal; returns where the store call may proceed.

        `Control` reads the record it is about to present (`get_approval`, an existing read)
        and decides with `check_consumable`, the pure function every store applies. Where no
        provider is named and the record carries no fingerprint, the precondition question does
        not arise and the store call is 0.6.1's exactly.

        **Where the verdict is a refusal, the provider is not called** (§6.6), and the refusal
        is raised from this read, with the reason 0.6.1 gives, and **nothing is written to the
        store**. Not from the store call: a `pending` record a human grants between this read
        and that call would then be consumed with no recheck, which is a skip. And nothing
        written, because the only write this read could ask for is the lapse of an expired
        grant, and whose clock decides that is `v0.1 §4.2 A3`'s question: the answer stays the
        store's. A `Control` whose clock ran ahead of its store's used to send the grant to
        `consume_approval`, the store consumed it by its own clock, and the row then said
        `consumed` while the events said expired and the receipt said blocked. Safe, and untrue.
        The row keeps what the store gave it, `APPROVAL_EXPIRED` records the lapse this clock
        saw, and `check_consumable` refuses the grant at every later presentation anyway.

        What the record carried is recorded either way (§6.11): a refusal that would have
        happened whatever the world did still says which fingerprint the approval was
        requested with.

        This narrows the window between the human's decision and the reservation to the time
        between this fetch and that store call, and does not close it (§6.7).
        """
        compared.reset()
        record = self._store.get_approval(approval_id)
        stored = None if record is None else record.request.precondition_fingerprint
        compared.at_request = stored
        # **One verdict, from one clock read**, reused by the approver gate below and by the
        # precondition path's raise (SPEC-v0.8 §2.4.1). Two reads a tick apart could produce a
        # gate that says "not lapsed" followed by a raise that says `expired`, which is the
        # divergence `v0.7 §12.5` reversed.
        verdict = check_consumable(record, approval_id, action.action_hash, self._clock())
        # SPEC-v0.8 §2.4: **the early return is gone.** It returned here whenever no provider
        # was named and the record carried no fingerprint, which is every deployment that does
        # not use `v0.7 §6`, and an approver check added after it would have been dead on that
        # path, green, and invisible to a mutation table.
        self._check_approver(action, approval_id, record, compared, verdict)
        if preconditions is None and stored is None:
            return
        if verdict.refusal is not None:
            raise verdict.refusal
        assert record is not None  # a verdict with no refusal carries its record
        self._compare(action, record, preconditions, compared)

    def _check_approver(
        self,
        action: Action,
        approval_id: str,
        record: ApprovalRecord | None,
        compared: _Compared,
        verdict: ApprovalVerdict,
    ) -> None:
        """Who answered, and whether they may have (SPEC-v0.8 §2.7, §4.1).

        **Gated on a record that is `granted` and that this clock does not consider lapsed**
        (§2.4.1). Everything else is left to the store, unchanged, and the reason is four rows
        long: a denied approval carries no verified approver, so an ungated check would refuse
        it `approver_unverified` and a human's no would stop appearing in the evidence as a no;
        a consumed one is `G2`'s replayed approval and a moved hash is `G1`, both of which would
        lose their reason; and a lapsed grant would lose `APPROVAL_EXPIRED` **and the store's
        own lapse write**, because that write happens inside `_take` and a refusal raised here
        never reaches it.

        The gate is the `check_consumable` verdict `_recheck` computed, the pure function
        `v0.1 §4.2` froze: no second implementation of a frozen rule, no second clock read, and
        the store still decides expiry and may disagree.

        **The lapsed row is checked and not skipped**, which is the difference between this and
        the version an independent review broke twice: first by skipping it, which was fail-open
        under clock skew, and then by deferring it past `_take`, which closed that and left a
        consumed grant and a reservation nothing releases. The comment below carries the cost.
        """
        if record is not None:
            # Recorded whatever this deployment checks, so a receipt says who answered even
            # where no approver identity is configured and nothing was refused.
            compared.approvers = record.approvers
        if self._approver_identity is None:
            return
        if verdict.record is None and not verdict.expire:
            # Denied, consumed, hash-moved, pending, unknown: the store's reason wins and this
            # check stands aside, because an ungated refusal would report an approver problem
            # for a human's no, for `G1`'s moved hash and for `G2`'s replayed approval.
            return
        # **`verdict.expire` is the lapsed row, and it is checked rather than skipped.** It means
        # granted, hash matching, and past its expiry by *this* clock, which is the one case where
        # `check_consumable` refuses a record the approver checks can still read. Skipping it was
        # fail-open: the store keeps its own clock, so where this host ran ahead the checks stood
        # aside and `consume_approval_and_reserve` then consumed the grant happily, and a
        # self-approval committed under a twenty-minute skew.
        #
        # What it costs to check it here instead: a grant that is **both** lapsed and refused on
        # approver grounds reports the approver reason rather than `expired`, so that row keeps no
        # `APPROVAL_EXPIRED` event and no lapse write. The grant is unusable either way,
        # `check_consumable` refuses it at every later presentation, and a lapsed grant whose
        # approver is fine still reports `expired` with its event and the store's own write,
        # because the check passes and `_take` decides. §2.4.1 carries the table.
        assert record is not None
        self._refuse_approver(
            action, approval_id, record.approvers, compared, record.request.required_roles
        )

    def _refuse_approver(
        self,
        action: Action,
        approval_id: str,
        approvers: tuple[VerifiedApprover, ...],
        compared: _Compared,
        required: tuple[RequiredRole, ...] = (),
    ) -> None:
        """§2.7, §3.6 and §4.1's refusals, over whatever the row recorded."""
        if not approvers:
            raise ApprovalMismatch(
                f"approval {approval_id} carries no verified approver, and this deployment "
                "names an approver identity; the approval is left granted",
                reason=APPROVER_UNVERIFIED,
                approval_id=approval_id,
            )
        for approver in approvers:
            # SPEC-v0.8 §3.6: **each** approver satisfies **every** required role. A control that
            # says who may answer is not satisfied by a committee in which one member could, which
            # is why this is inside the loop and `unsatisfied` is all-of rather than any-of.
            missing = unsatisfied(required, approver.entitled)
            if missing is not None:
                compared.unentitled = missing
                raise ApprovalMismatch(
                    f"approval {approval_id} was granted by an approver who does not hold the "
                    f"role {missing.role!r} required by control {missing.control!r}; the approval "
                    "is left granted",
                    reason=APPROVER_UNENTITLED,
                    approval_id=approval_id,
                )
        requester = (action.principal.agent, action.principal.user)
        for approver in approvers:
            if approver.principal == requester:
                # §4.1: on the resolved principal and never on the string, which is why two
                # grants whose `approver` strings differ are still one principal here.
                raise ApprovalMismatch(
                    f"approval {approval_id} was granted by {approver.agent!r}, which is the "
                    "principal that requested the action; the approval is left granted",
                    reason=APPROVER_IS_REQUESTER,
                    approval_id=approval_id,
                )

    def _compare(
        self,
        action: Action,
        record: ApprovalRecord,
        preconditions: _Preconditions | None,
        compared: _Compared,
    ) -> None:
        """The comparison itself, for `_recheck` and for observe mode (SPEC-v0.7 §6.2, §6.8).

        Three refusals, each its own reason, and `compared` says what was compared: both
        fingerprints where both exist, the one that exists where only one does, and the
        provider's failure by type. **Never a skip**: a fingerprint on only one side is
        `precondition_missing`, because "skip" would mean a store that drops the column, or a
        path that names no provider, turns the check off (§6.4).
        """
        stored = record.request.precondition_fingerprint
        compared.at_request = stored
        if preconditions is None and stored is None:
            return
        approval_id = record.approval_id
        if preconditions is not None:
            fresh, error = _fetched(preconditions, action)
            if fresh is None:
                compared.error = error
                _LOG.warning(
                    "%s: the precondition provider produced no fingerprint (%s); approval %s is "
                    "refused and left granted (SPEC-v0.7 §6.5)",
                    action.name,
                    error,
                    approval_id,
                )
                raise ApprovalMismatch(
                    f"approval {approval_id}: the precondition provider produced no "
                    f"fingerprint ({error}); nothing was reserved",
                    reason=_PRECONDITION_UNAVAILABLE,
                    approval_id=approval_id,
                )
            compared.at_recheck = fresh
        if stored is None or compared.at_recheck is None:
            raise ApprovalMismatch(
                f"approval {approval_id} has a precondition fingerprint on one side only "
                f"(requested with {stored}, presented with {compared.at_recheck}); a fingerprint "
                "on one side is a refusal and never a skip",
                reason=_PRECONDITION_MISSING,
                approval_id=approval_id,
            )
        if stored != compared.at_recheck:
            raise ApprovalMismatch(
                f"approval {approval_id} was granted against precondition {stored} and the "
                f"provider now reports {compared.at_recheck}; the approval is left granted",
                reason=_PRECONDITION_CHANGED,
                approval_id=approval_id,
            )

    @staticmethod
    def _invalidated(
        action: Action, mismatch: ApprovalMismatch, compared: _Compared
    ) -> dict[str, Any]:
        """`APPROVAL_INVALIDATED`'s data: the reason, and for a precondition refusal the two
        fingerprints it compared, hashes only, and the provider's failure by type (§6.2)."""
        data: dict[str, Any] = {"reason": mismatch.reason, "action_hash": action.action_hash}
        # SPEC-v0.8 §3.7: an entitlement refusal names the control and the role on the event as
        # well as in the message, because an operator reading the evidence should not have to
        # parse a sentence to find out which written expectation was not met. By the carrier the
        # precondition hashes already travel on, so no error type grows a keyword (§11.2).
        if mismatch.reason == APPROVER_UNENTITLED and compared.unentitled is not None:
            data["control"] = compared.unentitled.control
            data["role"] = compared.unentitled.role
        if mismatch.reason in _PRECONDITION_REASONS:
            data.update(compared.data())
        return data

    def _take(
        self,
        action: Action,
        approval_id: str | None,
        effect_key: str | None,
        lease: timedelta,
        charges: tuple[Charge, ...] = (),
    ) -> tuple[Approval | None, Reservation | None]:
        """Consume the approval, reserve the effect, or both at once (SPEC-v0.1 §4.2 A4).

        SPEC-v0.9 §3.3: the charges ride the reservation's own transaction, which is the whole of
        why `StateStore` was amended. The branch with no effect key passes none, because there is
        no reservation to ride and §2.4.1 has already refused a budgeted grant that reaches it.
        """
        if approval_id is not None and effect_key is not None:
            return self._store.consume_approval_and_reserve(
                approval_id, action.action_hash, effect_key, action.action_id, lease, charges
            )
        if approval_id is not None:
            return self._store.consume_approval(approval_id, action.action_hash), None
        if effect_key is not None:
            return None, self._store.reserve_effect(effect_key, action.action_id, lease, charges)
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

    def _propose_policy(
        self,
        candidate: Policy,
        *,
        authority: Authority | None = None,
        approval_id: str | None = None,
    ) -> Receipt:
        """Propose a policy change as an ordinary action (SPEC-v0.8 §8.2, §8.3).

        Ordinary action hash, ordinary effect key, ordinary events, ordinary receipt -- which
        is why §8 adds no event type: `v0.1 §6.2`'s vocabulary already describes a proposal, an
        approval request, a grant, a consumption and a commit, which is the whole life of a
        policy change. So §2, §3 and §4 apply without a second path: an unverifiable approver
        is refused, an unentitled one is refused, a proposer approving their own change is
        refused, and M-of-N counts.

        **`to` is computed the way the `Control` that will enforce it computes its own hash**,
        with that authority and that environment substituted (§8.2). `hash_with_authority` folds
        both in, so the same file in `staging` and in `prod` hashes differently and an approval
        is per deployment. That is what an operator wants, it is not obvious, and T353 pins it.

        Private, like `_delegate` and `_break_glass`: §11.1 adds the CLI group and no public
        `Control` method.
        """
        if self._observing:
            # **SPEC-v0.8 §8.4, and an independent review found this open.** Observe mode
            # requests no approval and still reserves and commits, so a proposal made while
            # observing minted a real marker with nobody having answered anything -- and
            # observe-then-enforce is the documented adoption path, so every hash proposed
            # during the observe phase was silently pre-approved for the enforce phase.
            #
            # Refused rather than silently skipped: an operator who ran the command deserves
            # to know it did nothing, and a proposal that looked like it worked and left no
            # approval is worse than one that did not run.
            raise InvalidArgument(
                "a policy change cannot be proposed under 'mode: observe'. Observe mode "
                "enforces nothing and asks nobody, so the approval it recorded would mark the "
                "hash approved with no human having answered, and the enforce-mode deployment "
                "that follows would find it already approved (SPEC-v0.8 §8.4)"
            )
        target = hash_with_authority(candidate, authority, self._environment)
        action = Action(
            name=POLICY_CHANGE_ACTION,
            arguments={"from": self._policy_hash, "to": target},
            principal=self._principal_for_proposal(),
            resource="policy",
            environment=self._environment,
        )

        def _installed() -> dict[str, str]:
            # The "execution" of a policy change is the fact that it was approved: nothing is
            # written to disk here, because installing the file is the operator's act and this
            # kernel does not edit an operator's policy. What the committed effect records is
            # that this hash was approved, which is exactly what §8.4 reads back.
            return {"approved": target}

        with _policy_change_in_flight():
            if approval_id is None:
                return self.execute(action, _installed, f"policy:{target}")
            with with_approval(approval_id):
                return self.execute(action, _installed, f"policy:{target}")

    def _principal_for_proposal(self) -> Principal:
        """Who is proposing, resolved as any other principal is (§8.2)."""
        found = _CONTEXT.get(None)
        if found is not None:
            return found.principal
        resolved = (
            None
            if self._identity is None
            else self._identity.resolve(
                IdentityContext(action=POLICY_CHANGE_ACTION, environment=self._environment)
            )
        )
        if resolved is None:
            raise IdentityError(
                "a policy change has a proposer, and nothing resolved one. Run inside "
                "ctrlrun.context(agent=..., user=...), or configure an identity provider "
                "(SPEC-v0.8 §8.2)"
            )
        return resolved

    def _replay_policy(self, candidate: Policy, *, limit: int) -> list[dict[str, Any]]:
        """What the last `limit` receipts would decide under `candidate` (SPEC-v0.8 §8.5).

        **Writes nothing, executes nothing, reserves nothing**, and reports *what changes*:
        never safer, riskier, too permissive, a score or a grade. `v0.4 §3.9`'s rule for
        `verify` applied here, because a replay that scored an operator's document would be the
        same claim in a new costume.

        A receipt whose action cannot be rebuilt is **named and skipped**, never counted as
        unchanged, on the distinction `v0.6 §3.2` draws for an unknown schema version.
        """
        if limit < 1:
            # §8.5 and the project's rule: refuse the unparseable rather than answer it.
            # `--last 0` printed "no recorded decision changes", which reads as "this policy
            # changes nothing" for an input that read nothing.
            raise InvalidArgument(
                f"--last must be at least 1, got {limit}. A replay over no receipts reports no "
                "change, which reads as a verdict about the policy rather than about the input"
            )
        rows: list[dict[str, Any]] = []
        receipts = list(self._store.receipts())[-limit:]
        for receipt in receipts:
            if isinstance(receipt, UnreadableReceipt):
                # SPEC-v0.11 §5.2: a row this binary cannot read back is **skipped and named**,
                # in the shape this loop already uses for a receipt it cannot rebuild an action
                # from. It is not dropped: a replay that silently left out the one row somebody
                # tampered with would report "no decision changes" about a store it could not
                # read, which is `SPEC-v0.4 §3.8`'s false green.
                rows.append(
                    {
                        "receipt_id": receipt.receipt_id,
                        "action": None,
                        "skipped": (
                            f"this row could not be read back as a receipt ({receipt.refusal})"
                        ),
                    }
                )
                continue
            rebuilt = _action_from_receipt(receipt, self._environment)
            if rebuilt is None:
                rows.append(
                    {
                        "receipt_id": receipt.receipt_id,
                        "action": receipt.action,
                        "skipped": (
                            f"schema {receipt.schema!r} is not one this binary reads"
                            if receipt.schema not in KNOWN_RECEIPT_SCHEMAS
                            else "this receipt does not carry what an action is rebuilt from"
                        ),
                    }
                )
                continue
            before = self._policy.evaluate(rebuilt)
            after = candidate.evaluate(rebuilt)
            if before.decision is after.decision and before.reason == after.reason:
                continue
            rows.append(
                {
                    "receipt_id": receipt.receipt_id,
                    "action": receipt.action,
                    "from": {"decision": str(before.decision), "reason": before.reason},
                    "to": {"decision": str(after.decision), "reason": after.reason},
                }
            )
        return rows

    def _break_glass(self, envelope_id: str, grant: Grant, *, reason: str = "") -> Delegation:
        """Open a break-glass grant beneath a declared envelope (SPEC-v0.8 §5.3).

        There is no flag. What this creates is an ordinary delegation: recorded, bounded by the
        envelope on every dimension `contained_dimension` knows, expiring, revocable and
        attenuable, and named on the receipt of every action taken under it. A setting that
        skipped a check would have none of those five properties, which is the whole argument
        of §5.1.

        **The opener is a verified principal and never an assertion, and there is no parameter
        that says otherwise.** An earlier build took `by: Principal | None`, which was an
        unauthenticated way to assert an opener *and the roles it holds*: passing a principal
        whose claims carried the envelope's role opened it in a deployment whose provider
        resolved somebody else entirely. `§11.2` keeps `_granting_principal` package-internal
        for exactly that reason, and a public `by=` was the same hole with a docstring. The
        opener is whoever the `ApproverIdentity` resolves, and a deployment that names none
        cannot open one at all.

        **Private, like `_delegate`.** `§11.2` adds no public `Control` method in v0.8; the
        surface item 5 adds is the CLI command, which calls this the way `ctrlrun delegate`
        calls `_delegate`.

        `reason` is free text on the `DELEGATION_CREATED` event. The kernel does not interpret
        it, exactly as it does not interpret `source:`.
        """
        authority = self._require_authority("break-glass")
        envelope = authority.envelopes.get(envelope_id)
        opener = self._opener_for(envelope_id, envelope)
        now = self._clock()
        try:
            planned = authority.plan_break_glass(
                envelope_id, grant, by=opener, store=self._store, now=now
            )
        except IdentityError:
            self._append_delegation(
                EventType.DELEGATION_REJECTED,
                {"reason": PRINCIPAL_EXPIRED, "parent_id": envelope_id},
            )
            raise
        except AuthorityEscalation as escalation:
            data: dict[str, Any] = {"reason": escalation.reason, "parent_id": envelope_id}
            if escalation.dimension is not None:
                data["dimension"] = escalation.dimension
            self._append_delegation(EventType.DELEGATION_REJECTED, data)
            raise
        self._store.put_delegation(planned.to_record())
        self._append_delegation(
            EventType.DELEGATION_CREATED,
            {
                "delegation_id": planned.delegation_id,
                "parent_id": planned.parent_id,
                "depth": planned.depth,
                "created_by_agent": opener.agent,
                "created_by_user": opener.user,
                "created_via": "break-glass",
                "reason": reason,
            },
        )
        _LOG.warning(
            "break-glass %s opened beneath %s by %s until %s: %s",
            planned.delegation_id,
            envelope_id,
            opener.agent,
            planned.grant.expires_at,
            reason or "no reason given",
        )
        return planned

    def _opener_for(self, envelope_id: str, envelope: BreakGlassEnvelope | None) -> Principal:
        """Who is opening this envelope, and may they? (SPEC-v0.8 §5.3.1.)

        Rule 4 does not apply to an envelope: its subject names the agents a break-glass grant
        may be **for**, and the opener is a human. What gates the opener is the envelope's
        `controls:`, whose `approver_role` this checks against the roles the opener holds.

        An unknown envelope is left to `plan_break_glass` to refuse, so `--envelope` naming an
        ordinary grant gets §5.3.1's message rather than one about a missing configuration.
        """
        identity = self._approver_identity
        if identity is None:
            raise InvalidArgument(
                "opening a break-glass envelope needs an approver identity: the envelope names "
                "the controls that gate who may open it, and with nobody resolved there is no "
                "principal to check them against. Build the Control with "
                "approver_identity=ApproverIdentity(provider, roles_claim=...) "
                "(SPEC-v0.8 §5.3.1). Note that `Control.from_file`, which is what the CLI "
                "builds, wires none: see SPEC-v0.8 §14.5, which records that as open"
            )
        opener = identity.resolve(
            IdentityContext(action="ctrlrun.break-glass", environment=self.environment)
        )
        if opener is None:
            raise IdentityError(
                "the approver identity resolved nobody, so this break-glass envelope has no "
                "opener to check against its controls (SPEC-v0.8 §5.3.1)"
            )
        if envelope is None:
            return opener
        # **Every cited control must resolve and must name a role.** Elsewhere a control that
        # names no `approver_role` gates nobody (§3.5), and that is right where the citation is
        # on an *action*: the control is documentation and the approval decides. Here the
        # citation **is** the gate, so the same rule reads the opposite way -- a typo in an
        # envelope's `controls:` silently admitted any verified principal, which an independent
        # review demonstrated with one transposed letter. `Authority.from_yaml` parses the
        # section without a registry to check against, so it is checked here, where both are.
        unresolved = [
            identifier
            for identifier in envelope.controls
            if (control := self._policy.controls.get(identifier)) is None
            or not control.approver_role
        ]
        if unresolved:
            raise InvalidArgument(
                f"break-glass envelope {envelope_id!r} cites {unresolved}, which "
                + (
                    "name no control in this policy's registry"
                    if any(self._policy.controls.get(name) is None for name in unresolved)
                    else "declare no 'approver_role'"
                )
                + ". An envelope's controls are what gate who may open it, so a citation that "
                "resolves to nothing would gate nobody (SPEC-v0.8 §5.3.1)"
            )
        required = tuple(
            RequiredRole(control=identifier, role=control.approver_role)
            for identifier, control in (
                (identifier, self._policy.controls.get(identifier))
                for identifier in envelope.controls
            )
            if control is not None and control.approver_role
        )
        held = roles_held(opener, identity.roles_claim)
        missing = unsatisfied(required, entitled_controls(required, held))
        if missing is not None:
            self._append_delegation(
                EventType.DELEGATION_REJECTED,
                {
                    "reason": APPROVER_UNENTITLED,
                    "parent_id": envelope_id,
                    "control": missing.control,
                    "role": missing.role,
                },
            )
            raise AuthorityEscalation(
                f"{opener.agent!r} does not hold the role {missing.role!r} required by control "
                f"{missing.control!r}, which gates who may open {envelope_id!r} "
                "(SPEC-v0.8 §5.3.1)",
                reason=APPROVER_UNENTITLED,
                parent_id=envelope_id,
            )
        return opener

    def hop(
        self,
        parent_id: str,
        grant: Grant,
        *,
        by: Principal,
        action_id: str | None = None,
    ) -> Delegation:
        """Hand part of this authority to another agent (SPEC-v0.10 §2.2).

        A hop **is** a delegation: same record, same `contained_dimension`, same chain walk, same
        `delegation_id`. What differs is one field, `created_via="hop"`, so an operator surface can
        answer "which of these crossed an agent boundary" and `ctrlrun scan` can say which
        principals hold authority no hop bounds (§6.4).

        **A separate method rather than `delegate(via=...)`**, because `created_via` is evidence
        about *which surface acted* and a caller that could write it could forge that evidence:
        API code passing `"cli"` would put a shell's fingerprint on a record no shell touched.
        A method whose name fixes the value cannot (§9).

        Every §5.3 check of `v0.3` applies unchanged, including rule 0's refusal of an expired
        credential: a hop is the most durable thing a principal can create across a boundary, so
        it is the last place a stale one should still work.

        **`action_id` links a relay's created hop to the action that created it** (§3.4.4).
        `DELEGATION_CREATED` is action-less by construction, which is true of `ctrlrun delegate`
        from a shell and false of a hop created mid-action; without the link a relay's created hop
        is related to its action by a timestamp alone.

        **Explicit, and deliberately not ambient.** No context variable holds the current action,
        and one read inside the executor would be `<unset>` on a worker thread, which is an
        ordinary shape for an agent fanning out. `transport.py` documents that hazard for its own
        register and is explicit that there it fails *safe*; here it would fail in the evidence
        direction, silently. A caller that knows its action id says so; one that does not gets an
        event carrying `None`, exactly as today, and §8 records the limit.
        """
        return self._delegate(parent_id, grant, by=by, via="hop", action_id=action_id)

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
        self,
        parent_id: str,
        grant: Grant,
        *,
        by: Principal,
        via: CreatedVia,
        action_id: str | None = None,
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
            # SPEC-v0.10 §3.4.4 — the action that created this hop, where the caller named one.
            # `None` keeps the event exactly as `v0.3 §7` has it, which is what `ctrlrun delegate`
            # from a shell produces and what every pre-v0.10 reader expects.
            action_id=action_id,
        )
        return delegation

    def _require_authority(self, what: str) -> Authority:
        if self._authority is None:
            raise InvalidArgument(
                f"this Control has no authority section, so there is nothing to {what}; "
                "load a document with an 'authority:' key (SPEC-v0.3 §4.1)"
            )
        return self._authority

    # --- clock skew (SPEC-v0.7 §3.6) ----------------------------------------------------

    def _report_clock_skew(
        self, action: Action | None = None, effect_key: str | None = None
    ) -> None:
        """Append `CLOCK_SKEW_DETECTED` for a new, exceeded measurement the store retained.

        **It observes and decides nothing.** The store sits below `Control` and has no sink, and
        `StateStore` is frozen, so a store with its own clock retains its latest measurement as
        the optional `clock_skew` attribute and this pulls it. A store without the attribute
        reports nothing, which is correct: only a store with a second clock has one to report.

        The value is used only if it is a `ctrlrun.state.ClockSkew`. Anything else, a read that
        raises, and an append the store refuses are logged once per store per kind and never
        raised: an observation must not be able to fail the action it observes, and a refusal it
        sits beside must reach the caller as the refusal it was. With no `action` the report is
        about the deployment and carries no `action_id`; beside an `AmbiguousEffect` it names the
        attempt whose refusal it accompanies. It does not say skew caused that refusal.

        A report is marked as made only once the store has accepted it, so one the store could
        not write is tried again by the next action, and sinks are handed only an event that was
        stored, with the store's `event_id` (`v0.2 §4.1`).
        """
        try:
            value = getattr(self._store, "clock_skew", None)
            if value is None:
                return
            if not isinstance(value, ClockSkew):
                self._warn_clock_skew(
                    _SKEW_NOT_A_MEASUREMENT,
                    f"it is a {type(value).__name__}, not a ctrlrun.state.ClockSkew",
                )
                return
            if not value.exceeded or value == self._skew_reported:
                return
            data = {
                "skew_us": value.skew // _MICROSECOND,
                "bound_us": value.bound // _MICROSECOND,
                "threshold_us": value.threshold // _MICROSECOND,
                "direction": "ahead" if value.skew > timedelta(0) else "behind",
                "trigger": value.trigger,
                "measured_at": iso_timestamp(value.measured_at),
            }
        except Exception as broke:
            self._warn_clock_skew(
                _SKEW_READ_RAISED, f"reading it raised {type(broke).__name__}: {broke}"
            )
            return
        try:
            stored = self._store.append_event(
                Event(
                    type=EventType.CLOCK_SKEW_DETECTED,
                    action_id=None if action is None else action.action_id,
                    ts=self._clock(),
                    data=data,
                    effect_key=effect_key,
                )
            )
        except Exception as broke:
            # `Exception`, and the width is `_spend_unneeded_approval`'s argument: there is
            # nothing to protect here. This is an observation; the action's own events, receipt
            # and refusal are written by the paths that follow and raise as they always did.
            self._warn_clock_skew(
                _SKEW_APPEND_FAILED,
                f"the store refused to append CLOCK_SKEW_DETECTED ({type(broke).__name__}: "
                f"{broke}), so the report is retried by the next action",
                ignored=False,
            )
            return
        self._skew_reported = value
        self._fan_out("on_event", stored, str(stored.type))

    def _warn_clock_skew(self, kind: str, detail: str, *, ignored: bool = True) -> None:
        with _SKEW_WARNED_LOCK:
            try:
                seen = _SKEW_WARNED.setdefault(self._store, set())
            except TypeError:
                seen = self._skew_warned
            if kind in seen:
                return
            seen.add(kind)
        _LOG.warning(
            "%s: %s: %s. Nothing about any action changes (SPEC-v0.7 §3.6)",
            type(self._store).__name__,
            (
                "its clock_skew attribute was ignored, so this store's clock skew is never reported"
                if ignored
                else "a clock skew report was not recorded"
            ),
            detail,
        )

    def _append_delegation(
        self, type_: EventType, data: Mapping[str, Any], *, action_id: str | None = None
    ) -> None:
        """Append one of §7's three delegation events and fan it out.

        `action_id` is `None` by default: these are about an authority record, created and
        revoked outside any action's life.

        **SPEC-v0.10 §3.4.4 amends that for one case.** A hop created *inside* a running action
        is not outside any action's life, and a relay's created hop is otherwise linked to the
        action that created it by nothing but a timestamp. `Control.hop(action_id=...)` supplies
        it; every other caller, and every hop created from a shell, keeps `None`.

        `Control` appends them and calls every sink, for `v0.2 §4.1`'s
        reason — the highest-privilege operations in the release must not be the only ones
        missing from the export path.
        """
        stored = self._store.append_event(
            Event(type=type_, action_id=action_id, ts=self._clock(), data=data)
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
        compared: _Compared | None = None,
    ) -> Receipt:
        # SPEC-v0.3 §6.3 — one place turns a terminal outcome into an observed receipt, so
        # `result`, `execution` and `would_have` cannot disagree about the same action. The
        # still-refuses rows of §6.2 pass no observation and keep their `denied` receipt with
        # both new fields null, which is what makes "would_have present on every observed run
        # and absent on every refused one" true in both directions.
        receipt = Receipt(
            # SPEC-v0.8 §5.4 — which grant let this through, on **every** action decided by
            # authority and not only under break-glass. `AuthorityResult.grant_id` already
            # reaches the events; what nothing did was put it on the receipt, so answering
            # "what did this grant let through" meant joining events by hand. A field that
            # existed only under break-glass would be one nothing exercises on the ordinary
            # path, and so one nobody would notice breaking.
            authority_grant_id=_AUTHORITY_GRANT_ID.get(None),
            task=_TASK.get(None),
            # SPEC-v0.10 §3.4 — the hop this action ran under, and never the one it created:
            # `_HOP` is set by `_authority_result` from the hop the decision was made against.
            # §3.4.4's relay writes its created hop to `DELEGATION_CREATED`, not here.
            hop=_HOP.get(None),
            scope_hash=_SCOPE_HASH.get(None),
            budget_charges=_BUDGET_CHARGES.get(()),
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
            policy_hash=self._policy_hash,
            policy_version=self._policy.version,
            controls=evaluation.controls,
            # SPEC-v0.7 §6.11: what the presenting pass compared, hashes only, `None` where
            # there was none.
            precondition_at_request=None if compared is None else compared.at_request,
            precondition_at_recheck=None if compared is None else compared.at_recheck,
            # SPEC-v0.8 §2.5: what §2 verified reaches the evidence, or the milestone records
            # nothing. Read from the row rather than from the `Approval`, which carries only the
            # string `v0.1 §4.1` froze.
            approvers=() if compared is None else compared.approvers,
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
    preconditions: Callable[[Action], Mapping[str, Any]] | None = None,
    task: str | None = None,
    hop: str | None = None,
    scope: Callable[[Action], Mapping[str, Any]] | None = None,
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

    `preconditions` reads the state an approval depends on, and is `Control.execute`'s keyword
    (SPEC-v0.7 §6.2): called with the `Action` when the approval is requested and again on the
    presenting pass, before the store call that consumes it, with a refusal where the two
    fingerprints differ or only one exists. The recheck narrows the window a human's approval
    leaves open; it does not close it (§6.7). Not callable is refused here, at decoration time.
    """
    if not name:
        raise InvalidArgument("protect(name=...) must be a non-empty action name")
    provider = _checked_preconditions(preconditions, f"protect({name!r}, preconditions=...)")
    _check_template(name, "effect", effect)
    _check_template(name, "resource", resource)
    # SPEC-v0.9 §6.3.1. A **template** over the call's arguments, like `effect` and `resource`
    # above and unlike `Control.execute`'s `task=`, which takes the resolved id. A decorator
    # whose task could only be a literal would be unusable for the thing a task is: a run id
    # that changes per call. The operator declares the template; nothing here infers a task
    # from an argument it was not pointed at, which is the line §6.3 draws.
    _check_template(name, "task", task)
    # SPEC-v0.9 §5.6's third row: **at decoration time** for `@protect`, which is `v0.7 §6.2`'s
    # rule for a non-callable `preconditions=`. A misconfiguration an operator hears about at
    # import is one they fix before an agent runs, not during.
    _checked_scope(scope, f"protect({name!r}, scope=...)")
    held = None if lease is None else _checked_lease(lease, f"protect({name!r}, lease=...)")
    _reconciler(reconcile, reconcile_eagerly, f"protect({name!r}")

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        signature = inspect.signature(func)
        _reject_asynchronous(func, name)
        _reject_variadic(signature, name)
        _reject_reserved_parameters(signature, name)
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
            bound_task = resolved._resolve_template(action, task, "task")
            # SPEC-v0.10 §3.1 — a hop id varies per call while a decorator is applied once, so it
            # is a template like the task, resolved from the action's arguments and refused inside
            # the recording path by the same function. `@protect(hop="{hop}")` is how a receiving
            # agent's protected tool takes the reference its caller passed it.
            bound_hop = resolved._resolve_template(action, hop, "hop")
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
                    preconditions=provider,
                    task=bound_task,
                    hop=bound_hop,
                    scope=scope,
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
                        preconditions=provider,
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


def _action_from_receipt(receipt: Receipt, environment: str) -> Action | None:
    """Rebuild the action a receipt records, or `None` where it cannot be (SPEC-v0.8 §8.5).

    **The schema is checked first**, which §8.5 names ("a schema the binary does not know") and
    an earlier build did not implement: `Receipt.from_dict` does not raise on an unknown one, so
    a receipt written by a later version rebuilt fine and was silently **graded** -- counted as
    unchanged, or reported as changed, on fields this binary may be reading wrongly. `v0.6 §3.2`
    draws the same distinction for a store row: skipped is not the same as unchanged.
    """
    if receipt.schema not in KNOWN_RECEIPT_SCHEMAS:
        return None
    try:
        return Action(
            name=receipt.action,
            arguments=dict(receipt.arguments),
            principal=receipt.principal,
            resource=receipt.resource,
            environment=receipt.environment or environment,
        )
    except (InvalidArgument, TypeError, ValueError):
        return None


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


def _reject_asynchronous(func: Callable[..., object], name: str) -> None:
    """Refuse an `async def`, a generator or an async generator as a protected function.

    SPEC-v0.1 §5.5 -- the wrapped function is the executor, and `wrapper` is synchronous: it
    calls the executor, takes the return value as the result, and commits. Handed a coroutine
    function, "the return value" is an un-awaited coroutine object, so the effect reached
    `COMMITTED` and the receipt was written **before the body had run**, and the key being
    committed, the legitimate retry was then refused with `DuplicateEffect` for ever. A
    consequential action recorded as done that never happened is the one outcome this library
    exists to prevent, and it arrived silently: no exception, only a `RuntimeWarning` about a
    coroutine nobody awaited.

    A generator function has the same shape for the same reason -- calling it runs no body.

    Refused at **decoration** time, on `_reject_variadic`'s precedent: the mistake is in the
    source, so it fails on import rather than on the first agent run. The test is the
    function, not its return value: a plain function that returns an awaitable is a normal
    executor and stays legal.
    """
    if inspect.iscoroutinefunction(func):
        kind = "an `async def`"
    elif inspect.isasyncgenfunction(func):
        kind = "an async generator"
    elif inspect.isgeneratorfunction(func):
        kind = "a generator"
    else:
        return
    raise InvalidArgument(
        f"protect({name!r}): a protected function must be synchronous, and this is {kind}. "
        "The decorator calls the executor and commits what it returns, so it would record "
        "the effect as committed before the body had run. Wrap the synchronous work instead, "
        "or call the protected function from your async code with a runner such as "
        "`asyncio.to_thread`."
    )


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


def _reject_reserved_parameters(signature: inspect.Signature, name: str) -> None:
    """Refuse a protected function whose parameter is a name ctrlrun resolves itself.

    SPEC-v0.6 §7.4's table, first row: *"May an **argument** be called this? No."* An
    independent review found the set inert for `data_scope` -- `RESERVED_ARGUMENTS` was
    consulted only by the condition splitter, which exempts derived subjects -- so a
    `@protect`-ed function could take a parameter called `data_scope` and shadow the thing a
    rule was written to read.

    **`DERIVED_SUBJECTS`, not `RESERVED_ARGUMENTS`.** The first version of this check used the
    whole reserved set and broke shipped code on the spot: `user` has been in it since v0.3 and
    protected functions in this repository take a `user` parameter. Those names are refused as
    *condition subjects* so a rule cannot read who is acting -- an argument of that name
    collides with nothing, because policy cannot see the principal at all. A derived subject is
    different: it is merged into the same mapping the arguments are read from.

    Refused at **decoration** time and not at call time, on `_reject_variadic`'s precedent:
    the mistake is in the source, so it should fail on import rather than on the first request.
    """
    from .policy import DERIVED_SUBJECTS

    offending = sorted(
        parameter for parameter in signature.parameters if parameter in DERIVED_SUBJECTS
    )
    if offending:
        raise InvalidArgument(
            f"protect({name!r}): {', '.join(repr(item) for item in offending)} is derived by "
            "ctrlrun and may not be a parameter of a protected function. SPEC-v0.6 §7.4 "
            "resolves it at evaluation from the arguments actually supplied, so an argument of "
            "the same name would mean two things in one rule"
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
