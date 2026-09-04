"""Reporting: the human report, `--json`, `--junit`, the exit codes. SPEC-v0.4 §4; T113-T117.

The report is where the three rules become visible or stop being true. A count that summed the
N/As, a counterexample on a pass, an N/A rendered as a green tick in a CI dashboard - each is
the same false green in a different costume, and each has a test here.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import pytest
from click.testing import CliRunner

from ctrlrun.cli.main import OBSERVE_BANNER, main
from ctrlrun.verify import Status, run
from ctrlrun.verify import guarantees as reg
from ctrlrun.verify.report import CLASS_NAME, REPORT_SCHEMA, SUITE_NAME

REPO_ROOT = Path(__file__).resolve().parents[1]
JUNIT_XSD = REPO_ROOT / "tests" / "data" / "junit-10.xsd"

V1 = "schema: ctrlrun.policy/v1\n"
V2 = "schema: ctrlrun.policy/v2\n"
V3 = "schema: ctrlrun.policy/v3\n"

ALL_APPLICABLE = (
    V2
    + """
actions:
  acme.refund:
    effect: "refund:{payment_id}"
    resource: "payment:{payment_id}"
    rules:
      - when: { amount_gte: 0, amount_lte: 1000 }
        decision: allow
      - when: { amount_gte: 0, amount_lte: 100000 }
        decision: approve
      - decision: deny
"""
)

#: A v1 document: five applicable, five N/A. The shape the badge and the job summary carry.
WITH_NOT_APPLICABLE = (
    V1
    + """
actions:
  acme.refund:
    rules:
      - when: { amount_gte: 0, amount_lte: 1000 }
        decision: allow
      - when: { amount_gte: 0, amount_lte: 100000 }
        decision: approve
      - decision: deny
