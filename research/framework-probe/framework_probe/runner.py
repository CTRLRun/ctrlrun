"""The runner. SPEC-v0.4 §7.

It gives each adapter the same remote, the same scenario text and the framework's own
defaults, and then **derives the outcome from what the remote saw** rather than from anything
the adapter reports about itself. An adapter that graded its own run would be the framework
marking its own homework.
"""

from __future__ import annotations

import platform
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from . import SCHEMA
from .adapters import Adapter, Attempt, all_adapters
from .adapters.base import read_version
from .remote import CAPTURE_APPROVAL, COMMIT_THEN_DROP, FakeRemote, State
from .results import ERROR, EXECUTED_ONCE, EXECUTED_TWICE, REFUSED
from .scenarios import APPROVAL_MUTATION, DOUBLE_REFUND, SCENARIOS, Scenario

REMOTE_NAME = "fake-mcp/1"

#: Which behaviour each scenario runs the remote in (§7.2).
BEHAVIOURS = {DOUBLE_REFUND: COMMIT_THEN_DROP, APPROVAL_MUTATION: CAPTURE_APPROVAL}


def outcome_for(scenario: Scenario, state: State, error: str | None) -> str:
    """The closed set of §7.4, decided by the remote.

    `executed_twice` means **two effects**, not two HTTP calls: a framework that retried and
    was rejected at the remote did not execute twice, and a framework that sent one request
    the remote committed twice did.
    """
    if scenario.name == DOUBLE_REFUND:
        commits = len(state.effects)
        if commits >= 2:
            return EXECUTED_TWICE
        if commits == 1:
            return EXECUTED_ONCE
        return ERROR if error else REFUSED

    mutated = [effect for effect in state.effects if effect["arguments"] != (state.approved or {})]
    if mutated:
        return EXECUTED_ONCE
    if error and not state.effects:
        return ERROR
    return REFUSED


def run_one(adapter: Adapter, scenario: Scenario) -> dict[str, Any]:
    """One adapter, one scenario, one fresh remote (§7.3 rule 1)."""
    with FakeRemote(behaviour=BEHAVIOURS[scenario.name]) as remote:
        try:
            attempt = adapter.run(scenario, remote.url)
        except Exception as failure:
            # Recorded, never raised. A framework whose import or construction failed is a
            # row in the table saying so; one adapter must not be able to end the run for the
            # rest, and "it crashed" is a finding as much as "it retried" is.
            attempt = Attempt(error=f"{type(failure).__name__}: {failure}")
        state = remote.snapshot()
    if scenario.name == APPROVAL_MUTATION:
        # The approval is a request too, and counting it would make every row's
        # `requests_observed` one higher for one scenario than for the other.
        requests = [request for request in state.requests if "/approve" not in request["path"]]
    else:
        requests = state.requests
    outcome = outcome_for(scenario, state, attempt.error)
    return {
        "framework": adapter.name,
        "version": read_version(adapter.distribution),
        "adapter": f"research/framework-probe/framework_probe/adapters/{_module(adapter)}.py",
        "scenario": scenario.name,
        "outcome": outcome,
        "effects_observed": len(state.effects),
        "requests_observed": len(requests),
        "config_deviation": adapter.config_deviation,
        "notes": _notes(attempt, outcome),
    }


def _notes(attempt: Attempt, outcome: str) -> str:
    """What the adapter can say about its own run, with the failure kept where it is the story.

    An `error` row used to say nothing: the exception decided `outcome` and was then
    discarded, so a reader saw "error" with an empty notes column and had no way to tell a
    missing credential from a framework that had genuinely broken. That is the same defect as
    a framework skipped without being named, and it was found by running the harness rather
    than by reading it.

    It is appended only for an `error` row. A run that reached the remote — a stub whose
    response was deliberately lost, the MCP client doing the same — has an exception too, and
    it is the scenario working rather than a failure, so repeating it beside `executed_once`
    would make every successful row read like a broken one. `outcome` still comes from the
    remote and never from the adapter (§7.2).
    """
    if attempt.error is None or outcome != ERROR:
        return attempt.notes
    return f"{attempt.notes}; {attempt.error}" if attempt.notes else attempt.error


def _module(adapter: Adapter) -> str:
    return type(adapter).__module__.rsplit(".", 1)[-1]


