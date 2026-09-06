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


SPEC_TEST_IDS = _spec_test_ids()


def test_the_specs_actually_define_test_ids():
    """The control: the id scanner finds something, or every citation check below is vacuous."""
    assert len(SPEC_TEST_IDS) > 100, len(SPEC_TEST_IDS)
    assert {"T155", "T155b", "T155c", "T156", "T164", "T167"} <= SPEC_TEST_IDS


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
    cited = set(re.findall(r"\bT\d+[a-z]*\b", " ".join(line)))
    assert cited, f"{page.name}: 'Verified by' cites no test"
    unknown = cited - SPEC_TEST_IDS
    assert not unknown, f"{page.name} cites tests no specification defines: {sorted(unknown)}"


@pytest.mark.parametrize("page", PAGES, ids=[p.stem for p in PAGES])
def test_the_section_does_not_reach_for_the_words_it_refuses(page: Path):
    """`SPEC-v0.6.md` §1.2's third rule, applied to the pages most tempted to break it.

    `signed` and `secure` are the two words an operations page drifts into, and
    "guaranteed exactly-once" is the claim the whole product exists to refuse. The scan is on
    word boundaries so `design`, `assign` and `security` are not hits, and it is deliberately
    narrower than `T180`'s allow-listed scan: nothing in this section has earned an exception
    yet, so there is no allow-list to erode.
    """
    prose = _FENCE.sub("", _body(page)).lower()
    for word in ("signed", "signing", "signature", "tamper-proof", "non-repudiation"):
        assert not re.search(rf"\b{re.escape(word)}\b", prose), f"{page.name} says {word!r}"
    assert not re.search(r"\bsecure\b", prose), f"{page.name} says 'secure'"
    assert "guaranteed exactly-once" not in prose, page.name

    # Every occurrence of "exactly-once" must sit in a sentence that denies it. Checked per
    # sentence rather than per page: an earlier version of this asserted the page contained
    # "not" somewhere, which every page in English does, so it would have passed a page that
    # promised exactly-once execution in one sentence and denied something else in another.
    for sentence in re.split(r"(?<=[.!?])\s+", prose):
        if "exactly-once" in sentence:
            assert re.search(r"\b(cannot|not|never|no)\b", sentence), (
                f"{page.name}: exactly-once is claimed rather than denied: {sentence[:120]!r}"
            )


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
    """Before `COMMIT` and during `COMMIT` are two behaviours, two tests, and two paragraphs.

    They are the pair a reader most wants collapsed into one sentence, and collapsing them is
    the double execution `T155c` exists to catch: nothing committed and retry the write is not
    the same instruction as unknown, so re-read.
    """
    body = _body(PRODUCTION / "how-reservation-works.mdx")
    assert "Before" in body and "During" in body
    cited = set(re.findall(r"\bT\d+[a-z]*\b", body))
    assert {"T155", "T155b", "T155c", "T156"} <= cited, sorted(cited)


def test_the_soak_page_is_the_render_of_the_published_results():
    """No hand-written number, and the duration is the measured one."""
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
    body = _body(PRODUCTION / "soak.mdx")
    results = sorted((REPO_ROOT / "research" / "soak" / "results").glob("*.json"))
    assert results, "no soak results to render"
    measured = json.loads(results[-1].read_text(encoding="utf-8"))
    assert measured["elapsed_human"] in body, measured["elapsed_human"]
    assert "week" in body, "the page does not say what the criterion asks for"
    assert re.search(r"\bnot met\b", body), "the page does not say the criterion is unmet"
    assert "exit_criterion_met" in body, "the page does not explain the JSON field"


def test_the_soak_page_never_calls_twenty_minutes_a_week():
    body = _body(PRODUCTION / "soak.mdx").lower()
    assert not re.search(r"soaked for (a|one) week", body)
    assert "for a week" not in body or "not met" in body


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

    The step that writes it is in the `check` job, after `scripts/check.sh`; the step that
    pushes it is in the `badge` job, which runs only on a push to `main`.
    """
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "render_badges.py --write-count" in workflow
    assert workflow.index("./scripts/check.sh") < workflow.index("render_badges.py --write-count")
    assert "tests-badge.json" in workflow


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
