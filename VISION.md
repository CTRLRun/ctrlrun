# VISION.md

> **This is not a build spec.** Nothing in this document is implemented, and nothing in it may be implemented before the v0.3 milestone. It exists so the long-term shape is written down once and stops leaking into READMEs, schemas, and PRs. Do not derive tasks from this file.

---

## 1. Thesis

Companies grant authority to humans, applications, and services. AI agents and autonomous workflows are joining that list. They will send, pay, refund, delete, deploy, grant, revoke, approve, submit, purchase, and cancel.

That creates a new infrastructure question:

**How much authority should a machine have over each consequential action — and how do we enforce it, prove it, and recover when execution goes wrong?**

CTRLRun is the enforcement infrastructure between **intention** and **consequence**. Not between prompt and model.

## 2. Two concentric circles

**Circle 1 — the wedge.** Agent executes payment → response lost → agent retries → CTRLRun refuses the blind retry. Narrow. Instantly understood. This is v0.1.

**Circle 2 — the product.** Action-level autonomy infrastructure: for each action, is it authorized, how much autonomy, is approval needed, was *this* action approved, is execution safe, did it already happen, what was the outcome. This is v0.2–v0.5.

CTRLRun is consequence-specific, not industry-specific. If an agent only reads, searches, summarizes, or answers, CTRLRun is low value. It earns its place where an agent has write access to the real world.

## 3. End-state architecture

```
              AI AGENT / AUTOMATION
   OpenAI · LangGraph · ADK · CrewAI · MCP · A2A · custom
                        │
                        ▼
              Integration layer (SDK / adapter / gateway)
                        │
                        ▼
                      ACTION  (canonical)
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
    PRINCIPAL        RESOURCE        CONSEQUENCE
    IDENTITY        + DATA SCOPE       CLASS
        └───────────────┼───────────────┘
                        ▼
                    AUTHORITY   (who may do what, where, how much, until when)
                        ▼
                     CONTROL    (the organizational reason for a restriction)
                        ▼
                      POLICY
          ┌─────────────┼─────────────┐
          ▼             ▼             ▼
        ALLOW     HUMAN OVERSIGHT    DENY
                        ▼
               EXACT-ACTION APPROVAL
                        ▼
                    EFFECT KEY → ATOMIC RESERVATION → EXECUTE
          ┌─────────────┼─────────────┐
          ▼             ▼             ▼
      COMMITTED       FAILED       AMBIGUOUS
          │                            ▼
          │                  RECOVERY (reconcile · human · compensate · safe retry)
          ▼
       RECEIPT → EVIDENCE HISTORY
```

The hard center, which must remain true however the ecosystem evolves: **exact identity → exact approval → effect key → reservation → COMMITTED/FAILED/AMBIGUOUS → no blind retry.** If this kernel is mediocre, nothing built on top of it matters.

## 4. Product surfaces (modules, not brands)

| Surface | Purpose |
|---|---|
| Kernel | Action-level authorization and execution semantics (OSS, never crippled) |
| Gateway | Protect existing MCP/API/tool servers with no agent changes |
| Authority | Delegated machine authority, constraints, attenuation |
| Oversight | Approval workflows: roles, M-of-N, sequential, escalation, separation of duties, break-glass |
| Evidence | Receipts, event ledger, verification, exports (OTel, SIEM) |

## 5. Candidate models (not built; expect them to change on contact with users)

**Authority grant**
```yaml
subject: { agent: refund-agent }
permissions: [stripe.refund]
resources: ["merchant:EU-42"]
constraints: { amount_lte: 5000, currency: [EUR] }
environment: [production]
expires_at: 2026-10-01T18:00:00Z
```
Delegation attenuates, never amplifies: `child ⊆ parent`. Human €100k → finance agent €25k → support agent €2k. A request beyond the chain → DENY, "authority escalation".

**Resource / data scope** — permission over *which* records, not just *which* tool: assigned cases only, permitted data categories, purpose, expiry. This is what makes healthcare, legal, and government workable.

**Consequence taxonomy (candidate)** — OBSERVE · COMMUNICATE · DATA_ACCESS · DATA_DISCLOSURE · DATA_MUTATION · FINANCIAL_EFFECT · PRIVILEGE_CHANGE · ELIGIBILITY_EFFECT · LEGAL_EFFECT · OPERATIONAL_EFFECT · SAFETY_CRITICAL_EFFECT · DESTRUCTIVE_EFFECT. Enables defaults per class. Twelve is probably too many; users will tell us.

**Control registry** — a named organizational reason for a restriction (owner, applies-to consequence, required decision, approver role, version). Receipts reference it, so an auditor can trace *requirement → policy → action → enforcement → oversight → execution → evidence*.

**Recovery** — declarative per-action `on_ambiguous: reconcile` / `on_failure: compensate`. CTRLRun coordinates safety semantics; it never becomes the workflow scheduler. Integrate with Temporal-class runtimes; don't recreate them.

**Verify** — `ctrlrun verify` runs deterministic adversarial scenarios against a real configuration and reports per-guarantee pass/fail with counterexamples. Badge means "declared guarantees pass", never "secure".

## 6. Standards posture

Align, don't invent: OWASP ACS, MCP, A2A, OAuth, OpenTelemetry, and NIST agent identity work. Only define semantics that don't already exist elsewhere — effect states and exact-action binding qualify; identity and tracing don't. Never claim compliance: a standard appears in a mapping doc only after code touches it and a test proves the guarantee.

## 7. Sector packs (templates, not engines)

Finance (payment authority, maker/checker, limits) · Healthcare (PHI disclosure, data scope, case assignment) · Legal (privileged documents, external disclosure, filing/settlement authority) · Insurance (claim authority, payout limits, eligibility) · Security (grant/revoke, credentials, isolation) · DevOps (prod deploy, DB mutation, deletion) · Government (benefits, records, permits). Same kernel, different `ctrlrun.yaml` and control registries.

## 8. What we will never build

LLM hosting · model routing · RAG · vector DB · agent memory · prompt management · agent builder UI · full IAM · SIEM · generic observability · general workflow engine · secrets manager · generic sandbox · generic DLP · agent marketplace · prompt firewall.

Owning consequential agent execution means building everything necessary for that. Not everything adjacent to AI agents.

## 9. How this file is used

- Opened for the first time at v0.3 planning.
- Never cited in a PR description as justification for scope.
- Rewritten only when users contradict it.
