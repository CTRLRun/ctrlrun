# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""The anchor, and the two things it makes detectable. SPEC-v0.11 §2, §3; T530-T541.

**The attack is the deliverable and it is written first.** The receipt chain detects alteration.
It does not detect truncation, because the head that would catch it is a row in the same
database. Measured at `main` before this item, on a six-receipt chain, in two statements::

    DELETE FROM receipts WHERE seq > 3
    UPDATE receipt_chain SET seq = ?, hash = ?

    two SQL statements: ok=True verified=3 breaks=[]

Three receipts erased, and the chain reports itself intact.

**The bounded claim is tested, not merely written** (`T531`). An anchor freezes a *prefix*: an
append lands at head + 1, above every anchored `seq`, so no anchored pair stops reproducing and a
later anchor freezes the forged chain as readily as an honest one. That is the first thing in
this project a reader could mistake for tamper-proofing, so `T531` runs a forged append and
requires **both** reports to stay clean.

`ANCHOR_BREAKS` is its own closed set and `CHAIN_BREAKS` does not change (`T541`, §3.4).
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ctrlrun import Control, Policy, SQLiteStateStore
from ctrlrun.action import Action, Principal
from ctrlrun.anchor import (
    ANCHOR_BREAKS,
    CHECKPOINT,
    INTERVAL,
    Anchor,
    make_anchor,
    verify_anchors,
)
from ctrlrun.errors import InvalidArgument
from ctrlrun.receipt import _document_hash, verify_chain

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
LEASE = timedelta(minutes=5)
ALLOW = "schema: ctrlrun.policy/v1\nactions:\n  stripe.refund:\n    decision: allow\n"

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
    """An operator's anchor provider, as small as the protocol allows (§3.2).

    **Outside the store**, which is the entire point: nothing a `DELETE` against the database
    reaches can change what this holds. `raise_on` makes it unreachable, which is a transport
    failure and not a finding about the evidence (§3.4).
    """

    def __init__(self, *, raise_on: tuple[str, ...] = (), disown: bool = False) -> None:
        self.held: dict[str, Anchor] = {}
        self.at = T0
        self.raise_on = raise_on
        self.disown = disown
        self.checks: list[tuple[int, str]] = []

    def _maybe_raise(self, call: str) -> None:
        if call in self.raise_on:
            raise ConnectionError(f"the timestamp authority is unreachable ({call})")

    def make(self, seq: int, hash: str, kind: str) -> tuple[str, datetime]:
        self._maybe_raise("make")
        self.at += timedelta(minutes=1)
        token = f"tok-{kind}-{seq}"
        self.held[token] = Anchor(seq=seq, hash=hash, token=token, kind=kind, at=self.at)
        return token, self.at

    def check(self, seq: int, hash: str, token: str) -> bool:
        self._maybe_raise("check")
        self.checks.append((seq, token))
        if self.disown:
            return False
        held = self.held.get(token)
        return held is not None and held.seq == seq and held.hash == hash

    def latest(self) -> tuple[int, str] | None:
        self._maybe_raise("latest")
        if not self.held:
            return None
        best = max(self.held.values(), key=lambda item: item.seq)
        return (best.seq, best.token)

    def since(self, seq: int) -> tuple[Anchor, ...]:
        self._maybe_raise("since")
        return tuple(item for item in self.held.values() if item.seq >= seq)


