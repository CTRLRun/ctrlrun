# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""The repository's trust signals, as assertions rather than intentions.

A visitor decides in thirty seconds whether an unknown project is safe to put in front of
money: does it release properly, does it say what it does not do, how is a bug reported, what
happens to the report. Each of those is a file or a workflow this repository controls, and each
is asserted here so that it cannot quietly go missing again.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
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
    """Actions are pinned to SHAs and CI's installs to hashes, so Dependabot is what moves
    them: the actions grouped monthly, the `requirements/` locks grouped weekly. Nothing else,
    and in particular not `pyproject.toml`: its version floors are deliberate minimums with a
    reason on each, and a bot raising them would exclude working installations for nothing."""
    config = yaml.safe_load((REPO_ROOT / ".github" / "dependabot.yml").read_text())
    entries = {
        (entry["package-ecosystem"], entry["directory"]): entry for entry in config["updates"]
    }
    assert set(entries) == {("github-actions", "/"), ("pip", "/requirements")}
    actions = entries["github-actions", "/"]
    assert actions["schedule"]["interval"] == "monthly"
    assert "groups" in actions, "ungrouped updates are one pull request per action"
    locks = entries["pip", "/requirements"]
    assert locks["schedule"]["interval"] == "weekly"
    assert "groups" in locks, "ungrouped updates are one pull request per package"


def test_the_atheris_ignore_matches_the_bound_it_exists_to_defend():
    """Dependabot relaxes a source bound that blocks an update: given `atheris<3.1` it raised
    the bound to `<3.2` and locked 3.1.0, which publishes no CPython 3.11 wheel and no sdist,
    so `fuzz.yml` could not install it. The ignore in `dependabot.yml` is what keeps that pull
    request from reopening weekly, and it is worth nothing if it drifts from the bound: this
    asserts the two carry the same number, so lifting one without the other is red here rather
    than an unbuildable lock somebody has to diagnose again."""
    config = yaml.safe_load((REPO_ROOT / ".github" / "dependabot.yml").read_text())
    locks = next(
        entry
        for entry in config["updates"]
        if (entry["package-ecosystem"], entry["directory"]) == ("pip", "/requirements")
    )
    ignored = {entry["dependency-name"]: entry["versions"] for entry in locks.get("ignore", [])}
    assert ignored.get("atheris") == [">=3.1"], "the atheris ignore is gone or has moved"
    source = (REPO_ROOT / "requirements" / "in" / "atheris.in").read_text()
    assert "atheris<3.1" in source, "the bound moved and the ignore did not"


# --- the third party's reading -------------------------------------------------------------


def test_the_scorecard_gate_runs_on_every_pull_request():
    """`scorecard.yml` reads `main` after a merge; `scorecard-gate.yml` is the same reading
    before it, so a change that would lower the published score is red on the pull request
    rather than a lower badge on Monday. It publishes nothing, and every floor it holds is
    what `main` scores today: the gate exists to keep the number from going down."""
    workflow = _workflow("scorecard-gate.yml")
    triggers = workflow[True] if True in workflow else workflow["on"]
    assert "pull_request" in triggers
    assert workflow["permissions"] == {"contents": "read"}
    steps = workflow["jobs"]["gate"]["steps"]
    action = next(s for s in steps if str(s.get("uses", "")).startswith("ossf/scorecard-action@"))
    assert action["with"]["publish_results"] is False
    assert action["with"]["results_format"] == "json"
    gate = next(s for s in steps if "Pinned-Dependencies" in str(s.get("run", "")))
    floors = dict(re.findall(r'"([A-Za-z-]+)":\s*(\d+)', gate["run"]))
    assert floors["Pinned-Dependencies"] == "10"
    assert floors["Token-Permissions"] == "10"
    assert floors["Vulnerabilities"] == "10", "a vulnerable pin in a lock must be red"
    assert all(int(score) >= 9 for score in floors.values()), floors


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


