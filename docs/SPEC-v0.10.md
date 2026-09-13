# SPEC-v0.10: Multi-agent

**Status:** draft. Written against `main` at `22c9948`, which is 0.9.0 released and tagged.

A delta over `SPEC-v0.1.md` through `SPEC-v0.9.md`, all nine of which remain binding. Where this
document and a build plan disagree, this document wins. Where this document is silent, the nine
before it are not.

v0.9 made an envelope that can be bounded and v0.8 made a yes that can be attributed. v0.10 asks the
one question those two were the prerequisites for: **when one agent hands work to another, what does
the second one hold?**

`ROADMAP.md` moved this item twice, to v0.8 and then to v0.10, and records both moves. The reason
given for the second is the sentence this document is built on: a hop-counting model that propagated
an unbounded grant approved by a string would be A2A on sand. So v0.10 does not count hops. It
propagates the object v0.9 built, under the relation v0.3 built, and spends the ledger v0.9 built.

**Almost nothing here is a new mechanism, and that is the design.** A hop is `v0.3 §5`'s delegation
over a boundary the kernel does not control. The containment check is `contained_dimension`
(`authority.py:889`), unchanged and with a third caller. The identifier is `delegation_id`, which
already exists. The charge is `v0.9 §2.7`'s, which already walks to the root. What v0.10 adds is
small, and §2.3 is the whole of it: **which grant decides.**

---

## 1. Scope

Five deliverables, in build-list order, and a release.

| Item | Section | Guarantee |
|---|---|---|
| 1. The hop, and the envelope that crosses it | §2 | G25 |
| 2. Identity and the receipt across a hop | §3 | G26 |
| 3. Upstream identity pinning | §4 | G27 |
| 4. One ordered list of checks, both modes | §5 | none |
| 5. The operator surfaces for a hop | §6 | none |
| 6. Release 0.10.0, without the tag | §11 | none |

Item 1 lands first because it moves `ctrlrun.guarantees/v6`, which everything downstream reads. Item
3 bumps `ctrlrun.policy/v8`, once, and nothing else may. Item 2 bumps `ctrlrun.receipt/v7`, once, and
§9.1 freezes that whole shape before any item starts. Item 4 touches `control.py` heavily and runs
alone.

**Acceptance tests are `T470` onward and live in the section that owns each item**, one subsection
per item, rather than in a section of their own. `T463` is the highest number in `tests/` at the tag
and `T458` the highest named in `SPEC-v0.9.md`; 464 to 469 are left unused rather than reclaimed, so
a number never means two things. The release item asserts the set is complete.

### 1.1 What this milestone is not, stated before anything else

**A hop does not make a receiving agent safe. It bounds what the receiving agent can do on the
issuer's authority.** Those are different sentences and only the second is true. §2.3 spends a page
on the difference because the first is what a reader assumes, and the assumption survives every
green test in this milestone.

**Nothing here detects a hijack.** An agent that has been talked into handing work to the wrong peer
hands it with a correctly narrowed envelope, and the kernel records a correct hop. `v0.9 §1.1` said
the same thing about task binding and it is no less true here: this is blast radius, not detection.

**CTRLRun does not become an A2A implementation.** It defines no wire format, no agent card, no task
lifecycle and no transport (§3.1). It consumes two strings from whatever envelope the deployment
already carries, and it claims no conformance with anything. `ROADMAP.md`'s "A2A, as code. No
conformance claim" is the whole of the claim.

**Upstream pinning is not provenance.** §4 is the honest slice of `ASI04` and nothing more: it
decides actions, it never inspects a package, a model, a registry or a build. The
`OWASP-AGENTIC-TOP10.md` row stays "out of scope" for the category and gains a sentence for the
slice, and §8 refuses the wider claim by name.

**No cross-store propagation.** §3.2 settles that a hop is a record and not a token, and the cost of
that answer is that both agents decide against the same store. A deployment where they do not is
refused, fail-closed and by name, and it is refused rather than approximated.

### 1.2 The rules of v0.10

Four, stated here as MUST sentences and measured in §7 and §10.

1. **A hop NARROWS or it is refused.** There is no widening at a boundary, no "trust the peer" flag
   and no dimension inherited by omission. `v0.3 §5.4`'s rule about omission is unchanged and has no
   exception here: a dimension the receiving envelope does not name is not inherited, it is refused.
2. **The issuing agent's budget is what a hop spends.** `v0.9 §2.7` already charges every ancestor,
   and a hop MUST NOT create a second root. §2.3 is where this is enforced, and §2.3.1 shows what
   happens without it.
3. **A hop is evidence, not a side channel.** Whatever crosses is on the receipt of both sides, and
   the two receipts name the same hop (§3.4).
4. **Nothing infers who the peer is.** The receiving agent's identity is resolved by `v0.3 §3`'s
   `IdentityProvider` and never read off the payload, exactly as `v0.3 §8.4` settled for the ACS
   hook (§3.3).

### 1.3 The four open questions, answered here

The build plan hands this document O1 to O4 and requires each to be answered in the spec rather than
left to an item, because all four cross a boundary and an item that answered one differently from
another would ship two models.

| | Question | Answer | Section |
|---|---|---|---|
| O1 | Does CTRLRun define a wire format or consume A2A's? | **Neither. What crosses is a reference, two strings, in whatever metadata the transport already has** | §3.1 |
| O2 | Is a hop a record in the store or a claim in a token? | **A record. Rule 2 forces it, and the cost is that both sides decide against one store** | §3.2 |
| O3 | What does depth mean across hops? | **`max_delegation_depth`, unchanged. A hop is a link in the same chain, not a second counter** | §2.5 |
| O4 | Does a receipt record the whole chain or its predecessor? | **The hop it ran under, and nothing derivable from it** | §3.4 |

An item that finds one of these wrong stops and reports rather than working around it.

### 1.4 What was probed, and what it changed

Every design claim below that crosses two modules was run before it was written, on the tree at
`22c9948`. `SPEC-v0.9.md` §13.0 is the reason: across three review rounds on that document every
citation was accurate and roughly three design claims per round were false, and the false half was
always "therefore Y happens at runtime".

Two probes changed this document rather than confirming it.

- **P1 changed §2 entirely.** The first draft made a hop an ordinary delegation and said the
  envelope therefore bounds the receiver. It does not. `Authority.evaluate` passes on *any* matching
  grant and returns `min(passed, key=_by_grant_id)` (`authority.py:1279`), so a receiving agent that
  holds a grant of its own is authorised by that one, the hop is never consulted, and
  `_charges_for` returns `()`. §2.3 exists because of what P1 printed, and rule 2 has no teeth
  without it.
- **P4 changed §4's pin from a key to a certificate.** The public key is the right thing to pin and
  extracting it needs `cryptography`, which this package does not depend on (`pyproject.toml:46`
  declares `pyyaml` and `click`). §4.2 takes the certificate, states the rotation cost, and answers
  it with a list rather than with a dependency.

---

## 2. The hop

### 2.1 The sharp case

A planning agent holds authority to refund up to 100,000 a day. It decomposes a job and hands one
refund to a worker agent. The worker is a separate process, possibly a separate service, reached
over whatever the deployment uses to pass work between agents.

Three things must be true afterwards, and before v0.10 none of them is.

1. The worker may refund **that** payment, for **that** amount, on **that** task, and nothing else.
2. The refund comes out of **the planner's** 100,000. If the worker has a budget of its own and
   spends that instead, the planner's envelope bounded nothing, and ten workers spend ten times what
   anybody granted, which is `v0.9 §2.7`'s argument arriving one level up.
3. Both sides' receipts say the same thing happened, and name it the same way.

### 2.2 A hop is a delegation, and that is not a simplification

A hop is a `v0.3 §5` delegation whose `created_via` is `"hop"`. The record is
`migrations.py:155`'s `delegations` row, unchanged. The relation is `contained_dimension`
(`authority.py:889`), unchanged, over all eight of `DIMENSIONS` (`authority.py:144`). The identifier
is the `delegation_id` the creation mints, unchanged.

**`contained_dimension` gets a third caller, not a fourth relation.** Its callers today are
break-glass (`authority.py:1410`), `plan_delegation` (`authority.py:1478`) and the evaluation-time
chain walk (`authority.py:1688`). A hop is the same call. An implementation that wrote a second
containment check for hops, agreeing with the first today, is refused by this sentence: `v0.9`'s
`_budgets_contained` and `v0.3`'s `_patterns_contained` are one implementation each for exactly this
reason, and a boundary is the last place to keep a copy.

**Why `created_via` and not a new column.** `CreatedVia` is a three-value `Literal`
(`authority.py:124`) that already distinguishes `api`, `cli` and `break-glass`, and it is already
written to `DELEGATION_CREATED`'s `data.created_via`. `"hop"` is a fourth value. A boolean column
beside it would make "is this a hop" answerable two ways, and the two would disagree the first time
one was backfilled. **This is a closed vocabulary and adding to it is a schema statement**, so §9
carries the row.

