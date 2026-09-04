"""Deriving scenarios from a configuration, and running them. SPEC-v0.4 §3.

Everything here is derived from the two documents verify reads. There is **no randomness** —
not seeded randomness, none: selection is sorted by codepoint, values come from a fixed table,
and two runs against one document choose the same actions with the same arguments (§3.2, T105).

Three rules this module is written against, and each has a place below where it would be easy
to break:

- **N/A is a statement about the document.** A guarantee whose scenario cannot be built from
  the configuration is `not_applicable` with the reason from §2.2. A scenario that could not be
  built for any other cause — a synthesized vector landing in the wrong rule, a store that
  would not open — is an internal error and exits 3 (§3.8). The two are never conflated.
- **Verify never touches the operator's store.** Every scenario runs against a scratch store in
  a temporary directory removed when the run ends. `state_path()` is never called and
  `Control.from_file()` is never used (§3.5, T103).
- **Every guarantee carries a positive control.** A refusal asserted against a scenario in
  which nothing ran passes on a kernel with the guard deleted, so each scenario establishes
  that the observable would have been visible had the guard not fired. A control that does not
  behave as specified is `fail` with `reason: "control failed"` — never a pass, never an N/A
  (§1.3, T125).
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from ..action import Action, Principal
from ..approval import DEFAULT_APPROVAL_TTL, ApprovalStatus, LocalApprovalProvider
from ..authority import (
    AUTHORITY_EXPIRED,
    CONTAINMENT,
    DEEP_WILDCARD,
    DIMENSIONS,
    REASON_PRECEDENCE,
    Authority,
    Grant,
    Subject,
    contained_dimension,
)
from ..control import Control, context, protect
from ..effect import EffectRecord, EffectState, resolve_effect_key, resolve_resource
from ..errors import (
    ActionDenied,
    AmbiguousEffect,
    ApprovalMismatch,
    AuthorityDenied,
    AuthorityEscalation,
    CTRLRunError,
    DuplicateEffect,
    InvalidArgument,
    NotExecuted,
    PolicyError,
)
from ..policy import Condition, Decision, Policy, _ActionPolicy, _Rule, discover_policy_path
from ..receipt import Event, EventType, Receipt, ReceiptResult, iso_timestamp
from ..state import InMemoryStateStore, SQLiteStateStore
from . import guarantees as reg
from .report import Counterexample, GuaranteeResult, Status
from .worker import OUTCOME_COMMITTED

_LOG = logging.getLogger(__name__)

#: Who verify records as the author of the approvals it grants itself (§3.5). Not a person,
#: and named so no reader of the evidence mistakes it for one.
APPROVER: Final = "ctrlrun-verify"

#: §3.6 — the base instant where no grant carries an `expires_at`.
FALLBACK_T0: Final = datetime(2026, 1, 1, tzinfo=UTC)

#: §3.1 — the one `--store-url` value v0.4 accepts. Everything else exits 2 naming v0.6.
SQLITE_STORE_URL: Final = "sqlite"

#: §3.4's derivation of a principal from a grant's subject.
WILDCARD: Final = "*"

_ONE_HOUR: Final = timedelta(hours=1)
_ONE_MICROSECOND: Final = timedelta(microseconds=1)

#: Every loop is bounded (§3.6). A child that wedges makes G4 fail red rather than hanging CI.
_CHILD_TIMEOUT_SECONDS: Final = 120

#: An extra N/A reason §2.2 does not name, because it does not arise in a single-grant
#: configuration: where a *second* grant covers the same action past the first one's expiry,
#: the expired grant refuses nothing observable and G8 would report a failure that is really a
#: property of a layered document. A statement about the configuration, so N/A and not FAIL.
EXPIRY_NOT_DECISIVE: Final = (
    "no grant's expiry is the last authority for an action it covers; another grant still "
    "permits the action after it lapses"
)


# --- refusals verify itself makes (§3.8, §10) -------------------------------------------


class VerifyRefused(CTRLRunError):
    """The configuration was refused or is unusable: exit 2 (§3.8).

    Private to the package by convention — SPEC-v0.4 §9.1 freezes `run`, `Status`, `Report`,
    `GuaranteeResult` and `Counterexample` and no other public name — and imported by
    `cli/main.py`, which is the only caller that needs to turn it into an exit code.
    """


class VerifyInternalError(CTRLRunError):
    """A defect in verify itself: exit 3 (§3.8).

    Never a FAIL and never an N/A. A defect in verify must not read as a defect in the kernel,
    and it must not read as a property of the configuration either.
    """


class _ControlFailed(Exception):
    """§1.3 — the positive control did not behave as specified."""

    def __init__(self, observed: str, expected: str) -> None:
        super().__init__(observed)
        self.observed = observed
        self.expected = expected


class _Violation(Exception):
    """The guarantee's own refusal did not happen, or did not happen for the stated reason."""

    def __init__(self, expected: str, observed: str) -> None:
        super().__init__(observed)
        self.expected = expected
        self.observed = observed


# --- the clock, the sink and the fake executors -----------------------------------------


class _Clock:
    """An injected clock, moved explicitly by the scenario that needs time to pass (§3.6).

    No scenario sleeps. G8's "one microsecond after `expires_at`" is a step, not a wait.
    """

    def __init__(self, base: datetime) -> None:
        self.now = base

    def __call__(self) -> datetime:
        return self.now

    def at(self, moment: datetime) -> None:
        self.now = moment

    def advance(self, delta: timedelta) -> None:
        self.now += delta


class _Recorder:
    """The one sink a scenario registers (§3.5): in memory, so a counterexample has evidence.

    No `JSONLEventSink`. Verify writes no evidence files, because evidence of a scenario that
    never happened, filed beside evidence of actions that did, is a receipt trail nobody can
    read.
    """

    def __init__(self) -> None:
        self.events: list[Event] = []
        self.receipts: list[Receipt] = []

    def on_event(self, event: Event) -> None:
        self.events.append(event)

    def on_receipt(self, receipt: Receipt) -> None:
        self.receipts.append(receipt)

    def types(self) -> list[str]:
        return [str(event.type) for event in self.events]


class _Executor:
    """An in-process fake. It counts, and it does whatever the scenario told it to do.

    Verify never calls the operator's executor (§1.2) and no fake opens a socket (§3.7).
    """

    def __init__(self, behaviour: Callable[[], Any] | None = None) -> None:
        self.calls = 0
        self._behaviour = behaviour

    def __call__(self) -> Any:
        self.calls += 1
        if self._behaviour is not None:
            return self._behaviour()
        return f"{APPROVER}-result"


def _raises(exception: BaseException) -> Callable[[], Any]:
    def behaviour() -> Any:
        raise exception

    return behaviour


# --- the loaded configuration -----------------------------------------------------------


@dataclass(frozen=True)
class _Loaded:
    policy: Policy
    policy_path: Path
    policy_sha: str
    authority: Authority | None
    authority_path: Path | None
    authority_sha: str | None
    authority_text: str | None
    authority_standalone: bool


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load(
    config: str | os.PathLike[str] | None, authority: str | os.PathLike[str] | None
) -> _Loaded:
    """Read the policy document, and the authority document beside it (§3.1).

    Those two and nothing else. Verify does not import the operator's package, does not read
    `$CTRLRUN_STATE`, and does not open the operator's store.
    """
    from ..authority import _optional_from_yaml

    policy_path = Path(config) if config is not None else discover_policy_path()
    try:
        policy_text = policy_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise VerifyRefused(f"policy file {policy_path} could not be read: {exc}") from exc
    try:
        policy = Policy.from_yaml(policy_text, source=str(policy_path))
    except PolicyError as exc:
        raise VerifyRefused(str(exc)) from exc

    try:
        in_document = _optional_from_yaml(policy_text, source=str(policy_path))
    except PolicyError as exc:
        raise VerifyRefused(str(exc)) from exc
    if authority is None:
        return _Loaded(
            policy=policy,
            policy_path=policy_path,
            policy_sha=_digest(policy_text),
            authority=in_document,
            authority_path=policy_path if in_document is not None else None,
            authority_sha=_digest(policy_text) if in_document is not None else None,
            authority_text=policy_text if in_document is not None else None,
            authority_standalone=False,
        )

    authority_path = Path(authority)
    if in_document is not None:
        # §3.1 — declaring authority in two places is refused, naming both, exactly as
        # `ctrlrun gateway --authority` refuses it. Choosing one silently would leave two
        # answers to "which grants are in force" on one machine.
        raise VerifyRefused(
            f"authority is declared twice: an 'authority:' section in {policy_path} and "
            f"--authority {authority_path}. Remove one; a configuration with two answers to "
            "'which grants are in force' is one nobody can resolve later"
        )
    try:
        authority_text = authority_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise VerifyRefused(
            f"authority document {authority_path} could not be read: {exc}"
        ) from exc
    try:
        loaded = Authority.from_yaml(authority_text, source=str(authority_path), standalone=True)
    except PolicyError as exc:
        raise VerifyRefused(str(exc)) from exc
    return _Loaded(
        policy=policy,
        policy_path=policy_path,
        policy_sha=_digest(policy_text),
        authority=loaded,
        authority_path=authority_path,
        authority_sha=_digest(authority_text),
        authority_text=authority_text,
        authority_standalone=True,
    )


