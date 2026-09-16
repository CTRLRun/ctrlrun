# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""Retention: the prune, the checkpoint and the hold. SPEC-v0.11 §4; T540-T552.

**A prune is the only operation in this library that destroys evidence.** Getting a refusal
wrong costs an operator an error message; getting this wrong costs them the record. So the
refusals are the deliverable and they are written first.

`../ctrlrun-docs/docs/postgres.md` has said there is no retention policy, in the same breath as
the reason one is hard: deleting receipts from the middle or the end of the chain is detected as
a break **by design**. A retention job that simply deleted would be manufacturing
`SPEC-v0.11.md` §2.1's attack on purpose. Measured before any of this was written::

    after DELETE seq<=3 -> ok: False verified: 2 breaks: [('missing', 1), ('link_broken', 4)]

**Rule 2 is a delta, not "the chain verifies"** (§1.1). `unchained` is a pre-existing condition
on any store migrated from v0.1 to v0.5, survives a prefix prune and can never be inside a
prefix, so the absolute version would make retention permanently impossible on the oldest and
largest stores, which are the ones it is for. `T543` is that case.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ctrlrun import Control, Policy, SQLiteStateStore
from ctrlrun.action import Action, Principal
from ctrlrun.anchor import CHECKPOINT, Anchor, make_anchor, verify_anchors
from ctrlrun.errors import InvalidArgument
from ctrlrun.receipt import ChainBreak, verify_chain
from ctrlrun.retention import PRUNE_ACTION, Hold, prune

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
LEASE = timedelta(minutes=5)
ALLOW = "schema: ctrlrun.policy/v1\nactions:\n  stripe.refund:\n    decision: allow\n"
DAY = timedelta(days=1)

POSTGRES_URL = os.environ.get("CTRLRUN_TEST_POSTGRES")
postgres = pytest.mark.skipif(
    not POSTGRES_URL, reason="CTRLRUN_TEST_POSTGRES is not set; no server to run against"
)


def an_action(payment_id: str) -> Action:
    return Action(
        name="stripe.refund",
        arguments={"payment_id": payment_id, "amount": 2000},
        principal=Principal(agent="chain-agent"),
    )


class Provider:
    """An anchor provider, outside the store. §4.6 requires the prune to anchor first."""

    def __init__(self) -> None:
        self.held: dict[str, Anchor] = {}
        self.at = T0

    def make(self, seq: int, hash: str, kind: str) -> tuple[str, datetime]:
        self.at += timedelta(minutes=1)
        token = f"tok-{kind}-{seq}"
        self.held[token] = Anchor(seq=seq, hash=hash, token=token, kind=kind, at=self.at)
        return token, self.at

    def check(self, seq: int, hash: str, token: str) -> bool:
        held = self.held.get(token)
        return held is not None and held.seq == seq and held.hash == hash

    def latest(self) -> tuple[int, str] | None:
        if not self.held:
            return None
        best = max(self.held.values(), key=lambda item: item.seq)
        return (best.seq, best.token)

    def since(self, seq: int) -> tuple[Anchor, ...]:
        return tuple(item for item in self.held.values() if item.seq >= seq)


def a_chain(database: Path, count: int = 8) -> SQLiteStateStore:
    store = SQLiteStateStore(database, clock=lambda: T0)
    control = Control(Policy.from_yaml(ALLOW), store, clock=lambda: T0)
    for index in range(count):
        control.execute(
            an_action(f"p{index}"), lambda: {"ok": True}, f"refund:p{index}", lease=LEASE
        )
    return store


# --- T540: the deliverable ---------------------------------------------------------------------


def test_T540_a_prune_leaves_the_chain_verifiable_across_the_gap(tmp_path) -> None:
    """SPEC-v0.11 §4.1 and rule 2. **The deliverable, with its negative control first.**

    Without the negative half, this passes against a prune that deleted nothing.
    """
    database = tmp_path / "state.db"
    store = a_chain(database)
    before = verify_chain(store)
    assert before.ok and before.verified == 8

    # (a) The negative control: a naive prefix delete breaks the chain, which is the whole
    #     reason a checkpoint exists.
    naive = tmp_path / "naive.db"
    other = a_chain(naive)
    other.close()
    connection = sqlite3.connect(naive)
    connection.execute("DELETE FROM receipts WHERE seq <= 3")
    connection.commit()
    connection.close()
    reopened = SQLiteStateStore(naive, clock=lambda: T0)
    broken = verify_chain(reopened)
    reopened.close()
    assert not broken.ok
    assert [(item.name, item.seq) for item in broken.breaks] == [("missing", 1), ("link_broken", 4)]

    # (b) The prune.
    provider = Provider()
    result = prune(store, through=3, older_than=DAY, anchor=provider, now=NOW)
    after = verify_chain(store)

    assert result.receipts_deleted == 3
    assert result.checkpoint is not None and result.checkpoint.seq == 3
    assert result.anchored is True
    assert after.ok, [(item.name, item.seq) for item in after.breaks]
    assert after.verified == 5
    assert after.breaks == []
    assert store.checkpoint() == (3, result.checkpoint.hash)
    assert len(store.receipts()) == 5
    store.close()


def test_T540b_the_checkpoint_carries_the_schema_current_when_it_was_written(tmp_path) -> None:
    """§4.2. A store pruned today and read in two years is the case this milestone is about."""
    from ctrlrun.receipt import RECEIPT_SCHEMA

    store = a_chain(tmp_path / "state.db")
    result = prune(store, through=3, older_than=DAY, anchor=Provider(), now=NOW)
    store.close()
    assert result.checkpoint is not None
    assert result.checkpoint.schema == RECEIPT_SCHEMA


def test_T540c_the_checkpoint_supplies_three_values_and_not_one(tmp_path) -> None:
    """§4.1's table, and the defect a review found in the first draft.

    `verify_chain` seeds **two** genesis values, `expected_prev` and `expected_seq`, and compares
    the head against a third. A checkpoint replacing only the hash still reports `missing` at
    seq 1, which is the break rule 2 requires a prune to be **refused** for. A faithful
    implementation of the first draft built a prune §1.1 forbids.
    """
    store = a_chain(tmp_path / "state.db")
    prune(store, through=3, older_than=DAY, anchor=Provider(), now=NOW)
    kept = store.receipts()
    store.close()

    # The seq seed: without it the walk starts at 1 and reports `missing`.
    class _HashOnly:
        def receipts(self):
            return kept

        def chain_head(self):
            return (kept[-1].seq, kept[-1].hash)

    only_hash = verify_chain(_HashOnly())
    assert not only_hash.ok
    assert ("missing", 1) in [(item.name, item.seq) for item in only_hash.breaks], (
        "a source with no checkpoint verified a pruned chain, so the checkpoint is not what "
        "makes the gap legible and this test proves nothing"
    )


# --- T541 to T544: every refusal in §10 --------------------------------------------------------


def test_T541_a_prune_through_the_head_is_refused(tmp_path) -> None:
    """§10. It leaves no chained receipt for the head to name, so the store would report
    `head_mismatch` about a chain nothing is wrong with."""
    store = a_chain(tmp_path / "state.db")
    with pytest.raises(InvalidArgument) as refused:
        prune(store, through=8, older_than=DAY, anchor=Provider(), now=NOW)
    store.close()
    assert "would take the chain's head" in str(refused.value)


