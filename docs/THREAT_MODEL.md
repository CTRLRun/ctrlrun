# Threat Model

CTRLRun sits in the execution path of consequential actions. This document states what it defends against, what it explicitly does not, and the fail-closed rules that follow. It is scoped to v0.1 and will grow with the roadmap.

## Assets

- **Correctness of consequential effects** — an effect happens at most once per intent, and only as authorized.
- **Integrity of human approval** — what a human approved is what executes.
- **Integrity of evidence** — receipts reflect what happened.

## Trust boundaries

```
 untrusted ─────────────┐        trusted ──────────────────┐
  agent reasoning       │         CTRLRun process           │
  LLM outputs           │         policy file               │
  tool outputs          │         StateStore (SQLite file)  │
  retry logic           │         approver's shell          │
  other agents          │                                   │
```

The agent is treated as a potentially compromised or hallucinating principal. Everything it proposes is verified; nothing it asserts is trusted.

## In scope — CTRLRun v0.1 defends against

| Threat | Control |
|---|---|
| Hallucinated or unlisted action | Unknown action → DENY |
| Prompt-injected agent proposing a forbidden action | Policy DENY; approval required for sensitive ones |
| Agent mutates action after human approval | Approval bound to `action_hash`; mismatch → DENY |
| Agent replays a consumed approval | Single-use, atomic consumption |
| Stale approval | Expiry checked at consumption |
| Duplicate execution on retry after timeout | Effect key + AMBIGUOUS + retry refused |
| Duplicate execution from concurrent agents | Atomic cross-process reservation |
| Silent loss of in-flight effect (worker crash) | Lease expiry → AMBIGUOUS, never released |
| Misclassifying unknown outcome as failure | Only `NotExecuted` → FAILED; else AMBIGUOUS |
| Malformed or missing policy | Load-time error; no Control without valid policy |
| Float-based hash collisions/mismatches | Floats rejected in arguments |

## In scope — CTRLRun v0.3 adds

The authority model answers a question v0.1 and v0.2 could not: *who is acting, and what are
they entitled to?* Everything above still holds; these are the threats the second axis closes.

| Threat | Control |
|---|---|
| A principal proposing an action nobody granted them | `authority:` present → no grant means DENY, including for actions the policy allows outright |
| An agent widening its own authority by delegating | Containment on every dimension, at creation **and** at every evaluation |
| A delegated grant that silently inherits what it does not name | Omission is rejected, never treated as unconstrained or inherited (§5.4) |
| A delegation handed to a wider population than its parent covered | A child subject may not carry a wildcard or drop its parent's `user` |
| A compromised chain that has to be cut in a hurry | `ctrlrun revoke` is transitive by structure: one write cuts a chain of any depth |
| Authority outliving the credential that created it | `delegable: true` requires `expires_at`; `Control.delegate` refuses an expired `by` |
| A credential that has expired mid-action | Refused before authority and before policy; a lease extension is refused and the record becomes `AMBIGUOUS` by the ordinary path |
| A forged or tampered token | `JWTIdentityProvider` verifies the signature against a JWKS or a pinned key, with the algorithm taken from its own allow-list and never from the token (RFC 8725 §3.1) |
| An ID token presented as an access token | `token_type` is required and `typ` is checked — the cross-JWT confusion of RFC 8725 §2 |
| A token for another audience or issuer | `aud` by exact membership on either wire shape, `iss` exact, `exp` required |
| Signing keys fetched from somewhere else | JWKS over HTTPS only, redirects refused outright, a duplicate `kid` refused rather than resolved, a failed fetch never emptying the cache |
| An unauthenticated principal reaching an authorization decision | `--principal-from-client-info` removed; `AcsControlHook` refuses an `Authority` without an `identity` provider |
| An environment chosen by the caller | The environment is set once on the `Control` and is never read off the wire |

## Out of scope — CTRLRun does not defend against