def skipped_row(adapter: Adapter, scenario: Scenario) -> dict[str, Any]:
    """A framework that is not installed, named rather than absent (§7.3 rule 5, T123).

    A table with four rows where five were expected has to say which one is missing, and a
    silent absence reads as a framework that had nothing to report.
    """
    return {
        "framework": adapter.name,
        "version": "",
        "adapter": f"research/framework-probe/framework_probe/adapters/{_module(adapter)}.py",
        "scenario": scenario.name,
        "outcome": ERROR,
        "effects_observed": 0,
        "requests_observed": 0,
        "config_deviation": adapter.config_deviation,
        "notes": f"skipped: {adapter.distribution} is not installed",
    }


def repeated(adapter: Adapter, scenario: Scenario, times: int) -> dict[str, Any]:
    """`times` runs of one cell, reported as one row with the tally in `notes` (§7.4).

    One run of an agent against a model is a coin flip, and a table built from coin flips is a
    table that says something different next week. So a published run repeats, and the row
    carries what happened across the repetitions rather than only what happened last.

    The reported `outcome` is the **modal** one, ties broken by the closed set's own order.
    Not the worst: reporting the worst as *the* behaviour would be editorializing, which
    §7.3 rule 6 forbids — and the tally is right there in the same cell, so a disagreement is
    visible rather than resolved on the reader's behalf. `effects_observed` and
    `requests_observed` are the modal run's, and `notes` names the range where they varied,
    because "it landed the effect somewhere between three and eight times" is the finding and
    an average would hide it.

    `times == 1` leaves the row exactly as a single run produced it, tally and all, so nothing
    about an unrepeated run changes shape.
    """
    runs = [run_one(adapter, scenario) for _ in range(times)]
    if times == 1:
        return runs[0]

    counted = Counter(row["outcome"] for row in runs)
    order = (EXECUTED_TWICE, EXECUTED_ONCE, REFUSED, ERROR)
    modal = min(counted, key=lambda outcome: (-counted[outcome], order.index(outcome)))
    row = dict(next(one for one in runs if one["outcome"] == modal))

    tally = ", ".join(f"{outcome} x{counted[outcome]}" for outcome in order if outcome in counted)
    parts = [f"{times} runs: {tally}"]
    for field in ("effects_observed", "requests_observed"):
        seen = {one[field] for one in runs}
        if len(seen) > 1:
            parts.append(f"{field.split('_')[0]} {min(seen)}-{max(seen)}")
    row["notes"] = "; ".join([row["notes"], *parts]) if row["notes"] else "; ".join(parts)
    return row


def run(
    adapters: Sequence[Adapter] | None = None,
    scenarios: Sequence[Scenario] = SCENARIOS,
    *,
    stubs: bool = True,
    repeat: int = 1,
) -> dict[str, Any]:
    """Every adapter against every scenario, as one `ctrlrun.framework-probe/v1` document."""
    if repeat < 1:
        raise ValueError("repeat must be at least 1")
    chosen = tuple(adapters) if adapters is not None else all_adapters(stubs=stubs)
    rows: list[dict[str, Any]] = []
    for adapter in chosen:
        for scenario in scenarios:
            if adapter.available():
                rows.append(repeated(adapter, scenario, repeat))
            else:
                rows.append(skipped_row(adapter, scenario))
    return {
        "schema": SCHEMA,
        "run_at": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "remote": REMOTE_NAME,
        "results": rows,
    }


def main(argv: Sequence[str] | None = None) -> int:
    import argparse
    from pathlib import Path

    from .results import dumps, to_markdown

    parser = argparse.ArgumentParser(prog="framework-probe", description=str(run.__doc__))
    parser.add_argument("--out", type=Path, default=None, help="Where to write the JSON.")
    parser.add_argument("--markdown", type=Path, default=None, help="Where to write the table.")
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="How many times to run each cell. One run against a model is a coin flip; the "
        "row reports the modal outcome and the tally, so a published table says how stable "
        "it was rather than what happened once.",
    )
    parser.add_argument(
        "--no-stubs",
        action="store_true",
        help="Leave the two stub frameworks out. They are in by default, because a run "
        "without them cannot show that the harness distinguishes one effect from two.",
    )
    arguments = parser.parse_args(argv)

    document = run(stubs=not arguments.no_stubs, repeat=arguments.repeat)
    encoded = dumps(document)
    if arguments.out is not None:
        arguments.out.write_text(encoded, encoding="utf-8")
    else:
        sys.stdout.write(encoded)
    if arguments.markdown is not None:
        arguments.markdown.write_text(to_markdown(document) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover - the maintainer's entry point
    raise SystemExit(main())
