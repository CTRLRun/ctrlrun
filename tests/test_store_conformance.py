"""The store conformance suite. Build-list item 1; SPEC-v0.6 §2, §8 T140-T146.

The suite predates the backend it grades. A Postgres store measured against a suite written for
Postgres has marked its own homework, which is why item 1 comes before item 3 -- and why these
tests run against the two backends that already exist, where every divergence is either a bug in
one of them or a place the protocol was never specified (§2.7).
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

from ctrlrun.conformance.report import SuiteStatus
from ctrlrun.conformance.store import SUITES, run
from ctrlrun.conformance.store.backends import InMemoryBackend, SQLiteBackend
from ctrlrun.conformance.store.fixtures import FIXTURES

# --- T140: every fixture fails its named suite, and every suite has a fixture -------------


def test_T140_every_fixture_fails_the_suite_named_for_it():
    """SPEC-v0.6 §2.6. A suite that only ever passes is a suite nothing exercises.

    The assertion is on the **case** that failed and its reason, not merely on the suite's
    status: a test asserting only that `reservation` failed cannot tell you which check ran,
    which is `v0.5 §5.4`'s finding about `denial` one layer down.
    """
    assert FIXTURES, "there are no fixtures, so the suite has never been shown to fail"
    for fixture in FIXTURES:
        report = run(fixture.backend())
        for suite_name in fixture.fails:
            suite = next((s for s in report.suites if s.name == suite_name), None)
            assert suite is not None, (
                f"{fixture.name}: names suite {suite_name!r}, which does not exist"
            )
            assert suite.status is SuiteStatus.FAIL, (
                f"{fixture.name}: {suite_name} reported {suite.status}, expected fail\n"
                f"{report.to_text()}"
            )
            failed = [case for case in suite.cases if case.status is SuiteStatus.FAIL]
            assert failed, f"{fixture.name}: {suite_name} failed with no failing case"
            wanted = fixture.case_in(suite_name)
            by_id = {case.id: case for case in failed}
            assert wanted in by_id, (
                f"{fixture.name}: expected case {wanted!r} to fail, got {sorted(by_id)}"
            )
            # Pinned to the REASON, not merely to the case. A case with two checks is a
            # subsumed guard otherwise: delete the first and the second still fails it, so a
            # mutation table reads green for a check nothing reached. Found by mutation on
            # `single-use`, and this line is what closed it.
            assert fixture.because, f"{fixture.name}: no reason fragment to pin"
            got = by_id[wanted].reason or ""
            assert fixture.because in got, (
                f"{fixture.name}: {wanted} failed for the wrong reason.\n"
                f"  expected to contain: {fixture.because!r}\n"
                f"  actual:              {got!r}"
            )


def test_T140_no_fixture_passes_everything():
    """A fixture that fails nothing is a fixture that proves nothing."""
    for fixture in FIXTURES:
        report = run(fixture.backend())
        assert not report.ok, f"{fixture.name} passed the whole suite; it is meant to be broken"


def test_T140_every_suite_is_named_by_a_fixture():
    """Both directions, per §2.6.

    A suite no fixture fails would report `pass` for every backend ever written; a fixture
    pointed at a renamed suite passes its own test by never being checked against anything.
    """
    named = {suite for fixture in FIXTURES for suite in fixture.fails}
    assert named == set(SUITES), (
        f"suites with no fixture: {sorted(set(SUITES) - named)}; "
        f"fixtures naming no such suite: {sorted(named - set(SUITES))}"
    )


def test_T140_every_fixture_names_a_case_that_exists():
    """A fixture pinned to a case id that no suite defines is pinned to nothing."""
    for fixture in FIXTURES:
        for suite_name in fixture.fails:
            ids = {case.id for case in SUITES[suite_name]}
            wanted = fixture.case_in(suite_name)
            assert wanted in ids, (
                f"{fixture.name}: case {wanted!r} is not in suite {suite_name!r} ({sorted(ids)})"
            )


# --- T141: both shipped backends pass every suite -----------------------------------------


@pytest.mark.parametrize("backend", [SQLiteBackend, InMemoryBackend], ids=["sqlite", "memory"])
def test_T141_the_shipped_backends_pass(backend, tmp_path):
    """SPEC-v0.6 §2.4. Two N/As are legitimate and they are the only two."""
    report = run(backend(tmp_path))
    assert report.ok, report.to_text()


def test_T141_sqlite_reports_no_not_applicable(tmp_path):
    """SQLite has durable, shareable storage, so nothing about it is inapplicable."""
    report = run(SQLiteBackend(tmp_path))
    assert report.not_applicable_cases == (), report.to_text()


def test_T141_in_memory_reports_only_the_two_honest_reasons(tmp_path):
    """§2.4's table, by **reason**. A third reason from this backend is a failure, not a property.

    Counted by reason rather than by case: the cross-process family grew when contended cases
    were added for `consume_approval_and_reserve`, `take_continuation` and `grant`/`deny`, and
    every one of them is inapplicable here for the *same* reason -- this backend's storage cannot
    be opened from another process. Pinning a number would have made adding a contended case look
    like a regression; pinning the reasons is what §2.4 actually says.
    """
    report = run(InMemoryBackend(tmp_path))
    na = {
        (suite.name, case.id)
        for suite in report.suites
        for case in suite.cases
        if case.status is SuiteStatus.NOT_APPLICABLE
    }
    assert ("reservation", "e1-cross-process") in na, report.to_text()
    reasons = {
        case.reason
        for suite in report.suites
        for case in suite.cases
        if case.status is SuiteStatus.NOT_APPLICABLE
    }
    assert reasons == {
        "this backend's storage cannot be opened from another process",
        "this backend's storage does not outlive the object that holds it",
    }, reasons
    for suite in report.suites:
        for case in suite.cases:
            if case.status is SuiteStatus.NOT_APPLICABLE:
                assert case.reason, f"{suite.name}/{case.id}: an N/A with no reason"


def test_T141_the_denominator_counts_cases_not_suites(tmp_path):
    """SPEC-v0.6 §2.4. An N/A **case** inside a passing **suite** must not vanish.

    The in-memory backend's `e1-cross-process` is N/A while `reservation` as a whole passes. Under
    a suite-level tally the report read `7/7 (1 not applicable)` and the three inapplicable cases
    were invisible to the fraction -- `v0.4 §3.8`'s "6/6 with two uncounted" in the other costume.
    Found by review; this test is what stops it coming back, and a mutation reverting
    `StoreReport.ok` to the suite-level tally fails here.
    """
    report = run(InMemoryBackend(tmp_path))

    na_cases = [
        case
        for suite in report.suites
        for case in suite.cases
        if case.status is SuiteStatus.NOT_APPLICABLE
    ]
    assert na_cases, "the in-memory backend should report inapplicable cases"

    # At least one of them sits inside a suite that otherwise passes -- the shape a suite-level
    # count cannot see. Without this the test would pass against the tally it exists to forbid.
    hidden = [
        suite.name
        for suite in report.suites
        if suite.status is SuiteStatus.PASS
        and any(c.status is SuiteStatus.NOT_APPLICABLE for c in suite.cases)
    ]
    assert hidden, "no N/A case is hidden inside a passing suite; this test proves nothing"

    assert len(report.not_applicable_cases) == len(na_cases)
    assert len(report.applicable_cases) == len(report.cases) - len(na_cases)
    assert len(report.cases) > len(report.suites), "the two tallies would be indistinguishable"

    tail = report.to_text().rsplit("\n", 1)[-1]
    assert f"/{len(report.applicable_cases)}" in tail, tail
    assert f"({len(na_cases)} not applicable)" in tail, tail


# --- T142: the report refuses a degenerate run ---------------------------------------------


def test_T142_zero_applicable_is_not_a_pass(tmp_path):
    """`0/0` reported as success is `v0.4 §3.8`'s false green."""
    from ctrlrun.conformance.report import SuiteResult
    from ctrlrun.conformance.store.report import StoreReport

    na = SuiteResult("reservation", SuiteStatus.NOT_APPLICABLE, "nothing ran")
    assert not StoreReport(backend="hollow", suites=(na,)).ok
    assert not StoreReport(backend="hollow", suites=()).ok