### 2.3 Which grant decides, which is the whole of rule 2

**An action proposed under a hop is evaluated against that hop's grant alone.**

Not "against the hop's grant as well". Not "against the narrowest matching grant". Against that one,
and if it does not authorise the action the action is refused, with no fallback to anything else the
receiving principal holds.

This is the one genuinely new rule in §2, and without it every other sentence in this milestone is
decoration.

#### 2.3.1 What the code does today, measured

`Authority.evaluate` collects every grant that matches shape and, where more than one passes,
returns `min(passed, key=_by_grant_id)` (`authority.py:1276-1279`), on `v0.3 §4.6`'s rule that holding two
permissions is never worse than holding one and that the grant named is a property of the set rather
than of the document. That rule is correct for a principal's own authority and it is exactly wrong
for a hop.

**P1, run against the tree at `22c9948`.** One document, two grants: `mmm-issuer` for the planner,
carrying a budget of 10,000 a day and `delegable: true`, and `aaa-receiver-own` for the worker,
carrying no budget. The planner delegates a correctly narrowed envelope to the worker: one resource,
100 a day. The worker then proposes a 5,000 refund on a resource the hop does not name, and the
evaluation is asked what it decided.

```
delegation created: dlg_41ad765577… depth 1
passed:             True
grant_id named:     aaa-receiver-own
delegation_id:      None
charges:            []
```

The hop was never consulted. The action was authorised by the worker's own grant, the resource
restriction the planner attached did not apply, and `charges` is empty, so the planner's 10,000
budget paid nothing. **Rename the worker's grant so it sorts after `dlg_` and the same document
decides the other way**, which is the second finding: with the grants otherwise identical the
outcome turns on the codepoint order of two identifiers.

`_by_grant_id`'s docstring says ties break on codepoint order so that reordering the file changes
neither the decision nor the id. That is true and it is a statement about a *set of the principal's
own grants*. It was never a statement about a set that contains somebody else's envelope.

#### 2.3.2 The rule, and what it does not promise

`Authority.evaluate` takes a `hop: str | None = None`. Where it is `None`, everything is exactly as
0.9.0, which is why every existing deployment upgrades untouched. Where it is not:

1. The candidate set is **exactly** the delegation that id names, plus its chain. No other grant of
   the principal is a candidate, passing or failing.
2. That delegation MUST exist, MUST be live, and its grant's `subject` MUST match the action's
   principal by `v0.3 §4.2`'s matching. Otherwise `authority_hop`, with `data.hop` naming what was
   presented and nothing else in it (§3.3 says why the id is safe to echo and the rest is not).
3. Every other refusal keeps the reason it already has. A revoked chain is `authority_revoked`, an
   expired ancestor is `authority_escalation` with `expired_parent_id`, a step that no longer
   contains is `authority_escalation` with `dimension`. **`authority_hop` is not a bucket for them**,
   and §7's G25 grades the containment refusal specifically so that a hop refused for the wrong
   reason cannot pass for a hop refused for the right one.
4. `_charges_for` is unchanged and therefore charges that delegation and every ancestor to the root
   (`authority.py:1285`), which is rule 2 satisfied by machinery that already exists.

**What it does not promise, and this is the residual of the whole milestone.** CTRLRun cannot make a
receiving agent present the hop it was given. An agent that holds a grant of its own can simply not
present the hop and act on its own authority. What the kernel guarantees is the disjunction, and the
disjunction is worth having:

- it presents the hop, and then the envelope bounds it and the issuer's budget pays; or
- it does not, and then it is acting on its own authority, against its own budget, and **its receipt
  carries no `hop`**, which is a fact an operator can query and a reconciliation can assert.

**The deployment rule that follows**, stated here because it is the operator's half of the
guarantee and no code enforces it: *an agent that only ever acts on handed-over work holds no root
grant of its own*. Then the hop is the only authority it has and the disjunction collapses to its
first branch. `examples/` ships one configured that way (§11), and §6's surfaces answer "which
principals hold a root grant" so an operator can check.

This is the same shape as `v0.3 §3.1`'s limit about a caller who builds an `Action` by hand and the
same shape as `THREAT_MODEL.md`'s "bypassing the decorator entirely": code inside the trust boundary
can decline to use the mechanism. It is stated rather than papered over because the paper version,
"a hop bounds the receiving agent", is what a reader will otherwise carry away.

### 2.4 Narrowing, checked at the hop and again at every evaluation

`v0.3 §5.4` dimension by dimension, `v0.3 §5.5` for patterns, `v0.9 §2.6` for the budget's two axes
including the window rule that reads backwards. Nothing is added and nothing is excepted. A hop that
widens on any of the eight is refused at creation with `AuthorityEscalation(reason="containment")`
and `data.dimension` naming the row, and the same eight are re-checked on every evaluation by the
chain walk.

**Re-checking is what makes a hop revocable**, and it is why §3.2's answer to O2 is forced: an
envelope that was checked only at the boundary would be exactly as wide as the issuer's authority was
at the moment of handover, for as long as it lived.

#### 2.4.1 Transitivity, which a hop relies on and a delegation does not

`v0.3 §5.6` rule 4 walks **every** parent to child step, so nothing in v0.3 needed the relation to be
transitive. A reader of a hop does need it: the sentence "the second agent holds a subset of what the
first agent held" is a claim about the composition of two links.

**Probed, not proved.** P2 builds two pools of 314 well-formed grants each, varying all eight
dimensions over a small vocabulary (action and resource patterns including `**`, constraint
operands, environment sets, expiries, tasks, and budgets varying both limit and window, with
concrete subjects in the first pool and wildcard ones in the second), takes every ordered pair where
`contained_dimension` answers `None`, and checks every `A ⊇ B ⊇ C` triple those pairs compose.
**1,954 contained pairs, 1,624 triples, no violation.** Per dimension the argument is short:
pattern containment composes, subset composes, "at least as strict" composes per operator, an
absent parent constraint constrains nothing at either step, and `v0.9 §2.6`'s budget proof is
already stated over nested intervals.

It is recorded as a probe rather than a proof because it is one, and because a future pattern
grammar extension is exactly where it would stop holding. `v0.3 §5.4`'s closing rule applies
unchanged: where containment cannot be decided, it fails.

### 2.5 Depth across hops, which is O3

**`max_delegation_depth`, unchanged, counting hops and ordinary delegations alike.** A hop is a link
in the same chain, so a root grant with `max_delegation_depth: 3` permits at most three links
whatever the mix, and depth is derived by walking to the root rather than read from the stored column
(`v0.3 §5.5`), so a hop cannot assert its way to a shorter chain.

**Rejected: a separate hop counter.** Two bounds over one chain means an operator must reason about
which binds, and the answer would be "whichever is smaller", which is one bound with extra
configuration. It also breaks `v0.9 §2.7`'s cost statement, which says a consumption writes or checks
one row per ancestor and points at `max_delegation_depth` as the thing that bounds it. One chain, one
bound, one cost.

**What that costs, stated plainly.** The default is 3, so a deployment with a two-hop pipeline has
one link left for ordinary delegation beneath it, and a three-hop pipeline has none. An operator who
wants a longer pipeline raises the number in the document, which is a reviewed file, and raises the
per-consumption cost with it. That is the trade and it is theirs to make.

### 2.6 Two hops hold no more than the root granted

The claim §2.4.1 exists to support, measured rather than asserted. P5 builds a root permitting
`amount_lte: 10000`, hops it to a worker, hops that to a second worker, and evaluates a 5,000 refund
by the second worker. It then narrows the root to `amount_lte: 100` in the document and evaluates the
identical action against the identical store.

```
hop1 depth=1  hop2 depth=2
under the WIDE root:    passed=True   grant=dlg_c7750e6b…
after the root NARROWS: passed=False  reason=authority_escalation  dimension=constraints
```

A narrowed root narrows everything two hops beneath it, on the next evaluation, with no writes and
without finding the children. That is `v0.3 §5.6`'s stated purpose holding across a boundary, and it
is the property §3.2 refuses to trade away.

### 2.7 Acceptance tests for item 1

| | Test |
|---|---|
| T470 | A hop that narrows on every dimension is created and admitted, and the action it authorises executes. The negative control for every row below |
| T471 | A hop that widens is refused at creation, by reason `containment` and `data.dimension`, parametrized over all eight of `DIMENSIONS` so each row breaks alone, as `v0.3`'s T76 does |
| T472 | **P1's document as a test.** The receiving principal holds a grant of its own that would authorise a wider action. Presented with the hop, the evaluation names the hop, refuses the wider action, and charges the issuer. Parametrized over both codepoint orders of the two grant ids, so §2.3.1's second finding cannot return |
| T473 | An action proposed with no hop is decided exactly as 0.9.0 decided it, over a document carrying hops. The upgrade-untouched assertion |
| T474 | A hop id that names nothing, a live hop addressed to a different principal, and a hop whose chain is revoked, each refused with its own reason and never with each other's |
| T475 | Two hops, then the root narrowed: §2.6's probe, as a test, against Postgres |
| T476 | The issuer's budget is charged for a consumption under a two-hop chain, one row per ancestor, under the `v0.6` multi-process standard against Postgres with a `multiprocessing.Barrier`. Threads against SQLite are not evidence here |
| T477 | `max_delegation_depth` counts hops and delegations in one chain: a third link is refused with `max_depth` whatever the mix of the first two |

