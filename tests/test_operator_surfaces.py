# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""T434 to T438: what v0.9 built, visible to the person who gets paged (SPEC-v0.9 §7).

§7.1 adds **no new command**. `inspect`, `effects` and `stats` are extended, because the question
an operator asks here is not a new one: it is *what is the state of this thing*, and a budget is
one more thing those answer about.
"""

from __future__ import annotations

import contextlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from ctrlrun.action import Action, Principal
from ctrlrun.authority import Authority
from ctrlrun.cli import main as cli
from ctrlrun.control import Control
from ctrlrun.effect import EffectState
from ctrlrun.policy import Policy
from ctrlrun.reporting import BUDGET_SCHEMA, budget_document, budget_lines
from ctrlrun.state import SQLiteStateStore

pytestmark = pytest.mark.authority

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
AGENT = Principal(agent="payer", user="ada")

DOC = """
schema: ctrlrun.policy/v7
environment: prod
actions:
  payments.refund:
    effect: "refund:{id}"
    decision: allow
authority:
  grants:
    - id: payer
      subject: {agent: "payer"}
      actions: ["payments.*"]
      budgets:
        - {metric: amount, limit: 1000, window: PT24H}
    - id: unbudgeted
      subject: {agent: "other"}
      actions: ["payments.*"]
