# CTRLRun — the operator MCP server

This is a **delta over [`SPEC-v0.1.md`](SPEC-v0.1.md), [`SPEC-v0.2.md`](SPEC-v0.2.md),
[`SPEC-v0.3.md`](SPEC-v0.3.md), [`SPEC-v0.4.md`](SPEC-v0.4.md), [`SPEC-v0.5.md`](SPEC-v0.5.md)
and [`SPEC-v0.6.md`](SPEC-v0.6.md)**. Everything in all six still holds; this document states
only what `ctrlrun mcp-operator` adds. A reference to an earlier contract is written `v0.1 §4.2`,
`v0.2 §6.4`, `v0.3 §3.2` or `v0.6 §7.1`; a bare `§4` is a section of this document. Section
numbers exist in all seven, so the prefix is not decoration — an unprefixed reference to an
earlier spec is a defect.

Tests are derived from §8. Public names added here are frozen in §9. Anything not in this
document or in v0.1–v0.6 is out of scope.

Words: MUST / MUST NOT / SHOULD are used in the RFC 2119 sense.

**It is not a kernel milestone.** What it depends on — the approval record, the effect record,
the identity provider — has been frozen since v0.3, and a server that answers approvals is not a
change to what an approval *is*. So it gates no kernel release and none gates it, and it lands in
whichever release comes next.

Unlike an adapter it carries **no version line of its own**, and the difference is worth naming
because `v0.5 §6.2` would otherwise seem to apply: an adapter is a separate distribution that
breaks when *its framework* makes a breaking release, which is not a kernel event. This is a
subcommand of the `ctrlrun` distribution, with no second upstream, so `adapters-langgraph-1.0`
has no analogue here and inventing one would be a version number nothing could move.

## 1. Scope

One sentence: **`ctrlrun mcp-operator` is an MCP server that exposes the operator's own commands
as tools, so that the human who has to answer an approval can answer it from the assistant they
are already talking to.**

It is a **transport for the answer**, and nothing else. `ctrlrun approve` answers a request at a
terminal, the webhook endpoint (`v0.2 §7.2`) answers one over HTTP from Slack, an adapter's
`InterruptApprovalProvider` answers one through a framework's interrupt (`v0.5 §2.4`), and this
answers one from an assistant. All four end in the same two store calls — `grant_approval` and
`deny_approval` — against the same record, with the same hash binding, the same single use and
the same expiry. That is what keeps it from being **a second approval path** in `v0.5 §1`'s
sense: a second path would be a second *decision*, with its own record and its own rules. There
is exactly one approval record per request and exactly one place its state changes.

### 1.1 What it is not

- **Not a gateway.** It fronts nothing, relays nothing, and has no upstream. `ctrlrun gateway`
  is in the agent's path; this is in the *approver's* path, and the two never meet.
- **Not a way to speak to the agent.** No tool here proposes, executes, resumes, reserves,
  commits or delegates. The server never calls `Control.execute`, `Control.resume`,
  `Control.delegate` or `Control.revoke`, and there is no tool that makes an action happen.
  An assistant that can approve is not thereby an assistant that can act.
- **Not a management plane.** No dashboard, no configuration, no policy editing, no store
  administration, no user management. `v0.2 §6.12` said the gateway is a policy checkpoint and
  not a management plane; this is a *reader and an answerer*, and less than that again.
- **Not an auto-approver.** There is no flag, environment variable or tool argument that grants
  without a human answering. `v0.4 §3.9` forbids verify a flag that relaxes a check and
  `v0.5 §3.8` extends it to adapters; the same rule applies here, and §3.7 states it.
- **Not a second composer.** `Control` is the only module that composes the others
  (`ARCHITECTURE §6`). The server holds a `Control`, reads through `Control.store`, and writes
  through the same `StateStore` methods `ctrlrun approve`, `ctrlrun deny` and `ctrlrun resolve`
  call. It composes nothing.

### 1.2 Why it exists

An approval that is easy to see and hard to answer gets answered by whoever has a terminal open,
which is not the person the policy meant. The gap this closes is not technical: it is that the
CLI requires a checkout, a shell and a store path, and the webhook requires somebody to have
built a Slack app. An MCP client is a thing an approver already has running.

## 2. Transport

**Streamable HTTP, POST only, one endpoint.** `v0.2 §6.2`'s accepted revision set applies
unchanged: `2026-07-28`, and `2025-03-26` through `2025-11-25`. The deprecated two-endpoint
HTTP+SSE transport of `2024-11-05` is not accepted.

This server is an **origin server**, not an intermediary, so two of `v0.2 §6`'s rules apply
differently and both differences are stated rather than left to be inferred:

| `v0.2 §6` rule | Here |
|---|---|
| Validate every mirrored header against the body (§6.4) | **Applies in full.** The body is believed and the headers are checked, by the same `ctrlrun.gateway.mcp.parse_request`. A disagreement is HTTP 400 and `-32020`, exactly as at the gateway |
| Relay everything that is not `tools/call` (§6.3) | **Does not apply.** There is nowhere to relay to. `initialize`, `notifications/initialized`, `tools/list` and `tools/call` are answered; every other method is `-32601 Method not found` |
| `Origin` is validated before anything else (§6.1) | **Applies in full**, with the same empty-by-default `--allow-origin` allowlist |
| Bodies are bounded by `--max-body-bytes` (§6.4) | **Applies in full**, same 1 MiB default, and the bound is on the *allocation* as well as on the decision: a `Content-Length` that is negative or unparseable is refused before anything is read |
| A repeated identity header is a refusal, never a collapse (`v0.3 §3.1`) | **Applies in full**, and the check is in `OperatorServer.handle` — not only in the stdlib handler — because `handle` is the surface §9.1 freezes and the one a deployment embeds behind its own proxy |
| `Mcp-Session-Id` | Never minted, never interpreted. This server holds no session |

