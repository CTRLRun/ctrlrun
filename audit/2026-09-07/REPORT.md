# CtrlRun user-impact bug audit

**Remediation update:** all eight findings below have been addressed in the working tree.
See [the fix verification report](/Users/arpanghoshal/ctrlrun/audit/2026-09-07/FIXES.md).
The findings and original observations below describe the pre-fix checkout; the accompanying
probe scripts now assert the corrected behavior.

Date: 7 September 2026. Reviewed checkout: `f0e9823dc1a7bfc5e8a23410756623e0dd8aa88e`, package version `0.6.0`, Python 3.12.3 on macOS.

**Eight confirmed findings: three High (P1), five Medium (P2).** The most serious finding can permit duplicate execution when an upstream returns a response belonging to another request. I would treat the three High findings as release blockers for the MCP gateway. The remaining findings affect compatibility, error handling, audit evidence, and startup.

At the time of the original audit, production source files had not been changed. The remediation update above links to the subsequent fixes and their verification. No real refunds or external business actions were performed.

## Severity and priority

| ID | Severity | Confirmed problem | User impact |
|---|---|---|---|
| CR-01 | **High / P1** | Upstream response ID is never matched to the request | An unrelated error can make an executed action retryable; the same simulated refund executed twice |
| CR-02 | **High / P1** | Successful SSE responses are parsed as ordinary JSON | Streaming tools return 502 and their effects become ambiguous despite a successful final result |
| CR-03 | **High / P1** | Empty upstream 202 acknowledgements become 502 | Normal MCP notification acknowledgement fails; session initialization and legacy response submission are affected |
| CR-04 | Medium / P2 | Accepted legacy responses require a newer `resultType` field | Successful legacy tool calls are recorded as ambiguous |
| CR-05 | Medium / P2 | MCP GET and DELETE are not implemented | Standalone streams and session cleanup receive HTML 501 responses without reaching the upstream |
| CR-06 | Medium / P2 | Resumption drops approval information from the terminal receipt | The final receipt cannot identify the approval or approver channel that authorized execution |
| CR-07 | Medium / P2 | Synthesized upstream errors use `id: null` | Clients cannot correlate the response to the original non-null request ID |
| CR-08 | Medium / P2 | Accepted IPv6 listen address uses an IPv4 server socket | Starting the gateway on `::1` fails |

P1 means a safety guarantee can fail or a supported integration path is unusable. P2 means a narrower supported flow fails or produces misleading evidence. No P0 issue was confirmed; that is not proof that none exists.

## CR-01 — Match the upstream response ID before changing effect state

**Location:** [server.py:1051](/Users/arpanghoshal/ctrlrun/src/ctrlrun/gateway/server.py:1051), in `httpx_forwarder._observe`.

The forwarder checks the JSON-RPC version and error code but never compares the response `id` with the outgoing request `id`. An unrelated `-32602` response is therefore accepted as evidence that this action did not execute. `classify()` maps that error to `FAILED`, which permits a retry.

**Reproduction:** send request `id=42` for `refund:txn_1`; have a fake upstream record the refund but return a validation error with `id=999`; send the identical request again. With the real gateway HTTP listener and a temporary SQLite database, the result was:

```json
{"remote_effects": 2, "effect_state": "failed", "attempts": 2, "response_ids": [999, 999]}
```

**Impact:** a faulty upstream or proxy returning a response for another request can defeat duplicate prevention. This does require an incorrect upstream response; the reproduction does not show duplicate execution against a correctly correlated response. A wrong-ID success can also falsely mark an action committed.

**Expected:** an absent, invalid, or mismatched response ID is an unreadable/ambiguous outcome, never evidence of non-execution. The retry must remain blocked. This is explicitly required by the repository's [SPEC-v0.2.md:669](/Users/arpanghoshal/ctrlrun/docs/SPEC-v0.2.md:669).

**Fix and regression check:** pass the expected ID into response validation; validate its type and value before classification. Cover wrong-ID errors, wrong-ID successes, absent IDs, and the retry count through real HTTP and durable storage.

## CR-02 — Successful SSE tool responses become ambiguous errors

**Location:** [server.py:1016](/Users/arpanghoshal/ctrlrun/src/ctrlrun/gateway/server.py:1016) and [server.py:1045](/Users/arpanghoshal/ctrlrun/src/ctrlrun/gateway/server.py:1045).

The HTTP client buffers the response with `client.post()`, and `_observe()` parses all of `response.content` with `json.loads()`. There is no live SSE processing path. The helpers in `gateway/legacy.py` are not connected to the forwarder.