def test_T142_an_unknown_case_name_raises(tmp_path):
    """`only` is a `run()` keyword and not a CLI flag (§8 T142): there is nothing to exit from,
    so an unknown name raises rather than silently running everything or nothing."""
    with pytest.raises(Exception) as raised:
        run(SQLiteBackend(tmp_path), only=("no-such-case",))
    assert "no-such-case" in str(raised.value)


# --- T143: E1 twice, and each catches what the other cannot --------------------------------


def test_T143_e1_in_process_and_cross_process_both_pass_on_sqlite(tmp_path):
    """SPEC-v0.6 §2.4. Both cases, against a real file."""
    report = run(SQLiteBackend(tmp_path), only=("e1-in-process", "e1-cross-process"))
    reservation = next(s for s in report.suites if s.name == "reservation")
    got = {case.id: case.status for case in reservation.cases}
    assert got["e1-in-process"] is SuiteStatus.PASS, report.to_text()
    assert got["e1-cross-process"] is SuiteStatus.PASS, report.to_text()


def test_T143_the_two_e1_cases_are_not_redundant(tmp_path):
    """The asymmetry that is why there are two of them (§2.4).

    `two-winners` is an in-process wrapper, so a subprocess opening the backend's `url()` gets
    the **real** store: the cross-process case cannot see the fixture at all. Asserting this
    is what stops somebody deleting the in-process case as a duplicate, which would leave
    `reservation` with nothing any fixture can fail.
    """
    from ctrlrun.conformance.store.fixtures import two_winners

    report = run(two_winners(tmp_path), only=("e1-in-process", "e1-cross-process"))
    reservation = next(s for s in report.suites if s.name == "reservation")
    got = {case.id: case.status for case in reservation.cases}
    assert got["e1-in-process"] is SuiteStatus.FAIL, report.to_text()
    assert got["e1-cross-process"] is SuiteStatus.PASS, (
        "the cross-process case saw the fixture; it is supposed to open the real backend "
        "through a subprocess, which is the whole reason the in-process case exists\n"
        + report.to_text()
    )


