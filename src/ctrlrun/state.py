"""StateStore protocol, SQLite and in-memory stores. Build-list item 6; SPEC-v0.1 §5.3.

Build-list items 3 and 4 need the evidence half of the protocol — events and receipts — and
the approval half, plus an in-memory store to hold both. The effect-reservation methods of
SPEC §5.3 and `SQLiteStateStore` arrive with build-list item 6.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol

from .approval import (
    APPROVAL_DENIED,
    HASH_MISMATCH,
    UNKNOWN_APPROVAL,
    Approval,
    ApprovalRecord,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalStore,
)
from .errors import ActionDenied, ApprovalMismatch, InvalidArgument
from .receipt import Event, Receipt


def _utc_now() -> datetime:
    return datetime.now(UTC)


class StateStore(ApprovalStore, Protocol):
    """Durable state behind a `Control` (SPEC-v0.1 §5.3).

    Extends `ApprovalStore` with the evidence half; the effect half lands with item 6.
    """

    def append_event(self, event: Event) -> None:
        """Append an event, assigning it the next `event_id`."""
        ...

    def put_receipt(self, receipt: Receipt) -> None:
        """Record a receipt for an action that reached a terminal state."""
        ...


class InMemoryStateStore:
    """Evidence and approvals held in process memory: for tests and `ctrlrun demo`.

    Nothing here survives the process, and nothing here is shared between processes, so it
    cannot provide the atomic reservation of SPEC-v0.1 §5.3 E1. `SQLiteStateStore` (build-list
    item 6) is the store for anything that matters. Approval consumption *is* atomic here —
    one lock covers the whole check-and-transition — which is what §4.2 A2 asks for within a
    single process.
    """

    def __init__(self, *, clock: Callable[[], datetime] = _utc_now) -> None:
        self._lock = threading.Lock()
        self._clock = clock
        self._events: list[Event] = []
        self._receipts: list[Receipt] = []
        self._approvals: dict[str, ApprovalRecord] = {}

    # --- evidence ---------------------------------------------------------------------

    def append_event(self, event: Event) -> None:
        with self._lock:
            self._events.append(replace(event, event_id=len(self._events) + 1))

    def put_receipt(self, receipt: Receipt) -> None:
        with self._lock:
            self._receipts.append(receipt)

    def events(self) -> tuple[Event, ...]:
        """An immutable snapshot of the event log, in append order."""
        with self._lock:
            return tuple(self._events)

    def receipts(self) -> tuple[Receipt, ...]:
        """An immutable snapshot of the receipts, in write order."""
        with self._lock:
            return tuple(self._receipts)

    # --- approvals (SPEC-v0.1 §4.2) ---------------------------------------------------

    def put_approval_request(self, request: ApprovalRequest) -> None:
        with self._lock:
            if request.request_id in self._approvals:
                raise InvalidArgument(f"approval {request.request_id} already exists")
            self._approvals[request.request_id] = ApprovalRecord(
                request=request, status=ApprovalStatus.PENDING
            )

    def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        with self._lock:
            return self._approvals.get(approval_id)

    def grant_approval(self, approval_id: str, approver: str) -> Approval:
        if not approver:
            raise InvalidArgument("approver must be a non-empty string")
        with self._lock:
            record = self._pending(approval_id)
            granted = replace(
                record,
                status=ApprovalStatus.GRANTED,
                approver=approver,
                granted_at=self._clock(),
            )
            self._approvals[approval_id] = granted
            return granted.as_approval()

    def deny_approval(self, approval_id: str, approver: str) -> None:
        if not approver:
            raise InvalidArgument("approver must be a non-empty string")
        with self._lock:
            record = self._pending(approval_id)
            self._approvals[approval_id] = replace(
                record, status=ApprovalStatus.DENIED, approver=approver
            )

    def consume_approval(self, approval_id: str, action_hash: str) -> Approval:
        """Take a granted approval for exactly this action, once (SPEC-v0.1 §4.2).

        The whole check-and-transition happens under one lock, so two callers presenting the
        same approval cannot both win (A2). The hash is checked *before* the status, so a
        mutated action leaves the approval untouched and still grantable for the action the
        human actually saw (A1, acceptance test T2).
        """
        with self._lock:
            record = self._approvals.get(approval_id)
            if record is None:
                raise ApprovalMismatch(
                    f"no approval {approval_id}",
                    reason=UNKNOWN_APPROVAL,
                    approval_id=approval_id,
                )
            if record.action_hash != action_hash:
                # A1: the record is left exactly as it was — this action is what is wrong.
                raise ApprovalMismatch(
                    f"approval {approval_id} authorizes {record.action_hash}, not {action_hash}",
                    reason=HASH_MISMATCH,
                    approval_id=approval_id,
                )
            if record.status is ApprovalStatus.DENIED:
                raise ActionDenied(
                    f"approval {approval_id} was denied by {record.approver}",
                    reason=APPROVAL_DENIED,
                )
            if record.status is not ApprovalStatus.GRANTED:
                raise ApprovalMismatch(
                    f"approval {approval_id} is {record.status}, not granted",
                    reason=str(record.status),
                    approval_id=approval_id,
                )
            now = self._clock()
            if now > record.expires_at:
                # A3: expiry is checked here, at consumption, not only at grant time.
                self._approvals[approval_id] = replace(record, status=ApprovalStatus.EXPIRED)
                raise ApprovalMismatch(
                    f"approval {approval_id} expired at {record.expires_at.isoformat()}",
                    reason=str(ApprovalStatus.EXPIRED),
                    approval_id=approval_id,
                )
            # SPEC: §4.2 A4 — from build-list item 6 this transition shares a transaction
            # with the effect reservation, so a refused reservation leaves it unconsumed.
            self._approvals[approval_id] = replace(
                record, status=ApprovalStatus.CONSUMED, consumed_at=now
            )
            return record.as_approval()

    def _pending(self, approval_id: str) -> ApprovalRecord:
        """The record for `approval_id`, if it still awaits an answer. Caller holds the lock."""
        record = self._approvals.get(approval_id)
        if record is None:
            raise ApprovalMismatch(
                f"no approval request {approval_id}",
                reason=UNKNOWN_APPROVAL,
                approval_id=approval_id,
            )
        if record.status is not ApprovalStatus.PENDING:
            raise ApprovalMismatch(
                f"approval request {approval_id} is already {record.status}",
                reason=str(record.status),
                approval_id=approval_id,
            )
        if self._clock() > record.expires_at:
            self._approvals[approval_id] = replace(record, status=ApprovalStatus.EXPIRED)
            raise ApprovalMismatch(
                f"approval request {approval_id} expired at {record.expires_at.isoformat()}",
                reason=str(ApprovalStatus.EXPIRED),
                approval_id=approval_id,
            )
        return record