def _rewrite_chain_from(database: Path, *, at: int, find: str, replace: str) -> None:
    """Alter one receipt and recompute every hash after it, plus the head.

    An administrator with write access who edits one row and leaves the hashes is caught by
    `verify_chain`; one who recomputes is not, and `THREAT_MODEL.md` has listed them as out of
    scope for the chain since v0.6. The anchor is what narrows that, for everything at or below
    an anchored `seq`.
    """
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT seq, json, prev_hash FROM receipts WHERE seq IS NOT NULL ORDER BY seq"
    ).fetchall()
    previous: str | None = None
    for row in rows:
        document = json.loads(row["json"])
        if row["seq"] == at:
            document = json.loads(row["json"].replace(find, replace))
            assert document != json.loads(row["json"]) or find not in row["json"], (
                f"the tamper {find!r} changed nothing at seq {at}"
            )
        if previous is not None:
            document["prev_hash"] = previous
        digest = _document_hash(document)
        connection.execute(
            "UPDATE receipts SET json = ?, prev_hash = ?, hash = ? WHERE seq = ?",
            (
                json.dumps(document, separators=(",", ":"), ensure_ascii=False),
                document.get("prev_hash"),
                digest,
                row["seq"],
            ),
        )
        previous = digest
    connection.execute(
        "UPDATE receipt_chain SET seq = ?, hash = ? WHERE id = 1", (rows[-1]["seq"], previous)
    )
    connection.commit()
    connection.close()


def a_chain(database: Path, count: int = 6) -> SQLiteStateStore:
    store = SQLiteStateStore(database, clock=lambda: T0)
    control = Control(Policy.from_yaml(ALLOW), store, clock=lambda: T0)
    for index in range(count):
        control.execute(
            an_action(f"p{index}"), lambda: {"ok": True}, f"refund:p{index}", lease=LEASE
        )
    return store


def truncate(database: Path, through: int) -> None:
    """§2.1's attack, in SQL, underneath the store: erase a suffix and fix the head.

    Two statements, which is the number that makes this invisible to the chain. Anything more
    would be testing a clumsier attacker than the one the threat model describes.
    """
    connection = sqlite3.connect(database)
    connection.execute("DELETE FROM receipts WHERE seq > ?", (through,))
    row = connection.execute("SELECT seq, hash FROM receipts ORDER BY seq DESC LIMIT 1").fetchone()
    connection.execute("UPDATE receipt_chain SET seq = ?, hash = ? WHERE id = 1", row)
    connection.commit()
    connection.close()


# --- T530: the deliverable ---------------------------------------------------------------------


def test_T530_a_truncation_past_an_anchored_seq_is_named(tmp_path) -> None:
    """SPEC-v0.11 §2.1, §3.4. **The attack this milestone exists for.**

    The negative control comes first: the same two statements against the same store with **no**
    anchor, so this test shows what is being fixed rather than asserting it. Without that half,
    every row below would pass against an anchor that reports `anchor_broken` unconditionally.
    """
    database = tmp_path / "state.db"
    store = a_chain(database)
    store.close()

    # (a) Without an anchor: the chain reports itself intact after three receipts are erased.
    truncate(database, through=3)
    unanchored = SQLiteStateStore(database, clock=lambda: T0)
    report = verify_chain(unanchored)
    unanchored.close()
    assert report.ok, (
        "§2.1 says a truncation with the head fixed is undetected, and this store detected it; "
        "the attack this item answers is not the attack being run"
    )
    assert report.verified == 3

    # (b) With one. The same two statements, and now they are named.
    second = tmp_path / "anchored.db"
    store = a_chain(second)
    provider = Provider()
    anchor = make_anchor(store, provider)
    assert anchor.seq == 6 and anchor.kind == INTERVAL
    before = verify_anchors(store, provider)
    assert before.ok and before.checked == 1, before
    store.close()

    truncate(second, through=3)
    reopened = SQLiteStateStore(second, clock=lambda: T0)
    chain = verify_chain(reopened)
    anchors = verify_anchors(reopened, provider)
    reopened.close()

    assert chain.ok, "the chain still does not notice, which is why the anchor exists"
    assert not anchors.ok
    assert not anchors.unavailable, "a truncation is a finding, never a transport failure"
    named = [(item.name, item.seq) for item in anchors.breaks]
    assert ("anchor_broken", 6) in named, named


# --- T531: the bounded claim, run rather than written ------------------------------------------