### 2.1 Loopback only, and no `--allow-remote`

`--listen` defaults to `127.0.0.1:8901`. A host outside `{127.0.0.1, localhost, ::1, [::1]}` is
`InvalidArgument` at startup, and **there is no flag that permits one**. Both IPv6 spellings are
accepted because `[::1]:8901` is what an operator types; the socket family is chosen from the
host, since `ThreadingHTTPServer` inherits `AF_INET` and a review found `::1` accepted by the
config and then unable to bind at all. T183 binds every host in the set rather than asserting
that the string was stored.

This is the single place the operator server is stricter than the gateway, and the reason is
§4.1: its read tools answer without a credential. A process whose reads are unauthenticated must
not be the process that opens a port to a network.

A deployment that needs it reachable from elsewhere puts a proxy in front of it, on the same
host, terminating authentication there — which is what `--principal-header` already requires of
anyone using it (`v0.3 §3.3`), so the arrangement is not an extra burden invented here. What
this refusal buys is that the burden cannot be *skipped*: with `--allow-remote` the whole of
`stats`, `receipts` and `inspect_action` — argument values, resources, principals, claim values
— would be one unauthenticated GET away from anybody who could route to the port, and the flag
that did it would read like the gateway's.

Loopback is not a trust boundary against other processes on the same host, and the startup block
says so (§6). It is the same boundary the store file already has.

### 2.2 The default port is not the gateway's

`8901`, not `8900`. Both processes are plausibly running on one host against one store — that is
the whole deployment this exists for — and two servers whose default ports collide produce a
bind error at the worst moment, or worse, a client pointed at the wrong one.

## 3. Identity

### 3.1 The provider is the operator's, and there are two of them

Exactly one of `--principal-header` or `--identity-jwt` MUST be given. There is no default.

**`--principal` is refused**, and this is the second place the operator server is stricter than
the gateway. `StaticIdentityProvider` answers with the same `Principal` for every request
(`v0.3 §3.3`), so every approval it produced would carry the same `approver` string whoever
called — and an approval whose approver cannot distinguish one human from another is an approval
with no attribution. §5.4 makes attribution a MUST; a provider that cannot satisfy it is refused
at startup rather than discovered in a receipt.

`--principal-header` and `--identity-jwt` mean exactly what they mean at the gateway
(`v0.3 §8.2`), construct exactly the same providers, and carry exactly the same warnings. A
header is worth what the proxy that sets it is worth.

### 3.2 The credential must name a human

A write tool's principal MUST have a non-`None` `user`. An `agent` with no `user` is a machine
credential, and a machine approving an action is the auto-approve §1.1 refuses, reached by
configuration instead of by a flag.

So the configuration is checked at startup, where it can still be fixed:

- `--principal-header NAME` **requires** `--user-header NAME`;
- `--identity-jwt` **requires** `--identity-jwt-user-claim`.

Either missing is `InvalidArgument` naming the missing flag, because a server whose write tools
could never succeed is a server that will be discovered to be broken by an approver at the
moment they are trying to stop something.

The startup check does not make the request-time check redundant, and the request-time check is
not documentation: a provider configured with `--user-header` still returns `user=None` for a
request that did not carry the header (`v0.3 §3.3` — an absent header contributes `user=None`),
and a JWT whose `--identity-jwt-user-claim` is absent from a particular token does the same. Both
reach §4.2's refusal, and T185 asserts it with the configuration correct.

### 3.3 Resolution, decline, raise, expiry

The server calls the provider directly with an `IdentityContext` whose `headers` are the
request's, lowercased — the same call the gateway makes (`v0.3 §8.2`), for the same reason: a
credential arrives in a header and `Control.resolve_principal` reads no headers.

`IdentityContext.action` is the **tool name, prefixed**: `mcp-operator.approve`,
`mcp-operator.deny`, `mcp-operator.resolve`. A provider is told what it is resolving a principal
*for*, and this server proposes no `Action`, so there is no action name to borrow. The prefix
exists so that a provider shared with a gateway cannot confuse the two: `mcp.<alias>.<tool>` is
an action a policy can name, and `mcp-operator.<tool>` is not and never will be (§9.3).

Then, in this order:

| What happened | Result |
|---|---|
| The provider raised `IdentityError` | Refused, `-41007`, message *"the credential offered was rejected"* — nothing about why |
| The provider raised anything else | Logged with the provider's type and the exception's, then refused identically. A `BaseException` that is not an `Exception` propagates (`v0.1 §5.5`) |
| The provider returned `None` | Refused, `-41007`. A decline is a refusal here, with no `context()` to fall back to and nothing that may be backfilled |
| It returned a `Principal` with `expires_at` set and now past it | Refused, `-41014` `ctrlrun.principal_expired`. `v0.3 §2.3`'s check, applied at the one entry point that never reaches `Control.execute` |
| It returned a `Principal` with `user is None` | Refused, `-41013` `ctrlrun.not_a_human` (§3.2) |
| Otherwise | The write proceeds, attributed to it (§5.4) |

