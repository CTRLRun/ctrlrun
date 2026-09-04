"""The composite action, the badge and `docs/verify.md`. SPEC-v0.4 §5; T118-T120.

The badge is the shortest sentence this project makes, and the one most likely to be read
without the report behind it. So its text is asserted as a *concatenation* and against a
regex rather than a word list — no adjective can be appended to it later — and the vocabulary
it is not allowed to use is asserted against the badge, the job summary and `docs/verify.md`
together.

T118's substance runs in this repository's CI, where the action actually executes. What is
asserted here is that the job exists, that it runs the action against both configurations, and
that it checks the two shapes the specification names — because a CI job nothing asserts is a
CI job somebody can delete.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from ctrlrun.verify import guarantees as reg
from ctrlrun.verify import run
from ctrlrun.verify.report import (
    BADGE_FAIL_COLOR,
    BADGE_LABEL,
    BADGE_PASS_COLOR,
    badge_from_document,
    summary_from_document,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
ACTION = REPO_ROOT / "action.yml"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
VERIFY_DOC = REPO_ROOT / "docs" / "verify.md"
README = REPO_ROOT / "README.md"
AUTHORITY_PAYMENTS = REPO_ROOT / "examples" / "authority" / "payments.yaml"
V1_PAYMENTS = REPO_ROOT / "examples" / "policies" / "payments.yaml"

#: SPEC-v0.4 §5.3 and §6.1 — the vocabulary the badge, the summary and the page may not use as
#: a claim about CTRLRun or about the operator's system. The same list `v0.2 §10` T31 holds the
#: sector templates to.
FORBIDDEN = ("secure", "safe", "compliant", "certified", "audited")

#: §5.2 — a regex rather than a word list, so no adjective can be appended to it later.
BADGE_MESSAGE = re.compile(r"^verified \d+/\d+$")


def _action() -> dict:
    return yaml.safe_load(ACTION.read_text(encoding="utf-8"))


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _write(directory: Path, document: str) -> Path:
    path = directory / "ctrlrun.yaml"
    path.write_text(document, encoding="utf-8")
    return path


ALL_APPLICABLE = """schema: ctrlrun.policy/v2
actions:
  acme.refund:
    effect: "refund:{payment_id}"
    rules:
      - when: { amount_gte: 0, amount_lte: 1000 }
        decision: allow
      - when: { amount_gte: 0, amount_lte: 100000 }
        decision: approve
      - decision: deny
"""

EMPTY = "schema: ctrlrun.policy/v1\nactions: {}\n"


# --- T118: the action runs in this repository's CI -----------------------------------------


def test_T118_the_action_is_a_composite_action_at_the_repository_root():
    action = _action()

    assert ACTION.exists()
    assert action["runs"]["using"] == "composite"
    assert set(action["inputs"]) >= {
        "policy",
        "authority",
        "only",
        "python-version",
        "install",
        "badge-path",
    }
    assert set(action["outputs"]) >= {
        "passed",
        "failed",
        "applicable",
        "not-applicable",
        "badge-message",
        "report-path",
    }


def test_T118_ci_runs_the_action_against_both_configurations():
    jobs = _workflow()["jobs"]

    assert "verify" in jobs, "CI does not run the action at all"
    steps = jobs["verify"]["steps"]
    used = [step.get("with", {}).get("policy") for step in steps if step.get("uses") == "./"]

    assert "examples/authority/payments.yaml" in used
    assert "examples/policies/payments.yaml" in used
    # `install: .` so the action dogfoods the checkout rather than the last release (§5.1).
    for step in steps:
        if step.get("uses") == "./":
            assert step["with"]["install"] == "."


def test_T118_ci_asserts_the_two_shapes_the_specification_names():
    """The N/A dogfood. A change that made verify silently count N/As is caught in CI rather
    than in a badge, and this test is what keeps the assertion in the job."""
    steps = _workflow()["jobs"]["verify"]["steps"]
    script = "\n".join(step.get("run", "") for step in steps)

    assert 'test "$AUTHORITY" = "verified 10/10"' in script
    assert 'test "$TEMPLATES" = "verified 5/5"' in script
    assert 'test "$TEMPLATES_NA" = "5"' in script


@pytest.mark.authority
def test_T118_the_two_configurations_really_do_report_those_shapes():
    """And the shapes CI asserts are the ones the code produces, checked here rather than only
    on a runner: a CI assertion that has drifted from the code fails once, in the wrong place,
    on somebody else's branch."""
    authority = run(AUTHORITY_PAYMENTS)
    templates = run(V1_PAYMENTS)

    assert authority.badge is not None
    assert authority.badge["message"] == "verified 10/10"
    assert authority.not_applicable == 0
    assert templates.badge is not None
    assert templates.badge["message"] == "verified 5/5"
    assert templates.not_applicable == 5