**Mutate every guard.** `SPEC-v0.9.md` §13.0's second-order lesson applies in full: items 2, 4 and 5
of that milestone each shipped a guard nothing exercised, found only by mutating the source and
watching the suite stay green. The candidate mutations here are the `hop is None` branch in
`evaluate`, the subject match in §2.3.2 rule 2, and the no-fallback rule itself, which is the one a
patch would most plausibly soften.

---

## 3. What carries a hop

### 3.1 The wire, which is O1

**CTRLRun defines no wire format for authority, and consumes none.**

What crosses a hop is a **reference**: two strings, `hop` and `task`, carried in whatever field the
transport already has for caller-supplied metadata. For A2A that is the message's `metadata` object.
For MCP it is `params.metadata`, which `ctrlrun.acs` already reads. For an in-process call it is two
keyword arguments. **The envelope itself never crosses.**

Three reasons, and the third is the load-bearing one.

1. **An envelope on the wire is an assertion; a reference is a lookup.** A receiver handed a
   serialized grant has to decide whether to believe it, which means signatures, which means keys,
   which means issuing. `v0.3 §1.1` is unchanged: CTRLRun consumes identity and issues none.
2. **A format is a compatibility surface.** A2A is moving, and a kernel that defined `ctrlrun.hop/v1`
   as a wire object would own a translation layer for every transport a deployment uses. Two strings
   in a metadata bag need no translation and no version.
3. **`v0.9 §2.7` cannot be satisfied by anything that crosses.** The issuer's budget is charged by
   walking to the root in the store. Whatever a receiver holds in its hands, the thing that decides
   is the record. So the record is what a hop is (§3.2), and the wire only has to say which one.

#### 3.1.1 Why a reference off the payload is safe when an agent id is not

This looks inconsistent with rule 4, and a reviewer should press on it, so the argument is written
out rather than left implicit.

`v0.3 §8.4` removed `params.metadata.agent_id` as an authorization input because a self-reported
principal is a principal the caller chose. A hop id read off the same payload is different in kind
for one reason: **it is not an input to the decision, it is the selection of which record the
decision is made against**, and the record was written by the issuer, is addressed to a subject, and
is re-checked in full.

The three ways a lying caller could try to use it, and what stops each:

| Attack | What stops it |
|---|---|
| Present a hop issued to a different agent | `Grant.matches_shape` matches the grant's `subject` against the action's principal (`authority.py:536`), and the principal is the `IdentityProvider`'s, never the payload's (§3.3). A hop addressed to somebody else matches nothing |
| Present a forged or guessed id | `new_delegation_id` is `dlg_` plus 16 bytes from `secrets.token_hex` (`authority.py:660`), and an id naming no live record is `authority_hop`. There is nothing to forge: the id is a lookup key into rows only the issuer wrote |
| Present a stale hop after the issuer narrowed or revoked | §2.4's re-check and `v0.3 §5.6`'s chain walk, both unchanged. §2.6 measures it |

**The one attack that is not stopped, and it is real.** A receiver that legitimately holds two hops
from the same issuer may present the wider one for work handed over under the narrower. Both are its
own, so subject matching passes and the chain is live. The envelope still bounds it by the union of
what it was actually given, which is strictly less than the issuer's own authority, but it is not
the specific envelope intended for this work.

What narrows it is the task, and the task arrives from the caller too, so this is `v0.9 §6.3.1`'s
residual reappearing one level up: the action hash is silent about the task, so nothing binds an
envelope to the work it was issued for except the grant's own `tasks` pattern. An issuer that wants
the tighter property issues a hop whose `tasks` names one concrete run rather than a pattern, which
costs one delegation record per run. §6 makes that visible and §8 records the residual.

### 3.2 A record, not a token, which is O2

**A hop is a row in the `delegations` table the issuer already writes to.** Not a claim in a token,
not a signed envelope, not a capability the receiver carries.

**Rule 2 forces this, and the forcing is worth following.** The issuing agent's budget is what a hop
spends. `v0.9 §2.7` spends it by walking the chain to the root and writing or checking one ledger row
per ancestor, in the transaction that reserves the effect (`v0.9 §3.3`). A token model puts the
receiver's ledger in a different store, so either the issuer's budget is not charged, which is rule 2
abandoned, or it is charged asynchronously, which is a budget that refuses after the money moved.
`v0.9 §12` already refuses a fleet-wide budget across stores, for `v0.7 §4.6`'s reason: the kernel's
consistency claim stops at its store's transaction.

**What that costs, stated plainly, because it is the largest limit in this milestone.** Both agents
must decide against the same store. A deployment whose agents run against separate stores cannot hop
between them in 0.10.0.

**And it fails closed rather than approximately.** P5(b) puts the child record in a store that never
saw its parent and evaluates:

```
parent record absent: passed=False  reason=authority_escalation  missing_parent_id=dlg_5c44df61…
```

That is `v0.3 §5.6` rule 1, which exists already and needs nothing added. A receiver whose store
cannot read the chain refuses the action and names the record it could not find, which is the
diagnosis and not merely the refusal. §6 turns that id into a command.

**Rejected, with reasons, because these are the obvious alternatives.**

- **A signed capability token.** Key generation, rotation and revocation, which is issuing, which
  `v0.3 §1.1` refuses and `SPEC-v0.6.md` §11 refuses again for receipts. It would also make
  revocation a freshness window rather than a write, and `v0.3 §5.7`'s "a chain of any depth is cut
  by one write" is the property an incident actually uses.
- **A token plus a revocation feed.** v0.8 ships a revocation feed and it would carry this. It does
  not carry the budget, and rule 2 is about the budget.
- **Replicating the issuer's delegation rows to the receiver's store.** A second copy of the
  authority record, with a staleness window in which a narrowed root has not narrowed. §2.6 is the
  property that trade gives up.

**What a later milestone would need in order to lift it**, recorded so that this section is a
decision and not a dead end: a ledger both sides can charge in one transaction, or an issuer-side
reservation the receiver calls before it acts. The first is a distributed transaction and the second
is a network call on the decision path. Neither is v0.10's, and §8 lists them.

### 3.3 Who the receiver is, which is rule 4

**The receiving agent's identity is resolved by `v0.3 §3`'s `IdentityProvider`, from the transport,
and never read off the payload.** `v0.3 §8.4`'s three sentences apply here word for word: where a
provider is configured, a self-reported name is ignored, not merged, not used as a fallback and not
compared. It is display data.

**A hop with no resolvable receiver is refused before an action exists**, which is `v0.1 §2.1`
refusing an unattributable action, not a new rule. At the gateway that is `-41007`
`ctrlrun.no_principal` with no receipt and no events, exactly as today.

**Where the resolution happens is `v0.3 §3.1`'s list and it does not grow.** Three places build an
`Action` and therefore three resolve: `@protect`'s wrapper, the gateway, and `ctrlrun.acs`'s request
hook. A caller that builds an `Action` by hand and passes it to `Control.execute` supplies its own
`Principal` and the provider never runs, which `v0.3 §3.1` already records as the same limit as
calling the wrapped function directly. **v0.10 adds no re-resolution and no principal-disagreement
check**, for the reason given there: it would be a check against the wrong threat.

**`data.hop` on a refusal carries the id and nothing else.** An `authority_hop` refusal echoes the
presented id, which is a value the caller already holds, and never the subject, the grant, or what
the hop would have permitted. A refusal that described the envelope would be an oracle for probing
one, and `v0.9 §5.5`'s rule about a scope hash reaching a receipt while the scope never does is the
same rule.

### 3.4 The receipt on both sides, which is rule 3 and O4

**One field on the receipt: `hop`, carrying the `delegation_id`.** Both sides write it, and it is the
same string on both, which is the whole of rule 3.

**No second identifier.** The build plan says to grep before inventing one and the grep answers:
`delegation_id` already names a delegation uniquely, is already minted by `new_delegation_id`, is
already in `DELEGATION_CREATED`, `DELEGATION_REVOKED` and `DELEGATION_REJECTED`, is already what
`ctrlrun revoke` takes, and is already on `AuthorityResult`. A `hop_id` beside it would be a second
name for one row.

**The two sides, and what each writes.**

| Side | When | `hop` holds |
|---|---|---|
| Issuer | the action during which it created the hop | the id of the hop it created |
| Receiver | every action it proposes under that hop | the id of the hop it presented |

