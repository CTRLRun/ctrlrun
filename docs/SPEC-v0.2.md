# CTRLRun v0.2 Specification

This is a **delta over [`SPEC-v0.1.md`](SPEC-v0.1.md)**. Everything in v0.1 still holds; this
document states only what v0.2 adds or changes. A reference to the kernel contract is written
`v0.1 §5.4`; a bare `§5.4` is a section of this document. Nearly every section number exists
in both, so the prefix is not decoration — an unprefixed reference to v0.1 is a defect.

Tests are derived from §10. Public names added here are frozen in §11. Anything not in this
document or in v0.1 is out of scope for v0.2.

Words: MUST / MUST NOT / SHOULD are used in the RFC 2119 sense.

**The MCP revision this document is written against** is **2026-07-28**, the current revision
as published at <https://modelcontextprotocol.io/specification/2026-07-28/>, read on
2026-09-03. It is a stateless protocol: there is no `initialize` handshake, no protocol-level
session, and no `Mcp-Session-Id`. Where this document depends on a rule of that revision it
cites the page. §6.2 states exactly which revisions the gateway accepts and why.

---

## 1. Scope

v0.2 delivers seven things, one build-list item each, plus a release.

| # | Deliverable | Ships in | Section |
|---|---|---|---|
| 1 | Reconciliation hook | core | §2 |
| 2 | Per-action `effect:` / `resource:` in policy | core | §3 |
| 3 | `EventSink` protocol; JSONL becomes a sink | core | §4 |
| 4 | `ctrlrun inspect <action_id>` | core | §5 |
| 5 | MCP gateway | `ctrlrun[gateway]` | §6 |
| 6 | Webhook approval provider | core (outbound), gateway (inbound) | §7 |
| 7 | OpenTelemetry sink, and the ACS design note | `ctrlrun[otel]` | §8, §9 |

Item 8 is the release: version, changelog, `docs/CLAIMS.md` regeneration, README.

**The dependency rule.** `pip install ctrlrun` MUST continue to install nothing but `pyyaml`
and `click`. Anything needing an HTTP server or a third-party SDK ships as an optional extra.
An extra's module MUST import lazily — `import ctrlrun` MUST NOT import `httpx` or any
`opentelemetry` package — and a missing extra MUST raise `MissingDependency` naming the
install command, never `ImportError` or `ModuleNotFoundError`.

The outbound half of the webhook provider (§7) is core because it needs neither: it is one
signed POST over stdlib `urllib.request`. Only the inbound endpoint needs a server, and that
is the gateway's.

**Carried out of v0.2.** `ROADMAP.md` lists two further v0.2 items — an `examples/` directory
and sector policy templates — that are not among the seven above and are not specified here.
Item 8 MUST either deliver them or move them in `ROADMAP.md`; it MUST NOT leave the roadmap
claiming them for a shipped release.

**Network code is kernel code.** The gateway sits in the execution path of consequential
actions. Every rule of v0.1 §3.4, §5.4 and §5.5 applies to it unchanged: it fails closed on
anything it cannot parse, and it never lets a transport error look like a definite failure.
§6.8 is the whole of that argument in one table.

---

## 2. Reconciliation hook

### 2.1 The hook

```python
ReconcileOutcome = Literal["committed", "not_executed", "unknown"]

protect(..., reconcile: Callable[[str], ReconcileOutcome] | None = None,
             reconcile_eagerly: bool = False)
Control.execute(..., reconcile: Callable[[str], ReconcileOutcome] | None = None,
                     reconcile_eagerly: bool = False)
```

The hook is given the **effect key** and nothing else. It answers one question — *did this
logical effect happen at the remote?* — and it is the only thing besides a human that may
move a record out of `AMBIGUOUS`.

An action with no effect key has nothing to reconcile, and the hook is never called for one.
This is **not** a decoration-time error, because the effect template may come from the policy
rather than the decorator (§3) and the policy is not loaded yet. It is a warning, logged once
per decorated function the first time an action runs with a `reconcile` hook and no effect
key. A dangling hook does nothing, unlike a mistyped template, which changes an identity —
which is why v0.1 §5.1 refuses that and this only says so.

### 2.2 Amendment to v0.1 §5.2

v0.1 §5.2 says only a human (`ctrlrun resolve`) can move `AMBIGUOUS → COMMITTED | FAILED`.
v0.2 amends that: **a `reconcile` hook may also move it, and only in the direction its answer
names.** Nothing else changes — `AMBIGUOUS` still MUST NOT collapse to `FAILED` by any other
path, an expired lease is still `AMBIGUOUS` and is still never silently released, and there is
still no configuration that releases a reservation.

Every `EFFECT_RESOLVED` event MUST carry `data.resolved_by ∈ {"human", "reconcile"}`, and the
effect record MUST store it, so evidence distinguishes a human's judgement from a machine's
answer. `ctrlrun effects` and `ctrlrun inspect` (§5) MUST show it.

### 2.3 When it is called

Two moments, and no others:

1. **Blocking** — at the moment an existing `AMBIGUOUS` record refuses a new attempt's
   reservation (v0.1 §5.4), *before* `AmbiguousEffect` is raised. A record whose lease
   expired is moved to `AMBIGUOUS` first (v0.1 §5.4) and then reconciled: the order matters,
   because
   reconciliation asks about a record, and the record must be in the state that describes it.
2. **Eager** — immediately after this attempt produced an `AMBIGUOUS` outcome, if
   `reconcile_eagerly=True`. Default `False`.

Eager reconciliation MUST NOT run when the outcome was produced by a `BaseException` that is
not an `Exception` — a `KeyboardInterrupt` or `SystemExit` mid-request. v0.1 §5.5 already
refuses to let tidying up swallow an interrupt; calling arbitrary user code while unwinding
one is the same mistake with a longer stack. The record stays `AMBIGUOUS` and the exception propagates.

**At most one call per attempt.** One `Control.execute` call MUST call `reconcile` at most
once, counted explicitly and not inferred from control flow. Both moments can arise in a
single attempt: a blocking reconcile answering `not_executed` unblocks the reservation, the
attempt then runs and ends `AMBIGUOUS`, and eager reconciliation would fire on the same
attempt. The second call MUST NOT happen. A hook that is wrong or slow costs one call per
attempt, never two, and a retry loop cannot amplify it.

### 2.4 Outcome mapping

| `reconcile` returns | Effect record | New attempt |
|---|---|---|
| `"committed"` | `AMBIGUOUS → COMMITTED` | refused, `DuplicateEffect(state="committed")` |
| `"not_executed"` | `AMBIGUOUS → FAILED` | permitted, `attempt += 1` (v0.1 §5.4) |
| `"unknown"` | unchanged | refused, `AmbiguousEffect` |

Anything the hook raises → `"unknown"`, logged as a warning on the `ctrlrun` logger with the
exception. Any return value that is not one of the three literals → `"unknown"`, logged as a
warning naming what came back. A hook that cannot answer must not be able to widen anything;
`"unknown"` is the value that changes nothing.

A `BaseException` that is not an `Exception` raised by the hook propagates, for the reason in
§2.3.

`"committed"` and `"not_executed"` are assertions about the remote, exactly as `NotExecuted`
is (v0.1 §5.5). CTRLRun cannot check them. The hook's author takes that responsibility
knowingly, which is why the hook is an explicit argument and not a default behaviour.

### 2.5 Events

Two new types join the closed set of v0.1 §6.2:

```
RECONCILIATION_STARTED
RECONCILIATION_RESOLVED
```

