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
| 2 | the anchor, and the two break kinds it makes detectable (§3) | `G28` |
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
   revocation, no signing. `SPEC-v0.3.md` §1.1's rule, that CTRLRun consumes identity and issues
   none, applied to time. It is the line between this milestone and the one `ROADMAP.md` keeps off
   the roadmap, and an anchor that minted anything would have crossed it.
2. **A prune leaves the chain verifiable across the gap, or it is refused.** A prune that produces a
   chain reporting `missing` has destroyed evidence and called it retention. There is no `--force`,
   no `--allow-gap`, and no setting that admits a chain nobody can verify.
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
  objects before the walk begins. §5 therefore has a narrower target than §12.5 implies.
- **`effects` was not exercised by the probe** and is not claimed here. The probe's actions carried
  no effect key, so it read nothing and exited 0. `SPEC-v0.7.md` §12.5 lists five surfaces; four are
  measured above and the fifth is item 1's to establish or drop.

### 2.4 What an anchor can and cannot fix

Stated here, once, in the section that makes the claim, rather than in a later section a reader may
not reach.

**An anchor freezes a prefix.** It records that at time T the chain's head was `(seq, hash)`, so
anything at or below that `seq` can no longer be removed or altered without the anchored pair
failing to reproduce.

That sentence is the whole claim, and everything else follows from it by arithmetic:

| Attack | Detected? |
|---|---|
| §2.1's truncation, when the anchored `seq` is above the new head | **yes**: the anchored `seq` is absent |
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
lives in CTRLRun's table, so deleting the newest row there leaves the older anchor reproducing and
the truncation invisible. Three SQL statements instead of two, which is the number §3.3 claimed the
design avoided.

| Call | Answers |
|---|---|
| `make(seq, hash)` | returns an opaque token, or raises |
| `check(seq, hash, token)` | whether that pair and that token correspond |
| **`latest()`** | **the highest `seq` the provider itself holds an anchor for, and its token** |

`latest()` is the one that closes the gap, because it is answered **outside**. CTRLRun's table is
then a cache and not a record: if it names fewer anchors than the provider holds, the provider wins
and the missing one is checked anyway.

**What CTRLRun refuses to accept as one**, and this is the fail-closed half:

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

### 3.3 Where it lives, and the question that decides it

**Outside the store.** This is O2 and it is the section's load-bearing decision, so the argument is
written rather than the conclusion.

An anchor inside the store is an anchor the writer under suspicion can rewrite, which is §2.1
exactly one level up: the head was in the database, and that is why two statements were enough. An
anchor in the same database would make it three.

So the anchor **record** is the operator's, held wherever their provider holds it, and CTRLRun keeps
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

### 3.4 The two break kinds, and the report they are not in

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
| `anchor_broken` | the chain does not reproduce an anchored pair: the anchored `seq` is absent, or the hash at it differs. §2.4's table is what this does and does not cover, and an append is not in it |
| `anchor_missing` | the provider holds anchors and the store's chain reaches a `seq` none of them covers, or a configuration that anchors holds none at all |

This is better than the amendment on three counts, and the third is the one that decides it. `G11`
is genuinely untouched rather than argued to be. `SPEC-v0.6.md` §9.2's closed set stays closed, so
this milestone amends one frozen surface instead of two. And §9's frozen table can name
`ANCHOR_BREAKS` as a symbol a test imports, where "`CHAIN_BREAKS` gains two members" is a membership
claim the frozen-name test's shape cannot express.

**`anchor_missing` is the row the section turns on**, and it is `SPEC-v0.10.md` §4.3's
`upstream_unverified` in a new place: an anchor that is never made would otherwise switch the check
off by being absent. `SPEC-v0.4.md` §3.8's false green is the failure this refuses.

A configuration that does **not** anchor reports neither, and `G28` is `N/A` with a reason true of
the operator's document. Anchoring is opt-in and then fail-closed, which is the rule `SPEC-v0.3.md`
states in its preamble and §1.2's items inherit.

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
`SPEC-v0.1.md` §5 does not carve out an exception for evidence about evidence and an operator
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
a refusal. `cli/main.py` already carries the distinction the draft missed: *"Un-released is not
held."*