**The issuer's half has a condition and it is stated rather than assumed.** A receipt exists for an
action. An issuer that creates a hop outside any action, from the CLI or from a script, writes
`DELEGATION_CREATED` and no receipt, because there is no action to carry one. So the issuer's `hop`
field is written when the hop is created **inside** an action's execution, which is the case rule 3
is about: an agent doing work hands part of it on. Created outside one, the evidence is the event,
and §6's surface reads the event.

#### 3.4.1 The predecessor, not the chain

**The receipt records the hop it ran under and nothing derivable from it.** Not the ancestors, not
the root, not the depth.

Three reasons.

1. **The chain is derivable from the id**, by the walk that already decides the action. A receipt
   that also carried it would be a second copy of state, and two copies is how two sources of truth
   begin disagreeing.
2. **The chain is already on the receipt exactly when money moved.** `v0.9`'s `budget_charges` names
   every grant charged, and `v0.9 §2.7` charges every ancestor, so a receipt for an action under a
   budgeted chain already carries the whole chain, in the field whose job is to say what was spent.
   Adding a second rendering of the same fact would let the two disagree on a receipt that is
   evidence about money.
3. **A receipt is on the hot path.** A chain is bounded by `max_delegation_depth`, so the cost is
   bounded, but the default is 3 and an operator may raise it; a field that grows with a
   configuration value is a field that gets truncated eventually.

**What that costs.** A receipt read after its delegation rows are gone names a hop that no longer
resolves. Delegation rows are not deleted by anything the kernel ships, and `v0.11`'s retention item
is where pruning is designed, so this is a limit on a future feature rather than a live one. It is
recorded here so that the retention design knows a receipt points at a `delegations` row and that
pruning one orphans the other. §8 carries it forward.

#### 3.4.2 The resumed leg, which `v0.9 §6.3.2` handed to this milestone by name

`v0.9 §6.3.2` decided that `Control.resume` does not evaluate the task dimension at all, because the
action is rehydrated from the store and carries no task, so a task-bound grant would deny every
resumed leg. It recorded the cost, *a resumed leg is unbound by task*, and it named the fix and the
milestone that would want it:

> Recovering the first leg's task would mean stamping it onto `EXECUTION_STARTED` so
> `_resumed_context` could read it back, which is a receipt-and-event change v0.9 does not make and
> v0.10 will want anyway, since a task crossing a hop is exactly its subject.

**v0.10 makes that change, and it is not optional here.** A hop is not a containment dimension, it
is the selection of which grant decides (§2.3). A resumed leg that did not know its hop would fall
back to deciding against any matching grant, which is §2.3.1's hole reappearing on every
continuation, and a continuation is exactly where an MCP multi round-trip lives.

**What lands.** `EXECUTION_STARTED` carries `data.hop` and `data.task`, which is an empty mapping
today at `control.py:1487` and `control.py:1561`. `_resumed_context` (`control.py:1916`) reads both
back where it already reads that event (`control.py:1943`), and the resumed leg is evaluated on
**both** dimensions rather than skipping either.

**This retires `v0.9 §6.3.2`'s third mode rather than extending it.** That section named "not
evaluated, on one dimension, on one path" as a third mode beside `v0.3 §5.6.1`'s two, and said it
got its own sentence because an implementer reaching for `v0.3 §5.6.1` gets the wrong one. With the
task recoverable from the event, the mode has nothing left to cover **on a leg this build
suspended**. `evaluate_task=False` stays in the signature, and `Control.resume` keeps using it in
exactly two places: a lease extension, which genuinely has no task, and **a leg suspended by 0.9.0**.
**The item that lands this updates `SPEC-v0.9.md` §6.3.2's table row in the same PR**, because a
spec that still says the dimension is never evaluated there would be describing code that evaluates
it.

**The upgrade case is the one to get right, and an earlier draft of this section got it wrong by
saying `resume` stops using `evaluate_task=False` outright.** An action suspended by 0.9.0 and
resumed by this build has an `EXECUTION_STARTED` whose `data` is `{}`, because 0.9.0 wrote it that
way. Evaluating the task dimension against a value that is absent would hit `v0.9 §6.4`, a grant
naming a task refuses an action naming none, and **every in-flight action across the upgrade would
be denied on the only receipt an MCP multi round-trip ever gets**. So:

| What `EXECUTION_STARTED` carries | Resumed leg |
|---|---|
| both values, written by this build | evaluated on **both** dimensions, and the hop selects the grant |
| neither, written by 0.9.0 | evaluated as 0.9.0 evaluated it: `evaluate_task=False`, and no hop selection |

**A missing value is absence, not a refusal**, and the distinction is load-bearing exactly once, at
the upgrade. It is not a widening the other way round: a leg this build suspended always carries
both, so the absent case cannot be manufactured by a caller, only by having been suspended before
the upgrade. T487 is the test, and §10 carries the row.

**And the ambient-context hazard stays closed, which is the reason to read the value from the event
and not from a context variable.** `v0.9 §6.3.2`'s closing paragraph works out that a task read from
a context variable at the resume path would let a process sitting inside some *other* `task=`
evaluate the resumed leg against an unrelated task. The event is durable, is bound to this
`action_id`, and was written by the leg that actually held the authority. **Nothing on this path
reads either value from an ambient context**, and a test asserts it by resuming inside an unrelated
`task=` and `hop=` and requiring the event's values to win.

#### 3.4.3 A relay agent is both sides at once, and the field is single-valued

The table above has two rows and a chain of two hops has a **middle**: an agent that presents one
hop and creates another during the same action. §2.6's own worked example contains one, so this is
the ordinary case rather than an edge.

`Receipt.hop` is one string, so the precedence is stated rather than left to an implementation.

**The receipt's `hop` names the hop the action ran UNDER, never the hop it created.** A receipt is
evidence about a decision, and the decision was made against the presented hop (§2.3). The created
hop authorised nothing on this action; it authorises somebody else's later one, and it is that
action's receipt that will name it.

**So the issuer row of the table above is the case where those two coincide**, and it is worth
saying which way round: an agent acting under no hop and creating one writes the created id, because
there is no presented one to displace it. An agent acting under a hop writes the presented one
whether or not it also created something.

**What finds the created hop instead**: `DELEGATION_CREATED` with `data.created_via = "hop"` and
`data.delegation_id`, which `v0.3 §7` already appends and which §6.2's surface already reads. The
evidence exists; it is an event rather than a receipt field, on §3.4.1's rule that a receipt carries
what decided this action and nothing derivable elsewhere.

**Rejected: two fields**, `hop_in` and `hop_out`. It would put a value on the hot path that is
`null` on every receipt except a relay's, and it would make "which hop" answerable two ways on the
one shape where an implementation is most likely to fill the wrong one. The two-hop chain in §2.6
is then reconstructed by the walk, which is what `chain[]` renders (§6.2).

### 3.5 `ctrlrun.receipt/v7`

Bumped **once**, by item 2, and the whole shape is frozen in §9.1 before any item starts, exactly as
`v0.9 §10.1` froze v6's. One field, `hop`. The rule since `SPEC-v0.3.md` §12.2 is unchanged: every
reader upgrades before any writer switches, so an older receipt on disk still parses, and a receipt
read from a store is hashed as the document it was read from.

### 3.6 Acceptance tests for item 2

| | Test |
|---|---|
| T478 | The issuer's receipt and the receiver's receipt carry the same `hop` string, for a hop created inside an action. Rule 3's positive control |
| T479 | A hop created outside any action writes `DELEGATION_CREATED` with `data.created_via = "hop"` and no receipt, and §6's surface finds it from the event |
| T480 | A hop presented with `params.metadata.agent_id` naming a different agent is decided on the `IdentityProvider`'s principal, and the payload's name reaches nothing but display. `v0.3`'s T91d at the hop |
| T481 | A hop addressed to another principal, presented by this one, refuses. The subject row of §3.1.1's table |
| T482 | A receiver whose store cannot read the chain refuses with `authority_escalation` and `missing_parent_id` naming the unreachable record. §3.2's probe as a test |
| T483 | `authority_hop`'s `data` carries the presented id and no other key, asserted by exact key set so a later field cannot be added without a test going red |
| T484 | A `ctrlrun.receipt/v6` receipt and a `v7` receipt in one chain both parse and the chain verifies across the boundary |
| T485 | A suspended action resumed under a task-bound, hop-selected grant is evaluated on both dimensions and is **not** denied, with `EXECUTION_STARTED` carrying both values. `v0.9 §6.3.2`'s cost, paid |
| T486 | The resume runs inside an unrelated `task=` and `hop=`: the event's values decide and the ambient ones reach nothing. `v0.9 §6.3.2`'s ambient-context hazard, still closed |
| T487 | A leg suspended by **0.9.0** and resumed by this build: `EXECUTION_STARTED` carries neither value, the leg is evaluated as 0.9.0 evaluated it, and a task-bound grant does **not** deny it. The upgrade case (§3.4.2, §10) |
| T488 | A relay agent presents one hop and creates another in the same action: its receipt's `hop` names the one it **acted under**, and the created hop is found from `DELEGATION_CREATED`. §3.4.3's precedence |

---

