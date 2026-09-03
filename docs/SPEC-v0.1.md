# CTRLRun v0.1 Specification

This is the contract for v0.1. Tests are derived from §7. Public names are frozen in §8. Anything not in this document is out of scope for v0.1.

Words: MUST / MUST NOT / SHOULD are used in the RFC 2119 sense.

---

## 1. Scope

v0.1 delivers exactly:

| Claim in README | Primitive |
|---|---|
| Per-action autonomy | Policy (§3) |
| Autonomous | `ALLOW` |
| Human approval | `APPROVE` + approval binding (§4) |
| Blocked | `DENY` |
| Approval bound to exact action | `action_hash` (§2.3) |
| Blocks duplicate execution attempts for the same logical effect | Effect key + reservation (§5) |
| Stops blind retries when outcome is uncertain | `AMBIGUOUS` state + retry rules (§5.4) |
| Records what actually happened | Receipt + event log (§6) |

Single approver. SQLite only. Python decorator + CLI + demo. Nothing else.

---

## 2. Action

### 2.1 Model

```python
@dataclass(frozen=True)
class Action:
    name: str                      # dotted, e.g. "stripe.refund"
    arguments: Mapping[str, Any]   # see §2.3 for allowed types
    principal: Principal           # who is acting
    resource: str | None = None    # opaque "type:id", e.g. "payment:txn_8231"
    environment: str = "production"
    action_id: str = <generated>   # "act_" + 12 hex chars; NOT part of the hash

@dataclass(frozen=True)
class Principal:
    agent: str                     # required, e.g. "refund-agent"
    user: str | None = None        # human on whose behalf, if any
```

`name`, `environment` and `principal.agent` MUST be non-empty. `resource` and `principal.user` are either `None` or non-empty. Empty string → `InvalidArgument`.

`action_id` identifies a *proposal*. A retry produces a new `action_id`. Identity of the *effect* is the effect key (§5), never `action_id`.

Action equality follows the proposal, not the content: two Actions are equal iff their `action_id` is equal, and `hash(action)` is `hash(action_id)`. Two proposals with identical content are distinct Actions that share an `action_hash`.

### 2.2 Canonical form

The canonical form of an Action is the UTF-8 encoding of the JSON serialization of:

```json
{"arguments": ..., "environment": ..., "name": ..., "principal": {"agent": ..., "user": ...}, "resource": ..., "schema": "ctrlrun.action/v1"}
```

with `sort_keys=True`, `separators=(",", ":")`, `ensure_ascii=False`, and all nested mappings sorted recursively. `action_id` and any timestamps are excluded. `canonicalize()` returns `bytes`.

`arguments` is snapshotted at construction into a deep-frozen structure — mappings become read-only mappings, lists become tuples, recursively — so a caller holding the original object cannot change a constructed Action's hash.

`Control.execute` MUST invoke the executor with arguments parsed back from the action's canonical form (`Action.canonical_arguments`), never with the original Python objects. Freezing prevents accidents; executing from canonical bytes makes mutation irrelevant to the approval binding. `Action.canonical_arguments` returns plain, mutable `dict`/`list` containers built fresh from the canonical bytes on each access, so the executor cannot reach back into the Action.

`canonical_arguments` is the recommended way to rebuild an Action from an existing one (a retry, §5.4). Passing another Action's stored `arguments` mapping is also valid — the frozen tuples it contains are accepted on input (§2.3) and canonicalize identically.

### 2.3 Argument types and `action_hash`

`action_hash = "sha256:" + hex(SHA-256(canonical_form))`.

Allowed argument value types: `str`, `int`, `bool`, `None`, `list` of allowed types, `dict[str, allowed]`.

**`float` MUST be rejected** at Action construction with `InvalidArgument`. Rationale: `0.1` and `0.10` and `0.1000000001` are the same money and different hashes. Use integer minor units (`amount=200000` cents) or decimal strings (`"2000.00"`).

