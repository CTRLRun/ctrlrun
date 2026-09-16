# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""The cookbook: every recipe runs, twice, offline, and refuses something.

A recipe page is the single source, and it lives in CTRLRun/ctrlrun-docs, where
`tools/docs_audit/render_cookbook.py` extracts the policy and the script into this
repository's `examples/cookbook/<name>/`. **That the directory is what its page shows is
checked there**, beside the page it is checked against; what is checked here is the half that
needs no page at all.

Each directory is run in a subprocess whose `sitecustomize` refuses every socket, twice in the
same working directory so a second run must refuse the same things rather than trip over a
stale record, and the exit status must be 0 — every recipe carries an `else: raise SystemExit`
on the path where a refusal did not happen, so a recipe that quietly starts succeeding fails
here.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
COOKBOOK = REPO_ROOT / "examples" / "cookbook"

if not COOKBOOK.exists():  # pragma: no cover - not a checkout
    pytest.skip("no repository checkout", allow_module_level=True)

RECIPES = sorted(p.name for p in COOKBOOK.iterdir() if p.is_dir() and p.name != "__pycache__")
REQUIRED_SECTIONS = (
    "## The policy",
    "## The code",
    "## What the agent sees",
    "## The receipt",
    "## When an AMBIGUOUS appears",
)


def _sandbox(recipe: str, tmp_path: Path) -> Path:
    """A private copy of one recipe's directory.

    **Recipes used to run in `examples/cookbook/<name>/` itself, and two tests share a recipe.**
    Under `pytest -n auto` they land on different workers and run concurrently in that one
    directory, so `verify-in-github-actions`, whose script writes `verify-report.json`, reads it
    and then `rm -f`s it, raced itself: one worker's delete landed between the other's write and
    read, `bash -euo pipefail` turned the `FileNotFoundError` into a non-zero exit, and the run
    went red with nothing wrong in the library.

    It reddened two different branches in one afternoon before it was reproduced, which is the
    cost worth naming: a suite that fails once in a while teaches people to re-run it, and this
    project's gate is only worth anything while green means green.

    Copying also stops the suite writing into the working tree at all, so `git status` after a
    test run says what it should.
    """
    destination = tmp_path / recipe
    shutil.copytree(COOKBOOK / recipe, destination)
    return destination


def _run(recipe: str, no_network: Path, where: Path) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(no_network), environment.get("PYTHONPATH", "")) if part
    )
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    for name in ("CTRLRUN_CONFIG", "CTRLRUN_STATE", "CTRLRUN_STORE_URL"):
        environment.pop(name, None)
    script = where / "main.py"
    command = (
        [sys.executable, str(script)] if script.exists() else ["bash", "-euo", "pipefail", "run.sh"]
    )
    if not script.exists():
        environment["PATH"] = os.pathsep.join(
            [str(Path(sys.executable).parent), environment.get("PATH", "")]
        )
    return subprocess.run(
        command,
        cwd=where,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@pytest.mark.parametrize("recipe", RECIPES)
def test_every_recipe_runs_offline_and_is_repeatable(recipe, no_network, tmp_path):
    """Twice in **one** directory, which is the point, and that directory is this test's own."""
    where = _sandbox(recipe, tmp_path)

    first = _run(recipe, no_network, where)
    assert first.returncode == 0, f"{recipe} failed:\n{first.stdout}\n{first.stderr}"
    second = _run(recipe, no_network, where)
    assert second.returncode == 0, f"{recipe} is not repeatable:\n{second.stdout}\n{second.stderr}"


@pytest.mark.parametrize("recipe", RECIPES)
def test_every_recipe_refuses_something_and_says_so(recipe, no_network, tmp_path):
    """The share unit is a failure and a refusal: every recipe's output shows one."""
    output = _run(recipe, no_network, _sandbox(recipe, tmp_path)).stdout.lower()
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
