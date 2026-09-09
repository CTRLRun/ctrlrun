"""The v0.6 release pass, packaging half. Item 9; SPEC-v0.6 §8's T181.

**T181 — core still installs nothing new.** v0.6 adds `ctrlrun[postgres]`, and an extra is only
an extra while `pip install ctrlrun` does not pull it in. The wheel and the sdist carry no
adapter, no research tree and no sector pack.

T180 — that no release document blurs alteration with authorship — is **not** here, and its
absence is deliberate rather than a deletion. It scans five documents and three of them are
pages now, so it runs in `CTRLRun/ctrlrun-docs`, the one checkout that can read both trees, as
`tests/test_release_documents.py`. It still covers this repository's `README.md` and
`CHANGELOG.md`, and this repository's CI runs it from there against the commit being proposed:
the `docs` job. Splitting the scan in two would have been two implementations of one rule, and
two rules that eventually disagree.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_T181_core_still_installs_pyyaml_and_click_and_nothing_else() -> None:
    """SPEC-v0.6 §8's T181. v0.6 adds `ctrlrun[postgres]`, and `psycopg` is only an extra
    while `pip install ctrlrun` does not pull it in."""
    path = REPO_ROOT / "pyproject.toml"
    if not path.exists():  # pragma: no cover - not a checkout
        pytest.skip("no repository checkout")
    project = tomllib.loads(path.read_text(encoding="utf-8"))["project"]
    names = {re.split(r"[<>=!\[ ]", entry)[0].lower() for entry in project["dependencies"]}
    assert names == {"pyyaml", "click"}, names
    assert "postgres" in project["optional-dependencies"], (
        "v0.6 ships a Postgres backend and it must be an extra"
    )


def test_T181_the_distributions_carry_no_adapter_no_research_and_no_pack() -> None:
    """The other half of T181, and the reason it is built rather than read: `MANIFEST.in`
    resolves against the **working tree** and not the index, so the only way to know what ships
    is to build it. v0.2 shipped four policy files `.gitignore` had swallowed, and every green
    build had already accounted for them.

    `research/` is the new name here. `research/framework-probe/` and `research/soak/` are both
    harnesses whose *results* are published and whose code ships nowhere (`v0.4 §7`, §8.1).

    **This overlaps `test_T136_the_ctrlrun_distributions_contain_no_adapter`, deliberately.**
    T136 grew the `research/` and `packs/` check when item 8's `tests/test_soak.py` needed a
    guarantee behind its skip; §8 asks T181 for all three in one place as the release check, and
    the two are allowed to agree. What is not allowed is neither of them running, which is why
    the skip below is the whole repository being absent and nothing narrower.
    """
    root = REPO_ROOT
    if not (root / "pyproject.toml").exists():  # pragma: no cover - not a checkout
        pytest.skip("no repository checkout")

    with tempfile.TemporaryDirectory() as area:
        built = subprocess.run(
            [sys.executable, "-m", "build", "--outdir", area, str(root)],
            capture_output=True,
            text=True,
        )
        if built.returncode != 0:
            if "No module named build" in built.stderr:  # pragma: no cover - dev dependency
                pytest.skip("python -m build is not installed")
            raise AssertionError(f"python -m build failed:\n{built.stderr[-2000:]}")

        names: list[str] = []
        for artifact in Path(area).iterdir():
            if artifact.suffix == ".whl":
                names += zipfile.ZipFile(artifact).namelist()
            elif artifact.name.endswith(".tar.gz"):
                with tarfile.open(artifact) as archive:
                    names += archive.getnames()

    assert names, "nothing was built"
    offending = [
        name
        for name in names
        # Anchored on a path **segment**, so a file called `research.py` is not a hit and a
        # directory called `research/` is. The first version matched the bare substring and
        # would have fired on `docs/research.md`.
        if any(part in {"adapters", "research", "packs"} for part in Path(name).parts)
    ]
    assert offending == [], offending