`tuple` is accepted on input and normalized to a JSON array: a tuple and the equivalent list have the same canonical form and therefore the same hash, so rejecting it would buy no safety.

Any other type, including `Decimal`, `set`, `bytes`, `datetime`, and arbitrary objects, and any non-`str` mapping key, MUST raise `InvalidArgument`. Validation is recursive: a disallowed value at any depth rejects the whole Action.

Two actions with the same canonical form MUST produce the same hash regardless of Python dict insertion order. Any material change (name, any argument, resource, principal, environment) MUST change the hash.

---

## 3. Policy

### 3.1 File format

`ctrlrun.yaml`, discovered from `CTRLRUN_CONFIG` env var, else `./ctrlrun.yaml`.

```yaml
schema: ctrlrun.policy/v1

actions:
  customer.read:
    decision: allow

  email.send:
    decision: allow

  stripe.refund:
    rules:
      - when: { amount_lte: 500 }
        decision: allow
      - when: { amount_lte: 5000 }
        decision: approve
      - decision: deny

  iam.grant_admin:
    decision: deny
```

An action entry has **either** `decision` **or** `rules` (a non-empty list). Both or neither → `PolicyError` at load time.

### 3.2 Rules

Rules are evaluated top to bottom; **first match wins**. A rule with no `when` always matches. If no rule matches → `DENY`.

`when` is a mapping of conditions, all of which must hold (AND). Condition keys are `<argument>_<op>`:

| Suffix | Meaning | Operand |
|---|---|---|
| `_eq` | equal | any allowed type |
| `_neq` | not equal | any |
| `_lt` `_lte` `_gt` `_gte` | numeric compare | `int` only |
| `_in` | membership | list |

Referencing an argument that is absent from the action → the condition is false (not an error). Applying a numeric op to a non-`int` argument → the condition is false and a warning is logged.

### 3.3 Decisions

```python
class Decision(str, Enum):
    ALLOW = "allow"
    APPROVE = "approve"
    DENY = "deny"
```

Exactly these three in v0.1. No `allow_with_log`, `transform`, or `terminate`.

### 3.4 Fail-closed defaults (not configurable in v0.1)

| Condition | Result |
|---|---|
| Action name not in `actions` | `DENY` (`reason="unknown_action"`) |
| Policy file missing | `PolicyError` at load; `Control` cannot be constructed |
| Policy malformed / unknown schema | `PolicyError` at load |
| Argument type mismatch in a rule | condition false → falls through |

---

## 4. Approval

### 4.1 Model

```python
@dataclass(frozen=True)
class ApprovalRequest:
    request_id: str        # "apr_" + 12 hex
    action_hash: str
    action: Action         # for human display
    created_at: datetime
    expires_at: datetime   # default now + 15 minutes

@dataclass(frozen=True)
class Approval:
    approval_id: str       # == request_id in v0.1
    action_hash: str
    approver: str          # free text in v0.1, e.g. "cli:local"
    granted_at: datetime
    expires_at: datetime
```

Stored records additionally carry `status: pending | granted | denied | expired | consumed`.

### 4.2 Invariants

- **A1 Exact binding.** An approval is valid for an Action only if `approval.action_hash == action.action_hash`. Anything else → `ApprovalMismatch`, decision `DENY`, event `APPROVAL_INVALIDATED`.
- **A2 Single use.** Consuming an approval (transitioning `granted → consumed`) MUST be atomic in the StateStore. A second presentation → `ApprovalMismatch(reason="consumed")`.
- **A3 Expiry.** `now > expires_at` → invalid. Expiry is checked at consumption time, not only at grant time.
- **A4 Consumption precedes execution.** The approval is consumed in the same store transaction as the effect reservation (§5.3). If reservation fails, the approval is *not* consumed.

### 4.3 Providers

```python
class ApprovalProvider(Protocol):
    def request(self, action: Action, ttl: timedelta) -> ApprovalRequest: ...
    def wait(self, request_id: str, timeout: timedelta | None) -> Approval | None: ...
```

