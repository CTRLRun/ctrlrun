"""The repository's trust signals, as assertions rather than intentions.

A visitor decides in thirty seconds whether an unknown project is safe to put in front of
money: does it release properly, does it say what it does not do, how is a bug reported, what
happens to the report. Each of those is a file or a workflow this repository controls, and each
is asserted here so that it cannot quietly go missing again.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

if not WORKFLOWS.is_dir():  # pragma: no cover - the sdist prunes .github
    pytest.skip("no repository checkout", allow_module_level=True)

_SHA_PIN = re.compile(r"uses:\s+([\w.-]+/[\w./-]+)@([0-9a-f]{40})\s+#\s*v?(\d+[\w.-]*)")
_ANY_USES = re.compile(r"uses:\s+(\S+)")


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _uses_lines() -> list[tuple[str, str]]:
    lines: list[tuple[str, str]] = []
    for path in [*sorted(WORKFLOWS.glob("*.yml")), REPO_ROOT / "action.yml"]:
        for line in path.read_text(encoding="utf-8").splitlines():
            if _ANY_USES.search(line):
                lines.append((path.name, line.strip()))
    return lines


# --- releases ------------------------------------------------------------------------------


def test_every_kernel_tag_produces_a_github_release():
    workflow = _workflow("release.yml")
    triggers = workflow[True] if True in workflow else workflow["on"]

    assert triggers["push"]["tags"] == ["v*"]
    assert "tag" in triggers["workflow_dispatch"]["inputs"], "no way to release a historical tag"
    job = workflow["jobs"]["release"]
    assert job["permissions"] == {"contents": "write"}
    assert workflow["permissions"] == {"contents": "read"}
    script = "\n".join(step.get("run", "") for step in job["steps"])
    assert "gh release create" in script
    assert "--notes-file release-notes.md" in script
    assert "CHANGELOG.md" in script
    assert "dist/*" in script


def test_the_release_workflow_leaves_an_existing_release_alone():
    script = "\n".join(
        step.get("run", "") for step in _workflow("release.yml")["jobs"]["release"]["steps"]
    )
    assert "gh release view" in script


# --- provenance ----------------------------------------------------------------------------


def test_every_action_is_pinned_to_a_commit():
    """A tag can be moved; a commit cannot. Every `uses:` names a full SHA with the version it
    stands for in a comment, so Dependabot can move it and a reader can still tell what it is."""
    unpinned = [
        (name, line)
        for name, line in _uses_lines()
        if "uses: ./" not in line and not _SHA_PIN.search(line)
    ]
    assert unpinned == [], unpinned
    assert len(_uses_lines()) > 10, "the pin check saw almost nothing"


def test_the_publish_workflow_attests_through_trusted_publishing():
    """`pypa/gh-action-pypi-publish` generates PEP 740 attestations by default from v1.11.0
    (its v1.11.0 release notes, read 2026-09-06). The README says releases carry them, so this
    asserts the version floor, that nothing turns them off, and that the job's only permission
    is the OIDC token trusted publishing needs."""
    text = (WORKFLOWS / "publish.yml").read_text(encoding="utf-8")
    pins = [m for m in _SHA_PIN.finditer(text) if m.group(1) == "pypa/gh-action-pypi-publish"]
    assert pins, "publish.yml does not use pypa/gh-action-pypi-publish"
    for pin in pins:
        major, minor, *_ = pin.group(3).split(".")
        assert (int(major), int(minor)) >= (1, 11), pin.group(0)
    assert "attestations: false" not in text
    workflow = _workflow("publish.yml")
    for job in ("pypi", "testpypi"):
        assert workflow["jobs"][job]["permissions"] == {"id-token": "write"}


def test_dependabot_keeps_the_pins_current_and_nothing_else():
    """Actions are pinned to SHAs, so Dependabot is what moves them: grouped, monthly. There is
    no pip entry on purpose: the version floors in pyproject.toml are deliberate minimums with a
    reason on each, and a bot raising them would exclude working installations for nothing."""
    config = yaml.safe_load((REPO_ROOT / ".github" / "dependabot.yml").read_text())
    ecosystems = {entry["package-ecosystem"]: entry for entry in config["updates"]}
    assert set(ecosystems) == {"github-actions"}
    actions = ecosystems["github-actions"]
    assert actions["schedule"]["interval"] == "monthly"
    assert "groups" in actions, "ungrouped updates are one pull request per action"


# --- the third party's reading -------------------------------------------------------------


def test_the_scorecard_workflow_publishes_and_the_readme_shows_it():
    workflow = _workflow("scorecard.yml")
    assert workflow["permissions"] == "read-all"
    job = workflow["jobs"]["analysis"]
    assert job["permissions"]["id-token"] == "write"
    step = next(
        s for s in job["steps"] if str(s.get("uses", "")).startswith("ossf/scorecard-action@")
    )
    assert step["with"]["publish_results"] is True

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "api.scorecard.dev/projects/github.com/CTRLRun/ctrlrun/badge" in readme


# --- community files -----------------------------------------------------------------------


def test_the_community_files_exist_and_say_what_they_must():
    contributing = (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    for phrase in (
        "scripts/check.sh",
        "Specification first",
        "mutation-tested",
        "docs/CLAIMS.md",
        "tools/docs_audit",
        "trusted publishing",
    ):
        assert phrase in contributing, phrase

    conduct = (REPO_ROOT / "CODE_OF_CONDUCT.md").read_text(encoding="utf-8")
    assert "Contributor Covenant" in conduct
    assert "contact@arpanghoshal.com" in conduct
    assert "[INSERT" not in conduct

    templates = REPO_ROOT / ".github" / "ISSUE_TEMPLATE"
    assert (templates / "bug.yml").exists()
    assert (templates / "ambiguous-effect.yml").exists()
    assert (templates / "feature.yml").exists()
    bug = yaml.safe_load((templates / "bug.yml").read_text())
    labels = [item.get("attributes", {}).get("label", "") for item in bug["body"]]
    assert any("ctrlrun inspect" in label for label in labels)
    assert any("receipt" in label.lower() for label in labels)
    ambiguous = yaml.safe_load((templates / "ambiguous-effect.yml").read_text())
    assert any(
        "events" in item.get("attributes", {}).get("label", "").lower()
        for item in ambiguous["body"]
    )
    feature = yaml.safe_load((templates / "feature.yml").read_text())
    assert any(
        "guarantee" in item.get("attributes", {}).get("label", "").lower()
        for item in feature["body"]
    )

    pr_template = (REPO_ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md").read_text(encoding="utf-8")
    for phrase in (
        "Specification first",
        "Tests first",
        "Mutation table",
        "CLAIMS.md",
        "docs_audit",
    ):
        assert phrase in pr_template, phrase

    assert (
        (REPO_ROOT / ".github" / "CODEOWNERS").read_text().strip().splitlines()[-1].startswith("*")
    )


def test_the_citation_names_the_repository_the_version_and_the_tagline():
    citation = yaml.safe_load((REPO_ROOT / "CITATION.cff").read_text(encoding="utf-8"))
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        version = tomllib.load(handle)["project"]["version"]

    assert citation["cff-version"] == "1.2.0"
    assert citation["title"] == "The last check before an AI agent does something it can't undo."
    assert citation["version"] == version
    assert citation["repository-code"] == "https://github.com/CTRLRun/ctrlrun"


def test_how_this_is_built_states_the_review_gap_and_the_tooling_once():
    page = (REPO_ROOT / "docs" / "how-this-is-built.md").read_text(encoding="utf-8")
    assert "no external security audit" in page
    assert 1 <= page.count("AI coding agents") <= 3, "stated plainly, not hyped"
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/how-this-is-built.md" in readme
    assert "Releases carry PyPI provenance attestations from GitHub Actions" in readme
    assert "Releases carry PyPI provenance attestations from GitHub Actions" in (
        REPO_ROOT / "SECURITY.md"
    ).read_text(encoding="utf-8")
