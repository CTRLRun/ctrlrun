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
    action_id: str = <generated>   # "act_" + 32 hex chars; NOT part of the hash

@dataclass(frozen=True)
class Principal:
    agent: str                     # required, e.g. "refund-agent"
    user: str | None = None        # human on whose behalf, if any
```

`name`, `environment` and `principal.agent` MUST be non-empty. `resource` and `principal.user` are either `None` or non-empty. Empty string → `InvalidArgument`.

An Action cannot exist without a principal, and the principal comes from `context()` (§8). A `@protect`-wrapped function called outside any `context()` therefore has no Action to decide: it MUST raise `ActionDenied(reason="no_principal")` and MUST log a warning on the `ctrlrun` logger naming the action, and MUST NOT write a receipt or any events. A call outside `context()` is a wiring bug, not an agent action — it does not belong in the evidence log, which records actions and would have no principal to attribute this one to. It must not be silent either, which is what the warning is for.

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

**No Unicode normalization is applied.** Strings are hashed as the exact code points given, so the NFC and NFD spellings of `café` are different arguments with different hashes. This is deliberate and fail-closed: an approval granted for one spelling does not authorize the other, which is the safe direction for a binding whose whole job is to notice that something changed. It also means a caller who re-encodes an argument between proposing an action and executing it has produced a different action — correctly, if inconveniently. Normalize at your system's edge, before the value reaches an Action, not inside the hash.

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
      - when: { amount_gte: 0, amount_lte: 50000 }      # €0.00 – €500.00
        decision: allow
      - when: { amount_gte: 0, amount_lte: 500000 }     # €0.00 – €5,000.00
        decision: approve
      - decision: deny

  iam.grant_admin:
    decision: deny
```

Amounts are integer minor units (§2.3). Bound both ends: `amount_lte` on its own admits a negative amount, and a refund of a negative amount is a charge.

An action entry has **either** `decision` **or** `rules` (a non-empty list). Both or neither → `PolicyError` at load time.

Key sets are **closed**. The top level accepts `schema` and `actions`; an action entry accepts `decision` and `rules`; a rule accepts `when` and `decision`. Any other key → `PolicyError` at load, so `action:` for `actions:` or `unless:` for `when:` fails loudly instead of silently denying everything at runtime. `schema` is required: a document without it is an unknown schema (§3.4).

`actions: {}` is valid and denies every action (§3.4). A missing `actions` key, or one whose value is not a mapping, → `PolicyError`. Action names MUST be non-empty strings and are matched exactly — no case folding, no prefixes.

### 3.2 Rules

Rules are evaluated top to bottom; **first match wins**. A rule with no `when` always matches. If no rule matches → `DENY`.

`when` is a mapping of conditions, all of which must hold (AND). Condition keys are `<argument>_<op>`:

| Suffix | Meaning | Operand |
|---|---|---|
| `_eq` | equal | any allowed type |
| `_neq` | not equal | any |
| `_lt` `_lte` `_gt` `_gte` | numeric compare | `int` only |
| `_in` | membership | list |

Referencing an argument that is absent from the action → the condition is false (not an error) **and a warning is logged**. Applying a numeric op to a non-`int` argument → the condition is false and a warning is logged.

The warning on an absent argument matters more than it looks. A call is bound to the function's signature with defaults applied (§8), so an argument is either always present or never present — an absent one is a mistyped name, not a value that happens to be missing on this call. Silently false is the right *decision* (a rule that cannot be evaluated must not match), but silence let `amont_lte` disappear into a catch-all `decision: allow` below it, turning a typo into a permissive policy. The decision stays false; it stops being quiet.

**Conditions address arguments, and only arguments.** A condition key naming an `Action` field — `action_id`, `agent`, `environment`, `principal`, `resource`, `user` — MUST raise `PolicyError` at load. `when: { environment_eq: production }` reads exactly like it scopes a rule to production and would match nothing at all, because conditions see `action.canonical_arguments` and nothing else. This is the same fail-closed reading as `{resource}` in §5.1: two candidate meanings for one name, so refuse and make the author rename, rather than silently pick the one they did not mean. Only the whole argument name is reserved — `resource_id_eq` is an ordinary condition on an ordinary argument. Scoping a rule by environment or principal is not in v0.1 (§9); an action that must be decided differently per environment gets a different action name until then.

