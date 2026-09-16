# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""Enforcement coverage: what this deployment has never exercised. SPEC-v0.11 §7.

The runtime half of `ctrlrun scan`, whose static half landed in v0.10. `scan` reads the source
and asks *is this call protected?*. This reads what the deployment has already recorded and asks
a different question: **which of the things you declared has nothing ever gone through?**

**From what is already written.** No new event type, no new column. The action name lives on the
**receipt** rather than on the event, which is a fact worth stating because it decides the whole
design: `ACTION_PROPOSED` carries an `action_hash` and nothing that maps it back to a name, and
every action that reached a decision leaves a receipt, a denial included. So the question is
answerable, and if it had needed a new event the answer would have been to say so and stop.

**Rule 4 (§1.1): a clean result is not a verdict.** No score, no percentage, no ratio, no badge,
and no sentence a reader could quote as one. `SPEC-v0.4.md` §3.9 is the precedent and it is worth
stating in full: `verify` never grades an operator's document, and a coverage number that ranked
their deployment would be the same claim in a new costume.

**A policy entry nothing exercised may be correctly unused.** An action declared for a quarterly
job, a deny rule that exists so the action is refused rather than unknown, a tool nobody has
needed yet: each is a reasonable thing to find here, and none of them is a defect. This module
reports a **list with a reason**, and the reason is a statement about the record rather than
about the operator.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, Protocol

#: `ctrlrun.coverage/v1`. Its own schema because it is its own document: `ctrlrun scan`'s report
#: answers a question about source and this answers one about a store, and folding them into one
#: name would make a consumer parse two shapes under it.
COVERAGE_SCHEMA: Final = "ctrlrun.coverage/v1"


class Unexercised(StrEnum):
    """What kind of thing nothing has gone through."""

    POLICY_ACTION = "policy_action"
    GATEWAY_TOOL = "gateway_tool"
    PROTECTED_ACTION = "protected_action"


#: The reason each kind carries. **A statement about the record, never about the operator**: it
#: says what was not found, and says in the same breath that not finding it may be correct.
_REASON: Final = {
    Unexercised.POLICY_ACTION: (
        "the policy declares this action and no receipt in this store names it"
    ),
    Unexercised.GATEWAY_TOOL: (
        "the gateway exposes this tool and no receipt in this store names the action it routes to"
    ),
    Unexercised.PROTECTED_ACTION: (
        "@protect declares this action in the source and no receipt in this store names it"
    ),
}


@dataclass(frozen=True)
class Unused:
    """One declared thing nothing has exercised, and why it is on the list."""

    kind: Unexercised
    name: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"kind": str(self.kind), "name": self.name, "reason": self.reason}


@dataclass(frozen=True)
class CoverageReport:
    """The list, and what it was computed from (§7).

    **There is no `score`, no `percentage`, no `ratio` and no `exit_code` that grades.** The
    counts here are inputs an operator needs to read the list at all -- *nothing exercised, over
    a store holding no receipts* and *nothing exercised, over a store holding forty thousand* are
    different findings -- and neither is a verdict. `T560` greps this module's own output for the
    vocabulary rule 4 forbids.
    """

    #: Everything declared that nothing has exercised, in codepoint order within each kind.
    unused: tuple[Unused, ...]
    #: How many receipts the answer was computed from. Context, not a denominator.
    receipts_read: int
    #: How many distinct action names those receipts carry.
    actions_seen: tuple[str, ...]
    #: Where the declarations came from, so a reader can disagree with the list.
    policy_path: str | None = None

    def of(self, kind: Unexercised) -> tuple[Unused, ...]:
        return tuple(item for item in self.unused if item.kind is kind)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": COVERAGE_SCHEMA,
            "policy": self.policy_path,
            "receipts_read": self.receipts_read,
            "actions_seen": list(self.actions_seen),
            "unused": [item.to_dict() for item in self.unused],
        }


class _CoverageStore(Protocol):
    def receipts(self) -> tuple[Any, ...]: ...


