"""What the store suite reports. SPEC-v0.6 §2.2, §9.7.

It differs from `ConformanceReport` in one field -- the subject is a backend rather than a
framework -- and reuses everything else. The N/A accounting lives in `conformance/report.py`'s
`_Tallied`, so a denominator of applicable cases, an N/A that carries its reason, and `ok` false
for `0/0` have one implementation rather than two that agree today.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ..report import OK, CaseResult, SuiteResult, SuiteStatus, _Tallied


@dataclass(frozen=True)
class StoreReport(_Tallied):
    """What `ctrlrun.conformance.store.run()` returns (SPEC-v0.6 §2.2)."""

    backend: str
    status: Literal["ok", "refused"] = OK
    reason: str | None = None
    suites: tuple[SuiteResult, ...] = ()

    @property
    def cases(self) -> tuple[CaseResult, ...]:
        return tuple(case for suite in self.suites for case in suite.cases)

    @property
    def applicable_cases(self) -> tuple[CaseResult, ...]:
        return tuple(c for c in self.cases if c.status is not SuiteStatus.NOT_APPLICABLE)

    @property
    def not_applicable_cases(self) -> tuple[CaseResult, ...]:
        return tuple(c for c in self.cases if c.status is SuiteStatus.NOT_APPLICABLE)

    @property
    def passed_cases(self) -> tuple[CaseResult, ...]:
        return tuple(c for c in self.cases if c.status is SuiteStatus.PASS)

    @property
    def ok(self) -> bool:
        """Every applicable **case** passed, and there was at least one.

        SPEC-v0.6 §2.4 counts cases, not suites, and the difference is not cosmetic: an N/A case
        inside a suite that otherwise passes is invisible to a suite-level fraction, which is
        `v0.4 §3.8`'s "6/6 with two uncounted" wearing the other costume. The in-memory backend
        reported `7/7 (1 not applicable)` under the inherited suite-level tally while
        `e1-cross-process` sat N/A inside a PASS suite. Found by review.
        """
        if self.status != OK:
            return False
        applicable = self.applicable_cases
        return bool(applicable) and len(self.passed_cases) == len(applicable)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "ctrlrun.store-conformance/v1",
            "backend": self.backend,
            "status": self.status,
            "reason": self.reason,
            "ok": self.ok,
            "suites": self._suites_as_dicts(),
        }

    def to_text(self) -> str:
        """The suite lines from `_Tallied`, with a **case-level** fraction as the last line."""
        body = self._render(self.backend).rsplit("\n", 1)[0]
        counted, skipped = len(self.applicable_cases), len(self.not_applicable_cases)
        tail = f" ({skipped} not applicable)" if skipped else ""
        return f"{body}\n{self.backend}: {len(self.passed_cases)}/{counted}{tail}"
