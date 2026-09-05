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
    """Every tracked non-Python file under `examples/`, `docs/` and `tests/` is shipped.

    `MANIFEST.in` has claimed for two releases that a test named this one keeps it honest,
    and there was no such test — so the first file it forgot was found by the CI job that
    builds an sdist and runs its tests, one push after it could have been found here. That is
    the same shape as v0.2's four `.gitignore`d policy files: setuptools resolves
    `MANIFEST.in` against the working tree, so a local run is green either way.

    Checked against **git**, not the filesystem, for the same reason: an untracked file is not
    in a fresh clone whatever this file says about it.

    `tests/` joined the list when `tests/data/junit-10.xsd` did, and it joined it the same way
    the first two did: the sdist job found the missing file one push after this test could
    have. `recursive-include tests *.py` had shipped every test and none of its data.
    """
    import fnmatch

    tracked = subprocess.run(
        ["git", "ls-files", "examples", "docs", "tests"],
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


# --- T136: `adapters/` is packaged by neither the wheel nor the sdist -----------------------


def test_T136_the_ctrlrun_distributions_contain_no_adapter():
    """SPEC-v0.5 §6.1. `pip install ctrlrun` must not grow, and an adapter is a **separate**
    distribution that depends on the kernel rather than the reverse.

    What this test does **not** demonstrate is that `prune adapters` is load-bearing. It is
    not: `packages.find` looks only in `src/`, so the sdist omits `adapters/` with the prune
    removed, and mutation-testing it leaves this test green. The prune is belt and braces,
    and `MANIFEST.in` now says so rather than taking credit.

    What the test does catch is the direction that would actually happen — a
    `recursive-include adapters *.py` added later by someone making the tests travel — which
    puts every adapter in the sdist and turns this red. That is worth a test because
    `MANIFEST.in` resolves against the working tree and not the index, which is how v0.2
    shipped four policy files that every green build had already accounted for.

    The wheel half is separate and is not subsumed: it fails if an adapter is ever moved
    under `src/`, where `packages.find` would collect it.
    """
    import subprocess
    import sys
    import tarfile
    import tempfile
    import zipfile
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as area:
        built = subprocess.run(
            [sys.executable, "-m", "build", "--outdir", area, str(root)],
            capture_output=True,
            text=True,
        )
        # `build` is a dev dependency, so the only skip this test admits is the interpreter
        # not having the module at all. Any *other* non-zero exit is a build that broke, and
        # skipping on it would turn the one check standing between an adapter and the wheel
        # into a check that reports nothing. It skipped in both CI jobs until `build` was
        # named in `[dev]`; a skip that wide is indistinguishable from a pass.
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
        if "adapters/" in name or "ctrlrun_langgraph" in name or "ctrlrun_openai_agents" in name
    ]
    assert not offending, offending


def test_T136_an_adapter_depends_on_ctrlrun_and_never_the_reverse():
    """The direction, asserted rather than assumed: `ctrlrun`'s own metadata names no adapter
    and no framework, in `dependencies` or in any extra.

    **The kernel half runs everywhere; the adapter half runs where there are adapters.** The
    sdist job unpacks the distribution and runs this suite from inside it, and `adapters/` is
    not there -- which is the other half of T136 holding, not a problem. Reading it there was a
    `FileNotFoundError`, found by that job on this branch's first run.

    The skip is narrow on purpose. It fires only when the directory is absent, and the absence
    is itself asserted by `test_T136_the_ctrlrun_distributions_contain_no_adapter` in the same
    file, so there is no configuration in which both halves go unchecked: in a checkout this
    loop runs, and in an sdist the other test is what proves the directory should be missing.
    """
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    with (root / "pyproject.toml").open("rb") as handle:
        kernel = tomllib.load(handle)["project"]

    declared = list(kernel["dependencies"])
    for extra in kernel.get("optional-dependencies", {}).values():
        declared += list(extra)
    lowered = " ".join(declared).lower()

    for framework in ("langgraph", "langchain", "openai-agents", "crewai", "autogen"):
        assert framework not in lowered, framework

    adapters = root / "adapters"
    if not adapters.is_dir():  # pragma: no cover - running from an unpacked sdist
        pytest.skip("no adapters/ here, which is what the sdist half of T136 asserts")

    found = sorted(path for path in adapters.iterdir() if (path / "pyproject.toml").is_file())
    assert found, "adapters/ exists but holds no distribution"
    for adapter in found:
        with (adapter / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle)["project"]
        assert any(name.startswith("ctrlrun") for name in project["dependencies"]), (
            f"{adapter.name} does not depend on ctrlrun"
        )


