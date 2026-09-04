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
            # SPEC-v0.3 §1.1 — `jwt` joins the list with build-list item 5. T92 asserts the
            # same thing from the identity tests' side; this is the one place that names
            # every extra at once, so a fourth cannot be added without editing it.
            "import ctrlrun, sys;"
            "print(sorted(n for n in sys.modules"
            " if n.split('.')[0] in ('httpx', 'opentelemetry', 'jwt')"
            " or n in ('ctrlrun.gateway', 'ctrlrun.otel', 'ctrlrun.acs',"
            " 'ctrlrun.jwt_identity')))",
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
        (
            "from ctrlrun.jwt_identity import JWTIdentityProvider;"
            "JWTIdentityProvider(secret='s', algorithms=['HS256'], issuer='i', audience='a',"
            " token_type='at+jwt')",
            "identity",
        ),
    ],
)
def test_T30_a_missing_extra_says_which_one_to_install(factory, extra, tmp_path):
    """Never `ModuleNotFoundError`: an operator reads that as a broken package rather than
    as an option they did not select. Run in a subprocess with the extra's module hidden."""
    script = (
        "import sys, importlib.abc\n"
        "class Block(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        f"        if name.split('.')[0] in ('httpx', 'opentelemetry', 'jwt'):\n"
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


def test_the_identity_extra_declares_pyjwt_with_its_crypto_extra():
    """§3.4 — without `[crypto]`, PyJWT can only do `HS*`, and every asymmetric algorithm
    raises at verification time rather than at install time. An extra that installs cleanly
    and then cannot verify an RS256 token is worse than one that names its dependency."""
    declared = " ".join(_pyproject()["project"]["optional-dependencies"]["identity"])

    assert "pyjwt" in declared.lower()
    assert "crypto" in declared.lower()


def test_the_core_dependencies_have_not_grown():
    """§1.1 — `pip install ctrlrun` installs `pyyaml` and `click`, and v0.3 changes that by
    exactly nothing: the whole authority model is stdlib plus the YAML parser already there."""
    declared = _pyproject()["project"]["dependencies"]
    names = {re.split(r"[<>=!~\[ ]", line.strip())[0].lower() for line in declared}

    assert names == CORE_DEPENDENCIES


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


# --- the sdist carries what the tests read (SPEC-v0.2 §1.1) -----------------------------


def _manifest_patterns() -> tuple[list[tuple[str, str]], set[str]]:
    """`MANIFEST.in`'s `recursive-include` pairs and its plain `include` files."""
    recursive: list[tuple[str, str]] = []
    plain: set[str] = set()
    for line in (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if not parts or parts[0].startswith("#"):
            continue
        if parts[0] == "recursive-include" and len(parts) >= 3:
            recursive += [(parts[1], pattern) for pattern in parts[2:]]
        elif parts[0] == "include":
            plain.update(parts[1:])
    return recursive, plain


def test_the_sdist_carries_everything_the_tests_read():
    """Every tracked file under `examples/` and `docs/` matches a `MANIFEST.in` include.

    `MANIFEST.in` has claimed for two releases that a test named this one keeps it honest,
    and there was no such test — so the first file it forgot was found by the CI job that
    builds an sdist and runs its tests, one push after it could have been found here. That is
    the same shape as v0.2's four `.gitignore`d policy files: setuptools resolves
    `MANIFEST.in` against the working tree, so a local run is green either way.

    Checked against **git**, not the filesystem, for the same reason: an untracked file is not
    in a fresh clone whatever this file says about it.
    """
    import fnmatch

    tracked = subprocess.run(
        ["git", "ls-files", "examples", "docs"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if tracked.returncode != 0:  # pragma: no cover - not a checkout
        pytest.skip("no repository checkout")
    recursive, plain = _manifest_patterns()

    missing = [
        path
        for path in tracked.stdout.split()
        if path not in plain
        and not any(
            path.startswith(f"{directory}/") and fnmatch.fnmatch(Path(path).name, pattern)
            for directory, pattern in recursive
        )
    ]

    assert not missing, f"MANIFEST.in does not ship: {sorted(missing)}"


def test_the_manifest_check_would_notice_a_file_it_does_not_ship():
    """The control. A check whose only evidence is a green suite is a check nothing
    exercises, and this one is only ever *not* triggered."""
    import fnmatch

    recursive, plain = _manifest_patterns()
    invented = "examples/authority/notes.rst"

    assert invented not in plain
    assert not any(
        invented.startswith(f"{directory}/") and fnmatch.fnmatch("notes.rst", pattern)
        for directory, pattern in recursive
    )


def test_the_package_never_encodes_a_token():
    """`docs/CLAIMS.md` — "CTRLRun issues no credential and defines no identity format".

    A claim in the README needs a test, and this one is structural: the package verifies
    tokens and never mints one, so no module may call `jwt.encode`, and only `jwt_identity`
    may name the verifier at all. Asserted over the source rather than at runtime, because
    "we never call it" is a property of the code and not of one execution.
    """
    minting = []
    for path in (REPO_ROOT / "src" / "ctrlrun").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if ".encode(" in text and "jwt" in text:
            for number, line in enumerate(text.splitlines(), 1):
                if "jwt" in line and ".encode(" in line:
                    minting.append(f"{path.name}:{number}")

    assert not minting, f"the package appears to sign a token: {minting}"


def test_every_test_docs_claims_cites_exists():
    """`docs/CLAIMS.md` maps every README sentence to the test that proves it, and a citation
    naming a test that does not exist is the same false claim the file exists to prevent.

    It has been regenerated by hand at three releases now, and the names drift: a test gets
    renamed, the row keeps the old one, and the table reads as evidence while pointing at
    nothing. This is cheap and it is the only thing standing between the two.
    """
    import re

    claims = REPO_ROOT / "docs" / "CLAIMS.md"
    if not claims.exists():  # pragma: no cover - not a checkout
        pytest.skip("no repository checkout")
    cited = set(re.findall(r"`(test_[A-Za-z0-9_]+)`", claims.read_text(encoding="utf-8")))
    defined: set[str] = set()
    for path in (REPO_ROOT / "tests").glob("*.py"):
        defined.update(re.findall(r"^def (test_[A-Za-z0-9_]+)", path.read_text(), re.M))

    assert cited, "CLAIMS.md cites no tests at all, which is not a table of evidence"
    assert not cited - defined, (
        f"CLAIMS.md cites tests that do not exist: {sorted(cited - defined)}"
    )
