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
  left". `v0.3 §1.1`'s rule that CTRLRun consumes and issues nothing is not relaxed here.
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
§2.3 says where the number comes from, and §11 says what a metric is never allowed to become.

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

**So money is budgeted in minor units**, which is what `examples/authority/payments.yaml` already
does with `amount_lte: 5000`, and §7's surfaces render it the way the rest of the document renders
that constraint. A string that looks like a number is a refusal and not a coercion, on the same
rule: `"100.50"` is a decimal wearing a string's clothes, and coercing it would put the drift back
through the door the float rejection closed.

**The kernel does not know what any metric means.** There is no branch anywhere on a metric name,
no ranking of two metrics, no default limit for a metric the kernel recognises. `amount` is not
special; it is an example. §11 carries this as a do-not-build line because a switch statement over
metric names is the first step of a consequence taxonomy, and the second step is scoring an
operator's actions.

### 2.4 What a budget is summed over

The grant it is written on, and every grant delegated beneath it, transitively. §2.6 is why.

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

### 2.6 Containment moves on two axes, and both are tested

`v0.3 §5`'s rule is `child ⊆ parent` on every dimension. On a budget that is two comparisons, and
the second is the one an implementer gets backwards.

| Axis | Rule | Why |
|---|---|---|
| `limit` | `child.limit <= parent.limit` | more money is more authority |
| `window` | `child.window <= parent.window` | **the same limit over a longer window is more authority, not less** |

The window rule reads backwards on first encounter, which is why it has its own row and its own
test in each direction. 100,000 a day is narrower than 100,000 a week: the child that stretches the
window to a week has given itself seven times the rate its parent permitted.

**A child that omits a budget its parent carries is rejected.** `v0.3 §5.4`'s rule that a child may
not discharge a dimension its parent constrains, unchanged and with no exception for budgets. A
child may add a budget its parent does not have.

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

Rendered in sorted order, like `constraints`, so two documents differing only in list order hash
alike.

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
`_authorize_and_reserve` is that transaction on both backends (`state.py:1014-1040` under
`self._lock`, `postgres.py:762-827` between `BEGIN` and `COMMIT`). A second backend written against
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
reservation path it takes is the one it took at 0.8.0, instruction for instruction. §8 requires a
test that proves it rather than a paragraph that asserts it.

### 3.4 The insert is idempotent on `(effect_key, attempt, grant_id, metric)`

`v0.6 §4.3.2` Table A1 row 2 says: on a lost `COMMIT` with no record found, **retry the insert,
once.** The retried transaction re-inserts the reservation and the charges with it. If the ledger
insert were an unconstrained append, the retry would double-charge, and it would do so precisely in
the case where an operator's network was already misbehaving.

So the ledger carries a unique constraint on `(effect_key, attempt, grant_id, metric)`, and the
insert is written so that a replay of the same transaction produces the same rows rather than twice
as many. Table A1 row 1's identity check then covers the charges without a third table, because they
are part of the row set the identity check is over.

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

### 3.6 Both backends, and the lock named in the code

**SQLite** takes `BEGIN IMMEDIATE`, a whole-database write lock, and the sum and the insert are
inside it. Nothing further is required and the reason is written beside the query rather than
assumed.

**Postgres runs READ COMMITTED with an explicit `BEGIN`**, and under READ COMMITTED a sum and an
insert are **not** serialised: two transactions read the same total and both insert. This is the
`postgres.py:1106` failure exactly.

The implementation names, in a comment beside the query, which mechanism makes it safe. The
specification does not choose between `SELECT ... FOR UPDATE` on a per-grant anchor row, a
serialisable subtransaction, or an exclusion constraint the insert collides on, because the choice
depends on what the final query shape is. It requires three things of whichever is chosen: it is
named in a comment, it is justified in the PR body, and **it is proven by a multi-process test
against Postgres**, never by threads. A counter that is correct in one process is not a claim about
anything an operator runs.

---

## 4. Reconciliation

### 4.1 The rule, in one sentence

**A charge is released exactly when its effect reaches `FAILED`, and held in every other state.**

The ledger has no state machine of its own. `effect.py:248-315` is already the complete table of
exits from a reservation, and this one rule covers every row of it.

### 4.2 Every exit from `RESERVED`, and what the ledger does

Eleven rows, because v0.8's item 4 needed three attempts on the analogous lapsed-row case: its spec
had ten rows and its code met an eleventh.