def test_T531_a_forged_append_is_NOT_detected_and_that_is_the_claim(tmp_path) -> None:
    """SPEC-v0.11 §2.4. **An anchor freezes a prefix.**

    An appended row lands at head + 1, above every anchored `seq`, so no anchored pair stops
    reproducing. A later anchor freezes the forged chain as readily as an honest one.

    An earlier draft of §2.4 said the anchor closes "a suffix erased **or appended**", and a
    review ran it. This test is that review, kept: if somebody later makes the anchor claim more
    than it can do, this goes red and the documentation has to change with it.
    """
    database = tmp_path / "state.db"
    store = a_chain(database, 4)
    provider = Provider()
    make_anchor(store, provider)
    store.close()

    connection = sqlite3.connect(database)
    last = connection.execute(
        "SELECT json, hash, seq FROM receipts ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    document = json.loads(last[0])
    document["seq"] = last[2] + 1
    document["prev_hash"] = last[1]
    document["receipt_id"] = "ctr_" + "f" * 28
    document["arguments"] = {"payment_id": "FORGED", "amount": 999999}
    digest = _document_hash(document)
    connection.execute(
        "INSERT INTO receipts (receipt_id, action_id, ts, json, seq, prev_hash, hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            document["receipt_id"],
            document["action_id"],
            document["finished_at"],
            json.dumps(document, separators=(",", ":"), ensure_ascii=False),
            document["seq"],
            last[1],
            digest,
        ),
    )
    connection.execute(
        "UPDATE receipt_chain SET seq = ?, hash = ? WHERE id = 1", (document["seq"], digest)
    )
    connection.commit()
    connection.close()

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    chain = verify_chain(reopened)
    anchors = verify_anchors(reopened, provider)
    forged = [item for item in reopened.receipts() if item.receipt_id == document["receipt_id"]]
    reopened.close()

    assert forged, "the forged row was not inserted, so this test asserts nothing"
    assert chain.ok, "the chain detects an append after all; §2.4's table needs correcting"
    assert anchors.ok, (
        "the anchor detected an append. That is a stronger claim than SPEC-v0.11 §2.4 makes, and "
        "if it is now true the table, ROADMAP.md's paragraph and G28's title all have to say so"
    )


# --- T532: the third statement, and which break wins -------------------------------------------


def test_T532_deleting_the_local_anchor_row_too_still_reports_the_tamper(tmp_path) -> None:
    """SPEC-v0.3 §3.3 and §3.4's precedence rule.

    The local table is a **cache**, not a record: `verify_anchors` asks the provider what it holds
    before reading it, so deleting the row does not remove the question. An earlier design had
    `make` and `check` alone, and a review broke it in one extra statement, because the set of
    questions then came from the rewritable side.

    **`anchor_broken` wins.** Both apply here, and `anchor_missing` reads to an operator as a
    misconfiguration where `anchor_broken` reads as tamper. A first implementation of this
    reported only the milder one, found by running exactly this case.
    """
    database = tmp_path / "state.db"
    store = a_chain(database)
    provider = Provider()
    make_anchor(store, provider)
    store.close()

    truncate(database, through=3)
    connection = sqlite3.connect(database)
    connection.execute("DELETE FROM anchors")
    connection.commit()
    connection.close()

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    assert reopened.anchors() == (), "the local cache was not emptied, so this proves nothing"
    report = verify_anchors(reopened, provider)
    reopened.close()

    named = [(item.name, item.seq) for item in report.breaks]
    assert ("anchor_broken", 6) in named, named
    assert ("anchor_missing", 6) in named, named
    assert report.breaks[0].name == "anchor_broken", (
        f"the milder break is named first, so a tamper reads as a config ticket: {named}"
    )


def test_T532b_an_emptied_cache_alone_is_missing_and_not_broken(tmp_path) -> None:
    """The other half: the chain is intact and only the cache was trimmed.

    That is not tampering with the evidence, and calling it `anchor_broken` would be the false
    positive §3.4 refuses in the other direction.
    """
    database = tmp_path / "state.db"
    store = a_chain(database)
    provider = Provider()
    make_anchor(store, provider)
    store.close()

    connection = sqlite3.connect(database)
    connection.execute("DELETE FROM anchors")
    connection.commit()
    connection.close()

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    report = verify_anchors(reopened, provider)
    reopened.close()

    named = [(item.name, item.seq) for item in report.breaks]
    assert named == [("anchor_missing", 6)], named


