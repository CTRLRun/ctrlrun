# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""Retention: a prune that leaves the chain verifiable, a checkpoint, and a hold.

`SPEC-v0.11.md` §4. There has been no retention policy until now, and
`../ctrlrun-docs/docs/postgres.md` says so in the same breath as the reason one is hard to write:
**deleting receipts from the middle or the end of the chain is detected as a break by design.**
That is the feature and not an obstacle, and a retention job that simply deleted would be
manufacturing `SPEC-v0.11.md` §2.1's attack on purpose.

**A prune removes a prefix**, receipts from genesis through some `seq`. Never a suffix, never a
middle: a suffix *is* §2.1's attack, a middle is `missing` by construction, and the only shape
that can leave a verifiable chain is the one that moves the chain's start.

**Rule 2 (§1.1): a prune introduces no break the chain did not already have, or it is refused.**
Stated as a delta and not as "the chain verifies", because `unchained` is a pre-existing condition
on any store migrated from v0.1 to v0.5, survives a prefix prune, and can never be inside a
prefix. The absolute version would make retention permanently impossible on the oldest and largest
stores, which are the ones it is for.

There is no `--force`, no `--allow-gap`, and no setting that admits a break the prune caused.

**This is the only operation in this library that destroys evidence.** Getting a refusal wrong
costs an operator an error message; getting this wrong costs them the record.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, Protocol

from .anchor import CHECKPOINT, Anchor, AnchorProvider, make_anchor
from .effect import EffectState
from .errors import InvalidArgument
from .receipt import RECEIPT_SCHEMA, ChainReport, Receipt, verify_chain

#: The action name a prune's receipt carries. **Not routed through `Control.execute`**, and §4.2
#: is why: `control.py`'s gate is
#: `if self._require_approved_policy and action.name != POLICY_CHANGE_ACTION`, so a prune as an
#: ordinary action would be refused on a deployment that had not approved its current policy --
#: and a store that cannot prune is a store that fills. That is precisely the trap O3 refuses by
#: keeping retention out of the policy document, and O4 would have let it back in through the
#: action gate.
#:
#: A prune is **an operator's act at the CLI**, not an agent's action. What authorises it is shell
#: access to the store, which policy does not mediate and has never claimed to.
PRUNE_ACTION: Final = "ctrlrun.retention.prune"

#: §4.4's table. An effect not in a terminal state still **holds** its charge, so deleting its
#: ledger row would hand back authority nobody granted, which is the hole `SPEC-v0.9.md` §4 exists
#: to close.
#:
#: **`COMMITTED` is not in this set and is not simply prunable either**: its charge is never
#: released, because a committed spend is a spend, so it counts toward a budget for as long as it
#: is inside `SPEC-v0.9.md` §7.3's window. `_ledger_refusals` carries that condition.
#:
#: `NEW` is absent because `effect.py` says it "is never written to a store".
HELD_EFFECT_STATES: Final = (
    EffectState.AMBIGUOUS,
    EffectState.RESERVED,
    EffectState.EXECUTING,
)


@dataclass(frozen=True)
class Hold:
    """A named range of receipts that refuses to be pruned (§4.3).

    O5 asked whether a hold needs a second state machine. It does not: a row with a range and a
    reason, consulted by the prune.

    **No expiry.** `SPEC-v0.9.md` §4's rule that an automatic expiry on a hold is the refund rule
    in a costume applies unchanged: a hold that lapsed on a timer would release evidence on a
    schedule nobody reviewed. A hold ends when a person ends it.
    """

    hold_id: str
    from_seq: int
    #: `None` means "to the end of the chain, and everything written after it".
    to_seq: int | None
    reason: str
    placed_by: str
    placed_at: datetime
    released_at: datetime | None = None
    released_by: str | None = None

    @property
    def live(self) -> bool:
        return self.released_at is None

    def covers(self, seq: int) -> bool:
        return self.live and seq >= self.from_seq and (self.to_seq is None or seq <= self.to_seq)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hold_id": self.hold_id,
            "from_seq": self.from_seq,
            "to_seq": self.to_seq,
            "reason": self.reason,
            "placed_by": self.placed_by,
            "placed_at": self.placed_at.isoformat(),
            "released_at": None if self.released_at is None else self.released_at.isoformat(),
            "released_by": self.released_by,
        }