| Exit | Effect record | Ledger |
|---|---|---|
| `commit_effect` | `COMMITTED` | **held, permanently.** A committed spend is a spend |
| `fail_effect` | `FAILED` | **released.** The executor proved nothing happened (`v0.1 §5.5`) |
| `mark_ambiguous` | `AMBIGUOUS` | **held.** R2: ambiguity is not a refund |
| lease lapses, nothing else happens | unchanged until someone plans against it | **held.** No transition has occurred |
| lease lapsed, another process plans against it | `AMBIGUOUS` via `plan_reservation`'s `ambiguate` | **held.** It is `AMBIGUOUS` now, and R2 applies |
| `resolve_effect(COMMITTED)` by a human | `COMMITTED` | **held.** The human said it happened |
| `resolve_effect(FAILED)` by a human | `FAILED` | **released.** The human said it did not |
| `reconcile` hook moves it, first pass | as the hook says | **as the human's equivalent**: the hook is an authority, not an exception |
| `reconcile` hook moves it, second pass | `v0.2 §2.3` permits exactly two | **same rule; §4.4's idempotence makes the second pass safe** |
| renewal after `FAILED` (`renews=True`, `attempt+1`) | `RESERVED` again | **a new charge**, per §4.3 |
| retry refused (`COMMITTED`, `AMBIGUOUS`, live lease) | unchanged | **nothing.** No reservation, no charge |

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
| A scope provider is configured where the configuration cannot be coherent | `InvalidArgument` at decoration time where `@protect` can see it, on `v0.7 §6.2`'s precedent for a non-callable `preconditions=` |

They are distinct because a test asserting only the exception type cannot tell which guard fired,
and a test that cannot tell is the first of `CONTRIBUTING.md`'s four shapes of a false green. Every
test in §8's item 2 subsection asserts the reason.

**Absent means absent.** A call naming no provider behaves exactly as 0.8.0 did, and §8 requires a
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

**`SPEC-v0.7.md` §6.7's residual window applies unchanged.** The check cannot run inside the atomic
reservation write, so there is a window between the scope being fetched and the reservation being
taken in which the scope could change. It is small, it is stated, and it is not closed by this
milestone. No sentence anywhere may imply this check is inside the atomic write, because it is not.

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

### 6.4 A grant that names a task refuses an action that names none

Fail closed. Rejected: treating a missing task as matching, because that makes the dimension optional
for the caller and therefore optional for an attacker, and an authorization dimension anybody may
decline to supply is decoration.

### 6.5 A grant that names no task authorises any task

**This is the one decision in v0.9 that could have changed behaviour for an existing grant, and it
was decided in the direction that does not.**

Two precedents point in opposite directions:

- `v0.3 §5.4`'s "omission is not unlimited" says an absent dimension should entitle nothing.
- Every dimension actually in `authority.py` says the opposite: `resources: None` grants any
  resource, by construction and by `v0.3 §4.2`'s explicit sentence.

**The second wins, and the reason is R5.** `v0.3 §5.4`'s rule is about a *child* dropping a
dimension its *parent* constrains, which is a statement about attenuation and is preserved exactly:
a child that omits `tasks` under a parent that names them is rejected, per §6.2. It is not a rule
about what a root grant's silence means, and reading it as one would refuse at 0.9.0 every action
that succeeded at 0.8.0 under every grant anybody has written, because no grant in existence names a
task.

That is the upgrade consequence, stated: had this gone the other way, v0.9 would have broken every
existing deployment on upgrade, and a milestone that did that would have got `v0.3 §1.2` backwards.

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

## 7. The guarantees

`ctrlrun.guarantees/v5` is G1 to G24. **The catalogue moves once**, with item 1's G24, and G22 and
G23 join it with their items. No stub rows: a guarantee that reports anything before its check
exists is a false green, which is what 0.6.1 had to fix.

Three, and v0.9 does not invent a fourth. `ROADMAP.md` assigned G22 to G24 to v0.9 and G25 onward to
v0.10, in version order; both `ctrlrun verify` and the OWASP pages refer to guarantees by id, so a
fourth here would either collide with v0.10 or renumber it, and a renumber is the maintainer's
change to make. **Items 3, 4 and 6 ship without a guarantee id**, and that is recorded here rather
than left to look like an oversight.

| Id | Title | Width | Positive control | `N/A` when |
|---|---|---|---|---|
| G22 | `held budget refuses next reserve` | 32 | a budget not exhausted permits the action | no grant in the document carries a budget |
| G23 | `a failing scope provider refuses` | 32 | a provider that answers permits the action | no scope provider is configured |
| G24 | `grant refused off its task` | 26 | a grant on the task it names permits the action | no grant in the document names a task |

G22's title says **held** and not *exhausted*: what refuses the next reserve is a budget whose
consumption is held by an effect nobody has resolved, and a title saying "exhausted" would describe
the ordinary case and miss the one the guarantee is about. G24's says **grant refused** and not
*action refused*, because what is compared is the grant's task dimension against the action's task,
and the refusal names that dimension (§6.2).

