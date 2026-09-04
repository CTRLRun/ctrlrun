# Roadmap

Dependency-first: every layer depends on the one below being correct. Milestones ship when their tests pass, not on dates. Nothing below v0.1 is in scope for code today.

Standards rule: integrate first, map second, never claim compliance. A standard appears in a mapping doc only after code touches it and a test proves the guarantee.

Sector rule: every pack cites its sources and ships its `REVIEW.md`. No compliance claims.

Track rule: kernel versions ship correctness; content tracks (packs, templates, mappings) ship on their own cadence and never block or share a version with the kernel.

## v0.1 — Kernel (current)

Action · Policy (ALLOW/APPROVE/DENY) · `@protect` · exact-action approval (hash, single-use, expiry) · effect key · SQLite atomic reservation · COMMITTED/FAILED/AMBIGUOUS · receipts + events (JSONL) · CLI · `ctrlrun demo` with four scenarios.

Exit: all acceptance tests in `SPEC-v0.1.md §7` pass; demo < 60 s; README literally true.

Standards: none. `THREAT_MODEL.md` is the only compliance-adjacent claim.

## v0.2 — Zero-friction deployment (shipped)

- MCP adapter and `ctrlrun gateway --upstream <mcp server>` so an existing MCP tool server gets CTRLRun semantics with no agent changes.
- OpenTelemetry export of events (align with ACS observability; don't invent a tracing format).
- Webhook approval provider (Slack/Teams/anything that can POST back).
- `ctrlrun inspect <action_id>`.
- Reconciliation hook: `@protect(..., reconcile=...)` resolves AMBIGUOUS automatically, and only where its answer points.
- `examples/` directory with standalone scripts per scenario (`double-refund/`, `approval-mutation/`, `agent-race/`, `approval-replay/`). In v0.1 `ctrlrun demo` is the example; separate scripts earn their keep once there is more than one way to wire CTRLRun in.
- Sector policy templates: `examples/policies/<sector>.yaml` for devops, payments, e-commerce, insurance, healthcare, legal, security, government, hr. Header comment: *"Starting point on the v0.1 kernel. Adapt before use."* Uses only v0.1 primitives. Tier one of the sector-pack content track below.

Adoption story: *existing MCP server + one CTRLRun gateway = action safety.*

**Reconciled against what shipped.** Three things arrived earlier than this file expected, and
one arrived that it did not list:

- The **OWASP ACS adapter** was a v0.3 standards line. Reading the v0.1.0 schemas showed a
  stable enough interface to build against, so it shipped here — with no compliance claim, and
  `docs/ACS.md` recording where the standard is silent.
- **`Suspended` / `Control.resume`** were not on any milestone. MCP elicitation (§6.9) needs a
  reservation held across a round trip the kernel does not control, and so does an advisory
  hook model like ACS. It is public API now, frozen in `SPEC-v0.2.md` §11.
- **Policy `schema: ctrlrun.policy/v2`** grew out of the gateway rather than being planned:
  a tool call has no decorator to carry an effect template.
- **`EventSink`** replaced the store's file writing, which the v0.1 kernel had owned.

Standards: OpenTelemetry export (code), MCP gateway. The OWASP ACS adapter shipped in v0.2 (see `docs/ACS.md`); "ACS-compatible" is still unearned and waits on an ACS conformance suite to measure against.

## v0.3 — Authority

- Principal abstraction and `IdentityProvider` interface.
- Authority grants: subject, permitted actions, resource patterns, constraints (limits, currency, environment), expiry.
- Delegation with attenuation: `child ⊆ parent`, enforced; escalation → DENY.
- Fourth signature demo: authority escalation.

This is the first point at which `VISION.md` may be opened for design input.

Standards: align principal/delegation semantics with NIST agent identity work and OAuth-based agent identity. Wording is "consumes identities from", never "implements". `--principal-from-client-info` is removed here: it exists in v0.2 only because a v0.1 policy cannot address the principal at all, and the authority model makes a self-reported name an authorization input.

## v0.4 — Verification

- `ctrlrun verify`: runs deterministic failure scenarios against a user's config and reports pass/fail per guarantee (mutated approval, replayed approval, duplicate reservation, concurrent reservation, ambiguous retry, unknown action fail-close, expired authority, delegation escalation).
- Counterexample output on failure.
- GitHub Action + badge ("CTRLRun Verified n/n"). The badge means *declared guarantees pass*, never "this agent is secure."

Standards: first mapping doc — each `ctrlrun verify` guarantee mapped to the OWASP Agentic Top 10 entries it mitigates. Entries not covered are listed as not covered.

## v0.5 — Framework ecosystem

- Thin adapters: OpenAI Agents SDK, LangGraph. Reuse each framework's own HITL/approval primitives where they exist; never reimplement them.
- Adapter contract documented so the community writes the rest (CrewAI, ADK, PydanticAI, TypeScript).

Standards: none new.

## v0.6 — Durable runtime

- Postgres StateStore (cross-host reservation).
- Schema migrations, recovery on restart, policy versioning, receipt integrity (hash chain / signatures).
- Control registry and data-scope primitives: the kernel-side objects a sector pack configures, shipped here so that a pack is configuration rather than code.

Exit: the v0.1 concurrency and mutation standard met against a real Postgres on two hosts under failure injection; a soak of at least one week with no unexplained AMBIGUOUS; a receipt chain tamper test.

Standards: `docs/CONTROL-MAPPING.md` — clause-level mapping of receipt integrity/retention to EU AI Act Art. 12 and SOC 2 CC6/CC7, and of exact-action approval to Art. 14. Each row points at a test. Written only when a design partner asks.

## Sector packs — their own version line

Versioned as `packs-<sector>-MAJOR.MINOR` — `packs-payments-1.0`, `packs-healthcare-1.0` — and never as a kernel version. A pack depends on the v0.6 control registry and data-scope primitives and on nothing after them, which is why the track is listed here; it is not a step in the chain. It appears in no kernel milestone's exit criteria, and v0.7 follows v0.6 whether or not a single pack exists.

**Templates (shipped in v0.2).** `examples/policies/<sector>.yaml`, written against v0.1 primitives only, each headed *"Starting point on the v0.1 kernel. Adapt before use."*

**Full depth (after v0.6).** The same nine sectors — devops, payments, e-commerce, insurance, healthcare, legal, security, government, hr — each pack shipping a control registry, approver roles, data scope, consequence defaults, and worked examples. Each pack is authored in one AI session and reviewed in a separate AI session that did not author it, against the cited public sources for that sector (PCI DSS and PSD2 for payments; HIPAA Security Rule for healthcare; SOX/COSO and maker-checker guidance for finance and insurance; ABA Model Rules for legal; NIST SP 800-53 AC/AU families for security; CIS Kubernetes benchmarks for devops; public-sector records-management rules for government; employment-law basics for hr). The review produces `packs/<sector>/REVIEW.md` listing every control, the source clause it derives from, and each gap or uncertainty found; unresolved gaps stay listed. Each pack README states: *"Authored and reviewed by AI against the cited public sources."* No pack describes itself as compliant with any regulation.

Released individually as `packs/<sector>/`, each when its own review is clean. A pack's major version tracks its own breaking changes — a renamed control, a removed approver role, a narrowed data scope — and never the kernel's; a pack and the kernel it runs on are two independent version numbers, and the compatibility statement is a supported kernel range in the pack's README. A pack never gates a kernel release and is never gated by one: nine packs is a list, not a milestone, and eight unwritten packs do not hold up v0.7.

Standards: none of its own. The sector rule above applies in full.

## v0.7 — Multi-agent

- A2A integration: task-bound delegated authority with limits, expiry, and depth.
- Authority propagation across agent hops.

Standards: none new.

## v0.8–0.9 — Hardening

Fuzzing, property tests, concurrency stress, failure injection, benchmarks, external security review, upgrade testing, compatibility guarantees, CodeQL/SAST/SBOM/signed artifacts.

Standards: none new.

## v1.0 — Stable contracts

1.0 means stable contracts, not feature count: Action schema, Receipt schema, effect semantics, Policy API, StateStore API, Adapter API. MCP production-grade. Authority model documented. Threat model published. Security audit complete. Upgrade path tested.

Standards: external security audit, then an EU controls pack. Phrased as "technical controls supporting a compliance program".

## Beyond v1.0

A management plane — organization-wide policy, approval center, fleet views, central evidence — is not on this roadmap. It gets built only if users pull toward it, and `VISION.md` describes the shape it would take.
