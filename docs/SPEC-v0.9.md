# SPEC-v0.9: Envelope

**Status:** draft. Written against `main` at `acf45c0`, which is 0.8.0 released and tagged.

A delta over `SPEC-v0.1.md` through `SPEC-v0.8.md`, all eight of which remain binding. Where this
document and a build plan disagree, this document wins. Where this document is silent, the eight
before it are not.

v0.8 asked who may say yes, and whether the kernel can tell. v0.9 asks the question `VISION.md` §5
has had no code under it since the beginning: **how much, over which records, for which task?**

Everything shipped so far decides one action at a time. A grant says `amount_lte: 5000`, and
`Authority.evaluate` compares one action's arguments against it and returns. Nothing counts. A
thousand actions each passing that constraint are a thousand passes, and the kernel has never had a
sentence to say about the thousandth that it did not say about the first. That is the qualitative
half of authority, and it is complete. v0.9 is the quantitative half, and it is the first milestone
whose central object is a number that moves.

---

## 1. Scope

Six deliverables, in build-list order.

| Item | Section | Guarantee |
|---|---|---|
| 1. Task-bound authority | §6 | G24 |
| 2. Scope providers | §5 | G23 |
| 3. The budget in the document | §2 | none |
| 4. The ledger, and the store amendment | §3 | none |
| 5. Consumption, reconciliation and release | §4 | G22 |
| 6. The operator surfaces | §7 | none |

Item 1 lands first because it bumps `ctrlrun.policy/v7` and `ctrlrun.guarantees/v5`, which
everything downstream reads. Items 3, 4 and 5 are one stack and do not reorder: item 4 cannot be
written before item 3 decides what a budget is, and item 5 cannot be written before item 4 decides
where the charge happens.

### 1.1 What this milestone is not, stated before anything else

On the pattern of `v0.4 §1.2`, `v0.5 §1.1`, `v0.6 §1.1`, `v0.7 §1.1` and `v0.8 §1.1`, because a
milestone about limits attracts more scope than any before it.

- **Not a rate limiter.** A rate limiter protects a service from load. A budget bounds authority,
  is evidenced in a document somebody reviewed, attenuates down a delegation chain, and holds its
  consumption through an unresolved outcome. The two are different objects that happen to count.
- **Not a quota service.** Nothing is served, nothing is published, no endpoint answers "how much is
  left". `v0.3 §1.1`'s rule that ctrlrun consumes and issues nothing is not relaxed here.
- **Not a consequence taxonomy.** A budget names a metric. The kernel does not know what `amount`
  means, does not know which of two actions is more serious, and does not rank, score or grade.
  Grading an operator's actions is the same claim `v0.4 §3.9` refuses to make about their policy.
- **Not compensation, and not a saga.** Nothing is undone. A budget refuses the next action; it has
  never had an opinion about the last one.
- **Not a fleet-wide budget across stores.** One store, one ledger. Two deployments sharing a
  provider account share nothing here, which is the same answer `v0.7 §4.6` gave for idempotency
  tokens and for the same reason: the kernel's consistency claim stops at its store's transaction.
- **Not a spend report, a forecast, or a trend.** §7 shows an operator what was consumed and what is
  held. It does not project, alert, or compare a period to the one before it.
- **Not a recall.** A budget cannot stop an action already in flight when a window rolls. The
  roadmap says so in its own "does not close" line and §4.6 says it again where somebody
  implementing reconciliation will read it.
- **Not an inference of which task an agent is on.** Nothing reads a prompt, an argument name, a
  heuristic or a model output. The task id arrives from the caller or the action is refused (§6.3).
- **Not a defence against a scope provider whose source is wrong.** A scope provider is worth
  exactly what the system behind it is worth. §5.8 states this as the ceiling on every claim §5
  makes, and it is not a caveat on the feature, it is the feature's boundary.
- **Not scope or task enforcement on the gateway and ACS paths.** Both build an `Action` and call
  `Control.execute` with no `scope=` and no `task=`, so neither check reaches them in v0.9. §5.2.2
  and §6.3.2 have the rows; giving those paths a provider is a gateway configuration surface and its
  own amendment.
- **Not a detector of a hijacked agent.** Task binding shrinks what a hijacked agent can do. Nothing
  in v0.9 reads anything to notice the hijack, and `ASI01` stays partial.

### 1.2 The rules of v0.9

Four that are new, and one inherited that makes the milestone safe to ship.

**R1. Consumed on reserve, in the same write.** A budget that is checked on one line and consumed on
another is a check-then-act race: two processes read the same remaining amount, both pass, both
spend. This is `v0.1 §5.3`'s rule for the effect itself, applied to the counter. It is why item 4 is
its own item, and it is the only reason `StateStore`'s freeze is touched (§3.3).

**R2. Ambiguity is not a refund.** An `AMBIGUOUS` effect keeps its consumption until a human or a
`reconcile` hook resolves it. If ambiguity released the hold, an agent that can generate ambiguity
could generate unlimited authority, and generating ambiguity is free for any flaky integration. This
is the correctness hole that parked budgets for four milestones, and §4 is where it is closed.

**R3. A budget names a metric, not a consequence.** The kernel sums a number it was pointed at.
§2.3 says where the number comes from, and §12 says what a metric is never allowed to become.

**R4. A scope provider answers a question; it does not detect a change.** `v0.7 §6`'s fingerprint
proves the world did not move between request and consumption. A scope provider answers whether this
record is in this principal's assigned scope. They share an ordering argument and a canonicalizer,
and they are not the same mechanism. §5.2 says exactly which parts are shared, and §5.7 says what a
deployment configuring both does, because an operator will configure both.

**R5, inherited from `v0.3 §1.2`. Opt in, then fail closed.** A grant carrying no budget, no scope
and no task behaves exactly as 0.8.0 did: nothing is counted, no ledger row is written, no
transaction is widened, and G22 to G24 report `N/A` with that reason. A grant carrying one gets no
partial mode. §6.4 records the one decision in this milestone that could change behaviour for an
existing grant, and what was done about it.

### 1.3 What was read

`authority.py` end to end, with attention to `Grant.__post_init__`, `contains`, the containment
rules and every dimension they cover, `_check_chain`, `_parent_for_creation`, `_walk`,
`_canonical_grant`'s closed field list, `canonical_grants`, `Authority.evaluate` and
`Authority.envelopes`. `control.py`'s `_secure`, `_presented`, `_take`, `execute`, `resume` and
every path reaching a `reconcile` hook. `state.py`'s `StateStore` protocol, `_authorize_and_reserve`,
`_plan`, `_reserve_locked`, `_transition`, `_transitioned`, `_resolvable`, and `migrations.py`.
`effect.py`'s `plan_reservation` in full. `postgres.py`'s `_authorize_and_reserve`, every explicit
`BEGIN`, `_resolve_lost_insert`, `_resolve_lost_renewal`, and the comment at `postgres.py:1106`.
`receipt.py`, `policy.py`'s authority loader and schema gate, `cli/main.py`'s `effects`, `inspect`,
`resolve` and `verify`.

### 1.4 What reading the code changed

Four things, each of which moved a decision this document would otherwise have got wrong.

1. **`plan_reservation` already contains the ledger's state machine.** §4.2 was going to give the
   ledger its own states. It does not need any: `effect.py:248-315` is the complete table of exits
   from a reservation, and a single rule over it ("released exactly on `FAILED`") covers every one.
   The ledger has no state machine, it has an invariant.
2. **`FAILED` is the only state that reserves the same effect key twice**, with `renews=True` and
   `attempt+1`. So "one effect key charges once" is wrong as stated, and §4.3 states it correctly:
   one effect key **holds at most one charge at a time**, and a retry after `FAILED` charges again
   because the release already happened and the previous attempt provably did not occur.
3. **Postgres's commit can itself be ambiguous**, and `v0.6 §4.3.2`'s two tables resolve it by
   re-reading, re-inserting once, or re-issuing an `UPDATE` once. A ledger that decremented a
   counter would double-charge on the A1 re-insert branch and double-release on the A2 re-issue
   branch. §3.4 makes the insert idempotent on a key and §4.4 makes release a compare-and-set on a
   flag rather than a decrement, and both are consequences of that table rather than general good
   practice.
4. **The same-transaction requirement is not only about the race.** Because the charge rides inside
   the reservation's transaction, an ambiguous commit is resolved for the charge by the same single
   re-read that resolves it for the reservation. Split them, and `v0.6 §4.3.2` needs a third table
   nobody has written. §3.3 uses this as the second half of its argument, and it is the stronger
   half.

---

## 2. Consequence budgets

### 2.1 The sharp case

A grant permits `payments.refund` with `amount_lte: 5000`. An agent issues four hundred refunds of
4,999 in eleven minutes. Every one of them is authorised, every one produces a valid receipt, and
the evidence trail is complete and correct about each. There is no sentence in v0.1 to v0.8 that is
false, and nothing refused anything. That is the whole of the problem, and "how much" is the whole
of the answer.

### 2.2 The shape

A budget lives on a grant, in the document, under the policy hash.

```yaml
authority:
  grants:
    payments-agent:
      subject: {agent: "payer"}
      actions: ["payments.*"]
      constraints: {amount_lte: 5000}
      budgets:
        - metric: amount        # `count`, or the name of an action argument
          limit: 100000         # what the sum may reach, exclusive of the action that would exceed it
          window: PT24H         # rolling, see §2.5
```

**A budget is a list, not a mapping**, because two budgets on one metric over two windows is the
first thing an operator asks for (100,000 a day and 500,000 a month) and a mapping keyed by metric
cannot express it. Every budget in the list must pass; the first that refuses is the one named in
the refusal, and the list order is the document's.

**`Grant.__post_init__` validates exactly what the loader validates.** Its own docstring gives the
reason and it applies here unchanged: `Control.delegate` takes a `Grant` built in Python, and §2.6's
containment relation is undefined on a budget whose window is negative or whose limit is a string.
Without constructor validation, containment would be discharging a proof about a value nothing
checked.

### 2.3 A metric is a name, and `count` is the one the kernel supplies

`count` is always available and always means one per action. It is the budget an operator reaches
for first and the only one whose meaning does not depend on the document.

**Which makes it the one metric the kernel does branch on, and §12's do-not-build line has to say
so rather than be contradicted by it.** The line forbids a branch that *ranks* or *classifies*
metrics: a taxonomy, a default limit for a name the kernel thinks it recognises, an opinion about
which of two is more serious. `count` is none of those. It is a second **source** for the number,
the way a metric naming an argument is a source, and item 5 resolves it in one place with no
ordering over names.

**An action argument literally named `count` does not win**, and it is worth saying which does: the
kernel-supplied meaning takes it, because an operator writing `metric: count` means "how many", and
a document that could silently retarget it at an argument would make the one metric whose meaning
does not depend on the document depend on it. An operator who wants to sum an argument called
`count` renames the argument.

Every other metric names an **action argument**, by name, and its value is summed. `metric: amount`
sums `action.arguments["amount"]`.

**An action that does not carry the argument, under a grant that budgets it, is refused.** Not
treated as zero. Treating a missing field as zero turns the absence of a value into unlimited
authority, which is the sentence `v0.3 §5.4` exists to refuse on the constraint side, and the
argument is not weaker here because the dimension is quantitative.

**The value must be an integer.** `v0.1 §2.3`'s `PlainValue` is
`str | int | bool | list | dict | None`, and `action.py:18` carries the reason in the source as
"Note the absence of float": a budget summed over floats would drift, and a drifting authority limit
is worse than none. `Decimal` is not in that set either, so it is not an accepted metric value and
this document does not add one: a decimal metric would need a canonical representation, a rule for
how it hashes, and receipt coverage for both, and that is a `v0.1 §2.3` amendment rather than
something §2 may decide on its own.

**And it must be a non-negative `int` that is not a `bool`.** `PlainValue` admits `bool`, and in
Python `isinstance(True, int)` is `True`, so `amount: false` would be a "non-negative integer" whose
value is zero. That is §2.3's own sentence about absence-as-zero wearing a bool's clothes. This
repository already guards the trap in two places: `authority.py:1491`
(`not isinstance(depth, int) or isinstance(depth, bool)`) and `verify/scenarios.py:378-379`'s
`_is_int`. The budget loader uses the same predicate.

**And non-negative, for two reasons.** A negative value would *reduce* the rolling sum and therefore
always pass §3.3.1's predicate, so an agent alternating `+100000` and `-100000` spends without
bound. This is not hypothetical arithmetic: `examples/policies/payments.yaml:29` already carries the
comment "a refund of a negative amount is a charge", which is why every band in that document binds
**both** ends. A budget that admitted negatives would be compensation, reachable by an attacker, and
§12 puts compensation out of scope.

**The §2.6 proof depends on this**, which is the sharper reason. Its step "any interval of length
`window_p` sits inside some interval of length `window_c`, whose sum is at most `limit_c`" needs the
sum to be monotonic over nested intervals, and a sum is monotonic only over non-negative terms.
Permit one negative value and the child's predicate stops implying the parent's, and §2.6.1's whole
table loses its justification.

**So money is budgeted in minor units**, which is what `examples/authority/payments.yaml` already
does with `amount_lte: 5000`, and §7's surfaces render it the way the rest of the document renders
that constraint. A string that looks like a number is a refusal and not a coercion, on the same
rule: `"100.50"` is a decimal wearing a string's clothes, and coercing it would put the drift back
through the door the float rejection closed.

**The kernel does not know what any metric means.** There is no branch anywhere on a metric name,
no ranking of two metrics, no default limit for a metric the kernel recognises. `amount` is not
special; it is an example. §12 carries this as a do-not-build line because a switch statement over
metric names is the first step of a consequence taxonomy, and the second step is scoring an
operator's actions.

### 2.4 What a budget is summed over

The grant it is written on, and every grant delegated beneath it, transitively. §2.6 is why.

### 2.4.1 A budgeted grant refuses an action whose effect key did not resolve

**Three drafts of this section were wrong, and a probe settled it.** The rule matters because
consumption happens on reserve (R1) and `effect_key` is optional (`v0.1 §5.1`), so without a rule an
agent proposes actions with no effect and spends nothing, forever.

**What the probes printed**, because this section is now written from them rather than from
reasoning about the code:

1. **A loader cannot see the effect template.** `control.py:4017-4026` resolves it as
   `effect if effect is not None else from_policy_effect`: the **decorator's `effect=` wins**, and
   `_warn_template_mismatch` is deliberately a warning so decorator-based deployments are not broken
   by the document. `examples/agent-race/`, `examples/double-refund/` and `examples/approval-replay/`
   each declare **no** `effect:` key in `ctrlrun.yaml` and supply it from `@protect` in `main.py`. A
   load error would refuse to start three shipped examples whose budgets are perfectly enforceable.
2. **On the standalone-authority path there is nothing to check against.**
   `_STANDALONE_KEYS = {"schema", "authority"}` (`authority.py:165`), so a standalone document
   **cannot carry `actions:`**, and `ctrlrun verify --authority` and `ctrlrun gateway --authority`
   both load that way. A load-time rule is not merely unhelpful there, it is unrunnable.

**So the check is where the effect key is resolved, and not before.** After
`resolved._resolve_effect(...)` has run and **strictly before `_secure`**: if a budgeted grant
decided this action and the effect key resolved to `None`, the action is **refused**, naming the
grant and the budget. This is the same placement rule §5.3 applies to the scope provider: as late as
the fact is available, as early as it is before the reservation.

