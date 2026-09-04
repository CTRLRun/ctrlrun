"""The result document and the table rendered from it. SPEC-v0.4 §7.4.

`outcome` is a **closed set**. The Markdown table is rendered from the JSON and never written
by hand, so a row nobody measured cannot appear in it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from . import SCHEMA

EXECUTED_ONCE = "executed_once"
EXECUTED_TWICE = "executed_twice"
REFUSED = "refused"
ERROR = "error"
OUTCOMES = (EXECUTED_ONCE, EXECUTED_TWICE, REFUSED, ERROR)

#: Every key a result row carries. `config_deviation` is present on every row — `null` where
#: there was none — because §7.3 rule 4 puts a deviation in the **table** and an absent key
#: reads as an absent deviation only to somebody who already knew the rule.
ROW_KEYS = (
    "framework",
    "version",
    "adapter",
    "scenario",
    "outcome",
    "effects_observed",
    "requests_observed",
    "config_deviation",
    "notes",
)


class InvalidResults(ValueError):
    """The document is not `ctrlrun.framework-probe/v1`."""


def validate(document: Mapping[str, Any]) -> None:
    """Raise unless `document` is a well-formed result file (§7.4)."""
    if document.get("schema") != SCHEMA:
        raise InvalidResults(f"schema is {document.get('schema')!r}, expected {SCHEMA!r}")
    for key in ("run_at", "python", "remote", "results"):
        if key not in document:
            raise InvalidResults(f"missing {key!r}")
    rows = document["results"]
    if not isinstance(rows, list):
        raise InvalidResults("'results' must be a list")
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise InvalidResults(f"results[{index}] is not an object")
        missing = [key for key in ROW_KEYS if key not in row]
        if missing:
            raise InvalidResults(f"results[{index}] is missing {missing}")
        if row["outcome"] not in OUTCOMES:
            raise InvalidResults(
                f"results[{index}]: outcome {row['outcome']!r} is not one of {OUTCOMES}"
            )
        deviation = row["config_deviation"]
        if deviation is not None and not isinstance(deviation, str):
            raise InvalidResults(f"results[{index}]: config_deviation must be null or a string")


#: How much of a note reaches the table. Long enough for a sentence a reader can act on, short
#: enough that one row stays one row.
NOTE_LIMIT = 200


def cell(value: str) -> str:
    r"""One table cell, safe to render whoever wrote the string.

    `notes` used to be written only by the harness's own authors. It now carries a measured
    framework's exception text (`runner._notes`), and a `|` or a newline in one of those does
    not make an ugly cell — it makes a **different table**: the row gains a phantom column and
    then terminates, and everything after it becomes body text. A table that a framework's
    error message can restructure is not "rendered from the JSON" in any sense that matters.

    Whitespace collapses, `|` and `\` are escaped, and the result is truncated. The JSON keeps
    the full string; this is the rendering.
    """
    collapsed = " ".join(str(value).split())
    escaped = collapsed.replace("\\", "\\\\").replace("|", "\\|")
    if len(escaped) <= NOTE_LIMIT:
        return escaped
    return escaped[: NOTE_LIMIT - 1].rstrip() + "…"


def _notes_for(notes: Sequence[tuple[str, str]]) -> str:
    """One cell for a framework's notes, labelled by scenario where the two differ.

    Identical notes collapse: one note repeated for both scenarios says nothing twice. Where
    they differ they are prefixed, because with per-scenario detail in them — a repetition
    tally, an effect range — an unlabelled `a; b` leaves a reader guessing which row each
    belongs to, and a table that has to be guessed at is one that will be quoted wrongly.
    """
    distinct = {note for _, note in notes}
    if len(distinct) <= 1:
        return next(iter(distinct), "")
    return " | ".join(f"{scenario}: {note}" for scenario, note in notes)


def to_markdown(document: Mapping[str, Any]) -> str:
    """The table §7.4 describes, rendered from the document and never stored.

    One row per framework, both scenarios side by side, and `config_deviation` as a column —
    not a footnote, not prose. A deviation a reader has to go looking for is one they will not
    find.

    Every cell goes through `cell()`: some of what lands here was written by the projects the
    table is about, and a table their exception text can reshape would be a table nobody can
    trust to say what the JSON says.
    """
    validate(document)
    rows: Sequence[Mapping[str, Any]] = document["results"]
    frameworks: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = frameworks.setdefault(
            row["framework"],
            {"version": row["version"], "deviation": None, "notes": [], "scenarios": {}},
        )
        entry["scenarios"][row["scenario"]] = row["outcome"]
        if row["config_deviation"]:
            entry["deviation"] = row["config_deviation"]
        if row["notes"]:
            entry["notes"].append((row["scenario"], row["notes"]))

    lines = [
        f"<!-- Rendered from {document['run_at']} by framework_probe.results.to_markdown. "
        "Do not edit by hand. -->",
        "",
        "| framework | version | double-refund | approval-mutation | config_deviation | notes |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for name in sorted(frameworks):
        entry = frameworks[name]
        scenarios = entry["scenarios"]
        lines.append(
            f"| {cell(name)} | {cell(entry['version'])} | "
            f"{cell(scenarios.get('double-refund', '-'))} | "
            f"{cell(scenarios.get('approval-mutation', '-'))} | "
            f"{cell(entry['deviation'] or '')} | {cell(_notes_for(entry['notes']))} |"
        )
    lines += [
        "",
        "This table reports **behaviour, not quality**. A framework that retries a lost "
        "response is doing what its documentation says it does; the finding is about what an "
        "agent stack does *without* an effect-level guard.",
    ]
    return "\n".join(lines)


def dumps(document: Mapping[str, Any]) -> str:
    validate(document)
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"
