# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""Receipts and the event log. Build-list item 8; SPEC-v0.1 §6.

`Control` produces a `Receipt` for every action that reaches a terminal state, and an `Event`
for every step it takes. `JSONLEventSink` is where those land on disk: `.ctrlrun/receipts.jsonl`
and `.ctrlrun/events.jsonl`, one JSON object per line, in append order.

A receipt is evidence, and evidence has to outlive the tool that wrote it — so the file form
is plain JSON with enums rendered by value, readable by anything that can read a line.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Protocol

from .action import Principal, canonical_bytes

# `approval.py` imports `action`, `errors` and `identity` and nothing else, so this is
# downward (ARCHITECTURE §6): a receipt records what an approval verified, and the record
# type it records is that module's.
from .approval import APPROVAL_DENIED as APPROVAL_DENIED_REASON
from .approval import (
    APPROVAL_UNRECORDED,
    APPROVALS_UNVERIFIABLE,
    APPROVER_UNENTITLED,
    VerifiedApprover,
)

# `decision.py` imports nothing from the package, so this is downward and the cycle
# `state -> receipt -> policy -> authority -> state` that ARCHITECTURE §6 recorded is gone.
# These two names were the whole of what a receipt needed from the decider.
from .decision import POLICY_UNAPPROVED, Decision
from .errors import CTRLRunError, InvalidArgument

#: SPEC-v0.3 §12.2. The bump landed with build-list item 1, because that is when the first v2
#: field appeared — the principal's claims, issuer and expiry. `execution` and `would_have`
#: joined them in item 4, under the same version string, so a reader parses one shape.
#: SPEC-v0.6 §9.5. `v3` adds `seq` and `prev_hash` (§6.2) and, in item 7, `policy_hash`,
#: `policy_version` and `controls`. Every `v2` field keeps its meaning, and `Receipt.from_dict`
#: reads all five with `.get`, so a `v2` receipt on disk still parses -- which is the rule every
#: reader upgrades before any writer switches.
#: SPEC-v0.7 §6.11. `v4` adds `precondition_at_request` and `precondition_at_recheck`, and it is
#: the first bump that does not rehash every older receipt: a receipt read from a store is
#: hashed as the document it was read from, and renders under its own schema's label and keys.
RECEIPT_SCHEMA: Final = "ctrlrun.receipt/v7"

_V1: Final = "ctrlrun.receipt/v1"
_V2: Final = "ctrlrun.receipt/v2"
_V3: Final = "ctrlrun.receipt/v3"
_V4: Final = "ctrlrun.receipt/v4"
_V5: Final = "ctrlrun.receipt/v5"
#: SPEC-v0.9 §10.1: `v6` is `task` (item 1), `scope_hash` (item 2) and `budget_charges` (item 5).
#: The version moves once, with whichever lands first, and the later items fill their fields
#: under the version already in place. Item 7 asserts all three are written by something.
_V6: Final = "ctrlrun.receipt/v6"
#: SPEC-v0.10 §3.5: `v7` is `hop`, and nothing else. Bumped once, by item 2, with the whole shape
#: frozen in §9.1 before any item started. The rule since `SPEC-v0.3 §12.2` is unchanged: every
#: reader upgrades before any writer switches, so a `v6` receipt on disk still parses.
_V7: Final = "ctrlrun.receipt/v7"

#: SPEC-v0.8 §8.5 — every receipt schema this binary reads. The policy replay checks it before
#: rebuilding an action: `from_dict` does not raise on an unknown one, so a receipt written by a
#: later version rebuilt fine and was silently **graded**, on fields this binary may be reading
#: wrongly. `v0.6 §3.2` draws the same line for a store row.
KNOWN_RECEIPT_SCHEMAS: Final = frozenset({_V1, _V2, _V3, _V4, _V5, _V6, _V7})

#: SPEC-v0.7 §6.11: each schema's top-level key set, exactly its released writers': `v1`, 19
#: keys, by 0.1.0 and 0.2.0; `v2`, 21, by 0.3.0rc1 to 0.5.0; `v3`, 26, by 0.6.0 and 0.6.1;
#: `v4`, 28. Counted from those releases' own `to_dict`, not from memory.
_V1_KEYS: Final = (
    "schema",
    "receipt_id",
    "action_id",
    "action",
    "action_hash",
    "principal",
    "resource",
    "arguments",
    "environment",
    "decision",
    "decision_reason",
    "approval_id",
    "approver",
    "effect_key",
    "attempt",
    "result",
    "error",
    "started_at",
    "finished_at",
)
_V2_KEYS: Final = (*_V1_KEYS[:16], "execution", "would_have", *_V1_KEYS[16:])
_V3_KEYS: Final = (*_V2_KEYS, "seq", "prev_hash", "policy_hash", "policy_version", "controls")
_V4_KEYS: Final = (*_V3_KEYS, "precondition_at_request", "precondition_at_recheck")
#: SPEC-v0.8 §11.3: `v5`, 30 keys. The whole shape is frozen before item 2 writes it, so a
#: reader can parse a `v5` receipt from any later item: `authority_grant_id` is item 5's and is
#: `None` until then, which is what "absent or null" means for a field nothing has filled.
_V5_KEYS: Final = (*_V4_KEYS, "approvers", "authority_grant_id")
#: SPEC-v0.9 §10.1 — `v6`'s frozen shape is `task` (item 1), `scope_hash` (item 2) and
#: `budget_charges` (item 5). **The tuple grows as each item lands, not all at once**, on the
#: guarantee catalogue's own rule: a key listed here is a key `to_dict` projects, so naming one
#: before something writes it is a `KeyError` on every receipt, which is the field-level form of
#: a stub row. Item 7 asserts all three are present before the release.
_V6_KEYS: Final = (*_V5_KEYS, "task", "scope_hash", "budget_charges")
#: SPEC-v0.10 §3.5 — `v7` adds one key, `hop`, and item 2 both names it and writes it, so there is
#: no window in which the tuple promises a projection nothing fills.
_V7_KEYS: Final = (*_V6_KEYS, "hop")
_KEYS: Final = {
    _V1: _V1_KEYS,
    _V2: _V2_KEYS,
    _V3: _V3_KEYS,
    _V4: _V4_KEYS,
    _V5: _V5_KEYS,
    _V6: _V6_KEYS,
    _V7: _V7_KEYS,
}

