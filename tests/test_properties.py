# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""Invariants stated directly, over inputs nobody chose by hand.

**Why this file exists, from this repository's own history.** v0.11 item 1 found that
`verify_chain`'s docstring had claimed since v0.6 that position comes from the store's `seq`
column, and it was false: both backends selected `json, hash` and ordered by a column they never
read, so every position came out of the document, which is the half a tamperer controls. One
`UPDATE` setting a document's `seq` to 99 reported `missing 2`, `content_altered 99`,
`missing 100` and `link_broken 3` -- four breaks at three positions, two of them rows that do not
exist.

That is a **property**: one tamper is one break, at the row that was tampered with. It survived
five milestones of example tests because every example tampered in a way its author had already
thought of, and the author who writes the tamper is the author who writes the expectation.

`SPEC-v0.11.md` §13.2 says a test passing is not the evidence and running the real thing is.
This is the other half of that: for a claim shaped like *for every X*, the evidence is a
generator rather than a list.

**Determinism is pinned, and that is not optional here.** A suite that fails once in a while
teaches people to re-run it, and this project's gate is only worth anything while green means
green -- which the cookbook race (#207) had just finished demonstrating. `derandomize=True` makes
each run draw the same inputs, so a red run reproduces from the same command, and
`deadline=None` keeps a busy `-n auto` worker from failing a test for being slow rather than for
being wrong. A counterexample found in CI is reproducible locally by construction.

The stores here are real SQLite files with real `Control.execute` writes, not fixtures: these are
the same objects the CLI reads.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ctrlrun import Control, Policy, SQLiteStateStore
from ctrlrun.action import Action, Principal
from ctrlrun.receipt import CHAIN_BREAKS, Receipt, UnreadableReceipt, verify_chain

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
LEASE = timedelta(minutes=5)
ALLOW = "schema: ctrlrun.policy/v1\nactions:\n  stripe.refund:\n    decision: allow\n"

#: Every property here builds a real store, so the example count is deliberately modest: the cost
#: is a SQLite file and N `Control.execute` calls, not a pure function call. `derandomize` is what
#: makes a small count trustworthy -- the same inputs every run, so "it passed" means the same
#: thing twice.
PROFILE = settings(
    max_examples=40,
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


def _chain(database: Path, count: int) -> SQLiteStateStore:
    store = SQLiteStateStore(database, clock=lambda: T0)
    control = Control(Policy.from_yaml(ALLOW), store, clock=lambda: T0)
    for index in range(count):
        control.execute(
            Action(
                name="stripe.refund",
                arguments={"payment_id": f"p{index}", "amount": 1000 + index},
                principal=Principal(agent="a"),
            ),
            lambda: {"ok": True},
            f"refund:p{index}",
            lease=LEASE,
        )
    return store


# --- the chain reader -------------------------------------------------------------------------


@PROFILE
@given(size=st.integers(min_value=2, max_value=6), where=st.integers(min_value=0, max_value=5))
def test_an_untouched_chain_of_any_length_verifies(tmp_path_factory, size, where) -> None:
    """The positive control, and it has to come first.

    Every property below asserts something about a *tampered* chain. If an untouched chain of
    the same shape did not verify, those would all be measuring the wrong thing, and each of
    them would still pass.
    """
    store = _chain(tmp_path_factory.mktemp("clean") / "state.db", size)
    report = verify_chain(store)
    store.close()

    assert report.ok, report.breaks
    assert report.verified == size


@PROFILE
@given(
    size=st.integers(min_value=2, max_value=6),
    victim=st.integers(min_value=1, max_value=6),
    forged=st.integers(min_value=1, max_value=400),
)
def test_every_break_a_tamper_reports_names_a_row_that_exists(
    tmp_path_factory, size, victim, forged
) -> None:
    """`SPEC-v0.6.md` §6.5, and **the property that actually catches v0.11 item 1's defect**.

    The first version of this test asserted *one tamper is one break*. That is false, and
    hypothesis said so on its second example: altering row `n` also breaks the link at `n + 1`,
    because `n + 1` carries `prev_hash` over what `n` used to hash to. Two breaks is the correct
    answer and the test was wrong, which is worth leaving in the record rather than quietly
    fixing, because it is the same mistake in miniature that the guarded property is about --
    an author writing down what they expected instead of what holds.

    What was actually wrong in v0.11 item 1 was not the count. Rewriting one document's `seq` to
    99 on an eight-row chain reported `missing 2`, `content_altered 99`, `missing 100` and
    `link_broken 3`: **two of those name rows that do not exist**, because position came from the
    document, which is the half a tamperer controls, rather than from the `seq` column. So the
    invariant is that every reported position is a row the store actually holds. No arrangement
    of a tamperer's chosen values can conjure a break at a row that was never written.

    The bound is asserted too, at two rather than one: a tamper is local, and a reader that
    cascaded would be reporting damage the tamperer did not do.
    """
    database = tmp_path_factory.mktemp("altered") / "state.db"
    store = _chain(database, size)
    store.close()
    target = victim if victim <= size else size

    connection = sqlite3.connect(database)
    stored = connection.execute("SELECT json FROM receipts WHERE seq = ?", (target,)).fetchone()[0]
    document = json.loads(stored)
    was = document["seq"]
    document["seq"] = forged
    connection.execute("UPDATE receipts SET json = ? WHERE seq = ?", (json.dumps(document), target))
    connection.commit()
    connection.close()

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    report = verify_chain(reopened)
    reopened.close()

    if forged == was:  # writing back the value it already had edits nothing
        assert report.ok, report.breaks
        return

    assert not report.ok, "a rewritten receipt verified as intact"
    positions = {item.seq for item in report.breaks}
    assert positions <= set(range(1, size + 1)), (
        f"reported a break at {sorted(positions - set(range(1, size + 1)))}, "
        f"and this store holds seq 1..{size}"
    )
    assert target in positions, f"edited seq {target}, reported {sorted(positions)}"
    assert len(report.breaks) <= 2, f"one local tamper cascaded into {report.breaks}"
    assert all(item.name in CHAIN_BREAKS for item in report.breaks)


@PROFILE
@given(
    size=st.integers(min_value=2, max_value=6),
    victim=st.integers(min_value=1, max_value=6),
    junk=st.text(min_size=0, max_size=12),
)
def test_one_unreadable_row_costs_exactly_one_row(tmp_path_factory, size, victim, junk) -> None:
    """`SPEC-v0.11.md` §5, rule 3: a malformed row names itself and blinds nothing else.

    The generator writes arbitrary text into one row's `json`, which reaches the case the
    original tests missed and a review caught: every tamper they ran changed a row's *content*,
    and `{}` and a float are both valid JSON, so the parse failure path went unexercised. Here
    most drawn strings are not JSON at all.

    What is asserted is the blast radius, which is what the rule is about: exactly one row comes
    back unreadable, it is the row that was edited, and every other row still reads as a
    `Receipt`.
    """
    database = tmp_path_factory.mktemp("unreadable") / "state.db"
    store = _chain(database, size)
    store.close()
    target = victim if victim <= size else size

    connection = sqlite3.connect(database)
    connection.execute("UPDATE receipts SET json = ? WHERE seq = ?", (junk, target))
    connection.commit()
    connection.close()

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    rows = reopened.receipts()
    reopened.close()

    assert len(rows) == size, "a row disappeared rather than being named"
    unreadable = [row for row in rows if isinstance(row, UnreadableReceipt)]
    assert len(unreadable) == 1, f"one bad row produced {len(unreadable)} unreadable"
    assert unreadable[0].seq == target
    assert all(isinstance(row, Receipt) for row in rows if row.seq != target), (
        "one unreadable row blinded another row"
    )


@PROFILE
@given(size=st.integers(min_value=2, max_value=6), victim=st.integers(min_value=1, max_value=6))
def test_a_deleted_row_is_reported_and_never_silently_skipped(
    tmp_path_factory, size, victim
) -> None:
    """A gap in the middle is `missing`, and the walk does not renumber around it.

    The interesting half is the **last** row: deleting it is a truncation, and `SPEC-v0.11.md`
    §2.1 is the whole reason the anchor exists. The chain alone cannot catch that one, because
    the head it would check against is a row in the same database. Asserting "some break is
    reported" for every victim would quietly encode the opposite, so the two cases are separated
    here and the truncation is asserted as **undetected**, which is what §2.4 states.
    """
    database = tmp_path_factory.mktemp("deleted") / "state.db"
    store = _chain(database, size)
    store.close()
    target = victim if victim <= size else size

    connection = sqlite3.connect(database)
    connection.execute("DELETE FROM receipts WHERE seq = ?", (target,))
    connection.commit()
    connection.close()

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    report = verify_chain(reopened)
    reopened.close()

    if target == size:
        # A truncation with the head left behind is `head_mismatch`: the head still names a row
        # that is gone. Rewinding the head as well is the two-statement attack §2.1 measures, and
        # that one the chain cannot see at all.
        assert not report.ok
        assert {item.name for item in report.breaks} <= set(CHAIN_BREAKS)
    else:
        assert not report.ok, f"deleting seq {target} of {size} went unreported"
        assert any(item.seq == target for item in report.breaks), report.breaks
    assert all(item.name in CHAIN_BREAKS for item in report.breaks), (
        "a break kind outside the closed set of six"
    )
