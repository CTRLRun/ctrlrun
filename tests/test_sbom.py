# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""The SBOM says what the wheel actually carries, and stays wrong-proof.

An SBOM is not a claim, which is why it is here at all: `ROADMAP.md`'s standards rule is
*integrate first, map second, never claim compliance*, and a bill of materials asserts no
conformance with anything. It is a measurement of one artifact.

**Which is exactly why a wrong one is worse than none.** A consumer reads it to decide what they
are taking on, and the failure mode is silent: nothing about a stale or padded SBOM looks broken.
`scripts/sbom.sh` measures the built wheel in an empty environment rather than reading
`pyproject.toml`, so the document cannot drift from the manifest without drifting from reality
first, and this file pins the result.

The environment is the subtle part. `python -m venv` seeds pip, and a scanner reading that
environment cannot tell "ctrlrun needs this" from "the venv came with this". The script
uninstalls pip before scanning; the assertion below is what stops that from rotting quietly if a
future Python seeds something else, because the failure would otherwise be an SBOM that
overstates the dependency surface and a suite that stays green.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "sbom.sh"

#: What `pyproject.toml` declares, and `test_core_declares_only_pyyaml_and_click` pins from the
#: other side. Names as the installed distributions spell them, which is not always how the
#: requirement does: PyPI serves `pyyaml` and the distribution calls itself `PyYAML`.
EXPECTED = {"PyYAML", "click"}


@pytest.fixture(scope="module")
def sbom(tmp_path_factory) -> dict:
    """One wheel, built once, scanned once. The slow part is the build, not the scan."""
    if not SCRIPT.exists():  # pragma: no cover - sdist prunes scripts/
        pytest.skip("scripts/ is not in this distribution")
    if shutil.which("cyclonedx-py") is None:
        pytest.skip("cyclonedx-py is not installed; it is in requirements/sbom.txt")

    workspace = tmp_path_factory.mktemp("sbom")
    build = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(workspace)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if build.returncode != 0:  # pragma: no cover - `build` is in the dev extra
        pytest.skip(f"could not build a wheel: {build.stderr[-400:]}")

    wheel = next(workspace.glob("*.whl"))
    out = workspace / "sbom.cdx.json"
    generated = subprocess.run(
        [str(SCRIPT), str(wheel), str(out)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert generated.returncode == 0, f"{generated.stdout}\n{generated.stderr}"
    return json.loads(out.read_text(encoding="utf-8"))


def test_the_sbom_lists_the_two_runtime_dependencies_and_nothing_else(sbom) -> None:
    """The measurement, and the reason the script uninstalls pip.

    Asserting equality rather than containment is deliberate. `>=` would pass an SBOM that had
    quietly grown `pip`, `setuptools` or a transitive dependency nobody chose, which is the
    failure this document exists to make visible.
    """
    found = {component["name"] for component in sbom.get("components", [])}

    assert found == EXPECTED, (
        f"the SBOM lists {sorted(found)}; the wheel declares {sorted(EXPECTED)}. "
        "Extra names usually mean the scanned environment was not empty."
    )


def test_the_sbom_names_this_distribution_as_its_root(sbom) -> None:
    """A bill of materials for nothing in particular is not one. The root carries the name, the
    version and the licence a consumer is actually taking on."""
    root = sbom["metadata"]["component"]

    assert root["name"] == "ctrlrun"
    assert root["type"] == "library"
    assert root["version"], "the root component carries no version"
    declared = {
        entry["license"].get("id") for entry in root.get("licenses", []) if "license" in entry
    }
    assert "Apache-2.0" in declared, f"root licences are {declared}"


def test_the_sbom_is_cyclonedx_and_says_which_version(sbom) -> None:
    """A consumer's scanner dispatches on these two fields. Without them the file is JSON that
    happens to look like an SBOM."""
    assert sbom["bomFormat"] == "CycloneDX"
    assert sbom["specVersion"].startswith("1."), sbom["specVersion"]


def test_every_component_carries_a_version_and_a_purl(sbom) -> None:
    """The two fields that make a component resolvable. A name alone does not identify what was
    installed, and a vulnerability scanner matching on names alone is guessing."""
    thin = [
        component["name"]
        for component in sbom.get("components", [])
        if not component.get("version") or not component.get("purl")
    ]

    assert thin == [], f"components with no version or no purl: {thin}"
