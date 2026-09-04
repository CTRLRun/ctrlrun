# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Public API names are frozen in `docs/SPEC-v0.1.md` §8. Before 1.0 they may still change, and
any change to one appears here.

## [Unreleased]

Work towards **0.3.0 — Authority**. Version is `0.3.0.dev0`; nothing in this section has
shipped, and the only thing in the tree today is the contract.

### Added

- **Delegation with attenuation** (build-list item 3, SPEC-v0.3 §5). A principal who holds a
  `delegable` grant can create a narrower one at runtime — `Control.delegate`, `ctrlrun
  delegate` — and revoke it — `Control.revoke`, `ctrlrun revoke`. A delegated grant is valid
  only if it is provably a subset of its parent on every dimension, checked **at creation and
  again at every evaluation**: a check performed only at creation would leave every delegation
  exactly as wide as the file used to be, which is the shape of every stale-permission incident
  there has ever been.
- **Omission never means unlimited.** A child that drops a dimension its parent constrains is
  rejected, not treated as inheriting the parent's limit and certainly not as unconstrained —
  including a child subject that carries a wildcard, omits `agent`, or drops the parent's
  `user`, each of which hands the grant to a wider population rather than a narrower one.
- **Revocation is transitive by structure.** Nothing is rewritten and no children are visited:
  a chain of any depth is cut by one write, because every evaluation walks to the root. Chain
  depth is **recomputed, never read** from the stored column, the walk is bounded and refuses a
  chain that revisits an id, and a stored delegation that cannot be read denies the action
  outright rather than being skipped in favour of a broader grant that happens to match.
- **A `delegations` table in both stores**, and `DelegationRecord` with it. A **new** table, so
  `CREATE TABLE IF NOT EXISTS` adds it to a database that already exists and v0.3 still needs
  no migration story; no existing table gains a column. `put_delegation` is an `INSERT` and
  never an upsert — an upsert on an existing id would clear `revoked_at`, which is `unrevoke`
  by another door in a release that says there is no such thing.
- **Three delegation event types**, `DELEGATION_CREATED`, `DELEGATION_REVOKED` and
  `DELEGATION_REJECTED`. They are about an authority record rather than an action, so
  `Event.action_id` becomes `str | None` and they carry `null`; `OTelEventSink` emits a
  standalone span for each rather than dropping them, because the highest-privilege operations
  in the release must not be the only ones missing from the export path.
- **An expired credential mints nothing.** `Control.delegate` refuses a `by` whose credential
  has lapsed, before it looks at the parent at all — a delegation is the most durable thing a
  principal can create, and it is the last place a stale credential should still work.
- **The `authority:` section: grants, patterns and evaluation** (build-list item 2,
  SPEC-v0.3 §4). A second axis, and it is **opt-in and then fail-closed**: a document with no
  `authority:` key behaves exactly as v0.2, and the moment one exists every principal needs a
  grant — including for actions the policy allows outright, including reads, including actions
  with no effect key. There is no `default: allow` and no flag that makes a missing grant
  permissive. `Authority`, `Grant`, `Subject` and `AuthorityResult` are public;
  `Control(authority=...)` takes one, and `Control.from_file` reads it from the same document.
- **Authority is evaluated before policy, and the two combine as the stricter of the pair.**
  A denial there appends `AUTHORITY_DENIED` and **never** `POLICY_EVALUATED`, so it cannot
  leave a pending approval request behind for an action that could never run. Neither axis
  loosens the other: authority cannot make a denied action allowed, and policy cannot make an
  unauthorized one permitted. **A grant carries no `decision:`** — how much autonomy an action
  has stays the same for every principal, and what differs is whether they may propose it at
  all.
- **A pattern grammar small enough that containment is decidable** (§4.4, §5.5): a literal, a
  `prefix*` that cannot cross a separator, and a final `**` whose preceding segments are all
  literals. `stripe.*` matches `stripe.refund` and not `stripe.refund.partial`; there is no
  `?`, no character class, and no short spelling for "everything" — granting the whole surface
  of a system is spelled `**`, one token, greppable, and impossible to write by accident.
