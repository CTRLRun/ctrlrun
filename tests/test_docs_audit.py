"""The documentation audit under `tools/docs_audit/`, and the capabilities generator.

Three checks and one generator, each with a control: a check whose only evidence is a green
run is a check nothing exercises, so every guard here is shown catching the thing it exists
to catch before it is trusted to pass.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS = REPO_ROOT / "tools" / "docs_audit"

if not TOOLS.exists():  # pragma: no cover - not a checkout
    pytest.skip("no repository checkout", allow_module_level=True)

sys.path.insert(0, str(TOOLS))

import links  # noqa: E402
import lint  # noqa: E402
import render_capabilities as capabilities  # noqa: E402
import snippets  # noqa: E402
from _files import fences, outside_fences  # noqa: E402

# --- the fence parser ---------------------------------------------------------------------


def test_fences_carry_their_language_tokens_and_line_numbers(tmp_path):
    text = (
        "intro\n\n```python runnable continue\nx = 1\n```\n\n"
        "````yaml runnable file=a.yaml\na: 1\n````\n"
    )
    found = list(fences(text, tmp_path / "page.md"))

    assert [(f.line, f.language, f.tokens) for f in found] == [
        (3, "python", ("runnable", "continue")),
        (7, "yaml", ("runnable", "file=a.yaml")),
    ]
    assert found[0].body == "x = 1\n"


def test_a_shorter_fence_inside_a_longer_one_does_not_close_it(tmp_path):
    text = "````markdown\n```python\nx\n```\n````\n"
    found = list(fences(text, tmp_path / "page.md"))

    assert len(found) == 1
    assert found[0].body == "```python\nx\n```\n"


def test_outside_fences_skips_code():
    text = "# Heading\n```\n# not a heading\n```\ntail\n"

    assert list(outside_fences(text)) == [(1, "# Heading"), (5, "tail")]


# --- the runnable-snippet harness ---------------------------------------------------------


def _page(tmp_path: Path, body: str) -> Path:
    page = tmp_path / "page.md"
    page.write_text(body, encoding="utf-8")
    return page


def test_a_passing_python_block_passes(tmp_path):
    outcome = snippets.run_document(_page(tmp_path, "```python runnable\nprint('ok')\n```\n"))

    assert outcome.ran == 1
    assert outcome.ok


def test_a_failing_python_block_is_reported_with_its_line(tmp_path):
    outcome = snippets.run_document(
        _page(tmp_path, "text\n\n```python runnable\nraise SystemExit('boom')\n```\n")
    )

    assert not outcome.ok
    assert outcome.failures[0].location.endswith("page.md:3")
    assert "boom" in outcome.failures[0].message


def test_an_unmarked_block_is_not_run(tmp_path):
    outcome = snippets.run_document(_page(tmp_path, "```python\nraise SystemExit(1)\n```\n"))

    assert outcome.ran == 0


def test_a_block_that_reaches_for_the_network_fails(tmp_path):
    """The socket guard is the whole reason the harness runs offline, so it is shown biting."""
    outcome = snippets.run_document(
        _page(
            tmp_path,
            "```python runnable\nimport socket\nsocket.getaddrinfo('example.invalid', 80)\n```\n",
        )
    )

    assert not outcome.ok
    assert "offline" in outcome.failures[0].message


def test_the_network_guard_is_what_made_it_fail(tmp_path):
    """The control for the test above: the same block passes with the guard removed."""
    guard = tmp_path / "no-guard"
    guard.mkdir()
    outcome = snippets.run_document(
        _page(
            tmp_path,
            "```python runnable\nimport socket\nassert callable(socket.getaddrinfo)\n"
            "assert socket.getaddrinfo.__module__ != 'sitecustomize'\n```\n",
        ),
        guard=guard,
    )

    assert outcome.ok, outcome.failures


def test_continue_appends_to_the_previous_python_blocks(tmp_path):
    outcome = snippets.run_document(
        _page(
            tmp_path,
            "```python runnable\ndef f():\n    return 1\n```\n"
            "```python runnable continue\nassert f() == 1\n```\n",
        )
    )

    assert outcome.ok, outcome.failures


def test_a_bash_block_runs_with_the_checkouts_cli_on_path(tmp_path):
    outcome = snippets.run_document(
        _page(tmp_path, "```bash runnable\nctrlrun --version\ntest ! -e /nonexistent\n```\n")
    )

    assert outcome.ok, outcome.failures


def test_a_failing_bash_block_fails(tmp_path):
    outcome = snippets.run_document(_page(tmp_path, "```bash runnable\nfalse\n```\n"))

    assert not outcome.ok


def test_a_yaml_block_is_loaded_by_the_real_policy_loader(tmp_path):
    outcome = snippets.run_document(
        _page(
            tmp_path,
            "```yaml runnable\nschema: ctrlrun.policy/v1\nactions:\n  a.b:\n"
            "    decision: nope\n```\n",
        )
    )

    assert not outcome.ok
    assert "PolicyError" in outcome.failures[0].message


def test_a_yaml_policy_is_written_for_later_blocks_to_read(tmp_path):
    outcome = snippets.run_document(
        _page(
            tmp_path,
            "```yaml runnable\nschema: ctrlrun.policy/v1\nactions:\n  a.b:\n"
            "    decision: allow\n```\n"
            "```python runnable\nfrom ctrlrun import Policy\n"
            "assert 'a.b' in Policy.from_file('ctrlrun.yaml').actions\n```\n",
        )
    )

    assert outcome.ok, outcome.failures


def test_a_yaml_authority_document_is_loaded_by_the_real_authority_loader(tmp_path):
    outcome = snippets.run_document(
        _page(
            tmp_path,
            "```yaml runnable\nschema: ctrlrun.policy/v3\nauthority:\n  grants:\n"
            "    - id: x\n      subject: { agent: a }\n      actions: ['a.b']\n"
            "      delegable: true\n```\n",
        )
    )

    # `delegable: true` without `expires_at` is refused by the authority loader (SPEC-v0.3).
    assert not outcome.ok
    assert "expires_at" in outcome.failures[0].message


def test_the_readme_and_docs_snippets_run(tmp_path):
    """The harness against the real documents. Zero runnable blocks today; that is the
    baseline the writing sessions clear by marking blocks as they make them run."""
    outcome = snippets.run_documents(snippets.documents())

    assert outcome.ok, [str(failure) for failure in outcome.failures]


# --- the forbidden-words lint -------------------------------------------------------------


def _empty() -> lint.Allowlist:
    return lint.Allowlist((), ())


def test_a_positioning_word_in_a_heading_is_flagged():
    found = lint.lint_text("# Runtime governance for agents\n", "x.md", _empty())

    assert {f.rule.id for f in found} == {"governance"}


def test_a_positioning_word_in_a_body_sentence_is_not():
    found = lint.lint_text("It is not a governance toolkit.\n", "x.md", _empty())

    assert found == []


def test_a_positioning_word_in_frontmatter_title_or_description_is_flagged():
    text = '---\ntitle: "Guardrails"\ndescription: A secure layer\n---\n\nbody secure\n'
    found = lint.lint_text(text, "x.mdx", _empty())

    assert [(f.line, f.rule.id) for f in found] == [(2, "guardrails"), (3, "secure")]


def test_a_claim_word_anywhere_is_flagged():
    text = "The finance sector pack is HIPAA ready.\n"
    found = lint.lint_text(text, "x.md", _empty())

    assert {f.rule.id for f in found} == {"sector", "pack", "hipaa"}


def test_a_word_inside_a_code_fence_is_not_linted():
    text = "```yaml\n# sector: finance\n```\n"

    assert lint.lint_text(text, "x.md", _empty()) == []


def test_package_is_not_pack():
    assert lint.lint_text("pip installs the package.\n", "x.md", _empty()) == []


def test_the_allowlist_permits_a_matching_line_in_a_matching_file():
    allowlist = lint.Allowlist((), (("docs/*.md", lint.re.compile("not a compliance claim")),))

    assert lint.lint_text("This is not a compliance claim.\n", "docs/x.md", allowlist) == []
    assert lint.lint_text("This is not a compliance claim.\n", "README.md", allowlist) != []
    assert lint.lint_text("A compliance product.\n", "docs/x.md", allowlist) != []


def test_the_allowlist_file_parses_and_names_only_known_keywords(tmp_path):
    loaded = lint.load_allowlist()
    assert loaded.allowed or loaded.excluded

    bad = tmp_path / "allow.txt"
    bad.write_text("permit * foo\n", encoding="utf-8")
    with pytest.raises(ValueError):
        lint.load_allowlist(bad)


def test_the_two_documents_the_rule_exempts_are_skipped(tmp_path):
    """`docs/OWASP-AGENTIC-TOP10.md` and `docs/THREAT_MODEL.md` list what is *not* covered, so
    they are allowed to name what they do not cover."""
    for name in lint.EXEMPT_BY_RULE:
        path = REPO_ROOT / name
        assert path.exists(), name
    assert lint.lint_paths([REPO_ROOT / name for name in lint.EXEMPT_BY_RULE]) == []


def test_the_lint_runs_against_the_real_documents():
    """The baseline: the findings that remain after the allowlist. Recorded in the PR that
    added this file; the writing sessions clear them and this assertion then tightens to
    `== []`."""
    findings = lint.lint_paths(lint.documents())

    assert isinstance(findings, list)


# --- the link check -----------------------------------------------------------------------


def test_slugs_follow_githubs_rule():
    assert links.slug("What the badge means") == "what-the-badge-means"
    assert links.slug("`ctrlrun verify`") == "ctrlrun-verify"
    assert links.slug("Not applicable is *not* a pass") == "not-applicable-is-not-a-pass"
    assert links.slug("Two ways in (and a third)") == "two-ways-in-and-a-third"


def test_a_relative_link_to_a_missing_file_is_broken(tmp_path):
    page = tmp_path / "a.md"
    page.write_text("[x](b.md)\n", encoding="utf-8")

    broken = links.check_text(page.read_text(), page)

    assert len(broken) == 1 and "does not exist" in broken[0].reason


def test_a_relative_link_and_anchor_that_exist_pass(tmp_path):
    (tmp_path / "b.md").write_text("## The `effect` key\n", encoding="utf-8")
    page = tmp_path / "a.md"
    page.write_text("[x](b.md#the-effect-key) [y](#own)\n\n## Own\n", encoding="utf-8")

    assert links.check_text(page.read_text(), page) == []


def test_a_missing_anchor_is_broken(tmp_path):
    (tmp_path / "b.md").write_text("## Real\n", encoding="utf-8")
    page = tmp_path / "a.md"
    page.write_text("[x](b.md#imaginary)\n", encoding="utf-8")

    broken = links.check_text(page.read_text(), page)

    assert len(broken) == 1 and "#imaginary" in broken[0].reason


def test_a_duplicate_heading_gets_a_numbered_anchor(tmp_path):
    page = tmp_path / "a.md"
    page.write_text("[x](#next-1)\n## Next\n## Next\n", encoding="utf-8")

    assert links.check_text(page.read_text(), page) == []


def test_a_github_blob_url_into_this_repository_is_an_internal_link(tmp_path):
    page = tmp_path / "a.md"
    page.write_text(
        "[ok](https://github.com/CTRLRun/ctrlrun/blob/main/docs/verify.md#what-the-badge-means)\n"
        "[bad](https://github.com/CTRLRun/ctrlrun/blob/main/docs/nope.md)\n"
        "[external](https://example.com/anything)\n",
        encoding="utf-8",
    )

    broken = links.check_text(page.read_text(), page)

    assert [b.target for b in broken] == [
        "https://github.com/CTRLRun/ctrlrun/blob/main/docs/nope.md"
    ]


def test_a_root_relative_docs_path_resolves_under_docs(tmp_path, monkeypatch):
    monkeypatch.setattr(links, "REPO_ROOT", tmp_path)
    (tmp_path / "docs" / "concepts").mkdir(parents=True)
    (tmp_path / "docs" / "concepts" / "effect-keys.mdx").write_text("# Effect keys\n")
    page = tmp_path / "docs" / "index.mdx"
    page.write_text('<Card href="/concepts/effect-keys" /> [x](/concepts/missing)\n')

    broken = links.check_text(page.read_text(), page)

    assert [b.target for b in broken] == ["/concepts/missing"]


def test_the_real_documents_have_no_broken_internal_links():
    assert links.check_paths(links.documents()) == []


# --- the capabilities generator -----------------------------------------------------------


def test_capabilities_yaml_loads_with_exactly_six_guarantees():
    loaded = capabilities.load()

    assert len([c for c in loaded if c.guarantee]) == 6
    assert len({c.id for c in loaded}) == len(loaded)


def test_every_description_is_at_most_fifteen_words():
    for entry in capabilities.load():
        assert len(entry.description.split()) <= 15, entry.id


def test_every_page_a_capability_names_is_in_the_information_architecture():
    ia = (REPO_ROOT / "docs" / "IA.md").read_text(encoding="utf-8")
    for entry in capabilities.load():
        assert f"`{entry.page}`" in ia or f" {entry.page}\n" in ia, entry.page


def test_every_claim_a_capability_names_is_a_row_in_claims_md():
    claims = (REPO_ROOT / "docs" / "CLAIMS.md").read_text(encoding="utf-8")
    for entry in capabilities.load():
        if entry.claim is None:
            assert entry.claim_note, entry.id
            continue
        assert f'"{entry.claim}"' in claims, f"{entry.id}: {entry.claim!r} is not a CLAIMS.md row"


def test_the_generated_copies_match_the_generator():
    """The drift test. Every rendered copy — the three files under `docs/generated/` and every
    marker block in the README or a docs page — is the generator's output for that format."""
    drift = capabilities.check(capabilities.load())

    assert drift == [], [str(d) for d in drift]


