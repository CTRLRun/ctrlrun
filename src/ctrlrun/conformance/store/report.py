"""What the store suite reports. SPEC-v0.6 §2.2, §9.7.

It differs from `ConformanceReport` in one field -- the subject is a backend rather than a
framework -- and reuses everything else. The N/A accounting lives in `conformance/report.py`'s
`_Tallied`, so a denominator of applicable cases, an N/A that carries its reason, and `ok` false
for `0/0` have one implementation rather than two that agree today.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ..report import OK, SuiteResult, _Tallied


@dataclass(frozen=True)
class StoreReport(_Tallied):
    """What `ctrlrun.conformance.store.run()` returns (SPEC-v0.6 §2.2)."""

    backend: str
    status: Literal["ok", "refused"] = OK
    reason: str | None = None
    suites: tuple[SuiteResult, ...] = ()

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
        return self._render(self.backend)
