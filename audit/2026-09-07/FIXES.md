# Verification of the eight audit fixes

All eight reported issues are addressed in the working tree. The changes are local; no
release or deployment was performed. The original findings and pre-fix observations remain
in `REPORT.md` and `probe-observations.jsonl`.

## Corrected behavior

| Finding | Fix | Regression evidence |
|---|---|---|
| CR-01: response mismatch permits duplicates | Validate the response envelope and ID before assigning an outcome. Missing, mismatched, or malformed replies stay ambiguous and cannot unlock a retry. | Real HTTP and SQLite probe now records **one** simulated refund, one attempt, and a blocked retry. Tests cover mismatched success and error IDs, missing IDs, and misleading types. |
| CR-02: broken SSE forwarding | Decode SSE incrementally, relay progress immediately, and persist the outcome before returning the matching final result. Strip resumption IDs on intercepted streams. Detect early termination and cancellation; close the upstream connection. | Tests verify progress arrives while the effect is still executing, successful completion commits, early EOF/wrong IDs stay ambiguous, and client cancellation closes an idle upstream stream. UTF-8 and line-ending boundary cases are covered. |
| CR-03: 202 becomes 502 | Separate completed empty HTTP responses from transport failures for non-intercepted traffic. | Notifications and legacy response POSTs preserve HTTP 202, empty bodies, and session headers. |
| CR-04: legacy successes become ambiguous | Normalize missing `resultType` only for accepted legacy revisions before outcome classification. | Successful responses from all three advertised 2025 revisions commit; current-revision responses without the required field remain ambiguous. |
| CR-05: unsupported GET/DELETE | Relay both HTTP methods with path, origin, and protocol-version checks; preserve session and resumption headers. | Real listener tests verify methods, headers, upstream status/body, standalone streaming, and refusal boundaries. |
| CR-06: resumed receipts lose approval | Recover the original attempt's consumed approval and start time from existing durable events, using the action ID rather than selecting a newer grant by hash. | In-memory, reopened SQLite, and reopened PostgreSQL checks preserve approval attribution and timing. Multiple rounds, expired consumed approvals, later unrelated grants, and successful/failed/ambiguous completion are covered. |
| CR-07: errors lose request ID | Carry the original request ID into synthesized upstream failure responses. | String/integer IDs survive transport failures. The original timeout probe now returns request ID 42 instead of null. |
| CR-08: IPv6 startup fails | Select an IPv6 socket for IPv6 listen addresses. | The real gateway binds successfully on `127.0.0.1`, `localhost`, and `::1`. |

The gateway transport implementation is in
[transport.py](/Users/arpanghoshal/ctrlrun/src/ctrlrun/gateway/transport.py), the listener and
error-response changes are in
[server.py](/Users/arpanghoshal/ctrlrun/src/ctrlrun/gateway/server.py), and resumed evidence
recovery is in [control.py:1000](/Users/arpanghoshal/ctrlrun/src/ctrlrun/control.py:1000).
No database schema or StateStore protocol change was required.

## Verification

- Added **66 regression cases**: 53 transport/parser cases, 12 resumption cases, and one
  PostgreSQL reopening case. Existing elicitation fixtures now echo the actual request ID
  instead of always replying with ID 1.
- The focused transport/elicitation run passed 56 tests; the expanded cases are also included
  in the final full check. The focused resumption suite passed 61 tests.
- Supplemental checks passed: **122 PostgreSQL/cross-host/receipt-chain/recovery tests** and
  **76 framework adapter tests**.
- Refreshed generated API source references and claims citations after the code moved.
- Lint, formatting, strict type checking, and whitespace checks passed.
- The final integrated `scripts/check.sh` passed: **4,486 tests passed, zero failures,
  zero skips**, in 315.48 seconds. Both local adapter packages and a fresh temporary
  PostgreSQL 16 instance were enabled. The temporary database was stopped after the run.
  Eight deprecation warnings remain in test helpers and the research framework probe;
  these did not fail the checks. See the [complete verification log](/Users/arpanghoshal/ctrlrun/audit/2026-09-07/final-check.log).

## Re-run the original scenarios

The three audit scripts now assert the fixed outcomes:

```bash
.venv/bin/python audit/2026-09-07/reproduce_findings.py
.venv/bin/python audit/2026-09-07/reproduce_listener.py
.venv/bin/python audit/2026-09-07/reproduce_duplicate_wire.py
```

The last two require local socket access. They use fake effects and a temporary SQLite
database. Saved results: [six corrected probes](/Users/arpanghoshal/ctrlrun/audit/2026-09-07/probe-fixed.jsonl),
[listener checks](/Users/arpanghoshal/ctrlrun/audit/2026-09-07/listener-fixed.jsonl), and
[duplicate prevention over HTTP and SQLite](/Users/arpanghoshal/ctrlrun/audit/2026-09-07/duplicate-fixed.jsonl).

This verifies the eight concrete findings and their regression coverage. It is not a claim
that all possible defects, every deployment environment, or prolonged production load have
been tested.
