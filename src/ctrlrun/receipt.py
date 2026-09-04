"""Receipts and the event log. Build-list item 8; SPEC-v0.1 §6.

`Control` produces a `Receipt` for every action that reaches a terminal state, and an `Event`
for every step it takes. `JSONLEventSink` is where those land on disk: `.ctrlrun/receipts.jsonl`
and `.ctrlrun/events.jsonl`, one JSON object per line, in append order.

A receipt is evidence, and evidence has to outlive the tool that wrote it — so the file form
is plain JSON with enums rendered by value, readable by anything that can read a line.
"""

from __future__ import annotations

import json
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Protocol

from .action import Principal
from .policy import Decision

#: SPEC-v0.3 §12.2. The bump landed with build-list item 1, because that is when the first v2
#: field appeared — the principal's claims, issuer and expiry. `execution` and `would_have`
#: joined them in item 4, under the same version string, so a reader parses one shape.
RECEIPT_SCHEMA: Final = "ctrlrun.receipt/v2"

#: The two files of SPEC-v0.1 §6, written beside the state database.
RECEIPTS_FILENAME: Final = "receipts.jsonl"
EVENTS_FILENAME: Final = "events.jsonl"

_ID_HEX_BYTES: Final = 16  # "ctr_" + 32 hex chars

#: SPEC-v0.3 §6.3 — the part of `would_have.blocked_reason`'s closed vocabulary that names
#: something other than a decision. The rest of it is decision reasons, reused verbatim:
#: `principal_expired`, `unknown_action`, `no_matching_rule`, a `rule[N]`, and §4.3's six
#: authority denials. It is closed because §6.4 buckets counts on it, and a bucketed count
#: over a string nobody constrained is a report that quietly stops adding up.
BLOCKED_APPROVAL_REQUIRED: Final = "approval_required"
BLOCKED_APPROVAL_MISMATCH: Final = "approval_mismatch"
BLOCKED_DUPLICATE: Final = "duplicate"
BLOCKED_IN_PROGRESS: Final = "in_progress"
BLOCKED_AMBIGUOUS: Final = "ambiguous"

#: The four that mean "the effect state or a presented approval would have stopped it", as
#: opposed to a decision that would have. `ctrlrun stats` counts them as one line (§6.4).
BLOCKED_BY_STATE: Final = frozenset(
    {
        BLOCKED_APPROVAL_MISMATCH,
        BLOCKED_DUPLICATE,
        BLOCKED_IN_PROGRESS,
        BLOCKED_AMBIGUOUS,
    }
)


def new_receipt_id() -> str:
    return f"ctr_{secrets.token_hex(_ID_HEX_BYTES)}"


def iso_timestamp(moment: datetime) -> str:
    """UTC ISO-8601 with a `Z` suffix, as in SPEC-v0.1 §6.1."""
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _principal_dict(principal: Principal) -> dict[str, Any]:
    """The principal as receipt data (SPEC-v0.3 §2.4).

    Receipts carry the whole thing — claims, issuer and expiry — because a receipt is the
    record and §2.1's distinction between "the provider stated no expiry" and "nothing was
    stored" is load-bearing. Spans make the opposite trade (§2.4): values are withheld there.
    """
    return {
        "agent": principal.agent,
        "user": principal.user,
        "claims": dict(principal.claims),
        "issuer": principal.issuer,
        "expires_at": None if principal.expires_at is None else iso_timestamp(principal.expires_at),
    }


class ReceiptResult(StrEnum):
    """The terminal outcome recorded on a receipt (SPEC-v0.1 §6.1, SPEC-v0.3 §6.3).

    `BLOCKED` covers duplicate, ambiguous-retry and approval-mismatch refusals. `OBSERVED`
    is the observe-mode result, and it is not in v0.1 §6.1's set — which is why the receipt
    schema bumped to v2 (§12.2) and why every reader upgrades before any writer switches.
    """

    COMMITTED = "committed"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"
    DENIED = "denied"
    BLOCKED = "blocked"
    #: SPEC-v0.3 §6.3 — every receipt an observe-mode run *observed*, including the ones
    #: nothing would have blocked. What the executor actually did is on `execution`; what
    #: enforce mode would have done is on `would_have`.
    OBSERVED = "observed"


