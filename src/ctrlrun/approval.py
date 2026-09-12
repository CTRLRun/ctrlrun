"""Approval requests, grants and providers. Build-list item 4; SPEC-v0.1 §4.

An approval authorizes one exact action: it carries the `action_hash` of what a human saw,
it can be used once, and it expires. Everything here exists to make those three properties
hard to lose. The store performs the state transitions (they must be atomic); this module
owns the models, the reason vocabulary, and the two providers that ask a human.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Final, Protocol, runtime_checkable

from .action import Action, Principal, canonical_bytes
from .errors import (
    ActionDenied,
    ApprovalMismatch,
    ApprovalTimeout,
    CTRLRunError,
    InvalidArgument,
)
from .identity import IdentityContext, IdentityProvider

_LOG = logging.getLogger("ctrlrun")

#: SPEC-v0.1 §4.1 — an approval request lives for fifteen minutes unless told otherwise.
DEFAULT_APPROVAL_TTL: Final = timedelta(minutes=15)

DEFAULT_POLL_INTERVAL: Final = timedelta(seconds=0.5)

#: `ApprovalMismatch.reason` values that are not simply the record's status.
UNKNOWN_APPROVAL: Final = "unknown"
HASH_MISMATCH: Final = "mismatch"

#: `ActionDenied.reason` when the human said no.
APPROVAL_DENIED: Final = "approval_denied"

#: "apr_" + 32 hex chars. 128 bits, not because anything in v0.1 can be attacked by guessing
#: an approval id — consuming one needs write access to the store, which is game over anyway —
#: but because a remote approval provider (v0.2, webhooks) turns this id into a bearer token,
#: and an id format is not a thing you get to widen later without breaking every stored record.
_ID_HEX_BYTES: Final = 16


def _utc_now() -> datetime:
    return datetime.now(UTC)


def new_request_id() -> str:
    return f"apr_{secrets.token_hex(_ID_HEX_BYTES)}"


def _require_aware(moment: datetime, field: str) -> None:
    # SPEC: §4 — expiry is a comparison, and comparing a naive datetime to an aware one
    # raises at the worst possible moment. Reject naive input instead.
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise InvalidArgument(f"{field} must be timezone-aware, got {moment!r}")


class ApprovalStatus(StrEnum):
    """The status carried by a stored approval record (SPEC-v0.1 §4.1).

    `StrEnum`, so a status renders as its value in events and CLI output (§6.1), and so a
    refusal reason can simply be the status the record was in.
    """

    PENDING = "pending"
    GRANTED = "granted"
    DENIED = "denied"
    EXPIRED = "expired"
    CONSUMED = "consumed"


#: SPEC-v0.8 §2.7, §4.1: the reasons an approver refusal carries. Values of the existing
#: `ApprovalMismatch.reason` field, because four refusals sharing a type is why every test
#: asserts the reason and never the type alone.
APPROVER_UNVERIFIED: Final = "approver_unverified"
APPROVER_IS_REQUESTER: Final = "approver_is_requester"


@dataclass(frozen=True)
class VerifiedApprover:
    """Who answered, as the surface that took the answer verified them (SPEC-v0.8 §2.5).

    **No claim value is here and none ever will be.** `v0.3 §2.4`'s rule is that evidence
    carries claim *names* where values are withheld, and what an entitlement decision means is
    *which control this approver satisfied*, which is what `entitled` says. A row holding the
    role value would put an identity provider's payload in an evidence table for no gain.

    `entitled` is filled by item 3 and is empty until then.
    """

    agent: str
    user: str | None
    issuer: str | None
    granted_at: datetime
    entitled: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.agent:
            raise InvalidArgument("a verified approver must carry a non-empty agent")
        _require_aware(self.granted_at, "verified approver granted_at")
        object.__setattr__(self, "entitled", tuple(self.entitled))

    @property
    def principal(self) -> tuple[str, str | None]:
        """What `§4.1` compares: agent and user, and nothing else."""
        return (self.agent, self.user)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "user": self.user,
            "issuer": self.issuer,
            "entitled": list(self.entitled),
            "granted_at": self.granted_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> VerifiedApprover:
        return cls(
            agent=str(document["agent"]),
            user=document.get("user"),
            issuer=document.get("issuer"),
            granted_at=datetime.fromisoformat(str(document["granted_at"])),
            entitled=tuple(document.get("entitled") or ()),
        )


@dataclass(frozen=True)
class ApproverIdentity:
    """How a deployment verifies who answered an approval (SPEC-v0.8 §2.3).

    **Opt in, then fail closed.** A `Control` built without one behaves exactly as 0.7.0 did;
    one built with it refuses any approval whose row carries no `VerifiedApprover`, wherever
    that approval came from and whatever the store did with the column.

    A second instance of `v0.3`'s `IdentityProvider` and never the agent's: the agent's provider
    reads what a proxy set for the agent, and a deployment where one object answers both doors
    is one where the agent's own token can grant the agent's own approvals.

    `roles_claim` names the claim an issuer puts roles in. It is item 3's, and nothing reads it
    yet; it lives here because a provider without it cannot answer §3, so a deployment that sets
    one and forgets the other would have a silent half-check.
    """

    provider: IdentityProvider
    roles_claim: str | None = None

    def __post_init__(self) -> None:
        if self.roles_claim is not None and not self.roles_claim.strip():
            raise InvalidArgument("roles_claim must be a non-empty string or None")
        if type(self.provider).__name__ == "StaticIdentityProvider":
            # §2.3: a warning and not a refusal: a single-operator deployment where the shell
            # genuinely is the human is real, and the record it produces is true. What is not
            # true is that such a record distinguishes anybody, and an operator who has not
            # thought about that should read it here rather than discover it in an audit.
            _LOG.warning(
                "the approver identity uses StaticIdentityProvider, which answers with one "
                "name for every request: every approval it verifies will carry an identical "
                "approver, and only self-approval can still be told apart (SPEC-v0.8 §2.3)"
            )

    def resolve(self, context: IdentityContext) -> Principal | None:
        """The principal this door's provider verifies, or `None` where it declines.

        A decline is not backfilled from anything the calling code said: there is no `context()`
        on this door to fall back to, and falling back would turn "nobody proved who this was"
        into an approval (`v0.3 §3.2`).
        """
        return self.provider.resolve(context)


#: SPEC-v0.8 §2.5: how a surface that resolved an approver hands that principal to the store
#: call that records it. **Package-internal on purpose** (§2.5.1): a public one would be an
#: unauthenticated way to assert a verified approver, which is `trust_approver` spelled as a
#: context manager. The shipped surfaces are its only callers, and the residual is stated in
#: §2.5.1 rather than hidden: anything inside the application's own process can call a private
#: function, so the kernel's claim is about what the shipped surfaces record.
_GRANTING_PRINCIPAL: ContextVar[tuple[Principal, tuple[str, ...]] | None] = ContextVar(
    "ctrlrun_granting_principal", default=None
)


@contextmanager
def _granting_principal(principal: Principal, *, entitled: Iterable[str] = ()) -> Iterator[None]:
    """Record `principal` as the verified approver of any grant made inside this block."""
    token = _GRANTING_PRINCIPAL.set((principal, tuple(entitled)))
    try:
        yield
    finally:
        _GRANTING_PRINCIPAL.reset(token)


def verified_approver_now(now: datetime) -> VerifiedApprover | None:
    """The verified approver a store should record for a grant taken at `now`, if any.

    Read by the shipped stores inside `grant_approval` and `deny_approval`. A store that does
    not read it records nothing, and `Control` then refuses every approval it granted, which is
    the fail-closed direction and the reason the check lives at consumption (§2.4).
    """
    found = _GRANTING_PRINCIPAL.get(None)
    if found is None:
        return None
    principal, entitled = found
    return VerifiedApprover(
        agent=principal.agent,
        user=principal.user,
        issuer=principal.issuer,
        granted_at=now,
        entitled=entitled,
    )


@dataclass(frozen=True)
class ApprovalRequest:
    """A pending question for a human: may this exact action run? (SPEC-v0.1 §4.1)"""

    request_id: str
    action_hash: str
    action: Action
    created_at: datetime
    expires_at: datetime
    #: SPEC-v0.6 §7.1 — the `policy_hash` in force when this request was created, or `None`.
    #:
    #: **At request time, not at grant time**, and §7.1 says why: the store has no policy, and
    #: giving `ctrlrun approve` one would make a malformed policy a failure of an evidence
    #: command -- the same argument §9.4 makes for `receipts` and `inspect` not loading one.
    #: The two instants differ only if the policy changed between the request and the answer,
    #: which is a narrower window than the one §7.2 is about and is recorded by the receipt's
    #: own `policy_hash` either way.
    policy_hash: str | None = None
    #: SPEC-v0.7 §6.2: the precondition fingerprint captured when this request was created, or
    #: `None` where the call that created it named no provider. A hash and never the state it
    #: was computed from (§6.10): `"sha256:"` over the canonical form of what the operator's
    #: provider returned, under the `ctrlrun.precondition/v1` domain tag.
    #:
    #: Captured at request time for `policy_hash`'s reason (`v0.6 §7.1`): the store has no
    #: provider, and giving `ctrlrun approve` one would make an unreachable resource a failure
    #: of the command a human answers with. `Control.execute` rechecks it on the presenting
    #: pass, strictly before the store call that consumes the approval, and refuses where the
    #: two differ or where only one side has one. That recheck **narrows** the window between
    #: the human's decision and the effect; a change landing after the comparison and before
    #: the reservation is not refused (§6.7).
    precondition_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if not self.request_id:
            raise InvalidArgument("approval request_id must be a non-empty string")
        if not self.action_hash:
            raise InvalidArgument("approval action_hash must be a non-empty string")
        _require_aware(self.created_at, "approval created_at")
        _require_aware(self.expires_at, "approval expires_at")
        if self.expires_at <= self.created_at:
            raise InvalidArgument(
                f"approval {self.request_id} expires at or before it was created; "
                "a request nobody can answer is not a request"
            )


@dataclass(frozen=True)
class Approval:
    """A human's grant, bound to one `action_hash` (SPEC-v0.1 §4.1).

    `approval_id == request_id` in v0.1: a request produces at most one approval.
    """

    approval_id: str
    action_hash: str
    approver: str
    granted_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if not self.approval_id:
            raise InvalidArgument("approval_id must be a non-empty string")
        if not self.action_hash:
            raise InvalidArgument("approval action_hash must be a non-empty string")
        if not self.approver:
            raise InvalidArgument("approver must be a non-empty string")
        _require_aware(self.granted_at, "approval granted_at")
        _require_aware(self.expires_at, "approval expires_at")


@dataclass(frozen=True)
class ApprovalRecord:
    """What the StateStore holds for a request: the request plus its status (SPEC §4.1)."""

    request: ApprovalRequest
    status: ApprovalStatus
    approver: str | None = None
    granted_at: datetime | None = None
    consumed_at: datetime | None = None
    #: SPEC-v0.8 §2.5: every approver a resolving surface verified, in the order they answered.
    #: Empty where the surface could not resolve one, which `Control` refuses at consumption
    #: wherever an `ApproverIdentity` is configured (§2.7). Item 4 makes this list longer than
    #: one; until then a granted record carries nought or one.
    approvers: tuple[VerifiedApprover, ...] = ()

    @property
    def approval_id(self) -> str:
        return self.request.request_id

    @property
    def policy_hash_at_approval(self) -> str | None:
        """Which policy was in force when this approval was created (SPEC-v0.6 §7.1).

        Where it differs from the receipt's `policy_hash`, the policy changed between the grant
        and its consumption -- which is what §7.2's table is about, and which no other field can
        say: the approval binds to an `action_hash`, and that is silent about the policy.
        """
        return self.request.policy_hash

    @property
    def action_hash(self) -> str:
        return self.request.action_hash

    @property
    def expires_at(self) -> datetime:
        return self.request.expires_at

    def as_approval(self) -> Approval:
        """The `Approval` this record stands for. Only a granted record has one."""
        if self.approver is None or self.granted_at is None:
            raise ApprovalMismatch(
                f"approval {self.approval_id} was never granted",
                reason=str(self.status),
                approval_id=self.approval_id,
            )
        return Approval(
            approval_id=self.approval_id,
            action_hash=self.action_hash,
            approver=self.approver,
            granted_at=self.granted_at,
            expires_at=self.expires_at,
        )


@dataclass(frozen=True)
class ApprovalVerdict:
    """What a store must do about one approval it was handed (SPEC-v0.1 §4.2).

    Exactly one of `record` and `refusal` is set. `expire` means the record must first be
    marked `expired`, and that write kept even though the approval is refused: a lapsed
    approval is evidence, not something to roll back.
    """

    record: ApprovalRecord | None = None
    refusal: CTRLRunError | None = None
    expire: bool = False


def check_consumable(
    record: ApprovalRecord | None, approval_id: str, action_hash: str, now: datetime
) -> ApprovalVerdict:
    """Decide whether a presented approval authorizes this action (SPEC-v0.1 §4.2).

    Pure: it reads a record and returns a verdict, so every store applies the same rules and
    performs the same writes. The hash is checked *before* the status, so a mutated action
    leaves the approval untouched and still grantable for the action the human actually saw
    (A1, acceptance test T2).
    """
    if record is None:
        return ApprovalVerdict(
            refusal=ApprovalMismatch(
                f"no approval {approval_id}", reason=UNKNOWN_APPROVAL, approval_id=approval_id
            )
        )
    if record.action_hash != action_hash:
        return ApprovalVerdict(
            refusal=ApprovalMismatch(
                f"approval {approval_id} authorizes {record.action_hash}, not {action_hash}",
                reason=HASH_MISMATCH,
                approval_id=approval_id,
            )
        )
    if record.status is ApprovalStatus.DENIED:
        return ApprovalVerdict(
            refusal=ActionDenied(
                f"approval {approval_id} was denied by {record.approver}",
                reason=APPROVAL_DENIED,
            )
        )
    if record.status is not ApprovalStatus.GRANTED:
        return ApprovalVerdict(
            refusal=ApprovalMismatch(
                f"approval {approval_id} is {record.status}, not granted",
                reason=str(record.status),
                approval_id=approval_id,
            )
        )
    if now > record.expires_at:
        # A3: expiry is checked here, at consumption, not only at grant time.
        return ApprovalVerdict(refusal=_expired(record, approval_id), expire=True)
    return ApprovalVerdict(record=record)


def check_answerable(
    record: ApprovalRecord | None, approval_id: str, now: datetime
) -> ApprovalVerdict:
    """Decide whether a request may still be granted or denied (SPEC-v0.1 §4.1)."""
    if record is None:
        return ApprovalVerdict(
            refusal=ApprovalMismatch(
                f"no approval request {approval_id}",
                reason=UNKNOWN_APPROVAL,
                approval_id=approval_id,
            )
        )
    if record.status is not ApprovalStatus.PENDING:
        return ApprovalVerdict(
            refusal=ApprovalMismatch(
                f"approval request {approval_id} is already {record.status}",
                reason=str(record.status),
                approval_id=approval_id,
            )
        )
    if now > record.expires_at:
        return ApprovalVerdict(refusal=_expired(record, approval_id), expire=True)
    return ApprovalVerdict(record=record)


def _expired(record: ApprovalRecord, approval_id: str) -> ApprovalMismatch:
    return ApprovalMismatch(
        f"approval {approval_id} expired at {record.expires_at.isoformat()}",
        reason=str(ApprovalStatus.EXPIRED),
        approval_id=approval_id,
    )


class ApprovalStore(Protocol):
    """The approval half of the `StateStore` protocol (SPEC-v0.1 §5.3).

    Split out so approval providers depend on the slice they use, and so `state.py` — which
    implements it — can be the only module that owns transitions.
    """

    def put_approval_request(self, request: ApprovalRequest) -> None:
        """Record a new request as `pending`. A reused `request_id` is an error."""
        ...

    def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        """The stored record, or `None` if there is no such approval."""
        ...

    def grant_approval(self, approval_id: str, approver: str) -> Approval:
        """Move `pending → granted`. Anything else raises `ApprovalMismatch`."""
        ...

    def deny_approval(self, approval_id: str, approver: str) -> None:
        """Move `pending → denied`. Anything else raises `ApprovalMismatch`."""
        ...

    def consume_approval(self, approval_id: str, action_hash: str) -> Approval:
        """Atomically move `granted → consumed`, for this `action_hash` only (§4.2)."""
        ...


@runtime_checkable
class ApprovalProvider(Protocol):
    """How a human is asked, and how the answer comes back (SPEC-v0.1 §4.3).

    `runtime_checkable` so a test can assert the shipped providers still answer to this
    shape; it checks method names only, which is why the static check matters more.
    """

    def request(self, action: Action, ttl: timedelta) -> ApprovalRequest:
        """Record a request for `action` and return it."""
        ...

    def wait(self, request_id: str, timeout: timedelta | None) -> Approval | None:
        """Block until answered: the `Approval` if granted, `None` if denied.

        Raises `ApprovalTimeout` if nobody answers within `timeout` or before the request
        itself expires.
        """
        ...


#: SPEC-v0.6 §7.1 — the `policy_hash` in force while a request is being built, set by `Control`
#: around the provider call and read here.
#:
#: A context variable for the reason `_PRESENTED_APPROVAL` is one: the `ApprovalProvider`
#: protocol is `request(action, ttl)` and §9.1 freezes it, so a provider cannot be handed a
#: policy — and giving one to every provider would put the policy in the hands of things whose
#: whole job is to ask a human. `Control` is the only object that has both, and this is how it
#: reaches the one function every shipped provider builds its requests with.
#:
#: **The residual, stated:** a provider that constructs an `ApprovalRequest` itself rather than
#: through `build_request` records no policy hash. All three shipped providers use it, and a
#: fourth that did not would get `None`, which reads as "not recorded" and never as a wrong hash.
_POLICY_AT_REQUEST: ContextVar[str | None] = ContextVar("ctrlrun_policy_at_request", default=None)


@contextmanager
def policy_in_force(policy_hash: str | None) -> Iterator[None]:
    """Record `policy_hash` on any request built inside this block (SPEC-v0.6 §7.1)."""
    token = _POLICY_AT_REQUEST.set(policy_hash)
    try:
        yield
    finally:
        _POLICY_AT_REQUEST.reset(token)


#: SPEC-v0.7 §6.2: the precondition fingerprint captured on the request pass, travelling to
#: `build_request` exactly as `_POLICY_AT_REQUEST` does and for the same reason: the provider
#: protocol takes an action and a ttl and nothing else.
#:
#: **The residual is `_POLICY_AT_REQUEST`'s, and here it is refused rather than read as "not
#: recorded".** A third-party provider that builds its `ApprovalRequest` itself records no
#: fingerprint, and the presenting pass, which names the provider, then meets an approval
#: without one: `precondition_missing` (§6.4), never a skip.
_PRECONDITION_AT_REQUEST: ContextVar[str | None] = ContextVar(
    "ctrlrun_precondition_at_request", default=None
)

#: SPEC-v0.7 §6.2: the domain tag inside the fingerprint's canonical input, so a fingerprint
#: can never equal another hash of the same mapping. Never a document on its own (§9.3).
_PRECONDITION_SCHEMA: Final = "ctrlrun.precondition/v1"


@contextmanager
def _precondition_at_request(fingerprint: str | None) -> Iterator[None]:
    """Record `fingerprint` on any request built inside this block (SPEC-v0.7 §6.2)."""
    token = _PRECONDITION_AT_REQUEST.set(fingerprint)
    try:
        yield
    finally:
        _PRECONDITION_AT_REQUEST.reset(token)


def _precondition_fingerprint(state: Mapping[str, Any]) -> str:
    """`"sha256:" + hex(SHA-256(canonical_bytes({"schema": ..., "state": state})))` (§6.2).

    Through `canonical_bytes` and nothing else, so the float rejection, the non-string-key
    refusal and the lone-surrogate refusal are inherited rather than re-argued. Whatever it
    raises is the caller's to turn into `precondition_unavailable`; this function decides
    nothing about the action.
    """
    document = {"schema": _PRECONDITION_SCHEMA, "state": dict(state)}
    return "sha256:" + hashlib.sha256(canonical_bytes(document)).hexdigest()


def build_request(action: Action, ttl: timedelta, now: datetime) -> ApprovalRequest:
    """Build a request for `action`, validating the ttl. Package-internal, not public API.

    Promoted from `_build_request` in v0.5 so `adapter.py`'s provider builds requests the same
    way the two shipped providers do. A second copy would be a second place for a non-positive
    ttl to become a request that expires before it is asked.
    """
    if ttl <= timedelta(0):
        raise InvalidArgument(f"approval ttl must be positive, got {ttl!r}")
    return ApprovalRequest(
        request_id=new_request_id(),
        action_hash=action.action_hash,
        action=action,
        policy_hash=_POLICY_AT_REQUEST.get(),
        precondition_fingerprint=_PRECONDITION_AT_REQUEST.get(),
        created_at=now,
        expires_at=now + ttl,
    )


class LocalApprovalProvider:
    """Requests go to the StateStore; `wait()` polls it (SPEC-v0.1 §4.3).

    The human answers out of band — `ctrlrun approve <id>` or `ctrlrun deny <id>` in another
    shell. Waiting is bounded by the request's own expiry, so a request nobody answers ends
    in `ApprovalTimeout` rather than a blocked agent.
    """

    def __init__(
        self,
        store: ApprovalStore,
        *,
        clock: Callable[[], datetime] = _utc_now,
        poll_interval: timedelta = DEFAULT_POLL_INTERVAL,
    ) -> None:
        self._store = store
        self._clock = clock
        self._poll_interval = poll_interval

    def request(self, action: Action, ttl: timedelta = DEFAULT_APPROVAL_TTL) -> ApprovalRequest:
        request = build_request(action, ttl, self._clock())
        self._store.put_approval_request(request)
        return request

    def wait(self, request_id: str, timeout: timedelta | None = None) -> Approval | None:
        deadline = self._clock() + timeout if timeout is not None else None
        while True:
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
                # Denied, consumed or already expired: this request will never be granted.
                return None
            now = self._clock()
            if now > record.expires_at or (deadline is not None and now >= deadline):
                raise ApprovalTimeout(
                    f"approval request {request_id} was not answered in time",
                    request_id=request_id,
                )
            time.sleep(self._poll_interval.total_seconds())


class ScriptedOutcome(StrEnum):
    """What a scripted approver does on one poll."""

    PENDING = "pending"
    GRANT = "grant"
    DENY = "deny"


class ScriptedApprovalProvider:
    """A human replaced by a fixed script: for tests and `ctrlrun demo` (SPEC-v0.1 §4.3).

    Each `wait()` poll takes the next step. `PENDING` means "no answer yet", so a script can
    make `wait=True` genuinely block. The script is one sequence shared by every request, in
    poll order. An exhausted script raises `ApprovalTimeout`: a scripted approver never
    grants by accident, and a test can never hang waiting for a step that will not come.

    The script does not outrank the clock. A step that lands after the request has expired
    raises `ApprovalTimeout` and is not applied, so the double cannot grant something the
    real provider would have refused (SPEC-v0.1 §4.3).
    """

    def __init__(
        self,
        store: ApprovalStore,
        script: Iterable[str | ScriptedOutcome],
        *,
        approver: str = "cli:scripted",
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not approver:
            raise InvalidArgument("approver must be a non-empty string")
        self._store = store
        self._script = tuple(_parse_outcome(step) for step in script)
        self._approver = approver
        self._clock = clock
        self._step = 0
        self.polls = 0

    def request(self, action: Action, ttl: timedelta = DEFAULT_APPROVAL_TTL) -> ApprovalRequest:
        request = build_request(action, ttl, self._clock())
        self._store.put_approval_request(request)
        return request

    def wait(self, request_id: str, timeout: timedelta | None = None) -> Approval | None:
        deadline = self._clock() + timeout if timeout is not None else None
        while True:
            if self._step >= len(self._script):
                raise ApprovalTimeout(
                    f"the approval script has no answer for {request_id}",
                    request_id=request_id,
                )
            record = self._store.get_approval(request_id)
            if record is None:
                raise ApprovalMismatch(
                    f"no approval request {request_id}",
                    reason=UNKNOWN_APPROVAL,
                    approval_id=request_id,
                )
            now = self._clock()
            if now > record.expires_at or (deadline is not None and now >= deadline):
                # SPEC §4.3 — the request's own expiry bounds the wait for every provider;
                # a scripted answer arriving after it is an answer to a dead request.
                raise ApprovalTimeout(
                    f"approval request {request_id} was not answered in time",
                    request_id=request_id,
                )
            outcome = self._script[self._step]
            self._step += 1
            self.polls += 1
            if outcome is ScriptedOutcome.GRANT:
                return self._store.grant_approval(request_id, self._approver)
            if outcome is ScriptedOutcome.DENY:
                self._store.deny_approval(request_id, self._approver)
                return None


def _parse_outcome(step: str | ScriptedOutcome) -> ScriptedOutcome:
    try:
        return ScriptedOutcome(step)
    except ValueError as exc:
        allowed = ", ".join(member.value for member in ScriptedOutcome)
        raise InvalidArgument(
            f"unknown scripted approval outcome {step!r}, expected one of {allowed}"
        ) from exc