@dataclass(frozen=True)
class Checkpoint:
    """What a prune leaves behind: the `seq` it pruned through and the hash at it (§4.2).

    **A row in a table of its own, and not a field on a receipt.** A receipt naming itself a
    checkpoint is a string in a document, and `SPEC-v0.3.md` §4.3.1 already settled that shape: a
    grant may legally be named `no_authority`, so evidence that could be spoofed by naming one is
    not evidence. A walk that believed `action == "ctrlrun.retention.prune"` would accept a forged
    prefix-erasure written by anyone who can insert a row.

    The receipt records that the prune happened, for a human. **This row is what the walk uses.**

    It carries `schema`, the receipt schema version current when it was written, because a store
    pruned today and read in two years is the case this milestone exists for.

    **This does not make a checkpoint unforgeable**, and saying so plainly is the point: a writer
    who can insert receipts can write one of these. What closes that is §3's anchor, and only for
    the window between anchors. The two features are one argument, which is why they are one
    milestone.
    """

    seq: int
    hash: str
    schema: str
    at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "hash": self.hash,
            "schema": self.schema,
            "at": self.at.isoformat(),
        }


@dataclass(frozen=True)
class PruneResult:
    """What a prune did, or what it refused and why."""

    through: int
    receipts_deleted: int
    ledger_rows_deleted: int
    checkpoint: Checkpoint | None
    anchored: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "through": self.through,
            "receipts_deleted": self.receipts_deleted,
            "ledger_rows_deleted": self.ledger_rows_deleted,
            "checkpoint": None if self.checkpoint is None else self.checkpoint.to_dict(),
            "anchored": self.anchored,
        }


class _LedgerRow(Protocol):
    """The ledger fields a prune reads, structurally.

    Not an import of `state.Consumption`: `state.py` imports **this** module for `Hold` and
    `Checkpoint`, and `ARCHITECTURE.md` §6 says dependencies point downward. The same reason
    `ChainSource` is a protocol in `receipt.py`.
    """

    @property
    def effect_key(self) -> str: ...

    @property
    def consumed_at(self) -> datetime: ...

    @property
    def released_at(self) -> datetime | None: ...


class _EffectRow(Protocol):
    @property
    def state(self) -> EffectState: ...


class RetentionStore(Protocol):
    """What a prune needs from a store.

    A `Protocol` rather than an import of `StateStore`, for `ChainSource`'s reason: `state.py`
    imports this module and `ARCHITECTURE.md` §6 says dependencies point downward.
    """

    def receipts(self) -> tuple[Any, ...]: ...

    def chain_head(self) -> tuple[int, str] | None: ...

    def checkpoint(self) -> tuple[int, str] | None: ...

    def put_checkpoint(self, checkpoint: Checkpoint) -> None: ...

    def holds(self) -> tuple[Hold, ...]: ...

    def consumptions(self) -> tuple[_LedgerRow, ...]: ...

    def get_effect(self, effect_key: str) -> _EffectRow | None: ...

    def pruning(self) -> AbstractContextManager[None]: ...

    def delete_prefix(self, through: int, effect_keys: Sequence[str]) -> tuple[int, int]: ...

    def put_anchor(self, anchor: Anchor) -> None: ...

    def anchors(self) -> tuple[Anchor, ...]: ...


def _pairs(report: ChainReport) -> set[tuple[str, int | None]]:
    """A chain report as the set rule 2 compares.

    **Per `(kind, seq)` pair, not per kind**, which §10 states and an earlier draft did not: a
    store already reporting `missing` at `seq 7` must not thereby be allowed to admit a **new**
    `missing` at `seq 1`. `unchained` carries no `seq` and compares as `(unchained, None)`.
    """
    return {(item.name, item.seq) for item in report.breaks}


def _after_prune(
    receipts: Sequence[Any], through: int, checkpoint: Checkpoint, head: tuple[int, str] | None
) -> ChainReport:
    """What `verify_chain` would report on this store after the prune, without doing it.

    The prune is validated against this rather than against its own arithmetic, because rule 2 is
    a statement about what the **reader** says and the reader is the thing an operator runs.

    **It must be the store and not a tidier version of it**, and an independent review found two
    ways it was not. It filtered to `Receipt`, so every row `from_dict` refuses vanished from the
    simulation and the prune refused honest prunes naming breaks that would not occur::

        prune(through=3) REFUSED, claiming: ... would leave the chain reporting missing at seq 6
        what the store ACTUALLY reports after that same prune: [('content_altered', 6),
                                                                ('link_broken', 7)]
        breaks the prune would really have caused: none

    One tampered row cost the whole retention feature, against rule 3. And it re-derived the head
    from the last kept receipt, where `delete_prefix` never touches `receipt_chain`, so a store
    whose head row was damaged was refused for a `head_mismatch` it already had.
    """
    kept = tuple(
        item for item in receipts if getattr(item, "seq", None) is None or item.seq > through
    )
    return verify_chain(_PrunedChain(kept, checkpoint, head))