**The rule is settlement, not release.** A prune excludes a ledger row whose effect is not in a
terminal state:

| Effect state | Charge | Prunable |
|---|---|---|
| `COMMITTED` | never released, because the spend happened | **yes.** It is history, not a hold |
| `FAILED` | released | **yes** |
| `AMBIGUOUS` | held until a human or a hook resolves it | **no.** `SPEC-v0.9.md` §4's hold, and deleting it would release authority nobody granted |
| `RESERVED`, or a lapsed lease | held, because no transition has occurred | **no.** Still in flight |

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

**The fix is §12.5's second candidate**: a reader that yields per row and reports a row it cannot
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

Every reader then chooses. `receipts` prints the row as unreadable and prints the others.
`verify_chain` reports `content_altered` at that `seq`, which is what it already does for a document
it cannot hash, so one tamper reads as one break whichever half catches it. `inspect` on an
unrelated action never sees it at all, which is the case §2.3 measured.

### 5.3 The negative control

**A store with no bad row reports exactly what it reports today**, byte for byte, on every reader.
This is the half that is easy to lose: a change that made a clean chain read differently would be a
change to shipped output, and item 1 is not a change to shipped output for anybody whose store is
intact.

---

## 6. One chain, five receipt schema versions

`ctrlrun.receipt/v7` is the schema today. A store kept since v0.6 holds **five**: `v3` (0.6), `v4`
(0.7), `v5` (0.8), `v6` (0.9), `v7` (0.10). The count is taken from `receipt.py`'s constants, which
is the only place it cannot be stale.

`ROADMAP.md`'s v0.11 prose said four and was corrected. **Its Exit line still says four**, and that
is the line `G31` is graded against, so item 4 corrects it there in the same edit. The same Exit line
also requires that *the checkpoint receipt of a prune carries the version current when it was
written*, which no guarantee in §8 covers: item 3 asserts it, because a checkpoint written today and
read in two years is the case this milestone exists for.

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
| `G28` | a truncated or appended chain is refused against its anchor | 2 |
| `G29` | a prune across a checkpoint leaves a chain that verifies | 3 |
| `G30` | a held range refuses to prune | 3 |
| `G31` | a chain spanning five receipt schema versions verifies end to end | 4 |

Each with a positive control, each graded or `N/A` with a reason true of the operator's document, and
**each grading the same under `--only` as in a full run**. `SPEC-v0.9.md` §13.8 records G22 passing
for a reason that had nothing to do with G22; the parametrized agreement test covers the shipped ids
and extends to these.

**`G28`'s positive control is the attack in §2.1**, run against a real store: truncate, fix the head,
require the break. A guarantee whose scenario has never seen the attack it exists for is
`SPEC-v0.4.md` §2.2's guarantee that could not have failed.

### 8.1 `G11`'s contract does not change, and §3.4 is what makes that true (O6)

O6 asked whether the two new break kinds are reported as `G11` failures or as a new guarantee. **A
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

**Item 2 asserts this directly**: `G11` passes on a store whose anchor report carries both kinds. A
guarantee whose independence is argued rather than run is what this section already got wrong once.

---

## 9. Public API additions, frozen for v0.11

One justification per row. Anything not here is a spec amendment before it is code.

**`SPEC-v0.10.md` §9.4 is why this section is written differently from its predecessors.** Three of
v0.10's §9 rows were frozen and never built, and nothing turned red because no test asserted that a
frozen name exists. That test now exists. **Every row below names the item that builds it**, and the
release item adds each to the frozen-name list or records in §12 why it was deliberately not built.