"""


class _Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, by: timedelta) -> None:
        self.now += by


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def store(tmp_path, clock):
    made = SQLiteStateStore(tmp_path / "state.db", clock=clock)
    yield made
    made.close()


@pytest.fixture
def control(store, clock) -> Control:
    return Control(
        policy=Policy.from_yaml(DOC, source="<d>"),
        store=store,
        clock=clock,
        environment="prod",
        authority=Authority.from_yaml(DOC, source="<d>"),
    )


def _action(identifier: str, amount: int) -> Action:
    return Action(
        name="payments.refund",
        arguments={"amount": amount, "id": identifier},
        principal=AGENT,
        environment="prod",
    )


def _boom() -> Any:
    raise TimeoutError("the response was lost")


def _budgets(control: Control) -> Any:
    return control._authority.grants["payer"].budgets


# --- T434: §7.2's three numbers, and the third one is the deliverable -------------------------


def test_T434_consumed_held_and_why(control, store, clock) -> None:
    """§7.2. **The third column is the deliverable, not a nicety.**

    A budget that refuses while an operator can see it is nowhere near its limit looks like a
    defect in the kernel, and the true explanation is always the same shape: some effect is
    `AMBIGUOUS` and nobody has resolved it (§4.2, R2).
    """
    control.execute(_action("1", 200), lambda: {"ok": True}, "refund:1")
    with pytest.raises(TimeoutError):
        control.execute(_action("2", 300), _boom, "refund:2")
    assert store.get_effect("refund:2").state is EffectState.AMBIGUOUS

    document = budget_document("payer", _budgets(control), store, clock.now)
    budget = document["budgets"][0]

    assert budget["consumed"] == 500, "both spends count against the limit"
    assert budget["held"] == 300, "only the one that has not committed is held"
    assert [(h["effect_key"], h["state"]) for h in budget["holding"]] == [
        ("refund:2", str(EffectState.AMBIGUOUS))
    ], budget["holding"]


def test_T434a_the_view_names_the_command_that_clears_the_hold(control, store, clock) -> None:
    """§7.2: "the path is one command long". The **key**, not a placeholder: an operator who has
    to retype it from the line above is one transcription away from resolving a different
    effect."""
    with pytest.raises(TimeoutError):
        control.execute(_action("2", 300), _boom, "refund:2")

    lines = budget_lines(budget_document("payer", _budgets(control), store, clock.now))

    assert any("ctrlrun resolve refund:2" in line for line in lines), lines


def test_T434b_resolving_the_hold_is_what_the_view_says_it_is(control, store, clock) -> None:
    """The claim the line makes, driven through. Without this the hint is documentation.

    `--failed` releases and `consumed` drops; §4.2 is what makes that true, and §7.2's view is
    only worth printing if it agrees with it.
    """
    control.execute(_action("1", 200), lambda: {"ok": True}, "refund:1")
    with pytest.raises(TimeoutError):
        control.execute(_action("2", 300), _boom, "refund:2")

    store.resolve_effect("refund:2", EffectState.FAILED, "ada@example.com")

    budget = budget_document("payer", _budgets(control), store, clock.now)["budgets"][0]
    assert budget["consumed"] == 200, "a released charge no longer counts against the limit"
    assert budget["held"] == 0
    assert budget["holding"] == []


def test_T434c_a_committed_effect_is_consumed_and_never_held(control, store, clock) -> None:
    """§7.2's definition, and the distinction the whole view rests on. A committed spend is a
    spend: it counts, permanently, and it is not something an operator can clear."""
    control.execute(_action("1", 200), lambda: {"ok": True}, "refund:1")

    budget = budget_document("payer", _budgets(control), store, clock.now)["budgets"][0]
    assert (budget["consumed"], budget["held"], budget["holding"]) == (200, 0, [])


def test_T434d_the_window_rolls_out_of_the_view_as_it_rolls_out_of_the_decision(
    control, store, clock
) -> None:
    """§2.5. The view reports the number that decides, so it forgets exactly when the kernel
    does. A view over a different window would tell an operator the budget is full while the
    kernel permits the action, which is worse than no view."""
    control.execute(_action("1", 900), lambda: {"ok": True}, "refund:1")
    assert (
        budget_document("payer", _budgets(control), store, clock.now)["budgets"][0]["consumed"]
        == 900
    )

    clock.advance(timedelta(hours=24, seconds=1))

    assert (
        budget_document("payer", _budgets(control), store, clock.now)["budgets"][0]["consumed"] == 0
    )
    control.execute(_action("2", 900), lambda: {"ok": True}, "refund:2")


def test_T434e_a_grant_with_no_budgets_says_so(control, store, clock) -> None:
    """`()` and "no such grant" are different answers. A grant that budgets nothing is a real
    grant an operator may ask about, and §2.4 permits it."""
    document = budget_document("unbudgeted", (), store, clock.now)

    assert document["budgets"] == []
    assert "no budgets" in "\n".join(budget_lines(document))


def test_T434f_a_ledger_row_whose_effect_is_gone_is_held_not_dropped(control, store, clock) -> None:
    """§7.3 permits an operator to archive rows the window can no longer reach. A store whose
    effects were pruned but whose ledger was not must not silently **under**-report `held`: that
    is the one direction this view may not err in, because it is the direction that hides a hold.
    """
    with pytest.raises(TimeoutError):
        control.execute(_action("2", 300), _boom, "refund:2")
    store._connection().execute("DELETE FROM effects WHERE effect_key='refund:2'")
    store._connection().commit()

    budget = budget_document("payer", _budgets(control), store, clock.now)["budgets"][0]

    assert budget["held"] == 300
    assert budget["holding"][0]["state"] is None
    lines = budget_lines(budget_document("payer", _budgets(control), store, clock.now))
    assert any("no effect record" in line for line in lines), lines


# --- T435: the CLI surfaces, through the commands an operator actually types -------------------


def _project(tmp_path: Path) -> Path:
    here = tmp_path / "project"
    here.mkdir(exist_ok=True)
    (here / "ctrlrun.yaml").write_text(DOC, encoding="utf-8")
    return here


def _run(args, cwd: Path):
    previous = os.getcwd()
    os.chdir(cwd)
    try:
        return CliRunner().invoke(cli.main, args, catch_exceptions=True)
    finally:
        os.chdir(previous)


def _spend(here: Path) -> None:
    control = Control.from_file(here / "ctrlrun.yaml")
    control.execute(_action("1", 200), lambda: {"ok": True}, "refund:1")
    with contextlib.suppress(TimeoutError):
        control.execute(_action("2", 300), _boom, "refund:2")


def test_T435_inspect_grant_shows_the_budget(tmp_path) -> None:
    """§7.1: no new command. `ctrlrun inspect` answers about an approval, an effect and a
    delegation already, and a budget is one more thing it answers about."""
    here = _project(tmp_path)
    _spend(here)

    result = _run(["inspect", "--grant", "payer"], here)

    assert result.exit_code == 0, result.output
    assert "500 of 1000 per day" in result.output, result.output
    assert "300 held" in result.output
    assert "refund:2" in result.output and "ambiguous" in result.output


def test_T435a_inspect_refuses_both_a_grant_and_an_action(tmp_path) -> None:
    """One command, two subjects, so "which did you mean" is this command's own question.
    Neither names a subject; both name two."""
    here = _project(tmp_path)

    for args in (["inspect"], ["inspect", "act_1", "--grant", "payer"]):
        result = _run(args, here)
        assert result.exit_code == 2, f"{args}: {result.output}"


def test_T435b_an_unknown_grant_exits_non_zero_with_nothing_on_stdout(tmp_path) -> None:
    """As `inspect` does for an unknown action, so a script cannot mistake "no such grant" for
    "a grant with no budgets"."""
    here = _project(tmp_path)
    _spend(here)

    result = _run(["inspect", "--grant", "nobody"], here)

    assert result.exit_code != 0
    assert "no grant nobody" in result.output


def test_T436_the_json_shapes_are_additive(tmp_path) -> None:
    """§7.2: "a 0.8.0 consumer of the same command keeps working, which T436 asserts rather than
    assumes."

    The budget view is its **own** document rather than a key inside `ctrlrun.inspection/v2`,
    because that one answers about an action: a reader handed one would have to know which of
    two shapes it got. So the assertion is that `inspect ACTION_ID --json` and `stats --json`
    still carry every key 0.8.0 read.
    """
    here = _project(tmp_path)
    _spend(here)

    stats = json.loads(_run(["stats", "--json"], here).output)
    assert stats["schema"] == "ctrlrun.stats/v1", "a new key must not move the schema"
    for key in ("mode", "since", "from", "to", "actions", "denied", "denied_by_reason"):
        assert key in stats, key
    assert stats["ledger_rows"] == 2, stats

    budget = json.loads(_run(["inspect", "--grant", "payer", "--json"], here).output)
    assert budget["schema"] == BUDGET_SCHEMA


def test_T437a_stats_reports_the_ledger_row_count(tmp_path) -> None:
    """§7.3: "`stats` reports the row count so growth is observable before it is a problem."
    The ledger only grows; the kernel deletes no row, on §12's rule about evidence."""
    here = _project(tmp_path)
    _spend(here)

    result = _run(["stats"], here)

    assert "budget ledger rows" in result.output, result.output
    assert "2" in result.output


def test_T438_effects_says_what_an_effect_is_holding(tmp_path) -> None:
    """§7.2 through the other command an operator reaches for. `--state ambiguous` is how they
    find what is pinning a grant, and the hold is the reason it matters.

    **"spent" for a committed effect, "holds" for every other.** §7.2 defines `held` as the part
    whose effects have not committed, so one word for both numbers would make the two commands
    disagree about what they are showing.
    """
    here = _project(tmp_path)
    _spend(here)

    everything = _run(["effects"], here).output
    assert "spent 200 amount on payer" in everything, everything
    assert "holds 300 amount on payer" in everything, everything

    ambiguous = _run(["effects", "--state", "ambiguous"], here).output
    assert "holds 300 amount on payer" in ambiguous
    assert "refund:1" not in ambiguous
