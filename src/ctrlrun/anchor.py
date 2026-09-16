# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""The anchor: the chain's head, recorded where the store's writer cannot reach it.

`SPEC-v0.11.md` §2 and §3. The receipt chain detects **alteration**. It does not detect
**truncation** or **append**, because the head that would catch them is a row in the same
database: two `UPDATE`s and the record is consistent and wrong. That has been true and written
down since `SPEC-v0.6.md` §6.4.

An anchor puts the pair the head already holds, a `seq` and the hash at it, somewhere the store's
writer does not control, at an interval the operator chooses.

**What an anchor proves, and what it does not** (§2.4, stated here because this is the first thing
in this project a reader could mistake for tamper-proofing):

An anchor **freezes a prefix**. It records that at time T the chain's head was `(seq, hash)`, so
anything at or below that `seq` can no longer be removed or altered without the anchored pair
failing to reproduce, unless an anchored checkpoint accounts for its removal (§4.6).

| Attack | Detected? |
|---|---|
| a truncation, when the anchored `seq` is above the new head | **yes** |
| any rewrite at or below an anchored `seq` | **yes**: the hash there differs |
| a forged **append** | **no.** It lands at head + 1, above every anchored `seq` |
| receipts written and erased between two anchors | **no.** Never at or below an anchored `seq` |
| an administrator who rewrites everything before the next anchor | **no** |
| who wrote any of it | **no.** Authorship is out of scope; signing stays off the roadmap |

The exposed window is `(last anchored seq, current head]`, and its size is the operator's choice
of interval. That is the number an operator tunes, and it is the number to quote rather than any
sentence about tamper-evidence.

**Rule 1 (§1.1): the anchor consumes a timestamp and issues nothing.** No key generation, no
rotation, no revocation, no signing. `SPEC-v0.3.md` §1.1's rule that ctrlrun consumes identity and
issues none, applied to time. Nothing in this module mints anything.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Protocol

from .action import canonical_bytes
from .errors import CTRLRunError, InvalidArgument

#: §3.2. The two kinds of anchor, and they are **ordered separately**.
#:
#: An `interval` anchor is the scheduled one: each must be above the last, because the chain only
#: grows. A `checkpoint` anchor is the one a prune takes over its checkpoint (§4.6), and it is
#: ordered only against other checkpoints.
#:
#: A first draft ordered all anchors jointly by `seq` and a review showed what that costs: a
#: deployment anchoring hourly and pruning at ninety days makes its checkpoint anchor far *below*
#: its newest interval anchor, so the joint rule refused it, so the prune was refused, **forever**.
INTERVAL: Final = "interval"
CHECKPOINT: Final = "checkpoint"
ANCHOR_KINDS: Final = (INTERVAL, CHECKPOINT)

#: §3.4's closed set, and it is **its own** set rather than two more names in `CHAIN_BREAKS`.
#:
#: `CHAIN_BREAKS` is closed on a `SPEC-v0.6.md` §6.5 surface and stays closed at six. A first
#: draft amended it to eight and a review ran what that costs: `G11`'s positive control is
#: `intact.ok and intact.verified >= 3` over the whole `ChainReport`, so **any** eighth kind
#: appearing there fails `G11` with `control failed`, the status that means the kernel is broken.
#: `anchor_missing` fires on every anchoring deployment while verify's own scratch store never
#: anchors, so the failure would have been universal rather than rare.
#:
#: Keeping them apart is better on three counts: `G11` is genuinely untouched rather than argued
#: to be, this milestone amends one frozen surface instead of two, and §9's frozen table can name
#: this as a **symbol** a test imports, where "`CHAIN_BREAKS` gains two members" is a membership
#: claim the frozen-name test's shape cannot express.
ANCHOR_BREAKS: Final = (
    "anchor_broken",
    "anchor_missing",
    "anchor_repudiated",
)

