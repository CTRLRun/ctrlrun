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

## Out of scope — CTRLRun does not defend against

- A compromised CTRLRun process, host, or Python environment.
- A root attacker or a malicious administrator with write access to the policy file or SQLite database.
- A compromised external service (Stripe lying about outcomes).
- A compromised approver, or social engineering of the approver. CTRLRun proves *what* was approved, not that the human was right.
- Executors that raise `NotExecuted` incorrectly (asserting no side effect when one occurred). This is an integration bug; v0.4 `verify` will include a check for it where reconciliation exists.
- Data exfiltration through *read* actions the policy allows. CTRLRun is not DLP.
- Denial of service by flooding approval requests.
- Bypassing the decorator entirely (calling the raw function). v0.2 gateway mode narrows this; process-level enforcement is out of scope.

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

- Single-host reservation only (SQLite). Multi-host needs Postgres (v0.6).
- Approver identity is free text; no authentication of the approver (v0.3).
- Receipts are not signed; a database admin can alter history (v0.6).
- No reconciliation; AMBIGUOUS always needs a human (v0.2 adds executor `check`).
- The decorator can be bypassed by code that doesn't use it.

## Disclosure

Report vulnerabilities privately to the maintainer (add address before first release). Do not open public issues for security reports.
