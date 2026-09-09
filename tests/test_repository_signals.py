"""The repository's trust signals, as assertions rather than intentions.

A visitor decides in thirty seconds whether an unknown project is safe to put in front of
money: does it release properly, does it say what it does not do, how is a bug reported, what
happens to the report. Each of those is a file or a workflow this repository controls, and each
is asserted here so that it cannot quietly go missing again.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
import sys
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
    # Exact, not a superset: a permission that arrives without a reason should fail here.
    # `id-token` and `attestations` are the two build provenance needs and are argued in
    # `test_the_release_workflow_attests_the_distributions_before_it_uploads_them`.
    assert job["permissions"] == {
        "contents": "write",
        "id-token": "write",
        "attestations": "write",
    }
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


def test_codeql_analyses_every_pull_request():
    """Static analysis that runs on a schedule alone reports findings against code that was
    merged a week ago. This asserts it runs on the pull request, covers the language the
    project is written in, and can write its findings somewhere a person will see them."""
    workflow = _workflow("codeql.yml")
    triggers = workflow[True] if True in workflow else workflow["on"]
    assert "pull_request" in triggers, "findings would arrive after the merge"
    assert "schedule" in triggers, "a new query release should reach old code"

    job = workflow["jobs"]["analyze"]
    assert workflow["permissions"] == {"contents": "read"}
    assert job["permissions"]["security-events"] == "write"
    assert job["timeout-minutes"] <= 30, "an analysis that can hang is a queue nobody clears"

    init = next(s for s in job["steps"] if "codeql-action/init" in str(s.get("uses", "")))
    assert init["with"]["languages"] == "python"
    assert "codeql-action/analyze" in "".join(str(s.get("uses", "")) for s in job["steps"])


def test_codeql_does_not_gate_a_merge():
    """Deliberate, and recorded here so it is a decision rather than an oversight: a static
    analyser's first run on an unfamiliar codebase is a reading list, not a verdict. The
    required checks stay the three that were already required, and the workflow's own comment
    says so -- if that changes, this test is where the argument gets rewritten."""
    workflow = (WORKFLOWS / "codeql.yml").read_text(encoding="utf-8")
    assert "Nothing here gates a merge" in workflow


# --- community files -----------------------------------------------------------------------


def test_the_community_files_exist_and_say_what_they_must():
    contributing = (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    for phrase in (
        "scripts/check.sh",
        "Specification first",
        "mutation-tested",
        # Named rather than linked: a contributor has to know the claims table exists and
        # which repository holds it, and a bare URL in this list would still pass if the
        # sentence around it stopped saying what the table is for.
        "CLAIMS.md",
        "CTRLRun/ctrlrun-docs",
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
    """What the *page* says -- that there was no external audit, and how plainly it says who
    wrote the code -- is asserted in `CTRLRun/ctrlrun-docs`, by
    `test_how_this_is_built_states_the_review_gap_and_the_tooling_once` there. The page is a
    page now, and this repository's CI runs that suite against this commit.

    What is left here is what this repository ships: the README sends a reader to it, and the
    provenance sentence appears in both the README and `SECURITY.md`.
    """
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "https://ctrlrun.dev/docs/how-this-is-built" in readme
    assert "Releases carry PyPI provenance attestations from GitHub Actions" in readme
    assert "Releases carry PyPI provenance attestations from GitHub Actions" in (
        REPO_ROOT / "SECURITY.md"
    ).read_text(encoding="utf-8")


# --- what a downloader can check ------------------------------------------------------------


def _statement(subjects: list[tuple[str, str]]) -> str:
    payload = {
        "_type": "https://in-toto.io/Statement/v1",
        "predicateType": "https://slsa.dev/provenance/v1",
        "predicate": {
            "buildDefinition": {"buildType": "https://actions.github.io/buildtypes/workflow/v1"}
        },
        "subject": [{"name": name, "digest": {"sha256": digest}} for name, digest in subjects],
    }
    return base64.b64encode(json.dumps(payload).encode()).decode()


def _bundle(subjects: list[tuple[str, str]]) -> str:
    return json.dumps(
        {
            "mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json",
            "verificationMaterial": {"certificate": {"rawBytes": "Zm FrZQ=="}},
            "dsseEnvelope": {
                "payloadType": "application/vnd.in-toto+json",
                "payload": _statement(subjects),
                "signatures": [{"sig": "ZmFrZQ=="}],
            },
        }
    )


def _a_release(tmp_path: Path, *, contents: dict[str, bytes]) -> tuple[Path, list[tuple[str, str]]]:
    dist = tmp_path / "dist"
    dist.mkdir()
    subjects = []
    for name, blob in contents.items():
        (dist / name).write_bytes(blob)
        subjects.append((name, hashlib.sha256(blob).hexdigest()))
    return dist, subjects


def _run(bundle: str, dist: Path, tmp_path: Path, stem: str = "ctrlrun-0.6.0"):
    path = tmp_path / "attestation.json"
    path.write_text(bundle + "\n", encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "release_provenance.py"),
            "--bundle",
            str(path),
            "--dist",
            str(dist),
            "--name",
            stem,
        ],
        capture_output=True,
        text=True,
    )


def test_the_release_workflow_attests_the_distributions_before_it_uploads_them():
    """Scorecard reads GitHub Releases, and the assets it reads are the ones `gh release
    create dist/*` picks up -- so the attestation has to be derived into `dist/` before that
    line runs, and after the build that produced the artifacts it is about."""
    job = _workflow("release.yml")["jobs"]["release"]
    names = [str(step.get("name") or step.get("uses", "")) for step in job["steps"]]

    def _step(predicate) -> int:
        return next(i for i, step in enumerate(job["steps"]) if predicate(i, step))

    attest = _step(lambda i, s: str(s.get("uses", "")).startswith("actions/attest-build-prov"))
    build = _step(lambda i, s: "Build the distributions" in names[i])
    derive = _step(lambda i, s: "release_provenance" in str(s.get("run", "")))
    create = _step(lambda i, s: "gh release create" in str(s.get("run", "")))
    assert build < attest < derive < create, (build, attest, derive, create)

    step = job["steps"][attest]
    assert step["with"]["subject-path"] == "dist/*", "the attestation must cover every artifact"
    assert job["permissions"]["id-token"] == "write", "sigstore needs the OIDC token"
    assert job["permissions"]["attestations"] == "write"
    assert job["permissions"]["contents"] == "write"


def test_every_step_that_signs_is_skipped_on_a_release_that_already_exists():
    """The workflow is idempotent by design. A re-run that re-attested would upload assets to
    a release nobody re-cut, and `gh release create` would then fail on the second half."""
    job = _workflow("release.yml")["jobs"]["release"]
    guarded = []
    for step in job["steps"]:
        name = str(step.get("name") or step.get("uses", ""))
        if "attest" in name.lower() or "release_provenance" in str(step.get("run", "")):
            guarded.append(name)
            assert step.get("if") == "steps.existing.outputs.exists == 'false'", name
    assert len(guarded) >= 2, f"the signing steps were not found at all: {guarded}"


def test_the_provenance_script_writes_the_two_suffixes_a_reader_and_a_scanner_look_for(tmp_path):
    """`.intoto.jsonl` is the SLSA convention and carries the DSSE envelopes; `.sigstore.json`
    is the bundle `gh attestation verify --bundle` reads without a network round trip."""
    dist, subjects = _a_release(tmp_path, contents={"ctrlrun-0.6.0.tar.gz": b"sdist"})
    result = _run(_bundle(subjects), dist, tmp_path)
    assert result.returncode == 0, result.stderr

    envelopes = (dist / "ctrlrun-0.6.0.intoto.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(envelopes) == 1
    assert json.loads(envelopes[0])["payloadType"] == "application/vnd.in-toto+json"

    bundle = json.loads((dist / "ctrlrun-0.6.0.sigstore.json").read_text(encoding="utf-8"))
    assert bundle["dsseEnvelope"]["payload"] == _statement(subjects)


def test_the_provenance_script_refuses_an_attestation_over_other_artifacts(tmp_path):
    """The guard the whole step exists for. An attestation is a statement about specific
    bytes; one whose subjects are not the bytes being uploaded is worse than none, because it
    reads as proof to anyone who checks that a file is there and not what is in it."""
    dist, _ = _a_release(tmp_path, contents={"ctrlrun-0.6.0.tar.gz": b"sdist"})
    other = [("ctrlrun-0.6.0.tar.gz", hashlib.sha256(b"a different build").hexdigest())]

    result = _run(_bundle(other), dist, tmp_path)
    assert result.returncode != 0, "an attestation over other bytes was accepted"
    assert result.stderr.startswith("release_provenance: digest mismatch"), result.stderr
    assert not list(dist.glob("*.intoto.jsonl")), "it wrote provenance it had just refused"


def test_the_provenance_script_refuses_an_attestation_that_misses_an_artifact(tmp_path):
    """Half the distributions attested is the failure that looks most like success: the sdist
    carries provenance, the wheel most people install does not, and every suffix check passes."""
    dist, subjects = _a_release(
        tmp_path, contents={"ctrlrun-0.6.0.tar.gz": b"sdist", "ctrlrun-0.6.0.whl": b"wheel"}
    )
    result = _run(_bundle(subjects[:1]), dist, tmp_path)
    assert result.returncode != 0, "an attestation covering one of two artifacts was accepted"
    # The message and not just the exit code: without it, an unhandled KeyError one branch
    # later satisfies every other assertion here and the guard can be deleted unnoticed.
    assert result.stderr.startswith("release_provenance: the attestation does not cover"), (
        result.stderr
    )
    assert "ctrlrun-0.6.0.whl" in result.stderr


def test_the_provenance_script_refuses_an_attestation_reaching_past_the_release(tmp_path):
    """The mirror of the missing-artifact case, and the one a reader assumes is covered by it.
    A statement naming a file that is not in `dist/` was made about a different build, and the
    two artifacts that are there being correct is not evidence about the one that is not."""
    dist, subjects = _a_release(tmp_path, contents={"ctrlrun-0.6.0.tar.gz": b"sdist"})
    reaching = [*subjects, ("ctrlrun-0.6.0-py3-none-any.whl", "00" * 32)]
    result = _run(_bundle(reaching), dist, tmp_path)
    assert result.returncode != 0, "an attestation naming an artifact nobody is releasing passed"
    assert result.stderr.startswith("release_provenance: the attestation covers artifacts"), (
        result.stderr
    )
    assert "ctrlrun-0.6.0-py3-none-any.whl" in result.stderr
    assert not list(dist.glob("*.sigstore.json"))


def test_the_provenance_script_refuses_a_subject_with_no_digest(tmp_path):
    """A subject naming a file but carrying no sha256 is a statement about a *name*, which is
    the one thing an attacker controls for free. The digest comparison one branch later would
    also refuse it, so this guard is kept for its message and the message is what is asserted
    -- a test reading only the exit code could not tell the two apart."""
    dist, _ = _a_release(tmp_path, contents={"ctrlrun-0.6.0.tar.gz": b"sdist"})
    bundle = json.loads(_bundle([("ctrlrun-0.6.0.tar.gz", "0" * 64)]))
    statement = {
        "_type": "https://in-toto.io/Statement/v1",
        "predicateType": "https://slsa.dev/provenance/v1",
        "subject": [{"name": "ctrlrun-0.6.0.tar.gz", "digest": {"sha512": "00"}}],
    }
    bundle["dsseEnvelope"]["payload"] = base64.b64encode(json.dumps(statement).encode()).decode()

    result = _run(json.dumps(bundle), dist, tmp_path)
    assert result.returncode != 0
    assert result.stderr.startswith("release_provenance: subject"), result.stderr
    assert "no sha256 digest" in result.stderr


def test_the_provenance_script_refuses_a_bundle_carrying_no_envelope(tmp_path):
    dist, _ = _a_release(tmp_path, contents={"ctrlrun-0.6.0.tar.gz": b"sdist"})
    result = _run(json.dumps({"mediaType": "x", "messageSignature": {}}), dist, tmp_path)
    assert result.returncode != 0
    assert result.stderr.startswith("release_provenance: bundle carries no dsseEnvelope"), (
        result.stderr
    )


def test_the_provenance_script_refuses_an_empty_bundle(tmp_path):
    """The shape a broken upstream action produces: the step is green, the file is empty, and
    the release ships with two zero-byte assets whose names say they are provenance."""
    dist, _ = _a_release(tmp_path, contents={"ctrlrun-0.6.0.tar.gz": b"sdist"})
    result = _run("", dist, tmp_path)
    assert result.returncode != 0
    assert result.stderr.startswith("release_provenance:") and "is empty" in result.stderr
    assert not list(dist.glob("*.sigstore.json"))


def test_security_md_says_how_to_check_a_release():
    """Provenance nobody is told how to verify is decoration."""
    security = (REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert "gh attestation verify" in security
    assert "--repo CTRLRun/ctrlrun" in security
