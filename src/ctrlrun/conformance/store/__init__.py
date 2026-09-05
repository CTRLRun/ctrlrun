"""The store conformance suite. Build-list item 1; SPEC-v0.6 §2.

It runs **this repository's own acceptance tests** -- the cases of `v0.1 §7`, `v0.2 §10` and
`v0.3 §10` that are statements about `StateStore` rather than about `Control` -- against any
backend. It is not a conformance programme, it certifies nothing, and passing it is not a claim
about a backend's quality. It answers one question: *does this backend refuse what SQLite
refuses, in the same way, for the same reason?*

**The suite predates the backend it grades**, and that ordering is the argument. A Postgres store
measured against a suite written for Postgres has marked its own homework, so item 1 comes before
item 3 and this package is written against the two backends that already exist.

**A kit that only ever passes is a kit nothing exercises.** `fixtures.py` ships thirteen stores
broken in one named way each, and T140 requires each to fail the suite named for it, by case and
by reason -- in both directions, so a suite no fixture fails and a fixture naming no real suite
are each a failure.

**Not applicable is not a pass.** §2.4 allows exactly two N/As, each a property of the backend
rather than of the harness: storage that cannot be opened from another process, and storage that
does not outlive the object holding it. Both describe `InMemoryStateStore`, which says so in its
own docstring, and `falsely-declares-no-url` is what keeps the declaration honest. Any third N/A
is a failure, `report.ok` is `False` for a zero denominator, and there is no flag that folds one
into the count.

Nothing in the kernel imports this package, and `import ctrlrun` does not reach it (T140f).
"""

from __future__ import annotations

from collections.abc import Sequence

from ...errors import InvalidArgument
from ..report import CaseResult, SuiteResult, SuiteStatus
from .backends import InMemoryBackend, SQLiteBackend, StoreBackend
from .report import StoreReport
from .suites import CONTENDERS, SUITES, all_case_ids, failed, selected

__all__ = [
    "SUITES",
    "CaseResult",
    "InMemoryBackend",
    "SQLiteBackend",
    "StoreBackend",
    "StoreReport",
    "SuiteResult",
    "SuiteStatus",
    "all_case_ids",
    "run",
]


def run(
    backend: StoreBackend, *, only: Sequence[str] = (), processes: int = CONTENDERS
) -> StoreReport:
    """Drive every case against `backend` and report what each came to (SPEC-v0.6 §2).

    `only` selects case ids; an unknown name **raises** rather than silently running everything
    or nothing. It is a keyword and not a CLI flag -- §9.4 adds no command, so there is nothing
    for an unknown name to exit from.

    `processes` is how many contenders the two E1 cases run, in threads and in OS processes
    respectively. It is `v0.1 §7` T3's eight by default. A review found it named in §2.4, §9.1
    and T143 while the code used a module constant, so the parameter that the specification
    freezes did not exist.

    Every case gets a freshly `reset()` backend, so no case can see another's rows. A case that
    raises out of its own body is reported as a failed case naming the exception, never as a
    crashed run: one broken case must not hide the twenty that worked.
    """
    if processes < 2:
        raise InvalidArgument(
            f"processes must be at least 2 to contend, got {processes}; a single contender "
            "cannot demonstrate that anything was refused"
        )
    chosen = selected(only)
    results = []
    for name, cases in SUITES.items():
        outcomes = []
        for item in cases:
            if item.id not in chosen:
                continue
            backend.reset()
            try:
                outcomes.append(item.body(backend, processes))
            except Exception as broke:
                outcomes.append(
                    failed(item.id, item.title, f"the case raised {type(broke).__name__}: {broke}")
                )
        if outcomes:
            results.append(SuiteResult.of(name, tuple(outcomes)))
    backend.reset()
    return StoreReport(backend=backend.name, suites=tuple(results))
