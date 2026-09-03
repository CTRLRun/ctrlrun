"""Receipts and the event log. Build-list item 8; SPEC-v0.1 §6.

Build-list item 3 needs the models: `Control` produces a `Receipt` for every action that
reaches a terminal state, and an `Event` for every step it takes. The JSONL writer that
lands these in `.ctrlrun/` arrives with item 8.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final

from .action import Principal
from .policy import Decision

RECEIPT_SCHEMA: Final = "ctrlrun.receipt/v1"

_ID_HEX_BYTES: Final = 6  # "ctr_" + 12 hex chars


def new_receipt_id() -> str:
    return f"ctr_{secrets.token_hex(_ID_HEX_BYTES)}"


def iso_timestamp(moment: datetime) -> str:
    """UTC ISO-8601 with a `Z` suffix, as in SPEC-v0.1 §6.1."""
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ReceiptResult(StrEnum):
    """The terminal outcome recorded on a receipt (SPEC-v0.1 §6.1).

    `BLOCKED` covers duplicate, ambiguous-retry and approval-mismatch refusals.
    """

    COMMITTED = "committed"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"
    DENIED = "denied"
    BLOCKED = "blocked"


class EventType(StrEnum):
    """The closed set of event types in SPEC-v0.1 §6.2."""

    ACTION_PROPOSED = "ACTION_PROPOSED"
    POLICY_EVALUATED = "POLICY_EVALUATED"
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    APPROVAL_GRANTED = "APPROVAL_GRANTED"
    APPROVAL_DENIED = "APPROVAL_DENIED"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    APPROVAL_INVALIDATED = "APPROVAL_INVALIDATED"
    APPROVAL_CONSUMED = "APPROVAL_CONSUMED"
    EFFECT_RESERVED = "EFFECT_RESERVED"
    EFFECT_RESERVATION_REFUSED = "EFFECT_RESERVATION_REFUSED"
    EXECUTION_STARTED = "EXECUTION_STARTED"
    EXECUTION_COMMITTED = "EXECUTION_COMMITTED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    EXECUTION_AMBIGUOUS = "EXECUTION_AMBIGUOUS"
    EFFECT_RESOLVED = "EFFECT_RESOLVED"
    ACTION_DENIED = "ACTION_DENIED"


@dataclass(frozen=True)
class Event:
    """One ordered step in the life of an action (SPEC-v0.1 §6.2).

    `event_id` is assigned by the StateStore on append, not by the caller.
    """

    type: EventType
    action_id: str
    ts: datetime
    data: Mapping[str, Any] = field(default_factory=dict)
    effect_key: str | None = None
    approval_id: str | None = None
    event_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "ts": iso_timestamp(self.ts),
            "type": str(self.type),
            "action_id": self.action_id,
            "effect_key": self.effect_key,
            "approval_id": self.approval_id,
            "data": dict(self.data),
        }

    def to_json(self) -> str:
        """One JSONL line. Enums render by value, for readers that never imported CTRLRun."""
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class Receipt:
    """Portable evidence of one action that reached a terminal state (SPEC-v0.1 §6.1)."""

    receipt_id: str
    action_id: str
    action: str
    action_hash: str
    principal: Principal
    resource: str | None
    arguments: Mapping[str, Any]
    environment: str
    decision: Decision
    decision_reason: str
    result: ReceiptResult
    started_at: datetime
    finished_at: datetime
    approval_id: str | None = None
    approver: str | None = None
    effect_key: str | None = None
    attempt: int = 1
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """The receipt as plain JSON-serializable data, in the field order of SPEC §6.1."""
        return {
            "schema": RECEIPT_SCHEMA,
            "receipt_id": self.receipt_id,
            "action_id": self.action_id,
            "action": self.action,
            "action_hash": self.action_hash,
            "principal": {"agent": self.principal.agent, "user": self.principal.user},
            "resource": self.resource,
            "arguments": dict(self.arguments),
            "environment": self.environment,
            "decision": str(self.decision),
            "decision_reason": self.decision_reason,
            "approval_id": self.approval_id,
            "approver": self.approver,
            "effect_key": self.effect_key,
            "attempt": self.attempt,
            "result": str(self.result),
            "error": self.error,
            "started_at": iso_timestamp(self.started_at),
            "finished_at": iso_timestamp(self.finished_at),
        }

    def to_json(self) -> str:
        """One JSONL line. Enums render by value (SPEC-v0.1 §6.1)."""
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> Receipt:
        """The inverse of `to_dict`: a receipt read back out of a store or a JSONL file."""
        principal = document["principal"]
        return cls(
            receipt_id=document["receipt_id"],
            action_id=document["action_id"],
            action=document["action"],
            action_hash=document["action_hash"],
            principal=Principal(agent=principal["agent"], user=principal["user"]),
            resource=document["resource"],
            arguments=document["arguments"],
            environment=document["environment"],
            decision=Decision(document["decision"]),
            decision_reason=document["decision_reason"],
            approval_id=document["approval_id"],
            approver=document["approver"],
            effect_key=document["effect_key"],
            attempt=document["attempt"],
            result=ReceiptResult(document["result"]),
            error=document["error"],
            started_at=datetime.fromisoformat(document["started_at"]),
            finished_at=datetime.fromisoformat(document["finished_at"]),
        )

    @classmethod
    def from_json(cls, line: str) -> Receipt:
        """Parse one JSONL line written by `to_json`."""
        document: dict[str, Any] = json.loads(line)
        return cls.from_dict(document)