# --- T533: G11's contract does not change ------------------------------------------------------


def test_T533_G11_passes_on_a_store_whose_anchor_report_carries_every_break(tmp_path) -> None:
    """SPEC-v0.11 §8.1, asserted directly rather than argued.

    **The first draft asserted this and was wrong.** It claimed a `ChainReport` could carry
    `anchor_broken` while `G11` passed; `G11`'s control reads `intact.ok` over the whole report,
    so one extra break of any kind fails it with `control failed`, the status that means the
    kernel is broken. Worse, `anchor_missing` fires on every anchoring deployment while verify's
    own scratch store never anchors, so the failure would have been universal.

    The separation is therefore **structural**: the anchor has its own report and its own closed
    set. This test is what makes that a property of the code rather than a paragraph.
    """
    database = tmp_path / "state.db"
    store = a_chain(database)
    provider = Provider()
    make_anchor(store, provider)
    store.close()

    truncate(database, through=3)
    connection = sqlite3.connect(database)
    connection.execute("DELETE FROM anchors")
    connection.commit()
    connection.close()

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    chain = verify_chain(reopened)
    anchors = verify_anchors(reopened, provider)
    reopened.close()

    assert not anchors.ok and len(anchors.breaks) >= 2
    # Every name the anchor reported is in ITS set and in no chain report.
    for item in anchors.breaks:
        assert item.name in ANCHOR_BREAKS, item.name
    assert chain.ok, (
        "an anchor break reached the ChainReport, which fails G11's control with `control "
        f"failed` on every anchoring deployment: {[(b.name, b.seq) for b in chain.breaks]}"
    )
    assert all(item.name not in ANCHOR_BREAKS for item in chain.breaks)


def test_T533b_the_two_closed_sets_do_not_overlap() -> None:
    """`CHAIN_BREAKS` stays closed at six and gains nothing (§3.4)."""
    from ctrlrun.receipt import CHAIN_BREAKS

    assert CHAIN_BREAKS == (
        "content_altered",
        "hash_missing",
        "link_broken",
        "missing",
        "head_mismatch",
        "unchained",
    ), "item 2 amended a frozen closed set; §3.4 says it does not"
    assert ANCHOR_BREAKS == ("anchor_broken", "anchor_missing", "anchor_repudiated")
    assert not set(CHAIN_BREAKS) & set(ANCHOR_BREAKS)
    # `anchor_unavailable` is deliberately in NEITHER: it is a transport failure, not a finding
    # about the evidence, and a set that conflated them would grade a briefly unreachable
    # timestamp authority indistinguishably from a truncation.
    assert "anchor_unavailable" not in ANCHOR_BREAKS
    assert "anchor_unavailable" not in CHAIN_BREAKS


# --- T534: fail-closed, in both directions -----------------------------------------------------


def test_T534_a_provider_that_raises_anchors_nothing(tmp_path) -> None:
    """§10's first row. An anchor half-made is worse than none: the local table would claim a
    pair the provider never saw, and every later verification would report `anchor_repudiated`
    about an honest store."""
    database = tmp_path / "state.db"
    store = a_chain(database)
    provider = Provider(raise_on=("make",))

    with pytest.raises(InvalidArgument) as refused:
        make_anchor(store, provider)

    assert "anchor_unavailable" in str(refused.value)
    assert store.anchors() == (), "an anchor was cached for a pair the provider never saw"
    store.close()