def test_no_credential_file_is_tracked():
    """`.env` is what the framework probe reads its OpenAI key from (SPEC-v0.4 §7), and it
    sits at the repository root where `git add -A` sweeps it up without asking. It was staged
    once; the only thing between it and a public commit was GitHub's push protection, which
    is a remote-side net and not a local guarantee.

    `.gitignore` now covers it, and this asserts the index rather than the ignore file --
    a path already tracked stays tracked no matter what `.gitignore` says afterwards, which
    is the half of the rule that ignoring alone does not give you.
    """
    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if tracked.returncode != 0:  # pragma: no cover - not a checkout
        pytest.skip("no repository checkout")

    offending = [
        path
        for path in tracked.stdout.split()
        if path.rsplit("/", 1)[-1].startswith(".env")
        and not path.rsplit("/", 1)[-1].endswith(".example")
    ]

    assert offending == [], offending


# --- T139: the README's adapter section, and `docs/adapters.md` -----------------------------


def _readme() -> str:
    path = REPO_ROOT / "README.md"
    if not path.exists():  # pragma: no cover - not a checkout
        pytest.skip("no repository checkout")
    return path.read_text(encoding="utf-8")


def test_T139_the_adapter_section_says_when_you_do_not_need_one_up_front():
    """SPEC-v0.5 §1.1, and the definition of done: the **introducing paragraph** says when you do
    not need an adapter.

    Not a footnote at the end. `@protect` covers anything in this process and the gateway covers
    anything over MCP, so most readers of this section need neither — and a section that led with
    the install line would sell an adapter to every one of them. The position is the requirement,
    so the assertion is on the position.
    """
    text = _readme()

    assert "## Using it inside an agent framework" in text
    section = text.split("## Using it inside an agent framework", 1)[1]
    section = section.split("\n## ", 1)[0]
    opening = section.strip().split("\n\n", 1)[0]

    assert "do not need an adapter" in opening.lower(), opening
    # And it says what covers you instead, in the same breath.
    assert "@protect" in opening and "gateway" in opening.lower(), opening
    # The install line comes after that paragraph, never before it.
    assert section.index("pip install") > section.index("do not need an adapter")


def test_T139_the_adapter_section_names_prevention_and_attribution():
    """§7 item 4 makes this the sentence a security reviewer reads first, so the README carries
    it too rather than leaving it to each adapter's own page."""
    text = _readme()
    section = text.split("## Using it inside an agent framework", 1)[1].split("\n## ", 1)[0]

    assert "prevention" in section and "attribution" in section
    assert "ctrlrun-langgraph" in section and "ctrlrun-openai-agents" in section


#: Words this project will not claim before the milestone that earns them. They may still be
#: *written* -- the badge paragraph says a passing run "does not mean secure, safe, compliant,
#: certified or audited", and `docs/adapters.md` says no adapter describes itself as conformant.
#: Forbidding the strings outright would delete those sentences, which are the honest half.
CLAIM_WORDS = ("conformant", "compliant", "certified", "aligned with")

#: What makes an occurrence a disclaimer rather than a claim.
NEGATORS = ("not ", "no ", "never", "cannot", "n't", "without")


def _claims(text: str) -> list[str]:
    """Sentences using a claim word **affirmatively**."""
    import re

    offending = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n\n", text):
        lowered = sentence.lower()
        if any(word in lowered for word in CLAIM_WORDS) and not any(
            negator in lowered for negator in NEGATORS
        ):
            offending.append(sentence.strip())
    return offending


def test_T139_the_readme_makes_no_conformance_claim():
    """A "conformance kit" names this repository's own acceptance tests run against an adapter
    (§5.1). It is not a certification, and no adapter is described as conformant.

    The check is on the **claim**, not the word. A test that forbade the strings would have
    deleted the badge paragraph's disclaimer -- *"does not mean secure, safe, compliant,
    certified or audited"* -- which is the sentence most worth keeping. So an occurrence is
    allowed where its sentence negates it, and flagged where it does not.
    """
    assert _claims(_readme()) == []


def test_T139_the_claim_check_can_see_a_claim():
    """The precondition, without which the test above passes on any document at all: a sentence
    that *does* make the claim must be caught."""
    assert _claims("CTRLRun is fully compliant with the standard.") != []
    assert _claims("This adapter is conformant.") != []
    # And the shapes that must stay allowed.
    assert _claims("It does not mean secure, safe, compliant, certified or audited.") == []
    assert _claims("No adapter describes itself as conformant.") == []