**No receipt and no events for any row above.** `v0.3 §3.2`'s last paragraph is the rule: a
refusal at the identity gate happens before an Action exists, so there is nothing to attribute a
receipt to. It would be *possible* here to attribute one — a refused `approve` names a request,
which names an action — and that alternative is rejected deliberately: it would let an
unauthenticated caller append to the evidence log by calling a tool it cannot use. Every refusal
is a warning on the `ctrlrun.mcp_operator` logger naming the tool, the request id and the
reason, which is where an operator looks for exactly this.

**The wire says less than the log, and each refusal is written once.** A rejected caller is told
that its credential was rejected and nothing about why, which is the correct amount to tell an
unauthenticated caller; the operator's log is told which provider rejected it, or when the
credential expired, or that it named an agent and no person. Both strings come from **one** raise
site, so the two cannot drift — and the line is written by the handler and not by the check, so a
refusal cannot be logged twice. T190 asserts the count, because the first real transcript showed
every identity refusal logged twice.

`principal_expired` gets its own code rather than reusing `-41007` because `v0.3 §4.3.1` requires
each refusal reason to be distinguishable in evidence: "your credential has expired" and "you
presented none" are different problems with different fixes, and a client that could not tell
them apart would tell its user to log in when the answer is to refresh.

## 4. The tools

### 4.1 Read tools answer without a credential

`list_pending_approvals`, `inspect_action`, `receipts`, `effects` and `stats` require no
principal. They write nothing, decide nothing and change nothing; the identity provider is not
consulted for them at all.

This is a real widening over the CLI, whose equivalent commands are protected by whatever
protects the store file, and it is bought back by §2.1 rather than waved away: the server binds
loopback and cannot be told otherwise. The two sentences belong together and neither survives
alone — a `--allow-remote` added later without also authenticating reads would publish the
evidence log, and T183 exists so that adding one fails a test.

A read tool MUST NOT consult the identity provider even when the request carries a credential.
A provider that ran on every request would make an expired credential turn `receipts` into a
refusal, which is a fail-*open* problem inverted into a fail-annoying one, and it would make the
cost of a JWKS fetch (`v0.3 §3.4`) a cost of reading.

### 4.2 Write tools refuse without one

`approve`, `deny` and `resolve` resolve identity per §3.3 **before** they touch the store, and
refuse on every row of that table. **A write this server refuses MUST leave the store
byte-identical**: no status transition, no event, no receipt. That covers every refusal in §3.3
and §7 that this server produces itself.

**A refusal the *store* produces is a different thing, and there is exactly one that writes.**
Answering a request whose `expires_at` has passed moves it from `pending` to `expired` and
commits, then refuses (`v0.1 §4.1`, `check_answerable`): *a lapsed approval is evidence, keep it,
then refuse*. That is the kernel's rule and this server does not get to have a different one —
`ctrlrun approve` and the webhook endpoint reach the same transition through the same call. It is
carved out here rather than left implicit because an earlier draft of this section asserted
byte-identity over *every* refusal, which is false, and a reader would have concluded something
untrue about the evidence log. T190 asserts the delta explicitly rather than asserting nothing
about it. No other store refusal writes: an unknown, granted, denied or consumed request, and an
effect that is not `AMBIGUOUS`, all refuse without moving anything.

### 4.3 Authority is not evaluated, and this is the same argument `v0.5 §4.1` makes

No tool here evaluates authority, and the §4.3.1 rows say so. The reason is that **an approval
grant authorizes nothing on its own.** A granted approval is a statement that a named human
answered yes to one exact `action_hash`; it becomes permission only when `Control.execute`
consumes it, and `Control.execute` evaluates the principal's expiry, then authority, then policy,
then the approval, in that order (`v0.3 §4.3.1`), every time, for the action the grant names. A
second authority evaluation here would be evaluating the *approver's* authority against the
*agent's* action, which is a different question that this release does not answer (see §10:
authenticating the approver's entitlement to approve is not in scope, and the honest place for it
is a separation-of-duties model that does not exist yet).

`resolve` is the same shape: it states what happened at a remote. It is `v0.1 §5.2`'s human
authority, and the store already refuses to apply it to anything but an `AMBIGUOUS` record.

### 4.4 The five read tools

| Tool | Arguments | Result |
|---|---|---|
| `list_pending_approvals` | `limit`: integer, optional, 1–200, default 50 | The pending, unexpired approval requests, oldest first |
| `inspect_action` | `action_id`: string, required | One `ctrlrun.inspection/v2` document (`v0.3 §12.2`) |
| `receipts` | `limit`: integer, optional, 1–200, default 20; `control`: string, optional | The last `limit` receipts as portable JSON, oldest last; `control` filters on `Receipt.controls` exactly as `ctrlrun receipts --control` does (`v0.6 §7.3`) |
| `effects` | `state`: string, optional, one of the `EffectState` values | The effect records, as `ctrlrun effects` reads them |
| `stats` | `since`: string, optional, an ISO-8601 timestamp with an offset or `<n>m`/`<n>h`/`<n>d` | One `ctrlrun.stats/v1` document (`v0.3 §6.4`) |

