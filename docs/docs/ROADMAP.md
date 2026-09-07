---
title: "Roadmap"
description: "What each version asked and answered, from the v0.1 kernel to v1.0, and what is deliberately not on the list."
---

Dependency-first: every layer depends on the one below being correct. Milestones ship when their tests pass, not on dates. Nothing below v0.1 is in scope for code today.

Standards rule: integrate first, map second, never claim compliance. A standard appears in a mapping doc only after code touches it and a test proves the guarantee.

Sector rule: every pack cites its sources and ships its `REVIEW.md`. No compliance claims.

Track rule: kernel versions ship correctness; the tracks that ship beside it (packs, templates, mappings, adapters) ship on their own cadence and never block or share a version with the kernel.

## v0.1 — Kernel ✅ shipped

Action · Policy (ALLOW/APPROVE/DENY) · `@protect` · exact-action approval (hash, single-use, expiry) · effect key · SQLite atomic reservation · COMMITTED/FAILED/AMBIGUOUS · receipts + events (JSONL) · CLI · `ctrlrun demo` with four scenarios.

Exit: all acceptance tests in `SPEC-v0.1.md §7` pass; demo < 60 s; README literally true.

Standards: none. `THREAT_MODEL.md` is the only compliance-adjacent claim.

## v0.2 — Zero-friction deployment ✅ shipped

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
  `docs/docs/ACS.md` recording where the standard is silent.
- **`Suspended` / `Control.resume`** were not on any milestone. MCP elicitation (§6.9) needs a
  reservation held across a round trip the kernel does not control, and so does an advisory
  hook model like ACS. It is public API now, frozen in `SPEC-v0.2.md` §11.
- **Policy `schema: ctrlrun.policy/v2`** grew out of the gateway rather than being planned:
  a tool call has no decorator to carry an effect template.
- **`EventSink`** replaced the store's file writing, which the v0.1 kernel had owned.

Standards: OpenTelemetry export (code), MCP gateway. The OWASP ACS adapter shipped in v0.2 (see `docs/docs/ACS.md`); "ACS-compatible" is still unearned and waits on an ACS conformance suite to measure against.

## v0.3 — Authority ✅ shipped

- Principal abstraction and `IdentityProvider` interface — `StaticIdentityProvider`,
  `HeaderIdentityProvider` in core, `JWTIdentityProvider` in `ctrlrun[identity]`.
- Authority grants: subject, permitted actions, resource patterns, constraints, environments,
  expiry. Opt-in, then fail-closed.
- Delegation with attenuation: `child ⊆ parent`, at creation **and** at every evaluation;
  escalation → DENY. Omission is rejected rather than inherited.
- Fifth signature demo: authority escalation, plus `examples/authority-escalation/` and
  `examples/authority/`.

**Two things were delivered that this line did not anticipate**, and they are recorded here
rather than left as a surprise in the changelog:

- **Observe mode and `ctrlrun stats`.** An enforcement kernel nobody dares turn on is not
  enforcement, so v0.3 ships the rollout path with the model: `mode: observe` runs every real
  decision and records what *would* have been blocked. It was scoped as build-list item 4 once
  the shape of the authority denial made it obvious that operators would need the numbers
  before they would accept the refusals.
- **`ctrlrun verify` exists as a stub that exits 2.** It runs nothing and claims nothing. It is
  here because observe mode's whole purpose is to lead somewhere, and the command an operator
  reaches for next should not be a `No such command` error that suggests they mistyped. The
  real one is v0.4, below, unchanged.

This is the first point at which `VISION.md` was opened for design input, and only §5's
authority-grant shape was taken from it.

Standards: v0.3 **consumes** identities and issues none — no token minting, no OAuth flow, no
authorization server, no introspection. It verifies a JWT (RFC 7519) against a JWKS (RFC 7517)
with RFC 8725's algorithm and explicit-typing rules applied, and it claims no conformance with
any of them. `--principal-from-client-info` is removed, and `AcsControlHook` gained the same
requirement for the same reason: a self-reported name cannot be an authorization input.

## v0.4 — Verification ✅ shipped