def _names_seen(store: _CoverageStore) -> tuple[str, ...]:
    """Every action name this store's receipts carry, in codepoint order.

    **Receipts, not events**, and the reason is not a preference: `ACTION_PROPOSED` carries an
    `action_hash` and nothing that maps it back to a name, so the events alone cannot answer the
    question. Every action that reached a decision leaves a receipt -- a denial included, which
    matters here, because an action that is always denied **has** been exercised and belongs
    nowhere on this list.

    A row this binary cannot read back (`SPEC-v0.11.md` §5.2) carries no action name and is
    skipped. It is already reported as `content_altered` by the chain, and a coverage list is not
    the place to report a tamper a second time under a different name.
    """
    seen = {
        name
        for receipt in store.receipts()
        for name in (getattr(receipt, "action", None),)
        if isinstance(name, str) and name
    }
    return tuple(sorted(seen))


def coverage(
    store: _CoverageStore,
    *,
    policy_actions: Sequence[str] = (),
    gateway_tools: Sequence[tuple[str, str]] = (),
    protected_actions: Sequence[str] = (),
    policy_path: str | None = None,
) -> CoverageReport:
    """What this deployment declared and has never exercised (§7).

    Everything is **supplied** rather than discovered: the policy's actions come from the loaded
    policy, the gateway's tools from its configuration, and `@protect`'s actions from `scan`'s
    static pass. This module reads a store and matches, in the shape `SPEC-v0.9.md` §5.4 settled
    for a scope provider, and for the same reason: a module that resolved an operator's document
    would be reading the policy from the wrong layer.

    `gateway_tools` is `(tool, action)` because a tool's own name is what an operator recognises
    and the action is what a receipt would carry.
    """
    seen = set(_names_seen(store))
    found: list[Unused] = []

    for action in sorted(set(policy_actions)):
        if action not in seen:
            found.append(
                Unused(Unexercised.POLICY_ACTION, action, _REASON[Unexercised.POLICY_ACTION])
            )
    for tool, action in sorted(set(gateway_tools)):
        if action not in seen:
            found.append(Unused(Unexercised.GATEWAY_TOOL, tool, _REASON[Unexercised.GATEWAY_TOOL]))
    for action in sorted(set(protected_actions)):
        if action not in seen:
            found.append(
                Unused(Unexercised.PROTECTED_ACTION, action, _REASON[Unexercised.PROTECTED_ACTION])
            )

    return CoverageReport(
        unused=tuple(found),
        receipts_read=len(store.receipts()),
        actions_seen=tuple(sorted(seen)),
        policy_path=policy_path,
    )


#: The sentence this report ends with, always, whether the list is empty or not.
#:
#: **Rule 4's whole content, in the place a reader cannot miss.** An empty list is the one most
#: likely to be quoted as a verdict, so the sentence is not conditional on there being findings.
#: `SPEC-v0.4.md` §3.9's precedent: `verify` never grades an operator's document.
NOT_A_VERDICT: Final = (
    "This is a list of what has not been exercised, not a score. A policy entry nothing "
    "exercised may be correctly unused: a quarterly job, a deny rule that exists so the action "
    "is refused rather than unknown, a tool nobody has needed yet."
)


def coverage_lines(report: CoverageReport) -> list[str]:
    """The human rendering, in the shape `ctrlrun scan` already uses (§7).

    A list with a reason per entry, and the sentence above. **No totals line, no ratio, and no
    "N of M"**: the counts that appear are the inputs, labelled as such.
    """
    lines = ["ctrlrun scan --coverage", ""]
    lines.append(
        f"read {report.receipts_read} receipt(s), naming {len(report.actions_seen)} action(s)"
    )
    lines.append("")
    for kind in Unexercised:
        entries = report.of(kind)
        if not entries:
            continue
        lines.append(f"never exercised: {kind} ({len(entries)})")
        for entry in entries:
            lines.append(f"  {entry.name}")
            lines.append(f"    {entry.reason}")
        lines.append("")
    if not report.unused:
        lines.append("everything declared has been exercised at least once")
        lines.append("")
    lines.append(NOT_A_VERDICT)
    return lines
