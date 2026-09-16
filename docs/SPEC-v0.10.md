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

**ctrlrun does not become an A2A implementation.** It defines no wire format, no agent card, no task
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
3. **A hop is evidence, not a side channel.** Every hop is named on both of its ends: by the
   receipt of each action run **under** it, and by the `DELEGATION_CREATED` event that **created**
   it, which names the action that created it where there was one (§3.4). An earlier draft said
   "the two receipts name the same hop", which is false of a relay: the middle agent of a two-hop
   chain acts under one hop and creates another, and a single-valued `Receipt.hop` cannot hold
   both (§3.4.4).
4. **Nothing infers who the peer is.** The receiving agent's identity is resolved by `v0.3 §3`'s
   `IdentityProvider` and never read off the payload, exactly as `v0.3 §8.4` settled for the ACS
   hook (§3.3).

### 1.3 The four open questions, answered here

The build plan hands this document O1 to O4 and requires each to be answered in the spec rather than
left to an item, because all four cross a boundary and an item that answered one differently from
another would ship two models.

| | Question | Answer | Section |
|---|---|---|---|
| O1 | Does ctrlrun define a wire format or consume A2A's? | **Neither. What crosses is a reference, two strings, in whatever metadata the transport already has** | §3.1 |
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

**`contained_dimension` gains no caller at all, and certainly no second relation.** Counted rather
than estimated: **seven** call sites at `22c9948`, three in `authority.py` (break-glass at `:1410`,
`plan_delegation` at `:1478`, the evaluation-time chain walk at `:1688`) and four in
`verify/scenarios.py` (`:2360`, `:2363`, `:4632`, `:4651`). **A hop is created through
`plan_delegation`**, which is already one of them, so the count does not move.

An earlier draft said "a third caller", wrong twice: three already existed, and a hop adds none. The
correction earns its place because the four it omitted are in `verify`, which is the module a fourth
relation would most plausibly be written in: a scenario needing "did this step narrow?" and finding
no helper is how a second implementation starts. §6.2 is where that pressure actually appears, and
it is answered there rather than by a copy. An implementation that wrote a second
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

**P1, run against the tree at `22c9948`.** One document, two grants: an issuer grant for the
planner carrying a budget of 10,000 a day and `delegable: true`, and a grant of the worker's own
carrying no budget. The planner delegates a correctly narrowed envelope to the worker: one resource,
100 a day. The worker proposes a 5,000 refund **on the resource the hop does name**, so the hop
genuinely admits the action, and the only question is which grant decides it. The two runs differ in
nothing but the codepoint order of the two grant ids.

```
worker's own grant sorts AFTER  dlg_ :  named dlg_aa59ede0…   charges [(dlg_aa59ede0…, amount, 5000),
                                                                      (issuer,        amount, 5000)]
worker's own grant sorts BEFORE dlg_ :  named aaa-receiver-own charges []
```

**One document, one action, and whether the issuer is charged 5,000 or nobody is charged at all
turns on how two identifiers sort.** In the second run the hop was never consulted, the resource
restriction the planner attached decided nothing, and the planner's 10,000 budget paid nothing.

**Two corrections to an earlier draft of this paragraph, because they are the kind this milestone
keeps finding.** It said the worker proposed "a refund on a resource the hop does not name", which
is false of the probe printed above: the action is *inside* the envelope, and that is what makes the
finding sharp rather than weaker. An action *outside* the envelope is worse and arrives by a
different route, since the hop then fails `matches_shape` and never competes at all, so the worker's
own grant authorises it outright with nothing to compare. And the draft said the rename "decides the
other way" without saying which way; the direction is above, measured.

`_by_grant_id`'s docstring says ties break on codepoint order so that reordering the file changes
neither the decision nor the id. That is true and it is a statement about a *set of the principal's
own grants*. It was never a statement about a set that contains somebody else's envelope.

#### 2.3.2 The rule, and what it does not promise

`Authority.evaluate` takes a `hop: str | None = None`. Where it is `None`, everything is exactly as
0.9.0, which is why every existing deployment upgrades untouched. Where it is not:

1. **The delegation that id names is the only candidate.** Its chain is **walked**, by
   `v0.3 §5.6`'s existing rules, and is **not offered**: an ancestor is never itself a candidate.
   No other grant of the principal is a candidate either, passing or failing.

   The distinction is not pedantry. An earlier draft said "exactly the delegation that id names,
   plus its chain", and read as "the ancestors are candidates too" an implementer gets a set in
   which a root addressed to `agent: "*"`, or one whose `user` pattern admits the receiver,
   authorises the action **at its own width**. That is §2.3.1's hole with one extra step, reached
   by an ambiguity rather than by a decision.

2. **`authority_hop` means the presented hop does not reach this action**, and it covers exactly
   the two things that are true of the hop rather than of the chain:
   - the id names no delegation at all, in which case `data.hop` is the presented id and there is
     nothing else to say;
   - the delegation exists but its grant does not match the action's shape, in which case
     `data.hop` names it and **`data.dimension`** names which of `subject`, `actions`, `resources`
     or `environments` failed.

   **`matches_shape` cannot supply that dimension and §9 therefore carries a name for it.**
   `Grant.matches_shape` (`authority.py:534`) returns a `bool` and returns `False` at the first of
   the four that fails, so the information exists on the stack and is thrown away. The addition is
   `unmatched_shape(grant, action) -> str | None`, returning the first failing row in the order
   above, with **`matches_shape` reimplemented as `unmatched_shape(...) is None`** so there is one
   implementation and not two that agree today. This is `contained_dimension`'s shape applied to
   the other half of `v0.3 §4.3`'s `iff`, and §2.2's no-second-relation rule is the reason it is
   done this way round rather than by a second walk in `evaluate`.

3. **Every refusal the chain produces keeps the reason it already has**: `authority_revoked` for a
   revoked link, `authority_expired` for an expired hop, `authority_escalation` with
   `expired_parent_id`, `missing_parent_id` or `dimension` for the chain-walk rules,
   `authority_constraint` and `authority_task` for the grant's own dimensions. **`authority_hop` is
   not a bucket for any of them.**

4. `_charges_for` is unchanged and therefore charges that delegation and every ancestor to the root
   (`authority.py:1285`), which is rule 2 satisfied by machinery that already exists.

#### 2.3.3 Why rules 2 and 3 are shaped that way, measured

An earlier draft wrote rule 2 as "MUST exist, **MUST be live**, and its grant's `subject` MUST
match ... Otherwise `authority_hop`", and rule 3 as "every other refusal keeps the reason it already
has". **Those two contradicted each other on the same input**, and the review round found it: a
revoked hop is not live, so rule 2 demanded `authority_hop` while rule 3 forbade it. An *expired*
hop was named by neither, rule 3 having listed only an expired ancestor.

Liveness is therefore struck from rule 2. Existence and shape are the two things the hop itself
owns; everything about whether a live hop is still good is the chain's, and the chain already
answers it. P9, the only grant the principal holds being the hop:

```
case                       reason                   grant_id
inside the envelope        authority_grant          'dlg_30448e1d…'
action not covered         no_authority             None
resource not covered       no_authority             None
environment not covered    no_authority             None
constraint fails           authority_constraint     'dlg_30448e1d…'
subject is someone else    no_authority             None
```

**Four of the six report `no_authority` with no id, and that is the milestone's headline refusal.**
(An earlier draft said three, in a section whose whole authority is that it was measured rather than
asserted, while the table above it printed four. The fourth is `subject is someone else`, which is
the row §3.1.1's attack table and §10's row both turn on.)
`matches_shape` (`authority.py:536`) filters on subject, action, resource and environment before any
outcome is collected, so a hop that does not cover those falls out of the loop entirely and
`evaluate` returns `AuthorityResult(False, NO_AUTHORITY)` with `grant_id=None`. An operator whose
peer was handed a narrow envelope and proposed something outside it is told **"you hold no authority
at all"**, with nothing to hand `ctrlrun inspect --hop`.