**`list_pending_approvals` is a filter, not a new store method.** It walks the
`APPROVAL_REQUESTED` events (`v0.1 §6.2`), reads each named request with
`StateStore.get_approval`, and keeps those whose status is `pending` and whose `expires_at` has
not passed. Both methods are already on the frozen protocol. A `StateStore.pending_approvals()`
would be a new row on a protocol three backends implement and a new case in the store conformance
suite (`v0.6 §2`), for a read that composes from what is there.

The cost is stated rather than hidden, and it is larger than the walk: `limit` bounds the
**response**, not the work. The scan stops early only once it has found `limit` *pending*
requests, so a store holding a hundred thousand answered ones does a hundred thousand
`get_approval` calls for one listing. `receipts`, `effects` and `inspect_action` likewise read
the store's collection in full before slicing. That is acceptable at the size this server is for
— one team's approval queue on one host — and it is the reason §2.1 refuses to open a port: the
read path is unauthenticated *and* unbounded in work, and either alone would be tolerable.

**A record whose stored status is `pending` and whose `expires_at` has passed is not pending.**
The store marks it `expired` when somebody tries to answer it (`v0.1 §4.1`, `check_answerable`),
which means a listing that trusted the stored status would offer an approver a request that
refuses the moment they answer it. The filter recomputes; T186 asserts it.

Each entry carries: `request_id`, `action_id`, `action` (the name), `action_hash`, `arguments`
(the canonical arguments), `resource`, `environment`, `principal` (`agent` and `user` — **not**
claims), `created_at`, `expires_at`, `expires_in_seconds`, and `policy_hash` (`v0.6 §7.1`) where
the request carries one.

`principal.claims` are withheld, and so is `issuer`, for `v0.3 §2.4`'s reason applied one step
further out: a claim can hold an employee number, a case id or a licence, and this listing is
rendered by a third-party assistant into somebody's chat history. `inspect_action` returns the
inspection document unchanged — claims included, once the action has a receipt to carry them
(`v0.3 §2.4`) — because that document is the evidence record and an approver looking one action
up has asked for it; the *listing* is a queue.

T182 asserts the withholding against the **whole rendered document** and not against the
`principal` key, because a claim reaching some other field would satisfy the narrower
assertion — and it asserts the other half too, that the same claims *are* in `inspect_action`,
without which it is a test that the listing is empty.

### 4.5 The three write tools

| Tool | Arguments | Effect |
|---|---|---|
| `approve` | `request_id`: string, required | `StateStore.grant_approval(request_id, approver)`, then `APPROVAL_GRANTED` |
| `deny` | `request_id`: string, required | `StateStore.deny_approval(request_id, approver)`, then `APPROVAL_DENIED` |
| `resolve` | `effect_key`: string, required; `outcome`: `"committed"` or `"failed"`, required; `reason`: string, required | `StateStore.resolve_effect(effect_key, state, resolver)`, then `EFFECT_RESOLVED` |

These are the calls `ctrlrun approve`, `ctrlrun deny` and `ctrlrun resolve` make, in the same
order, with the same event shapes. Nothing here is a second implementation of an approval: the
store performs the transition, and the store is where hash binding, single use and expiry live.

**`approve` binds to a hash it never chooses.** It names a `request_id`; the record's
`action_hash` is whatever was stored when the request was created (`v0.1 §4.1`), and the grant
carries it. There is no argument by which a caller could approve a *different* action than the
one the request names, which is why the mutation case (T187) is a test of the kernel reached
through this server rather than of anything this server does.

**`resolve` requires a reason, and `ctrlrun resolve` does not.** `v0.1 §5.2` makes a resolution
a human's claim about the real world, and the CLI's version is typed by somebody who has just
looked at the remote system. A resolution arriving through an assistant has a conversation behind
it and no record of it, so the reason is the record. It MUST be non-empty after stripping
whitespace; a whitespace-only reason is `-32602` and the effect is not resolved. The reason
reaches `EFFECT_RESOLVED.data.reason` and nowhere else. It is neither of the two things already
on that event: `data.resolved_by` is `v0.1 §5.2`'s `human` constant, saying what *kind* of
authority moved the record, and `data.resolver` is §5.4's attributed name, saying *whose*.

### 4.6 `tools/list`

The eight tools, with JSON Schema for each argument set, and a description that says in its first
clause whether the tool writes. A write tool's description MUST say that it requires an
authenticated human and that the answer is recorded under their name — the assistant renders it,
and an approver who did not know their name was going into the evidence log should learn it
before they answer rather than after.

`tools/list` needs no credential: which tools exist is not a secret, and refusing it would leave
a client unable to discover the read tools it *can* use.

## 5. What is recorded

### 5.1 Nothing new

No new table, no new column, no new event type, no new receipt field, no new schema string, no
new policy key. The evidence a write through this server leaves is the evidence the same write
through the CLI leaves, with one string different (§5.4). That is the test of whether the surface
is the right size: a server that had needed a new event type would have been a server doing
something the kernel does not already do.

### 5.2 Events

`APPROVAL_GRANTED`, `APPROVAL_DENIED` and `EFFECT_RESOLVED`, appended to the **store**, with the
data the CLI writes plus `data.via = "mcp-operator"` on each.

To the store and **not** through the `Control`'s `EventSink`s, because this server does not
compose the kernel (§1.1) and `Control` is the only thing that fans out. That is exactly what
`ctrlrun approve` does, so an approval answered here reaches an OTel exporter or the JSONL file
by the same route it does from the terminal — which is to say, not at all until the action that
consumes the approval writes its receipt. It is stated because it would otherwise be discovered,
and it is why §9.4 has no `--otel`: a flag that exported nothing would be a flag the operator
believed took effect.