def test_the_docs_lock_covers_the_suite_the_check_job_runs():
    """The readiness block records what `pytest --collect-only` finds, and the Postgres tests are
    collected only when psycopg is importable. A `docs` job installed from a lock with fewer
    extras than the `check` job's counts a smaller suite than the one that ran, and fails the
    audit against a number that was right.

    Asserted over the **locks**, not over a `pip install -e .[...]` string in the workflow: since
    the pinned-install change both jobs install from `requirements/*.txt` by hash, and the extras
    live in `scripts/lock.sh`. A test reading the workflow would now read nothing.
    """
    root = Path(__file__).resolve().parents[1]

    def distributions(lock: str) -> set[str]:
        text = (root / "requirements" / lock).read_text(encoding="utf-8")
        return {
            line.split("==")[0].strip().lower()
            for line in text.splitlines()
            if line and not line.startswith((" ", "#", "-"))
        }

    missing = distributions("ci.txt") - distributions("docs.txt")
    assert missing == set(), (
        f"the docs job installs from a lock missing {sorted(missing)}, so it collects a smaller "
        "suite than the check job runs"
    )


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
        # The contribution agreement is the DCO and nothing more; the file has to say so.
        "Developer Certificate of Origin",
        "git commit -s",
        # The written policies the best-practices criteria point at, each by its heading.
        "## Coding standards",
        "## Code review",
        "new functionality MUST arrive with\ntests",
    ):
        assert phrase in contributing, phrase

    governance = (REPO_ROOT / "GOVERNANCE.md").read_text(encoding="utf-8")
    for phrase in ("## Decisions", "## Roles", "## Continuity", "@arpanghoshal", "@rohanrkamath"):
        assert phrase in governance, phrase

    security = (REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8")
    for phrase in ("## Response process", "72 hours", "Security Advisory", "Credit"):
        assert phrase in security, phrase

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
        "Signed-off-by",
    ):
        assert phrase in pr_template, phrase

    assert (
        (REPO_ROOT / ".github" / "CODEOWNERS").read_text().strip().splitlines()[-1].startswith("*")
    )


def test_every_pull_request_commit_is_signed_off():
    """CONTRIBUTING.md asks for a `Signed-off-by` trailer on every commit; the `dco` job is
    what makes that a gate rather than a request. It runs on pull requests only: a push to
    `main` is a merge of commits the job already read."""
    job = _workflow("ci.yml")["jobs"]["dco"]
    assert job["if"] == "github.event_name == 'pull_request'"
    run = "\n".join(step.get("run", "") for step in job["steps"])
    assert "--no-merges" in run
    # Only git's parsed trailer block counts, so a sentence in the body that mentions the
    # trailer cannot satisfy the check; and there is no exemption keyed on a name or an
    # email, because either is a string anyone can set. A sign-off under another address
    # (Dependabot's, which signs as support@github.com) passes on one fact only: GitHub's
    # own signature on the commit, read back from GitHub rather than from the commit.
    assert "trailers:key=Signed-off-by" in run
    assert "[bot]" not in run
    assert "verification.verified" in run
    assert job["steps"][-1]["env"]["GH_TOKEN"] == "${{ github.token }}"


def test_the_citation_names_the_repository_the_version_and_the_tagline():
    citation = yaml.safe_load((REPO_ROOT / "CITATION.cff").read_text(encoding="utf-8"))
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        version = tomllib.load(handle)["project"]["version"]

    assert citation["cff-version"] == "1.2.0"
    assert citation["title"] == "The last check before an AI agent does something it can't undo."
    assert citation["version"] == version
    assert citation["repository-code"] == "https://github.com/CTRLRun/ctrlrun"