def test_T139_docs_adapters_exists_and_leads_with_the_three_ways_in():
    path = REPO_ROOT / "docs" / "adapters.md"
    if not path.exists():  # pragma: no cover - not a checkout
        pytest.skip("no repository checkout")
    text = path.read_text(encoding="utf-8")

    assert "You probably do not need one" in text
    assert "@protect" in text and "gateway" in text
    # Item 6's list is the evidence the contract was read by somebody who could not read the code.
    assert "§12.10" in text
    assert _claims(text) == []


def test_the_claims_table_line_numbers_point_at_what_they_name():
    """`docs/CLAIMS.md` says *"Line numbers refer to that tag"*, and until this test they did not:
    seven of twenty-nine resolvable references had drifted by the time v0.5 was cut, pointing at
    a string literal, a comment, or the middle of another function.

    A claims table is evidence, and a reference that lands on the wrong line is the same failure
    as a cited test that does not exist -- which the test below this one has caught since v0.4.
    Both are cheap and both are the only thing standing between the table and rot.

    Only references whose cell **names** a symbol are checked, since those are the ones that can
    be resolved mechanically. A drifted line for a reference with no symbol is not caught here,
    which is stated rather than left to be assumed.
    """
    import re

    claims = REPO_ROOT / "docs" / "CLAIMS.md"
    if not claims.exists():  # pragma: no cover - not a checkout
        pytest.skip("no repository checkout")

    text = claims.read_text(encoding="utf-8")
    # `cli/main.py:400` as well as `state.py:400`. The first version's `[a-z_]+\.py` matched no
    # path with a directory in it, so **every** `cli/main.py` reference was invisible to this
    # guard -- and a review found two of them stale, one broken by the commit that added this
    # very check to the rows beside it. A guard that cannot see a whole class of reference is
    # attribution dressed as prevention.
    reference = re.compile(r"`((?:[a-z_]+/)*[a-z_]+\.py):(\d+)`")
    identifier = re.compile(r"`@?([A-Za-z_][\w.]*)")

    stale = []
    checked = 0
    for row in text.splitlines():
        found_refs = reference.findall(row)
        if not found_refs:
            continue
        # A cell may name several symbols -- "Only `NotExecuted` maps to `FAILED`" -- and the
        # cited line legitimately contains any one of them. Requiring the *nearest* one is how
        # this test first failed against references that were in fact correct.
        named = {name.split(".")[-1] for name in identifier.findall(row)}
        for filename, number in found_refs:
            source = REPO_ROOT / "src" / "ctrlrun" / filename
            if not source.exists():
                continue
            lines = source.read_text(encoding="utf-8").splitlines()
            index = int(number)
            line = lines[index - 1] if 0 < index <= len(lines) else ""
            checked += 1
            if not any(name in line for name in named):
                stale.append(f"{filename}:{index} is {line.strip()[:60]!r}, names {sorted(named)}")

    assert checked, "CLAIMS.md cites no code locations at all"
    assert stale == [], stale


def test_every_documented_install_names_a_distribution_this_repository_builds():
    """`pip install ctrlrun-langgraph` shipped in the README of a release where no such
    distribution existed, so the line 404s for anyone who follows it.

    The publication state is not knowable offline, so this asserts the half that is: every
    `pip install <name>` in the documentation names something this repository actually builds —
    `ctrlrun` itself, one of its extras, or a distribution under `adapters/`. A typo, a rename,
    or a doc written ahead of the code fails here.

    What it deliberately does **not** claim is that the name is on PyPI. That is a release-time
    fact and `.github/workflows/publish.yml` is what makes it true; saying otherwise would be
    the kind of loosely-true assertion this suite keeps refusing.
    """
    import tomllib

    root = REPO_ROOT
    with (root / "pyproject.toml").open("rb") as handle:
        kernel = tomllib.load(handle)["project"]
    known = {kernel["name"]}
    known |= {f"{kernel['name']}[{extra}]" for extra in kernel.get("optional-dependencies", {})}

    adapters = root / "adapters"
    if not adapters.is_dir():  # pragma: no cover - running from an unpacked sdist
        # `adapters/` is pruned from the sdist (SPEC-v0.5 §6.1) while the README that names
        # them ships inside it, so here the invariant is **unverifiable rather than violated**.
        # Same reasoning as T136's other half, and the same narrowness: it holds in a checkout,
        # which is where a doc is written, and the `adapters` CI job runs it with the tree
        # present. Asserting from the sdist would fail every adapter name on principle.
        pytest.skip("adapters/ is not in this distribution, which SPEC-v0.5 §6.1 requires")

    for path in sorted(adapters.iterdir()):
        manifest = path / "pyproject.toml"
        if manifest.is_file():
            with manifest.open("rb") as handle:
                known.add(tomllib.load(handle)["project"]["name"])

    documents = [root / "README.md", root / "docs" / "adapters.md"]
    documents += sorted((root / "adapters").glob("*/README.md")) if adapters.is_dir() else []

    unknown = []
    for document in documents:
        if not document.exists():  # pragma: no cover - not a checkout
            continue
        for match in re.findall(r"pip install \"?([A-Za-z0-9_.\[\]-]+)\"?", document.read_text()):
            if match.startswith(".") or match.startswith("-"):
                continue
            if match.split("==")[0] not in known:
                unknown.append(f"{document.name}: {match}")

    assert unknown == [], unknown