**Every `N/A` reason is a statement about the operator's document**, per `verify/guarantees.py`'s own
module docstring. `N/A` is excluded from the denominator, so a false `N/A` is a false green, and a
guarantee that could not have failed is not a pass.

**G22 is exercised under the v0.6 multi-process standard against Postgres.** `ROADMAP.md` says so in
the exit criterion, by name, and it is not optional: a counter that is correct in one process is not
a claim about anything an operator runs. Threads against SQLite are not evidence about Postgres.

Titles are at most 32 characters, against `report._TITLE_WIDTH` (`report.py:37`), and a wider one
breaks the table's alignment. v0.7 had to shorten G12's and v0.8 had to shorten three; the widths
above are counted rather than estimated, and two of the three were over on the first draft.

---

## 8. Acceptance tests

T379 onward. v0.8 ended at T378. One subsection per item; each item writes its own and item 7
asserts the set is complete.

Every test in this section obeys three rules the milestone repeats because they are where its
defects will be:

1. **Every refusal test asserts the reason and, where there is one, the dimension by name.** Not the
   exception type alone.
2. **Every test of a budget, a hold or a release that makes a claim about concurrency runs
   multi-process against Postgres.** Threads against SQLite test a different program.
3. **Every guarantee has a positive control that could have failed.**

### 8.1 Item 1: task-bound authority (§6)

- T379 a grant naming `invoice-run-*` permits an action on `invoice-run-7`.
- T380 the same grant refuses an action on `payroll-3`, and the refusal names the `tasks` dimension.
- T381 a grant naming a task refuses an action carrying no task (§6.4), naming the dimension.
- T382 a grant naming no task permits an action carrying a task (§6.5), and a receipt from it is
  field-for-field what 0.8.0 wrote.
- T383 a child naming three tasks under a parent naming two is rejected at delegation.
- T384 a child omitting `tasks` under a parent naming them is rejected (`v0.3 §5.4`).
- T385 a task changed in the document moves the policy hash (§6.7).
- T386 a break-glass delegation is attenuated on `tasks`, at the second level, on T337's pattern.
- T387 a `ctrlrun.policy/v7` `tasks` key in a `v6` document is refused, in `policy.py`'s existing
  older-reader shape.

### 8.2 Item 2: scope providers (§5)

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
- T396 no provider configured: the whole path is 0.8.0's, receipt compared field by field.
- T397 the scope hash reaches the receipt and no scope content does.
- T398 a precondition provider and a scope provider both configured: both run, scope first, both
  hashes on the receipt under distinct fields (§5.7).

### 8.3 Item 3: the budget in the document (§2)

- T399 a well-formed budget loads and renders.
- T400 a child limit above its parent's is rejected.
- T401 a child window **longer** than its parent's is rejected (§2.6, the axis that reads backwards).
- T402 a child window shorter than its parent's is accepted.
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

### 8.4 Item 4: the ledger and the store amendment (§3)

- T408 a charge and its reservation are in one transaction: a failure after the charge leaves
  neither.
- T409 **multi-process, Postgres**: N processes racing one budget spend at most the limit.
- T410 the same, SQLite under `BEGIN IMMEDIATE`.
- T411 the A1 re-insert branch of `v0.6 §4.3.2` does not double-charge (§3.4).
- T412 a three-level delegation charges all three grants (§2.7).
- T413 a grant with no budget takes 0.8.0's reservation path, proven by the receipt and by the
  absence of any ledger row.
- T414 the migration runs on both backends and a 0.8.0 store upgrades.

### 8.5 Item 5: consumption, reconciliation and release (§4)

- T415 to T425: **one test per row of §4.2's table**, eleven rows, eleven tests.
- T426 a renewal after `FAILED` charges again, and the two rows are distinct by `attempt` (§4.3).
- T427 a `reconcile` hook running twice releases once (§4.4).
- T428 an A2 re-issue of `fail_effect` releases once, not twice (§4.4).
- T429 an exhausted budget refuses, reason distinct, naming grant, metric and window, **and not the
  remaining amount** (§4.5), asserted by word.
- T430 the refusal names the **ancestor** that refused, where a child is within its own budget
  (§2.7).
- T431 **G22, multi-process against Postgres**: a budget exhausted by an ambiguous effect refuses the
  next reserve until reconciled, and releases on `FAILED`.
- T432 G22's positive control: a budget not exhausted permits.

### 8.6 Item 6: the operator surfaces (§7)

