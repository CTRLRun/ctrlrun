#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""Erasing the end of the receipt log costs two SQL statements. An anchor makes it show.

The receipt chain detects **alteration**: edit a receipt and the hash no longer matches. It does
not detect **truncation**, and that has been written down since `SPEC-v0.6.md` §6.4 rather than
discovered here. The reason is structural: the head that would catch it is a row in the same
database, so an administrator with write access deletes the tail and updates one more row.

    DELETE FROM receipts WHERE seq > 3;
    UPDATE receipt_chain SET seq = 3, hash = '<the hash at 3>';

Two statements, and `ctrlrun receipts --verify-chain` reports the log intact.

An **anchor** records the pair the head holds -- a `seq` and the hash at it -- somewhere the
database's writer does not control. This example runs both halves so you can see the difference,
and prints what an anchor does **not** prove as plainly as what it does.

    python examples/anchored-chain/main.py

No network, and a state directory of its own under `.ctrlrun/examples/`.
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ctrlrun import Action, Control, Policy, Principal, SQLiteStateStore
from ctrlrun.anchor import Anchor, make_anchor, verify_anchors
from ctrlrun.receipt import verify_chain

HERE = Path(__file__).resolve().parent
STATE = Path(".ctrlrun/examples/anchored-chain")


class FileAnchorProvider:
    """An anchor provider, in the shape `SPEC-v0.11.md` §3.2 defines and nothing more.

    **ctrlrun ships none**, deliberately: `ROADMAP.md` names RFC 3161, and an RFC 3161 client is
    a network client, which does not belong in a wheel whose rule is stdlib plus `pyyaml` and
    `click`. So the provider is yours. A real one would be a timestamp authority, a transparency
    log, an append-only bucket in another account, or a file on a host your database's writer
    cannot reach.

    This one is a JSON file in a **different directory** from the store, which is the smallest
    thing that illustrates the property. It is not a good anchor and does not pretend to be: an
    anchor is worth exactly what its record is worth, and a file beside the database is worth
    nothing. The same sentence `THREAT_MODEL.md` uses about a revocation feed applies here.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._held: dict[str, Anchor] = {}
        self._clock = datetime(2026, 1, 1, tzinfo=UTC)

    def make(self, seq: int, hash: str, kind: str) -> tuple[str, datetime]:
        self._clock += timedelta(minutes=1)
        token = f"anchor-{kind}-{seq}"
        self._held[token] = Anchor(seq=seq, hash=hash, token=token, kind=kind, at=self._clock)
        return token, self._clock

    def check(self, seq: int, hash: str, token: str) -> bool:
        held = self._held.get(token)
        return held is not None and held.seq == seq and held.hash == hash

    def latest(self) -> tuple[int, str] | None:
        if not self._held:
            return None
        newest = max(self._held.values(), key=lambda item: item.seq)
        return (newest.seq, newest.token)

    def since(self, seq: int) -> tuple[Anchor, ...]:
        return tuple(item for item in self._held.values() if item.seq >= seq)


def refund(payment_id: str, amount: int) -> Action:
    return Action(
        name="stripe.refund",
        arguments={"payment_id": payment_id, "amount": amount},
        principal=Principal(agent="payments-agent"),
    )


def four_refunds(database: Path) -> None:
    store = SQLiteStateStore(database)
    control = Control(Policy.from_file(HERE / "ctrlrun.yaml"), store)
    for index in range(4):
        control.execute(refund(f"pi_{index}", 1200), lambda: {"ok": True}, f"refund:pi_{index}")
    store.close()


def erase_the_tail(database: Path, keep_through: int) -> None:
    """The attack, in the two statements it really takes. Nothing here goes through ctrlrun."""
    connection = sqlite3.connect(database)
    connection.execute("DELETE FROM receipts WHERE seq > ?", (keep_through,))
    row = connection.execute("SELECT seq, hash FROM receipts ORDER BY seq DESC LIMIT 1").fetchone()
    connection.execute("UPDATE receipt_chain SET seq = ?, hash = ? WHERE id = 1", row)
    connection.commit()
    connection.close()


def main() -> None:
    if STATE.exists():
        shutil.rmtree(STATE)
    STATE.mkdir(parents=True)

    print("1. Four refunds, then somebody erases the last two.\n")
    plain = STATE / "not-anchored" / "state.db"
    plain.parent.mkdir(parents=True)
    four_refunds(plain)
    erase_the_tail(plain, keep_through=2)

    store = SQLiteStateStore(plain)
    report = verify_chain(store)
    store.close()
    print(f"   ctrlrun receipts --verify-chain: {report.verified} of {report.chained} verified")
    print(f"   breaks: {[break_.name for break_ in report.breaks] or 'none'}")
    print(f"   the chain says it is intact: {report.ok}")
    print("   Two receipts are gone and nothing says so. This is SPEC-v0.6 §6.4, by design.\n")

    print("2. The same four refunds, anchored first, then the same two statements.\n")
    anchored = STATE / "anchored" / "state.db"
    anchored.parent.mkdir(parents=True)
    # The provider's record lives OUTSIDE the store's directory, which is the whole idea.
    provider = FileAnchorProvider(STATE / "outside" / "anchors.json")

    four_refunds(anchored)
    store = SQLiteStateStore(anchored)
    anchor = make_anchor(store, provider)
    print(f"   anchored seq {anchor.seq} at {anchor.at.isoformat()}")
    clean = verify_anchors(store, provider)
    print(f"   before any tamper: ok={clean.ok}, {clean.checked} anchor(s) reproduce\n")
    store.close()

    erase_the_tail(anchored, keep_through=2)
    store = SQLiteStateStore(anchored)
    chain = verify_chain(store)
    anchors = verify_anchors(store, provider)
    store.close()

    print(f"   the chain still says intact:  {chain.ok}")
    print(f"   the anchor says:              ok={anchors.ok}")
    for problem in anchors.breaks:
        print(f"     {problem.name} at seq {problem.seq}: {problem.detail}")

    print()
    print("What an anchor proves: everything at or below an anchored seq is frozen. Removing or")
    print("altering any of it stops the anchored pair reproducing, and the operator's own record")
    print("is what decides, not a row in the database under suspicion.")
    print()
    print("What it does NOT prove, which matters as much:")
    print("  - an APPEND is not detected. A forged receipt lands above every anchored seq, so no")
    print("    anchored pair stops reproducing, and the next anchor freezes it like any other.")
    print("  - receipts written and erased BETWEEN two anchors are not detected either.")
    print("  - it does not say who wrote any of it. An anchor is not a signature.")
    print("  - an administrator who rewrites everything before the next anchor is out of scope.")
    print()
    print("The window you are exposed to is (last anchored seq, current head]. Its size is your")
    print("choice of interval, and that is the number to tune and to quote.")


if __name__ == "__main__":
    main()
