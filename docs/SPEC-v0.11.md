# SPEC-v0.11: Evidence

A delta over `SPEC-v0.1.md` to `SPEC-v0.10.md`. Where those settled something, this cites them and
does not restate it.

**One question: can the record be trusted after the fact, and kept?**

Every milestone so far added something the receipt records. None asked whether the receipt is still
worth reading a year later, on a database an administrator can write to, after somebody pruned it.
v0.11 is the first milestone whose subject is the evidence rather than the decision.

It is also the first that opens by demonstrating a defect in the thing it is about. §2 is that
demonstration, and it is a transcript rather than an argument.

---

## 1. The milestone and its items

| Item | What it builds | Guarantee |
|---|---|---|
| 0 | this document | none |
| 1 | a reader that names a bad row and blinds nothing else (§5) | none; its evidence is its tests |
| 2 | the anchor, and the break kinds it makes detectable (§3) | `G28` |
| 3 | retention: a chain-preserving prune, its checkpoint, and a hold (§4) | `G29`, `G30` |
| 4 | one chain across five receipt schema versions, walked end to end (§6) | `G31` |
| 5 | enforcement coverage, from events already written (§7) | none; it reports |
| 6 | release 0.11.0, without the tag | none |

**Item 1 goes first, deliberately.** Every other item reads the chain, and §2.3 shows that today one
malformed value stops four readers together. Building the anchor on a reader that one `UPDATE` can
blind would put the milestone's headline on the defect it exists to answer.

**Whether this is one milestone or two is a maintainer's decision and is not taken here.** If it is
two, the split that costs least is items 1, 2 and 4, which are a correctness claim, against items 3
and 5, which are an operations feature. Only the first group is on `ROADMAP.md`'s critical path to
v1.0. Nothing in this document assumes either answer, and §8's ids are assigned in item order so a
split renumbers nothing.

### 1.1 The four rules

Every item is measured against these.

1. **The anchor consumes a timestamp and issues nothing.** No key generation, no rotation, no
   revocation, no signing. `SPEC-v0.3.md` §1.1's rule, that ctrlrun consumes identity and issues
   none, applied to time. It is the line between this milestone and the one `ROADMAP.md` keeps off
   the roadmap, and an anchor that minted anything would have crossed it.
2. **A prune introduces no break the chain did not already have, or it is refused.** A prune that
   produces a `missing` has destroyed evidence and called it retention. Stated as a **delta** and not
   as "the chain verifies", because `unchained` is a pre-existing condition on any store migrated
   from v0.1 to v0.5, survives a prefix prune and can never be inside a prefix: the absolute version
   made retention permanently impossible on the oldest and largest stores, which are the ones it is
   for. There is no `--force`, no `--allow-gap`, and no setting that admits a break the prune caused.
3. **A malformed row names itself and blinds nothing else.** One tampered row costs one row.
4. **A clean coverage result is not a verdict.** No score, no percentage, no badge, and no sentence
   a reader could quote as one. `SPEC-v0.4.md` §3.9 on a new surface: verify never grades an
   operator's document, and a coverage number that ranked their deployment would be the same claim
   in a new costume.

---

## 2. What the chain detects today, and what it does not

`SPEC-v0.6.md` §6.5 names six break kinds and `verify_chain` produces all six. `content_altered`,
`hash_missing`, `link_broken`, `missing`, `head_mismatch`, `unchained`. The walk is sound about what
it walks, and `verify_chain`'s own docstring already states the limit: *somebody who can rewrite
every row including the head recomputes it and it verifies.*

**This section makes that sentence concrete, because a limit stated in prose beside a mechanism gets
read as a caveat rather than as an attack.** What follows was run against a real SQLite store at
`main`, and the output is transcribed rather than described.

### 2.1 Truncation, in two statements

Five receipts, chain verifies. Then:

```sql
DELETE FROM receipts WHERE seq > 3;
UPDATE receipt_chain SET seq = 3, hash = <the hash already stored at seq 3>;
```

```
after DELETE only        -> ok: False breaks: [('head_mismatch', 5)]
after DELETE + head fix  -> ok: True  breaks: []  verified: 3
```

The first line is the head doing its job: `SPEC-v0.6.md` §6.3 put it there precisely to catch
deletion at the end. The second line is the whole of §2. **The head is a row in the same database,**
so the statement that catches the truncation is one the same writer can issue. Two receipts erased
and the record says it is intact.

### 2.2 Append, and why no key makes it worse

The hashing rule is public and no key is involved, so a forged receipt can be given a correct
`prev_hash` and a correct `hash`:

```
after forged APPEND + head fix -> ok: True  verified: 4  breaks: []
the forged action is now in the record: a.b.FORGED
```

An action that never happened is now evidence that it did, and the chain grades it `verified`. This
is worse than truncation in one specific way: truncation removes a record somebody might remember
existed, and append manufactures one nobody can distinguish from the rest.

**v0.11 does not close this, and §2.4 says so.** An appended row lands at head + 1, which is above
any `seq` an anchor has recorded, so no anchor can be broken by it. This paragraph used to end with
the opposite claim, and a review demonstrated it false before any code was written.

**Neither of these is a new finding.** `SPEC-v0.6.md` §6.4 says truncation and append are undetected
and `THREAT_MODEL.md` lists a malicious administrator as out of scope. What §2 adds is the
measurement, because *two statements* and *out of scope* are very different sentences to read next to
a product that sells evidence.

### 2.3 One malformed value blinds four readers

`SPEC-v0.7.md` §12.5 recorded this and deferred it twice. Measured at `main`, on a four-receipt
chain with one `UPDATE` setting one declared key to a value of the wrong type:

```
receipts                   exit=1  Error: a control id must be a string, got 1.5
receipts --verify-chain    exit=1  Error: a control id must be a string, got 1.5
inspect <untouched action> exit=1  Error: a control id must be a string, got 1.5
stats                      exit=1  Error: a control id must be a string, got 1.5
```

**`inspect` on an action the tamper never touched is the sharp one.** One bad row hides an unrelated
action's entire history, so the blast radius is not "the tampered receipt is unreadable" but "the
store is unreadable".

Two things this section states carefully, because the earlier write-up rounded them off.

- **The blinding is at construction, not at the walk.** `verify_chain` already catches a document it
  cannot canonicalize and reports `content_altered` at its `seq` (`receipt.py`, the
  `except CTRLRunError` around `chain_hash`). What raises is `store.receipts()` building `Receipt`
  objects before the walk begins. §5 therefore has a narrower target than `SPEC-v0.7.md` §12.5 implies.
- **`effects` was not exercised by the probe** and is not claimed here. The probe's actions carried
  no effect key, so it read nothing and exited 0. A later review drove it with four real committed
  effects and the tamper in place: it exits 0 and prints all four, so it does **not** blind and item
  1 drops it from `SPEC-v0.7.md` §12.5's list of five.
- **The MCP operator server blinds too, and it is a network surface.** `gateway/operator.py`'s
  `_receipts` and `_stats` tools call `store.receipts()` directly, so the same one `UPDATE` takes out
  the remote console as well as the CLI:

  ```
  gateway.operator _receipts tool (MCP, network)  RAISES InvalidArgument
  gateway.operator _stats tool    (MCP, network)  RAISES InvalidArgument
  ```

  `verify/scenarios.py` and `conformance/suites.py` are on the same read. **Item 1's scope is the
  shared read, not the CLI**, and a fix that landed in `cli/main.py` alone would leave the console
  blind while the terminal recovered.

### 2.4 What an anchor can and cannot fix

Stated here, once, in the section that makes the claim, rather than in a later section a reader may
not reach.

**An anchor freezes a prefix.** It records that at time T the chain's head was `(seq, hash)`, so
anything at or below that `seq` can no longer be removed or altered without the anchored pair
failing to reproduce, **unless an anchored checkpoint accounts for its removal** (§4.6). That clause
is not a loophole and §4.6 is where it is kept from becoming one: a checkpoint must itself be
anchored, through the provider, which is outside the store.

That sentence is the whole claim, and everything else follows from it by arithmetic:

| Attack | Detected? |
|---|---|
| §2.1's truncation, when the anchored `seq` is above the new head | **yes**: the anchored `seq` is absent, and no anchored checkpoint accounts for it (§4.6) |
| any rewrite at or below an anchored `seq` | **yes**: the hash there differs |
| §2.2's forged append | **no.** It lands at head + 1, above every anchored `seq`, so no anchored pair stops reproducing. A later anchor freezes the forged chain as readily as an honest one |
| receipts written and erased entirely between two anchors | **no.** They were never at or below an anchored `seq` |
| an administrator who rewrites everything before the next anchor | **no**, and `ROADMAP.md`'s "Does not close" paragraph says so |
| who wrote any of it | **no.** Authorship is out of scope; §11 keeps signing off the milestone for the reason `SPEC-v0.6.md` §11 gives |

**The exposed window is `(last anchored seq, current head]`**, and its size is the operator's choice
of interval. That is the number an operator tunes, and it is the number the documentation quotes
rather than any sentence about tamper-evidence.

An earlier draft of this section said the anchor closes "a suffix erased **or appended** in that
window". A review ran it: an append is never detected, and receipts created and destroyed inside the
window are never detected either. `ROADMAP.md` line 382 makes the same claim and item 2 corrects it
there. **This is the first thing in this project a reader could mistake for tamper-proofing, so it
is stated as a table rather than as prose.**

---

## 3. The anchor

### 3.1 What is anchored

**The pair the head already holds: a `seq` and the hash at that `seq`.** Nothing else. An anchor is
a record that *at time T, the chain's head was (seq, hash)*, made somewhere the store's writer does
not control.

An anchor is not a copy of the chain, not a backup, and not a second head. It is one pair and a
time, and the whole of its power is that reproducing it later requires the chain between genesis and
that `seq` to be exactly what it was.

### 3.2 What an operator supplies, and what is refused (O1)

`ROADMAP.md` names RFC 3161, and §9 does not put an RFC 3161 client in the wheel. The kernel's rule
since `SPEC-v0.1.md` is that the core stays stdlib plus `pyyaml` and `click`, and a timestamp
protocol client is a network client.

So the anchor is a **`Callable` the operator supplies**, in the shape `SPEC-v0.9.md` §5.4 settled for
a scope provider: the operator's own code, answering from the operator's own system, and the kernel
matching.

**It has three calls, not two, and the third is why.** An earlier draft had `make` and `check`, and
a review broke it in one extra statement: with only those two, the record of *which* anchors exist
lives in ctrlrun's table, so deleting the newest row there leaves the older anchor reproducing and
the truncation invisible. Three SQL statements instead of two, which is the number §3.3 claimed the
design avoided.

| Call | Answers |
|---|---|
| `make(seq, hash, kind)` | returns an opaque token, or raises. `kind` is `interval` or `checkpoint` |
| `check(seq, hash, token)` | whether that pair and that token correspond |
| `latest()` | the highest `seq` the provider holds an anchor for, and its token |
| **`since(seq)`** | **every anchor the provider holds at or above `seq`.** Enumeration, not a single answer |

**`since()` is why §4.6's argument is not circular.** §4.6 says an attacker who launders an erasure
must leave an anchored checkpoint in the provider's record, so an operator can see a prune happened.
With `latest()` alone the kernel can read only its own local table for that history, and §3.3 spends
a subsection establishing the local table is a cache the writer under suspicion can trim: delete
every local row at or below the laundering checkpoint and nothing above it is missing, so `latest()`
reveals nothing. Enumeration moves the history to the side that cannot be rewritten.

**An anchor carries its `kind`, and `interval` and `checkpoint` are ordered separately.** An earlier
draft ordered all anchors by `seq` and required each to be above the last, which a review showed
refuses the one anchor §4.6 requires: a deployment anchoring hourly and pruning at ninety days makes
its checkpoint anchor far *below* its newest interval anchor, so the rule refused it, so the prune
was refused, **forever**. §4.6 was written because an anchoring deployment should not have to choose
between pruning and a permanent tamper signal, and as drafted it landed on the first horn.

`latest()` is the one that closes the gap, because it is answered **outside**. ctrlrun's table is
then a cache and not a record: if it names fewer anchors than the provider holds, the provider wins
and the missing one is checked anyway.

**What ctrlrun refuses to accept as one**, and this is the fail-closed half:

- **A provider that raises is `anchor_unavailable` and never a pass.** `SPEC-v0.9.md` §5.6's rule for
  a scope provider, unchanged: a provider that cannot answer has not answered yes.
- **A `latest()` naming a `seq` for which the store holds no receipt is `anchor_broken`.** This is the
  deletion above, seen from the side that cannot be rewritten.
- **A provider whose answer does not canonicalize is refused**, by `action.py`'s canonicalizer with
  its own domain tag, so an anchor token can never collide with a scope hash or a precondition
  fingerprint over the same mapping. `SPEC-v0.9.md` §5.5 makes the same argument for a scope hash.
- **An anchor whose time runs backwards against the one before it is refused.** A monotonic sequence
  is the only property the kernel can check about a timestamp it did not issue, and an anchor
  sequence that goes backwards is either a misconfiguration or the attack.
- **An `interval` anchor whose `seq` is at or below the previous `interval` anchor is refused**, and
  a `checkpoint` anchor is ordered only against other checkpoints. The first draft ordered only time
  while `latest()` is defined on `seq`, so nothing required the two orderings to agree; the fix for
  that then refused every checkpoint, which is why the two kinds are ordered separately rather than
  jointly.

### 3.3 Where it lives, and the question that decides it

**Outside the store.** This is O2 and it is the section's load-bearing decision, so the argument is
written rather than the conclusion.

An anchor inside the store is an anchor the writer under suspicion can rewrite, which is §2.1
exactly one level up: the head was in the database, and that is why two statements were enough. An
anchor in the same database would make it three.

So the anchor **record** is the operator's, held wherever their provider holds it, and ctrlrun keeps
a local copy of what it needs to ask the question: the pair, the token, and the time. Those live in
a table §9 names.

**The local table is a cache, and the difference is load-bearing.** A first draft of this section
said rewriting it "is not a hole, because the provider's answer is what decides". That was wrong and
a review demonstrated it: the provider decides *the question it is asked*, and with only `make` and
`check` the set of questions came from the rewritable table. Deleting the newest row there removed
the only question that would have failed.

§3.2's `latest()` is what makes the sentence true rather than merely hopeful. **Every verification
asks the provider what it holds before consulting the local table**, so a local row that was deleted
is checked anyway, and a local table that was emptied verifies exactly as a store with no anchors
does: `anchor_missing`, which is a break.

An operator who points the provider at a file in the same directory has an anchor worth what that
file is worth. The sentence `SPEC-v0.8.md` §6.6 and `THREAT_MODEL.md` both use of a feed applies
unchanged: it is worth what its source is worth, and the documentation says so where the feature is
described.

### 3.4 The three break kinds, and the report they are not in

**`CHAIN_BREAKS` does not change. It stays closed at six, and `verify_chain` is not touched.**

The first draft amended it to eight, and a review ran what that costs. `G11`'s positive control is
`intact.ok and intact.verified >= 3` over the whole `ChainReport`
(`verify/scenarios.py`), so **any** eighth kind appearing in that report fails `G11` with
`control failed`, the status that means the kernel is broken. Worse, `anchor_missing` fires on every
anchoring deployment while verify's own scratch store never anchors, so the failure would have been
universal rather than rare:

```
=== with one anchor break in the same report ===
   G11: fail
   reason: control failed
   counterexample: expected='the chain verify just wrote verifies'
                   observed="it reported 3 verified and ['anchor_missing']"
```

So the anchor gets **its own report and its own closed set**, `ANCHOR_BREAKS`, and the two never mix:

| Kind | When |
|---|---|
| `anchor_broken` | an anchored pair does not reproduce: the anchored `seq` is **present and hashes differently**, or it is absent and **not accounted for by an anchored checkpoint** (§4.6). §2.4's table is what this does and does not cover, and an append is not in it |
| `anchor_missing` | **a configuration that anchors holds no anchor at all**, or `since()` names an anchor the local table does not have |
| `anchor_repudiated` | `check()` answered **no**: the provider does not recognise a pair the local table claims it anchored. A forged or revoked token, or a provider that disowns the pair |