- T433 `inspect` shows consumed, held, and **why** the held part is held.
- T434 `verify` reports G22 to G24 against the shipped examples, none of them `N/A`.
- T435 each `N/A` reason, where one is produced, is a true statement about the document under test.
- T436 the `--json` shapes are additive: a 0.8.0 consumer of the same command does not break.

### 8.7 Item 7: release

- T437 `ctrlrun.receipt/v6`, `ctrlrun.policy/v7` and `ctrlrun.guarantees/v5` each complete, with
  every frozen field written by something.
- T438 `import ctrlrun` still imports nothing from an extra, and `ctrlrun demo` still runs under 60
  seconds with no network.

---

## 9. Public API additions, frozen for v0.9

One justification per row. Anything not here is a spec amendment before it is code.

| Addition | Why an existing name does not serve |
|---|---|
| `Budget` (a metric, a limit, a window) | nothing in `authority.py` carries a quantity over a period |
| `Grant.budgets` | `constraints` decides one action and cannot count |
| `Grant.tasks` | no dimension names a unit of work |
| `Charge` | the store needs a value object for what a reservation spends |
| `charges=` on `reserve_effect` and `consume_approval_and_reserve` | §3.3, the milestone's one amendment to a frozen protocol |
| `scope=` on `@protect` and `Control.execute` | `preconditions=` answers a different question (§5.2) |

### 9.1 Schemas

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
| `budget_charges` | item 5 | which grants were charged, which metrics, how much |

Item 7 asserts every one of them is written by something before the release PR opens. This is
`SPEC-v0.7.md` §12's D27 rule, which v0.8 ran for three items without incident.

**`ctrlrun.guarantees/v5`** is G1 to G24, moved once by item 1 with G24 (§7).

### 9.2 The module map

No new module. Budgets and tasks are `authority.py`; the ledger is `state.py`, `postgres.py` and
`migrations.py`; the scope provider is `control.py` beside `v0.7 §6`'s recheck. A reorganisation of
`control.py` is out of scope (§11) and does not become in scope as a side effect.

---

## 10. Fail-closed table for v0.9

| Situation | Outcome |
|---|---|
| A budget's metric names an argument the action does not carry | **refused** (§2.3). Never zero |
| A budget limit is a float, a `Decimal`, a decimal string, negative, or not a number | **load error**, at the loader and at the constructor (§2.2, §2.3) |
| A child grant's budget exceeds its parent's on either axis | **rejected at delegation** (§2.6) |
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

## 11. Explicitly out of scope

Each with its reason, from the roadmap's do-not-build list for v0.9.

- **A consequence taxonomy.** A budget names a metric, not a class. There is no branch on a metric
  name anywhere, no ranking of two metrics, and no default limit for a metric the kernel thinks it
  recognises. A switch statement over metric names is the first step, and scoring an operator's
  actions is the second.
- **Compensation, or a saga.** Nothing is undone. A budget refuses the next action and has never had
  an opinion about the last one.
- **A fleet-wide budget across stores.** One store, one ledger. `v0.7 §4.6` gave the same answer for
  idempotency tokens: the kernel's consistency claim stops at its store's transaction.
- **Anything that reads a prompt to decide which task an agent is on** (§6.3).
- **A quota endpoint, a spend API, or a published balance.** `v0.3 §1.1`: CTRLRun consumes and issues
  nothing.
- **An automatic expiry on a hold.** It is the refund R2 refuses, on a delay (§4.6).
- **A fourth guarantee id** (§7).
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

## 12. What building v0.9 settled

*One subsection per question the drafting could not close, each stating what the code decided and
which section carries it. `SPEC-v0.4.md` §12 through `SPEC-v0.8.md` §14 are the format.*

**This section is empty on purpose, and item 7 writes it in one pass**, per the pace decision the
maintainer recorded in the milestone's plan on 2026-09-12: the spec amendment an item owes as it
lands is its §9 name row, its MUST sentences and its §10 fail-closed row, and the prose explaining
what building settled is written once over the finished milestone rather than six times over
guesses.

**The cost of that, and what pays it.** v0.5's item 6 could tell which parts of that document had
been stress-tested by somebody other than their author by looking for a §12 entry behind them, and
all four of its most serious findings sat in sections that had none. Written at the end, §12 loses
that signal during the milestone. What replaces it: **an item that settles something surprising
leaves a line in its `CHANGELOG` entry when it lands**, and item 7 writes §12 from those lines. An
item whose PR body reports a question it could not settle has already written its §12 entry, and
should say so.

The four open questions this document hands the items are O1 to O4 in the build plan, and each is
answered here in the section that carries it: O1 in §3.3, O2 in §4.5, O3 in §5.2 and §5.7, O4 in
§6.5. An item that finds one of those answers wrong stops and reports rather than working around it,
because all four are load-bearing for a section rather than local to a function.