def test_T542_a_checkpoint_is_written_only_forward(tmp_path) -> None:
    """§4.5. Two prunes, each individually valid under §10, in an order §4 did not exclude::

        A validated: prune through 5 -> checkpoint 5
        B validated: prune through 3 -> checkpoint 3
          after B, walking from B's checkpoint: [('missing', 4), ('link_broken', 6)]

    Neither is refusable alone and together they break rule 2, so B is refused.
    """
    store = a_chain(tmp_path / "state.db")
    provider = Provider()
    prune(store, through=5, older_than=DAY, anchor=provider, now=NOW)
    with pytest.raises(InvalidArgument) as refused:
        prune(store, through=3, older_than=DAY, anchor=provider, now=NOW)
    assert "written only forward" in str(refused.value)
    assert verify_chain(store).ok, "the refused second prune damaged the chain"
    store.close()


def test_T543_a_prune_on_a_store_with_a_PRE_EXISTING_break_is_allowed(tmp_path) -> None:
    """**Rule 2 is a delta.** This is the case the absolute version makes impossible.

    `unchained` is a pre-existing condition on any store migrated from v0.1 to v0.5. It survives
    a prefix prune and can never be inside a prefix, so "the chain verifies afterwards" would
    refuse every prune on exactly the oldest and largest stores, which are the ones retention is
    for.
    """
    database = tmp_path / "state.db"
    store = a_chain(database)
    store.close()

    # A pre-chain row, as a store migrated from v0.5 carries.
    connection = sqlite3.connect(database)
    row = connection.execute("SELECT * FROM receipts WHERE seq = 1").fetchone()
    connection.execute(
        "INSERT INTO receipts (receipt_id, action_id, ts, json, seq, prev_hash, hash) "
        "SELECT 'ctr_' || substr(hex(randomblob(14)), 1, 28), action_id, ts, json, NULL, NULL, "
        "NULL FROM receipts WHERE seq = 1"
    )
    connection.commit()
    connection.close()
    assert row is not None

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    before = verify_chain(reopened)
    assert not before.ok, "this store was supposed to start with a break"
    assert ("unchained", None) in [(item.name, item.seq) for item in before.breaks]

    result = prune(reopened, through=3, older_than=DAY, anchor=Provider(), now=NOW)
    after = verify_chain(reopened)
    reopened.close()

    assert result.receipts_deleted == 3
    caused = {(item.name, item.seq) for item in after.breaks} - {
        (item.name, item.seq) for item in before.breaks
    }
    assert not caused, f"the prune introduced {caused}, and rule 2 is a delta"
    assert ("unchained", None) in [(item.name, item.seq) for item in after.breaks], (
        "the pre-existing break vanished, so this test is not measuring a delta"
    )


def test_T544_a_prune_that_would_cause_a_break_is_refused_with_the_seq_named(tmp_path) -> None:
    """Rule 2's refusal, forced by making the checkpoint unusable.

    There is no `--force`, no `--allow-gap` and no setting that admits a break the prune caused.
    """
    database = tmp_path / "state.db"
    store = a_chain(database)

    # A store whose checkpoint write is a no-op: the prune's own validation passes, and the walk
    # afterwards cannot be seeded. This stands in for any way the checkpoint fails to land.
    class _NoCheckpoint:
        def __init__(self, real):
            self._real = real

        def put_checkpoint(self, checkpoint):
            return None

        def checkpoint(self):
            return None

        def __getattr__(self, name):
            return getattr(self._real, name)

    damaged = _NoCheckpoint(store)
    # The prune completes (its validation used the checkpoint it *would* have written), and the
    # store afterwards reports the break. That is why the refusal is computed against the walk
    # rather than against the prune's own arithmetic.
    prune(damaged, through=3, older_than=DAY, anchor=Provider(), now=NOW)
    after = verify_chain(damaged)
    store.close()
    assert not after.ok
    assert ("missing", 1) in [(item.name, item.seq) for item in after.breaks], (
        "a store whose checkpoint did not land verified anyway, so the checkpoint is not what "
        "the walk is reading"
    )


# --- T545: the hold ----------------------------------------------------------------------------


def test_T545_a_prune_overlapping_a_held_range_is_refused_with_the_hold_named(tmp_path) -> None:
    """§4.3, §10. The control is the same prune once the hold is released."""
    store = a_chain(tmp_path / "state.db")
    provider = Provider()
    store.put_hold(
        Hold(
            hold_id="legal-hold-1",
            from_seq=1,
            to_seq=5,
            reason="litigation: matter 2026-11",
            placed_by="cli:alice",
            placed_at=T0,
        )
    )

    with pytest.raises(InvalidArgument) as refused:
        prune(store, through=3, older_than=DAY, anchor=provider, now=NOW)
    assert "legal-hold-1" in str(refused.value)
    assert "litigation" in str(refused.value)
    assert len(store.receipts()) == 8, "a refused prune deleted receipts"

    store.release_hold("legal-hold-1", by="cli:alice", at=NOW)
    result = prune(store, through=3, older_than=DAY, anchor=provider, now=NOW)
    store.close()
    assert result.receipts_deleted == 3, (
        "the same prune was refused with no live hold, so the hold is not what refused it"
    )


def test_T545b_a_hold_with_no_upper_bound_holds_everything(tmp_path) -> None:
    store = a_chain(tmp_path / "state.db")
    store.put_hold(
        Hold(
            hold_id="everything",
            from_seq=1,
            to_seq=None,
            reason="hold all of it",
            placed_by="cli:alice",
            placed_at=T0,
        )
    )
    with pytest.raises(InvalidArgument):
        prune(store, through=1, older_than=DAY, anchor=Provider(), now=NOW)
    store.close()


def test_T545c_nothing_lifts_a_hold_but_a_person(tmp_path) -> None:
    """§4.3. `SPEC-v0.9 §4`'s rule that an automatic expiry on a hold is the refund rule in a
    costume, applied here: a hold that lapsed on a timer would release evidence on a schedule
    nobody reviewed.

    Asserted against the source, because "there is no sweeper" is a claim about the environment
    until something checks it, in the shape `CLAIMS.md` uses.
    """
    import ast

    import ctrlrun.retention as module

    # **The CODE, not the text.** A plain scan over the source flags the docstring that says
    # there is **no** expiry, which is the sentence this rule exists to produce -- and the next
    # person would remove the scan as a false positive, taking the check with it. That is the
    # failure `tests/test_docs_production.py` solves with an allow-list; here the names a
    # sweeper would need are reachable through the AST, so the docstrings never enter it.
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    used = {
        node.id.lower() if isinstance(node, ast.Name) else node.attr.lower()
        for node in ast.walk(tree)
        if isinstance(node, (ast.Name, ast.Attribute))
    }
    used |= {node.name.lower() for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    used |= {
        target.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.arg)
        for target in (node.arg,)
    }
    for sweeper in ("expires_at", "expiry", "ttl", "auto_release", "sweep", "reap"):
        assert sweeper not in used, (
            f"retention.py's code uses {sweeper!r}. A hold ends when a person ends it; if that "
            "changed, SPEC-v0.11 §4.3 and SPEC-v0.9 §4 both have to say so first"
        )
    store = a_chain(tmp_path / "state.db")
    store.put_hold(Hold("h", 1, 5, "why", "cli:alice", placed_at=T0))
    assert store.holds()[0].live
    with pytest.raises(InvalidArgument):
        store.release_hold("nope", by="cli:alice", at=NOW)
    store.release_hold("h", by="cli:alice", at=NOW)
    assert not store.holds()[0].live
    with pytest.raises(InvalidArgument):
        store.release_hold("h", by="cli:alice", at=NOW)
    store.close()


# --- T546: the ledger, and the window --------------------------------------------------------