Two things make that unacceptable rather than untidy. `v0.9 §8`'s G24 already set the precedent, in
`verify/guarantees.py`'s own words: the refusal reports `authority_task` "rather than a bare
no_authority so an operator can tell this from having no grant at all". And §6.3 promises that every
refusal naming a delegation prints a command with its argument filled in, which is unkeepable when
the refusal carries no id.

So rule 2's second clause exists: **a hop that is presented and does not match the action's shape is
`authority_hop` with `data.dimension`**, not `no_authority`. A principal that presented no hop is
unaffected, and `no_authority` keeps its meaning there.

**`authority_constraint` is deliberately left alone**, and the row above is why it is worth saying:
it already names the grant, so an operator already has the id, and folding it into `authority_hop`
would lose which of the grant's own conditions failed. The line between the two reasons is whether
the hop reached the action at all.

**What it does not promise, and this is the residual of the whole milestone.** ctrlrun cannot make a
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
in the same chain, so a chain under a document declaring `max_delegation_depth: 3` holds at most
three links whatever the mix, and depth is derived by walking to the root rather than read from the stored column
(`v0.3 §5.5`), so a hop cannot assert its way to a shorter chain.

**Rejected: a separate hop counter.** Two bounds over one chain means an operator must reason about
which binds, and the answer would be "whichever is smaller", which is one bound with extra
configuration. It also breaks `v0.9 §2.7`'s cost statement, which says a consumption writes or checks
one row per ancestor and points at `max_delegation_depth` as the thing that bounds it. One chain, one
bound, one cost.

**It is a property of the `authority:` section, not of a grant**, and an earlier draft said "a root
grant with `max_delegation_depth: 3`", which is wrong in the direction that makes the trade below
look cheaper than it is. `_AUTHORITY_KEYS` is `{"max_delegation_depth", "grants", "break_glass"}`
(`authority.py:171`); the value is held once on the `Authority` (`authority.py:1145`) and compared at
`:1403` and `:1471`. **One number governs every chain in the deployment.**

**What that costs, stated plainly.** The default is 3, so a deployment with a two-hop pipeline has
one link left for ordinary delegation beneath it, and a three-hop pipeline has none. An operator who
wants a longer pipeline raises the number in the document, which is a reviewed file, and raises the
per-consumption cost **for every chain in the deployment**, not for the pipeline that needed it.
There is no per-grant override and this document does not add one: a second bound over one chain
means reasoning about which binds, which §2.5 has already refused once above. That is the trade and
it is theirs to make.

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

**ctrlrun defines no wire format for authority, and consumes none.**

What crosses a hop is a **reference**: two strings, `hop` and `task`, carried in whatever field the
transport already has for caller-supplied metadata. For A2A that is the message's `metadata` object.
For MCP it is `params.metadata`, which `ctrlrun.acs` already reads. For an in-process call it is two
keyword arguments. **The envelope itself never crosses.**

Three reasons, and the third is the load-bearing one.

1. **An envelope on the wire is an assertion; a reference is a lookup.** A receiver handed a
   serialized grant has to decide whether to believe it, which means signatures, which means keys,
   which means issuing. `v0.3 §1.1` is unchanged: ctrlrun consumes identity and issues none.
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

#### 3.1.2 Where the reference is read, which §9 must freeze and an earlier draft left out

§3.1 says what crosses and was silent about **where it is read**, which is the half an item needs.
Measured at `22c9948`:

- **The gateway reads no caller metadata at all.** `grep -n metadata src/ctrlrun/gateway/server.py
  src/ctrlrun/gateway/mcp.py src/ctrlrun/gateway/wire.py` returns nothing, and
  `gateway/server.py:738`, `:753` and `:755` call `_control.evaluate(action)` and
  `_control.execute(action, executor, effect_key)` positionally. **`v0.9`'s `task` never reaches the
  gateway either**, which is `v0.9 §6.3.2`'s "the gateway's `tools/call`: no task" row, still true.
- **The ACS hook reads `params.metadata`** (`acs.py:412`) and also calls `evaluate` and `execute`
  without a task or a hop.

**So §9's frozen list, which stops at `Control`'s Python signatures, does not let item 1 wire either
surface**, and §9's own rule is that anything not there is a spec amendment before it is code. The
rows are added: the gateway and the ACS hook each read `hop` and `task` from the caller metadata
their transport already carries, under `v0.3 §8.2`'s rule of exactly one source and no default.

**And reading them there is safe for §3.1.1's reason and no other.** They are lookup keys, not
assertions: the principal still comes from the `IdentityProvider` and never from the payload, and a
hop addressed to somebody else matches nothing. A reviewer who accepts §3.1.1 for the in-process
case must accept it here; a reviewer who rejects it rejects both, which is why the argument is made
once and cited twice.

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

**A refusal names which dimension stopped it and never what the envelope contains.** That line is
drawn here, once, because §2.3.2 rule 2 and this paragraph were written against each other: an
earlier draft of this section said `data.hop` "carries the id and nothing else", while rule 2 added
`data.dimension`, and T483 asserted an exact key set that matched neither.

What a refusal may carry: the presented id, which the caller already holds, and the **name of the
row** that stopped it, one of `subject`, `actions`, `resources`, `environments`. What it may never
carry: the patterns, the limits, the subject, the expiry, or anything else about what the hop
**would** have permitted.

**The distinction is the one that matters for the oracle.** "Your resources row stopped you" tells a
peer which dimension to stop varying; it does not tell them which resources are permitted, and they
would learn the dimension by trial in four calls anyway. Enumerating the envelope's contents is
what `v0.9 §5.5` refuses when it lets a scope hash reach a receipt while the scope never does, and
that refusal is unchanged. Against it stands §6.3's promise that an operator paged at 3am gets a
refusal that says which link took the resource away, and a refusal naming no dimension cannot keep
it.

So the key set is exact and small, and T483 asserts it as a set rather than as a prose claim:

| Case | `data` keys |
|---|---|
| the id names no delegation | `reason`, `hop` |
| the delegation exists and its grant does not reach the action | `reason`, `hop`, `grant_id`, `dimension` |

`reason` is present on every `AUTHORITY_DENIED` payload `_authority_data` builds
(`control.py:999`), so a test asserting "the presented id and no other key" was asserting something
the recording path has never produced for any refusal.

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

**v0.10 makes that change, and what it buys is evidence, not blast radius.** An earlier draft said a
resumed leg that did not know its hop "would fall back to deciding against any matching grant, which
is §2.3.1's hole reappearing on every continuation". **That is wrong, and the code says so in a
comment**: at `control.py:1847`, *"the policy is evaluated again for the receipt, not to re-decide.
The action was decided and reserved on the first leg; refusing here would strand a reservation the
remote may already be acting on"*, and `v0.3 §5.6.1` gives authority the same treatment. The resumed
leg records a denial and the executor still runs. So what a hop on `resume` fixes is **which grant
the receipt names**, on the only receipt an MCP multi round-trip ever gets (`v0.9 §8.3`), and that
is worth fixing on its own terms rather than by borrowing an argument that does not apply.

**The decision point the earlier draft missed is `Control._suspend`, and it is a real one.** §3.4.3
carries it, because it is not about the resumed leg at all.

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

**The discriminator is the presence of the KEY, never the value.** This is stated because the
obvious reading of the table is wrong in the fail-open direction: a 0.10 build suspending an action
that carries a hop and no task writes `{"hop": "dlg_...", "task": None}`, and an implementer keying
on *value* reads "task is absent, therefore 0.9.0, therefore no hop selection either" and drops a
hop that is right there in the event. So: `"hop" in data` and `"task" in data` decide, `data == {}`
is the 0.9.0 row, and a `None` under a present key is a value this build wrote and means the caller
supplied none. T487 asserts the key-presence reading against a `{"hop": ..., "task": None}` event,
which is the case a value-keyed implementation gets wrong while every other test stays green.