def test_a_hand_edit_to_a_generated_file_is_drift(tmp_path):
    loaded = capabilities.load()
    capabilities.write(loaded, tmp_path)
    target = tmp_path / capabilities.FILENAMES["readme"]
    target.write_text(target.read_text().replace("| yes |", "| YES |", 1), encoding="utf-8")

    drift = capabilities.check(loaded, directory=tmp_path, pages=[])

    assert [d.path for d in drift] == [capabilities.relative(target)]


def test_a_marker_block_in_a_page_is_compared_with_the_render(tmp_path):
    loaded = capabilities.load()
    capabilities.write(loaded, tmp_path)
    fresh = tmp_path / "fresh.md"
    fresh.write_text("intro\n\n" + capabilities.render("readme", loaded) + "\nafter\n")
    stale = tmp_path / "stale.md"
    stale.write_text(
        "intro\n\n" + capabilities.render("readme", loaded).replace("Fail closed", "Fails open")
    )
    unclosed = tmp_path / "unclosed.md"
    unclosed.write_text(capabilities.render("mdx", loaded).replace("{/* end generated */}", ""))

    drift = capabilities.check(loaded, directory=tmp_path, pages=[fresh, stale, unclosed])

    assert sorted(d.path.rsplit("/", 1)[-1] for d in drift) == ["stale.md", "unclosed.md"]