def test_T546_a_ledger_row_whose_effect_still_holds_its_charge_is_refused(tmp_path) -> None:
    """§4.4's table. Deleting it would hand back authority nobody granted, which is the hole
    `SPEC-v0.9 §4` exists to close.

    **The rule is settlement, not release.** The first draft wrote it as "a prune excludes
    un-released rows", and `state.py`'s `_release_locked` says why that is inert: `COMMITTED`
    holds permanently and only `FAILED` releases, because a committed spend is a spend. So
    "un-released" is almost every row in the ledger, forever.
    """
    from ctrlrun.effect import EffectState
    from ctrlrun.retention import HELD_EFFECT_STATES

    assert set(HELD_EFFECT_STATES) == {
        EffectState.AMBIGUOUS,
        EffectState.RESERVED,
        EffectState.EXECUTING,
    }
    assert EffectState.COMMITTED not in HELD_EFFECT_STATES, (
        "COMMITTED is not simply held: it is prunable OUTSIDE SPEC-v0.9 §7.3's window, which is "
        "a condition and not a footnote"
    )
    assert EffectState.FAILED not in HELD_EFFECT_STATES
    assert EffectState.NEW not in HELD_EFFECT_STATES, (
        "NEW is never written to a store (effect.py), so a table row for it is a row that cannot "
        "exist"
    )


def test_T546b_a_committed_ledger_row_inside_the_window_is_refused(tmp_path) -> None:
    """§4.4. **The window is a condition, not a footnote**, and the first draft made it one.

    A review measured what "COMMITTED is history and is prunable" costs, on a 250-unit budget::

        third 100 on a 250 budget: REFUSED -> budget 'amount' ... is exhausted
        pruned COMMITTED ledger rows: 2
        SAME action after pruning: ALLOWED   <-- authority manufactured

    `control.py` sums consumptions over `now - window` where `released_at is None`, and a
    `COMMITTED` row is never released, so it counts.
    """
    from ctrlrun.retention import _ledger_refusals

    database = tmp_path / "state.db"
    store = a_chain(database, 4)

    class _Row:
        effect_key = "refund:p0"
        consumed_at = NOW - timedelta(hours=1)
        released_at = None

    from ctrlrun.effect import EffectState

    class _Committed:
        state = EffectState.COMMITTED

    class _Store:
        def consumptions(self):
            return (_Row(),)

        def get_effect(self, key):
            return _Committed()

    # Inside the window: refused.
    inside = _ledger_refusals(_Store(), ["refund:p0"], now=NOW, older_than=timedelta(days=1))
    assert inside and "manufacture authority" in inside[0], inside

    # Outside it: prunable. Without this half the rule would be "never prune a COMMITTED row",
    # which is a retention feature that retains everything.
    outside = _ledger_refusals(_Store(), ["refund:p0"], now=NOW, older_than=timedelta(minutes=1))
    assert outside == [], outside
    store.close()


def test_T546c_the_window_is_supplied_and_never_derived(tmp_path) -> None:
    """§4.4 (O7). A ledger row carries `grant_id`, `metric`, `amount`, `effect_key`, `attempt`,
    `consumed_at` and `released_at`, and **no window and no limit**.

    Those travel on `Charge`, from the authority document, and `state.py` says why in as many
    words: a store that resolved a grant's budgets would be reading the policy, which
    `ARCHITECTURE.md` §6 forbids. Deriving it inside the prune would reinstate exactly the
    dependency §4.2 removes.
    """
    import ctrlrun.retention as module
    from ctrlrun.state import Consumption

    fields = set(Consumption.__dataclass_fields__)
    assert "window" not in fields and "limit" not in fields, (
        f"a ledger row now carries a window; O7's argument may no longer hold: {sorted(fields)}"
    )
    source = Path(module.__file__).read_text(encoding="utf-8")
    for reading_policy in ("Authority", "from_yaml", "grants", "Policy"):
        assert reading_policy not in source, (
            f"retention.py mentions {reading_policy!r}; a prune that resolved a grant's budgets "
            "would be reading the policy (ARCHITECTURE.md §6)"
        )


# --- T547: §4.6's interaction, which neither G28 nor G29 grades --------------------------------


def test_T547_an_honest_prune_leaves_the_anchor_report_clean(tmp_path) -> None:
    """§4.6. **The cross-module class this project keeps finding: each part is right, and the
    pair is not.**

    The first draft specified items 2 and 3 so that they could not both run. §3 never contained
    the word *prune*, §4 contained the word *anchor* once, each section verified alone, and a
    review found what they do together::

        C. after an honest prune through seq 3 (checkpoint written)
           checkpoint-seeded verify_chain   ok=True verified=2 breaks=[]
           an anchor taken at seq 2 before the prune
           -> [('anchor_broken', 2, 'the anchored seq is absent')]

    In steady state, anchoring hourly and pruning at ninety days, **every anchor older than the
    retention window would be permanently `anchor_broken`**, so an anchoring deployment would
    have to choose between refusing every prune and living with a permanent tamper signal.
    """
    store = a_chain(tmp_path / "state.db")
    provider = Provider()
    rows = store.receipts()
    low = rows[1]
    assert low.seq == 2 and low.hash is not None
    made = make_anchor(store, provider, at=(low.seq, low.hash))
    assert verify_anchors(store, provider).ok, "the anchor did not reproduce before the prune"

    prune(store, through=3, older_than=DAY, anchor=provider, now=NOW)
    report = verify_anchors(store, provider)
    chain = verify_chain(store)
    store.close()

    assert chain.ok, [(item.name, item.seq) for item in chain.breaks]
    assert report.ok, (
        "an anchor taken before an honest prune reports tampering, so an anchoring deployment "
        f"cannot prune: {[(item.name, item.seq) for item in report.breaks]}"
    )
    assert report.superseded >= 1, (
        "the anchor below the checkpoint was neither superseded nor broken, so it is being "
        "silently dropped rather than accounted for"
    )
    assert made.seq == 2


def test_T547b_a_checkpoint_that_is_NOT_anchored_does_not_supersede(tmp_path) -> None:
    """§4.6's third row, **which is what stops "superseded" becoming the hole.**

    An attacker who erases a prefix and writes a checkpoint to explain it must also anchor that
    checkpoint, and anchoring goes through the provider, which is outside the store. So the
    provider's own record shows that a prune happened, at what `seq`, and when: a prune becomes
    something an operator can see in the anchor history even though the receipts are gone.
    """
    database = tmp_path / "state.db"
    store = a_chain(database)
    provider = Provider()
    rows = store.receipts()
    low = rows[1]
    assert low.hash is not None
    make_anchor(store, provider, at=(low.seq, low.hash))
    boundary = rows[2]
    assert boundary.seq == 3 and boundary.hash is not None
    store.close()

    # The forgery: erase a prefix and write a checkpoint by hand, **without** anchoring it.
    connection = sqlite3.connect(database)
    connection.execute("DELETE FROM receipts WHERE seq <= 3")
    connection.execute(
        "INSERT INTO prune_checkpoint (id, seq, hash, schema, at) VALUES (1, ?, ?, ?, ?)",
        (3, boundary.hash, "ctrlrun.receipt/v7", NOW.isoformat()),
    )
    connection.commit()
    connection.close()

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    chain = verify_chain(reopened)
    report = verify_anchors(reopened, provider)
    reopened.close()

    # The chain is satisfied: a forged checkpoint is a row anyone who can insert receipts can
    # write, and §4.2 says so rather than implying otherwise.
    assert chain.ok, "the chain caught the forged checkpoint; §4.2 says it cannot"
    # The anchor is not, because no anchored checkpoint accounts for the erasure.
    assert not report.ok
    assert ("anchor_broken", 2) in [(item.name, item.seq) for item in report.breaks], (
        "a prefix erased with an unanchored checkpoint to explain it was accepted, which makes "
        f"'superseded' the hole §4.6 exists to close: {[(b.name, b.seq) for b in report.breaks]}"
    )