@dataclass(frozen=True)
class _PrunedChain:
    """The store as it would read after a prune, seeded from the checkpoint.

    **Three values, not one**, which §4.1 measured and an earlier draft got wrong:
    `verify_chain` seeds `expected_prev = GENESIS_HASH` and `expected_seq = 1`, and compares the
    head against the last surviving receipt. A checkpoint that replaced only the hash still
    reported `missing` at seq 1, so a faithful implementation of the first draft built a prune
    rule 2 forbids:

        after PREFIX delete of seq<=3   -> ok: False breaks: [('missing', 1), ('link_broken', 4)]
        walk seeded from the checkpoint HASH only:
                                        -> ok: False breaks: [('missing', 1)]
    """

    _receipts: tuple[Any, ...]
    _checkpoint: Checkpoint
    #: The store's **own** head row, unchanged: `delete_prefix` never writes `receipt_chain`.
    _head: tuple[int, str] | None

    def receipts(self) -> tuple[Any, ...]:
        return self._receipts

    def chain_head(self) -> tuple[int, str] | None:
        return self._head

    def checkpoint(self) -> tuple[int, str] | None:
        return (self._checkpoint.seq, self._checkpoint.hash)


def _held_by(holds: Sequence[Hold], through: int) -> Hold | None:
    """The first live hold overlapping the prefix `1..through`, or `None`.

    A prune takes a prefix, so it overlaps a hold whenever the hold covers **any** `seq` at or
    below `through`. A hold with no `to_seq` runs to the end of the chain and therefore always
    overlaps.
    """
    for hold in holds:
        if not hold.live:
            continue
        if hold.from_seq <= through:
            return hold
    return None


def _ledger_refusals(
    store: RetentionStore, effect_keys: Sequence[str], *, now: datetime, older_than: timedelta
) -> list[str]:
    """§4.4's table, as refusals naming the row and the reason.

    **Two conditions, and the first draft had only one.** It wrote the rule as *a prune excludes
    un-released rows*, and `state.py`'s `_release_locked` says why that is inert: `COMMITTED`
    holds permanently and only `FAILED` releases, because a committed spend is a spend. So
    "un-released" is almost every row in the ledger, forever, and the rule would have made the
    feature do nothing while §10 turned it into a refusal.

    The rule is **settlement**, and then the **window**:

    * an effect that is not in a terminal state still holds its charge, so deleting its row hands
      back authority nobody granted;
    * a `COMMITTED` row still counts toward a budget while it is inside `SPEC-v0.9.md` §7.3's
      window. A review measured the cost of treating `COMMITTED` as simply prunable, on a
      250-unit daily budget::

          third 100 on a 250 budget: REFUSED -> budget 'amount' ... is exhausted
          pruned COMMITTED ledger rows: 2
          SAME action after pruning: ALLOWED   <-- authority manufactured

    **The window is supplied, not derived** (O7). A ledger row carries no window and no limit;
    those travel on `Charge`, from the authority document, and a store that resolved a grant's
    budgets would be reading the policy, which `ARCHITECTURE.md` §6 forbids. Deriving it here
    would also have two failure modes with no good answer: a row whose `grant_id` has left the
    document has no window at all, and a window an operator lengthens later would retroactively
    un-prune rows already pruned.

    So the operator passes `--older-than`, as they pass the range, and the kernel checks the rule
    rather than deriving the input. The number to use is **the longest window on any budget of
    any grant**, which `SPEC-v0.9.md` §7.3 makes a safe over-approximation.
    """
    refusals: list[str] = []
    boundary = now - older_than
    wanted = set(effect_keys)
    for row in store.consumptions():
        if row.effect_key not in wanted:
            continue
        effect = store.get_effect(row.effect_key)
        state = None if effect is None else effect.state
        if state in HELD_EFFECT_STATES:
            refusals.append(
                f"the ledger row for effect {row.effect_key!r} is {state}, which still holds its "
                "charge; deleting it would hand back authority nobody granted (SPEC-v0.9 §4)"
            )
        elif state is EffectState.COMMITTED and row.consumed_at > boundary:
            refusals.append(
                f"the ledger row for effect {row.effect_key!r} was consumed at "
                f"{row.consumed_at.isoformat()}, inside --older-than; a COMMITTED charge is never "
                "released, so it still counts toward a budget and pruning it would manufacture "
                "authority (SPEC-v0.9 §7.3)"
            )
    return refusals


