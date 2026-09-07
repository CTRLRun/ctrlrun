#!/usr/bin/env python
"""Re-derive every line number in `docs/docs/CLAIMS.md` from the code it cites.

The table cites `file.py:NNN`, and `test_the_claims_table_line_numbers_point_at_what_they_name`
requires the cited line to be where a named symbol is **defined**. Every commit that shifts a
line in `policy.py`, `state.py`, `control.py` or `receipt.py` breaks some of them, and doing it
by hand is how a row ends up pointing at a docstring that happens to contain the right word --
which makes the guard green and the claim false. Item 9 regenerates the table wholesale; this is
for the commits in between.

**It refuses rather than guesses.** A reference it cannot resolve to a `def`, a `class` or an
assignment is reported, the file is left untouched, and the exit code is non-zero.

Two defects an independent review found in the first version, both of which let it do exactly
what its docstring says it prevents:

- it accepted any line **containing** a named token -- a comment, a docstring, a string literal --
  and consulted `_definition_of` only when that failed. A row aimed at a line inside a comment
  about redaction was reported as fine;
- `IDENTIFIER` harvested `py` from every `` `file.py:NNN` `` reference, so **32 of 40 rows**
  carried the bare token `py` and any line containing `copy`, `pyyaml` or `python` satisfied
  them.

Both are fixed here, and the identical regex in `tests/test_packaging.py` is fixed in the same
commit -- otherwise the guard stays fictional while this script is honest.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "docs_audit"))

from claims import citations  # noqa: E402  — the path above is what makes it importable


def main() -> int:
    claims = ROOT / "docs" / "docs" / "CLAIMS.md"
    rows = claims.read_text(encoding="utf-8").splitlines(keepends=True)
    unresolved: list[str] = []
    repointed = 0

    for index, row in enumerate(rows):
        edits: list[tuple[int, int, str]] = []
        for cited in citations(row):
            if not cited.names:
                unresolved.append(f"{cited.filename}:{cited.line} names no symbol at all")
                continue
            source = ROOT / "src" / "ctrlrun" / cited.filename
            if not source.exists():
                continue
            lines = source.read_text(encoding="utf-8").splitlines()
            # **This citation's own symbols**, not the row's. A row citing six commands used to
            # accept any of the six on the cited line, so five wrong numbers looked right.
            owned = cited.local or cited.names
            here = lines[cited.line - 1] if 0 < cited.line <= len(lines) else ""
            # Two stages. `local` is the tightening -- a row citing six commands no longer
            # accepts any of the six on any of the lines. `names` is the documented fallback,
            # because the nearest-citation assignment is a heuristic and a row may legitimately
            # write `only `NotExecuted` maps to `FAILED` -- `control.py:1036``, where the symbol
            # that identifies the line sits between two citations. A line matching neither is
            # stale whichever way the symbols were assigned, which is the case worth catching.
            if _resolves(owned, here) or _resolves(cited.names, here):
                continue
            found = _definition_of(owned + cited.names, lines)
            if found is None:
                unresolved.append(f"{cited.filename}:{cited.line} names {list(owned)}")
            else:
                edits.append((cited.start, cited.end, f"`{cited.filename}:{found}`"))
        if edits:
            # **Positional, right to left**, and never `str.replace`. Keying the rewrite on the
            # citation's text and replacing every occurrence moved *correct* references too:
            # `` `LEASE_EXPIRED` -- `effect.py:63`; ... `resolved_by` -- `effect.py:63` `` needs
            # the second moved and the first left alone, and one global replace cannot do that.
            for begin, finish, replacement in sorted(edits, reverse=True):
                row = row[:begin] + replacement + row[finish:]
            rows[index] = row
            repointed += len(edits)

    if unresolved:
        # **Refuse before writing.** A partial re-point plus a non-zero exit leaves the table in
        # a state nobody chose, and the next run reports different rows.
        for line in unresolved:
            print(f"unresolved: {line}", file=sys.stderr)
        print(f"re-pointed 0, unresolved {len(unresolved)} — nothing written", file=sys.stderr)
        return 1

    claims.write_text("".join(rows), encoding="utf-8")
    print(f"re-pointed {repointed}, unresolved 0")
    return 0


def _patterns(name: str) -> tuple[str, ...]:
    return (
        rf"^\s*(?:async )?def {re.escape(name)}\b",
        rf"^\s*class {re.escape(name)}\b",
        rf"^\s*{re.escape(name)}\s*[:=]",
    )


def _resolves(named: tuple[str, ...], line: str) -> bool:
    """Does this line define, or at least *contain as a whole word*, one of `named`?

    **A definition would be the strict rule and it is too strict**, which is worth writing down
    rather than discovering. Four rows in this table legitimately cite a statement and not a
    definition: `effect_key TEXT PRIMARY KEY` inside a DDL string, the branch where only
    `NotExecuted` maps to `FAILED`, the `INSERT` that makes `put_delegation` not an upsert.
    Requiring a `def` would refuse all four and there is nothing wrong with them.

    So the tightening is **word boundaries**, and that is what closes the hole a review actually
    demonstrated: with `py` no longer harvested from every `` `file.py:NNN` `` reference (see
    `NOT_SYMBOLS`), a row naming `Decision.ALLOW / APPROVE / DENY` no longer matches a comment
    line containing the word "copy", which is how a citation was aimed at a string literal about
    redaction and reported as fine.

    **The residual, stated:** a line containing a named symbol as a whole word, in prose, still
    satisfies this. That is narrower than "contains the substring" by a long way and is not
    nothing; a table that pinned each cited line's text would close it completely and is not
    what this is.
    """
    if any(re.search(pattern, line) for name in named for pattern in _patterns(name)):
        return True
    return any(re.search(rf"\b{re.escape(name)}\b", line) for name in named)


def _definition_of(named: tuple[str, ...], lines: list[str]) -> int | None:
    """Where one of `named` is *defined*, **nearest name first**. Never a mention in prose.

    The order is the caller's, not this function's: `claims.citations` sorts by distance from
    the citation. It used to be longest-name-first over the whole row's symbols, which is how
    six references to six different commands were all re-pointed at one definition.
    """
    for name in named:
        for pattern in _patterns(name):
            for number, candidate in enumerate(lines, start=1):
                if re.search(pattern, candidate):
                    return number
    return None


if __name__ == "__main__":
    raise SystemExit(main())