The event is appended **after** the transition and is not part of it. An `append_event` that
failed would leave a granted approval with no `APPROVAL_GRANTED` beside it — `v0.1 §6`'s existing
window, which `ctrlrun approve` and the webhook endpoint have too, and which this server does not
widen or narrow. `via` is a **new key in an existing event's data**, which
`v0.1 §6.2` permits — event data is an open mapping — and not a new event type. A reader that
does not know the key ignores it.

It earns its place: `approver = "mcp-operator:alice"` (§5.4) already says who, and `via` says
through what, which is the question an incident review asks when an approval turns out to have
been given from a phone at 2 a.m.

### 5.3 Receipts

This server writes no receipt, because it proposes no action. The receipt for an approved action
is written by whatever executes it, and it carries `approver` exactly as it would have if the
answer had come from `ctrlrun approve`. T184's "attributed" half asserts against that receipt,
not against anything this server produced — which is the point: the attribution has to survive
the trip through the kernel or it is not attribution.

### 5.4 Attribution

The `approver` and `resolver` string is `mcp-operator:<name>`, where `<name>` is
`principal.user` — which §3.2 guarantees is not `None`.

`principal.agent` is deliberately not in it. The agent name on an approver's credential is
whatever the identity provider put there for a human's token; it varies by deployment, it is not
the human, and a string that sometimes names a person and sometimes names a service is not
evidence. The `via` key (§5.2) carries the channel and the receipt's `principal` block carries
the acting agent, so nothing is lost.

`StateStore` already refuses a name containing a control character (`v0.1 §5.3`, `_approver`), so
a `user` claim carrying a newline cannot forge a row in `ctrlrun effects` output. The refusal
surfaces here as `-41007` with the store's message; it is not caught and re-worded, because a
provider emitting control characters in a verified claim is a configuration defect and the
store's sentence names it exactly.

## 6. Startup

The block `ctrlrun mcp-operator` prints before the socket opens, on the model of `v0.3 §8.4`:

```
ctrlrun mcp-operator — listening on 127.0.0.1:8901/mcp
environment  production
store        .ctrlrun/state.db
identity     HeaderIdentityProvider
             trusts the header 'x-approver': it is worth what the proxy that sets it is
             worth, and that proxy must authenticate the caller and overwrite the header
             on every request (SPEC-v0.3 §3.3)
read tools   answer without a credential; loopback is not a boundary against other
             processes on this host
write tools  approve, deny, resolve — each needs a credential naming a human, and each
             answer is recorded under that name
```

The observe-mode banner (`v0.3 §6.5`) is printed by the CLI before this block, as it is for
every command that loads the operator's policy. An operator server against an observing
deployment is worth the line: the approvals it lists were requested by a `Control` that is not
enforcing, and answering one changes nothing in the world.

## 7. Failure

| Situation | Response |
|---|---|
| Body over `--max-body-bytes` | HTTP 413, no body |
| Body is not JSON, or not JSON-RPC 2.0, or a batch | HTTP 400, `-32700` / `-32600` |
| `MCP-Protocol-Version` outside the accepted set | HTTP 400, `-32022` |
| A mirrored header disagrees with the body | HTTP 400, `-32020` |
| An `Origin` header not in `--allow-origin` | HTTP 403, no body |
| A method other than the four of §2 | HTTP 200, `-32601` |
| An unknown tool name | HTTP 200, `-32602`, naming the tool |
| A tool argument of the wrong type, or a required one missing | HTTP 200, `-32602`, naming the argument |
| A write tool with no principal, a declined or rejected credential | HTTP 403, `-41007` |
| A write tool whose principal has no `user` | HTTP 403, `-41013` |
| A write tool whose principal has expired | HTTP 403, `-41014` |
| `approve`/`deny` on an unknown, answered or expired request | HTTP 200, `-41003`, with the store's reason |
| `resolve` on a record that is not `AMBIGUOUS`, or an unknown key | HTTP 200, `-41003`, with the store's reason |
| `resolve` with a blank reason | HTTP 200, `-32602` |
| The store raises anything else | HTTP 500, `-32603`, and the exception is logged, never returned |

A CTRLRun refusal is a JSON-RPC **error**, never a `result` with `isError: true`, for `v0.2
§6.10`'s reason: `isError` reaches the model as text, and a refusal to let a human's assistant do
something is not a tool result.

An identity refusal is HTTP 403 with the JSON-RPC error in the body, matching the gateway's
`-41007` (`v0.2 §6.10`). A refusal the store produced is HTTP 200, because the request was
well-formed and authenticated and the answer is *no*.

## 8. Acceptance tests

Numbering continues `v0.6 §8`, whose last test is T181.

### T182 — Read tools answer with no credential, and the listing withholds claims

Against a store holding one pending request, one receipt and one effect: `list_pending_approvals`,
`inspect_action`, `receipts`, `effects` and `stats` each return a result over a request carrying
no identity header and no bearer token. `tools/list` does too. The identity provider is a
recording double and asserts it was **not** called (§4.1's second paragraph — the negative half,
without which a provider that ran and declined would pass the first half).

### T183 — There is no way to bind a non-loopback address

`OperatorConfig(host="0.0.0.0")` raises `InvalidArgument`, and so does every other non-loopback
host. The test also asserts, by name, that no field of `OperatorConfig` and no option of
`ctrlrun mcp-operator` is called `allow_remote` — §2.1's two sentences hold together or not at
all, and a later flag must fail a test rather than a review.

