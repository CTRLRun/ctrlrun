"""The dependency rule. Build-list item 6a; SPEC-v0.2 §1.1, and the first half of T30.

`pip install ctrlrun` must not grow. An extra's module imports lazily, and a missing extra
raises `MissingDependency` naming the install command — never `ImportError`, which reads to
an operator as a broken package rather than an unselected option.

T30's own assertion, that a **subprocess** running `import ctrlrun` pulls in no module from
an extra, lands with item 8 when there is a second extra to check. The subprocess matters:
in-process it would pass or fail on whatever pytest happened to import first.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

from ctrlrun import MissingDependency

REPO_ROOT = Path(__file__).resolve().parents[1]

#: SPEC-v0.2 §1.1 — what `pip install ctrlrun` is allowed to pull in, and nothing else.
CORE_DEPENDENCIES = {"pyyaml", "click"}


def _pyproject() -> dict:
    path = REPO_ROOT / "pyproject.toml"
    if not path.exists():  # installed without the source tree
        pytest.skip("no repository checkout")
    return tomllib.loads(path.read_text(encoding="utf-8"))


def test_T30_a_subprocess_importing_ctrlrun_pulls_in_no_module_from_an_extra():
    """SPEC-v0.2 §10's T30, in one place. A **subprocess**, because in-process this would
    pass or fail on whatever pytest happened to import first."""
    finished = subprocess.run(
        [
            sys.executable,
            "-c",
            "import ctrlrun, sys;"
            "print(sorted(n for n in sys.modules"
            " if n.split('.')[0] in ('httpx', 'opentelemetry')"
            " or n in ('ctrlrun.gateway', 'ctrlrun.otel', 'ctrlrun.acs')))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert finished.stdout.strip() == "[]"


@pytest.mark.parametrize(
    ("factory", "extra"),
    [
        ("from ctrlrun.gateway import serve; serve(upstream='http://x', alias='a')", "gateway"),
        ("from ctrlrun.otel import OTelEventSink; OTelEventSink()", "otel"),
    ],
)
def test_T30_a_missing_extra_says_which_one_to_install(factory, extra, tmp_path):
    """Never `ModuleNotFoundError`: an operator reads that as a broken package rather than
    as an option they did not select. Run in a subprocess with the extra's module hidden."""
    script = (
        "import sys, importlib.abc\n"
        "class Block(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        f"        if name.split('.')[0] in ('httpx', 'opentelemetry'):\n"
        "            raise ImportError(name)\n"
        "        return None\n"
        "sys.meta_path.insert(0, Block())\n"
        "from ctrlrun import MissingDependency\n"
        "try:\n"
        f"    {factory}\n"
        "except MissingDependency as exc:\n"
        "    print(str(exc))\n"
        "except BaseException as exc:\n"
        "    print('WRONG:', type(exc).__name__, exc)\n"
    )
    finished = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )

    assert f"pip install 'ctrlrun[{extra}]'" in finished.stdout, finished.stdout
    assert "WRONG" not in finished.stdout


def test_the_otel_extra_declares_the_api_sdk_and_exporter():
    """§11 — the API alone would do for a library, but an operator installing this wants to
    export without assembling a stack."""
    declared = " ".join(_pyproject()["project"]["optional-dependencies"]["otel"])

    assert "opentelemetry-api" in declared
    assert "opentelemetry-sdk" in declared
    assert "otlp" in declared


#: A spec-shaped code block: valid Python, and deliberately not what the formatter would write.
_ALIGNED_BLOCK = """@dataclass(frozen=True)
class Principal:
    agent: str                     # aligned, so a reader can compare down the column
    user: str | None = None        # and the alignment is the point
"""

_SPEC_PAGE = (
    "A specification, whose code blocks are illustration rather than code.\n\n"
    "```python\n" + _ALIGNED_BLOCK + "```\n"
)


def _ruff_format_check(path: Path) -> int:
    """`ruff format --check` under this repository's configuration, on one file."""
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "format",
            "--check",
            "--no-cache",
            "--config",
            str(REPO_ROOT / "pyproject.toml"),
            str(path),
        ],
        capture_output=True,
        text=True,
    ).returncode


def test_the_formatter_leaves_markdown_alone(tmp_path):
    """`ruff format` reads Python code blocks inside Markdown and `ruff check` does not, so
    whether a spec's examples get reformatted turns on whether they happen to parse. SPEC-v0.1
    and v0.2 keep their aligned comments only because theirs carry unparseable placeholders;
    SPEC-v0.3's parse, and a CI check that had never fired before went red on them.

    A specification's blocks elide and annotate and line comments up to be read. The formatter
    cannot know that, so `[tool.ruff.format] exclude` tells it, and this test is what stops the
    exclusion being dropped by someone who does not meet the trap until CI does.
    """
    page = tmp_path / "SPEC-probe.md"
    page.write_text(_SPEC_PAGE, encoding="utf-8")

    assert _ruff_format_check(page) == 0, "the formatter wants to rewrite a Markdown file"


def test_the_formatter_would_have_reformatted_that_block_as_python(tmp_path):
    """The control for the test above. Without it a `ruff` that had stopped working — or a
    block that was already formatted — would pass that test while proving nothing, which is a
    negative test against something the environment already prevents."""
    module = tmp_path / "probe.py"
    module.write_text(_ALIGNED_BLOCK, encoding="utf-8")

    assert _ruff_format_check(module) != 0, (
        "the probe is already formatted, so the Markdown assertion proves nothing"
    )