def test_T534b_an_unreachable_provider_at_verification_is_unavailable_not_broken(tmp_path):
    """§10's second row, and the distinction the whole of §3.4 turns on.

    **Refusing to act when you cannot ask is fail-closed; reporting tampering when you cannot ask
    is a false positive.** An earlier draft made this a break, so a briefly unreachable timestamp
    authority graded `G28` `fail`, indistinguishable in the report from a truncation.
    """
    database = tmp_path / "state.db"
    store = a_chain(database)
    provider = Provider()
    make_anchor(store, provider)

    provider.raise_on = ("since",)
    report = verify_anchors(store, provider)
    store.close()

    assert report.unavailable is True
    assert report.ok is False, "unavailable is not a pass either"
    assert report.breaks == [], (
        "an unreachable provider produced a break, so a network blip is indistinguishable from "
        f"a truncation: {[(b.name, b.seq) for b in report.breaks]}"
    )
    assert report.reason and "could not be reached" in report.reason


def test_T534c_a_configuration_that_anchors_and_holds_none_is_missing(tmp_path) -> None:
    """§3.4's row that the section turns on. An anchor that is never made would otherwise switch
    the check off by being absent, which is `SPEC-v0.4 §3.8`'s false green."""
    database = tmp_path / "state.db"
    store = a_chain(database)
    report = verify_anchors(store, Provider())
    store.close()

    assert not report.ok
    assert not report.unavailable
    assert [item.name for item in report.breaks] == ["anchor_missing"]


def test_T534d_the_ordinary_window_between_anchors_is_not_a_break(tmp_path) -> None:
    """§2.4 and §3.4. **`anchor_missing` does not fire on the exposed window.**

    An earlier draft read *"the store's chain reaches a `seq` none of them covers"*, which is the
    state every honest deployment lives in between anchors. A review measured it, one honest
    action after an anchor::

        A. honest deployment, anchor just taken at head 4   -> []
        B. same deployment, ONE honest action later
           -> [('anchor_missing', 5, 'the chain reaches seq 5; ...')]

    `G28` would have failed on every anchoring deployment except in the instant after an anchor.
    **A fail-closed check that fires on the honest case is not fail-closed, it is broken.**
    """
    database = tmp_path / "state.db"
    store = a_chain(database, 4)
    provider = Provider()
    make_anchor(store, provider)
    assert verify_anchors(store, provider).ok, "an anchor just taken does not reproduce"

    # One honest action later, which is where every deployment spends its time.
    control = Control(Policy.from_yaml(ALLOW), store, clock=lambda: T0)
    control.execute(an_action("later"), lambda: {"ok": True}, "refund:later", lease=LEASE)
    report = verify_anchors(store, provider)
    store.close()

    assert report.ok, (
        "the ordinary window between anchors was reported as a break, so this check fires on "
        f"every honest deployment: {[(b.name, b.seq) for b in report.breaks]}"
    )
    assert report.breaks == []


def test_T534e_a_provider_that_disowns_a_pair_is_repudiated(tmp_path) -> None:
    """§3.4. `anchor_repudiated` exists because the outside record is **allowed to say no**, and
    that is its one substantive answer and the entire reason for holding it outside the store.
    The first draft's set had no name for `check()` returning false."""
    database = tmp_path / "state.db"
    store = a_chain(database)
    provider = Provider()
    make_anchor(store, provider)

    provider.disown = True
    report = verify_anchors(store, provider)
    store.close()

    assert not report.ok and not report.unavailable
    assert [(item.name, item.seq) for item in report.breaks] == [("anchor_repudiated", 6)]
    assert provider.checks, "check() was never called, so the provider was never asked"


# --- T535: the orderings, which are per kind ---------------------------------------------------


def test_T535_an_interval_anchor_must_be_above_the_last_interval_anchor(tmp_path) -> None:
    database = tmp_path / "state.db"
    store = a_chain(database, 3)
    provider = Provider()
    make_anchor(store, provider)

    with pytest.raises(InvalidArgument) as refused:
        make_anchor(store, provider)

    assert "must be above the last interval anchor" in str(refused.value)
    store.close()


