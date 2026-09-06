"""The documentation site's pages, held to `docs/STYLE.md` and `docs/IA.md`.

The rules a machine can check: frontmatter, the word budget, the closing Next block, the
navigation, and on every Concepts page a definitional first sentence. The rest is what a
reviewer reads for.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"

if not (DOCS / "docs.json").exists():  # pragma: no cover - not a checkout
    pytest.skip("no repository checkout", allow_module_level=True)


def _published() -> set[str]:
    """Every page path `docs.json` lists, so a Markdown document that is a site page is tested
    like one and a Markdown document that is not is left alone."""

    found: set[str] = set()

    def walk(node: object) -> None:
        # Only the strings inside a `pages` array are page paths. A group's own label is a
        # string too, and on a case-insensitive filesystem the label "Architecture" resolved
        # to ARCHITECTURE.md and was tested as a page that does not exist.
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            for key, value in node.items():
                if key == "pages":
                    found.update(item for item in value if isinstance(item, str))
                walk(value)

    walk(json.loads((DOCS / "docs.json").read_text(encoding="utf-8"))["navigation"])
    return found


#: Site pages: the MDX ones, and the Markdown documents `docs.json` publishes. The second half
#: was missing until the deployed site showed a filename title above each document's own H1 --
#: eighteen pages no test looked at, because the glob said `*.mdx`.
PAGES = sorted(
    [path for path in DOCS.rglob("*.mdx") if "generated" not in path.parts]
    + [DOCS / f"{slug}.md" for slug in sorted(_published()) if (DOCS / f"{slug}.md").exists()]
)

#: Long-form documents that predate the site and are read the way a specification is. The word
#: budget is for pages written to be read in one sitting.
LONG_FORM = frozenset(
    {
        "ARCHITECTURE",
        "THREAT_MODEL",
        "CLAIMS",
        "ROADMAP",
        "verify",
        "postgres",
        "adapters",
        "authority",
        "ACS",
        "OWASP-AGENTIC-TOP10",
        "how-this-is-built",
    }
)
CONCEPTS = sorted((DOCS / "concepts").glob("*.mdx"))
WORD_BUDGET = 900
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_FENCE = re.compile(r"^```.*?^```", re.M | re.S)


def _frontmatter(page: Path) -> dict[str, str]:
    matched = _FRONTMATTER.match(page.read_text(encoding="utf-8"))
    assert matched, f"{page.name} has no frontmatter"
    fields: dict[str, str] = {}
    for line in matched.group(1).splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip().strip('"')
    return fields


def _body(page: Path) -> str:
    return _FRONTMATTER.sub("", page.read_text(encoding="utf-8"), count=1)


# The end tag is matched the way a browser's parser reads one, not the way it is usually typed.
# Tag names are case-insensitive; whitespace is allowed around the slash and the name; and an
# end tag carrying attribute-like text -- `</script bar>` -- still closes the element, because
# the parser ignores what it finds there rather than refusing the tag. A filter that misses any
# of those spellings leaves the block in the text, where a structured-data payload counts
# against the page's prose budget, which is the one thing this helper exists to prevent.
#
# `\bscript\b` on both ends so that a tag merely beginning with those letters -- `<scriptish>`
# -- neither opens nor closes a block.
_SCRIPT = re.compile(r"<\s*script\b.*?<\s*/\s*script\b[^>]*>", re.S | re.I)


def _prose(page: Path) -> str:
    """The page with code blocks, structured data, JSX tags and frontmatter removed.

    A `<script type="application/ld+json">` block is markup for a search engine, not words a
    reader reads, so it does not count against the page's budget.
    """
    text = _SCRIPT.sub("", _FENCE.sub("", _body(page)))
    return re.sub(r"<[^>]+>", "", text)


def _navigation_pages() -> set[str]:
    document = json.loads((DOCS / "docs.json").read_text(encoding="utf-8"))
    found: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, str):
            found.add(node)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            for key in ("pages", "groups", "tabs", "anchors", "dropdowns"):
                if key in node:
                    walk(node[key])

    walk(document["navigation"])
    return found


@pytest.mark.parametrize("page", PAGES, ids=[p.relative_to(DOCS).as_posix() for p in PAGES])
def test_every_page_has_a_title_and_a_description(page: Path):
    fields = _frontmatter(page)
    assert fields.get("title"), page.name
    assert fields.get("description"), page.name
    assert "!" not in fields["title"] and "!" not in fields["description"]


@pytest.mark.parametrize("page", PAGES, ids=[p.relative_to(DOCS).as_posix() for p in PAGES])
def test_every_page_ends_with_next_links(page: Path):
    if page.suffix == ".md":
        return  # a document read on GitHub too ends where its own text ends
    body = _body(page).rstrip()
    assert "## Next" in body, page.name
    tail = body.split("## Next", 1)[1]
    assert tail.count("](/") + tail.count("](http") >= 2, f"{page.name}: Next needs two links"


@pytest.mark.parametrize("page", PAGES, ids=[p.relative_to(DOCS).as_posix() for p in PAGES])
def test_every_page_links_to_why_and_to_get_started_or_is_one_of_them(page: Path):
    if page.suffix == ".md":
        return  # same reason: read on GitHub too, where a site path resolves to nothing
    slug = page.relative_to(DOCS).with_suffix("").as_posix()
    text = _body(page)
    if slug != "why":
        assert "](/why)" in text, f"{page.name} does not link to Why"
    if not slug.startswith("get-started/") and slug != "index":
        assert "](/get-started/" in text, f"{page.name} does not link to Get started"


@pytest.mark.parametrize("page", PAGES, ids=[p.relative_to(DOCS).as_posix() for p in PAGES])
def test_every_page_is_in_the_navigation(page: Path):
    slug = page.relative_to(DOCS).with_suffix("").as_posix()
    assert slug in _navigation_pages(), f"{slug} is not in docs.json"


@pytest.mark.parametrize("page", PAGES, ids=[p.relative_to(DOCS).as_posix() for p in PAGES])
def test_every_page_but_a_reference_page_fits_the_word_budget(page: Path):
    slug = page.relative_to(DOCS).with_suffix("").as_posix()
    if slug.startswith("reference/") or slug == "index" or slug in LONG_FORM:
        return
    words = len(_prose(page).split())
    assert words <= WORD_BUDGET, f"{page.name}: {words} words of prose, budget {WORD_BUDGET}"


@pytest.mark.parametrize("page", CONCEPTS, ids=[p.stem for p in CONCEPTS])
def test_every_concepts_page_opens_with_a_definitional_sentence(page: Path):
    """The sentence an assistant can quote standalone: the page's subject, then *is*."""
    first = _prose(page).strip().split("\n\n", 1)[0].replace("\n", " ")
    assert re.match(
        r"^(An?|The|Authority|Observe mode|Fail closed|A decision)\b.*?\b(is|means|answers)\b",
        first,
    ), f"{page.name} does not open with a definition: {first[:80]!r}"