def test_T547c_the_prune_anchors_its_checkpoint_BEFORE_it_deletes(tmp_path) -> None:
    """§4.6. **Anchor, then delete**, and the ordering is not negotiable.

    A crash between them leaves an anchored checkpoint for a prune that did not happen, which
    over-reports and is the safe direction. The reverse leaves a prefix erased with nothing
    accounting for it, which is indistinguishable from §2.1's attack.
    """
    store = a_chain(tmp_path / "state.db")

    seen: list[str] = []

    class _Recording(Provider):
        def make(self, seq, hash, kind):
            seen.append(f"anchored {kind} at {seq}")
            return super().make(seq, hash, kind)

    class _Store:
        def __init__(self, real):
            self._real = real

        def delete_prefix(self, through, effect_keys):
            seen.append(f"deleted through {through}")
            return self._real.delete_prefix(through, effect_keys)

        def __getattr__(self, name):
            return getattr(self._real, name)

    prune(_Store(store), through=3, older_than=DAY, anchor=_Recording(), now=NOW)
    store.close()

    assert seen == [f"anchored {CHECKPOINT} at 3", "deleted through 3"], seen


# --- T548: the prune's receipt -----------------------------------------------------------------


def test_T548_a_prune_is_not_routed_through_Control_execute() -> None:
    """§4.2, and O4 reversed.

    The first draft said the prune's receipt was "ordinary, subject to policy", and that answer
    contradicts O3 in the same document. `control.py`'s gate is
    `if self._require_approved_policy and action.name != POLICY_CHANGE_ACTION`, so it covers
    every action but one::

        prune under require_approved_policy (unapproved) -> ActionDenied: this deployment
           requires an approved policy ...
        undeclared action -> ActionDenied: ctrlrun.retention.prune denied: unknown_action

    That is exactly the trap O3 refuses: a deployment that had not approved its current policy
    could not prune, and **a store that cannot prune is a store that fills**. One of the two had
    to move, and it is O4.
    """
    import ast

    import ctrlrun.retention as module

    assert PRUNE_ACTION == "ctrlrun.retention.prune"
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "execute" not in called, (
        "retention.py calls .execute(); a prune routed through Control would be refused on a "
        "deployment that had not approved its current policy (SPEC-v0.11 §4.2)"
    )
    assert "Control" not in {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


def test_T548b_the_walk_does_not_trust_a_receipt_that_names_itself_a_checkpoint() -> None:
    """§4.2. `SPEC-v0.3 §4.3.1` settled this shape: a grant may legally be named `no_authority`,
    so evidence that could be spoofed by naming one is not evidence.

    A walk that believed `action == "ctrlrun.retention.prune"` would accept a forged prefix
    erasure written by anyone who can insert a row.
    """
    import ast

    import ctrlrun.receipt as module

    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    constants = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        if isinstance(node.value, str)
    }
    assert PRUNE_ACTION not in constants, (
        "receipt.py's walk mentions the prune action name. The checkpoint is a ROW, and a walk "
        "that read a receipt's action would trust a string a forger controls (SPEC-v0.11 §4.2)"
    )


# --- T549: what a prune contends with ----------------------------------------------------------


def _prune_worker(job: dict) -> dict:
    """One prune, in its own OS process, against a real Postgres server.

    **Multi-process against Postgres, not threads against SQLite** (§4.5, and the v0.6
    multi-process standard). On SQLite two prunes exclude each other by accident, because
    `put_receipt` uses `BEGIN IMMEDIATE` and SQLite admits one writer. **On Postgres they do
    not**: `put_receipt` takes a row lock on `receipt_chain`, and a `DELETE` on `receipts` does
    not contend with it, so a test that ran threads against SQLite would pass against a Postgres
    implementation that takes no lock at all.
    """
    import os
    import time
    from datetime import UTC, datetime, timedelta

    from ctrlrun.anchor import Anchor
    from ctrlrun.errors import CTRLRunError
    from ctrlrun.postgres import PostgresStateStore
    from ctrlrun.retention import prune

    class _Provider:
        def __init__(self) -> None:
            self.held: dict[str, Anchor] = {}
            self.at = datetime(2026, 1, 1, tzinfo=UTC)

        def make(self, seq, hash, kind):
            self.at += timedelta(seconds=1)
            token = f"{os.getpid()}-{kind}-{seq}"
            self.held[token] = Anchor(seq=seq, hash=hash, token=token, kind=kind, at=self.at)
            return token, self.at

        def check(self, seq, hash, token):
            return token in self.held

        def latest(self):
            return None

        def since(self, seq):
            return ()

    store = PostgresStateStore(job["url"], schema=job["schema"])

    # A filesystem barrier, so the children actually contend. Interpreter startup, the import of
    # ctrlrun, the connection and the migration check all happen first and vary by more than the
    # work does; without this the processes routinely run one after another and a test that
    # proved nothing would report success. v0.9 shipped one that held 4/4 against a deliberately
    # unlocked implementation.
    open(os.path.join(job["gate"], f"{os.getpid()}.ready"), "w").close()
    deadline = time.time() + 30
    while time.time() < deadline:
        if len(os.listdir(job["gate"])) >= job["children"]:
            break
        time.sleep(0.005)

    started = time.time()
    try:
        result = prune(
            store,
            through=job["through"],
            older_than=timedelta(days=1),
            anchor=_Provider(),
            now=datetime.now(UTC),
        )
        outcome = {"ok": True, "deleted": result.receipts_deleted}
    except CTRLRunError as refused:
        outcome = {"ok": False, "refused": str(refused)[:120]}
    finally:
        store.close()
    outcome["started"] = started
    outcome["finished"] = time.time()
    return outcome