**A missing key is absence, not a refusal**, and the distinction is load-bearing exactly once, at
the upgrade. It is not a widening the other way round: a leg this build suspended always carries
both keys, so the absent case cannot be manufactured by a caller, only by having been suspended
before the upgrade. §10 carries the row.

**And the ambient-context hazard stays closed, which is the reason to read the value from the event
and not from a context variable.** `v0.9 §6.3.2`'s closing paragraph works out that a task read from
a context variable at the resume path would let a process sitting inside some *other* `task=`
evaluate the resumed leg against an unrelated task. The event is durable, is bound to this
`action_id`, and was written by the leg that actually held the authority. **Nothing on this path
reads either value from an ambient context**, and a test asserts it by resuming inside an unrelated
`task=` and `hop=` and requiring the event's values to win.

#### 3.4.3 `Control._suspend` is the decision point, and it is where the fallback reopens

**`Control._suspend` re-decides authority, raises, and takes no hop.** It is the lease extension:
an action that suspends across a round trip asks to keep holding its reservation, and
`control.py:2218` evaluates authority again and `control.py:2227` raises `AuthorityDenied` when it
no longer passes. The source states why in the comment above it, and the sentence is `v0.3 §5.7`'s:
*"Without this, §5.7's 'a chain of any depth is cut by one write' is false for exactly the actions
in flight when an operator hits the switch."*

**So it is the one place §10's row "there is no fallback" can be falsified.** Under §2.3.2 the
decision at `execute`'s top is pinned to the hop; the decision at `_suspend`, reached mid-execution,
is not, so a receiver holding any grant of its own keeps its reservation across the round trip on
that grant after the hop is cut. Revocation would then fail to cut exactly the in-flight action the
check at `:2218` exists to cut, which is the guarantee inverted rather than weakened.

**The hop is passed to `_suspend` as a parameter, and it is never read from a context variable.**

An earlier draft of this section said the opposite: that `_suspend` "runs inside the
`Control.execute` call that was given the hop, on the same stack, so the ambient value is this
action's by construction". **That is false on every round after the first**, and a second review
round found it. `_suspend` is called from inside `_outcome` (`control.py:2029`), and `_outcome`'s
own docstring says *"`execute` and `resume` both come through here"*. So round two onward reaches
the lease extension from `Control.resume`, whose ambient context belongs to **the resuming
process**: unset, or holding some unrelated hop that would then decide this action's extension.
The shipped suite already proves the shape is the designed case, in
`tests/test_attempt_cap.py`'s five-round run.

That is the `v0.9 §6.3.2` ambient-context hazard arriving on the path the earlier draft thought was
safe, and it fails in the direction that decides an extension against somebody else's envelope.

| Path | Where the hop comes from | Why |
|---|---|---|
| `Control.execute`, `@protect` | the caller's `hop=` | the action is being proposed now |
| `Control._suspend` | **a parameter**, threaded `execute` to `_outcome` to `_suspend` | the caller that knows says so; nothing on this path is ambient |
| `Control.resume` | `EXECUTION_STARTED`'s `data.hop` (§3.4.2) | a different call, whose ambient context is unrelated |
| `Control.evaluate` | the caller's `hop=` | it must agree with `execute` (§9) |

**`_HOP` exists, and it is evidence only.** It carries the hop to the receipt the way `_TASK`
carries the task, and no decision reads it. That separation is the rule rather than an accident:
a grep for a decision-path read is an acceptance test (T488b), because the failure mode is a later
edit reaching for the ambient value again, which is precisely what happened once already.

**T488a pins the shape and T488b pins the absence.** The first asserts every caller that can supply
a hop declares it and that `_suspend` takes it as a parameter; the second greps the decision path
for a read of `_HOP`. A behavioural test that suspended **once** would exercise only round one,
which is the round the earlier draft was right about, and would have gone green over the defect.
**Any behavioural test of this must suspend at least twice.**

**One residual, until item 2 lands.** `Control.resume` cannot supply a hop until
`EXECUTION_STARTED` carries one (§3.4.2), so between item 1 and item 2 a resumed round's lease
extension is decided with **no** hop. That is unbound rather than bound to the wrong envelope,
which is the safer of the two, and it is recorded here rather than discovered in item 2.

#### 3.4.4 A relay agent is both sides at once, and the field is single-valued

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

**What finds the created hop instead**: `DELEGATION_CREATED`, carrying `data.created_via = "hop"`,
`data.delegation_id`, and **`action_id` naming the action that created it**.

**That last field is new, and an earlier draft of this section assumed it was already there.** It is
not: `_append_delegation` (`control.py:4316`) writes `Event(type=type_, action_id=None, ...)`, and
its docstring gives the reason, that the three `DELEGATION_*` types "are about an authority record,
created and revoked outside any action's life". That is true of `ctrlrun delegate` from a shell and
**false of a hop created inside a running action**, which is the ordinary case here. Left as it is,
the relay's created hop is linked to the relay's action by nothing but a timestamp, and the
"evidence exists" sentence above would have been a promise the event could not keep.

So: **`Control.hop` takes an optional `action_id=`, and passes it to `DELEGATION_CREATED`.** Where
the creator supplies it the event names the action that created the hop; where it does not, the
event carries `None` exactly as today, and `v0.3 §7`'s note that these events name their record in
`data.delegation_id` is unchanged.

**Explicit, and deliberately not ambient.** A second review round established what the alternative
costs. There is no context variable holding the current action: the nine in `control.py` carry the
principal, the grant id, the task, the scope hash, the authority result, the budget charges, the
policy-change flag, the presented approval and the idempotency token, and none of them an action.
Adding one and reading it inside the executor, where a relay's `control.hop(...)` call lives, gives
this:

```
  same thread                            dlg_THE_HOP
  threading.Thread                       <unset>
  ThreadPoolExecutor.submit              <unset>
  asyncio.run (copies the context)       dlg_THE_HOP
  Thread + copy_context() (opt-in)       dlg_THE_HOP
```

A relay whose executor hands work off from a worker thread, which is an ordinary shape for an agent
fanning out, would write `action_id=None` **silently**. `transport.py` already documents that hazard
for its own register and is explicit that there it fails *safe*; here it would fail in the evidence
direction, which is the one nothing turns red.

**What that costs, stated rather than hidden.** A relay that does not pass the id gets no link from
its action to the hop it created, and the two are related by a timestamp. §1.2's rule 3 is therefore
true of every hop's **two ends** unconditionally, and the further claim, that a relay's created hop
can be traced to the action that created it, holds **only where the creator supplies the id**. §7's
G26 grades the unconditional half; §8 records the conditional one as a limit rather than a promise.

**Rejected: two fields**, `hop_in` and `hop_out`. It would put a value on the hot path that is
`null` on every receipt except a relay's, and it would make "which hop" answerable two ways on the
one shape where an implementation is most likely to fill the wrong one. The two-hop chain in §2.6 is
then reconstructed by the walk, which is what `chain[]` renders (§6.2).

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
| T483 | `authority_hop`'s `data` matches §3.3's table **by exact key set**, both rows, so a later field cannot be added without a test going red, and so the envelope's contents cannot leak into a refusal one key at a time |
| T484 | A `ctrlrun.receipt/v6` receipt and a `v7` receipt in one chain both parse and the chain verifies across the boundary |
| T485 | A suspended action resumed under a task-bound, hop-selected grant is evaluated on both dimensions and is **not** denied, with `EXECUTION_STARTED` carrying both values. `v0.9 §6.3.2`'s cost, paid |
| T486 | The resume runs inside an unrelated `task=` and `hop=`: the event's values decide and the ambient ones reach nothing. `v0.9 §6.3.2`'s ambient-context hazard, still closed |
| T487 | A leg suspended by **0.9.0** and resumed by this build: `EXECUTION_STARTED` carries neither value, the leg is evaluated as 0.9.0 evaluated it, and a task-bound grant does **not** deny it. The upgrade case (§3.4.2, §10) |
| T488 | A relay agent presents one hop and creates another in the same action: its receipt's `hop` names the one it **acted under**, and the created hop is found from `DELEGATION_CREATED`. §3.4.4's precedence |