#: **`anchor_unavailable` is deliberately NOT in `ANCHOR_BREAKS`**, and an earlier draft put it
#: there. It is a **transport failure**, not a finding about the evidence, and a set that
#: conflates the two teaches an operator to ignore the ones that matter: a timestamp authority
#: briefly unreachable would grade `G28` `fail`, indistinguishable in the report from a
#: truncation. `SPEC-v0.10.md`'s `upstream_unverified` is the precedent and it cuts the other way,
#: because it is a *decision-time* refusal rather than a break in a verification report.
#:
#: **Refusing to act when you cannot ask is fail-closed; reporting tampering when you cannot ask
#: is a false positive.** So an unreachable provider makes the report `unavailable`, which is
#: neither `ok` nor broken, and `G28` grades `N/A` with that reason.
ANCHOR_UNAVAILABLE: Final = "anchor_unavailable"

#: The domain tag an anchor's provider answer is canonicalized under, so an anchor token can never
#: collide with a scope hash or a precondition fingerprint computed over the same mapping.
#: `SPEC-v0.9.md` §5.5 makes the same argument for a scope hash.
ANCHOR_DOMAIN: Final = "ctrlrun.anchor/v1"


@dataclass(frozen=True)
class Anchor:
    """One anchored pair, as the provider recorded it and as the local table caches it (§3.1).

    Not a copy of the chain, not a backup, and not a second head: one pair and a time. The whole
    of its power is that reproducing it later requires the chain between genesis and that `seq`
    to be exactly what it was.

    `at` is the **provider's** time and never this process's clock. Rule 1 is that the anchor
    consumes a timestamp and issues none, so a time ctrlrun generated would be ctrlrun vouching
    for itself, which is the thing an external anchor exists to stop.
    """

    #: The chain position this anchor froze.
    seq: int
    #: The chain hash at that position.
    hash: str
    #: Whatever the provider returned to identify its own record. Opaque here, always.
    token: str
    #: `interval` or `checkpoint` (§3.2).
    kind: str
    #: When the provider says it anchored this pair.
    at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "hash": self.hash,
            "token": self.token,
            "kind": self.kind,
            "at": self.at.isoformat(),
        }


class AnchorProvider(Protocol):
    """The operator's own code, answering from the operator's own system (§3.2).

    `ROADMAP.md` names RFC 3161 and §9 puts **no** RFC 3161 client in the wheel: the kernel's rule
    since `SPEC-v0.1.md` is that the core stays stdlib plus `pyyaml` and `click`, and a timestamp
    protocol client is a network client. So this is the shape `SPEC-v0.9.md` §5.4 settled for a
    scope provider, with the kernel matching rather than fetching.

    **Four calls, not two, and the last two are why.** An earlier draft had `make` and `check`
    alone, and a review broke it in one extra statement: with only those, the record of *which*
    anchors exist lives in ctrlrun's own table, so deleting the newest row there leaves the older
    anchor reproducing and the truncation invisible. Three SQL statements instead of two, which is
    the number this design claimed to avoid. `latest()` and `since()` move that history to the
    side that cannot be rewritten.
    """

    def make(self, seq: int, hash: str, kind: str) -> tuple[str, datetime]:
        """Anchor this pair. Returns the provider's token and the time it recorded, or raises.

        **The return is a pair rather than §3.2's bare token, and this is a deviation the PR
        records rather than one made quietly.** §3.2's table says *"returns an opaque token"*
        while §3.3 says ctrlrun caches *"the pair, the token, and the time"*, and §10 refuses
        *"an anchor whose time runs backwards against the one before it"*. A time the provider
        does not supply is one ctrlrun would have to read from its own clock, which rule 1
        forbids: the anchor consumes a timestamp and issues none.
        """
        ...

    def check(self, seq: int, hash: str, token: str) -> bool:
        """Does the provider still vouch for this pair under this token?

        **It is allowed to say no**, and that is its one substantive answer and the entire reason
        for holding the record outside the store. `anchor_repudiated` exists for it.
        """
        ...

    def latest(self) -> tuple[int, str] | None:
        """The highest `seq` the provider holds an anchor for, and its token. `None` if it holds
        none.

        Answered **outside**, which is what makes §3.3's sentence true rather than hopeful: a
        local table that was emptied verifies exactly as a store that never anchored does.
        """
        ...

    def since(self, seq: int) -> Sequence[Anchor]:
        """Every anchor the provider holds at or above `seq`. Enumeration, not a single answer.

        **This is why §4.6's argument is not circular.** With `latest()` alone the kernel can read
        only its own local table for the history of which anchors exist, and that table is a cache
        the writer under suspicion can trim: delete every local row at or below a laundering
        checkpoint and nothing above it is missing, so `latest()` reveals nothing.
        """
        ...