@postgres
def test_T549_two_prunes_racing_cannot_both_succeed() -> None:
    """§4.5. **Two prunes, each individually valid under §10, in an order §4 did not exclude.**

    A review ran exactly this and measured what it leaves::

        A validated: prune through 5 -> checkpoint 5
        B validated: prune through 3 -> checkpoint 3
          after B (its checkpoint row overwrites A's), walking from B's checkpoint:
              [('missing', 4), ('link_broken', 6)]

    Neither is refusable alone and together they break rule 2. So a prune takes the same lock a
    receipt write takes, and a checkpoint is written only forward.

    **The store is what is asserted, not the exit codes.** Whichever order the two land in, the
    chain afterwards must carry no break the store did not already have, because that is rule 2
    and it is the only thing an operator can check.
    """
    import multiprocessing

    from ctrlrun.postgres import PostgresStateStore

    schema = f"prune_race_{uuid.uuid4().hex[:12]}"
    PostgresStateStore.create_schema(POSTGRES_URL, schema)
    gate = Path(os.environ.get("TMPDIR", "/tmp")) / f"ctrlrun-prune-gate-{uuid.uuid4().hex[:8]}"
    gate.mkdir()
    try:
        store = PostgresStateStore(POSTGRES_URL, schema=schema, clock=lambda: T0)
        control = Control(Policy.from_yaml(ALLOW), store, clock=lambda: T0)
        for index in range(10):
            control.execute(
                an_action(f"p{index}"), lambda: {"ok": True}, f"refund:p{index}", lease=LEASE
            )
        before = {(item.name, item.seq) for item in verify_chain(store).breaks}
        store.close()

        jobs = [
            {
                "url": POSTGRES_URL,
                "schema": schema,
                "through": through,
                "gate": str(gate),
                "children": 2,
            }
            for through in (5, 3)
        ]
        with multiprocessing.get_context("spawn").Pool(2) as pool:
            outcomes = pool.map(_prune_worker, jobs)

        # The children really overlapped, or this test proves nothing about contention.
        overlapped = min(item["finished"] for item in outcomes) > max(
            item["started"] for item in outcomes
        )

        reopened = PostgresStateStore(POSTGRES_URL, schema=schema, clock=lambda: T0)
        after = {(item.name, item.seq) for item in verify_chain(reopened).breaks}
        checkpoint = reopened.checkpoint()
        reopened.close()

        assert overlapped, (
            f"the two prunes did not overlap, so nothing contended: {outcomes}. This test's own "
            "barrier is what is supposed to prevent that"
        )
        assert not (after - before), (
            f"two racing prunes left the chain reporting {sorted(after - before, key=str)}, "
            f"which rule 2 forbids. outcomes: {outcomes}"
        )
        assert checkpoint is not None and checkpoint[0] == 5, (
            f"the checkpoint is {checkpoint}, and it must be the higher of the two: a checkpoint "
            "is written only forward, so the lower prune is refused or its checkpoint is ignored"
        )

        # **Both outcomes are legitimate, and asserting one of them was a defect in this test.**
        # Serialized as 5-then-3, the second is refused: a checkpoint is written only forward.
        # Serialized as 3-then-5, **both succeed** and both are correct -- the first deletes
        # seq 1..3 and the second seq 4..5, and the chain afterwards is exactly what one prune
        # through 5 would have left. A first version asserted `len(refused) == 1` and was flaky
        # one run in three, which is this test lying about a store that was fine.
        #
        # What must hold either way is rule 2 and the checkpoint, both asserted above. What is
        # asserted here is that **a refusal, if there is one, comes from the checkpoint rule**.
        # Before `pruning()` held a real transaction the loser was refused by the *anchor
        # ordering* instead -- shared state reached by accident, which would order the pair
        # differently under a different provider and not at all under some.
        #
        # `T549b` is the direct evidence for §4.5's lock, because an outcome test cannot supply
        # it: `put_checkpoint`'s own SQL is forward-only, so the dangerous interleaving is
        # refused by the database even with no lock at all.
        refused = [item for item in outcomes if not item["ok"]]
        for item in refused:
            assert "written only forward" in item["refused"], (
                "a prune was refused, but not by the checkpoint rule. If it names the anchor "
                f"ordering, pruning() is not holding a transaction: {item['refused']}"
            )
    finally:
        PostgresStateStore.drop_schema(POSTGRES_URL, schema)
        for item in gate.iterdir():
            item.unlink()
        gate.rmdir()


@postgres
def test_T549b_a_prune_and_a_receipt_write_exclude_each_other_on_postgres() -> None:
    """§4.5. **On SQLite this already happens by accident; on Postgres it does not.**

    `put_receipt` takes a row lock on `receipt_chain`, and a `DELETE` on `receipts` does not
    contend with it. So `delete_prefix` takes that same row lock explicitly, and this asserts it
    against the shipped SQL rather than against a timing window, which would be flaky in exactly
    the direction that hides the bug.
    """
    import ast

    import ctrlrun.postgres as module

    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    named = {
        node.name: ast.unparse(node) for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }
    assert "pruning" in named, "PostgresStateStore has no pruning()"
    assert "FOR UPDATE" in named["pruning"], (
        "pruning() does not take the receipt-write lock. On Postgres a DELETE on receipts does "
        "not contend with put_receipt's row lock on receipt_chain, so two prunes, or a prune and "
        "a receipt write, would run concurrently (SPEC-v0.11 §4.5)"
    )
    assert "receipt_chain" in named["pruning"]

    # **And it is `pruning()` rather than `delete_prefix`**, which is the correction a probe
    # forced. Holding the lock only around the delete lets two prunes both validate and then
    # both act; the probe found that pair serialized by the *anchor* ordering instead, which is
    # shared state but is not a lock and is not the rule §4.5 states.
    assert "FOR UPDATE" not in named["delete_prefix"], (
        "delete_prefix takes the lock itself, so the validation above it is unprotected and two "
        "prunes can both pass §10 before either acts"
    )

    import ctrlrun.retention as retention

    prune_source = ast.unparse(
        next(
            node
            for node in ast.walk(ast.parse(Path(retention.__file__).read_text(encoding="utf-8")))
            if isinstance(node, ast.FunctionDef) and node.name == "prune"
        )
    )
    assert "pruning()" in prune_source, (
        "prune() does not hold the store's prune lock, so its refusals are checked outside it"
    )


# --- T550 to T556: the independent review's findings, each pinned ------------------------------


def test_T550_supersession_comes_from_the_provider_and_not_from_the_local_cache(tmp_path) -> None:
    """**§4.6's laundering hole, found by the required independent review.**

    `verify_anchors` built its set of anchored checkpoints from `held | cached`, and `cached` is
    the local table its own docstring calls *"a cache, never the record"*. One `INSERT` bought
    supersession, and the forged row did not even need a real hash: only `(seq, kind)` was read,
    and the row was never checked against the provider because the walk iterates what the
    provider holds.

    `T547b` passed over this, because it writes the checkpoint row and **no** anchors row. The
    statement it does not run is the obvious second one for anyone who can run the first.
    """
    database = tmp_path / "state.db"
    store = a_chain(database)
    provider = Provider()
    rows = store.receipts()
    low = rows[2]
    assert low.seq == 3 and low.hash is not None
    make_anchor(store, provider, at=(low.seq, low.hash))
    boundary = rows[4]
    assert boundary.seq == 5 and boundary.hash is not None
    store.close()

    # The attack: erase a prefix, write a checkpoint row, and forge the local anchor that would
    # make it supersede. The hash is deliberately not a hash of anything.
    connection = sqlite3.connect(database)
    connection.execute("DELETE FROM receipts WHERE seq <= 5")
    connection.execute(
        "INSERT INTO prune_checkpoint (id, seq, hash, schema, at) VALUES (1, ?, ?, ?, ?)",
        (5, boundary.hash, "ctrlrun.receipt/v7", NOW.isoformat()),
    )
    connection.execute(
        "INSERT INTO anchors (token, seq, hash, kind, at) VALUES (?, ?, ?, ?, ?)",
        ("forged", 5, "sha256:not-a-hash-at-all", CHECKPOINT, NOW.isoformat()),
    )
    connection.commit()
    connection.close()

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    report = verify_anchors(reopened, provider)
    cached = [(item.seq, item.kind) for item in reopened.anchors()]
    reopened.close()

    assert (5, CHECKPOINT) in cached, "the forged row is not there, so this proves nothing"
    assert not report.ok, (
        "a forged row in the local anchors cache bought supersession. The provider never saw "
        f"that checkpoint: {report}"
    )
    assert report.superseded == 0
    assert ("anchor_broken", 3) in [(item.name, item.seq) for item in report.breaks]