- `ctrlrun verify`: runs the kernel's own failure scenarios against the operator's config and reports pass, fail or **not applicable** per guarantee. Ten of them, `ctrlrun.guarantees/v1`: mutated approval · replayed approval · duplicate effect · concurrent reservation across real OS processes · ambiguous blocks a blind retry · unknown action fail-close · no principal · expired authority · delegation escalation on every dimension including omission · unknown exception is ambiguous, never failed.
- **Not applicable is not a pass.** A guarantee this configuration cannot exercise is reported `N/A` with the reason, excluded from the denominator and listed separately. There is no flag that folds one into the count.
- **Every guarantee carries a positive control.** A refusal asserted against a scenario in which nothing ran passes on a kernel with the guard deleted, so a control that misbehaves is `fail` with `reason: "control failed"` — never a pass, and never an N/A.
- Counterexample output on failure: the ordered events, receipts and effect records that show the violation.
- GitHub Action + badge, "CTRLRun verified N/M", where M is **applicable** guarantees. The badge means *declared guarantees pass*, never "this agent is secure."
- `research/framework-probe/`, outside `src/` and never packaged: what an agent stack does with a lost response when nothing guards the effect. Behaviour, not quality.

Exit: every acceptance test in `SPEC-v0.4.md §8` passes, and every one in v0.1, v0.2 and v0.3 still does. `ctrlrun verify` against `examples/authority/payments.yaml` reports 11/11; against `examples/policies/payments.yaml`, 6/6 with five not applicable — the N/A rule dogfooded rather than described.

Standards: first mapping doc — `docs/docs/OWASP-AGENTIC-TOP10.md`, each guarantee mapped to the OWASP Top 10 for Agentic Applications entries it mitigates, and the four entries CTRLRun does not address listed by name. A reading of somebody else's taxonomy, and it says so on its first line.

## v0.5 — Adapter contract (Released 2026-09-05)

- The adapter contract, documented. It is one of the six contracts v1.0 freezes, so it is written to be lived with.
- Two reference adapters, to prove the contract is real rather than aspirational: OpenAI Agents SDK and LangGraph. Each reuses the framework's own HITL primitives — LangGraph's `interrupt()` and checkpointers, the Agents SDK's tool-approval interruption — mapped onto **`ApprovalRequired` / `with_approval`**, which v0.1 has shipped since the kernel. Never a second approval path beside the framework's own. They ship on the adapters track under their own versions like every other adapter; what v0.5 owns is the requirement that two exist and that the contract survived writing them.

  **This line said `Suspended` / `Control.resume` until `SPEC-v0.5.md` was written, and it was wrong.** `Suspended` exists for the remote asking a question *mid-execution*, where the reservation is already taken and must stay taken; an approval gate has none to hold, because v0.1 consumes the approval in the same transaction as the reservation and a human deliberating for an hour must pin nothing. `SPEC-v0.5.md` §3.1 argues it in full. The correction is recorded here rather than made silently, on the rule `SPEC-v0.4.md` §9.4 set for the threat model's sentence about a check verify could not deliver.
- LangGraph and not LangChain, deliberately. LangGraph owns the primitive the adapter reuses, and LangChain's agent path runs on LangGraph, so one adapter covers both. A separate LangChain adapter would buy only the legacy `AgentExecutor` path.
- An adapter is an entry point, so each one is a `SPEC` §4.3.1 row before it is code: principal validity and expiry, then authority, then policy.

Exit: two adapters pass the v0.1 and v0.3 acceptance suites through the adapter surface; and a third is written against the contract alone, in a session that may not read the kernel, because a contract that only its author can implement is not a contract.

Standards: none new.

## Adapters — their own version line

**Three ways in, and only one of them is an adapter.** `@protect` covers anything running in this process, today, with no adapter and no framework support: a raw OpenAI call or a LangChain tool is a decorated function. The gateway covers anything reaching its tools over MCP, in any language, also with no adapter. An adapter exists for one reason — to route an APPROVE through a framework's own interrupt instead of raising past it — so a framework with no human-in-the-loop primitive of its own has nothing for an adapter to reuse and does not need one. That is the answer to "what about X" for every X, and it is why this list is short rather than growing by one each time a framework is named.

Versioned as `adapters-<framework>-MAJOR.MINOR` — `adapters-crewai-1.0`, `adapters-google-adk-1.0` — and never as a kernel version. An adapter answers to two upstreams and neither is this roadmap: it breaks when its framework makes a breaking release, on that project's schedule, for reasons that have nothing to do with what the kernel is doing. Each adapter's README states a supported kernel range and a supported framework range, and its major version tracks whichever of the two forced the break. Each is reviewed in a session that did not write it, on the rule that covers every adapter already.