def prune(
    store: RetentionStore,
    *,
    through: int,
    older_than: timedelta,
    anchor: AnchorProvider,
    now: datetime,
) -> PruneResult:
    """Delete receipts from genesis through `seq`, leaving the chain verifiable across the gap.

    **The order is forced, not chosen** (§4.5, §4.6), and every step below is a refusal the
    document already states. The caller writes the prune's receipt **before** calling this, for a
    reason the store made unavoidable::

        writing the prune's receipt inside the prune's transaction ->
            OperationalError: cannot start a transaction within a transaction

    `put_receipt` opens `BEGIN IMMEDIATE` on the store's own connection, so a prune already
    holding that transaction cannot write through it. §4.2 says the prune writes a receipt and
    §4.5 says it takes the receipt-write lock, and the two are unsatisfiable together in the
    other order. **The crash window is the safe one**: a receipt for a prune that did not happen
    over-reports, where a prune with no receipt is indistinguishable from §2.1's attack.

    Then, in this order:

    1. refuse a prune through the chain's head, which would leave no chained receipt for the head
       to name;
    2. refuse a checkpoint at or below the one already recorded, which is the second of two
       racing prunes (§4.5);
    3. consult holds **inside** the prune's transaction, because a hold placed between a consult
       and a delete would be honoured by neither;
    4. refuse any ledger row §4.4's table holds;
    5. compute what the chain would report **after** the prune and refuse any `(kind, seq)` pair
       the store did not already report, which is rule 2;
    6. **anchor the checkpoint, then delete.** Not the reverse: a crash between them leaves an
       anchored checkpoint for a prune that did not happen, which over-reports and is the safe
       direction, where the reverse leaves a prefix erased with nothing accounting for it.
    """
    if through < 1:
        raise InvalidArgument(f"--through must be at least 1, got {through}")
    if older_than < timedelta(0):
        raise InvalidArgument("--older-than must not be negative")

    # §4.5: **the lock is held across the validation and the delete**, not only the delete.
    # Two prunes that both validated and then both acted would each be individually valid under
    # §10 and together break rule 2, which is the case §4.5 measured. A first implementation took
    # the lock inside `delete_prefix`, and a probe against a real Postgres server found the pair
    # serialized by the *anchor* ordering instead: shared state, but not a lock.
    #
    # A hold is therefore consulted **inside** this, which §4.5 also requires: a hold placed
    # between a consult and a delete would be honoured by neither.
    with store.pruning():
        return _prune_locked(store, through=through, older_than=older_than, anchor=anchor, now=now)