def test_T138_item_sixs_questions_are_recorded_and_each_is_answered():
    """SPEC-v0.5 §8 T138: a third adapter was written against the contract alone, and **the list
    of questions it could not answer is the deliverable**, recorded in `docs/adapters.md` with
    each answered by an edit to `SPEC-v0.5.md`.

    *"If the list cannot be emptied, v0.5 is not done."* So this asserts the list exists, that
    every entry names where it was answered, and that the sections it names are real. The
    adapter itself is disposable and is deliberately not in the repository — asserting its
    existence would be asserting the wrong half.

    This test was missing when v0.5 was otherwise complete, and `docs/adapters.md` carried a
    summary of the list rather than the list. A summary cannot be checked against the spec.
    """
    adapters = REPO_ROOT / "docs" / "adapters.md"
    spec = REPO_ROOT / "docs" / "SPEC-v0.5.md"
    if not adapters.exists() or not spec.exists():  # pragma: no cover - not a checkout
        pytest.skip("no repository checkout")

    text = adapters.read_text(encoding="utf-8")
    # The answer column is `(.*?)` and not `(.+?)` on purpose: an **empty** answer must still
    # match, so the "every row says where it was answered" check below is what catches it. With
    # `(.+?)` the row simply stopped being a row and the count assertion caught it instead --
    # a subsumed guard, found by mutating an answer to empty and watching the wrong test fail.
    rows = re.findall(r"^\| (Q\d+) \| (.+?) \| (.+?) \|(.*?)\|$", text, re.M)

    assert len(rows) >= 14, f"item 6 raised fourteen questions; {len(rows)} are recorded"
    assert [q for q, *_ in rows] == [f"Q{n}" for n in range(1, len(rows) + 1)], [
        q for q, *_ in rows
    ]

    # Every row says where it was answered, and every section it names exists in the spec.
    spec_text = spec.read_text(encoding="utf-8")
    unanswered = [q for q, _, _, answer in rows if not answer.strip()]
    assert unanswered == [], unanswered

    cited = {
        section for _, _, _, answer in rows for section in re.findall(r"§(\d+(?:\.\d+)*)", answer)
    }
    assert cited, "no row names a section of SPEC-v0.5"
    missing = [
        s for s in sorted(cited) if f"### {s} " not in spec_text and f"## {s}. " not in spec_text
    ]
    assert missing == [], f"rows cite sections that do not exist: {missing}"

    # The four the report called security- or correctness-critical are marked as such.
    severities = {severity.strip().strip("*").lower() for _, _, severity, _ in rows}
    assert "security" in severities, severities


def test_the_throwaway_sector_configuration_ships_nowhere():
    """SPEC-v0.6 §7.5, §8's T177b: *"it lives in the test suite and in no `packs/` directory, no
    `examples/`, and no distribution."*

    The artefact is disposable and what it found is the deliverable, so the packaging assertion
    is what keeps it disposable: a configuration that reached a wheel would be a sector pack
    nobody agreed to ship, and §11 ships none.
    """
    root = REPO_ROOT
    assert not (root / "packs").exists(), "a `packs/` directory appeared; §11 ships no pack"

    manifest = (root / "MANIFEST.in").read_text(encoding="utf-8")
    assert "packs" not in manifest

    # The configuration lives in exactly one place, and that place is a test file. The marker is
    # assembled rather than written, so this file does not match its own search.
    marker = "clinician-of" + "-record"
    holders = [
        path
        for path in root.rglob("*.py")
        if marker in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert [path.name for path in holders] == ["test_sector_configuration.py"], (
        f"the throwaway configuration appears in {[str(p) for p in holders]}; §7.5 keeps it in "
        "the test suite and nowhere else"
    )