**The frameworks.** Every adapter is Python and in-process. OpenAI Agents SDK · LangGraph · Google ADK · Microsoft Agent Framework (Python) · Claude Agent SDK (Python) · PydanticAI · CrewAI · Strands Agents · LlamaIndex. The first two are v0.5's references and arrive with the contract; they are on this track and not in that milestone's version, because an OpenAI or LangGraph breaking release is no more a kernel event than a CrewAI one. Each reuses its framework's own approval and interrupt primitives where they exist and reimplements none of them, ships when its own review is clean, and gates no kernel release. The order they arrive in is demand, not this list.

**Everything else.** The contract, so the community writes the rest.

Standards: none of its own.

## v0.6 — Durable runtime ✅ shipped

- Postgres StateStore (cross-host reservation).
- Schema migrations, recovery on restart, policy versioning, receipt integrity — a hash chain, which detects **alteration** and not authorship.

  **This line said "hash chain / signatures" until `SPEC-v0.6.md` was written, and the slash was the problem.** A chain says the log was not altered after the fact; a signature proves origin, and proving origin brings key generation, rotation and revocation with it — which is *issuing*, and this project verifies what it is handed (`SPEC-v0.3.md` §1.1). Signing is out of scope for v0.6 and `SPEC-v0.6.md` §11 argues it, noting that a chain plus an external timestamp or anchor gets most of the benefit without keys, as a v0.8+ option rather than a gap. The correction is recorded here rather than made silently, on the rule `SPEC-v0.4.md` §9.4 set for the threat model's sentence about a check verify could not deliver.
- Control registry and data-scope primitives: the kernel-side objects a sector pack configures, shipped here so that a pack is configuration rather than code.

Exit: the v0.1 concurrency and mutation standard met against a real Postgres on two hosts under failure injection; a published soak with no unexplained AMBIGUOUS and a positive control that fired; a receipt chain tamper test.

**All three are met, and the third was met by amending it rather than by meeting it as written — which is recorded here rather than quietly rephrased.**

- *The concurrency and mutation standard against a real Postgres* — met, with one narrowing stated in item 4's PR and repeated here: it was run as **separate OS processes against one Postgres, with the connection broken by a proxy the tests own**, not as two hosts, because this build environment has no container runtime. Separate processes give separate connections, no shared memory and no shared file locks, which is what `BEGIN IMMEDIATE` was silently relying on and what a second host removes, so the reservation guarantee is exercised. A **network partition between hosts** is not, and stays unclaimed.
- *The receipt chain tamper test* — met. Six cases, each asserting **which** break was reported and **where**; `SPEC-v0.6.md` §6.5 names all six, and §6.4 says what the chain does not cover before saying what it does.
- *A soak with no unexplained AMBIGUOUS* — met, and **the criterion it is measured against was changed on 2026-09-07, downwards.** It read *at least one week of calendar time*, and the week was removed rather than waited out. `SPEC-v0.6.md` §8.1 carries the amendment in full: what was traded, what it costs, and what did not change. In short — elapsed hours were a proxy for the wrong question, since whether an unattributed `AMBIGUOUS` exists is settled by the injection ledger and the positive control and not by the clock; what a week would have bought is whether anything **accumulates**, and that is now unestablished by anything here and is claimed by nothing. The published run is twenty minutes, 889,735 actions, 0 unattributed, control fired, and the duration is printed everywhere the run is quoted so a reader can discount it. **This is a release gate that was relaxed by the person it was blocking, and the honest version of that sentence is this one rather than its absence.**

Standards: `docs/CONTROL-MAPPING.md` — clause-level mapping of receipt integrity/retention to EU AI Act Art. 12 and SOC 2 CC6/CC7, and of exact-action approval to Art. 14. Each row points at a test. Written only when a design partner asks.

## Sector packs — their own version line

Versioned as `packs-<sector>-MAJOR.MINOR` — `packs-payments-1.0`, `packs-healthcare-1.0` — and never as a kernel version. A pack depends on the v0.6 control registry and data-scope primitives and on nothing after them, which is why the track is listed here; it is not a step in the chain. It appears in no kernel milestone's exit criteria, and v0.7 follows v0.6 whether or not a single pack exists.

