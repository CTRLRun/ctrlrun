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

if not (DOCS / "docs" / "try-it.mdx").exists():  # pragma: no cover - not a checkout
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

TRY_IT = (DOCS / "docs" / "try-it.mdx").read_text(encoding="utf-8")
SCRIPT = (DOCS / "try-it.js").read_text(encoding="utf-8")
HARNESS = (DOCS / "assets" / "verify-browser-demo.mjs").read_text(encoding="utf-8")
BADGE = (DOCS / "docs" / "verify" / "get-the-badge.mdx").read_text(encoding="utf-8")


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


VERIFIED = json.loads((DOCS / "assets" / "browser-demo.verified.json").read_text(encoding="utf-8"))


def test_the_page_says_it_runs_the_released_version_and_where_the_proof_is():
    assert "verify-browser-demo.mjs" in TRY_IT
    assert "released version" in TRY_IT


def test_the_page_quotes_the_run_the_harness_recorded():
    """`browser-demo.verified.json` is written by the harness at the end of a run that passed,
    and it is the only source for the numbers the page quotes: the date, the Pyodide, Python
    and SQLite versions in the "Verified" paragraph, and the `ctrlrun X on Python Y` line at
    the top of the transcript. A page that quoted a version nothing had run would be the copy
    problem again, one level up."""
    for key in ("date", "pyodide", "python", "sqlite", "ctrlrun"):
        assert VERIFIED.get(key), f"browser-demo.verified.json has no {key}"
    verified = TRY_IT.split("Verified, and how to check", 1)[1].split("</Accordion>", 1)[0]
    assert f"**{VERIFIED['date']}**" in verified, "the page's verified date is not the run's"
    assert f"Pyodide {VERIFIED['pyodide']}" in verified
    assert f"Python {VERIFIED['python']}" in verified
    assert f"SQLite {VERIFIED['sqlite']}" in verified
    assert f"`ctrlrun` {VERIFIED['ctrlrun']} from PyPI" in verified
    transcript = TRY_IT.split("```text", 1)[1].split("```", 1)[0]
    assert transcript.strip().startswith(
        f"ctrlrun {VERIFIED['ctrlrun']} on Python {VERIFIED['python']}"
    ), transcript.strip().splitlines()[0]
    assert f"Last run {VERIFIED['date']}" in HARNESS, "the harness comment names another run"
    assert "browser-demo.verified.json" in HARNESS


def _python_in_the_script(name: str) -> str:
    """One of the Python programs the Try-it page runs, lifted out of the JavaScript."""
    body = re.search(rf"var {name} = \[(.*?)\]\.join", SCRIPT, re.S)
    assert body, f"docs/try-it.js: no {name} array — the page's Python moved"
    try:
        lines = json.loads(f"[{body.group(1)}]")
    except json.JSONDecodeError as exc:  # pragma: no cover - a malformed array is the failure
        raise AssertionError(
            f"docs/try-it.js: {name} is no longer JSON-parseable ({exc}). Keep it to "
            "double-quoted strings with no comment inside the array and no trailing comma: "
            "verify-browser-demo.mjs parses it the same way."
        ) from exc
    return "\n".join(lines)


def _browser_demo_program() -> str:
    return _python_in_the_script("PROGRAM")


def _playground_module() -> str:
    return _python_in_the_script("PLAYGROUND")


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
    assert style, "docs/docs/try-it.mdx: the transcript box is no longer a <pre> with inline style"
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
    assert style, "docs/docs/try-it.mdx: the transcript box is no longer a <pre> with inline style"
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


# --- the playground ------------------------------------------------------------------------


def _playground_step():
    """`step`, from the module the page runs, executed here against the checkout's ctrlrun.

    The page's JavaScript owns the DOM and nothing else: every outcome a reader sees is the JSON
    this function returns. So the sequence the page tells the reader to try is run here, natively,
    and each outcome asserted — with no Node, no Pyodide and no network. This is the third check
    the page describes, and it exists because the first two once both stayed green while the
    page's own copy of the Python was broken.
    """
    namespace: dict[str, object] = {}
    exec(compile(_playground_module(), "docs/try-it.js PLAYGROUND", "exec"), namespace)
    step = namespace["step"]

    def call(**request):
        return json.loads(step(json.dumps(request)))

    return call


def test_the_playground_module_is_valid_python_and_defines_step():
    namespace: dict[str, object] = {}
    exec(compile(_playground_module(), "docs/try-it.js PLAYGROUND", "exec"), namespace)
    assert callable(namespace.get("step"))