**Reproduction:** return HTTP 200, `Content-Type: text/event-stream`, and an SSE message containing a matching `id=42` and a successful `resultType: complete`. The gateway returns **HTTP 502** and stores **AMBIGUOUS**.

**Impact:** a successful consequential operation looks unsuccessful to the user and requires reconciliation or operator resolution. Long-lived streams also cannot deliver progress through this buffered path. The repository promises event-by-event forwarding in [SPEC-v0.2.md:757](/Users/arpanghoshal/ctrlrun/docs/SPEC-v0.2.md:757).

**Fix and regression check:** implement streaming reads and forwarding, matching the final JSON-RPC response to the action before recording its outcome. Test progress delivery, successful completion, early termination, and client cancellation over a real streaming connection.

## CR-03 — Valid 202 acknowledgements are converted to 502

**Location:** [server.py:1049](/Users/arpanghoshal/ctrlrun/src/ctrlrun/gateway/server.py:1049) and [server.py:412](/Users/arpanghoshal/ctrlrun/src/ctrlrun/gateway/server.py:412).

An empty response body cannot parse as JSON, so `_observe()` returns `payload=None`. `_relay()` treats that as a forwarding failure even when the upstream successfully acknowledged a notification with HTTP 202.

**Reproduction:** send a `notifications/initialized` message under accepted revision `2025-11-25`; upstream returns HTTP 202 with an empty body. Client receives **HTTP 502**. No policy refusal occurred.

**Impact:** clients see a server error during a normal initialization acknowledgement. Legacy client-to-server JSON-RPC responses have the same acknowledgement problem. Actual client recovery behavior depends on the SDK, but the incorrect status is confirmed. The intended passthrough behavior is documented in [SPEC-v0.2.md:456](/Users/arpanghoshal/ctrlrun/docs/SPEC-v0.2.md:456).

**Fix and regression check:** preserve the HTTP status, headers, and empty body for successfully completed non-intercepted requests. Distinguish an empty response from a transport failure. Test notifications and legacy response POSTs with real HTTP 202 acknowledgements.

## CR-04 — Legacy success is classified as ambiguous

**Location:** [outcome.py:173](/Users/arpanghoshal/ctrlrun/src/ctrlrun/gateway/outcome.py:173).

The parser accepts the 2025 protocol revisions, but the result classifier requires `resultType: complete` without considering the negotiated revision. Its comment still assumes older servers are rejected.

**Reproduction:** use `MCP-Protocol-Version: 2025-11-25`; return a matching-ID result with `content` and `isError: false`, without `resultType`. The client receives HTTP 200 with the result, while the effect record says **AMBIGUOUS**.

**Impact:** the tool reports success but CtrlRun's evidence and subsequent duplicate handling disagree. Operators must resolve successful legacy calls manually or through reconciliation.

**Fix and regression check:** make response validation revision-aware and normalize successful legacy results before outcome classification. Preserve conservative handling for malformed responses. Test every advertised revision through the actual forwarding path.

## CR-05 — GET and DELETE never reach the upstream

**Location:** [server.py:1100](/Users/arpanghoshal/ctrlrun/src/ctrlrun/gateway/server.py:1100), the HTTP `Handler` class.

Only `do_POST` is implemented. The stdlib therefore supplies its default unsupported-method response for GET and DELETE.

**Reproduction:** send GET and DELETE to `/mcp` on a real local gateway with an accepted legacy revision. Both return **HTTP 501**; forwarded calls remain **zero**.

**Impact:** clients cannot open a standalone stream or terminate a legacy session through the gateway. The repository promises both methods are relayed in [SPEC-v0.2.md:418](/Users/arpanghoshal/ctrlrun/docs/SPEC-v0.2.md:418).

**Fix and regression check:** add method-aware forwarding with the same path/origin controls, and streaming support where required. Check upstream method, session headers, status, and body on both routes.

## CR-06 — Resumed actions lose approval attribution

**Location:** [control.py:986](/Users/arpanghoshal/ctrlrun/src/ctrlrun/control.py:986), especially the `None` approval passed at line 992.

`Control.resume()` calls the outcome writer without the approval consumed on the first leg. The terminal receipt therefore has no approval ID or approver channel, even though an approval authorized the action.

**Reproduction:** request approval for a refund, grant it as `human:alice`, execute until `Suspended`, then resume successfully. The approval is **consumed**, but the committed receipt has **`approval_id=null` and `approver=null`**.

**Impact:** downstream receipt exports and audit consumers lose the direct connection between the consequential action and its authorization. Earlier events still retain evidence; this is not a claim that every trace of the approval disappears. The same probe also showed a zero-second receipt duration after a two-minute suspension because `started_at` is reset on resume.

