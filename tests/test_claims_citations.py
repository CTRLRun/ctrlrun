"""Which symbol a claims citation is about, and the false green that came of guessing.

`scripts/repoint-claims.py` writes the line numbers and
`test_the_claims_table_line_numbers_point_at_what_they_name` checks them. They answered "which
symbol is this citation about" separately, and both answered it the same wrong way: with the
symbols of the whole **row**.

So a row citing six commands had every reference re-pointed to one definition -- whichever the
repointer found first -- and the guard accepted all six, because that line does define a symbol
the row names. A row claiming to cite six commands cited one, six times, and nothing said so.
It was caught by reading the diff, which is not a check.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS = REPO_ROOT / "tools" / "docs_audit"

if not TOOLS.exists():  # pragma: no cover - not a checkout
    pytest.skip("no repository checkout", allow_module_level=True)

sys.path.insert(0, str(TOOLS))

import claims  # noqa: E402

SIX = (
    '| "the commands work from the shell" | `approve` — `cli/main.py:295`; '
    "`receipts` — `cli/main.py:357`; `effects` — `cli/main.py:431`; "
    "`resolve` — `cli/main.py:449`; `inspect` — `cli/main.py:483`; "
    "`stats` — `cli/main.py:658` | `test_T10_resolve_failed_permits_a_retry` |"
)


def test_each_citation_owns_the_symbol_beside_it():
    """The regression, stated as the property it broke."""
    found = claims.citations(SIX)

    assert [c.line for c in found] == [295, 357, 431, 449, 483, 658]
    # The nearest symbol, which is the one the citation is about. The last citation also owns
    # the row's test column, because that column is nearest to it and there is nothing after
    # it -- harmless, and stated rather than asserted away.
    assert [c.local[0] for c in found] == [
        "approve",
        "receipts",
        "effects",
        "resolve",
        "inspect",
        "stats",
    ]
    # And the property that broke: no citation owns a *different* command in the same row.
    ordered = ["approve", "receipts", "effects", "resolve", "inspect", "stats"]
    commands = set(ordered)
    for cited, mine in zip(found, ordered, strict=True):
        assert commands & set(cited.local) == {mine}, (cited.line, cited.local)


def test_a_symbol_between_two_citations_goes_to_the_nearer():
    """`` `stats` -- `cli/main.py:658` `` is about `stats`, not about the citation before it.

    Splitting the row at the *next* citation's start gave every symbol to the reference it
    followed, which is the one it is not about.
    """
    row = "| x | `approve` — `cli/main.py:295`; `stats` — `cli/main.py:658` |"

    assert [c.local for c in claims.citations(row)] == [("approve",), ("stats",)]


def test_a_multi_word_span_contributes_every_identifier_in_it():
    """`` `ctrlrun delegate` `` names `delegate`, and a tokenizer that took only the first
    identifier in a span left the citation owning `ctrlrun` and looking stale."""
    row = "| x | `ctrlrun delegate` — `cli/main.py:822` |"

    assert "delegate" in claims.citations(row)[0].local


def test_a_citations_own_path_is_not_harvested_as_a_symbol():
    """`cli` out of `` `cli/main.py:295` `` would sort nearest to every citation in the row.

    `NOT_SYMBOLS` catches the extension; nothing caught the directory.
    """
    for cited in claims.citations(SIX):
        assert "cli" not in cited.names
        assert "main" not in cited.names


def test_the_positions_let_two_citations_of_one_line_move_apart():
    """`` `LEASE_EXPIRED` -- `effect.py:63`; ... `resolved_by` -- `effect.py:63` `` needs the
    second re-pointed and the first left alone.

    The repointer keyed its rewrite on the citation's *text* and called `str.replace`, which
    moved both -- so fixing a stale reference broke a correct one in the same row. The spans
    are what make the rewrite positional.
    """
    row = "| x | `LEASE_EXPIRED` — `effect.py:63`; `resolved_by` — `effect.py:63` |"
    first, second = claims.citations(row)

    assert first.line == second.line == 63
    assert first.start != second.start
    assert row[first.start : first.end] == "`effect.py:63`"
    assert row[second.start : second.end] == "`effect.py:63`"
    assert first.local == ("LEASE_EXPIRED",)
    assert second.local == ("resolved_by",)