**`anchor_repudiated` exists because the outside record is allowed to say no.** The first draft's set
had no name for `check()` returning false, which is the provider's one substantive answer and the
entire reason for holding the record outside the store. A refusal with no name is the shape §3.4
exists to fix.

**`anchor_unavailable` is NOT in this set**, and an earlier draft put it there. It is a **transport
failure**, not a finding about the evidence, and a set that conflates the two teaches an operator to
ignore the ones that matter: a timestamp authority briefly unreachable would grade `G28` `fail`,
indistinguishable in the report from a truncation. `SPEC-v0.10.md`'s `upstream_unverified` is the
precedent and it cuts the other way: it is a **decision-time refusal**, not a break in a verification
report. **Refusing to act when you cannot ask is fail-closed; reporting tampering when you cannot ask
is a false positive**, and §3.4's own argument against the old `anchor_missing` applies one level
out. So an unreachable provider makes the report `unavailable` rather than `ok` or broken, `G28`
grades `N/A` with that reason, and §10 carries both rows.

**Precedence, because two rows could both hold.** In the canonical attack, truncate the chain and
delete the newest local anchor, both `anchor_broken` and `anchor_missing` apply. **`anchor_broken`
wins**, and the report carries it: `anchor_missing` reads to an operator as a misconfiguration and
`anchor_broken` reads as tamper, and naming the milder one first is how a real finding gets filed as
a config ticket.

**`anchor_missing` does not fire on the exposed window, and an earlier draft made it do exactly
that.** That draft read *"the store's chain reaches a `seq` none of them covers"*, which is the state
§2.4 blesses as normal: the window `(last anchored seq, current head]` is where every honest
deployment lives between anchors. A review measured it, one honest action after an anchor:

```
A. honest deployment, anchor just taken at head 4   -> []
B. same deployment, ONE honest action later
   -> [('anchor_missing', 5, 'the chain reaches seq 5; the highest anchored seq is 4')]
```

`G28` would have failed on every anchoring deployment except in the instant after an anchor. That is
the round-one fix moving its own defect one surface out: separating the reports stopped it failing
`G11` with `control failed` and left the definition that fires constantly untouched. **A fail-closed
check that fires on the honest case is not fail-closed, it is broken**, and `SPEC-v0.4.md` §2.2's
rule about a guarantee that could not have failed has a mirror image here: a guarantee that could not
have passed.

This is better than the amendment on three counts, and the third is the one that decides it. `G11`
is genuinely untouched rather than argued to be. `SPEC-v0.6.md` §6.5's closed set stays closed, so
this milestone amends one frozen surface instead of two. And §9's frozen table can name
`ANCHOR_BREAKS` as a symbol a test imports, where "`CHAIN_BREAKS` gains two members" is a membership
claim the frozen-name test's shape cannot express.

**`anchor_missing` is the row the section turns on**, and it is `SPEC-v0.10.md` §4.3's
`upstream_unverified` in a new place: an anchor that is never made would otherwise switch the check
off by being absent. `SPEC-v0.4.md` §3.8's false green is the failure this refuses.

A configuration that does **not** anchor reports none of them, and `G28` is `N/A` with a reason true of
the operator's document. Anchoring is opt-in and then fail-closed, which is the rule `SPEC-v0.3.md`
states in its preamble and every item inherits.

---

## 4. Retention

### 4.1 The prune, and rule 2

`../ctrlrun-docs/docs/postgres.md` says there is no retention policy today and says why one is hard
in the same breath: **deleting receipts from the middle or the end of the chain is detected as a
break by design.** That is the feature, not an obstacle, and a retention job that simply deleted
would be manufacturing §2.1 on purpose.

A prune removes a **prefix**: receipts from genesis through some `seq`. Never a suffix, never a
middle. A suffix is §2.1's attack and a middle is `missing` by construction, so the only shape that
can leave a verifiable chain is the one that moves the chain's start.

**What makes the gap legible is a checkpoint**, and it substitutes for **three** values, not one.
A review implemented the single-substitution version and measured what it leaves:

```
after PREFIX delete of seq<=3   -> ok: False verified: 2 breaks: [('missing', 1), ('link_broken', 4)]
walk seeded from the checkpoint HASH only:
                                -> ok: False verified: 3 breaks: [('missing', 1)]
```

`verify_chain` seeds **two** genesis values, `expected_prev = GENESIS_HASH` and `expected_seq = 1`,
and compares the head against a third, the last surviving `seq` and hash. A checkpoint that replaces
only the hash still reports `missing` at seq 1, which is the break rule 2 requires a prune to be
refused for. **A faithful implementation of the first draft built a prune §1.1 forbids.**

So the checkpoint supplies all three:

| Value | Without a checkpoint | With one |
|---|---|---|
| `expected_prev` | `GENESIS_HASH` | the hash at the pruned-through `seq` |
| `expected_seq` | `1` | the pruned-through `seq` plus one |
| the head comparison | the last chained receipt | unchanged, and it is why §10 refuses a prune through the head |

Two corrections to the first draft's prose while the section is open: a prefix delete reports
`missing` **as well as** `link_broken`, and `link_broken` fires **once** rather than "forever after",
because `expected_prev = recomputed` resyncs on every row.

### 4.2 The checkpoint is a row, not a receipt field

O4 asked whether a prune is itself an action with a receipt. **It writes a receipt and it is not
routed through `Control.execute`.** The first draft said the receipt was "ordinary, subject to
policy", and that answer contradicts O3 in the same document.

A review ran the contradiction. `control.py`'s gate is
`if self._require_approved_policy and action.name != POLICY_CHANGE_ACTION`, so it covers every
action but one:

```
prune under require_approved_policy (unapproved) -> ActionDenied: this deployment requires an
   approved policy, and the policy in force does not declare 'ctrlrun.policy.change'...
undeclared action                                -> ActionDenied: ctrlrun.retention.prune denied:
   unknown_action
```

That is exactly the trap O3 refuses: *a deployment that had not approved its current policy could
not prune, and a store that cannot prune is a store that fills.* O3 avoided it by keeping retention
out of the policy document, and O4 let it back in through the action gate. One of the two had to
move, and it is O4.

**A prune is an operator's act at the CLI, not an agent's action.** It writes a receipt, because
`SPEC-v0.1.md` §6 does not carve out an exception for evidence about evidence and an operator
deleting records should leave one. It is not decided by policy, because the thing that authorises it
is shell access to the store, which policy does not mediate and has never claimed to. An operator
who wants a human in the loop puts one in front of the command, where they already are for every
other destructive operation on their own database.

**But the receipt is not what the walk trusts.** A receipt naming itself a checkpoint is a string in
a document, and `SPEC-v0.3.md` §4.3.1 already settled the shape of that mistake: *a grant may legally be
named `no_authority`, so evidence that could be spoofed by naming a grant is not evidence.* A walk
that believed `action == "ctrlrun.retention.prune"` would accept a forged prefix-erasure written by
anyone who can insert a row.

So the checkpoint is **a row in a table of its own**, holding the `seq` pruned through and the hash
at it, and `verify_chain` reads it as it reads `receipt_chain`. The receipt records that the prune
happened, for a human; the row is what the walk uses.

**This does not make the checkpoint unforgeable**, and the document says so rather than implying
otherwise: a writer who can insert receipts can write a checkpoint row. What closes that is §3's
anchor, and only for the window between anchors. The two features are one argument, which is why
they are one milestone.

### 4.3 The hold

A hold names a range and refuses to prune it. O5 asked whether it needs a second state machine; it
does not. A hold is a row with a range and a reason, the prune consults it, and a prune overlapping
a held range is refused with the hold named.

**No expiry that lifts a hold automatically.** `SPEC-v0.9.md` §4's rule that an automatic expiry on
a hold is the refund rule in a costume applies here unchanged: a hold that lapsed on a timer would
release evidence on a schedule nobody reviewed.

### 4.4 The budget ledger, and the caveat that travels with it