class EventType(StrEnum):
    """The closed set of event types in SPEC-v0.1 §6.2, extended by SPEC-v0.2 §2.5."""

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
    RECONCILIATION_STARTED = "RECONCILIATION_STARTED"
    RECONCILIATION_RESOLVED = "RECONCILIATION_RESOLVED"
    #: SPEC-v0.2 §11 — named for what happens to the attempt, not for the transport that
    #: caused it: `receipt.py` does not learn MCP vocabulary (ARCHITECTURE §6).
    EXECUTION_SUSPENDED = "EXECUTION_SUSPENDED"
    EXECUTION_RESUMED = "EXECUTION_RESUMED"
    #: SPEC-v0.3 §7 — the five types authority and delegation add. `AUTHORITY_RESOLVED` is
    #: appended for *every* action that passes authority, not only for a delegated one:
    #: evidence has to record that CTRLRun checked and found a grant, or a deployment with a
    #: permissive grant is indistinguishable from one with no `authority:` section at all.
    #: The three `DELEGATION_*` types are produced by `Control.delegate` and `Control.revoke`,
    #: which land with build-list item 3; the vocabulary is closed here so a reader of an
    #: evidence file has one list to check against.
    AUTHORITY_RESOLVED = "AUTHORITY_RESOLVED"
    AUTHORITY_DENIED = "AUTHORITY_DENIED"
    DELEGATION_CREATED = "DELEGATION_CREATED"
    DELEGATION_REVOKED = "DELEGATION_REVOKED"
    DELEGATION_REJECTED = "DELEGATION_REJECTED"


