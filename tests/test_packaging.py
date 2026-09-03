"""The dependency rule. Build-list item 6a; SPEC-v0.2 §1.1, and the first half of T30.

`pip install ctrlrun` must not grow. An extra's module imports lazily, and a missing extra
raises `MissingDependency` naming the install command — never `ImportError`, which reads to
an operator as a broken package rather than an unselected option.

T30's own assertion, that a **subprocess** running `import ctrlrun` pulls in no module from
an extra, lands with item 8 when there is a second extra to check. The subprocess matters:
in-process it would pass or fail on whatever pytest happened to import first.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

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