---

## 4. Upstream identity pinning

### 4.1 The sharp case, and what it is a slice of

A policy authorises `stripe.refund`. The gateway fronts an MCP server at a name the operator wrote
down. Someone swaps what answers at that name, or leaves the server in place and moves the schema of
the tool sitting under the approved action name so that `amount` now means something else. Every
grant still matches, every constraint still holds, the receipt still says `stripe.refund`, and the
action is authorised against a server nobody reviewed.

**This is the honest slice of `ASI04` and it is smaller than the category.** ctrlrun still decides
actions. It never inspects a package, a model, a registry, a build or a signature chain, and
`OWASP-AGENTIC-TOP10.md`'s `ASI04` row keeps its "out of scope" verdict for supply chain at large,
gaining one sentence for what this does cover. §8 refuses the wider claim by name, and the release
item is told not to overclaim the row.

### 4.2 What is pinned

`upstream:` is a new action-entry key, beside `mcp:`, and carries at most two pins.

| Key | Compared against | Shape | Feeds |
|---|---|---|---|
| `tls_cert_sha256` | the SHA-256 of the upstream's **leaf certificate**, DER form | a **list** of `sha256:…` strings | §4.3 checks 1 and 2 |
| `tls_cert_file` | the certificate itself | a path to a PEM file holding one or more certificates | §4.3 check 3 |
| `tool_schema_sha256` | `"sha256:" + hex(SHA-256(canonical_bytes(<the tool's entry in tools/list>)))` | one `sha256:…` string | §4.3 checks 1 and 2 |

**Two TLS keys, because a digest cannot be a trust anchor, and an earlier draft had only the
digest.** §4.3's check 3 makes the pinned certificates the connection's only trust anchors, and
`SSLContext.load_verify_locations` takes PEM: there is no way to hand OpenSSL a hash and have it
validate a chain against it. A deployment that configured only `tls_cert_sha256` had given the
kernel nothing to build a trust store from, so check 3 was unimplementable from the configuration
the document defined.

**The mechanism works, which is why this is a configuration gap and not a dead end.** P8, against a
CA-signed leaf rather than a self-signed one, which is the realistic shape:

```
ssl.VERIFY_X509_PARTIAL_CHAIN available: True
the pinned server   HANDSHAKE OK
a swapped server    REFUSED  SSLCertVerificationError: CERTIFICATE_VERIFY_FAILED
```

A CA-signed leaf loaded through `load_verify_locations(cadata=<PEM>)` with
`verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN` is a valid anchor, and a swap is refused at the
handshake before any request byte.

**An entry that pins by digest alone gets checks 1 and 2 and not check 3**, stated as a limit rather
than discovered, and the startup check (check 1) says so on the line it prints, because a pin an
operator believes is enforcing at the handshake and is not is worse than no pin.