def test_the_registry_manifest_agrees_with_the_version_and_the_readme_marker():
    """`server.json` is the fourth file carrying the version, after `pyproject.toml`,
    `CHANGELOG.md` and `CITATION.cff`, and the only one the release notes would not make
    obvious on a diff. It is also the one with a second copy of the number, because the
    registry records the server version and the package version separately, and a manifest
    naming a PyPI version that was never released is accepted here and rejected at publish
    time, which is the wrong end to find out.

    The name is pinned to the README's `mcp-name:` marker for the same reason in the other
    direction. The official registry verifies the PyPI namespace by finding that token in the
    package's long description, which is this README; if the two drift the manifest stays
    valid, the README stays valid, and only the publish fails, with an ownership error that
    does not name either file.
    """
    manifest = json.loads((REPO_ROOT / "server.json").read_text(encoding="utf-8"))
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        version = tomllib.load(handle)["project"]["version"]
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    assert manifest["version"] == version
    packages = manifest["packages"]
    assert [package["identifier"] for package in packages] == ["ctrlrun"]
    assert packages[0]["registryType"] == "pypi"
    assert packages[0]["registryBaseUrl"] == "https://pypi.org"
    assert packages[0]["version"] == version
    # The package a registry client installs is the stdio one (SPEC-mcp-operator §2.3): the
    # loopback-HTTP mode needs a proxy in front of it and is not something a client can start
    # from a manifest. The listing said `streamable-http` on 127.0.0.1 for one release and no
    # desktop client could use it.
    assert packages[0]["transport"] == {"type": "stdio"}
    assert packages[0]["runtimeHint"] == "uvx"
    arguments = [a.get("value") or a.get("name") for a in packages[0]["packageArguments"]]
    assert arguments == ["mcp-operator", "--stdio"]
    assert [v["name"] for v in packages[0]["environmentVariables"]] == ["CTRLRUN_CONFIG"]

    name = manifest["name"]
    # The registry's matcher is `strings.Index(description, "mcp-name: " + name)` followed by a
    # boundary check, so the separator is one space exactly and the case is the manifest's. A
    # tab, two spaces or a lowercased namespace all read fine to a human and none of them match.
    marker = readme.find(f"mcp-name: {name}")
    assert marker != -1, f"README carries no 'mcp-name: {name}'"
    # Its boundary rule, in full: end of content, any character a server name cannot contain,
    # or a comment close. Both spellings of the close, because the registry accepts both and a
    # check that knew only `-->` would reject a README the registry is happy with.
    rest = readme[marker + len(f"mcp-name: {name}") :]
    boundary = rest == "" or not re.match(r"[A-Za-z0-9._/-]", rest) or re.match(r"--!?>", rest)
    assert boundary, (
        "the marker is glued to a trailing character, which the registry reads as a longer name"
    )


def test_the_registry_namespace_is_the_github_owner_with_its_own_case():
    """The registry decides what a publisher may claim by reading `repository_owner` out of the
    GitHub OIDC token (or the organisation's login, on the token path), formatting it into
    `io.github.<owner>/*`, and matching that against `server.json`'s name with
    `strings.HasPrefix`. That compare is case-sensitive and nothing lowercases either side, so a
    manifest saying `io.github.ctrlrun/...` in a repository owned by `CTRLRun` is a 403 at
    publish time and a name that reads perfectly well in review.

    Checked against the live registry rather than argued from the source: of 1,200 `io.github.*`
    entries, 795 carry a mixed-case namespace and not one differs in case from its own
    repository owner. Publishing is also the point of no return -- a name cannot be changed
    afterwards without stranding whoever pinned it -- so the pin belongs here, before the first
    publish, and not in the release checklist.
    """
    manifest = json.loads((REPO_ROOT / "server.json").read_text(encoding="utf-8"))
    owner = manifest["repository"]["url"].removeprefix("https://github.com/").split("/")[0]
    namespace = manifest["name"].split("/")[0]

    assert namespace == f"io.github.{owner}", (
        f"{namespace} is not the namespace {owner} owns; the publish would be refused"
    )


def test_the_registry_manifest_fits_the_fields_the_registry_will_accept():
    """`description` and `title` are capped at 100 characters, and the cap is enforced where it
    cannot be seen: `mcp-publisher validate` returns a 422 naming the field, and nothing in this
    repository would have said so first. The manifest shipped at 288 characters for a day.
    """
    manifest = json.loads((REPO_ROOT / "server.json").read_text(encoding="utf-8"))

    for field in ("description", "title"):
        assert 1 <= len(manifest[field]) <= 100, f"{field} is {len(manifest[field])} characters"


