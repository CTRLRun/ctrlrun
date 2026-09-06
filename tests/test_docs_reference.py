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

if not (TOOLS.exists() and (DOCS / "reference").exists()):  # pragma: no cover
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

    pages = {p.stem for p in (DOCS / "reference" / "api").glob("*.mdx")}
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
        page = DOCS / "reference" / "api" / "ReconcileOutcome.mdx"
        return page.exists() and "No docstring" not in page.read_text(encoding="utf-8")
    return bool(inspect.getdoc(obj))


# --- hand-written pages cover what the loaders accept -------------------------------------


def _keys_in(page: Path) -> set[str]:
    return set(re.findall(r"`([A-Za-z_][\w./:-]*)`", page.read_text(encoding="utf-8")))


def test_the_policy_reference_names_every_key_and_operator_the_loader_accepts():
    from ctrlrun import policy

    named = _keys_in(DOCS / "reference" / "policy-yaml.mdx")
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

    named = _keys_in(DOCS / "reference" / "authority-yaml.mdx")
    expected = (
        set(authority._AUTHORITY_KEYS) | set(authority._GRANT_KEYS) | set(authority._SUBJECT_KEYS)
    )
    assert expected - named == set(), (
        f"authority-yaml.mdx does not name: {sorted(expected - named)}"
    )
    assert str(authority.DEFAULT_MAX_DELEGATION_DEPTH) in (
        DOCS / "reference" / "authority-yaml.mdx"
    ).read_text(encoding="utf-8")


def test_the_exit_codes_page_says_what_verify_says():
    from click.testing import CliRunner

    from ctrlrun.cli.main import main

    help_text = " ".join(CliRunner().invoke(main, ["verify", "--help"]).output.split())
    page = " ".join((DOCS / "reference" / "exit-codes.mdx").read_text(encoding="utf-8").split())
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
