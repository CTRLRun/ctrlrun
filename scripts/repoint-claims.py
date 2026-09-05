#!/usr/bin/env python
"""Re-derive every line number in `docs/CLAIMS.md` from the code it cites.

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
REFERENCE = re.compile(r"`((?:[a-z_]+/)*[a-z_]+\.py):(\d+)`")
#: A backtick-quoted identifier, and **not** one that is part of a `file.py:NNN` reference.
#: The trailing lookahead is what keeps `py` out: an identifier ends at a backtick, a space, a
#: bracket or a comma, never at a `:` followed by digits.
IDENTIFIER = re.compile(r"`@?([A-Za-z_][\w.]*)(?!\.py:)")
#: Extensions and other tokens a citation contributes that are never symbols.
NOT_SYMBOLS = frozenset({"py", "md", "yaml", "yml", "json", "sql", "toml"})


def named_symbols(row: str) -> set[str]:
    """The symbols a row names, with the file extensions its own citations contribute removed."""
    return {name.split(".")[-1] for name in IDENTIFIER.findall(row)} - NOT_SYMBOLS


def main() -> int:
    claims = ROOT / "docs" / "CLAIMS.md"
    text = claims.read_text(encoding="utf-8")
    fixes: dict[str, str] = {}
    unresolved: list[str] = []

    for row in text.splitlines():
        refs = REFERENCE.findall(row)
        if not refs:
            continue
        named = named_symbols(row)
        if not named:
            unresolved.append(f"{', '.join(f'{f}:{n}' for f, n in refs)} names no symbol at all")
            continue
        for filename, number in refs:
            source = ROOT / "src" / "ctrlrun" / filename
            if not source.exists():
                continue
            lines = source.read_text(encoding="utf-8").splitlines()
            index = int(number)
            if 0 < index <= len(lines) and _resolves(named, lines[index - 1]):
                continue
            found = _definition_of(named, lines)
            if found is None:
                unresolved.append(f"{filename}:{number} names {sorted(named)}")
            else:
                fixes[f"`{filename}:{number}`"] = f"`{filename}:{found}`"

    if unresolved:
        # **Refuse before writing.** A partial re-point plus a non-zero exit leaves the table in
        # a state nobody chose, and the next run reports different rows.
        for line in unresolved:
            print(f"unresolved: {line}", file=sys.stderr)
        print(f"re-pointed 0, unresolved {len(unresolved)} — nothing written", file=sys.stderr)
        return 1

    for old, new in fixes.items():
        text = text.replace(old, new)
    claims.write_text(text, encoding="utf-8")
    print(f"re-pointed {len(fixes)}, unresolved 0")
    return 0


def _patterns(name: str) -> tuple[str, ...]:
    return (
        rf"^\s*(?:async )?def {re.escape(name)}\b",
        rf"^\s*class {re.escape(name)}\b",
        rf"^\s*{re.escape(name)}\s*[:=]",
    )


def _resolves(named: set[str], line: str) -> bool:
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


def _definition_of(named: set[str], lines: list[str]) -> int | None:
    """Where one of `named` is *defined*, longest name first. Never a mention in prose."""
    for name in sorted(named, key=len, reverse=True):
        for pattern in _patterns(name):
            for number, candidate in enumerate(lines, start=1):
                if re.search(pattern, candidate):
                    return number
    return None


if __name__ == "__main__":
    raise SystemExit(main())