Where a `when` is present it MUST be a non-empty mapping: `when: {}` and `when:` (null) → `PolicyError` at load. An empty mapping is far more likely a truncated edit than an intended catch-all, and the catch-all already has a spelling — omit `when`.

Operands are validated at load against the argument types of §2.3, recursively. A `float` operand → `PolicyError` with the same rationale as §2.3; so does any other disallowed type, including the `date` and `datetime` an unquoted YAML scalar produces. An unknown operator suffix → `PolicyError`; a key is split on its **longest** matching suffix, so `amount_neq` is `amount` with `_neq`, never `amount_n` with `_eq`.

**`bool` is not `int`** for the numeric ops, on either side: a `bool` operand → `PolicyError` at load, and a `bool` argument → the condition is false with a warning, exactly like any other non-`int`. Python makes `bool` a subclass of `int`; policy must not inherit that.

Equality (`_eq`, `_neq`, and membership under `_in`) is **type-strict**, applied recursively inside lists and mappings: `True` never equals `1`, and a container never equals a scalar. §2.3 makes `True` and `1` different actions with different hashes; a policy in which they are the same value would be a hole.

### 3.3 Decisions

```python
class Decision(StrEnum):
    ALLOW = "allow"
    APPROVE = "approve"
    DENY = "deny"
```

Exactly these three in v0.1. No `allow_with_log`, `transform`, or `terminate`.

`StrEnum` (stdlib, Python ≥ 3.11), not `(str, Enum)`: a member must render as its value under `str()` and f-string interpolation, not as `Decision.ALLOW`, because those renderings reach receipts and CLI output (§6.1).

### 3.4 Fail-closed defaults (not configurable in v0.1)

| Condition | Result |
|---|---|
| Action name not in `actions` | `DENY` (`reason="unknown_action"`) |
| `actions: {}` | every action `DENY` (`reason="unknown_action"`) |
| Policy file missing / unreadable | `PolicyError` at load; `Control` cannot be constructed |
| `CTRLRUN_CONFIG` set but empty | `PolicyError` at load |
| Policy malformed / unknown schema | `PolicyError` at load |
| `schema` key absent | `PolicyError` at load — treated as an unknown schema, never as "assume v1" |
| Argument type mismatch in a rule | condition false → falls through |

---

## 4. Approval

### 4.1 Model

```python
@dataclass(frozen=True)
class ApprovalRequest:
    request_id: str  # "apr_" + 32 hex (128 bits; it is a bearer token in v0.2)
    action_hash: str
    action: Action  # for human display
    created_at: datetime
    expires_at: datetime  # default now + 15 minutes


@dataclass(frozen=True)
class Approval:
    approval_id: str  # == request_id in v0.1
    action_hash: str
    approver: str  # free text in v0.1, e.g. "cli:local"
    granted_at: datetime
    expires_at: datetime
```

Stored records additionally carry `status: pending | granted | denied | expired | consumed`.

### 4.2 Invariants

- **A1 Exact binding.** An approval is valid for an Action only if `approval.action_hash == action.action_hash`. Anything else → `ApprovalMismatch`, the action is refused (receipt `result: blocked`), event `APPROVAL_INVALIDATED`. The receipt keeps the decision policy actually reached — `decision: approve` — because a refusal at the approval gate does not retract what the policy said.
- **A2 Single use.** Consuming an approval (transitioning `granted → consumed`) MUST be atomic in the StateStore. A second presentation → `ApprovalMismatch(reason="consumed")`.
- **A3 Expiry.** `now > expires_at` → invalid. Expiry is checked at consumption time, not only at grant time.
- **A4 Consumption precedes execution.** The approval is consumed in the same store transaction as the effect reservation (§5.3). If reservation fails, the approval is *not* consumed.

### 4.3 Providers

```python
class ApprovalProvider(Protocol):
    def request(self, action: Action, ttl: timedelta) -> ApprovalRequest: ...

    def wait(self, request_id: str, timeout: timedelta | None) -> Approval | None:
        """Block until the request is answered.

        Returns the `Approval` if it was granted, and `None` if it was denied — `None`
        means "answered, no", never "still waiting". Raises `ApprovalTimeout` if nobody
        answers within `timeout`, or before the request's own `expires_at`, whichever
        comes first. A `wait()` timeout is client-side: it changes nothing in the store,
        so the request stays pending until it is granted, denied, or expires.
        """
```