- A compromised CTRLRun process, host, or Python environment.
- A root attacker or a malicious administrator with write access to the policy file or SQLite database.
- A compromised external service (Stripe lying about outcomes).
- A compromised approver, or social engineering of the approver. CTRLRun proves *what* was approved, not that the human was right.
- Executors that raise `NotExecuted` incorrectly (asserting no side effect when one occurred). This is an integration bug; v0.4 `verify` will include a check for it where reconciliation exists.
- Data exfiltration through *read* actions the policy allows. CTRLRun is not DLP.
- Denial of service by flooding approval requests.
- Bypassing the decorator entirely (calling the raw function). v0.2 gateway mode narrows this; process-level enforcement is out of scope.
- **A compromised identity provider.** CTRLRun *consumes* identities: it verifies a token somebody else issued and maps the verified claims onto a `Principal`. It issues nothing, and an issuer that signs a token for the wrong subject has told CTRLRun the truth as far as CTRLRun can tell. Everything downstream — grants, delegation, receipts — is then wrong, correctly and consistently.
- **A `HeaderIdentityProvider` behind a proxy that does not overwrite the header.** It is worth exactly what the thing setting it is worth, and RFC 7239 §8.1 says the same of the header it standardizes. If the agent can set the header, the agent chooses its own authority. It warns at construction and it is still the operator's call.
- **A revoked token before its `exp`.** There is no revocation channel: a verified token is valid until it expires, which is why one with no `exp` is refused. Shared-signals mechanisms exist and v0.3 implements none of them. Short lifetimes are the whole of the story.
- **A tenant-templated issuer.** `issuer` is matched as an exact string, so a multi-tenant endpoint cannot be configured correctly here. Pointing it at one without pinning the tenant makes every tenant on that platform a valid issuer — stated because the fail-open is inviting.
- **Authority across an agent-to-agent hop.** A grant covers the principal CTRLRun resolved for *this* call. Propagating attenuated authority across hops is v0.7.
- **Approving an authority change.** `ctrlrun delegate --as` is an assertion typed at a shell, not an authentication; the record keeps `created_via` so a reader can tell an act from an assertion. Authenticating the *approver* remains out of scope, as in v0.1.

## Fail-closed rules (v0.1, not configurable)

| Condition | Result |
|---|---|
| action not in policy | DENY |
| policy missing / malformed | cannot start |
| approval missing / expired / mismatched / consumed | DENY |
| effect key template unresolvable | DENY |
| effect COMMITTED / AMBIGUOUS / in-progress | reservation refused |
| lease expired mid-execution | AMBIGUOUS |
| executor raised non-`NotExecuted` | AMBIGUOUS |
| StateStore unavailable | exception; no execution |

## Known v0.1 limitations

- **Effect key templates do not escape placeholder values.** A template is literal text with values substituted in, so `refund:{tenant}:{payment_id}` resolves `tenant="acme:evil", payment_id="p1"` and `tenant="acme", payment_id="evil:p1"` to the same key. Arguments come from the agent, which this model treats as untrusted, so a crafted argument can make two distinct logical effects share one identity. The consequence is a refusal, not a double execution — the second attempt is blocked as a duplicate — so this costs availability, not correctness, and it fails in the safe direction. Until values are escaped, put the untrusted placeholder last, or use a delimiter the value cannot contain.
- Single-host reservation only (SQLite). Multi-host needs Postgres (v0.6).
- Approver identity is free text; no authentication of the approver (v0.3).
- Receipts are not signed; a database admin can alter history (v0.6).
- No reconciliation; AMBIGUOUS always needs a human (v0.2 adds executor `check`).
- The decorator can be bypassed by code that doesn't use it.

## Known v0.2 limitations (specified, not yet shipped)

These follow from `SPEC-v0.2.md` and are recorded here as they are decided, not after the code
lands. Nothing in this section describes behaviour you can run today.

