"""The adapter conformance kit. Build-list item 3; SPEC-v0.5 §5.

It runs **this repository's own acceptance tests** -- the suites of `v0.1 §7` and `v0.3 §10` --
against an adapter, through the surface `adapter.py` defines. It is not a conformance programme,
it certifies nothing, and passing it is not a claim about an adapter's quality. It answers one
question: *does an action driven through this adapter get the same refusals as an action driven
through `@protect`?*

**A kit that only ever passes is a kit nothing exercises.** So `fixtures.py` ships nine adapters
broken in one named way each, and the kit's own tests require each to fail the suite named for
it. That is `v0.4 §1.3`'s positive-control rule, one level up, and it is why item 3 comes before
the reference adapters: "two adapters pass the suites" is this milestone's exit criterion and it
means nothing until the suite can fail.

**Not applicable is not a pass**, here as everywhere. An adapter whose framework carries nothing
back from its resumption cannot exercise the binding suite, and that suite is reported
`not_applicable` with the adapter's own reason, excluded from the denominator, and shown
separately. `report.ok` is `False` for a zero denominator, because `0/0` reported as success is
the same false green as `6/6` with two of them uncounted.

**In core, and needing nothing `ctrlrun` does not already install.** SPEC-v0.5 §5.1 planned this
as `ctrlrun[conformance]` on the premise that a kit needs `pytest`. Building it showed the premise
was wrong -- the suites drive a `Control` and compare exceptions, which is `assert` and `try`, and
an adapter author runs the result *inside* their own pytest rather than through one. An extra with
no dependency behind it is a `MissingDependency` that can never fire and an install line that
installs nothing, so the kit is core, exactly as `verify/` is, for the reason `v0.4 §1` gives: a
check somebody has to remember to install is a check that does not run. SPEC-v0.5 §12.1 records
it.

"Stdlib" is the loose word and is not used: the suites build their scratch policies with
`Policy.from_yaml`, so pyyaml is a runtime dependency here as it is everywhere in the kernel. What
matters is the claim that holds — **nothing from an extra**, and nothing the module map puts in
`conformance/`'s "must not know about" column — and T134b asserts that by walking the package's
imports rather than by asserting a word.

Nothing in the kernel imports this package, and `import ctrlrun` does not reach it (T134).
"""

from __future__ import annotations

from .report import CaseResult, ConformanceReport, SuiteResult, SuiteStatus
from .suites import SUITES, CallRequest, ConformanceAdapter, run

__all__ = [
    "SUITES",
    "CallRequest",
    "CaseResult",
    "ConformanceAdapter",
    "ConformanceReport",
    "SuiteResult",
    "SuiteStatus",
    "run",
]
