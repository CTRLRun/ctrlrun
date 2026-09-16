# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""The upgrade path, and the one irreversible thing a v0.11 release does.

`SPEC-v0.11.md` §6 and the release item. **Run against the released 0.10.0 from PyPI by the
release pass, not against a fixture**: a fixture is a claim about what 0.10.0 did, and the check
exists because the claim might be wrong.

Measured during the 0.11.0 release pass, `pip install ctrlrun==0.10.0` into a clean venv::

    0.10.0 wrote 4 receipts; migration HEAD 0007_budget_ledger; chain ok=True
           schemas: ['ctrlrun.receipt/v7']
    this build:  opened it: 4 receipts, chain ok=True, HEAD 0008_anchor_checkpoint_hold
                 anchors readable, checkpoint None, holds empty
    0.10.0 again: REFUSED the migrated store -- SchemaMismatch: This database was written by a
                 newer build of ctrlrun and records a migration this one does not know.

**The refusal is the point.** Migration `0008_anchor_checkpoint_hold` is additive -- three new
tables and nothing altered -- so 0.10.0 could in principle read every row it wrote. It refuses
anyway, because a build that does not know a migration cannot know what the rows it *can* read
now mean, and `SPEC-v0.6.md` §3.2 settled that a store row a binary does not know is a refusal
rather than a guess. **Installing 0.11.0 is the irreversible step here**, and it is irreversible
in the safe direction: nothing is corrupted and nothing is lost, an older binary simply declines.

What is kept here is the shape and the invariant, so a later change that breaks either goes red
in the ordinary suite rather than only in a release rehearsal.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from ctrlrun import Control, Policy, SQLiteStateStore
from ctrlrun.action import Action, Principal
from ctrlrun.migrations import HEAD, MIGRATIONS
from ctrlrun.receipt import RECEIPT_SCHEMA, verify_chain

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
LEASE = timedelta(minutes=5)
ALLOW = "schema: ctrlrun.policy/v1\nactions:\n  stripe.refund:\n    decision: allow\n"

#: What 0.10.0's migration ledger ended at. The store this milestone upgrades from.
V0_10_HEAD = "0007_budget_ledger"


def _a_chain(database, count: int = 4) -> SQLiteStateStore:
    store = SQLiteStateStore(database, clock=lambda: T0)
    control = Control(Policy.from_yaml(ALLOW), store, clock=lambda: T0)
    for index in range(count):
        control.execute(
            Action(
                name="stripe.refund",
                arguments={"payment_id": f"p{index}", "amount": 2000},
                principal=Principal(agent="a"),
            ),
            lambda: {"ok": True},
            f"refund:p{index}",
            lease=LEASE,
        )
    return store


def test_0008_is_additive_and_moves_no_row_that_0_10_0_wrote(tmp_path) -> None:
    """The invariant behind the transcript above: three tables added, nothing altered.

    A migration that rewrote a receipt would move a hash, and every receipt on disk would stop
    verifying silently. `SPEC-v0.6.md` §3 makes a migration forward-only and additive, and this
    is that rule for `0008` specifically.
    """
    entry = next(item for item in MIGRATIONS if item.id == HEAD)
    assert HEAD == "0008_anchor_checkpoint_hold"
    for statement in (*entry.statements, *(entry.postgres or ())):
        verb = statement.strip().split()[0].upper()
        assert verb == "CREATE", (
            f"migration 0008 runs a {verb}: it must only create, or a store written by 0.10.0 "
            f"changes underneath a chain that was verifying\n{statement.strip()[:160]}"
        )
        assert "receipts" not in statement.lower() or "prune_checkpoint" in statement.lower(), (
            f"migration 0008 touches the receipts table:\n{statement.strip()[:160]}"
        )


def test_a_store_written_before_0008_upgrades_and_its_chain_still_verifies(tmp_path) -> None:
    """The half a fixture can carry: a pre-0008 store opens, migrates, and verifies.

    Written by this build and then walked back to 0.10.0's ledger state, because the released
    wheel is not importable from inside this process. The release pass runs the real thing; this
    keeps the invariant checkable every day.
    """
    database = tmp_path / "state.db"
    store = _a_chain(database)
    before = [(item.seq, item.hash) for item in store.receipts()]
    store.close()

    connection = sqlite3.connect(database)
    connection.execute("DROP TABLE anchors")
    connection.execute("DROP TABLE prune_checkpoint")
    connection.execute("DROP TABLE holds")
    connection.execute("DELETE FROM schema_version WHERE migration_id = ?", (HEAD,))
    connection.commit()
    applied = {row[0] for row in connection.execute("SELECT migration_id FROM schema_version")}
    connection.close()
    assert V0_10_HEAD in applied and HEAD not in applied, applied

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    report = verify_chain(reopened)
    rows = reopened.receipts()

    assert [(item.seq, item.hash) for item in rows] == before, (
        "migrating moved a receipt's seq or hash; every receipt on disk would stop verifying"
    )
    assert report.ok, [(item.name, item.seq) for item in report.breaks]
    assert {item.schema for item in rows} == {RECEIPT_SCHEMA}
    # The three tables are there and empty: a store that has never anchored, pruned or held.
    assert reopened.anchors() == ()
    assert reopened.checkpoint() is None
    assert reopened.holds() == ()
    reopened.close()


def test_an_older_binary_refuses_a_migrated_store_rather_than_reading_it(tmp_path) -> None:
    """**The irreversible step, and it is irreversible in the safe direction.**

    `0008` is additive, so 0.10.0 *could* read every row it wrote. It refuses anyway: a build
    that does not know a migration cannot know what the rows it can read now mean. Nothing is
    corrupted and nothing is lost; an older binary declines.

    This stands in for the older reader by narrowing the known set to what 0.10.0 knew, which is
    the shape `tests/test_upgrade_0_9_to_0_10.py` already uses for the same reason.
    """
    import ctrlrun.migrations as module
    from ctrlrun.errors import SchemaMismatch

    database = tmp_path / "state.db"
    store = _a_chain(database)
    store.close()

    knew_seven = tuple(item for item in module.MIGRATIONS if item.id != HEAD)
    original = module.MIGRATIONS
    try:
        module.MIGRATIONS = knew_seven  # type: ignore[assignment]
        try:
            SQLiteStateStore(database, clock=lambda: T0)
        except SchemaMismatch as refused:
            message = str(refused)
        else:
            raise AssertionError(
                "a binary that does not know 0008 opened a store that records it; an older "
                "reader must refuse rather than guess what the rows it can read now mean"
            )
    finally:
        module.MIGRATIONS = original  # type: ignore[assignment]

    assert "newer build" in message, message
    # And the store is untouched by the refusal, which is what makes it safe rather than merely
    # loud: the operator downgrades, is refused, upgrades again, and has lost nothing.
    reopened = SQLiteStateStore(database, clock=lambda: T0)
    assert verify_chain(reopened).ok
    assert len(reopened.receipts()) == 4
    reopened.close()