def test_every_render_carries_the_generated_comment_and_the_close_marker():
    loaded = capabilities.load()
    for fmt in capabilities.FORMATS:
        text = capabilities.render(fmt, loaded)
        assert f"generated from docs/capabilities.yaml ({fmt})" in text.splitlines()[0]
        assert "end generated" in text.splitlines()[-1]


def test_the_readme_render_has_the_six_guarantees_and_the_three_ways_in():
    text = capabilities.render("readme", capabilities.load())
    rows = [line for line in text.splitlines() if line.startswith("| **")]

    assert len(rows) == 6
    assert "| Guarantee | `@protect` | Gateway | Adapter |" in text


def test_the_generator_refuses_a_malformed_entry(tmp_path):
    bad = tmp_path / "capabilities.yaml"
    bad.write_text(
        "capabilities:\n  - id: X\n    name: n\n    description: d\n    guarantee: true\n"
        "    ways_in: {decorator: true, gateway: true, adapter: true}\n    since: v0.1\n"
        "    page: p\n    claim: c\n",
        encoding="utf-8",
    )
    with pytest.raises(capabilities.CapabilitiesError, match="kebab-case"):
        capabilities.load(bad)


def test_the_generator_refuses_a_long_description(tmp_path):
    bad = tmp_path / "capabilities.yaml"
    bad.write_text(
        "capabilities:\n  - id: x\n    name: n\n    description: "
        + " ".join(["word"] * 16)
        + "\n    guarantee: true\n"
        "    ways_in: {decorator: true, gateway: true, adapter: true}\n    since: v0.1\n"
        "    page: p\n    claim: c\n",
        encoding="utf-8",
    )
    with pytest.raises(capabilities.CapabilitiesError, match="16 words"):
        capabilities.load(bad)