def test_the_publish_workflow_tells_the_registry_after_pypi():
    """The registry verifies ownership by fetching the PyPI metadata for the version the
    manifest names and finding the README marker in it, so a job that raced the upload would
    fail on an ownership error that has nothing to do with ownership. `needs: pypi` is what
    orders them, and it is asserted here because the ordering is invisible in the file: the
    jobs are siblings, and nothing but this key stops them running together.
    """
    workflow = _workflow("publish.yml")
    job = workflow["jobs"]["registry"]

    assert "pypi" in job["needs"], "the registry would be told about an unpublished version"
    assert "kernel == 'true'" in job["if"], "an adapter tag publishes no MCP server"
    # Exact, not a superset. `id-token` is the entire credential; the publish stores nothing.
    assert job["permissions"] == {"id-token": "write", "contents": "read"}

    script = "\n".join(step.get("run", "") for step in job["steps"])
    assert "mcp-publisher login github-oidc" in script, "a stored token would outlive the job"
    assert "./mcp-publisher publish" in script


def test_the_publisher_binary_is_pinned_and_checked():
    """`releases/latest` is whatever the registry cut this morning, downloaded into a job that
    holds a publish credential. The version is pinned in the URL and the bytes are checked
    against a digest, which is `test_every_action_is_pinned_to_a_commit`'s argument for a
    dependency that arrives by `curl` rather than by `uses:`.
    """
    steps = _workflow("publish.yml")["jobs"]["registry"]["steps"]
    # The digest is passed through `env:` rather than written into the script, so the step is
    # read whole; a check that only read `run:` would pass on a workflow carrying no digest.
    script = "\n".join(
        step.get("run", "") + "\n".join(str(value) for value in step.get("env", {}).values())
        for step in steps
    )

    assert "releases/latest" not in script, "the publisher would change under the release"
    assert re.search(r"releases/download/v\d+\.\d+\.\d+/mcp-publisher_", script)
    assert re.search(r"\b[0-9a-f]{64}\b", script), "no digest to check the download against"
    assert "sha256sum --check --strict" in script


def test_how_this_is_built_states_the_review_gap_and_the_tooling_once():
    """What the *page* says -- that there was no external audit, and how plainly it says who
    wrote the code -- is asserted in `CTRLRun/ctrlrun-docs`, by
    `test_how_this_is_built_states_the_review_gap_and_the_tooling_once` there. The page is a
    page now, and this repository's CI runs that suite against this commit.

    What is left here is what this repository ships: the README sends a reader to it, and the
    provenance sentence appears in both the README and `SECURITY.md`.
    """
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "https://docs.ctrlrun.dev/how-this-is-built" in readme
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


# --- what CI installs ----------------------------------------------------------------------

_PIP_INSTALL = re.compile(r"(?:^|[\s;&|])pip install\s+(.*)$")


def _pip_installs() -> list[tuple[str, list[str]]]:
    """Every `pip install` a workflow runs, as (file, arguments). `action.yml` is left out on
    purpose: its `pip install "$CTRLRUN_INSTALL"` is the consumer's own requirement, unpinned
    by design and documented as such in the input's description."""
    found: list[tuple[str, list[str]]] = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("#"):
                continue
            match = _PIP_INSTALL.search(line)
            if match:
                found.append((path.name, match.group(1).split()))
    return found


def test_every_pip_install_in_a_workflow_is_pinned():
    """OpenSSF Scorecard's Pinned-Dependencies rule for `pip install`, asserted here so the
    suite catches a regression before the pull-request gate does. Three shapes pass: hashes
    required (`--require-hashes -r requirements/<x>.txt`), an editable install of a local path
    with `--no-deps`, or nothing but wheels built in the same job. Anything else, `python -m
    pip install --upgrade pip` included, resolves against PyPI at run time, which is a
    different set of bytes every morning."""
    installs = _pip_installs()
    assert installs, "no `pip install` in any workflow; the pattern is wrong"
    for name, args in installs:
        positional = [a for a in args if not a.startswith("-")]
        editable_local = (
            "--no-deps" in args and "-e" in args and all("://" not in a for a in positional)
        )
        wheels_only = bool(positional) and all(a.endswith(".whl") for a in positional)
        assert "--require-hashes" in args or editable_local or wheels_only, (
            f"{name}: pip install {' '.join(args)}"
        )
        # `--no-deps` says nothing about the isolated environment pip builds the package in,
        # which fetches setuptools from PyPI unpinned. The backend comes from the lock instead.
        if editable_local:
            assert "--no-build-isolation" in args, f"{name}: pip install {' '.join(args)}"


