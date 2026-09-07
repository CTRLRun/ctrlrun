"""The Reference pages: generated where the code can say it, and tested against the code where
it cannot.

- The CLI page, the receipt and event page, the errors page and the Python API pages are the
  generators' output, byte for byte.
- Every frozen public name has a docstring and a page.
- The policy and authority YAML pages name every key the loaders accept and every operator.
- The exit-codes page says what `ctrlrun verify --help` says.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS = REPO_ROOT / "tools" / "docs_audit"
DOCS = REPO_ROOT / "docs"

if not (TOOLS.exists() and (DOCS / "docs" / "reference").exists()):  # pragma: no cover
    pytest.skip("no repository checkout", allow_module_level=True)

sys.path.insert(0, str(TOOLS))

import render_api  # noqa: E402
import render_cli  # noqa: E402
import render_schemas  # noqa: E402

# --- generated pages match the code ------------------------------------------------------


def test_the_cli_reference_matches_clicks_help_text():
    assert render_cli.TARGET.read_text(encoding="utf-8") == render_cli.render()


def test_the_cli_reference_would_notice_a_changed_option():
    page = render_cli.TARGET.read_text(encoding="utf-8")
    assert "--verify-chain" in page and "--store-url" in page
    assert render_cli.render().replace("--verify-chain", "--verify-chan") != page


def test_the_schema_and_error_pages_match_the_code():
    assert render_schemas.SCHEMAS.read_text(encoding="utf-8") == render_schemas.render_schemas()
    assert render_schemas.ERRORS.read_text(encoding="utf-8") == render_schemas.render_errors()


def test_the_schema_page_names_every_receipt_field_and_event_type():
    import dataclasses

    from ctrlrun import Receipt
    from ctrlrun.receipt import EventType

    page = render_schemas.SCHEMAS.read_text(encoding="utf-8")
    for item in dataclasses.fields(Receipt):
        assert f"`{item.name}`" in page, item.name
    for member in EventType:
        assert f"`{member.value}`" in page, member.value


def test_the_errors_page_names_every_exception():
    import inspect

    from ctrlrun import errors

    page = render_schemas.ERRORS.read_text(encoding="utf-8")
    for name, obj in inspect.getmembers(errors, inspect.isclass):
        if obj.__module__ == errors.__name__:
            assert f"### {name}" in page, name


def test_the_api_reference_matches_the_docstrings():
    assert render_api.check(render_api.render()) == []


def test_every_rendered_api_signature_is_the_one_python_would_accept():
    """A reader copies the signature off the page and calls it. It has to work.

    The renderer used to emit `name: annotation` and nothing else, which dropped the `*` and
    every default — so `protect('stripe.refund', 'refund:{id}')`, taken straight off
    `reference/api/protect.mdx`, raised `TypeError: takes 1 positional argument but 2 were
    given`. The launch-readiness audit tried thirteen of these pages and all thirteen raised.

    This compares the rendered parameter list against `inspect.signature`, name by name and
    marker by marker, for every public callable. It is stricter than "the page exists" and it
    is the check that would have caught it.
    """
    import inspect

    import ctrlrun

    checked = 0
    for name in sorted(ctrlrun.__all__):
        member = getattr(ctrlrun, name)
        if not inspect.isfunction(member) and not inspect.isclass(member):
            continue
        page = DOCS / "docs" / "reference" / "api" / f"{name}.mdx"
        if not page.exists():
            continue
        # The first python block is the import line; the signature is the one that declares.
        blocks = re.findall(
            r"^```python\n(.*?)^```$", page.read_text(encoding="utf-8"), re.M | re.S
        )
        declaring = [b for b in blocks if b.lstrip().startswith(("def ", "class "))]
        assert declaring, f"{name}.mdx has no signature block"
        rendered = declaring[0]

        if inspect.isfunction(member):
            declared = f"def {name}("
            assert rendered.startswith(declared), rendered[:80]
            params = rendered[len(declared) : rendered.rindex(")")]
        else:
            found = re.search(r"def __init__\((.*)\)", rendered)
            if found is None:
                continue  # a Protocol or a dataclass with no __init__ of its own
            params = found.group(1)

        target = member if inspect.isfunction(member) else member.__init__
        expected = [
            p.name
            for p in inspect.signature(target).parameters.values()
            if p.name not in {"self", "cls"}
        ]
        rendered_names = [
            piece.strip().split(":")[0].split("=")[0].strip().lstrip("*")
            for piece in _split_parameters(params)
            if piece.strip() not in {"", "*", "/"}
        ]
        assert rendered_names == expected, (
            f"{name}: page says {rendered_names}, code says {expected}"
        )

        # The marker itself: everything after a keyword-only parameter's `*` must be one.
        signature = inspect.signature(target)
        keyword_only = [p.name for p in signature.parameters.values() if p.kind is p.KEYWORD_ONLY]
        if keyword_only and not any(
            p.kind is p.VAR_POSITIONAL for p in signature.parameters.values()
        ):
            assert "*" in _split_parameters(params) or any(
                piece.strip().startswith("*") for piece in _split_parameters(params)
            ), f"{name}: {keyword_only} are keyword-only and the page shows no `*`"
        checked += 1

    assert checked >= 20, f"only {checked} signatures compared; this check found nothing to do"


def _split_parameters(text: str) -> list[str]:
    """Split on the commas that separate parameters, not the ones inside brackets."""
    pieces, depth, current = [], 0, ""
    for character in text:
        if character in "[({":
            depth += 1
        elif character in "])}":
            depth -= 1
        if character == "," and depth == 0:
            pieces.append(current)
            current = ""
            continue
        current += character
    if current.strip():
        pieces.append(current)
    return pieces


def test_every_frozen_public_name_has_a_docstring_and_a_page():
    import importlib

    import ctrlrun

    names = [(ctrlrun, name) for name in ctrlrun.__all__]
    for module_path, name in render_api.EXTRA_NAMES:
        try:
            names.append((importlib.import_module(module_path), name))
        except ImportError:  # pragma: no cover - an extra not installed here
            continue
    missing_doc = [name for module, name in names if not _documented(getattr(module, name))]
    assert missing_doc == [], f"public names with no docstring: {missing_doc}"

    pages = {p.stem for p in (DOCS / "docs" / "reference" / "api").glob("*.mdx")}
    for module_path, name in [("ctrlrun", n) for n in ctrlrun.__all__] + list(
        render_api.EXTRA_NAMES
    ):
        assert render_api._slug(module_path, name) in pages, f"{module_path}.{name} has no page"


def _documented(obj: object) -> bool:
    import inspect
    import typing

    if typing.get_origin(obj) is typing.Literal:
        # A Literal alias has no `__doc__`; its docstring is the string statement after the
        # assignment, which griffe reads and the API page shows.
        page = DOCS / "docs" / "reference" / "api" / "ReconcileOutcome.mdx"
        return page.exists() and "No docstring" not in page.read_text(encoding="utf-8")
    return bool(inspect.getdoc(obj))


# --- hand-written pages cover what the loaders accept -------------------------------------


def _keys_in(page: Path) -> set[str]:
    return set(re.findall(r"`([A-Za-z_][\w./:-]*)`", page.read_text(encoding="utf-8")))


def test_the_policy_reference_names_every_key_and_operator_the_loader_accepts():
    from ctrlrun import policy

    named = _keys_in(DOCS / "docs" / "reference" / "policy-yaml.mdx")
    expected = (
        set(policy._TOP_LEVEL_KEYS)
        | set(policy._ENTRY_KEYS)
        | set(policy._RULE_KEYS)
        | set(policy._MCP_KEYS)
        | set(policy._CONTROL_KEYS)
        | set(policy._DATA_KEYS)
        | set(policy._OPERATORS)
        | {
            policy.POLICY_SCHEMA,
            policy.POLICY_SCHEMA_V2,
            policy.POLICY_SCHEMA_V3,
            policy.POLICY_SCHEMA_V4,
        }
        | set(policy.DERIVED_SUBJECTS)
    )
    assert expected - named == set(), f"policy-yaml.mdx does not name: {sorted(expected - named)}"


def test_the_authority_reference_names_every_grant_key():
    from ctrlrun import authority

    named = _keys_in(DOCS / "docs" / "reference" / "authority-yaml.mdx")
    expected = (
        set(authority._AUTHORITY_KEYS) | set(authority._GRANT_KEYS) | set(authority._SUBJECT_KEYS)
    )
    assert expected - named == set(), (
        f"authority-yaml.mdx does not name: {sorted(expected - named)}"
    )
    assert str(authority.DEFAULT_MAX_DELEGATION_DEPTH) in (
        DOCS / "docs" / "reference" / "authority-yaml.mdx"
    ).read_text(encoding="utf-8")


def test_the_exit_codes_page_says_what_verify_says():
    from click.testing import CliRunner

    from ctrlrun.cli.main import main

    help_text = " ".join(CliRunner().invoke(main, ["verify", "--help"]).output.split())
    page = " ".join(
        (DOCS / "docs" / "reference" / "exit-codes.mdx").read_text(encoding="utf-8").split()
    )
    for sentence in (
        "0 every applicable guarantee passed and at least one was applicable",
        "1 a guarantee FAILED",
        "2 the configuration was refused or is unusable",
        "3 an internal error in verify itself",
    ):
        assert sentence in help_text, sentence
    assert "every applicable guarantee passed and at least one was applicable" in page
    assert "an internal error in verify itself" in page
    assert "`0/0`" in page


def test_the_exit_codes_page_matches_the_report():
    from ctrlrun.verify.report import Report

    source = Path(__import__("inspect").getsourcefile(Report)).read_text(encoding="utf-8")
    assert "return 2" in source and "self.applicable == 0" in source


def test_every_quoted_verify_transcript_is_one_verify_actually_prints():
    """The pages that quote `ctrlrun verify` are checked against a real run of their own policy.

    Both of them were **written rather than captured**. Each showed eight `PASS  stripe.refund`
    rows; the policy on the same page makes verify exercise `k8s.delete_namespace` on every one
    of them, because it takes the first action that fits in alphabetical order. Each also
    dropped the stderr line G7's own scenario logs, and one misaligned a column. Found by the
    launch-readiness audit, which ran them.

    That is the false-green problem this project spends thousands of words warning about,
    arriving in its own quoted evidence. So the transcript is compared with a run: every line
    except the `policy` line, whose absolute path is machine-specific and is labelled as such
    on both pages.
    """
    import subprocess
    import sys
    import tempfile

    pages = [
        DOCS / "docs" / "guides" / "verify-in-ci.mdx",
        DOCS / "docs" / "cookbook" / "verify-in-github-actions.mdx",
    ]
    for page in pages:
        text = page.read_text(encoding="utf-8")
        # The guide indents its blocks inside `<Steps>`, so the fence and the document are
        # both offset; the cookbook page's are flush.
        policy = re.search(r"```yaml[^\n]*\n(\s*schema: ctrlrun\.policy.*?)```", text, re.S)
        assert policy, f"{page.name} quotes no policy"
        quoted = re.search(r"```text\n(.*?CTRLRun verify.*?)```", text, re.S)
        assert quoted, f"{page.name} quotes no transcript"

        with tempfile.TemporaryDirectory() as directory:
            indent = " " * (len(policy.group(1)) - len(policy.group(1).lstrip(" ")))
            document = "\n".join(
                line[len(indent) :] if line.startswith(indent) else line
                for line in policy.group(1).splitlines()
            )
            (Path(directory) / "ctrlrun.yaml").write_text(document, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-m", "ctrlrun.cli.main", "verify"],
                cwd=directory,
                capture_output=True,
                text=True,
                check=False,
            )
        printed = (result.stderr + result.stdout).splitlines()

        page_indent = " " * (len(quoted.group(1)) - len(quoted.group(1).lstrip(" ")))
        shown = [
            line[len(page_indent) :] if line.startswith(page_indent) else line
            for line in quoted.group(1).splitlines()
        ]
        skip = ("policy     ",)
        shown = [line for line in shown if line.strip() and not line.startswith(skip)]
        printed = [line for line in printed if line.strip() and not line.startswith(skip)]
        assert shown == printed, (
            f"{page.name} quotes a transcript verify does not print:\n"
            + "\n".join(f"  page: {line}" for line in shown if line not in printed)
            + "\n"
            + "\n".join(f"  real: {line}" for line in printed if line not in shown)
        )


def test_every_api_page_says_how_to_import_the_thing_it_documents():
    """Zero of seventy-one carried an import line, and the five behind an extra never named it.

    A reference page that gives a class name and no route to it is browsable and not usable —
    the audit's phrase, and the right one. `PostgresStateStore`, `OTelEventSink`,
    `JWTIdentityProvider`, `AcsControlHook` and `serve` all raise `MissingDependency` without
    their extra, and no page said which.
    """

    pages = sorted((DOCS / "docs" / "reference" / "api").glob("*.mdx"))
    assert len(pages) > 50, len(pages)
    for page in pages:
        if page.stem == "index":
            continue
        text = page.read_text(encoding="utf-8")
        dotted = re.search(r"^`(ctrlrun[\w.]*)\.(\w+)` — ", text, re.M)
        assert dotted, f"{page.name} does not name what it documents"
        module, name = dotted.group(1), dotted.group(2)
        assert f"from {module} import {name}" in text, f"{page.name} has no import line"

        # Read the generator's own map rather than a second copy of it. The copy that used
        # to be here listed a `conformance` extra that had been reversed, so the test asserted
        # the same false install line the generator emitted and the drift was invisible from
        # both sides. `test_every_extra_the_reference_names_is_an_extra_that_exists` is what
        # checks that map against the extras `pyproject.toml` actually declares.
        extra = render_api.EXTRA_FOR.get(module)
        if extra is not None:
            assert f'pip install "ctrlrun[{extra}]"' in text, (
                f"{page.name} needs the {extra} extra and does not say so"
            )


def test_every_extra_the_reference_names_is_an_extra_that_exists():
    """A reference page that says `pip install "ctrlrun[conformance]"` sends a reader to a
    command that installs nothing.

    SPEC-v0.5 §5.1 planned the conformance kit as an extra and then reversed it -- the kit
    needs nothing `ctrlrun` does not already install, and `conformance/__init__.py` records
    why. `EXTRA_FOR` was not updated, so two generated pages kept promising an extra that
    `pyproject.toml` does not declare, plus a `MissingDependency` that can never fire. Both
    halves of that sentence were false.
    """
    import tomllib

    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        declared = set(tomllib.load(handle)["project"].get("optional-dependencies", {}))

    named = set(render_api.EXTRA_FOR.values())

    assert named <= declared, (
        f"the API reference names extras that pyproject.toml does not declare: "
        f"{sorted(named - declared)}"
    )