- **A lazily-validating upstream can win a retry it should not have.** The gateway maps the
  JSON-RPC errors that the specification defines as emitted *before dispatch* — `-32700`,
  `-32600`, `-32601`, `-32602`, and MCP's `-32020` / `-32021` / `-32022`, plus HTTP `401` and a
  scope-challenge `403` — to `FAILED`, permitting an automatic retry. They are the closest
  thing MCP offers to an executor raising `NotExecuted` (SPEC-v0.1 §5.5): the peer is stating
  in band that it rejected the request rather than running the method. An upstream that does
  work and *then* returns `-32602` violates JSON-RPC 2.0, and CTRLRun will retry against a side
  effect that already landed. The alternative — mapping every error to `AMBIGUOUS` — makes a
  routine token expiry or a typo'd tool name cost a human `ctrlrun resolve`, which is how a
  guarantee becomes something people switch off. The asymmetry stays where v0.1 put it:
  `-32603 Internal error` and every unrecognized code are `AMBIGUOUS`.
- **`not_executed_on_error: true` is an operator's assertion, and is not checked.** It maps a
  tool result carrying `isError: true` to `FAILED` for one tool. It is `NotExecuted` expressed
  in YAML by the person who knows their upstream, and it is wrong in exactly the same way if
  they are wrong.
- **An approval does not cover input elicited mid-call.** A tool call held open across an MCP
  multi round-trip exchange executes with `inputResponses` the approver never saw. Two of the
  three mutation paths are closed — the continuation must present the exact `requestState` the
  gateway relayed, and its arguments must canonicalize identically to the approved ones — so
  the approved call cannot be altered. What remains is the content of the elicited answer
  itself, which a compromised upstream chooses the question for. It is recorded
  (`EXECUTION_RESUMED` carries the keys and a digest) but not approved. Deny the tool if that
  is unacceptable; binding an approval across a round trip is a v0.3 question.
- **The gateway's principal is not authenticated.** `--principal-header` is worth whatever the
  proxy that sets it is worth, and `--principal-from-client-info` reads a field the MCP
  specification says implementations *"SHOULD NOT rely on … for security decisions"*. It is
  survivable only because a v0.1 policy cannot address the principal at all, so an
  unauthenticated one misattributes a receipt and cannot widen a decision. That stops being
  true with the authority model, and the `clientInfo` option is removed in v0.3.
- **Reservation is still single-host.** Two gateways in front of one upstream share no
  reservations unless they share a state file on one machine.

## Known v0.3 limitations

- **`Authority` is built at load time and is not hot-reloaded.** Revocation and expiry are
  live — read from the store and the clock on every evaluation — but an *edit to the file* is
  not. Narrowing a ceiling, bringing an expiry forward, removing `delegable` or deleting a
  grant takes effect when the process next loads the document, which for `ctrlrun gateway`
  means a restart. The runtime lever is `ctrlrun revoke`, one delegation at a time, by id.
- **There is no way to list delegations**, so there is no way to sweep a subtree. The ids are
  in the events file. Cutting a chain of *unknown* width means setting `delegable: false` on
  the root grant and restarting, after which §5.6 rule 6 denies every descendant.
- **Observe mode executes.** It is the rollout path, not a sandbox: effects land at remotes
  and the records of them are real. What it suspends is CTRLRun's refusals, wholesale — every
  ⚠ row of `SPEC-v0.3.md` §9 at once. It is not a per-action opt-out and cannot be made one.
- **A `mode: observe` writer and a ≤ 0.2 reader do not mix.** `ReceiptResult` gains
  `observed`, and `Receipt.from_dict` parses `result` into a closed enum — so an older process
  reading the same store raises. Upgrade every reader before switching any writer.
- **Claims are receipt data, not action identity.** They are deliberately outside the action
  hash, so an approval survives a token rotation — and equally, a claim that changed between
  proposal and execution does not invalidate one. Matching a grant on a claim is out of scope
  (§13): it needs an answer to "what does a missing claim mean" that v0.3 does not have.

## Disclosure

Report vulnerabilities privately to contact@arpanghoshal.com. Do not open public issues for security reports. `SECURITY.md` has the process and what counts as a vulnerability.