"""
)

EMPTY = V1 + "actions: {}\n"
OBSERVING = V3 + "mode: observe\nactions:\n  acme.read:\n    decision: allow\n"
MALFORMED = "schema: not-a-schema\nactions: {}\n"


def _write(directory: Path, document: str, name: str = "ctrlrun.yaml") -> Path:
    path = directory / name
    path.write_text(document, encoding="utf-8")
    return path


def _by_id(report):
    return {result.id: result for result in report.guarantees}


# --- T113: the human report ---------------------------------------------------------------


def test_T113_one_line_per_guarantee_in_catalogue_order(tmp_path):
    report = run(_write(tmp_path, ALL_APPLICABLE))

    lines = report.to_text().split("\n")
    ordered = [line.split()[0] for line in lines if line[:1] == "G" and line[1:2].isdigit()]

    assert ordered == [guarantee.id for guarantee in reg.GUARANTEES]


def test_T113_every_not_applicable_line_carries_its_reason(tmp_path):
    """An N/A without a reason is indistinguishable from a guarantee somebody switched off."""
    report = run(_write(tmp_path, WITH_NOT_APPLICABLE))

    text = report.to_text()
    for result in report.guarantees:
        if result.status is Status.NOT_APPLICABLE:
            assert result.reason
            line = next(line for line in text.split("\n") if line.startswith(f"{result.id} "))
            assert result.reason in line, line


def test_T113_the_summary_is_the_last_line_and_names_the_not_applicable_ids(tmp_path):
    """`tail -1` is meaningful, and the N/A count is a separate sentence - never a parenthesis
    inside the fraction, and never summed into it."""
    report = run(_write(tmp_path, WITH_NOT_APPLICABLE))

    text = report.to_text()
    last = text.split("\n")[-1]

    assert last == report.summary_line()
    assert last.startswith(f"{report.passed}/{report.applicable} declared guarantees pass.")
    assert "5 not applicable: G3, G4, G5, G8, G9." in last
    # The fraction is passes over applicable. A report with five N/As does not say 10/10.
    assert "10/10" not in text


def test_T113_a_failing_report_names_the_subject_and_prints_the_counterexample(
    tmp_path, monkeypatch
):
    from ctrlrun.verify import scenarios

    monkeypatch.setattr(scenarios, "run_attempts", lambda payloads: None)

    report = run(_write(tmp_path, ALL_APPLICABLE), only=("G4",))
    text = report.to_text()

    assert _by_id(report)["G4"].status is Status.FAIL
    assert "FAIL" in text
    assert "acme.refund" in text
    assert "expected:" in text
    assert "observed:" in text


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        (ALL_APPLICABLE, "8/8 declared guarantees pass. 2 not applicable"),
        (WITH_NOT_APPLICABLE, "5/5 declared guarantees pass. 5 not applicable"),
        (EMPTY, "0/0 declared guarantees pass. 10 not applicable"),
    ],
    ids=["passing", "some-na", "all-na"],
)
def test_T113_the_summary_over_three_shapes_of_configuration(tmp_path, document, expected):
    report = run(_write(tmp_path, document))

    assert report.to_text().split("\n")[-1].startswith(expected)


# --- T114: `--json` validates, and the counterexample is conditional -----------------------

#: SPEC-v0.4 §4.2, field for field.
TOP_LEVEL = {
    "schema",
    "catalogue",
    "ctrlrun_version",
    "started_at",
    "finished_at",
    "policy",
    "authority",
    "store",
    "partial",
    "summary",
    "guarantees",
}
GUARANTEE_FIELDS = {
    "id",
    "title",
    "status",
    "reason",
    "action",
    "arguments",
    "effect_key",
    "grant_id",
    "descends_from",
    "detail",
    "counterexample",
}
SUMMARY_FIELDS = {"passed", "failed", "applicable", "not_applicable", "skipped", "badge"}


def test_T114_the_document_matches_the_schema_field_for_field(tmp_path):
    import hashlib

    path = _write(tmp_path, WITH_NOT_APPLICABLE)
    document = json.loads(run(path).to_json())

    assert set(document) == TOP_LEVEL
    assert document["schema"] == REPORT_SCHEMA == "ctrlrun.verify/v1"
    assert document["catalogue"] == reg.CATALOGUE == "ctrlrun.guarantees/v1"
    assert set(document["policy"]) == {"path", "sha256", "schema", "mode", "actions"}
    assert document["authority"] is None
    assert document["store"] == {"backend": "sqlite", "scratch": True}
    assert document["partial"] is False
    assert set(document["summary"]) == SUMMARY_FIELDS
    passed = document["summary"]["passed"]
    applicable = document["summary"]["applicable"]
    assert document["summary"]["badge"] == f"{passed}/{applicable}"
    for row in document["guarantees"]:
        assert set(row) == GUARANTEE_FIELDS
    # The digest is computed independently here: a report and a policy that do not hash the
    # same are a report about something else.
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    assert document["policy"]["sha256"] == expected


@pytest.mark.authority
def test_T114_the_authority_block_carries_its_own_digest(tmp_path):
    import hashlib

    policy = _write(
        tmp_path,
        V3
        + """
authority:
  grants:
    - id: only
      subject: { agent: "bot" }
      actions: ["acme.refund"]
      expires_at: "2027-01-01T00:00:00Z"
