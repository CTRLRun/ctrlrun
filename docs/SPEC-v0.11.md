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

**An anchor proves the log existed in this form at that time.** It does not prove who wrote it, and
it does not stop an administrator who rewrites everything *before the next anchor*. What it closes
is the window between two anchors: a suffix erased or appended in that window no longer verifies,
because the anchored pair no longer reproduces.

That is a real narrowing and it is not tamper-proofing. `ROADMAP.md`'s "Does not close" paragraph
says authorship, and §11 keeps signed receipts off this milestone for the reason `SPEC-v0.6.md` §11
gives.

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

So the anchor is a **`Callable` the operator supplies**, in the shape `SPEC-v0.9.md` §5.3 settled for
a scope provider: the operator's own code, answering from the operator's own system, and the kernel
matching. It is handed the pair and returns an opaque token; it is handed the pair and the token
later and answers whether they correspond.

**What CTRLRun refuses to accept as one**, and this is the fail-closed half:

- **A provider that raises is `anchor_unavailable` and never a pass.** `SPEC-v0.9.md` §5.3's rule for
  a scope provider, unchanged: a provider that cannot answer has not answered yes.
- **A provider whose answer does not canonicalize is refused**, by `action.py`'s canonicalizer with
  its own domain tag, so an anchor token can never collide with a scope hash or a precondition
  fingerprint over the same mapping.
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
only what it needs to ask the question: the pair, the token, and the time. Those live in a new table
(§9.2), and **the table being rewritable is not a hole**, because rewriting it changes only what
CTRLRun asks the provider about; the provider's answer is what decides, and the provider is outside.

An operator who points the provider at a file in the same directory has an anchor worth what that
file is worth. `SPEC-v0.9.md` §5.5's sentence about a scope provider applies verbatim: it is worth
what its source is worth, and the documentation says so where the feature is described.

### 3.4 The two break kinds

`CHAIN_BREAKS` is a closed set on a `SPEC-v0.6.md` §6.5 surface, and it goes from six to eight. Both
names are frozen in §9 before item 2 starts.

| Kind | When |
|---|---|
| `anchor_broken` | the chain does not reproduce an anchored pair: the anchored `seq` is absent, or the hash at it differs. This is §2.1's truncation and §2.2's append, and any rewrite at or below the anchored `seq` |
| `anchor_missing` | the store holds a chain but no anchor the configuration says should be there |

**`anchor_missing` is the row the section turns on**, and it is `SPEC-v0.10.md` §4.3's
`upstream_unverified` in a new place: an anchor that is never made would otherwise switch the check
off by being absent. A configuration that anchors, and a chain with no anchor, is a break and not a
pass. `SPEC-v0.4.md` §3.8's false green is the failure this refuses.

A configuration that does **not** anchor reports neither, and `G28` is `N/A` with a reason true of
the operator's document. Anchoring is opt-in and then fail-closed, which is `SPEC-v0.3.md` §1.2.

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

**What makes the gap legible is a checkpoint**, and the walk starts from it instead of
`GENESIS_HASH`. Without one, the first surviving receipt's `prev_hash` names a row that is gone and
the chain reports `link_broken` forever after.

### 4.2 The checkpoint is a row, not a receipt field

O4 asked whether a prune is itself an action with a receipt. **It is** and it gets one, because a
prune is a consequential action taken against the operator's own evidence and `SPEC-v0.1.md` §5 does
not carve out an exception for the kernel's own operations. That receipt is ordinary: it is subject
to policy, and an operator may require approval for it exactly as for anything else.

**But the receipt is not what the walk trusts.** A receipt naming itself a checkpoint is a string in
a document, and `SPEC-v0.3.md` §7 already settled the shape of that mistake: *a grant may legally be
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

So a prune on a live ledger **excludes un-released rows**, implemented and not merely restated. A
prune that released a hold by deleting it would be manufacturing authority, which is the hole
`SPEC-v0.9.md` §4 exists to close.

---

## 5. A reader that names a bad row

### 5.1 The narrower target

§2.3 established that the blinding is at **construction**: `store.receipts()` builds every row with
`Receipt.from_json`, and one row that `from_dict` refuses raises out of the generator before any
caller sees a single receipt. `verify_chain` already handles a document it cannot hash.

So the fix is not a new break kind, and `SPEC-v0.7.md` §12.5's first candidate is declined here with
the reason: `content_altered` already covers a document that cannot be canonicalized, and a second
name for the same fact would be two names for one break.