**Fix and regression check:** persist and restore the original approval reference and timing context across suspension and process restart. Verify the final receipt and exported evidence after one and multiple suspension rounds.

## CR-07 — Transport error replies lose the request ID

**Location:** [server.py:929](/Users/arpanghoshal/ctrlrun/src/ctrlrun/gateway/server.py:929).

Synthesized upstream failures pass `None` to `json_rpc_error()` even though the parsed request ID is known.

**Reproduction:** request `id=42`, simulate a read timeout, and inspect the gateway's `-41010` response: **`id=null`**.

**Impact:** response correlation is broken. Depending on the client, users may see a generic transport/protocol failure or a pending request timeout instead of the actionable CtrlRun error. The local effect remains ambiguous in this probe, so this issue does not itself unlock a retry.

**Fix and regression check:** carry the original request ID into `_upstream_response()`. Test both string and integer IDs on connection failure, unreadable responses, and read timeout.

## CR-08 — IPv6 loopback configuration fails at startup

**Location:** [server.py:1189](/Users/arpanghoshal/ctrlrun/src/ctrlrun/gateway/server.py:1189).

Configuration permits `host="::1"`, but the server inherits the default IPv4 address family.

**Reproduction:** construct the accepted IPv6 configuration and call `build_server()`. On the reviewed machine it raises **`gaierror: [Errno 8] nodename nor servname provided, or not known`**.

**Impact:** users choosing IPv6 loopback cannot start the gateway. This is separate from the MCP operator listener, which has its own implementation and tests.

**Fix and regression check:** select the correct socket family for the accepted host, following the existing operator listener approach where appropriate. Start the actual gateway on each accepted IPv4, IPv6, and hostname configuration.

## Verification and coverage

- Main existing suite: **4,294 passed, 46 skipped, 8 warnings**, in 262.10 seconds with local socket access enabled. The first sandboxed attempt had socket-permission failures; those are environment failures and are not counted as product bugs.
- Ruff lint and formatting: passed. Strict mypy: passed for all 45 source files.
- Six targeted in-process probes reproduced CR-01, CR-02, CR-03, CR-04, CR-06, and CR-07 through the real forwarding/classification code with HTTPX MockTransport.
- Real loopback listener probes reproduced CR-05 and CR-08.
- CR-01 was additionally reproduced end to end using real loopback HTTP, the gateway listener, and a temporary SQLite database.
- Supplemental adapter suite: **76 passed**. Both local adapter source packages were made importable in a temporary isolated Python environment using the existing installed framework dependencies. An initial PYTHONPATH-only attempt passed 74 tests but could not expose the adapters to two tests' isolated subprocesses; the correctly configured rerun passed all 76.
- Supplemental PostgreSQL/cross-host/receipt-chain/recovery suite: **121 passed**, using an isolated temporary PostgreSQL 16.9 instance, which was stopped afterward. These include tests skipped by the main run and some repeated SQLite/in-memory checks, so the numbers should not be added as a unique-test total.
- Evidence: [main suite log](/Users/arpanghoshal/ctrlrun/audit/2026-09-07/existing-tests.log), [adapter log](/Users/arpanghoshal/ctrlrun/audit/2026-09-07/adapter-tests.log), [PostgreSQL log](/Users/arpanghoshal/ctrlrun/audit/2026-09-07/postgres-tests.log), and [probe observations](/Users/arpanghoshal/ctrlrun/audit/2026-09-07/probe-observations.jsonl).

The existing green suite does not cover these complete failure paths. For example, SSE utility code exists without being called by the real forwarder, and the wrong-ID path reaches state changes before any correlation check.

This review combined the full available test suite with focused code inspection and targeted boundary probes. It does not certify that every possible bug is absent. Live customer deployments, production infrastructure, browser documentation interactions, and prolonged load/fault testing were not exercised here.

## Reproduction files

Run from `/Users/arpanghoshal/ctrlrun`:

```bash
.venv/bin/python audit/2026-09-07/reproduce_findings.py
.venv/bin/python audit/2026-09-07/reproduce_listener.py
.venv/bin/python audit/2026-09-07/reproduce_duplicate_wire.py
```

The last two require permission to bind local sockets. All three use fake business effects.
Following remediation, the scripts assert the intended behavior. The original defective
observations remain in `probe-observations.jsonl`, and permanent regression coverage lives in
the test suite described in `FIXES.md`.

**Recommended order:** fix response correlation first; then streaming and acknowledgement handling; then legacy response normalization, method forwarding, and error correlation; then preserve resumed receipt context and repair IPv6 startup. Require these paths to pass through the real listener before treating the gateway as ready for consequential production actions.