- **Two new event types and one new error.** `AUTHORITY_RESOLVED` is appended for *every*
  action that passes authority, not only a delegated one, so a deployment with a permissive
  grant is distinguishable from one with no section at all. `AuthorityDenied` subclasses
  `ActionDenied` — an authority denial *is* the action being denied — and carries a reason from
  §4.3's closed set, never a grant id: a grant may legally be named `no_authority`, and
  evidence that can be spoofed by naming a grant is not evidence.
- **Extended `Principal`, `IdentityProvider`, and the two core providers** (build-list item 1,
  SPEC-v0.3 §2, §3). `Principal` gains `claims`, `issuer` and `expires_at`, validated the way
  arguments are — no `float`, no containers, a timezone-aware expiry — and **none of the three
  is in the action hash**, so an approval survives a token rotation. `StaticIdentityProvider`
  and `HeaderIdentityProvider` ship in core; the JWT one lands with item 5. A provider's answer
  wins over `context()`, a `None` is a decline that leaves the v0.1 path intact, and a provider
  that *raises* is never backfilled — falling back there would turn a rejected credential into
  a successful action.
- **An expired principal is refused before authority and before policy**, with a `denied`
  receipt and no approval request. `Control.evaluate` returns `deny`/`principal_expired`
  instead, because it may not write.
- **`ctrlrun inspect`** shows the issuer, the expiry and the claim *names*; the values reach
  `--json`. The OTel sink exports the issuer and the names only — a span goes to a third party
  by default, a receipt is evidence meant to be read.

- **`docs/SPEC-v0.3.md`** — the v0.3 contract, a delta over v0.1 and v0.2. Seven deliverables:
  an extended `Principal` and the `IdentityProvider` protocol; the `authority:` section with
  grants, patterns and constraints; delegation with structural attenuation; observe mode and
  `ctrlrun stats`; a JWT identity provider and the gateway wiring for both; examples and docs;
  the release. Tests come from §10 (T60–T93).
- **The `identity` extra**, empty until build-list item 5 adds the JWT verifier and the `pyjwt`
  line it needs. `pip install ctrlrun` still installs nothing but `pyyaml` and `click`.

### Changed

- **BREAKING: a condition naming `claims`, `issuer` or `expires_at` is refused at load**, in a
  document of *every* schema version (§4.5, §12.1). The reservation lives in the condition-key
  splitter, which runs for every condition in every document, and gating it on `v3` would leave
  the same name meaning two things in two files. A `v1` policy whose protected function takes an
  argument called `claims` stops loading until the argument is renamed; the load error says so.
- **`Control.evaluate` returns the combined decision**, not the policy axis alone. Its signature
  and `Evaluation`'s two fields are unchanged, and it still writes nothing — it would simply
  stop answering "what will happen to this action" if it reported one axis while `Control.execute`
  acted on both.
- **`Control.resume` records the authority axis on its receipt** (§5.6.1). Evaluated and
  recorded, not re-decided: the reservation is held and the remote may already be acting on it.
  A lease extension is the other way round — refused where authority no longer covers the
  action, so the lease lapses and the record becomes `AMBIGUOUS` by the ordinary path.
- **`AcsControlHook` refuses a `Control` that holds an `Authority`** (§8.4). It reads
  `params.metadata.agent_id` off the inbound envelope, and an authorization decision may not be
  made against a principal the caller asserted — the same sentence that removed
  `--principal-from-client-info`. It gains an `identity` provider of its own with item 5.
- **`policy._Condition` is now `policy.Condition`, and `policy.parse_conditions` is public.**
  A grant's `constraints:` is in exactly a rule's `when:` syntax and is parsed by the same code:
  a second condition evaluator would be a second place for `True` to start comparing equal to
  `1`. The top-level policy key set gains `authority`, which needs `ctrlrun.policy/v3`.
