"""Which symbol each `file.py:NNN` citation in `docs/CLAIMS.md` belongs to.

One producer, imported by both `scripts/repoint-claims.py`, which **writes** the line numbers,
and `test_the_claims_table_line_numbers_point_at_what_they_name`, which **checks** them. They
disagreed, and the disagreement was invisible in exactly the way this repository keeps finding:

    | ... | `approve` -- `cli/main.py:295`; `receipts` -- `cli/main.py:357`;
            `effects` -- `cli/main.py:431`; `resolve` -- `cli/main.py:449`; ... |

Both sides collected the symbols of the **whole row** and resolved every citation against the
set. So the repointer sent all six references to one line -- whichever definition it happened to
find first -- and the guard accepted them, because that line does define a symbol the row names.
A row claiming to cite six commands cited one, six times, and the check said it was fine.

The fix is that a citation belongs to the symbols **near it**, not to the row. `citations` pairs
each reference with the identifiers around it, nearest first, so `cli/main.py:295` is resolved
against `approve` and not against `stats`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: `cli/main.py:400` as well as `state.py:400`. A first version matched no path with a directory
#: in it, so every `cli/main.py` reference was invisible to the guard -- and a review found two
#: of them stale, one broken by the very commit that added the guard to the rows beside it.
REFERENCE = re.compile(r"`((?:[a-z_]+/)*[a-z_]+\.py):(\d+)`")

#: A backtick-quoted span, and every identifier inside it. Matching only the identifier that
#: begins a span missed the second word of `` `ctrlrun delegate` ``, so a citation aimed at
#: `def delegate` owned only the token `ctrlrun` and looked stale when it was not.
SPAN = re.compile(r"`([^`]+)`")
IDENTIFIER = re.compile(r"[A-Za-z_][\w.]*")

#: Extensions and other tokens a citation contributes that are never symbols. Without this,
#: **32 of 40 rows** carried the bare token `py`, so any line containing `copy`, `pyyaml` or
#: `python` satisfied them.
NOT_SYMBOLS = frozenset({"py", "md", "yaml", "yml", "json", "sql", "toml"})


@dataclass(frozen=True)
class Citation:
    """One `file.py:NNN` reference, and the symbols it is about."""

    filename: str
    line: int
    #: Where the citation sits in the row, so a rewrite can be positional rather than a global
    #: `str.replace` of its text -- two citations to the same `file.py:NNN` in one row must be
    #: able to move independently.
    start: int
    end: int
    #: Nearest first. A resolver should try them in this order and stop at the first that
    #: resolves, so a row citing several files sends each reference to its own symbol.
    names: tuple[str, ...]
    #: The symbols **this** citation owns: those not separated from it by another citation.
    #: A row citing six commands gives each reference only the names beside it, so a line that
    #: defines a *different* command in the same row no longer satisfies it. `names` stays the
    #: whole row, nearest first, because re-pointing a stale reference needs somewhere to look
    #: when the local names are all statements rather than definitions.
    local: tuple[str, ...]


def _bare(name: str) -> str:
    """`Control.delegate` names `delegate`: the table writes the qualified name, the source
    defines the last segment."""
    return name.split(".")[-1]


def citations(row: str) -> list[Citation]:
    """Every citation in one table row, each paired with the symbols nearest to it.

    Distance is measured in characters from the reference to the identifier, so a symbol
    immediately before a reference beats one at the other end of the row. Both directions
    count: a row may write `` `plan_reservation` -- `effect.py:163` ... and `control.py:1036`
    (only `NotExecuted` maps to `FAILED`) ``, where the symbol that identifies the second
    reference comes **after** it. Requiring the symbol to precede the citation would refuse
    that row, and it is a legitimate one.
    """
    # Spans a reference occupies, so `cli` is not harvested as a symbol out of
    # `` `cli/main.py:295` ``. `NOT_SYMBOLS` catches the extension; nothing caught the
    # directory, and a stray `cli` sorts nearest to every citation in the row.
    spans = [(m.start(), m.end()) for m in REFERENCE.finditer(row)]
    symbols = []
    for span in SPAN.finditer(row):
        if any(start <= span.start() < end for start, end in spans):
            continue  # the span *is* a `file.py:NNN` citation, not a list of symbols
        for found in IDENTIFIER.finditer(span.group(1)):
            name = _bare(found.group(0))
            if name not in NOT_SYMBOLS:
                symbols.append((span.start(1) + found.start(), name))
    found = []
    for reference in REFERENCE.finditer(row):
        at = reference.start()
        nearest = tuple(
            name for _, name in sorted(symbols, key=lambda pair: (abs(pair[0] - at), pair[0]))
        )
        # `dict.fromkeys` rather than a set: the order is the whole point.
        ordered = tuple(dict.fromkeys(nearest))
        owned = tuple(
            dict.fromkeys(
                name
                for position, name in symbols
                # A symbol belongs to the citation it is closest to. Splitting on the *next*
                # citation's start instead gave `` `stats` -- `cli/main.py:658` `` to the
                # reference before it, which is the one it is not about.
                if min(spans, key=lambda span: abs(position - span[0]))[0] == reference.start()
            )
        )
        found.append(
            Citation(
                reference.group(1),
                int(reference.group(2)),
                reference.start(),
                reference.end(),
                ordered,
                owned,
            )
        )
    return found