def test_T118_the_action_uploads_the_report_and_writes_a_job_summary():
    steps = _action()["runs"]["steps"]
    uploads = [step for step in steps if str(step.get("uses", "")).startswith("actions/upload-")]
    script = "\n".join(step.get("run", "") for step in steps)

    assert uploads, "the action uploads nothing"
    paths = uploads[0]["with"]["path"]
    assert "verify-report.json" in paths
    assert "verify-report.xml" in paths
    assert "GITHUB_STEP_SUMMARY" in script
    # Rendered from the report, never from a second run: `--report verify-report.json`.
    assert "--report verify-report.json" in script
    # One run. The summary and the badge come from the document it wrote, not from a second
    # verify, so they can never disagree about what happened (§5.1).
    assert script.count('ctrlrun verify "${arguments[@]}"') == 1


# --- T119: the badge says what it is allowed to say ----------------------------------------


def test_T119_the_rendered_badge_text_is_exactly_CTRLRun_verified_N_over_M(tmp_path):
    report = run(_write(tmp_path, ALL_APPLICABLE))
    badge = report.badge

    assert badge is not None
    rendered = f"{badge['label']} {badge['message']}"
    assert rendered == f"CTRLRun verified {report.passed}/{report.applicable}"
    assert re.fullmatch(r"CTRLRun verified \d+/\d+", rendered)
    assert BADGE_MESSAGE.fullmatch(badge["message"])
    assert badge["label"] == BADGE_LABEL == "CTRLRun"
    assert badge["schemaVersion"] == 1


def test_T119_the_denominator_is_applicable_and_never_the_catalogue_size():
    report = run(V1_PAYMENTS)
    badge = report.badge

    assert badge is not None
    assert badge["message"] == f"verified {report.passed}/{report.applicable}"
    assert report.applicable == 5
    assert len(reg.GUARANTEES) == 10
    assert "/10" not in badge["message"]


def test_T119_the_colour_is_about_failures_and_has_no_amber_for_not_applicable(
    tmp_path, monkeypatch
):
    from ctrlrun.verify import scenarios

    passing = run(V1_PAYMENTS)
    assert passing.not_applicable == 5
    assert passing.badge is not None
    assert passing.badge["color"] == BADGE_PASS_COLOR

    monkeypatch.setattr(scenarios, "run_attempts", lambda payloads: None)
    failing = run(_write(tmp_path, ALL_APPLICABLE))
    assert failing.failed == 1
    assert failing.badge is not None
    assert failing.badge["color"] == BADGE_FAIL_COLOR


def test_T119_the_link_target_carries_the_exact_phrase():
    page = VERIFY_DOC.read_text(encoding="utf-8")

    assert "declared guarantees pass" in page
    # And it is the anchor the badge links to, not a phrase buried somewhere else.
    heading = page.index("## What the badge means")
    assert "declared guarantees pass" in page[heading : heading + 600]


@pytest.mark.parametrize("word", FORBIDDEN)
def test_T119_no_claim_uses_the_forbidden_vocabulary(tmp_path, word):
    """Asserted against the badge, its JSON, the job summary and `docs/verify.md` together.

    `docs/verify.md` names the words in order to refuse them, and the sentence that does is the
    only place any of them may appear on the page.
    """
    report = run(_write(tmp_path, ALL_APPLICABLE))
    document = json.loads(report.to_json())

    assert word not in json.dumps(badge_from_document(document)).lower()
    assert word not in summary_from_document(document).lower()

    # On the page, each of these words appears only inside a sentence that refuses it. The
    # unit is the paragraph rather than the line, because the refusal and the word it refuses
    # are often on different lines of the same wrapped sentence.
    page = VERIFY_DOC.read_text(encoding="utf-8")
    offending = [
        paragraph
        for paragraph in page.split("\n\n")
        if word in paragraph.lower()
        and not any(marker in paragraph.lower() for marker in ("not ", "never", "no "))
    ]
    assert not offending, f"{word!r} used as a claim: {offending}"


def test_T119_the_action_and_the_workflow_make_no_forbidden_claim():
    text = (ACTION.read_text(encoding="utf-8") + WORKFLOW.read_text(encoding="utf-8")).lower()

    for word in FORBIDDEN:
        assert word not in text, word


# --- T120: the job fails on FAIL and succeeds on N/A ---------------------------------------