| Symbol | Item | Why an existing name does not serve |
|---|---|---|
| `ctrlrun.anchor.AnchorProvider` | 2 | the operator's own code answering from the operator's own system, in the shape `SPEC-v0.9.md` §5.4 settled for a scope provider. Three calls, not two (§3.2), and `latest()` is the one that makes §3.3 true. A default implementation that fetched anything would put a network client in the wheel every user installs |
| `ctrlrun.anchor.ANCHOR_BREAKS` | 2 | its own closed set, because putting the two kinds in `CHAIN_BREAKS` fails `G11`'s control (§3.4). A **symbol**, so the frozen-name test asserts it by import rather than by membership |
| `ctrlrun.anchor.verify_anchors` | 2 | the walk that produces an `AnchorReport`. Separate from `verify_chain`, which is not touched |
| `ctrlrun.anchor.AnchorReport` | 2 | what `verify_anchors` returns |
| `anchor=` on `Control` | 2 | a deployment anchors or it does not; it is a property of the deployment, not of an action, so it does not go on `execute` |
| `ctrlrun.state.StateStore.put_anchor` / `.anchors` | 2 | **amends `SPEC-v0.6.md` §9.2's frozen protocol.** The bar is *a second backend could not be written without it*, and an anchor's local cache cannot be reconstructed from the tables that exist |
| `ctrlrun.state.StateStore.put_checkpoint` / `.checkpoint` | 3 | the same amendment. §4.2 is why a checkpoint the walk trusts cannot live in a receipt document |
| `ctrlrun.state.StateStore.put_hold` / `.holds` / `.release_hold` | 3 | **the first draft froze `ctrlrun hold` with no storage at all**, and `G30` grades a held range refusing to prune against a table that did not exist. A review found it; this is the row that was missing |
| `ctrlrun.migrations` gains `0008_anchor_checkpoint_hold` | 2, 3 | the three tables above. Named rather than written `0008_…`, because a row naming no symbol is what §9.4 is about |
| `ctrlrun anchor`, `ctrlrun prune`, `ctrlrun hold` | 2, 3 | CLI commands. An anchor is made on a schedule by an operator and a prune is an operator's act (§4.2), where every other surface in this kernel is a library call made by an agent. §11 keeps the management plane off the roadmap and these are not one: each writes rows and prints lines |

**Every row above names something a test can import**, which the first draft's did not. A review put
each row into `_FROZEN_V0_10`'s shape and found three that could not be written as a test row at all
(`StateStore gains anchor and checkpoint methods`, `migration 0008_…`, and `CHAIN_BREAKS gains two
members`, the last being a membership claim the test's shape cannot express). That is `SPEC-v0.10.md`
§9.4's failure reproduced inside the section written to prevent it.

**Item 2 adds the v0.11 list to `tests/test_repository_signals.py` and every later item extends it**,
rather than the release item doing it once. §9.4's gap was found at release precisely because nothing
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
| the anchor provider raises, times out, or answers a shape the canonicalizer refuses | `anchor_unavailable`; the anchor is not made and nothing is recorded as anchored |
| a configuration anchors and the store holds no anchor | `anchor_missing`, a break, never a pass |
| an anchored pair does not reproduce | `anchor_broken` |
| an anchor's time runs backwards against the one before it | refused |
| a prune that would leave the chain reporting **any** break: `missing`, `link_broken`, `head_mismatch` or `unchained` | refused, with the `seq` named. The first draft enumerated two, and a review found a prune **through the head** leaves `head_mismatch` and is refused by neither |
| a prune through the chain's head | refused. It leaves no chained receipt for the head to name |
| a checkpoint naming a `seq` at or below the one already recorded | refused (§4.5): this is the second of two racing prunes |
| a prune that would delete a ledger row whose effect is `AMBIGUOUS` or still in flight | refused (§4.4). A `COMMITTED` row is history and is prunable |
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

## 11.1 Round one of the spec review, and what it changed

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
| §9 froze three rows naming no symbol | §9.4's failure inside the section written to prevent it. Every row now names something a test imports, and `ctrlrun hold` gained the storage `G30` grades |

Five citations pointed at the right file and the wrong section, and one quoted a sentence that is not
in the document it named. All corrected; every file-qualified reference now resolves.

**What this says about the process.** Item 0's review is required because a spec is the one artefact
with no test, and every error above would have become code. The two that a reviewer could only find
by *running* something, `G11`'s control and the ledger's release rule, are the two that would have
shipped.

---

## 12. What building v0.11 settled

Written by item 6, in one pass, from the CHANGELOG line each item leaves.

**`SPEC-v0.10.md` §11's rule is in force here from the start:** a sentence in this document whose
truth depends on a later item is a test that item owes, named in its table, or it is not in the
document. Every forward-looking sentence above is either a MUST in §1.1, a row in §9 naming its item,
or a row in §10.