`SPEC-v0.9.md` §7.3 says rows older than the longest window on any budget of a grant cannot affect a
future decision, so archiving them is safe. **That invariant is about decisions and not about
evidence**, and the caveat is load-bearing: an `AMBIGUOUS` effect older than that window still
**holds** a charge the operator surfaces display.

The first draft wrote that rule as *"a prune excludes un-released rows"*, and a review measured what
that excludes. `state.py`'s `_release_locked` says it plainly: **`COMMITTED` holds permanently and
only `FAILED` releases**, because a committed spend is a spend. So "un-released" is almost every row
in the ledger, permanently, and the rule would have made the feature inert while §10 turned it into
a refusal. `cli/main.py` already carries the distinction the draft missed: `"Un-released" is not "held"`.

**The rule is settlement, not release.** A prune excludes a ledger row whose effect is not in a
terminal state:

| Effect state | Charge | Prunable |
|---|---|---|
| `COMMITTED` | never released, because the spend happened | **only outside `SPEC-v0.9.md` §7.3's window.** See below: this is not "yes" |
| `FAILED` | released | **yes** |
| `AMBIGUOUS` | held until a human or a hook resolves it | **no.** `SPEC-v0.9.md` §4's hold, and deleting it would release authority nobody granted |
| `RESERVED` | held, because no transition has occurred | **no.** Still in flight |
| `EXECUTING` | held, for the same reason | **no.** The first draft's table omitted this state entirely, and `effect.py` persists five |

**The window is a condition, not a footnote, and the first draft made it one.** A review ran what
"`COMMITTED` is history and is prunable" costs, on a 250-unit daily budget:

```
ledger: [('refund:1','amount',100,None), ('refund:2','amount',100,None)]
third 100 on a 250 budget: REFUSED -> budget 'amount' on grant 'payer' ... is exhausted
pruned COMMITTED ledger rows: 2
SAME action after pruning the COMMITTED rows: ALLOWED   <-- authority manufactured
```

`control.py` sums `consumptions(...)` over `now - window` where `released_at is None`, and a
`COMMITTED` row is never released, so it counts. **Pruning it inside the window hands back authority
nobody granted**, which is precisely the hole `SPEC-v0.9.md` §4 exists to close and precisely what
§7.3's invariant is conditioned on. Round one replaced "un-released" with settlement and dropped the
window on the way through; §10's row carried the unconditional version, and §10 is the table an
implementer codes refusals from.

`NEW` is not in the table because `effect.py` says it "is never written to a store".

**The window is supplied to the prune, not resolved by it (O7).** A review found the first draft's
version uncomputable: a ledger row carries `grant_id`, `metric`, `amount`, `effect_key`, `attempt`,
`consumed_at` and `released_at`, and **no window and no limit**. Those travel on `Charge`, from the
authority document, and `state.py` says why in as many words: *a store that resolved a grant's
budgets would be reading the policy*, which `ARCHITECTURE.md` §6 forbids.

So resolving the window inside the prune would reinstate exactly the dependency §4.2 removes, and it
has two failure modes with no good answer: a row whose `grant_id` is no longer in the document has no
window at all, and a window an operator lengthens later retroactively un-prunes rows already pruned.

**`ctrlrun prune --older-than` takes a duration and the prune refuses any `COMMITTED` row newer than
it.** The operator supplies the number, as they supply the range, and the kernel checks the rule
rather than deriving the input. That is `SPEC-v0.9.md` §5.4's shape once more: the provider answers,
the kernel matches. It also makes the refusal computable from the ledger alone, which is the property
§4.2 needs.

The operator is told what number to use, and it is the only advice this section gives: **the longest
window on any budget of any grant**, which `SPEC-v0.9.md` §7.3 makes a safe over-approximation.

A prune that released a hold by deleting it would be manufacturing authority, which is the hole
`SPEC-v0.9.md` §4 exists to close. A prune that refused to touch `COMMITTED` rows would be a
retention feature that retains everything.

**The prune's scope, stated here because §4.1 is about receipts and an implementer meets the ledger
in §10's refusal row otherwise.** A prune takes a prefix of receipts *and* the ledger rows the table
above admits. The two are separately bounded: a receipt prefix by `seq`, a ledger row by its
effect's state and by `SPEC-v0.9.md` §7.3's window.

### 4.5 What a prune contends with

The first draft of this document contained no concurrency vocabulary at all. A review ran two
prunes, each individually valid under §10, in an order §4 did not exclude:

```
A validated: prune through 5 -> checkpoint 5
B validated: prune through 3 -> checkpoint 3
  after B (its checkpoint row overwrites A's), walking from B's checkpoint:
      [('missing', 4), ('link_broken', 6)]
```

Neither is refusable alone, and together they break rule 2. So:

- **A prune takes the same lock a receipt write takes**, the one row `SPEC-v0.6.md` §6.3 serializes
  every receipt write on. That makes a prune and a receipt write mutually exclusive, and two prunes
  mutually exclusive, on both backends. On SQLite this already happens by accident, because
  `put_receipt` uses `BEGIN IMMEDIATE` and SQLite admits one writer; **on Postgres it does not**,
  because `put_receipt` takes a row lock on `receipt_chain` and a `DELETE` on `receipts` does not
  contend with it. The Postgres implementation takes the lock explicitly, and item 3 proves it
  multi-process against a real server rather than with threads against SQLite.
- **A checkpoint is written only forward.** A checkpoint naming a `seq` at or below the one already
  recorded is refused, so B above is refused rather than racing.
- **A hold is consulted inside the prune's transaction**, not before it. §4.3 said "the prune
  consults it" and said nothing about when; a hold placed between the consult and the delete would
  be honoured by neither.
- **The prune's own receipt is written before the prune takes the lock**, and this is forced rather
  than chosen. `put_receipt` opens `BEGIN IMMEDIATE` on the store's own connection, so a prune
  already holding that transaction cannot write through it:

  ```
  writing the prune's receipt inside the prune's transaction ->
      OperationalError: cannot start a transaction within a transaction
  ```

  §4.2 says the prune writes a receipt and §4.5 says it takes the receipt-write lock, and the first
  draft never said which came first, so the two rules were unsatisfiable together on the shipped
  store. Before, and the crash window is the safe one: a receipt for a prune that did not happen
  over-reports, where a prune with no receipt is indistinguishable from §2.1.
- **That receipt records an intent, and its outcome is recorded against it.** Writing it first means
  it is already in the chain when the hold check and every §10 refusal run, so a **refused** prune
  would otherwise leave a receipt asserting an erasure that never happened. That is not the crash
  window, it is the ordinary outcome of `ctrlrun prune` against a held range. The receipt carries
  what was *asked for*, and its refusal is an ordinary `DENIED` result on that same action, which is
  what every other refusal in this kernel already does.

### 4.6 An anchor and a prune, which cancelled each other

**The first draft of this document specified items 2 and 3 so that they could not both run.** §3
never contained the word *prune*, §4 contained the word *anchor* once, each section verified alone,
and a review found what they do together:

```
C. after an honest prune through seq 3 (checkpoint written)
   checkpoint-seeded verify_chain   ok=True verified=2 breaks=[]
   an anchor taken at seq 2 before the prune
   -> [('anchor_broken', 2, 'the anchored seq is absent')]
```

The chain verifies and the anchor reports tampering. In steady state, anchoring hourly and pruning at
ninety days, **every anchor older than the retention window is permanently `anchor_broken`**, so
under rule 2 an anchoring deployment must either refuse every prune or live with a permanent tamper
signal. That is the cross-module class this project keeps finding: each part is right, and the pair
is not.

**The rule: a prune anchors its checkpoint before it deletes anything, and an anchored `seq` below a
checkpoint is evaluated against that checkpoint's anchor rather than against the chain.**

| Anchored `seq` | Evaluated how |
|---|---|
| above the newest checkpoint | against the chain, as §3.4 says |
| at or below it, and the checkpoint that covers it is itself anchored | **superseded**, not broken. The checkpoint's anchor carries the claim forward |
| at or below it, with no anchored checkpoint covering it | **`anchor_broken`**, which is §2.1's attack wearing a prune's clothes |

