# CTRLRun Architecture (v0.1 kernel)

Context: agent frameworks model work as `model → tool call → response`. That is fine for reads. For writes it is missing the semantics every serious system has around consequential operations: authorization bound to the exact operation, identity of the effect (not the request), atomic reservation, and an honest distinction between *failed* and *unknown*. CTRLRun adds those semantics around the dangerous part and nothing else.

The contract is in [`SPEC-v0.1.md`](SPEC-v0.1.md). This document explains the shape and the reasoning.

## 1. The boundary CTRLRun owns

```
Agent reasoning                     (not ours)
      │  "I want to do X"
      ▼
┌──────────────────────────────┐
│           CTRLRun            │
│  normalize → decide →        │
│  approve → reserve →         │
│  execute → resolve → record  │
└──────────────────────────────┘
      │
      ▼
Real-world effect                   (not ours either)
```

CTRLRun sits between *intention* and *consequence*. It does not sit between prompt and model. Everything upstream (planning, prompting, retrieval, memory) and everything downstream (the remote system's own semantics) is out of scope.

## 2. Canonical flow

```
function call (kwargs)
      │
      ▼
   ACTION  ──────────── canonicalize ──► action_hash
      │
      ▼
   POLICY  ──► ALLOW / APPROVE / DENY        (unknown → DENY)
      │              │            │
      │              ▼            └──► receipt(denied)
      │        approval request
      │              │
      │        human grants (bound to action_hash)
      │              │
      ▼              ▼
   consume approval + reserve effect         (one transaction)
      │
      ▼
   EXECUTING ──► executor runs
      │
      ├── returns            ──► COMMITTED
      ├── raises NotExecuted ──► FAILED     (retry permitted)
      └── raises anything    ──► AMBIGUOUS  (retry refused; human resolves)
      │
      ▼
   RECEIPT + events
```

## 3. Four public concepts

Internally the machinery has a dozen types. Publicly a developer needs four:

| Concept | Question it answers |
|---|---|
| **Action** | What exactly does the agent want to do? |
| **Decision** | Can it happen — automatically, with a human, or not at all? |
| **Effect** | What happened in the real world? |
| **Receipt** | Can we prove it? |

Every other type is subordinate to one of these. Don't promote a fifth to the public surface before v0.3.

## 4. Key decisions and trade-offs

### 4.1 Autonomy is per action, not per agent
The same agent is autonomous for `customer.read`, supervised for `stripe.refund` over €500, and prohibited from `iam.grant_admin`. This is the product's central idea. There are no "modes"; there is one policy file.

*Trade-off:* the policy language must stay tiny or this becomes OPA. v0.1 has six comparison ops and first-match-wins. That is deliberate.

### 4.2 Approval binds to a hash, not a request ID
A human approves a *canonical action*, not a ticket. If the agent changes any material field between approval and execution, the hash differs and the approval is void. This is what makes human oversight mean something.

*Trade-off:* canonicalization becomes security-critical. Floats are rejected because equal money can hash differently. Argument order must not matter. A schema version is embedded so future changes can't silently invalidate old approvals.

### 4.3 Effect identity is separate from action identity
A retry is a new proposal (`action_id`) for the same logical effect (`effect_key`). Idempotency keyed on the request would let a retry through. Idempotency keyed on the *intent* (`refund:{payment_id}`) catches it.

*Trade-off:* the developer has to declare the key. We make that one decorator argument and fail loudly on a bad template rather than silently degrading.

### 4.4 AMBIGUOUS is a first-class terminal state
A timeout after a request was sent is not a failure. The remote may have committed. Frameworks that map timeout → failed → retry are how double refunds happen. CTRLRun refuses to guess: `AMBIGUOUS` blocks retries until a human resolves it.

*Trade-off:* this creates operational work (someone must run `ctrlrun resolve`). That is the correct place for the work to land. v0.2 adds a `reconcile` hook (`SPEC-v0.2.md` §2) for executors that can ask the remote what happened: it is the second — and only other — authority permitted to move a record out of `AMBIGUOUS`, and only where its answer points. An answer it cannot give is `"unknown"`, which changes nothing.

### 4.5 The executor opts into FAILED
Only `NotExecuted` maps to `FAILED`. Every other exception is `AMBIGUOUS`. The library cannot know whether an arbitrary exception fired before or after the side effect; the executor author can. Making the safe outcome the default means a lazy integration is a safe integration.

### 4.6 Reservation is atomic across processes
Agents run as separate workers. Thread locks are not enough. SQLite with `BEGIN IMMEDIATE` and a unique constraint gives a real cross-process lock for a single host; Postgres (v0.6) extends it across hosts. The concurrency test spawns processes, not threads, so this can't regress unnoticed.

### 4.7 Fail closed, not configurable
Unknown action, missing policy, expired approval, inconsistent state → `DENY`. There is no `default: allow`. Permissive defaults are the one thing that must be impossible by accident. If a user wants reads to be free, they list them.

### 4.8 Receipts are portable JSON, not a dashboard
Evidence has to leave the system to be useful (audit, SIEM, a PR comment). JSONL on disk plus SQLite. No UI in v0.1, no server, no lock-in.

### 4.9 Leases, not locks
A reservation that never completes (worker crash) can't hold the key forever, but it can't be silently released either — the effect may have happened. Expired lease → `AMBIGUOUS`. Same principle as 4.4.

The length is the caller's (`Control(lease=...)`, `@protect(lease=...)`, five minutes by default) because only they know how long the work takes; the meaning of expiry is not. A default that is too short for a slow action would make every success ambiguous, and the user's remedy would be to drop the effect key and lose duplicate protection altogether — so we make the knob, not the escape hatch, the obvious move.

## 5. Data model (SQLite)

```sql
effects(
  effect_key TEXT PRIMARY KEY,
  state TEXT NOT NULL,
  action_id TEXT NOT NULL,        -- current/last attempt
  attempt INTEGER NOT NULL DEFAULT 1,
  lease_expires_at TEXT,
  result_json TEXT, error TEXT,
  created_at TEXT, updated_at TEXT
);
approvals(
  approval_id TEXT PRIMARY KEY,
  action_hash TEXT NOT NULL,
  status TEXT NOT NULL,           -- pending|granted|denied|expired|consumed
  action_json TEXT NOT NULL,
  approver TEXT, created_at TEXT, granted_at TEXT, expires_at TEXT, consumed_at TEXT
);
receipts(receipt_id TEXT PRIMARY KEY, action_id TEXT, effect_key TEXT, result TEXT, json TEXT, ts TEXT);
events(event_id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, type TEXT, action_id TEXT, effect_key TEXT, approval_id TEXT, data_json TEXT);
```

Pragmas: `journal_mode=WAL`, `busy_timeout=5000`, `synchronous=NORMAL`.

## 6. Module map

| Module | Owns | Must not know about |
|---|---|---|
| `action.py` | model, canonicalization, hash | policy, storage |
| `policy.py` | YAML → rules → Decision; `effect:`/`resource:` templates | approvals, effect *state* |
| `identity.py` | `IdentityProvider`, `IdentityContext`, static and header providers | policy, authority, storage |
| `authority.py` | `Grant`, `Subject`, `Authority`, matching, containment, delegation planning | approvals, effect state, executors, sinks |
| `approval.py` | request/grant/consume, providers | executors |
| `effect.py` | key templating, state enum, transition rules | SQLite |
| `state.py` | `StateStore` protocol + SQLite/in-memory impls | policy, decorator, sinks |
| `control.py` | `Control` orchestration, decorator, context, suspend/resume | CLI |
| `receipt.py` | Receipt/Event models, `EventSink`, JSONL sink | everything else |
| `cli/` | click commands, demo | internals beyond `Control` |

Dependencies point downward only. `Control` is the only module that composes the others.

The gateway (v0.2) does not change this. It builds an Action and calls `Control` — including
`Control.resume` for an elicitation's second leg — rather than reserving and committing for
itself. A gateway that owned the reservation would be a second module composing the others,
and a second implementation of SPEC-v0.1 §5.5's asymmetry, which is the one rule in this
codebase that must not drift.

The same holds for authority (v0.3). `authority.py` reads the store through the `StateStore`
protocol and **writes nothing and appends nothing**: `Authority.evaluate` returns a result and
`plan_delegation` returns the record it *would* write, and `Control` performs every write and
fans every event out to the sinks. An `Authority` that wrote for itself would be a second module
composing storage and evidence, and it would leave the highest-privilege operations in the
release as the only ones invisible to the export path (SPEC-v0.3 §4.8).

One exception, added in v0.2 and worth stating rather than discovering: `policy.py` imports the
template grammar (`template_placeholders`) from `effect.py`, because SPEC-v0.2 §3.1 requires an
`effect:` / `resource:` template to be validated when the policy loads and the grammar is
security-critical enough that a second copy of it is worse than the import. Policy still knows
nothing of effect state — no records, no transitions, no reservations — and `effect.py` does not
import `policy.py`, so there is no cycle.

v0.3 makes the same exception once more, for the same reason: `authority.py` imports the
condition parser and evaluator (`Condition`, `parse_conditions`) from `policy.py`, because a
grant's `constraints:` is in exactly a rule's `when:` syntax and the two axes MUST share one
evaluator (SPEC-v0.3 §4.5). A second condition evaluator would be a second place for `True` to
start comparing equal to `1`. `policy.py` does not import `authority.py`, so there is no cycle,
and policy still cannot see a principal: `agent_eq` and every other reserved name are still
refused at load (§4.7).

## 7. What changes after v0.1 (and what doesn't)

Stable from v0.1 onward: the four public concepts, the action canonical form (versioned), the effect state machine, fail-closed defaults, the executor outcome mapping.

Expected to change: policy language (providers), StateStore backends, approval providers, receipt fields (additive only). See [`ROADMAP.md`](ROADMAP.md).