def _prune_locked(
    store: RetentionStore,
    *,
    through: int,
    older_than: timedelta,
    anchor: AnchorProvider,
    now: datetime,
) -> PruneResult:
    """Everything a prune does while it holds the receipt-write lock."""
    receipts = tuple(item for item in store.receipts() if isinstance(item, Receipt))
    positions = [item.seq for item in receipts if item.seq is not None]
    if not positions:
        raise InvalidArgument("this store holds no chained receipt; there is nothing to prune")

    head = store.chain_head()
    if head is None:
        raise InvalidArgument("this store has no chain head; there is nothing to prune")
    # **The bound is the highest chained receipt, not the head row**, and an independent review
    # is why. `chain_head()` reads `receipt_chain`, which is the row `SPEC-v0.11.md` §2.1 already
    # assumes an attacker rewrites -- it is the whole reason the anchor exists. Deciding the
    # prune's limit from it meant one `UPDATE receipt_chain SET seq = 99` turned
    # `prune --through 8` into a delete of every receipt in the store, after which both
    # `verify_chain` and `verify_anchors` reported clean:
    #
    #     after UPDATE receipt_chain SET seq=99, prune --through 8 COMPLETED, deleted 8
    #     end state: receipts=0  verify_chain ok=True  verify_anchors ok=True
    #
    # The rule-2 delta permitted it because `head_mismatch` at 99 pre-existed, which is rule 2
    # read literally producing total erasure. The receipts are the thing being deleted, so they
    # are what bounds the deletion; the head is checked **as well**, below, because a prune that
    # leaves the head naming a row it just deleted is §10's refusal too.
    head_seq = max(positions)
    if through >= head_seq:
        # §10: a prune through the head leaves no chained receipt for the head to name, so the
        # store would report `head_mismatch` about a chain nothing is wrong with.
        raise InvalidArgument(
            f"a prune through seq {through} would take the chain's head (seq {head_seq}); a "
            "prune takes a prefix and must leave at least one chained receipt behind"
        )

    recorded = store.checkpoint()
    if recorded is not None and through <= recorded[0]:
        # §4.5: two prunes, each individually valid, in an order §4 did not exclude. B's
        # checkpoint row overwrote A's and the walk from B's checkpoint reported
        # [('missing', 4), ('link_broken', 6)]. A checkpoint is written only forward.
        raise InvalidArgument(
            f"this store is already pruned through seq {recorded[0]}; a checkpoint is written "
            f"only forward, and a prune through seq {through} would move it backwards"
        )

    held = _held_by(store.holds(), through)
    if held is not None:
        raise InvalidArgument(
            f"hold {held.hold_id!r} covers receipts this prune would delete: {held.reason}"
        )

    prefix = [item for item in receipts if item.seq is not None and item.seq <= through]
    if not prefix:
        raise InvalidArgument(f"no chained receipt at or below seq {through}; nothing to prune")

    boundary_receipt = max(prefix, key=lambda item: item.seq or 0)
    if boundary_receipt.hash is None:
        raise InvalidArgument(
            f"the receipt at seq {through} has no stored hash, so a checkpoint over it would "
            "name a hash nobody can compare against"
        )
    # **The checkpoint names the pair that exists, not the number the operator typed**, and an
    # independent review is why. This took `seq=through` with the hash of whatever readable
    # receipt happened to be highest at or below it, so on a chain whose seq 3 had already been
    # deleted by somebody else, `prune --through 3` wrote a checkpoint asserting `(3, hash@2)` --
    # a pair that never existed -- and **that fabricated pair is what went to the provider**::
    #
    #     checkpoint written : seq=3 hash=sha256:874c40cb...
    #     the real hash at seq 3 was : sha256:09694471...
    #     the hash at seq 2 is       : sha256:874c40cb...
    #
    # §4.6's whole argument rests on the anchored checkpoint being a claim an operator can check
    # against the chain, so a checkpoint that names a hash the chain never had corrupts exactly
    # the external record the anchor exists to provide. The deletion still takes the operator's
    # `through`; only the claim is narrowed to a row that was really there.
    boundary_seq = boundary_receipt.seq
    assert boundary_seq is not None  # `prefix` filtered on it
    checkpoint = Checkpoint(
        seq=boundary_seq,
        hash=boundary_receipt.hash,
        # The version current when it was written, because a store pruned today and read in two
        # years is the case this milestone is about (§4.2).
        schema=RECEIPT_SCHEMA,
        at=now,
    )

    effect_keys = [item.effect_key for item in prefix if item.effect_key]
    refusals = _ledger_refusals(store, effect_keys, now=now, older_than=older_than)
    if refusals:
        raise InvalidArgument("this prune was refused: " + "; ".join(refusals))

    before = _pairs(verify_chain(store))
    after = _pairs(_after_prune(store.receipts(), through, checkpoint, head))
    caused = after - before
    if caused:
        # Rule 2, as a **delta**: `unchained` is a pre-existing condition on any store migrated
        # from v0.1 to v0.5 and can never be inside a prefix, so the absolute version would make
        # retention permanently impossible on the oldest and largest stores.
        named = ", ".join(f"{name} at seq {seq}" for name, seq in sorted(caused, key=str))
        raise InvalidArgument(
            f"this prune would leave the chain reporting {named}, which this store does not "
            "report now. A prune that introduces a break has destroyed evidence and called it "
            "retention; there is no flag that admits it"
        )

    # §4.6: anchor the checkpoint, **then** delete. An attacker who erases a prefix and writes a
    # checkpoint to explain it must also anchor that checkpoint, and anchoring goes through the
    # provider, which is outside the store. So the provider's own record shows that a prune
    # happened, at what `seq`, and when: a prune becomes something an operator can see in the
    # anchor history even though the receipts are gone.
    make_anchor(store, anchor, kind=CHECKPOINT, at=(checkpoint.seq, checkpoint.hash))
    store.put_checkpoint(checkpoint)
    deleted_receipts, deleted_rows = store.delete_prefix(through, effect_keys)

    return PruneResult(
        through=through,
        receipts_deleted=deleted_receipts,
        ledger_rows_deleted=deleted_rows,
        checkpoint=checkpoint,
        anchored=True,
    )