# --- T144: `outcome` catches both shapes ---------------------------------------------------


def test_T144_outcome_has_two_fixtures_and_each_reaches_a_different_check(tmp_path):
    """§2.6. Two checks, two fixtures, neither reaching the other's -- the deterministic
    isolation two independent defences otherwise hide."""
    from ctrlrun.conformance.store.fixtures import guesses_failed, raises_not_executed

    guesses = run(guesses_failed(tmp_path), only=("no-failed-on-refusal", "no-not-executed"))
    raises = run(raises_not_executed(tmp_path), only=("no-failed-on-refusal", "no-not-executed"))

    def status(report, case_id):
        outcome = next(s for s in report.suites if s.name == "outcome")
        return next(c.status for c in outcome.cases if c.id == case_id)

    assert status(guesses, "no-failed-on-refusal") is SuiteStatus.FAIL
    assert status(guesses, "no-not-executed") is SuiteStatus.PASS
    assert status(raises, "no-not-executed") is SuiteStatus.FAIL
    assert status(raises, "no-failed-on-refusal") is SuiteStatus.PASS


def test_T144_resolve_effect_may_write_failed(tmp_path):
    """§2.5's correction. `ctrlrun resolve --failed` is not a refusal path, and a suite that
    failed it would fail both shipped backends -- or invite an implementer to remove the only
    route out of `AMBIGUOUS`."""
    report = run(SQLiteBackend(tmp_path), only=("no-failed-on-refusal",))
    outcome = next(s for s in report.suites if s.name == "outcome")
    assert all(case.status is SuiteStatus.PASS for case in outcome.cases), report.to_text()


