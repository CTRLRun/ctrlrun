"""Approval requests, grants and providers. Build-list item 4; SPEC-v0.1 §4.

An approval authorizes one exact action: it carries the `action_hash` of what a human saw,
it can be used once, and it expires. Everything here exists to make those three properties
hard to lose. The store performs the state transitions (they must be atomic); this module
owns the models, the reason vocabulary, and the two providers that ask a human.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final, Protocol, runtime_checkable

from .action import Action
from .errors import (
    ActionDenied,
    ApprovalMismatch,
    ApprovalTimeout,
    CTRLRunError,
    InvalidArgument,
)

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


@dataclass(frozen=True)
class ApprovalRequest:
    """A pending question for a human: may this exact action run? (SPEC-v0.1 §4.1)"""

    request_id: str
    action_hash: str
    action: Action
    created_at: datetime
    expires_at: datetime

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

    @property
    def approval_id(self) -> str:
        return self.request.request_id

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


def _build_request(action: Action, ttl: timedelta, now: datetime) -> ApprovalRequest:
    if ttl <= timedelta(0):
        raise InvalidArgument(f"approval ttl must be positive, got {ttl!r}")
    return ApprovalRequest(
        request_id=new_request_id(),
        action_hash=action.action_hash,
        action=action,
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
        request = _build_request(action, ttl, self._clock())
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
        request = _build_request(action, ttl, self._clock())
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
