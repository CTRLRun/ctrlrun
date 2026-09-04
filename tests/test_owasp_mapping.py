"""`docs/OWASP-AGENTIC-TOP10.md`. SPEC-v0.4 §6; T121.

The mapping is complete in **both** directions, and the second direction is the one that makes
the first credible: every entry with no guarantee is listed by name under "Not covered by
CTRLRun", so a reader can see the size of what is left out without counting rows.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ctrlrun.verify import guarantees as reg

REPO_ROOT = Path(__file__).resolve().parents[1]
MAPPING = REPO_ROOT / "docs" / "OWASP-AGENTIC-TOP10.md"
README = REPO_ROOT / "README.md"
VERIFY_DOC = REPO_ROOT / "docs" / "verify.md"

#: The edition this document is written against, as recorded in it. Derived from the
#: OWASP-owned `OWASP/secure-agent-playbook` repository and corroborated against two
#: independent third-party summaries; the published PDF is behind a download form, which the
#: document says in as many words rather than implying a reading nobody made.
EDITION = "2026"
ENTRIES = {
    "ASI01:2026": "Agent Goal Hijack",
    "ASI02:2026": "Tool Misuse",
    "ASI03:2026": "Identity & Privilege Abuse",
    "ASI04:2026": "Agentic Supply Chain Vulnerabilities",
    "ASI05:2026": "Unexpected Code Execution",
    "ASI06:2026": "Memory & Context Poisoning",
    "ASI07:2026": "Insecure Inter-Agent Communication",
    "ASI08:2026": "Cascading Failures",
    "ASI09:2026": "Human-Agent Trust Exploitation",
    "ASI10:2026": "Rogue Agents",
}

#: §6.1 — every ASI code in the document has to look like one, so a typo is not silently a
#: new entry.
CODE = re.compile(r"ASI\d{2}:\d{4}")

#: `v0.2 §10` T31's list, unchanged. A mapping table presented as coverage is a compliance
#: claim wearing a table's clothes, so the same words are refused here.
COMPLIANCE_WORDS = (
    "compliant",
    "compliance",
    "certified",
    "certification",
    "accredited",
    "attestation",
    "conforms to",
    "aligned with",
)


def _document() -> str:
    return MAPPING.read_text(encoding="utf-8")


def _flat() -> str:
    """The document with its line wrapping removed, so a phrase can be asserted whole.

    A sentence that reads correctly and wraps across two lines is still the sentence; a test
    that could not see it would be a test that pushes prose into one long line to satisfy it.
    """
    return " ".join(_document().split())


def _sections() -> tuple[str, str]:
    """The mapping table and the `Not covered by CTRLRun` half, as text."""
    text = _document()
    split = text.index("## Not covered by CTRLRun")
    return text[:split], text[split:]


# --- T121: the mapping is complete in both directions --------------------------------------


def test_T121_every_guarantee_in_the_registry_appears_in_the_mapping():
    mapping, _ = _sections()

    for guarantee in reg.GUARANTEES:
        assert f"**{guarantee.id}**" in mapping, guarantee.id
        assert guarantee.title in mapping, guarantee.id


def test_T121_every_code_in_the_document_is_one_of_the_ten_in_the_cited_edition():
    found = set(CODE.findall(_document()))

    assert found, "the document names no entry at all"
    assert found <= set(ENTRIES), sorted(found - set(ENTRIES))
    for code in found:
        assert CODE.fullmatch(code), code


def test_T121_every_entry_in_the_cited_edition_appears_in_one_half_or_the_other():
    """None appears in neither. An entry silently absent would read as covered."""
    mapping, not_covered = _sections()

    for code in ENTRIES:
        assert code in mapping or code in not_covered, code


def test_T121_the_four_uncovered_entries_are_listed_by_name():
    """§6.1's disclaimer says four of the ten are not addressed at all, and this is the test
    that keeps that sentence true rather than merely written."""
    _, not_covered = _sections()

    fully_uncovered = {"ASI04:2026", "ASI05:2026", "ASI06:2026", "ASI07:2026"}
    for code in fully_uncovered:
        assert code in not_covered, code
        assert ENTRIES[code] in not_covered, code
    assert "Four of the ten entries are not addressed by CTRLRun at all" in _flat()


def test_T121_a_partly_addressed_entry_appears_in_both_halves():
    """§6.2 item 4 — the honest place for a hedge is next to the thing it qualifies."""
    mapping, not_covered = _sections()

    for code in ("ASI01:2026", "ASI09:2026"):
        assert code in mapping, code
        assert code in not_covered, code
    assert "Not covered" in not_covered


def test_T121_every_entry_title_is_the_one_the_cited_edition_uses():
    document = _document()

    for code, title in ENTRIES.items():
        if code not in document:
            continue
        # The title has to appear somewhere the code does, so a code cannot drift onto a
        # different entry's sentence without the pair going out of step.
        assert title in document, (code, title)


def test_T121_the_document_makes_no_compliance_claim():
    text = _document().lower()

    for word in COMPLIANCE_WORDS:
        if word not in text:
            continue
        # The disclaimer names the words in order to refuse them. Every occurrence has to sit
        # in a paragraph that does.
        offending = [
            paragraph
            for paragraph in _document().split("\n\n")
            if word in paragraph.lower()
            and not any(marker in paragraph.lower() for marker in ("not ", "never", "no "))
        ]
        assert not offending, f"{word!r} used as a claim: {offending}"


def test_T121_the_first_line_before_any_table_is_the_disclaimer():
    text = _document()
    first_table = text.index("|")

    disclaimer = " ".join(text[:first_table].split())
    assert "is a **reading** of somebody else's taxonomy" in disclaimer
    assert "not a compliance claim" in disclaimer


def test_T121_the_edition_and_the_date_it_was_read_are_recorded():
    text = _document()

    assert EDITION in text
    assert "2025-12-09" in text
    assert "2026-09-04" in text
    assert "genai.owasp.org" in text
    # And how the codes were derived, because the published PDF could not be retrieved and
    # saying so is the difference between a citation and a claim.
    assert "could not be retrieved" in _flat()
    assert "secure-agent-playbook" in text


@pytest.mark.parametrize("guarantee", reg.GUARANTEES, ids=lambda g: g.id)
def test_T121_every_guarantee_row_names_at_least_one_entry(guarantee):
    mapping, _ = _sections()
    row = next(line for line in mapping.split("\n") if line.startswith(f"| **{guarantee.id}**"))

    assert CODE.search(row), guarantee.id


# --- linked from where a reader will be -----------------------------------------------------


def test_the_mapping_is_linked_from_the_readme_and_from_the_verify_page():
    assert "OWASP-AGENTIC-TOP10.md" in README.read_text(encoding="utf-8")
    assert "OWASP-AGENTIC-TOP10.md" in VERIFY_DOC.read_text(encoding="utf-8")