def test_every_build_in_a_workflow_uses_the_backend_from_the_lock():
    """`python -m build` creates an isolated environment and installs the backend into it from
    PyPI, unpinned, on the trusted publish path of all places. `--no-isolation` makes it use the
    setuptools every lock carries (`requirements/in/backend.in`)."""
    builds: list[tuple[str, str]] = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if "python -m build" in line and not line.startswith("#"):
                builds.append((path.name, line))
    assert builds, "no `python -m build` in any workflow; the pattern is wrong"
    for name, line in builds:
        assert "--no-isolation" in line, f"{name}: {line}"
    inputs = (REPO_ROOT / "requirements" / "in" / "backend.in").read_text(encoding="utf-8")
    assert "setuptools" in inputs


def test_every_lock_a_workflow_installs_from_exists_and_is_hashed():
    """A `-r requirements/<x>.txt` names a file `scripts/lock.sh` wrote, and every requirement
    in it carries a hash, so `--require-hashes` has something to check against. The `docs` job
    checks this repository out under `ctrlrun/`, which is why that prefix is dropped."""
    named: set[str] = set()
    for _, args in _pip_installs():
        if "-r" in args:
            named.add(args[args.index("-r") + 1].removeprefix("ctrlrun/"))
    assert named, "no workflow installs from a lock"
    for lock in sorted(named):
        path = REPO_ROOT / lock
        assert path.is_file(), lock
        lines = path.read_text(encoding="utf-8").splitlines()
        assert "uv pip compile" in lines[1], f"{lock} was not written by scripts/lock.sh"
        assert "requirements/in/backend.in" in lines[1], f"{lock} carries no build backend"
        for index, line in enumerate(lines):
            if line and not line.startswith(("#", " ")):
                assert "--hash=" in lines[index + 1], f"{lock}: {line} carries no hash"


# --- coverage is measured and the floors are held ---------------------------------------------