`RECONCILIATION_STARTED` carries `data.effect_key` and `data.trigger ∈ {"blocking", "eager"}`.
`RECONCILIATION_RESOLVED` carries `data.outcome ∈ {"committed", "not_executed", "unknown"}`
and, when the outcome was forced to `"unknown"`, `data.reason ∈ {"raised", "invalid_return"}`
with `data.error` naming it. `RECONCILIATION_RESOLVED` MUST be appended for every
`RECONCILIATION_STARTED`, `"unknown"` included: evidence has to record that CTRLRun asked and
learned nothing, or a silent hook is indistinguishable from no hook.

Where the outcome moved the record, `EFFECT_RESOLVED` with `data.resolved_by = "reconcile"`
follows `RECONCILIATION_RESOLVED`.

### 2.6 What is not bounded

CTRLRun gives the hook no timeout, exactly as it gives the executor none (v0.1 §5.5). A hook that
hangs hangs the attempt that called it, and in the blocking case that is a *retry* hanging on
a call the caller did not write. This is a stated limit, not an oversight: a timeout here
would need a thread or a signal, and killing a half-finished reconciliation query is how you
get a wrong answer instead of no answer. Bound it inside the hook.

---

## 3. Policy: `effect:` and `resource:` per action

### 3.1 Schema

```yaml
schema: ctrlrun.policy/v2

actions:
  mcp.payments.create_refund:
    effect: "refund:{payment_id}"
    resource: "payment:{payment_id}"
    mcp:
      not_executed_on_error: true
      mrtr: deny
    rules:
      - when: { amount_gte: 0, amount_lte: 50000 }
        decision: allow
      - decision: approve
```

The gateway (§6) has no decorator to carry an effect template, so the policy file has to. The
closed key set of v0.1 §3.1 grows: an action entry now accepts `decision`, `rules`, `effect`,
`resource` and `mcp`; the `mcp` mapping accepts `not_executed_on_error` and `mrtr` and nothing
else. Any other key at either level is still a load-time `PolicyError`.

**`schema: ctrlrun.policy/v2`.** v0.2 loads both `ctrlrun.policy/v1` and `ctrlrun.policy/v2`.
Using `effect`, `resource` or `mcp` in a document declaring `v1` is a `PolicyError` naming the
key and the required schema. A `v1` file keeps loading unchanged, and a `v2` file fails to
load on v0.1 — correctly, because v0.1 would ignore the effect template and execute with no
duplicate protection at all. The schema string is the only thing standing between those two
outcomes, so it is not optional and not inferred.

`effect` and `resource` are templates under the grammar and the placeholder rules of v0.1 §5.1,
validated **at load time**: a malformed template is a `PolicyError`, not a runtime
`EffectKeyError`. `mcp.not_executed_on_error` MUST be a `bool`; `mcp.mrtr` MUST be the string
`"deny"` or `"allow"`. Anything else → `PolicyError`. Both are optional and default to the
fail-closed value (`false`, `"deny"`).

### 3.2 Precedence

Where both a decorator and the policy declare `effect` or `resource` for the same action, the
**decorator's value is used**. The policy is a deployment artefact and the decorator is a
statement in the code that will run; when they disagree, the code is what executes, and
silently substituting the policy's template would change an effect identity without changing a
line of the program.

A mismatch MUST be logged as a warning on the `ctrlrun` logger naming the action, both
templates, and which one is in force. The warning is emitted when the decorated function first
resolves its `Control` — at decoration time if `control=` was passed, otherwise on the first
call — and at most once per decorated function, because a warning that repeats per call is a
warning nobody reads. It is a warning and not an error: an operator adding templates to a
policy for the gateway's sake should not break a running decorator-based deployment.

The gateway has no decorator, so for a gateway action the policy's templates are the only
ones; an action with no `effect:` in policy has no logical effect and gets no reservation,
which is the documented escape hatch of v0.1 §5.1 and is right for reads. `ctrlrun gateway` MUST
print, at startup, the list of actions in its policy that have no `effect:` — a write with no
effect key is exactly the configuration this product exists to prevent, and it should be
visible on the line that starts the process, not discovered in a receipt.

### 3.3 A policy edit can void an approval

`resource` is part of the canonical form and therefore of `action_hash` (v0.1 §2.2). Editing a
policy's `resource:` template changes the hash of every action it applies to, so approvals
granted before the edit no longer authorize anything (v0.1 §4.2 A1). This is correct and is stated
here so it is not surprising: a pending approval describes an action that no longer exists.

---

## 4. `EventSink`

### 4.1 The protocol

```python
class EventSink(Protocol):
    def on_event(self, event: Event) -> None: ...
    def on_receipt(self, receipt: Receipt) -> None: ...

Control(..., sinks: Sequence[EventSink] = ())
```

Sinks are called by `Control` **after** the authoritative store write for that record has
succeeded, in registration order, with the `event_id` the store assigned.

### 4.2 Sinks never raise into the kernel

`Control` MUST catch every `Exception` a sink raises, log it as a warning on the `ctrlrun`
logger naming the sink's class and the record, and continue with the remaining sinks. A
failing sink MUST NOT change a decision, an effect state, a receipt, or the exception the
caller sees.

A `BaseException` that is not an `Exception` propagates, for the reason in v0.1 §5.5: swallowing a
`KeyboardInterrupt` while exporting telemetry is worse than losing the export.

This is v0.1 §6.1's existing rule generalized. SQLite is authoritative and everything else is a
convenience export of what it already holds; by the time a sink runs, the effect has committed
at the remote and the record is durable, and raising there would reach the caller as an
exception on a successful action — which an agent reads as a failure, and retries.

### 4.3 What is and is not a sink

`JSONLEventSink` replaces the `EventLog` that `SQLiteStateStore` writes today. `Control` owns
it; the store stops writing files. `Control.from_file()` installs
`JSONLEventSink(state_path().parent)` by default, so the two files of v0.1 §6 land exactly where
v0.1 put them and existing evidence directories are unchanged.

**The store's own `events` and `receipts` tables are not sinks.** They are written inside the
store, in its transaction, before any sink is called. Demoting the authoritative write to one
of a list of best-effort exporters would invert v0.1 §6.1's ordering guarantee and leave "the store
is authoritative" as a sentence with nothing behind it. `EventSink` is the interface for the
copies; it is not the interface for the record.

Sinks are not transactional, not ordered across processes, and not retried. A sink that must
not lose records buffers and retries inside itself.

---

## 5. `ctrlrun inspect <action_id>`

One action's whole history in one place: what was proposed, what the policy said, which
approval was involved, what happened to the effect, and what the receipt records.

```
ctrlrun inspect <action_id> [--json]
```

`action_id` is matched **exactly** — no prefixes, no globs, consistent with v0.1 §3.1's rule for
action names. An unknown `action_id` MUST exit non-zero with a message on stderr and print
nothing on stdout, so a script cannot mistake "no such action" for "an action with no events".

Human output is a header block followed by the event timeline, ordered by `event_id`:

```
act_71ab…                       stripe.refund
principal   refund-agent (user: alice)
resource    payment:txn_8231
environment production
arguments   {"amount": 2000, "currency": "EUR", "payment_id": "txn_8231"}
decision    approve  (rule[1])
approval    apr_9918…  granted by cli:local, consumed
effect      refund:txn_8231  committed  attempt 1
receipt     ctr_29182f1a0b3c  committed

  1  2026-09-03T10:12:01.120Z  ACTION_PROPOSED
  2  2026-09-03T10:12:01.121Z  POLICY_EVALUATED     decision=approve reason=rule[1]
  …
```

`--json` emits one object:

```json
{
  "schema": "ctrlrun.inspection/v1",
  "action_id": "act_71ab…",
  "receipt": { … } | null,
  "effect": { … } | null,
  "approvals": [ … ],
  "events": [ … ]
}
```

`receipt` is `null` for an action still awaiting a human (v0.1 §6.1). `effect` is `null` for an
action with no effect key. `approvals` is every approval record carrying this action's hash,
not only the consumed one, because an invalidated or expired approval is part of the history.

Enums render by value everywhere, in both formats (v0.1 §6.1).

---

## 6. MCP gateway

```
ctrlrun gateway --upstream <url> --alias <name> [options]
```

An HTTP process that speaks MCP on both sides. An MCP client points at it instead of at the
tool server; it applies CTRLRun's decision, effect and evidence semantics to `tools/call` and
relays everything else. No agent changes.

Ships in `ctrlrun[gateway]`, whose only dependency is an HTTP client (§6.11).

### 6.1 Model

One gateway process fronts **one** upstream MCP server under **one** alias. Several upstreams
means several processes. A single process multiplexing upstreams would have to choose an
upstream per request from something in the request, and every candidate for that something is
agent-controlled.

```
MCP client ──HTTP──► ctrlrun gateway ──HTTP──► upstream MCP server
                            │
                     Control.from_file()
                     policy · approvals · effects · receipts
```

State lives where v0.1 §8 says it lives: `.ctrlrun/state.db` beside the policy file. A gateway and
a decorator-based worker sharing a policy share a store, and therefore share reservations.

The listening address defaults to `127.0.0.1:8900`, per the transport's *"When running
locally, servers **SHOULD** bind only to localhost"*. Binding anything else requires
`--allow-remote` and logs a warning naming the address.

The MCP endpoint path defaults to `/mcp`. The gateway MUST refuse to start if that path
overlaps `/ctrlrun/`, which is reserved for the approvals endpoint (§7).

**`Origin` is validated before anything else.** *"Servers **MUST** validate the `Origin`
header on all incoming connections to prevent DNS rebinding attacks. If the `Origin` header is
present and invalid, servers **MUST** respond with HTTP 403 Forbidden."* The allowlist is
`--allow-origin`, repeatable, empty by default: with no allowlist, **any** request carrying an
`Origin` header is refused. A request with no `Origin` — the normal case for a non-browser
client — is permitted. An empty allowlist that accepted every origin would be validation in
name only.

`GET` and `DELETE` on the MCP endpoint MUST return `405 Method Not Allowed`, per the
revision's instruction for traffic from older clients. The gateway MUST NOT mint or echo an
`Mcp-Session-Id` and MUST ignore `Last-Event-ID`.

### 6.2 Protocol revisions the gateway accepts

**Only revisions that require header–body validation: `2026-07-28` and later.** A request
whose `MCP-Protocol-Version` header is absent, or names an earlier revision, MUST be refused
with HTTP 400 and `UnsupportedProtocolVersion` (`-32022`), `data.supported = ["2026-07-28"]`.

Half of that is the revision's own instruction to intermediaries: *"Intermediaries that enforce
policy based on mirrored headers (e.g., routing or rate-limiting by tenant) **SHOULD** verify
that the `MCP-Protocol-Version` header indicates a version that requires header–body
validation. If the version is older or the header is absent, the intermediary **SHOULD**
reject the request rather than trusting unvalidated header values."* CTRLRun is that
intermediary, and its decision is worth more than a rate limit.

The other half is scope, and is stated rather than dressed up as safety. Because the gateway
parses every body anyway (§6.4), it does not actually *need* the mirrored headers, and could
in principle decide a legacy request correctly. What it would also need is the legacy
transport: connection-scoped sessions with `Mcp-Session-Id` and DELETE, a standalone GET
stream, server-initiated JSON-RPC requests arriving on SSE, and `Last-Event-ID` resumption —
a second proxy to build and a second set of failure modes to map onto §6.8. v0.2 does not
build it. **This means clients that have not moved to `2026-07-28` cannot use the gateway**,
which as of this writing is most of them; that cost is real and is the reason this paragraph
exists rather than a footnote.

It also disposes of three things the item-0 brief assumed and the current revision has removed:
there is no `initialize` handshake, no protocol-level session, and no `Mcp-Session-Id`.
"Missing session" is not a refusal condition in v0.2 because sessions do not exist.

### 6.3 What is intercepted

**`tools/call` and nothing else.** Every other JSON-RPC method — `tools/list`,
`resources/read`, `prompts/get`, `server/discover`, `subscriptions/listen`, and anything an
extension defines — is relayed to the upstream and its response relayed back, unchanged,
including a `subscriptions/listen` stream that stays open.

"Unchanged" is about the JSON-RPC payload, not about scrutiny: §6.4 applies to every request
the gateway forwards, intercepted or not.

A relayed method has **no CTRLRun outcome**. No Action is built, no policy is evaluated, no
effect is reserved, no receipt is written, and an unreachable upstream is an HTTP error and
nothing more. `tools/list` is not an action; only calling a tool is.

HTTP request headers are relayed to the upstream unchanged, hop-by-hop headers excepted. In
particular the gateway MUST forward `Authorization` verbatim and MUST NOT mint, exchange,
inspect or strip a token: the upstream is the OAuth resource server and the gateway is not.
It MUST relay a `401` and its `WWW-Authenticate` challenge back to the client untouched, which
is why §6.10 forbids the gateway from ever generating a `401` of its own.

### 6.4 The gateway validates headers against bodies

The revision mirrors `method`, `params.name`/`params.uri` and annotated tool parameters into
`Mcp-Method`, `Mcp-Name` and `Mcp-Param-{Name}` so intermediaries can route without parsing
bodies, and requires servers that process a body to reject any disagreement with HTTP 400 and
`-32020 HeaderMismatch`, because *"different components in the network rely on different
sources of truth (e.g., a load balancer routing on the header value while the MCP server
executes based on the body value)"*.

That is precisely the hazard CTRLRun would create if it took the shortcut the headers exist
for. So:

- The gateway MUST parse the body of every request it forwards and MUST validate
  `MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name` and every `Mcp-Param-{Name}` against it,
  decoding the `=?base64?…?=` sentinel before comparing, and comparing integers numerically.
  A mismatch, or a missing required header, is HTTP 400 with `-32020`.
- The gateway MUST decide what to intercept from the **body's** `method` and `params.name`,
  never from the headers. The headers are checked; the body is believed.
- The gateway MUST NOT rely on the upstream to perform this validation. A `-32020` arriving
  *from* the upstream means the gateway forwarded a request it should have refused, and is a
  gateway bug.

Request bodies are bounded by `--max-body-bytes`, default 1 MiB. Over that, HTTP 413 and no
forwarding: a body the gateway will not read is a body it cannot decide about.

A body that is a JSON array is refused. The revision is explicit — *"The body of the HTTP POST
**MUST** be a single JSON-RPC *request* or *notification*"* — so there is no batch to
attribute, and a batch the gateway cannot attribute to one principal and one action is exactly
the thing it must not pass through.

### 6.5 Principal

An Action cannot exist without a principal, and the gateway has no `context()` to take one
from. Exactly one of these MUST be given at startup; there is **no default**:

| Flag | Principal source |
|---|---|
| `--principal <agent>` | a fixed agent name, for a single-tenant gateway |
| `--principal-header <name>` | the named HTTP request header |
| `--principal-from-client-info` | `_meta["io.modelcontextprotocol/clientInfo"].name` |

`--user-header <name>` optionally supplies `principal.user`. `--environment` supplies the
action's environment, default `production`.