def test_T535b_a_checkpoint_anchor_is_ordered_only_against_other_checkpoints(tmp_path) -> None:
    """§3.2. **The two kinds are ordered separately**, and a joint ordering refused the one anchor
    §4.6 requires.

    A deployment anchoring hourly and pruning at ninety days makes its checkpoint anchor far
    *below* its newest interval anchor. A draft that ordered all anchors by `seq` refused it, so
    the prune was refused, **forever**, and §4.6 exists precisely so an anchoring deployment does
    not have to choose between pruning and a permanent tamper signal.
    """
    database = tmp_path / "state.db"
    store = a_chain(database, 6)
    provider = Provider()
    interval = make_anchor(store, provider)
    assert interval.seq == 6

    # A checkpoint far below the newest interval anchor, which is the shape a prune produces.
    low = Anchor(
        seq=2,
        hash=store.receipts()[1].hash or "",
        token="tok-checkpoint-2",
        kind=CHECKPOINT,
        at=T0 + timedelta(hours=1),
    )
    provider.held[low.token] = low
    store.put_anchor(low)

    # It is accepted, and a SECOND checkpoint at or below it is not.
    assert {item.kind for item in store.anchors()} == {INTERVAL, CHECKPOINT}
    with pytest.raises(InvalidArgument) as refused:
        make_anchor(store, provider, kind=CHECKPOINT)
    store.close()
    assert "checkpoint" in str(refused.value)


def test_T535c_an_anchor_whose_time_runs_backwards_is_refused(tmp_path) -> None:
    """§3.2. A monotonic sequence is the only property the kernel can check about a timestamp it
    did not issue, and a sequence that goes backwards is either a misconfiguration or the attack;
    ctrlrun cannot tell which, so it refuses."""
    database = tmp_path / "state.db"
    store = a_chain(database, 3)
    provider = Provider()
    make_anchor(store, provider)

    control = Control(Policy.from_yaml(ALLOW), store, clock=lambda: T0)
    control.execute(an_action("more"), lambda: {"ok": True}, "refund:more", lease=LEASE)
    provider.at = T0 - timedelta(days=1)

    with pytest.raises(InvalidArgument) as refused:
        make_anchor(store, provider)
    store.close()
    assert "runs backwards" in str(refused.value)


def test_T535d_a_provider_answering_a_shape_the_kernel_refuses_anchors_nothing(tmp_path) -> None:
    """An operator's provider is their own code. One that returns `None` on failure rather than
    raising is the shape that would otherwise cache an anchor whose token is the string
    `"None"`."""
    database = tmp_path / "state.db"
    store = a_chain(database, 3)

    class Wrong:
        def make(self, seq, hash, kind):
            return None

        def check(self, seq, hash, token):
            return True

        def latest(self):
            return None

        def since(self, seq):
            return ()

    with pytest.raises(InvalidArgument) as refused:
        make_anchor(store, Wrong())
    assert "must return (token, time)" in str(refused.value)
    assert store.anchors() == ()
    store.close()


# --- T536: rule 1 ------------------------------------------------------------------------------


def test_T536_the_anchor_module_issues_nothing() -> None:
    """Rule 1 (§1.1): **the anchor consumes a timestamp and issues nothing.**

    No key generation, no rotation, no revocation, no signing. That is the line between this
    milestone and the one `ROADMAP.md` keeps off the roadmap, and an anchor that minted anything
    would have crossed it. Asserted against the module's source, because "it does not mint
    anything" is a claim about the environment until something checks it, in the shape
    `CLAIMS.md` uses.
    """
    import ctrlrun.anchor as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    for minted in (
        "secrets.",
        "os.urandom",
        "generate_private_key",
        "generate_key",
        "sign(",
        "PrivateKey",
        "uuid4(",
    ):
        assert minted not in source, (
            f"ctrlrun/anchor.py contains {minted!r}. Rule 1 is that the anchor consumes a "
            "timestamp and issues nothing; if this is now signing, ROADMAP.md and SPEC-v0.6 §11 "
            "both have to say so first"
        )
    # And the time an anchor carries is the provider's, never this process's clock.
    assert "datetime.now" not in source, (
        "the anchor read a clock of its own. An anchor's time must be the provider's: a time "
        "ctrlrun generated would be ctrlrun vouching for itself"
    )


