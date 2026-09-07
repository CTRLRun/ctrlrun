"""The portable documents `ctrlrun inspect --json` and `ctrlrun stats --json` produce.

SPEC-mcp-operator.md §9.1. These lived in `ctrlrun/cli/main.py` until the operator MCP server
needed the same two documents: `ctrlrun.inspection/v2` and `ctrlrun.stats/v1` are what an
evidence pipeline reads, and a second producer that agreed with the first on the day it was
written is a pair that will disagree later. So there is one producer each, here, and two
callers -- the CLI, which renders a document for a human, and `ctrlrun.gateway.operator`, which
returns it to an assistant.

Core and stdlib, above `control.py` and beside `cli/` and `verify/`: it reads what a store
already holds and composes nothing (`ARCHITECTURE §6`). Nothing in the kernel imports it, and
`import ctrlrun` does not (T192).

Only the CLI prints. Nothing here formats a line for a terminal.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from .approval import ApprovalRecord
from .effect import EffectRecord
from .errors import InvalidArgument
from .policy import OBSERVE, Decision
from .receipt import (
    BLOCKED_APPROVAL_REQUIRED,
    BLOCKED_BY_STATE,
    Event,
    EventType,
    Receipt,
    ReceiptResult,
    iso_timestamp,
)
from .state import StateStore

#: SPEC-v0.2 §5 — the schema of one `ctrlrun inspect --json` document.
#: SPEC-v0.3 §12.2 — v2 where the header block gained the principal's issuer, expiry and
#: claim names. The values reach `--json`; the human block shows only the names, because a
#: claim can hold an employee number or a case id and that output is read over shoulders.
INSPECTION_SCHEMA: Final = "ctrlrun.inspection/v2"

#: SPEC-v0.3 §6.4 — one `ctrlrun stats --json` document.
STATS_SCHEMA: Final = "ctrlrun.stats/v1"

#: The three relative units of SPEC-v0.3 §6.4, and the `timedelta` keyword each names.
_RELATIVE_UNITS: Final[Mapping[str, str]] = {"m": "minutes", "h": "hours", "d": "days"}


def inspection_for(store: StateStore, action_id: str) -> dict[str, Any] | None:
    """One action's whole history, chosen and assembled, or `None` if there is no such action.

    **The choosing is here and not at the call site**, and that is the correction an independent
    review asked for: moving only the serializer left `ctrlrun inspect --json` and the operator
    server each deciding *which* receipt, *which* effect key and *which* `action_hash` — and the
    receipt-versus-`ACTION_PROPOSED` fallback below is the subtle part, the one that decides
    whether an action still awaiting a human can be inspected at all. Two copies of that agree
    today and disagree later, which is the whole argument for this module.
    """
    events = tuple(event for event in store.events() if event.action_id == action_id)
    receipt = next((found for found in store.receipts() if found.action_id == action_id), None)
    if not events and receipt is None:
        return None

    # The hash comes from the receipt, or from `ACTION_PROPOSED` for an action still awaiting a
    # human — which has no receipt yet (SPEC-v0.1 §6.1) and is exactly the case where the
    # pending request is the only thing there is to show.
    action_hash = receipt.action_hash if receipt is not None else None
    if action_hash is None:
        action_hash = next(
            (
                str(event.data["action_hash"])
                for event in events
                if event.type is EventType.ACTION_PROPOSED and "action_hash" in event.data
            ),
            None,
        )
    approvals: tuple[ApprovalRecord, ...] = (
        () if action_hash is None else store.approvals_for(action_hash)
    )

    key = receipt.effect_key if receipt is not None else None
    if key is None:
        key = next((event.effect_key for event in events if event.effect_key), None)
    effect = None if key is None else store.get_effect(key)

    return inspection_document(action_id, receipt, effect, approvals, events)


def inspection_document(
    action_id: str,
    receipt: Receipt | None,
    effect: EffectRecord | None,
    approvals: Sequence[ApprovalRecord],
    events: Sequence[Event],
) -> dict[str, Any]:
    """One action's whole history as portable JSON (SPEC-v0.2 §5)."""
    return {
        "schema": INSPECTION_SCHEMA,
        "action_id": action_id,
        "receipt": None if receipt is None else receipt.to_dict(),
        "effect": None if effect is None else effect_document(effect),
        "approvals": [approval_document(record) for record in approvals],
        "events": [event.to_dict() for event in events],
    }


def effect_document(record: EffectRecord) -> dict[str, Any]:
    """The effect record as plain JSON data, enums by value (SPEC-v0.1 §6.1)."""
    return {
        "effect_key": record.effect_key,
        "state": str(record.state),
        "action_id": record.action_id,
        "attempt": record.attempt,
        "created_at": iso_timestamp(record.created_at),
        "updated_at": iso_timestamp(record.updated_at),
        "lease_expires_at": (
            None if record.lease_expires_at is None else iso_timestamp(record.lease_expires_at)
        ),
        "error": record.error,
        #: Which authority moved this out of `AMBIGUOUS`, or `None` (SPEC-v0.6 §5.3). Additive
        #: to `ctrlrun.inspection/v2`: a reader that does not know the key ignores it.
        "resolved_by": record.resolved_by,
    }