- **BREAKING: `context(environment=...)` is removed** — already recorded below, and item 1 is
  where it happens. `Control(environment=...)` replaces it.
- **BREAKING: `ctrlrun gateway --principal-from-client-info` is removed.** It exits non-zero
  naming `--principal-header`, rather than starting with a principal the operator did not ask
  for. `--environment` now defaults to unset so `$CTRLRUN_ENVIRONMENT` is not silently
  outranked, and `--user-header` without `--principal-header` is an error rather than a flag
  that cannot take effect.
- **A repeated identity header is refused** — `-41007`, HTTP 403, upstream untouched. A
  `Mapping` holds one value per name, so something had to decide what two become, and under
  authority that decision picks the principal.
- **`ctrlrun.receipt/v2` and `ctrlrun.inspection/v2`.** The receipt carries the whole principal
  and reserves `execution` and `would_have` as `null` until observe mode fills them, so the
  shape a reader parses settles once rather than changing twice under one version string. The
  stored Action carries the whole principal too — without it every receipt written by
  `Control.resume` reported no claims and no expiry, on the only receipt an MCP
  multi-round-trip action ever gets.

- **Four edits claimed by the previous change were never in the file.** A batch that wrote only
  at the end discarded every successful replacement before the one that raised, and only the
  failing one was re-run — so `authority_unreadable` was referenced by §4.6, three rows of §9 and
  T77d while §4.3's closed reason set never defined it; the precedence order was never reordered;
  the `pyyaml>=6.0` rationale was missing from §4.2; and `authority_grant` still had no route into
  evidence. A later edit then deleted §4.2's `environments` paragraph believing it a duplicate,
  when it was the only copy. All five are restored, and the tooling now writes after each edit so
  a later failure cannot undo an earlier success.
- **BREAKING: `context(environment=...)` is removed; `Control` takes `environment=` instead.**
  v0.3 makes the environment an authorization input — a grant may scope to
  `environments: ["staging"]` — and a dimension the subject sets is not one. On the decorator path
  the value came from the agent's own call site, so a grant scoped to staging was a real
  restriction through the gateway and decoration in-process.

  It is now set once per `Control`, from its `environment=` argument, else
  `$CTRLRUN_ENVIRONMENT`, else the policy document's top-level `environment:`, else
  `"production"`. The gateway's `--environment` and the ACS hook's configuration are unchanged —
  they were already this rule. A deployment that ran several environments from one process runs
  one `Control` each. `SPEC-v0.1.md` §8's frozen signature is amended in the same change, as the
  rule for a frozen name requires.

- **`docs/SPEC-v0.3.md` amended against an independent review of §4 and §5.** The contract was
  read by two reviewers who had not written it, and they found two authorization holes the
  author's own pass had missed.

  **An expired credential could mint permanent authority.** `Control.delegate`'s checks matched
  the creating principal on `agent` and `user`, which do not stop being equal when a token stops
  being valid — and §2.3's expiry refusal is scoped to `Control.execute`, which a delegation is
  not. A principal whose every action was being refused could still write an unexpiring,
  re-delegable grant for an agent of its choosing. §5.3 gains a rule 0.

  **The ACS hook read its principal off the wire.** `ctrlrun.acs` takes `agent_id` and
  `environment` from `params.metadata` on the inbound envelope. Under v0.2 that was survivable on
  the argument `v0.2 §6.5` makes for `clientInfo`; §4 ends it, and §8.1 removes
  `--principal-from-client-info` over exactly this. Removing the flag while the same pattern
  lived in another module would have made the removal a gesture. §8.4 gives the hook an
  `IdentityProvider` and refuses to construct one without it against a `Control` holding an
  `Authority`.

  Also: an unreadable stored delegation is now `authority_unreadable` and denies outright rather
  than being skipped in favour of a broader grant that happens to match; `Control.resume` joins
  the list of call sites that must use the combined decision, and §5.6.1 says what authority does
  across a lease extension, a commit and a resume; a `delegable` grant must declare `expires_at`,
  which is the only thing bounding the grantee population a compromised holder can reach; and
  `pyyaml>=6.0` becomes a floor, because PyYAML 5 returns naive datetimes and would reject the
  specification's own example.