#: The two files of SPEC-v0.1 §6, written beside the state database.
#: SPEC-v0.6 §6.2. The `prev_hash` of receipt 1, and the hash the head row starts at (§3.7), so
#: that a database which was empty and one which already held a thousand *unchained* receipts
#: begin their chain identically.
GENESIS_HASH: Final = "sha256:" + "00" * 32

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

#: SPEC-v0.7 §5.5 — the attempt ceiling's refusal, in the same three places: `ActionDenied.reason`,
#: `EFFECT_RESERVATION_REFUSED.data.reason`, and `would_have.blocked_reason` in observe mode. It is
#: a value of existing fields and not a new type: an operator's `max_attempts` is a policy saying
#: no, and an agent loop's `except ActionDenied` is written for exactly that.
BLOCKED_ATTEMPT_CEILING: Final = "attempt_ceiling"

#: SPEC-v0.8 §4.1 — observe mode records a mismatch's **own** reason now, where it recorded
#: `BLOCKED_APPROVAL_MISMATCH` for every one of them, so the closed vocabulary above grows by the
#: reasons an approval refusal actually carries, whether it is raised as an `ApprovalMismatch`
#: or, for a human's no, as an `ActionDenied`. They are the values `check_consumable` and
#: `Control` already raise, listed here because §6.4 buckets counts on this set.
#:
#: **Widening the set is not decoration: without it the change would have been a silent
#: under-count.** An independent review measured it. An observe-mode approval refusal, including a
#: plain hash mismatch that has nothing to do with v0.8, landed in no bucket at all, so
#: `would_have_been_blocked` went from 1 to 0 and `ctrlrun stats` under-reported exactly what it
#: exists to report. The comment above says a bucketed count over a string nobody constrained is a
#: report that quietly stops adding up; this is that, and the fix is to constrain the string.
#: **`approval_denied` and not `denied`, and the difference is a report that was already wrong.**
#: `check_consumable` catches a denied record one branch before the generic status branch and
#: raises `ActionDenied(reason=APPROVAL_DENIED)`, so `"denied"`, which is `str(ApprovalStatus.
#: DENIED)`, is a value nothing on this path can carry, while `"approval_denied"` is recorded by
#: observe mode's `ActionDenied` handler and was in no bucket at all. An observe-mode run where a
#: **human said no** was therefore counted nowhere, which is close to the most important thing
#: such a report can say. That predates v0.8 and is fixed here, because this is the commit that
#: writes the set and argues for closing it.
BLOCKED_APPROVAL_REASONS: Final = frozenset(
    {
        "mismatch",
        "consumed",
        "expired",
        "pending",
        "unknown",
        APPROVAL_DENIED_REASON,
        "precondition_changed",
        "precondition_missing",
        "precondition_unavailable",
        "approver_unverified",
        "approver_is_requester",
        # **Items 3 and 4's reasons, and their absence was the same defect one item later.**
        # The paragraph above records `approval_denied` landing in no bucket and being fixed
        # here; `approver_unentitled` and `approvals_unverifiable` were then coined without
        # being added here, so an observe-mode run that would have refused an unentitled
        # approver reported `would_have_been_blocked = 0`. A set maintained by hand is a set
        # the next reason is missed from, which is why
        # `test_every_approval_refusal_reason_is_counted_by_stats` enumerates them from
        # `approval.py` instead of restating them.
        APPROVER_UNENTITLED,
        APPROVALS_UNVERIFIABLE,
        APPROVAL_UNRECORDED,
        # SPEC-v0.8 §8.4: observe mode records it as a `would_have.blocked_reason`, so it needs
        # a bucket like every other refusal. This set has been missed twice already, which is
        # why `test_every_approval_refusal_reason_is_counted_by_stats` enumerates the reasons
        # from source rather than trusting this list.
        POLICY_UNAPPROVED,
    }
)