## 4. Upstream identity pinning

### 4.1 The sharp case, and what it is a slice of

A policy authorises `stripe.refund`. The gateway fronts an MCP server at a name the operator wrote
down. Someone swaps what answers at that name, or leaves the server in place and moves the schema of
the tool sitting under the approved action name so that `amount` now means something else. Every
grant still matches, every constraint still holds, the receipt still says `stripe.refund`, and the
action is authorised against a server nobody reviewed.

**This is the honest slice of `ASI04` and it is smaller than the category.** CTRLRun still decides
actions. It never inspects a package, a model, a registry, a build or a signature chain, and
`OWASP-AGENTIC-TOP10.md`'s `ASI04` row keeps its "out of scope" verdict for supply chain at large,
gaining one sentence for what this does cover. §8 refuses the wider claim by name, and the release
item is told not to overclaim the row.

### 4.2 What is pinned

`upstream:` is a new action-entry key, beside `mcp:`, and carries at most two pins.

| Key | Compared against | Shape |
|---|---|---|
| `tls_cert_sha256` | the SHA-256 of the upstream's **leaf certificate**, DER form | a **list** of `sha256:…` strings |
| `tool_schema_sha256` | `"sha256:" + hex(SHA-256(canonical_bytes(<the tool's entry in tools/list>)))` | one `sha256:…` string |