@dataclass(frozen=True)
class AnchorBreak:
    """One thing wrong with the anchor record, named and placed (§3.4)."""

    name: str
    seq: int | None
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "seq": self.seq, "detail": self.detail}


@dataclass(frozen=True)
class AnchorReport:
    """What `verify_anchors` found. Separate from `ChainReport`, structurally (§3.4, §8.1).

    **`G11`'s contract does not change**, and the separation is what makes that true rather than
    argued. `G11`'s control reads `intact.ok` over the whole `ChainReport`, so it cannot carry an
    anchor break and pass: one extra break of any kind fails the control. A first draft asserted
    that a report could carry `anchor_broken` while `G11` passed, and running it showed it cannot.

    **Three states, not two.** `ok` and `unavailable` are different answers, and collapsing them
    is how a briefly unreachable timestamp authority becomes indistinguishable from a truncation.
    """

    #: Every anchor reproduced and nothing was missing. False if anything at all was wrong.
    ok: bool
    #: The provider could not be reached or would not answer. **Not a break, and not a pass.**
    unavailable: bool
    #: Why, where `unavailable`; `None` otherwise.
    reason: str | None
    #: How many anchored pairs were actually checked against the chain.
    checked: int
    #: How many were superseded by an anchored checkpoint (§4.6). Reported, never hidden.
    superseded: int
    breaks: list[AnchorBreak]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "unavailable": self.unavailable,
            "reason": self.reason,
            "checked": self.checked,
            "superseded": self.superseded,
            "breaks": [item.to_dict() for item in self.breaks],
        }


class AnchorSource(Protocol):
    """What `verify_anchors` needs from a store: the local anchor cache, the chain, the checkpoint.

    A `Protocol` rather than an import of `StateStore`, because `state.py` imports *this* module
    and `ARCHITECTURE.md` §6 says dependencies point downward. The same reason `ChainSource`
    exists in `receipt.py`.
    """

    def anchors(self) -> tuple[Anchor, ...]: ...

    def checkpoint(self) -> tuple[int, str] | None: ...

    def receipts(self) -> tuple[Any, ...]: ...

    def chain_head(self) -> tuple[int, str] | None: ...


def canonical_anchor(seq: int, hash: str, kind: str) -> bytes:
    """The bytes a provider is asked to vouch for, under this module's own domain tag (§3.2).

    Through `canonical_bytes`, so there is one canonicalizer in this library and not two
    (`SPEC-v0.6.md` §6.2). The domain tag is what stops an anchor token colliding with a scope
    hash or a precondition fingerprint computed over the same mapping, which is `SPEC-v0.9.md`
    §5.5's argument for a scope hash.
    """
    return canonical_bytes({"domain": ANCHOR_DOMAIN, "seq": seq, "hash": hash, "kind": kind})


def _checked_kind(kind: str) -> str:
    if kind not in ANCHOR_KINDS:
        raise InvalidArgument(
            f"an anchor kind must be one of {', '.join(ANCHOR_KINDS)}, got {kind!r}"
        )
    return kind


class _AnchorStore(Protocol):
    """What `make_anchor` needs from a store: the head, the local cache, and a way to add to it.

    Narrower than `AnchorSource` on purpose. Making an anchor never reads a receipt, so a
    protocol that demanded `receipts()` here would say that it does.
    """

    def chain_head(self) -> tuple[int, str] | None: ...

    def anchors(self) -> tuple[Anchor, ...]: ...

    def put_anchor(self, anchor: Anchor) -> None: ...