def _fail_script() -> str:
    steps = _action()["runs"]["steps"]
    return "\n".join(step.get("run", "") for step in steps if "STATUS" in str(step.get("env", "")))


def test_T120_the_job_fails_on_a_failed_guarantee_and_on_a_refused_configuration():
    script = _fail_script()

    assert "0) echo" in script and "exit 0" in script
    assert "1) echo" in script and "::error::a declared guarantee failed" in script
    assert "2) echo" in script and "the configuration was refused" in script
    # Exit 2 fails the job. A configuration nothing could be checked against is not a pass.
    assert script.count("exit 1") >= 3


def test_T120_no_input_makes_a_failure_not_fail_the_job():
    """Asserted against the parsed action, not its text: the text names `continue-on-error`
    in order to refuse it, and a check that could not tell a comment from a key would have to
    be deleted the first time somebody explained the rule."""
    action = _action()

    for name in action["inputs"]:
        for suggestive in ("continue", "ignore", "soft", "fail", "tolerate", "warn"):
            assert suggestive not in name, name
    for step in action["runs"]["steps"]:
        assert "continue-on-error" not in step, step.get("name")


def test_T120_a_configuration_with_not_applicable_guarantees_still_writes_a_badge():
    """N/A is not a failure and it is not a pass; the job's green means "nothing that could be
    checked was wrong", which is exactly what the badge says."""
    report = run(V1_PAYMENTS)

    assert report.exit_code == 0
    assert report.badge is not None
    assert report.badge["message"] == "verified 5/5"


def test_T120_a_failing_run_writes_a_red_badge_and_a_non_zero_exit(tmp_path, monkeypatch):
    from ctrlrun.verify import scenarios

    monkeypatch.setattr(scenarios, "run_attempts", lambda payloads: None)
    report = run(_write(tmp_path, ALL_APPLICABLE))

    assert report.exit_code == 1
    assert report.badge is not None
    assert report.badge["color"] == BADGE_FAIL_COLOR


def test_T120_a_partial_run_writes_no_badge(tmp_path):
    report = run(_write(tmp_path, ALL_APPLICABLE), only=("G6",))

    assert report.partial is True
    assert report.badge is None
    assert badge_from_document(json.loads(report.to_json())) is None


def test_T120_an_exit_2_configuration_writes_no_badge(tmp_path):
    report = run(_write(tmp_path, EMPTY))

    assert report.exit_code == 2
    assert report.badge is None
    assert badge_from_document(json.loads(report.to_json())) is None


def test_T120_the_renderer_writes_no_badge_file_where_none_is_allowed(tmp_path):
    """The action calls this, so "no badge for a partial run" has to hold at the file level and
    not only in a property nothing writes out."""
    from ctrlrun.verify.report import main

    report = run(_write(tmp_path, ALL_APPLICABLE), only=("G6",))
    document = tmp_path / "report.json"
    document.write_text(report.to_json(), encoding="utf-8")
    badge = tmp_path / "badge.json"
    summary = tmp_path / "summary.md"

    assert main(["--report", str(document), "--badge", str(badge), "--summary", str(summary)]) == 0
    assert not badge.exists()
    assert summary.read_text(encoding="utf-8").startswith("### CTRLRun verify")


def test_T120_the_renderer_writes_the_badge_where_one_is_allowed(tmp_path):
    from ctrlrun.verify.report import main

    report = run(V1_PAYMENTS)
    document = tmp_path / "report.json"
    document.write_text(report.to_json(), encoding="utf-8")
    badge = tmp_path / "badge.json"

    assert main(["--report", str(document), "--badge", str(badge)]) == 0
    written = json.loads(badge.read_text(encoding="utf-8"))
    assert written == report.badge
    assert BADGE_MESSAGE.fullmatch(written["message"])


# --- the README badge and the documentation links ------------------------------------------


def test_the_readme_carries_the_badge_and_links_it_to_what_it_means():
    readme = README.read_text(encoding="utf-8")

    assert "img.shields.io/endpoint" in readme
    assert "verify-badge.json" in readme
    assert "docs/verify.md#what-the-badge-means" in readme


def test_the_readme_documentation_table_links_the_verify_page():
    readme = README.read_text(encoding="utf-8")

    assert "docs/verify.md" in readme
    assert "declared guarantees pass" in readme


def test_the_job_summary_carries_the_not_applicable_rows_in_full():
    """A summary that listed only failures would make an all-N/A run look like a clean one."""
    report = run(V1_PAYMENTS)
    summary = report.job_summary()

    for result in report.guarantees:
        assert f"| {result.id} |" in summary
        if result.reason:
            assert result.reason in summary
    assert report.summary_line() in summary