**The certificate, not the public key, and the trade is real.** The key is the right thing to pin,
because a renewal keeps it and a certificate pin fires on every rotation. P4 measured what it costs:
the leaf certificate is reachable in the standard library (`getpeercert(binary_form=True)` on
`ctrlrun.transport.HTTPSConnection`, 781 bytes on the probe's self-signed cert), and extracting the
`SubjectPublicKeyInfo` from it needs `cryptography`, which `pyproject.toml:46` does not declare and
which would land in the wheel of every user who never fronts anything. **A runtime dependency for
one optional key is the wrong trade**, and the dependency rule of `v0.2 §1.1`, `v0.3 §1`,
`v0.4 §1` and `v0.5 §1`, that `pip install ctrlrun` installs nothing but `pyyaml` and `click`, is
not bent for a convenience.

**What answers the rotation cost instead: the key is a list.** An operator adds the next certificate
before the rotation and removes the old one after, and neither the swap nor the rotation needs a
restart in the middle. A single-valued pin would have made every renewal an outage, which is how a
pin gets switched off permanently.

**The tool schema hash reuses `canonical_bytes`**, which `v0.9 §5.5` already uses for the scope hash
(`control.py:648`) and which refuses a float at any depth and sorts keys. A second canonicalizer for
JSON-RPC payloads would be a second place for `True` to start comparing equal to `1`. The hash is
over the **whole** advertised entry, name, description and input schema together, because a
description that changed is a tool whose behaviour an operator has not reviewed.

### 4.3 Where it is checked, which is three places for one rule

**Only the gateway can pin, and that is a limit with a reason.** Pinning requires holding the
connection to the upstream. The gateway does (`GatewayConfig.upstream`, one upstream per gateway,
which `v0.2 §12` puts out of scope and this document does not reopen). Two other surfaces do not, and §4.4 says so.

| | When | What it does | What it is |
|---|---|---|---|
| 1 | gateway startup | one connection and one `tools/list`; a mismatch **refuses to start**, printing the observed hash beside the pinned one | the legible one, and the only one where the operator is present |
| 2 | decision time | `Control` compares the pin against what **this process last observed** for that upstream: `upstream_mismatch` where it differs, `upstream_unverified` where nothing has been observed | **the `DENY`**, and the one that is stale by design |
| 3 | the outbound handshake | the pinned certificates are the connection's **only trust anchors**, so a swapped server fails the handshake before the first request byte | the enforcing one |

**Check 2 attributes; check 3 prevents.** That distinction is `v0.3 §5.6` rule 3's, written the same
way and for the same reason: a decision made against an observation is a decision about the past.
The gateway forwards *after* deciding, so check 2 is answering with the previous connection's
observation, and an upstream swapped in the window between them is caught by check 3 and not by
check 2. **Stating it this way round is the point.** An implementer who built only check 2 would
have a DENY that a well-timed swap walks past, and would have tests that pass.

**Check 3 turns a swap into `NotExecuted`, not into a DENY**, because a handshake that fails has
provably handed over no request byte, which is exactly `SPEC-v0.7.md` §2.3's claim and needs nothing
new to be true. The effect did not happen, the kernel records it `FAILED`, and the action fails
rather than being denied. `ROADMAP.md`'s v0.10 entry says a swapped server "is a `DENY`"; it is a
DENY at check 2 and a failed action at check 3, and **the release item reconciles that line rather
than leaving the roadmap saying something the code does not do.**

**The residual, which is `v0.7 §6.7`'s and is inherited rather than closed.** Between check 2's
observation and check 3's handshake the world can move. v0.7 stated that window for a precondition
and did not close it; §4 does not close it either, and closing it would mean deciding inside the
connection, which is a decision taken after the executor has begun.

### 4.4 The two surfaces that cannot pin, and why the in-process one is refused rather than missing

**The ACS hook cannot.** `acs.py`'s own docstring settles it: ACS is advisory, the *platform* runs
the tool, and `AcsControlHook` never holds a connection to anything. There is no observation point,
so there is nothing to pin. A pin on an action reaching the kernel through the ACS hook is a **load
error**, naming the surface, rather than a key that silently does nothing.

**In-process `@protect` is refused, and P4 is why the refusal is a decision rather than a gap.** The
probe stood up a TLS server and read the leaf certificate off `ctrlrun.transport.HTTPSConnection`
after `connect()`, so the observation point demonstrably exists. It is refused anyway:

- it works **only** for executors that route through `ctrlrun.transport` or
  `ctrlrun.gateway.transport.request`. `transport.py`'s module docstring already states that limit
  for the `NotExecuted` claim, and an executor using `requests`, httpx directly or a raw socket gets
  no check at all;
- so the guarantee would be conditional on the operator's own executor code, which is a guarantee
  `verify` cannot grade and an operator cannot check. `SPEC-v0.4.md` §3.9 refuses exactly this shape
  of claim;
- and it would be **silently** conditional, which is the fail-open direction: a pin configured, no
  check performed, nothing red.

So a pin on an action that reaches the kernel in-process is a load error too, on the same rule and
with the same message shape. §8 records what lifting it would take.

### 4.5 The refusals

| Reason | When |
|---|---|
| `upstream_mismatch` | an observed certificate hash is in no pinned list, or an observed tool schema hash differs from the pin |
| `upstream_unverified` | the entry pins an upstream and nothing in this process has observed one for it |

Both are `ActionDenied` with their own reason, under the standing rule that `errors.py`'s closed set
already covers every refusal here, which `v0.9` kept for a whole milestone without an exception. At the gateway both return a new JSON-RPC code,
**`-41013` `ctrlrun.upstream_unpinned`**, HTTP 403, with `reason` and `action_id` in `data` as
`-41012` carries them, and **the upstream is never called**. A distinct code earns its keep on
`v0.3 §8.4`'s test: `-41001` means this action is not permitted to anyone, `-41012` means not to
you, and `-41013` means not against **that server**, which a client answers differently from either.

**`upstream_unverified` is the fail-closed half and it is the one to get right.** A pin that does
nothing when nothing was observed is a pin that an upstream can switch off by never being observed.

### 4.6 `ctrlrun.policy/v8`

Bumped **once**, here, by item 3. The key it adds is `upstream:` on an action entry.

**An older reader refuses the document rather than ignoring the key**, for the reason `v0.9 §10.1`
gives about `tasks:`: an older reader that ignored `upstream:` would authorise the action against
any server at all, which is the whole of what the key restricts. The refusal takes the shape
`policy.py` already uses for v3, v4, v5 and v7 keys (`require_v7`, `policy.py:1176`), and the
consequence sentence is written in the same voice.

### 4.7 Acceptance tests for item 3

| | Test |
|---|---|
| T489 | A pinned certificate that matches admits the action; the negative control |
| T490 | A swapped server behind the same name is refused at check 2 with `upstream_mismatch` and `-41013`, and the upstream is never called, asserted by a listener that records connections |
| T491 | The same swap at check 3: the handshake fails, `NotExecuted` is raised before any request byte, and the effect is recorded `FAILED` and not `AMBIGUOUS` |
| T492 | A tool whose advertised schema moved under an approved action name is refused; the identical schema is admitted. Both hashes computed through `canonical_bytes` |
| T493 | An entry pinning an upstream that nothing has observed is refused `upstream_unverified`, not admitted |
| T494 | Rotation: two hashes in `tls_cert_sha256`, either certificate admitted, a third refused |
| T495 | The gateway refuses to start on a mismatch, printing observed beside pinned, exiting non-zero with nothing on stdout |
| T496 | `upstream:` in a `ctrlrun.policy/v7` document is a load error naming the key and its consequence |
| T497 | `upstream:` on an action that reaches the kernel through the ACS hook, or in-process, is a load error naming the surface |

---

## 5. One ordered list of checks, both modes

### 5.1 The debt, and why it is a refactor

`SPEC-v0.9.md` §4.2.1b is the statement of what is wrong, and it is read before anything here.
`_secure` and `_observe_secure` are separate implementations whose checks run in different orders,
and `_Observation` keeps the **first** reason it is given. So for an action tripping more than one
refusal, observe mode names the one it reached first, which is not always the one enforce mode
raises.

Measured on the tree at `22c9948`, since the ordering is the whole subject: `_secure` presents the
approval at `control.py:2352` and calls `_in_scope` at `control.py:2379`; `_observe_secure` calls
`_in_scope` at `control.py:1623`, above its approval handling. That is §4.2.1b's first named case, in
line numbers.

v0.9 aligned three cases one at a time, the approval gate (T451), the reservation (T452) and the
scope provider (T458). **Those three reorderings produced four regressions between them**, which
§13.8 enumerates: an observed resumed receipt reporting another action's spend, a throwaway
`_Observation` writing its event twice so a receipt said `ALLOW` beside a log saying `DENY`, a
dropped `effect_key` on the one event naming which effect a budget refused, and an
`InvalidArgument` subclass that stopped being picklable. That is the argument for doing this once as
a refactor rather than as a fifth patch, and it is the reason this item gets the tree to itself.

### 5.2 What lands

**The order is declared once, as data, and both modes walk it.** One ordered sequence of checks,
each naming its reason, with `_secure` raising at the first that fails and `_observe_secure`
recording the first that fails. Neither keeps a copy of the order.

**This item changes no behaviour an operator asked for**, and that is worth stating before the tests
rather than after: its whole value is in what it makes impossible. A sixth unaligned case cannot
arise, because there is no second order for one to differ from.

`SPEC-v0.9.md` §4.2.1b's two known-unaligned cases are the test cases and not the goal:

- **scope against the approval gate**: an action both out of scope and awaiting approval;
- **`policy_unapproved` against anything decided after it**: enforce refuses it above authority and
  policy, observe records it below both, so it is lost whenever something later blocks first.

**`v0.9 §4.2.1b`'s promise is amended, not merely met.** That section says the rule to rely on is
that observe mode reports a refusal exactly when enforce mode would refuse, and **not** that it
always names the same one. After this item the stronger sentence is true and the spec says so: for
every action, observe mode's `blocked_reason` is the reason enforce mode raises. §10 carries the
row.

### 5.3 The proof obligation, which is the item

**The property test is generated, not enumerated.** For every pair of refusals an action can trip,
the enforce-mode reason and the observe-mode `blocked_reason` agree. Pairs are generated from the
declared order rather than listed by hand, because a hand-written list is exactly what left two
cases unaligned in v0.9: the list is the thing under test.

A pair that cannot be constructed is **reported by name** rather than skipped silently, on the
mutation-pattern rule that a negative test proves nothing unless the thing it forbids would
otherwise happen. A generator that quietly produced zero pairs would be the false green here, and it
is the most likely one.

### 5.4 What this item does not do

**It is not a reorganisation of `control.py`.** `SPEC-v0.9.md` §12 refuses that, and §8 refuses it
again. This item unifies one ordered list across two methods and does not move anything else out of
the file.

**It does not change observe mode's contract.** Observe mode still charges nothing (`v0.9 §4.2.1`),
still refuses nothing, still writes no `denied` receipt, and `v0.9 §4.2.1a`'s limit about sizing a
budget from an observed run is untouched.

### 5.5 Acceptance tests for item 4

| | Test |
|---|---|
| T498 | The generated property: over every constructible pair of refusals, enforce's raised reason equals observe's `blocked_reason`. The pair set is asserted non-empty and its size is reported |
| T499 | `v0.9 §4.2.1b`'s first named case: out of scope and awaiting approval, both modes name the same reason |
| T500 | Its second: `policy_unapproved` against a later refusal, both modes name the same reason |
| T501 | Every pair the generator could not construct is named in the test's own output, and the list is asserted against the declared order so a shrinking pair set fails red |
| T502 | The four v0.9 regressions as regression tests: the resumed observed receipt's spend, the doubled `_Observation` event, the `effect_key` on the budget-refusal event, and the picklability of every `InvalidArgument` subclass across `verify`'s JSON-over-stdin children |

T502's last row is the one nothing in this repository would otherwise catch, which is why §13.8
named it, and it belongs to this item because this item rewrites the code that broke it.

---

## 6. The operator surfaces for a hop

### 6.1 No new command

`v0.9 §7.1`'s reasoning applies unchanged: a hop is one more thing `inspect` answers about, and §9
names no command. The management plane stays off the roadmap, `v0.9 §12`'s row that
`ctrlrun receipts`, `inspect` and `--json` are the interface is untouched, and a hop view is not an
exception to it.

### 6.2 The question, which is `v0.9 §7.2`'s shape one level up

The 3am question for a budget was *this refused, and I cannot see why*. For a hop it is **which
envelope did the peer actually hold, and which hop narrowed it**.

`ctrlrun inspect --hop <delegation-id>` answers it, emitting `ctrlrun.hop/v1`:

| Key | Holds |
|---|---|
| `hop` | the delegation id asked about |
| `created_by` | the principal that created it, agent and user, from the record |
| `created_at`, `created_via` | when, and by which surface. `created_via` is `"hop"` for a hop and its other three values for an ordinary delegation, so one command answers about both |
| `subject` | who it was issued to |
| `depth` | derived by walking to the root, never read from the stored column (`v0.3 §5.5`) |
| `chain[]` | one entry per ancestor to the root: `id`, `depth`, `revoked_at`, and **the dimension on which each step narrows** |
| `revoked_at` | on the hop itself, or `null` |

**`chain[]` carrying which dimension narrowed at each step is the part that answers the question.**
An operator looking at a refused action knows the chain is valid or it is not; what they cannot see
today is which link took the resource away. This renders it once per step.

**Its own document rather than a key in `ctrlrun.inspection/v2`**, on `v0.9 §10.1`'s argument for
`ctrlrun.budget/v1`: that one answers about an **action** and this answers about an **authority
record**, and a reader handed one would have to know which shape it got before it could read either.

### 6.3 One command from the refusal to the thing that explains it

**Every refusal that names a delegation prints the command with its argument filled in**, not with a
placeholder. `authority_hop` prints the presented id, `authority_escalation` with
`missing_parent_id` prints that id, `authority_revoked` prints the revoked one.

```
denied: stripe.refund  authority_escalation  (a record in the chain could not be read)
        ctrlrun inspect --hop dlg_5c44df6177f0a1b2c3d4e5f60718293a
```

A placeholder is what makes an operator stop and go looking, and the id is already in hand at the
point the line is printed. This is the one thing §6 must get right; everything else in it is a
rendering.

### 6.4 Which principals hold a root grant

§2.3.2's deployment rule is an operator's half of the guarantee and needs a surface, or it is
advice. `ctrlrun inspect --hop` with no argument is **not** the answer, and neither is a new
command: `ctrlrun scan` already reads a document and reports what it found, so the root-grant
holders go there, as a line saying which principals hold authority that no hop bounds.

A deployment following §2.3.2 shows its worker agents absent from that line. One that does not shows
them present, which is the fact and not a verdict: `SPEC-v0.4.md` §3.9's rule that CTRLRun never
grades an operator's document holds here, so `scan` reports and does not score.

### 6.5 Acceptance tests for item 5

| | Test |
|---|---|
| T503 | `inspect --hop` over a two-hop chain renders every ancestor, each with the dimension it narrowed on, and `depth` derived by walking rather than read from the column |
| T504 | An unknown hop id exits non-zero with nothing on stdout, as `inspect` does for an unknown action |
| T505 | Each of the three refusals prints its command with the real id substituted; asserted by running the printed command and requiring exit 0 |
| T506 | `--json` emits `ctrlrun.hop/v1` with exactly the keys §6.2 names |
| T507 | `ctrlrun scan` names every principal holding a root grant, and omits one holding only hops |

---

## 7. The guarantees

`ctrlrun.guarantees/v6` is G1 to G27. **The catalogue moves once**, with item 1's G25, and G26 and
G27 join it with their items. No stub rows: a guarantee that reports anything before its check
exists is a false green, which is what 0.6.1 had to fix and what G17 shipped as in v0.8.

**Three, and v0.10 does not invent a fourth.** `ROADMAP.md` assigns v0.10 no guarantee ids and gives
it **no exit criterion at all**, which `SPEC-v0.9.md` §8 records. So the numbers come from the
catalogue's own version-order rule (`v0.4 §2.3`: a guarantee that is added takes the next one), G24
being the highest at the tag, and **§7.3 writes the exit criterion this milestone is measured
against**, since the roadmap has none to inherit. Items 4 and 5 ship without a guarantee id, and
that is recorded here rather than left to look like an oversight: item 4's value is a property test
over a generated pair set (§5.3) and item 5's is a rendering.

| Id | Title | Width | Positive control | `N/A` when |
|---|---|---|---|---|
| G25 | `a hop narrows or it is refused` | 30 | a hop that narrows correctly admits the action | no grant in the document is delegable |
| G26 | `both receipts name one hop` | 26 | the two ids compared and equal | no grant in the document is delegable |
| G27 | `a swapped upstream is denied` | 28 | the pinned upstream admits the action | no action entry pins an upstream |

**G27 grades §4.3's check 2 and only check 2**, and the scope is written here because the title
would fit either. Check 2 is the one that produces a `DENY`, which is what the guarantee's own words
say; check 3 refuses at the handshake and produces `NotExecuted` with the effect `FAILED`, which is
a different outcome under a different name. A scenario that graded the handshake would report `PASS`
for a guarantee whose title promises a denial that never happened, and a scenario allowed to grade
either would report `PASS` without anybody knowing which.

It is also the only one `verify` can grade without a network: the comparison at check 2 is a pure
function over two strings, so `verify` seeds an observation and asserts the refusal, with no TLS
listener and no certificate to generate. Check 3's coverage is T491's, which is an acceptance test
rather than a guarantee, and §4.3's table says which is which.

Titles are counted against `report._TITLE_WIDTH`'s 32 (`verify/report.py:37`) rather than estimated,
because v0.7 had to shorten one and v0.8 three.

**G25's title says "narrows or it is refused" and not "widening is refused"**, because the guarantee
grades both halves and a title naming only the negative would let the positive control drift out.

### 7.1 G25 must not pass for a reason that has nothing to do with G25

**This is `SPEC-v0.9.md` §13.8's finding written as a requirement**, and it is the single most
important sentence in §7. G22 proved that a budget holds a charge and passed because an earlier
scenario had left a context variable set; `ctrlrun verify --only G22` reported **FAIL** on a shipped
example while a full run reported `PASS`.

G25 is in exactly the position to repeat it. A scenario that hops a narrow envelope to a principal
holding **nothing else** passes whether or not §2.3's rule exists, because there is no other grant
for the evaluation to fall back to. That is a guarantee about the containment relation, which v0.3
already had, wearing this milestone's name.

**So G25's scenario MUST give the receiving principal a grant of its own that would admit the wider
action**, and assert that the hop decides anyway. That is P1's document (§2.3.1), and it is the only
shape in which G25 grades the rule item 1 adds rather than the relation v0.3 shipped.

### 7.2 Every guarantee grades the same alone as in a full run

`ctrlrun verify --only <Gn>` and a full run MUST agree, for every one of G25, G26 and G27. There is
a test for this over G22 to G24; **it is extended to the new three rather than copied**, so a fourth
milestone does not add a fourth copy.

The rule that produced it: a guarantee that reads state an earlier scenario left behind is graded on
that state and not on its own. Each of the three here is at risk in a different way, and each is
named so an implementer knows what to look for: G25 reads a store that scenarios share, G26 compares
two receipts that another scenario could have written, and G27 reads an observation register that is
per-process by construction (§4.3) and therefore the most order-dependent of the three.

### 7.3 The exit criterion, written here because the roadmap has none

v0.10 exits when all of the following hold, and the release item asserts each:

- `ctrlrun.guarantees/v6`, with G25, G26 and G27 each graded or `N/A` with a reason true of the
  operator's document, and each grading the same under `--only` as in a full run;
- all three **`PASS`** on a shipped example, so the milestone's guarantees are graded on what this
  repository ships rather than only on a fixture. At least one shipped example exercises a hop, and
  at least one pins an upstream;
- `ctrlrun verify` reports everything 0.9.0 reported, unchanged;
- an action under a two-hop chain charges every ancestor, proved against Postgres under the v0.6
  multi-process standard;
- observe mode's `blocked_reason` equals enforce mode's raised reason over the generated pair set
  (§5.3), with the pair set asserted non-empty.

`ROADMAP.md`'s v0.10 section gains this criterion in the release item's docs PR, and the reconciled
note records that the criterion was written in the spec because the roadmap carried none.

---

## 8. Explicitly out of scope

Each with its reason. **Three are the roadmap's own boundary for v0.10** (no A2A conformance claim,
no provenance beyond the pin, nothing that inspects a package). The rest are this document's, or
inherited from an earlier milestone's list and restated because they are live temptations here.