An empty or absent value where one is configured is refused: `no_principal`, HTTP 403,
`-41007`, **no receipt and no events**, and a warning naming the action. This is v0.1 §2.1's rule
for a call outside `context()`, applied to the same situation over a socket — there is no
principal to attribute the refusal to, so it does not belong in the evidence log, and it must
not be silent either.

**`--principal-from-client-info` is explicit because the spec says not to trust it.** Of
`clientInfo` and `serverInfo` the revision says: *"[they] are self-reported by the sender and
are not verified by the protocol… Implementations **SHOULD NOT** use them to change the
behavior of the client or server, and **SHOULD NOT** rely on them for security decisions."*
The item-0 brief proposed it as the default; it must not be, and it must never be silent.

It is nonetheless offerable in v0.2 because of a fact about v0.1 that will stop being true:
**a policy cannot address the principal at all** (v0.1 §3.2 refuses `agent_eq`, `user_eq` and every
other reserved name at load). The principal is attribution on a receipt, not an input to a
decision, so an unauthenticated one misattributes evidence and cannot widen an outcome. The
flag MUST log a warning at startup saying so. `# SPEC: v0.2 §6.5` — the authority model
(v0.3) makes the principal an authorization input, and this flag MUST be revisited in the same
release that makes that change.

The same limit applies to `--principal-header`, one step removed: the header is worth whatever
the thing that sets it is worth. Set it in a trusted proxy that authenticates the caller. If
the agent sets it, it is self-reported, exactly like `clientInfo`.

### 6.6 From `tools/call` to an Action

| Action field | Source |
|---|---|
| `name` | `mcp.<alias>.<tool_name>` where `tool_name` is the body's `params.name` |
| `arguments` | the body's `params.arguments`, or `{}` if absent |
| `principal` | §6.5 |
| `resource` | the policy's `resource:` template (§3), resolved against the arguments |
| `environment` | `--environment` |

`<alias>` MUST match `^[a-z0-9][a-z0-9_-]*$` — no dots — so the alias boundary in the action
name is unambiguous even though tool names may themselves contain dots (`admin.tools.list`
becomes `mcp.acme.admin.tools.list`). Action names are opaque and matched exactly (v0.1 §3.1); no
case folding is applied to the tool name, which the revision says is case-sensitive.

The effect key is the policy's `effect:` template resolved against the constructed action
(v0.1 §5.1), before the policy is evaluated, with v0.1 §5.1's refusal shape when it cannot be resolved.

**`params._meta` is not part of the action.** It carries the protocol version, `clientInfo`,
`clientCapabilities`, a progress token and W3C trace context — all transport metadata, and all
volatile. Including it would make the action hash change between two identical tool calls and
void every approval. It is forwarded upstream unchanged and, for `traceparent`, read by the
OTel sink (§8).

**Arguments the Action model cannot represent are refused.** MCP tool schemas routinely use
`"type": "number"`, and v0.1 §2.3 rejects `float` at construction. A `tools/call` carrying a float
anywhere in `params.arguments`, at any depth, MUST be refused with HTTP 400 and `-41008`
`ctrlrun.unrepresentable_argument`, naming the JSON pointer to the offending value. The
gateway MUST NOT round, truncate, or coerce it to a string: `0.1` and `0.10` are the same
money and different hashes, and a gateway that quietly picks a spelling has broken the
approval binding for every action that touches the value. The fix belongs in the tool's schema
— integer minor units or decimal strings (v0.1 §2.3). This is a real limit on which MCP servers can
be fronted, and it is stated rather than worked around.

The refusal is recorded like v0.1 §5.1's `effect_key_error`: `ACTION_PROPOSED` then
`ACTION_DENIED`, `decision: "deny"`, `decision_reason: "unrepresentable_argument"`, no
`POLICY_EVALUATED`, no reservation, a `denied` receipt. There is a principal, so it belongs in
the evidence log.

### 6.7 The gateway forwards the canonical action, not the bytes it received

v0.1 §2.2 requires `Control.execute` to invoke the executor with `Action.canonical_arguments`
rather than the caller's objects. The gateway's executor is "POST this to the upstream", so
the request it sends MUST be built with `params.arguments` taken from
`Action.canonical_arguments`, not copied from the received body. Every other member —
`jsonrpc`, `id`, `method`, `params.name`, `params._meta` — is carried through unchanged.

This is the whole point of canonicalization moved into the network: what a human approved, and
what was hashed, reserved and recorded, is byte-for-byte what the upstream receives. A gateway
that relayed the original bytes would be binding an approval to one document and executing
another.

The transformation is key order and whitespace only, so `Mcp-Name` and every `Mcp-Param-{Name}`
header the client sent still agrees with the body it forwards; the gateway relays them as
received, having already validated each against the canonical arguments (§6.4). Nothing needs
recomputing, and if a header ever stopped matching after canonicalization it would mean
canonicalization had changed a *value*, which v0.1 §2.3's tests exist to catch.

### 6.8 Outcome mapping

This table is the reason the gateway is specified at all. It is v0.1 §5.5 for a network hop, and
its asymmetry MUST NOT be inverted.

| What the gateway observed | Effect state | Returned to the client |
|---|---|---|
| The connection was never established — DNS failure, refused, TLS handshake failure, connect timeout | `FAILED` | `-41011` `ctrlrun.upstream_not_executed`, HTTP 502 |
| A well-formed JSON-RPC **result**, `resultType: "complete"`, no `isError` or `isError: false` | `COMMITTED` | the upstream's response, unchanged |
| A well-formed JSON-RPC **result**, `isError: true`, tool **not** marked `not_executed_on_error` | `AMBIGUOUS` | the upstream's response, unchanged |
| A well-formed JSON-RPC **result**, `isError: true`, tool marked `not_executed_on_error: true` | `FAILED` | the upstream's response, unchanged |
| `resultType: "input_required"` | §6.9 | §6.9 |
| A `resultType` the gateway does not recognize, or an absent one | `AMBIGUOUS` | the upstream's response, unchanged |
| A JSON-RPC **error** with code `-32700`, `-32600`, `-32601`, `-32602`, `-32020`, `-32021` or `-32022` | `FAILED` | the upstream's error, unchanged |
| A JSON-RPC **error** with any other code, `-32603` included | `AMBIGUOUS` | the upstream's error, unchanged |
| HTTP `401`, or HTTP `403` carrying a `WWW-Authenticate` challenge | `FAILED` | the upstream's response and challenge, unchanged |
| Write timeout, read timeout, connection reset, protocol error, TLS failure after the request was sent | `AMBIGUOUS` | `-41010` `ctrlrun.upstream_ambiguous`, HTTP 502 |
| A response body that is not valid JSON, or not a JSON-RPC message, or whose `id` does not match | `AMBIGUOUS` | `-41010`, HTTP 502 |
| An SSE response stream that closed before delivering a final response | `AMBIGUOUS` | `-41010` appended to the stream, then closed |
| Any HTTP status with no parseable JSON-RPC body — 5xx, 429, an HTML error page | `AMBIGUOUS` | `-41010`, HTTP 502 |
| The **client** disconnected mid-stream (cancellation) | `AMBIGUOUS` | nothing; the stream is gone |

Notes that carry the weight:

**Connection reuse is disabled for intercepted calls.** "The connection was never established"
is the only claim in this table that asserts non-execution, and it is only provable if no
request byte can have been written. A pooled connection that the upstream closed while idle
fails on *write*, which is indistinguishable from a request that arrived. So the gateway MUST
use a fresh connection for every intercepted `tools/call` and MUST NOT reuse one. It costs a
handshake per consequential action, and it buys the only `FAILED` in the table that comes from
the transport.