`wait()` never *consumes* an approval; consumption happens in `Control` (§4.2 A4). A provider may record the answer on a human's behalf — `ScriptedApprovalProvider` writes its scripted grant or denial to the store — but it never invents one.

v0.1 ships:

- `LocalApprovalProvider` — writes the request to the StateStore; `wait()` polls until `ctrlrun approve <id>` / `ctrlrun deny <id>` runs in another shell, or timeout → `ApprovalTimeout`.
- `ScriptedApprovalProvider` — for tests and `ctrlrun demo`; grants/denies/mutates per a script.

Default behaviour of `@protect` on `APPROVE` when `wait=False` (the default): raise `ApprovalRequired(request_id=...)` immediately, so an agent loop can surface it. The caller re-invokes with `ctrlrun.with_approval(request_id)` in context. With `wait=True`, the decorator blocks on `provider.wait()`.

---

## 5. Effects

### 5.1 Effect key

`@protect(..., effect="refund:{payment_id}")`. The template is resolved against the action's arguments (and `resource` via `{resource}`). Missing placeholder → `EffectKeyError` (the action is denied; never a silent `None`).

`resource` is a template too — `@protect(..., resource="payment:{payment_id}")` — with the same syntax and the same resolver. It differs in *when* it resolves. `resource` is part of the canonical form and therefore of `action_hash` (§2.2), so it MUST be resolved against the bound call arguments **before** the Action is constructed, and a missing placeholder raises `InvalidArgument` at construction time, consistent with §2. The effect template resolves afterwards, against the constructed Action, which is why it can reference `{resource}` and `resource` cannot reference `{effect}`. A `resource` containing no placeholders is a literal.

An action with no `effect=` declared has no logical effect: no reservation, no duplicate protection, receipt `effect_key: null`. This is the documented escape hatch for reads.

Effect keys are opaque strings, globally unique within a StateStore. Namespace them: `refund:{payment_id}`, not `{payment_id}`.

**Template grammar.** A template is literal text and `{name}` placeholders, where `name` is an identifier — a letter or underscore, then letters, digits or underscores — because it names an argument, and an argument name is a Python parameter name. Nothing else: no `{{` escapes, no format specs, no attribute or index access, no unmatched brace. Anything else → `InvalidArgument`, raised by `@protect` at **decoration time**, so a typo fails at import rather than mid-run. The grammar is deliberately smaller than `str.format`: an effect key is an identity, not a formatted string, and a typo must never become part of one.

**Placeholder values.** A placeholder MUST resolve to a non-empty `str` or an `int`, and `bool` is not an `int` here any more than it is in §3.2. `None`, `""`, `bool`, and any list or mapping → `EffectKeyError`. `None` and `""` identify nothing and would collide across unrelated actions; a container has no stable rendering; a `bool` names no effect. An `effect_key` passed directly to `Control.execute` is under the same rule: a non-empty string or `None`, anything else → `InvalidArgument`.

**`{resource}` and an argument of the same name.** `{resource}` in an effect template names the action's `resource` field. If the action also carries an argument named `resource`, the template is ambiguous → `EffectKeyError`. Two candidate values for one identity is the fail-closed case: refuse, rather than silently pick the one the author did not mean, because which one it is decides whether two attempts are the same effect. `{resource}` on an action with no `resource` is a missing placeholder, as any other unresolvable name is.

**Resolution order, and the shape of the refusal.** The effect key is resolved **before** the policy is evaluated. An action whose logical effect cannot be identified cannot be protected against duplication, whatever the policy would have said about it, and asking a human to approve an action that can never reserve wastes the one scarce resource in the system. So an unresolvable template refuses an action the policy would have allowed, and the refusal is recorded: receipt `result: "denied"`, `decision: "deny"`, `decision_reason: "effect_key_error"`, `effect_key: null`; events `ACTION_PROPOSED` then `ACTION_DENIED` with `data.reason = "effect_key_error"`, and **no** `POLICY_EVALUATED`, because the policy never ran. The `decision` is the fail-closed value rather than a rule the policy reached — the reason says which. Unlike a call outside `context()` (§2.1), there is a principal here to attribute the refusal to, so it belongs in the evidence log.

**`EffectKeyError` is not an `ActionDenied`.** It subclasses `CTRLRunError` directly. "The action is denied" above describes the outcome, not the exception: an unresolvable template is a wiring bug in the agent's own code, and an agent loop's `except ActionDenied` — written to handle a policy saying no — must not swallow it.

