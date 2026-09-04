"""The report and its three renderings. SPEC-v0.4 §4.

`run()` performs the work and returns a `Report`; rendering it costs nothing and can be done
more than once. `exit_code` lives here rather than in the CLI so §4.4 has one implementation,
and so a caller embedding verify in their own tool reaches the same answer the command does.

Every enum renders **by value** (§4, T117), which is `v0.1 §6.1`'s rule applied to a new
document: evidence has to be readable by something that never imported CTRLRun.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ElementTree
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Final

from .guarantees import CATALOGUE, EFFECT_TEMPLATE_NOTE, GUARANTEES

#: SPEC-v0.4 §4.2 — the schema of one `ctrlrun verify --json` document.
REPORT_SCHEMA: Final = "ctrlrun.verify/v1"

#: §4.3 — the JUnit suite and class names, fixed so a CI dashboard's history is stable.
SUITE_NAME: Final = "ctrlrun.verify"
CLASS_NAME: Final = "ctrlrun.guarantees"

#: §5.2 — the two Shields colours. There is no amber for N/A: the badge's colour is about
#: failures, and the N/A count lives in the report the badge links to.
BADGE_PASS_COLOR: Final = "brightgreen"
BADGE_FAIL_COLOR: Final = "red"
BADGE_LABEL: Final = "CTRLRun"

_TITLE_WIDTH: Final = 32


class Status(StrEnum):
    """The closed set of four (§4). `SKIPPED` exists only under `--only` (§4.6)."""

    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class Counterexample:
    """Enough evidence to reproduce a violation without rerunning verify (§4.5).

    `receipts` and `events` are the portable JSON of `receipt.py` — the same documents
    `ctrlrun inspect` emits — in `event_id` order, for the scenario's actions only. One
    receipt cannot show a double execution, which is why T104 asserts both are present.
    """

    expected: str
    observed: str
    receipts: tuple[Mapping[str, Any], ...] = ()
    events: tuple[Mapping[str, Any], ...] = ()
    effects: tuple[Mapping[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "expected": self.expected,
            "observed": self.observed,
            "receipts": [dict(item) for item in self.receipts],
            "events": [dict(item) for item in self.events],
            "effects": [dict(item) for item in self.effects],
        }

    def to_text(self) -> str:
        """The rendered form the human report indents and the JUnit failure text carries."""
        lines = [f"expected: {self.expected}", f"observed: {self.observed}"]
        for receipt in self.receipts:
            lines.append(
                f"receipt {receipt.get('receipt_id')} {receipt.get('action')} "
                f"result={receipt.get('result')} decision={receipt.get('decision')} "
                f"effect_key={receipt.get('effect_key')} attempt={receipt.get('attempt')}"
            )
        for effect in self.effects:
            lines.append(
                f"effect {effect.get('effect_key')} state={effect.get('state')} "
                f"attempt={effect.get('attempt')}"
            )
        for event in self.events:
            lines.append(f"event {event.get('event_id')} {event.get('type')} {event.get('data')}")
        return "\n".join(lines)


@dataclass(frozen=True)
class GuaranteeResult:
    """One line of the report (§2.1, §4.2).

    `reason` is non-null for `not_applicable`, `fail` and `skipped`, and null for `pass`.
    `counterexample` is present **only** on `fail`: a counterexample on a pass would be
    evidence of a failure that did not happen (§4.2, T114).
    """

    id: str
    title: str
    status: Status
    reason: str | None = None
    action: str | None = None
    arguments: Mapping[str, Any] | None = None
    effect_key: str | None = None
    grant_id: str | None = None
    descends_from: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)
    counterexample: Counterexample | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": str(self.status),
            "reason": self.reason,
            "action": self.action,
            "arguments": None if self.arguments is None else dict(self.arguments),
            "effect_key": self.effect_key,
            "grant_id": self.grant_id,
            "descends_from": list(self.descends_from),
            "detail": dict(self.detail),
            "counterexample": (
                None if self.counterexample is None else self.counterexample.to_dict()
            ),
        }


@dataclass(frozen=True)
class Report:
    """What one verify run found, as a value (§9.1)."""

    guarantees: tuple[GuaranteeResult, ...]
    policy: Mapping[str, Any]
    authority: Mapping[str, Any] | None
    store: Mapping[str, Any]
    started_at: datetime
    finished_at: datetime
    ctrlrun_version: str
    partial: bool = False

    # --- counts (§4.1: passes over applicable, and N/A is never summed in) --------------

    @property
    def passed(self) -> int:
        return sum(1 for result in self.guarantees if result.status is Status.PASS)

    @property
    def failed(self) -> int:
        return sum(1 for result in self.guarantees if result.status is Status.FAIL)

    @property
    def not_applicable(self) -> int:
        return sum(1 for result in self.guarantees if result.status is Status.NOT_APPLICABLE)

    @property
    def skipped(self) -> int:
        return sum(1 for result in self.guarantees if result.status is Status.SKIPPED)

    @property
    def applicable(self) -> int:
        """Passes plus failures. `not_applicable` and `skipped` are excluded, by §1's rule."""
        return self.passed + self.failed

    @property
    def badge_message(self) -> str:
        """`verified N/M`, where M is **applicable** and never the catalogue size (§5.2)."""
        return f"verified {self.passed}/{self.applicable}"

    @property
    def exit_code(self) -> int:
        """SPEC-v0.4 §4.4. N/A never changes it by itself; zero applicable is never a pass."""
        if self.failed:
            return 1
        if self.applicable == 0:
            # §3.8 — `0/0` reported as success is the same false green as `8/8` with five N/As.
            return 2
        return 0

    @property
    def badge(self) -> dict[str, Any] | None:
        """The Shields endpoint document, or `None` where §5.2 forbids one.

        A partial run writes no badge, and neither does an exit of 2 or 3. The `None` is the
        rule, expressed once: a fraction computed over a subset somebody chose is the false
        green this release exists to prevent, wearing a different costume.
        """
        if self.partial or self.exit_code not in (0, 1):
            return None
        return {
            "schemaVersion": 1,
            "label": BADGE_LABEL,
            "message": self.badge_message,
            "color": BADGE_PASS_COLOR if self.failed == 0 else BADGE_FAIL_COLOR,
        }

    # --- renderings ---------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": REPORT_SCHEMA,
            "catalogue": CATALOGUE,
            "ctrlrun_version": self.ctrlrun_version,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "policy": dict(self.policy),
            "authority": None if self.authority is None else dict(self.authority),
            "store": dict(self.store),
            "partial": self.partial,
            "summary": {
                "passed": self.passed,
                "failed": self.failed,
                "applicable": self.applicable,
                "not_applicable": self.not_applicable,
                "skipped": self.skipped,
                "badge": f"{self.passed}/{self.applicable}",
            },
            "guarantees": [result.to_dict() for result in self.guarantees],
        }

    def to_json(self) -> str:
        """One object, schema `ctrlrun.verify/v1` (§4.2)."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=False)

    def to_text(self) -> str:
        """The human report of §4.1. The summary is the last line, so `tail -1` means something."""
        lines = [
            f"CTRLRun verify — ctrlrun {self.ctrlrun_version}, catalogue {CATALOGUE}",
            f"policy     {self.policy['path']} ({self.policy['schema']}, "
            f"mode: {self.policy['mode']})",
        ]
        if self.authority is None:
            lines.append("authority  none")
        else:
            where = (
                "same document"
                if self.authority["path"] == self.policy["path"]
                else str(self.authority["path"])
            )
            lines.append(f"authority  {where}, {self.authority['grants']} grants")
        lines.append(
            f"store      {self.store['backend']}, scratch (created and destroyed for this run)"
        )
        lines.append("")
        noted = False
        for result in self.guarantees:
            lines.append(_result_line(result))
            note = result.detail.get("note")
            if note and not noted:
                # §4.1's example prints the `@protect` sentence once, under the first
                # guarantee the missing template takes out. Every one of them carries it in
                # `detail.note` (T102); repeating it on three consecutive lines would push
                # the rows that differ off the reader's screen.
                noted = True
                lines += _wrapped_note(str(note))
            if result.counterexample is not None:
                lines += [f"     {line}" for line in result.counterexample.to_text().split("\n")]
        lines.append("")
        lines.append(self.summary_line())
        return "\n".join(lines)

    def summary_line(self) -> str:
        """§4.1 — passes over applicable, then the N/A ids as a separate sentence."""
        na = [result.id for result in self.guarantees if result.status is Status.NOT_APPLICABLE]
        summary = f"{self.passed}/{self.applicable} declared guarantees pass."
        summary += f" {len(na)} not applicable"
        summary += f": {', '.join(na)}." if na else "."
        if self.skipped:
            skipped = [result.id for result in self.guarantees if result.status is Status.SKIPPED]
            summary += f" {len(skipped)} not selected: {', '.join(skipped)}."
        return summary

    def to_junit(self) -> str:
        """§4.3. N/A is `<skipped>` — reported as skipped and never as a pass."""
        duration = (self.finished_at - self.started_at).total_seconds()
        suite = ElementTree.Element(
            "testsuite",
            {
                "name": SUITE_NAME,
                "tests": str(len(self.guarantees)),
                "failures": str(self.failed),
                "errors": "0",
                "skipped": str(self.not_applicable + self.skipped),
                "time": f"{duration:.3f}",
                "timestamp": self.started_at.replace(tzinfo=None).isoformat(),
            },
        )
        for result in self.guarantees:
            case = ElementTree.SubElement(
                suite,
                "testcase",
                {"classname": CLASS_NAME, "name": f"{result.id} {result.title}", "time": "0.000"},
            )
            if result.status is Status.FAIL:
                failure = ElementTree.SubElement(
                    case,
                    "failure",
                    {"message": result.reason or "failed", "type": result.id},
                )
                failure.text = (
                    "" if result.counterexample is None else result.counterexample.to_text()
                )
            elif result.status in (Status.NOT_APPLICABLE, Status.SKIPPED):
                ElementTree.SubElement(
                    case, "skipped", {"message": result.reason or str(result.status)}
                )
        suites = ElementTree.Element(
            "testsuites",
            {
                "name": SUITE_NAME,
                "tests": str(len(self.guarantees)),
                "failures": str(self.failed),
                "errors": "0",
                "time": f"{duration:.3f}",
            },
        )
        suites.append(suite)
        return ElementTree.tostring(suites, encoding="unicode", xml_declaration=True)

    def job_summary(self) -> str:
        """§5.4 — a Markdown table, with the N/A rows in full."""
        lines = [
            f"### CTRLRun verify — {self.policy['path']}",
            "",
            "| id | guarantee | status | action | reason |",
            "| --- | --- | --- | --- | --- |",
        ]
        for result in self.guarantees:
            lines.append(
                f"| {result.id} | {result.title} | `{result.status}` | "
                f"{result.action or ''} | {result.reason or ''} |"
            )
        lines += ["", self.summary_line()]
        return "\n".join(lines)


def _subject(result: GuaranteeResult) -> str:
    """What a line names: the grant where the guarantee is about one, else the action.

    §3.2 requires the chosen action to appear in every output format, and it does — on
    `action`, in the JSON. This is the human column, and §4.1 puts the grant there for G8 and
    G9 because those two are about a grant rather than about the action they used to reach it.
    """
    return result.grant_id or result.action or ""


def _result_line(result: GuaranteeResult) -> str:
    label = f"{result.id:<4} {result.title:<{_TITLE_WIDTH}}"
    status = {
        Status.PASS: "PASS",
        Status.FAIL: "FAIL",
        Status.NOT_APPLICABLE: "N/A ",
        Status.SKIPPED: "SKIP",
    }[result.status]
    if result.status is Status.PASS:
        trailer = _subject(result)
        extra = result.detail.get("summary")
        if extra:
            trailer = f"{trailer} ({extra})".strip()
    elif result.status is Status.FAIL:
        subject = _subject(result)
        trailer = f"{result.reason} — {subject}" if subject else str(result.reason)
    else:
        trailer = result.reason or ""
    return f"{label} {status}  {trailer}".rstrip()


def _wrapped_note(note: str) -> list[str]:
    """The continuation lines of §4.1's N/A block, aligned under the reason."""
    indent = " " * (5 + _TITLE_WIDTH + 7)
    lines: list[str] = []
    current = "("
    for word in note.split():
        candidate = word if current == "(" else f"{current} {word}"
        if current != "(" and len(candidate) > 58:
            lines.append(indent + current)
            current = word
        else:
            current = f"({word}" if current == "(" else candidate
    lines.append(indent + current + ")")
    return lines


def catalogue_ids() -> tuple[str, ...]:
    return tuple(guarantee.id for guarantee in GUARANTEES)


__all__ = [
    "BADGE_FAIL_COLOR",
    "BADGE_LABEL",
    "BADGE_PASS_COLOR",
    "CLASS_NAME",
    "EFFECT_TEMPLATE_NOTE",
    "REPORT_SCHEMA",
    "SUITE_NAME",
    "Counterexample",
    "GuaranteeResult",
    "Report",
    "Status",
    "catalogue_ids",
]