# --- T537: both backends -----------------------------------------------------------------------


@postgres
def test_T537_postgres_anchors_and_names_a_truncation_the_same_way() -> None:
    """The amendment is to `StateStore`, so a backend without it could not implement §3 at all."""
    import psycopg

    from ctrlrun.postgres import PostgresStateStore

    schema = f"anchor_{uuid.uuid4().hex[:12]}"
    PostgresStateStore.create_schema(POSTGRES_URL, schema)
    try:
        store = PostgresStateStore(POSTGRES_URL, schema=schema, clock=lambda: T0)
        control = Control(Policy.from_yaml(ALLOW), store, clock=lambda: T0)
        for index in range(6):
            control.execute(
                an_action(f"p{index}"), lambda: {"ok": True}, f"refund:p{index}", lease=LEASE
            )
        provider = Provider()
        anchor = make_anchor(store, provider)
        assert anchor.seq == 6
        assert verify_anchors(store, provider).ok
        cached = store.anchors()
        assert len(cached) == 1 and cached[0].token == anchor.token
        assert cached[0].at == anchor.at, "the anchor's time did not survive the round trip"
        store.close()

        with psycopg.connect(POSTGRES_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'DELETE FROM "{schema}".receipts WHERE seq > 3')
                cursor.execute(
                    f'SELECT seq, hash FROM "{schema}".receipts ORDER BY seq DESC LIMIT 1'
                )
                row = cursor.fetchone()
                cursor.execute(
                    f'UPDATE "{schema}".receipt_chain SET seq = %s, hash = %s WHERE id = 1', row
                )
            connection.commit()

        reopened = PostgresStateStore(POSTGRES_URL, schema=schema, clock=lambda: T0)
        chain = verify_chain(reopened)
        report = verify_anchors(reopened, provider)
        reopened.close()

        assert chain.ok, "the Postgres chain detected the truncation, so §2.1 does not hold here"
        assert ("anchor_broken", 6) in [(item.name, item.seq) for item in report.breaks]
    finally:
        PostgresStateStore.drop_schema(POSTGRES_URL, schema)


# --- T538: the migration -----------------------------------------------------------------------


def test_T538_migration_0008_creates_the_three_tables(tmp_path) -> None:
    """SPEC-v0.11 §9. One migration, because a migration id is a name that cannot be amended
    once a store has applied it: item 2 creates all three tables and item 3 fills two of them."""
    from ctrlrun.migrations import HEAD, MIGRATIONS

    assert HEAD == "0008_anchor_checkpoint_hold"
    assert MIGRATIONS[-1].id == HEAD

    database = tmp_path / "state.db"
    store = SQLiteStateStore(database, clock=lambda: T0)
    store.close()
    connection = sqlite3.connect(database)
    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    connection.close()
    assert {"anchors", "prune_checkpoint", "holds"} <= tables, sorted(tables)


def test_T538b_an_older_store_migrates_forward_and_keeps_its_chain(tmp_path) -> None:
    """The upgrade path: a store written before 0008 gains the tables and its chain still
    verifies. Nothing about an existing receipt moves."""
    database = tmp_path / "state.db"
    store = a_chain(database, 4)
    before = [(item.seq, item.hash) for item in store.receipts()]
    store.close()

    connection = sqlite3.connect(database)
    connection.execute("DROP TABLE anchors")
    connection.execute("DROP TABLE prune_checkpoint")
    connection.execute("DROP TABLE holds")
    connection.execute(
        "DELETE FROM schema_version WHERE migration_id = '0008_anchor_checkpoint_hold'"
    )
    connection.commit()
    connection.close()

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    assert [(item.seq, item.hash) for item in reopened.receipts()] == before
    assert verify_chain(reopened).ok
    assert reopened.anchors() == ()
    assert reopened.checkpoint() is None
    reopened.close()


# --- T530b: the other half of §2.4's "yes" column ----------------------------------------------