**Templates (shipped in v0.2).** `examples/policies/<sector>.yaml`, written against v0.1 primitives only, each headed *"Starting point on the v0.1 kernel. Adapt before use."*

**Full depth (after v0.6).** The same nine sectors — devops, payments, e-commerce, insurance, healthcare, legal, security, government, hr — each pack shipping a control registry, approver roles, data scope, consequence defaults, and worked examples. Each pack is authored in one AI session and reviewed in a separate AI session that did not author it, against the cited public sources for that sector (PCI DSS and PSD2 for payments; HIPAA Security Rule for healthcare; SOX/COSO and maker-checker guidance for finance and insurance; ABA Model Rules for legal; NIST SP 800-53 AC/AU families for security; CIS Kubernetes benchmarks for devops; public-sector records-management rules for government; employment-law basics for hr). The review produces `packs/<sector>/REVIEW.md` listing every control, the source clause it derives from, and each gap or uncertainty found; unresolved gaps stay listed. Each pack README states: *"Authored and reviewed by AI against the cited public sources."* No pack describes itself as compliant with any regulation.

Released individually as `packs/<sector>/`, each when its own review is clean. A pack's major version tracks its own breaking changes — a renamed control, a removed approver role, a narrowed data scope — and never the kernel's; a pack and the kernel it runs on are two independent version numbers, and the compatibility statement is a supported kernel range in the pack's README. A pack never gates a kernel release and is never gated by one: nine packs is a list, not a milestone, and eight unwritten packs do not hold up v0.7.

Standards: none of its own. The sector rule above applies in full.

## The operator MCP server ✅ shipped

Not a kernel milestone, and listed here because a reader will look for it. `ctrlrun mcp-operator`
exposes the operator's read and write commands as MCP tools, so the person who has to answer an
approval can answer it from their own assistant. It gates no kernel release and none gates it,
and it lands in whichever release comes next — it is a subcommand of the `ctrlrun` distribution,
so unlike an adapter it carries no version line of its own. `docs/SPEC-mcp-operator.md` is the
contract and `docs/SPEC-v0.3.md` §4.3.1 carries its two entry-point rows.

It authenticates *who* answered and records it. It does not check that they were entitled to —
that is separation of duties, which is still not built, and the specification says so in the
place a reader would otherwise assume otherwise.

Standards: none new.

## `ctrlrun scan` ✅ shipped

Not a kernel milestone, and listed here because the question it answers had no command.
`ctrlrun scan` reads a Python tree and a policy document and reports the consequential call
sites and policy entries CTRLRun is **not** covering — the gap between *installed* and *in the
path*. `docs/SPEC-scan.md` is the contract; it was written first and its §8 tests were red
before any of it existed.

It gates no release and none gates it, and it lands in whichever release comes next. **It is
not a v0.6 feature**, and lands before that tag no more than the operator server does:
`SPEC-v0.6.md` §9.4's claim is about the surface *that milestone* grew, and a subcommand
arriving before the tag does not retroactively make it one. `test_T177c` holds v0.6's list
frozen and names anything added afterwards separately, which is where `scan` sits. Like the operator server it is a subcommand of the
`ctrlrun` distribution and carries no version line of its own; unlike the operator server it
adds no entry point at all, and `SPEC-scan.md` §9.2 states that as a rule rather than a fact
about the first implementation, because the tempting version of this tool builds an action for
each call site it finds and asks the policy what would happen to it — which would be a principal
invented by a tool from a source file.

The honest half is the load-bearing half: a scanner reports what it found where it looked, and
a clean result is not a verdict. §4 enumerates what it misses by construction — dynamic
dispatch, reachability, anything outside the tree, and a deployment whose protection is entirely
the gateway — and requires that the report say so on every run, including the run with no
findings. A number that improves when the vocabulary is shortened is a number that will be, so
there is no score, no percentage and no badge (§10).

Five sections of the specification carry a paragraph beginning *Found by*, and they are the
record of what writing the tests and running the command changed about the design: the plural
rule that separates `stripe.refunds.create` from `refunds_report`; `execute` dropped from the
vocabulary, because `cursor.execute` was 90 of 208 findings against this repository's own
`src/`; a policy action whose decorator supplies its own effect template no longer reported as
missing one; a call on an expression matched rather than filed as undetermined, which took that
list from 216 entries to 10; and `undetermined` removed from the finding kinds it was listed
among and contradicted by.

Standards: none new.

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
