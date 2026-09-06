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

PAGES = sorted(path for path in DOCS.rglob("*.mdx") if "generated" not in path.parts)
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


_SCRIPT = re.compile(r"<script.*?</script>", re.S)


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
    body = _body(page).rstrip()
    assert "## Next" in body, page.name
    tail = body.split("## Next", 1)[1]
    assert tail.count("](/") + tail.count("](http") >= 2, f"{page.name}: Next needs two links"


@pytest.mark.parametrize("page", PAGES, ids=[p.relative_to(DOCS).as_posix() for p in PAGES])
def test_every_page_links_to_why_and_to_get_started_or_is_one_of_them(page: Path):
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
    if slug.startswith("reference/") or slug == "index":
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