- **A wire format, an agent card, a task lifecycle, or a transport.** §3.1. Two strings in metadata
  the deployment already carries, and no translation layer.
- **Any A2A conformance, compliance or alignment claim.** `ROADMAP.md` says "A2A, as code. No
  conformance claim", and `v0.9 §12`'s last row forbids the vocabulary outright.
- **Cross-store propagation.** §3.2. A hop between agents whose stores differ is refused, and the
  two things that would lift it, a ledger both sides charge in one transaction and an issuer-side
  reservation the receiver calls, are named there rather than implied.
- **A signed capability token**, and therefore key generation, rotation and revocation, which is
  issuing (`v0.3 §1.1`, `SPEC-v0.6.md` §11).
- **Replicating delegation rows to a second store.** §3.2: a staleness window in which a narrowed
  root has not narrowed, which is the property §2.6 exists to keep.
- **A separate hop counter.** §2.5. One chain, one bound, one cost.
- **Provenance at large.** No package, model, registry, build or signature chain is inspected. §4.1,
  and `OWASP-AGENTIC-TOP10.md`'s `ASI04` row keeps its verdict.
- **In-process upstream pinning**, and **pinning at the ACS hook.** §4.4, each refused as a load
  error rather than left as a key that silently does nothing.
- **A dependency on `cryptography`**, and therefore a public-key pin. §4.2.
- **More than one upstream per gateway**, unchanged from `v0.2 §12`, which is what lets §4 speak of
  "the upstream" in the singular.
- **A hop that widens under any flag.** No `trust_peer`, no `allow_widening`, no
  `hop_soft_check`, no development setting that admits a hop the containment relation refuses.
  The no-relaxing-flag rule of `v0.4 §3.9`, `v0.5 §3.8`, `v0.6 §1.1`, `v0.7 §2` and `v0.8 §2`, in
  this milestone's vocabulary.
- **Making a receiving agent present its hop.** §2.3.2. The kernel records which happened; it cannot
  compel the choice.
- **Binding a hop into the action hash.** `v0.9 §6.3.1`'s argument is unchanged: it moves every
  action hash in existence, breaks every outstanding approval, and belongs to a milestone willing to
  pay for a migration.
- **A reorganisation of `control.py`.** `SPEC-v0.9.md` §12 refused it and item 4 does not become an
  exception by touching the file heavily.
- **A management plane**: a hop browser, a topology view, an agent graph. `ctrlrun inspect`,
  `receipts` and `--json` are the interface. The Pro track's dashboard is on its own roadmap and
  never on a kernel version line.
- **Deleting delegation rows**, and any pruning of them. §3.4.1 records that a receipt points at one;
  `v0.11`'s retention item is where that is designed.
- **A second identifier for a hop.** §3.4.
- **Emergency stop**, `ctrlrun suspend`. On v0.7's list, v0.8's and v0.9's, and unchanged here:
  revocation already cuts a chain of any depth with one write.
- **Moving the H1 or the category line.** A maintainer's act, never a PR's.
- **Any compliance, conformance, certification or alignment claim.**

---

## 9. Public API additions, frozen for v0.10

One justification per row. Anything not here is a spec amendment before it is code.