def test_the_playground_runs_the_sequence_the_page_tells_the_reader_to_try():
    """Steps 1 to 6 of "Try this, in order", each outcome as the page states it."""
    step = _playground_step()

    # 1. €500 on txn_1: allowed and committed.
    first = step(op="refund", payment_id="txn_1", amount=50000, lose_reply=False)
    assert first["outcome"] == "executed", first
    assert first["receipt"]["decision"] == "allow"
    assert first["receipt"]["result"] == "committed"
    assert first["remote_calls"] == 1

    # 2. €2,000 on txn_2: a human decides; approve; €5,000 on it is refused; €2,000 executes.
    asked = step(op="refund", payment_id="txn_2", amount=200000, lose_reply=False)
    assert asked["outcome"] == "approval_required", asked
    assert asked["remote_calls"] == 0
    granted = step(op="approve", request_id=asked["request_id"])
    assert granted["approval_id"] == asked["request_id"]
    assert granted["action_hash"].startswith("sha256:")
    mutated = step(op="refund", payment_id="txn_2", amount=500000, approval_id=asked["request_id"])
    assert mutated["outcome"] == "approval_mismatch", mutated
    assert mutated["reason"] == "mismatch"
    assert mutated["remote_calls"] == 0, "the mutated amount reached the remote"
    assert mutated["receipt"]["result"] == "blocked"
    approved = step(op="refund", payment_id="txn_2", amount=200000, approval_id=asked["request_id"])
    assert approved["outcome"] == "executed", approved
    assert approved["receipt"]["decision"] == "approve"
    assert approved["receipt"]["approval_id"] == asked["request_id"]
    assert approved["remote_calls"] == 1

    # 3. The same approval presented again: consumed.
    replayed = step(op="refund", payment_id="txn_2", amount=200000, approval_id=asked["request_id"])
    assert replayed["outcome"] == "approval_mismatch", replayed
    assert replayed["reason"] == "consumed"
    assert replayed["remote_calls"] == 1

    # 4. €20,000 on txn_3: denied, and no request was created.
    denied = step(op="refund", payment_id="txn_3", amount=2000000, lose_reply=False)
    assert denied["outcome"] == "denied", denied
    assert "request_id" not in denied
    assert denied["remote_calls"] == 0
    assert denied["receipt"]["result"] == "denied"

    # 5. €500 on txn_4 with the reply lost: AMBIGUOUS; the retry is refused; one remote call.
    lost = step(op="refund", payment_id="txn_4", amount=50000, lose_reply=True)
    assert lost["outcome"] == "reply_lost", lost
    assert lost["receipt"]["result"] == "ambiguous"
    assert lost["remote_calls"] == 1
    retried = step(op="refund", payment_id="txn_4", amount=50000, lose_reply=False)
    assert retried["outcome"] == "ambiguous_retry", retried
    assert retried["remote_calls"] == 1, "the blind retry reached the remote"
    assert retried["receipt"]["result"] == "blocked"

    # 6. €500 on txn_1 again: the effect already happened.
    duplicate = step(op="refund", payment_id="txn_1", amount=50000, lose_reply=False)
    assert duplicate["outcome"] == "duplicate", duplicate
    assert duplicate["remote_calls"] == 1


def test_the_playground_refuses_a_negative_amount_as_the_policy_says():
    """Both ends of every band are bound; a refund of a negative amount is a charge."""
    step = _playground_step()
    charged = step(op="refund", payment_id="txn_1", amount=-500, lose_reply=False)
    assert charged["outcome"] == "denied", charged
    assert charged["remote_calls"] == 0


def test_the_playground_has_no_way_to_grant_but_the_human_button():
    """No auto-approve, no dry run, no flag: the only path to a grant is `op: approve` with a
    request id, which is `grant_approval` on the store — the write `ctrlrun approve` makes."""
    module = _playground_module()
    assert "grant_approval(" in module
    assert module.count("grant_approval(") == 1
    for forbidden in ("auto_approve", "dry_run", "ScriptedApprovalProvider", "mode"):
        assert forbidden not in module, forbidden
    step = _playground_step()
    with pytest.raises(Exception):  # noqa: B017 - an unknown request id is not a grant
        step(op="approve", request_id="apr_0000")


def test_the_playground_policy_on_the_page_is_the_policy_in_the_module():
    """The page shows a YAML block and the module carries one; a reader reasons from the block
    they can see, so the two are held equal rather than described as equal."""
    module = _playground_module()
    in_module = re.search(r'POLICY = """\n(.*?)"""', module, re.S)
    assert in_module, "the module no longer carries POLICY as a triple-quoted string"
    shown = TRY_IT.split("```yaml", 1)[1].split("```", 1)[0]

    assert shown.strip() == in_module.group(1).strip()


def test_the_page_names_every_outcome_the_module_can_return():
    """Each `outcome` the module reports has the line the page tells the reader to expect."""
    module = _playground_module()
    outcomes = set(re.findall(r'result\["outcome"\] = "(\w+)"', module))
    assert outcomes == {
        "executed",
        "approval_required",
        "approval_mismatch",
        "denied",
        "duplicate",
        "ambiguous_retry",
        "reply_lost",
    }
    for exception in (
        "ApprovalRequired",
        "ApprovalMismatch",
        "ActionDenied",
        "DuplicateEffect",
        "AmbiguousEffect",
        "AMBIGUOUS",
        "consumed",
    ):
        assert exception in TRY_IT, exception
    assert 'case "' in SCRIPT
    for outcome in outcomes:
        assert f'case "{outcome}":' in SCRIPT, f"the page has no branch for {outcome}"


def test_the_playground_wires_every_control_the_page_mounts():
    playground = re.search(r'PLAYGROUND_ID = "([^"]+)"', SCRIPT).group(1)
    assert f'id="{playground}"' in TRY_IT
    for name in ("amount", "payment_id", "lose_reply", "run", "approve"):
        assert f'name="{name}"' in TRY_IT, name
        assert f'[name="{name}"]' in SCRIPT, f"the script never selects {name}"
    assert "runPython(PLAYGROUND)" in SCRIPT
    assert "runPython(PLAYGROUND)" in HARNESS, "the harness does not run the playground"
    assert "step(" in HARNESS


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
    for slug in (
        "docs/try-it",
        "docs/verify/get-the-badge",
        "docs/study/does-your-framework-double-execute",
    ):
        assert slug in found, f"{slug} is not in docs.json"
        assert f"`{slug}`" in plan, f"{slug} has no row in docs/SEO.md"