### Notes on what the contract decides

Recorded here because each is a decision a reader of the code would otherwise have to
reconstruct:

- **Authority is opt-in as a section and fail-closed once present.** A file with no
  `authority:` key behaves exactly as v0.2. With one, every principal needs a grant; no grant
  means DENY.
- **Claims are receipt data, not action identity.** The canonical form of an Action is
  unchanged, so an approval survives a token rotation.
- **Omission never means unlimited.** A delegated grant that drops a dimension its parent
  constrains is rejected, not treated as unconstrained.
- **Observe mode is top-level only.** A partially-enforced configuration is the failure mode
  that rule exists to prevent.
- **Policy still cannot see the principal, and a grant carries no decision.** Authority is a
  second, independent axis that permits or denies; the two results combine as the stricter of
  the pair. How much autonomy an action has stays the same for every principal.
- **A delegation may change who acts, not how many.** Its subject must name a concrete agent —
  present, no wildcard — and a user its parent's pattern admits wherever the parent names one.
  Otherwise one delegation hands the grant to every agent, or strips the human it was bound to,
  and omitting the key reaches the same place as an asterisk.
- **Revocation and expiry are live; an edit to the document is not.** Grants are read when the
  document is loaded. `ctrlrun revoke` is the runtime kill switch and it covers delegations
  only; there is no hot reload and no runtime revoke for a root grant.

### Deprecated

- Nothing new. `--principal-from-client-info`, deprecated in 0.2.0, is **removed** in 0.3.0 by
  build-list item 1; SPEC-v0.3 §8.1 has the replacement.

### Fixed

- **`ruff format` reformatted Python code blocks inside the specs; `ruff check` never looked
  at them.** Whether a spec's examples were rewritten therefore turned on whether they happened
  to parse: `SPEC-v0.1.md` and `SPEC-v0.2.md` keep their aligned comments only because theirs
  carry placeholders like `<generated>`, and `SPEC-v0.3.md`'s parse, so a CI check that had
  never fired before went red on them and the alignment was flattened to make it green.

  A specification's code blocks are illustration rather than code — they elide, they annotate,
  they line comments up so a reader can compare down the column — and the formatter has no way
  to know that. `[tool.ruff.format] exclude` now tells it, `force-exclude` makes the exclusion
  mean the same thing however ruff is invoked, and SPEC-v0.3's alignment is restored.
  `test_the_formatter_leaves_markdown_alone` keeps it that way, with a control test that fails
  if the probe it relies on is not actually unformatted.

- **The gateway compared mirrored header values by re-parsing them, and Python's parser is
  lenient.** `Mcp-Param-{Name}` validation ran the header through `int()`, so a body carrying
  `amount: 2000` was certified as agreeing with headers spelling it `2_000`, `+2000`, `02000`,
  ` 2000`, `2000 `, and the Arabic-indic and fullwidth digit forms; booleans were matched
  case-insensitively, so `TRUE`, `True` and `tRuE` all agreed with `true`. None of those is
  what a JSON serializer writes, and each is read differently — or refused — by another
  parser: `parseInt("2_000")` is 2 in JavaScript and `strconv.Atoi` errors in Go.

  That is the exact hazard SPEC-v0.2 §6.4 exists to prevent. The gateway's job there is to
  certify that a routing intermediary and CTRLRun are looking at the same value, and it was
  certifying agreement that held only under Python's rules. CTRLRun's own decisions were never
  affected — the action is built from the body, and the headers are only checked — so this
  costs an intermediary's correctness rather than an approval binding.

  A header value must now be the body value's canonical rendering, compared as text, with no
  parser in the comparison — and only a string, an integer or a boolean has one. The revision
  permits `x-mcp-header` on those three types alone and omits the header for a `null`, so a
  header naming an argument of any other type is refused rather than compared against a
  rendering CTRLRun invented for it. This also declines the revision's SHOULD that servers compare
  integers numerically (`42.0` equals `42`): v0.1 §2.3 refuses a float in the body outright, so
  the leniency has no legitimate case here. SPEC-v0.2 §6.4 states both rules and the reasoning.