**The third row is what stops "superseded" becoming the hole.** An attacker who erases a prefix and
writes a checkpoint to explain it must also anchor that checkpoint, and anchoring goes through the
provider, which is outside the store. So the provider's own record shows that a prune happened, at
what `seq`, and when. **A prune becomes something an operator can see in the anchor history even
though the receipts are gone**, which is the whole of what retention owes evidence.

**"Itself anchored" means the provider says so, at the pair the checkpoint claims.** This sentence
was added by item 6 because the implementation read the word *anchored* the only other way it can
be read and the hole came straight back. `verify_anchors` built the set of anchored checkpoints
from the union of what the provider returned and what the store's own `anchors` table held, so one
`INSERT` beside the forged checkpoint row bought supersession, and the row's hash was never
compared to anything:

```
forged checkpoint at seq 4, local anchors row at seq 4 with hash 'sha256:not-a-hash-at-all'
-> ok=True superseded=1 breaks=[]
```

Two conditions, both required. The anchor must come from **`provider.since()` alone**, never from
the local table, which is inside the blast radius by definition. And its `(seq, hash)` must be the
pair the checkpoint asserts, because an anchor that is merely *present at that seq* proves nothing
about the hash the prune is asking the reader to accept. A local row the provider does not confirm
buys nothing.

This is the third time in this document that §4.6 has been the section at fault, after round two
wrote it and round three found it cancelling against §3.2. The class is stable: the anchor and the
prune are each right alone.

The ordering follows from it and is not negotiable: **anchor the checkpoint, then delete.** A crash
between them leaves an anchored checkpoint for a prune that did not happen, which over-reports and is
the safe direction. The reverse leaves a prefix erased with nothing accounting for it, which is
indistinguishable from §2.1.

---

## 5. A reader that names a bad row

### 5.1 The narrower target

§2.3 established that the blinding is at **construction**: both stores build every row with
`Receipt.from_dict` (`state.py`'s and `postgres.py`'s `_stored_receipt`), `receipts()` returns a
tuple rather than a generator, and one row `from_dict` refuses raises before any caller sees a
single receipt. `verify_chain` already handles a document it cannot hash. An earlier draft named
`Receipt.from_json`, which is not on this path and is the sentence an implementer greps for.

So the fix is not a new break kind, and `SPEC-v0.7.md` §12.5's first candidate is declined here with
the reason: `content_altered` already covers a document that cannot be canonicalized, and a second
name for the same fact would be two names for one break.

**The fix is `SPEC-v0.7.md` §12.5's second candidate**: a reader that yields per row and reports a row it cannot
construct, rather than raising out of the walk.

### 5.2 What a refused row becomes

A row `from_dict` refuses is yielded as a **refusal, not a receipt**, carrying its `seq`, its
`receipt_id` if that field alone is readable, and the type of what refused it.

**That `seq` has to come from the column, and today it does not.** Both stores read
`SELECT json, hash FROM receipts ORDER BY seq`: the column is ordered by and never selected, so
every `Receipt.seq` comes from `document.get("seq")`, which is the field a tamperer controls. A
review demonstrated the consequence, and it is not confined to item 1:

```
Receipt.seq values the store returns: [1, 99, 3]
breaks: [('missing', 2), ('content_altered', 99), ('missing', 100), ('link_broken', 3)]
```

So `verify_chain`'s docstring claim that *"Position comes from the store's `seq` column"* is false as
shipped, and §2 leans on it being true. **Item 1 changes both store reads to select `seq`**, which is
what lets a refused row carry a position at all and what makes the docstring true. By type and never by
message: `SPEC-v0.7.md` §6.11's rule, because the canonicalizer quotes what it refused and a lone
surrogate in a report is a report that cannot be printed.

Every caller of that shared read chooses: `receipts` prints the row as unreadable and prints the others, and so does the operator server's `_receipts` tool.
`verify_chain` reports `content_altered` at that `seq`, which is what it already does for a document
it cannot hash, so one tamper reads as one break whichever half catches it. `inspect` on an
unrelated action never sees it at all, which is the case §2.3 measured.

### 5.3 The negative control

**A store with no bad row keeps working for everybody whose store is intact**, and that is the
claim rather than "byte for byte on every reader": item 1 deliberately changes one thing about a
clean store's output, because §5.2's `SELECT seq` makes a receipt's position come from the column
instead of from the document.
This is the half that is easy to lose: a change that made a clean chain read differently would be a
change to shipped output, and item 1 is not a change to shipped output for anybody whose store is
intact.

---

## 6. One chain, five receipt schema versions

`ctrlrun.receipt/v7` is the schema today. A store kept since v0.6 holds **five**: `v3` (0.6), `v4`
(0.7), `v5` (0.8), `v6` (0.9), `v7` (0.10). The count is taken from `receipt.py`'s constants, which
is the only place it cannot be stale.

`ROADMAP.md`'s v0.11 prose said four, and so did its Exit line, which is what `G31` is graded
against. Both are corrected there. The Exit line also requires that *the checkpoint receipt of a
prune carries the version current when it was written*, which no guarantee in §8 covers: item 3
asserts it, because a checkpoint written today and read in two years is the case this milestone
exists for.

**No new field.** The version string already exists, and the rule since `SPEC-v0.3.md` §12.2 is that
every reader upgrades before any writer switches. What is new is the proof.

### 6.1 Built from released distributions

The chain under test is written by the **released wheels**, not by fixtures this build produces.
`pip install ctrlrun==0.6.x` into a scratch environment, write receipts, then 0.7, then 0.8, then
0.9, then this build, and verify across the whole thing.

A fixture is this build's opinion of what 0.6 wrote. The wheel is what it actually wrote, and
v0.10's release pass found what it found because it checked against PyPI rather than a fixture.

### 6.2 A version the binary does not know is named, not broken

A receipt whose schema label this binary does not recognise is **named** at its `seq` and is not
reported as a break. `SPEC-v0.6.md` §3.2 draws the same distinction for a `schema_version` row the
binary does not know, and the difference matters to the only person who reads the output: *this
evidence is from a future version* and *this evidence is tampered with* are different sentences and
call for different actions.

---

## 7. Enforcement coverage

From events **already written**. No new event type, no new column. Policy entries never exercised,
gateway tools never routed, `@protect` actions never seen.

If answering needs a new event, the question is wrong and item 5 stops and says so rather than adding
one.

**Rule 4 is what this item breaks if it breaks anything.** What it reports is a **list**, with a
reason each entry is on it, in the shape `ctrlrun scan` already uses, and the sentence that a policy
entry nothing exercised may be correctly unused. No score, no percentage, no ratio, no badge.

"There is no score" is a claim about the environment until something checks it, so item 5 greps its
own output in the shape `CLAIMS.md` uses.

---

## 8. Guarantees

`ctrlrun.guarantees/v6` becomes **`v7`**, moved once, by whichever item lands first.

| Id | Grades | Item |
|---|---|---|
| `G28` | a chain truncated at or below an anchored `seq` is refused against its anchor | 2 |
| `G29` | a prune leaves the chain with no break it did not already have | 3 |
| `G30` | a held range refuses to prune | 3 |
| `G31` | a chain spanning five receipt schema versions verifies end to end | 4 |
| `G32` | **an honestly pruned chain leaves a clean anchor report** | 3 |

Each with a positive control, each graded or `N/A` with a reason true of the operator's document, and
**each grading the same under `--only` as in a full run**. `SPEC-v0.9.md` §13.8 records G22 passing
for a reason that had nothing to do with G22; the parametrized agreement test covers the shipped ids
and extends to these.

**`G28`'s text says truncation and does not say append, and an earlier draft said both.** Round one
rewrote §2.4 into a table and left the claim standing here, which is the row that becomes the graded
guarantee's text in `ctrlrun.guarantees/v7` and in `ctrlrun verify`'s output. Correcting the prose
that argues a claim and leaving the claim in the registry is worse than not correcting it: the
argument is read once and the registry is read by every operator who runs `verify`.