# --- argument synthesis (§3.3) ----------------------------------------------------------


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _negate_value(operand: object) -> Any:
    """§3.3's `X_neq` row: a value that is not the operand, in the operand's own shape."""
    if isinstance(operand, int) and not isinstance(operand, bool):
        return operand + 1
    if isinstance(operand, str):
        return f"{operand}-x"
    return APPROVER


def _bounds(conditions: Sequence[Condition]) -> tuple[int | None, int | None]:
    lower: int | None = None
    upper: int | None = None
    for condition in conditions:
        if condition.op in ("gte", "gt"):
            candidate = condition.operand + (0 if condition.op == "gte" else 1)
            lower = candidate if lower is None else max(lower, candidate)
        elif condition.op in ("lte", "lt"):
            candidate = condition.operand - (0 if condition.op == "lte" else 1)
            upper = candidate if upper is None else min(upper, candidate)
    return lower, upper


def _argument_value(conditions: Sequence[Condition]) -> tuple[bool, Any]:
    """§3.3's per-argument table, applied to every condition naming one argument.

    Returns `(satisfiable, value)`. Contradictory bounds are a normal outcome and not an
    error: they mean this action cannot be driven to that decision.
    """
    equals = [c for c in conditions if c.op == "eq"]
    members = [c for c in conditions if c.op == "in"]
    excluded = [c.operand for c in conditions if c.op == "neq"]
    lower, upper = _bounds(conditions)

    if equals:
        return True, equals[0].operand
    if members:
        for item in members[0].operand:
            if not any(item == other and type(item) is type(other) for other in excluded):
                return True, item
        return False, None
    if lower is not None or upper is not None:
        if lower is not None and upper is not None and lower > upper:
            return False, None
        # §3.3 — the lower bound of the satisfiable interval, the upper where there is no
        # lower. A fixed choice, so two runs agree.
        value = lower if lower is not None else upper
        assert value is not None
        while any(_is_int(other) and other == value for other in excluded):
            if upper is not None and value >= upper:
                return False, None
            value += 1
        return True, value
    if excluded:
        return True, _negate_value(excluded[0])
    return False, None


def _solve(conditions: Sequence[Condition]) -> dict[str, Any] | None:
    """An argument mapping satisfying every condition of one rule, or `None`."""
    grouped: dict[str, list[Condition]] = {}
    for condition in conditions:
        grouped.setdefault(condition.argument, []).append(condition)
    vector: dict[str, Any] = {}
    for argument in sorted(grouped):
        satisfiable, value = _argument_value(grouped[argument])
        if not satisfiable:
            return None
        vector[argument] = value
    return vector


def _negations(condition: Condition) -> list[tuple[str, Any]]:
    """§3.3's negation table: how to make one condition false.

    Negating a rule's conjunction gives a disjunction, so failing an earlier rule means
    failing **one** of its conditions, and the engine tries each in a fixed order.
    """
    operand = condition.operand
    if condition.op == "gte":
        return [(condition.argument, operand - 1)]
    if condition.op == "gt":
        return [(condition.argument, operand)]
    if condition.op == "lte":
        return [(condition.argument, operand + 1)]
    if condition.op == "lt":
        return [(condition.argument, operand)]
    if condition.op == "eq":
        return [(condition.argument, _negate_value(operand))]
    if condition.op == "neq":
        return [(condition.argument, operand)]
    if condition.op == "in":
        first = operand[0] if operand else APPROVER
        return [(condition.argument, _negate_value(first))]
    return []


def _candidates(rules: Sequence[_Rule], index: int) -> Iterator[dict[str, Any]]:
    """Vectors that aim at `rules[index]`, in a fixed order, bounded at 64 (§3.3).

    `v0.1 §3.2` is first-match-wins, so a vector that satisfies rule *i* must also fail every
    rule before it. A bound rather than a solver: the work is finite, the failure mode is
    legible, and a configuration whose rules cannot be driven within it reports N/A rather
    than running long.
    """
    base = _solve(rules[index].conditions)
    if base is None:
        return
    options: list[list[tuple[str, Any]]] = []
    for rule in rules[:index]:
        ways = [pair for condition in rule.conditions for pair in _negations(condition)]
        if not ways:
            # An earlier rule with no conditions always matches, so this one is unreachable.
            return
        options.append(ways)
    for count, combination in enumerate(itertools.product(*options)):
        if count >= reg.CANDIDATE_BOUND:
            return
        vector = dict(base)
        for argument, value in combination:
            vector[argument] = value
        yield vector


def _placeholders(policy: Policy, name: str) -> dict[str, Any]:
    """§3.3's last row: an argument named only by a template gets `ctrlrun-verify-<name>`.

    Every value verify invents carries the prefix, so a value that ever appeared anywhere it
    should not have is recognizable on sight.
    """
    from ..effect import template_placeholders

    values: dict[str, Any] = {}
    for template in (policy.effect_template(name), policy.resource_template(name)):
        if template is None:
            continue
        for placeholder in template_placeholders(template):
            values.setdefault(placeholder, f"{reg.SYNTHETIC_PREFIX}-{placeholder}")
    return values


# --- selection (§3.2, §3.4) -------------------------------------------------------------


@dataclass(frozen=True)
class _Selection:
    """One guarantee's concrete scenario: what to run, as whom, and where it lands."""

    action: str
    arguments: Mapping[str, Any]
    decision: Decision
    resource: str | None
    effect_key: str | None
    principal: Principal
    environment: str
    grant: Grant | None = None
    rule_reason: str = ""

    def build(self) -> Action:
        return Action(
            name=self.action,
            arguments=dict(self.arguments),
            principal=self.principal,
            resource=self.resource,
            environment=self.environment,
        )


def _principal_for(subject: Subject) -> Principal:
    """§3.4 — the principal comes from the grant under test, never from the policy."""
    return Principal(
        agent=str(_from_pattern(subject.agent or WILDCARD)),
        user=_from_pattern(subject.user),
    )


def _from_pattern(pattern: str | None) -> str | None:
    if pattern is None:
        return None
    if pattern == WILDCARD:
        return APPROVER
    if pattern.endswith(WILDCARD):
        return f"{pattern[:-1]}{APPROVER}"
    return pattern