## [0.2.0] — unreleased

Everything below ships. `pip install ctrlrun` still installs nothing but `pyyaml` and
`click`; the gateway, the ACS hook and the OpenTelemetry sink live in extras.

### Added

- **MCP gateway** — `ctrlrun gateway --upstream <url> --alias <name>`, in `ctrlrun[gateway]`.
  An existing MCP tool server gets CTRLRun semantics with no agent changes: `tools/call`
  becomes an Action, everything else is relayed unchanged. The request forwarded upstream is
  built from the action's *canonical* arguments, so what was hashed, reserved and recorded is
  byte-for-byte what the tool receives. Serves `2026-07-28` and `2025-03-26`–`2025-11-25` in
  passthrough. A fresh connection per intercepted call, because "the connection was never
  established" is the only observation that proves non-execution.
- **Reconciliation hook** — `@protect(..., reconcile=...)`. The second authority permitted to
  move a record out of `AMBIGUOUS`, and only where its answer points. An exception, a nonsense
  return value and a hook that is never called all mean `"unknown"`, which changes nothing.
- **`Suspended` and `Control.resume`** — an executor may say "the remote asked for something
  before it will finish". The record stays `EXECUTING`, the lease is extended, the
  continuation is held, no receipt is written, and the signal reaches the caller. Built for
  MCP elicitation; used by the ACS adapter for the same reason.
- **`EventSink`** — a protocol receiving every `Event` and `Receipt`, called after the
  authoritative store write. `JSONLEventSink` is the v0.1 file writer under that interface.
  A sink that raises is logged and skipped; it can never change a decision or an outcome.
- **`OTelEventSink`** — in `ctrlrun[otel]`. One OpenTelemetry span per action, one span event
  per step, `ctrlrun.*` attributes. Argument values are withheld unless asked for.
- **`WebhookApprovalProvider`** — core, over stdlib `urllib.request`. One signed POST on
  `APPROVAL_REQUESTED`; the gateway serves the signed inbound grant/deny at
  `POST /ctrlrun/approvals/<request_id>`. An undelivered notification is not an approval.
- **`ctrlrun inspect <action_id>`** — one action's whole history: proposal, decision,
  approvals, effect, receipt and the event timeline. `--json` emits `ctrlrun.inspection/v1`.
- **Policy `schema: ctrlrun.policy/v2`** — per-action `effect:`, `resource:` and `mcp:`
  templates, needed because a gateway call has no decorator to carry them. Where a decorator
  and the policy disagree, the decorator wins and the mismatch is warned about once.
- **ACS control hook** — `ctrlrun.acs.AcsControlHook`, in `ctrlrun[gateway]`. Answers the
  OWASP Agent Control Standard's `steps/toolCallRequest` and `steps/toolCallResult`. See
  `docs/ACS.md` for the mapping and for the four places ACS is silent. **No compliance
  claim**: at the commit read there is no ACS reference implementation and no conformance
  suite, so there is nothing to be conformant with.
- **`examples/`** — four standalone failure scenarios, an ACS integration example, and nine
  sector policy templates under `examples/policies/`.

### Changed

- `StateStore.append_event` returns the event as stored, where it returned `None`. Sinks must
  be called with the `event_id` the store assigned, and the store is the only thing that knows
  it. Callers that ignore the return value are unaffected. Recorded in `SPEC-v0.1.md` §8.