# --- T145: close() is not a fence ----------------------------------------------------------


@pytest.mark.parametrize("backend", [SQLiteBackend, InMemoryBackend], ids=["sqlite", "memory"])
def test_T145_close_releases_but_does_not_fence(backend, tmp_path):
    """SPEC-v0.6 §2.7. `close()` MUST release what the store holds open and MUST NOT make the
    store refuse later calls.

    Specified rather than discovered: `SQLiteStateStore.close()` documents that a later caller
    gets a fresh connection, and `InMemoryStateStore.close()` releases nothing because it holds
    nothing. Making `close()` a fence would have made it the fault-injection hook §2.5 refuses
    to add, and would have made the shipped stores wrong rather than the suite.
    """
    handle = backend(tmp_path)
    store = handle.open()
    store.reserve_effect("refund:closed", "act_close")
    store.close()

    assert store.get_effect("refund:closed") is not None, "a read after close() must work"
    store.begin_execution("refund:closed", "act_close")
    store.commit_effect("refund:closed", "act_close", {"ok": True})

    record = store.get_effect("refund:closed")
    assert record is not None and str(record.state) == "committed", (
        "a write after close() must work; close() releases what the store holds open and does "
        "not make it refuse later calls (§2.7)"
    )

    other = handle.reopen()
    if other is not None:
        seen = other.get_effect("refund:closed")
        assert seen is not None and str(seen.state) == "committed", (
            "the write made after close() is not visible to another handle on the same storage"
        )


# --- T146: every divergence item 1 found is specified and asserted --------------------------

SPEC = Path(__file__).resolve().parents[1] / "docs" / "SPEC-v0.6.md"


def test_T146_every_divergence_is_written_into_the_spec():
    """SPEC-v0.6 §2.7. A suite that passes on both existing backends on its first run is a
    suite that is not asking anything, so each divergence becomes a paragraph there.

    This test names them, so a paragraph deleted from §2.7 fails here rather than being
    noticed by nobody.
    """
    text = SPEC.read_text(encoding="utf-8")
    # `split` would stop at "### 2.7.1", which also matches "### 2.7". Index to the end of §2.
    section = text[text.index("### 2.7 ") : text.index("\n## 3. ")]
    for divergence in DIVERGENCES:
        assert divergence in section, f"§2.7 no longer specifies: {divergence!r}"


#: Each string is a phrase §2.7 must keep. The `close()` case was known before the item started;
#: the rest were found by writing the suite. A paragraph deleted from §2.7 fails here rather than
#: being noticed by nobody.
DIVERGENCES = [
    "`close()` is a release of resources, not a fence",
    "§2.4's barrier could not open the window it described",
    "`extend_lease`'s refusal taxonomy was never written down",
    "A backend must isolate its own storage",
    "`SQLiteStateStore` and `InMemoryStateStore` do not diverge",
    "positive control",
    "The protocol was short by two methods",
]


# --- T140f: the suite is not imported at package import -------------------------------------


def test_T140f_import_ctrlrun_does_not_reach_the_store_suite():
    """Beside T30, T92, T125b and T134. A testing tool in the execution path is a dependency
    nobody meant to take."""
    probe = textwrap.dedent("""
        import sys
        import ctrlrun
        leaked = sorted(
            name for name in sys.modules
            if name.startswith("ctrlrun.conformance") or name.startswith("ctrlrun.verify")
        )
        print(",".join(leaked))
    """)
    with tempfile.TemporaryDirectory() as work:
        script = Path(work) / "probe.py"
        script.write_text(probe, encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(script)], capture_output=True, text=True, check=True
        )
    assert done.stdout.strip() == "", f"import ctrlrun pulled in {done.stdout.strip()}"