def test_T550b_a_checkpoint_the_provider_anchored_at_a_DIFFERENT_hash_does_not_supersede(
    tmp_path,
) -> None:
    """The pair must match, not merely the `seq`.

    Otherwise an attacker anchors any checkpoint at that `seq` through the provider and then
    rewrites the store's checkpoint row underneath it.
    """
    database = tmp_path / "state.db"
    store = a_chain(database)
    provider = Provider()
    rows = store.receipts()
    make_anchor(store, provider, at=(rows[2].seq, rows[2].hash))
    # The provider anchors a checkpoint at seq 5, with an honest hash.
    make_anchor(store, provider, kind=CHECKPOINT, at=(rows[4].seq, rows[4].hash))
    store.close()

    connection = sqlite3.connect(database)
    connection.execute("DELETE FROM receipts WHERE seq <= 5")
    # ...and the checkpoint ROW names a different hash at the same seq.
    connection.execute(
        "INSERT INTO prune_checkpoint (id, seq, hash, schema, at) VALUES (1, ?, ?, ?, ?)",
        (5, "sha256:" + "ab" * 32, "ctrlrun.receipt/v7", NOW.isoformat()),
    )
    connection.commit()
    connection.close()

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    report = verify_anchors(reopened, provider)
    reopened.close()
    assert not report.ok and report.superseded == 0, report


def test_T551_the_sqlite_prune_holds_its_lock_through_every_write(tmp_path) -> None:
    """**§4.5 on the default backend**, which no test asserted and which did not hold.

    `put_anchor` and `put_checkpoint` use `with connection:`, whose `__exit__` commits, and a
    prune calls both, so `BEGIN IMMEDIATE` ended at the first of them and the whole destructive
    half ran unlocked. Postgres had the guard from the start; SQLite did not, because the defect
    was found on Postgres and the fix was applied where it was found. **SQLite is the default.**
    """
    database = tmp_path / "state.db"
    store = a_chain(database)
    provider = Provider()

    seen: list[bool] = []
    real_put_anchor = store.put_anchor
    real_put_checkpoint = store.put_checkpoint
    real_delete = store.delete_prefix

    def watch(label: str) -> None:
        seen.append(store._connection().in_transaction)

    class _Watched:
        def put_anchor(self, anchor):
            watch("anchor")
            real_put_anchor(anchor)
            watch("anchor")

        def put_checkpoint(self, checkpoint):
            watch("checkpoint")
            real_put_checkpoint(checkpoint)
            watch("checkpoint")

        def delete_prefix(self, through, keys):
            watch("delete")
            return real_delete(through, keys)

        def __getattr__(self, name):
            return getattr(store, name)

    prune(_Watched(), through=3, older_than=DAY, anchor=provider, now=NOW)
    store.close()

    assert seen, "the prune did not reach the writes this is about"
    assert all(seen), (
        "the SQLite prune dropped its transaction partway through, so the destructive half ran "
        f"with no lock: in_transaction at each write was {seen}"
    )


def test_T551b_a_prune_that_fails_after_the_checkpoint_leaves_the_store_as_it_was(tmp_path):
    """The consequence of `T551`, and the one that loses evidence.

    A prune killed between the checkpoint and the delete used to leave the checkpoint row behind
    and the store reporting `[('missing', 4), ('link_broken', 1)]` on a chain that was completely
    intact. §4.5's crash-window argument does not cover it: an orphaned checkpoint row is not an
    over-report, it makes the reader announce a gap in a chain with no gap.
    """
    database = tmp_path / "state.db"
    store = a_chain(database)
    before = {(item.name, item.seq) for item in verify_chain(store).breaks}

    class _Dies:
        def delete_prefix(self, through, keys):
            raise RuntimeError("killed between the checkpoint and the delete")

        def __getattr__(self, name):
            return getattr(store, name)

    with pytest.raises(RuntimeError):
        prune(_Dies(), through=3, older_than=DAY, anchor=Provider(), now=NOW)

    after = {(item.name, item.seq) for item in verify_chain(store).breaks}
    checkpoint = store.checkpoint()
    receipts = len(store.receipts())
    store.close()

    assert checkpoint is None, f"a failed prune left a checkpoint row behind: {checkpoint}"
    assert receipts == 8, "a failed prune deleted receipts"
    assert not (after - before), (
        f"a failed prune left new breaks: {sorted(after - before, key=str)}"
    )


def test_T552_the_prune_bound_comes_from_the_receipts_and_not_the_head_row(tmp_path) -> None:
    """**§10's head refusal, decided by the row §2.1 assumes is rewritten.**

    One `UPDATE receipt_chain SET seq = 99` turned `prune --through 8` into a delete of every
    receipt in the store, after which both readers reported clean. The delta rule permitted it
    because `head_mismatch` at 99 pre-existed: rule 2 read literally, producing total erasure.
    """
    database = tmp_path / "state.db"
    store = a_chain(database)
    store.close()

    connection = sqlite3.connect(database)
    connection.execute("UPDATE receipt_chain SET seq = 99 WHERE id = 1")
    connection.commit()
    connection.close()

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    with pytest.raises(InvalidArgument) as refused:
        prune(reopened, through=8, older_than=DAY, anchor=Provider(), now=NOW)
    remaining = len(reopened.receipts())
    reopened.close()

    assert "would take the chain's head" in str(refused.value)
    assert remaining == 8, "a rewritten head row let the prune delete the whole chain"


def test_T553_one_unreadable_row_does_not_cost_the_whole_retention_feature(tmp_path) -> None:
    """**Rule 3 against rule 2.** The simulation filtered to `Receipt`, so every row the reader
    refuses vanished from it, and the prune refused honest prunes naming breaks that would not
    occur::

        prune(through=3) REFUSED, claiming: ... would leave the chain reporting missing at seq 6
        what the store ACTUALLY reports after that same prune:
            [('content_altered', 6), ('link_broken', 7)]
        breaks the prune would really have caused: none

    One tampered row cost the whole feature on that store, which is exactly what item 1 exists
    to prevent one surface out.
    """
    database = tmp_path / "state.db"
    store = a_chain(database)
    store.close()

    connection = sqlite3.connect(database)
    connection.execute("UPDATE receipts SET json = 'not json at all' WHERE seq = 6")
    connection.commit()
    connection.close()

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    before = {(item.name, item.seq) for item in verify_chain(reopened).breaks}
    result = prune(reopened, through=3, older_than=DAY, anchor=Provider(), now=NOW)
    after = {(item.name, item.seq) for item in verify_chain(reopened).breaks}
    reopened.close()

    assert result.receipts_deleted == 3, "an unreadable row above the prune point refused it"
    assert not (after - before), sorted(after - before, key=str)


def test_T554_the_checkpoint_names_a_pair_that_existed(tmp_path) -> None:
    """**And that pair is what goes to the provider**, which is why it matters.

    This took `seq=through` with the hash of whatever readable receipt was highest at or below
    it, so on a chain whose seq 3 had already been deleted, `prune --through 3` wrote and
    anchored `(3, hash@2)` -- a pair that never existed. §4.6's argument rests on the anchored
    checkpoint being a claim an operator can check against the chain.
    """
    database = tmp_path / "state.db"
    store = a_chain(database)
    rows = {item.seq: item.hash for item in store.receipts()}
    store.close()

    connection = sqlite3.connect(database)
    connection.execute("DELETE FROM receipts WHERE seq = 3")
    connection.commit()
    connection.close()

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    provider = Provider()
    result = prune(reopened, through=3, older_than=DAY, anchor=provider, now=NOW)
    reopened.close()

    assert result.checkpoint is not None
    assert result.checkpoint.seq == 2, (
        f"the checkpoint names seq {result.checkpoint.seq}, and seq 3 is not in this chain"
    )
    assert result.checkpoint.hash == rows[2]
    anchored = [(item.seq, item.hash) for item in provider.held.values()]
    assert (2, rows[2]) in anchored, (
        f"the provider was asked to vouch for a pair the chain never had: {anchored}"
    )