- `SQLiteStateStore` no longer writes JSONL. `Control` does, through `JSONLEventSink`, and
  `Control.from_file()` installs one by default — so the two files land exactly where v0.1 put
  them and existing evidence directories are unchanged.
- `docs/SPEC-v0.2.md` §9 amended: it forbade an ACS adapter on the reading that ACS had no
  stable interface. The v0.1.0 schemas say otherwise, so the adapter ships. The no-claim rule
  is untouched.

### Deprecated

- **`ctrlrun gateway --principal-from-client-info` — removed in 0.3. Use
  `--principal-header`.** It takes the agent's name from `_meta["io.modelcontextprotocol/
  clientInfo"]`, which the MCP revision says is self-reported and *"SHOULD NOT"* be relied on
  for security decisions. It is offerable in 0.2 only because of a fact that stops being true:
  a v0.1 policy cannot address the principal at all (`SPEC-v0.1.md` §3.2 refuses `agent_eq`
  and every other reserved name at load), so an unauthenticated principal misattributes
  evidence and cannot widen an outcome. v0.3's authority model makes the principal an
  authorization input, at which point a self-reported name cannot be one. The flag warns at
  startup and its `--help` says so.

### Removed

- `SQLiteStateStore.journal`, and the `EventLog` class behind it. `JSONLEventSink` is that
  class under the sink interface, and it is `Control`'s now.

### Fixed

- `.gitignore` ignored `ctrlrun.yaml` unanchored, so it matched at any depth and silently kept
  every example's policy file out of the repository. Anchored to `/ctrlrun.yaml`.

### Compatibility

- **A v0.1 `ctrlrun.yaml` loads unchanged.** `ctrlrun.policy/v2` is opt-in and additive; a
  document declaring `v1` that uses a v2 key is a load-time `PolicyError` naming the key and
  the schema it needs, because a v0.1 reader would ignore the template and execute with no
  duplicate protection at all.
- **The receipt schema is unchanged** — `ctrlrun.receipt/v1` still describes every receipt
  v0.2 writes. `EFFECT_RESOLVED` gains `data.resolved_by`, and four event types join the set:
  `RECONCILIATION_STARTED`, `RECONCILIATION_RESOLVED`, `EXECUTION_SUSPENDED`,
  `EXECUTION_RESUMED`.
- A database written by v0.1 is read by v0.2 without migration: the one new table
  (`continuations`) is created on open.

### Notes

- The spec is written against **MCP revision 2026-07-28**, which removed the `initialize`
  handshake, protocol-level sessions and `Mcp-Session-Id`, and made `Mcp-Method` / `Mcp-Name`
  required request headers that servers must validate against the body. The gateway will also
  serve `2025-03-26` through `2025-11-25` in passthrough mode, relaying session ids, `GET` SSE
  streams and `DELETE` without interpreting them; header–body validation applies only where the
  headers exist. Decisions come from the parsed body on every revision, so header trust is
  never the guarantee. The deprecated `2024-11-05` HTTP+SSE transport is not served.
- An MCP tool call held open across a multi round-trip elicitation keeps its effect reservation
  in `EXECUTING` with an extended lease, so concurrent duplicates stay blocked for the whole
  exchange and only the final result is mapped to an outcome. This needs the upstream to supply
  a `requestState`, the protocol's only correlator and an optional one; without it the first
  leg is `AMBIGUOUS`, because the alternative would let any client walk past duplicate
  protection by inventing an `inputResponses` field.
- A policy file using the new `effect:` / `resource:` / `mcp:` keys must declare
  `schema: ctrlrun.policy/v2`. `ctrlrun.policy/v1` files keep loading unchanged; a `v2` file
  will not load on 0.1.0, which is the point — 0.1.0 would ignore the effect template and
  execute with no duplicate protection.
- MCP tool arguments that CTRLRun cannot canonicalize — any JSON number with a fraction — will
  be refused by the gateway, never rounded or coerced. Tools that move money through the
  gateway need integer minor units or decimal strings in their schema.