def test_the_generator_refuses_a_count_of_guarantees_other_than_six(tmp_path):
    """The README matrix has six rows and the generator is where that is enforced, so a file
    with five is refused rather than rendered five rows long."""
    entry = (
        "  - id: g{n}\n    name: n\n    description: d\n    guarantee: true\n"
        "    ways_in: {{decorator: true, gateway: true, adapter: true}}\n    since: v0.1\n"
        "    page: p\n    claim: c\n"
    )
    bad = tmp_path / "capabilities.yaml"
    bad.write_text("capabilities:\n" + "".join(entry.format(n=n) for n in range(5)))
    with pytest.raises(capabilities.CapabilitiesError, match="exactly six"):
        capabilities.load(bad)


def test_the_generator_refuses_a_null_claim_without_a_note(tmp_path):
    bad = tmp_path / "capabilities.yaml"
    bad.write_text(
        "capabilities:\n  - id: x\n    name: n\n    description: d\n    guarantee: true\n"
        "    ways_in: {decorator: true, gateway: true, adapter: true}\n    since: v0.1\n"
        "    page: p\n    claim: null\n",
        encoding="utf-8",
    )
    with pytest.raises(capabilities.CapabilitiesError, match="claim_note"):
        capabilities.load(bad)


# --- CI runs all four ---------------------------------------------------------------------


def test_ci_runs_the_three_checks_and_the_drift_check():
    """`docs/STYLE.md` says the `docs` job runs them. A guard that CI does not run is prose."""
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    for script in ("snippets.py", "lint.py", "links.py", "render_capabilities.py --check"):
        assert f"python tools/docs_audit/{script}" in workflow, script


def test_the_scripts_run_as_scripts():
    """Each tool is documented as `python tools/docs_audit/<x>.py`; imported-from-tests is not
    the same thing, so each is started the way a person starts it."""
    for script, arguments in (
        ("render_capabilities.py", ["--check"]),
        ("links.py", ["README.md"]),
        ("snippets.py", ["--list", "README.md"]),
        ("lint.py", ["docs/STYLE.md"]),
    ):
        completed = subprocess.run(
            [sys.executable, str(TOOLS / script), *arguments],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, f"{script}: {completed.stdout}{completed.stderr}"