@pytest.mark.parametrize("page", CONCEPTS, ids=[p.stem for p in CONCEPTS])
def test_every_concepts_page_says_what_it_does_not_do(page: Path):
    assert "## What it does not do" in _body(page) or "## What it never does" in _body(page)


def test_the_home_page_carries_the_fixed_copy_and_the_generated_grid():
    text = (DOCS / "index.mdx").read_text(encoding="utf-8")
    assert "The last check before an AI agent does something it can't undo." in text
    assert "Autonomy belongs to the action, not the agent." in text
    assert "generated from docs/capabilities.yaml (mdx)" in text
    assert '"mcpServers"' in text and "/mcp" in text


def test_the_why_page_opens_with_the_opener_and_stays_under_700_words():
    page = DOCS / "why.mdx"
    prose = _prose(page).strip()
    assert prose.startswith(
        "Everyone is rushing to ship AI agents without thinking about consequences."
    )
    assert len(prose.split()) <= 700, len(prose.split())
    assert "](/how-this-is-built)" in _body(page)


def test_the_site_ignores_what_is_not_a_page():
    ignored = (DOCS / ".mintignore").read_text(encoding="utf-8")
    for name in ("BUILD-PROMPTS-*.md", "README.md", "IA.md", "STYLE.md", "generated/", "assets/"):
        assert name in ignored, name