def make_anchor(
    store: _AnchorStore,
    provider: AnchorProvider,
    *,
    kind: str = INTERVAL,
    at: tuple[int, str] | None = None,
) -> Anchor:
    """Anchor the chain's current head, and cache what came back (§3.1, §3.2).

    **Fail-closed, in the direction §10's table states**: a provider that raises, times out or
    answers a shape the canonicalizer refuses means the anchor is **not made** and nothing is
    recorded as anchored. An anchor half-made is worse than none, because the local table would
    then claim a pair the provider never saw and every later verification would report
    `anchor_repudiated` about an honest store.

    Two orderings are refused here rather than at verification, because both are the operator's
    configuration going wrong and the cheapest place to say so is the moment it happens:

    * an `interval` anchor at or below the previous `interval` anchor, since the chain only grows;
    * an anchor whose time runs backwards against the one before it of the same kind. A monotonic
      sequence is the only property the kernel can check about a timestamp it did not issue, and a
      sequence that goes backwards is either a misconfiguration or the attack.
    """
    kind = _checked_kind(kind)
    if at is not None:
        # §4.6: a prune anchors its **checkpoint**, and a checkpoint names the `seq` pruned
        # through, which is below the head by construction. Without this a checkpoint anchor
        # could only ever be taken over the head, and §4.6's supersession rule -- an anchored
        # `seq` at or below an anchored checkpoint is superseded rather than broken -- would
        # have nothing to match against. Found by a mutation: ordering the two kinds jointly
        # survived every test, because nothing could produce a checkpoint anchor below an
        # interval one for the per-kind rule to have to allow.
        seq, digest = at
    else:
        head = store.chain_head()
        if head is None:
            raise InvalidArgument(
                "this store has no chain head to anchor; nothing has written a chained receipt yet"
            )
        seq, digest = head
    _require_ordering(store.anchors(), seq, kind)

    try:
        answer = provider.make(seq, digest, kind)
    except CTRLRunError:
        raise
    except Exception as refused:
        raise InvalidArgument(
            f"{ANCHOR_UNAVAILABLE}: the anchor provider did not answer "
            f"({type(refused).__name__}), so nothing was anchored"
        ) from refused

    token, anchored_at = _checked_answer(answer)
    anchor = Anchor(seq=seq, hash=digest, token=token, kind=kind, at=anchored_at)
    _require_time_moves_forward(store.anchors(), anchor)
    store.put_anchor(anchor)
    return anchor


def _checked_answer(answer: object) -> tuple[str, datetime]:
    """What a provider returned, or a refusal naming what was wrong with it.

    By shape and not by trust: an operator's provider is their own code, and a provider that
    returns `None` on failure rather than raising is the shape that would otherwise cache an
    anchor whose token is the string `"None"`.
    """
    if not isinstance(answer, tuple) or len(answer) != 2:
        raise InvalidArgument(
            "an anchor provider's make() must return (token, time); it returned "
            f"{type(answer).__name__}"
        )
    token, at = answer
    if not isinstance(token, str) or not token:
        raise InvalidArgument(
            f"an anchor token must be a non-empty string, got {type(token).__name__}"
        )
    if not isinstance(at, datetime) or at.tzinfo is None:
        raise InvalidArgument(
            "an anchor's time must be a timezone-aware datetime, because an anchor is a claim "
            f"about when; got {type(at).__name__}"
        )
    return token, at


def _require_ordering(existing: Iterable[Anchor], seq: int, kind: str) -> None:
    """§3.2's ordering rule, **per kind** and never jointly.

    A `checkpoint` anchor is ordered only against other checkpoints. Ordering the two kinds
    together refused every checkpoint a pruning deployment would ever take, because a prune's
    checkpoint sits far below the newest interval anchor.
    """
    same = [anchor for anchor in existing if anchor.kind == kind]
    if not same:
        return
    highest = max(anchor.seq for anchor in same)
    if seq <= highest:
        raise InvalidArgument(
            f"an {kind} anchor must be above the last {kind} anchor: this one names seq {seq} "
            f"and the last names seq {highest}"
        )


def _require_time_moves_forward(existing: Iterable[Anchor], anchor: Anchor) -> None:
    same = [item for item in existing if item.kind == anchor.kind]
    if not same:
        return
    latest = max(item.at for item in same)
    if anchor.at < latest:
        raise InvalidArgument(
            f"this {anchor.kind} anchor's time {anchor.at.isoformat()} is before the last one's "
            f"{latest.isoformat()}; a timestamp sequence that runs backwards is a "
            "misconfiguration or the attack, and ctrlrun cannot tell which"
        )


