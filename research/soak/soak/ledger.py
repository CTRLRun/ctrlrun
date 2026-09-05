"""What the harness injected, and what it found. SPEC-v0.6 §8.1.

The ledger is the whole of the definition of "unexplained", so it is a separate module with its
own tests rather than a dictionary inside the runner: a criterion that lives in a comprehension
is a criterion nobody can check.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

#: Every way this harness knows how to produce an `AMBIGUOUS` outcome. A cause not in this set is
#: a defect in the harness, not a finding about the kernel -- so `classify` refuses one rather
#: than counting it as explained.
CAUSES: tuple[str, ...] = (
    "executor_timeout",
    "executor_unknown_exception",
    "lease_expired",
    "store_connection_lost",
    #: The positive control. Injected and **deliberately not recorded**, so the run must report
    #: it as unexplained. See `soak.__doc__`.
    "uncontrolled",
)

EXPLAINED: frozenset[str] = frozenset(CAUSES) - {"uncontrolled"}


@dataclass(frozen=True)
class Injection:
    """One failure the harness caused, recorded **before** it was caused."""

    at: datetime
    effect_key: str
    action_id: str
    cause: str


@dataclass(frozen=True)
class Ambiguity:
    """One `AMBIGUOUS` record the run found, and what the store says about it."""

    effect_key: str
    action_id: str
    error: str | None
    resolved_by: str | None


@dataclass(frozen=True)
class Finding:
    """An `AMBIGUOUS` with no injection behind it. This is what the exit criterion counts."""

    effect_key: str
    action_id: str
    error: str | None
    resolved_by: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "effect_key": self.effect_key,
            "action_id": self.action_id,
            "error": self.error,
            "resolved_by": self.resolved_by,
        }


class Ledger:
    """A SQLite file of what was injected. Separate from the store under test, on purpose.

    Writing the ledger into the store the soak is exercising would make the harness a participant
    in the thing it measures -- its rows would contend for the same locks, and a store failure
    would take the evidence of that failure with it.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS injections("
                "  at TEXT NOT NULL,"
                "  effect_key TEXT NOT NULL,"
                "  action_id TEXT NOT NULL,"
                "  cause TEXT NOT NULL"
                ")"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS injections_by_key ON injections(effect_key)"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def record(self, injection: Injection) -> None:
        """Note an injection. Called **before** the failure is caused (§8.1)."""
        if injection.cause not in CAUSES:
            raise ValueError(f"unknown cause {injection.cause!r}; add it to CAUSES or fix the call")
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO injections(at, effect_key, action_id, cause) VALUES(?,?,?,?)",
                (
                    injection.at.isoformat(),
                    injection.effect_key,
                    injection.action_id,
                    injection.cause,
                ),
            )

    def injections(self) -> Iterator[Injection]:
        with self._connect() as connection:
            for row in connection.execute(
                "SELECT at, effect_key, action_id, cause FROM injections ORDER BY at"
            ):
                yield Injection(
                    at=datetime.fromisoformat(row[0]),
                    effect_key=row[1],
                    action_id=row[2],
                    cause=row[3],
                )

    def explained_keys(self) -> set[tuple[str, str]]:
        """`(effect_key, action_id)` pairs with a recorded, explaining injection.

        Keyed on the **attempt** and not on the effect key alone: one key may be attempted more
        than once, and an injection against attempt 1 explains nothing about attempt 2.
        """
        return {
            (injection.effect_key, injection.action_id)
            for injection in self.injections()
            if injection.cause in EXPLAINED
        }


def classify(ambiguities: list[Ambiguity], ledger: Ledger) -> list[Finding]:
    """Every `AMBIGUOUS` with no recorded injection behind it (§8.1).

    This is the exit criterion, and it is deliberately the whole of it: `ROADMAP.md` asks for a
    week with **no** unexplained `AMBIGUOUS`, so a non-empty return is a finding to investigate
    and not a number to publish beside a green tick.
    """
    explained = ledger.explained_keys()
    return [
        Finding(
            effect_key=item.effect_key,
            action_id=item.action_id,
            error=item.error,
            resolved_by=item.resolved_by,
        )
        for item in ambiguities
        if (item.effect_key, item.action_id) not in explained
    ]


def report(
    *,
    started: datetime,
    ended: datetime,
    actions: int,
    ambiguities: list[Ambiguity],
    findings: list[Finding],
    control_fired: bool,
    backend: str,
) -> dict[str, object]:
    """The published table (§8.1). The duration is measured, never asserted."""
    elapsed = ended - started
    return {
        "schema": "ctrlrun.soak/v1",
        "backend": backend,
        "started_at": started.astimezone(UTC).isoformat(),
        "ended_at": ended.astimezone(UTC).isoformat(),
        "elapsed_seconds": round(elapsed.total_seconds(), 3),
        "elapsed_human": _human(elapsed.total_seconds()),
        "actions": actions,
        "ambiguous": len(ambiguities),
        "explained": len(ambiguities) - len(findings),
        "unexplained": len(findings),
        "findings": [finding.to_dict() for finding in findings],
        # §8.1: *"a harness that reports zero without being able to see one is reporting that it
        # looked."* A table whose control did not fire is not evidence and says so in itself.
        "positive_control_fired": control_fired,
        "exit_criterion_met": control_fired and not findings,
    }


def _human(seconds: float) -> str:
    minutes, second = divmod(int(seconds), 60)
    hours, minute = divmod(minutes, 60)
    days, hour = divmod(hours, 24)
    if days:
        return f"{days}d {hour}h {minute}m"
    if hour:
        return f"{hour}h {minute}m {second}s"
    return f"{minute}m {second}s"


def render(document: dict[str, object]) -> str:
    """The table a human reads, and the one the maintainer reads before it goes in a PR."""
    lines = [
        f"CTRLRun soak — {document['backend']}",
        f"  ran            {document['elapsed_human']} "
        f"({document['started_at']} → {document['ended_at']})",
        f"  actions        {document['actions']}",
        f"  ambiguous      {document['ambiguous']}"
        f"  ({document['explained']} explained, {document['unexplained']} unexplained)",
        f"  control fired  {'yes' if document['positive_control_fired'] else 'NO'}",
        "",
    ]
    if not document["positive_control_fired"]:
        lines.append(
            "  The positive control did not fire. This run cannot see an unexplained ambiguity, "
            "so its count of zero would mean nothing (SPEC-v0.6 §8.1)."
        )
    for finding in document["findings"]:  # type: ignore[union-attr]
        lines.append(f"  UNEXPLAINED  {finding['effect_key']}  {finding['error']}")
    if document["exit_criterion_met"]:
        lines.append("  no unexplained ambiguity")
    return "\n".join(lines)


def to_json(document: dict[str, object]) -> str:
    return json.dumps(document, indent=2, sort_keys=True)
