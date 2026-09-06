"""The cookbook: every recipe runs, twice, offline, and its directory is what its page shows.

A recipe page is the single source; `tools/docs_audit/render_cookbook.py` extracts the policy
and the script into `examples/cookbook/<name>/`. Here each directory is run in a subprocess
whose `sitecustomize` refuses every socket, twice in the same working directory so a second
run must refuse the same things rather than trip over a stale record, and the exit status must
be 0 — every recipe carries an `else: raise SystemExit` on the path where a refusal did not
happen, so a recipe that quietly starts succeeding fails here.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS = REPO_ROOT / "tools" / "docs_audit"
COOKBOOK = REPO_ROOT / "examples" / "cookbook"
PAGES = REPO_ROOT / "docs" / "cookbook"

if not (TOOLS.exists() and PAGES.exists()):  # pragma: no cover - not a checkout
    pytest.skip("no repository checkout", allow_module_level=True)

sys.path.insert(0, str(TOOLS))

import render_cookbook  # noqa: E402
from snippets import NO_NETWORK  # noqa: E402

RECIPES = sorted(p.name for p in COOKBOOK.iterdir() if p.is_dir() and p.name != "__pycache__")
REQUIRED_SECTIONS = (
    "## The policy",
    "## The code",
    "## What the agent sees",
    "## The receipt",
    "## When an AMBIGUOUS appears",
)


@pytest.fixture(scope="session")
def no_network(tmp_path_factory):
    directory = tmp_path_factory.mktemp("no-network")
    (directory / "sitecustomize.py").write_text(NO_NETWORK, encoding="utf-8")
    return directory


def _run(recipe: str, no_network: Path) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(no_network), environment.get("PYTHONPATH", "")) if part
    )
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    for name in ("CTRLRUN_CONFIG", "CTRLRUN_STATE", "CTRLRUN_STORE_URL"):
        environment.pop(name, None)
    script = COOKBOOK / recipe / "main.py"
    command = (
        [sys.executable, str(script)] if script.exists() else ["bash", "-euo", "pipefail", "run.sh"]
    )
    if not script.exists():
        environment["PATH"] = os.pathsep.join(
            [str(Path(sys.executable).parent), environment.get("PATH", "")]
        )
    return subprocess.run(
        command,
        cwd=COOKBOOK / recipe,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_every_recipe_directory_is_what_its_page_shows():
    assert render_cookbook.check(render_cookbook.recipes()) == []


def test_a_hand_edit_to_an_extracted_file_is_drift(tmp_path, monkeypatch):
    monkeypatch.setattr(render_cookbook, "EXAMPLES", tmp_path)
    extracted = render_cookbook.recipes()
    render_cookbook.write(extracted)
    first = next(iter(extracted))
    target = tmp_path / first / "main.py"
    target.write_text(target.read_text() + "\n# edited by hand\n")

    assert any(first in item for item in render_cookbook.check(extracted))


@pytest.mark.parametrize("recipe", RECIPES)
def test_every_recipe_runs_offline_and_is_repeatable(recipe, no_network):
    first = _run(recipe, no_network)
    assert first.returncode == 0, f"{recipe} failed:\n{first.stdout}\n{first.stderr}"
    second = _run(recipe, no_network)
    assert second.returncode == 0, f"{recipe} is not repeatable:\n{second.stdout}\n{second.stderr}"


@pytest.mark.parametrize("recipe", RECIPES)
def test_every_recipe_refuses_something_and_says_so(recipe, no_network):
    """The share unit is a failure and a refusal: every recipe's output shows one."""
    output = _run(recipe, no_network).stdout.lower()
    refusals = (
        "refused",
        "blocked",
        "denied",
        "a human",
        "a checker",
        "did not verify",
        "approval_required",
    )
    assert any(word in output for word in refusals), output


@pytest.mark.parametrize("page", sorted(p.stem for p in PAGES.glob("*.mdx") if p.stem != "index"))
def test_every_recipe_page_has_the_five_sections(page):
    text = (PAGES / f"{page}.mdx").read_text(encoding="utf-8")
    for section in REQUIRED_SECTIONS:
        assert section in text, f"{page}: missing {section!r}"


def test_the_cookbook_directories_are_tracked_by_git():
    listed = subprocess.run(
        ["git", "ls-files", "examples/cookbook"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if listed.returncode != 0:  # pragma: no cover - not a checkout
        pytest.skip("no repository checkout")
    tracked = set(listed.stdout.split())
    needed = {
        path.relative_to(REPO_ROOT).as_posix()
        for path in COOKBOOK.rglob("*")
        if path.is_file() and path.suffix in (".py", ".yaml") and "__pycache__" not in path.parts
    }
    assert not needed - tracked, f"untracked: {sorted(needed - tracked)}"