def verify_anchors(store: AnchorSource, provider: AnchorProvider) -> AnchorReport:
    """Check every anchor the provider holds against the chain in this store (§3.4).

    **The provider is asked what it holds before the local table is consulted**, and that ordering
    is the section's load-bearing decision (§3.3). ctrlrun's table is a *cache*, not a record: a
    row deleted from it is checked anyway, because the question came from outside; a table that
    was emptied verifies exactly as a store with no anchors does, which is `anchor_missing`.

    An earlier draft had this read the local table and ask the provider about what it found. That
    is the same defect one level up: the set of questions came from the rewritable side, so
    deleting the newest local row removed the only question that would have failed.

    **Precedence, because two rows can both hold.** In the canonical attack, truncate the chain
    *and* delete the newest local anchor, and both `anchor_broken` and `anchor_missing` apply.
    `anchor_broken` wins: `anchor_missing` reads to an operator as a misconfiguration and
    `anchor_broken` reads as tamper, and naming the milder one first is how a real finding gets
    filed as a config ticket.

    **`anchor_missing` does not fire on the exposed window.** An earlier definition read *"the
    store's chain reaches a `seq` none of them covers"*, which is the state §2.4 blesses as normal:
    `(last anchored seq, current head]` is where every honest deployment lives between anchors.
    One honest action after an anchor would have failed `G28` on every anchoring deployment. A
    fail-closed check that fires on the honest case is not fail-closed, it is broken.
    """
    try:
        held = list(provider.since(0))
        newest = provider.latest()
    except Exception as refused:
        return AnchorReport(
            ok=False,
            unavailable=True,
            reason=(
                f"the anchor provider could not be reached ({type(refused).__name__}); "
                "nothing about the anchors is known either way"
            ),
            checked=0,
            superseded=0,
            breaks=[],
        )

    cached = {anchor.token: anchor for anchor in store.anchors()}
    breaks: list[AnchorBreak] = []

    if not held and not cached:
        # §3.4: a configuration that anchors and holds no anchor at all. This is
        # `SPEC-v0.10.md` §4.3's `upstream_unverified` in a new place: an anchor that is never
        # made would otherwise switch the check off by being absent, which is `SPEC-v0.4.md`
        # §3.8's false green.
        return AnchorReport(
            ok=False,
            unavailable=False,
            reason=None,
            checked=0,
            superseded=0,
            breaks=[
                AnchorBreak(
                    "anchor_missing",
                    None,
                    "this configuration anchors and the provider holds no anchor at all",
                )
            ],
        )

    by_seq = _chain_hashes(store)
    checkpoint = store.checkpoint()
    checkpoint_seq = None if checkpoint is None else checkpoint[0]
    # §4.6: the set of checkpoints that may supersede an anchor comes from the **provider alone**.
    #
    # **An earlier version unioned the local table into this, and an independent review bought
    # supersession with one `INSERT` into it.** That is the laundering hole §4.6 exists to close,
    # reopened by the same confusion §3.3 spends a subsection on: the local table is a cache the
    # writer under suspicion can write, so a rule that reads it takes its answer from the side
    # that cannot be trusted. The forged row did not even need a real hash -- only `(seq, kind)`
    # was read -- and it was never checked against the provider, because the walk iterates what
    # the provider holds.
    #
    # **And the pair must match**, not merely the `seq`. An anchored checkpoint supersedes only
    # if the provider vouches for the hash the store's checkpoint row actually names; otherwise
    # an attacker anchors any checkpoint at that `seq` and rewrites the row underneath it.
    anchored_checkpoints = {
        anchor.seq
        for anchor in held
        if anchor.kind == CHECKPOINT
        and checkpoint is not None
        and anchor.seq == checkpoint[0]
        and anchor.hash == checkpoint[1]
    }

    checked = 0
    superseded = 0
    for anchor in sorted(held, key=lambda item: (item.seq, item.kind)):
        present = by_seq.get(anchor.seq)
        if anchor.token not in cached:
            # §3.2: the provider names an anchor the local table lacks. This is the deletion
            # attack seen from the side that cannot be rewritten.
            #
            # **It does not `continue`, and an earlier version did.** In the canonical attack the
            # chain is truncated *and* the newest local row deleted, so both this and
            # `anchor_broken` apply, and a probe of exactly that showed the earlier version
            # reporting only this one. §3.4's rule is that `anchor_broken` wins, for the reason
            # it gives: `anchor_missing` reads to an operator as a misconfiguration and
            # `anchor_broken` reads as tamper, so naming the milder one alone is how a real
            # finding gets filed as a config ticket. Both are reported, and the sort below puts
            # the one that means tamper first.
            breaks.append(
                AnchorBreak(
                    "anchor_missing",
                    anchor.seq,
                    f"the provider holds an anchor at seq {anchor.seq} that this store's local "
                    "table does not; the cache was trimmed, or this is not the store that was "
                    "anchored",
                )
            )
            if present is not None and present == anchor.hash:
                # The chain still reproduces it, so the cache is the only thing wrong. The
                # provider's answer is what decides, and it decided the pair is intact; there is
                # nothing further to check, because `check()` needs a token this store lost.
                continue

        if present is None:
            # §4.6: absent is not automatically broken. An anchored `seq` at or below a
            # checkpoint that is ITSELF anchored is **superseded**: the checkpoint's anchor
            # carries the claim forward, and the provider's own record still shows that a prune
            # happened, at what seq and when.
            if (
                checkpoint_seq is not None
                and anchor.seq <= checkpoint_seq
                and checkpoint_seq in anchored_checkpoints
            ):
                superseded += 1
                continue
            breaks.append(
                AnchorBreak(
                    "anchor_broken",
                    anchor.seq,
                    f"the anchored seq {anchor.seq} is absent from this chain, and no anchored "
                    "checkpoint accounts for its removal",
                )
            )
            continue

        if present != anchor.hash:
            breaks.append(
                AnchorBreak(
                    "anchor_broken",
                    anchor.seq,
                    f"seq {anchor.seq} hashes to {present} and the anchor froze {anchor.hash}",
                )
            )
            continue

        if anchor.token not in cached:
            # Already reported `anchor_missing` above, and `anchor_broken` just now if the chain
            # disagreed too. There is no cached token to ask `check()` about.
            continue

        try:
            vouched = provider.check(anchor.seq, anchor.hash, anchor.token)
        except Exception as refused:
            return AnchorReport(
                ok=False,
                unavailable=True,
                reason=(
                    f"the anchor provider could not be reached ({type(refused).__name__}) while "
                    f"checking seq {anchor.seq}; nothing about the anchors is known either way"
                ),
                checked=checked,
                superseded=superseded,
                breaks=[],
            )
        if not vouched:
            breaks.append(
                AnchorBreak(
                    "anchor_repudiated",
                    anchor.seq,
                    f"the provider does not recognise the pair at seq {anchor.seq} under the "
                    "token this store holds for it",
                )
            )
            continue
        checked += 1

    if newest is not None and not any(anchor.seq == newest[0] for anchor in held):
        breaks.append(
            AnchorBreak(
                "anchor_missing",
                newest[0],
                f"the provider's latest() names seq {newest[0]} and its since() did not return "
                "it; the provider is answering two different questions inconsistently",
            )
        )

    # `anchor_broken` first, whatever order they were found in: an operator reading the top of a
    # report must see the one that means tamper rather than the one that means misconfiguration.
    breaks.sort(key=lambda item: ANCHOR_BREAKS.index(item.name))
    return AnchorReport(
        ok=not breaks,
        unavailable=False,
        reason=None,
        checked=checked,
        superseded=superseded,
        breaks=breaks,
    )


def _chain_hashes(store: AnchorSource) -> dict[int, str]:
    """Every chained receipt's `seq` and the hash stored for it.

    Read through the same `receipts()` every other reader uses, so a row this binary cannot
    construct (`SPEC-v0.11.md` §5.2) is simply not in the map: it has no hash to compare, and
    `verify_chain` is already reporting `content_altered` about it. The anchor does not report
    the same fact a second time under a different name.
    """
    found: dict[int, str] = {}
    for receipt in store.receipts():
        seq = getattr(receipt, "seq", None)
        digest = getattr(receipt, "hash", None)
        if isinstance(seq, int) and isinstance(digest, str):
            found[seq] = digest
    return found
