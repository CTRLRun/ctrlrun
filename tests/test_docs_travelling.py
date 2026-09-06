"""The three pages meant to travel: the browser demo, the study, and the badge.

Each makes a claim that is easy to fake and expensive to get wrong, so each is held to what it
rests on: the browser demo to the harness that proved it runs, the study to the published
results and nothing else, and the badge page to the exact phrase the badge means.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"
TOOLS = REPO_ROOT / "tools" / "docs_audit"

if not (DOCS / "try-it.mdx").exists():  # pragma: no cover - not a checkout
    pytest.skip("no repository checkout", allow_module_level=True)

sys.path.insert(0, str(TOOLS))

import render_probe  # noqa: E402

#: `MANIFEST.in` prunes `research/` from the distributions, and two release guards assert it
#: is absent, so from inside an sdist there are no published results to render the study page
#: from. The page is edited in a checkout, which is where these two run; CI's `check` job and
#: every developer run are checkouts.
IN_CHECKOUT = (REPO_ROOT / "research" / "framework-probe" / "results").is_dir()
needs_results = pytest.mark.skipif(
    not IN_CHECKOUT,
    reason="no research/framework-probe/results: the distributions prune it by design",
)

TRY_IT = (DOCS / "try-it.mdx").read_text(encoding="utf-8")
SCRIPT = (DOCS / "try-it.js").read_text(encoding="utf-8")
HARNESS = (DOCS / "assets" / "verify-browser-demo.mjs").read_text(encoding="utf-8")
BADGE = (DOCS / "verify" / "get-the-badge.mdx").read_text(encoding="utf-8")


# --- the browser demo ----------------------------------------------------------------------


def test_the_page_the_script_and_the_harness_name_one_pyodide_build():
    """The page says which build it was verified against; the script loads that build; the
    harness is what verified it. Three places, one version, or the page is describing something
    nobody ran."""
    versions = set(re.findall(r"v?(\d+\.\d+\.\d+)", SCRIPT.split("PYODIDE =")[1].split("\n")[0]))
    assert len(versions) == 1, versions
    version = versions.pop()

    assert version in TRY_IT, f"the page does not name Pyodide {version}"
    assert version in HARNESS, f"the harness does not name Pyodide {version}"


def test_the_script_does_nothing_unless_the_page_mounts_it():
    """Mintlify includes every .js file on every page and cannot scope one, so the script must
    find its container or return. Without this it would run on the whole site."""
    assert "getElementById(CONTAINER)" in SCRIPT
    assert "if (!container" in SCRIPT
    container = re.search(r'CONTAINER = "([^"]+)"', SCRIPT).group(1)
    assert f'id="{container}"' in TRY_IT, (
        "the page does not mount the container the script looks for"
    )


def test_the_script_does_not_load_sqlite3_as_a_package():
    """The one thing that failed the first time: sqlite3 is bundled into Pyodide 314 and is not
    a loadable package, so passing it to loadPackage throws before anything runs."""
    packages = re.search(r"PACKAGES = \[([^\]]*)\]", SCRIPT).group(1)

    assert "sqlite3" not in packages
    assert "micropip" in packages and "pyyaml" in packages
    assert "sqlite3" in HARNESS, "the harness no longer proves sqlite3 is there"


def test_the_script_wires_itself_after_the_page_renders():
    """The site is a single-page app: the script runs once, before React paints the container,
    and not again on a client-side navigation. Wiring only on DOMContentLoaded left the button
    dead on the deployed site, so the script watches the DOM as well.
    `docs/assets/verify-browser-wiring.mjs` proves both halves against a real DOM."""
    assert "MutationObserver" in SCRIPT
    assert "subtree: true" in SCRIPT
    assert (DOCS / "assets" / "verify-browser-wiring.mjs").exists()


def test_the_page_tells_a_reader_what_to_do_when_it_does_not_run():
    """A page that can fail in somebody's browser owes them the command that always works.

    Asserted with the line wrapping removed: a sentence that reads correctly and wraps across
    two lines is still the sentence, and a test that could not see it would push prose onto one
    long line.
    """
    assert "pip install ctrlrun && ctrlrun demo" in TRY_IT
    assert "pip install ctrlrun && ctrlrun demo" in SCRIPT
    assert "That is this page failing, not the library." in " ".join(TRY_IT.split())


def test_the_page_says_it_runs_the_released_version_and_where_the_proof_is():
    assert "verify-browser-demo.mjs" in TRY_IT
    assert "released version" in TRY_IT
    assert "2026-09-06" in TRY_IT and "2026-09-06" in HARNESS


def _browser_demo_program() -> str:
    """The Python the Try-it page runs, lifted out of the JavaScript that carries it."""
    body = re.search(r"var PROGRAM = \[(.*?)\]\.join", SCRIPT, re.S)
    assert body, "docs/try-it.js: no PROGRAM array — the page's Python moved"
    try:
        lines = json.loads(f"[{body.group(1)}]")
    except json.JSONDecodeError as exc:  # pragma: no cover - a malformed array is the failure
        raise AssertionError(
            f"docs/try-it.js: PROGRAM is no longer JSON-parseable ({exc}). Keep it to "
            "double-quoted strings with no comment inside the array and no trailing comma: "
            "verify-browser-demo.mjs parses it the same way."
        ) from exc
    return "\n".join(lines)


def test_the_browser_demo_program_is_valid_python():
    """The page's Python is a string inside a JavaScript file, which nothing else compiles.

    It shipped ending a line on a trailing `+` outside brackets. That is a SyntaxError, so
    every reader who pressed the button got a traceback where the demo should have been, and
    both harnesses stayed green: the wiring one stubs Pyodide, and the demo one carried its own
    copy of the program. This compiles the string the page actually runs, on every commit,
    with no network and no Node.
    """
    program = _browser_demo_program()
    compile(program, "docs/try-it.js PROGRAM", "exec")


def test_the_program_ends_on_an_expression_pyodide_can_return():
    """`runPython` returns the value of the last expression. An assignment there returns
    `None`, and the page would reveal an empty transcript with nothing raised."""
    last = _browser_demo_program().rstrip().splitlines()[-1]
    assert not last.startswith((" ", "\t")), f"the last line is indented, not a value: {last!r}"
    assert re.match(r"^\w+\s*=[^=]", last) is None, f"the last line assigns: {last!r}"
    assert "buffer.getvalue()" in last, f"the last line does not return the demo: {last!r}"


def test_the_transcript_box_scrolls_down_rather_than_growing():
    """A `min-height` with no `max-height` is a box that can only get taller.

    That is what shipped. The transcript grew the page instead of scrolling, `scrollTop`
    stayed 0 so the script's follow-the-newest-line was dead code, and the only scrollbar the
    reader got was the horizontal one the long lines need. Measured in a browser at 820px: the
    box grew to 910px with `scrollHeight == clientHeight`. With both bounds it holds at 452px
    and scrolls to 458.
    """
    style = re.search(r"<pre\s*\n\s*style=\{\{(.*?)\}\}", TRY_IT, re.S)
    assert style, "docs/try-it.mdx: the transcript box is no longer a <pre> with inline style"
    box = dict(re.findall(r"(\w+):\s*\"([^\"]*)\"", style.group(1)))

    assert "maxHeight" in box, "minHeight without maxHeight is a box that can only grow"
    assert box.get("overflowY") == "auto", "the box cannot scroll vertically"
    assert box.get("overflowX") == "auto", "the long lines must scroll inside the box"
    assert box.get("whiteSpace") == "pre", "the demo's columns are aligned; do not wrap them"


def test_the_transcript_does_not_depend_on_how_the_theme_lays_the_box_out():
    """The site's theme sets `display: flex` on this `<pre>`, and we do not own that rule.

    In a flex container every appended child is a flex item in a *row*, so a reveal that
    appended a node per line laid the whole transcript out sideways: measured on the deployed
    page, all five refusals sat at the same y and the box scrolled 6530px wide against a 529px
    frame. `say` never hit it, because setting `textContent` makes one item however the box is
    laid out. So the lines go inside a single block child, which is one flex item, and the
    page also asks for `display: block`. Either alone fixes it; both together mean a theme
    that changes its mind cannot put the transcript in a row again.
    """
    assert 'body.style.display = "block"' in SCRIPT
    assert "output.appendChild(body)" in SCRIPT, "the transcript needs one container child"
    assert "body.appendChild(node)" in SCRIPT
    assert "output.appendChild(node)" not in SCRIPT, "a line appended straight into the <pre>"

    style = re.search(r"<pre\s*\n\s*style=\{\{(.*?)\}\}", TRY_IT, re.S)
    assert style, "docs/try-it.mdx: the transcript box is no longer a <pre> with inline style"
    box = dict(re.findall(r"(\w+):\s*\"([^\"]*)\"", style.group(1)))
    assert box.get("display") == "block", "the theme's flex would lay the lines out in a row"


def test_the_reveal_follows_the_newest_line_only_for_a_reader_at_the_bottom():
    """Following the output is right until somebody scrolls up to re-read, and then it is
    yanking them away from what they are reading."""
    assert "output.scrollHeight - output.scrollTop - output.clientHeight" in SCRIPT
    assert "if (following) output.scrollTop = output.scrollHeight;" in SCRIPT


def test_the_harness_runs_the_program_the_page_runs():
    """A harness with its own copy of the artifact verifies the copy. This one reads
    docs/try-it.js, so the program it proves is the program the reader gets."""
    assert "try-it.js" in HARNESS, "the harness no longer reads the page's script"
    assert "runPython(PROGRAM)" in HARNESS
    assert "run_demo(" not in HARNESS, "the harness has grown its own copy of the program again"


@pytest.mark.authority
def test_the_page_quotes_lines_the_demo_prints():
    """The transcript on the page is the demo's own output, not a sketch of it.

    Generated ids are masked as `apr_…` and `dlg_…` on the page, so each quoted line is
    compared up to where its id begins — the same masking the README's own verbatim test does,
    for the same reason: ids differ per run and everything else must not.
    """
    from click.testing import CliRunner

    from ctrlrun.cli.main import main

    with CliRunner().isolated_filesystem():
        result = CliRunner().invoke(main, ["demo"])
    assert result.exit_code == 0, result.output
    printed = [line.rstrip() for line in result.output.splitlines() if line.strip()]

    quoted = TRY_IT.split("```text", 1)[1].split("```", 1)[0]
    checked = 0
    for line in quoted.splitlines():
        if not line.strip() or line.startswith("ctrlrun "):
            continue
        prefix = re.split(r"(?:apr|dlg)_…", line)[0].rstrip()
        checked += 1
        assert any(actual.startswith(prefix) for actual in printed), line
    assert checked > 15, f"only {checked} transcript lines checked"


# --- the study -----------------------------------------------------------------------------


@needs_results
def test_the_study_page_is_the_render_of_the_published_results():
    assert render_probe.main(["--check"]) == 0


@needs_results
def test_the_study_page_names_no_framework_the_harness_did_not_run():
    """The rule this page exists under: never populated from unrun adapters. CrewAI and AutoGen
    have adapters and were never executed, so they appear only under 'Not run'."""
    page = render_probe.TARGET.read_text(encoding="utf-8")
    table = page.split("| Framework |", 1)[1].split("\n\n", 1)[0]

    found = render_probe.latest()
    assert found is not None, "there is a results file; this test is about what it contains"
    _, document = found
    unrun = {entry["framework"] for entry in document["results"] if render_probe.unrun(entry)}
    assert unrun, "the fixture no longer contains an unrun adapter; this guard is unexercised"

    for framework in unrun:
        assert framework not in table, f"{framework} was never run and is in the table"
        assert f"**{framework}**" in page.split("### Not run", 1)[1], framework


def test_the_study_page_says_no_published_results_when_there_are_none(monkeypatch, tmp_path):
    """The page renders honestly on a repository with no run at all."""
    monkeypatch.setattr(render_probe, "RESULTS", tmp_path)
    text = render_probe.render()

    assert render_probe.NO_RESULTS in text
    assert "| Framework |" not in text


@needs_results
def test_the_study_page_quotes_the_fairness_rules_and_links_the_harness():
    page = render_probe.TARGET.read_text(encoding="utf-8")

    assert "behaviour, not quality" in page
    assert "## The fairness rules" in page
    assert len(render_probe.FAIRNESS) == 6
    for rule in render_probe.FAIRNESS:
        assert rule in page
    assert "research/framework-probe/README.md" in page


# --- the badge -----------------------------------------------------------------------------


def test_the_badge_page_uses_the_exact_phrase_and_explains_n_a():
    assert "declared guarantees pass" in BADGE
    assert "Not applicable is not a pass" in BADGE
    assert "does not mean secure, safe, compliant, certified or audited" in BADGE


def test_the_badge_page_keeps_the_write_permission_where_it_belongs():
    """The action writes the badge and never publishes it. A page that told a reader to give the
    whole workflow `contents: write` would undo the reason for that."""
    assert "permissions:\n          contents: write" in BADGE
    assert "github.event_name == 'push'" in BADGE
    assert "this job only" in BADGE


def test_the_badge_page_has_no_empty_gallery():
    """A gallery of repositories carrying the badge appears when one exists. Until then the
    section is absent, not empty: an empty one is worse than none."""
    for word in ("gallery", "Gallery", "trusted by", "Used by", "Adopters"):
        assert word not in BADGE, f"the badge page has a {word!r} section with nothing in it"


def test_all_three_pages_are_in_the_navigation_and_the_search_plan():
    document = json.loads((DOCS / "docs.json").read_text(encoding="utf-8"))
    plan = (DOCS / "SEO.md").read_text(encoding="utf-8")
    found: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, str):
            found.add(node)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)

    walk(document["navigation"])
    for slug in ("try-it", "verify/get-the-badge", "study/does-your-framework-double-execute"):
        assert slug in found, f"{slug} is not in docs.json"
        assert f"`{slug}`" in plan, f"{slug} has no row in docs/SEO.md"