## [0.1.0] — 2026-09-03

First packaged release. The v0.1 kernel is complete: every acceptance test in
`docs/SPEC-v0.1.md` §7 passes, including the multi-process concurrency test.

### Added

- **Action** — canonical form, `action_hash`, deep-frozen arguments. `float` is rejected in
  arguments: `0.1` and `0.10` are the same money and different hashes.
- **Policy** — YAML loader with `ALLOW` / `APPROVE` / `DENY` and fail-closed defaults. An
  unknown action is denied; there is no default-allow. See the config-breaking rule below
  for how condition keys are validated.
- **`@ctrlrun.protect()`** — binds a function call to an Action, evaluates it, and executes
  from the action's canonical arguments rather than the caller's objects.
- **Approval binding** — approvals carry the `action_hash` of what a human saw, and are
  single-use and expiring. A mutated action cannot present an approval granted for another.
- **Effect key and reservation** — template-resolved effect identity, reserved atomically
  across processes via `BEGIN IMMEDIATE` and a unique constraint on `effect_key`.
- **Effect state machine** — `NEW → RESERVED → EXECUTING → COMMITTED | FAILED | AMBIGUOUS`.
  Only an executor raising `NotExecuted` produces `FAILED`; every other exception, timeouts
  included, produces `AMBIGUOUS`, and only a human resolves it.
- **Receipts and events** — portable JSONL evidence for every action.
- **CLI** — `init`, `demo`, `approve`, `deny`, `receipts`, `effects`, `resolve`.
- **`ctrlrun demo`** — four failure scenarios, in process, no network.
- `SECURITY.md` and `docs/CLAIMS.md`, which maps every README claim to its code and test.

### Config-breaking rules

Rules that reject a policy file which an earlier build of this kernel would have loaded.
A `ctrlrun.yaml` written before this release may need an edit; the process refuses to start
until it gets one, which is the point.

- **A condition key naming an `Action` field is now a load-time `PolicyError`.** The
  reserved names are `action_id`, `agent`, `environment`, `principal`, `resource` and
  `user`. `when: { environment_eq: production }` reads exactly like it scopes a rule to
  production, and matched nothing at all — conditions address an action's *arguments*, and
  those are not arguments. Combined with a catch-all `decision: allow` beneath it, a rule
  that looked restrictive silently permitted everything. If a protected function genuinely
  takes an argument by one of those names, rename the argument (SPEC-v0.1 §3.2). Only the
  whole name is reserved: `resource_id_eq` is unaffected.
- **A condition on an argument the action does not carry now logs a warning.** The decision
  is unchanged — still false, still never an error, per SPEC-v0.1 §3.2 — but a typo such as
  `amont_lte` no longer disappears in silence. Nothing to edit; expect new log output.

### Notes

- Requires Python ≥ 3.11. Runtime dependencies are `pyyaml` and `click`.
- Single-host only: reservation is atomic across processes on one machine via SQLite.
  Multi-host needs the Postgres store planned for v0.6.
- Receipts are not signed. A database administrator can alter history (v0.6).
- Approver identity is free text and is not authenticated (v0.3).
- Generated ids (`act_`, `apr_`, `ctr_`) are 128 bits. An approval id is not a bearer token
  in v0.1 — consuming one needs write access to the store — but it becomes one with the
  webhook provider in v0.2, and an id format cannot be widened after records exist.
- Effect key templates do not escape placeholder values, so a crafted argument can make two
  distinct effects share one key. The result is a refusal rather than a double execution;
  `docs/THREAT_MODEL.md` states the limit and the workaround.
- Policy conditions address an action's arguments only. Scoping a rule by environment,
  resource or principal arrives with the authority model in v0.3.

[Unreleased]: https://github.com/CTRLRun/ctrlrun/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/CTRLRun/ctrlrun/releases/tag/v0.1.0