**The certificate, not the public key, and the trade is real.** The key is the right thing to pin,
because a renewal keeps it and a certificate pin fires on every rotation. P4 measured what it costs:
the leaf certificate is reachable in the standard library (`getpeercert(binary_form=True)` on
`ctrlrun.transport.HTTPSConnection`, 781 bytes on the probe's self-signed cert), and extracting the
`SubjectPublicKeyInfo` from it needs `cryptography`, which `pyproject.toml:46` does not declare and
which would land in the wheel of every user who never fronts anything. **A runtime dependency for
one optional key is the wrong trade**, and the dependency rule of `v0.2 §1.1`, `v0.3 §1`,
`v0.4 §1` and `v0.5 §1`, that `pip install ctrlrun` installs nothing but `pyyaml` and `click`, is
not bent for a convenience.

**What answers the rotation cost instead: both keys are plural, and they move together.** An
operator adds the next certificate before the rotation and removes the old one after, and neither
the swap nor the rotation needs a restart in the middle. A single-valued pin would have made every
renewal an outage, which is how a pin gets switched off permanently.

**"Both" is load-bearing and an earlier draft said only the digest.** `tls_cert_file` was added to
§4.2 for check 3 without revisiting this paragraph, which left a rotation that moved only the
documented key. Measured against a real listener:

```
tls_cert_sha256 (list, mid-rotation): ['sha256:b83ee95…', 'sha256:3f0d12a…']
A, file=[a]                            check2 digest admits: True   check3 handshake: OK
B (rotated), file=[a] (not updated)    check2 digest admits: True   check3 handshake: REFUSED
B (rotated), file=[a,b]                check2 digest admits: True   check3 handshake: OK
```

Row two is the outage the list exists to prevent, one layer down. So: **`tls_cert_file` holds every
certificate `tls_cert_sha256` names**, the loader checks that correspondence and refuses a document
where they disagree, and T494 exercises the rotation through **both** keys rather than the digest
alone. A pin whose two halves can drift is a pin that fails at the handshake on a day an operator
believed they had prepared for.

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
new to be true. **Except behind a proxy, and that exception is not small.**

`gateway/transport._observed` refuses the never-connected claim when a proxy is configured, on
purpose and by `v0.7 §12.2.10`'s rule: the `CONNECT` line is a byte written. Measured:

```
_through_a_proxy() with HTTPS_PROXY set: True
  proxied=False -> never_connected     effect_state -> failed
  proxied=True  -> after_request_sent  effect_state -> ambiguous
```

So in a deployment with `HTTPS_PROXY` or `ALL_PROXY` set, which is most of the corporate ones and
exactly the ones that pin upstreams, **a swapped server leaves an `AMBIGUOUS` effect record that
only a human moves**, on every call. That is fail-closed and it is also an operational cost worth
naming: an operator whose upstream is swapped behind a proxy gets a queue of records to adjudicate
rather than a clean refusal.

**Which is an argument for check 2, not against check 3.** Check 2 denies before the connection is
attempted, so a pin an operator keeps current never reaches the handshake at all. §10's row and
T491 are therefore **conditioned on the unproxied case**, and T491b asserts the proxied one reaches
`AMBIGUOUS` rather than pretending otherwise. The effect did not happen, the kernel records it `FAILED`, and the action fails
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
so there is nothing to pin.

**Where that is refused is at the surface's constructor, not at load**, and an earlier draft of this
section said "a **load error**, naming the surface", which is not implementable. There is one
loader: `Policy.from_file` is reached by `Control.from_file` and `@protect` (`control.py:761`), by
`ctrlrun scan` (`scan.py:458`, `:521`) and by `verify`'s worker (`verify/worker.py:69`), and **it
cannot know which surface will later run an action**. The same `ctrlrun.yaml` is loaded by the
gateway, by a decorator-based worker, by `scan` and by `verify`.

**And a load error would have broken §7.3's own exit criterion.** That section requires a shipped
example that pins an upstream and requires G27 to grade `PASS` on it; `verify` loads through the
in-process path, so a document carrying `upstream:` would refuse to load under `verify` and G27
could never be graded on anything this repository ships. §7 already has `verify` seeding an
observation for check 2 in-process, which is precisely the configuration a load error would forbid.

So the rule is: **`AcsControlHook` refuses at construction** with `InvalidArgument` naming the key
and the surface, exactly as `v0.3 §8.4` has it refuse a hook built with no identity provider. There
is no constructor-time analogue for "in-process", because any `Control` may be used in-process, so
§4.4's second half is answered differently below.

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

**So what happens in-process is a refusal at decision time, under `upstream_unverified`.** There is
no earlier moment at which the kernel knows: `Control.execute` is where an in-process action first
exists, and nothing before it distinguishes a `Control` that will be used in-process from one the
gateway holds. That is not a special case, it is §4.5's existing fail-closed row doing its job: the
in-process path observes no upstream, so nothing is verified, so the action is refused. **An
operator who pins an upstream for an action their own code executes directly gets a refusal on
every call**, which is loud, correct, and exactly what the pin says they asked for.

`verify` and `scan` load such a document without error, which is what §7.3 needs, and `verify`
grades G27 by seeding an observation (§7). §8 records what lifting the in-process limit would take.

### 4.5 The refusals

| Reason | When |
|---|---|
| `upstream_mismatch` | an observed certificate hash is in no pinned list, or an observed tool schema hash differs from the pin |
| `upstream_unverified` | the entry pins an upstream and nothing in this process has observed one for it |

Both are `ActionDenied` with their own reason, under the standing rule that `errors.py`'s closed set
already covers every refusal here, which `v0.9` kept for a whole milestone without an exception. At the gateway both return a new JSON-RPC code,
**`-41016` `ctrlrun.upstream_unpinned`**, HTTP 403, with `reason` and `action_id` in `data` as
`-41012` carries them, and **the upstream is never called**. A distinct code earns its keep on
`v0.3 §8.4`'s test: `-41001` means this action is not permitted to anyone, `-41012` means not to
you, and `-41016` means not against **that server**, which a client answers differently from either.

**`-41016`, and the number was checked rather than assumed.** An earlier draft of this section took
`-41013`, which is **already allocated**: `SPEC-mcp-operator.md` §9.3 adds `-41013`
`ctrlrun.not_a_human` and `-41014` `ctrlrun.principal_expired` to `v0.2 §6.10`'s table, and
`gateway/operator.py:112-121` ships `-41013`, `-41014` and `-41015`. **There is one namespace**,
which that section says in as many words, so the range is walked before a number is taken. `-41001`
to `-41015` are allocated at `22c9948`, and `-41016` is the first free one. This document's §1.4
claims every cross-module assertion was probed; allocating a taken number was a one-line grep that
claim did not cover, and the rule it adds is that **a new identifier in a shared namespace is
searched for before it is spent**.

**`upstream_unverified` is the fail-closed half and it is the one to get right.** A pin that does
nothing when nothing was observed is a pin that an upstream can switch off by never being observed.

### 4.6 `ctrlrun.policy/v8`

Bumped **once**, here, by item 3. The key it adds is `upstream:` on an action entry.

**An older reader refuses the document rather than ignoring the key**, for the reason `v0.9 §10.1`
gives about `tasks:`: an older reader that ignored `upstream:` would authorise the action against
any server at all, which is the whole of what the key restricts.

**The refusal takes the shape `policy.py` uses for an ACTION-ENTRY key, which is not `require_v7`'s.**
An earlier draft cited `require_v7` (`policy.py:1176`); that function walks
`document["authority"]["grants"]` and the `break_glass` entries, because `tasks:` and `budgets:` are
**grant** keys. `upstream:` sits on an action entry, so the shape to follow is `_V4_ENTRY_KEYS`
(`policy.py:155`) and `_V5_ENTRY_KEYS` (`policy.py:164`), enforced in the action-entry parser at
`policy.py:1496`. The rationale that makes `require_v7` shared with `authority.py`, that
`v0.3 §8.3`'s `--authority` file carries no policy section, **does not apply here at all**: a
standalone authority document carries no action entries, so there is nothing on that path to gate.

### 4.7 Acceptance tests for item 3

| | Test |
|---|---|
| T489 | A pinned certificate that matches admits the action; the negative control |
| T490 | A swapped server behind the same name is refused at check 2 with `upstream_mismatch` and `-41016`, and the upstream is never called, asserted by a listener that records connections |
| T491 | The same swap at check 3: the handshake fails, `NotExecuted` is raised before any request byte, and the effect is recorded `FAILED` and not `AMBIGUOUS` |
| T492 | A tool whose advertised schema moved under an approved action name is refused; the identical schema is admitted. Both hashes computed through `canonical_bytes` |
| T493 | An entry pinning an upstream that nothing has observed is refused `upstream_unverified`, not admitted |
| T494 | Rotation: two hashes in `tls_cert_sha256`, either certificate admitted, a third refused |
| T495 | The gateway refuses to start on a mismatch, printing observed beside pinned, exiting non-zero with nothing on stdout |
| T496 | `upstream:` in a `ctrlrun.policy/v7` document is a load error naming the key and its consequence |
| T497 | `upstream:` and the ACS hook: `AcsControlHook` refuses **at construction** with `InvalidArgument` naming the key and the surface, as `v0.3 §8.4` refuses a hook built with no identity provider. **Not a load error**: §4.4 shows one cannot be implemented, and would stop `verify` loading the example §7.3 requires |
| T497b | `upstream:` in-process: the document **loads**, `verify` and `scan` read it, and the action is refused `upstream_unverified` at decision time (§4.4) |

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

**The order is declared once, as data, and every check that can refuse declares where it sits in
it.** The enforcing path raises at the first that fails; the observing path records the one
**earliest in the declared order**, whatever order it happened to reach them in.

**The unit of ordering is the POSITION, not the reason**, and that is the correction three review
rounds converged on. A first design ranked the reason. It cannot work, for two structural reasons:
`approval_required` and `precondition_changed` are both members of `BLOCKED_APPROVAL_REASONS` and
enforce mode raises them on **opposite sides** of `_in_scope`, so no single rank for the second is
both above and below `out_of_scope`; and the attempt-ceiling fast path runs in `Control.execute`
before `_secure` is called at all, so its reason outranks everything `_secure` decides. A review
demonstrated three live regressions from the reason-ranked version, on pairs the previous build had
right.

**No check moves**, and that is deliberate: v0.9 aligned three cases by reordering and the three
reorderings produced four regressions between them (§13.8), which is §5.1's whole argument. What
changes is that each `observation.block()` call site names its position, so the observing path can
report the reason enforce mode would raise without either path being rearranged.

**The list starts at `Control.execute`'s entry, not at `_secure`.** An earlier draft said "`_secure`
raising ... and `_observe_secure` recording", which reads as though the sequence lived inside those
two methods. It cannot, and `v0.9 §4.2.1b`'s second named case is the proof: `policy_unapproved` is
decided by `_require_approved` at `control.py:1293`, **above** authority at `:1298`, and it returns
early when observing (`control.py:892-901`), while `_observe_secure` is not called until
`control.py:1526` and does not reach its own copy of the check until `:1609`. By then
`_Observation.block` has kept the first reason it was given.

**P7**, one action tripping both `policy_unapproved` and `no_authority`, same document, the mode the
only difference:

```
enforce: RAISED ActionDenied  reason='policy_unapproved'
observe: ran, receipt result=observed  would_have.blocked_reason='no_authority'
```

**An item that unified only the two `_secure` methods would leave that case exactly as unaligned as
it is today, and its generated pair test would still pass**, because the pair set would be built
from a list that never covered it. That is the false green §5.3 exists to refuse, arriving through
the door this paragraph left open.

**The order to declare is already written down, as a comment.** `control.py:1296-1297` says
`principal_expired -> authority -> policy -> approval -> reservation -> execution`. Item 4's work is
to make that comment the data every refusing check names itself against, extended with the points
`_secure` adds beneath `approval` and with the ceiling fast path above it.

**Nothing is moved to a point**, which an earlier draft of this paragraph asked for. Observe mode's
`policy_unapproved` stays where it is and declares `POLICY_UNAPPROVED`, which is above authority;
the reported reason is then enforce mode's without either path being rearranged.

**So this item's extent is `Control.execute` and the two `_secure` methods**, and §5.4's "two
methods" is amended here rather than left to contradict this paragraph. Moving one check inside
`execute` is what the declared order requires; it is not the reorganisation of `control.py` that §8
forbids. The boundary is stated so an item does not have to guess: **item 4 may move a check to the
point the declared order gives it, and may move nothing for any other reason.**

**This item changes no behaviour an operator asked for**, and that is worth stating before the tests
rather than after: its whole value is in what it makes impossible. A sixth unaligned case cannot
arise, because there is no second order for one to differ from.

**And `v0.9 §4.2.1b`'s "two known-unaligned cases" is three.** That section named two; P7 above is
the second of them, measured, and it is the one that needs the whole path rather than `_secure`. An
item that treats the list as closed at two will generate a pair set that omits it. The cases are
the test cases and not the goal:

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
again. This item unifies one declared order across `execute` and the two `_secure` methods (§5.2),
and does not move anything else out of
the file.

**It does not change observe mode's contract.** Observe mode still charges nothing (`v0.9 §4.2.1`),
still refuses nothing, still writes no `denied` receipt, and `v0.9 §4.2.1a`'s limit about sizing a
budget from an observed run is untouched.

### 5.5 Acceptance tests for item 4

| | Test |
|---|---|
| T498 | The generated property: over every constructible pair of refusals, enforce's raised reason equals observe's `blocked_reason`. The pair set is asserted non-empty and its size is reported |
| T499 | `v0.9 §4.2.1b`'s first named case: out of scope and awaiting approval, both modes name the same reason |
| T499a | P7 as a test: an action tripping both `policy_unapproved` and a later refusal names `policy_unapproved` in **both** modes. The case that proves the list starts at `execute`'s entry, and the one a `_secure`-only refactor leaves broken while every other pair goes green |
| T500 | Its second: `policy_unapproved` against a later refusal, both modes name the same reason |
| T501 | Every pair the generator could not construct is named in the test's own output, and the list is asserted against the declared order so a shrinking pair set fails red |
| T502 | The four v0.9 regressions as regression tests. **Two already existed under their own numbers** when this was audited at release: the `effect_key` on the budget-refusal event (`test_T461...`) and the picklability of `_UnmeasurableError` (`test_T462...`). The other two are `T502a`, the resumed *observed* receipt's spend — `T439` covers observe, `T447` covers resume, and neither covered the two together — and `T502b`, the doubled `ACTION_DENIED` on a resumed observed leg, which is the row that had no test under any name |

T502's last row is the one nothing in this repository would otherwise catch, which is why §13.8
named it, and it belongs to this item because this item rewrites the code that broke it.

**A number, not a property, is what was owed here.** The audit that closed this found two of the
four rows already tested under other numbers, which is the same defect §9.4 describes in a
different place: a table that describes the tree, checked by a human reading both. `T502b` is the
one that was genuinely absent, and it is absent-shaped for a reason worth keeping — every
assertion near it checked that the event *appeared*, and an event that appears twice appears.

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
| `schema` | `ctrlrun.hop/v1`, so a reader that is handed one document knows which shape it got. `ctrlrun inspect` without `--hop` answers about an **action** and `--hop` answers about an **authority record**, and the two are not interchangeable |
| `root_id` | the grant the walk ended at, so an operator can name the root without re-walking |
| `missing_parent_id` | the record the store could **not** read, or `null`. A chain that cannot be verified is refused rather than trusted (§3.2), and this is the id that says where it stopped |

**`chain[]` carrying which dimensions narrowed at each step is the part that answers the question.**
An operator looking at a refused action knows the chain is valid or it is not; what they cannot see
today is which link took the resource away. This renders it once per step.

**Plural, and it needs a helper that does not exist.** An earlier draft said "the dimension on which
each step narrows", singular, which has no defined answer: T470 is "a hop that narrows on **every**
dimension", so a step routinely narrows on several at once. And `contained_dimension`
(`authority.py:889`) computes the **complement** of what is wanted: it returns the first row the
child *violates*, or `None` when the child is contained. There is no function anywhere in the tree
that answers "which rows did this step make strictly stricter"; the seven call sites of
`contained_dimension` (§2.2) are all asking the other question.

So §9 carries a row for `narrowed_dimensions(parent, child) -> tuple[str, ...]`, returning the
subset of `DIMENSIONS` on which the child is strictly stricter, in `DIMENSIONS` order. **It is a
reporting helper and it decides nothing**, which is the line that keeps §2.2's no-second-relation
rule intact: `contained_dimension` remains the only thing any decision calls, and a build in which
`narrowed_dimensions` disagreed with it would be wrong about a rendering and not about an
authorization. The two are tested against each other on the same pairs so the disagreement is still
caught.

**Its own document rather than a key in `ctrlrun.inspection/v2`**, on `v0.9 §10.1`'s argument for
`ctrlrun.budget/v1`: that one answers about an **action** and this answers about an **authority
record**, and a reader handed one would have to know which shape it got before it could read either.

### 6.3 One command from the refusal to the thing that explains it

**Every refusal that names a delegation prints the command with its argument filled in**, not with a
placeholder. `authority_hop` prints the presented id, `authority_revoked` prints the revoked one,
and `authority_escalation` with `missing_parent_id` prints **the presented hop**, naming the
unreachable ancestor in the prose beside it.

**That last row is not a detail, and an earlier draft got it backwards by printing the missing id.**
The id `missing_parent_id` carries is by construction the record the store could **not** read, so
`ctrlrun inspect --hop <that id>` is the unknown-id path and exits non-zero with nothing on stdout
(§6.5's T504). A refusal whose one suggested command is guaranteed to fail is worse than no
suggestion: it sends an operator to a dead end and teaches them the line is noise. The presented hop
**is** readable, its `chain[]` is what shows where the walk stopped, and that is the thing the
operator needs.

```
denied: stripe.refund  authority_escalation
        dlg_5c44df6177f0a1b2c3d4e5f60718293a is live, but dlg_a3da9cb912f04e7788b1c5d6e7f80912
        above it could not be read
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
them present, which is the fact and not a verdict: `SPEC-v0.4.md` §3.9's rule that ctrlrun never
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
| G26 | `a hop is named on both sides` | 28 | the two ids compared and equal | no grant in the document is delegable |
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

**G26's title says "named on both sides" and not "both receipts name one hop"**, which is what an
earlier draft called it. The two ends of a hop are not always two receipts: a relay's receipt names
the hop it acted under, so the hop it *created* is named by `DELEGATION_CREATED` and that event's
`action_id` (§3.4.4). **G26's scenario MUST be built over a chain with a middle**, not over a single
issuer and a single receiver, because the single-link shape grades a pairing that was never in doubt
and would pass on a build where the relay case is broken. That is §7.1's rule applied to G26 rather
than restated for it.

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

**Which grant, and why it is not a coin flip.** Round one of the review called this construction
unimplementable, on the grounds that `verify` cannot mint a root grant into the operator's document
and that a sibling delegation's id is `secrets.token_hex`, so `min(passed, key=_by_grant_id)` would
be choosing between two random ids and a pre-§2.3 build would pass about half the time. **Round two
built the shape and measured it**: for the *wider* action only the receiving principal's own grant
passes, so `min()` has one element and a build with no §2.3 rule admits it **every** run, not half.
The detection is deterministic.

The construction is therefore: a second hop to the same receiving principal, wider than the first,
and the narrow one presented. **Both orders are asserted anyway**, because the cost is one loop and
the alternative is trusting an argument about `min()` over a set whose size depends on which action
the scenario picked.

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
- **In-process upstream pinning**, and **pinning at the ACS hook.** §4.4. The ACS hook refuses at
  construction; in-process the action is refused `upstream_unverified` at decision time. **Neither
  is a load error**, which §4.4 shows cannot be implemented from one loader that cannot know which
  surface will run the action.
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
- **Tracing a relay's created hop to the action that created it, without the creator's help.** §3.4.4:
  `action_id=` is supplied or it is not, and an ambient current-action variable is refused because it
  is `<unset>` on a worker thread and fails in the evidence direction.
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
| `hop=` on `Control.evaluate` | **also amends a frozen signature**, for `v0.9 §10.3`'s reason: without it `Control.evaluate` and `Control.execute` disagree about a hop-selected grant |
| `hop=` and `task=` on `ctrlrun.adapter.needs_approval` | the row above closes the disagreement one frame too shallow, and an earlier draft stopped there. `needs_approval(control, action, arguments, *, resource=None)` (`adapter.py:428`) ends `return control.evaluate(proposed).decision is Decision.APPROVE`, takes no task and no hop, and is the public pre-invocation predicate for the OpenAI Agents SDK shape. Without them the predicate evaluates against the receiver's whole candidate set while `execute` evaluates against the hop alone, so it answers "no human needed" for a call `execute` then refuses. Its own docstring makes the argument for `resource=`: "a predicate that skipped it would evaluate a different action from the one that runs" |
| `hop` and `task` read from caller metadata at the gateway and the ACS hook | §3.1.2. Neither surface reads any caller metadata for authorization today and neither threads `v0.9`'s task, so without these rows item 1 cannot wire the two surfaces where a hop actually arrives over a wire |
| `action_id=` on `Control.hop`, and `action_id` on `DELEGATION_CREATED` | §3.4.4. The event is action-less by construction (`control.py:4316`), which is true of `ctrlrun delegate` from a shell and false of a hop created mid-action. Without it a relay's created hop is linked to the action that created it by nothing but a timestamp, and §1.2's rule 3 is false across every middle link |
| `Control.hop(parent_id, grant, *, by) -> Delegation` | `Control.delegate` hardcodes `via="api"` (`control.py:3863`) and the private `_delegate` takes `via`. Exposing `via=` publicly would let API code write `"cli"` or `"break-glass"` into `created_via`, which is **evidence about which surface acted**, and a caller that can forge it makes the field decorative. A method whose name fixes the value cannot |
| `CreatedVia` gains `"hop"`, from three values to four | **an exported type alias whose value changes** (`authority.py:124`), read by `_CREATED_VIA` at parse time (`authority.py:834`), which raises `_UnreadableError` on a value it does not know. **The blast radius of an older binary meeting one is the whole deployment, not one delegation**: see §9.3, which is not a footnote |
| `Receipt.hop` | §3.4. One field, the id both sides name |
| `data.hop` and `data.task` on `EXECUTION_STARTED` | §3.4.2. The event's `data` is `{}` today (`control.py:1487`, `control.py:1561`); it becomes the durable binding `_resumed_context` reads back, which is what lets a resumed leg be evaluated on both dimensions instead of skipping them |
| `AuthorityResult.hop` | **amends a `v0.3 §11` frozen shape**, and is recorded here rather than slipped in. `AuthorityResult`'s fields are exported and none of them can hold a presented id that names **no** delegation: `grant_id` and `delegation_id` both mean "the grant that decided", and reusing either would make `_authority_data` write `delegation_id` and `depth` for a record nobody has. Present on every result an action under a hop produces, passing or failing, which is what lets §6.3 print a command for each |
| `AUTHORITY_HOP` in `REASON_PRECEDENCE` | `REASON_PRECEDENCE` is an exported tuple and `evaluate`'s only path from a collected failure to a return walks it; a reason absent from it falls through to `no_authority`, which is the refusal §2.3.3 exists to stop reporting. It sits **above `no_authority` and below every reason that inspects a grant the hop actually reached**, because those are more specific |
| `AUTHORITY_HOP` (`"authority_hop"`) | a presented hop that names no live delegation addressed to this principal is not any existing reason: it is not `no_authority`, which means nothing matched, and not `authority_escalation`, which means a chain step failed. §2.3.2 rule 3 forbids using it as a bucket for either |
| `UpstreamPin`, and `upstream` on an action entry | §4.2. `McpOptions` (`policy.py:543`) carries per-tool assertions an operator makes about their upstream and is the closest existing name; it holds claims about **behaviour** (`not_executed_on_error`) and this holds claims about **identity**, and merging them would put an authorization input in a structure whose documented job is a `NotExecuted` hint |
| `unmatched_shape(grant, action)` | §2.3.2 rule 2. `matches_shape` (`authority.py:534`) answers **whether** a grant covers an action and discards **which** of subject, actions, resources or environments failed, which is the value `data.dimension` has to carry. `matches_shape` becomes a call to it, so the two cannot drift |
| `narrowed_dimensions(parent, child)` | §6.2. `contained_dimension` answers which row a child **violates**; no name in the tree answers which rows it **narrows**, which is what an operator reading a chain needs. A reporting helper that decides nothing, so §2.2's one-relation rule is untouched |
| `UPSTREAM_MISMATCH`, `UPSTREAM_UNVERIFIED` | §4.5. Two reasons, separately observable, because "the server changed" and "nobody has checked" are different findings and an operator fixes them differently |
| `ssl_context=` on `ctrlrun.gateway.transport.request` | §4.3's check 3 cannot be implemented without it: `request` builds its client as `httpx.Client(timeout=timeout, follow_redirects=False)` (`gateway/transport.py:155`) and accepts no context, no verify argument and no client. A module-level default is refused rather than omitted: one context set globally pins every caller of this module to one certificate, and the gateway fronts one upstream while the ACS hook and in-process callers share the module |
| `-41016` `ctrlrun.upstream_unpinned` | §4.5, on `v0.3 §8.4`'s test for `-41012`: a client answers "not permitted to anyone", "not permitted to you" and "not against that server" three different ways |

### 9.4 Three rows in this table did not ship, and the table said they had

Written at release, against the shipped tree, because §9 is the section a reader trusts for the
public surface and **a frozen name that names nothing is worse than a missing row**: a missing row
is a gap, and a wrong one is an answer.

This is §11's finding in its sharpest form. Every row above was justified before it was code, and
three of them were then not built. Nothing turned red, because **no test in this repository asserted
that a name §9 freezes exists.** The rule §11 states covers a sentence about a later item; these are
sentences about *this document's own frozen table*, and they need the same discipline.

**Two of the three are now built rather than recorded.** A gap that can be closed is closed; only
the one whose *name* was wrong and whose *reason* was right stays as a row.

| Row | Disposition |
|---|---|
| `hop=` and `task=` on `ctrlrun.adapter.needs_approval` | **Built.** The signature is `(control, action, arguments, *, resource=None, task=None, hop=None)` and both are threaded into `Control.evaluate`. Without them the predicate evaluated against the receiver's whole candidate set while `execute` evaluates against the hop **alone** (§2.3), so it answered "a human is needed" for a call `execute` then refuses: the framework surfaces an approval item, a human says yes, the call fails anyway. **It was never an authority hole** — `Control.execute` is the enforcement point and refuses either way, so nothing wider ever ran; what it cost was the framework's own approval item and a receipt nobody could explain. `T128c` drives both and asserts they agree, with the no-hop case as its negative control |
| `ctrlrun.hop/v1`'s key set (§6.2) | **Fixed in §6.2**, which listed eight of the eleven keys the document carries. `schema`, `root_id` and `missing_parent_id` are now in the table. Under-describing is the safe direction for a reader and the wrong one for a schema somebody writes a consumer against. `T506b` parses the table out of this file and compares it to the emitted document, so the two cannot drift again; `ctrlrun.hop/v1` is not re-cut, because nothing it emitted changed |
| `ssl_context=` on `ctrlrun.gateway.transport.request` | **Not built, deliberately, and this row is why.** Check 3 shipped on `ctrlrun.upstream.observe_upstream(url, *, verify=...)` and on the forwarder's `verify`; `transport.request` still takes no context, no verify argument and no client. The row's *reason* held and its *name* did not: its objection to a module-level default — one context pins every caller of a shared module to one certificate — is answered by putting check 3 where the gateway builds its forwarder, which is per-gateway rather than per-process. Adding the parameter now would add a second way to configure the same pin |

**The test this asked v0.11 for is in the same commit as this section**, because asking a later
milestone for it would be the exact mistake the section is about.
`test_every_v0_10_name_the_spec_freezes_is_importable_with_the_parameter_it_names` walks the rows
and checks each name imports with the parameter its row gives.
`test_the_row_of_section_9_that_did_not_ship_still_has_not` pins `ssl_context=` in the other
direction, so building it later fails there and this row comes out in the same commit: **the
document and the tree are wrong together or right together, never one of each.**

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

**No new module.** The hop is `authority.py` and `control.py`. The pin is `policy.py` for the key,
**`control.py` for check 2's decision** and `gateway/` for checks 1 and 3; an earlier draft of this
line said "`gateway/` for the three checks", which contradicted §4.3's own table, where check 2 is
`Control` comparing the pin against what was observed. The `DENY` lives where every other `DENY`
lives. The ordered list is `control.py`; the surface is `reporting.py` and `cli/`. A reorganisation of `control.py` is out of scope (§8) and does not become in scope as a side
effect of item 4 rewriting two methods inside it.

### 9.3 The first hop makes 0.10.0 a one-way upgrade, and §9's "no migration" does not soften it

**Stated as its own subsection because §9's migration paragraph reads as though the absence of a
migration made rollback cheap.** It does not, and the mechanism is worse than v0.9's one-way
migration `0007`.

`CreatedVia`'s vocabulary is closed. `_delegation_from_record` (`authority.py:834`) looks the stored
string up in `_CREATED_VIA` and raises `_UnreadableError` on a value it does not know, and
`Authority._candidates` (`authority.py:1608`) reads **every** delegation row before filtering any of
them, so one unreadable row aborts the whole evaluation. `authority.py:118-123` already says so, in
the source, in as many words: a value outside the vocabulary "makes `_candidates` raise and answers
`authority_unreadable` for **every action in the deployment**", and a deployment that wrote rows a
reader does not know "would deny everything, which is fail-closed and useless".

**So the moment the first hop is written, 0.9.x can no longer run against that store**, and not for
the delegation it cannot read: for every action by every principal. `v0.3 §4.6` makes an unreadable
delegation a denial rather than a skip, deliberately, so this is the fail-closed direction working
as designed and it is still a deployment that stops.

**The release item carries this in the notes**, beside the upgrade check it already owes, and the
note says the thing operators need: the irreversible step is **creating the first hop**, not
installing 0.10.0. A deployment that installs and creates none can still roll back.

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
| A relay agent presents one hop and creates another in the same action | its receipt's `hop` names the hop it **acted under**; the created one is evidence as `DELEGATION_CREATED` (§3.4.4) |
| An action entry pins an upstream and nothing has observed one | **refused**, `upstream_unverified` (§4.5). Never admitted |
| An observed certificate hash is in no pinned list | **refused**, `upstream_mismatch`, `-41016`, upstream never called (§4.5) |
| A swapped upstream at handshake time | **`NotExecuted` before the first request byte**, effect `FAILED`, not `AMBIGUOUS` (§4.3) |
| An advertised tool schema moved under an approved action name | **refused**, `upstream_mismatch` (§4.2) |
| `upstream:` in a `ctrlrun.policy/v7` document | **load error**, naming the key and its consequence (§4.6) |
| `upstream:` on an action reaching the kernel through the ACS hook | **`InvalidArgument` at `AcsControlHook`'s construction**, naming the key and the surface (§4.4). Never a load error: one loader cannot know which surface will run the action |
| `upstream:` on an action reaching the kernel in-process | **refused at decision time**, `upstream_unverified` (§4.4, §4.5). The document loads, so `verify` and `scan` can read it |
| An older binary meets a `created_via` of `"hop"` | **every action in the deployment is refused** `authority_unreadable`, not just that delegation: `_candidates` reads all rows before filtering and raises (§9.3). Fail closed, and a rollback that stops the deployment |
| Observe mode and enforce mode reach different refusals | **cannot arise**: one declared order, walked by both (§5.2). This row is a MUST and §5.3 is its proof obligation |

---

## 11. What building v0.10 settled

*One subsection per question the drafting could not close, each stating what the code decided and
which section carries it. `SPEC-v0.4.md` §12 through `SPEC-v0.9.md` §13 are the format.*

### 11.0 What the milestone settled about itself

**Every sentence this document wrote about what a later item would do was wrong.** That is the
finding, and it is sharper than v0.9's because it names a shape rather than a rate.

`SPEC-v0.9.md` §13.0 established that a specification is reliable on facts checkable by reading one
line and unreliable on facts that need a value followed through two modules, and this document took
that seriously: three review rounds ran, **every one of the 44 line citations round three checked
was exact**, and §1.4 recorded two probes that changed the draft before it was written. The
citation discipline worked.

What broke was a different class, and round three named it: **a sentence about what a later item
would do.** §3.4.3's "one residual, until item 2 lands". §4.3's check 2, described in a table as
though it had a source. §5.2's "move observe mode's `policy_unapproved`". §6.4's `scan` line.
§4.2's "the loader checks that correspondence". Each was written as settled prose about code that
did not exist yet; each shipped differently or not at all, and nothing turned red in between.

**The rule that follows, and it is this milestone's contribution to the series: a spec sentence
whose truth depends on a later item is a test that item owes, named in that item's table, or it is
not in the document.** A residual that expires when another PR merges needs a red test at the flip,
not a note.

### 11.1 The mutation table found three guards nothing exercised, and the independent reviews
found what the mutation table could not

Items 2, 4 and 5 each shipped a guard no test covered, each caught by mutating the source. That is
v0.9's lesson holding. **What it did not catch is the class that mattered most**, and the three
independent reviews did:

- a **reason-ranked** decision order, which cannot express enforce mode's real order because the
  approval axis straddles the scope check and the ceiling fast path sits above `_secure`
  entirely. The review demonstrated three live regressions on pairs the parent commit had right;
- `Control.resume` dropping the hop one frame below where it recovered it, so a receiver holding
  any grant of its own kept its reservation **after the hop was cut**;
- **nothing in the product observing an upstream**, so two of §10's rows described outcomes no
  shipped code path could produce.

Each is a claim that crosses two modules, and each was invisible to a test written by the person
who wrote the code. The standing rule that an independent reviewer reads **every file that calls
into the changed code, not just the diff**, is what found all three, and `v0.3`'s own record says
why: a self-review of that spec found four defects, and an independent one found two authorization
holes visible only from a file the spec did not mention.

### 11.2 A test can be green, mutated, and still prove nothing

Item 4's T498 was generated, non-empty, and asserted the wrong thing: it compared
`_Observation.block` against the same rank function `block` itself calls. A review inverted the
declared order at §5.1's own first named case and **all 4,062 tests stayed green**.

The rule §5.3 already carried, restated because it needed teeth: a property test over the kernel's
internals is a restatement of an implementation. **T498 drives `Control.execute` in both modes**,
and the acceptance criterion for it is that inverting `DECISION_ORDER` turns something red.

### 11.3 The order is a property of positions, not of reasons

§5.2 prescribed one ordered list both modes walk, with checks moved to declared points. Item 4
shipped rank-selection instead, on the sound argument that v0.9's three reorderings produced four
regressions and a change that moves nothing cannot regress a position. **That argument was right
about the risk and wrong about the mechanism.**

A reason cannot carry the ordering, for two reasons that are structural rather than incidental:
`precondition_changed` and `approval_required` are both members of `BLOCKED_APPROVAL_REASONS` and
enforce mode raises them on opposite sides of `_in_scope`, so no single rank for the first is both
above and below `out_of_scope`; and the attempt-ceiling fast path runs in `execute` before `_secure`
is called at all. So the **call site declares where it is**, and the reason travels as evidence.

### 11.4 What v0.10 did not close

Stated here rather than discovered by a reader.

- **A receiving agent can decline to present the hop it was given.** §2.3.2. The kernel records
  which happened and §6.4's `scan` line makes the deployment rule checkable; it cannot compel the
  choice.
- **A receiver holding two hops presents whichever it likes.** §3.1.1, and the task that would
  narrow it arrives from the caller too.
- **Cross-store propagation.** §3.2, forced by rule 2 and refused fail-closed.
- **A hop created inside an action is not named on its creator's receipt.** §3.4's issuer row
  describes it; `Receipt.hop` carries the hop an action ran *under*, and the created one is named
  by `DELEGATION_CREATED` and its `action_id`. Rule 3 holds through the event, not the receipt.
- **`Receipt.hop` is read from a context variable at receipt time**, so a nested `evaluate` inside
  an executor overwrites it. Pre-existing in class for `Receipt.task` since v0.9.

