"""What search engines and assistants read: titles, descriptions, one H1, and the FAQ data.

`docs/SEO.md` names the target query per page and the sentence written to answer it. The rules
a machine can hold are here; the sentences themselves are what a reviewer reads for.
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

import test_docs_site  # noqa: E402 - one definition of "a site page", not two

PAGES = test_docs_site.PAGES
TITLE_LIMIT = 60
DESCRIPTION_LIMIT = 155
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_FENCE = re.compile(r"^```.*?^```", re.M | re.S)
_IDS = [p.relative_to(DOCS).as_posix() for p in PAGES]


def _field(page: Path, name: str) -> str:
    matched = re.search(rf'^{name}: "(.*)"$', page.read_text(encoding="utf-8"), re.M)
    return matched.group(1) if matched else ""


def _body(page: Path) -> str:
    return _FRONTMATTER.sub("", page.read_text(encoding="utf-8"), count=1)


@pytest.mark.parametrize("page", PAGES, ids=_IDS)
def test_every_title_and_description_fits_a_search_result(page: Path):
    """A title over 60 characters and a description over 155 are truncated where a stranger
    reads them, which is the one place the words have to work."""
    title, description = _field(page, "title"), _field(page, "description")

    assert title and len(title) <= TITLE_LIMIT, f"{page.name}: title is {len(title)} characters"
    assert description, page.name
    assert len(description) <= DESCRIPTION_LIMIT, (
        f"{page.name}: description is {len(description)} characters"
    )


@pytest.mark.parametrize("page", PAGES, ids=_IDS)
def test_no_page_has_a_second_h1(page: Path):
    """Mintlify renders the frontmatter title as the page's H1, so a `# ` in the body is a
    second one. Code blocks are stripped first: a `#` there is a comment."""
    prose = _FENCE.sub("", _body(page))

    assert not re.search(r"^# ", prose, re.M), f"{page.name} has an H1 in its body"


@pytest.mark.parametrize("page", PAGES, ids=_IDS)
def test_the_description_is_not_the_title_again(page: Path):
    assert _field(page, "description").lower() != _field(page, "title").lower()


def test_the_faq_structured_data_matches_the_page():
    """The FAQ carries FAQ structured data. Every question in it is a question on the page, so
    the markup cannot answer something the reader never sees."""
    text = (DOCS / "faq.mdx").read_text(encoding="utf-8")
    block = text.split("application/ld+json", 1)[1].split("</script>", 1)[0]
    document = json.loads(block.split("JSON.stringify(", 1)[1].rsplit(")", 1)[0].strip())

    assert document["@type"] == "FAQPage"
    questions = [entry["name"] for entry in document["mainEntity"]]
    on_page = re.findall(r'<Accordion title="([^"]+)">', text)

    # Fourteen since the Production section added the two questions it provokes. The count is
    # pinned rather than derived so that dropping an accordion and leaving its markup behind --
    # which is how structured data comes to answer something the reader cannot see -- fails
    # here instead of shipping.
    assert len(questions) == 14, f"{len(questions)} questions in the structured data"
    assert len(on_page) == 14, f"{len(on_page)} accordions on the page"

    # The two lists are the same questions in the same order and not the same strings: an
    # accordion is read with the page around it and says "Is it production-ready?", while a
    # search result carries the question alone and has to name the product. What must not
    # happen is a question in the markup that the reader cannot find on the page, so the counts
    # are pinned and every answer must be non-empty.
    assert len(set(questions)) == 14, "the structured data asks the same question twice"
    for entry in document["mainEntity"]:
        assert entry["acceptedAnswer"]["text"].strip(), entry["name"]


def test_seo_md_names_every_page_and_its_query():
    """`docs/SEO.md` is the plan: one row per page. A page with no row is a page nobody decided
    what it was for."""
    plan = (DOCS / "SEO.md").read_text(encoding="utf-8")
    missing = []
    for page in PAGES:
        slug = page.relative_to(DOCS).with_suffix("").as_posix()
        if slug.startswith("reference/api/") and slug != "reference/api/index":
            continue  # one row covers the generated API pages
        if f"`{slug}`" not in plan:
            missing.append(slug)
    assert missing == [], f"docs/SEO.md has no row for: {missing}"


def test_the_definitional_words_appear_where_the_plan_says():
    """The words written once, for search, are on the pages that own them."""
    for slug, word in (
        ("concepts/effect-keys", "idempotency"),
        ("get-started/three-ways-in", "human-in-the-loop"),
        ("concepts/outcomes-and-ambiguous", "double execution"),
    ):
        text = (DOCS / f"{slug}.mdx").read_text(encoding="utf-8").lower()
        assert word in text, f"{slug} does not carry {word!r}"


def test_the_site_declares_its_social_image_and_indexing():
    document = json.loads((DOCS / "docs.json").read_text(encoding="utf-8"))
    seo = document["seo"]

    assert seo["metatags"]["og:image"].startswith("/images/")
    assert seo["metatags"]["twitter:card"] == "summary_large_image"
    assert (DOCS / "images" / "social-preview.png").exists()