### T184 — Each write tool refuses without a principal and succeeds with one, attributed

For each of `approve`, `deny` and `resolve`, three assertions:

1. With no credential: `-41007`, and the store is unchanged — the approval is still `pending`,
   the effect still `ambiguous`, and no event was appended.
2. With a credential naming `alice`: it succeeds, and the store shows the transition.
3. The evidence names her. `APPROVAL_GRANTED.data.approver == "mcp-operator:alice"`,
   `data.via == "mcp-operator"`, and — for `approve` — the receipt written when the agent
   afterwards executes the approved action carries `approver = "mcp-operator:alice"` (§5.3).

### T185 — A credential that names no human is refused

With `--user-header` configured but the request carrying only the agent header: `-41013`, and the
store is unchanged. The same with a JWT whose user claim is absent. `--principal` is refused at
construction (§3.1), and `--principal-header` without `--user-header` is too (§3.2).

### T186 — An expired request is not listed, and an expired credential cannot answer

Two halves, deliberately in one test because they are the same clock:

- A request whose `expires_at` has passed but whose stored status is still `pending` does not
  appear in `list_pending_approvals`, and answering it returns `-41003` with reason `expired`.
- A principal whose `expires_at` has passed is refused with `-41014` on every write tool, and the
  store is unchanged. Not `-41007`: §3.3's last paragraph.

### T187 — A mutated action is refused after an MCP-relayed approval

The signature test (`v0.1 §7 T3`), reached through this server. An action is proposed, the
request is approved through `approve`, the arguments are then changed by one unit, and
`Control.execute` refuses with `ApprovalMismatch`. The receipt records `approval_mismatch`.

The point of running it here is that the answer travelled through a different transport and the
binding held; the test asserts that the *original* action still executes with the same grant, so
that "everything was refused" cannot pass it.

### T188 — `resolve` requires a reason and records who resolved it

`resolve` with `reason` absent, empty, or whitespace-only is `-32602` and the effect is untouched
in all three. With a reason: the effect moves, `EFFECT_RESOLVED.data.reason` carries it, `data.resolver` and
`EffectRecord.resolved_by` are `mcp-operator:alice`, and `EFFECT_RESOLVED.data.resolved_by` is
`v0.1 §5.2`'s `human` constant. The four are asserted separately, because a test that only
checked the effect moved would pass with the reason dropped on the floor.