v0.1 ships:

- `LocalApprovalProvider` — writes the request to the StateStore; `wait()` polls until `ctrlrun approve <id>` / `ctrlrun deny <id>` runs in another shell, or timeout → `ApprovalTimeout`.
- `ScriptedApprovalProvider` — for tests and `ctrlrun demo`; grants/denies/mutates per a script.

Default behaviour of `@protect` on `APPROVE` when `wait=False` (the default): raise `ApprovalRequired(request_id=...)` immediately, so an agent loop can surface it. The caller re-invokes with `ctrlrun.with_approval(request_id)` in context. With `wait=True`, the decorator blocks on `provider.wait()`.

---

## 5. Effects

### 5.1 Effect key

`@protect(..., effect="refund:{payment_id}")`. The template is resolved against the action's arguments (and `resource` via `{resource}`). Missing placeholder → `EffectKeyError` (the action is denied; never a silent `None`).

An action with no `effect=` declared has no logical effect: no reservation, no duplicate protection, receipt `effect_key: null`. This is the documented escape hatch for reads.

Effect keys are opaque strings, globally unique within a StateStore. Namespace them: `refund:{payment_id}`, not `{payment_id}`.

### 5.2 States

```python
class EffectState(str, Enum):
    NEW = "new"
    RESERVED = "reserved"
    EXECUTING = "executing"
    COMMITTED = "committed"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"
```

```
NEW → RESERVED → EXECUTING → COMMITTED
                            → FAILED
                            → AMBIGUOUS
```

`AMBIGUOUS` MUST NOT collapse to `FAILED`. Only a human (`ctrlrun resolve`) can move `AMBIGUOUS → COMMITTED | FAILED`.

### 5.3 Reservation

```python
class StateStore(Protocol):
    def reserve_effect(self, effect_key: str, action_id: str, lease: timedelta) -> Reservation: ...
    def begin_execution(self, effect_key: str, action_id: str) -> None: ...
    def commit_effect(self, effect_key: str, action_id: str, result: Any) -> None: ...
    def fail_effect(self, effect_key: str, action_id: str, error: str) -> None: ...
    def mark_ambiguous(self, effect_key: str, action_id: str, error: str) -> None: ...
    def get_effect(self, effect_key: str) -> EffectRecord | None: ...
    # approvals
    def put_approval_request(...); def grant_approval(...); def deny_approval(...)
    def consume_approval(self, approval_id: str, action_hash: str) -> Approval: ...
    # evidence
    def append_event(self, event: Event) -> None: ...
    def put_receipt(self, receipt: Receipt) -> None: ...
```

- **E1 Atomic reservation.** `reserve_effect` MUST succeed for at most one caller per `effect_key` across threads *and processes*. SQLite implementation: `PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000; BEGIN IMMEDIATE; INSERT ... ; COMMIT` with `UNIQUE(effect_key)`.
- **E2 Existing effect.** If a record exists, reservation outcome depends on its state (§5.4).
- **E3 Lease.** A reservation carries `lease_expires_at` (default 5 min). A record in `RESERVED` or `EXECUTING` whose lease has expired is treated as `AMBIGUOUS` (the executor may have died mid-flight). It is not silently released.

### 5.4 Retry rules

When a new action arrives for an `effect_key` that already has a record:

| Existing state | New reservation | Raised |
|---|---|---|
| `COMMITTED` | refused | `DuplicateEffect(state=committed)` |
| `AMBIGUOUS` | refused | `AmbiguousEffect` — human must resolve |
| `RESERVED` / `EXECUTING` (lease live) | refused | `DuplicateEffect(state=in_progress)` |
| `RESERVED` / `EXECUTING` (lease expired) | refused; record moved to `AMBIGUOUS` | `AmbiguousEffect` |
| `FAILED` | **allowed** — new attempt, same key, `attempt += 1` | — |