def test_no_page_states_a_guarantee_count_the_catalogue_does_not_have():
    """`ctrlrun verify` grew from ten guarantees to eleven and four pages kept saying ten.

    Found by the launch-readiness audit, which ran verify and read the number. A count in
    prose is a claim like any other, and this is the cheapest way to hold one: scan for a
    number-of-guarantees phrase and compare it with the catalogue.
    """
    from ctrlrun.verify.guarantees import GUARANTEES

    words = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "eleven": 11,
        "twelve": 12,
    }
    # Only a count **of the catalogue**. "Two guarantees are not applicable" is a count of the
    # N/As in one run and is not this claim, so the pattern needs the determiner that makes it
    # about the whole set — which is exactly how the wrong ones were written: *all ten
    # guarantees*.
    number = r"(\d+|" + "|".join(words) + r")"
    pattern = re.compile(
        rf"\b(?:all|every one of the|the whole set of)\s+{number}\s+guarantees\b"
        rf"|\b{number}\s+guarantees (?:you can check|in the catalogue)\b",
        re.I,
    )
    wrong: list[str] = []
    for page in PAGES:
        for line in _prose(page).splitlines():
            for found in pattern.finditer(line):
                token = next(group for group in found.groups() if group).lower()
                stated = words.get(token, int(token) if token.isdigit() else None)
                if stated is not None and stated != len(GUARANTEES):
                    wrong.append(f"{page.name}: {line.strip()[:90]!r}")
    assert wrong == [], f"the catalogue has {len(GUARANTEES)}; these say otherwise: {wrong}"


def test_the_verify_shapes_the_roadmap_quotes_are_the_ones_verify_reports():
    """`ROADMAP.md` is a site page and quoted `10/10` and `5/5` long after both moved."""
    roadmap = (DOCS / "ROADMAP.md").read_text(encoding="utf-8")
    assert "10/10" not in roadmap and "5/5" not in roadmap, "a stale verify shape is quoted"
    assert "11/11" in roadmap and "6/6" in roadmap


def test_how_this_is_built_does_not_undercount_the_suite_it_describes():
    """Its thesis is that every claim maps to a test, so its own count has to be one.

    A floor, like the readiness block's and for the same reason: the suite only grows, and a
    number every pull request had to regenerate would be regenerated without being read. It
    was 1,625 functions and 2,442 cases against a real 1,704 and 3,944 — off by fifteen
    hundred, on the page that argues the tests are the evidence.
    """
    functions: set[str] = set()
    for module in (REPO_ROOT / "tests").glob("*.py"):
        functions.update(re.findall(r"^def (test_\w+)", module.read_text(encoding="utf-8"), re.M))

    text = (DOCS / "how-this-is-built.md").read_text(encoding="utf-8")
    found = re.search(r"([\d,]+) test functions, ([\d,]+) cases", text)
    assert found, "the page no longer states a suite size"
    stated_functions = int(found.group(1).replace(",", ""))
    stated_cases = int(found.group(2).replace(",", ""))

    assert stated_functions <= len(functions), (
        f"the page claims {stated_functions:,} test functions and there are {len(functions):,}"
    )
    assert stated_cases >= stated_functions, "cases cannot be fewer than functions"
    # The stated case count is a floor too, and 3,900 is the size of the suite when this was
    # written. A drop below it is a suite that lost a tenth of itself unnoticed.
    assert stated_cases >= 3_900, stated_cases


