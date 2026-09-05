#!/usr/bin/env python
"""Re-derive every line number in `docs/CLAIMS.md` from the code it cites.

The table cites `file.py:NNN`, and `test_the_claims_table_line_numbers_point_at_what_they_name`
requires the cited line to contain one of the symbols the row names. Every commit that shifts a
line in `policy.py`, `state.py`, `control.py` or `receipt.py` breaks some of them, and doing it
by hand is how a row ends up pointing at a docstring that happens to contain the right word --
which makes the guard green and the claim false. Item 9 regenerates the table wholesale; this is
for the commits in between.

**It refuses rather than guesses.** A reference it cannot resolve to a `def`, a `class` or an
assignment is reported and left alone, because a row whose symbol has no definition is a row
somebody has to read.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = re.compile(r"`((?:[a-z_]+/)*[a-z_]+\.py):(\d+)`")
IDENTIFIER = re.compile(r"`@?([A-Za-z_][\w.]*)")


def main() -> int:
    claims = ROOT / "docs" / "CLAIMS.md"
    text = claims.read_text(encoding="utf-8")
    fixes: dict[str, str] = {}
    unresolved: list[str] = []

    for row in text.splitlines():
        refs = REFERENCE.findall(row)
        if not refs:
            continue
        named = {name.split(".")[-1] for name in IDENTIFIER.findall(row)}
        for filename, number in refs:
            source = ROOT / "src" / "ctrlrun" / filename
            if not source.exists():
                continue
            lines = source.read_text(encoding="utf-8").splitlines()
            index = int(number)
            if 0 < index <= len(lines) and any(name in lines[index - 1] for name in named):
                continue
            found = _definition_of(named, lines)
            if found is None:
                unresolved.append(f"{filename}:{number} names {sorted(named)}")
            else:
                fixes[f"`{filename}:{number}`"] = f"`{filename}:{found}`"

    for old, new in fixes.items():
        text = text.replace(old, new)
    claims.write_text(text, encoding="utf-8")

    for line in unresolved:
        print(f"unresolved: {line}", file=sys.stderr)
    print(f"re-pointed {len(fixes)}, unresolved {len(unresolved)}")
    return 1 if unresolved else 0


def _definition_of(named: set[str], lines: list[str]) -> int | None:
    """Where one of `named` is *defined*, longest name first. Never a mention in prose."""
    for name in sorted(named, key=len, reverse=True):
        for pattern in (
            rf"^\s*(?:async )?def {re.escape(name)}\b",
            rf"^\s*class {re.escape(name)}\b",
            rf"^\s*{re.escape(name)}\s*[:=]",
        ):
            for number, candidate in enumerate(lines, start=1):
                if re.search(pattern, candidate):
                    return number
    return None


if __name__ == "__main__":
    raise SystemExit(main())