`FAILED` means the executor *proved* nothing happened (§5.5). That is the only state that permits automatic retry.

### 5.5 Executor outcome mapping

The wrapped function is the executor. Its result is mapped:

| Executor behaviour | Effect state |
|---|---|
| returns normally | `COMMITTED` |
| raises `ctrlrun.NotExecuted` | `FAILED` |
| raises anything else (incl. `TimeoutError`, connection errors) | `AMBIGUOUS` |

The executor opts *into* `FAILED` by raising `NotExecuted`, asserting it knows the remote side did nothing (e.g. HTTP 4xx validation error before any side effect). The default for the unknown is `AMBIGUOUS`. This asymmetry is deliberate and MUST NOT be inverted.

---

## 6. Evidence

### 6.1 Receipt

One per action that reached a terminal state (including denials). JSON, one per line in `.ctrlrun/receipts.jsonl`, and stored in SQLite.

```json
{
  "schema": "ctrlrun.receipt/v1",
  "receipt_id": "ctr_29182f1a0b3c",
  "action_id": "act_71ab...",
  "action": "stripe.refund",
  "action_hash": "sha256:…",
  "principal": {"agent": "refund-agent", "user": null},
  "resource": "payment:txn_8231",
  "arguments": {"amount": 2000, "currency": "EUR", "payment_id": "txn_8231"},
  "environment": "production",
  "decision": "approve",
  "decision_reason": "rule[1]",
  "approval_id": "apr_9918…",
  "approver": "cli:local",
  "effect_key": "refund:txn_8231",
  "attempt": 1,
  "result": "committed",
  "error": null,
  "started_at": "2026-09-03T10:12:01.120Z",
  "finished_at": "2026-09-03T10:12:01.480Z"
}
```

`result ∈ {committed, failed, ambiguous, denied, blocked}` where `blocked` covers duplicate/ambiguous-retry/approval-mismatch refusals. No signatures in v0.1 (v0.6).

### 6.2 Events

Appended in order to the `events` table and `.ctrlrun/events.jsonl`:

```
ACTION_PROPOSED
POLICY_EVALUATED
APPROVAL_REQUESTED
APPROVAL_GRANTED
APPROVAL_DENIED
APPROVAL_EXPIRED
APPROVAL_INVALIDATED
APPROVAL_CONSUMED
EFFECT_RESERVED
EFFECT_RESERVATION_REFUSED
EXECUTION_STARTED
EXECUTION_COMMITTED
EXECUTION_FAILED
EXECUTION_AMBIGUOUS
EFFECT_RESOLVED
ACTION_DENIED
```

Each event: `{event_id, ts, type, action_id, effect_key?, approval_id?, data}`.

---

## 7. Acceptance tests

Each MUST exist as a pytest test with the given ID in its name. All MUST pass for v0.1.

### T1 — Lost response, blind retry blocked (the signature test)
Given a fake remote that commits and then raises `TimeoutError`.
When the agent executes `refund(payment_id="txn_1", amount=2000)` with effect `refund:txn_1`.
Then the effect record is `AMBIGUOUS`, receipt `result=ambiguous`.
When the agent retries the same call (new `action_id`).
Then `AmbiguousEffect` is raised, no execution occurs (fake remote call count == 1), receipt `result=blocked`.

### T2 — Approval mutation blocked
Given policy `stripe.refund` requires approval for 500 < amount ≤ 5000.
When the agent proposes `amount=2000` and a human grants the request.
And the agent then executes with `amount=5000` presenting that approval.
Then `ApprovalMismatch` is raised, no execution, approval remains `granted` (not consumed), event `APPROVAL_INVALIDATED`.

### T3 — Concurrent agents, one reservation
Given a fresh SQLite store on disk.
When 8 processes (`multiprocessing`) simultaneously execute the same action with effect `refund:txn_9`.
Then exactly one `COMMITTED` receipt exists, seven `blocked` receipts, fake remote call count == 1.