def test_ci_measures_coverage_on_one_version_and_holds_the_floors():
    """CONTRIBUTING.md states 90% of statements and 80% of branches; this is the step that
    holds them, on one version of the matrix, from the JSON `scripts/check.sh` writes."""
    workflow = yaml.safe_load((WORKFLOWS / "ci.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["check"]["steps"]
    check = next(s for s in steps if s.get("name") == "check")
    assert "CTRLRUN_COVERAGE" in check["env"]
    floors = next(s for s in steps if s.get("name") == "Coverage floors")
    assert "scripts/coverage_floor.py coverage.json" in floors["run"]
    assert "--statements 90" in floors["run"] and "--branches 80" in floors["run"]
    assert floors["if"] == "matrix.python-version == '3.12'"

    script = (REPO_ROOT / "scripts" / "check.sh").read_text(encoding="utf-8")
    assert "--cov=ctrlrun --cov-branch" in script and "json:coverage.json" in script
    contributing = (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "90% of\nstatements and 80% of branches" in contributing


def test_coverage_floor_names_the_number_that_slipped(tmp_path):
    report = tmp_path / "coverage.json"
    report.write_text(
        json.dumps(
            {
                "totals": {
                    "covered_lines": 91,
                    "num_statements": 100,
                    "covered_branches": 79,
                    "num_branches": 100,
                }
            }
        )
    )
    script = REPO_ROOT / "scripts" / "coverage_floor.py"
    held = subprocess.run(
        [sys.executable, str(script), str(report), "--statements", "90", "--branches", "79"],
        capture_output=True,
        text=True,
    )
    assert held.returncode == 0, held.stderr
    slipped = subprocess.run(
        [sys.executable, str(script), str(report), "--statements", "90", "--branches", "80"],
        capture_output=True,
        text=True,
    )
    assert slipped.returncode == 1
    assert slipped.stderr.strip() == "coverage_floor: below the floor: branches"


# --- a build anyone can repeat ---------------------------------------------------------------


def test_every_build_in_a_workflow_is_reproducible():
    """Every `python -m build` in a workflow runs with `SOURCE_DATE_EPOCH` set to the commit's
    timestamp and normalises the sdist afterwards, so the distributions a tag publishes are the
    ones a reader rebuilds from it (CONTRIBUTING.md, Releases)."""
    found = 0
    for path in sorted(WORKFLOWS.glob("*.yml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                run = str(step.get("run", ""))
                if "python -m build" not in run:
                    continue
                found += 1
                lines = [line.strip() for line in run.splitlines() if line.strip()]
                build = next(i for i, line in enumerate(lines) if "python -m build" in line)
                assert 'export SOURCE_DATE_EPOCH="$(git log -1 --format=%ct)"' in lines[:build], (
                    f"{path.name}: {step.get('name')} builds without SOURCE_DATE_EPOCH"
                )
                assert any("scripts/normalize_sdist.py" in line for line in lines[build:]), (
                    f"{path.name}: {step.get('name')} builds without normalising the sdist"
                )
    assert found >= 4, found  # ci.yml twice, publish.yml, release.yml


def _tarball(path: Path, files: dict[str, bytes], *, mtime: int, uid: int, order: list[str]):
    with tarfile.open(path, "w:gz") as tar:
        for name in order:
            info = tarfile.TarInfo(name)
            info.size = len(files[name])
            info.mtime = mtime
            info.uid = info.gid = uid
            info.uname = info.gname = "somebody"
            info.mode = 0o664
            tar.addfile(info, io.BytesIO(files[name]))


def test_normalize_sdist_makes_two_builds_of_the_same_tree_identical(tmp_path):
    files = {"pkg-1.0/PKG-INFO": b"Name: pkg\n", "pkg-1.0/src/a.py": b"print(1)\n"}
    first, second = tmp_path / "first.tar.gz", tmp_path / "second.tar.gz"
    _tarball(first, files, mtime=1_700_000_000, uid=1000, order=list(files))
    _tarball(second, files, mtime=1_700_000_099, uid=1001, order=list(reversed(files)))
    assert first.read_bytes() != second.read_bytes()

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "normalize_sdist.py"),
            str(first),
            str(second),
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "SOURCE_DATE_EPOCH": "1789312180"},
    )
    assert result.returncode == 0, result.stderr
    assert first.read_bytes() == second.read_bytes()
    with tarfile.open(first, "r:gz") as tar:
        members = tar.getmembers()
        assert [m.name for m in members] == sorted(files)
        assert {m.mtime for m in members} == {1789312180}
        assert {(m.uid, m.gid, m.uname, m.gname, m.mode) for m in members} == {
            (0, 0, "", "", 0o644)
        }
        for member in members:
            extracted = tar.extractfile(member)
            assert extracted is not None and extracted.read() == files[member.name]


def test_normalize_sdist_refuses_without_an_epoch_and_refuses_a_wheel(tmp_path):
    wheel = tmp_path / "pkg-1.0-py3-none-any.whl"
    wheel.write_bytes(b"not a tar")
    without = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "normalize_sdist.py"), str(wheel)],
        capture_output=True,
        text=True,
        env={k: v for k, v in os.environ.items() if k != "SOURCE_DATE_EPOCH"},
    )
    assert without.returncode == 2 and "SOURCE_DATE_EPOCH" in without.stderr
    refused = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "normalize_sdist.py"), str(wheel)],
        capture_output=True,
        text=True,
        env={**os.environ, "SOURCE_DATE_EPOCH": "1"},
    )
    assert refused.returncode != 0 and "not a .tar.gz sdist" in refused.stderr


# --- every source file says who holds it and under what licence -------------------------------

_SOURCE_DIRS = ("src", "tests", "fuzz", "scripts", "adapters", "examples")
_COPYRIGHT_LINE = "# SPDX-FileCopyrightText: 2026 The ctrlrun contributors"
_LICENSE_LINE = "# SPDX-License-Identifier: Apache-2.0"