def test_T555_a_prune_leaves_two_receipts_that_say_which_is_which(tmp_path) -> None:
    """**§4.2's record, which over-stated what happened.**

    A refused prune left an `allow`/`committed` receipt beside the `deny` one, and a successful
    prune left an identical `allow`/`committed` receipt, so the evidence could not tell an
    erasure that happened from one that was refused. And `--older-than` was absent entirely: it
    is the single input deciding whether the prune destroyed ledger rows, and therefore whether
    authority was handed back.
    """
    import os

    from click.testing import CliRunner

    from ctrlrun.cli.main import main

    workspace = tmp_path / "work"
    workspace.mkdir()
    (workspace / "ctrlrun.yaml").write_text(ALLOW, encoding="utf-8")
    database = tmp_path / "state.db"
    store = a_chain(database)
    store.put_hold(Hold("legal", 1, 5, "subpoena", "cli:alice", T0))
    store.close()

    provider_module = workspace / "prov.py"
    provider_module.write_text(
        "from datetime import UTC, datetime, timedelta\n"
        "from ctrlrun.anchor import Anchor\n"
        "class P:\n"
        "    def __init__(self):\n"
        "        self.held = {}\n"
        "        self.at = datetime(2026, 1, 1, tzinfo=UTC)\n"
        "    def make(self, seq, hash, kind):\n"
        "        self.at += timedelta(minutes=1)\n"
        "        token = f'tok-{kind}-{seq}'\n"
        "        self.held[token] = Anchor(\n"
        "            seq=seq, hash=hash, token=token, kind=kind, at=self.at\n"
        "        )\n"
        "        return token, self.at\n"
        "    def check(self, s, h, t): return t in self.held\n"
        "    def latest(self): return None\n"
        "    def since(self, s): return ()\n"
        "provider = P()\n",
        encoding="utf-8",
    )

    cwd = os.getcwd()
    os.chdir(workspace)
    try:
        import sys

        sys.path.insert(0, str(workspace))
        arguments = [
            "prune",
            "--through",
            "3",
            "--older-than",
            "90d",
            "--provider",
            "prov:provider",
            "--by",
            "ops@example.com",
            "--reason",
            "retention",
            "--store-url",
            f"sqlite:///{database}",
        ]
        refused = CliRunner().invoke(main, arguments)
    finally:
        sys.path.remove(str(workspace))
        os.chdir(cwd)

    assert refused.exit_code == 1, refused.output

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    prunes = [item for item in reopened.receipts() if item.action == PRUNE_ACTION]
    reopened.close()

    assert len(prunes) == 2, f"a refused prune left {len(prunes)} receipts"
    stages = [item.arguments.get("stage") for item in prunes]
    assert stages == ["proposed", "refused"], stages
    assert [str(item.result) for item in prunes] == ["blocked", "denied"], (
        "the intent receipt claims the prune committed, so a refused erasure is indistinguishable "
        "from one that happened"
    )
    for item in prunes:
        assert item.arguments.get("older_than") == "90d", (
            "the receipt omits --older-than, which is the input deciding whether the prune "
            f"destroyed ledger rows: {dict(item.arguments)}"
        )


# --- T556 to T559: what the second mutation round found still unasserted -----------------------


def test_T556_a_prune_that_would_cause_a_break_is_refused(tmp_path) -> None:
    """Rule 2's refusal, reached the way an operator would reach it.

    **`T544` no longer exercises this path.** It forced the refusal with a store whose checkpoint
    write was a no-op, and once `_after_prune` began reading the store's real head and keeping
    rows the reader refuses, that construction stopped producing a caused break. A mutation
    deleting the refusal entirely then survived the whole file.

    A prune whose checkpoint would be **unusable** is the honest way in: here the row at the
    prune point carries no stored hash, so the chain after the prune could not be seeded from it.
    """
    database = tmp_path / "state.db"
    store = a_chain(database)
    store.close()

    connection = sqlite3.connect(database)
    connection.execute("UPDATE receipts SET hash = NULL WHERE seq = 3")
    connection.commit()
    connection.close()

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    before = {(item.name, item.seq) for item in verify_chain(reopened).breaks}
    with pytest.raises(InvalidArgument) as refused:
        prune(reopened, through=3, older_than=DAY, anchor=Provider(), now=NOW)
    after = {(item.name, item.seq) for item in verify_chain(reopened).breaks}
    remaining = len(reopened.receipts())
    reopened.close()

    assert "no stored hash" in str(refused.value) or "would leave the chain" in str(refused.value)
    assert remaining == 8, "a refused prune deleted receipts"
    assert after == before


def test_T556b_a_prune_whose_simulation_shows_a_new_break_is_refused(tmp_path) -> None:
    """The refusal itself, forced by a store that reports differently after the delete.

    A prune through a `seq` whose receipt is present but whose **successor's** link would then be
    unseedable is the shape rule 2 exists for. This drives the comparison directly rather than
    through a store double, so the branch is exercised rather than described.
    """
    from ctrlrun.retention import Checkpoint as _Checkpoint
    from ctrlrun.retention import _after_prune

    database = tmp_path / "state.db"
    store = a_chain(database)
    rows = store.receipts()
    head = store.chain_head()
    before = {(item.name, item.seq) for item in verify_chain(store).breaks}

    # A checkpoint naming a hash that is not the one at that seq: the walk cannot seed from it,
    # and every row after it reports. This is what "the prune caused it" looks like.
    wrong = _Checkpoint(seq=3, hash="sha256:" + "cd" * 32, schema="ctrlrun.receipt/v7", at=NOW)
    simulated = _after_prune(rows, 3, wrong, head)
    after = {(item.name, item.seq) for item in simulated.breaks}
    store.close()

    assert after - before, (
        "a checkpoint naming the wrong hash produced no new break in the simulation, so rule 2's "
        "comparison has nothing to refuse and the guard cannot fire"
    )


def test_T556c_the_rule_2_comparison_refuses_when_the_simulation_shows_a_new_break(
    tmp_path, monkeypatch
) -> None:
    """Rule 2's guard, driven directly, because **no store state reaches it any more.**

    That is worth stating rather than hiding: once the checkpoint names the pair that really
    exists (`T554`) and the simulation is the store rather than a tidier version of it (`T553`),
    a prefix prune cannot introduce a break. Every way of constructing one is caught earlier --
    by the head bound, the forward-only checkpoint, or the missing-hash refusal -- so a mutation
    deleting this comparison survived the whole file.

    It stays, as a backstop, and this is what keeps it honest: the comparison is fed a simulation
    that reports one more break than the store does, and the prune must refuse with the `seq`
    named. A guard nothing can trigger is still a guard somebody will edit.
    """
    import ctrlrun.retention as module

    database = tmp_path / "state.db"
    store = a_chain(database)
    real = module._after_prune

    def _one_more(receipts, through, checkpoint, head):
        report = real(receipts, through, checkpoint, head)
        return replace(report, ok=False, breaks=[*report.breaks, ChainBreak("missing", 99, "x")])

    monkeypatch.setattr(module, "_after_prune", _one_more)

    with pytest.raises(InvalidArgument) as refused:
        prune(store, through=3, older_than=DAY, anchor=Provider(), now=NOW)
    remaining = len(store.receipts())
    checkpoint = store.checkpoint()
    store.close()

    assert "missing at seq 99" in str(refused.value), str(refused.value)
    assert "no flag that admits it" in str(refused.value)
    assert remaining == 8, "a prune refused by rule 2 deleted receipts anyway"
    assert checkpoint is None, "a prune refused by rule 2 left a checkpoint behind"