### 5.2 States

```python
class EffectState(StrEnum):
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
    # An approved action takes authority and effect identity in ONE call (§4.2 A4).
    def consume_approval_and_reserve(self, approval_id: str, action_hash: str,
                                     effect_key: str, action_id: str,
                                     lease: timedelta) -> tuple[Approval, Reservation]: ...
    # The one-sided cases, for an action that has only one half to take.
    def reserve_effect(self, effect_key: str, action_id: str, lease: timedelta) -> Reservation: ...
    def consume_approval(self, approval_id: str, action_hash: str) -> Approval: ...
    def begin_execution(self, effect_key: str, action_id: str) -> None: ...
    def commit_effect(self, effect_key: str, action_id: str, result: Any) -> None: ...
    def fail_effect(self, effect_key: str, action_id: str, error: str) -> None: ...
    def mark_ambiguous(self, effect_key: str, action_id: str, error: str) -> None: ...
    def get_effect(self, effect_key: str) -> EffectRecord | None: ...
    # approvals
    def put_approval_request(...); def grant_approval(...); def deny_approval(...)
    # evidence
    def append_event(self, event: Event) -> Event: ...   # v0.2 §4.1: returns it as stored
    def put_receipt(self, receipt: Receipt) -> None: ...
```

`append_event` returned `None` in v0.1. SPEC-v0.2 §4.1 requires `Control` to hand every event
to its `EventSink`s **with the `event_id` the store assigned**, and the store is the only thing
that knows it, so the stored event is returned. Callers that ignore the return value are
unaffected; this is stated here rather than only in the v0.2 delta because §8 is where a store
implementor reads the protocol.

`consume_approval_and_reserve` is what §4.2 A4 asks for, and a caller MUST NOT reconstruct it by sequencing `consume_approval` and `reserve_effect`: two calls cannot share a transaction, so the split consumes an approval for an attempt that then fails to reserve — the exact hole A4 closes. `reserve_effect` is for an action with no approval to take (`ALLOW` with an `effect=`), `consume_approval` for an approved action with no effect key (§5.1); neither is a building block for the other case.

Inside the one call: the approval is checked first, so its refusal is the one raised when a replayed approval and a duplicate effect both apply (T4); the reservation is decided before either half is written, so a refused reservation leaves the approval granted (T12).

- **E1 Atomic reservation.** `reserve_effect` MUST succeed for at most one caller per `effect_key` across threads *and processes*. SQLite implementation: `PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000; BEGIN IMMEDIATE; INSERT ... ; COMMIT` with `UNIQUE(effect_key)`.
- **E2 Existing effect.** If a record exists, reservation outcome depends on its state (§5.4).
- **E3 Lease.** A reservation carries `lease_expires_at`. A record in `RESERVED` or `EXECUTING` whose lease has expired is treated as `AMBIGUOUS` (the executor may have died mid-flight). It is not silently released.

**How long a lease is.** Five minutes by default, set per Control (`Control(..., lease=...)`) and overridden per action (`@protect(..., lease=...)`), because the right length is a property of the work, not of the library. A five-minute lease on a twenty-minute deploy expires mid-flight and turns every success into `AMBIGUOUS` — safe, but wrong, and the fix a user reaches for is to stop declaring an `effect=` at all, which removes duplicate protection entirely. A guarantee people switch off is not a guarantee.

The lease MUST be a positive `timedelta`. Zero or negative reserves nothing — the first contender would find the record expired and declare a healthy effect ambiguous — so it is `InvalidArgument`, raised by `@protect` at **decoration time** (with the template checks of §5.1), and by `Control` and `Control.execute` when either is handed one.

Length is the only thing configurable here. Past its lease, however long, a record is `AMBIGUOUS` and only a human moves it on: there is no setting that releases an expired reservation, and none that extends a lease already granted.

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
| raises anything else (incl. `TimeoutError`, connection errors, `KeyboardInterrupt`) | `AMBIGUOUS` |

The executor opts *into* `FAILED` by raising `NotExecuted`, asserting it knows the remote side did nothing (e.g. HTTP 4xx validation error before any side effect). The default for the unknown is `AMBIGUOUS`. This asymmetry is deliberate and MUST NOT be inverted.