#: The ones that mean "the effect state or a presented approval would have stopped it", as
#: opposed to a decision that would have. `ctrlrun stats` counts them as one line (§6.4).
BLOCKED_BY_STATE: Final = frozenset(
    {
        BLOCKED_APPROVAL_MISMATCH,
        BLOCKED_DUPLICATE,
        BLOCKED_IN_PROGRESS,
        BLOCKED_AMBIGUOUS,
        BLOCKED_ATTEMPT_CEILING,
        *BLOCKED_APPROVAL_REASONS,
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
    """The closed set of event types in SPEC-v0.1 §6.2, extended by SPEC-v0.2 §2.5, SPEC-v0.3
    §7 and SPEC-v0.7 §3.6."""

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
    #: evidence has to record that ctrlrun checked and found a grant, or a deployment with a
    #: permissive grant is indistinguishable from one with no `authority:` section at all.
    #: The three `DELEGATION_*` types are produced by `Control.delegate` and `Control.revoke`,
    #: which land with build-list item 3; the vocabulary is closed here so a reader of an
    #: evidence file has one list to check against.
    AUTHORITY_RESOLVED = "AUTHORITY_RESOLVED"
    AUTHORITY_DENIED = "AUTHORITY_DENIED"
    DELEGATION_CREATED = "DELEGATION_CREATED"
    DELEGATION_REVOKED = "DELEGATION_REVOKED"
    DELEGATION_REJECTED = "DELEGATION_REJECTED"
    #: SPEC-v0.7 §3.6: this host's clock and the store's disagree past the threshold, beyond
    #: the measurement's own bound. Named for what happened, not for the store that noticed.
    #: `action_id` is `None` on the report of a measurement taken at open, like the three
    #: `DELEGATION_*` types; the report beside an expired lease names that attempt. It records
    #: a fact beside a refusal and decides nothing: no lease is evaluated against it.
    CLOCK_SKEW_DETECTED = "CLOCK_SKEW_DETECTED"


@dataclass(frozen=True)
class Event:
    """One ordered step in the life of an action (SPEC-v0.1 §6.2).

    `event_id` is assigned by the StateStore on append, not by the caller.

    `action_id` is `None` for the three `DELEGATION_*` types (SPEC-v0.3 §7): they are about an
    authority record, created and revoked outside any action's life, and they name the
    delegation in `data.delegation_id`. Inventing a synthetic `action_id` would put a value in
    a field every reader takes to name a real proposal. The same holds for a
    `CLOCK_SKEW_DETECTED` reporting a measurement taken when the store opened (SPEC-v0.7 §3.6),
    which is about the deployment and not about an action.
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
        """One JSONL line. Enums render by value, for readers that never imported ctrlrun."""
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


def _controls_of(value: object) -> tuple[str, ...]:
    """A receipt's `controls`, parsed rather than coerced (SPEC-v0.6 §7.3).

    `tuple(value or ())` turned the string `"abc"` into `('a', 'b', 'c')` -- three controls that
    were never cited, in a document a reader would take as evidence. Everything else in
    `from_dict` parses into a closed set; this did not, and an independent review found it.
    """
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, list | tuple):
        raise InvalidArgument(
            f"a receipt's 'controls' must be a list of control ids, got {type(value).__name__}"
        )
    for item in value:
        if not isinstance(item, str):
            raise InvalidArgument(f"a control id must be a string, got {item!r}")
    return tuple(value)


@dataclass(frozen=True)
class Receipt:
    """Portable evidence of one action that reached a terminal state (SPEC-v0.1 §6.1).

    `ctrlrun.receipt/v4` (SPEC-v0.7 §6.11) adds `precondition_at_request` and
    `precondition_at_recheck`: the fingerprint the approval was requested with, and the one
    computed on the presenting pass, each `None` where there was none. Hashes only, never the
    state they were computed from. On a refusal they say which side moved or was missing; on a
    committed action they are equal, and the receipt records that the world was compared
    before the reservation. That comparison narrows the window between a human's decision and
    the effect and does not close it (§6.7).

    `schema` is the schema the receipt is written under. A receipt read from a store keeps the
    one it was written with, renders under that schema's label and keys, and is hashed as the
    document it was read from, so a `v3` receipt a released 0.6 wrote still rehashes to its
    stored hash under a `v4` binary.
    """

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
    #: This receipt's place in the store's one chain (SPEC-v0.6 §6.2). Assigned by
    #: `put_receipt` in the transaction that writes the row, `None` on a receipt that has not
    #: been written yet and on one written before the chain existed (§3.7, §6.5's `unchained`).
    #:
    #: **It is inside the hashed content**, and that is what makes deletion and reordering
    #: detectable rather than only edits: two adjacent rows swapped *with* their `seq` change
    #: both documents, and swapped *without* them break the links. There is no swap that is
    #: invisible.
    seq: int | None = None
    #: The `hash` of receipt `seq - 1`, or `GENESIS_HASH` for `seq == 1` (§6.2).
    prev_hash: str | None = None
    #: SPEC-v0.6 §7.1 — the hash of the policy that decided this action, and the operator's own
    #: label for it. The hash is authoritative and the label is for humans: two documents sharing
    #: a `version:` and differing in content are two different policies, and the hash says so.
    #: `None` on a receipt written before v0.6.
    policy_hash: str | None = None
    policy_version: str | None = None
    #: §7.3 — the control ids the matched rule cited, unioned with the action's. **Attribution,
    #: not prevention**: citing a control does not cause an approval, the rule's `decision:`
    #: does. This says which written expectation the rule exists to serve, so a receipt can
    #: answer "under what".
    controls: tuple[str, ...] = ()
    #: The hash **as the store recorded it**, filled in on read and `None` on a receipt that has
    #: not been written. Not in `to_dict()`: a document cannot contain its own hash, which is why
    #: §6.2 makes this a column. Keeping it here rather than behind a store method is what lets
    #: `verify_chain` compare stored against recomputed without the protocol growing a second
    #: reader (§9.1).
    hash: str | None = None
    #: SPEC-v0.7 §6.11: the fingerprint the presented approval was requested with, and the one
    #: the presenting pass computed. `None` where there was none. Read only from a `v4`
    #: document, so no reader surfaces the value of a key a document's schema does not declare.
    precondition_at_request: str | None = None
    precondition_at_recheck: str | None = None
    #: SPEC-v0.8 §2.5: every approver a resolving surface verified for the approval this action
    #: consumed. Empty where none was, which is every 0.7.0 deployment and every surface §2.6
    #: names as unable to resolve. Read only from a `v5` document.
    approvers: tuple[VerifiedApprover, ...] = ()
    #: SPEC-v0.8 §5.4: the grant that decided this action, for **every** grant and not only for
    #: break-glass. Item 5 fills it; `None` until then, which is the "absent or null" §11.4's
    #: frozen shape promises a reader.
    authority_grant_id: str | None = None
    #: SPEC-v0.9 §6: the task this action was bound to, or `None` where the caller named none,
    #: which is every 0.8.0 call. **Not part of the action hash** (§6.3.1): a field on `Action`
    #: would move every hash in existence and invalidate every stored approval.
    task: str | None = None
    #: SPEC-v0.9 §5.5: `"sha256:…"` over what the scope provider returned, or `None` where none
    #: was configured. **The hash and never the scope**: a scope is a list of what a principal
    #: may touch, and an evidence store is not the place to accumulate a second copy of an
    #: authorization system's state (`v0.7 §6.10`). Its own domain tag, so it can never equal a
    #: precondition fingerprint over the same mapping.
    scope_hash: str | None = None
    #: SPEC-v0.10 §3.4: the hop this action ran **under**, or `None`. One string, and the same
    #: string on both of a hop's ends, which is §1.2's rule 3.
    #:
    #: **Never the hop this action created** (§3.4.4). A relay presents one hop and creates
    #: another in the same action, and a receipt is evidence about a decision: the decision was
    #: made against the presented hop, and the created one authorised nothing here. It is named
    #: by its own `DELEGATION_CREATED` event instead.
    #:
    #: Not part of the action hash, for `v0.9 §6.3.1`'s reason, which is unchanged: a field on
    #: `Action` moves every hash in existence and invalidates every stored approval.
    hop: str | None = None
    #: SPEC-v0.9 §10.1: which grants this action charged, which metrics, how much. One entry per
    #: ancestor charged (§2.7), so a reader can tell an action that spent a child's budget from
    #: one that spent a root's. Empty where the deciding grant budgets nothing, which is every
    #: grant written before v0.9.
    #:
    #: **On an `observed` receipt it is a counterfactual, not a spend** (§4.2.1a). Observe mode
    #: charges nothing and its ledger stays empty, so this carries what the action *would have*
    #: been charged, which is the number a budget is sized from before it is turned on. `result`
    #: is what tells the two apart, and `v0.3 §6.2` makes every number on an observed receipt a
    #: counterfactual; a consumer summing these to measure real spend must filter on it.
    budget_charges: tuple[Mapping[str, Any], ...] = ()
    #: The schema this receipt is written under (§6.11). A receipt this binary builds is
    #: `RECEIPT_SCHEMA`; one read from a store keeps the label its document declared, or `""`
    #: where it declared none, which renders with no `schema` key at all.
    schema: str = RECEIPT_SCHEMA
    #: SPEC-v0.7 §6.11: **hash what was stored.** The document this receipt was read from, set
    #: by the store read path *after* its own `replace(..., hash=...)`, and `None` on a receipt
    #: this binary built. `chain_hash()` hashes it when present.
    #:
    #: Not an `__init__` parameter and not carried through `dataclasses.replace()` (rule (a)):
    #: a modified copy has no stored document and is hashed from what it now says, so G11's own
    #: tamper, `replace(target, decision_reason=...)`, is still `content_altered`, and a read-back
    #: receipt written again is hashed from the dictionary `put_receipt` serializes. Excluded from
    #: equality, because two receipts saying the same thing are the same evidence however each
    #: was obtained.
    _stored_document: Mapping[str, Any] | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def chain_hash(self) -> str:
        """This receipt's own hash: `sha256:` + SHA-256 of its canonical form (§6.2).

        **Derived, and deliberately not a field.** A document cannot contain its own hash, so
        `hash` is a column and this recomputes it -- which is what makes the chain checkable by
        any reader rather than only by the writer.

        The canonical form is `v0.1 §2.3`'s, through `canonical_bytes`. A second canonicalizer
        is the drift this codebase must not have (§6.2).

        SPEC-v0.7 §6.11: a receipt read from a store is hashed as **the document it was read
        from**, not as this binary would render it. Every schema then rehashes to its stored
        hash, and every tamper the schema bump could hide (a key added, a label changed or
        removed, a label nobody knows) changes that document and is `content_altered` by
        construction, with no rule about key sets for a reader to get wrong.
        """
        document = self._stored_document
        return _document_hash(self.to_dict() if document is None else document)

    def to_dict(self) -> dict[str, Any]:
        """The receipt as plain JSON-serializable data, in the field order of SPEC §6.1.

        Rendered under its own schema's label and key set (SPEC-v0.7 §6.11): a `v4` receipt as
        `v4`, a `v3` one as the `v3` document, and a `v1` or `v2` one under that version's label
        and keys, where 0.6.1 rendered all three under the `v3` label. A label this binary does
        not know renders under `v3`'s keys, the widest set it reads whatever the label says,
        and never shows the two `v4` fields it did not read. The hash no longer depends on
        this rendering for a receipt read from a store.
        """
        full = self._full_document()
        keys = _KEYS.get(self.schema, _V3_KEYS)
        if self.schema == _V1:
            full["principal"] = {"agent": self.principal.agent, "user": self.principal.user}
        if not self.schema:
            keys = keys[1:]
        return {key: full[key] for key in keys}

    def _full_document(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
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
            "seq": self.seq,
            "prev_hash": self.prev_hash,
            "policy_hash": self.policy_hash,
            "policy_version": self.policy_version,
            "controls": list(self.controls),
            "precondition_at_request": self.precondition_at_request,
            "precondition_at_recheck": self.precondition_at_recheck,
            "approvers": [approver.to_dict() for approver in self.approvers],
            "authority_grant_id": self.authority_grant_id,
            # Always present and nullable, like `authority_grant_id` directly above it rather
            # than like `_authority_data`'s omitted keys: `_KEYS` projects a fixed tuple, so a
            # conditional key is a `KeyError`, and a reader tells "no task" from "this binary
            # predates tasks" by the schema label.
            "task": self.task,
            "scope_hash": self.scope_hash,
            "budget_charges": [dict(charge) for charge in self.budget_charges],
            "hop": self.hop,
        }

    def to_json(self) -> str:
        """One JSONL line. Enums render by value (SPEC-v0.1 §6.1)."""
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> Receipt:
        """The inverse of `to_dict`: a receipt read back out of a store or a JSONL file.

        SPEC-v0.7 §6.11: **never raises over the schema or over a key.** `receipts()` builds
        every row through here, so a raise on one tampered row would blind every reader at
        once; an absent or unknown `schema`, or an extra key, is left to the hash to report. The
        two precondition fields are read only from a `v4` document, so no reader surfaces the
        value of a key the document's schema does not declare. A row that cannot be parsed at
        all -- not an object, or missing a field every schema has -- raises as it did at 0.6.1.
        """
        declared = document.get("schema")
        schema = declared if isinstance(declared, str) else ""
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
                claims=_claims_of(principal.get("claims")),
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
            # `.get` for the same reason as the two above: a receipt written before v0.6 carries
            # neither key and must parse back rather than raise. §6.5 reports those as
            # `unchained`, which is never a pass.
            seq=document.get("seq"),
            prev_hash=document.get("prev_hash"),
            policy_hash=document.get("policy_hash"),
            policy_version=document.get("policy_version"),
            controls=_controls_of(document.get("controls")),
            precondition_at_request=(
                document.get("precondition_at_request") if schema in (_V4, _V5, _V6, _V7) else None
            ),
            precondition_at_recheck=(
                document.get("precondition_at_recheck") if schema in (_V4, _V5, _V6, _V7) else None
            ),
            # SPEC-v0.8 §2.5, and `v0.7 §6.11`'s rule for a key a document's schema does not
            # declare: read it only from a `v5` document, so no reader surfaces a field an older
            # writer never wrote. **Never raises**, whatever the column holds: `from_dict` is
            # the one function every reader of a chain goes through, and a raise on one tampered
            # row would blind every reader at once.
            approvers=_approvers_of(document.get("approvers")) if schema in (_V5, _V6, _V7) else (),
            authority_grant_id=(
                document.get("authority_grant_id") if schema in (_V5, _V6, _V7) else None
            ),
            # SPEC-v0.9 §6, under `v0.7 §6.11`'s rule: read only from a `v6` document, so no
            # reader surfaces a field an older writer never wrote. A non-string is dropped rather
            # than raised on, for the reason three fields above: `from_dict` is what every reader
            # of a chain goes through.
            task=(
                document.get("task")
                if schema in (_V6, _V7) and isinstance(document.get("task"), str)
                else None
            ),
            scope_hash=(
                document.get("scope_hash")
                if schema in (_V6, _V7) and isinstance(document.get("scope_hash"), str)
                else None
            ),
            budget_charges=(
                tuple(
                    entry
                    for entry in document.get("budget_charges", ())
                    if isinstance(entry, Mapping)
                )
                if schema in (_V6, _V7) and isinstance(document.get("budget_charges"), list)
                else ()
            ),
            hop=(
                document.get("hop")
                if schema == _V7 and isinstance(document.get("hop"), str)
                else None
            ),
            schema=schema,
        )

    @classmethod
    def from_json(cls, line: str) -> Receipt:
        """Parse one JSONL line written by `to_json`."""
        document: dict[str, Any] = json.loads(line)
        return cls.from_dict(document)


def _claims_of(value: object) -> dict[str, Any]:
    """`Principal.claims` out of a document, never raising (`v0.7 §6.11`, SPEC-v0.8 §3.4).

    `_frozen_claims` refuses a claim JSON can hold — an array of numbers, a float, a nested
    object — and `Principal` runs it on construction, so a receipt carrying one raised out of
    `from_dict` and blinded every reader of the chain rather than the one field. Dropped here
    for `_approvers_of`'s reason, and the drop is visible: the receipt reads back with fewer
    claims than the principal that produced it, and its stored hash no longer matches.
    """
    if not isinstance(value, Mapping):
        return {}
    # `bool | int | str` and no float branch: a float is not an `int` in Python, so it falls
    # through to the drop below with every other shape. A branch that cannot fire is
    # documentation, not defence.
    kept: dict[str, Any] = {}
    for name, claim in value.items():
        if not isinstance(name, str) or not name:
            continue
        if isinstance(claim, bool | int | str):
            kept[name] = claim
        elif isinstance(claim, list | tuple) and all(
            isinstance(item, str) and item for item in claim
        ):
            kept[name] = tuple(claim)
    return kept


def _approvers_of(value: object) -> tuple[VerifiedApprover, ...]:
    """`Receipt.approvers` out of a document, never raising (SPEC-v0.8 §2.5, `v0.7 §6.11`).

    A malformed entry is dropped rather than thrown, on the rule `from_dict` already follows:
    one tampered row must not blind every reader of the chain. What a dropped entry costs is
    visible, because the receipt then shows fewer approvers than the row that produced it.
    """
    if not isinstance(value, list):
        return ()
    found = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        try:
            found.append(VerifiedApprover.from_dict(item))
        except (KeyError, ValueError, TypeError, InvalidArgument):
            continue
    return tuple(found)


def _document_hash(document: Mapping[str, Any]) -> str:
    """`"sha256:" + hex(SHA-256(canonical_bytes(document)))`, the chain's one hash (§6.2).

    One function, so `chain_hash()` and a store's `put_receipt` cannot come to hash two
    different things. `put_receipt` hashes the exact dictionary it serializes (SPEC-v0.7 §6.11
    rule (b)), and this is what it hashes it with.
    """
    return "sha256:" + hashlib.sha256(canonical_bytes(document)).hexdigest()


def _stored_receipt(
    document: Mapping[str, Any], stored_hash: str | None, stored_seq: int | None
) -> Receipt:
    """A receipt as a store read it: its stored hash, its stored `seq`, and the document.

    SPEC-v0.7 §6.11: the stored document is set **after** `replace(..., hash=...)`, because
    rule (a) makes `replace()` drop it; setting it first would build a receipt and then throw
    away the one thing the read was for. The only writer of the private field.

    SPEC-v0.11 §5.2: `stored_seq` is the **column**, and it is what a receipt's position comes
    from. Until v0.11 both stores selected `json, hash` and ordered by a column they never read,
    so every `Receipt.seq` came from `document.get("seq")` -- the one field a tamperer controls.
    `verify_chain`'s docstring said position came from the column and it was false as shipped:
    one `UPDATE` to a document's `seq` turned one tamper into four breaks at three positions,
    two of which named rows that do not exist.

    Required rather than defaulted, because both callers are stores reading their own table and a
    default would let a third caller silently reintroduce the document's value.
    """
    receipt = replace(Receipt.from_dict(document), hash=stored_hash, seq=stored_seq)
    object.__setattr__(receipt, "_stored_document", document)
    return receipt


@dataclass(frozen=True)
class UnreadableReceipt:
    """A stored row `Receipt.from_dict` refused, named at its `seq` (SPEC-v0.11 §5.2).

    **One tampered row costs one row** (SPEC-v0.11 §1.1, rule 3). Until v0.11 a single malformed
    *value* of a declared key -- a float where a control id belongs -- raised out of
    `Receipt.from_dict` while a store built every row, so `receipts()` returned nothing at all
    and `ctrlrun receipts`, `receipts --verify-chain`, `ctrlrun inspect`, `ctrlrun stats` and the
    operator server's `_receipts` and `_stats` tools went blind together. `inspect` on an action
    the tamper never touched was the sharp case: the blast radius was not "this receipt is
    unreadable" but "this store is unreadable".

    A reader gets this instead of a raise. It carries where the row is and what refused it, and
    nothing else it could not read.

    **`refusal` is a type name and never a message** (SPEC-v0.7 §6.11's rule): the canonicalizer
    quotes what it refused, and a lone surrogate echoed into a report is a report that cannot be
    printed.

    This is **not** a new `CHAIN_BREAKS` kind. `SPEC-v0.7.md` §12.5 offered that as one of two
    candidates and `SPEC-v0.11.md` §5.1 declines it: `content_altered` already names a document
    that cannot be canonicalized, and a second name for one fact would be two names for one break.
    `verify_chain` reports a row it cannot construct exactly as it already reports a document it
    cannot hash.
    """

    #: The row's position, from the store's `seq` **column**. `None` for a pre-chain row.
    seq: int | None
    #: `receipt_id` if that field alone was readable, else `None`. Never inferred.
    receipt_id: str | None
    #: The **type name** of what refused the row, never its message.
    refusal: str
    #: The row's stored hash, off the column. `None` where the column holds none.
    hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """The refusal as plain data, in the shape a reader prints it."""
        return {
            "seq": self.seq,
            "receipt_id": self.receipt_id,
            "refusal": self.refusal,
            "hash": self.hash,
        }


def _readable(rows: Iterable[Receipt | UnreadableReceipt]) -> tuple[Receipt, ...]:
    """Only the rows that read back (SPEC-v0.11 §5.2).

    **For a caller whose answer a refused row cannot change**, and for no other. A refused row
    has no `action_id` to match, no `finished_at` to bucket and no `result` to count, so a reader
    asking any of those questions can only leave it out.

    Never where its absence would read as a pass. That is `SPEC-v0.4.md` §3.8's false green and
    it is the way rule 3 is most likely to be broken by accident: a grader that quietly dropped a
    row it could not read would report a clean result about a store with a forgery in it.
    `verify_chain` therefore does **not** use this, and neither does anything that grades.
    """
    return tuple(row for row in rows if isinstance(row, Receipt))


def _read_receipt(
    stored_json: str, stored_hash: str | None, stored_seq: int | None
) -> Receipt | UnreadableReceipt:
    """One stored row, as a receipt or as a named refusal (SPEC-v0.11 §5.2).

    The one place a store turns a row into something a reader holds, so the two backends cannot
    come to disagree about what a row it cannot construct becomes.

    **It takes the stored text and not a parsed document, because parsing is one of the ways a
    row refuses.** The first version of this took a `Mapping` and both stores called
    `json.loads(row["json"])` in the generator expression that fed it, so `json` set to anything
    that is not JSON at all raised `JSONDecodeError` *outside* this guard and blinded every
    reader exactly as before -- and worse than before, because `JSONDecodeError` is not a
    `CTRLRunError`, so `cli/main.py`'s handler did not catch it either and `ctrlrun receipts`
    printed a traceback. One `UPDATE receipts SET json = 'not json'` was enough. The rule this
    broke is rule 3 itself, and the reason the first version's tests missed it is that they
    tampered with a row's *content*: `{}` and a float among the controls are both valid JSON.

    `CTRLRunError` and the four builtins `from_dict` can raise: `InvalidArgument` through the
    parsers it calls, `KeyError` or `TypeError` from a row that is not an object at all, which
    `from_dict`'s own docstring says raises as it did at 0.6.1, and `ValueError`, which
    `JSONDecodeError` subclasses. A reader that recovered from one and not another would still be
    blindable by one `UPDATE`.
    """
    document: object = None
    try:
        document = json.loads(stored_json)
        # Narrowed here rather than trusted: `json.loads("3")` is an `int`, and `_stored_receipt`
        # would raise `TypeError` on it, which this catches -- but naming the refusal at the
        # parse says what is wrong with the row rather than what the next line tripped over.
        if not isinstance(document, Mapping):
            raise TypeError(f"a stored receipt must be an object, got {type(document).__name__}")
        return _stored_receipt(document, stored_hash, stored_seq)
    except (CTRLRunError, KeyError, TypeError, ValueError, AttributeError) as refused:
        identifier = document.get("receipt_id") if isinstance(document, Mapping) else None
        return UnreadableReceipt(
            seq=stored_seq,
            receipt_id=identifier if isinstance(identifier, str) else None,
            refusal=type(refused).__name__,
            hash=stored_hash,
        )


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


# --- the chain (SPEC-v0.6 §6) ------------------------------------------------------------------


#: §6.5's closed set of break names. A chain that only catches the easy case is worse than none,
#: because it gets quoted as though it caught all of them -- so a break is reported *by name* and
#: at a `seq`, never as a bare "invalid".
#: What `link_broken` and `head_mismatch` say a row hashes to when nothing can: §6.5's names are
#: a closed set, so a document the canonicalizer refuses is `content_altered` like any other
#: altered document, and the rows that link to it are told why the comparison has no left side.
_NO_HASH: Final = "<no canonical form>"

CHAIN_BREAKS: Final = (
    "content_altered",
    "hash_missing",
    "link_broken",
    "missing",
    "head_mismatch",
    "unchained",
)


@dataclass(frozen=True)
class ChainBreak:
    """One thing wrong with the chain, named (§6.5)."""

    name: str
    seq: int | None
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "seq": self.seq, "detail": self.detail}


@dataclass(frozen=True)
class ChainReport:
    """What `verify_chain` found. `ok` is false if anything at all was wrong (§6.5).

    **`unchained` is never a pass.** A receipt written before the chain existed is reported with
    its count and `ok` is `False`, because folding pre-chain rows into a green count is
    `v0.4 §3.8`'s false green in a new costume: the summary would say "verified" about rows
    nothing verified.
    """

    ok: bool
    #: Receipts that were chained **and** had nothing reported against them. Not the number of
    #: rows with a `seq`: a count of "chained" printed beside a detected forgery reads "3 of 3
    #: receipts verified" about a chain with a forgery in it, which is the false green in prose.
    verified: int
    #: Every row that carries a `seq`, whether or not it survived the walk.
    chained: int
    unchained: int
    breaks: list[ChainBreak]
    head_seq: int | None = None
    head_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "verified": self.verified,
            "chained": self.chained,
            "unchained": self.unchained,
            "head_seq": self.head_seq,
            "head_hash": self.head_hash,
            "breaks": [item.to_dict() for item in self.breaks],
        }


class ChainSource(Protocol):
    """What `verify_chain` needs from a store: the receipts, and the head (§6.3, §6.5).

    A `Protocol` rather than an import of `StateStore`, because `state.py` imports *this* module
    and `ARCHITECTURE.md` §6 says dependencies point downward.
    """

    def receipts(self) -> tuple[Receipt | UnreadableReceipt, ...]: ...

    def chain_head(self) -> tuple[int, str] | None: ...

    # `checkpoint()` is read where a source has one (SPEC-v0.11 §4.2), and is **not** declared
    # here. A source without one has not been pruned, which is what every store said before
    # v0.11 and what every test double still says; requiring it would make this protocol's own
    # amendment a breaking change for a method whose answer is almost always `None`.


def _checkpoint_of(store: ChainSource) -> tuple[int, str] | None:
    """The `seq` a prune pruned through and the hash at it, where this source has one (§4.2).

    **The row, never a receipt field.** `SPEC-v0.3.md` §4.3.1 settled that shape: a grant may
    legally be named `no_authority`, so evidence that could be spoofed by naming one is not
    evidence, and a walk that believed `action == "ctrlrun.retention.prune"` would accept a
    forged prefix-erasure written by anyone who can insert a row.

    This does **not** make a checkpoint unforgeable: a writer who can insert receipts can write
    one. What closes that is `SPEC-v0.11.md` §3's anchor, and only for the window between
    anchors. The two features are one argument.
    """
    reader = getattr(store, "checkpoint", None)
    if reader is None:
        return None
    found = reader()
    return None if found is None else (int(found[0]), str(found[1]))


def verify_chain(store: ChainSource) -> ChainReport:
    """Walk the store's receipt chain and name every break (SPEC-v0.6 §6.5).

    **Position comes from the store's `seq` column; content comes from the document.** True since
    v0.11 and not before: both stores selected `json, hash` and ordered by a column they never
    read, so every position this walked came out of the document after all (SPEC-v0.11 §5.2). One
    `UPDATE` setting a document's `seq` to 99 then produced `missing 2`, `content_altered 99`,
    `missing 100` and `link_broken 3` -- four breaks at three positions, two of them rows that do
    not exist -- where the same tamper now reports `content_altered` once, at 2.

    The store
    returns receipts ordered by that column, and this walks them in that order without re-sorting
    -- which is what makes the column load-bearing rather than decorative, and what makes §6.5's
    table true. A reader that re-sorted by the *document's* `seq` would be checking the document
    against itself: swapping two rows' `seq` columns would then reorder nothing it could see, and
    the one tamper that leaves both documents byte-for-byte intact would be invisible. Physical
    row order is what no store promises, which is why nothing here depends on it.

    Rows with `seq IS NULL` have **no position**. They are counted separately and never
    interleaved: a `NULL` sorted to either end would manufacture a gap at one end or a head
    mismatch at the other.

    What this does not do is in §6.4, and the most important line of it is that a chain says the
    log was not altered and says nothing about who wrote it. Somebody who can rewrite every row
    *including the head* recomputes it and it verifies; `THREAT_MODEL.md` has always listed a
    malicious administrator as out of scope. What this closes is the partial tamper.
    """
    receipts: list[Receipt | UnreadableReceipt] = list(store.receipts())
    # In the store's order, which is by the `seq` **column**, and not re-sorted here. A reader
    # that re-sorted by the *document's* `seq` would be checking the document against itself.
    chained = [receipt for receipt in receipts if receipt.seq is not None]
    unchained = [receipt for receipt in receipts if receipt.seq is None]
    breaks: list[ChainBreak] = []

    if unchained:
        breaks.append(
            ChainBreak(
                "unchained",
                None,
                f"{len(unchained)} receipt(s) were written before the chain existed and carry no "
                "seq; nothing about them is verified",
            )
        )

    # SPEC-v0.11 §4.1: **three values, not one.** A prune moves the chain's start, and this walk
    # seeds two genesis values and compares the head against a third. A checkpoint that replaced
    # only the hash still reported `missing` at seq 1, so a faithful implementation of the first
    # draft built a prune §1.1's rule 2 forbids:
    #
    #     after PREFIX delete of seq<=3  -> breaks: [('missing', 1), ('link_broken', 4)]
    #     seeded from the checkpoint HASH only
    #                                    -> breaks: [('missing', 1)]
    #
    # Read defensively, because a `ChainSource` is a protocol an operator's own backend and this
    # project's own test doubles implement: one without a checkpoint has not been pruned.
    checkpoint = _checkpoint_of(store)
    expected_prev = GENESIS_HASH if checkpoint is None else checkpoint[1]
    expected_seq = 1 if checkpoint is None else checkpoint[0] + 1
    for receipt in chained:
        seq = receipt.seq
        assert seq is not None  # filtered above; this narrows the type
        if seq != expected_seq:
            breaks.append(
                ChainBreak(
                    "missing",
                    expected_seq,
                    f"seq {expected_seq} is not here; the next receipt says it is seq {seq}. A "
                    "row was deleted, or two were exchanged",
                )
            )
            # Resync on what is actually there, so one hole reports one gap rather than
            # renumbering every receipt after it.
            expected_seq = seq
        if isinstance(receipt, UnreadableReceipt):
            # SPEC-v0.11 §5.2: a row the store could not construct is reported exactly as a
            # document that cannot be canonicalized is reported four lines below, and for the
            # same reason: `put_receipt` builds what it stores, so a row `from_dict` refuses is a
            # row nothing in this library wrote. §5.1 declines SPEC-v0.7 §12.5's other candidate
            # here: a second break name for one fact would be two names for one break.
            #
            # By type, never by message (SPEC-v0.7 §6.11).
            breaks.append(
                ChainBreak(
                    "content_altered",
                    seq,
                    f"the stored row cannot be read back as a receipt ({receipt.refusal}), so "
                    "its hash cannot be recomputed; nothing that writes receipts could have "
                    "stored it",
                )
            )
            expected_prev = _NO_HASH
            expected_seq = seq + 1
            continue
        try:
            recomputed = receipt.chain_hash()
        except CTRLRunError as refused:
            # SPEC-v0.7 §6.11: a stored document this reader cannot canonicalize is a document
            # nothing in this library wrote -- `put_receipt` hashes what it serializes, so every
            # row it wrote canonicalizes by construction. It is therefore **altered**, and named
            # here rather than raised out of the walk: one such row used to stop the whole read,
            # so `ctrlrun receipts --verify-chain` exited with no report at all and a forgery at
            # another `seq` went unnamed.
            #
            # By its type, never its message: the canonicalizer quotes what it refused, and a
            # lone surrogate in a report is a report that cannot be printed.
            breaks.append(
                ChainBreak(
                    "content_altered",
                    seq,
                    f"the stored document has no canonical form ({type(refused).__name__}), so "
                    "its hash cannot be recomputed; nothing that writes receipts could have "
                    "stored it",
                )
            )
            expected_prev = _NO_HASH
            expected_seq = seq + 1
            continue
        stored = receipt.hash
        if stored is None:
            # A chained row whose stored hash is gone. **Not a skip.** This was the only check
            # against the column, and skipping it meant `UPDATE receipts SET hash=NULL` -- one
            # statement, no `WHERE` -- destroyed every independent copy of every receipt's hash
            # while the chain reported itself intact and exited 0. `v0.4 §3.8`'s false green
            # exactly: a check that could not be made, resolved as a pass. Found by review.
            #
            # `unchained` does not cover it. That name is for a receipt with no `seq`, written
            # before the chain existed; this row has a `seq` and has been stripped.
            breaks.append(
                ChainBreak(
                    "hash_missing",
                    seq,
                    "the stored hash is gone, so there is nothing to compare the document "
                    "against; the row was chained and something removed it",
                )
            )
        elif stored != recomputed:
            breaks.append(
                ChainBreak(
                    "content_altered",
                    seq,
                    f"the recomputed hash {recomputed} is not the stored {stored}",
                )
            )
        if receipt.prev_hash != expected_prev:
            breaks.append(
                ChainBreak(
                    "link_broken",
                    seq,
                    f"prev_hash is {receipt.prev_hash}, and the receipt before it hashes to "
                    f"{expected_prev}",
                )
            )
        # The **document's** hash, never the column's. Carrying the stored value forward let one
        # corrupted column accuse the next receipt of `link_broken` as well, so a single tamper
        # read as two and an operator went looking for the wrong one.
        expected_prev = recomputed
        expected_seq = seq + 1

    head = store.chain_head()
    head_seq, head_hash = (None, None) if head is None else head
    if head is None:
        breaks.append(
            ChainBreak("head_mismatch", None, "the store has no chain head row to compare against")
        )
    else:
        # With a checkpoint and an empty chain, the head is the checkpoint: everything the head
        # named was pruned, and the checkpoint is what accounts for it.
        last_seq = chained[-1].seq if chained else (0 if checkpoint is None else checkpoint[0])
        last_hash = expected_prev
        if head_seq != last_seq or head_hash != last_hash:
            breaks.append(
                ChainBreak(
                    "head_mismatch",
                    head_seq,
                    f"the head names seq {head_seq} / {head_hash}, and the last receipt is "
                    f"seq {last_seq} / {last_hash}",
                )
            )

    accused = {item.seq for item in breaks if item.seq is not None}
    return ChainReport(
        ok=not breaks,
        verified=sum(1 for item in chained if item.seq not in accused),
        chained=len(chained),
        unchained=len(unchained),
        breaks=breaks,
        head_seq=head_seq,
        head_hash=head_hash,
    )