**And it is an existential rule, not a universal one.** An earlier draft said "a grant **none** of
whose actions carries a template", which left the hole it was written to close: a grant covering
`payments.*` where `payments.refund` has a template and `payments.sweep` does not would load clean,
and the agent spends nothing on `sweep` forever. A grant's `actions` are patterns, so which entries
they cover is not decidable at load anyway. At execute it is not a question: the key resolved or it
did not.

**What `Control.evaluate` reports, stated because it diverges.** `Control.evaluate` never resolves an
effect key (`v0.3 §4.3.1`: it "resolves earlier still, before `Control.execute` is entered"), so it
cannot make this check and does not: it reports what authority and policy say. An action that
`evaluate` calls `ALLOW` may therefore be refused by `execute` on this rule. That is the same
divergence `evaluate` already has about preconditions and reservations, it is fail-closed in the
direction that matters, and `ctrlrun.adapter.needs_approval` is the caller that sees it.

Rejected: charging such an action with a synthesised key. Two attempts of one logical action would
charge twice, because what makes a charge idempotent is the effect key (§3.4), and inventing one
would be inventing the identity the caller declined to give.

### 2.5 The window is rolling

A rolling window: the sum is taken over consumptions in the interval `[now - window, now]`.

**Rejected: a fixed window,** where the sum resets at a boundary. An attacker spends the limit at
23:59 and the limit again at 00:01, which is the first thing anybody tries and a doubling of the
stated authority for the cost of waiting two minutes. A fixed window is cheaper to implement,
because it needs a counter and a boundary rather than timestamped rows and a sum over a range. It is
not cheaper for this milestone, because §3.2's ledger needs timestamped rows anyway to make §4's
holds and releases addressable. So the cheap version buys nothing and costs the straddle.

The cost of rolling is stated rather than hidden: the sum is over a range, so it is a query rather
than a read, and §3.5 says what makes that query bounded.

### 2.6 Containment moves on two axes, and the window axis is the one to get right

`v0.3 §5`'s rule is `child ⊆ parent` on every dimension. On a budget that is two comparisons.

| Axis | Rule | Why |
|---|---|---|
| `limit` | `child.limit <= parent.limit` | more money is more authority |
| `window` | **`child.window >= parent.window`** | over the same limit, a **shorter** window is a **higher rate**, and therefore more authority |

**The window rule reads backwards on first encounter, and the backwards reading is the dangerous
one**, so the arithmetic is written out rather than asserted.

A parent of `limit: 100000, window: PT24H` may spend 100,000 in any rolling day. A child of
`limit: 100000, window: PT168H` may spend 100,000 in any rolling week, which is one seventh of the
parent's rate: **narrower, and contained.** A child of `limit: 100000, window: PT1H` may spend
100,000 every hour, which is 2,400,000 a day: **twenty-four times the parent's authority**, and the
rule must refuse it. Push the child's window to `PT1S` and it is effectively unlimited.

The proof, because a containment rule deserves one. §2.5 makes a budget the predicate *the sum over
every interval of length `window` is at most `limit`*. Given `limit_c <= limit_p`,
`window_c >= window_p`, **and §2.3's non-negative values, without which the sum is not monotonic
over nested intervals and none of this holds**, take any interval of length `window_p`: it sits inside some interval of
length `window_c`, whose sum is at most `limit_c`, which is at most `limit_p`. So every spend
pattern the child permits, the parent permits. That is exactly `child ⊆ parent`.

#### 2.6.1 Which child budget is compared with which parent budget

§2.2 makes `budgets` a list precisely so an operator can write 100,000 a day **and** 500,000 a
month on one metric. That makes "the child's budget" ambiguous, and the rule has to say which pairs
with which or `_budgets_contained` cannot be written.

**For every parent budget there must exist a child budget on the same metric with
`limit <= parent.limit` and `window >= parent.window`.** One parent budget may be discharged by one
child budget; a child budget may discharge more than one parent budget.

Matching on the metric alone is what an earlier draft did, and it is undecidable the moment a parent
carries two budgets on `amount`, which is the case §2.2 exists for. Matching on
`(metric, window)` pairs, which is the shape `_constraints_contained` uses for `(argument, op)`, is
worse: only equal windows would ever pair, the window axis would become vacuous, and a child that
lengthened its window would read as having omitted its parent's budget.

Worked, on the case §2.2 names:

| Parent | Child | Contained? |
|---|---|---|
| `amount 100000/PT24H`, `amount 500000/P30D` | `amount 50000/PT24H`, `amount 100000/P30D` | **yes**, each parent budget discharged |
| `amount 100000/PT24H`, `amount 500000/P30D` | `amount 50000/P30D` | **yes**: one child budget discharges both, being under each limit and at least as long as each window |
| `amount 100000/PT24H`, `amount 500000/P30D` | `amount 50000/PT24H` | **no**: the monthly parent budget is discharged by nothing |
| `amount 100000/PT24H` | `amount 100000/PT1H` | **no**: shorter window, higher rate |

**A child that omits a budget its parent carries is rejected**, which the rule above states as "for
every parent budget there must exist a child budget". `v0.3 §5.4`, unchanged and with no exception
for budgets.

**A child budget on a metric its parent does not budget is an addition, not an escalation**, and is
permitted, on the same rule that lets a child add a constraint.

### 2.7 Consumption charges every ancestor in the chain

**The rule that makes the feature mean anything.** A consumption under a delegated grant charges
that grant and every ancestor up to the root, and any of them being exhausted refuses the action.

Without it: a holder of a 100,000-a-day grant delegates ten children, each correctly contained at
100,000 a day, and spends 1,000,000. Each child is individually within its parent. The chain is
individually valid at every link. The total is ten times the authority anybody granted, and
`v0.3 §5`'s entire containment argument would have decided nothing quantitative.

**Rejected: per-grant ledgers**, for exactly that reason, and it is recorded as rejected rather than
omitted because it is the obvious first implementation and it is wrong.

This makes the depth of a chain a cost: a consumption writes or checks one row per ancestor.
`max_delegation_depth` already bounds that depth, and §3.5 states the bound rather than leaving it
to be discovered under load.

### 2.8 Budgets render into the policy hash

`_canonical_grant`'s closed field list (`authority.py:1423-1436`) renders every field that narrows
what a grant permits. A budget narrows what a grant permits. Therefore it renders, including inside
a break-glass envelope, which `canonical_grants` already walks through `_canonical_grant`.

**The consequence if it did not**: an operator widens a budget from 10,000 to 10,000,000, the policy
hash does not move, and every approval bound to that hash by `v0.6 §7.1` stays valid against a
document that now permits a thousand times more. `SPEC-v0.8.md` §5.2 gives this reason for `max_ttl`
and it is the same defect in the same field list.

**Rendered in document order, not sorted, and this amends an earlier draft of this sentence.** The
draft said sorted "like `constraints`, so two documents differing only in list order hash alike",
and `constraints` really does behave that way. Budgets do not, for two reasons. §2.2 gives the list
order meaning, because the first budget to refuse is the one named in the refusal, so two documents
differing in list order differ in what an operator is told. And the hash's job is to move when the
document changes: sorting makes two different documents hash alike, which is the direction that
loses evidence rather than the direction that creates it.

**The window renders as integer seconds, not as the document's ISO-8601 spelling**, which amends
§10's `Budget` row as originally written. That row said the document's own spelling was the
canonical one; it is not implementable, because `canonical_grants` renders parsed grants and
`Budget` keeps a `timedelta` rather than the source text. Seconds is also what
`_canonical_envelope` already renders `max_ttl` as. The consequence is stated rather than hidden:
`PT24H` and `P1D` hash alike, which is correct, since they are the same window and a hash that
distinguished them would move for a document that changed nothing.

---

## 3. The ledger, and the store amendment

### 3.1 Why this is its own item

Because R1 is a claim about a transaction, and a transaction is the one thing a specification cannot
delegate to an implementer's judgement. `postgres.py:1106` carries a comment recording that this
repository has already found this exact bug once, in the form of eight authorised refunds, and the
comment exists because a read and a write that looked adjacent were not atomic.

### 3.2 The ledger is a table

One row per consumption:

| Column | What it holds |
|---|---|
| `grant_id` | the grant charged; one row per ancestor, per §2.7 |
| `metric` | the metric, as written in the document |
| `amount` | the value summed; `1` for `count` |
| `effect_key` | **what makes reconciliation possible**: a release must find the rows one effect wrote |
| `attempt` | `v0.7 §5`'s attempt number, and §3.4's idempotence key |
| `consumed_at` | the timestamp §2.5's rolling sum ranges over |
| `released_at` | `NULL` while held; set on release, per §4.4 |

**Not a column on an existing table, and not a counter.** A counter cannot be released selectively,
cannot be summed over a rolling window, and cannot say which effect is holding what, which is §7's
whole deliverable. A column on `effects` could hold one charge but not the per-ancestor rows §2.7
requires.

### 3.3 The store amendment, against `SPEC-v0.6 §9.2`'s two bars

`StateStore` has been frozen since v0.6 and the expected number of new methods has been zero. v0.9
is the first milestone with a candidate a column cannot satisfy, and the bar is stated before it is
cleared: **a second backend could not be written without it.**

**It is cleared, twice.**

*First, the race.* The charge must land inside the transaction that writes the reservation.
`_authorize_and_reserve` is that transaction on the two **durable** backends:
`state.py:1760-1795` inside `SQLiteStateStore`'s `BEGIN IMMEDIATE`, and `postgres.py:762-827`
between `BEGIN` and `COMMIT`. (`state.py:1014-1040` is `InMemoryStateStore`'s, guarded by a
`threading.Lock`, which serialises threads in one process and makes no cross-process claim at all.
It is named here because an earlier draft cited it as evidence for "both backends", and a mutex is
not evidence about a transaction.) A second backend written against
today's declared protocol has **nowhere to put the charge**: every declared method that writes is
either the reservation itself or a later transition, so the charge would necessarily land outside
the reservation's transaction, and outside it there is no serialisation between the sum and the
insert. Two processes read the same total and both pass. That is not a quality-of-implementation
difference between backends; it is a backend that cannot be correct.

*Second, and this is the stronger half, ambiguity.* `v0.6 §4.3.2` resolves a lost `COMMIT` by
re-reading on a fresh connection, with two tables covering a lost `INSERT` and a lost compare-and-set
`UPDATE`. Because the charge rides inside the reservation's transaction, the single re-read that
resolves the reservation resolves the charge with it: the transaction committed or it did not, and
the re-read says which. Split them into two transactions and `v0.6 §4.3.2` needs a third table that
nobody has written, covering a reservation that landed with a charge that may not have, which is a
state no operator could reason about and no `resolve` command could fix.

#### 3.3.0 The contingency is discharged: a spike ran it before any item built on it

This section made the amendment **conditional** on a plain column not being enough, and said an item
finding otherwise should stop. That question was answered by a throwaway spike against Postgres
before item 1 started, rather than by three merged items later:

- **A column undercounts every ancestor but the leaf.** One effect under a three-level chain charges
  `root`, `mid` and `leaf` per §2.7. The effects row is 1:1 with the effect and carries one
  `grant_id`, so the per-grant rolling sum read **0 for `root` and 0 for `mid`** where the ledger
  read 100 for each. That is precisely the escalation §2.7 exists to prevent, produced by the design
  that would have avoided the amendment.
- **A second transaction right after the reservation loses the charge.** A crash between the two left
  `reserved=1, charged=0`: the effect happens and the budget never sees it. And `v0.6 §4.3.2`'s
  re-read resolves the reservation while saying nothing about the charge, which is the paragraph
  above restated as a measurement.

So the amendment stands, and it stands on something that was run.

**The shape: an optional parameter on the two existing methods, not a new method.**

```python
def reserve_effect(
    self, effect_key: str, action_id: str, lease: timedelta = DEFAULT_LEASE,
    charges: tuple[Charge, ...] = (),
) -> Reservation: ...

def consume_approval_and_reserve(
    self, approval_id: str, action_hash: str, effect_key: str, action_id: str,
    lease: timedelta = DEFAULT_LEASE, charges: tuple[Charge, ...] = (),
) -> tuple[Approval, Reservation]: ...
```

Rejected alternatives, each with the reason it loses:

- **A new method, `reserve_effect_with_charges`.** Two methods that must stay behaviourally
  identical except for one argument, forever, and every future change to reservation semantics has
  to be made twice or silently diverges. `v0.6 §9.2`'s bar is about what a backend needs, not about
  how the need is spelled, and the spelling that minimises the ways a backend can be subtly wrong is
  the one argument.
- **A separate ledger protocol the store composes.** It reads as the cleanest design and it is the
  one that loses hardest: composition puts the charge in a different object, and a different object
  is a different transaction unless the composition also exposes the transaction, at which point the
  protocol has a transaction in it and is no longer a ledger protocol. This is the alternative that
  produced the second bar above.
- **A column on `effects`.** Cannot hold the per-ancestor rows of §2.7, cannot be summed over §2.5's
  window, cannot be released per effect while another effect's charge on the same grant is held.

**The default is `()` and it is load-bearing.** A grant with no budget passes no charges, and the
reservation path it takes is the one it took at 0.8.0, instruction for instruction. §9 requires a
test that proves it rather than a paragraph that asserts it.

### 3.3.1 Where the predicate is evaluated, and what `Charge` therefore carries

**The question §3.3 leaves open if it does not answer it here**, and the one an implementer hits on
the first morning. `charges=` makes the ledger *write* atomic with the reservation. R1's race is not
about the write; it is about the **check**: two processes read the same total, both pass, both
spend. Making the write atomic while leaving the check outside buys nothing at all.

So: **the check happens in the store, inside the same transaction, and `Charge` carries the whole
predicate.**

```python
@dataclass(frozen=True)
class Charge:
    grant_id: str      # which grant is charged; one per ancestor, per §2.7
    metric: str
    amount: int        # §2.3: an integer
    limit: int         # what the sum may reach
    window: timedelta  # what the sum is taken over
```

`limit` and `window` are on the `Charge` rather than looked up by the store, because a store that
resolved a grant's budgets would be reading the policy, and `ARCHITECTURE.md` §6's module map has
`state.py` not knowing about `policy.py`. The store evaluates one arithmetic predicate it was handed, over rows it owns.

