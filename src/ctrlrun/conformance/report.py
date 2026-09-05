"""What the kit reports. SPEC-v0.5 §5.2.

`v0.4 §4.1`'s output rules, one level up: a denominator of **applicable** suites, an N/A that
carries the reason that made it inapplicable, and no flag that folds one into the count.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

#: Why a report carries no suites at all. The only value, and it is not a failure of the
#: adapter: SPEC-v0.5 §3.6 refuses an observing `Control` rather than reporting ten results
#: about a configuration nobody deployed.
REFUSED: Literal["refused"] = "refused"
OK: Literal["ok"] = "ok"


class SuiteStatus(StrEnum):
    """`StrEnum` for `v0.1 §6.1`'s reason: these render into reports read by tools that never
    imported CTRLRun, and `"SuiteStatus.PASS"` is not a status."""

    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class CaseResult:
    """One acceptance test, driven through the adapter."""

    id: str
    title: str
    status: SuiteStatus
    reason: str | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status is not SuiteStatus.PASS and not self.reason:
            raise ValueError(
                f"{self.id}: a {self.status} case must carry a reason. An N/A without one is an "
                "N/A nobody can check, and a failure without one is a failure nobody can fix"
            )


@dataclass(frozen=True)
class SuiteResult:
    """One named group of cases, and what it came to."""

    name: str
    status: SuiteStatus
    reason: str | None = None
    cases: tuple[CaseResult, ...] = ()

    @classmethod
    def of(cls, name: str, cases: tuple[CaseResult, ...]) -> SuiteResult:
        """A suite's status, derived from its cases and never set independently.

        `FAIL` if any case failed. `NOT_APPLICABLE` only if **every** case is -- a suite with one
        applicable case that passed is a pass, and reporting the whole suite N/A because part of
        it could not run would hide the part that did.
        """
        failed = [case for case in cases if case.status is SuiteStatus.FAIL]
        if failed:
            return cls(name, SuiteStatus.FAIL, failed[0].reason, cases)
        applicable = [case for case in cases if case.status is not SuiteStatus.NOT_APPLICABLE]
        if not applicable:
            reason = cases[0].reason if cases else "the suite has no cases"
            return cls(name, SuiteStatus.NOT_APPLICABLE, reason, cases)
        return cls(name, SuiteStatus.PASS, None, cases)


class _Tallied:
    """The N/A accounting, in one place (SPEC-v0.6 §9.7).

    `ConformanceReport` (v0.5) and `StoreReport` (v0.6) differ in exactly one field -- whether
    the subject is a framework or a backend -- and agree on everything `v0.4 §3.8` fixes: a
    denominator of applicable suites, an N/A that carries its reason, `ok` false for a zero
    denominator, and no flag that folds one into the count. Two copies of that which agreed
    today is the drift SPEC-v0.6 §6.2 refuses for canonicalization, one layer down.
    """

    status: Literal["ok", "refused"]
    reason: str | None
    suites: tuple[SuiteResult, ...]

    @property
    def applicable(self) -> tuple[SuiteResult, ...]:
        return tuple(
            suite for suite in self.suites if suite.status is not SuiteStatus.NOT_APPLICABLE
        )

    @property
    def not_applicable(self) -> tuple[SuiteResult, ...]:
        return tuple(suite for suite in self.suites if suite.status is SuiteStatus.NOT_APPLICABLE)

    @property
    def passed(self) -> tuple[SuiteResult, ...]:
        return tuple(suite for suite in self.suites if suite.status is SuiteStatus.PASS)

    @property
    def ok(self) -> bool:
        """Did every applicable suite pass, and was there at least one?

        The second clause is the rule rather than a detail. A refused report has no suites and a
        fully-inapplicable one has no denominator, and `0/0` reported as success is exactly the
        false green `v0.4 §3.8` refuses by name -- the same defect as `6/6` with two of them
        quietly uncounted, wearing the other costume.
        """
        if self.status != OK:
            return False
        applicable = self.applicable
        return bool(applicable) and len(self.passed) == len(applicable)

    def _suites_as_dicts(self) -> list[dict[str, Any]]:
        return [
            {
                "name": suite.name,
                "status": str(suite.status),
                "reason": suite.reason,
                "cases": [
                    {
                        "id": case.id,
                        "title": case.title,
                        "status": str(case.status),
                        "reason": case.reason,
                        "detail": dict(case.detail),
                    }
                    for case in suite.cases
                ],
            }
            for suite in self.suites
        ]

    def _render(self, subject: str) -> str:
        """`4/4 (1 not applicable)`, never `5/5`.

        The N/A count is outside the fraction and never inside it, and each one names the reason
        that made it inapplicable. There is no flag that folds one in.
        """
        if self.status != OK:
            return f"{subject}: REFUSED - {self.reason}"
        lines = []
        for suite in self.suites:
            mark = {
                SuiteStatus.PASS: "PASS",
                SuiteStatus.FAIL: "FAIL",
                SuiteStatus.NOT_APPLICABLE: "N/A ",
            }[suite.status]
            reason = f"  - {suite.reason}" if suite.reason else ""
            lines.append(f"  {mark}  {suite.name}{reason}")
            for case in suite.cases:
                if case.status is not SuiteStatus.PASS:
                    lines.append(f"          {case.id} {case.title}: {case.reason}")
        counted = len(self.applicable)
        skipped = len(self.not_applicable)
        tail = f" ({skipped} not applicable)" if skipped else ""
        lines.append("")
        lines.append(f"{subject}: {len(self.passed)}/{counted}{tail}")
        return "\n".join(lines)


@dataclass(frozen=True)
class ConformanceReport(_Tallied):
    """What the adapter kit's `run()` returns (SPEC-v0.5 §5.2)."""

    framework: str
    status: Literal["ok", "refused"] = OK
    reason: str | None = None
    suites: tuple[SuiteResult, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "framework": self.framework,
            "status": self.status,
            "reason": self.reason,
            "ok": self.ok,
            "suites": self._suites_as_dicts(),
        }

    def to_text(self) -> str:
        return self._render(self.framework)
