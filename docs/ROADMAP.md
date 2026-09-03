# Roadmap

Dependency-first: every layer depends on the one below being correct. Milestones ship when their tests pass, not on dates. Nothing below v0.1 is in scope for code today.

## v0.1 — Kernel (current)

Action · Policy (ALLOW/APPROVE/DENY) · `@protect` · exact-action approval (hash, single-use, expiry) · effect key · SQLite atomic reservation · COMMITTED/FAILED/AMBIGUOUS · receipts + events (JSONL) · CLI · `ctrlrun demo` with four scenarios.

Exit: all acceptance tests in `SPEC-v0.1.md §7` pass; demo < 60 s; README literally true.

## v0.2 — Zero-friction deployment

- MCP adapter and `ctrlrun gateway --upstream <mcp server>` so an existing MCP tool server gets CTRLRun semantics with no agent changes.
- OpenTelemetry export of events (align with ACS observability; don't invent a tracing format).
- Webhook approval provider (Slack/Teams/anything that can POST back).
- `ctrlrun inspect <action_id>`.
- Reconciliation hook: executor may implement `check(effect_key) -> committed | not_executed | unknown` to resolve AMBIGUOUS automatically.

Adoption story: *existing MCP server + one CTRLRun gateway = action safety.*

## v0.3 — Authority

- Principal abstraction and `IdentityProvider` interface.
- Authority grants: subject, permitted actions, resource patterns, constraints (limits, currency, environment), expiry.
- Delegation with attenuation: `child ⊆ parent`, enforced; escalation → DENY.
- Fourth signature demo: authority escalation.

This is the first point at which `VISION.md` may be opened for design input.

## v0.4 — Verification

- `ctrlrun verify`: runs deterministic failure scenarios against a user's config and reports pass/fail per guarantee (mutated approval, replayed approval, duplicate reservation, concurrent reservation, ambiguous retry, unknown action fail-close, expired authority, delegation escalation).
- Counterexample output on failure.
- GitHub Action + badge ("CTRLRun Verified n/n"). The badge means *declared guarantees pass*, never "this agent is secure."

## v0.5 — Framework ecosystem

- Thin adapters: OpenAI Agents SDK, LangGraph. Reuse each framework's own HITL/approval primitives where they exist; never reimplement them.
- Adapter contract documented so the community writes the rest (CrewAI, ADK, PydanticAI, TypeScript).

## v0.6 — Production durability

- Postgres StateStore (cross-host reservation).
- Schema migrations, recovery on restart, policy versioning, receipt integrity (hash chain / signatures).

## v0.7 — Multi-agent

- A2A integration: task-bound delegated authority with limits, expiry, and depth.
- Authority propagation across agent hops.

## v0.8–0.9 — Hardening

Fuzzing, property tests, concurrency stress, failure injection, benchmarks, external security review, upgrade testing, compatibility guarantees, CodeQL/SAST/SBOM/signed artifacts.

## v1.0 — Stable contracts

1.0 means stable contracts, not feature count: Action schema, Receipt schema, effect semantics, Policy API, StateStore API, Adapter API. MCP production-grade. Authority model documented. Threat model published. Security audit complete. Upgrade path tested.

## Beyond v1.0

A management plane — organization-wide policy, approval center, fleet views, central evidence — is not on this roadmap. It gets built only if users pull toward it.