def test_every_source_file_carries_its_copyright_and_license():
    """The licence is in `LICENSE` and the copyright is the contributors'; a file copied out of
    this tree on its own says both at the top, in the SPDX form a tool can read. A shebang, where
    there is one, stays on line one, and the two tags follow it."""
    missing: list[str] = []
    checked = 0
    for directory in _SOURCE_DIRS:
        for path in sorted((REPO_ROOT / directory).rglob("*")):
            if path.suffix not in (".py", ".sh") or not path.is_file():
                continue
            checked += 1
            lines = path.read_text(encoding="utf-8").splitlines()
            if lines and lines[0].startswith("#!"):
                lines = lines[1:]
            if lines[:2] != [_COPYRIGHT_LINE, _LICENSE_LINE]:
                missing.append(str(path.relative_to(REPO_ROOT)))
    assert checked > 100, checked
    assert not missing, "\n".join(missing)


# --- every name a spec freezes is a name that exists ------------------------------------------

#: `SPEC-v0.10 §9` froze a table of public API additions, and **three of its rows were never
#: built**: `hop=`/`task=` on `needs_approval`, `ssl_context=` on `gateway.transport.request`, and
#: three keys of `ctrlrun.hop/v1`. Nothing went red, because nothing in this repository asserted
#: that a frozen name exists. §9.4 records the gap and asks for this test; this is it, for the
#: rows that did ship. A row added to §9 without a line here is a row that can quietly not ship.
_FROZEN_V0_10: tuple[tuple[str, str, str | None], ...] = (
    ("ctrlrun.control", "protect", "hop"),
    ("ctrlrun.control", "Control.execute", "hop"),
    ("ctrlrun.control", "Control.evaluate", "hop"),
    ("ctrlrun.control", "Control.hop", None),
    ("ctrlrun.authority", "Authority.evaluate", "hop"),
    ("ctrlrun.upstream", "observe_upstream", "verify"),
    ("ctrlrun.upstream", "check", "upstream"),
    ("ctrlrun.upstream", "pinned_context", "certs"),
    ("ctrlrun.adapter", "needs_approval", "hop"),
    ("ctrlrun.adapter", "needs_approval", "task"),
)


def test_every_v0_10_name_the_spec_freezes_is_importable_with_the_parameter_it_names():
    """SPEC-v0.10 §9, asserted rather than described.

    A frozen name that names nothing is worse than a missing row: a missing row is a gap, and a
    wrong one is an answer. Three rows of §9 shipped as nothing and the table went on saying they
    had, for a whole milestone, because no test ever looked.
    """
    import importlib
    import inspect

    for module_name, dotted, parameter in _FROZEN_V0_10:
        module = importlib.import_module(module_name)
        target: object = module
        for part in dotted.split("."):
            assert hasattr(target, part), f"{module_name}.{dotted}: {part!r} does not exist"
            target = getattr(target, part)
        if parameter is None:
            continue
        signature = inspect.signature(target)  # type: ignore[arg-type]
        assert parameter in signature.parameters, (
            f"{module_name}.{dotted} does not take {parameter!r}; SPEC-v0.10 §9 freezes it. "
            f"It takes: {sorted(signature.parameters)}"
        )