### T4 — Approval replay blocked
Given a granted approval.
When the exact same action executes twice, presenting the same approval.
Then the first commits; the second raises `ApprovalMismatch(reason="consumed")` **and** `DuplicateEffect` would also apply — the approval check runs first and its error is the one raised.

### T5 — Approval expiry
Given an approval with `expires_at` in the past.
Then executing raises `ApprovalMismatch(reason="expired")`.

### T6 — Unknown action fails closed
Given a policy without `foo.bar`.
Then executing `foo.bar` raises `ActionDenied(reason="unknown_action")` and produces a `denied` receipt.

### T7 — Canonicalization stability
Two Actions built from dicts with different insertion orders produce identical `action_hash`. Changing any single field changes it. Passing `amount=20.0` raises `InvalidArgument`.

### T8 — FAILED permits retry
Executor raises `NotExecuted` on the first call, returns on the second. Then two receipts: `failed` (attempt 1) and `committed` (attempt 2), one effect record, `attempt == 2`.

### T9 — Expired lease becomes AMBIGUOUS
Reserve, begin execution, never finish; advance clock past lease. A new attempt raises `AmbiguousEffect`; the record is `AMBIGUOUS`.

### T10 — `ctrlrun resolve`
After T1, `ctrlrun resolve refund:txn_1 --failed` moves the record to `FAILED`; a retry then executes. `--committed` moves it to `COMMITTED`; a retry is `DuplicateEffect`.

### T11 — Demo runs
`ctrlrun demo` exits 0 in < 60 s, prints all four scenario headings and four `BLOCKED` lines, and writes receipts.

### T12 — Approval consumed atomically with reservation
Inject a store that fails `reserve_effect`; the approval remains `granted`.

---

## 8. Public API (frozen)

```python
# ctrlrun/__init__.py
from .protect import protect, Control, context, with_approval
from .action import Action, Principal, action_hash, canonicalize
from .policy import Decision, Policy
from .approval import Approval, ApprovalRequest, ApprovalProvider, LocalApprovalProvider, ScriptedApprovalProvider
from .effect import EffectState, EffectRecord
from .state import StateStore, SQLiteStateStore, InMemoryStateStore
from .receipt import Receipt, Event
from .errors import (
    CTRLRunError, InvalidArgument, PolicyError, EffectKeyError,
    ActionDenied, ApprovalRequired, ApprovalTimeout, ApprovalMismatch,
    DuplicateEffect, AmbiguousEffect, NotExecuted,
)
```

```python
protect(name: str, *, effect: str | None = None, resource: str | None = None,
        wait: bool = False, control: Control | None = None) -> Callable

context(agent: str, user: str | None = None, environment: str | None = None) -> ContextManager
with_approval(request_id: str) -> ContextManager

Control.from_file(path=None) -> Control
Control(policy: Policy, store: StateStore, approvals: ApprovalProvider, clock=...)
Control.evaluate(action: Action) -> Evaluation   # decision + reason, no side effects
Control.execute(action: Action, executor: Callable[[], Any], effect_key: str | None) -> Receipt
```

CLI (`click`):

```
ctrlrun init                    # writes ctrlrun.example.yaml → ctrlrun.yaml, creates .ctrlrun/
ctrlrun demo                    # four scenarios
ctrlrun approve <request_id>
ctrlrun deny <request_id>
ctrlrun receipts [--last N] [--json]
ctrlrun effects [--state ambiguous]
ctrlrun resolve <effect_key> (--committed | --failed)
```

---

## 9. Explicitly out of scope for v0.1

Authority, identity providers, delegation, resource/data scope, consequence taxonomy, CONTROL registry, multi-approver, separation of duties, OPA/Cedar/ACS, MCP gateway, OpenTelemetry, webhooks, Postgres, `ctrlrun verify`, compensation, framework adapters, signatures on receipts, any UI or server.