def approval_document(record: ApprovalRecord) -> dict[str, Any]:
    """One approval record as plain JSON data, enums by value (SPEC-v0.1 §6.1)."""
    return {
        "approval_id": record.approval_id,
        "action_hash": record.action_hash,
        "status": str(record.status),
        "approver": record.approver,
        "created_at": iso_timestamp(record.request.created_at),
        "expires_at": iso_timestamp(record.expires_at),
        "granted_at": None if record.granted_at is None else iso_timestamp(record.granted_at),
        "consumed_at": None if record.consumed_at is None else iso_timestamp(record.consumed_at),
    }


def since_boundary(argument: str | None) -> datetime | None:
    """Parse a `--since` window into an inclusive lower bound (SPEC-v0.3 §6.4).

    Absolute ISO-8601 **with an offset**, or a relative `<n><unit>` where the unit is exactly
    one of `m`, `h` or `d`. No months, no weeks, no bare numbers: `2mo` and `1w` are ambiguous
    enough that guessing one would silently report the wrong window, which is worse than
    refusing.

    Raises `InvalidArgument`. The CLI turns that into a `click.UsageError` so the exit code
    stays 2; the operator server turns it into `-32602`.
    """
    if argument is None:
        return None
    text = argument.strip()
    # `.isascii()` as well as `.isdigit()`: `str.isdigit()` is true for '²' and '١٢', and
    # `int()` refuses both, so a superscript or an Arabic-Indic digit raised a bare `ValueError`
    # out of a function documented to raise `InvalidArgument`.
    digits = text[:-1]
    if (
        text
        and text[-1] in _RELATIVE_UNITS
        and digits.isascii()
        and digits.isdigit()
        and int(digits) > 0
    ):
        return datetime.now(UTC) - timedelta(**{_RELATIVE_UNITS[text[-1]]: int(digits)})
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        raise InvalidArgument(
            f"--since {argument!r} is not a window this command accepts. Give an ISO-8601 "
            "timestamp with an offset (2026-09-01T00:00:00Z), or one of <n>m, <n>h, <n>d "
            "(30m, 24h, 7d). No months, no weeks, and no bare numbers"
        )
    return parsed


def stats_document(
    counted: Sequence[Receipt], *, mode: str, boundary: datetime | None
) -> dict[str, Any]:
    """The numbers of SPEC-v0.3 §6.4, from `would_have` in observe mode and `result` in enforce.

    The two are deliberately different shapes. An enforce-mode receipt carries no
    counterfactual and no structured `blocked_reason` — a `blocked` receipt keeps the
    duplicate/ambiguous distinction only inside `error` as exception text (SPEC-v0.1 §6.1) — so
    the document reports what it can substantiate and the CLI's footer says what it cannot.
    """
    finished = [receipt.finished_at for receipt in counted]
    document: dict[str, Any] = {
        "schema": STATS_SCHEMA,
        "mode": mode,
        "since": None if boundary is None else iso_timestamp(boundary),
        "from": iso_timestamp(min(finished)) if finished else None,
        "to": iso_timestamp(max(finished)) if finished else None,
        "actions": len(counted),
    }
    if mode != OBSERVE:
        refused = [r for r in counted if r.result is ReceiptResult.DENIED]
        document["denied"] = len(refused)
        document["denied_by_reason"] = tally(r.decision_reason for r in refused)
        document["ambiguous_outcomes"] = len(
            [r for r in counted if r.result is ReceiptResult.AMBIGUOUS]
        )
        return document
    # Every observe-mode number comes off `would_have`, so the counterfactuals are pulled out
    # once. A receipt with none was written by one of §6.2's still-refuses rows: the action was
    # genuinely stopped and there is nothing counterfactual to count.
    counterfactuals = [r.would_have for r in counted if r.would_have is not None]
    denied = [w for w in counterfactuals if w.decision is Decision.DENY]
    blocked = [w for w in counterfactuals if w.blocked_reason in BLOCKED_BY_STATE]
    document["would_have_been_denied"] = len(denied)
    document["denied_by_reason"] = tally(w.blocked_reason or w.reason for w in denied)
    document["would_have_needed_approval"] = len(
        [w for w in counterfactuals if w.blocked_reason == BLOCKED_APPROVAL_REQUIRED]
    )
    document["would_have_been_blocked"] = len(blocked)
    document["blocked_by_reason"] = tally(w.blocked_reason for w in blocked)
    document["ambiguous_outcomes"] = len(
        [r for r in counted if r.execution is ReceiptResult.AMBIGUOUS]
    )
    return document


def tally(reasons: Iterable[str | None]) -> dict[str, int]:
    """Counts by reason, largest first, then alphabetically so the output is stable."""
    counts: dict[str, int] = {}
    for reason in reasons:
        counts[str(reason)] = counts.get(str(reason), 0) + 1
    return dict(sorted(counts.items(), key=lambda pair: (-pair[1], pair[0])))