**Why those seven error codes are `FAILED`.** Each is, by the definition of JSON-RPC 2.0 or of
this MCP revision, determined from the request before the method runs: parse error, invalid
request, method not found, invalid params, and the three header/metadata validation errors the
revision requires servers to raise before processing (*"Servers **MUST** reject requests with
a `400 Bad Request` HTTP status and JSON-RPC error code `-32020` … if any validation fails"*).
`-32603 Internal error` is not in that class and never will be. This is the one judgement in
the table that rests on a peer obeying a contract rather than on something CTRLRun observed;
an upstream that returns `-32602` after doing work violates JSON-RPC 2.0, and CTRLRun will
permit a retry it should not have. It is a limit to state, not to design around; item 5 MUST
carry it into `THREAT_MODEL.md`, along with the gateway's other three — `not_executed_on_error`
and `mrtr: allow` are unverifiable operator assertions (v0.1 §5.5's asymmetry, delegated to YAML),
and a principal from a header or from `clientInfo` is not authenticated (§6.5).

**An authorization rejection is `FAILED`, and it matters more than it looks.** An expired or
insufficiently scoped token is the most common thing that goes wrong between a gateway and an
upstream, and the authorization spec puts the check before the method: *"Invalid or expired
tokens **MUST** receive a HTTP 401 response"*, and an `insufficient_scope` `403` is raised
*"When a client makes a request with an access token with insufficient scope"*. Neither
reaches the tool. Recording those `AMBIGUOUS` would mean a routine token refresh left an
effect key needing a human, which is how a guarantee turns into something people switch off.
The challenge is relayed untouched (§6.3) so the client can step up and retry, and the retry
is permitted because the record is `FAILED` (v0.1 §5.4).

**An unrecognized or absent `resultType` is `AMBIGUOUS`, deliberately against the client
rule.** The revision tells clients to treat an absent `resultType` as `"complete"`, for
compatibility with servers implementing earlier versions. The gateway refuses those servers at
the version check (§6.2), so on a response it accepts, `resultType` is required and its
absence means the response is malformed — and a malformed answer about a consequential action
is an unknown outcome, not a success.

**`isError: true` is not a failure.** The revision's own examples of a tool execution error
are *"API failures · Input validation errors · Business logic errors"* — the first of which
says nothing about whether a side effect landed. `AMBIGUOUS` is the default and
`not_executed_on_error: true` (§3.1) is where an operator asserts, per tool, that their
upstream reports errors only before acting. It is the `NotExecuted` of v0.1 §5.5 in YAML: the same
assertion, made by the person who knows, with the same consequences if they are wrong.

**The upstream's own answer is returned unchanged.** Where the gateway received a well-formed
JSON-RPC response, it relays it verbatim; rewriting a tool's result or error would corrupt the
contract between the agent and the tool. The gateway synthesizes a response only when it never
got one. So that a client is not left guessing what CTRLRun recorded, the gateway MUST add to
every response it returns for an intercepted call:

```json
"_meta": {
  "com.ctrlrun/receipt": {
    "action_id": "act_…", "receipt_id": "ctr_…",
    "effect_key": "refund:txn_1", "result": "ambiguous", "attempt": 1
  }
}
```

`com.ctrlrun/` is a legal `_meta` prefix under the revision's key-naming rules (reverse DNS,
second label neither `mcp` nor `modelcontextprotocol`), and `_meta` on a result is not
validated against a tool's `outputSchema`.

**Streaming.** When the upstream answers with `text/event-stream`, the gateway MUST relay each
SSE event as it arrives — a buffered progress notification is not a progress notification —
while parsing each to find the final JSON-RPC response, which is what the table above is
about. If the stream ends without one, the gateway appends one more event carrying `-41010`
and closes.

**Client disconnection is cancellation** under this revision: *"Closing the SSE response
stream **MUST** be treated by the server as cancellation of that request."* The upstream may
already have committed, so the effect is `AMBIGUOUS`, exactly as a timeout is. The gateway MUST
NOT record it as failed and MUST NOT cancel-and-retry.

### 6.9 Multi round-trip tool calls (MRTR)

This revision lets a server answer `tools/call` with `resultType: "input_required"`, after
which the client re-sends the same call — new JSON-RPC `id`, same `params`, plus
`inputResponses` and `requestState`. That splits one tool call across two requests, and it
interacts with both of CTRLRun's bindings: the second request carries input a human approving
the first never saw, and both requests resolve to the same effect key.

Behaviour is per tool, from `mcp.mrtr` in the policy (§3.1). Default `deny`.

**`mrtr: deny` (default)**

- A `tools/call` carrying `inputResponses` or `requestState` is refused before the policy is
  evaluated: HTTP 400, `-41009` `ctrlrun.mrtr_not_permitted`, recorded in the shape of §6.6's
  unrepresentable-argument refusal (`decision_reason: "mrtr_not_permitted"`), no reservation.
- An `input_required` **result** is recorded `AMBIGUOUS`. The upstream received the request,
  will not complete it, and has said nothing about what it did; the retry that would complete
  it is one this gateway refuses to forward. A human resolves the key.

So an eliciting tool blocks one effect key the first time it is called, loudly, once. The
remedy is one line of policy — either `mrtr: allow`, or `decision: deny` for that tool, which
refuses before execution and never touches an effect key.

The cost lands only on tools that have one. A tool with no `effect:` in policy reserves
nothing (§3.2), so an `input_required` result from it produces an `ambiguous` receipt and
blocks nothing at all. An eliciting *read* costs a log line; an eliciting *write* costs a
`ctrlrun resolve`. That is the right way round.

**`mrtr: allow`**

- The retry is forwarded, and `inputResponses` and `requestState` are included in the action's
  arguments under exactly those names. The second leg is therefore a different action with a
  different hash, and a human approving it approves the elicited input too. If they were
  excluded, one approval would authorize any answer to the elicitation, which is v0.1 §4.2 A1's
  hole reopened.
- The fold is for hashing, policy and evidence only. The **forwarded** request puts them back
  where MCP expects them — `params.inputResponses` and `params.requestState` beside
  `params.arguments`, not inside it — with every value taken from `Action.canonical_arguments`
  as §6.7 requires. What was hashed and what is sent stay the same values in a different
  shape; if that shape were wrong the upstream would reject the retry, which is the safe
  direction but still a bug.
- If `params.arguments` already contains a key named `inputResponses` or `requestState`, the
  call is refused (`reserved_argument_name`). Two candidate values for one name is the
  fail-closed case, as it is for `{resource}` in v0.1 §5.1.
- An `input_required` result is recorded `FAILED`, so the retry reserves the same effect key as
  attempt 2 (v0.1 §5.4).

That last mapping is an assertion, and `mrtr: allow` is where an operator makes it. It has more
behind it than most: this revision *requires* the client to re-send the original params, so a
server with an irreversible side effect before an `input_required` is already unsafe under
MCP's own contract. But "already unsafe" is not "did nothing", CTRLRun does not get to assume
a well-behaved upstream, and so the declaration is required and its default is `deny`.

### 6.10 Decision, approval, and what the client sees

| CTRLRun outcome | JSON-RPC code | HTTP | `data` |
|---|---|---|---|
| `ALLOW` | — | as upstream | — |
| `DENY` | `-41001` `ctrlrun.denied` | 403 | `reason`, `action_id` |
| `APPROVE`, none granted | `-41002` `ctrlrun.approval_required` | 403 | `request_id`, `expires_at`, `action_hash` |
| approval denied by a human | `-41003` `ctrlrun.approval_denied` | 403 | `request_id` |
| `DuplicateEffect` | `-41004` `ctrlrun.duplicate_effect` | 409 | `effect_key`, `state` |
| `AmbiguousEffect` | `-41005` `ctrlrun.ambiguous_effect` | 409 | `effect_key` |
| `ApprovalMismatch` | `-41006` `ctrlrun.blocked` | 409 | `reason` |
| no principal | `-41007` `ctrlrun.no_principal` | 403 | — |
| unrepresentable argument | `-41008` | 400 | `pointer` |
| MRTR refused | `-41009` | 400 | `tool` |
| upstream outcome unknown | `-41010` | 502 | `effect_key`, `action_id` |
| upstream proven not executed | `-41011` | 502 | `effect_key`, `action_id` |

`data.error` carries the `ctrlrun.*` token; `message` is a human sentence. Codes are outside
the JSON-RPC reserved range because the revision reserves `-32020…-32099` for the MCP
specification and instructs that *"New error codes for purposes not defined by this
specification **SHOULD** be allocated outside the JSON-RPC reserved range (`-32768` to
`-32000`)"*.

**Why a JSON-RPC error and not `isError: true`.** A tool result with `isError: true` is fed to
the model to let it self-correct, and it is indistinguishable from the tool's own failure. A
refusal by CTRLRun is not an outcome of the tool; it is the statement that the tool did not
run. A JSON-RPC error says that unambiguously, and keeps a policy denial out of a channel
whose contents reach the model as text.

**Never HTTP 401.** 401 is the transport's OAuth challenge, and a client receiving one starts
an authorization flow that cannot fix a policy denial. Refusals are 403 or 409.

**Approval flow.** With `--wait-approvals`, the gateway blocks on the provider up to
`--approval-timeout` (default 900 s) and then returns `-41002`. Without it, `-41002` is
immediate. A client comes back by **re-sending the identical `tools/call`**: the gateway looks
for the newest granted, unexpired approval whose `action_hash` equals the action's
(`StateStore.find_granted_approval`, §11) and executes inside `with_approval` for it.

Matching on the hash rather than on a presented request id is safe for the reason A1 exists:
the hash covers the principal, the arguments, the resource and the environment, so an approval
can only match an identical action from the same principal — which is the same action — and it
is still single-use and still consumed atomically with the reservation (v0.1 §4.2 A4). Losing the
race to another process yields `ApprovalMismatch(reason="consumed")` and `-41006`, which is
correct.

**"No" is an answer, and re-asking is not free.** Before creating a request, the gateway MUST
look for an unexpired `denied` request for the same `action_hash`; if one exists it MUST
return `-41003` and MUST NOT create another. Without that rule, an agent that dislikes a
denial resends the call and gets a fresh notification to a human, and the loop is not a denial
of service so much as a way to wear an approver down until one of them clicks the wrong
button. A denial holds for the life of the request that carried it; after it expires, asking
again is legitimate.

`ctrlrun approve` and `ctrlrun deny` work on gateway requests unchanged: same store.

### 6.11 What the gateway refuses, in one list

| Condition | HTTP | Code |
|---|---|---|
| `Origin` present and not allowlisted | 403 | — (empty body permitted) |
| `GET` / `DELETE` on the MCP endpoint | 405 | — |
| Body larger than `--max-body-bytes` | 413 | — |
| Body not valid JSON | 400 | `-32700` |
| Body not a JSON-RPC 2.0 message, or a JSON array (batch) | 400 | `-32600` |
| `MCP-Protocol-Version` absent, or earlier than `2026-07-28` | 400 | `-32022` |
| `Mcp-Method` or `Mcp-Name` absent, or disagreeing with the body | 400 | `-32020` |
| An `Mcp-Param-{Name}` disagreeing with the body | 400 | `-32020` |
| No principal derivable | 403 | `-41007` |
| A `float` anywhere in `params.arguments` | 400 | `-41008` |
| `inputResponses`/`requestState` under `mrtr: deny` | 400 | `-41009` |

Everything above the principal row is refused before an Action exists and therefore leaves no
receipt and no events, only a log line. Everything from the principal row down is described in
§6.5, §6.6 and §6.9.

### 6.12 What the gateway is not

It is one process with a thread per connection, fronting one upstream. It is not a load
balancer, not a reverse proxy for a fleet, and not an authorization server. Reservation is
still single-host (v0.1 §5.3 E1), so two gateways in front of one upstream do **not** share
reservations unless they share a state file on one machine. Put it behind a real proxy for
TLS termination, rate limiting and authentication; CTRLRun decides, and does not aspire to
terminate.

The extra's only dependency is an HTTP client with a connect/write/read exception taxonomy
precise enough to feed §6.8's first row; the listening side is stdlib
`http.server.ThreadingHTTPServer`. A gateway that could not distinguish "never connected" from
"no reply" would have to call everything `AMBIGUOUS`, which is safe and useless.

---

## 7. Webhook approval provider

```python
WebhookApprovalProvider(store, url, secret, *, timeout=..., retries=2,
                        replay_window=timedelta(seconds=300), respond_to=None)
```

Outbound in core over stdlib `urllib.request`; the inbound endpoint is served by the gateway.

### 7.1 Outbound

On `APPROVAL_REQUESTED`, one POST to `url` with `Content-Type: application/json` and body

```json
{"schema": "ctrlrun.approval_request/v1", "request_id": "apr_…", "action_hash": "sha256:…",
 "action": { … }, "created_at": "…", "expires_at": "…", "respond_to": "https://…/ctrlrun/approvals/apr_…"}
```

signed with `CTRLRun-Signature: t=<unix seconds>,v1=<hex>` where the MAC is
`HMAC-SHA256(secret, f"{t}.{body}")` over the **exact bytes sent**. `respond_to` is present
only when `--public-url` was given.

The provider MUST NOT follow redirects: a redirect to another host would deliver the signed
payload, and the action, somewhere the operator did not name. It MUST refuse an `http://` URL
unless it is loopback and `--allow-insecure-webhook` was given.

`retries` bounded attempts with backoff, then give up: the request stays `pending`,
`ApprovalRequired` is raised as usual with a real `request_id` that `ctrlrun approve` can still
answer, and the failure is logged as a warning. The action is refused either way — an
undelivered request is not an approval.

### 7.2 Inbound

The inbound endpoint is the gateway's. A decorator-based deployment can use the outbound half
on its own — the notification carries the `request_id` and `ctrlrun approve` answers it — and
`respond_to` is then absent, which is how the receiving system knows there is nowhere to POST
back to and that the answer has to come from the CLI. An inbound endpoint without a gateway is
not in v0.2: it would be a second server, and the rule in §1 is that there is one.

`POST /ctrlrun/approvals/<request_id>`, served by the gateway, body

```json
{"request_id": "apr_…", "action_hash": "sha256:…", "decision": "grant" | "deny", "approver": "slack:U123"}
```

with the same signature header. Every one of these MUST hold or the request is rejected with
HTTP 400, no state change, and a warning:

- the signature verifies, compared with `hmac.compare_digest`;
- `|now − t| ≤ replay_window` (default 300 s);
- the `request_id` in the path equals the one in the body;
- the `action_hash` in the body equals the stored request's;
- the stored request exists and is `pending`.

Then `grant_approval` / `deny_approval` as v0.1 §4 defines them, with `approver` recorded verbatim.

**No nonce store is needed.** Inside the replay window, a replayed grant is idempotent — the
record is already `granted` — and outside it the timestamp check rejects. A replay arriving
after consumption MUST NOT resurrect the approval: `grant_approval` on a record that is
`consumed`, `denied` or `expired` MUST refuse. Single use (v0.1 §4.2 A2) is enforced at
consumption, not at grant, and this endpoint does not get to weaken it.

### 7.3 The secret

Read from `CTRLRUN_WEBHOOK_SECRET` or `--webhook-secret-file`, never from a command-line
argument, which every process listing on the host can read. Fewer than 32 bytes →
`InvalidArgument`. Set but empty → `InvalidArgument`, as `CTRLRUN_CONFIG` fails in v0.1 §3.4.

This changes what an approval id is. `CHANGELOG` for 0.1.0 noted that `apr_` ids become bearer
tokens with the webhook provider; they do not. The bearer is the shared secret, and the id
identifies which request the signed message answers. An id alone grants nothing.

---

## 8. OpenTelemetry sink

`OTelEventSink()`, in `ctrlrun[otel]`, lazily imported. An `EventSink` (§4) and nothing more:
it never decides, never blocks, and never raises.

**One span per action**, opened on `ACTION_PROPOSED` and ended on the action's terminal event
(`ACTION_DENIED`, `EXECUTION_COMMITTED`, `EXECUTION_FAILED`, `EXECUTION_AMBIGUOUS`, or the
refusal that terminates a blocked attempt). Span name is the action name. Every event becomes
a span event named by its `EventType`, with `data` flattened to `ctrlrun.*` attributes.

Attributes: `ctrlrun.action_id`, `ctrlrun.action.name`, `ctrlrun.action_hash`,
`ctrlrun.principal.agent`, `ctrlrun.principal.user`, `ctrlrun.environment`,
`ctrlrun.resource`, `ctrlrun.decision`, `ctrlrun.decision_reason`, `ctrlrun.effect_key`,
`ctrlrun.attempt`, `ctrlrun.result`, `ctrlrun.approval_id`, `ctrlrun.approver`,
`ctrlrun.receipt_id`.

**Argument values are not attributes** unless `--otel-arguments` is given. Arguments carry
customer identifiers and amounts, and a trace backend is not the receipt store. The default is
the fail-closed one.

Span status: `OK` for `committed`; `ERROR` for `failed` and `ambiguous`; **unset** for
`denied` and `blocked`. A refusal is the system working as designed, and marking it an error
teaches an on-call rotation to ignore the signal that matters.

Never blocks: the sink hands spans to whatever span processor the application configured and
never flushes synchronously. With no configured tracer provider, the API's no-op provider makes
it free. A process that dies mid-action leaves that span unended — stated, not solved.

**Trace context.** The gateway MUST read `traceparent` (and `tracestate`, `baggage`) from
`params._meta` when present and use it as the parent context, per this revision's reservation
of those keys for W3C Trace Context. Those keys stay transport metadata: they are not part of
the action (§6.6).

A retry is a new action and therefore a new span; the two are correlated by
`ctrlrun.effect_key`, which is the identity that actually spans attempts (v0.1 §5).

---

## 9. ACS: design note only

Item 7 writes `docs/ACS-NOTE.md`. **No code, no adapter, no claim.**

The Agent Control Standard is an open specification for runtime agent governance, published at
v0.1.0 on 27 May 2026 as a project of the OWASP GenAI Security Project
(<https://agentcontrolstandard.org/>). It defines validation checkpoints across an agent's
lifecycle, expresses policy as YAML, and extends OpenTelemetry with agent-specific semantic
conventions while mapping security events to OCSF.

The note states, and does no more than state:

- where CTRLRun would sit in that model — the tool-call checkpoint, and only that one;
- what an adapter would carry across, and what has no counterpart on the ACS side: effect
  identity, atomic reservation, and `AMBIGUOUS` as a terminal state are CTRLRun's, not the
  standard's, and an adapter that flattened them would export the wrong thing;
- what CTRLRun's OTel attributes (§8) would have to be renamed to, to align with ACS's
  conventions, and the cost of doing that to receipts already written.

`ROADMAP.md`'s standards rule governs: integrate first, map second, never claim compliance.
The words "ACS-compatible" MUST NOT appear in the README, in docstrings, or in CLI output in
v0.2. They are earned by an adapter with tests, which is not in this release.

---

## 10. Acceptance tests

Each MUST exist as a pytest test carrying the given ID in its name. All MUST pass for v0.2, in
addition to T1–T12.

### T13 — Reconciliation unblocks a retry
Given an `AMBIGUOUS` record for `refund:txn_1` and a `reconcile` returning `"not_executed"`.
When a new attempt runs, the record moves to `FAILED`, the attempt executes, the record is
`COMMITTED` with `attempt == 2`, and the events are `RECONCILIATION_STARTED`,
`RECONCILIATION_RESOLVED(outcome="not_executed")`, `EFFECT_RESOLVED(resolved_by="reconcile")`.

### T14 — Reconciliation answering `committed` refuses the retry
Same setup, `reconcile` returns `"committed"`. The record moves to `COMMITTED`, the new attempt
raises `DuplicateEffect(state="committed")` rather than `AmbiguousEffect`, and the fake remote
call count is unchanged.

### T15 — A reconcile hook that fails changes nothing, and runs once
Three cases, one test file. A hook that raises, and a hook returning `"maybe"`, each leave the
record `AMBIGUOUS`, raise `AmbiguousEffect`, and append
`RECONCILIATION_RESOLVED(outcome="unknown")` with `data.reason`. And with
`reconcile_eagerly=True` on a path where both trigger points apply, the hook is called exactly
once per `Control.execute`.

### T16 — Policy templates, and the decorator wins
A `v2` policy with `effect:` and `resource:` produces the same effect key and `action_hash` for
a gateway-built action as `@protect(effect=…, resource=…)` does for the equivalent call. Where
both exist and differ, the decorator's value is in force and a warning naming both is logged
once. `effect:` in a `v1` document is a `PolicyError`; a malformed template in a `v2` document
is a `PolicyError` at load, not an `EffectKeyError` at runtime.

### T17 — A failing sink cannot affect an action
A sink whose `on_event` and `on_receipt` always raise is registered. The action still commits,
the receipt is written, the effect record is `COMMITTED`, the caller sees no exception, and a
warning naming the sink's class is logged. A second, working sink registered after it still
receives every record.

### T18 — `ctrlrun inspect`
After T1, `ctrlrun inspect <action_id> --json` emits `ctrlrun.inspection/v1` with the receipt,
the effect record, the approval records for that hash, and the events in `event_id` order.
Every enum renders by value. An unknown `action_id` exits non-zero with empty stdout.

### T19 — The gateway forwards the canonical action
A `tools/call` whose `params.arguments` has keys in non-sorted order with insignificant
whitespace reaches a fake upstream with `arguments` in canonical form and identical values, and
with `Mcp-Name` and `Mcp-Param-*` recomputed to match. The action hash recorded on the receipt
equals the hash of what the upstream received.

### T20 — The gateway refuses what it cannot decide about
Parameterized over §6.11: an unparseable body, a JSON array body, a missing
`MCP-Protocol-Version`, `MCP-Protocol-Version: 2025-11-25`, a missing `Mcp-Method`, an
`Mcp-Name` disagreeing with `params.name`, an `Mcp-Param-*` disagreeing with the body, a body
over the size limit, an unallowlisted `Origin`, and a `GET`. Each produces the specified status
and code, and the fake upstream's request count stays zero.

### T21 — No principal, no action
With `--principal-header X-Agent` configured and the header absent, the call is refused with
`-41007`, the upstream is not called, **no receipt and no events are written**, and a warning
naming the action is logged.

### T22 — A float argument is refused, not coerced
`tools/call` with `{"amount": 20.0}` is refused with `-41008` naming the pointer; the upstream
is not called; a `denied` receipt with `decision_reason: "unrepresentable_argument"` and no
`POLICY_EVALUATED` event is written.

### T23 — A lost response through the gateway blocks the retry (the signature test, over HTTP)
A fake upstream that commits and then closes the connection without a response. The effect is
`AMBIGUOUS`, the client receives `-41010`, and the response's
`_meta["com.ctrlrun/receipt"].result` is `"ambiguous"`. The identical `tools/call` sent again
receives `-41005`, the upstream is called exactly once, and a `blocked` receipt is written.

### T24 — Upstream outcomes map as specified
Parameterized over §6.8: connection refused → `FAILED`; `isError: true` → `AMBIGUOUS`;
`isError: true` with `not_executed_on_error: true` → `FAILED`; `-32602` → `FAILED`; `-32603` →
`AMBIGUOUS`; a `401` with a `WWW-Authenticate` challenge → `FAILED` with the challenge relayed
verbatim, after which a retry is permitted; read timeout → `AMBIGUOUS`; HTML 502 →
`AMBIGUOUS`; an unrecognized `resultType`, and an absent one → `AMBIGUOUS`; an SSE stream
closing before its final response → `AMBIGUOUS`. In every non-synthesized case the upstream's
own response reaches the client unchanged apart from `_meta["com.ctrlrun/receipt"]`.

### T25 — Approval over the gateway
A tool whose policy says `approve`. The first call returns `-41002` with a `request_id` and
writes no receipt. `ctrlrun approve <id>` runs against the same store. The identical call then
executes, consumes that approval, and commits. A third identical call returns `-41006`
(`consumed`) or `-41004`, and the upstream is called exactly once. Separately: after
`ctrlrun deny <id>`, resending the identical call returns `-41003` and creates **no** second
approval request, until the denied one expires.

### T26 — MRTR is refused by default and bounded when allowed
Under the default, a `tools/call` carrying `inputResponses` is refused with `-41009` and the
upstream is not called, and an `input_required` result leaves the effect `AMBIGUOUS`. Under
`mrtr: allow`, the retry is forwarded, `inputResponses` is part of the action's arguments and
therefore of its hash, an `input_required` result leaves the effect `FAILED`, and the retry
reserves the same key as attempt 2. A tool argument literally named `inputResponses` is refused
under both settings.

### T27 — Webhook signature, both directions
The outbound POST carries `CTRLRun-Signature` verifying against the exact bytes sent. Inbound:
a valid grant moves the request to `granted`; a wrong signature, a timestamp outside the replay
window, a path/body `request_id` disagreement, and an `action_hash` disagreement each return
400 and leave the record `pending`. A replayed valid grant is idempotent, and a grant replayed
after consumption does not resurrect the approval.

### T28 — An undelivered webhook does not approve anything
The webhook endpoint refuses every attempt. The provider gives up after its bounded retries,
`ApprovalRequired` is raised with a `request_id` that `ctrlrun approve` can still answer, the
request is `pending`, no receipt is written, and a warning is logged.

### T29 — The OTel sink exports and never blocks
With an in-memory span exporter, one action produces one span from `ACTION_PROPOSED` to its
terminal event, carrying every attribute in §8 and one span event per event. Argument values do
not appear unless the option is set. An exporter that raises does not affect the action (T17's
rule applied to this sink). Span status is `ERROR` for `ambiguous` and unset for `denied`.

### T30 — Core installs no extras, and a missing extra says so
A **subprocess** running `import ctrlrun` reports no module from an extra in `sys.modules` —
in-process it would pass or fail on what pytest happened to import first. With the extra
absent, `ctrlrun gateway` and `OTelEventSink()` raise `MissingDependency` whose message
contains `pip install 'ctrlrun[gateway]'` / `'ctrlrun[otel]'`, never `ModuleNotFoundError`.

---

## 11. Public API and CLI additions (frozen for v0.2)

```python
# ctrlrun/__init__.py — added
from .effect import ReconcileOutcome
from .receipt import EventSink, JSONLEventSink
from .approval import WebhookApprovalProvider
from .errors import MissingDependency
# lazily importable, not re-exported at package import:
#   ctrlrun.otel.OTelEventSink        (ctrlrun[otel])
#   ctrlrun.gateway.serve             (ctrlrun[gateway])
```

```python
ReconcileOutcome = Literal["committed", "not_executed", "unknown"]

protect(..., reconcile: Callable[[str], ReconcileOutcome] | None = None,
             reconcile_eagerly: bool = False)
Control(..., sinks: Sequence[EventSink] = ())
Control.execute(..., reconcile: Callable[[str], ReconcileOutcome] | None = None,
                     reconcile_eagerly: bool = False)

Policy.effect_template(action_name: str) -> str | None
Policy.resource_template(action_name: str) -> str | None
Policy.mcp_options(action_name: str) -> McpOptions

@dataclass(frozen=True)
class McpOptions:
    not_executed_on_error: bool = False
    mrtr: Literal["deny", "allow"] = "deny"

StateStore.find_granted_approval(action_hash: str) -> Approval | None
StateStore.find_denied_request(action_hash: str) -> ApprovalRequest | None
StateStore.resolve_effect(effect_key, state, *, resolved_by: str) -> None
```

**Removed:** `SQLiteStateStore.journal`. The store no longer writes JSONL; `Control` does,
through `JSONLEventSink` (§4.3). The files it writes, and where, are unchanged.

Two event types join v0.1 §6.2: `RECONCILIATION_STARTED`, `RECONCILIATION_RESOLVED`.
`EFFECT_RESOLVED` gains `data.resolved_by`. The receipt schema is **unchanged**:
`ctrlrun.receipt/v1` still describes every receipt v0.2 writes.

New JSON-RPC error codes, frozen: `-41001` denied · `-41002` approval_required · `-41003`
approval_denied · `-41004` duplicate_effect · `-41005` ambiguous_effect · `-41006` blocked ·
`-41007` no_principal · `-41008` unrepresentable_argument · `-41009` mrtr_not_permitted ·
`-41010` upstream_ambiguous · `-41011` upstream_not_executed.

CLI:

```
ctrlrun inspect <action_id> [--json]

ctrlrun gateway --upstream URL --alias NAME
                [--listen HOST:PORT]                 default 127.0.0.1:8900
                [--path PATH]                        default /mcp
                (--principal AGENT | --principal-header NAME | --principal-from-client-info)
                [--user-header NAME] [--environment NAME]
                [--wait-approvals] [--approval-timeout SECONDS]
                [--upstream-timeout SECONDS] [--max-body-bytes N]
                [--allow-origin ORIGIN]... [--allow-remote]
                [--public-url URL] [--allow-insecure-webhook]
                [--otel] [--otel-arguments]
```

Extras in `pyproject.toml`: `gateway` (an HTTP client) and `otel` (the OpenTelemetry API, SDK
and OTLP/HTTP exporter). Neither is in `dependencies`.

---

## 12. Explicitly out of scope for v0.2

Everything in v0.1 §9 that v0.2 does not deliver, and specifically: legacy MCP revisions
(`2025-11-25` and earlier, the `initialize` handshake, `Mcp-Session-Id`, resumable streams);
more than one upstream per gateway; multi-host reservation; an ACS adapter or any ACS claim;
authenticating the principal or the approver; signed receipts; MCP `tasks`, `apps` or any other
extension; `ctrlrun verify`; framework adapters; Postgres; anything in `VISION.md`.
