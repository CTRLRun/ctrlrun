# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Public API names are frozen in `docs/SPEC-v0.1.md` §8. Before 1.0 they may still change, and
any change to one appears here.

## [Unreleased]

Nothing yet.

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
