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
from datetime import UTC, datetime
from typing import Any, Final, ParamSpec, TypeVar

from .action import Action, Principal
from .errors import ActionDenied, InvalidArgument, NotExecuted
from .policy import Decision, Evaluation, Policy
from .receipt import Event, EventType, Receipt, ReceiptResult, new_receipt_id
from .state import InMemoryStateStore, StateStore

_LOG = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")

DEFAULT_ENVIRONMENT: Final = "production"

#: Denial reason when a protected function is called outside `ctrlrun.context()`.
NO_PRINCIPAL: Final = "no_principal"


def _utc_now() -> datetime:
    return datetime.now(UTC)


# --- the ambient context ---------------------------------------------------------------


@dataclass(frozen=True)
class _Invocation:
    principal: Principal
    environment: str


_CONTEXT: ContextVar[_Invocation] = ContextVar("ctrlrun_context")


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
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        # SPEC: §8 — the frozen signature also carries `approvals: ApprovalProvider`; the
        # provider protocol and its implementations arrive with build-list item 4.
        self._policy = policy
        self._store = store
        self._clock = clock

    @classmethod
    def from_file(cls, path: str | os.PathLike[str] | None = None) -> Control:
        """Build a Control from a policy file (`$CTRLRUN_CONFIG`, else `./ctrlrun.yaml`).

        A missing or malformed policy raises `PolicyError`: a Control cannot be constructed
        without one (SPEC-v0.1 §3.4).
        """
        # SPEC: §5.3 — SQLiteStateStore replaces this default with build-list item 6.
        return cls(Policy.from_file(path), InMemoryStateStore())

    @property
    def policy(self) -> Policy:
        return self._policy

    @property
    def store(self) -> StateStore:
        return self._store

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
        """
        if effect_key is not None:
            # SPEC: §5.3 — atomic effect reservation is build-list item 6. Accepting a key
            # and not reserving it would silently drop the duplicate protection it asks for.
            raise NotImplementedError(
                f"effect_key={effect_key!r}: effect reservation is not implemented yet "
                "(build-list item 6)"
            )

        started_at = self._clock()
        self._append(EventType.ACTION_PROPOSED, action, {"action_hash": action.action_hash})
        evaluation = self._policy.evaluate(action)
        self._append(
            EventType.POLICY_EVALUATED,
            action,
            {"decision": str(evaluation.decision), "reason": evaluation.reason},
        )

        if evaluation.decision is Decision.DENY:
            self._append(EventType.ACTION_DENIED, action, {"reason": evaluation.reason})
            self._record(action, evaluation, ReceiptResult.DENIED, started_at)
            raise ActionDenied(
                f"{action.name} denied: {evaluation.reason}",
                reason=evaluation.reason,
                action_id=action.action_id,
            )
        if evaluation.decision is Decision.APPROVE:
            # SPEC: §4 — approval request, grant and hash binding are build-list item 4.
            raise NotImplementedError(
                f"{action.name}: decision 'approve' is not implemented yet (build-list item 4)"
            )

        self._append(EventType.EXECUTION_STARTED, action, {})
        try:
            executor()
        except NotExecuted as exc:
            self._append(EventType.EXECUTION_FAILED, action, {"error": str(exc)})
            self._record(action, evaluation, ReceiptResult.FAILED, started_at, error=str(exc))
            raise
        except BaseException as exc:
            # SPEC: §5.5 — anything that is not NotExecuted is an AMBIGUOUS outcome, never
            # FAILED, and "anything" means BaseException: a KeyboardInterrupt mid-request
            # leaves the same unknown outcome a timeout does. Narrowing this to Exception
            # is a regression, not a cleanup.
            error = f"{type(exc).__name__}: {exc}"
            self._append(EventType.EXECUTION_AMBIGUOUS, action, {"error": error})
            self._record(action, evaluation, ReceiptResult.AMBIGUOUS, started_at, error=error)
            raise
        self._append(EventType.EXECUTION_COMMITTED, action, {})
        return self._record(action, evaluation, ReceiptResult.COMMITTED, started_at)

    def _append(self, type_: EventType, action: Action, data: Mapping[str, Any]) -> None:
        self._store.append_event(
            Event(type=type_, action_id=action.action_id, ts=self._clock(), data=data)
        )

    def _record(
        self,
        action: Action,
        evaluation: Evaluation,
        result: ReceiptResult,
        started_at: datetime,
        error: str | None = None,
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
            result=result,
            started_at=started_at,
            finished_at=self._clock(),
            error=error,
        )
        self._store.put_receipt(receipt)
        return receipt


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
    """
    if not name:
        raise InvalidArgument("protect(name=...) must be a non-empty action name")

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        signature = inspect.signature(func)
        _reject_variadic(signature, name)

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            if effect is not None:
                # SPEC-v0.1 §5.1 — the shared template resolver is build-list item 5.
                raise NotImplementedError(
                    f"{name}: effect={effect!r}: effect keys are not implemented yet "
                    "(build-list item 5)"
                )
            if resource is not None and "{" in resource:
                # SPEC-v0.1 §5.1 — `resource` is a template too, resolved by the same
                # resolver against the bound arguments. Until item 5 lands, refuse rather
                # than hash a literal `customer:{customer_id}` into the action.
                raise NotImplementedError(
                    f"{name}: resource={resource!r}: resource templates are not implemented "
                    "yet (build-list item 5)"
                )
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
                resource=resource,
                environment=invocation.environment,
            )
            resolved = control if control is not None else _default_control()
            returned: list[R] = []

            def executor() -> None:
                returned.append(_invoke(func, signature, action.canonical_arguments))

            resolved.execute(action, executor, effect_key=None)
            return returned[0]

        return wrapper

    return decorator


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