def test_T530b_a_rewrite_at_or_below_an_anchored_seq_fails_the_anchor(tmp_path) -> None:
    """§2.4's second row: **any rewrite at or below an anchored `seq`** is detected, because the
    hash there differs.

    `T530` covers a row that is *absent*. This covers one that is *present and different*, which
    is a different branch and was reached by no test: a mutation deleting the hash comparison
    entirely left the whole file green.

    The chain catches this one too, and that is the point rather than a redundancy: the anchor
    must not go quiet about a tamper just because another reader would have caught it, or an
    operator who runs only the anchor check learns nothing.
    """
    database = tmp_path / "state.db"
    store = a_chain(database, 5)
    provider = Provider()
    anchor = make_anchor(store, provider)
    store.close()

    # The administrator who rewrites **every row including the head**, which is the case
    # `THREAT_MODEL.md` has always said the chain alone cannot catch: alter receipt 2, then
    # recompute every hash after it and the head, so the chain is internally consistent again.
    # That is the tamper the anchor exists for, and a half-done one -- editing the document and
    # leaving the hash column -- is caught by `verify_chain` instead and proves nothing here.
    _rewrite_chain_from(database, at=2, find='"amount":2000', replace='"amount":1')

    reopened = SQLiteStateStore(database, clock=lambda: T0)
    chain = verify_chain(reopened)
    report = verify_anchors(reopened, provider)
    rewritten = [item for item in reopened.receipts() if item.seq == 2]
    reopened.close()

    # The tamper landed, and the chain cannot see it. Without both of these the test would pass
    # against a rewrite that never happened, or against one the chain already caught, and in
    # neither case would it be showing what the anchor adds.
    assert rewritten and rewritten[0].arguments.get("amount") == 1, "the rewrite did not land"
    assert chain.ok, (
        "the chain caught a full rewrite, so this test is not exercising the case the anchor "
        f"exists for: {[(b.name, b.seq) for b in chain.breaks]}"
    )

    assert not report.ok and not report.unavailable
    named = [(item.name, item.seq) for item in report.breaks]
    assert ("anchor_broken", anchor.seq) in named, (
        f"a rewrite below the anchored seq did not fail the anchor: {named}"
    )


# --- T535e: the ordering that a joint rule would have refused forever --------------------------


def test_T535e_a_checkpoint_anchor_below_the_newest_interval_anchor_is_accepted(tmp_path) -> None:
    """§3.2 and §4.6. **The case a joint ordering refuses, and refuses permanently.**

    A deployment anchoring hourly and pruning at ninety days takes its checkpoint anchor over the
    `seq` it pruned through, which is far *below* its newest interval anchor. A draft that
    ordered all anchors by `seq` refused it, so the prune was refused, and §4.6 exists precisely
    so that an anchoring deployment does not have to choose between pruning and a permanent
    tamper signal.

    **A mutation is why this test exists.** Restoring the joint ordering survived every other
    test in this file, because nothing could produce a checkpoint anchor below an interval one
    for the per-kind rule to have to allow: `make_anchor` anchored the head and nothing else.
    That was a gap in the implementation as much as in the tests, and `at=` closes it.
    """
    database = tmp_path / "state.db"
    store = a_chain(database, 6)
    provider = Provider()
    interval = make_anchor(store, provider)
    assert interval.seq == 6 and interval.kind == INTERVAL

    # What a prune does: anchor the checkpoint over the seq it is about to prune through.
    rows = store.receipts()
    below = rows[1]
    assert below.seq == 2 and below.hash is not None
    checkpoint = make_anchor(store, provider, kind=CHECKPOINT, at=(below.seq, below.hash))
    store.close()

    assert checkpoint.kind == CHECKPOINT
    assert checkpoint.seq == 2, (
        "a checkpoint anchor below the newest interval anchor was refused, which makes a "
        "pruning deployment choose between retention and a permanent tamper signal (§4.6)"
    )
    assert checkpoint.seq < interval.seq
