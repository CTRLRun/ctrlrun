"""The Production section, and the first-glance signals that point at it.

The section answers one question — *can I run this for real, and what happens when the parts
that fail, fail?* — so the rules a machine can check here are about **honesty** rather than
about shape. `test_docs_site.py` already holds every page in `docs/` to frontmatter, a word
budget, a `## Next` block and the navigation. What is asserted below is what this section
would be worth nothing without:

- every page says what it does **not** do, and cites the acceptance tests it rests on **by an
  id that exists in `docs/SPEC-v0.6.md` §8**, so a page cannot cite a test nobody wrote;
- the readiness block is the generator's, byte for byte, in all three places it appears, and
  its **Not yet** list is inside it rather than below it, where a reader would stop first;
- the soak page states the measured duration and that `ROADMAP.md`'s exit criterion is **not**
  met by it, because "soaked" is the sentence a stranger will quote;
- the section does not reach for the vocabulary the third rule of `SPEC-v0.6.md` §1.2 refuses.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"
PRODUCTION = DOCS / "production"
TOOLS = REPO_ROOT / "tools" / "docs_audit"

if not (DOCS / "docs.json").exists():  # pragma: no cover - not a checkout
    pytest.skip("no repository checkout", allow_module_level=True)

PAGES = sorted(PRODUCTION.glob("*.mdx"))

#: Paths this repository has and a **distribution deliberately does not**. `MANIFEST.in` prunes
#: `.github`, and `T181` asserts that no `research/` path is in the wheel or the sdist — so their
#: absence inside an sdist is the packaging rule working, not a deletion.
REPOSITORY_ONLY = (
    REPO_ROOT / ".github" / "workflows" / "ci.yml",
    REPO_ROOT / "research" / "soak" / "results",
)


def _repository_only(path: Path) -> Path:
    """A path that exists in a checkout and not in a distribution, or a skip saying why.

    **The skip is guarded so it cannot hide a deletion.** If one of these paths is missing and
    another is present, we are in a checkout with a file removed, and that is a failure — which
    is the whole risk of skipping on a missing file. Only when *every* repository-only path is
    absent together is this the sdist job, running the suite from inside a distribution that
    carries `docs/` and `tests/` and neither of these.
    """
    if path.exists():
        return path
    present = [candidate for candidate in REPOSITORY_ONLY if candidate.exists()]
    names = [str(item.relative_to(REPO_ROOT)) for item in present]
    assert not present, (
        f"{path.relative_to(REPO_ROOT)} is missing from a tree that still has {names}; "
        "that is a deletion, not an sdist"
    )
    pytest.skip(f"{path.relative_to(REPO_ROOT)} is not in a distribution, by design")


_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.S)
_FENCE = re.compile(r"^```.*?^```", re.M | re.S)


def _body(page: Path) -> str:
    return _FRONTMATTER.sub("", page.read_text(encoding="utf-8"), count=1)


def _spec_test_ids() -> set[str]:
    """Every acceptance-test id `SPEC-v0.6.md` and its predecessors define."""
    found: set[str] = set()
    for spec in sorted(DOCS.glob("SPEC-v0.*.md")):
        text = spec.read_text(encoding="utf-8")
        found.update(re.findall(r"^#### (T\d+[a-z]*) ", text, re.M))
    return found


def _written_test_ids() -> set[str]:
    """Every acceptance-test id a test function in `tests/` is named for."""
    found: set[str] = set()
    for module in sorted((REPO_ROOT / "tests").glob("*.py")):
        text = module.read_text(encoding="utf-8")
        found.update(re.findall(r"^def test_(T\d+[a-z]*)_", text, re.M))
    return found


SPEC_TEST_IDS = _spec_test_ids()
WRITTEN_TEST_IDS = _written_test_ids()


def test_the_specs_and_the_suite_actually_define_test_ids():
    """The control: both scanners find something, or every citation check below is vacuous."""
    assert len(SPEC_TEST_IDS) > 100, len(SPEC_TEST_IDS)
    assert len(WRITTEN_TEST_IDS) > 100, len(WRITTEN_TEST_IDS)
    assert {"T155", "T155c", "T156", "T164", "T167"} <= SPEC_TEST_IDS & WRITTEN_TEST_IDS

    # And the two sets are **not** the same set, which is why checking against one of them was
    # not enough: a specification may name a test that was written under another id or folded
    # into a neighbour's assertions.
    assert SPEC_TEST_IDS - WRITTEN_TEST_IDS, "every specified id has a test; this check is moot"


def test_the_section_exists_and_has_a_page_for_each_thing_that_breaks():
    expected = {
        "index",
        "postgres",
        "how-reservation-works",
        "migrations",
        "recovery",
        "receipt-integrity",
        "soak",
        "operations",
    }
    assert {page.stem for page in PAGES} == expected


@pytest.mark.parametrize("page", PAGES, ids=[p.stem for p in PAGES])
def test_every_production_page_says_what_it_does_not_do(page: Path):
    assert "## What this does not do" in _body(page), page.name


@pytest.mark.parametrize("page", PAGES, ids=[p.stem for p in PAGES])
def test_every_production_page_cites_acceptance_tests_that_exist(page: Path):
    """A page rests on named tests, and a citation resolves to a test the spec defines.

    Written because a "Verified by" line is the easiest sentence in this section to write and
    the easiest to get wrong: an id that names nothing reads exactly like one that names the
    test that would have caught the thing the paragraph promises.
    """
    body = _body(page)
    line = [row for row in body.splitlines() if row.startswith("**Verified by")]
    assert line, f"{page.name} has no 'Verified by' line"
    cited = set(re.findall(r"\bT\d+[a-z]*\b", body))

    # **The id must name a test that exists**, and checking it against the specifications is
    # not that check. An independent review found three pages citing `T166`, `T169` and `T178`
    # — every one of them a `####` heading in a specification, and every one implemented under
    # another id or folded into a neighbour's assertions. An id that names nothing reads
    # exactly like one that names the test that would have caught the thing the paragraph
    # promises, which is the only reason the line is there.
    #
    # The reverse direction is deliberately not asserted: `T155e` is a test the implementation
    # added and the specification never headed, and a page citing it is citing the thing that
    # runs. What is written is what a reader can go and read.
    unwritten = cited - WRITTEN_TEST_IDS
    assert not unwritten, f"{page.name} cites tests nobody wrote: {sorted(unwritten)}"


#: The vocabulary `SPEC-v0.6.md` §1.2's third rule refuses, on word boundaries so
#: `design`, `assign` and `security` are not hits. `exactly[ -]once` matches both spellings,
#: which `lint.py` already does and this scan did not.
FORBIDDEN: tuple[str, ...] = (
    "signed",
    "signing",
    "signature",
    "signs?",
    "authorship",
    "tamper-proof",
    "non-repudiation",
    "secure",
    "compliant",
    "certified",
    "exactly[ -]once",
)

#: Sentences on a Production page that use one of those words to say what CTRLRun is **not**.
#: Lower-cased and whitespace-collapsed the way the scan sees them. Adding one is a deliberate
#: act with this list in the diff.
ALLOWED: frozenset[str] = frozenset(
    {
        # `receipt-integrity.mdx`. The sentence the rule exists to produce, and the reason
        # this is an allow-list rather than a word list: a plain scan would flag it, somebody
        # would remove the scan as a false positive, and the vocabulary would drift back in
        # unwatched. `T180` allow-lists the same sentence, one line at a time, in
        # `tests/test_release_v0_6.py`.
        "## what this does not do - **it does not tell you who wrote a receipt.** alteration is not authorship, it does not survive an administrator who can rewrite every row including the head, and it vouches for nothing that was never recorded.",  # noqa: E501
    }
)


def _sentences(text: str) -> list[str]:
    return [" ".join(part.split()) for part in re.split(r"(?<=[.!?])\s+", text)]


def test_the_forbidden_scan_would_fire_on_the_claims_it_exists_to_catch():
    """The positive control. Every word above is absent from every page today, so without this
    the whole scan is a check nothing exercises — `v0.4 §1.3`, and mutation pattern 3."""
    claims = (
        "CTRLRun gives you guaranteed exactly-once execution.",
        "Receipts are signed, which proves authorship.",
        "The chain is tamper-proof and gives you non-repudiation.",
        "This makes your agent secure and compliant.",
        "One effect runs exactly once against the remote.",
    )
    for claim in claims:
        hits = [w for w in FORBIDDEN if re.search(rf"\b{w}\b", claim.lower())]
        assert hits, f"the scan would not fire on {claim!r}"

    # And not on the words that merely contain them.
    innocent = "The design is assigned to a designated reviewer with security in mind."
    assert not [w for w in FORBIDDEN if re.search(rf"\b{w}\b", innocent.lower())], innocent


@pytest.mark.parametrize("page", PAGES, ids=[p.stem for p in PAGES])
def test_the_section_does_not_reach_for_the_words_it_refuses(page: Path):
    """`SPEC-v0.6.md` §1.2's third rule, applied to the pages most tempted to break it.

    An **allow-list of exact sentences**, on `T180`'s design and for its reason: a plain
    forbidden-word list would flag *"Alteration is not authorship"* — the sentence this rule
    exists to produce — which would then be removed as a false positive, taking the check with
    it. An allow-list fails on a **new** occurrence, and whoever adds one comes here and says
    they meant it.

    Three things an independent review found wrong with the first version, all fixed here.
    It omitted **`authorship`**, **`sign`** and **`signs`**, which are the words the rule is
    actually about — `receipt-integrity.mdx` could have gained *"the chain proves authorship"*
    and stayed green. Its `exactly-once` check was narrower than `lint.py`'s own
    `\bexactly[ -]once\b`, so the un-hyphenated form on `recovery.mdx` was invisible to it.
    And every one of its words was absent from every page, so nothing exercised any of them —
    which the positive control below now settles.
    """
    for sentence in _sentences(_FENCE.sub("", _body(page)).lower()):
        hit = [word for word in FORBIDDEN if re.search(rf"\b{word}\b", sentence)]
        if hit:
            assert sentence in ALLOWED, f"{page.name} says {hit[0]!r}: {sentence[:130]!r}"


def test_the_first_line_of_the_section_says_which_store_and_why():
    """SQLite is the default and production-grade on one host; Postgres is for many hosts.

    The order matters as much as the content. A section that opened on Postgres would tell a
    reader with one host that they are not really in production, which is false and is the
    reason most of them would reach for a database they do not need.
    """
    first = _FENCE.sub("", _body(PRODUCTION / "index.mdx")).strip().split("\n\n", 1)[0]
    first = " ".join(first.split())
    assert "SQLite" in first and "Postgres" in first, first[:120]
    assert first.index("SQLite") < first.index("Postgres"), "Postgres comes first: " + first[:120]
    assert "one host" in first or "a single host" in first, first[:160]


def test_the_two_rows_of_the_lost_commit_are_not_merged():
    """Before `COMMIT` and during `COMMIT` are two behaviours, two rows, and two instructions.

    They are the pair a reader most wants collapsed into one sentence, and collapsing them is
    the double execution `T155c` exists to catch: nothing committed and retry the write is not
    the same instruction as unknown, so re-read.

    **This test was rewritten because it did not detect the merge it is named for.** It read
    `"Before" in body and "During" in body`, and "Before" is satisfied by the page's own first
    sentence — *"Before your executor runs"*. An independent review replaced the table with a
    single row reading *"before, during or after `COMMIT` — any exception at all — retry the
    write"* and all 47 tests passed. What is asserted now is the **table**: two distinct rows,
    each naming one side of the boundary, and each carrying its own instruction.
    """
    body = _body(PRODUCTION / "how-reservation-works.mdx")
    rows = [row for row in body.splitlines() if row.startswith("| ") and row.count("|") >= 4]
    before = [row for row in rows if "**Before `COMMIT`**" in row]
    during = [row for row in rows if "**During or after `COMMIT`**" in row]

    assert len(before) == 1, f"the table has {len(before)} rows for the before-COMMIT case"
    assert len(during) == 1, f"the table has {len(during)} rows for the during-COMMIT case"
    assert before != during, "one row is carrying both cases"

    # And the two rows say different things. The before row is a retryable failed write; the
    # during row is unknown and is re-read. A merged row cannot satisfy both of these.
    assert "retry the write" in before[0], before[0]
    assert "re-read" not in before[0], "the before-COMMIT row tells the reader to re-read"
    assert "re-read the record on a fresh connection" in during[0], during[0]
    assert "retry" not in during[0], "the during-COMMIT row tells the reader to retry"

    cited = set(re.findall(r"\bT\d+[a-z]*\b", body))
    assert {"T155", "T155c", "T156"} <= cited, sorted(cited)


def test_the_soak_page_is_the_render_of_the_published_results():
    """No hand-written number, and the duration is the measured one."""
    _repository_only(REPO_ROOT / "research" / "soak" / "results")
    drift = subprocess.run(
        [sys.executable, str(TOOLS / "render_soak.py"), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert drift.returncode == 0, drift.stdout + drift.stderr


def test_the_soak_page_does_not_let_a_reader_believe_the_criterion_was_met():
    """The published run is twenty minutes and the criterion is a week. Both are on the page.

    `research/soak/README.md` already states this; a docs page that omitted it would be the
    only place a stranger reads, saying the flattering half.
    """
    results_dir = _repository_only(REPO_ROOT / "research" / "soak" / "results")
    body = _body(PRODUCTION / "soak.mdx")
    results = sorted(results_dir.glob("*.json"))
    assert results, "no soak results to render"
    measured = json.loads(results[-1].read_text(encoding="utf-8"))
    assert measured["elapsed_human"] in body, measured["elapsed_human"]
    assert "week" in body, "the page does not say what the criterion asks for"
    assert re.search(r"\bnot met\b", body), "the page does not say the criterion is unmet"
    assert "exit_criterion_met" in body, "the page does not explain the JSON field"


def test_the_soak_page_agrees_with_the_run_about_whether_the_criterion_is_met():
    """The page says met or unmet, and which one is **computed** from the published run.

    The old version of this asserted `"for a week" not in body or "not met" in body`, and
    `"not met"` is separately required by the test above, so the disjunction could never be
    false. Worse, it pinned the unflattering answer: on the day a week is finally run the page
    would keep saying the criterion was unmet and nothing would notice. The generator now
    derives it, and this asserts the derivation rather than the string.
    """
    import sys

    _repository_only(REPO_ROOT / "research" / "soak" / "results")
    sys.path.insert(0, str(TOOLS))
    try:
        import render_soak
    finally:
        sys.path.pop(0)

    found = render_soak.latest()
    assert found is not None, "no soak results to render"
    _, run = found
    long_enough, clean = render_soak.criterion(run)
    body = _body(PRODUCTION / "soak.mdx")

    assert render_soak.CRITERION_DAYS == 7, "the roadmap's criterion is a week"
    if long_enough and clean:
        assert "exit criterion is met by this run" in body
        assert "not met" not in body, "the page still says unmet for a run that met it"
    else:
        assert re.search(r"\bnot met\b", body), "the page does not say the criterion is unmet"
        assert "criterion is met by this run" not in body

    # The control: the derivation is not a constant. A run of a week with nothing unattributed
    # meets it, and the same run one second short does not.
    week = dict(run, elapsed_seconds=7 * 86_400, unexplained=0)
    assert render_soak.criterion(week) == (True, True)
    assert render_soak.criterion(dict(week, elapsed_seconds=7 * 86_400 - 1))[0] is False
    assert render_soak.criterion(dict(week, unexplained=1))[1] is False


def test_production_is_a_top_level_group_between_get_started_and_guides():
    """First-glance signal A3: a reader scanning the sidebar finds it without opening anything."""
    document = json.loads((DOCS / "docs.json").read_text(encoding="utf-8"))
    groups = [
        group["group"]
        for tab in document["navigation"]["tabs"]
        if tab["tab"] == "Documentation"
        for group in tab["groups"]
    ]
    assert "Production" in groups, groups
    assert groups.index("Get started") < groups.index("Production") < groups.index("Guides")


def test_every_production_page_is_in_the_production_group():
    document = json.loads((DOCS / "docs.json").read_text(encoding="utf-8"))
    listed = [
        page
        for tab in document["navigation"]["tabs"]
        for group in tab["groups"]
        if group["group"] == "Production"
        for page in group["pages"]
    ]
    expected = {f"production/{page.stem}" for page in PAGES} | {"postgres"}
    assert set(listed) == expected, listed
    assert len(listed) == len(expected), f"a page is listed twice: {listed}"
    assert listed[0] == "production/index", "the section's front door comes first"


READINESS_HOMES = ("README.md", "docs/index.mdx", "docs/production/index.mdx")


@pytest.mark.parametrize("home", READINESS_HOMES)
def test_the_readiness_block_is_the_generators_in_every_place_it_appears(home: str):
    """One block, three homes. A hand-edited copy in any of them is the drift this refuses."""
    sys.path.insert(0, str(TOOLS))
    try:
        import render_readiness
    finally:
        sys.path.pop(0)
    page = REPO_ROOT / home
    blocks = render_readiness.marker_blocks(page.read_text(encoding="utf-8"))
    assert len(blocks) == 1, f"{home} carries {len(blocks)} readiness blocks"
    _, fmt, embedded = blocks[0]
    assert embedded == render_readiness.render(fmt, render_readiness.state()), home


@pytest.mark.parametrize("home", READINESS_HOMES)
def test_the_not_yet_list_is_inside_the_block_and_not_below_it(home: str):
    """The half a reader would skip if it were a separate section they could scroll past."""
    sys.path.insert(0, str(TOOLS))
    try:
        import render_readiness
    finally:
        sys.path.pop(0)
    _, _, embedded = render_readiness.marker_blocks((REPO_ROOT / home).read_text(encoding="utf-8"))[
        0
    ]
    assert "**Not yet:**" in embedded, home
    for claim, _why in render_readiness.NOT_YET:
        assert claim in embedded, f"{home} is missing {claim!r}"


def test_the_recorded_readiness_still_matches_what_it_was_measured_from():
    """Everything in the block except the test count, re-measured here rather than trusted.

    Written because an independent review proved the gap by mutation: it set `pyproject.toml`
    to `0.7.0` and every test in this file stayed green, because the two tests below compare
    the pages to the **recorded** JSON rather than to reality. `render_readiness.py --check`
    caught it — but that runs in the `docs` CI job, which is not one of the required status
    checks, so it cannot block a merge. This runs in the suite that can.

    The test **count** is deliberately not re-measured here: it is a floor, it moves with every
    pull request, and collecting the suite from inside the suite is not something to do per
    test. `--check` owns that one; this owns everything that should never move on its own.
    """
    import sys
    import tomllib

    sys.path.insert(0, str(TOOLS))
    try:
        import render_readiness
    finally:
        sys.path.pop(0)
    from ctrlrun.verify.guarantees import GUARANTEES

    recorded = render_readiness.state()
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        assert recorded["version"] == tomllib.load(handle)["project"]["version"]
    assert recorded["guarantees"] == len(GUARANTEES)

    if not (REPO_ROOT / "research" / "soak" / "results").exists():
        # In a distribution the results are pruned, so `soak()` reports none — which says
        # nothing about whether the recorded figures drifted. The version and the guarantee
        # count above are checked either way.
        _repository_only(REPO_ROOT / "research" / "soak" / "results")
    published = render_readiness.soak()
    if published is None:
        assert recorded["soak"] is None
    else:
        assert recorded["soak"] == {
            "elapsed": published["elapsed_human"],
            "elapsed_seconds": published["elapsed_seconds"],
            "backend": published["backend"].split(" ")[0],
            "actions": published["actions"],
            "unexplained": published["unexplained"],
            "positive_control": published["positive_control_fired"],
        }


def test_the_not_yet_list_grows_the_week_until_the_week_is_run():
    """The soak's absence is derived, not written down, so it leaves on its own when it is due.

    The other three entries are statements nothing can measure. This one can be measured, and a
    hard-coded line would keep saying the week was owed on the day it was finally run — the
    flattering failure, in the list whose whole job is the unflattering half.
    """
    import sys

    sys.path.insert(0, str(TOOLS))
    try:
        import render_readiness
    finally:
        sys.path.pop(0)

    recorded = render_readiness.state()
    week = dict(recorded["soak"], elapsed_seconds=7 * 86_400, unexplained=0)
    met = dict(recorded, soak=week)
    short = dict(recorded, soak=dict(week, elapsed_seconds=7 * 86_400 - 1))
    dirty = dict(recorded, soak=dict(week, unexplained=3))

    assert render_readiness.not_yet(met) == render_readiness.NOT_YET
    for data in (short, dirty):
        entries = render_readiness.not_yet(data)
        assert len(entries) == len(render_readiness.NOT_YET) + 1
        assert entries[0][0] == "No soak of the length the roadmap asks for."


def test_the_readiness_block_refuses_a_shrunken_suite_and_accepts_a_grown_one():
    """The count is a floor. A suite that grew is fine; one that shrank is a claim that rotted.

    Without this, `--check` would either be regenerated by every pull request that adds a test
    — and so regenerated without being read — or would never notice a suite that lost a third
    of itself while the README kept the old number.
    """
    sys.path.insert(0, str(TOOLS))
    try:
        import render_readiness
    finally:
        sys.path.pop(0)
    recorded = render_readiness.state()
    grown = dict(recorded, tests=recorded["tests"] + 500)
    shrunk = dict(recorded, tests=recorded["tests"] - 1)
    assert render_readiness.check(grown, pages=[]) == []
    assert any("collects" in item for item in render_readiness.check(shrunk, pages=[]))


def test_the_badge_row_is_generated_and_carries_the_test_count_badge():
    sys.path.insert(0, str(TOOLS))
    try:
        import render_badges
    finally:
        sys.path.pop(0)
    assert render_badges.check() == []
    alts = [badge.alt for badge in render_badges.BADGES]
    assert "Tests" in alts and "CTRLRun verified" in alts, alts
    document = render_badges.tests_badge(3825)
    assert document == {
        "schemaVersion": 1,
        "label": "tests",
        "message": "3,825",
        "color": "B8730A",
    }
    assert "passing" not in json.dumps(document), "the badge says a count, not an outcome"


def test_ci_publishes_the_test_count_badge_after_the_suite_has_passed():
    """Order is the whole claim: a badge written before the run would count a red suite.

    **Asserted against the parsed workflow, not against substring positions in the file.** The
    first version compared `workflow.index(...)` of two strings, which says nothing about
    execution — two steps in different jobs have a text order and no run order. An independent
    review changed the step's condition to `always() && …`, which makes it run after a red
    suite, and the test passed. What is asserted now is the thing that decides it: the step is
    in the same job, at a later index, and its condition contains no status function, so it
    inherits the implicit `success()` and is skipped when the suite is red.
    """
    import yaml

    ci = _repository_only(REPO_ROOT / ".github" / "workflows" / "ci.yml")
    workflow = yaml.safe_load(ci.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["check"]["steps"]
    ran = [i for i, step in enumerate(steps) if "./scripts/check.sh" in str(step.get("run", ""))]
    wrote = [
        i
        for i, step in enumerate(steps)
        if "render_badges.py --write-count" in str(step.get("run", ""))
    ]
    assert len(ran) == 1 and len(wrote) == 1, (ran, wrote)
    assert ran[0] < wrote[0], "the badge is written before the suite runs"

    condition = str(steps[wrote[0]].get("if", ""))
    for override in ("always(", "success(", "failure(", "cancelled(", "!"):
        assert override not in condition, (
            f"the write step overrides the implicit success(): {condition!r}"
        )

    # And the publishing job waits on that job, so a red matrix leg publishes nothing.
    badge = workflow["jobs"]["badge"]
    assert "check" in badge["needs"] and "verify" in badge["needs"], badge["needs"]
    assert badge["if"] == "github.event_name == 'push' && github.ref == 'refs/heads/main'"

    # The matrix leg the step is guarded on has to be one the matrix actually runs, or the
    # artifact is never uploaded and the *verify* badge's download fails on the next push.
    guarded = re.search(r"matrix\.python-version == '([^']+)'", condition)
    assert guarded, condition
    assert guarded.group(1) in workflow["jobs"]["check"]["strategy"]["matrix"]["python-version"]


def test_the_readme_says_where_it_runs_before_the_badges():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    header = "Runs in production on a single file, or on Postgres across hosts. Apache-2.0."
    assert header in readme
    assert readme.index(header) < readme.index("img.shields.io")


def test_the_home_page_offers_to_run_it_for_real():
    home = (DOCS / "index.mdx").read_text(encoding="utf-8")
    assert "Run it for real" in home
    assert "/production/index" in home


def test_capabilities_names_the_two_stores_and_what_each_is_for():
    capabilities = (DOCS / "capabilities.yaml").read_text(encoding="utf-8")
    assert "durable" in capabilities.lower()
    assert "Postgres" in capabilities and "SQLite" in capabilities


def test_the_faq_answers_the_two_questions_this_section_provokes():
    faq = (DOCS / "faq.mdx").read_text(encoding="utf-8")
    assert "Is SQLite really enough" in faq
    assert "production-ready" in faq.lower()


def test_claims_carries_a_row_for_the_readiness_block():
    claims = (DOCS / "CLAIMS.md").read_text(encoding="utf-8")
    assert "readiness" in claims.lower()
    assert "production" in claims.lower()