"Anything else" means `BaseException`, not `Exception`. A `KeyboardInterrupt` or `SystemExit` arriving mid-request leaves exactly the outcome a timeout leaves — the remote may already have committed — so the effect MUST be recorded `AMBIGUOUS` before the exception is re-raised. Narrowing that catch to `Exception` is a regression, not a cleanup.

**An outcome the store refuses to write.** The mapping above is a write to the effect record, and that write can be refused: while the executor ran, this attempt's lease lapsed and another attempt declared the effect `AMBIGUOUS` (§5.3 E3), which only a human moves it out of. The attempt is then recorded `ambiguous` — receipt `result: "ambiguous"`, event `EXECUTION_AMBIGUOUS` — whatever the executor returned or raised, because what happened at the remote is now exactly as unknown as a timeout. The store's refusal (`AmbiguousEffect`, or `DuplicateEffect`) is what propagates to the caller.

It propagates *instead of* `NotExecuted`, and that is the point of stating it: `NotExecuted` is the one exception an agent may read as permission to retry, and this attempt no longer holds the key it would retry. A refused `FAILED` MUST NOT reach the caller as `NotExecuted`, and MUST NOT move the record: `AMBIGUOUS` does not collapse to `FAILED` by this path either (§5.2).

**Recording the unknown never masks what caused it.** When the outcome being written is itself `AMBIGUOUS`, a refusal by the store MUST NOT replace the executor's exception. The record is already in the state this attempt was trying to put it in, the receipt says `ambiguous` either way, and the exception the executor raised is the one the caller sees. `mark_ambiguous` is therefore idempotent, and a failure to record an unknown outcome is logged, not raised — the alternative is a library that swallows a `KeyboardInterrupt` while tidying up after it.

---

## 6. Evidence

### 6.1 Receipt

One per action that reached a terminal state (including denials). JSON, one per line in `.ctrlrun/receipts.jsonl`, and stored in SQLite.

An action awaiting approval has no receipt; `APPROVAL_REQUESTED` is its evidence. The request stays `pending` in the store until it is granted, denied, or expires, and can be presented later. This is the one place a proposed action leaves no receipt: it has not reached a terminal state, and `result` has no value for "waiting for a human".

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

**When only one of the two writes succeeds.** SQLite is authoritative and the JSONL file is a convenience export of what it already holds, so the store is written first and the file second. A failed file write MUST be logged on the `ctrlrun` logger and MUST NOT be raised. By the time it runs, the effect has committed at the remote and the record is durable; raising there would reach the caller as an exception on a successful action, and an agent that reads it as a failure retries — which is the one mistake this library exists to prevent. Nothing is hidden by the loss: `ctrlrun receipts` and `ctrlrun effects` read the database, not the files. The reverse order is not available: a store that refuses the write has not recorded the action, and there is nothing to export.

Enums MUST render by value everywhere evidence is produced — receipt JSON, event `data`, and CLI output: `"approve"`, never `"Decision.APPROVE"`. This is why `Decision` (§3.3) and `EffectState` (§5.2) are `StrEnum`; the guard is `test_decision_renders_by_value` in `tests/test_policy.py`, which pins `str()` and f-string interpolation. A receipt is read by tools that never imported CTRLRun.

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

`APPROVAL_INVALIDATED` is the umbrella: it is appended whenever a presented approval did not authorize the action, with `data.reason` saying which invariant failed (`mismatch`, `consumed`, `expired`, `pending`, `unknown`). The more specific event, where one exists, is appended *first*: an expired approval produces `APPROVAL_EXPIRED` then `APPROVAL_INVALIDATED`, and a denied one produces `APPROVAL_DENIED` then `ACTION_DENIED` — a denial is a human's answer, not an invalid approval, so it does not take the umbrella.

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
from .control import protect, Control, context, with_approval
from .action import Action, Principal, action_hash, canonicalize
from .policy import Decision, Policy
from .approval import (
    Approval,
    ApprovalRequest,
    ApprovalProvider,
    LocalApprovalProvider,
    ScriptedApprovalProvider,
)
from .effect import EffectState, EffectRecord
from .state import StateStore, SQLiteStateStore, InMemoryStateStore
from .receipt import Receipt, Event
from .errors import (
    CTRLRunError,
    InvalidArgument,
    PolicyError,
    EffectKeyError,
    ActionDenied,
    ApprovalRequired,
    ApprovalTimeout,
    ApprovalMismatch,
    DuplicateEffect,
    AmbiguousEffect,
    NotExecuted,
)
```

```python
protect(name: str, *, effect: str | None = None, resource: str | None = None,
        wait: bool = False, lease: timedelta | None = None,
        control: Control | None = None) -> Callable