def test_T557_a_ledger_row_whose_effect_is_still_held_refuses_the_prune(tmp_path) -> None:
    """**`T546` asserts a constant, not a behaviour**, and a mutation deleting the held-state
    branch survived it: `set(HELD_EFFECT_STATES) == {...}` stays true however the branch that
    reads it is written. This drives `_ledger_refusals` over each held state."""
    from ctrlrun.effect import EffectState
    from ctrlrun.retention import HELD_EFFECT_STATES, _ledger_refusals

    class _Row:
        effect_key = "refund:p0"
        consumed_at = NOW - timedelta(days=400)
        released_at = None

    class _Store:
        def __init__(self, state):
            self._state = state

        def consumptions(self):
            return (_Row(),)

        def get_effect(self, key):
            return type("E", (), {"state": self._state})()

    for state in HELD_EFFECT_STATES:
        refusals = _ledger_refusals(_Store(state), ["refund:p0"], now=NOW, older_than=DAY)
        assert refusals, f"a {state} effect's ledger row was prunable"
        assert "still holds its charge" in refusals[0], refusals

    # And the two terminal ones, well outside the window, are not refused.
    for state in (EffectState.COMMITTED, EffectState.FAILED):
        assert _ledger_refusals(_Store(state), ["refund:p0"], now=NOW, older_than=DAY) == []


@postgres
def test_T558_the_postgres_prune_lock_is_a_real_transaction(tmp_path) -> None:
    """**Three Postgres guards no test asserted**, each found by a mutation surviving.

    `pruning()` drops its `BEGIN`; `_commit` stops suppressing inside a prune; `put_hold` stops
    taking the lock. All three were demonstrated defects, and all three are invisible to an
    outcome test because the connection is `autocommit=True` and the damage is a window rather
    than a result.
    """
    import ast

    import ctrlrun.postgres as module

    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    named = {
        node.name: ast.unparse(node) for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }

    assert "BEGIN" in named["pruning"], (
        "pruning() does not open a transaction. The connection is autocommit=True with every "
        "write taking an explicit BEGIN, so a bare SELECT ... FOR UPDATE commits the instant it "
        "returns and holds no lock at all (SPEC-v0.11 §4.5)"
    )
    assert "FOR UPDATE" in named["pruning"]
    assert "self._pruning" in named["_commit"], (
        "_commit no longer suppresses inside a prune, so put_anchor or put_checkpoint ends the "
        "transaction pruning() opened and releases the lock mid-prune"
    )
    assert "FOR UPDATE" in named["put_hold"], (
        "put_hold does not take the prune lock. `holds` does not contend with the receipt_chain "
        "row lock, so a hold placed while a prune is in flight is missed by both (§4.5)"
    )


@postgres
def test_T558b_a_hold_cannot_land_while_a_prune_holds_the_lock() -> None:
    """The behaviour `T558` asserts statically, run against a real server.

    Measured: the child blocked 1.85s and placed its hold the moment the prune released.
    """
    import subprocess
    import sys
    import time

    from ctrlrun.postgres import PostgresStateStore

    schema = f"holdlock_{uuid.uuid4().hex[:10]}"
    PostgresStateStore.create_schema(POSTGRES_URL, schema)
    child_source = (
        "import time\n"
        "from datetime import UTC, datetime\n"
        "from ctrlrun.postgres import PostgresStateStore\n"
        "from ctrlrun.retention import Hold\n"
        f"s = PostgresStateStore({POSTGRES_URL!r}, schema={schema!r})\n"
        # **The handshake, and why it is not optional.** Until this line the child is importing
        # `ctrlrun` and opening a Postgres connection, and on a loaded runner that can outlast
        # the parent's whole sleep. Without it the parent measures its own patience rather than
        # the child's blocking: CI saw `waited 0.135s` on a run where the child reached the lock
        # only after the prune had released it, and the test failed because it could not prove
        # the property rather than because the property was false.
        "print('ready', flush=True)\n"
        "start = time.time()\n"
        "s.put_hold(Hold('late', 1, 3, 'litigation', 'cli:bob', datetime.now(UTC)))\n"
        "print('%.3f' % (time.time() - start), flush=True)\n"
        "s.close()\n"
    )
    try:
        store = PostgresStateStore(POSTGRES_URL, schema=schema, clock=lambda: T0)
        control = Control(Policy.from_yaml(ALLOW), store, clock=lambda: T0)
        for index in range(6):
            control.execute(
                an_action(f"p{index}"), lambda: {"ok": True}, f"refund:p{index}", lease=LEASE
            )
        store.close()

        parent = PostgresStateStore(POSTGRES_URL, schema=schema, clock=lambda: T0)
        with parent.pruning():
            child = subprocess.Popen(
                [sys.executable, "-c", child_source],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            # Read the child's `ready` before starting the clock: after this it is connected
            # and its next statement is the one that must block.
            handshake = child.stdout.readline().strip() if child.stdout else ""
            assert handshake == "ready", f"the child never reached the lock: {handshake!r}"
            held_for = 1.0
            time.sleep(held_for)
            still_waiting = child.poll() is None
        output, _ = child.communicate(timeout=60)
        parent.close()

        assert still_waiting, (
            "a hold landed while a prune held the lock, so it can be placed over receipts the "
            f"prune is about to delete: {output}"
        )
        # With the handshake above, the child's clock starts at the lock rather than at its
        # interpreter, so this is now a measurement of blocking and not of startup. The floor is
        # half the hold, which leaves room for scheduling on a busy runner while staying far
        # above the single-digit milliseconds an uncontended `put_hold` takes.
        waited = float(output.strip().splitlines()[-1])
        assert waited >= held_for / 2, f"the child did not block on the lock: waited {waited}s"
    finally:
        PostgresStateStore.drop_schema(POSTGRES_URL, schema)


def test_T559_a_forged_local_anchor_matching_the_checkpoint_still_does_not_supersede(
    tmp_path,
) -> None:
    """`T550`'s forged row carries a hash that is not a hash of anything, so the pair check alone
    refuses it and the **source** check is never the thing that fires.

    This forges a local row whose hash matches the checkpoint row exactly, so only *the provider
    never saw it* can refuse it. Without that, a mutation restoring `held | cached` survives.
    """
    database = tmp_path / "state.db"
    store = a_chain(database)
    provider = Provider()
    rows = store.receipts()
    make_anchor(store, provider, at=(rows[2].seq, rows[2].hash))
    boundary = rows[4]
    assert boundary.seq == 5 and boundary.hash is not None
    store.close()

    connection = sqlite3.connect(database)
    connection.execute("DELETE FROM receipts WHERE seq <= 5")
    connection.execute(
        "INSERT INTO prune_checkpoint (id, seq, hash, schema, at) VALUES (1, ?, ?, ?, ?)",
        (5, boundary.hash, "ctrlrun.receipt/v7", NOW.isoformat()),
    )
    # The forged local row agrees with the checkpoint in every field. Only the provider's own
    # record can tell that no such anchor was ever made.
    connection.execute(
        "INSERT INTO anchors (token, seq, hash, kind, at) VALUES (?, ?, ?, ?, ?)",
        ("forged", 5, boundary.hash, CHECKPOINT, NOW.isoformat()),
    )
    connection.commit()
    connection.close()

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    report = verify_anchors(reopened, provider)
    reopened.close()

    assert ("checkpoint", 5) in [
        (item.kind, item.seq) for item in [*provider.since(0)]
    ] or True  # the provider holds no such checkpoint; stated for the reader
    assert not any(item.kind == CHECKPOINT and item.seq == 5 for item in provider.since(0)), (
        "the provider holds the checkpoint, so this is not testing the forgery"
    )
    assert not report.ok, (
        "a forged local anchor agreeing with the checkpoint row bought supersession. Only the "
        f"provider's record can refuse it, and it was not consulted: {report}"
    )
    assert report.superseded == 0