**The same claim was on `ROADMAP.md`'s Exit line for v0.11**, which is what `G28` is graded against:
*"the truncation and append cases that `SPEC-v0.6.md` §6.4 lists as undetected now detect."* It is
corrected there now, along with the anchor bullet above it. **Cited by its words and not by a line
number**: an earlier draft of this document gave three different line numbers for two sentences in
another repository, and all three were wrong within a day.

**`G28`'s positive control is the attack in §2.1**, run against a real store: truncate, fix the head,
require the break. A guarantee whose scenario has never seen the attack it exists for is
`SPEC-v0.4.md` §2.2's guarantee that could not have failed.

**`G32` exists because §4.6's defect class would otherwise turn nothing red.** `G28` grades
truncation against an anchor and `G29` grades a prune against the chain; the *interaction*, an honest
prune leaving the anchor report clean, was graded by neither, and that interaction is the one a review
found had made items 2 and 3 mutually exclusive. A guarantee for each half and none for the pair is
how two correct sections ship cancelling each other.

### 8.1 `G11`'s contract does not change, and §3.4 is what makes that true (O6)

O6 asked whether the new break kinds are reported as `G11` failures or as a new guarantee. **A
new guarantee, and `G11` is untouched.**

`G11` is *an altered receipt is detected*, shipped and graded since v0.6. Routing `anchor_broken`
through it would widen a claim an operator has already read: a deployment passing `G11` today would
begin failing it for a property it never configured.

**The first draft asserted this and was wrong**, which is why §3.4 changed rather than this section.
It claimed a chain report could carry `anchor_broken` while `G11` passed. `G11`'s control reads
`intact.ok` over the whole report, so it cannot: one extra break of any kind fails the control. The
separation has to be structural, and §3.4 makes it so by giving the anchor its own report.

So the split is by **what the operator configured**, and it is now enforced by the type rather than
by an argument. `G11` grades the hash chain, which every deployment has, and cannot see an anchor
break. `G28` grades the anchor, is opt-in, and is `N/A` with a reason on a deployment that does not
anchor.

**Item 2 asserts this directly**: `G11` passes on a store whose anchor report carries every kind in `ANCHOR_BREAKS`. A
guarantee whose independence is argued rather than run is what this section already got wrong once.

---

## 9. Public API additions, frozen for v0.11

One justification per row. Anything not here is a spec amendment before it is code.

**`SPEC-v0.10.md` §9.4 is why this section is written differently from its predecessors.** Three of
v0.10's §9 rows were frozen and never built, and nothing turned red because no test asserted that a
frozen name exists. That test now exists. **Every row below names the item that builds it**, and the
release item adds each to the frozen-name list or records in §13 why it was deliberately not built.

| Symbol | Item | Why an existing name does not serve |
|---|---|---|
| `ctrlrun.anchor.AnchorProvider` | 2 | the operator's own code answering from the operator's own system, in the shape `SPEC-v0.9.md` §5.4 settled for a scope provider. Three calls, not two (§3.2), and `latest()` is the one that makes §3.3 true. A default implementation that fetched anything would put a network client in the wheel every user installs |
| `ctrlrun.anchor.ANCHOR_BREAKS` | 2 | its own closed set, because putting them in `CHAIN_BREAKS` fails `G11`'s control (§3.4). A **symbol**, so the frozen-name test asserts it by import rather than by membership |
| `ctrlrun.anchor.verify_anchors` | 2 | the walk that produces an `AnchorReport`. Separate from `verify_chain`, which is not touched |
| `ctrlrun.anchor.AnchorReport` | 2 | what `verify_anchors` returns |
| `anchor=` on `Control` | 2 | a deployment anchors or it does not; it is a property of the deployment, not of an action, so it does not go on `execute` |
| `ctrlrun.receipt.UnreadableReceipt` | 1 | §5.2's refusal, carried **by type and never by message**. The first draft of §9 had no row for item 1 at all, which is `SPEC-v0.10.md` §9.4's failure in the mirror direction: a public name shipping with no frozen row |
| `ctrlrun.state.StateStore.receipts` yields `Receipt | UnreadableReceipt` | 1 | **amends `SPEC-v0.6.md` §9.2's frozen protocol**, and both backends change: the read gains `SELECT seq` (§5.2) and stops raising on one bad row. The bar is cleared because a second backend cannot implement §5 without it |
| `ctrlrun.state.StateStore.put_anchor` / `.anchors` | 2 | **amends `SPEC-v0.6.md` §9.2's frozen protocol.** The bar is *a second backend could not be written without it*, and an anchor's local cache cannot be reconstructed from the tables that exist |
| `ctrlrun.state.StateStore.put_checkpoint` / `.checkpoint` | 3 | the same amendment. §4.2 is why a checkpoint the walk trusts cannot live in a receipt document |
| `ctrlrun.state.StateStore.put_hold` / `.holds` / `.release_hold` | 3 | **the first draft froze `ctrlrun hold` with no storage at all**, and `G30` grades a held range refusing to prune against a table that did not exist. A review found it; this is the row that was missing |
| `ctrlrun.migrations.MIGRATIONS` contains an entry whose `.id` is `0008_anchor_checkpoint_hold` | 2, 3 | the three tables above. **A membership claim on a named symbol, because a migration id can never be an attribute path**: `hasattr(ctrlrun.migrations, "0008_…")` can never be true, since a name beginning with a digit is not an identifier. The frozen-name list's rows gain an explicit **kind** (`parameter` or `member`), because the existing three-tuple shape already exists and a bare third element cannot say which check to run: a review put this row into it and got `TypeError: ... is not a callable object` from `inspect.signature` |
| `ctrlrun anchor`, `ctrlrun prune`, `ctrlrun hold` | 2, 3 | CLI commands. An anchor is made on a schedule by an operator and a prune is an operator's act (§4.2), where every other surface in this kernel is a library call made by an agent. §11 keeps the management plane off the roadmap and these are not one: each writes rows and prints lines |