context(agent: str, user: str | None = None) -> ContextManager
# `environment` was a parameter here until v0.3. SPEC-v0.3 §2.5 removed it: the environment
# became an authorization input, and a dimension the subject sets is not one. It is set once
# on the Control instead.
with_approval(request_id: str) -> ContextManager

Control.from_file(path=None) -> Control
Control(policy: Policy, store: StateStore, approvals: ApprovalProvider | None = None, *,
        clock=..., approval_ttl: timedelta = timedelta(minutes=15),
        lease: timedelta = timedelta(minutes=5))
Control.evaluate(action: Action) -> Evaluation   # decision + reason, no side effects
Control.execute(action: Action, executor: Callable[[], Any], effect_key: str | None,
                *, lease: timedelta | None = None) -> Receipt
```

`approvals=None` builds a `LocalApprovalProvider(store)`: the local provider is the only one that asks a real human, so it is the safe default, and a `Control` is never without a way to ask.

**Where the state lives.** `Control.from_file()` opens a `SQLiteStateStore` at `.ctrlrun/state.db` **in the policy file's directory**, creating the directory if absent. Beside the policy, not beside the process: workers started from different working directories but sharing a policy must share one store, or reservation is atomic within each of them and meaningless between them (§5.3 E1).

`CTRLRUN_STATE` overrides the path. Set but empty → `InvalidArgument`, as `CTRLRUN_CONFIG` fails in §3.4: a configured-but-blank path is a misconfiguration, and falling back to the default would put an agent's effects in a store nobody is watching.

An in-memory SQLite path — `:memory:`, or any path carrying `mode=memory` — MUST be refused with `InvalidArgument`, wherever it comes from. Such a database is private to a single connection, so it cannot reserve across threads, let alone processes; accepting one would drop E1 silently, which is the one failure mode this store exists to prevent. `InMemoryStateStore` is the supported in-process store, and says what it cannot do.

`effect` and `resource` are templates (§5.1). The call is bound to the wrapped function's signature and defaults are applied, so a defaulted argument is part of the action and of its hash — the action must describe what will actually run.

`lease` is how long one action's reservation is held (§5.3 E3). `@protect(..., lease=...)` overrides the `Control`'s, which is five minutes unless it was given one; `execute(lease=None)` means the Control's. It is the only lease knob: a lease that is not a positive `timedelta` is `InvalidArgument` wherever it is offered, and for `@protect` that is at decoration time, alongside the template syntax check.

`protect` MUST reject a function declaring `*args` or `**kwargs`, at decoration time, with `InvalidArgument`. An Action's arguments are a mapping of *named* values (§2.1), and policy conditions and templates address them by name; a variadic parameter has no such name and could never be written into a rule. Positional-only and keyword-only parameters are accepted — they have names.

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

Every command but `init` and `demo` works on the store `state_path()` resolves — the one an agent sharing this policy is already using — so `ctrlrun approve` answers a request some other process is waiting on.

**Where the demo keeps its state.** `ctrlrun demo` MUST NOT write to that store. It keeps its own at `.ctrlrun/demo/state.db`, with the JSONL evidence of §6 beside it, and it MUST empty that directory of a previous run's evidence by filename — `state.db`, its `-wal` and `-shm`, `receipts.jsonl`, `events.jsonl` — never by removing a directory. Two reasons, both about not touching what it does not own: the demo reserves effect keys (`refund:txn_1`, `refund:txn_123`), which in a live store would collide with real work or block it; and a demo that is not repeatable is not a demo, since the second run would refuse every scenario as a duplicate. Its last line MUST print the `CTRLRUN_STATE=… ctrlrun receipts` command that reads what it just wrote.

---

## 9. Explicitly out of scope for v0.1

Authority, identity providers, delegation, resource/data scope, consequence taxonomy, CONTROL registry, multi-approver, separation of duties, OPA/Cedar/ACS, MCP gateway, OpenTelemetry, webhooks, Postgres, `ctrlrun verify`, compensation, framework adapters, signatures on receipts, any UI or server.