The two spellings of `resolved_by` are not a mistake, and the test pins both: the **record's** is
*who* (`v0.6 §5.3` made it queryable rather than buried in the executor's error text), the
**event's** is *what kind of authority* — `human` or `reconcile` (`v0.2 §2.5`). One name, two
facts, and a test asserting only one would let either drift into the other.

### T189 — The server never executes, never resumes, and never composes

Static, over `ctrlrun/gateway/operator.py`: it references no `Control` method but `store`,
`policy` and `environment`. `execute`, `resume`, `delegate`, `revoke` and `evaluate` appear
nowhere in it, and neither does `reserve_effect`, `commit_effect` or `put_approval_request`.
§1.1's "not a second composer" is a property of the source, so it is asserted against the source.

### T190 — A tool call that is refused writes nothing, in every refusal shape

Over the whole of §7's table, for each row that names a write tool: the event count and every
approval and effect record are identical before and after. Written as one table-driven test so
that a new refusal shape added later without this property fails.

**And the one row that is not byte-identical**, asserted as its own case with its own expected
delta: answering a lapsed request moves it `pending → expired` and appends no event. A table
that had simply omitted the row would be a table whose "every row" claim was false, which is how
the first draft of §4.2 came to assert something the kernel does not do.

And the logging half of §3.3: a refused write emits **exactly one** warning on the
`ctrlrun.mcp_operator` logger, and for an expired credential that line names the person and the
expiry while the client's message names neither.

### T191 — The server is reachable over a real socket, and refuses what §7 says

The end-to-end half: a `ThreadingHTTPServer` on a real port, a real POST, `initialize` then
`tools/list` then a read tool then a write tool. Plus the four transport refusals — oversized
body, batch, bad revision, header–body mismatch — because those are `ctrlrun.gateway.mcp`'s and
the test proves this server actually routes through it rather than reimplementing it.

### T192 — `import ctrlrun` does not import the operator server

`sys.modules` in a subprocess, as T30, T92, T125b and T134 do: after `import ctrlrun`, neither
`ctrlrun.gateway.operator` nor `ctrlrun.reporting`'s importers pull anything from an extra, and
`ctrlrun.gateway.operator` is absent from `sys.modules` entirely.

### T193 — One producer per document

`ctrlrun inspect --json` and `inspect_action` return the same document for the same action;
`ctrlrun stats --json` and `stats` return the same document for the same window. Asserted by
equality, not by shape: §1.1's "not a second composer" is worth nothing if the two drift.

## 9. Public API and CLI additions (frozen)

### 9.1 The names

```python
# ctrlrun.gateway.operator — ctrlrun[gateway], lazy, NOT re-exported at package import
#   ctrlrun.gateway.operator.OperatorConfig
#   ctrlrun.gateway.operator.OperatorServer
#   ctrlrun.gateway.operator.operator_identity_provider
#   ctrlrun.gateway.operator.build_operator_server
#   ctrlrun.gateway.operator.serve_operator_forever

# ctrlrun.gateway — the entry point the CLI calls
#   ctrlrun.gateway.serve_operator(**options) -> None

# ctrlrun.reporting — core, stdlib, NOT re-exported at package import
#   ctrlrun.reporting.inspection_for        — reads a store and chooses; one producer (§9.1)
#   ctrlrun.reporting.inspection_document
#   ctrlrun.reporting.effect_document
#   ctrlrun.reporting.approval_document
#   ctrlrun.reporting.stats_document
#   ctrlrun.reporting.since_boundary
#   ctrlrun.reporting.tally
#   ctrlrun.reporting.INSPECTION_SCHEMA
#   ctrlrun.reporting.STATS_SCHEMA
```

`OperatorServer` takes its identity provider as an argument, exactly as `Gateway` takes its
forwarder: `serve_operator` builds one with `operator_identity_provider` from the operator's
flags, and it is never built from anything in a request.

**It ships in `ctrlrun[gateway]` and imports nothing from an extra.** `v0.2 §1`'s rule is that
anything needing an HTTP server is an extra, and that is the whole of why it is there — it has
no upstream, so it never calls `http_client()` and never raises `MissingDependency`. Refusing to
start for a missing `httpx` would be refusing for a dependency it does not use. A core-only
install that reaches it gets a working server, and `import ctrlrun` still imports neither (T192).

`ctrlrun.reporting` is **new and core**, and it is the one structural change this document makes
to code that already shipped. It holds the two portable documents `ctrlrun inspect --json` and
`ctrlrun stats --json` produce, moved out of `ctrlrun/cli/main.py` unchanged, so that the CLI and
this server have **one** producer each rather than two that agree today. It sits above
`control.py` beside `cli/` and `verify/`, it imports no module from an extra, and nothing in the
kernel imports it. The CLI keeps every line that *renders* a document for a human, because only
the CLI prints (`ARCHITECTURE §6`).

**The choosing moved with the serializing**, and that is a correction an independent review
asked for: an earlier draft moved only `inspection_document`, leaving each caller to decide
*which* receipt, *which* effect key and *which* `action_hash` — and the receipt-versus-
`ACTION_PROPOSED` fallback is the subtle half, the one that decides whether an action still
awaiting a human can be inspected at all. `inspection_for(store, action_id)` owns both, and T193
asserts the two callers agree on an action with a receipt **and** on one without.

### 9.2 No new error type, event type, schema, table, column or policy key

Stated as a list so that adding one is visibly a change to this section:

- **Errors**: none. Every refusal is an existing `CTRLRunError` or a JSON-RPC error object.
- **Event types**: none. `data.via` is a key in an existing event's open data mapping (§5.2).
- **Schemas**: none. `ctrlrun.inspection/v2` and `ctrlrun.stats/v1` are returned unchanged.
- **Tables and columns**: none. The server issues no DDL and opens the store the CLI opens.
- **Policy keys**: none. The policy is loaded to report its mode and its actions, and nothing in
  it addresses this server.
- **`Control` methods**: none, and this is the load-bearing one. The server reads `Control.store`,
  `Control.policy` and `Control.environment`, all of which are properties frozen since v0.3, and
  calls nothing.

### 9.3 The JSON-RPC codes

Two are added to `v0.2 §6.10`'s table. Both sit in the `-410xx` range that release reserved, and
neither is reachable from the gateway.

| Code | Token | HTTP |
|---|---|---|
| `-41013` | `ctrlrun.not_a_human` | 403 |
| `-41014` | `ctrlrun.principal_expired` | 403 |

Reused unchanged: `-41003` `ctrlrun.approval_denied` for a store refusal about an approval or an
effect, and `-41007` `ctrlrun.no_principal`.

`mcp-operator.<tool>` is **not** an action name and MUST NOT be one. A policy naming
`mcp-operator.approve` is naming an action nothing proposes; the string exists only as
`IdentityContext.action` (§3.3).

### 9.4 The CLI

```
ctrlrun mcp-operator [--listen HOST:PORT] [--path PATH] [--environment ENV]
                     [--allow-origin ORIGIN]... [--max-body-bytes N]
                     [--store-url URL]
                     ( --principal-header NAME --user-header NAME
                     | --identity-jwt [--identity-jwt-* ...] )
                     [--authority PATH]
```

Every `--identity-jwt-*` flag is `ctrlrun gateway`'s, spelled identically and meaning the same
thing — and validated by the same function, not by a copy of it: `--identity-jwt` requires the
four settings that have no safe default, and any `--identity-jwt-*` flag without it is refused.
A copy would have drifted, and an independent review found that the first draft had no copy at
all: a server could start with an unpinned `typ`, which accepts an ID token. The checks are
`InvalidArgument` and never `assert`, because `python -O` deletes an assert and a guard a
runtime flag can remove is not a guard.

There is no `--principal`, no `--allow-remote`, no `--upstream`, no `--alias`, no
`--wait-approvals`, no `--otel` (§5.2 — it would export nothing), and no flag that relaxes any
check (§1.1).

`--authority` is accepted and loaded for one reason only: `Control.from_file()` may find an
`authority:` section in the policy, and a `Control` built without it would be a *different*
`Control` from the operator's — which is the thing `v0.4 §3.9` forbids verify from doing. It
changes no decision this server makes (§4.3).

### 9.5 What the independent review changed

This is an authorization surface, so it got a review in a session that did not write it, reading
the specification and every file that calls into the changed code. Ten findings; **all ten were
accepted** and none was declined, so there is nothing to record here as a decline. Four changed
the contract and are named because a reader of an earlier draft would otherwise be reading
something false.

| Found | Was | Is |
|---|---|---|
| The `--identity-jwt-*` checks were absent, and three `assert`s stood in for them | A server could start with an unpinned issuer, audience, algorithm or `typ`; under `python -O` the asserts vanished, and an unpinned `typ` accepts an ID token — an OIDC login would approve a payment | The gateway's `check_jwt_flags` is **shared, not copied** (§9.4), and every check is an `InvalidArgument`. T185 covers each missing setting and runs one case under `-O` |
| §4.2 claimed every refusal leaves the store byte-identical | False: answering a lapsed request moves it `pending → expired` and commits before refusing (`v0.1 §4.1`). T190's table omitted the row, so the claim was asserted nowhere | §4.2 carves out the one store refusal that writes and says why the kernel is right; T190 asserts the delta explicitly |
| T189 split the source on a marker and checked 81% of it | A `Control.execute` inside `do_POST` passed. The excluded fifth was the part that handles the socket | The forbidden vocabulary is in the test, assembled from pieces so it cannot match itself, and the scan covers the whole file. M26 confirms |
| `::1` was accepted by §2.1 and could not bind at all | `ThreadingHTTPServer` inherits `AF_INET`, so `--listen ::1:8901` exited with a `gaierror` traceback — an `OSError` the CLI does not catch. T183 asserted only that the string was stored: mutation pattern 3 | The socket family is chosen from the host, `[::1]` is accepted too, and T183 **binds** every host in the set |

The other six: `--otel` was inert and is gone (§5.2, §9.4); §11's "anything unexpected → store
unchanged" overclaimed and now names `v0.1 §6`'s existing event window; a `Content-Length` of
`-1` reached `read(-1)` and is refused (§2); the repeated-identity-header check lived only in the
stdlib handler and is now in `handle` (§2); the startup block omitted the `store` line §6 shows;
`serve_operator` leaked the store `from_file` opened when `--store-url` named another. Two
subsumed guards were collapsed and §9.1's frozen list was completed.

Four questions the review could not turn into findings are recorded because the absence is worth
as much as a finding: no path reaches a store write without a resolved, unexpired, human
principal; the attributed name cannot forge a row (`v0.1 §5.3`'s `_approver` refuses every
character that could); `GET`/`DELETE` never reach `handle`; and `--since` behaves exactly as it
did before the move to `ctrlrun.reporting`, exit code included.

## 10. Explicitly out of scope

Everything `v0.6 §11` excludes, plus:

- **Authenticating the approver's *entitlement*.** This server authenticates *who* is answering;
  it does not check that they were allowed to. `v0.3 §13` and `v0.5`'s do-not-build list already
  exclude authenticating the approver, separation of duties, M-of-N and break-glass, and nothing
  here changes that. The honest statement is in §4.3: any human whose credential the provider
  verifies can answer any pending request, exactly as any human who can run `ctrlrun approve`
  can today. **This is attribution, not authorization**, and no document may describe it as the
  latter.
- **stdio transport.** An MCP server launched over stdio by the assistant has no credential to
  verify — the process is whatever the client started, and every candidate identity is asserted
  by it. That is `--principal-from-client-info` (`v0.3 §8.1`) in a fourth costume, and it is the
  one thing this server cannot afford. HTTP with a proxy is the shape that has an answer.
- **Notifications, subscriptions or a push of pending approvals.** An approver asks; the server
  answers. A server that pushed would need a session, and §2 has none.
- **Resources or prompts.** Tools only.
- **A tool that proposes, executes or resumes an action** (§1.1).
- **A tool that edits policy, grants authority, delegates, revokes, or writes anything but the
  three transitions of §4.5.**
- **Approving more than one request in one call.** A batch approval is one human answer standing
  for several actions, and `v0.1 §4.2`'s binding is to one hash. The assistant may call the tool
  repeatedly; the server will not pretend that was one decision.
- **A `--auto-approve`, `--dry-run` or development mode of any kind** (§1.1, `v0.5 §3.8`).

## 11. Fail-closed table

| Situation | Result |
|---|---|
| No identity flag given | Refuses to start |
| `--principal` given | Refuses to start (§3.1) |
| `--principal-header` without `--user-header` | Refuses to start (§3.2) |
| `--identity-jwt` without `--identity-jwt-user-claim` | Refuses to start (§3.2) |
| A non-loopback `--listen` | Refuses to start (§2.1) |
| A write tool, no credential | `-41007`, store unchanged |
| A write tool, credential declined or rejected | `-41007`, store unchanged |
| A write tool, credential names no human | `-41013`, store unchanged |
| A write tool, credential expired | `-41014`, store unchanged |
| `resolve` with no reason | `-32602`, store unchanged |
| An unknown tool, an unknown method, a bad argument | A JSON-RPC error, store unchanged |
| Anything the store refuses | The store's refusal, unchanged. Store unchanged, **except** that answering a lapsed request records the lapse (§4.2) |
| Anything unexpected | `-32603`, logged, and nothing about the exception on the wire. Store unchanged for everything this server checks; a store call that committed and whose event then failed is `v0.1 §6`'s existing window, which `ctrlrun approve` has too (§5.2) |