**The fix is §12.5's second candidate**: a reader that yields per row and reports a row it cannot
construct, rather than raising out of the walk.

### 5.2 What a refused row becomes

A row `from_dict` refuses is yielded as a **refusal, not a receipt**, carrying its `seq`, its
`receipt_id` if that field alone is readable, and the type of what refused it. By type and never by
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
(0.7), `v5` (0.8), `v6` (0.9), `v7` (0.10). `ROADMAP.md` said four and was corrected; the count is
taken from `receipt.py`'s constants, which is the only place it cannot be stale.

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

### 8.1 `G11`'s contract does not change (O6)

O6 asked whether the two new break kinds are reported as `G11` failures or as a new guarantee. **A
new guarantee, and `G11` is untouched**, for a reason worth stating because the alternative is the
tempting one.

`G11` is *an altered receipt is detected*, and it has been shipped and graded since v0.6. Routing
`anchor_broken` through it would silently widen a claim an operator has already read and relied on:
a deployment that passes `G11` today would begin to fail it tomorrow for a property it never
configured, and one that never anchors would have `G11` grade something the deployment cannot do.

So the split is by **what the operator configured**, not by what the walk found. `G11` grades the
hash chain, which every deployment has. `G28` grades the anchor, which is opt-in, and is `N/A` with a
reason on a deployment that does not anchor.

**One consequence, stated because it is the part that surprises.** `verify_chain` reports all eight
kinds in one list, so a chain report can carry `anchor_broken` while `G11` passes. That is correct
and not a contradiction: `G11`'s subject is whether the rows hash to what they claim, and an anchor
break says the rows are not the rows that were anchored. Both sentences can be true, and a reader who
sees only one of them has been told less than the report says.

---

## 9. Public API additions, frozen for v0.11

One justification per row. Anything not here is a spec amendment before it is code.

**`SPEC-v0.10.md` §9.4 is why this section is written differently from its predecessors.** Three of
v0.10's §9 rows were frozen and never built, and nothing turned red because no test asserted that a
frozen name exists. That test now exists. **Every row below names the item that builds it**, and the
release item adds each to the frozen-name list or records in §12 why it was deliberately not built.

| Addition | Item | Why an existing name does not serve |
|---|---|---|
| `AnchorProvider`, a `Callable` protocol | 2 | the operator's own code answering from the operator's own system, in the shape `SPEC-v0.9.md` §5.3 settled for a scope provider. A default implementation that fetched anything would put a network client in the wheel every user installs |
| `anchor=` on `Control` | 2 | a deployment anchors or it does not, and it is a property of the deployment rather than of an action, so it does not go on `execute` |
| `CHAIN_BREAKS` gains `anchor_broken` and `anchor_missing` | 2 | amends a closed set on a frozen surface (`SPEC-v0.6.md` §6.5). Recorded here rather than slipped in, as `SPEC-v0.10.md` §9 recorded its amendments to frozen signatures |
| `ctrlrun anchor` | 2 | a CLI command, because an anchor is made on a schedule by an operator and every other surface in this kernel is a library call made by an agent. §11 keeps the management plane off the roadmap and this is not one: it writes one row and prints one line |
| `ctrlrun prune` and `ctrlrun hold` | 3 | the same argument. A prune is an operator's act with a receipt, not an agent's |
| `StateStore` gains anchor and checkpoint methods | 2, 3 | **amends `SPEC-v0.6.md` §9.2's frozen protocol.** The bar is *a second backend could not be written without it*, and it is cleared twice: a checkpoint the walk trusts cannot live in a receipt document (§4.2), and an anchor record cannot be reconstructed from the tables that exist. If an item finds a column would have done, it stops and the spec is wrong |
| migration `0008_…` | 2, 3 | the anchor and checkpoint tables |

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
| a prune that would leave the chain reporting `missing` or `link_broken` | refused, with the `seq` named |
| a prune overlapping a held range | refused, with the hold named |
| a prune that would delete an un-released ledger row | refused |
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

## 12. What building v0.11 settled

Written by item 6, in one pass, from the CHANGELOG line each item leaves.

**`SPEC-v0.10.md` §11's rule is in force here from the start:** a sentence in this document whose
truth depends on a later item is a test that item owes, named in its table, or it is not in the
document. Every forward-looking sentence above is either a MUST in §1.1, a row in §9 naming its item,
or a row in §10.
