#!/usr/bin/env python
"""Run the v0.6 soak and print its table. SPEC-v0.6 §8.1.

    python research/soak/run.py --minutes 30 --postgres "$CTRLRUN_TEST_POSTGRES"
    python research/soak/run.py --minutes 5                      # SQLite, for a smoke run

**The duration is measured and printed, never asserted.** `ROADMAP.md`'s exit criterion is a week
with no unexplained `AMBIGUOUS`, and a run shorter than that does not meet it — the table says how
long it actually ran and `exit_criterion_met` is about the ambiguity count, not the clock. A
harness that reported "criterion met" after thirty minutes would be making the one claim §8.1
says would be exactly as false as it looks.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from soak.ledger import Ledger, classify, render, report, to_json
from soak.runner import (
    Runner,
    ambiguities_in,
    postgres_store,
    sqlite_store,
)


def _control_sees_an_unrecorded_ambiguity(work: Path, seed: int) -> bool:
    """Prove the harness can detect an unexplained ambiguity, in its own store (§8.1).

    Without this, "zero unexplained" is a report that the harness looked, not that there was
    nothing to find -- `v0.4 §1.3`'s rule, and the reason it is required rather than encouraged.
    """
    import tempfile

    with tempfile.TemporaryDirectory(dir=work) as scratch:
        room = Path(scratch)
        ledger = Ledger(room / "control-ledger.db")
        runner = Runner(
            open_store=sqlite_store(room / "control-state.db"),
            ledger=ledger,
            seed=seed,
            control=True,
        )
        runner.run(until=datetime.now(UTC) + timedelta(seconds=5), workers=2)
        store = sqlite_store(room / "control-state.db")()
        found = ambiguities_in(store)
        close = getattr(store, "close", None)
        if close is not None:
            close()
        return bool(classify(found, ledger))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The v0.6 soak (SPEC-v0.6 §8.1).")
    parser.add_argument("--minutes", type=float, default=5.0, help="How long to run.")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--postgres", default=None, help="A Postgres URL. Default: SQLite.")
    parser.add_argument("--out", type=Path, default=None, help="Write the JSON table here.")
    parser.add_argument(
        "--no-control",
        action="store_true",
        help="Disable the positive control. The table then says so and is not evidence.",
    )
    arguments = parser.parse_args(argv)

    work = Path(arguments.out).parent if arguments.out else Path.cwd()
    work.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(work / "soak-ledger.db")

    schema = None
    if arguments.postgres:
        from ctrlrun.postgres import PostgresStateStore

        schema = f"soak_{uuid.uuid4().hex[:10]}"
        PostgresStateStore.create_schema(arguments.postgres, schema)
        opened = postgres_store(arguments.postgres, schema)
        backend = f"postgres (schema {schema})"
    else:
        opened = sqlite_store(work / "soak-state.db")
        backend = "sqlite"

    # **Two phases, and the control comes first** (`v0.4 §1.3`).
    #
    # The control injects an ambiguity it deliberately does not record, and the harness must
    # report it. It runs in its own store and its own ledger, so the thing it proves -- that an
    # unexplained ambiguity is *visible* -- does not become an unexplained ambiguity in the
    # measured run. A first version ran the control inside the measured phase, which made
    # `exit_criterion_met` unreachable: the run could prove it could see, or report zero, never
    # both.
    control_fired = False
    if not arguments.no_control:
        control_fired = _control_sees_an_unrecorded_ambiguity(work, arguments.seed)
        if not control_fired:
            print(
                "the positive control did not fire: this harness cannot see an unexplained "
                "ambiguity, so nothing it reports about one is evidence (SPEC-v0.6 §8.1)",
                file=sys.stderr,
            )

    started = datetime.now(UTC)
    runner = Runner(open_store=opened, ledger=ledger, seed=arguments.seed, control=False)
    try:
        runner.run(until=started + timedelta(minutes=arguments.minutes), workers=arguments.workers)
        ended = datetime.now(UTC)
        store = opened()
        found = ambiguities_in(store)
        close = getattr(store, "close", None)
        if close is not None:
            close()
    finally:
        if schema:
            from ctrlrun.postgres import PostgresStateStore

            PostgresStateStore.drop_schema(arguments.postgres, schema)

    findings = classify(found, ledger)
    document = report(
        started=started,
        ended=ended,
        actions=runner.progress.actions,
        ambiguities=found,
        findings=findings,
        control_fired=control_fired,
        backend=backend,
    )
    print(render(document))
    if arguments.out:
        Path(arguments.out).write_text(to_json(document) + "\n", encoding="utf-8")

    # Non-zero when the run found something or could not have found anything. Not when the run
    # was short: the clock is the maintainer's call, and §8.1 says item 9 does not tag until
    # item 8 reports.
    return 0 if document["exit_criterion_met"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