| Addition | Why an existing name does not serve |
|---|---|
| `hop=` on `@protect` and `Control.execute` | nothing carries which envelope an action is proposed under. It cannot go on `Action` for `v0.9 §6.3.1`'s reason, which is unchanged and not weaker here: the payload `canonicalize` builds is fixed, and adding to it moves every action hash in existence, so every outstanding approval stops matching and every receipt's hash ceases to reproduce |
| `hop=` on `Authority.evaluate` | **amends a signature `SPEC-v0.3.md` §11 froze and `v0.9 §10.3` already amended once**, and is recorded here the same way rather than slipped in. §2.3 puts the selection of the deciding grant inside the evaluation, which is the only place that can refuse a fallback |
| `hop=` on `Control.evaluate` | **also amends a frozen signature**, for `v0.9 §10.3`'s reason: without it `Control.evaluate` and `Control.execute` disagree about a hop-selected grant, and `ctrlrun.adapter.needs_approval` routes through `evaluate` |
| `Control.hop(parent_id, grant, *, by) -> Delegation` | `Control.delegate` hardcodes `via="api"` (`control.py:3863`) and the private `_delegate` takes `via`. Exposing `via=` publicly would let API code write `"cli"` or `"break-glass"` into `created_via`, which is **evidence about which surface acted**, and a caller that can forge it makes the field decorative. A method whose name fixes the value cannot |
| `CreatedVia` gains `"hop"`, from three values to four | **an exported type alias whose value changes** (`authority.py:124`), read by `_CREATED_VIA` at parse time (`authority.py:834`), which raises `_UnreadableError` on a value it does not know. An older binary meeting a `"hop"` row therefore reports the delegation unreadable, which is the fail-closed direction and is stated rather than discovered |
| `Receipt.hop` | §3.4. One field, the id both sides name |
| `data.hop` and `data.task` on `EXECUTION_STARTED` | §3.4.2. The event's `data` is `{}` today (`control.py:1487`, `control.py:1561`); it becomes the durable binding `_resumed_context` reads back, which is what lets a resumed leg be evaluated on both dimensions instead of skipping them |
| `AUTHORITY_HOP` (`"authority_hop"`) | a presented hop that names no live delegation addressed to this principal is not any existing reason: it is not `no_authority`, which means nothing matched, and not `authority_escalation`, which means a chain step failed. §2.3.2 rule 3 forbids using it as a bucket for either |
| `UpstreamPin`, and `upstream` on an action entry | §4.2. `McpOptions` (`policy.py:543`) carries per-tool assertions an operator makes about their upstream and is the closest existing name; it holds claims about **behaviour** (`not_executed_on_error`) and this holds claims about **identity**, and merging them would put an authorization input in a structure whose documented job is a `NotExecuted` hint |
| `UPSTREAM_MISMATCH`, `UPSTREAM_UNVERIFIED` | §4.5. Two reasons, separately observable, because "the server changed" and "nobody has checked" are different findings and an operator fixes them differently |
| `-41013` `ctrlrun.upstream_unpinned` | §4.5, on `v0.3 §8.4`'s test for `-41012`: a client answers "not permitted to anyone", "not permitted to you" and "not against that server" three different ways |

**No new error type.** `errors.py`'s closed set already covers every refusal here: `authority_hop`
is an `AuthorityDenied` reason, and both upstream reasons are `ActionDenied` reasons. If an item
disagrees, the item stops and the maintainer is asked.

**No new `StateStore` method and no amendment to the protocol.** `SPEC-v0.6.md` §9.2's bar is *a
second backend could not be written without it*, and nothing in v0.10 clears it: a hop is a
`delegations` row, which `put_delegation` and `get_delegation` already write and read.

**No migration, and the build plan expected one.** `0007_budget_ledger` stays the last
(`migrations.py:411`). The build plan's numbering table reserves `0008` for this milestone; the tree
says none is needed, and §1's opening rule applies: where this document and a build plan disagree,
this document wins.

Measured, because "no migration needed" is exactly the claim that is wrong one time in three:

- **`Receipt.hop` needs none.** A receipt is stored as a whole JSON document in one `json TEXT`
  column (`migrations.py:138`, written at `state.py:1638`), so a new receipt field is a new key in
  that document and not a column.
- **`data.hop` and `data.task` on `EXECUTION_STARTED` need none**, for the same reason: an event's
  payload is `data_json TEXT` (`migrations.py:146`, written at `state.py:1581`).
- **`created_via = "hop"` needs none.** The column is already `TEXT` on both backends
  (`migrations.py:162` for SQLite, `migrations.py:258` for Postgres).
- **The pin stores nothing.** §4's observations are per-process by construction (§4.3) and the pin
  itself lives in the policy document.

**An item that finds it does need one stops and reports** rather than writing `0008` quietly, because
a migration is the one irreversible thing a release does and v0.9's was one-way.

### 9.1 Schemas

**`ctrlrun.policy/v8`**, bumped **once**, by **item 3**. The key it adds is `upstream:` on an action
entry (§4.6).

**`ctrlrun.receipt/v7`**, bumped **once**, by **item 2**. The whole v7 shape is frozen here before
any item starts:

| Field | Written by | Holds |
|---|---|---|
| `hop` | item 2 | the `delegation_id` of the hop this action ran under, or of the hop this action created, or absent |

Item 6 asserts it is written by something before the release PR opens, which is `SPEC-v0.7.md` §12's
D27 rule, run without incident for three items in v0.8 and three in v0.9.

**`ctrlrun.guarantees/v6`** is G1 to G27, moved once by item 1 with G25 (§7).

**`ctrlrun.hop/v1`**, added by **item 5** for §6.2, and recorded here rather than slipped in. Its own
document rather than a key inside `ctrlrun.inspection/v2`, on `v0.9 §10.1`'s argument for
`ctrlrun.budget/v1`: that one answers about an action, this answers about an authority record, and a
reader handed one would have to know which shape it got before it could read either. §6.2 has the
key table.

### 9.2 The module map

**No new module.** The hop is `authority.py` and `control.py`; the pin is `policy.py` for the key and
`gateway/` for the three checks; the ordered list is `control.py`; the surface is `reporting.py` and
`cli/`. A reorganisation of `control.py` is out of scope (§8) and does not become in scope as a side
effect of item 4 rewriting two methods inside it.

---

## 10. Fail-closed table for v0.10

| Situation | Outcome |
|---|---|
| A hop widens on any of the eight dimensions | **refused at creation** (§2.4), `reason="containment"` with `data.dimension` |
| A hop omits a dimension its parent constrains | **refused** (§2.4, `v0.3 §5.4`). Omission is never "unlimited" |
| An action presents a hop that names no live delegation | **refused**, `authority_hop` (§2.3.2) |
| An action presents a hop addressed to another principal | **refused**, `authority_hop`. The principal is the `IdentityProvider`'s, never the payload's (§3.3) |
| A presented hop does not authorise the action, and the principal holds a wider grant of its own | **refused. There is no fallback** (§2.3.2). This is the row the milestone exists for |
| The receiver's store cannot read an ancestor of the hop | **refused**, `authority_escalation` with `missing_parent_id` naming it (§3.2) |
| A hop's chain is revoked, expired, or no longer contained | **refused**, each under its own existing reason, never under `authority_hop` (§2.3.2 rule 3) |
| A chain of hops and delegations exceeds `max_delegation_depth` | **refused**, `max_depth` (§2.5) |
| A hop is presented with no resolvable receiving identity | **refused before an action exists** (§3.3), `-41007` at the gateway, no receipt and no events |
| A resumed leg whose `EXECUTION_STARTED` carries no hop or task, written by 0.9.0 | **evaluated as 0.9.0 evaluated it**: `evaluate_task=False`, no hop selection. A missing value is absence, not a refusal, or every in-flight action across the upgrade would be denied on the only receipt it gets (§3.4.2's table, T487) |
| A relay agent presents one hop and creates another in the same action | its receipt's `hop` names the hop it **acted under**; the created one is evidence as `DELEGATION_CREATED` (§3.4.3) |
| An action entry pins an upstream and nothing has observed one | **refused**, `upstream_unverified` (§4.5). Never admitted |
| An observed certificate hash is in no pinned list | **refused**, `upstream_mismatch`, `-41013`, upstream never called (§4.5) |
| A swapped upstream at handshake time | **`NotExecuted` before the first request byte**, effect `FAILED`, not `AMBIGUOUS` (§4.3) |
| An advertised tool schema moved under an approved action name | **refused**, `upstream_mismatch` (§4.2) |
| `upstream:` in a `ctrlrun.policy/v7` document | **load error**, naming the key and its consequence (§4.6) |
| `upstream:` on an action reaching the kernel in-process or through the ACS hook | **load error**, naming the surface (§4.4) |
| An older binary meets a `created_via` of `"hop"` | **the delegation is unreadable**, which `v0.3 §4.6` makes a denial and not a skip (§9) |
| Observe mode and enforce mode reach different refusals | **cannot arise**: one declared order, walked by both (§5.2). This row is a MUST and §5.3 is its proof obligation |

---

## 11. What building v0.10 settled

*One subsection per question the drafting could not close, each stating what the code decided and
which section carries it. `SPEC-v0.4.md` §12 through `SPEC-v0.9.md` §13 are the format.*

**This section is empty on purpose, and item 6 writes it in one pass**, on the pace decision
`SPEC-v0.9.md` §13 records: the spec amendment an item owes as it lands is its §9 name row, its MUST
sentences and its §10 fail-closed row, and the prose explaining what building settled is written
once over the finished milestone rather than five times over guesses.

**The cost of that, and what pays it**, restated because v0.9 found it real: written at the end, this
section loses the during-the-milestone signal that a reviewer uses to tell which claims have been
stress-tested by somebody other than their author. What replaces it is unchanged, **an item that
settles something surprising leaves a line in its `CHANGELOG` entry when it lands**, and item 6
writes §11 from those lines. An item whose PR body reports a question it could not settle has
already written its §11 entry and should say so.

**And one thing is already known to belong here, so it is named rather than rediscovered.**
`SPEC-v0.9.md` §13.8's rate is the number to beat: across two review rounds on finished v0.9 code,
roughly seventeen defects, about half of them introduced by fixing the other half, and not one of
them an action running that should have been refused. Every one was reporting, tooling, or observe
mode. **Item 4 rewrites observe mode**, which is where that half lived, so this milestone's own
version of that count is the first thing §11 should carry.