@dataclass(frozen=True)
class Event:
    """One ordered step in the life of an action (SPEC-v0.1 §6.2).

    `event_id` is assigned by the StateStore on append, not by the caller.

    `action_id` is `None` for the three `DELEGATION_*` types (SPEC-v0.3 §7): they are about an
    authority record, created and revoked outside any action's life, and they name the
    delegation in `data.delegation_id`. Inventing a synthetic `action_id` would put a value in
    a field every reader takes to name a real proposal.
    """

    type: EventType
    action_id: str | None
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
class _WouldHave:
    """What enforce mode would have done with an observed action (SPEC-v0.3 §6.3).

    Private, like `policy._ActionPolicy`, because SPEC-v0.3 §11 freezes `Receipt.would_have`
    and not a type name for it: the shape a reader parses is the JSON object, and adding a
    public class here would be an addition to a frozen surface.

    `decision` and `reason` are the combined §4.6 result — what enforce mode would have
    *reached*. `blocked_reason` is what would have been *done* with it, and is `None` where
    the action would have run unimpeded. The pair is not a duplicate: "the policy said allow
    and the effect was already committed" is a real and common answer, and a single field
    could not hold both halves.
    """

    decision: Decision
    reason: str
    blocked_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": str(self.decision),
            "reason": self.reason,
            "blocked_reason": self.blocked_reason,
        }

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> _WouldHave:
        return cls(
            decision=Decision(document["decision"]),
            reason=document["reason"],
            blocked_reason=document.get("blocked_reason"),
        )


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
    #: SPEC-v0.3 §6.3 — what the executor actually did, in observe mode: `committed`,
    #: `failed` or `ambiguous`, or `None` where it never ran. Always `None` in enforce mode,
    #: where `result` already carries it; duplicating it would give two fields that can
    #: disagree.
    execution: ReceiptResult | None = None
    #: The counterfactual, present on every observed run and absent on every refused one
    #: (§6.3). That is what keeps "never infer from the absence of a field" true in both
    #: directions.
    would_have: _WouldHave | None = None

    def to_dict(self) -> dict[str, Any]:
        """The receipt as plain JSON-serializable data, in the field order of SPEC §6.1."""
        return {
            "schema": RECEIPT_SCHEMA,
            "receipt_id": self.receipt_id,
            "action_id": self.action_id,
            "action": self.action,
            "action_hash": self.action_hash,
            "principal": _principal_dict(self.principal),
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
            "execution": None if self.execution is None else str(self.execution),
            "would_have": None if self.would_have is None else self.would_have.to_dict(),
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
        expires_at = principal.get("expires_at")
        return cls(
            receipt_id=document["receipt_id"],
            action_id=document["action_id"],
            action=document["action"],
            action_hash=document["action_hash"],
            # `.get` for the three v0.3 fields: a receipt written by 0.2 carries only the two
            # older keys, and must parse back rather than raise (SPEC-v0.3 §2.4).
            principal=Principal(
                agent=principal["agent"],
                user=principal["user"],
                claims=principal.get("claims") or {},
                issuer=principal.get("issuer"),
                expires_at=None if expires_at is None else datetime.fromisoformat(expires_at),
            ),
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
            # `.get` again: a 0.2 receipt carries neither key, and must parse back rather
            # than raise (§12.2).
            execution=(
                None if document.get("execution") is None else ReceiptResult(document["execution"])
            ),
            would_have=(
                None
                if document.get("would_have") is None
                else _WouldHave.from_dict(document["would_have"])
            ),
            error=document["error"],
            started_at=datetime.fromisoformat(document["started_at"]),
            finished_at=datetime.fromisoformat(document["finished_at"]),
        )

    @classmethod
    def from_json(cls, line: str) -> Receipt:
        """Parse one JSONL line written by `to_json`."""
        document: dict[str, Any] = json.loads(line)
        return cls.from_dict(document)


class EventSink(Protocol):
    """Somewhere a copy of every `Event` and `Receipt` goes (SPEC-v0.2 §4.1).

    `Control` calls a sink *after* the authoritative store write for that record has
    succeeded, in registration order, with the `event_id` the store assigned. A sink is the
    interface for the copies; it is not the interface for the record — the store's own
    `events` and `receipts` tables are written inside the store, in its transaction, before
    any sink runs (§4.3).

    Sinks are not transactional, not ordered across processes, and not retried. A sink that
    must not lose records buffers and retries inside itself. And a sink never raises into the
    kernel: `Control` catches every `Exception` and carries on (§4.2).
    """

    def on_event(self, event: Event) -> None: ...

    def on_receipt(self, receipt: Receipt) -> None: ...


class JSONLEventSink:
    """The JSONL half of the evidence: two append-only files in one directory (SPEC §6).

    `receipts.jsonl` and `events.jsonl` beside the state database, so `.ctrlrun/` holds the
    whole record of what an agent did. The store is authoritative — these files are the
    portable copy, written after the store accepted the same record.

    Each write opens, appends one line and closes, so several processes sharing a store
    (SPEC-v0.1 §5.3 E1) interleave whole lines rather than fragments of them.

    SPEC-v0.2 §4.3 — this used to live inside `SQLiteStateStore`, which wrote both halves.
    `Control` owns it now, as one `EventSink` among however many an application registers.
    The files it writes, and where, are unchanged.
    """

    def __init__(self, directory: str | os.PathLike[str]) -> None:
        self._directory = Path(directory)

    @property
    def directory(self) -> Path:
        return self._directory

    @property
    def receipts_path(self) -> Path:
        return self._directory / RECEIPTS_FILENAME

    @property
    def events_path(self) -> Path:
        return self._directory / EVENTS_FILENAME

    def on_receipt(self, receipt: Receipt) -> None:
        """Append one receipt as a JSON line."""
        self._append(self.receipts_path, receipt.to_json())

    def on_event(self, event: Event) -> None:
        """Append one event as a JSON line, in the order the store assigned it."""
        self._append(self.events_path, event.to_json())

    def _append(self, path: Path, line: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{line}\n")