#: `SPEC-v0.11 §9`'s table, in the shape §9 itself specifies. **Four elements, not three**: the
#: rows gain an explicit `kind`, because the v0.10 shape above cannot express what half of v0.11's
#: rows claim. A review put them into the three-tuple and got
#: `TypeError: ... is not a callable object` from `inspect.signature`, which is `SPEC-v0.10 §9.4`'s
#: failure reproduced inside the section written to prevent it.
#:
#:   * `name`      -- the dotted path exists.
#:   * `parameter` -- it exists and its signature takes this parameter.
#:   * `member`    -- the dotted path is a collection containing this value. A migration id can
#:                    never be an attribute path: `hasattr(ctrlrun.migrations, "0008_...")` cannot
#:                    be true, because a name beginning with a digit is not an identifier.
#:   * `returns`   -- its return annotation names this type.
#:
#: **Created by item 1 rather than item 2.** §9 names item 2 because item 2 was expected to be
#: the first with rows here, but item 1 has two of its own, and §9's whole point is that nothing
#: turns red at release that could have turned red during the item. Item 2 extends this tuple.
_FROZEN_V0_11: tuple[tuple[str, str, str | None, str], ...] = (
    # Item 1 (SPEC-v0.11 §5.2).
    ("ctrlrun.receipt", "UnreadableReceipt", None, "name"),
    ("ctrlrun.state", "StateStore.receipts", "UnreadableReceipt", "returns"),
    # Item 2 (SPEC-v0.11 §3).
    ("ctrlrun.anchor", "AnchorProvider", None, "name"),
    ("ctrlrun.anchor", "ANCHOR_BREAKS", None, "name"),
    ("ctrlrun.anchor", "verify_anchors", None, "name"),
    ("ctrlrun.anchor", "AnchorReport", None, "name"),
    ("ctrlrun.control", "Control", "anchor", "parameter"),
    ("ctrlrun.state", "StateStore.put_anchor", None, "name"),
    ("ctrlrun.state", "StateStore.anchors", None, "name"),
    # A **membership** claim on a named symbol, because a migration id can never be an attribute
    # path: `hasattr(ctrlrun.migrations, "0008_...")` cannot be true, since a name beginning with
    # a digit is not an identifier. This is the row §9 says a bare third element cannot express,
    # and the reason every row here carries a `kind`.
    ("ctrlrun.migrations", "MIGRATIONS", "0008_anchor_checkpoint_hold", "member"),
    # Item 3 (SPEC-v0.11 §4).
    ("ctrlrun.state", "StateStore.put_checkpoint", None, "name"),
    ("ctrlrun.state", "StateStore.checkpoint", None, "name"),
    ("ctrlrun.state", "StateStore.put_hold", None, "name"),
    ("ctrlrun.state", "StateStore.holds", None, "name"),
    ("ctrlrun.state", "StateStore.release_hold", None, "name"),
)


def test_every_v0_11_name_the_spec_freezes_exists_in_the_shape_it_names():
    """SPEC-v0.11 §9, asserted rather than described, and extended by each item as it lands.

    `SPEC-v0.10 §9.4` is why this exists: three of v0.10's frozen rows shipped as nothing and
    the table went on saying they had, for a whole milestone, because no test ever looked.
    """
    import importlib
    import inspect
    import typing

    for module_name, dotted, target, kind in _FROZEN_V0_11:
        module = importlib.import_module(module_name)
        held: object = module
        for part in dotted.split("."):
            assert hasattr(held, part), f"{module_name}.{dotted}: {part!r} does not exist"
            held = getattr(held, part)
        if kind == "name":
            continue
        assert target is not None, f"{module_name}.{dotted}: a {kind} row needs a target"
        if kind == "parameter":
            signature = inspect.signature(held)  # type: ignore[arg-type]
            assert target in signature.parameters, (
                f"{module_name}.{dotted} does not take {target!r}; SPEC-v0.11 §9 freezes it. "
                f"It takes: {sorted(signature.parameters)}"
            )
        elif kind == "member":
            assert target in {
                getattr(item, "id", item) for item in typing.cast(typing.Iterable[object], held)
            }, f"{module_name}.{dotted} does not contain {target!r}; SPEC-v0.11 §9 freezes it"
        elif kind == "returns":
            annotation = str(inspect.signature(held).return_annotation)  # type: ignore[arg-type]
            assert target in annotation, (
                f"{module_name}.{dotted} returns {annotation}, which does not name {target!r}; "
                "SPEC-v0.11 §9 freezes it"
            )
        else:  # pragma: no cover - a kind nobody defined is a row nobody can check
            raise AssertionError(f"{module_name}.{dotted}: unknown frozen-row kind {kind!r}")


def test_the_row_of_section_9_that_did_not_ship_still_has_not():
    """§9.4's remaining row, pinned in the other direction.

    Two of §9.4's three rows are built. This is the one deliberately not built: check 3 shipped on
    `upstream.observe_upstream(url, *, verify=...)` and the forwarder's `verify`, which is
    per-gateway where a parameter on the shared request helper would be per-process. Adding it
    later would be a second way to configure the same pin, so if somebody does, this fails and
    §9.4's row comes out in the same commit.

    The document and the tree are wrong together or right together, never one of each. That is
    the state §9.4 exists because of.
    """
    import inspect

    from ctrlrun.gateway import transport

    assert "ssl_context" not in inspect.signature(transport.request).parameters, (
        "transport.request now takes ssl_context: delete its row from SPEC-v0.10 §9.4, and say "
        "there which of the two pin-configuration surfaces is now the one to use"
    )