def test_no_page_says_every_call_leaves_a_receipt():
    """It does not. An approval-required call has no receipt until somebody decides it.

    Five pages said *"every call leaves a receipt, refused ones too"*. A receipt is written
    when an action reaches a **terminal** state, and *waiting on a human* is not one — so a
    reader following `cookbook/protect-an-mcp-server` counted four protected calls and three
    receipts and had no way to tell whether that was the docs or a bug. Reproduced by the
    launch-readiness audit and again below, so this is a measurement rather than an opinion.
    """
    import tempfile

    import ctrlrun
    from ctrlrun import Control, Policy, SQLiteStateStore
    from ctrlrun.errors import ActionDenied, ApprovalRequired

    document = """schema: ctrlrun.policy/v2
actions:
  a.small:
    effect: "e:{id}"
    rules:
      - when: { amount_gte: 0, amount_lte: 10 }
        decision: allow
      - decision: approve
  a.nope:
    decision: deny
"""
    with tempfile.TemporaryDirectory() as directory:
        policy_path = Path(directory) / "ctrlrun.yaml"
        policy_path.write_text(document, encoding="utf-8")
        store = SQLiteStateStore(Path(directory) / "state.db")
        control = Control(Policy.from_file(policy_path), store)

        @ctrlrun.protect("a.small", effect="e:{id}", control=control)
        def small(id: str, amount: int) -> str:
            return "ok"

        @ctrlrun.protect("a.nope", control=control)
        def nope() -> str:
            return "ok"

        with ctrlrun.context(agent="ag"):
            small(id="1", amount=5)
            try:
                nope()
            except ActionDenied:
                pass
            else:
                raise AssertionError("the deny rule did not deny")
            try:
                small(id="2", amount=500)
            except ApprovalRequired:
                pass
            else:
                raise AssertionError("the approve rule did not ask")

        written = len(list(store.receipts()))
    assert written == 2, f"three calls, {written} receipts — the shape of this claim changed"

    forbidden = re.compile(r"every (?:`?tools/call`?|call) (?:leaves|has) a receipt", re.I)
    wrong = []
    for page in PAGES:
        for line in _prose(page).splitlines():
            found = forbidden.search(line)
            if found and "reaches a decision" not in line:
                wrong.append(f"{page.name}: {line.strip()[:90]!r}")
    assert wrong == [], wrong


@pytest.mark.parametrize(
    ("opening", "closing"),
    [
        ("<script", "</script>"),
        ("<SCRIPT", "</SCRIPT>"),
        ("<script", "</script >"),
        ("<script", "</ script>"),
        ("<script", "</SCRIPT\n>"),
        ("<script", "</script bar>"),
        ("< script", "</script\t\n bar>"),
    ],
    ids=[
        "plain",
        "upper-case",
        "space-before-gt",
        "space-after-slash",
        "newline",
        "attribute-like-text",
        "space-in-start-tag-and-junk-in-end-tag",
    ],
)
def test_the_prose_filter_strips_a_script_block_however_its_tags_are_written(
    tmp_path: Path, opening: str, closing: str
):
    """Every spelling of the tags closes the same block, and the word budget must see none of it.

    HTML tag names are case-insensitive and an end tag may carry whitespace before its `>`. A
    filter that misses a spelling leaves the block in the text, where a structured-data payload
    -- markup for a search engine, not words a reader reads -- is counted against the page's
    budget and can push a page over it for a reason no author could see.
    """
    page = tmp_path / "page.mdx"
    page.write_text(
        f'---\ntitle: t\n---\n\nvisible prose\n\n{opening} type="application/ld+json">\n'
        f'{{"@type": "SoftwareApplication", "hidden": "wordone wordtwo"}}\n{closing}\n',
        encoding="utf-8",
    )

    prose = _prose(page)

    assert "visible prose" in prose
    assert "wordone" not in prose, f"{opening} ... {closing} reached the word budget"