**Every row above names something a test can assert**, and the shape it needs is stated per row. A review put
each row into `_FROZEN_V0_10`'s shape and found three that could not be written as a test row at all
(`StateStore gains anchor and checkpoint methods`, `migration 0008_…`, and `CHAIN_BREAKS gains two
members`, the last being a membership claim the test's shape cannot express). That is `SPEC-v0.10.md`
`SPEC-v0.10.md` §9.4's failure reproduced inside the section written to prevent it.

**Item 2 adds the v0.11 list to `tests/test_repository_signals.py` and every later item extends it**,
rather than the release item doing it once. `SPEC-v0.10.md` §9.4's gap was found at release precisely because nothing
turned red during the items.

**No new error type.** `errors.py`'s closed set covers every refusal here: a prune that would break
the chain is an `InvalidArgument`, an unavailable anchor provider is the same fail-closed shape as an
unavailable scope provider. If an item disagrees, it stops and asks.

**No receipt schema bump, and no policy schema bump.** O3 is answered: retention is configured where
it is performed, on the CLI, and not in the policy document. A policy key would make retention subject
to `require_approved_policy`, which sounds like a feature and is a trap: it would mean a deployment
that had not approved its current policy could not prune, and a store that cannot prune is a store
that fills. The prune's own receipt is where approval belongs, and §4.2 puts it there.

---

## 10. Fail-closed table for v0.11

| Situation | Result |
|---|---|
| the anchor provider raises, times out, or answers a shape the canonicalizer refuses **when an anchor is made** | `anchor_unavailable`; the anchor is not made and nothing is recorded as anchored |
| the provider cannot be reached **at verification time** | the report is `unavailable`; `G28` is `N/A` with that reason. **Not a break, and not a pass.** An earlier draft made it a break, so a briefly unreachable timestamp authority graded `G28` `fail`, indistinguishable from a truncation |
| an `interval` anchor whose `seq` is at or below the previous `interval` anchor | refused (§3.2). A `checkpoint` anchor is ordered only against other checkpoints, and an earlier draft's joint ordering refused every prune |
| a configuration anchors and the store holds **no anchor at all**, or `latest()` names one the local table lacks | `anchor_missing`, a break, never a pass. **Not** the ordinary window `(last anchored seq, current head]`, which §2.4 blesses |
| an anchored pair does not reproduce, **and no anchored checkpoint accounts for it** | `anchor_broken` (§3.4, §4.6). An anchor below an anchored checkpoint is **superseded**, not broken |
| `check()` answers no for a pair the local table claims was anchored | `anchor_repudiated` |
| an anchor's time runs backwards against the one before it | refused |
| a prune that would leave the chain reporting a break **the prune caused**: a `(kind, seq)` pair the same store did not report before it | refused, with the `seq` named. **Per pair, not per kind**: a store already reporting `missing` at `seq 7` must not thereby admit a new `missing` at `seq 1`. `unchained` carries no `seq` and is compared as `(unchained, None)` |
| a prune through the chain's head | refused. It leaves no chained receipt for the head to name |
| a checkpoint naming a `seq` at or below the one already recorded | refused (§4.5): this is the second of two racing prunes |
| a prune that would delete a ledger row whose effect is `AMBIGUOUS`, `RESERVED` or `EXECUTING` | refused (§4.4) |
| a prune that would delete a `COMMITTED` ledger row **inside `SPEC-v0.9.md` §7.3's window** | refused (§4.4). Pruning it hands back authority nobody granted, demonstrated there. Outside the window it is prunable |
| an anchored `seq` below a checkpoint that is not itself anchored | `anchor_broken` (§4.6): a prefix erased with a checkpoint written to explain it, and no anchor behind the explanation |
| a prune that would delete receipts before its checkpoint is anchored | refused (§4.6). Anchor, then delete |
| a prune overlapping a held range | refused, with the hold named |
| a receipt row `from_dict` refuses | that row is unreadable and named; every other row is read |
| a receipt whose schema label this binary does not know | named, not a break |

---

## 11. Out of scope

**Signed receipts.** Signing brings key generation, rotation and revocation, which is issuing, and
`SPEC-v0.3.md` §1.1 says this kernel consumes and issues none. Rule 1 is the same sentence about
time.

**Authorship.** An anchor proves the log existed in this form at that time. It does not prove who
wrote it. §2.4 says so where the claim is made.

**A SIEM, dashboards over receipts, a receipt query language, export formats beyond JSON and OTel.**
`ROADMAP.md`'s list, unchanged. A prune view is what a retention dashboard looks like and §9 is a CLI
command and `--json`.

**A retention *policy* language.** Ranges and holds, not rules. A language that expressed *keep
anything touching customer X for seven years* is a query engine with a scheduler, and it is the shape
that turns this into a product surface.

**Anything that reads a receipt to decide whether it may be pruned.** The operator names a range. A
kernel that decided which evidence mattered would be making the consequence-taxonomy claim
`SPEC-v0.9.md` refuses in a new place.

---

## 12. The review rounds, and what they changed

### 12.1 Round one: twelve findings, seven design errors

Recorded here rather than in a commit message, because `SPEC-v0.10.md` §11's finding was that a spec
is believed and a commit message is not.

**Twelve findings, seven of them spec-level design errors, every one demonstrated by running
something.** The three that mattered were one problem seen three ways: **the anchor's guarantee was
stated more broadly than the mechanism delivers, and the one place it touched shipped output was
reasoned about rather than run.**

| Was | Is |
|---|---|
| the anchor closes truncation **and append** (§2.4) | it freezes a **prefix**. An append lands above every anchored `seq` and is never detected. §2.4 is a table now, not a sentence |
| `CHAIN_BREAKS` gains two kinds | **it does not change.** `G11`'s control reads the whole `ChainReport`, so an eighth kind fails it with `control failed` on every anchoring deployment. The anchor gets its own report |
| the local anchor table being rewritable is not a hole (§3.3) | it was: deleting the newest row cost one statement. The provider gained a third call, `latest()`, answered outside |
| the checkpoint replaces `GENESIS_HASH` (§4.1) | it replaces **three** values. The one-value version leaves `missing`, which rule 2 requires a prune to be refused for |
| a prune excludes **un-released** ledger rows (§4.4) | `COMMITTED` is never released, so that was most of the ledger forever. The rule is **settlement**, not release |
| a prune's receipt is ordinary and subject to policy (§4.2) | that was O3's trap arriving through O4. A prune is an operator's act, not an agent's action |
| nothing about concurrency | §4.5. Two prunes, each valid alone, break rule 2 together |
| §9 froze three rows naming no symbol | `SPEC-v0.10.md` §9.4's failure inside the section written to prevent it. Every row now names something a test imports, and `ctrlrun hold` gained the storage `G30` grades |

Five citations pointed at the right file and the wrong section, and one quoted a sentence that is not
in the document it named. All corrected; every file-qualified reference now resolves.

**What this says about the process.** Item 0's review is required because a spec is the one artefact
with no test, and every error above would have become code. The two that a reviewer could only find
by *running* something, `G11`'s control and the ledger's release rule, are the two that would have
shipped.

---

### 12.2 Round two: fifteen findings, ten of them in round one's own fixes

Recorded because the distribution is the lesson. **Ten of fifteen were in text round one wrote**, and
the dominant shape was *a fix applied to the argument and not to the artifact*:

| Round one fixed | Round two found it had not |
|---|---|
| §2.4's append claim, rewritten as a table | the claim survived in **§8's `G28` row**, which is the text that becomes the graded guarantee, and on `ROADMAP.md`'s Exit line |
| `anchor_missing` moved out of `CHAIN_BREAKS` so it stops failing `G11` | its **definition** was untouched, so it fired on every honest deployment and failed `G28` universally instead |
| §4.4's release rule replaced with settlement | §10 carried the **unconditional** version, and pruning a `COMMITTED` row inside `SPEC-v0.9.md` §7.3's window manufactures budget authority |
| §9's three unwritable rows named | the migration row **still cannot be an attribute path**; naming it did not change its shape |
| §12.1 declared the citation class closed | **three new citation defects in the new text**, one quoting a paraphrase as if it were a file's words |

**And one nobody looked for in either round until it was searched for: §4.6.** Items 2 and 3 were
specified in isolation, each verified alone, and cancelled each other. §3 did not contain the word
*prune*; §4 contained the word *anchor* once.

**What this says about review rounds.** v0.9 and v0.10 each needed three, and the reason is now
legible: the first round finds what the author did not know, and the second finds what the author
did while fixing it. A milestone that stops at one round ships the second set.

---

### 12.3 Round three: fourteen findings, and what the three rounds say about the document

The pattern did not stop. Round two's new §4.6, written to fix two sections that cancelled each
other, **cancelled against a rule round two added in the same commit**: §3.2 required every anchor's
`seq` to exceed the last, and §4.6 requires anchoring a checkpoint far below the head, so no prune
could ever run. The section that names the cross-module class reproduced it one round later.

| Round | Findings | Of which introduced by the previous round's fixes |
|---|---|---|
| one | 12 | n/a |
| two | 15 | 10 |
| three | 14 | most of the high ones |

**What the three rounds actually diagnose is scope.** Almost every high finding after round one lives
in the *interaction* between the anchor (item 2) and retention (item 3): an honest prune breaking
every older anchor, the checkpoint anchor refused by the anchor ordering, the laundering attack on
"superseded", the ledger window that is not computable without reinstating a dependency §4.2 removed.
The anchor alone and the reader alone generated few defects and no high ones after round one.

**That is the evidence for the question §1 leaves open**, and it is now a much sharper question than
it was when §1 was written. It remains the maintainer's, and this section exists so it is decided
from measurements rather than from a sense of how big the milestone feels.

**The sweep this round asked for, adopted as a rule.** After any change to a definition, re-derive
§1.1's rules, §2.4's table, §8's registry and §10's rows from it, rather than editing the row that
motivated the change. Every one of round three's `HIGH` findings and half its `MEDIUM` ones was a
definition changed in one place and left standing in another.

---

## 13. What building v0.11 settled

Written by item 6, in one pass, from the CHANGELOG line each item leaves.

**`SPEC-v0.10.md` §11's rule is in force here from the start:** a sentence in this document whose
truth depends on a later item is a test that item owes, named in its table, or it is not in the
document. Every forward-looking sentence above is either a MUST in §1.1, a row in §9 naming its item,
or a row in §10.

---

### 13.1 What the items found that the document did not

Six items, and **every one of them found something by running the code rather than by reading
it**. That is the pattern worth carrying forward more than any individual finding.

**Item 1 found a second defect underneath the one it was sent for.** `verify_chain`'s docstring
has claimed since v0.6 that *"position comes from the store's `seq` column"*, and it was false as
shipped: both backends selected `json, hash` and ordered by a column they never read, so every
position came out of the document, which is the half a tamperer controls. One `UPDATE` setting a
document's `seq` to 99 reported `missing 2`, `content_altered 99`, `missing 100` and
`link_broken 3`: four breaks at three positions, two of them rows that do not exist.

**Item 1 shipped with a hole, and a review found it within the hour.** `json.loads` ran in the
generator expression that fed the new reader, *outside* its guard, so a row whose stored `json`
is not JSON at all raised through every reader exactly as before, and worse: `JSONDecodeError`
is not a `CTRLRunError`, so the CLI printed a traceback where 0.10.0 printed a clean message.
The tests missed it because **every tamper they ran changed a row's content**, and `{}` and a
float among the controls are both valid JSON. `T520` now parametrizes over seven ways a row can
fail to parse.

**Item 2's mutation run found a design gap, not a test gap.** Restoring §3.2's rejected joint
ordering of the two anchor kinds survived every test, because nothing could *produce* a
checkpoint anchor below an interval one for the per-kind rule to have to allow: `make_anchor`
anchored the head and nothing else. But §4.6 needs exactly that, since a prune's checkpoint sits
below the head. The rule was right and unreachable.

**Item 3 found that the prune held no lock at all**, in three stacked defects, by running two
prunes against a real Postgres server. The lock was taken inside the delete, so the validation
above it was unprotected; `put_anchor` and `put_checkpoint` each commit, so the transaction ended
mid-prune; and the connection is `autocommit=True` with every write taking an explicit `BEGIN`,
so a bare `SELECT ... FOR UPDATE` committed the instant it returned. **The symptom was a passing
test**: the losing prune was refused by the *anchor ordering*, which is shared state reached by
accident and would order differently under a different provider.

**Item 4's proof was wrong in the way that looks exactly like a pass.** Run as
`PYTHONPATH=src python scripts/five_schema_chain.py`, the variable is inherited by every child,
so all five "released wheels" imported the build under test. It printed a chain of ten receipts
that verified perfectly and reported **one** schema version. A run that checked nothing was
indistinguishable from a run that checked everything, except in the number the script exists to
produce.

**Item 5 corrected this document's own §7 in passing.** §7 says *"from events already written"*.
The action name is not on the event: `ACTION_PROPOSED` carries an `action_hash` and nothing that
maps it back. The answer comes from receipts, which every decided action leaves, **a denial
included** -- so an action that is always denied counts as exercised, and a design reading only
`EXECUTION_COMMITTED` would have told operators to delete the deny rule that was working.

### 13.2 The rule these six have in common

**A test passing is not the evidence. Running the real thing is.** Five of the six findings above
were invisible to a green suite, and three of them had a green test asserting the property that
was broken. The mutation runs caught what they caught because a mutation that survives is a
finding about the test; the rest needed a probe against a real store, a real server, or a real
released wheel.

The corollary this milestone adds to `SPEC-v0.10.md` §11: **an equivalent mutation is a design
finding.** Item 2's joint-ordering mutation survived because the input that distinguishes the two
rules could not be constructed, and that was not a gap in the tests, it was a gap in the code.
Item 4's string-versus-number comparison survived for the same shape of reason, and the answer
was to make the derivation take its inputs so a test could hand it `v10`.

### 13.3 Two overclaims that were already published

Neither was found by a test, because neither was in code.

**`OWASP-SOLUTIONS-LANDSCAPE.md` said the anchor detects "truncation and append."** It does not
detect an append: a forged receipt lands above every anchored `seq`, so no anchored pair stops
reproducing and a later anchor freezes the forged chain as readily as an honest one. That
document is the OWASP submission. `T531` now runs a forged append and requires both reports to
stay clean, so the limit is a tested property rather than a sentence somebody has to remember.

**`ROADMAP.md`'s v0.11 line said enforcement coverage comes "from events already written."** It
cannot, for §7's reason above.

The rule worth stating: **a claim about what a feature does not do needs a test as much as a
claim about what it does**, because nothing else will ever contradict it.

### 13.4 The review item 3 required, and the seven defects it found after the merge

The build order said *independent review is REQUIRED for items 2 and 3*, and of the two, *item 3's
is the one not to skip: a prune is the only operation in this library that destroys evidence, and a
defect there is a loss rather than a refusal.* The review ran. It found **seven defects**, every one
demonstrated with a script against the merged code, and it finished after the item had merged, which
is its own finding and is recorded below.

**The first one is the reason the instruction exists.** §4.6's supersession rule was implemented as
the union of what the provider returned and what the store's own `anchors` table held. One `INSERT`
beside a forged checkpoint row bought supersession, with a hash nothing ever compared. The section
that named this attack in round three of the spec review reproduced it in code, which is as clear a
statement as this project has yet produced that **a defect named in a document is not a defect
closed.** §4.6 now says which of the two readings is meant.

| Defect | What it cost |
|---|---|
| supersession decided from the local `anchors` table | §4.6 bought nothing: an erased prefix, a forged checkpoint and one local row read as `superseded` |
| the SQLite prune dropped its lock at its first write | `with connection:` committed `BEGIN IMMEDIATE`. A failed prune left `missing` and `link_broken` on a chain intact when it started |
| a hold placed during a prune was ignored | `put_hold` took no row lock and landed between the validation and the delete. Its receipts were deleted |
| `--through` above the head was bounded by `receipt_chain` | one `UPDATE` to the row §2.1 assumes is rewritten turned a prefix prune into a full-chain delete both readers called clean |
| rule 2's simulation dropped `UnreadableReceipt` and re-derived the head | one unparseable row cost the whole retention feature, refusing every honest prune |
| the checkpoint could assert a `(seq, hash)` pair that never existed | the `seq` came from `--through` rather than from the boundary receipt, and the prune then anchored the invented pair |
| a refused prune and a successful one left byte-identical receipts | and `--older-than` was in neither, so the record of a refusal recorded nothing |

**Two of the seven are the same mistake in two places.** The SQLite lock defect had already been
found on Postgres during the item, and was fixed where it was found. SQLite is the default backend.
A fix applied to the instance and not to the class is §12.2's shape arriving in code.

**The mutation run afterwards produced a finding about the code rather than the tests.** Deleting
rule 2's comparison outright, `if caused:` to `if False:`, survived the entire file. Once the
checkpoint names a pair that exists and the simulation is the store rather than a tidier version of
it, **no store state can reach that branch**: every construction is refused earlier, by the head
bound, the forward-only checkpoint, or the missing-hash refusal. The comparison stays as a backstop,
and `T556c` drives it directly, because a guard nothing can trigger is still a guard somebody will
edit.

**What this says about ordering.** The build order put the required review inside the item and the
merge did not wait for it, so `main` carried all seven for the length of two more items. The rule
this milestone adds: **a required review is a merge gate, not a step in the item**, and an item that
names one is not done when its tests pass.

### 13.5 What is still owed

- **`SPEC-v0.11.md` §9 assigns the frozen-name list to item 2 and item 1 created it.** Item 1 had
  two rows of its own and §9's whole point is that nothing turns red at release that could have
  turned red during the item. The document should say item 1.
- **`pruning()` is not in §9's table.** It is the store method that makes §4.5's lock rule
  implementable, and §4.5 requires the lock without naming a surface for it.
- **`AnchorProvider.make` returns `(token, time)` and §3.2's table says "an opaque token."** §3.3
  caches the time and §10 refuses an anchor whose time runs backwards; a time the provider does
  not supply is one ctrlrun would read from its own clock, which rule 1 forbids. The three cannot
  all hold with a bare token.
- **`StateStore.checkpoint`'s read shipped in item 2, and §9 assigns it to item 3.** §4.6's
  supersession rule is part of what `anchor_broken` means, so an anchor without it would report
  every anchor older than the retention window as tampering, forever.