def test_ci_runs_the_check_script():
    """`scripts/check.sh` is what CI's `check` job runs, so "it passed locally" and "it passed
    in CI" cannot come to mean different things. If a step goes back to naming the tools inline
    the two ends can drift again, which is how the Markdown surprise reached `main`.

    Skipped outside a checkout: `MANIFEST.in` prunes `.github`, so an sdist carries no workflow
    to read and a packager building from one has no CI wiring to check.
    """
    workflow = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    if not workflow.exists():
        pytest.skip("no repository checkout")
    steps = yaml.safe_load(workflow.read_text())["jobs"]["check"]["steps"]
    commands = " ".join(step.get("run", "") for step in steps)

    assert "scripts/check.sh" in commands
    for tool in ("pytest", "mypy", "ruff"):
        assert not re.search(rf"(^|\s){tool}\s", commands), (
            f"CI's check job invokes {tool} directly; it should go through scripts/check.sh"
        )


def test_no_dataclass_field_has_an_unhashable_default():
    """Python 3.11 refuses *any* unhashable dataclass default; 3.12 relaxed the check to
    `list`, `dict` and `set` only.

    So `claims: Mapping[...] = MappingProxyType({})` imports cleanly on a 3.12 developer
    machine and raises `ValueError` at class definition on 3.11 — the floor this package
    supports, and the version a local run never sees. CI's matrix caught it once; this catches
    it on whichever version happens to be running, because a guard only CI knows about is one
    that fails after the push rather than before it.

    The fix is always `field(default_factory=...)`.
    """
    import dataclasses
    import importlib
    import pkgutil

    import ctrlrun

    offenders = []
    for info in pkgutil.walk_packages(ctrlrun.__path__, prefix="ctrlrun."):
        if any(part in info.name for part in ("otel", "jwt_identity")):
            continue  # lazily imported behind an extra; importing here would defeat T30
        try:
            module = importlib.import_module(info.name)
        except Exception:  # pragma: no cover - an extra that is not installed
            continue
        for value in vars(module).values():
            if not dataclasses.is_dataclass(value) or not isinstance(value, type):
                continue
            for spec in dataclasses.fields(value):
                default = spec.default
                if default is dataclasses.MISSING:
                    continue
                try:
                    hash(default)
                except TypeError:
                    # Attempting the hash rather than reading `__class__.__hash__`, which is
                    # what 3.11's dataclasses check reads. Python 3.12 made `mappingproxy`
                    # hashable-when-its-mapping-is, so the class-level read passes on 3.12 and
                    # fails on 3.11 — testing the version that is running would have missed
                    # exactly the bug this exists for.
                    offenders.append(f"{value.__module__}.{value.__name__}.{spec.name}")

    assert offenders == [], (
        "unhashable dataclass defaults fail at import on Python 3.11: " + ", ".join(offenders)
    )


def test_core_declares_only_pyyaml_and_click():
    declared = {
        name.split("[")[0].split(">")[0].split("=")[0].strip().lower()
        for name in _pyproject()["project"]["dependencies"]
    }

    assert declared == CORE_DEPENDENCIES


def test_the_gateway_extra_declares_its_http_client():
    """§6.11 — the extra's only dependency is an HTTP client, and the listening side is
    stdlib `http.server`. An ASGI stack here would be a second server in the package."""
    extras = _pyproject()["project"]["optional-dependencies"]

    assert extras["gateway"], "the gateway extra must declare its HTTP client"
    assert not any("uvicorn" in name or "starlette" in name for name in extras["gateway"])


def test_an_extra_is_never_in_core_dependencies():
    project = _pyproject()["project"]
    core = {
        name.split("[")[0].split(">")[0].split("=")[0].strip() for name in project["dependencies"]
    }
    for extra, names in project["optional-dependencies"].items():
        if extra == "dev":
            continue
        for name in names:
            assert name.split("[")[0].split(">")[0].split("=")[0].strip() not in core


def test_importing_ctrlrun_does_not_import_the_gateway_extra():
    """A subprocess, because in-process this would pass on whatever pytest imported first."""
    finished = subprocess.run(
        [sys.executable, "-c", "import ctrlrun, sys; print('httpx' in sys.modules)"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert finished.stdout.strip() == "False"


def test_importing_ctrlrun_does_not_import_a_tls_stack():
    """§7's outbound half is stdlib, but `urllib.request` drags in `ssl`, and §1.1's rule is
    that a core install pays only for what it uses. The import is inside the method."""
    finished = subprocess.run(
        [sys.executable, "-c", "import ctrlrun, sys; print('ssl' in sys.modules)"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert finished.stdout.strip() == "False"


def test_importing_ctrlrun_does_not_import_the_gateway_package():
    finished = subprocess.run(
        [sys.executable, "-c", "import ctrlrun, sys; print('ctrlrun.gateway' in sys.modules)"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert finished.stdout.strip() == "False"


def test_a_missing_extra_raises_MissingDependency_naming_the_install_command(monkeypatch):
    """Never `ImportError` or `ModuleNotFoundError` (§1.1): an operator reads those as a
    broken package rather than as an option they did not select."""
    import ctrlrun.gateway as gateway

    monkeypatch.setattr(gateway, "_HTTP_CLIENT", "no_such_module_at_all")

    with pytest.raises(MissingDependency) as raised:
        gateway.serve(upstream="http://localhost:8000", alias="acme")

    assert "pip install 'ctrlrun[gateway]'" in str(raised.value)
    assert not isinstance(raised.value, ImportError)


def test_MissingDependency_is_a_ctrlrun_error():
    from ctrlrun import CTRLRunError

    assert issubclass(MissingDependency, CTRLRunError)


def test_MissingDependency_names_the_extra_and_the_command():
    error = MissingDependency("httpx", "gateway")

    assert "httpx" in str(error)
    assert "pip install 'ctrlrun[gateway]'" in str(error)