"""
        + ALL_APPLICABLE[len(V2) :],
    )
    document = json.loads(run(policy).to_json())

    assert set(document["authority"]) == {"path", "sha256", "grants", "max_delegation_depth"}
    assert document["authority"]["sha256"] == hashlib.sha256(policy.read_bytes()).hexdigest()
    assert document["authority"]["grants"] == 1


def test_T114_a_counterexample_is_present_exactly_on_the_fail_rows(tmp_path, monkeypatch):
    """Asserted in both directions, on a run containing a pass, a fail and an N/A."""
    from ctrlrun.verify import scenarios

    monkeypatch.setattr(scenarios, "run_attempts", lambda payloads: None)

    # A silenced control executor fails G1, G2, G7 and G10; G6 still passes; the effect and
    # authority guarantees are N/A in this document. One run, all three statuses.
    monkeypatch.setattr(scenarios._Executor, "__call__", lambda self: None)
    failing = json.loads(run(_write(tmp_path, WITH_NOT_APPLICABLE)).to_json())
    statuses = {row["status"] for row in failing["guarantees"]}

    assert statuses == {"pass", "fail", "not_applicable"}

    for row in failing["guarantees"]:
        if row["status"] == "fail":
            assert row["counterexample"] is not None, row["id"]
            assert set(row["counterexample"]) == {
                "expected",
                "observed",
                "receipts",
                "events",
                "effects",
            }
            assert row["reason"] is not None
        else:
            # A counterexample on a pass would be evidence of a failure that did not happen.
            assert row["counterexample"] is None, row["id"]
        if row["status"] == "pass":
            assert row["reason"] is None
        else:
            assert row["reason"] is not None


# --- T115: `--junit` produces a file CI parsers accept ------------------------------------


def _schema():
    xmlschema = pytest.importorskip("xmlschema")
    return xmlschema.XMLSchema(str(JUNIT_XSD))


def test_T115_the_checked_in_schema_records_its_provenance():
    """JUnit XML has no normative schema (§1.4), so the one it is validated against says
    where it came from and under what licence."""
    readme = (JUNIT_XSD.parent / "README.md").read_text(encoding="utf-8")

    assert "windyroad" in readme
    assert "Apache License 2.0" in readme
    assert "no normative schema" in readme
    assert "Windy Road Technology" in JUNIT_XSD.read_text(encoding="utf-8")


@pytest.mark.parametrize("document", [ALL_APPLICABLE, WITH_NOT_APPLICABLE, EMPTY])
def test_T115_the_junit_file_validates(tmp_path, document):
    report = run(_write(tmp_path, document))

    _schema().validate(report.to_junit())


def test_T115_not_applicable_is_skipped_and_never_absent_and_never_a_pass(tmp_path):
    """The same rule as everywhere else, in the vocabulary a CI dashboard already has."""
    report = run(_write(tmp_path, WITH_NOT_APPLICABLE))
    suite = ElementTree.fromstring(report.to_junit())

    assert suite.tag == "testsuite"
    assert suite.get("name") == SUITE_NAME
    cases = suite.findall("testcase")
    assert len(cases) == len(reg.GUARANTEES)
    by_name = {case.get("name", ""): case for case in cases}
    for result in report.guarantees:
        case = by_name[f"{result.id} {result.title}"]
        assert case.get("classname") == CLASS_NAME
        if result.status is Status.NOT_APPLICABLE:
            skipped = case.find("skipped")
            assert skipped is not None, result.id
            assert skipped.get("message") == result.reason
            assert case.find("failure") is None
        elif result.status is Status.PASS:
            assert list(case) == [], result.id


def test_T115_the_counts_are_correct_and_the_failure_carries_the_counterexample(
    tmp_path, monkeypatch
):
    from ctrlrun.verify import scenarios

    monkeypatch.setattr(scenarios, "run_attempts", lambda payloads: None)
    report = run(_write(tmp_path, ALL_APPLICABLE))
    xml = report.to_junit()
    _schema().validate(xml)
    suite = ElementTree.fromstring(xml)

    assert int(suite.get("tests", "")) == len(reg.GUARANTEES)
    assert int(suite.get("failures", "")) == report.failed == 1
    assert int(suite.get("skipped", "")) == report.not_applicable + report.skipped
    failure = suite.find("./testcase/failure")
    assert failure is not None
    assert failure.get("type") == "G4"
    assert failure.get("message") == reg.CONTROL_FAILED
    assert failure.text is not None and "expected:" in failure.text


def test_T115_a_skipped_row_under_only_says_not_selected(tmp_path):
    report = run(_write(tmp_path, ALL_APPLICABLE), only=("G6",))
    suite = ElementTree.fromstring(report.to_junit())
    _schema().validate(report.to_junit())

    messages = {
        case.get("name", "").split()[0]: (case.find("skipped").get("message"))
        for case in suite.findall("testcase")
        if case.find("skipped") is not None
    }

    assert messages["G1"] == reg.NOT_SELECTED
    assert "G6" not in messages


# --- T116: every exit code is reachable ---------------------------------------------------


def _cli(tmp_path, document, *arguments, monkeypatch=None):
    _write(tmp_path, document)
    runner = CliRunner()
    return runner.invoke(main, ["verify", *arguments])


def test_T116_exit_0_when_every_applicable_guarantee_passes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = _cli(tmp_path, WITH_NOT_APPLICABLE)

    assert result.exit_code == 0, result.output
    assert "declared guarantees pass" in result.stdout


def test_T116_exit_1_when_a_guarantee_fails(tmp_path, monkeypatch):
    from ctrlrun.verify import scenarios

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(scenarios, "run_attempts", lambda payloads: None)

    result = _cli(tmp_path, ALL_APPLICABLE)

    assert result.exit_code == 1, result.output


@pytest.mark.parametrize(
    ("document", "arguments", "expected_in_stderr"),
    [
        (MALFORMED, (), "unknown policy schema"),
        (EMPTY, (), "nothing was checked"),
        (ALL_APPLICABLE, ("--only", "G99"), "G99"),
        (ALL_APPLICABLE, ("--store-url", "postgres://x/y"), "v0.6"),
    ],
    ids=["malformed", "zero-applicable", "unknown-only", "unsupported-store"],
)
def test_T116_exit_2_for_a_configuration_that_is_refused(
    tmp_path, monkeypatch, document, arguments, expected_in_stderr
):
    monkeypatch.chdir(tmp_path)

    result = _cli(tmp_path, document, *arguments)

    assert result.exit_code == 2, result.output
    assert expected_in_stderr in result.stderr


def test_T116_observe_mode_exits_2_with_the_banner_and_runs_nothing(tmp_path, monkeypatch):
    """The banner on stderr, stdout empty, and **no scenario ran** - the scratch directory was
    never created."""
    import ctrlrun.verify as verify_module

    monkeypatch.chdir(tmp_path)
    made: list[str] = []
    original = verify_module.tempfile.mkdtemp

    def recording(*args, **kwargs):
        made.append("scratch")
        return original(*args, **kwargs)

    monkeypatch.setattr(verify_module.tempfile, "mkdtemp", recording)

    result = _cli(tmp_path, OBSERVING)

    assert result.exit_code == 2
    assert OBSERVE_BANNER in result.stderr
    assert "observe" in result.stderr
    assert result.stdout == ""
    assert made == []


def test_T116_exit_3_for_an_internal_error(tmp_path, monkeypatch):
    """A defect in verify must not read as a defect in the kernel, and it must not read as a
    property of the configuration either."""
    from ctrlrun.verify import scenarios

    monkeypatch.chdir(tmp_path)

    def broken(self, name, vector, decision, expected_reason):
        raise scenarios.VerifyInternalError("injected: the synthesizer is wrong")

    monkeypatch.setattr(scenarios.Engine, "_checked", broken)

    result = _cli(tmp_path, ALL_APPLICABLE)

    assert result.exit_code == 3, result.output
    assert "internal error" in result.stderr


def test_T116_a_run_with_five_not_applicable_still_exits_0(tmp_path, monkeypatch):
    """N/A never changes the exit code by itself."""
    monkeypatch.chdir(tmp_path)

    result = _cli(tmp_path, WITH_NOT_APPLICABLE)

    assert result.exit_code == 0
    assert "5 not applicable" in result.stdout


def test_T116_json_and_junit_can_be_combined(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "verify.xml"

    result = _cli(tmp_path, WITH_NOT_APPLICABLE, "--json", "--junit", str(target))

    assert result.exit_code == 0
    assert json.loads(result.stdout)["schema"] == REPORT_SCHEMA
    _schema().validate(target.read_text(encoding="utf-8"))


# --- T117: enums render by value ----------------------------------------------------------


ENUM_REPRS = (
    "Status.",
    "Decision.",
    "EffectState.",
    "ReceiptResult.",
    "ApprovalStatus.",
    "EventType.",
)


@pytest.mark.parametrize("document", [ALL_APPLICABLE, WITH_NOT_APPLICABLE, EMPTY])
def test_T117_no_output_format_renders_an_enum_by_its_member_name(tmp_path, monkeypatch, document):
    """The existing guard, applied to `ctrlrun.verify/v1`. A run that fails is included so the
    counterexample - which carries receipts, events and effect records - is covered too."""
    from ctrlrun.verify import scenarios

    monkeypatch.setattr(scenarios, "run_attempts", lambda payloads: None)
    report = run(_write(tmp_path, document))

    printed = report.to_json() + report.to_text() + report.to_junit() + report.job_summary()

    for name in ENUM_REPRS:
        assert name not in printed, name


def test_T117_every_status_in_the_json_is_one_of_the_four_values(tmp_path, monkeypatch):
    from ctrlrun.verify import scenarios

    monkeypatch.setattr(scenarios, "run_attempts", lambda payloads: None)
    document = json.loads(run(_write(tmp_path, ALL_APPLICABLE), only=("G4", "G6")).to_json())

    values = {row["status"] for row in document["guarantees"]}

    assert values <= {"pass", "fail", "not_applicable", "skipped"}
    assert isinstance(document["guarantees"][0]["status"], str)