class Engine:
    """Derives and runs the scenarios for one configuration (§3).

    One instance per `run()`. It owns the scratch directory, and removes it when the run ends
    — including when it ends by exception (§3.5).
    """

    def __init__(self, loaded: _Loaded, scratch: Path) -> None:
        self._loaded = loaded
        self._scratch = scratch
        self._t0 = _base_instant(loaded.authority)
        self._default_environment = (loaded.policy.environment or "production").strip()

    # --- derivation ---------------------------------------------------------------------

    @property
    def policy(self) -> Policy:
        return self._loaded.policy

    @property
    def authority(self) -> Authority | None:
        return self._loaded.authority

    def _synthesize(
        self, name: str, decision: Decision, mutation: Mapping[str, Any] | None = None
    ) -> tuple[dict[str, Any], str] | None:
        """An argument vector driving `name` to `decision`, and the rule reason it reached.

        **The vector is checked before it is used** (§3.3). Having built one, the engine
        evaluates the action it just constructed and asserts the decision is the one it was
        aiming for; a vector that lands in a different rule is an internal error, not a FAIL,
        because it would run, refuse something, and report a guarantee that was never
        exercised.
        """
        entry: _ActionPolicy | None = self.policy.actions.get(name)
        if entry is None:
            return None
        extras = _placeholders(self.policy, name)
        if entry.decision is not None:
            if entry.decision is not decision:
                return None
            vector = dict(extras)
            if mutation:
                vector.update(mutation)
            return self._checked(name, vector, decision, "decision")
        for index, rule in enumerate(entry.rules):
            if rule.decision is not decision:
                continue
            for candidate in _candidates(entry.rules, index):
                vector = {**extras, **candidate}
                if mutation:
                    vector.update(mutation)
                checked = self._checked(name, vector, decision, f"rule[{index}]")
                if checked is not None:
                    return checked
        return None

    def _checked(
        self, name: str, vector: dict[str, Any], decision: Decision, expected_reason: str
    ) -> tuple[dict[str, Any], str] | None:
        try:
            action = Action(
                name=name,
                arguments=vector,
                principal=Principal(agent=APPROVER),
                resource=self._resource(name, vector),
                environment=self._default_environment,
            )
        except (InvalidArgument, CTRLRunError):
            return None
        evaluation = self.policy.evaluate(action)
        if evaluation.decision is not decision:
            return None
        if evaluation.reason != expected_reason:
            raise VerifyInternalError(
                f"{name}: a synthesized vector aimed at {expected_reason} landed in "
                f"{evaluation.reason!r} (SPEC-v0.4 §3.3)"
            )
        return vector, evaluation.reason

    def _resource(self, name: str, arguments: Mapping[str, Any]) -> str | None:
        template = self.policy.resource_template(name)
        if template is None:
            return None
        return resolve_resource(template, arguments)

    def _effect_key(self, action: Action) -> str | None:
        template = self.policy.effect_template(action.name)
        if template is None:
            return None
        return resolve_effect_key(template, action)

    def select(
        self,
        *,
        decisions: Sequence[Decision] = (Decision.ALLOW, Decision.APPROVE),
        needs_effect: bool = False,
        grant_filter: Callable[[Grant], bool] | None = None,
        mutation: Mapping[str, Any] | None = None,
    ) -> _Selection | None:
        """§3.2 — the first action, sorted by codepoint, that satisfies the requirements.

        Where an `authority:` section exists the principal comes from a grant that actually
        covers the action (§3.4), because an action nothing authorizes is refused by the
        authority axis before the policy axis is ever reached (`v0.3 §4.3`), and a scenario
        built on one would exercise a different guarantee than the one it claims.
        """
        for name in sorted(self.policy.actions):
            if needs_effect and self.policy.effect_template(name) is None:
                continue
            for decision in decisions:
                synthesized = self._synthesize(name, decision, mutation)
                if synthesized is None:
                    continue
                arguments, reason = synthesized
                selection = self._bind(name, arguments, decision, reason, grant_filter)
                if selection is not None:
                    return selection
        return None

    def _bind(
        self,
        name: str,
        arguments: dict[str, Any],
        decision: Decision,
        reason: str,
        grant_filter: Callable[[Grant], bool] | None,
    ) -> _Selection | None:
        resource = self._resource(name, arguments)
        if self.authority is None:
            selection = _Selection(
                action=name,
                arguments=arguments,
                decision=decision,
                resource=resource,
                effect_key=None,
                principal=Principal(agent=APPROVER),
                environment=self._default_environment,
                rule_reason=reason,
            )
            return replace(selection, effect_key=self._effect_key(selection.build()))
        for grant_id in sorted(self.authority.grants):
            grant = self.authority.grants[grant_id]
            if grant_filter is not None and not grant_filter(grant):
                continue
            principal = _principal_for(grant.subject)
            environment = grant.environments[0] if grant.environments else self._default_environment
            action = Action(
                name=name,
                arguments=arguments,
                principal=principal,
                resource=resource,
                environment=environment,
            )
            if not grant.matches_shape(action) or not grant.constraints_hold(action):
                continue
            selection = _Selection(
                action=name,
                arguments=arguments,
                decision=decision,
                resource=resource,
                effect_key=self._effect_key(action),
                principal=principal,
                environment=environment,
                grant=grant,
                rule_reason=reason,
            )
            return selection
        return None

    # --- the scratch store, and the Control every scenario drives -----------------------

    def control(
        self, gid: str, *, clock: _Clock | None = None, authority: bool = True
    ) -> tuple[Control, SQLiteStateStore, _Recorder, _Clock]:
        """One scratch store per guarantee, and a `Control` composed the way an app composes one.

        `Control.from_file()` is not used: it would open the operator's store. The store is a
        file, because `SQLiteStateStore` refuses `:memory:` precisely so that G4 can mean
        something.
        """
        directory = self._scratch / gid
        directory.mkdir(parents=True, exist_ok=True)
        moving = clock if clock is not None else _Clock(self._t0)
        store = SQLiteStateStore(directory / "state.db", clock=moving)
        recorder = _Recorder()
        control = Control(
            self.policy,
            store,
            LocalApprovalProvider(store, clock=moving),
            clock=moving,
            sinks=[recorder],
            authority=self.authority if authority else None,
            environment=self._default_environment,
        )
        return control, store, recorder, moving

    def _control_for(
        self, gid: str, selection: _Selection, *, clock: _Clock | None = None
    ) -> tuple[Control, SQLiteStateStore, _Recorder, _Clock]:
        directory = self._scratch / gid
        directory.mkdir(parents=True, exist_ok=True)
        moving = clock if clock is not None else _Clock(self._t0)
        store = SQLiteStateStore(directory / "state.db", clock=moving)
        recorder = _Recorder()
        control = Control(
            self.policy,
            store,
            LocalApprovalProvider(store, clock=moving),
            clock=moving,
            sinks=[recorder],
            authority=self.authority,
            environment=selection.environment,
        )
        return control, store, recorder, moving

    def approve(
        self, control: Control, store: SQLiteStateStore, action: Action, selection: _Selection
    ) -> str | None:
        """Grant verify's own approval, through the call `ctrlrun approve` makes (§3.5).

        G1 and G2 are *about* this mechanism; every other scenario that meets an approval gate
        uses it to get on with the guarantee it is actually testing, and the report names the
        action as approved so a reader is not left thinking a human was involved.
        """
        if selection.decision is not Decision.APPROVE:
            return None
        request = control.approvals.request(action, DEFAULT_APPROVAL_TTL)
        store.grant_approval(request.request_id, APPROVER)
        return request.request_id

    def execute(
        self,
        control: Control,
        action: Action,
        executor: _Executor,
        effect_key: str | None,
        approval_id: str | None,
    ) -> Receipt:
        if approval_id is None:
            return control.execute(action, executor, effect_key)
        from ..control import with_approval

        with with_approval(approval_id):
            return control.execute(action, executor, effect_key)

    def refused(
        self,
        thunk: Callable[[], Any],
        refusals: tuple[type[BaseException], ...],
        expected: str,
        ran: str,
    ) -> BaseException:
        """Run one attempt that MUST be refused, and hand back the refusal.

        The `else` branch is the point: an attempt that was not refused is a `_Violation`
        naming what happened, and so is one that raised something else. Without this, a kernel
        with the guard deleted would let the executor's own exception escape the scenario, and
        verify would report an internal error — a defect in the kernel reading as a defect in
        verify.
        """
        try:
            thunk()
        except refusals as refusal:
            return refusal
        except (VerifyRefused, VerifyInternalError):
            raise
        except Exception as escaped:
            raise _Violation(
                expected,
                f"{ran}, and raised {type(escaped).__name__}: {escaped}",
            ) from escaped
        raise _Violation(expected, ran)

    # --- results ------------------------------------------------------------------------

    def na(self, gid: str, reason: str, **detail: Any) -> GuaranteeResult:
        """`not_applicable`, with the reason that made it so (§1, §2.1).

        Excluded from the denominator and shown separately. There is no flag that folds one
        into the count, and there is no path from a failed run to this status.
        """
        guarantee = reg.BY_ID[gid]
        return GuaranteeResult(
            id=gid,
            title=guarantee.title,
            status=Status.NOT_APPLICABLE,
            reason=reason,
            descends_from=guarantee.descends_from,
            detail=detail,
        )

    def _passed(
        self, gid: str, selection: _Selection | None, detail: Mapping[str, Any]
    ) -> GuaranteeResult:
        guarantee = reg.BY_ID[gid]
        carried = dict(detail)
        return GuaranteeResult(
            id=gid,
            title=guarantee.title,
            status=Status.PASS,
            reason=None,
            action=None if selection is None else selection.action,
            arguments=None if selection is None else dict(selection.arguments),
            effect_key=None if selection is None else selection.effect_key,
            grant_id=carried.pop("grant_id", None),
            descends_from=guarantee.descends_from,
            detail=carried,
        )

    def _failed(
        self,
        gid: str,
        selection: _Selection | None,
        reason: str,
        example: Counterexample,
        detail: Mapping[str, Any],
    ) -> GuaranteeResult:
        guarantee = reg.BY_ID[gid]
        carried = dict(detail)
        return GuaranteeResult(
            id=gid,
            title=guarantee.title,
            status=Status.FAIL,
            reason=reason,
            action=None if selection is None else selection.action,
            arguments=None if selection is None else dict(selection.arguments),
            effect_key=None if selection is None else selection.effect_key,
            grant_id=carried.pop("grant_id", None),
            descends_from=guarantee.descends_from,
            detail=carried,
            counterexample=example,
        )

    def graded(
        self,
        gid: str,
        selection: _Selection | None,
        store: SQLiteStateStore,
        recorder: _Recorder,
        body: Callable[[dict[str, Any]], None],
    ) -> GuaranteeResult:
        """Run one scenario body and turn what it raised into a result.

        `_ControlFailed` is `fail` with `reason: "control failed"` — §1.3's rule, in one
        place, so no scenario can accidentally report a broken control as a pass. Anything
        else that escapes is an internal error and exits 3: a defect in verify must not read
        as a defect in the kernel.
        """
        detail: dict[str, Any] = {}
        try:
            body(detail)
        except _ControlFailed as failure:
            return self._failed(
                gid,
                selection,
                reg.CONTROL_FAILED,
                counterexample(store, recorder, failure.expected, failure.observed),
                detail,
            )
        except _Violation as violation:
            return self._failed(
                gid,
                selection,
                violation.observed,
                counterexample(store, recorder, violation.expected, violation.observed),
                detail,
            )
        except (VerifyRefused, VerifyInternalError):
            raise
        except Exception as exc:
            raise VerifyInternalError(f"{gid}: {type(exc).__name__}: {exc}") from exc
        return self._passed(gid, selection, detail)

    # --- G1: a mutated action cannot present a granted approval -------------------------

    def _mutation(self, selection: _Selection) -> dict[str, Any] | None:
        """A different action that still reaches the same rule, and the same grant.

        A mutation that changed the decision would be refused by the policy before the
        approval was ever consulted, and G1 would report a refusal it never tested. Every
        candidate is re-checked against `Policy.evaluate` and against the grant.
        """
        candidates: list[dict[str, Any]] = []
        for name in sorted(selection.arguments):
            value = selection.arguments[name]
            if isinstance(value, str):
                candidates.append({name: f"{value}-mutated"})
        # The fallback is an argument no rule and no grant mentions, so the decision cannot
        # move: it changes the canonical form, which is the whole of what an approval binds to.
        candidates.append({f"{reg.SYNTHETIC_PREFIX.replace('-', '_')}_mutation": "1"})
        for mutation in candidates:
            arguments = {**dict(selection.arguments), **mutation}
            try:
                action = Action(
                    name=selection.action,
                    arguments=arguments,
                    principal=selection.principal,
                    resource=self._resource(selection.action, arguments),
                    environment=selection.environment,
                )
            except CTRLRunError:
                continue
            evaluation = self.policy.evaluate(action)
            if evaluation.decision is not selection.decision:
                continue
            if evaluation.reason != selection.rule_reason:
                continue
            if selection.grant is not None and not (
                selection.grant.matches_shape(action) and selection.grant.constraints_hold(action)
            ):
                continue
            if action.action_hash == selection.build().action_hash:
                continue
            return arguments
        return None

    def g1(self) -> GuaranteeResult:
        selection = self.select(decisions=(Decision.APPROVE,))
        if selection is None:
            return self.na("G1", reg.NO_APPROVE_RULE)
        mutated_arguments = self._mutation(selection)
        if mutated_arguments is None:
            raise VerifyInternalError(
                "G1: no mutation of the chosen action keeps the same decision; the fallback "
                "argument should always have (SPEC-v0.4 §3.3)"
            )
        control, store, recorder, _ = self._control_for("G1", selection)

        def body(detail: dict[str, Any]) -> None:
            action = selection.build()
            approval_id = self.approve(control, store, action, selection)
            detail["approved_by_verify"] = True
            mutated = Action(
                name=selection.action,
                arguments=mutated_arguments,
                principal=selection.principal,
                resource=self._resource(selection.action, mutated_arguments),
                environment=selection.environment,
            )
            mutated_executor = _Executor()
            refusal = self.refused(
                lambda: self.execute(
                    control, mutated, mutated_executor, self._effect_key(mutated), approval_id
                ),
                (ApprovalMismatch,),
                "ApprovalMismatch(reason='mismatch') on the mutated action",
                "the mutated action ran under an approval granted for a different action",
            )
            reason = getattr(refusal, "reason", "")
            _expect(
                reason == "mismatch",
                "ApprovalMismatch(reason='mismatch')",
                f"ApprovalMismatch(reason={reason!r})",
            )
            _expect(
                mutated_executor.calls == 0,
                "the executor is not reached",
                f"the executor was called {mutated_executor.calls} times",
            )
            record = store.get_approval(str(approval_id))
            _expect(
                record is not None and record.status is ApprovalStatus.GRANTED,
                "the approval is still granted and not consumed",
                f"the approval is {None if record is None else record.status}",
            )
            _expect(
                _named_event(recorder, EventType.APPROVAL_INVALIDATED, reason="mismatch"),
                "APPROVAL_INVALIDATED with reason 'mismatch'",
                f"events were {recorder.types()}",
            )
            executor = _Executor()
            receipt = self.execute(control, action, executor, selection.effect_key, approval_id)
            _expect_control(
                receipt.result is ReceiptResult.COMMITTED and executor.calls == 1,
                "the unmutated action, presenting the same approval, commits",
                f"it ended {receipt.result} after {executor.calls} executor calls",
            )

        try:
            return self.graded("G1", selection, store, recorder, body)
        finally:
            store.close()

    # --- G2: a consumed approval cannot be presented again -------------------------------

    def g2(self) -> GuaranteeResult:
        selection = self.select(decisions=(Decision.APPROVE,))
        if selection is None:
            return self.na("G2", reg.NO_APPROVE_RULE)
        control, store, recorder, _ = self._control_for("G2", selection)

        def body(detail: dict[str, Any]) -> None:
            action = selection.build()
            approval_id = self.approve(control, store, action, selection)
            detail["approved_by_verify"] = True
            executor = _Executor()
            first = self.execute(control, action, executor, selection.effect_key, approval_id)
            _expect_control(
                first.result is ReceiptResult.COMMITTED and executor.calls == 1,
                "the first presentation commits",
                f"it ended {first.result} after {executor.calls} executor calls",
            )
            replayed = selection.build()
            refusal = self.refused(
                lambda: self.execute(
                    control, replayed, executor, selection.effect_key, approval_id
                ),
                (ApprovalMismatch,),
                "ApprovalMismatch(reason='consumed') on the second presentation",
                "the same approval authorized a second execution",
            )
            reason = getattr(refusal, "reason", "")
            # `v0.1 §7` T4's note: where the action also carries an effect key,
            # `DuplicateEffect` would apply as well, and the approval check runs first.
            _expect(
                reason == str(ApprovalStatus.CONSUMED),
                "ApprovalMismatch(reason='consumed')",
                f"ApprovalMismatch(reason={reason!r})",
            )
            _expect(
                executor.calls == 1,
                "the executor ran exactly once across both presentations",
                f"the executor was called {executor.calls} times",
            )
            detail["also_duplicate_effect"] = selection.effect_key is not None

        try:
            return self.graded("G2", selection, store, recorder, body)
        finally:
            store.close()

    # --- G3: a committed effect cannot be executed again ---------------------------------

    def g3(self) -> GuaranteeResult:
        selection = self.select(needs_effect=True)
        if selection is None:
            return self.na("G3", reg.NO_EFFECT_TEMPLATE, note=reg.EFFECT_TEMPLATE_NOTE)
        control, store, recorder, _ = self._control_for("G3", selection)

        def body(detail: dict[str, Any]) -> None:
            key = str(selection.effect_key)
            action = selection.build()
            executor = _Executor()
            first = self.execute(
                control, action, executor, key, self.approve(control, store, action, selection)
            )
            record = store.get_effect(key)
            _expect_control(
                first.result is ReceiptResult.COMMITTED
                and record is not None
                and record.state is EffectState.COMMITTED,
                "the first attempt commits and the record reaches COMMITTED",
                f"it ended {first.result} with the record "
                f"{None if record is None else record.state}",
            )
            second = selection.build()
            refusal = self.refused(
                lambda: self.execute(
                    control, second, executor, key, self.approve(control, store, second, selection)
                ),
                (DuplicateEffect,),
                "DuplicateEffect(state='committed') on the second attempt",
                "the second attempt on a committed effect key executed",
            )
            state = getattr(refusal, "state", "")
            _expect(
                state == str(EffectState.COMMITTED),
                "DuplicateEffect(state='committed')",
                f"DuplicateEffect(state={state!r})",
            )
            _expect(
                executor.calls == 1,
                "the remote is not called a second time",
                f"the executor was called {executor.calls} times",
            )
            after = store.get_effect(key)
            _expect(
                after is not None and after.attempt == 1 and after.action_id == action.action_id,
                "the record still carries attempt 1 and the first attempt's action_id",
                f"the record is {after}",
            )

        try:
            return self.graded("G3", selection, store, recorder, body)
        finally:
            store.close()

    # --- G4: concurrent attempts produce exactly one winner ------------------------------

    def g4(self) -> GuaranteeResult:
        selection = self.select(needs_effect=True)
        if selection is None:
            return self.na("G4", reg.NO_EFFECT_TEMPLATE, note=reg.EFFECT_TEMPLATE_NOTE)
        # §2.2 — the children run under the **real** clock, because a callable is not
        # picklable and `spawn` is the default start method on macOS and Windows. A grant that
        # lapsed before this run therefore cannot cover them, and that is a property of the
        # document plus the wall clock rather than a broken guarantee.
        now = datetime.now(UTC)
        if selection.grant is not None and selection.grant.is_expired(now):
            return self.na("G4", reg.GRANT_ALREADY_EXPIRED)
        clock = _Clock(now)
        control, store, recorder, _ = self._control_for("G4", selection, clock=clock)

        def body(detail: dict[str, Any]) -> None:
            detail["processes"] = reg.PROCESSES
            detail["summary"] = f"{reg.PROCESSES} processes"
            key = str(selection.effect_key)
            # The control first: 8 processes on 8 **distinct** keys, all committing. Without
            # it the guarantee is satisfied by 8 children that failed to start, and the report
            # would say so in green (§2.2).
            control_keys = [
                f"{key}-{reg.SYNTHETIC_PREFIX}-control-{index}" for index in range(reg.PROCESSES)
            ]
            control_results, control_calls, control_pids = self._contend(
                control, store, selection, control_keys, "g4-control"
            )
            committed = [
                item for item in control_results if item.get("outcome") == OUTCOME_COMMITTED
            ]
            _expect_control(
                len(committed) == reg.PROCESSES and control_calls == reg.PROCESSES,
                f"{reg.PROCESSES} processes on {reg.PROCESSES} distinct keys all commit",
                f"{len(committed)} committed after {control_calls} executor calls; "
                f"{[item.get('message') for item in control_results if item.get('message')]}",
            )
            _expect_control(
                len(control_pids) == reg.PROCESSES and os.getpid() not in control_pids,
                "every attempt ran in its own OS process",
                f"{len(control_pids)} distinct child pids, parent {os.getpid()}",
            )
            detail["distinct_child_pids"] = len(control_pids)
            detail["parent_pid_among_children"] = os.getpid() in control_pids

            contended = [key] * reg.PROCESSES
            results, calls, pids = self._contend(
                control, store, selection, contended, "g4-contended"
            )
            winners = [item for item in results if item.get("outcome") == OUTCOME_COMMITTED]
            _expect(
                len(winners) == 1,
                "exactly one of the concurrent attempts commits",
                f"{len(winners)} of {reg.PROCESSES} attempts committed",
            )
            _expect(
                calls == 1,
                "the fake executor ran exactly once",
                f"the executor ran {calls} times across {reg.PROCESSES} processes",
            )
            _expect(
                len(pids) == reg.PROCESSES and os.getpid() not in pids,
                "every contending attempt ran in its own OS process",
                f"{len(pids)} distinct child pids, parent {os.getpid()}",
            )
            record = store.get_effect(key)
            _expect(
                record is not None and record.state is EffectState.COMMITTED,
                "the contended key ends COMMITTED",
                f"the record is {None if record is None else record.state}",
            )

        try:
            return self.graded("G4", selection, store, recorder, body)
        finally:
            store.close()

    def _contend(
        self,
        control: Control,
        store: SQLiteStateStore,
        selection: _Selection,
        keys: Sequence[str],
        label: str,
    ) -> tuple[list[dict[str, Any]], int, set[int]]:
        """Run one attempt per key in its own OS process, and read back what each did."""
        directory = self._scratch / "G4" / label
        counters = directory / "calls"
        results = directory / "results"
        counters.mkdir(parents=True, exist_ok=True)
        results.mkdir(parents=True, exist_ok=True)
        payloads: list[str] = []
        for index, key in enumerate(keys):
            action = selection.build()
            approval_id = self.approve(control, store, action, selection)
            payloads.append(
                json.dumps(
                    {
                        "index": index,
                        "policy_path": str(self._loaded.policy_path),
                        "authority_yaml": self._loaded.authority_text,
                        "authority_source": str(self._loaded.authority_path or ""),
                        "authority_standalone": self._loaded.authority_standalone,
                        "store_path": str(store.path),
                        "environment": selection.environment,
                        "action": selection.action,
                        "arguments": dict(selection.arguments),
                        "agent": selection.principal.agent,
                        "user": selection.principal.user,
                        "resource": selection.resource,
                        "effect_key": key,
                        "approval_id": approval_id,
                        "counter_dir": str(counters),
                        "result_dir": str(results),
                    }
                )
            )
        run_attempts(payloads)
        read: list[dict[str, Any]] = []
        pids: set[int] = set()
        for path in sorted(results.glob("*.json")):
            document = json.loads(path.read_text(encoding="utf-8"))
            read.append(document)
            pids.add(int(document["pid"]))
        return read, len(list(counters.iterdir())), pids

    # --- G5: an ambiguous outcome blocks a blind retry -----------------------------------

    def g5(self) -> GuaranteeResult:
        selection = self.select(needs_effect=True)
        if selection is None:
            return self.na("G5", reg.NO_EFFECT_TEMPLATE, note=reg.EFFECT_TEMPLATE_NOTE)
        control, store, recorder, _ = self._control_for("G5", selection)

        def body(detail: dict[str, Any]) -> None:
            # The control carries more weight than any other in the catalogue: it is the only
            # thing separating "CTRLRun blocks blind retries" from "CTRLRun blocks retries",
            # and the second sentence describes a library nobody can deploy (§2.2).
            control_key = f"{selection.effect_key!s}-{reg.SYNTHETIC_PREFIX}-control"
            seen: list[int] = []

            def not_executed_then_commit() -> Any:
                seen.append(1)
                if len(seen) == 1:
                    raise NotExecuted("ctrlrun-verify: the remote did nothing")
                return f"{APPROVER}-result"

            honest = _Executor(not_executed_then_commit)
            first = selection.build()
            with suppress(NotExecuted):
                self.execute(
                    control,
                    first,
                    honest,
                    control_key,
                    self.approve(control, store, first, selection),
                )
            failed = store.get_effect(control_key)
            _expect_control(
                failed is not None and failed.state is EffectState.FAILED,
                "an executor that raises NotExecuted leaves the record FAILED",
                f"the record is {None if failed is None else failed.state}",
            )
            retry = selection.build()
            admitted = self.execute(
                control, retry, honest, control_key, self.approve(control, store, retry, selection)
            )
            _expect_control(
                admitted.result is ReceiptResult.COMMITTED and honest.calls == 2,
                "the retry after NotExecuted is admitted and executes",
                f"it ended {admitted.result} after {honest.calls} executor calls",
            )

            key = str(selection.effect_key)
            remote = _Executor(
                _raises(
                    TimeoutError("ctrlrun-verify: the response was lost after the remote acted")
                )
            )
            lost = selection.build()
            with suppress(TimeoutError):
                self.execute(
                    control, lost, remote, key, self.approve(control, store, lost, selection)
                )
            record = store.get_effect(key)
            _expect(
                record is not None and record.state is EffectState.AMBIGUOUS,
                "the timed-out attempt leaves the record AMBIGUOUS",
                f"the record is {None if record is None else record.state}",
            )
            receipt = _last_receipt(store, lost.action_id)
            _expect(
                receipt is not None and receipt.result is ReceiptResult.AMBIGUOUS,
                "the timed-out attempt's receipt is `ambiguous`",
                f"the receipt is {None if receipt is None else receipt.result}",
            )
            blind = selection.build()
            self.refused(
                lambda: self.execute(
                    control, blind, remote, key, self.approve(control, store, blind, selection)
                ),
                (AmbiguousEffect,),
                "AmbiguousEffect on the retry",
                "the retry after an ambiguous outcome reached the remote",
            )
            _expect(
                remote.calls == 1,
                "the remote is called once, not twice",
                f"the remote was called {remote.calls} times",
            )
            blocked = _last_receipt(store, blind.action_id)
            _expect(
                blocked is not None and blocked.result is ReceiptResult.BLOCKED,
                "the retry's receipt is `blocked`",
                f"the receipt is {None if blocked is None else blocked.result}",
            )
            after = store.get_effect(key)
            _expect(
                after is not None and after.state is EffectState.AMBIGUOUS,
                "the record is still AMBIGUOUS afterwards",
                f"the record is {None if after is None else after.state}",
            )

        try:
            return self.graded("G5", selection, store, recorder, body)
        finally:
            store.close()

    # --- G6: an action the policy does not list is refused -------------------------------

    def g6(self) -> GuaranteeResult:
        listed = sorted(self.policy.actions)
        if not listed:
            return self.na("G6", reg.NO_ACTIONS)
        digest = hashlib.sha256("\n".join(listed).encode("utf-8")).hexdigest()[:12]
        absent = f"ctrlrun.verify.absent.{digest}"
        # The guarantee is the **behaviour** - an action the policy does not list never
        # executes - and not one particular reason string. §2.2 names `unknown_action`, which
        # is what a v0.2 configuration produces; under an `authority:` section `v0.3 §4.3`
        # evaluates authority first and no grant covers a name derived to collide with
        # nothing, so the same refusal arrives as an authority denial before policy is
        # reached. Both are the guarantee holding. What is asserted is that the refusal
        # happened, that its reason is one this configuration can actually produce, and that
        # nothing ran; the reason is reported so a reader can see which axis refused
        # (SPEC-v0.4 §12.1).
        reachable = {"unknown_action"}
        if self.authority is not None:
            reachable |= set(REASON_PRECEDENCE)
        selection = self.select()
        principal = Principal(agent=APPROVER) if selection is None else selection.principal
        environment = self._default_environment if selection is None else selection.environment
        control, store, recorder, _ = self._control_for(
            "G6",
            selection
            or _Selection(
                action=absent,
                arguments={},
                decision=Decision.DENY,
                resource=None,
                effect_key=None,
                principal=principal,
                environment=environment,
            ),
        )

        def body(detail: dict[str, Any]) -> None:
            detail["absent_action"] = absent
            detail["reachable_reasons"] = sorted(reachable)
            executor = _Executor()
            action = Action(
                name=absent,
                arguments={},
                principal=principal,
                environment=environment,
            )
            refusal = self.refused(
                lambda: control.execute(action, executor, None),
                (ActionDenied,),
                f"ActionDenied with a reason in {sorted(reachable)}",
                f"the unlisted action {absent} was not refused",
            )
            reason = getattr(refusal, "reason", "")
            detail["refused_by"] = reason
            _expect(
                reason in reachable,
                f"ActionDenied with a reason in {sorted(reachable)}",
                f"ActionDenied(reason={reason!r}), which this configuration cannot produce",
            )
            _expect(
                executor.calls == 0,
                "the executor is not reached",
                f"the executor was called {executor.calls} times",
            )
            receipt = _last_receipt(store, action.action_id)
            _expect(
                receipt is not None and receipt.result is ReceiptResult.DENIED,
                "a `denied` receipt is written",
                f"the receipt is {None if receipt is None else receipt.result}",
            )
            # The control. Without it the guarantee is satisfied by a Control that refuses
            # everything, and the report would say so in green.
            if selection is not None:
                detail["control"] = "an action this configuration admits reaches a decision"
                evaluation = control.evaluate(selection.build())
                _expect_control(
                    evaluation.decision is not Decision.DENY,
                    f"{selection.action} evaluates to something other than a denial",
                    f"it evaluated to {evaluation.decision}/{evaluation.reason}",
                )
            else:
                # Nothing in this configuration can run, so the strongest control available
                # is that a **listed** action is refused for some reason other than being
                # unlisted. Recorded, because a weaker control is not the same control.
                detail["control"] = "a listed action is refused for some other reason"
                known = Action(
                    name=listed[0],
                    arguments={},
                    principal=principal,
                    environment=environment,
                )
                evaluation = control.evaluate(known)
                _expect_control(
                    evaluation.reason != "unknown_action",
                    f"the listed action {listed[0]} does not evaluate to unknown_action",
                    f"it evaluated to {evaluation.decision}/{evaluation.reason}",
                )

        try:
            return self.graded("G6", None, store, recorder, body)
        finally:
            store.close()

    # --- G7: an action with no principal is refused --------------------------------------

    def _protected(
        self, selection: _Selection, control: Control, executor: _Executor
    ) -> Callable[..., Any]:
        """A `@protect`-decorated fake with the chosen action's argument names.

        The decorator is the entry point G7 is about — `v0.1 §2.1`'s refusal happens in the
        wrapper, before a `Control` is asked anything — so the scenario goes through it rather
        than round it. `__signature__` gives the fake real parameter names without an `exec`;
        `protect` reads it, and `_reject_variadic` sees named parameters, which is what a
        protected function must have for policy to be able to address its arguments.
        """
        import inspect

        parameters = [
            inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY)
            for name in sorted(selection.arguments)
        ]

        def fake(**kwargs: Any) -> Any:
            return executor()

        fake.__signature__ = inspect.Signature(parameters)  # type: ignore[attr-defined]
        fake.__name__ = "ctrlrun_verify_action"
        decorated: Callable[..., Any] = protect(selection.action, control=control)(fake)
        return decorated

    def g7(self) -> GuaranteeResult:
        selection = self.select()
        if selection is None:
            # SPEC: §2.2 says G7 is never N/A, and §1.3 requires a positive control. Both
            # cannot hold for a policy in which nothing can run: the control is "the identical
            # call inside `context()` runs", and there is no such call. T101b requires an empty
            # `actions:` to leave zero applicable guarantees, which settles it — this is a
            # statement about the document, so N/A, and never a silent pass.
            return self.na("G7", reg.EVERY_ACTION_DENIED)
        control, store, recorder, _ = self._control_for("G7", selection)

        def body(detail: dict[str, Any]) -> None:
            executor = _Executor()
            call = self._protected(selection, control, executor)
            arguments = dict(selection.arguments)
            refusal = self.refused(
                lambda: call(**arguments),
                (ActionDenied,),
                "ActionDenied(reason='no_principal') outside context()",
                "an action proposed with no principal ran",
            )
            reason = getattr(refusal, "reason", "")
            _expect(
                reason == "no_principal",
                "ActionDenied(reason='no_principal')",
                f"ActionDenied(reason={reason!r})",
            )
            _expect(
                executor.calls == 0,
                "the executor is not reached",
                f"the executor was called {executor.calls} times",
            )
            _expect(
                store.receipts() == () and store.events() == (),
                "no receipt and no event are written",
                f"{len(store.receipts())} receipts and {len(store.events())} events exist",
            )
            with context(selection.principal.agent, selection.principal.user):
                try:
                    call(**arguments)
                except Exception as pending:
                    from ..errors import ApprovalRequired

                    if not isinstance(pending, ApprovalRequired):
                        raise
                    # §3.5 — verify grants its own approval, through the call
                    # `ctrlrun approve` makes, and the report says so.
                    detail["approved_by_verify"] = True
                    store.grant_approval(pending.request_id, APPROVER)
                    from ..control import with_approval

                    with with_approval(pending.request_id):
                        call(**arguments)
            _expect_control(
                executor.calls == 1,
                "the identical call inside context() runs",
                f"the executor was called {executor.calls} times inside context()",
            )

        try:
            return self.graded("G7", selection, store, recorder, body)
        finally:
            store.close()

    # --- G8: an expired grant refuses an action it would otherwise permit ----------------

    def _expiry_is_decisive(self, selection: _Selection) -> bool:
        """Does this grant's expiry actually decide the action, or does another grant cover it?

        Asked before the scenario runs, against a throwaway in-memory store, so a layered
        configuration reports N/A rather than a failure that is really a property of the
        document (§2.2's N/A rule, applied one level down).

        It asks only **whether** authority still passes, never **why** it stopped. The reason
        is the guarantee's own observable, and a pre-check that filtered on it would turn a
        kernel denying for the wrong cause into an N/A — a false N/A, which §9.2 calls a false
        pass wearing the other costume.
        """
        grant = selection.grant
        if grant is None or grant.expires_at is None or self.authority is None:
            return False
        store = InMemoryStateStore()
        try:
            result = self.authority.evaluate(
                selection.build(), now=grant.expires_at + _ONE_MICROSECOND, store=store
            )
        finally:
            store.close()
        return not result.passed

    def g8(self) -> GuaranteeResult:
        if self.authority is None:
            return self.na("G8", reg.NO_AUTHORITY_SECTION)
        if not any(grant.expires_at is not None for grant in self.authority.grants.values()):
            return self.na("G8", reg.NO_EXPIRES_AT)
        excluded: set[str] = set()
        selection: _Selection | None = None
        matched_any = False
        for _ in range(len(self.authority.grants) + 1):
            candidate = self.select(
                grant_filter=lambda grant: grant.expires_at is not None and grant.id not in excluded
            )
            if candidate is None:
                break
            matched_any = True
            if self._expiry_is_decisive(candidate):
                selection = candidate
                break
            excluded.add(str(candidate.grant.id if candidate.grant else ""))
        if selection is None:
            return self.na("G8", EXPIRY_NOT_DECISIVE if matched_any else reg.NO_GRANT_MATCHES)
        grant = selection.grant
        assert grant is not None
        expires_at = grant.expires_at
        assert expires_at is not None
        clock = _Clock(expires_at)
        control, store, recorder, _ = self._control_for("G8", selection, clock=clock)

        def body(detail: dict[str, Any]) -> None:
            detail["grant_id"] = grant.id
            detail["expires_at"] = iso_timestamp(expires_at)
            # The before-expiry half **is** the control, and it is why the two halves are one
            # scenario: a kernel that denied everything would satisfy the after half alone.
            action = selection.build()
            executor = _Executor()
            receipt = self.execute(
                control,
                action,
                executor,
                selection.effect_key,
                self.approve(control, store, action, selection),
            )
            _expect_control(
                receipt.result is ReceiptResult.COMMITTED and executor.calls == 1,
                "at expires_at exactly, the action passes authority and runs",
                f"it ended {receipt.result} after {executor.calls} executor calls",
            )
            _expect_control(
                _named_event(recorder, EventType.AUTHORITY_RESOLVED, grant_id=grant.id),
                f"AUTHORITY_RESOLVED naming {grant.id}",
                f"events were {recorder.types()}",
            )
            clock.at(expires_at + _ONE_MICROSECOND)
            later = selection.build()
            after_executor = _Executor()
            refusal = self.refused(
                lambda: self.execute(control, later, after_executor, selection.effect_key, None),
                (AuthorityDenied,),
                "AuthorityDenied(reason='authority_expired') one microsecond later",
                "the action still ran after its grant expired",
            )
            reason = getattr(refusal, "reason", "")
            _expect(
                reason == AUTHORITY_EXPIRED,
                "AuthorityDenied(reason='authority_expired')",
                f"AuthorityDenied(reason={reason!r})",
            )
            _expect(
                after_executor.calls == 0,
                "the executor is not reached after the grant expires",
                f"the executor was called {after_executor.calls} times",
            )
            denied_receipt = _last_receipt(store, later.action_id)
            _expect(
                denied_receipt is not None and denied_receipt.result is ReceiptResult.DENIED,
                "a `denied` receipt is written",
                f"the receipt is {None if denied_receipt is None else denied_receipt.result}",
            )

        try:
            return self.graded("G8", selection, store, recorder, body)
        finally:
            store.close()

    # --- G9: a delegation cannot widen its parent ----------------------------------------

    def g9(self) -> GuaranteeResult:
        if self.authority is None:
            return self.na("G9", reg.NO_AUTHORITY_SECTION)
        delegable = [
            self.authority.grants[grant_id]
            for grant_id in sorted(self.authority.grants)
            if self.authority.grants[grant_id].delegable
        ]
        if not delegable:
            return self.na("G9", reg.NO_DELEGABLE_GRANT)
        parent = delegable[0]
        selection = self.select(grant_filter=lambda grant: grant.id == parent.id)
        if selection is None:
            return self.na("G9", reg.NO_GRANT_MATCHES)
        control, store, recorder, _ = self._control_for("G9", selection)

        def body(detail: dict[str, Any]) -> None:
            detail["grant_id"] = parent.id
            by = _principal_for(parent.subject)
            narrowed, child_principal, tightened = _narrow(parent, selection)
            detail["constraints_tightened"] = tightened
            delegation = control.delegate(parent.id, narrowed, by=by)
            _expect_control(
                store.get_delegation(delegation.delegation_id) is not None,
                "a child narrowed on every dimension the parent constrains is accepted",
                "the delegation row was not written",
            )
            _expect_control(
                _named_event(
                    recorder,
                    EventType.DELEGATION_CREATED,
                    delegation_id=delegation.delegation_id,
                ),
                "DELEGATION_CREATED reaches a registered sink",
                f"events were {recorder.types()}",
            )
            isolated = not parent.subject.matches(child_principal)
            detail["delegation_isolated"] = isolated
            child_action = Action(
                name=selection.action,
                arguments=dict(selection.arguments),
                principal=child_principal,
                resource=selection.resource,
                environment=selection.environment,
            )
            executor = _Executor()
            child_key = (
                None
                if selection.effect_key is None
                else f"{selection.effect_key}-{reg.SYNTHETIC_PREFIX}-child"
            )
            approval_id = self.approve(control, store, child_action, selection)
            receipt = self.execute(control, child_action, executor, child_key, approval_id)
            _expect_control(
                receipt.result is ReceiptResult.COMMITTED,
                "an action within the child's limits passes authority",
                f"it ended {receipt.result}",
            )
            if isolated:
                # Where the parent's subject does not match the child's, the delegation is
                # the *only* authority for this action, so the event names it. Where it does
                # — a parent addressed to `agent: "*"` — `v0.3 §4.6` picks the lowest grant
                # id among those that passed, and naming the delegation would be asserting
                # something the model does not promise.
                _expect_control(
                    _named_event(
                        recorder,
                        EventType.AUTHORITY_RESOLVED,
                        delegation_id=delegation.delegation_id,
                    ),
                    f"AUTHORITY_RESOLVED naming {delegation.delegation_id}",
                    f"events were {recorder.types()}",
                )

            exercised: list[str] = []
            unconstrained: list[str] = []
            omissions: dict[str, str] = {}
            for dimension in DIMENSIONS:
                widened = _widen(parent, narrowed, dimension)
                if widened is None or contained_dimension(parent, widened) is None:
                    unconstrained.append(dimension)
                    continue
                offending = contained_dimension(parent, widened)
                if offending != dimension:
                    raise VerifyInternalError(
                        f"G9: the widened child for {dimension!r} fails containment on "
                        f"{offending!r} instead (SPEC-v0.4 §3.4)"
                    )
                before = len(store.delegations(include_revoked=True))
                self._refuse_delegation(
                    control, store, recorder, parent, widened, dimension, before
                )
                omissions[dimension] = self._refuse_omission(
                    control, store, recorder, parent, narrowed, dimension
                )
                exercised.append(dimension)
            _expect(
                bool(exercised),
                "at least one dimension is exercised",
                "the parent constrains no dimension a child could widen",
            )
            detail["dimensions_exercised"] = exercised
            detail["dimensions_unconstrained"] = unconstrained
            detail["omissions"] = omissions
            detail["summary"] = f"{len(exercised)} of {len(DIMENSIONS)} dimensions"

        try:
            return self.graded("G9", selection, store, recorder, body)
        finally:
            store.close()

    def _refuse_delegation(
        self,
        control: Control,
        store: SQLiteStateStore,
        recorder: _Recorder,
        parent: Grant,
        child: Grant,
        dimension: str,
        before: int,
    ) -> None:
        rejected = len(
            [event for event in recorder.events if event.type is EventType.DELEGATION_REJECTED]
        )
        escalation = self.refused(
            lambda: control.delegate(parent.id, child, by=_principal_for(parent.subject)),
            (AuthorityEscalation,),
            f"AuthorityEscalation(reason='containment', dimension={dimension!r})",
            f"a child widened on {dimension} was accepted",
        )
        _expect(
            getattr(escalation, "reason", "") == CONTAINMENT
            and getattr(escalation, "dimension", None) == dimension,
            f"AuthorityEscalation(reason='containment', dimension={dimension!r})",
            f"AuthorityEscalation(reason={getattr(escalation, 'reason', None)!r}, "
            f"dimension={getattr(escalation, 'dimension', None)!r})",
        )
        now = len(
            [event for event in recorder.events if event.type is EventType.DELEGATION_REJECTED]
        )
        _expect(
            now == rejected + 1,
            "exactly one DELEGATION_REJECTED is appended",
            f"{now - rejected} were appended",
        )
        _expect(
            len(store.delegations(include_revoked=True)) == before,
            "no delegation row is written for the child that was refused",
            "a delegation row was written",
        )

    def _refuse_omission(
        self,
        control: Control,
        store: SQLiteStateStore,
        recorder: _Recorder,
        parent: Grant,
        narrowed: Grant,
        dimension: str,
    ) -> str:
        """The omission half: a child that **drops** a dimension its parent constrains.

        Two shapes, and both are the guarantee: `actions: []` and a subject with neither an
        agent nor a user are refused by the model at construction, one layer below
        containment; everything else reaches `Control.delegate` and is refused there. Omission
        never means unlimited, whichever layer says so.
        """
        try:
            omitting = _omit(narrowed, parent, dimension)
        except InvalidArgument:
            return "refused at construction"
        if omitting is None:
            return "the parent leaves nothing to omit on this dimension"
        before = len(store.delegations(include_revoked=True))
        self._refuse_delegation(control, store, recorder, parent, omitting, dimension, before)
        return "refused by containment"

    # --- G10: an unknown exception is an AMBIGUOUS outcome, never FAILED ------------------

    def g10(self) -> GuaranteeResult:
        selection = self.select()
        if selection is None:
            return self.na("G10", reg.EVERY_ACTION_DENIED)
        control, store, recorder, _ = self._control_for("G10", selection)

        rows: tuple[tuple[str, BaseException, ReceiptResult, EffectState], ...] = (
            (
                "timeout",
                TimeoutError("ctrlrun-verify: the request timed out"),
                ReceiptResult.AMBIGUOUS,
                EffectState.AMBIGUOUS,
            ),
            (
                "runtime_error",
                RuntimeError("ctrlrun-verify: the executor raised"),
                ReceiptResult.AMBIGUOUS,
                EffectState.AMBIGUOUS,
            ),
            # The row that must not be dropped: `NotExecuted` is the control, and the
            # guarantee is the asymmetry, so both directions are asserted or neither is.
            (
                "not_executed",
                NotExecuted("ctrlrun-verify: the remote did nothing"),
                ReceiptResult.FAILED,
                EffectState.FAILED,
            ),
        )

        def body(detail: dict[str, Any]) -> None:
            observed: dict[str, str] = {}
            for label, exception, expected_result, expected_state in rows:
                action = selection.build()
                key = (
                    None
                    if selection.effect_key is None
                    else f"{selection.effect_key}-{reg.SYNTHETIC_PREFIX}-{label}"
                )
                executor = _Executor(_raises(exception))
                try:
                    self.execute(
                        control,
                        action,
                        executor,
                        key,
                        self.approve(control, store, action, selection),
                    )
                except (VerifyRefused, VerifyInternalError):
                    raise
                except Exception:  # every row raises by design; the receipt is the observable
                    # `v0.1 §5.5` propagates the executor's own exception, whatever it is.
                    # What this row asserts is the *mapping*, which lives on the receipt and
                    # on the effect record, so the exception's own type is not re-asserted
                    # here — a check that did would go red for a kernel that mapped
                    # correctly and merely wrapped.
                    pass
                receipt = _last_receipt(store, action.action_id)
                observed[label] = "" if receipt is None else str(receipt.result)
                is_control = label == "not_executed"
                check = _expect_control if is_control else _expect
                expectation = f"{type(exception).__name__} produces a {expected_result} receipt"
                check(
                    receipt is not None and receipt.result is expected_result,
                    expectation,
                    f"{type(exception).__name__} produced "
                    f"{None if receipt is None else receipt.result}",
                )
                if key is not None:
                    record = store.get_effect(key)
                    check(
                        record is not None and record.state is expected_state,
                        f"{type(exception).__name__} leaves the record {expected_state}",
                        f"the record is {None if record is None else record.state}",
                    )
            detail["rows"] = observed

        try:
            return self.graded("G10", selection, store, recorder, body)
        finally:
            store.close()