**The evaluation order inside the transaction**, which is the whole of the amendment's value: sum
the un-released rows for `(grant_id, metric)` over `[now - window, now]`, compare `sum + amount <= limit`
(**inclusive**, matching §2.2's "what the sum may reach"), and either insert the rows and the reservation together or write neither.

**What the store raises, and the direction a store that ignores it fails in.** `errors.py` is
closed and §10 adds nothing to it, so the store raises a **package-internal** exception naming the
grant, the metric and the window, and `Control` converts it to §4.5's `ActionDenied` with its events
and its receipt. `ActionDenied` is `Control`'s to raise, with a receipt and an `ACTION_DENIED` event
beside it; a store that raised it would be minting evidence, which is `control.py`'s job.

**`SPEC-v0.8.md` §2.5 is NOT the precedent for this, and an earlier draft cited it as one.** That
section's carrier is safe because of its direction, which it states: *"A third-party store that
ignores it records nothing, and §2.7's consume-side check then refuses every approval it grants,
which is the fail-closed direction."* This carrier has the **opposite** direction. `StateStore` is a
structural `Protocol` (`state.py:511`), so a third-party store that implements `charges=` as "write
the rows" and never evaluates the predicate **silently disables every budget on the deployment**,
and nothing detects it. §3.3.3 spots this shape for the read; an earlier draft missed it for the refusal, which
is the authorization-relevant half.

**So the predicate is part of the declared contract, and the conformance kit enforces it.**

- `charges=`'s contract is a **MUST**: a store that accepts charges MUST evaluate §3.3.1's predicate
  inside the same transaction and MUST refuse when it fails. A store that cannot is not a conforming
  store, and says so by not accepting charges rather than by accepting and ignoring them.
- **`ctrlrun.conformance`'s store suite gains the case**, which turns the MUST into something a
  third-party backend discovers at its own test time rather than in an operator's production. The
  kit exists for exactly this (`v0.5 §9`), and a MUST no suite checks is the
  documentation-rather-than-defence shape the mutation-pattern list calls out.
- **And the suite is opt-in, so it detects rather than enforces.** A store that never runs it still
  disables every budget silently. So `Control`, **at construction, when any grant carries a budget**,
  issues one probe reservation against a synthetic grant with `limit: 0` and a nonzero charge and
  **refuses to start unless the store refuses it**. In-band, one round trip, needs nothing the
  amendment does not already add, and it is the difference between detecting the fail-open and
  foreclosing it. A store that cannot evaluate the predicate is then a store an operator cannot
  accidentally deploy with budgets configured.
- **The suite's case races the sum and the insert, not merely the predicate's presence.** A store
  that evaluates the predicate in a *separate statement* from the insert passes a naive "does it
  refuse" test and fails open under concurrency, which is `postgres.py:1106`'s bug exactly. The kit
  already has the harness (`conformance/store/worker.py`), and T443 uses it.

**Rejected: evaluating at the authority gate.** `Control.execute` evaluates authority at `control.py:819`, and
`control.py:1094-1095`'s ordering comment places that at *principal_expired, authority, policy,
approval, reservation*, so it runs **before** the transaction. Two processes would read the same total, both pass, and `charges=` would faithfully
record both spends. It reads like the natural home, because every other authority question is
decided there, and it is the one place the check cannot work.

**`limit: 0` does not stop a grant, and an earlier draft said it did.** A zero limit refuses every
action with a nonzero metric value and permits **unboundedly many zero-valued ones**, each producing
a reservation, an effect record and a valid receipt. A zero-valued action never moves the sum, so
nothing ever refuses it. An operator who wants a grant that may not act expresses that by not
granting it, and §7's surfaces do not pretend otherwise.

**What this costs, stated.** The store now evaluates an arithmetic predicate rather than only
storing rows, which is a genuine widening of what a `StateStore` does. It is the narrowest widening
that closes the race: one comparison, no document, no policy, no principal, over rows the store
already owns.

### 3.3.2 Where `Control` catches it, because an unhandled refusal escapes with no receipt

`_secure`'s loop catches `AmbiguousEffect` (`control.py:2057`), `ActionDenied` (`control.py:2079`),
`ApprovalMismatch` (`control.py:2104`) and `DuplicateEffect` (`control.py:2132`). **A new exception
type escapes all four**: no receipt,
no event, and a package-internal exception in the caller's hands.

That is not hypothetical, and this repository has already met it once.
`control.py:1748-1751` records it in the source: *"A record a human resolved while this attempt was
still running answers `InvalidArgument`, which escaped: no receipt, no event, and the caller handed
a store error about its own effect key. Found by review, round 2."*

**And routing it through the existing `ActionDenied` handler is also wrong**: that handler appends
`APPROVAL_DENIED` (`control.py:2083-2089`) unconditionally, so a budget refusal would fabricate an
approval denial for an action no human ever saw.

**The carrier is not an `ActionDenied` subclass, and its handler precedes the `ActionDenied`
clause.** Subclassing is the obvious implementation, since the handler converts to one, and it would
put `except ActionDenied` at `control.py:2079` first, fabricating the very `APPROVAL_DENIED` this
section exists to prevent, with T437 passing or failing on clause order alone.

**So `_secure`'s loop gains its own handler**, converting the carrier into `ActionDenied` with
reason `budget_exhausted`, appending `ACTION_DENIED` and no approval event, and writing a `denied`
receipt. §9.5 asserts all three: a receipt exists, `ACTION_DENIED` is appended, and
`APPROVAL_DENIED` is **not**.

### 3.3.3 The read the surfaces need, which is the third part of the amendment

§7 shows an operator what a budget consumed and what it is holding, and G22 checks it. Neither can
be done through any method `StateStore` declares, and `charges=` is write-only.

So the amendment is **one parameter and one read method**:

```python
def consumptions(
    self,
    *,
    grant_id: str | None = None,
    metric: str | None = None,
    since: datetime | None = None,
    effect_key: str | None = None,
) -> tuple[Consumption, ...]: ...
```

**`grant_id` is optional**, because §7.3 has `stats` report the ledger's row count so growth is
observable, and a required `grant_id` would make that require enumerating every grant id that ever
existed, runtime delegations and revoked grants included, one call each. `Consumption`'s fields are
frozen in §10.

**`effect_key` was added by item 5 and is recorded here rather than slipped in**, on §10's rule that
anything not in that section is a spec amendment before it is code. §8.3 makes the resumed leg's
receipt the only receipt an MCP multi round-trip or ACS action ever gets, so it has to report what
that action spent; a gateway that restarted mid-round has nothing in memory to report it from, and
the ledger is the record. Without the filter that read is a scan of the whole ledger per
resumption. The other three filters could not serve: the question is "what did *this effect* spend",
and a resumption knows its effect key and not which grants a chain of ancestors charged.

**And the "why" of §7.2 is a join, not a column.** `get_effect(effect_key)` is already declared
(`state.py:563`), so an un-released row's holding state is read by joining its `effect_key` through
it. Said explicitly because the alternative an implementer reaches for is putting the effect's state
on the ledger row, which would denormalise the state machine §4.1 deliberately does not give the
ledger.

This clears `v0.6 §9.2`'s bar in the plainest way, and in exactly the way that section's own example
did: **a second backend implementing `charges=` and nothing else would satisfy every declared method
and break `ctrlrun inspect` and `ctrlrun verify`.** `v0.6 §2.7.2` records that finding for `events()`
and `receipts()`, in those words, and it is the same finding here. An earlier draft of §3.3 said "an
optional parameter, not a new method", which was true of the write and silent about the read.

### 3.4 The insert is idempotent on `(effect_key, attempt, grant_id, metric)`

`v0.6 §4.3.2` Table A1 row 2 says: on a lost `COMMIT` with no record found, **retry the insert,
once.** The retried transaction re-inserts the reservation and the charges with it. If the ledger
insert were an unconstrained append, the retry would double-charge, and it would do so precisely in
the case where an operator's network was already misbehaving.

So the ledger carries a unique constraint on `(effect_key, attempt, grant_id, metric)`, and the
insert is written so that a replay of the same transaction produces the same rows rather than twice
as many. Table A1 row 1 then covers the charges without a third table, **and the reason is the
transaction and not the identity check**: `_resolve_lost_insert` re-reads one effect record
(`postgres.py:909`) and compares it with `_is_our_own_write` (`postgres.py:926`), over the effects
row alone. Ledger rows are not in that comparison and could not be, because a ledger row carries a
store-assigned `consumed_at` that will not compare byte for byte. What makes the charge resolved is
that it rode the same transaction, so the effects row's presence *is* proof the charge committed.
An earlier draft said the identity check was over the charges too, which would send an implementer
to widen `_is_our_own_write` over the ledger, where it fails.

### 3.5 What bounds the query

§2.5's rolling sum is a range query, and §2.7 makes one per ancestor. Two bounds, both already in
the system:

- **Depth** is bounded by `max_delegation_depth`, which `v0.3` already validates and the policy hash
  already covers. A consumption touches at most that many grants.
- **Time** is bounded by the window: rows older than the longest window on any budget of a grant
  cannot affect its sum. They are not deleted by the kernel, because deleting evidence is not
  something this project does quietly, and §7.3 says what an operator may do about growth.

An index on `(grant_id, metric, consumed_at)` is what makes the range query a range scan. It is
named here because a ledger without it is correct and unusable, and "correct and unusable" is how a
governance control gets turned off.

### 3.5.1 The migration is named here, as v0.6's and v0.7's were

`migrations.py` currently ends at `0006_verified_approver`. v0.9 adds **`0007_budget_ledger`**: the
table of §3.2, the unique constraint of §3.4, the index of §3.5, and **on Postgres only, the
`budget_anchor` table §3.6.1's lock takes** (one row per grant id ever charged, created on demand).
The anchor is named here because §3.5's growth discussion otherwise misses it: it accumulates a
permanent row per grant, runtime delegations included, and the kernel deletes none of them.

It is named in the specification rather than left to the item because `v0.6 §3.7` and `v0.7 §6.11`
both named theirs, with their DDL and their collation, and a migration discovered in a diff is a
migration nobody reviewed. **Forward-only**, per `v0.6 §3`.

T414 tests it in both directions, and the reverse is the one that gets forgotten: `v0.6 §3.5`
requires that a **0.8.0 binary opening a migrated database refuses at open** with `SchemaMismatch`
naming the migration, and `v0.7`'s T264 is the precedent. A migration that only runs forwards turns
a rollback into silent corruption.

### 3.6 Both backends, and the lock, now measured rather than left open

**SQLite** takes `BEGIN IMMEDIATE` (`state.py:1777`), a whole-database write lock taken before the
first read, and the sum and the insert are inside it. Nothing further is required.

**Postgres runs READ COMMITTED with an explicit `BEGIN`** (`postgres.py:782`), and under READ
COMMITTED a sum and an insert are **not** serialised.

#### 3.6.1 What the spike measured

An earlier draft of this section left the mechanism open between three candidates and asked item 4
to pick one. **The spike ran all three**, 24 processes racing one budget that permits exactly ten
spends, each process reserving a distinct effect key so the effects table's own uniqueness does not
serialise them, four runs:

| Mechanism | Result over four runs | Verdict |
|---|---|---|
| sum then insert, no lock | spent 1200, 1000, 1200, 1200 against a limit of 1000 | **overspends, and passes sometimes** |
| `SELECT ... FOR UPDATE` on a per-grant anchor row, before the sum | 1000, 1000, 1000, 1000 | **correct, and stable** |
| `SET TRANSACTION ISOLATION LEVEL SERIALIZABLE` | 800, 600, 600, 800, with zero refusals | holds, at a cost that disqualifies it |

**So the mechanism is the anchor-row lock**, named here rather than left to the item: a
`SELECT grant_id FROM ... WHERE grant_id = ? FOR UPDATE` taken **before** the sum, per grant charged.
Per grant and not per store, so two budgets on two grants do not serialise against each other.

**Why SERIALIZABLE loses, which was not obvious before it was run.** It holds the limit, so it is not
*wrong*. But it under-spends by 20 to 40 percent, and its aborts arrive as
`SerializationFailure` rather than as refusals: in the runs above it produced **zero** clean
refusals, converting every one into a retryable error. An operator would get a budget that silently
delivers less authority than it grants and an agent that sees database errors where §4.5 promises a
denial naming the grant, the metric and the window.

#### 3.6.2 The naive implementation passes sometimes, so the test runs repeatedly

**The finding that changes how G22 is tested.** The unlocked implementation held the limit in one
run of four. It is not reliably wrong; it is *occasionally* right, which is worse, because a
concurrency test run once against a broken implementation reports `PASS` about a quarter of the time.

That is `CONTRIBUTING.md`'s fourth mutation pattern exactly, "windows not actually reproduced", and
it is the shape v0.8 was warned about and v0.9 can now demonstrate. So:

- **G22's multi-process test runs the race repeatedly and asserts the invariant every time**, not
  once. The spike's ratio is the guide: at four runs a broken implementation escapes roughly one
  time in 250, and at ten it does not escape.
- **Item 4's mutation table includes removing the `FOR UPDATE`**, and the row is only green if the
  test goes red *reliably*. A mutation that produces an intermittent failure is a mutation the
  table must report as intermittent rather than as caught.

---

## 4. Reconciliation

### 4.1 The rule, in one sentence

**A charge is released exactly when its effect reaches `FAILED`, and held in every other state.**

The ledger has no state machine of its own. `effect.py:248-315` is already the complete table of
exits from a reservation, and this one rule covers every row of it.

### 4.2 Every disposition of a held charge

**Retitled from "every exit from `RESERVED`", which was wrong about the code and would have made an
implementer's completeness check impossible.** `commit_effect` and `fail_effect` transition out of
`EXECUTING`, not `RESERVED` (`state.py:77`, `state.py:1073-1079`); `resolve_effect` transitions out
of `AMBIGUOUS` only (`_resolvable`, `state.py:316-332`). The genuine exits from `RESERVED` are
`begin_execution`, `mark_ambiguous` via `_UNFINISHED` (`state.py:80`), another planner's `ambiguate`
(`effect.py:308-315`), and the lease simply running out. The rule is over the **charge**, not over
one state, so the table is too.

v0.8's item 4 needed three attempts on the analogous lapsed-row case because its spec had ten rows
and its code met an eleventh. This table was reviewed against `control.py` and `state.py` for that
reason, and four rows below were added by that review rather than by the drafting.

| Disposition | Effect record | Ledger |
|---|---|---|
| `commit_effect` | `COMMITTED` | **held, permanently.** A committed spend is a spend |
| `fail_effect` | `FAILED` | **released.** The executor proved nothing happened (`v0.1 §5.5`) |
| `mark_ambiguous` | `AMBIGUOUS` | **held.** R2: ambiguity is not a refund |
| lease lapses, nothing else happens | unchanged until someone plans against it | **held.** No transition has occurred |
| lease lapsed, another process plans against it | `AMBIGUOUS` via `plan_reservation`'s `ambiguate` | **held.** It is `AMBIGUOUS` now, and R2 applies |
| `resolve_effect(COMMITTED)` by a human | `COMMITTED` | **held.** The human said it happened |
| `resolve_effect(FAILED)` by a human | `FAILED` | **released.** The human said it did not |
| `reconcile` hook moves it | as the hook says | **as the human's equivalent**: the hook is an authority, not an exception |
| the **second `_take`** after that hook | a fresh reservation, or a refusal | **a fresh charge, or none.** `v0.2 §2.3` permits two *takes*, not two hook runs: `_secure`'s guard is `if reconciled or not self._reconciled(...)` (`control.py:2064-2070`) and `execute`'s docstring says the hook "runs at most once per call". An earlier draft had this row as a second hook pass, which cannot happen |
| **the attempt ceiling refuses after the reservation was won** (`control.py:1242`, `_refuse_ceiling(..., reserved=True)`, `v0.7 §5.5`) | the kernel itself drives `begin_execution` then `fail_effect` | **released**, by the `FAILED` rule. A charge taken and released inside one call where the executor never ran, driven by the kernel rather than by any outcome. **This is the shape v0.8's item 4 missed** |
| **`begin_execution` is refused** after the reservation was won (`control.py:1250-1264`) | `DuplicateEffect` or `AmbiguousEffect`; the reservation is taken away | **held**, by the ambiguity rule. Mechanically the lapsed-lease row, but a distinct call path with a distinct receipt, so it gets its own test |
| **observe mode reserves** (`control.py:1528`, `control.py:1530`, outside `_take`) | `RESERVED` | **nothing is charged.** `v0.3 §6.2` makes observe mode record rather than enforce, and a budget that consumed there would enforce: the run would refuse at the limit while claiming to be observing, and the counterfactual an operator adopts observe mode to get would be wrong. §4.2.1 |
| **a suspension holds the effect** (`hold_continuation` extends the lease, `state.py:1099-1112`) | `RESERVED`, lease extended | **held**, for as long as the continuation is held. §4.6 says this is not bounded by the kernel |
| `begin_execution` succeeds | `EXECUTING` | **held.** No ledger movement; listed because the table claims completeness |
| **`fail_effect` is REFUSED** after the executor raised `NotExecuted` (`control.py:1740-1763`, `_unrecorded`) | the record moved on while the executor ran: the lease lapsed and another declared it `AMBIGUOUS`, **or a human resolved it** to `COMMITTED` or `FAILED` | **§4.1 over the state the record actually reached**: held if `AMBIGUOUS` or `COMMITTED`, and **already released** if a human resolved it `FAILED`. **This call releases nothing itself.** See the warning below |
| `commit_effect` is refused (`control.py:1851-1869`, `_unrecorded`) | the record moved on, same set | **§4.1 over the state reached**, same as the row above |
| `mark_ambiguous` is refused (`control.py:1800-1822`; it logs and folds the refusal into the error text rather than calling `_unrecorded`) | the record moved on, same set | **§4.1 over the state reached** |
| renewal after `FAILED` (`renews=True`, `attempt+1`) | `RESERVED` again | **a new charge**, per §4.3 |
| retry refused (`COMMITTED`, `AMBIGUOUS`, live lease) | unchanged | **nothing.** No reservation, no charge |

**The `fail_effect`-refused row is the one to read twice, and it took two rounds to state.** The rule
is "released exactly on `FAILED`", and the row above it says `fail_effect` releases because the
executor proved nothing happened. An implementer who turns that into *release when the executor
raises `NotExecuted`*, or *release in the `fail_effect` code path*, gets this row backwards and hands
back authority for an effect somebody may have committed.

**The release is keyed on the record reaching `FAILED`, never on the call that tried to put it
there.** That is why these three rows defer to §4.1 rather than asserting "held": a human may have
resolved the record `FAILED` while the attempt ran (`resolve_effect`'s `RESOLUTIONS`, `state.py:83`),
in which case the charge is already released and *nothing further happens* rather than being held.
A round-two draft of these rows said "held, NOT released" flatly, which is false for exactly that
sub-case, and the source comment those rows cite (`control.py:1748-1751`) is about a human resolving
the record. §9.5's test drives the lapse concurrently rather than asserting the happy path.

### 4.2.1 Observe mode charges nothing, and says so in the report

`v0.3 §6.2`'s observe mode enforces nothing and records what it would have done. A budget consumed
there would be the one check in the kernel that enforced under observation, and the adoption path
this project documents is observe-then-enforce: an operator would hit a limit during the phase whose
entire purpose is to hit nothing.

**What it does instead**: the observe report says the action *would have been* refused on a budget,
naming the grant and the metric, exactly as it reports what a policy would have decided. The
predicate is `check_charges`, the same function all three stores enforce with, so the report and the
enforcement cannot drift **on the arithmetic**: a pilot that says "this would have been fine" about
an action enforce mode refuses is worse than no pilot.

### 4.2.1b What observe mode does not promise about *which* refusal

**The arithmetic is shared; the ordering is not**, and that limit is stated here rather than
discovered. `_secure` and `_observe_secure` are separate implementations, for the reason
`_observe_secure`'s docstring gives: they differ in almost every branch, and one writes no receipt
and raises nothing. What they do not share is the order their checks run in, and `_Observation`
keeps the **first** reason it is given.

So for an action that trips more than one refusal, observe mode names the one *it* reached first,
which is not always the one enforce mode would raise. Three cases were found and aligned, each with
a test: the approval gate (T451), the reservation (T452), and the scope provider (T458). Two more
are known and **not** aligned in v0.9:

- **Scope against the approval gate.** `_secure` presents the approval above `_in_scope`;
  `_observe_secure` checks scope first. An action both out of scope and awaiting approval is
  reported `out_of_scope` by the pilot while enforce mode pages a human.
- **`policy_unapproved` against anything decided after it.** Enforce refuses it above authority and
  policy; observe records it below both, so it is lost whenever something later blocks first.

Neither is an enforcement difference: observe mode refuses nothing either way, and the action runs
in both. What an operator loses is the **category** of a refusal that would have happened, on an
action that would have been refused regardless. **The rule to rely on is that observe mode reports
a refusal exactly when enforce mode would refuse, and not that it always names the same one.**

Aligning the rest means one ordered list of checks both modes walk, which is a refactor of
`_secure` and `_observe_secure` together rather than a fourth reordering. Three reorderings in this
milestone produced four regressions between them, and this section is the honest statement of where
that stopped.

§2.3's and §2.4.1's refusals are reported the same way, under their own reasons. Enforce mode
refuses those actions, so saying so is what observe mode is for, and an operator needs to know
whether the budget is too small or the action cannot be measured at all. Observe mode writes no
`denied` receipt for them: it records what enforce mode would have done and refuses nothing.

### 4.2.1a What observe mode cannot tell you about a budget

An earlier draft of §4.2.1 ended "that is how an operator sizes a budget before turning it on."
**That is not true, and the limit is worth stating rather than discovering.**

Observe mode charges nothing, so the ledger it evaluates against is only ever filled by enforce-mode
runs. A deployment observing *every* action has an empty ledger, every predicate passes, and the
report says no budget would have refused anything, no matter how much the agent proposed to spend.
The report is informative in a **mixed** deployment, where a new action is piloted in observe mode
against a grant other actions are already enforcing, and that is the shape §4.2.1's test drives.

Sizing a budget from an observed run needs the counterfactual spend, which observe mode does write:
every `observed` receipt carries `budget_charges`, what the action *would have* been charged. Adding
those up over a window is the sizing question, and it is a question for a reporting surface over
receipts rather than for the kernel's hot path. The kernel's job here is the honest report of what
enforcement would have done against the state that exists.

### 4.3 One effect key holds at most one charge at a time

Stated carefully, because the obvious phrasing is wrong.

"One effect key charges once" is false: `plan_reservation` returns `renews=True` with `attempt+1`
for a `FAILED` record, which is the one path where the same effect key reserves twice. And it
**should** charge again, because §4.2 released the first charge when the effect failed, and the
failure is a proof that the first spend did not occur.

The true property: **at most one un-released charge per `(effect_key, grant_id, metric)` at any
time.** It follows from charging inside the reservation, because the reservation already has exactly
this property, and it is `attempt` in §3.4's uniqueness key that keeps the second attempt's row
distinct from the first's rather than colliding with it.

**If this does not fall out of the implementation, the charge is in the wrong place**, and item 4 is
wrong rather than item 5 needing a workaround. Item 5 tests it as a property over renewals, second
attempts and a twice-running `reconcile` hook.

### 4.4 Release is a compare-and-set on a flag, never a decrement

`fail_effect`, `resolve_effect` and every other transition after the reservation are compare-and-set
`UPDATE`s, and `v0.6 §4.3.2` Table A2 row 2 says a lost `COMMIT` on one is resolved by **re-issuing
the same `UPDATE`, once.**

A decrement is not idempotent under a re-issue: the second one subtracts again and the operator's
budget quietly grows. Setting `released_at` where it `IS NULL` is idempotent by construction, and a
re-issue is a no-op. This is why §3.2's row carries a nullable timestamp rather than the ledger
holding a running total.

The same property is what makes the two-pass `reconcile` hook of `v0.2 §2.3` safe without a special
case.

### 4.5 The refusal

`ActionDenied`, with a reason of its own, naming **the grant, the metric and the window**. It does
not name the remaining amount.

**Why not the remaining amount.** A refusal that reports the balance is an oracle: refused actions
cost nothing, so an attacker binary-searches the exact limit in a few dozen refusals and knows
precisely how much authority to use without tripping it. An operator debugging at 3am does want the
number, and gets it from §7's `inspect`, which requires access to the store rather than the ability
to be refused.

The reason is distinct from every other denial reason, because a test asserting only the exception
type could not tell an exhausted budget from an out-of-scope record, which is the first of
`CONTRIBUTING.md`'s four shapes of a false green.

The grant named is **the one that refused**, which under §2.7 may be an ancestor rather than the
grant that would otherwise have decided. An operator whose child grant is well within its own budget
needs to be told that the parent is not.

### 4.6 What reconciliation does not do

**A budget cannot recall an action already in flight when a window rolls.** Actions in flight hold
their charges, and a window that rolls forward changes what the next reserve may do and nothing
about what is already reserved. This is the roadmap's own "does not close" line, and it is written
here rather than only there because this is the section somebody implementing releases will read.

**A budget does not expire a hold.** An `AMBIGUOUS` effect holds its charge indefinitely, by R2, and
the only thing that moves it is a human or a hook. An automatic timeout that released holds would be
the refund R2 refuses, on a delay.

---

## 5. Scope providers

### 5.1 The sharp case

A grant permits `records.read` on `customer:*`. An agent is handed a customer id by a document it
summarised, and reads customer 90210, which belongs to somebody else. Every check passes: the action
is permitted, the resource matches the pattern, the principal is resolved, the approval is valid.
Nothing in v0.1 to v0.8 has an opinion about **whose** record it is, because the identifier came from
the attacker and the pattern was written to match identifiers.

This is what `../ctrlrun-docs/docs/OWASP-AGENTIC-TOP10.md`'s `ASI06` row currently says, in as many
words: nothing bites on an identifier an attacker chose. §5 is the bite.

### 5.2 What is shared with `v0.7 §6`, and what is not

Stated before the mechanism, because the two look alike and are not.

| | `v0.7 §6` precondition fingerprint | v0.9 scope provider |
|---|---|---|
| Question answered | did the world move between request and consumption? | is this record in this principal's scope? |
| Compared against | a fingerprint captured earlier | the action's own resource |
| When it binds | approvals only, request and consumption | every protected action, whether or not an approval exists |
| On failure | `ApprovalMismatch`, approval left granted | `ActionDenied` |

**Shared:** the ordering argument (§5.3), the canonicalizer and its float and non-string-key
refusals, and the rule that a hash reaches the evidence and the content never does.

**Not shared:** the mechanism, the call site's semantics, the failure type, and the surface. A scope
provider is a sibling of the precondition provider, not a mode of it. §5.7 says what a deployment
configuring both does.

### 5.2.1 This amends `SPEC-v0.7.md` §6.9, and says so

`v0.7 §6.9` decided this question in advance, in these words:

> v0.9's scope providers are *"a resource-ownership precondition through v0.7's fingerprint
> mechanism"*, and they configure this hook rather than adding a second one.

(Verbatim; an earlier draft bolded "configure this hook", which the source does not.)

`ROADMAP.md`'s v0.9 bullet says the same. **§5.2 concludes the opposite, so this document amends
that sentence rather than quietly departing from it.** This document opens by saying all eight prior
specs remain binding; a spec that overturns a binding sentence owes the citation, and §10.1 records
the amendment alongside the schema bumps.

Three mechanical differences are the reason, and none of them was visible when `v0.7 §6.9` was
written:

1. **The matching step is different in kind.** v0.7 compares a hash to a hash. v0.9 matches the
   action's resource against a returned scope using `contains()` (`authority.py:250`), which is the
   segment relation of `v0.3 §5.5`. A hook whose output is only ever hashed cannot express that.
2. **The binding scope is different.** `v0.7 §6.8` restricts the fingerprint to the `APPROVE`
   path's presenting pass and explicitly excludes `ALLOW`, `DENY` and `resume`. A scope check that
   only bound on approved actions would leave every auto-allowed action unscoped, which is most of
   them.
3. **The failure type is different.** v0.7 raises `ApprovalMismatch` and leaves the approval
   granted, which is meaningful because an approval exists. v0.9 raises `ActionDenied`, because on
   the `ALLOW` path there is no approval for a mismatch to be about.

**What the amendment costs**: a second hook on the same path, and §5.7's ordering question, which
would not exist if they were one mechanism. It is the price of the three differences above.

### 5.2.2 Where a scope provider binds, and where it does not

This is the **first of the two columns** the milestone's plan requires on `v0.3 §4.3.1`'s entry-point
table, and §6.3.2 is the second. The plan's wording is the standard: *"The rows that say no are
written down as deliberately as the rows that say yes."* `v0.3 §4.3.1` has thirteen rows and an
earlier draft of this section covered six, which is the missing-enumeration failure that table exists to
prevent, and the one that let an expired credential mint permanent authority in v0.3.

| Entry point (all thirteen of `v0.3 §4.3.1`'s rows) | Scope provider | Why |
|---|---|---|
| `@protect` to `Control.execute`, `ALLOW` | **runs** | the main case, and the one `v0.7 §6.8` does not cover |
| `@protect` to `Control.execute`, `APPROVE`, **requesting** pass | **not called** | the action is not being taken yet. A human is therefore asked to approve an action that may be refused `out_of_scope` at consumption: fail-closed, wasteful of a human's attention, and stated rather than discovered |
| `@protect` to `Control.execute`, `APPROVE`, **presenting** pass | **runs**, before the precondition recheck (§5.7) | this is where the action is taken |
| `@protect` to `Control.execute`, `DENY` | **not called** | already refused; a provider call would turn an unreachable source into a second failure for an action nobody was going to run (`v0.7 §6.6`) |
| `Control.execute` called directly | **runs**, identically | the caller built the action; the provider is the caller's too |
| `Control.evaluate` | **not called** | it writes nothing and consumes nothing (`v0.3 §4.3.1`'s own cell); a network fetch on a path documented as a pure decision would change what that path is |
| `Control.resume` | **not called** | `v0.7 §6.8` refuses a provider here because a refusal strands a reservation the remote may already be acting on, and a scope provider has the identical problem |
| `Control.delegate` / `Control.revoke` | **not called** | no action and no resource to place |
| The gateway's `tools/call` | **not called** | it builds an `Action` and calls `Control.execute`, and has no `scope=` to pass. See the limitation below |
| `ctrlrun.acs`'s request hook | **not called** | same shape, same limitation |
| **`ctrlrun.verify.run`** | **it CONSTRUCTS one** | `v0.3 §4.3.1` says verify "drives the rows above" with its own provider for G16, and §8.1 has it build a raising provider to grade G23. An earlier draft folded this into "the evidence commands take no action", which contradicted §8.1 by nine pages |
| **An adapter's protected tool** to `@protect` to `Control.execute` | **runs** | `v0.3 §4.3.1` row 9 is "yes, `@protect` does, from the bound call": it reaches `Control.execute` like any other protected call. An earlier draft said adapters "answer approvals, they do not originate actions", which is true of `InterruptApprovalProvider.wait` and **false of this row**, which is the safety-relevant one |
| `ctrlrun.adapter.needs_approval` to `Control.evaluate` | **not called** | it is `Control.evaluate`, above |
| `ctrlrun.adapter.InterruptApprovalProvider.wait` | **not called** | it records an answer; it originates no action |
| `ctrlrun mcp-operator`'s read tools | **not called** | they are consulted for nothing and take no action |
| `ctrlrun mcp-operator`'s write tools | **not called** | they grant, deny and resolve; none originates an action |
| observe mode | **runs, and refuses nothing**: the report says the action would have been refused out of scope (§4.2.1) | |

**The gateway, ACS and adapter-without-a-task rows are a real limitation and not an oversight**, so they are stated as one:
a deployment that wants scope enforcement on those paths does not get it in v0.9, and §1.1 carries
the sentence. Giving them a provider is a gateway configuration surface, which is its own amendment.

A test counts the provider's calls on each row, on `v0.7 §6.8`'s pattern.

### 5.3 The ordering is the safety argument, and it is `v0.7 §6.2`'s unchanged

The provider is called **strictly before the reservation**, on the presenting pass, before **every**
call to `_take`. "Every" because `_secure` may take twice, once more after a `reconcile` hook moves
an `AMBIGUOUS` record (`v0.2 §2.3`), and the hook is a network call whose duration would otherwise
sit inside the window.

Move it after the reservation and a *scope check* becomes capable of producing an ambiguous effect:
the reservation is held, the provider hangs, the lease lapses, and the record is `AMBIGUOUS` with
nobody knowing whether anything happened. A control that can manufacture the state it exists to
prevent is worse than no control, and this is the same paragraph `v0.7 §6.2` wrote for the same
reason.

### 5.4 The provider returns the scope; the kernel matches

```python
provider: Callable[[Action], Mapping[str, Any]]
```

It returns the principal's assigned scope. **The kernel** matches the action's resource against it,
using the `contains()` relation `authority.py:250` already implements for every other pattern
dimension.

**Rejected: a provider returning a boolean or a decision.** Three reasons, and the third is the one
that settles it:

1. It makes the provider the authorizer and the kernel a caller, which inverts the relationship
   every other check in this project has with its inputs.
2. The evidence trail would record only that something said yes, which is the sentence v0.8 spent a
   whole milestone deleting about approvers.
3. The matching rule would live outside the kernel, where no test in this repository reaches it, and
   where two operators would write it two different ways. `contains()` is tested against the segment
   relation of `v0.3 §5.5`; a provider's own matching is tested against nothing.

### 5.5 The hash reaches the receipt; the scope never does

The returned scope is hashed through `canonical_bytes` with its own domain tag, and the hash is what
lands on the receipt.

`v0.7 §6.10` is the precedent and the reason is the same: evidence must be verifiable without being
a copy of the operator's data. A scope is a list of what a principal may touch, which is exactly the
kind of data an operator would be alarmed to find written into every receipt, and an evidence store
is not the place to accumulate a second copy of an authorization system's state.

The domain tag keeps a scope hash from ever equalling a precondition fingerprint over the same
mapping, which matters precisely because §5.7 permits both to be configured.

### 5.6 Three refusal reasons, distinct, each with its own test

| Situation | Refusal |
|---|---|
| The provider raises, returns a non-mapping, or returns something the canonicalizer refuses | `ActionDenied`, reason `scope_unavailable`. **Nothing reserved, nothing executed.** This is G23 |
| The provider answers and the action's resource is not in the returned scope | `ActionDenied`, reason `out_of_scope` |
| A `scope=` that is not callable | `InvalidArgument`, **at decoration time** for `@protect` and at call time for `Control.execute`, which is exactly `v0.7 §6.2`'s rule for a non-callable `preconditions=`. Stated as a concrete check rather than as "a configuration that cannot be coherent", because the vaguer wording told an implementer nothing about what to test |

They are distinct because a test asserting only the exception type cannot tell which guard fired,
and a test that cannot tell is the first of `CONTRIBUTING.md`'s four shapes of a false green. Every
test in §9's item 2 subsection asserts the reason.

**Absent means absent.** A call naming no provider behaves exactly as 0.8.0 did, and §9 requires a
test that drives the whole path and compares the receipt field by field, not a paragraph asserting
it.

### 5.7 A deployment that configures both

Permitted, and both run. The precondition fingerprint is checked on the approval path as `v0.7 §6`
specifies; the scope provider is checked on every protected action as §5.3 specifies. **The scope
provider runs first**, because `out_of_scope` is a statement about authority and
`precondition_changed` is a statement about freshness, and an operator reading a refusal is better
served by being told the principal never had the right to the record than by being told the record
moved.

Both hashes reach the receipt, under their own field names and their own domain tags. Neither
substitutes for the other and configuring one does not satisfy the other's requirement.

### 5.8 The ceiling on every claim §5 makes

**A scope provider is worth exactly what its source is worth.** If the system that answers "whose
record is this" is wrong, compromised, or stale, the kernel enforces a wrong answer precisely and
records having done so. Nothing here validates the provider's source, and nothing could.

**`SPEC-v0.7.md` §6.7's residual window applies, and where both providers are configured it is
wider than v0.7 left it.** The check cannot run inside the atomic reservation write, so there is a
window between the scope being fetched and the reservation being taken in which the scope could
change. No sentence anywhere may imply this check is inside the atomic write, because it is not.

**The widening is stated rather than glossed**, because an earlier draft of this section said
"unchanged" and that was false. §5.7 runs the scope provider **before** the precondition recheck, so
in the both-configured case the precondition's own fetch-to-reservation window now contains a second
network call of unbounded duration, and a slow scope provider is exactly the thing that makes a
precondition fingerprint stale. `control.py:2050-2054` carries a standing instruction that the
recheck sits immediately before `_take` with nothing between them, and putting scope *before* the
recheck obeys its letter while widening the window it exists to narrow.

**It is worth it, and the reason is diagnosability.** `out_of_scope` is a statement that the
principal never had the right to the record; `precondition_changed` is a statement that the record
moved. An operator handed the second when the first is true will go looking for a race that is not
there. The alternative ordering, recheck then scope, keeps `v0.7 §6.7`'s window exactly as it was
and reports the wrong one of the two refusals; item 2 implements the ordering above and the PR body
carries this trade so a reviewer can overturn it.

---

## 6. Task-bound authority

### 6.1 What it is

One more dimension on a grant, attenuated by the same `child ⊆ parent` rule as actions, resources
and environments. Mechanically the smallest thing in this milestone, and the reason it is here is
that it is the object v0.10 propagates across an A2A hop.

```yaml
authority:
  grants:
    payments-agent:
      subject: {agent: "payer"}
      actions: ["payments.*"]
      tasks: ["invoice-run-*"]
```

### 6.2 Containment, and the refusal

`contains()` on the `tasks` dimension, with the same segment relation `v0.3 §5.5` defines, and the
refusal is an `AuthorityEscalation` **naming the dimension**, exactly as `authority.py:1105` already
does for every other. A refusal that did not name the dimension would be the least diagnosable one
in the file, which is the same observation `SPEC-v0.8.md` §5.2 made about `delegable`.

### 6.3 The task id arrives from the caller

From the caller, and from nowhere else. Nothing reads a prompt, an argument name, a heuristic or a
model output to decide which task an agent is on.

This is on the roadmap's do-not-build list by name, and it is the line between a containment
primitive and a product that guesses. A kernel that inferred the task would be making an
authorization decision from attacker-influenced text, which is the failure mode this entire project
exists to refuse.

### 6.3.1 How the task id reaches the kernel, and why it is not on the `Action`

§6.3 says it comes from the caller. **This section says by what name**, because §10 must freeze it
and because the obvious route is catastrophic.

**`task=` on `@protect` and on `Control.execute`**, carried to the authority evaluation through the
same context mechanism `v0.7 §6.2` uses for the precondition fingerprint and `v0.6 §7.1` uses for
`policy_hash`. It is **not** a field on `Action` and it does **not** enter `canonicalize`.

**Why not `Action.task`.** `Action` is a frozen dataclass (`action.py:203-215`) and `canonicalize`
builds a fixed payload (`action.py:332-348`). Adding a field to that payload **moves every action
hash in existence**: every stored approval binds to an `action_hash` (`v0.1 §4`), so every
outstanding approval in every deployment would stop matching the action it was granted for, and
every receipt's hash would cease to reproduce. That is not a migration, it is a break, and no
milestone before 1.0 gets to take it for one dimension.

**What that costs, stated plainly, because it is a real cost and not a technicality.** The action
hash is **silent about the task**. So an approval does not bind a task: an approval granted while
the agent was on `invoice-run-7` can be consumed on `payroll-3` as far as the *approval* is
concerned.

**And the residual is larger than "a different task", which is worth stating precisely.** Containment
catches a task the grant's pattern does not match. Under the natural configuration, the one §6.1
ships as its example (`tasks: ["invoice-run-*"]`), **an approval is fungible across every concrete
run**: a human approves a refund while the agent is on `invoice-run-7`, the agent consumes it on
`invoice-run-9`, containment passes because both match the pattern, and nothing catches it. That is
the real cost, and a section headed "what that costs, stated plainly" owes it rather than the
easier version.

**What stands in for it**: authority is evaluated on the execute path, against the grant, with the
task in hand, every time. So the `payroll-3` consumption is refused by §6.2's containment check
rather than by the approval machinery. The refusal happens; it is a different guard than a reader
might assume, and §6 says which so that nobody documents it as the other one. **A yes is
attribution until an entitlement check stands behind it** is `v0.8`'s rule, and this is its
quantitative cousin: the approval attributes, the grant decides.

Binding the task into the action hash is a `v0.1 §2.3` amendment with a migration story, and it
belongs to whatever milestone is willing to pay for one. This one is not, and §12 carries it.

### 6.3.2 Where the task binds: `Control.resume` is the row that would otherwise break

The **second** of the two columns the milestone's plan requires on `v0.3 §4.3.1`.

| Entry point (all thirteen of `v0.3 §4.3.1`'s rows) | Task | Why |
|---|---|---|
| `@protect` to `Control.execute` | from `task=`, and §6.4 refuses a task-bound grant with none | the main case |
| `Control.execute` called directly | from `task=`, same rule | |
| `Control.evaluate` | from `task=`, same rule | it must agree with `execute` or `needs_approval` is told the wrong thing, which is why §10 freezes `task=` on it too |
| **`Control.resume`** | **not evaluated on this dimension** | see below. NOT `v0.3 §5.6.1`'s evaluated-and-recorded, which would put `AUTHORITY_DENIED` on the only receipt the action gets |
| `Control.delegate` / `Control.revoke` | the child's `tasks` are contained in the parent's (§6.2) | delegation-time, not action-time |
| The gateway's `tools/call` | **no task**, so a task-bound grant refuses it | the limitation below |
| `ctrlrun.acs`'s request hook | **no task**, same | the limitation below |
| `ctrlrun.verify.run` | it supplies one when it builds a task-bound scenario | G24 is graded, so it must |
| An adapter's protected tool to `@protect` | from `task=` if the adapter's caller passed one, else none | it reaches `@protect` like any protected call, so a task-bound grant refuses it unless the caller supplies one |
| `ctrlrun.adapter.needs_approval` to `Control.evaluate` | as `Control.evaluate` | |
| `InterruptApprovalProvider.wait`, the `mcp-operator` read tools, the `mcp-operator` write tools | **no task, and none needed** | none originates an action |

**Why `resume` is the row that would otherwise break.** `Control.resume` evaluates authority at
`control.py:1578` and takes no `task=`. The action is rehydrated from the store
(`control.py:1557`), and §6.3.1 keeps the task off `Action`, so the rehydrated action carries none.
Under a grant naming `tasks`, §6.4 would fire on **every resumed leg**.

**And `v0.3 §5.6.1`'s "evaluated and recorded, not re-decided" does NOT fix this. An earlier draft
claimed it did, and the probe says otherwise.** That section's own following sentence:

> The resumed leg therefore appends `AUTHORITY_RESOLVED` or `AUTHORITY_DENIED` and records the
> combined §4.6 decision on its receipt — which, for an MCP multi round-trip or ACS action, is the
> only receipt that action ever gets (§8.3).

"Not re-decided" means the denial does not **block** execution; the code confirms it
(`control.py:1590` sets `Decision.DENY` and `control.py:1606` proceeds). It does **not** keep the
denial off the receipt. So citing `v0.3 §5.6.1` here would have specified precisely the outcome this
section exists to prevent: every resumed leg's only receipt saying the action was denied by
authority, in every task-bound deployment.

**What v0.9 does instead: the task dimension is not evaluated on the resumed leg at all.**
`_authority_result` is told the leg is a resumption and skips that one dimension, evaluating every
other exactly as today. The receipt is what 0.8.0 wrote.

**This is a third mode, and it is named rather than borrowed.** `v0.3 §5.6.1` has two, evaluated-and-
decided and evaluated-and-recorded; this is *not evaluated*, on one dimension, on one path. It gets
its own sentence because an implementer reaching for `v0.3 §5.6.1` gets the wrong one, as this document did.

**What it costs, and it is the real residual of §6.** A resumed leg is **unbound by task**. An agent
that suspends on a task it holds and resumes is not re-checked on that dimension, so task binding
constrains the first leg of a multi round-trip and not the continuations. Task binding limits blast
radius rather than detecting a hijack (§1.1), and this is one of the places that sentence is doing
work. Recovering the first leg's task would mean stamping it onto `EXECUTION_STARTED` so
`_resumed_context` (`control.py:1621-1666`) could read it back, which is a receipt-and-event change
v0.9 does not make and v0.10 will want anyway, since a task crossing a hop is exactly its subject.

**And the ambient-context hazard is closed by the same rule.** `_authority_result` is reached from
`control.py:711`, `:1096`, `:1578` and `:1923`. Were the task read from a context variable at
`:1578`, a resuming process sitting inside some *other* `task=` would evaluate the resumed leg
against an unrelated task. Not evaluating the dimension there forecloses that too.

### 6.4 A grant that names a task refuses an action that names none

Fail closed. Rejected: treating a missing task as matching, because that makes the dimension optional
for the caller and therefore optional for an attacker, and an authorization dimension anybody may
decline to supply is decoration.

### 6.5 A grant that names no task authorises any task

**This is the one decision in v0.9 that could have changed behaviour for an existing grant, and it
was decided in the direction that does not.**

**`v0.3 §5.4` already settled this in writing, and the settling paragraph is the citation that
matters**, so this is a confirmation rather than a judgement call. Its penultimate paragraph
(§5.4 closes with "Where containment cannot be decided, it fails"):

> The asymmetry with §4.2 — where a *root* grant that omits `resources` is unconstrained on
> resources — is deliberate and worth saying out loud. A root grant is written by an operator, in a
> file under review, and its omissions are that operator's decision. A delegation is created at
> runtime by a principal the threat model does not trust, and its omissions are exactly what an
> attacker would write.

(Quoted verbatim from `SPEC-v0.3.md`, punctuation included.) The last clause is the one that
decides v0.9's question: a root grant's omissions are an operator's decision, and no grant anybody
has written names a task.

**The task dimension inherits that asymmetry unchanged.** §5.4 constrains a *child* dropping a
dimension its *parent* constrains, and that is preserved exactly: a child omitting `tasks` under a
parent that names them is rejected, per §6.2. It says nothing about a root grant's silence.

**The upgrade consequence, stated because it is why this matters.** Read the other way, v0.9 would
refuse at 0.9.0 every action that succeeded at 0.8.0, under every grant anybody has written, because
no grant in existence names a task. It would have broken every existing deployment on upgrade, and a
milestone that did that would have got `v0.3 §1.2` backwards.

### 6.6 A break-glass envelope attenuates on this dimension by the ordinary rule

`SPEC-v0.8.md` §5 shaped the envelope as an ordinary `Grant` plus `max_ttl`, deliberately, so that
v0.9 would attenuate it rather than meet a second kind of authority. Item 1 **asserts** this with a
test at the second level, on T337's pattern, and builds no second path.

If item 1 finds it needs a second path, it stops and reports, because that would mean v0.8's shaping
failed and the maintainer needs to know before item 1 works around it.

### 6.7 `_canonical_grant` renders the task dimension

Same field list, same reason as §2.8: a dimension outside the hash is a dimension an operator can
widen without the hash moving.

---

## 7. The operator surfaces

Item 6. What v0.9 built has to be visible to the person who gets paged, and visible without a
management plane: `ctrlrun receipts`, `inspect`, `effects`, `stats` and `--json` are the interface,
and §12 keeps a dashboard out of this repository.

### 7.1 No new command

`inspect`, `effects` and `stats` are extended. **No new CLI command is added**, and §10 names none.

A new command is a surface this project keeps forever, and the question an operator asks here is
not a new question: it is *what is the state of this thing*, which is what `inspect` answers about
an approval, an effect and a delegation already. A budget is one more thing it answers about.

### 7.2 Consumed, held, and why it is held

Three numbers per budget, and the third is the one that matters at 3am:

| Shown | From |
|---|---|
| **consumed** | the un-released sum over the rolling window (§2.5) |
| **held** | the part of that sum whose effects are not `COMMITTED` |
| **why**, per held charge | the `effect_key` holding it and that effect's state |

**The "why" is the deliverable, not a nicety.** A budget that refuses while an operator can see it
is nowhere near its limit looks like a bug in the kernel, and the true explanation is always the
same shape: some effect is `AMBIGUOUS` and nobody has resolved it (§4.2, R2). Without the third
column an operator cannot get from the refusal to `ctrlrun resolve`, which is the action that
actually clears it. With it, the path is one command long.

`--json` shapes are **additive**: a 0.8.0 consumer of the same command keeps working, which T436
asserts rather than assumes.

### 7.3 Growth, and what an operator may do about it

The ledger only grows: the kernel deletes no row, on the rule that it does not quietly delete
evidence (§12). §3.5 bounds what the *query* touches, which is the correctness question; this is the
operational one.

**Rows older than the longest window on any budget of a grant cannot affect any future decision.**
That is the sentence an operator needs, and it is a consequence of §2.5 rather than a promise this
milestone implements: it means such rows may be archived out of the live store by whatever an
operator already uses to archive a database, without changing what the kernel decides. ctrlrun
ships no retention command, no vacuum, and no policy key that expires evidence. **What it owes here
is the invariant that makes somebody else's retention safe**, and that invariant is stated in the
paragraph above.

`stats` reports the row count so growth is observable before it is a problem.

**One caveat on archiving, because the invariant above is about decisions and not about evidence.**
An `AMBIGUOUS` effect older than the longest window still **holds** a charge that §7.2 must display.
Archiving its row is decision-safe and release-safe (§4.4's compare-and-set is a no-op on a row that
is gone) and it drops that charge from the operator's view. An operator archiving a live ledger
excludes un-released rows, and this sentence is why.

### 7.4 What `verify` reports

G22, G23 and G24, each graded or `N/A` (§8). **At least one shipped example exercises a budget, a
scope and a task**, so this milestone's guarantees are not all `N/A` on everything this repository
ships, which item 7 checks and item 6 is the cheaper place to fix.

---

## 8. The guarantees

`ctrlrun.guarantees/v5` is G1 to G24. **The catalogue moves once**, with item 1's G24, and G22 and
G23 join it with their items. No stub rows: a guarantee that reports anything before its check
exists is a false green, which is what 0.6.1 had to fix.

Three, and v0.9 does not invent a fourth. `ROADMAP.md` assigns G22 to G24 to v0.9, twice and
explicitly. **It does not name G25 and gives v0.10 no exit criterion at all**, so the reason a fourth
is refused here is the catalogue's own version-order rule (`v0.4 §2.3`: a guarantee that is added
takes the next one) and not an assignment that exists. Both `ctrlrun verify` and the OWASP pages
refer to guarantees by id, so a fourth taken here would be a number v0.10 then has to work around,
and renumbering is the maintainer's change to make. **Items 3, 4 and 6 ship without a guarantee id**, and that is recorded here rather
than left to look like an oversight.

| Id | Title | Width | Positive control | `N/A` when |
|---|---|---|---|---|
| G22 | `held budget refuses next reserve` | 32 | a budget not exhausted permits the action | no grant in the document carries a budget |
| G23 | `a failing scope provider refuses` | 32 | a provider that answers permits the action | no scope provider is configured (**and §8.1 argues this one**) |
| G24 | `grant refused off its task` | 26 | a grant on the task it names permits the action | no grant in the document names a task |

G22's title says **held** and not *exhausted*: what refuses the next reserve is a budget whose
consumption is held by an effect nobody has resolved, and a title saying "exhausted" would describe
the ordinary case and miss the one the guarantee is about. G24's says **grant refused** and not
*action refused*, because what is compared is the grant's task dimension against the action's task,
and the refusal names that dimension (§6.2).

**Every `N/A` reason is a statement about the operator's document**, per `verify/guarantees.py`'s own
module docstring. `N/A` is excluded from the denominator, so a false `N/A` is a false green, and a
guarantee that could not have failed is not a pass.

### 8.0 Adding two containment dimensions changes G9, and G9 breaks if it is not told

`DIMENSIONS` (`authority.py:126`) is a six-element tuple, exported, and `verify/scenarios.py`
iterates it to build G9's widened children (`scenarios.py:2091`) and reports
`f"{len(exercised)} of {len(DIMENSIONS)} dimensions"` (`scenarios.py:2118`).

**Two ways this goes wrong, and both are silent.**

- Add `budgets` and `tasks` to `contained_dimension`'s ordered checks **and** to `DIMENSIONS`, but
  leave G9's `_narrowed` helper copying only the six fields it copies today
  (`scenarios.py:4050-4059`): every widening then returns `"budgets"` as the first violated row,
  `offending != dimension`, and G9 raises `VerifyInternalError` on any document carrying a budget.
  Which §7.4 requires a shipped document to carry.
- Add them to `contained_dimension` and **not** to `DIMENSIONS`: G9 reports "6 of 6 dimensions",
  stays green, and never exercises either new dimension. That is the false green §9's rule 3 exists
  to refuse, and it is the more likely mistake because it is the one nothing turns red.

**And a third way, which is loud rather than silent, and which `grep -rn DIMENSIONS src/ tests/`
finds in ten seconds.** Two existing acceptance tests consume the name and an earlier draft of this
section named neither:

- `tests/test_verify_authority.py:92` asserts **exact list equality**:
  `results["G9"].detail["dimensions_exercised"] == list(DIMENSIONS)`, with
  `dimensions_unconstrained == []`. That is a `SPEC-v0.4 §8` acceptance test, so the definition of
  done requires it to keep passing. It goes red unless the G9-selected grant in
  `examples/authority/payments.yaml` carries **both** a budget and a `tasks` key.
- `tests/test_verify_authority.py:153` parametrizes over `DIMENSIONS`, so it silently gains two
  cases, which the inline fixture must satisfy.

**So `DIMENSIONS` grows to eight, `_narrowed` and `_widen` carry `budgets` and `tasks`, the
G9-selected shipped grant carries both, and §9.6 asserts G9 exercises eight of eight.** Item 1 lands
`tasks` and item 3 lands `budgets`, so each moves the count by one, each must update the shipped
example in the same PR, and neither may leave the helper behind. **This couples §8.0 to §2.4.1**: a
budget may only sit on a grant whose actions resolve an effect key, so the grant G9 selects must be
one of those.

### 8.1 G23's `N/A` is the one that is not a statement about the document

`verify/guarantees.py`'s module docstring says every `N/A` reason is a statement about the
operator's **document**, and `verify` reads a document. **G23's is not**: a scope provider is a
Python callable passed at decoration or call time, and no document key mentions it, so `verify`
cannot see whether one is configured.

Two ways out, and this document takes the second. Rejected: letting G23 report `N/A` on a fact
`verify` cannot observe, which would be a reason that is not true of anything it read, and a false
`N/A` is a false green.

**Taken: G23 is graded against a scenario `verify` constructs**, the way `v0.4 §3` has it construct
every other scenario, wiring a provider that raises and asserting nothing was reserved and nothing
executed. It therefore **never reports `N/A`**, and the `N/A` column above says "no scope provider
is configured" for completeness of the table rather than as a reachable state. §9.6's T435 excludes
G23 for that reason, and says so rather than quietly skipping it.

This is the exception `v0.4 §2` did not anticipate: a guarantee about a **code** surface rather than
a document one. It is argued here rather than left for an implementer to notice, because the
noticing would happen at the point of writing the scenario and the cheap wrong answer, an unreachable
`N/A`, is the one that passes CI.

**G22 is exercised under the v0.6 multi-process standard against Postgres.** `ROADMAP.md` says so in
the exit criterion, by name, and it is not optional: a counter that is correct in one process is not
a claim about anything an operator runs. Threads against SQLite are not evidence about Postgres.

Titles are at most 32 characters, against `report._TITLE_WIDTH` (`verify/report.py:37`), and a wider one
breaks the table's alignment. v0.7 had to shorten G12's and v0.8 had to shorten three; the widths
above are counted rather than estimated, and two of the three were over on the first draft.

---

## 9. Acceptance tests

T379 onward. v0.8 ended at T378. One subsection per item; each item writes its own and item 7
asserts the set is complete.

Every test in this section obeys three rules the milestone repeats because they are where its
defects will be:

1. **Every refusal test asserts the reason and, where there is one, the dimension by name.** Not the
   exception type alone.
2. **Every test of a budget, a hold or a release that makes a claim about concurrency runs
   multi-process against Postgres.** Threads against SQLite test a different program.
3. **Every guarantee has a positive control that could have failed.**

### 9.1 Item 1: task-bound authority (§6)

- T379 a grant naming `invoice-run-*` permits an action on `invoice-run-7`.
- T380 the same grant refuses an action on `payroll-3`, and the refusal names the `tasks` dimension.
- T381 a grant naming a task refuses an action carrying no task (§6.4), naming the dimension.
- T382 a grant naming no task permits an action carrying a task (§6.5), and a receipt from it is
  what 0.8.0 wrote **but for the `schema` tag and the absent v6 keys**. Stated that way because
  `to_dict()` stamps the current schema (`v0.7 §6.11`), so a literally field-for-field assertion
  cannot pass once §10.1 bumps the receipt, and a test asserting the impossible gets deleted rather
  than fixed.
- T383 a child naming three tasks under a parent naming two is rejected at delegation.
- T384 a child omitting `tasks` under a parent naming them is rejected (`v0.3 §5.4`).
- T385 a task changed in the document moves the policy hash (§6.7).
- T386 a break-glass delegation is attenuated on `tasks`, at the second level, on T337's pattern.
- T386a `Control.resume` under a task-bound grant does **not evaluate** the task dimension (§6.3.2):
  the resumed leg is not denied, the receipt is what 0.8.0 wrote, and it does not say
  `AUTHORITY_DENIED`. The test that proves the rule is real is the one where the resuming process
  sits inside an unrelated `task=` context and the leg still is not evaluated against it.
- T387 a `ctrlrun.policy/v7` `tasks` key in a `v6` document is refused, in `policy.py`'s existing
  older-reader shape.

### 9.2 Item 2: scope providers (§5)

- T388 a provider returning a scope containing the resource permits the action. **G23's positive
  control.**
- T389 a provider returning a scope not containing it refuses, reason `out_of_scope`, nothing
  reserved.
- T390 a provider that raises refuses, reason `scope_unavailable`, **nothing reserved and nothing
  executed**. This is G23.
- T391 a provider returning a non-mapping refuses with the same reason.
- T392 a provider returning something the canonicalizer refuses (a float, a non-string key) refuses
  with the same reason.
- T393 the provider is called **before** the store call, proven by a provider that records the
  store's state when invoked.
- T394 the provider is called before the **second** take, after a `reconcile` hook moves an
  `AMBIGUOUS` record.
- T395 a provider that hangs past the lease leaves nothing reserved, because it never reached the
  reservation.
- T396 no provider configured: the whole path is 0.8.0's, receipt compared field by field but for
  the `schema` tag and the absent v6 keys (see T382).
- T397 the scope hash reaches the receipt and no scope content does.
- T398 a precondition provider and a scope provider both configured: both run, scope first, both
  hashes on the receipt under distinct fields (§5.7).
- T398a a `scope=` that is not callable raises `InvalidArgument` at decoration time under `@protect`
  and at call time under `Control.execute` (§5.6's third row, which had no test).
- T398b the provider is called on `ALLOW`, on `APPROVE`'s presenting pass and in observe mode, and
  **not** on `DENY`, `Control.evaluate` or `Control.resume`, counting its calls on each (§5.2.2).

### 9.3 Item 3: the budget in the document (§2)

- T399 a well-formed budget loads and renders.
- T400 a child limit above its parent's is rejected.
- T401 a child window **shorter** than its parent's is rejected (§2.6, the axis that reads backwards),
  and the test is written with the parent at `PT24H` and the child at `PT1H` so the 24x figure is on
  the page next to the assertion.
- T402 a child window **longer** than its parent's is accepted, same limit.
- T401a **belongs to the item that owns §2.4.1's check, and that is not item 3.** §2.4.1 was a load
  error in a draft and is not one now: the probes in that section show a loader cannot see a
  decorator-supplied `effect=` and cannot run at all on the standalone-authority path. The check is
  at execute time, after the effect key resolves and before `_secure`, so it is **item 5's**, beside
  the charge it protects. Listed here rather than deleted, because a test id that vanished would
  look like an oversight.
- T402a the matching rule of §2.6.1, over the two-budget parent §2.2 exists for: each of the four
  rows of that section's worked table is a case.
- T403 a child omitting a budget its parent carries is rejected.
- T404 a child adding a budget on a metric its parent does not budget is accepted.
- T405 a budget changed in the document moves the policy hash; a budget in a break-glass envelope
  does too (§2.8).
- T406 two budgets on one metric over two windows both load and both are kept in document order.
- T407 a float limit, a `Decimal` limit, a decimal **string** limit (`"100.50"`), a negative limit,
  a zero window and a non-numeric limit are each refused at the loader **and** at
  `Grant.__post_init__`, each with its own message. The string case is the one worth writing first:
  YAML hands a quoted number back as a `str`, so it is the shape an operator actually produces, and
  a coercion here would put the drift back through the door `v0.1 §2.3` closed (§2.3).

### 9.4 Item 4: the ledger and the store amendment (§3)

- T408 a charge and its reservation are in one transaction: a failure after the charge leaves
  neither.
- T409 **multi-process, Postgres**: N processes racing one budget spend at most the limit, **run
  repeatedly** (§3.6.2), each process taking a distinct effect key so the effects table's own
  uniqueness does not serialise them and hide the defect.
- T409a removing the `FOR UPDATE` makes T409 fail, and fail *reliably* across repeats (§3.6.2).
- T410 the same, SQLite under `BEGIN IMMEDIATE`.
- T411 the A1 re-insert branch of `v0.6 §4.3.2` does not double-charge (§3.4).
- T412 a three-level delegation charges all three grants (§2.7).
- T413 a grant with no budget takes 0.8.0's reservation path, proven by the receipt (but for the
  `schema` tag and the absent v6 keys, see T382) and by the absence of any ledger row.
- T414 `0007_budget_ledger` runs on both backends and a 0.8.0 store upgrades; **and a 0.8.0 binary
  opening the migrated database refuses at open with `SchemaMismatch` naming the migration**
  (`v0.6 §3.5`, on `v0.7`'s T264 pattern). The reverse direction is the one that gets forgotten.

### 9.5 Item 5: consumption, reconciliation and release (§4)

- T415 to T433: **one test per row of §4.2's table**, nineteen rows, nineteen tests. Seven of those
  rows were added by this spec's two review rounds rather than by its drafting, and they are the
  ones an implementer would not derive: the attempt ceiling releasing after a won reservation,
  `begin_execution` refused after a won reservation, observe mode reserving outside `_take`, a
  suspension holding a charge, and the three paths where `fail_effect`, `commit_effect` or
  `mark_ambiguous` is itself refused (the first two through `_unrecorded`; `mark_ambiguous` logs and
  folds the refusal into the error text instead, which is why §4.2's row cites the range without
  claiming the call).
- **The `fail_effect`-refused test drives the lapse concurrently**, because the whole row is that the
  executor proved nothing happened and the charge is nonetheless held. A test that calls
  `fail_effect` on a record still in `EXECUTING` asserts the row above it instead and passes.
- T434 a renewal after `FAILED` charges again, and the two rows are distinct by `attempt` (§4.3).
- T435 an A2 re-issue of `fail_effect` releases once, not twice (§4.4). **Written as a mutation of
  the release into a decrement**, which is the implementation this row exists to forbid; a test that
  calls `fail_effect` once cannot tell the two apart.
- T436 an exhausted budget refuses, reason distinct, naming grant, metric and window, **and not the
  remaining amount** (§4.5), asserted by word.
- T437 the refusal produces a **receipt** and an `ACTION_DENIED`, and **no `APPROVAL_DENIED`**
  (§3.3.2). The last clause is the assertion: the existing `ActionDenied` handler appends one
  unconditionally, so a budget refusal routed through it would fabricate an approval denial.
- T438 the refusal names the **ancestor** that refused, where a child is within its own budget
  (§2.7).
- T439 observe mode charges nothing and reports what would have been refused (§4.2.1).
- T440 a **negative** metric value is refused (§2.3), and the test that proves it matters alternates
  `+n` and `-n` and asserts the budget is not thereby reset.
- T441 **G22, multi-process against Postgres**: a budget exhausted by an ambiguous effect refuses the
  next reserve until reconciled, and releases on `FAILED`.
- T442 G22's positive control: a budget not exhausted permits.
- T443 the **conformance** case, in two parts (§3.3): a store that accepts `charges=` and does not
  evaluate the predicate fails the store suite; **and so does one that evaluates it in a statement
  separate from the insert**, raced multi-process through `conformance/store/worker.py`. The second
  part is the one that matters, because the first is the failure nobody ships and the second is
  `postgres.py:1106`.
- T443a `Control` refuses to construct against a store that does not refuse the `limit: 0` probe,
  when any grant carries a budget (§3.3).

### 9.6 Item 6: the operator surfaces (§7)

- T444 `inspect` shows consumed, held, and **why** the held part is held, naming the `effect_key`
  and its state (§7.2), assembled by joining through `get_effect` rather than from a ledger column.
- T445 `stats` reports the ledger row count without being given a grant id (§7.3, §3.3.3).
- T446 `verify` reports G22 to G24 against the shipped examples, none of them `N/A`.
- T447 each `N/A` reason, where one is produced, is a true statement about the document under test.
  **G23 is excluded by name**, because §8.1 makes its `N/A` unreachable.
- T448 **G9 exercises eight of eight dimensions** (§8.0). The test asserts the count and that
  `budgets` and `tasks` are each the offending dimension for their own widening, which is what a
  "6 of 6" false green would hide.
- T449 the `--json` shapes are additive: a 0.8.0 consumer of the same command does not break.
- T450 no new CLI command appeared (§7.1), asserted against the command list.

### 9.7 Item 7: release

- T451 `ctrlrun.receipt/v6`, `ctrlrun.policy/v7` and `ctrlrun.guarantees/v5` each complete, with
  every frozen field written by something.
- T452 `import ctrlrun` still imports nothing from an extra, and `ctrlrun demo` still runs under 60
  seconds with no network.

---

## 10. Public API additions, frozen for v0.9

One justification per row. Anything not here is a spec amendment before it is code.

| Addition | Why an existing name does not serve |
|---|---|
| `Budget` (`metric: str`, `limit: int`, `window: timedelta`) | nothing in `authority.py` carries a quantity over a period. `window` is a `timedelta` in Python, an ISO-8601 duration in the document, and **integer seconds in `_canonical_grant`**, because a `timedelta` is not a `PlainValue` (`action.py:19`) and cannot render through `canonical_bytes`. An earlier draft of this row said the document's spelling was canonical; it is not implementable, since `canonical_grants` renders parsed grants and a `Budget` keeps no source text. §2.8 carries the consequence |
| `Grant.budgets` | `constraints` decides one action and cannot count. A `Budget`'s window is a **whole number of seconds**, bounded, because it is stored and hashed as integer seconds: a sub-second window would round to `0` on the way into a delegation row and read back unreadable, dead for ever |
| `Grant.tasks` | no dimension names a unit of work |
| `DIMENSIONS` grows from six entries to eight | **an exported public name whose value changes** (`authority.py:126`, `__all__` at `authority.py:1787`). `verify/scenarios.py` iterates it for G9 and prints `len(DIMENSIONS)`, so this is not a private constant. §9.6 has the test |
| `Charge` | the store needs a value object for what a reservation spends, carrying the whole predicate (§3.3.1). It refuses a negative or non-integer `amount` or `limit` the way `Budget` does: a negative amount **refunds** the budget, which is the compensation §12 forbids, reachable by anyone calling the store directly |
| `charges=` on `reserve_effect` and `consume_approval_and_reserve` | §3.3, half of the milestone's amendment to a frozen protocol |

**All four new names live in `ctrlrun.state`**, beside `StateStore` itself, and are imported from
there rather than from the package root: they are the vocabulary of the store protocol, and a
third-party backend already imports `StateStore` from that module.
| `scope=` on `@protect` and `Control.execute` | `preconditions=` answers a different question (§5.2), and §5.2.1 amends `v0.7 §6.9` to add it |
| `task=` on `@protect` and `Control.execute` | nothing carries a unit of work today, and it cannot go on `Action` without moving every action hash in existence (§6.3.1) |
| `task=` on `Authority.evaluate` | **amends a signature frozen in `SPEC-v0.3.md` §11**, and §10.3 records the amendment rather than slipping it in. `Authority.evaluate(action, *, now, store)` is frozen there; the task has to reach the decision, and `v0.7 §6.2`'s context variables are request-time stamps in `approval.py`, not inputs to an authority decision |
| `task=` on `Control.evaluate` | **also amends a frozen signature** (`SPEC-v0.3.md` §11: "its signature and `Evaluation`'s two fields are unchanged"). Required by §6.3.2: without it `Control.evaluate` and `Control.execute` disagree about a task-bound grant, and `ctrlrun.adapter.needs_approval` routes through `evaluate` |
| `consumptions()` on `StateStore` | §3.3.3: the surfaces and G22 need a read, and `charges=` is write-only. Rows come back **in insertion order**, which is deterministic and identical across the three backends and is *not* a time ordering: host clock skew, which `v0.7 §3` models, inverts `consumed_at` against the id. Its `effect_key` filter was added by item 5 for §8.3's resumed receipt, and §3.3.3 records why the other three could not serve |
| `check_charges` | §3.3.1's predicate in one place, so three backends cannot drift on the arithmetic. Public for the same reason `plan_reservation` is: a third-party store decides with it rather than reimplementing it |
| `Consumption` | what `consumptions()` returns: `grant_id`, `metric`, `amount`, `effect_key`, `attempt`, `consumed_at`, `released_at`. Frozen here because §3.3.3 returns it and §7.2 renders it, and a return type specified nowhere is a spec amendment waiting to happen |

### 10.1 Schemas

**`ctrlrun.policy/v7`**, bumped **once**, by **item 1**. Keys it adds: `tasks` (item 1) and `budgets`
(item 3) on a grant and on a break-glass envelope. Item 3 stacks on item 1 rather than racing it; two
branches racing a schema bump is how a catalogue ends up with a stub row. The older-reader refusal
takes the shape `policy.py` already uses for v3, v4 and v5 keys.

**`ctrlrun.receipt/v6`**, bumped **once**, by whichever of items 1, 2 and 5 lands first. The whole v6
shape is frozen here before any of them starts:

| Field | Written by | Holds |
|---|---|---|
| `task` | item 1 | the task the action was bound to, or absent |
| `scope_hash` | item 2 | `sha256:…` over the returned scope, never its content (§5.5) |
| `budget_charges` | item 5 | which grants were charged, which metrics, how much. On an `observed` receipt it is the counterfactual charge and not a spend (§4.2.1a), which `result` tells apart |

Item 7 asserts every one of them is written by something before the release PR opens. This is
`SPEC-v0.7.md` §12's D27 rule, which v0.8 ran for three items without incident.

**`ctrlrun.guarantees/v5`** is G1 to G24, moved once by item 1 with G24 (§8).

**`ctrlrun.budget/v1`**, added by **item 6** for §7.2, and recorded here rather than slipped in.
Its own document rather than a key inside `ctrlrun.inspection/v2`, because that one answers about an
**action** and this answers about a **grant**: a reader handed one would have to know which of two
shapes it got before it could read either. §7.1's "no new command" is the surface question and a
separate one, and `ctrlrun inspect --grant` keeps it.

| Key | Holds |
|---|---|
| `grant_id` | the grant asked about, document grant or runtime delegation alike |
| `at` | the instant the three numbers were read, because every one of them is a rolling-window answer and stale without it |
| `budgets[].metric`, `.limit`, `.window_seconds` | the budget as declared. Seconds, for `_canonical_grant`'s reason in §10 |
| `budgets[].consumed` | the un-released sum over the window: **the number that decides** (§3.3.1) |
| `budgets[].held` | the part of it whose effects have not reached `COMMITTED` (§7.2) |
| `budgets[].holding[]` | `effect_key`, `state`, `amount`, `attempt`, `consumed_at`: §7.2's "why", one entry per held charge. `state` is `null` where the effect record is gone, which §7.3's archiving paragraph permits |

`ctrlrun.stats/v1` gains `ledger_rows` **additively** and does not move, per §7.3. It is omitted
rather than reported as `0` on a store with no ledger: "no rows" and "this store predates budgets"
are different facts, and a diagnostic that conflates them sends an operator looking for spend that
was never possible.

### 10.3 Two frozen signatures amended, recorded as `v0.3 §11` requires

`SPEC-v0.3.md` §11 freezes `Authority.evaluate(action, *, now, store)` and says of `Control.evaluate`
that "its signature and `Evaluation`'s two fields are unchanged". v0.9 adds `task=` to both, and
`v0.3 §11`'s own rule for changing a frozen name is that the signature is amended in the same item
that changes it. §5.2.1 follows that rule for `v0.7 §6.9`; these two rows follow it here.

Both take `task: str | None = None`. **"An existing caller is unchanged" is nearly true and not
quite**, which is the kind of claim this document has been wrong about before, so it is checked
rather than asserted: `tests/test_verify_authority.py:112-120` monkeypatches `Authority.evaluate`
with `def wrong_reason(self, action, *, now, store)`, and once `_authority_result` passes `task=`
that patch raises `TypeError`. Item 1 updates it in the same PR.

### 10.2 The module map

No new module. Budgets and tasks are `authority.py`; the ledger is `state.py`, `postgres.py` and
`migrations.py`; the scope provider is `control.py` beside `v0.7 §6`'s recheck. A reorganisation of
`control.py` is out of scope (§12) and does not become in scope as a side effect.

---

## 11. Fail-closed table for v0.9

| Situation | Outcome |
|---|---|
| A budget's metric names an argument the action does not carry | **refused** (§2.3). Never zero |
| An action whose effect key resolved to `None`, under a grant that budgets | **refused at execute** (§2.4.1), after the key resolves and before the reservation. **Not a load error**: §2.4.1's probes show a loader cannot see a decorator-supplied `effect=`, and cannot run at all on the standalone-authority path |
| A budget limit is a float, a `Decimal`, a decimal string, negative, or not a number | **load error**, at the loader and at the constructor (§2.2, §2.3) |
| A child grant's budget exceeds its parent's limit, **or shortens its window** | **rejected at delegation** (§2.6): a shorter window over the same limit is a higher rate |
| A parent budget is matched by no child budget on that metric | **rejected at delegation** (§2.6.1) |
| A child omits a budget its parent carries | **rejected** (§2.6, `v0.3 §5.4`) |
| Any ancestor's budget is exhausted | **refused**, naming that ancestor (§2.7, §4.5) |
| The ledger write fails | **the reservation fails with it**: one transaction (§3.3) |
| An ambiguous commit on the reservation | resolved by `v0.6 §4.3.2`'s single re-read, charges included (§3.3) |
| An effect is `AMBIGUOUS` | **charge held**, indefinitely, until a human or a hook (§4.2, R2) |
| A scope provider raises, hangs, or returns the wrong shape | **refused**, `scope_unavailable`, nothing reserved (§5.6) |
| The action's resource is not in the returned scope | **refused**, `out_of_scope` (§5.6) |
| A grant names a task and the action carries none | **refused**, naming the dimension (§6.4) |
| A grant names no task | **permitted** for any task (§6.5), and this is the deliberate exception |
| Anything in v0.9 is misconfigured in a way the kernel cannot interpret | `InvalidArgument`, at decoration time where `@protect` can see it |

---

## 12. Explicitly out of scope

Each with its reason. **Four are the roadmap's own do-not-build list for v0.9** (a consequence
taxonomy, compensation or saga, a fleet-wide budget across stores, anything that reads a prompt to
decide which task an agent is on). The rest are this document's, or inherited from an earlier
milestone's list and restated because they are live temptations here. The distinction is drawn
because an earlier draft attributed all of them to the roadmap, and nine of thirteen were not.

- **A consequence taxonomy.** A budget names a metric, not a class. There is no branch on a metric
  name anywhere, no ranking of two metrics, and no default limit for a metric the kernel thinks it
  recognises. A switch statement over metric names is the first step, and scoring an operator's
  actions is the second.
- **Compensation, or a saga.** Nothing is undone. A budget refuses the next action and has never had
  an opinion about the last one.
- **A fleet-wide budget across stores.** One store, one ledger. `v0.7 §4.6` gave the same answer for
  idempotency tokens: the kernel's consistency claim stops at its store's transaction.
- **Anything that reads a prompt to decide which task an agent is on** (§6.3).
- **A quota endpoint, a spend API, or a published balance.** `v0.3 §1.1`: ctrlrun consumes and issues
  nothing.
- **An automatic expiry on a hold.** It is the refund R2 refuses, on a delay (§4.6).
- **A fourth guarantee id** (§8).
- **Deleting ledger rows.** The kernel does not quietly delete evidence; §3.5 says what bounds the
  query and §7 says what an operator may do about growth.
- **A management plane**: an approval UI, a budget editor, a spend dashboard. `ctrlrun receipts`,
  `inspect` and `--json` are the interface, and a management plane is the Pro track's, on its own
  roadmap, never on a kernel version line.
- **Signed receipts**, which bring key generation, rotation and revocation, which is issuing.
- **A reorganisation of `control.py`.** Not this milestone, and not as a side effect of one.
- **Moving the H1 or the category line.** `ROADMAP.md` records that *action governance* becomes true
  in code when v0.9 ships and that the line moves then. No PR in this milestone touches a marketing
  surface; that is a separate act by the maintainer.
- **Any compliance, conformance, certification or alignment claim.**

---

## 13. What building v0.9 settled

*One subsection per question the drafting could not close, each stating what the code decided and
which section carries it. `SPEC-v0.4.md` §12 through `SPEC-v0.8.md` §14 are the format.*

**This section is empty on purpose, and item 7 writes it in one pass**, per the pace decision the
maintainer recorded in the milestone's plan on 2026-09-12: the spec amendment an item owes as it
lands is its §10 name row, its MUST sentences and its §11 fail-closed row, and the prose explaining
what building settled is written once over the finished milestone rather than six times over
guesses.

**The cost of that, and what pays it.** v0.5's item 6 could tell which parts of that document had
been stress-tested by somebody other than their author by looking for a §13 entry behind them, and
all four of its most serious findings sat in sections that had none. Written at the end, §13 loses
that signal during the milestone. What replaces it: **an item that settles something surprising
leaves a line in its `CHANGELOG` entry when it lands**, and item 7 writes §13 from those lines. An
item whose PR body reports a question it could not settle has already written its §13 entry, and
should say so.

The four open questions this document hands the items are O1 to O4 in the build plan, and each is
answered here in the section that carries it: O1 in §3.3, O2 in §4.5, O3 in §5.2 and §5.7, O4 in
§6.5. An item that finds one of those answers wrong stops and reports rather than working around it,
because all four are load-bearing for a section rather than local to a function.

### 13.0 What the milestone settled about itself

**Every design claim this document made about its own code had to be probed, and the ones that were
not were wrong about a third of the time.** Three adversarial review rounds ran against the drafted
spec. Across them, *every citation* they made was accurate, and roughly three *design claims* per
round were false. The same ratio held for the items' own reasoning and for the reviews of the
finished code: the accurate half is always "this line says X", and the unreliable half is always
"therefore Y happens at runtime".

The rule the milestone adopted after the second round, and which §1's standing instructions now open
with: **probe before you assert.** A ten-line script against the real objects settles in a minute
what cross-module reasoning gets wrong one time in three. Almost every entry below was found by one.

The second-order version of the same lesson, which cost more: a *test* asserting a runtime claim is
itself a claim, and a green test is not evidence that the claim is load-bearing. Items 2, 4 and 5
each shipped guards that nothing exercised, found only by mutating the source and watching the suite
stay green. §9's mutation table is the milestone's answer, and it earned its place.

### 13.1 Item 1: task-bound authority

**A template that cannot resolve must be refused *inside* the recording path.** `@protect(task=
"{run_id}")` with no such argument raised `EffectKeyError` outside every recorder: no
`ACTION_PROPOSED`, no `ACTION_DENIED`, no `denied` receipt, and a caller holding a template error
about an action nothing recorded. Probed rather than reasoned about, which is the entry: 0 events
and 0 receipts before, `ACTION_PROPOSED` + `ACTION_DENIED` and one `denied` receipt after.

`_resolve_effect`'s body became `_resolve_template` over either template, so the effect template and
the task template cannot drift into recording different things for the same class of mistake. The
same shape had been found once before on a store refusal escaping `_secure`'s except clauses, and
`control.py`'s own comment records it; §6.3 carries the rule.

### 13.2 Item 2: scope providers

**Two refusals need two reasons, and observe mode is where that stops being pedantry.**
`_ObservedRefusedError` carried no reason, so `_observe_secure` blocked with a hardcoded
`out_of_scope` for both cases, and a deployment whose scope *source* was down read a counterfactual
saying the record was not the principal's. Observe mode exists to tell an operator what enforce mode
would do; reporting the wrong category is the one way it can be worse than useless. §5.6 carries the
two reasons, and the same argument recurred one dimension over in item 5 (§13.5).

**Three guards were green against a mutated kernel**, all of `CONTRIBUTING.md`'s first shape. The
canonicalizer was removable because the shape guard refused the malformed input first and the hash
never had to; the mapping guard was removable because a list reaches `dict()` inside the hash and
raises there anyway; and observe mode had no scope test at all, so collapsing its two refusal paths
into one was invisible. A subsumed branch may be kept for its message, on the condition that a test
asserts which message it got, and those tests now do.

### 13.3 Item 3: the budget in the document

**§2.6's window axis was inverted in the drafted spec, and the review's exhaustive check is what
established the right one.** 12,871 transitivity triples with zero non-transitive, and 2,515
contained pairs against 300 simulated spend timelines each, with zero soundness violations:
`child.window >= parent.window` is correct, because a *shorter* window is a higher rate and therefore
more authority. The draft's `<=` would have accepted a child at 24× its parent's authority.

**One corrupt delegation row could take down an entire deployment.** An oversized stored window
raised `OverflowError` out of `Authority.evaluate`, and `_candidates` reads *every* delegation row on
*every* evaluation, so one bad row denied nothing and crashed everything, for every principal and
every action, with no event and no receipt to find it by. `OverflowError` was in neither except
tuple. §2.4 carries the bound and the refusal; an unrelated principal's unrelated action now gets
`authority_unreadable`.

**`timedelta(seconds=True)` is a one-second window.** `_is_int` exists in this item precisely
because `isinstance(True, int)`, and it guarded `limit` while `window` went through a cast one field
away. The failure *grants* authority, and the loader refuses the same input, so it was a direct
violation of §2.2's "the model refuses exactly what the loader refuses". A bool trap is not an edge
case in a codebase that already wrote the guard once and stopped one field short.

### 13.4 Item 4: the ledger

**The charge was dropped on both lost-`COMMIT` re-issue branches, which falsified §3.3's own
stronger bar for touching a frozen protocol.** `_resolve_lost_insert` and `_resolve_lost_renewal`
call `_authorize_and_reserve` again after an ambiguous `COMMIT` and did not forward `charges` --
neither resolver even took them. The retried transaction re-inserted the reservation and nothing
else. Probed: `reserved=True charged=0`, and against a budget permitting one spend, driven ten
times, 10 reservations and 0 ledger rows with a real spend of 1000 against a limit of 100.

That is `reserved=1, charged=0`: the exact state §3.3.0's throwaway spike named as *disqualifying*
the alternative design, reproduced inside the chosen one. §3.3's second bar is that one re-read
resolves the reservation and the charge together, and it was not met until this was fixed.

**A concurrency test without a barrier proves nothing, and the milestone learned it twice.** The
item's own race test held 4/4 against a deliberately unlocked implementation: interpreter startup,
importing the package, the connection and the migration check all happen before the contended work
and vary by more than it does, so the processes ran one after another. With a `multiprocessing
.Barrier` the unlocked version spends 2400 against a limit of 1000. A v0.7 test, T247, then flaked
twice on CI in one session with its own guard reporting "nothing was contended" -- the same defect,
in a test written a milestone earlier, fixed the same way.

### 13.5 Item 5: consumption, holds and releases

**§4.2's rule is keyed on the state *reached*, never on the call that tried to reach it**, and the
mutation that proves it matters survived the entire suite before item 5's second review. Moving the
release above the state check makes a *refused* `fail_effect` release the hold on an `AMBIGUOUS`
record, which is a manufacturable refund and the thing the item exists to stop. It is also **not**
an equivalent mutant only in the in-memory store: both SQL backends run the transition in one
transaction and roll it back when the check raises, so there the order is redundant with the
rollback, while the in-memory store mutates a dict under a lock and the order *is* the atomicity.
The code says so now, because a future maintainer reading two of the three backends would conclude
the ordering is arbitrary.

**A refusal nobody can see is not a refusal.** §2.3's and §2.4.1's guards escaped as a bare
`InvalidArgument` with no `ACTION_DENIED` and no receipt, leaving the one record an operator has of
a refused action empty. They also ran *after* the approval gate, so a human could be asked to
approve a refund the kernel had already decided to refuse, and a granted approval was left behind
for an action nothing could execute. Both halves are §2.3's now; the exception type is unchanged,
because neither is a budget running out.

**A duplicate-charge guard killed §2.2's own motivating shape.** "Two budgets on one metric over two
windows is the first thing an operator asks for", and it arrives as two charges differing only in
`limit` and `window`. A guard refusing *any* duplicate `(grant_id, metric)` pair meant the loader
accepted the document, observe mode reported it clean, `ctrlrun verify` could not grade it, and
enforce mode died with no receipt at all. What the guard is actually for is two charges on one
metric carrying **different amounts**, which §3.4's key would silently collapse. §3.3.1 carries both
sentences.

**The resumed leg was reporting a number from a context variable.** §8.3 makes that receipt the only
one an MCP multi round-trip or ACS action ever gets, and `resume` never reset the variable: a
restarted gateway reported no charges for an action that spent, and a gateway that had run another
action since reported *that* action's spend. The ledger is the record, and it is read by effect key
and attempt (§3.3.3). The test that caught the first half had to be written to run in a fresh
`contextvars.Context`, because a test reusing the caller's context is testing the case that works.

**§4.2.1 overclaimed, and the fix was to make the claim true rather than to soften it.** The draft
said observe mode is "how an operator sizes a budget before turning it on". It is not: observe mode
charges nothing, so a deployment observing every action has an empty ledger and the report says no
budget would refuse anything, however much the agent proposes. §4.2.1a states that limit, and
observed receipts now carry the counterfactual charge -- which a probe found to be an empty tuple,
making the sizing path the section described impossible.

### 13.6 Item 6: the operator surfaces

**One word for two numbers makes two commands disagree.** §7.2 defines *held* as the part of the
consumed sum whose effects have not committed, so `ctrlrun effects` says **spent** for a committed
effect and **holds** for every other. A committed charge is never released, because a committed
spend is a spend, and calling that a hold would have `effects` and `inspect --grant` reporting
different things under the same word.

**One document has one producer, and §7.3's row count proved it again.** Adding `ledger_rows` to
`ctrlrun stats` in the CLI alone left the operator MCP server returning a different shape under the
same `ctrlrun.stats/v1` name. `SPEC-mcp-operator §9.1` exists for this and its test caught it
immediately; the count moved into `ctrlrun.reporting` beside every other shared shape.

**A ledger row whose effect record is gone is reported held, not skipped.** §7.3 permits an operator
to archive rows the window can no longer reach, so a store whose effects were pruned but whose
ledger was not is reachable. Skipping such a row would **under**-report `held`, which is the one
direction this view may not err in, because it is the direction that hides a hold from the person
looking for it.

### 13.7 What `ctrlrun verify` settled

**A budget is a configuration fact, and never a defect in verify.** A grant whose budget is smaller
than the vector `_synthesize` picked refuses the action before the guarantee is reached, and verify
reported that as an internal error, exit 3, on guarantees with nothing to do with budgets. This is
the third time the same shape has been found: a shipped example declaring `approvals_required: 2`
did it, then one declaring an approver role did it, and both were fixed by having verify supply what
the document needs. A budget gets the same answer, and verify now sizes its own action vector.

Where no value fits, the guarantee is `N/A` -- and that needed its own reason rather than falling
through to the grant miss, which told an operator that no grant's `resources:` matched, about a
document whose patterns matched perfectly. A budget smaller than any single action in a band makes
that band unreachable, which is worth saying in those words. §7.4's shipped example keeps a budget
large enough that its own approve band is reachable, because a daily budget smaller than one
permitted action is legal and almost always a mistake.

### 13.8 The two review rounds, and the guarantee that was not graded

**Written after the milestone merged**, because both rounds ran against code that was already
pushed and called done, and what they found is the part of this milestone most worth carrying
forward.

**The headline guarantee was passing for a reason that had nothing to do with it.** G22 proves that
a budget holds a charge, and it resolved the charges it fills the budget with through
`Control._charges_for`, which answers from a context variable only `execute` sets. G22 runs it
*before* its own control leg, so it returned `()` whenever nothing had executed in that context yet.
The synthetic hold reserved nothing, the budget was never filled, and the next action ran.
`ctrlrun verify --only G22` therefore reported **FAIL** on `examples/authority/payments.yaml` -- the
status that means the kernel is broken -- while a full run reported `PASS`, because an earlier
scenario's `execute` had left its result in that variable.

Two things make this the most instructive finding here. The first is that **the milestone's own
evidence that budgets work was partly an accident of ordering**, and every green run said otherwise.
The second is how it was found: not by either review, but by a document written to test one of the
review's *mutation survivors*. Neither adversarial pass looked at whether a guarantee grades the
same alone as in a full run, and that question turned out to be the one that mattered. It is now a
test, and all twenty-four guarantees were checked by hand against both shipped examples: G22 was the
only one.

**Fixing a review's findings introduced four more defects**, all in the same two files, and the
second round found them. An observed resumed receipt reported another action's spend, because the
call that was also the contextvar's reset got skipped on the observe path -- T448's defect, on the
one receipt §8.3 makes the whole evidence for an MCP multi round-trip. A throwaway `_Observation()`
discarded a block and wrote its event twice, so a receipt said `ALLOW` while the log beside it said
the action was denied. Splitting a method dropped an `effect_key` from the one event that names
which effect a budget refused. And an `InvalidArgument` subclass with a keyword-only field stopped
being picklable, which nothing in this repository would ever have caught, because verify's children
speak JSON over stdin.

**The rate is the lesson, not any one of them.** Across both rounds roughly seventeen defects were
found, about half of them introduced by fixing the other half. Three reorderings of the observe path
produced four regressions between them, which is why §4.2.1b states a limit rather than attempting a
fourth: the fix is one ordered list of checks both modes walk, and that is a refactor of `_secure`
and `_observe_secure` together rather than another patch.

**What did not move in either round: enforcement.** Not one of the seventeen was an action running
that should have been refused. Every one was reporting, tooling, or observe mode, which enforces
nothing. The decision path -- consumed inside the reservation, ambiguity holding, every ancestor
charged, containment on both axes -- was probed adversarially twice, including sixteen enforcing and
sixteen observing threads against Postgres, and did not move. That asymmetry is worth recording
because it is what made the release defensible: a defect in `verify` or in observe mode ships as a
patch release, and a defect in the decision path would have shipped inside a one-way store
migration.
