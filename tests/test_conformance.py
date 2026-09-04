"""The conformance kit. SPEC-v0.5 §5; T130-T134b.

The kit's own tests are the answer to the question the kit itself cannot answer: *would it
notice?* Nine adapters broken in one named way each, and one that is not, and the pair is the
whole point -- a kit that failed everything would satisfy T130 as surely as one that failed
nothing.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from ctrlrun.conformance import ConformanceReport, SuiteResult, SuiteStatus, run
from ctrlrun.conformance.fixtures import BROKEN, Reference
from ctrlrun.conformance.report import CaseResult
from ctrlrun.conformance.suites import ALLOW, SUITES, T0
from ctrlrun.control import Control
from ctrlrun.policy import Policy
from ctrlrun.state import InMemoryStateStore

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Every `run()` drives the `authority` suite, which builds an `authority:` section and appends
#: SPEC-v0.3 §7 events. `conftest`'s guard asks that a test say so rather than leak them, and
#: here it is true of the whole module rather than of nine tests individually.
pytestmark = pytest.mark.authority

OBSERVING = """
schema: ctrlrun.policy/v3
mode: observe
actions:
  stripe.refund:
    decision: approve
"""


# --- T130: the kit fails a broken adapter, per suite and by name ---------------------------


@pytest.mark.parametrize("name", sorted(BROKEN))
def test_T130_each_broken_fixture_fails_the_suite_named_for_it(name):
    """§5.4. Each fails **the suite named in its row**; incidental failures of other suites are
    permitted and expected, because "and no other" is false of an adapter broken badly enough.

    What this forbids is a fixture that fails *nothing* — the false green the whole kit exists
    to make impossible — and a fixture whose named suite passed, which would mean the suite is
    not testing what its name says.
    """
    broken, expected = BROKEN[name]

    report = run(broken(framework=name))

    failed = {suite.name for suite in report.suites if suite.status is SuiteStatus.FAIL}
    assert expected in failed, (
        f"{name} was supposed to fail the {expected!r} suite and did not; "
        f"it failed {sorted(failed)}"
    )
    assert report.ok is False


def test_T130_every_fixture_names_a_suite_that_exists():
    """A fixture pointed at a suite that was renamed away would pass its own test by never
    being checked against anything."""
    assert {suite for _, suite in BROKEN.values()} <= set(SUITES)


def test_T130_every_suite_has_at_least_one_fixture_that_fails_it():
    """The other direction, and the one that catches a suite nothing exercises: a suite no
    broken adapter can fail is a suite that would report `pass` for every adapter ever written.
    """
    covered = {suite for _, suite in BROKEN.values()}

    assert set(SUITES) == covered, f"no fixture fails: {sorted(set(SUITES) - covered)}"


def test_T130_a_failing_suite_says_which_case_and_why():
    """A report that said only "kernel: fail" would send an adapter author to read the kit."""
    broken, _ = BROKEN["swallows-not-executed"]

    report = run(broken(framework="swallows-not-executed"))
    kernel = next(suite for suite in report.suites if suite.name == "kernel")
    failures = [case for case in kernel.cases if case.status is SuiteStatus.FAIL]

    assert failures
    assert all(case.reason for case in failures)
    assert any(case.id == "T8" for case in failures), [case.id for case in failures]
    assert "T8" in report.to_text()


# --- T131: the kit passes a correct adapter ------------------------------------------------


def test_T131_a_correct_adapter_passes_every_suite():
    """Without this, T130 is satisfied by a kit that fails everything."""
    report = run(Reference())

    assert report.ok is True
    assert [suite.status for suite in report.suites] == [SuiteStatus.PASS] * len(SUITES)
    assert report.not_applicable == ()
    assert report.to_text().endswith("reference: 5/5")


def test_T131_the_reference_adapter_is_the_surface_and_nothing_else():
    """It is `@protect(wait=True)` with an `InterruptApprovalProvider`, and that single keyword
    is the entire difference an adapter makes (§1.1). If this ever needs more, the contract has
    grown something an adapter author will have to discover for themselves."""
    import inspect

    from ctrlrun.conformance import fixtures

    source = inspect.getsource(fixtures.Reference)

    # `wait=True` is the default and the reference never overrides it. `GrantsForItself` does,
    # which is exactly its defect: `wait=False` hands `ApprovalRequired` to the adapter, and an
    # adapter that answers it itself has become the second approval path.
    assert inspect.signature(fixtures._protected).parameters["wait"].default is True
    assert "wait=False" not in source
    for forbidden in ("grant_approval", "deny_approval", "reserve_effect", "Principal("):
        assert forbidden not in source, forbidden


# --- T132: not applicable is not a pass, one level up --------------------------------------


def test_T132_a_non_carrier_reports_binding_not_applicable_with_its_reason():
    report = run(Reference(carries=False, framework="no-carry"))

    binding = next(suite for suite in report.suites if suite.name == "binding")
    assert binding.status is SuiteStatus.NOT_APPLICABLE
    assert "carries_approved_arguments=False" in (binding.reason or "")
    assert "attribution" in (binding.reason or "").lower()


def test_T132_the_denominator_counts_applicable_suites_only():
    report = run(Reference(carries=False, framework="no-carry"))

    assert len(report.applicable) == len(SUITES) - 1
    assert report.to_text().endswith("no-carry: 4/4 (1 not applicable)")
    assert "5/5" not in report.to_text()
    assert report.ok is True


def test_T132_there_is_no_flag_that_folds_an_na_into_the_count():
    """§5.3, and `v0.4 §1`. Asserted the way v0.4 asserts it: by signature."""
    import inspect

    for parameter in inspect.signature(run).parameters.values():
        assert parameter.name in {"adapter", "deployment"}, parameter.name


def test_T132_the_binding_suite_is_the_mutation_check_alone():
    """Its control and the denial case live in `kernel` on purpose: a `binding` suite containing
    them would report `pass` for an adapter that cannot perform the one check it is named for,
    which is an N/A folded into the count one level down."""
    assert [case.id for case in SUITES["binding"]] == ["B1"]
    assert {case.id for case in SUITES["kernel"]} >= {"B2", "B3"}


# --- T132b / T132c: refusal, and a zero denominator ----------------------------------------


def test_T132b_the_kit_refuses_an_observing_control():
    store = InMemoryStateStore()
    observing = Control(
        Policy.from_yaml(OBSERVING, source="<test>"), store, environment="production"
    )

    report = run(Reference(), observing)

    assert report.status == "refused"
    assert "mode: observe" in (report.reason or "")
    assert report.suites == ()
    assert report.ok is False
    assert "REFUSED" in report.to_text()


def test_T132b_an_enforcing_deployment_is_not_refused():
    """The control. Without it, the refusal is satisfied by a kit that refuses every Control."""
    store = InMemoryStateStore()
    enforcing = Control(Policy.from_yaml(ALLOW, source="<test>"), store, environment="production")

    report = run(Reference(), enforcing)

    assert report.status == "ok"
    assert report.ok is True


def test_T132c_a_report_whose_every_suite_is_not_applicable_is_not_a_pass():
    """`0/0` reported as success is the same false green as `6/6` with two of them uncounted.
    Asserted on `ConformanceReport` directly, because it is the rule and not a property of any
    particular adapter."""
    report = ConformanceReport(
        framework="hypothetical",
        suites=(
            SuiteResult(
                "kernel",
                SuiteStatus.NOT_APPLICABLE,
                "nothing here applies",
                (CaseResult("T1", "t", SuiteStatus.NOT_APPLICABLE, "nothing here applies"),),
            ),
        ),
    )

    assert report.applicable == ()
    assert report.ok is False


def test_a_failing_or_not_applicable_case_must_carry_a_reason():
    """An N/A without one is an N/A nobody can check."""
    for status in (SuiteStatus.FAIL, SuiteStatus.NOT_APPLICABLE):
        with pytest.raises(ValueError):
            CaseResult("T1", "t", status)


def test_a_suite_is_not_applicable_only_when_every_case_is():
    """One applicable case that passed is a pass. Reporting the whole suite N/A because part of
    it could not run would hide the part that did."""
    mixed = SuiteResult.of(
        "mixed",
        (
            CaseResult("A", "a", SuiteStatus.NOT_APPLICABLE, "not here"),
            CaseResult("B", "b", SuiteStatus.PASS),
        ),
    )

    assert mixed.status is SuiteStatus.PASS


# --- T133: neither the kit nor an adapter reaches the network ------------------------------


NO_SOCKETS = textwrap.dedent(
    """
    import socket

    class _Refused(socket.socket):
        def __init__(self, *args, **kwargs):
            raise OSError("the conformance kit opened a socket")

    socket.socket = _Refused
    socket.create_connection = lambda *a, **k: (_ for _ in ()).throw(OSError("no network"))
    socket.getaddrinfo = lambda *a, **k: (_ for _ in ()).throw(OSError("no network"))
    """
)

RUN_THE_KIT = textwrap.dedent(
    """
    import logging
    logging.disable(logging.CRITICAL)
    from ctrlrun.conformance import run
    from ctrlrun.conformance.fixtures import Reference
    report = run(Reference())
    assert report.ok, report.to_text()
    print("ok")
    """
)

OPENS_A_SOCKET = RUN_THE_KIT + textwrap.dedent(
    """
    import urllib.request
    urllib.request.urlopen("http://127.0.0.1:1/", timeout=1)
    """
)


def guarded(tmp_path: Path, script: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / "sitecustomize.py").write_text(NO_SOCKETS, encoding="utf-8")
    (tmp_path / "script.py").write_text(script, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(tmp_path / "script.py")],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={"PYTHONPATH": f"{tmp_path}:{REPO_ROOT / 'src'}", "PATH": "/usr/bin:/bin"},
        timeout=180,
    )


def test_T133_the_kit_runs_with_the_network_taken_away(tmp_path):
    result = guarded(tmp_path, RUN_THE_KIT)

    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_T133_and_the_guard_can_see_a_socket_when_there_is_one(tmp_path):
    """The precondition, without which the negative test proves nothing: a run that opens no
    socket passes the guard whether the guard works or not."""
    result = guarded(tmp_path, OPENS_A_SOCKET)

    assert result.returncode != 0
    assert "no network" in result.stderr or "opened a socket" in result.stderr


# --- T134: import boundaries ----------------------------------------------------------------


IMPORT_CTRLRUN = textwrap.dedent(
    """
    import sys
    import ctrlrun

    leaked = sorted(
        name
        for name in sys.modules
        if name in {"ctrlrun.verify", "ctrlrun.conformance", "httpx", "jwt", "pytest"}
        or name.startswith("opentelemetry")
        or name.startswith("ctrlrun.conformance.")
    )
    assert not leaked, leaked
    assert "ctrlrun.adapter" in sys.modules, "adapter is core and in the action path"
    print("ok")
    """
)


def test_T134_import_ctrlrun_imports_neither_verify_nor_conformance():
    """`v0.4`'s T125b, extended. `ctrlrun.adapter` **is** imported: it is core and in the action
    path, and this test is where the line between the two is written down."""
    result = subprocess.run(
        [sys.executable, "-c", IMPORT_CTRLRUN],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(REPO_ROOT / "src"), "PATH": "/usr/bin:/bin"},
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_T134b_the_kit_is_stdlib_and_needs_no_extra():
    """SPEC-v0.5 §12.1. It was planned as `ctrlrun[conformance]` on the premise that a kit needs
    `pytest`; building it showed the premise was wrong, and an extra with no dependency behind
    it is a `MissingDependency` that can never fire and an install line that installs nothing.

    So the assertion is the honest one: the package declares no `conformance` extra, and nothing
    the kit imports comes from outside the standard library and `ctrlrun` itself.
    """
    import tomllib

    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        pyproject = tomllib.load(handle)

    extras = pyproject["project"]["optional-dependencies"]
    assert "conformance" not in extras, (
        "an extra with no dependency behind it installs nothing and can never raise "
        "MissingDependency"
    )
    assert pyproject["project"]["dependencies"] == ["pyyaml>=6.0", "click"]


def test_T134b_the_kit_imports_nothing_from_an_extra():
    import ast

    package = REPO_ROOT / "src" / "ctrlrun" / "conformance"
    forbidden = {"httpx", "jwt", "opentelemetry", "pytest", "yaml"}

    for module in sorted(package.glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            assert not set(names) & forbidden, (module.name, names)


# --- the kit is deterministic ----------------------------------------------------------------


def test_two_runs_of_the_same_adapter_report_identically():
    """`v0.4 §3.6`'s discipline: no RNG, and a clock only the kit moves. A kit whose report
    depended on the wall clock would be one whose failures nobody could reproduce."""
    first = run(Reference()).to_dict()
    second = run(Reference()).to_dict()

    assert first == second
    assert T0.year == 2026