def run_attempts(payloads: Sequence[str]) -> None:
    """Launch one OS process per payload and wait for all of them (§2.2 G4).

    `python -m ctrlrun.verify.worker`, payload on stdin. Not `multiprocessing`: `spawn`
    re-imports the caller's `__main__` in every child, so a caller who ran `verify.run()` from
    an unguarded script would fork-bomb itself, and requiring an `if __name__ == "__main__"`
    guard in the program under verification is not a trade a verification tool gets to make.

    Bounded, so a child that wedges fails red rather than hanging CI: a timeout is not a test
    failure (§3.6).
    """
    started: list[subprocess.Popen[bytes]] = []
    for payload in payloads:
        process = subprocess.Popen(
            [sys.executable, "-m", "ctrlrun.verify.worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        assert process.stdin is not None
        process.stdin.write(payload.encode("utf-8"))
        process.stdin.close()
        started.append(process)
    for process in started:
        try:
            process.wait(timeout=_CHILD_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:  # pragma: no cover - only on a wedged child
            process.kill()
            process.wait(timeout=10)


def _base_instant(authority: Authority | None) -> datetime:
    """§3.6 — `T0`, derived from the document so two runs agree.

    The earliest `expires_at` among the grants minus one hour, so every grant is live when a
    scenario needs one; `2026-01-01T00:00:00Z` where no grant carries an expiry.
    """
    if authority is None:
        return FALLBACK_T0
    expiries = [
        grant.expires_at for grant in authority.grants.values() if grant.expires_at is not None
    ]
    if not expiries:
        return FALLBACK_T0
    return min(expiries) - _ONE_HOUR


# --- evidence for a counterexample (§4.5) -----------------------------------------------


def _effect_dict(record: EffectRecord) -> dict[str, Any]:
    return {
        "effect_key": record.effect_key,
        "state": str(record.state),
        "action_id": record.action_id,
        "attempt": record.attempt,
        "created_at": iso_timestamp(record.created_at),
        "updated_at": iso_timestamp(record.updated_at),
        "error": record.error,
    }


def counterexample(
    store: SQLiteStateStore, recorder: _Recorder, expected: str, observed: str
) -> Counterexample:
    """The ordered evidence for one scenario, in `event_id` order (§4.5)."""
    return Counterexample(
        expected=expected,
        observed=observed,
        receipts=tuple(receipt.to_dict() for receipt in store.receipts()),
        events=tuple(event.to_dict() for event in store.events()),
        effects=tuple(_effect_dict(record) for record in store.list_effects()),
    )


__all__ = [
    "APPROVER",
    "EXPIRY_NOT_DECISIVE",
    "SQLITE_STORE_URL",
    "Engine",
    "VerifyInternalError",
    "VerifyRefused",
    "_Clock",
    "_ControlFailed",
    "_Executor",
    "_Recorder",
    "_Selection",
    "_Violation",
    "counterexample",
    "load",
    "run_attempts",
]


# --- the scenarios (§2.2) ----------------------------------------------------------------
#
# Each is written the same way round: the *control* establishes that the observable would have
# been visible had the guard not fired, and the refusal is asserted **by name** — the reason,
# the receipt field, the event type — never by exception class alone (§2.1, §8). Five refusals
# that all raise `AuthorityDenied` are five guards a check asserting only the type cannot tell
# apart.


def _expect(held: bool, expected: str, observed: str) -> None:
    """The guarantee's own refusal did not happen, or not for the stated reason."""
    if not held:
        raise _Violation(expected, observed)


def _expect_control(held: bool, expected: str, observed: str) -> None:
    """§1.3 — a control that does not behave as specified is FAIL, never PASS, never N/A."""
    if not held:
        raise _ControlFailed(observed, expected)


def _named_event(recorder: _Recorder, type_: EventType, **data: Any) -> bool:
    return any(
        event.type is type_ and all(event.data.get(key) == value for key, value in data.items())
        for event in recorder.events
    )


def _last_receipt(store: SQLiteStateStore, action_id: str) -> Receipt | None:
    for receipt in reversed(store.receipts()):
        if receipt.action_id == action_id:
            return receipt
    return None


# --- deriving a child grant, dimension by dimension (§3.4) -------------------------------


def _tightened(condition: Condition) -> Condition | None:
    """One numeric bound moved inward. `eq` and `in` are left as the parent's (§3.4)."""
    if not _is_int(condition.operand):
        return None
    if condition.op in ("gte", "gt"):
        return replace(condition, operand=condition.operand + 1)
    if condition.op in ("lte", "lt"):
        return replace(condition, operand=condition.operand - 1)
    return None


def _loosened(condition: Condition) -> Condition:
    """One dimension moved outward — the widened child of §3.4's table."""
    if condition.op in ("gte", "gt") and _is_int(condition.operand):
        return replace(condition, operand=condition.operand - 1)
    if condition.op in ("lte", "lt") and _is_int(condition.operand):
        return replace(condition, operand=condition.operand + 1)
    if condition.op == "in":
        first = condition.operand[0] if condition.operand else APPROVER
        return replace(condition, operand=(*condition.operand, _negate_value(first)))
    return replace(condition, operand=_negate_value(condition.operand))


def _narrow(parent: Grant, selection: _Selection) -> tuple[Grant, Principal, dict[str, str]]:
    """The narrowed child of §3.4 — G9's positive control.

    Every dimension is narrowed to the *thing under test*: the action the scenario runs, the
    resource it names, the environment it runs in. That keeps the control runnable, which is
    the point of a control — a child narrowed to a literal nothing satisfies would be accepted
    and then prove nothing.

    The child's subject names an agent the parent's does not match, where it can. Containment
    permits it (`v0.3 §5.4` leaves which concrete agent a child names unconstrained — that is
    what delegation is for), and it is what lets the control assert that authority passed *via
    the delegation* rather than via the parent that also covers the action.
    """
    agent = f"{reg.SYNTHETIC_PREFIX}-delegate"
    user = _from_pattern(parent.subject.user)
    subject = Subject(agent=agent, user=user)
    child_principal = Principal(agent=agent, user=user)
    action = Action(
        name=selection.action,
        arguments=dict(selection.arguments),
        principal=child_principal,
        resource=selection.resource,
        environment=selection.environment,
    )
    constraints: dict[str, Condition] = dict(parent.constraints)
    tightened: dict[str, str] = {}
    for key, condition in parent.constraints.items():
        candidate = _tightened(condition)
        if candidate is None:
            continue
        trial = dict(constraints)
        trial[key] = candidate
        probe = replace(parent, id="", constraints=trial, delegable=False)
        if probe.constraints_hold(action) and contained_dimension(parent, probe) is None:
            constraints[key] = candidate
            tightened[key] = f"{condition.operand} -> {candidate.operand}"
    child = Grant(
        id="",
        subject=subject,
        actions=(selection.action,),
        resources=None if parent.resources is None else (str(selection.resource),),
        constraints=constraints,
        environments=None if parent.environments is None else (selection.environment,),
        expires_at=None if parent.expires_at is None else parent.expires_at - _ONE_HOUR,
        delegable=False,
    )
    offending = contained_dimension(parent, child)
    if offending is not None:
        raise VerifyInternalError(
            f"G9: the narrowed child is not contained in {parent.id!r} on {offending!r}; "
            "the control cannot be built (SPEC-v0.4 §3.4)"
        )
    return child, child_principal, tightened


def _widen(parent: Grant, narrowed: Grant, dimension: str) -> Grant | None:
    """The narrowed child, widened on exactly one dimension. `None` where there is nothing
    to widen, which is how a dimension the parent does not constrain is reported unexercised
    rather than silently counted as covered (§2.2 G9)."""
    if dimension == "subject":
        return replace(narrowed, subject=Subject(agent=WILDCARD, user=narrowed.subject.user))
    if dimension == "actions":
        return replace(narrowed, actions=(DEEP_WILDCARD,))
    if dimension == "resources":
        if parent.resources is None:
            return None
        return replace(narrowed, resources=(DEEP_WILDCARD,))
    if dimension == "constraints":
        if not parent.constraints:
            return None
        return replace(
            narrowed,
            constraints={key: _loosened(value) for key, value in parent.constraints.items()},
        )
    if dimension == "environments":
        if parent.environments is None:
            return None
        return replace(
            narrowed,
            environments=(*parent.environments, f"{reg.SYNTHETIC_PREFIX}-env"),
        )
    if dimension == "expires_at":
        if parent.expires_at is None:
            return None
        return replace(narrowed, expires_at=parent.expires_at + _ONE_HOUR)
    raise VerifyInternalError(f"G9: unknown containment dimension {dimension!r}")


def _omit(narrowed: Grant, parent: Grant, dimension: str) -> Grant | None:
    """The narrowed child with one dimension **dropped**. May raise `InvalidArgument`.

    `actions: []` and a subject with neither an agent nor a user are refused by the model at
    construction, one layer below containment. That refusal is the guarantee too: omission
    never means unlimited, whichever layer says so.
    """
    if dimension == "subject":
        if parent.subject.user is None:
            return None
        return replace(narrowed, subject=Subject(agent=narrowed.subject.agent))
    if dimension == "actions":
        return replace(narrowed, actions=())
    if dimension == "resources":
        return None if parent.resources is None else replace(narrowed, resources=None)
    if dimension == "constraints":
        return None if not parent.constraints else replace(narrowed, constraints={})
    if dimension == "environments":
        return None if parent.environments is None else replace(narrowed, environments=None)
    if dimension == "expires_at":
        return None if parent.expires_at is None else replace(narrowed, expires_at=None)
    raise VerifyInternalError(f"G9: unknown containment dimension {dimension!r}")
