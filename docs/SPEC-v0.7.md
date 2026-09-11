# CTRLRun v0.7 Specification: Execution boundary

A **delta** over `SPEC-v0.1.md`, `SPEC-v0.2.md`, `SPEC-v0.3.md`, `SPEC-v0.4.md`, `SPEC-v0.5.md` and
`SPEC-v0.6.md`. All six remain binding in full; nothing here relaxes one. Tests are derived from
§8. Public names are frozen in §9. Anything not in this document is out of scope for v0.7.

A reference to an earlier contract is written `v0.1 §5.4` or `v0.6 §7.2`; a bare `§5` is a section
of this document. Section numbers exist in all seven, so the prefix is not decoration.

Words: MUST / MUST NOT / SHOULD are used in the RFC 2119 sense.

v0.6 asked *does it still hold when the process dies, the host goes away, and the database is
somewhere else?* v0.7 asks: **does it hold at the edges the kernel does not control?**

Every guarantee shipped so far is a guarantee about what happens inside CTRLRun. There are three
places where that stops being enough:

- **The kernel does not decide whether the remote acted.** An executor does, by raising
  `NotExecuted` or not, and `v0.1 §5.5` gives the executor that decision with nothing but a
  docstring to defend it.
- **The kernel does not own the clock its leases are measured against**, once the store is on
  another host. v0.6 moved the store so several hosts could share it, and the clock stayed behind.
- **The kernel does not know whether the world still looks the way it did when a human said
  yes.** An approval binds to an `action_hash` and an expiry, and to nothing about the resource.

This document says what the kernel owes at each of those edges. It was written against the code
rather than against the notes that planned it, and §1.4 lists the nine places where the two
disagreed.

---

## 1. Scope

v0.7 delivers five things, one build-list item each, plus a release. The `#` column is the
build-list position.

| # | Deliverable | Ships in | Section |
|---|---|---|---|
| 1 | Clock-skew detection: a measurement, one event type, a verify guarantee, a store conformance case | core; the measurement itself in `ctrlrun[postgres]` | §3 |
| 2 | `ctrlrun.transport`, the `NotExecuted` classifier for `http.client` and `urllib` | core, stdlib; the httpx variant in `ctrlrun[gateway]` | §2 |
| 3 | The provider idempotency token, derived from `(effect_key, attempt)` | core | §4 |
| 4 | The attempt ceiling, `max_attempts`, and the amendment to `v0.1 §5.4` | core | §5 |
| 5 | Precondition fingerprints, rechecked before the reservation | core | §6, §7 |
| 6 | Release 0.7.0 | none | none |

The dependency rule of `v0.2 §1.1` and every release since is unchanged and binding:
`pip install ctrlrun` MUST continue to install `pyyaml` and `click` and nothing else. **Everything
here is core and stdlib** except two pieces that exist only because their client lives in an extra:
the skew measurement, which needs a Postgres connection (`ctrlrun[postgres]`), and the httpx
variant of the classifier (`ctrlrun[gateway]`). `urllib` and `http.client` are stdlib, which is
precisely why the classifier for them belongs in core rather than behind the extra it has lived in
until now.

### 1.1 What this milestone is not, stated before anything else

`v0.4 §1.2`, `v0.5 §1.1` and `v0.6 §1.1` each put the list of non-guarantees before the list of
guarantees. This document does it a fourth time, for the reason the other three gave: the list
matters more than the features do.

- **Not a second outcome vocabulary.** `COMMITTED`, `FAILED` and `AMBIGUOUS` are `v0.1 §5.2`'s three
  outcomes and there is no fourth. The classifier speaks `NotExecuted` or re-raises what it caught
  (§2.2). There is no result enum, no "probably failed", no return code an executor must remember
  to act on.
- **Not a place where `FAILED` may mean something new.** `FAILED` means the thing definitely did
  not happen. A classifier that reported `FAILED` from an exception type, a precondition check that
  wrote `FAILED` for a refusal, or a ceiling that wrote `FAILED` for an attempt that ran would each
  be `v0.6 §1.1`'s forbidden sentence arriving through a new door. The ceiling writes `FAILED` only
  for an attempt whose executor was never called (§5.5), which is the one case where it is true.
- **Not a relaxation flag.** No `assume_failed`, no `optimistic` mode, no `skip_preconditions`, no
  threshold that turns skew detection off, no ceiling of zero that means "unlimited". `v0.4 §3.9`,
  `v0.5 §3.8` and `v0.6 §1.1` forbid a flag that makes the thing being checked differ from the
  thing that ships, and every item here is under the same rule.
- **Not a fence.** A fencing token works only where the resource validates it, and Stripe, an
  SMTP server and the Kubernetes API validate no CTRLRun token. §11 has the whole argument.
- **Not a budget.** A ceiling counts attempts on one effect key; it does not meter authority across
  keys. Budgets are v0.9, and v0.9 uses this milestone's attempt number rather than building one.
- **Not a scope provider.** §6's hook is general precisely so that v0.9 can configure one through
  it. v0.7 ships no ownership fields and no scope semantics.
- **Not a change to how a lease is evaluated.** Skew is observed and reported (§3). `v0.1 §5.3`
  is not amended, and every lease is decided by the same comparison against the same clock as at
  0.6.1.
- **Not a new `StateStore` method.** `StateStore` is frozen by `v0.6 §9.2` and stays frozen. v0.7
  adds a column through the migration runner (§6.11) and a read-only attribute on one concrete
  store that is not part of the protocol (§3.6), and says why the second is not the first.
- **Not a new error type.** The closed set in `errors.py` gains nothing. A precondition mismatch is
  an `ApprovalMismatch` with its own `reason`, a renewal past the ceiling is an `ActionDenied` with
  its own `reason`, and a token asked for outside an executor is an `InvalidArgument`.
- **Not a new entry point, and not a new `Control` method.** Item 5 adds a check to an entry point
  that exists, so `v0.3 §4.3.1` grows a column rather than a row (§7).

### 1.2 The rules of v0.7

Every item is measured against these.

- **The kernel does not decide `FAILED`, an executor does, so the kernel stops leaving that
  decision undefended.** The rule is already written and already implemented, in
  `gateway/outcome.py`, reachable only by installing `ctrlrun[gateway]`. It is promoted to core,
  not rewritten, and the gateway then calls the one implementation (§2.1).
- **A fingerprint narrows a window. It does not close one.** The recheck is a network call, so it
  cannot run inside the atomic reservation write, so a window remains (§6.7). Every sentence about
  it says *narrows*, from §6's first.
- **An idempotency token stable across a renewal defeats the one retry the kernel permits.** It is
  derived from `(effect_key, attempt)`, never from `effect_key` alone (§4.1).

And a fourth, inherited from `v0.1 §5.5` and central here in a way it has never been: **never map
an unknown exception to `FAILED`.** Every previous milestone applied it to code the project owned.
This one applies it to code it does not, which is why item 2 ships a classifier and not a
convention.

### 1.3 What was read

Read on **2026-09-11**.

- `v0.1 §2.3`, `§4.2`, `§5.3`, `§5.4`, `§5.5`, `§7`, `§8`; `v0.2 §6.8`, `§6.10`, `§11`;
  `v0.3 §4.3.1`, `§11`, `§12.2`; `v0.4 §1.2`, `§1.3`, `§2`, `§3.5` to `§3.9`, `§4`; `v0.5 §1.1`, `§9`;
  `v0.6 §1.1`, `§3`, `§6`, `§7.1`, all of `§7.2`, `§8`, `§9`, `§10`, `§11`, `§12`.
- `ROADMAP.md` (in `CTRLRun/ctrlrun-docs`), sections v0.7, v0.8 (the guarantee-ID note), v0.9 and
  v0.11; `ARCHITECTURE.md` §6; `THREAT_MODEL.md`.
- **End to end:** `src/ctrlrun/gateway/outcome.py`, `gateway/transport.py` and the executor in
  `gateway/server.py`; `effect.py`; `control.py`'s `execute`, `_secure`, `_presented`, `_take`,
  `_outcome` and `resume`; `approval.py`; `receipt.py`; `errors.py`; `verify/guarantees.py` and the
  G5 and G10 scenarios in `verify/scenarios.py`; the reservation methods of `state.py` and
  `postgres.py`; `acs.py`'s request and result hooks.
- **Provider documentation**, for the idempotency-key limits §4.2 cites. Each was fetched on
  2026-09-11 and each figure below is quoted from it; nothing else is asserted:
  - Stripe, *Idempotent requests* (`docs.stripe.com/api/idempotent_requests`): keys *"are up to
    255 characters long"*; Stripe saves *"the resulting status code and body of the first request
    made for any given idempotency key, regardless of whether it succeeds or fails. Subsequent
    requests with the same key return the same result, including `500` errors"*; results are
    saved *"only after the execution of an endpoint begins"*, so a request that fails parameter
    validation is not saved; keys may be pruned once they are at least 24 hours old.
  - Adyen, *API idempotency* (`docs.adyen.com/development-resources/api-idempotency/`): the key is
    *"a unique identifier for the message with a maximum of 64 characters"*, valid *"for a period
    of 7 to 14 days after first submission"*.
  - Square, *CreatePayment* reference: `idempotency_key`, minimum length 1, maximum length 45.
  - PayPal, *Idempotency* (`developer.paypal.com/api/rest/reference/idempotency/`): recommends the
    UUID standard for `PayPal-Request-Id` *"because it meets the 38 single-byte character limit"*.
  - RFC 9562 (which obsoletes RFC 4122): name-based UUIDs derived from SHA-256 *"MUST NOT utilize
    UUIDv5 and MUST be within the UUIDv8 space"*; the version is the top four bits of octet 6 and
    the variant the top two bits of octet 8.

  The examples in this repository name four remote systems: Stripe (by far the most often), GitHub,
  Slack and the Kubernetes API. **The last three were not checked for an idempotency header**, and
  this document asserts nothing about whether they have one or what it accepts.

**No compliance, conformance, certification or alignment claim** is made in this document. "Store
conformance suite" names this repository's own acceptance tests, as `v0.6 §2.1` says on its first
line.

### 1.4 What reading the code changed

The build notes that planned this milestone were written from the roadmap and the project's working
notes. Reading the code found nine places where they and it disagree. Each is resolved in the section
named, and none of them is left to be discovered by the item that meets it.

1. **The acceptance tests start at T209, not T182.** `v0.6 §8` ends at T181, and the notes say
   v0.7 continues from there. It cannot: `SPEC-mcp-operator.md` took T182 to T193 and
   `SPEC-scan.md` took T194 to T208, and both are implemented under those names in
   `tests/test_mcp_operator.py` and `tests/test_scan.py`. §8 begins at T209, the first free number.
2. **One human "yes" does not buy unlimited dispatches.** The roadmap's v0.7 bullet says *"one human
   approval plus an executor that always reports 'nothing happened' is unlimited dispatches"*. For
   an `APPROVE` action it is one dispatch: the first reservation consumes the approval, and every
   renewal needs a new one (§5.2). The unbounded case is real and is the `ALLOW` action with an
   effect key, which renews with no human at all. §5 is written against the code's version, and
   item 6 reconciles the roadmap sentence.
3. **On Postgres the attempt number is not yet unique per key.** The renewal `UPDATE` is
   conditioned on `state = 'failed'` and not on the attempt it planned from
   (`postgres.py:787-800`), so under `READ COMMITTED` a renewal planned against attempt *k* can land
   after another process has renewed to *k+1* and failed again, and write *k+1* a second time.
   Through 0.6.1 that was harmless. v0.7 makes the attempt number load-bearing twice, in the token
   and in the ceiling, and a reused number would give two dispatches one token and let a ceiling of
   N admit N+1 executions. Item 4 conditions the `UPDATE` on the attempt as well (§5.6). That is a
   tighter `WHERE` clause inside an existing method, not a new method.
4. **A receipt schema bump breaks every chained receipt unless the rehash rule changes.**
   `Receipt.chain_hash()` recomputes over `to_dict()` (`receipt.py:314`), and `to_dict()` stamps the
   binary's current schema and its current key set (`receipt.py:318-345`). `v0.6 §6.4`'s last bullet
   records exactly this and calls it *"a durable property of the design"*. Adding the `v4` fields
   the ordinary way would report every receipt a released 0.6 wrote as `content_altered`. Item 5
   makes a receipt render under the schema it was written with (§6.11), which amends that bullet.
5. **Verify's network guard refuses loopback.** `v0.4 §3.7` says no scenario opens a socket, and
   T107's guard replaces `socket.socket` outright. G12 needs a loopback peer it controls. The rule
   is amended to *nothing leaves the host* and T107's guard to refuse every non-loopback address
   (§8.9, G12).
6. **Verify and the test suite inject clocks into Postgres stores.** `verify/scenarios.py:553`
   builds every Postgres scratch store with verify's injected clock, which is anchored to the
   document rather than to now, and the Postgres tests pass frozen clocks throughout. Every one of
   those stores will measure a large skew at open, and the measurement will be true. §3.8 says what
   item 1 does about it, which is not to switch the detector off.
7. **`max_attempts` needs `ctrlrun.policy/v5`.** Action-entry key sets are closed and every key
   added since v0.2 has been gated on a schema version (`policy.py:115-127`). The milestone's plan
   lists one policy key and no schema bump; the key cannot ship without one (§5.3).
8. **The gateway raises `NotExecuted` unchained.** `gateway/server.py:616` raises it from the
   token string with no `from`, because the transport has already reduced the exception to an enum.
   The promoted rule chains it (§2.5).
9. **The byte count has to be taken before the call, not after it.** The notes say bytes are
   *"counted per successful call, so a `sendall` that transfers some bytes and then raises counts as
   having written"*. A count taken after a successful call cannot see a call that failed part way,
   and `sendall` reports nothing about partial progress when it raises. The mark is therefore set
   before the first byte is handed over and never cleared (§2.3). That is the notes' intent, and
   the wording is corrected here so the implementation does not follow the words.

---

## 2. The transport classifier

### 2.1 One rule, moved, not copied

`gateway/outcome.py` states the rule, and it is right:

> the connection was never established, so no request byte can have been written; the peer said,
> in band and before dispatch, that it rejected the request; and everything else after the first
> byte is `AMBIGUOUS`.

It is reachable only through `ctrlrun[gateway]`. `@protect` is the surface the README leads with,
and the most safety-critical line its users write is the one that decides between `NotExecuted`
and everything else, which today they write unaided. Map a post-dispatch `ConnectionResetError` to
`NotExecuted` and blind retry is back, with a receipt that says `failed` in a confident voice.

**What moves.** The part of the rule that is about a transport rather than about JSON-RPC moves to
a new core module, `src/ctrlrun/transport.py`:

- the `Transport` enumeration of what a transport observed, with its members and values unchanged
  (`never_connected`, `after_request_sent`, `unreadable_response`, `stream_ended_early`,
  `client_disconnected`);
- **`effect_state(observed: Transport) -> EffectState`**, the one function that decides: `FAILED`
  for `Transport.NEVER_CONNECTED` and `AMBIGUOUS` for every other member. That function is the rule.

**What stays.** `gateway/outcome.py` imports both and keeps what is MCP's: the JSON-RPC codes and
tokens, `PRE_DISPATCH_ERROR_CODES`, the `UpstreamResult` / `UpstreamError` / `UpstreamStatus`
observations, `GatewayOutcome`, and the rule that an HTTP `401`, or a `403` carrying a
`WWW-Authenticate` challenge, is `FAILED`. The notes say the gateway keeps "only what is JSON-RPC";
the `401` rule is not JSON-RPC, and it stays anyway, because it rests on the MCP authorization
specification putting the token check before the method (`v0.2 §6.8`), which HTTP in general does
not do (§2.4). `outcome._transport` calls `transport.effect_state` for the effect and adds the
synthesized code and token around it.

`ctrlrun.gateway.outcome.Transport` remains importable and **is** `ctrlrun.transport.Transport`, the
same object, so nothing that imports the gateway's name changes.

**A test asserts identity, not equality** (T227): the function the gateway's classification path
calls is `ctrlrun.transport.effect_state`, checked by object identity, and the gateway's
`Transport` is the core one. Two implementations that agree today are the drift this item exists
to remove, and a test that compared their outputs would pass right up to the day they stopped
agreeing.

**Rejected: a copy in core.** Two implementations of the one decision the product exists to get
right, with a comment asking whoever edits one to edit the other. **Rejected: core importing
`gateway.outcome`.** It would put a module from an extra in the import path of `import ctrlrun`,
which T30, T92, T125b, T134 and T140f all forbid, and it would point the dependency upward.

### 2.2 The classifier speaks the kernel's existing vocabulary

The classifier raises `NotExecuted`, **chained from the original exception** (`raise NotExecuted(...)
from exc`), only where non-execution is proven by §2.3's two conditions. Everywhere else it
re-raises the original exception untouched, and the kernel already records that `AMBIGUOUS`
(`v0.1 §5.5`). It adds no error type, no third outcome and no return code.

The chaining matters beyond tidiness. `NotExecuted` is the one exception an agent may read as
permission to retry, and a receipt that says `failed` is only as good as the evidence behind it.
`__cause__` is where that evidence travels, and the `NotExecuted` message names the original
exception's type and text, because the message is what `EXECUTION_FAILED.data.error` records.

**Rejected: a result enum the executor acts on**, such as `classify(exc) -> Outcome`. An executor
that called it and forgot to act on the answer would be back to reasoning unaided, with a function
call in the code that looks like it did the reasoning. An exception cannot be forgotten.

### 2.3 What counts as proven, for `http.client` and `urllib`

Two conditions, **both** required, and the classifier claims `NotExecuted` only when it has
observed both:

1. **The classifier opened the connection itself, fresh, for this call.** Not reused, not pooled,
   not supplied by the caller, and not opened by an opener the classifier did not build.
2. **Zero request bytes were handed to the socket**, counted by the classifier's own connection
   *above* TLS: application bytes offered to the socket object `http.client` writes to, after the
   TLS layer where there is one.

**How the count is taken.** The mark is set **immediately before** the first byte is handed to the
socket, and it is never cleared for the life of the connection object. It is set inside the
connection's `send`, after any connection `send` itself opens: `http.client`'s `send` calls
`connect()` when no socket exists yet, and a mark set on entry would precede the connect and make
every connect failure look like a write. A `sendall` that transfers some bytes and then raises is
therefore counted as having written, because the mark was already set, and that is the only honest
count available: `sendall` does not report partial progress when it raises, and `send` accepting
*n* bytes says nothing about whether the peer read them. §1.4 item 9 records why the planning notes'
"counted per successful call" is not the rule.

**Where the claim can originate.** `NotExecuted` is raised from one place: the classifier's
`connect()`, on a connection object whose mark is unset and whose socket its own `connect()`
created, for an `Exception` raised by the `http.client` connect it wraps: name resolution, the TCP
connect, a proxy tunnel, the TLS handshake. DNS failure (`socket.gaierror`), refusal, connect
timeout and a TLS handshake failure all raise there, before `send` has offered anything. The claim
rests on the mark and not on the exception's type, so any `Exception` from that call is covered and
no table of types is kept. An exception raised by the classifier's own code around it, and every
exception raised anywhere else, propagates as it was raised.

**A `BaseException` from `connect()` propagates untouched**, although no byte was offered. A
`KeyboardInterrupt` or `SystemExit` converted into `NotExecuted` would be an interrupt swallowed and
turned into a retry permission, which is the mistake `v0.1 §5.5`'s last paragraph refuses by name.
It reaches `Control` as what it is, and `Control` records it `AMBIGUOUS`: conservative, and never a
claim the classifier did not make.

**Why a TLS handshake failure is `NotExecuted`.** The handshake writes records to the TCP socket,
below the count, and none of them is a request byte: a server whose handshake failed has received
no application data to act on. CPython's `ssl` module exposes no API for TLS 1.3 early data, so no
application byte can leave inside the handshake. A client certificate the server rejects is a
different case, and it lands on the right side by construction: under TLS 1.3 the client learns of
it on its first read, after the request was offered, so it is the original exception.

| What happened | Offered before it | Connection | Answer |
|---|---|---|---|
| DNS failure, refusal, connect timeout | nothing | the classifier's, fresh | `NotExecuted`, chained |
| TLS handshake failure, no proxy tunnel | nothing (handshake records are below the count) | the classifier's, fresh | `NotExecuted`, chained |
| Proxy tunnel refused, or TLS to the target failed, after the `CONNECT` line was sent | the `CONNECT` line | the classifier's | the original exception |
| Reset, broken pipe, read timeout after the request was offered | at least one byte | any | the original exception |
| A `sendall` that raises part way | at least one byte, since the mark came first | any | the original exception |
| Any failure on a connection object that offered bytes for an earlier request | at least one byte | reused | the original exception |
| Any failure on a socket the caller set on the connection | unknown | the caller's | the original exception |
| An HTTP response of any status | at least one byte | any | returned, or raised as `urllib` raises it; never `NotExecuted` (§2.4) |
| An exception before any connection exists: a malformed URL, an unknown scheme | nothing | none | the original exception |
| An exception raised by the classifier's own bookkeeping | any | any | that exception, never `NotExecuted` |

Four rows deserve their argument rather than a cell.

- **The tunnel row is conservative, and deliberately so.** The `CONNECT` line is not the request,
  and a target behind a refused tunnel has received nothing. The count cannot tell a `CONNECT` line
  from a request line without the classifier learning HTTP proxy semantics, and a classifier that
  learned them would be one more place to be wrong. Counting the tunnel line as written loses a
  `NotExecuted` that would have been true; it can never produce one that is false.
- **The reuse row is conservative for the same reason.** A second request on an `http.client`
  connection whose peer closed the idle socket fails on reconnect or on write, and the second
  request's bytes may never have left. The classifier does not try to separate the two, because the
  case `v0.2 §6.8` warns about, a pooled connection that fails on write in a way indistinguishable
  from a request that arrived, is the case it would get wrong.
- **An exception before any connection is `AMBIGUOUS`**, which costs something: a typo in a URL
  leaves the effect for a human. It is the fail-closed direction, the classifier did not observe
  that it opened a connection, and an executor that validates its own arguments before calling out
  may raise `NotExecuted` itself on that evidence, which is then its claim (§2.4).
- **The bookkeeping row exists because a classifier is code.** An exception raised while counting,
  wrapping or deciding is an exception like any other, and it reaches the kernel as one: `AMBIGUOUS`.

**Redirects are not followed.** `urllib`'s default opener follows a `30x` with a second request on
a second connection, after the first request was delivered and answered; a `POST` answered with
`303 See Other` may already have created the thing it redirects to. The classifier's opener has no
redirect handler, so a `30x` comes back as `urllib.error.HTTPError` like any other status, and the
executor decides what it means. **Proxies are honoured**, since the count is taken on the socket
the classifier's connection writes to, whatever it is connected to: an unreachable proxy is a
connection never established, and a proxy that accepted the request and then failed upstream
returns a status.

**Rejected: classifying by exception type.** `ConnectionResetError` arrives both before the peer
read the request and after it acted on it; `TimeoutError` arrives from `connect()` and from `recv()`.
A mapping from type to outcome is a guess with a table in front of it. **If it cannot observe, it
does not claim.**

### 2.4 No HTTP status is ever `NotExecuted` from the classifier

HTTP defines no "rejected before dispatch" answer the way JSON-RPC's pre-dispatch codes do, so
`outcome.py`'s in-band branch has no HTTP analogue. A `400` from one provider is validation that
preceded any side effect, and from another it follows a partial write. A `409`, a `429` and a
`503` each mean different things at different providers, and the classifier knows none of them.

**The executor may still raise `NotExecuted` on its own provider-specific evidence**, exactly as
`v0.1 §5.5` has always let it. The classifier's docstring says so and says what it means: that is
then the executor's claim, not the classifier's, and `THREAT_MODEL.md`'s most dangerous integration
bug, an executor that raises `NotExecuted` after the remote acted, is exactly as possible as it was
yesterday. The classifier makes the transport half of the decision provable; the application half
belongs to the person who knows the provider.

The gateway's `401` / challenged-`403` rule is not a counterexample. It stays in `outcome.py` and
out of `ctrlrun.transport`, because it rests on a statement the MCP authorization specification
makes about where the token check sits (§2.1).

### 2.5 The httpx variant, in `ctrlrun[gateway]`

**What the gateway does today** (`gateway/transport.py:207-208`, `240-246`). An intercepted call is
forwarded with `fresh=True`, which builds a new `httpx.Client` for that call and closes it after
(`owned = fresh or STREAM.get() is not None`). The exceptions are mapped: `httpx.ConnectError` and
`httpx.ConnectTimeout` to `Transport.NEVER_CONNECTED`; the listener's own cancellation to
`CLIENT_DISCONNECTED`; every other `Exception` to `AFTER_REQUEST_SENT`. A `BaseException` that is not
an `Exception` propagates out of the executor, where `Control` records it `AMBIGUOUS`. The non-fresh
path uses a pooled client and maps the same way, but its observation is never recorded as an effect:
it serves relayed and `GET`/`DELETE` traffic only (`server.py:414-421`, `439`).

That mapping is right and is what gets promoted. httpx does not expose a count of request bytes
written after the connection is established, so the variant claims exactly one thing: **the
connection was never established, on a client it built for this call with no connection reuse.**
Where httpx cannot show that zero request bytes were written after connecting, it claims only that.

**The promotion.** `ctrlrun/gateway/transport.py` gains the observation function the forwarder
uses today, private, and one public function built on it:

```python
def request(method: str, url: str, *, content: bytes | None = None,
            headers: Mapping[str, str] | None = None, timeout: float) -> "httpx.Response": ...
```

It builds an `httpx.Client` for the one call, follows no redirects (httpx's default, stated rather
than inherited), sends, reads, closes, and on an exception asks the private observation function
what was observed and `ctrlrun.transport.effect_state` what that records: `NotExecuted` chained from
the httpx exception, or the httpx exception untouched. `HTTPForwarder`'s fresh path calls the same
private observation function, and T226 asserts it by identity. httpx is imported lazily and a
missing extra is `MissingDependency` naming the install line, as every extra is.

**The gateway's executor chains.** `server.py:616` raises `NotExecuted(str(outcome.token or
observed))` with no cause, because the forwarder has already reduced the exception to an enum.
After item 2 the forwarder keeps the exception it observed beside the enum and the executor raises
`from` it, so a gateway `failed` receipt carries the same evidence a `@protect` one does.

**A custom forwarder's observation is its author's claim.** `Gateway` accepts a forwarder other than
`HTTPForwarder`, and such a forwarder returns a `Transport` member the gateway believes. That seam
predates this milestone and is unchanged; it is the executor's `NotExecuted` one level down, and
it is written here so nobody reads §2's rule as covering it.

### 2.6 No parameter changes a classification

Timeouts and TLS contexts pass through to the connection. Nothing the caller sets can widen what
counts as `FAILED`: there is no `assume_failed`, no `optimistic`, no `trust_reused_connections`, no
development setting, and no environment variable. **If a parameter could change a classification,
it does not exist**, and T229 enumerates the public signatures so that adding one is a failing test
before it is a merged one.

### 2.7 What the classifier does not do

- **It does not make an executor correct.** It proves the transport half of `NotExecuted`. An
  executor that catches the classifier's original exception and raises `NotExecuted` anyway has
  made a claim the classifier refused to make.
- **It does not retry.** A `NotExecuted` from the classifier moves the record to `FAILED`, and
  `v0.1 §5.4` then permits the caller's next attempt. The classifier itself sends once.
- **It is not an HTTP client library.** It is `urllib` and `http.client` with a counter and an
  opener, and anything they do not do it does not do. `requests`, `aiohttp` and every other client
  are out of scope (§11); an executor using one applies `v0.1 §5.5` itself, as it does today.

### 2.8 The surface

```python
# ctrlrun.transport: core, stdlib. NOT re-exported at package import.
class Transport(StrEnum): ...                         # moved from gateway/outcome.py, unchanged
def effect_state(observed: Transport) -> EffectState: ...          # the rule, §2.1
class HTTPConnection(http.client.HTTPConnection): ...              # counting, §2.3
class HTTPSConnection(http.client.HTTPSConnection): ...            # counting, above TLS
def urlopen(url: str | urllib.request.Request, data: bytes | None = None, *,
            timeout: float | None = ..., context: ssl.SSLContext | None = None
            ) -> http.client.HTTPResponse: ...                     # urllib-shaped, §2.3

# ctrlrun.gateway.transport: ctrlrun[gateway], lazy
def request(method, url, *, content=None, headers=None, timeout) -> httpx.Response: ...  # §2.5
```

`urlopen` accepts what `urllib.request.urlopen` accepts for `http` and `https` URLs and nothing
else, and its opener carries the proxy, default-error and error-processor handlers and no redirect,
`ftp:`, `file:` or `data:` handler. The two connection classes are drop-in subclasses: a caller who
constructs one and calls `request()` / `getresponse()` gets `http.client`'s behaviour, plus
`NotExecuted` from `connect()` where §2.3 proves it.

`ctrlrun.transport` imports the standard library, `ctrlrun.errors` and `ctrlrun.effect`, and
nothing else (T228). It is not re-exported from `ctrlrun/__init__.py`: an executor imports it by
name, and `import ctrlrun` does not grow a module most callers never use.

---

## 3. Clock skew

### 3.1 What goes wrong, and why nothing names it

`PostgresStateStore` takes `clock: Callable[[], datetime] = _utc_now` and every liveness check goes
through it. A lease is an absolute datetime written by whichever host reserved and read by whichever
host asks. Through v0.5 that was one process and one clock. v0.6 moved the store to another host so
that several hosts could share it, and each of them brought its own clock.

The failure is fail-closed and therefore quiet. A host running ahead of the one that reserved sees a
live lease as expired, `plan_reservation` takes `v0.1 §5.3 E3`'s branch, and the record goes
`AMBIGUOUS` while its real holder is mid-flight and about to succeed. A human then runs
`ctrlrun resolve` on a healthy execution, and nothing anywhere names the cause. A host running
behind sees an expired lease as live and refuses with `DuplicateEffect(state="in_progress")` for
longer than it should, which costs availability and is equally unexplained.

### 3.2 Lease evaluation is unchanged

Item 1 observes and reports. **It changes no decision.** Every lease is compared against the
application clock exactly as at 0.6.1, and T213 asserts that the same live and expired leases are
decided identically with skew present. `v0.1 §5.3` is not amended.

**Rejected: switching liveness to the store's clock.** It would redefine `v0.1 §5.3` for every
backend, change what a lease written by a 0.6 host means to a 0.7 one sharing its store, and move a
correctness decision onto a server round trip on the reservation path. It may be right one day; it
is a change to the kernel's oldest timing rule and is not in this milestone (§11).

### 3.3 Only a store with its own clock is measured

That is `PostgresStateStore`. `SQLiteStateStore` and `InMemoryStateStore` read the application's
clock by construction: their `clock=` parameter is the only clock there is, so there is nothing for
it to diverge from. G13 is `N/A` on them with that reason (§8.9), and the store conformance suite's
new case says so rather than passing vacuously (T214).

### 3.4 The measurement, and why latency is never reported as skew

One round trip: read the application clock (`t0`), run `SELECT clock_timestamp()` on the store's
connection (`s`), read the application clock again (`t1`). Then

- **`midpoint = t0 + (t1 - t0) / 2`**, the best estimate of the application's time when the server
  read its clock;
- **`bound = (t1 - t0) / 2`**, half the observed round trip: the server's reading happened somewhere
  inside `[t0, t1]`, so the true offset lies within `skew ± bound`;
- **`skew = midpoint - s`**: positive means the application clock is **ahead** of the store's.

A measurement is **reported** only where `|skew| > threshold + bound`. A slow link widens `bound`
and so raises the bar exactly as far as the uncertainty it introduced; it cannot manufacture a
report. `clock_timestamp()` and not `now()`, because `now()` is the transaction's start time and a
measurement taken inside a transaction would be off by however long the transaction had run.

**Rejected: comparing against one application reading.** It reports network latency as skew and
fires on every slow link, and a detector that fires always is a detector nobody keeps.

### 3.5 When it is measured

- **At store open, always.** `PostgresStateStore.__init__` measures once, after the migration check,
  on the connection it just opened. This is the measurement that catches a host that came up wrong.
- **On `E3`'s path**: when `_plan` is about to move a record whose lease expired to `AMBIGUOUS`, the
  store re-measures after that write and before raising the refusal. That is the moment skew does
  its harm, and a measurement there puts a stated clock disagreement beside an `AMBIGUOUS` that would
  otherwise have none.
  The refusal is already the slow path, and the extra round trip is spent only there.
- **Never on the ordinary reservation path.** A reservation that reserves, or that meets a live
  lease, a committed record or an ambiguous one, measures nothing. The cost to the happy path is
  zero, and T215 asserts it by counting the store's server-clock reads.
- **`E3`'s re-measurement at most once per `DEFAULT_LEASE`**, per store; the at-open measurement
  does not count against it. A host whose clock is wrong stays wrong, and one skewed host must not
  flood a sink with a report per expired lease. The harm is measured in leases, so the interval is
  one.

**A failed measurement changes nothing.** A measurement that raises is logged on `ctrlrun.postgres`
and leaves the store's retained measurement as it was. It never raises into a store method, never
refuses an open, and never alters a refusal: an observation that could fail the thing it observes
would be a decision.

### 3.6 How it reaches the `EventSink`

The store sits below `Control` and has no sink; `ARCHITECTURE.md` §6 says `postgres.py` must not
know about sinks, and v0.6 §4.3.4's branch reports go to the store's own logger for that reason.
With `StateStore` frozen, the measurement has to reach `Control` some other way.

**Decided: the store retains, `Control` pulls.**

- `PostgresStateStore` keeps its most recent measurement as a **read-only attribute**,
  `clock_skew: ClockSkew | None`, whether or not it exceeded the threshold. `None` means no
  measurement succeeded. It also logs every reported measurement on `ctrlrun.postgres` at `WARNING`.
- `ClockSkew` is a frozen dataclass in `ctrlrun.state` (core): `skew`, `bound` and `threshold` as
  `timedelta`, `measured_at` (the application's midpoint), `trigger` (`"open"` or
  `"lease_expired"`), and the derived `exceeded`.
- `Control` reads `getattr(store, "clock_skew", None)` at the start of every `execute` and `resume`,
  and again immediately after a reservation is refused with `AmbiguousEffect`. Where the value is
  exceeded and is not the measurement it last reported, `Control` appends **`CLOCK_SKEW_DETECTED`**
  through `_append`, so the store writes it and every sink receives it with the store-assigned
  `event_id` (`v0.2 §4.1`). The at-open report carries no `action_id`, like the three `DELEGATION_*`
  types (`v0.3 §7`), because it is about the deployment and not about an action. The `E3` report
  carries the `action_id` and `effect_key` of the attempt whose refusal it accompanies, so a reader
  sees that the record went `AMBIGUOUS` while this host's clock disagreed with the store's. It does
  not say skew was the cause; it puts the fact beside the refusal, where a human resolving it looks.
- `data`: `skew_us`, `bound_us`, `threshold_us` (integer microseconds, exact, so the numbers a reader
  sees are the numbers the decision used), `direction` (`"ahead"` or `"behind"`), `trigger`, and
  `measured_at`.

**Why this is not a new `StateStore` method.** The protocol does not change; `Control` reads the
attribute with a `None` default, and a store without it (SQLite, the in-memory store, any third-party
backend) reports nothing. That is correct rather than convenient: only a store with its own clock has
anything to report, and a third-party store with one may expose the same attribute and be read the
same way. It is a public name on `PostgresStateStore`, and §9 lists it.

**Rejected: a keyword-only constructor argument that `Control` wires**, such as
`on_clock_skew=callable`. The store exists before any `Control` that uses it, so a callback given at
construction can only reach a `Control` through a forwarding shim the operator builds by hand, and
the at-open measurement would fire before the shim had anywhere to forward to. **Rejected: a logger
record `Control` bridges.** The evidence log would then depend on logging configuration: a
`logging.disable`, a filter or `propagate = False` on the `ctrlrun` logger would silently remove an
event from the record, and a handler bridging records to a `Control` is process-wide state shared by
every `Control` in the process, with no way to tell which store a record came from without a key
nobody would keep correct. The at-open record would also be emitted before any `Control` existed to
bridge it. **Rejected: the store appending the event itself.** It would put a second composer of
evidence below `Control`, and events it appended would never reach a sink.

### 3.7 The threshold

**Default: one second** (`DEFAULT_CLOCK_SKEW_THRESHOLD`). **The operator may set it**, as
`PostgresStateStore(..., clock_skew_threshold=timedelta(...))`, to any positive `timedelta` up to
`DEFAULT_LEASE`. Zero, a negative value, a value above `DEFAULT_LEASE` and anything that is not a
`timedelta` are `InvalidArgument` at construction. **No value turns detection off**: the measurement
is taken regardless, and the threshold decides only what is reported.

The argument, against `DEFAULT_LEASE`'s five minutes:

- **Where the harm starts.** A host ahead by *s* sees every lease expire *s* early. The first
  attempt to suffer is one whose work finishes within *s* of its lease, and a lease sized for its
  work has some margin but not a guaranteed one: `v0.1 §5.3` says the right length is a property of
  the work. One second is a third of a percent of the default lease, early enough to name drift
  before it produces its first unexplained `AMBIGUOUS` on any sensibly sized lease.
- **Where the noise is.** A host whose clock is synchronized does not drift by a second, and §3.4's
  bound absorbs the round trip. A report at one second is a statement that a clock is not
  synchronized, which is the condition an operator can fix, and not a statement about latency.
- **Why the ceiling is `DEFAULT_LEASE`.** A threshold above it would stay silent while a
  default-lease reservation was declared `AMBIGUOUS` mid-flight by skew alone, which is the harm the
  detector exists to name. An operator whose actions all carry long leases has no need to tolerate
  more than five minutes of divergence, and one who wants to is asking the detector not to detect.
- **What a false report costs**: one event, rate-limited, and a log line. What a missed one costs: a
  human resolving a healthy effect by hand with no cause on the record. The asymmetry argues for the
  sensitive side of the noise, not the lax one.

### 3.8 Injected clocks, and what item 1 must not do about them

Verify builds its Postgres scratch stores with an injected clock anchored to the document
(`v0.4 §3.6`, `verify/scenarios.py:553`), and the Postgres tests pass frozen clocks. Every such store
measures a skew of weeks or months at open, and **the measurement is true**: those stores' application
clock is not the server's. `Control` will report it, and a test that asserts a complete event sequence
against a Postgres store with an injected clock will see one more event than it did.

Item 1 accounts for that in the tests, by asserting the sequence it meant or by giving the store a
clock aligned with the server where skew is not the subject. **It does not add a way to switch the
detector off**, for a test or for anyone: the rule of §1.1 has no test-only exception, and a detector
with an off switch in the suite is a detector whose suite never saw it run. G13 builds its own clocks
(§8.9).

### 3.9 What it does not do

It corrects no clock, refuses no action, extends and shortens no lease, and makes no statement about
which host is right. It says that two clocks disagree, by how much, within what bound, and when. The
remedy is the operator's, and it is almost always a time daemon.

---

## 4. The provider idempotency token

### 4.1 Why not the effect key

The effect key is stable across `v0.1 §5.4`'s renewal: attempt 2 of `refund:txn_1` has the same key
as attempt 1. Send the key as the provider's idempotency key and the provider sees the one retry the
kernel permits as a duplicate of the attempt that failed.

Stripe documents what happens then: it saves *"the resulting status code and body of the first
request … regardless of whether it succeeds or fails"*, and a later request with the same key gets
the same result back (§1.3). So the retry the kernel admitted *because the executor proved nothing
happened* is answered with the cached failure, and never reaches the provider at all. A safety
mechanism defeating a safety mechanism.

Stripe also documents that it saves nothing where parameters failed validation before execution
began, so on Stripe the replay bites where the failed attempt had reached an endpoint. Other providers
document other rules. **The kernel cannot know which rule a provider follows**, and a token scoped to
the attempt is correct under all of them: a renewal is a new attempt and gets a new token, and a
repeat within one attempt, which is what provider-side deduplication is for, keeps the old one.

### 4.2 The derivation

```text
input  = canonical_bytes({"schema": "ctrlrun.idempotency/v1",
                          "effect_key": effect_key, "attempt": attempt})
digest = SHA-256(input)[0:16]
digest[6] = (digest[6] & 0x0F) | 0x80        # version 8   (RFC 9562 §4.2, §5.8)
digest[8] = (digest[8] & 0x3F) | 0x80        # variant 10  (RFC 9562 §4.1)
token  = the 16 bytes as a lowercase 8-4-4-4-12 hex string, 36 ASCII characters
```

For `("refund:txn_1", 1)` the canonical input is
`{"attempt":1,"effect_key":"refund:txn_1","schema":"ctrlrun.idempotency/v1"}` and the token is
**`382ee448-97da-8107-b674-8c253650d93f`**; for attempt 2 it is
`28bb40af-814c-8fb6-ba63-e8663b1c036d`, and for `("refund:txn_2", 1)` it is
`89977bc9-d128-8ae8-871f-f5265155e60f`. T235 pins the first as a literal, so a change to the
derivation is a red test and not a silent change.

- **Through `canonical_bytes`**, so two hosts agree byte for byte, and the float rejection and the
  lone-surrogate refusal are inherited rather than re-argued (`v0.6 §6.2`). There is one
  canonicalizer and this is not a second one.
- **A versioned domain tag in the input**, `ctrlrun.idempotency/v1`, so a later derivation cannot
  collide with this one and a token can never equal a hash computed over the same pair for another
  purpose.
- **SHA-256, rendered as a UUID of version 8.** RFC 9562 says a name-based UUID derived from SHA-256
  belongs in the version 8 space and not in version 5's. The shape is chosen for the limits §1.3
  verified: 36 characters is inside Stripe's 255, Adyen's 64, Square `CreatePayment`'s 45 and PayPal's
  38, and PayPal recommends the UUID form outright. Keeping 122 bits of the digest leaves collisions
  between two `(effect_key, attempt)` pairs out of practical reach for any single provider account.
- **The effect key does not appear in the token.** Stripe asks callers not to put *"sensitive data
  (for example, email addresses or personal identifiers)"* in idempotency keys, and an effect key is
  built from arguments that often are.

**Rejected: `effect_key` alone** (§4.1). **Rejected: 64 hex characters, or a readable prefix such as
`ctrlrun-v1-`.** Both exceed Adyen's, Square's and PayPal's documented limits, and a token a provider
refuses is a token nobody sends. **Rejected: a random UUID stored on the record.** It would need a new
column and a new store write, and it would not be derivable from a receipt by anyone else.

### 4.3 One accessor

```python
ctrlrun.idempotency_token() -> str
```

It reads a context variable that `Control` sets **for exactly the executor's run**: in `_outcome`,
around `executor()`, and only when the attempt holds its reservation (`held_key is not None`). The
value is the derivation of `(held_key, attempt)`, where `attempt` is the number the store assigned
to this reservation. The zero-argument executor signature does not change, and a 0.6.1 executor runs
untouched (T237).

**Outside an executor it fails closed**, with `InvalidArgument`, because a token invented outside an
attempt identifies nothing. The same refusal applies inside an executor whose action has no effect
key, inside an observe-mode run whose reservation was refused (it holds no attempt, and handing it
attempt 1's token would name the real holder's attempt), and on another thread the executor started
without copying its context: a context variable does not cross a thread unless the caller copies it,
and a missing value is refused rather than guessed.

**Why `InvalidArgument`.** `errors.py` describes it as "an argument cannot be accepted as given" and,
in the same docstring, as the kind of wiring bug "a StateStore transition no record can make, such as
committing an effect nobody reserved". Asking for the token of an attempt that does not exist is that
shape exactly, and `_check_environment` and `with_approval("")` already use it for wiring bugs that are
not a policy saying no. **Rejected: `ActionDenied`**, which an agent loop catches as a policy saying no,
and nothing was proposed. **Rejected: `AmbiguousEffect`**, which would send a human to resolve an effect
that has no record. **Rejected, emphatically: `NotExecuted`**, which an executor that let it propagate
would turn into a `FAILED` record and a permitted retry.

**Why not a keyword argument on the executor.** The executor is called with no arguments, by
`v0.1 §5.5` and by every adapter, gateway and hook that wraps one; an executor that sometimes took a
keyword would be a second calling convention every wrapper had to remember.

### 4.4 Stable within an attempt, changed by a renewal

**A renewal changes it.** `plan_reservation` assigns `record.attempt + 1` on the renewal branch
(`effect.py:207-211`) and the store writes that number (`state.py:1616-1628`,
`postgres.py:787-800`). A new attempt number is a new token, and T232 drives a `FAILED` renewal and
asserts the two differ. That test is the one item 3 exists for.

**`Control.resume` does not change it.** `resume` takes the continuation, rehydrates the action and
calls `_outcome` with `held.record.attempt` (`control.py:992`). `take_continuation` returns the record
unchanged (`state.py:1921-1944`, `_continuable`), and `hold_continuation` moves only the lease and
`updated_at` (`effect.py:419`, `plan_lease_extension`). A resumed leg is the same attempt with the same
number and the same token, which is what a provider that deduplicates across the elicitation round trip
needs (T233).

**The attempt number must be unique per key for this to hold**, and on Postgres it is not yet
(§1.4 item 3). Item 4 fixes it (§5.6); item 3 relies on the fix and T232's Postgres run is the test
that would see it missing.

### 4.5 No receipt field

The token is a pure function of two things every receipt of an attempt that ran already carries:
`effect_key` and `attempt` (`receipt.py:263-264`). It is derivable, so storing it would be a second
copy of a fact that could disagree with the first.

**Which receipts.** A receipt whose `result` is `committed`, `failed` or `ambiguous` in enforce mode
records the attempt that ran, and its `(effect_key, attempt)` names the token that attempt's executor
was given. An `observed` receipt does so where its reservation was held, which its
`would_have.blocked_reason` says: `duplicate`, `in_progress` and `ambiguous` mean it was not. A
`blocked` or `denied` receipt records an attempt that never ran, and no token was ever issued for it.

**The derivation is public**, as `ctrlrun.effect.idempotency_token_for(effect_key: str, attempt: int)
-> str`, so that a receipt can be re-derived by anyone and a `reconcile` hook can ask its provider
about the attempt it is reconciling. The hook's signature is frozen (`v0.2 §11`) and hands it only the
effect key; the attempt is on the record, and the hook reads it there:
`idempotency_token_for(key, store.get_effect(key).attempt)`.

**Rejected: making the accessor answer inside a `reconcile` hook.** The hook runs in two places
(`v0.2 §2.3`). Eagerly, the attempt being reconciled is the one whose executor just ran; blocking, it
is an earlier attempt, the `AMBIGUOUS` one that refused this reservation. A context variable that meant
"this attempt" in one call stack and "some earlier attempt" in the other is a token that names two
different things depending on how it was reached. The function called with the record's attempt names
one.

Nothing here joins item 5's receipt bump: the fields are already there.

### 4.6 What it is for

**A deterministic handle for reconciliation to observe with.** Its value is that an `AMBIGUOUS` effect
can ask the provider "did `(effect_key, attempt)` happen?" by a key the provider already indexes,
without a bespoke lookup hook for each provider. After `AMBIGUOUS` the kernel refuses blind retry, so
provider-side deduplication rarely fires, and that is not what the token is for.

The handle is worth what the provider retains: Stripe may prune a key after 24 hours and Adyen keeps
one for 7 to 14 days (§1.3). A reconciliation that runs after the provider forgot the key learns
nothing from it, and says `unknown`.

**Nothing in any document says the token makes a retry safe.** Reconciliation retries the
*observation*; the token gives it something deterministic to observe with, and gives nothing
permission to act twice (§11).

---

## 5. The attempt ceiling: an amendment to `v0.1 §5.4`

### 5.1 The gap

`grep -rn "max_attempt\|attempt >" src/` returns nothing. `plan_reservation` renews a `FAILED` record
with `attempt = record.attempt + 1` and no bound (`effect.py:207-211`). An executor that raises
`NotExecuted` on every call, on an `ALLOW` action with an effect key, is an unlimited number of
provider dispatches, each recorded as an ordinary retry.

Each renewal is individually *correct*: the executor proved nothing happened. The gap is between
"the only automatic retry", which reads as one, and what the code permits, which is unbounded.

### 5.2 Three sentences, first, because they are what a reviewer checks

**How a renewal after `FAILED` is authorised today.** By the policy decision the new proposal
reaches, and by nothing the first attempt left behind. `plan_reservation` admits the renewal with
`attempt + 1` and consults no approval; the first attempt's approval was consumed in the transaction
that reserved it (`state.py:1553-1556`, `postgres.py:584-587`), so presenting it again is refused by
`check_consumable` with `reason="consumed"` (`approval.py:229-236`), and with nothing presented
`_presented` creates a new request and raises `ApprovalRequired` (`control.py:1620-1648`). **An
`APPROVE` action therefore needs a new granted approval for every renewal, and an `ALLOW` action
renews with no human at all.** The gateway and the ACS hook present the newest granted, unexpired
approval for the action's hash (`v0.2 §6.10`), which a human may have granted several of; each still
buys one dispatch. (This corrects the roadmap, §1.4 item 2.)

**What happens to an approval on a refused attempt.** Where the ceiling is found by §5.5's fast path,
before the approval gate, nothing has been written and a presented approval stays `granted`. Where it
is found by the check after the reservation, the approval was consumed in the same transaction that
assigned the attempt number, and **it stays consumed**: the store has no way to un-consume an
approval, `v0.1 §4.2 A2` is that consumption is single-use and atomic, and v0.7 adds no method. What
is lost is bounded: every later attempt on that key is also over the ceiling, so an unspent approval
would open nothing there until the operator raised it.

**What a crash between the reservation and the release leaves.** The record is `RESERVED`, or
`EXECUTING` if the crash fell after `begin_execution`, under a live lease. When the lease lapses the
next reservation attempt declares it `AMBIGUOUS` (`v0.1 §5.3 E3`), and a human or a `reconcile` hook
must resolve it **although nothing ran**. That is exactly what a crash between the reservation and the
executor call leaves today, and it costs a human, never an execution. The fast path makes it rare,
since only a race reaches the check after the reservation.

### 5.3 The key

```yaml
schema: ctrlrun.policy/v5

actions:
  stripe.refund:
    effect: "refund:{payment_id}"
    max_attempts: 3
    rules:
      - when: { amount_gte: 0, amount_lte: 50000 }
        decision: allow
      - decision: deny
```

**`max_attempts`**, per action, set by the operator: the number of attempts that may **execute** on
one effect key, **the first included**. `max_attempts: 1` means no renewal after `FAILED`;
`max_attempts: 3` means the first attempt and two renewals.

- **An integer, at least 1.** `0`, a negative, a `bool`, a float and a string are `PolicyError` at load,
  naming the key, the action and the line, so a malformed ceiling fails the policy rather than the
  execution (T244). The loader already reads YAML node marks to name the line of a duplicated key
  (`policy.py:539-559`), and the ceiling's refusal uses the same marks. `bool` is refused although
  Python makes it an `int`, on `v0.1 §3.2`'s rule. There is no upper bound: a very large ceiling is
  the operator's statement that they meant it.
- **It requires `schema: ctrlrun.policy/v5`.** Action-entry key sets are closed, and every key added
  since v0.2 has been gated on a schema version so that a document names the reader it needs
  (`v0.3 §12.1`). A `v4` document using `max_attempts` is a `PolicyError` naming the key and `v5`. An
  0.6.1 reader refuses a `v5` document outright, which is the fail-closed direction: a reader that
  ignored the key would renew without a ceiling.
- **It is inside the policy hash.** `_canonical_policy` hashes each action entry whole
  (`policy.py:761-790`), so two documents with different ceilings have different hashes and a
  receipt records which ceiling refused it.
- **`Policy.max_attempts(action_name) -> int | None`** reads it, beside `effect_template` and
  `mcp_options` (`v0.2 §11`).
- **It is not refused on an action with no `effect:` template.** The template may come from the
  decorator, which the policy cannot see (`v0.2 §3.2`). Where a call resolves no effect key there is
  no record to count on, and the first time that happens for an action whose entry declares a ceiling,
  `Control` logs one warning naming the action, as it does for a `reconcile` hook with no key.

**No policy-wide default.** A ceiling is a property of the provider an action calls, of how its
rejections behave and what a retry costs there, and that differs per action. A top-level default would
set a ceiling on actions whose author never considered one, and would be a second place to look when
reading why a renewal was refused. One key, per action. **Not both**, because nothing needs both.

### 5.4 Absent means no ceiling

An action that declares no `max_attempts` renews exactly as at 0.6.1. The roadmap decided it: G15 is
`N/A` with a reason where the configuration names no ceiling, so an operator sees the gap in `verify`
rather than having a number chosen for them (§8.9). **Rejected: a hardcoded default.** Any number would
be arbitrary, and it would refuse at 0.7.0 a renewal that succeeded at 0.6.1, which is a behaviour
change nobody asked for arriving through an upgrade.

### 5.5 Where the decision is taken

**On the attempt number the store assigned.** `reserve_effect` and `consume_approval_and_reserve` are
frozen and cannot carry a ceiling. The reservation already assigns the attempt number in its write,
so `Control` compares the returned `reservation.attempt` against the ceiling **after `_secure` returns
and before `begin_execution`** (`control.py:712-719`). That is the check, and it is the guarantee:
where the attempt number is unique per key (§5.6), at most `max_attempts` reservations can ever carry
a number within the ceiling, whatever the concurrency.

Above the ceiling:

1. **The executor is not called.**
2. **The record is released as `FAILED`**, through `begin_execution` then `fail_effect`, with an
   `error` naming the ceiling: `attempt 4 refused: max_attempts is 3 (SPEC-v0.7 §5)`. `FAILED` is true:
   nothing ran. `begin_execution` here is a state transition that `fail_effect` requires, and
   `EXECUTION_STARTED` is **not** appended, because that event is the claim that something started.
3. **`EFFECT_RESERVATION_REFUSED` is appended** with `data.reason = "attempt_ceiling"`,
   `data.attempt` and `data.max_attempts`, on the existing event type, so the history says why.
4. **A `blocked` receipt** is written, keeping the decision the policy reached (`v0.1 §4.2 A1`'s
   precedent) and carrying the approval where one was consumed.
5. **`ActionDenied(reason="attempt_ceiling")` is raised.**

**Why `ActionDenied`.** It is the only type in the closed set whose meaning is true here: "the action
may not run, and `reason` says why", which is what an operator's ceiling says. `DuplicateEffect` would
tell the caller the effect happened or is happening, and it did not; the gateway would say
`duplicate_effect` to an agent that then believes a refund landed. `AmbiguousEffect` would send a human
to resolve an effect whose outcome is known. `NotExecuted`, which is the one exception an agent reads
as permission to retry, is out of the question. An agent loop's `except ActionDenied` is written for a
policy saying no, and `max_attempts` is a policy saying no. The gateway maps it to `-41001` and the ACS
hook to `deny` with the reason in `codes`, with no change to either.

**Why the receipt is `blocked` while the exception is `ActionDenied`.** The receipt describes what
stopped the attempt, which is the effect's own history, beside `duplicate` and `ambiguous` in
`v0.1 §6.1`'s `blocked`; it keeps the decision the policy actually reached, and a `denied` receipt
would have to record a `deny` the policy never rendered. The exception describes what the caller
should do, which is stop. In observe mode the refusal is recorded as `would_have.blocked_reason =
"attempt_ceiling"`, a new value in `v0.3 §6.3`'s closed vocabulary and a member of `BLOCKED_BY_STATE`,
and the action runs (§5.7).

**The fast path.** Before the approval gate, between policy evaluation and `_secure`
(`control.py:708-712`), `Control` reads the record once: where it is `FAILED` and its `attempt` is
already at or above the ceiling, the call is refused the same way, with nothing reserved, nothing
released and nothing consumed, and **no approval request is created**. It saves a write, it saves a
presented approval from being spent, and it saves a human from being asked about an attempt that could
never run, which `v0.3 §4.3`'s first reason says a denial must never do.

It is a fast path and **never the guarantee**. Two callers who both read attempt N−1 both pass it, and
only the check on the assigned number stops the second. The two defences are independent, so each gets
its own deterministic test with the other defeated (T245, T245b), because two defences against one
failure hide each other's mutations. The two are distinguishable by what they leave: the fast path
writes nothing and the record keeps its attempt number; the check after the reservation leaves the
record `FAILED` at the refused number, with `EFFECT_RESERVED` before the refusal.

**The order of `Control.execute`, amended.** `v0.3 §4.3.1` fixes it as `principal_expired` →
authority → policy → approval → reservation → execution. It becomes `principal_expired` → authority →
policy → **the ceiling's fast path** → approval, **with §6's precondition fetch** → reservation →
**the ceiling's check** → execution.

**Rejected: a new keyword on `reserve_effect`**, because it changes the frozen protocol. **Rejected: a
read before reserving as the only check**, because two callers who both read N−1 both get through,
which is attribution and not prevention. **Rejected: counting receipts or events**, because the record
already carries the number and the store is the only thing that assigns it atomically.

### 5.6 The attempt number must be unique per key, and on Postgres it is not yet

On SQLite the renewal reads and writes inside one `BEGIN IMMEDIATE` (`state.py:1543-1561`), and the
in-memory store under one lock, so no two reservations of one key can carry the same attempt number.
On Postgres the plan is read with a plain `SELECT` under `READ COMMITTED` and the renewal is
`UPDATE … WHERE effect_key = %s AND state = 'failed'` (`postgres.py:787-800`). Between the read and the
`UPDATE`, another process can renew to *k+1*, run, fail and commit, leaving the record `FAILED` again;
the stale `UPDATE` then matches and writes *k+1* a second time.

Nothing in 0.6.1 depended on the number, so this is not a defect there. v0.7 depends on it twice: two
dispatches sharing a number share a token, and a ceiling of N admits one execution more per stale
reader.

**Item 4 conditions the renewal on the attempt it planned from**:
`… WHERE effect_key = %s AND state = 'failed' AND attempt = %s`, with the planned-from number. A stale
renewal then matches no row and is refused exactly as a lost renewal race is refused today
(`postgres.py:802-807`). SQLite's `UPDATE` gains the same condition for defence in depth, where
`BEGIN IMMEDIATE` makes it unreachable, which is why it is worth keeping (the constraint is the last
word, as `state.py:1650-1657` says of the unique index). This is a tighter `WHERE` clause inside an
existing method. It adds no method, changes no signature and changes no decision `plan_reservation`
makes.

T246 opens the window deterministically on Postgres with the proxy the tests own, holding the stale
`UPDATE` until another process has renewed and failed. The store conformance suite states the
property, attempt numbers are never reused on one key, and gains a case for it if its barrier can reach
between a store's read and its write; `v0.6 §2.4` says the in-process case cannot open windows inside a
store, and if that holds here §12 says so and T246 is the only test of the defence.

### 5.7 What the ceiling does not touch

- **`AMBIGUOUS` is untouched.** The ceiling acts only on a reservation the attempt holds, which is
  `RESERVED`; it never reads an `AMBIGUOUS` record as anything, and it can never move one. A record
  moved on while the ceiling was deciding makes `begin_execution` or `fail_effect` refuse, and that
  refusal propagates after a `blocked` receipt, as `v0.1 §5.5` has the store's refusal propagate.
- **An attempt a human or a hook resolved to `FAILED` still counts.** It was dispatched, and its outcome
  was unknown; the ceiling bounds dispatches, and a resolution says the attempt may be followed by
  another, not that it did not happen at the provider's door. An operator who wants more raises
  `max_attempts`, which the policy hash records.
- **`Control.resume` is untouched.** It reserves nothing, so there is no new number to compare.
- **Observe mode records and runs.** The fast path and the check record `attempt_ceiling` in
  `would_have.blocked_reason` and the action executes, because observe mode suppresses CTRLRun's
  decisions and not the record of an effect that happened (`v0.3 §6.2`, `v0.6 §7.2.3`).

### 5.8 The amendment, as it lands in `SPEC-v0.1.md`

`v0.1 §5.4` reads today:

> ### 5.4 Retry rules
>
> When a new action arrives for an `effect_key` that already has a record:
>
> | Existing state | New reservation | Raised |
> |---|---|---|
> | `COMMITTED` | refused | `DuplicateEffect(state=committed)` |
> | `AMBIGUOUS` | refused | `AmbiguousEffect` — human must resolve |
> | `RESERVED` / `EXECUTING` (lease live) | refused | `DuplicateEffect(state=in_progress)` |
> | `RESERVED` / `EXECUTING` (lease expired) | refused; record moved to `AMBIGUOUS` | `AmbiguousEffect` |
> | `FAILED` | **allowed** — new attempt, same key, `attempt += 1` | — |
>
> `FAILED` means the executor *proved* nothing happened (§5.5). That is the only state that permits
> automatic retry.

Item 4 leaves that text as it is and adds, directly beneath it in `SPEC-v0.1.md`, exactly this:

> **Amendment (v0.7, `SPEC-v0.7.md` §5).** The `FAILED` row is bounded where the action's policy entry
> declares `max_attempts` (`schema: ctrlrun.policy/v5`): at most `max_attempts` attempts execute on one
> effect key, the first included.
>
> | Existing state | New reservation | Raised |
> |---|---|---|
> | `FAILED` at attempt *n*, and no `max_attempts`, or *n* + 1 ≤ `max_attempts` | allowed: attempt *n* + 1, same key | none |
> | `FAILED` at attempt *n*, and *n* + 1 > `max_attempts` | refused. Where the record is read before the approval gate, nothing is written. Otherwise the store assigns attempt *n* + 1, and the record is released as `FAILED` without the executor being called | `ActionDenied(reason="attempt_ceiling")` |
>
> The decision is taken on the attempt number the store assigned to the reservation, after the
> reservation and before the executor; a read of the record before the approval gate may refuse the
> same renewal earlier and is never the only check. The refusal appends `EFFECT_RESERVATION_REFUSED`
> with `data.reason = "attempt_ceiling"` and writes a `blocked` receipt. `FAILED` is still the only
> state that permits an automatic retry: the ceiling removes permission from that row and grants none to
> any other.

T251 asserts that the amendment is present in `SPEC-v0.1.md` beneath the unchanged table.

---

## 6. Precondition fingerprints

**A precondition fingerprint narrows the window between a human's decision and the action's
execution; it does not close one.** The recheck is a network call and cannot run inside the atomic
reservation write, so after it compares, the world may still change before the reservation, and after
the reservation, before the executor's request lands. What it takes away is the exposure of human
deliberation, minutes or hours, and what it leaves is the time between a fetch and an effect, which is
milliseconds plus however long the executor takes to reach the provider. That is worth having, and it
is attribution with a narrowed window, not prevention.

### 6.1 The sharp case: the world changed between request and consumption

`v0.1 §4.2` binds an approval to an `action_hash` and an expiry, and **that is right**: a human
approved an action. It binds to nothing about the state of the world the human looked at. A human
approves *delete customer C123* when the balance is zero and the account inactive. Thirty minutes later
the balance is $50,000 and the account is active. The action has not changed, the hash has not changed,
and the approval opens it.

`v0.6 §7.2` decided the case where the *policy* moved between grant and consumption. This section
decides the case where the *world* moved. The operator supplies a provider that reads the state the
decision depends on; the kernel hashes what it returns when the approval is requested, and again when
the approval is presented, and refuses where the two differ.

### 6.2 The mechanism

- **Opt-in.** `@protect(..., preconditions=provider)` and `Control.execute(..., preconditions=
  provider)`, where `provider: Callable[[Action], Mapping[str, Any]]`. Absent means absent: every
  existing caller upgrades untouched, and no approval acquires a fingerprint it did not ask for. A
  `preconditions=` that is not callable is `InvalidArgument`, at decoration time for `@protect`.
- **Hashed through `canonical_bytes`, stored as `sha256:…`.** The fingerprint is
  `"sha256:" + hex(SHA-256(canonical_bytes({"schema": "ctrlrun.precondition/v1", "state": <what the
  provider returned>})))`. The float rejection, the non-string-key refusal and the lone-surrogate
  refusal are inherited, and the domain tag keeps a fingerprint from ever equalling another hash of the
  same mapping.
- **Captured when the approval is requested.** On the pass that creates the request, `Control` calls
  the provider in `_presented`, before `self._approvals.request(...)` (`control.py:1634-1635`), and the
  fingerprint travels to `build_request` through a context variable exactly as `policy_hash` does
  (`v0.6 §7.1`, `approval.py:325-347`). It lands on `ApprovalRequest.precondition_fingerprint`, beside
  `policy_hash`, and is persisted in the new `approvals.precondition_fingerprint` column on SQLite and
  Postgres, and on the object in the in-memory store. `policy_hash` is the precedent, and `v0.6 §7.1`
  already argues request time against grant time: the store has no provider, and giving
  `ctrlrun approve` one would make an unreachable resource a failure of the command a human answers
  with.
- **Rechecked on `Control.execute`'s presenting pass, strictly before the store call that consumes the
  approval.** In `_secure`, after `_presented` returns a presented id (`control.py:1312-1314`) and
  immediately before **each** call to `_take` (`control.py:1331-1333`), whichever of
  `consume_approval_and_reserve`, `consume_approval` or `reserve_effect` it will make. "Each" because
  `_secure` may take twice, once more after a `reconcile` hook moves an `AMBIGUOUS` record
  (`v0.2 §2.3`), and the hook is a network call whose duration would otherwise sit inside the window.

**The ordering is the safety argument.** The fetch runs strictly before the reservation, so a provider
that hangs or raises can only fail closed: nothing reserved, nothing executed, no ambiguity possible.
Move it after the reservation and a *precondition check* becomes capable of producing an ambiguous
effect: the reservation is held, the provider hangs, and nobody knows whether to release it.

| On the presenting pass under `APPROVE` | What happens |
|---|---|
| The approval carries no fingerprint and the call names no provider | unchanged from 0.6.1; the provider question does not arise |
| Both present, and equal | the store call proceeds exactly as at 0.6.1 |
| Both present, and different | **refused**: `ApprovalMismatch(reason="precondition_changed")`; the approval is left `granted` (§6.3) |
| The approval carries a fingerprint and the call names no provider | **refused**: `ApprovalMismatch(reason="precondition_missing")`; left `granted` (§6.4) |
| The call names a provider and the approval carries no fingerprint | **refused**: `ApprovalMismatch(reason="precondition_missing")`; left `granted` (§6.4) |
| The provider raises, returns something that is not a mapping, or returns something the canonicalizer refuses | **refused**: `ApprovalMismatch(reason="precondition_unavailable")`; nothing reserved; left `granted` (§6.5) |
| The approval would be refused anyway: unknown, hash mismatch, consumed, expired, denied | **the provider is not called**; the refusal and its reason are exactly 0.6.1's (§6.6) |

| On the request pass under `APPROVE` | What happens |
|---|---|
| The call names a provider, and it returns a canonicalizable mapping | the request is created carrying the fingerprint; `ApprovalRequired` as today |
| The provider raises, returns a non-mapping, or returns something the canonicalizer refuses | **refused before any request exists**: `ActionDenied(reason="precondition_unavailable")`, a `denied` receipt keeping `decision: approve`, `ACTION_DENIED` with the reason; no human is asked |

Every refusal on the presenting pass appends `APPROVAL_INVALIDATED` with `data.reason` naming which, and
the two fingerprints it compared, hashes only. The three reasons are distinct because a mismatch and an
ordinary `ApprovalMismatch` share a type, and a test that asserted only the type could not tell which
guard fired (`CONTRIBUTING.md`, the first of the four shapes of a false green). Every test of this section asserts the `reason`.

### 6.3 Why a mismatch leaves the approval granted

On `v0.6 §7.2.1`'s precedent, for its three reasons applied to the world rather than to the policy.

- **A human's yes is not spent on a world they did not see.** They approved the action against the
  state they looked at. Consuming the approval would make the operator ask again for an action that
  was refused by a fact, not by the human.
- **The approval authorizes nothing on its own.** It is bound to one `action_hash`, and every
  presentation is rechecked against the world. While the world differs it opens nothing; if the world
  returns to the state the human saw, it opens exactly the action it was granted for.
- **It still expires.** `v0.1 §4.2 A3` checks expiry at consumption, and the refusal is recorded
  against the approval, so the history shows a grant that met a changed world.

The asymmetry with `v0.6 §7.2`'s `ALLOW` row is the same one that section draws: the `ALLOW` row
spends a token that would otherwise outlive an action that ran; this row keeps one for an action that
did not.

### 6.4 Why a fingerprint on only one side is a refusal and never a skip

**A request created with a fingerprint whose approval comes back from the store without one** is a
mismatch. The reachable causes are ordinary: a store that does not persist the new column, a database
restored from before the migration, or a third-party `ApprovalProvider` that constructs its
`ApprovalRequest` itself rather than through `build_request` and so never records one (the residual
`approval.py:334-336` states for `policy_hash`, which there reads as "not recorded" and here is
refused). In every one of them "skip" would mean **a store that drops the column turns the check
off**, which is a check that fails open on the path nobody tests.

**An approval carrying a fingerprint presented by a call that names no provider** is a mismatch too.
The reachable case is the gateway and the ACS hook, which present the newest granted approval for an
action's hash (`v0.2 §6.10`) and name no provider, because the policy document cannot carry a Python
callable. An approval requested by `@protect(preconditions=...)` for the same hash was granted against a
world state that such a path has no way to recheck. Refusing leaves the approval `granted` for the path
that can.

Both refusals share `precondition_missing`, and are told apart by the event's two fields, one of which
is null.

### 6.5 Why a provider that fails refuses the action, with its own reason

A provider that raises an `Exception`, returns a value that is not a `Mapping`, or returns one that
`canonical_bytes` refuses (a float at any depth, a non-string key, a lone surrogate) has not produced
a fingerprint, so the comparison cannot be made, and **a check that cannot be made is not a check that
passed** (`v0.4 §3.8`). The action is refused with nothing reserved and nothing executed.

`precondition_unavailable` is distinct from `precondition_changed` because the remedies are: one is a
world that moved, which a human looks at; the other is a provider that is down or wrong, which an
operator fixes. The provider's exception is recorded in the event's `error` **by its type name
only**: a provider that put the balance it read into its exception message would otherwise carry raw
state into the evidence through the one field nobody thought to check. What the provider logs for
itself is the operator's; nothing CTRLRun writes carries the message or the return value.

**A provider that hangs** holds the call and nothing else. There is no timeout parameter: a timeout
that fired would have to decide something, the only decision available is refusal, and a provider can
refuse by raising after its own timeout, which a provider doing I/O needs anyway. While it hangs nothing
is reserved, so the cost is availability and never ambiguity, and that is the safety argument of §6.2
doing its work. A `BaseException` that is not an `Exception` (`KeyboardInterrupt`, `SystemExit`)
propagates untouched, with nothing reserved and no receipt, as it does from `_presented`'s provider call
today.

### 6.6 Why the provider is not called for an approval that would be refused anyway

Before calling the provider, `Control` reads the approval record it is about to present
(`get_approval`, an existing read) and applies `check_consumable`, the same pure function every store
applies. Where that verdict is a refusal, the provider is not called and `_take` raises the refusal it
raises today, with the reason it gives today.

Two reasons. **Every existing reason survives unchanged:** T2's `mismatch`, T4's `consumed` and T5's
`expired` would otherwise become `precondition_changed` whenever the world had also moved, and
`v0.1 §7` T4 already fixes that the approval check's own error is the one raised. **And the provider
is spent only where its answer can matter**, which is a courtesy to the resource it reads. Nothing is
skipped by this: an approval `check_consumable` refuses is refused by the store as well, with nothing
consumed.

### 6.7 The residual window, stated

The recheck **narrows** the window from the whole of human deliberation down to the span between the
provider's fetch and the effect. That span has three segments, and the recheck acts on none of them:

1. **Fetch to compare.** The provider reads the resource; the comparison runs on what it read. A change
   after the read and before the compare is invisible to it.
2. **Compare to reserve.** The comparison has passed; `consume_approval_and_reserve` has not yet run. A
   change here is not refused. **T261b opens exactly this window and asserts the action is not
   refused**, and its name and docstring say it is the residual this section documents.
3. **Reserve to effect.** The reservation is held and the executor is on its way to the provider. The
   world can move until the provider applies the request.

**Rejected: folding the fingerprint into the stored `action_hash`,** so that the store's atomic
comparison would compare it. It would change what a frozen column means, break `find_granted_approval`
and `approvals_for`, which look approvals up by the action's own hash, and still narrow nothing further:
the atomic comparison would compare a fingerprint fetched before the transaction, so segments 1 and 2
remain and segment 3 is untouched. **Rejected: rechecking after the reservation.** It would make the
check capable of producing an ambiguous effect (§6.2). **Rejected: rechecking inside the executor.** That
is the executor's business and always was; the kernel's recheck exists because a human's approval is the
kernel's to honour.

**What lies beyond the kernel's reach.** A resource that accepts a conditional write, an `If-Match` on a
version or a compare-and-swap on a balance, can refuse a stale request itself, at the one point where the
state and the write meet. Whether an executor sends one is the executor's choice and the provider's
feature. CTRLRun does not do it and does not claim it, and nothing in this section's recheck substitutes
for it.

### 6.8 Where this binds, and where it does not

- **`APPROVE` on `Control.execute`'s presenting pass, and nowhere else.** That is the only place a
  decision made earlier by a human is being turned into an effect now.
- **Not `ALLOW`.** There is no decision-to-execution gap: the policy decides and the action runs in the
  same call, so there is no earlier state to compare against. T258 counts the provider's calls and
  asserts none. A presented approval on an `ALLOW` action is spent by `v0.6 §7.2.2`'s path without a
  recheck, because nothing it could say would change what runs.
- **Not `DENY`.** Nothing runs.
- **Not `Control.resume`**, for `v0.6 §7.2.3`'s reason: refusing there strands a reservation held open
  across a round trip the remote may already be acting on, which `v0.2 §6.9.2` forbids. The resumed leg's
  approval was consumed on the first leg, and T259 asserts the provider is never called.
- **Observe mode rechecks and records.** Where an approval is presented under `APPROVE`, observe mode
  calls the provider where enforce mode would, records a failure as `APPROVAL_INVALIDATED` with the
  precondition reason and `would_have.blocked_reason = "approval_mismatch"`, and runs, spending no grant
  (`v0.6 §7.2.3`). Observe mode creates no request (`v0.3 §6.2`), so its request pass never fetches.

§7 gives every `v0.3 §4.3.1` row its answer.

### 6.9 A general hook, not a balance check

The provider is fetched strictly before the reservation, hashed, and fail-closed, and it is given the
whole `Action`, principal and resource included. That shape is deliberate: v0.9's scope providers are *"a
resource-ownership precondition through v0.7's fingerprint mechanism"*, and they configure this hook
rather than adding a second one. **v0.7 builds no scope semantics**: no ownership fields, no principal
comparison, no notion of which records belong to whom. What the provider reads, and why, is the
operator's.

### 6.10 What never reaches the evidence

**Raw resource state never reaches a receipt, an event, a log line or the approvals table.** Balances,
account states and PHI stay out of the evidence; only the fingerprint does. The provider's return value
exists in memory for as long as it takes to canonicalize and hash it, and T260 searches every written row,
every JSONL line and every captured log record for a sentinel value the provider returned.

The fingerprint goes to three places, all of them hashes: `approvals.precondition_fingerprint`,
`APPROVAL_INVALIDATED`'s data on a refusal, and the receipt's two fields (§6.11). It is not added to the
webhook document (`ctrlrun.approval_request/v1` is unchanged), to `ctrlrun inspect`, or to anything a
human is shown to decide with: a hash tells a human nothing about the world they are approving, and the
human reads the world in their own systems.

### 6.11 `ctrlrun.receipt/v4`, the migration, and the rehash rule

**Two receipt fields**, `precondition_at_request` and `precondition_at_recheck`: the approval's stored
fingerprint and the one computed on the presenting pass, `null` where there was none. On a refusal they
say which side moved or was missing; on a committed action they are equal, and the receipt records that
the world was checked. The schema becomes **`ctrlrun.receipt/v4`**, and it moves once, in item 5, which
is why item 5 is last.

**One column**, through migration **`0005_precondition_fingerprint`**: `approvals.precondition_fingerprint
TEXT NULL` on SQLite, `COLLATE "C"` on Postgres as `0004_policy_provenance`'s column is. It is a column and
not a field in some JSON because `ApprovalRecord` is rebuilt from columns (`v0.3 §5.2`, `v0.6 §3.7`).
Existing rows keep `NULL`, which is exactly what an approval requested without a provider means. The
migration is forward-only, runs in one transaction, and is proved against a database **built by 0.6.1's
own code** (`v0.6 §3.5`), in both directions: 0.7 opens and migrates it keeping every row; 0.6.1 refuses the
migrated database at open with `SchemaMismatch` naming `0005` and both versions (T264). The runner is why a
column is possible at all, and that makes the change safe, not cheap.

**The rehash rule, which amends `v0.6 §6.4`'s last bullet.** `chain_hash()` recomputes over `to_dict()`,
and `to_dict()` stamps the current schema and the current key set (`receipt.py:314`, `318-345`). A `v4`
binary rendering a `v3` receipt as `v4` would recompute a different document and report every receipt a
released 0.6 wrote as `content_altered`. So, from item 5:

- `Receipt` carries **`schema`**, the schema string of the document it was read from, and a receipt this
  binary writes is `ctrlrun.receipt/v4`.
- `to_dict()` renders **the key set of that schema**. A `v4` receipt renders `v3`'s keys plus the two
  precondition fields under `"schema": "ctrlrun.receipt/v4"`. Every other schema this binary knows,
  `v1`, `v2` and `v3`, renders exactly as 0.6.1 renders it, which for a `v3` receipt is the document it
  was written as, byte for byte. `v1` and `v2` receipts carry no `seq` and are never rehashed (`unchained`,
  `v0.6 §6.5`); they keep 0.6.1's rendering so that nothing a reader already sees changes.
- `Receipt.from_dict` refuses a document whose `schema` is absent or is not one of the four, with
  `InvalidArgument` naming it. A document that does not say which rule hashes it cannot be rehashed by
  any rule, and rendering it under this binary's would be a guess.

**Every reader upgrades before any writer switches** (`v0.3 §12.2`). The chain walk, `ctrlrun receipts
--verify-chain` and `ctrlrun verify` read `v3` and `v4`, and **a chain spanning both verifies end to end**
(T265). An 0.6.1 process never meets a `v4` receipt in a store, because it refuses the migrated database
at open; a `v4` JSONL line handed to one parses and rehashes wrongly, which is the reason the rule exists.
What `v0.6 §6.4` records for databases written between that milestone's items 6 and 7 is unchanged: those
receipts say `v3` and lack keys `v3` has, and they were never released.

v0.11's broader rule, that a receipt whose schema the binary does not know is named rather than reported
as a break, stays v0.11's. v0.7 needs only the two shapes it writes and reads, and proves them.

---

## 7. The `v0.3 §4.3.1` column: does it recheck preconditions?

v0.7 adds no entry point. It adds a check to one, so the table grows a column rather than a row. Every
existing row answers it, including the rows whose answer is no, because a missing enumeration is how
this project's worst hole arrived (`v0.3 §4.3.1`).

| Entry point | Captures a fingerprint at request | Rechecks at presentation | Why |
|---|---|---|---|
| `@protect` → `Control.execute` | yes, where the decorator names `preconditions=` | yes, under `APPROVE`, before each `_take`; refuses a fingerprinted approval where it names no provider | the only place a human's earlier decision becomes an effect now (§6.8) |
| `Control.execute` called directly | yes, where the call passes `preconditions=` | yes, as above | the same method; the keyword is how a direct caller names a provider |
| `Control.evaluate` | no | **no** | it decides and writes nothing; no approval is consumed, so there is nothing to bind a fingerprint to, and a provider call from a read-only query would give it I/O it has never had |
| `Control.resume` | no | **no** | `v0.6 §7.2.3`: refusing strands a reservation the remote may be acting on; the approval was consumed on the first leg |
| `Control.delegate` / `Control.revoke` | no | **no** | they create and remove authority and consume no approval |
| The gateway's `tools/call` | no | **no provider**, and it **refuses** a presented approval that carries a fingerprint (`precondition_missing`) | it goes through `Control.execute`, and a policy document cannot name a Python callable; an approval requested with a fingerprint was granted against a world this path cannot recheck (§6.4) |
| `ctrlrun.acs`'s request hook | no | **no provider**, and refuses a fingerprinted approval, as the gateway | the same shape and the same reason; the platform executes after the hook answers, so its window is wider still, which is a reason to refuse rather than to skip |
| `ctrlrun.verify.run` | informational | informational | it drives the first two rows with its own provider for G16 (§8.9) |
| An adapter's protected tool → `@protect` → `Control.execute` | yes, where the decorator names one; `InterruptApprovalProvider` builds its requests through `build_request`, so the fingerprint is recorded | yes | the `@protect` row reached through a framework (`v0.5 §4.1`) |
| `ctrlrun.adapter.needs_approval` → `Control.evaluate` | no | **no** | `Control.evaluate`'s reason; it writes nothing and consumes nothing |
| `ctrlrun.adapter.InterruptApprovalProvider.wait` → `grant_approval` / `deny_approval` | no; the request already exists | **no** | it records an answer; the fingerprint was fixed when the request was created, and `Control.execute` rechecks in full before consuming (`v0.5 §4.1`) |
| `ctrlrun mcp-operator`'s read tools | no | **no** | they read |
| `ctrlrun mcp-operator`'s write tools → `grant_approval` / `deny_approval` / `resolve_effect` | no | **no** | a grant authorizes nothing on its own (`v0.5 §4.1`, `SPEC-mcp-operator.md` §4.3); the recheck is at consumption, and `resolve_effect` touches no approval |

Two paths that are not rows answer it as well, for the same reason as the operator server's write tools:
**`ctrlrun approve` and `ctrlrun deny`**, and **`WebhookApprovalProvider`'s callback**, record an answer to a
request whose fingerprint already exists, and consume nothing.

**`v0.3 §4.3.1`'s order** is amended as §5.5 states, and the new column is recorded there by item 5, in the
same commit as the code.

---

## 8. Acceptance tests

Each MUST exist as a pytest test with the given ID in its name. All MUST pass for v0.7, and every test of
`v0.1 §7`, `v0.2 §10`, `v0.3 §10`, `v0.4 §8`, `v0.5 §8`, `v0.6 §8`, `SPEC-mcp-operator.md` and
`SPEC-scan.md` MUST still pass. **Numbering starts at T209** (§1.4 item 1).

A negative test states its precondition: a test asserting a refusal also asserts that the thing it forbids
would otherwise have happened, or it is a test against behaviour the library refuses anyway
(`CONTRIBUTING.md`, the third of the four shapes of a false green). Every wait is bounded.

### 8.1 Item 1: Clock skew (§3)

#### T209: An application clock ahead of the store's is named, with its bound
A `PostgresStateStore` whose injected application clock runs ahead of the server's by the threshold plus
five seconds. `clock_skew.exceeded` is true, `skew` is positive, `bound` is at most half the round trip the
test measured, and the first `Control.execute` against the store appends `CLOCK_SKEW_DETECTED` with
`direction: "ahead"`, `trigger: "open"`, `skew_us`, `bound_us`, `threshold_us`, and no `action_id`.

#### T210: The same, behind
The injected clock behind by the threshold plus five seconds: `direction: "behind"`, negative `skew`.

#### T211: The positive control, the real clock, is silent
The real clock against a server on the same host. A measurement **is present** (`clock_skew` is not `None`,
so the detector ran) and **no** `CLOCK_SKEW_DETECTED` is appended. Without the first half, a detector that
never ran would pass the second.

#### T212: Latency alone is never reported
An application clock that agrees with the server at the midpoint but advances by twice the threshold
between the two reads around `clock_timestamp()`, which is a round trip of that length and no skew.
Nothing is reported, and the retained measurement's `bound` is half that round trip.

#### T213: Lease evaluation is byte-for-byte unchanged
With skew present and reported, a set of live and expired leases is reserved against and each is decided
exactly as at 0.6.1: the same grants, the same `DuplicateEffect(state="in_progress")` refusals, the same
`AmbiguousEffect` refusals, the same records afterwards. The expected values are computed by
`plan_reservation` with the application clock, not by the store under test.

#### T214: The store conformance suite's skew case
Graded against Postgres: an injected skew is reported and an aligned clock is not. On SQLite and the
in-memory store it is `not_applicable` with the reason *this backend exposes no clock measurement; SQLite
and the in-memory store read only the application's clock and have none to expose*, a sentence true of
every backend that reaches it, a third-party store with a clock it does not expose included. `v0.6 §8`
T141's "no other N/A is accepted" is amended to accept this one, in the same commit, and §9.6 records the
amendment.

#### T215: When it measures, and when it does not
Server-clock reads are counted. One at open. One on a reservation that meets an expired lease and declares
it `AMBIGUOUS`, and the `CLOCK_SKEW_DETECTED` that follows carries that attempt's `action_id`,
`effect_key` and `trigger: "lease_expired"`. **None** on a reservation that is granted, renewed, or refused
for a live lease, a committed record or an ambiguous one. A second expired lease within `DEFAULT_LEASE`
of the first re-measures nothing.

#### T216: A failed measurement changes nothing
The measurement query is made to raise, at open and on the `E3` path. The store opens; the reservation's
refusal is the same `AmbiguousEffect` with the same record written; nothing is raised that 0.6.1 did not
raise; a log record names the failure.

#### T217: The event reaches every sink, through `Control`, with the store's id
A recording sink receives `CLOCK_SKEW_DETECTED` with the `event_id` the store assigned, and the store's
`events()` holds the same event. A second `execute` against the same measurement appends nothing.

#### T218: The threshold refuses what it must
`clock_skew_threshold` of zero, a negative, a value above `DEFAULT_LEASE`, `None`, a `bool` and an `int` are
each `InvalidArgument` at construction. No accepted value stops the measurement from being taken.

#### T219: G13 in verify
§8.9's G13 entry, graded against `--store-url postgresql://…`, `N/A` with its sentence otherwise, with its
control asserted by `v0.4` T125's standard: a detector that fires always fails, and one that never fires
fails.

### 8.2 Item 2: `ctrlrun.transport` (§2)

#### T220: A byte written and the peer killed is `AMBIGUOUS`, never `FAILED`
**The test this item exists for.** A real loopback server accepts, reads at least one request byte, and is
killed (the socket closed with `SO_LINGER` zero, so the client sees a reset). **The test asserts the server
received the byte** before it asserts anything else. Through `@protect` with an effect key, using
`ctrlrun.transport.urlopen` and, separately, `HTTPConnection`: the exception the caller sees is not
`NotExecuted`, the receipt is `ambiguous`, the record is `AMBIGUOUS`, and a retry is refused.

#### T221: Refused, DNS failure, connect timeout, TLS handshake failure are `NotExecuted`
Each against a real target where one can be made: a loopback port bound and not listening; a
connect that times out, bounded (a full loopback backlog where the platform drops rather than refuses,
and the test states which mechanism it used, because platforms differ); a loopback TLS server presenting
a certificate the client's context rejects. DNS failure is the one case a test cannot produce reliably
without a network, so `socket.getaddrinfo` is made to raise `socket.gaierror` for the test's host name,
which reproduces the path exactly: the exception leaves `connect()` before any byte is offered, and §2.3's
claim rests on that and not on the type. Each: `NotExecuted` whose `__cause__` is the original
exception, the record `FAILED`, a retry admitted. **Each asserts its precondition**: the server side saw
zero application bytes.

#### T222: A `sendall` that raises part way is `AMBIGUOUS`
A peer with a small receive buffer that reads nothing and then closes, so `sendall` of a large body
transfers some bytes and raises. The test asserts the peer's socket received at least one byte, and that
the answer is the original exception.

#### T223: A connection the classifier did not open never claims
A socket the caller set on an `HTTPConnection`; a connection reused for a second request after the first
succeeded and the server closed it; an opener the test built with `urllib` handlers of its own. Whatever
each raises, it is never `NotExecuted`.

#### T224: No HTTP status is `NotExecuted`
Responses of 301, 303, 400, 401, 409, 429, 500 and 503: none becomes `NotExecuted`; the `30x` is not
followed (the server counts one request); `urllib` raises `HTTPError` as it would.

#### T225: `urllib` and `http.client` both, proxies included
Every case of T220 to T224 through both surfaces. Through a loopback HTTP proxy the test owns: an
unreachable proxy is `NotExecuted`; a refused `CONNECT` after the line was sent is the original exception.

#### T226: The httpx variant, and the gateway uses it
`ctrlrun.gateway.transport.request`: a refused connection is `NotExecuted` chained from `httpx.ConnectError`;
a reset after the request is the httpx exception; a pooled or caller-supplied client cannot be passed at all.
`HTTPForwarder`'s fresh path calls the same private observation function, asserted by identity.

#### T227: One implementation of the rule
`ctrlrun.gateway.outcome.Transport is ctrlrun.transport.Transport`, and the function the gateway's
classification path calls **is** `ctrlrun.transport.effect_state`, asserted by object identity (a spy
installed on the core module is the one the gateway reaches), never by comparing outputs.

#### T228: The import rules
In a subprocess: `import ctrlrun` imports no `httpx`, `psycopg`, `jwt` or `opentelemetry` module, and not
`ctrlrun.verify` or `ctrlrun.conformance` (T30, T92, T125b, T134, T140f unchanged); `import ctrlrun.transport`
imports none of the extras either. By AST: every import in `transport.py` is a standard-library module,
`ctrlrun.errors` or `ctrlrun.effect`.

#### T229: No parameter widens `FAILED`
The public signatures of `urlopen`, `HTTPConnection`, `HTTPSConnection` and `request` are compared against
§2.8's lists; an unlisted parameter fails. No `CTRLRUN_*` environment variable is read by the module.

#### T230: G12 in verify, under the amended network guard
§8.9's G12 entry. T107's guard is amended to refuse every non-loopback address for `connect`, `bind` and
`getaddrinfo`; the same subprocess asserts the guard is live (a connect to `192.0.2.1`, TEST-NET-1, is refused
by it) and that G12 was graded, not `N/A` and not skipped.

#### T231: The gateway's `NotExecuted` carries its cause
An intercepted `tools/call` against an upstream port that refuses: the effect is `FAILED`, the client gets
`-41011`, and the `NotExecuted` the gateway's executor raised has the httpx exception as its `__cause__`,
where 0.6.1's had none (`server.py:616`).

### 8.3 Item 3: The idempotency token (§4)

#### T232: A `FAILED` renewal changes the token
**The test this item exists for.** An executor reads `idempotency_token()` and raises `NotExecuted`; the
renewal's executor reads it again and commits. The two differ, and each equals
`idempotency_token_for(receipt.effect_key, receipt.attempt)` of its own receipt. On both backends, and on
Postgres under two processes renewing concurrently.

#### T233: Stable within one attempt, across `Control.resume`
Read twice in one executor: equal. An executor that suspends and is resumed: the resumed leg reads the same
token as the first leg, and `attempt` is unchanged on the record. (If resume ever changes the attempt, this is
the test that says so.)

#### T234: Two keys at one attempt differ
`refund:txn_1` and `refund:txn_2`, each at attempt 1.

#### T235: A pure function, pinned
`idempotency_token_for("refund:txn_1", 1) == "382ee448-97da-8107-b674-8c253650d93f"`, as a literal. The same
pair in a subprocess, against SQLite and against Postgres, gives the same string. The value parses as a UUID of
version 8 and variant RFC 9562's, and is 36 ASCII characters. A float, a `bool` attempt, an attempt below 1 and
an empty key are `InvalidArgument`.

#### T236: Outside an executor it fails closed
`InvalidArgument` from: the top level; inside `Control.evaluate`; inside an executor whose action has no effect
key; inside an observe-mode executor whose reservation was refused; on a thread the executor started without
copying its context. Each asserts it is not `NotExecuted` and that no record changed.

#### T237: The zero-argument executor is unchanged
An executor written for 0.6.1, taking no arguments and never calling the accessor, runs and commits exactly as
before, through `@protect`, `Control.execute`, the gateway and an adapter.

#### T238: A receipt re-derives its token
For every receipt of an attempt that ran in T232 and T233, the token the executor read equals
`idempotency_token_for(effect_key, attempt)` computed from the receipt alone.

#### T239: G14 in verify
§8.9's G14 entry, with its control.

### 8.4 Item 4: The attempt ceiling (§5)

#### T240: Exactly N dispatches, then a named refusal
`max_attempts: 3` and an executor that raises `NotExecuted` on every call. Three dispatches; the fourth
attempt raises `ActionDenied` with `reason == "attempt_ceiling"` (the reason, not only the type); its receipt
is `blocked`; `EFFECT_RESERVATION_REFUSED` carries `reason`, `attempt` and `max_attempts`; the record is `FAILED`.

#### T241: A refused attempt never calls the executor
The executor's call count after the refusal is still three, through both the fast path and the check after the
reservation.

#### T242: The positive control: under the ceiling, 0.6.1's behaviour
With `max_attempts: 3`, attempts one to three behave exactly as at 0.6.1, record for record and event for event.

#### T243: No ceiling, no change
A document with no `max_attempts` renews without bound, as 0.6.1 does, over more attempts than any ceiling in
this suite; G15 reports `N/A` with its sentence.

#### T244: The loader refuses a malformed ceiling
`0`, `-1`, `true`, `1.5`, `"3"` and a mapping: each a `PolicyError` naming `max_attempts`, the action and the
line. `max_attempts` in a `ctrlrun.policy/v4` document: a `PolicyError` naming `v5`. Two documents differing
only in a ceiling have different `policy_hash` values.

#### T245: The concurrency case: the check on the assigned number, alone
The fast path is defeated on purpose rather than raced for: two callers both pass it having read the record
`FAILED` at attempt N−1; the first reserves attempt N, runs and fails; only then does the second reserve. It
is assigned attempt N+1 and refused by the check after the reservation, leaving the record `FAILED` at N+1
with `EFFECT_RESERVED` before its refusal. The executor ran N times in all. (Had the second reserved while
the first held its lease, it would have met `DuplicateEffect(state="in_progress")`, which is not the window
this test is about.)

#### T245b: The fast path, alone
The check after the reservation is disabled by the test; a record already `FAILED` at the ceiling is refused
by the fast path with nothing reserved (the record's attempt unchanged, no `EFFECT_RESERVED`), no approval
request created and a presented approval still `granted`.

#### T246: A stale renewal on Postgres never lands
With the proxy the tests own, a renewal planned against attempt *k* is held before its `UPDATE` until another
process has renewed to *k+1* and failed. The held renewal is refused; the record is `FAILED` at *k+1*; no
attempt number was written twice. Mutating the `WHERE` clause back to 0.6.1's makes this test fail with two
reservations carrying *k+1*.

#### T247: Both backends, and the v0.6 multi-process standard
T240 to T245b on SQLite and on Postgres; on Postgres, T245 with the contenders in separate OS processes.

#### T248: What happens to an approval on a refused attempt
Through the fast path, the presented approval is `granted` afterwards. Through the check after the
reservation, it is `consumed` and the `blocked` receipt names it. Both asserted by reading the stored status.

#### T249: A crash between the reservation and the release
The process is killed after the reservation and before `fail_effect`. The record is `RESERVED` or `EXECUTING`
under its lease; after the lease lapses the next attempt is refused with `AmbiguousEffect` and the record is
`AMBIGUOUS`. It is never `FAILED` by the crash.

#### T250: Observe mode, `resume`, and resolved attempts
In observe mode an attempt past the ceiling executes and its receipt carries `would_have.blocked_reason ==
"attempt_ceiling"`. A resumed leg past the ceiling is not refused. An attempt resolved to `FAILED` by
`ctrlrun resolve` counts toward the ceiling.

#### T251: The amendment is in `SPEC-v0.1.md`
`SPEC-v0.1.md` §5.4 carries the original table unchanged and, beneath it, the amendment block of §5.8 naming
`SPEC-v0.7.md` §5.

#### T252: G15 in verify
§8.9's G15 entry, with its control.

### 8.5 Item 5: Precondition fingerprints (§6, §7)

#### T253: A moved fingerprint refuses, by its own reason
Requested when the provider returns state A, granted, presented when it returns state B:
`ApprovalMismatch` with `reason == "precondition_changed"`; the executor's call count is zero; no effect record
exists; `APPROVAL_INVALIDATED` carries both fingerprints.

#### T254: The approval is left granted
After T253 the approval's stored status is `granted`, and presenting it again when the provider returns A
executes and commits.

#### T255: A provider that fails refuses the action
On the presenting pass a provider that raises: `precondition_unavailable`, nothing reserved, nothing executed,
approval `granted`, a reason distinct from T253's. On the request pass: `ActionDenied(reason=
"precondition_unavailable")`, **no request in the store**, a `denied` receipt keeping `decision: approve`.

#### T256: What the canonicalizer refuses, refuses
The provider returns a float at depth three, a mapping with an integer key, a string holding a lone surrogate,
and a list: each `precondition_unavailable` on the presenting pass and `ActionDenied` on the request pass.

#### T257: A fingerprint on one side only is a refusal
A request created with a fingerprint, the column then set to `NULL` directly in the database, presented with the
provider: `precondition_missing`. An approval carrying a fingerprint presented by a call with no provider,
through `Control.execute`, the gateway and the ACS hook: `precondition_missing`, `-41006` with the reason, and
`deny` with `["ctrlrun.blocked", "precondition_missing"]` respectively. Never a skip.

#### T258: `ALLOW` and `DENY` never call the provider
The provider's call count is zero for an `ALLOW` action with and without a presented approval, and for a `DENY`.

#### T259: `Control.resume` never calls the provider
A suspended `APPROVE` action with a provider is resumed: the provider's count after the resume equals its count
before.

#### T260: Raw provider output reaches no evidence
The provider returns a sentinel string. After a committed action, a refused one and an unavailable one, the
sentinel appears in no row of any table, no JSONL line, no event, no receipt and no captured log record.

#### T261: A change before the compare is refused
The resource changes after the request and before the presenting pass calls the provider: refused,
`precondition_changed`.

#### T261b: The residual window: a change after the compare and before the reservation is not refused
The test changes the resource after the comparison has returned and before `consume_approval_and_reserve`
runs, by wrapping the store call so the change lands inside it, ahead of the real call. Nothing is added to
the library for the test's sake. **The action is not refused**, and the test's name and docstring say this is
the residual window §6.7 documents. This is the honest test, and the one that keeps the documentation true.

#### T262: An approval that would be refused anyway never reaches the provider
Consumed, expired, hash-mismatched and denied approvals, each with a provider whose world has also moved: the
provider's count is zero and each reason is 0.6.1's (`consumed`, `expired`, `mismatch`, `approval_denied`).

#### T263: The recheck runs before each take
A presenting pass that meets an `AMBIGUOUS` record, whose `reconcile` hook answers `not_executed`, calls the
provider twice, and a world that moves during the hook is refused on the second take.

#### T264: The migration, in both directions, on both backends
A database built by **0.6.1's own code** in a subprocess (a pinned `ctrlrun==0.6.1`, never a hand-written
fixture) is opened by 0.7, migrated, and every row is present by content with `precondition_fingerprint`
`NULL`. The migrated database opened by 0.6.1 is refused with `SchemaMismatch` naming `0005` and both versions,
before any other table is read. SQLite and Postgres.

#### T265: `v3` and `v4` in one chain
A chain written by 0.6.1's code and continued by 0.7 verifies end to end with `verify_chain`, `ctrlrun
receipts --verify-chain` and G11's reader; each `v3` receipt rehashes to its stored hash; each new receipt is
`v4`. A document with no `schema`, and one saying `ctrlrun.receipt/v9`, is refused by `from_dict` naming it.
Mutating `to_dict` to render every receipt as `v4` makes this test fail with `content_altered` at the first
`v3` receipt.

#### T266: The store conformance suite covers the column
A case asserting a store persists and returns `precondition_fingerprint`; a broken-store fixture that drops it
fails that case by name (`v0.6` T140's rule: every fixture fails its named case).

#### T267: §7's column, row by row
Every row whose answer is executable is driven: `@protect` and `Control.execute` recheck; `Control.evaluate`,
`Control.resume`, `needs_approval`, `InterruptApprovalProvider.wait` and the operator server's write tools
never call the provider (counted); the gateway and the ACS hook refuse a fingerprinted approval.

#### T268: The documentation says narrows
`README.md`, `CHANGELOG.md`, this document's §6 and the docstrings of every public name §6 adds are scanned for
*prevent*, *close*, *closes*, *guarantee* and *ensure* in any sentence that also mentions a precondition or a
fingerprint, against an allow-list of exact lines that disclaim, on `v0.6` T180's design: a new occurrence
fails, and whoever adds it says in the test which kind it is. A positive control asserts the pattern fires on
*"the recheck prevents a stale approval"*.

#### T269: G16 in verify
§8.9's G16 entry, with its control and its note.

### 8.6 Item 6: Release

#### T270: Verify against the shipped examples
`ctrlrun verify` against `examples/policies/payments.yaml` and `examples/authority/payments.yaml` reports what
0.6.1 reported for G1 to G11, plus G12 to G16 each graded or `N/A` with its sentence, under
`ctrlrun.guarantees/v3`. All sixteen ids are present in the registry before the release PR opens.

#### T271: Core still installs nothing new, and the demo still runs offline
`pip install ctrlrun` installs `pyyaml` and `click` and nothing else; `ctrlrun demo` runs every scenario in under
60 seconds with the network taken away by the guard, not assumed away.

### 8.7 The migration upgrade case

T264 is the upgrade case in both directions and is listed here as well because the milestone's definition of done names it: the
forward direction proved against 0.6.1's own code, the backward direction refused at open and named.

### 8.8 The import assertions for the new core module

T228 is the import assertion for `ctrlrun.transport`, beside T30, T92, T125b, T134 and T140f.

### 8.9 The five guarantees

`ctrlrun.guarantees/v3` is G1 to G16. Each entry below fixes `v0.4 §2.1`'s fields. **Every `N/A` reason is a
sentence that must be true of what the operator handed verify**, because an `N/A` is excluded from the
denominator and a false one is a false green (0.6.1 fixed exactly that). A failed control is `fail` with
`reason: "control failed"`, never a pass and never an `N/A` (`v0.4 §1.3`).

---

#### G12: A byte written and the peer killed is `AMBIGUOUS`, never `FAILED`

**Invariant.** `ctrlrun.transport` claims `NotExecuted` only for a connection it opened and handed no request
byte; after one byte, every failure is the original exception and the effect is `AMBIGUOUS`.

**Descends from.** `v0.1 §5.5`, `v0.2 §6.8`, T220, T221.

**Requires.** One action the configuration can drive to `allow` or `approve`, as G10 requires (verify grants its
own approval, `v0.4 §3.5`).

**N/A when.** Every action in the policy is denied. Reason: `every action in the policy is denied`, G10's
sentence, and true for G10's reason: there is no action through which an outcome could be recorded.
**Never `N/A` because of the environment.** A sandbox that will not let verify bind a loopback socket is an
internal error, exit 3 (`v0.4 §3.8`), because it is a fact about the machine and not about the document.

**Observable.** Verify binds a listener on `127.0.0.1` at an ephemeral port. The executor calls
`ctrlrun.transport.urlopen` against it; the listener reads at least one byte, **records that it did** (the
scenario asserts this first), and resets. The exception is not `NotExecuted`; the receipt is `ambiguous`; where
the action has an effect key the record is `AMBIGUOUS`.

**Control.** A loopback socket verify bound and did not listen on: the call raises `NotExecuted` chained from
`ConnectionRefusedError`, the receipt is `failed`, and where there is a key the record is `FAILED`. A classifier
that never claimed would pass the observable and fail this; one that always claimed would fail the observable.
The guarantee is the asymmetry, so both directions are asserted or neither is, as G10's are.

**What it amends.** `v0.4 §3.7`'s "no scenario opens a socket" becomes **nothing verify does leaves the host**:
G12 opens loopback sockets to listeners verify itself bound, and nothing else. T107's guard is amended to match
(T230).

---

#### G13: Divergence between the store's clock and this host's is named

**Invariant.** A store with its own clock that disagrees with the application clock by more than the threshold,
beyond the measurement's own bound, is reported by `CLOCK_SKEW_DETECTED`, and a lease is decided exactly as it
would be without the report.

**Descends from.** `v0.1 §5.3 E3`, T209 to T213.

**Requires.** A `--store-url` naming Postgres.

**N/A when.** The store verify was given reads only the application's clock. Reason: `the store verify was given
reads only the application's clock, so there is no second clock to diverge from; pass --store-url
postgresql://… to grade this`. True of every run it appears on: SQLite has no clock of its own.

**Observable.** A scratch store whose application clock is the server-aligned clock (below) shifted ahead by the
threshold plus one second: the verify `Control`'s first action is preceded by `CLOCK_SKEW_DETECTED` with
`direction: "ahead"` and `skew_us` above `threshold_us`. The same shifted behind: `direction: "behind"`.

**Control.** A scratch store whose application clock is aligned with the server's, by the offset a first
measurement found: a measurement is present and nothing is reported. **Aligned, not the raw clock**, because the
host running verify is often a CI runner and not the operator's production host, and a verify that failed
because a runner's clock drifted would be grading the wrong machine. A detector that always fires fails this; one
that never runs fails it too, because the measurement must be present.

---

#### G14: The provider token changes across a renewal, provably

**Invariant.** The token an executor reads is `idempotency_token_for(effect_key, attempt)` for the attempt it is
running, so a renewal after `FAILED` sees a different token and one attempt sees one token.

**Descends from.** `v0.1 §5.4`, T232, T233, T238.

**Requires.** As G5: one action declaring an `effect:` template whose placeholders the synthesized arguments
resolve.

**N/A when.** As G5, with G5's reason and note: `no action declares an effect: template`, and *in a
`ctrlrun.policy/v1` document the template lives in the `@protect` decorator, which verify does not read*. True,
because the token is defined only for an attempt that holds a reservation, and without a key there is no
reservation and no attempt to name.

**Observable.** Attempt 1's executor reads the token and raises `NotExecuted`; attempt 2's reads it and commits.
The two differ, and each equals the derivation from its own receipt's `effect_key` and `attempt`.

**Control.** Inside attempt 1 the accessor read twice returns one string, and the accessor called outside any
executor raises `InvalidArgument`. A kernel that returned a fresh random string on every read would pass the
observable and fail this; one that returned the effect key's token would fail the observable.

**Why G14 depends on the document at all.** It does not depend on what the operator wrote the way G15 and G16
do; it depends on there being an effect key, which is the same thing G5 depends on, and a guarantee graded where
no reservation could exist would be one that could not have failed.

---

#### G15: A renewal past the operator's ceiling is refused

**Invariant.** An action whose policy entry declares `max_attempts: N` executes at most N attempts on one effect
key, the first included, and the refusal names the ceiling.

**Descends from.** `v0.1 §5.4` as amended (§5.8), T240 to T245b.

**Requires.** One action that declares both an `effect:` template and `max_attempts`, that the configuration can
drive to `allow` or `approve`, with `max_attempts` no greater than 100.

**N/A when.** No such action exists. Reason: `no action with an effect: template declares max_attempts`, which is
the gap the roadmap wants an operator to see. Or every such action's ceiling is above verify's bound. Reason:
`every declared max_attempts is above verify's bound of 100 attempts`, true of the document and of verify's
stated bound, on the precedent of G4's `GRANT_ALREADY_EXPIRED`: every loop verify runs is bounded
(`v0.4 §3.6`).

**Observable.** An executor that raises `NotExecuted` on every call: exactly N calls; attempt N+1 raises
`ActionDenied` with `reason == "attempt_ceiling"`; the call count is still N; the record is `FAILED`; the refused
attempt's receipt is `blocked`.

**Control.** Every attempt up to N executed: the call count reached N, so for N of 2 or more the renewal was
admitted. A kernel that refused every renewal would fail this.

---

#### G16: A precondition whose fingerprint moved is refused before the reservation

**Invariant.** An approval requested with a precondition fingerprint is refused, before any reservation, when the
fingerprint computed at presentation differs; the approval is left `granted`.

**Descends from.** `v0.1 §4.2`, T253, T254.

**Requires.** One action the configuration can drive to `approve`, as G1 requires.

**N/A when.** No action requires approval. Reason: `no action requires approval`, G1's sentence, and true because
a precondition binds only where an approval is consumed (§6.8).

**Observable.** Verify supplies its own provider. The request is created while it returns one state and granted;
at presentation it returns another. `ApprovalMismatch` with `reason == "precondition_changed"`; the executor's
count is zero; no `EFFECT_RESERVED` for the action; the approval is `granted`.

**Control.** The same, with the provider returning the original state at presentation: the action executes and
commits.

**Note, printed once beneath the table** as G3's is: *verify supplies its own precondition provider; whether your
`@protect` declares one is in your code, which verify does not read.* The roadmap's exit sentence says G16 is `N/A`
where the configuration names no fingerprint. A fingerprint is named in code (§6.2), not in any document verify
reads, so that sentence cannot be made true; G16 is instead graded as G5 and G10 are, against the kernel
in this configuration with verify's own stand-in for the operator's code, and the note says so. Item 6 reconciles the
roadmap sentence.

---

**The catalogue moves once.** Item 1 bumps `ctrlrun.guarantees/v2` to `v3` and lands G13; items 2 to 5 land G12,
G14, G15 and G16. Between items, unreleased `main` carries a partial `v3`, and item 6 asserts all five are present
before the release PR opens (T270). **Rejected: stub rows for the unbuilt guarantees**, because a guarantee that
reports anything before its check exists is a false green.

---

## 9. Public API additions (frozen for v0.7)

v0.5 added no table, no column, no event, no error and no CLI command, and said so as evidence its surface was the
right size. **v0.7 cannot make that claim and does not pretend to.** Every row below is a specification amendment
first: its name and shape are here, what every caller does about it is written in the section it cites, and only
then is there code.

### 9.1 The six additions, one justification per row

| Addition | Item | Name | Why it clears the bar |
|---|---|---|---|
| A new core module | 2 | `ctrlrun.transport`: `Transport`, `effect_state`, `HTTPConnection`, `HTTPSConnection`, `urlopen` | The rule exists and is right, and it is unreachable from core. Moving it is the only way `@protect` gets it without an extra and without a second copy (§2.1). |
| One accessor | 3 | `ctrlrun.idempotency_token() -> str`, re-exported at package import | An executor is called with no arguments, so a value per attempt has to be read from somewhere; a context variable `Control` sets is the only place that changes no signature (§4.3). **`idempotency_token`, not `idempotency_key`**: in this codebase "key" is the effect key, and a reader seeing `idempotency_key` beside `effect_key` would reasonably assume they are one thing, which is the exact mistake §4.1 exists to prevent. |
| One policy key | 4 | `max_attempts` (an action-entry key) | An operator-set bound where there is none, per action because it is a property of the provider (§5.3). **`max_attempts` and not `attempt_ceiling` or `max_retries`**: it counts what executes, the first attempt included, so `max_attempts: 3` reads as what it does; a retry count is off by one from the number that bounds dispatches. |
| One event type | 1 | `CLOCK_SKEW_DETECTED` | The only way a measurement reaches the evidence log and every sink (§3.6). Named as `v0.1 §6.2`'s types are, a noun and what happened to it, and for what happened rather than for the store that noticed it, as `EXECUTION_SUSPENDED` is (`v0.2 §11`). |
| One column | 5 | `approvals.precondition_fingerprint`, migration `0005_precondition_fingerprint` | `ApprovalRecord` is rebuilt from columns, so a value the recheck reads back must be one (`v0.6 §3.7`). Named for what it holds, beside `policy_hash_at_approval`. |
| One receipt schema bump | 5 | `ctrlrun.receipt/v4` | Two fields a reader must be able to see, and a version is how a reader knows to look (§6.11). |

### 9.2 What else this document adds, and why each one is here

The table above counts additions by kind; the names below are what those additions need in order to exist,
listed so that no public name arrives unlisted, which is the correction `v0.6 §9.1.1` had to make after the fact.

```python
# ctrlrun.gateway.transport (ctrlrun[gateway], lazy). ctrlrun.transport's own names are in §2.8
def request(method, url, *, content=None, headers=None, timeout) -> httpx.Response   # §2.5

# ctrlrun.effect (§4.5)
def idempotency_token_for(effect_key: str, attempt: int) -> str

# ctrlrun.state (§3.6), core
@dataclass(frozen=True)
class ClockSkew:
    skew: timedelta; bound: timedelta; threshold: timedelta
    measured_at: datetime; trigger: str       # "open" | "lease_expired"
    @property
    def exceeded(self) -> bool: ...

DEFAULT_CLOCK_SKEW_THRESHOLD: Final = timedelta(seconds=1)   # ctrlrun.postgres

# ctrlrun.postgres (§3.6, §3.7). Not StateStore methods: attributes of the one concrete store with a clock
class PostgresStateStore:
    def __init__(self, url, *, clock=..., schema="public",
                 clock_skew_threshold: timedelta = DEFAULT_CLOCK_SKEW_THRESHOLD) -> None: ...
    clock_skew: ClockSkew | None                                    # read-only

# ctrlrun.policy (§5.3)
class Policy:
    def max_attempts(self, action_name: str) -> int | None: ...

# ctrlrun.control / ctrlrun (§6.2)
protect(..., preconditions: Callable[[Action], Mapping[str, Any]] | None = None)
Control.execute(..., preconditions: Callable[[Action], Mapping[str, Any]] | None = None)

# ctrlrun.approval (§6.2)
class ApprovalRequest:  precondition_fingerprint: str | None = None

# ctrlrun.receipt (§6.11)
class Receipt:  schema: str; precondition_at_request: str | None; precondition_at_recheck: str | None
BLOCKED_ATTEMPT_CEILING: Final = "attempt_ceiling"   # joins BLOCKED_BY_STATE
```

**Reason strings**, each a value of an existing field and each asserted by name in §8:

| Field | Value | Where |
|---|---|---|
| `ActionDenied.reason`, `EFFECT_RESERVATION_REFUSED.data.reason` | `attempt_ceiling` | §5.5 |
| `ApprovalMismatch.reason`, `APPROVAL_INVALIDATED.data.reason` | `precondition_changed`, `precondition_missing`, `precondition_unavailable` | §6.2 |
| `ActionDenied.reason` (request pass) | `precondition_unavailable` | §6.2 |
| `would_have.blocked_reason` | `attempt_ceiling` | §5.5 |

The two `preconditions=` keywords and `clock_skew_threshold=` are keywords on existing callables, not new
methods. `ClockSkew`, `Receipt.schema` and `ApprovalRequest.precondition_fingerprint` are fields and a record
type. `idempotency_token_for` exists because §4.5's re-derivation needs a public definition to re-derive from,
and `request` because the httpx variant is the gateway's rule offered to an executor using httpx (§2.5).
`clock_skew` is the one that most resembles a store method, and §3.6 argues why it is not one.

**And no other public name.** No new `Control` method, no new `StateStore` method, no new error type, no new
approval provider, no new sink, **no new CLI command and no new CLI flag**.

### 9.3 Schemas

| Schema | Change |
|---|---|
| `ctrlrun.action/v1` | **unchanged** |
| `ctrlrun.policy/v5` | new: `max_attempts` on an action entry (§5.3). `v1` to `v4` still load, unchanged |
| `ctrlrun.receipt/v4` | new fields `precondition_at_request`, `precondition_at_recheck`; a receipt renders under its own schema (§6.11) |
| `ctrlrun.guarantees/v3` | G1 to G16; G12 to G16 added (§8.9) |
| `ctrlrun.idempotency/v1` | new: the domain tag inside the token's canonical input (§4.2). Never a document on its own |
| `ctrlrun.precondition/v1` | new: the domain tag inside the fingerprint's canonical input (§6.2). Never a document on its own |
| `ctrlrun.verify/v1`, `ctrlrun.store-conformance/v1`, `ctrlrun.inspection/v2`, `ctrlrun.approval_request/v1` | unchanged. Verify's report carries more guarantee ids under the same shape; the store suite's report carries one more case |

**A `ctrlrun.receipt/v4` writer and an 0.6 reader do not mix**, for `v0.3 §12.2`'s reason and with its
instruction: upgrade every reader before upgrading any writer. The migration makes the store half of that
automatic (an 0.6.1 store refuses the migrated database); the JSONL half is the operator's, and item 6 says so in
the changelog.

### 9.4 The guarantee catalogue

`ctrlrun.guarantees/v3` is G1 to G16, and the version moves once, in item 1 (§8.9). The ids were assigned on
2026-09-10 in version order, which is why v0.8 takes G17 to G21 and `v4`.

### 9.5 The module map

`ARCHITECTURE.md` §6 gains one row, and the direction is unchanged:

| Module | Owns | Must not know about |
|---|---|---|
| `transport.py` | the transport half of `v0.1 §5.5`'s asymmetry: what a transport observed, what that records, and the counting connections for `http.client` and `urllib` | policy, approvals, storage, sinks, `Control`, anything from an extra |

It sits beside `effect.py` and imports `errors.py` and `effect.py`. Nothing in the kernel imports it except
`gateway/outcome.py` and `gateway/transport.py`, which are above it, and verify's G12 scenario. `effect.py` gains
`idempotency_token_for` and imports `action.canonical_bytes`, which it already sits above.

### 9.6 What v0.7 amends in v0.1 to v0.6

Each in the item that makes it true, and each recorded here so it can be found.

1. **`v0.1 §5.4`**, by the amendment block of §5.8, written into `SPEC-v0.1.md` beneath the unchanged table
   (item 4).
2. **`v0.1 §6.2`'s event list** gains `CLOCK_SKEW_DETECTED` (item 1).
3. **`v0.2 §6.8`'s transport rows** are unchanged in meaning and now implemented by `ctrlrun.transport`; the
   gateway's `NotExecuted` is chained (item 2).
4. **`v0.3 §4.3.1`** gains §7's column and §5.5's order (items 4 and 5).
5. **`v0.4 §3.7`** becomes *nothing verify does leaves the host*, and T107's guard refuses every non-loopback
   address (item 2).
6. **`v0.6 §6.4`'s last bullet**: an additive receipt field no longer breaks the rehash of an older receipt,
   because a receipt renders under its own schema; the unreleased builds that bullet describes are unchanged
   (item 5).
7. **`v0.6 §8` T141**'s "no other N/A is accepted" admits the skew case's `not_applicable` on SQLite and the
   in-memory store (item 1).
8. **`v0.6 §4.2`'s renewal compare-and-set** is conditioned on the planned-from attempt as well as the state
   (item 4, §5.6).

---

## 10. Fail-closed table for v0.7

`v0.1 §3.4`, `v0.2 §6.11`, `v0.3 §9`, `v0.4 §10`, `v0.5 §10` and `v0.6 §10` hold in full. These rows are v0.7's
own, and none of them is configurable.

| Condition | Result |
|---|---|
| A connection the classifier opened fresh fails in `connect()` with no request byte offered | `NotExecuted`, chained from the original; the record `FAILED` (§2.3) |
| Any failure after one request byte was offered, including a `sendall` that raised part way | The original exception; `AMBIGUOUS` (§2.3) |
| A connection the classifier did not open, or reused | Never `NotExecuted` (§2.3) |
| An HTTP response of any status | Never `NotExecuted` from the classifier (§2.4) |
| An exception before any connection exists, or inside the classifier's own bookkeeping | That exception; `AMBIGUOUS` (§2.3) |
| A `30x` response | Not followed; returned or raised as a status (§2.3) |
| The skew measurement raises | Logged; nothing refused; no decision changed; the retained measurement unchanged (§3.5) |
| Skew past the threshold | `CLOCK_SKEW_DETECTED`, rate-limited. **No lease decided differently** (§3.2) |
| `clock_skew_threshold` zero, negative, above `DEFAULT_LEASE`, or not a `timedelta` | `InvalidArgument` at construction (§3.7) |
| `idempotency_token()` outside an executor, for an action with no key, for an unheld observe-mode attempt, or on a thread without the context | `InvalidArgument` (§4.3) |
| `max_attempts` of 0, negative, `bool`, float, string or mapping | `PolicyError` at load, naming the key, the action and the line (§5.3) |
| `max_attempts` in a document below `ctrlrun.policy/v5` | `PolicyError` naming `v5` (§5.3) |
| A record already `FAILED` at the ceiling | Refused before the approval gate; nothing written; no request created (§5.5) |
| A reservation assigned an attempt number above the ceiling | Executor not called; record released `FAILED`; `blocked` receipt; `ActionDenied(reason="attempt_ceiling")` (§5.5) |
| A crash between that reservation and its release | `AMBIGUOUS` once the lease lapses; a human or a hook resolves it; never `FAILED` (§5.2) |
| A Postgres renewal planned against a stale attempt number | Matches no row; refused (§5.6) |
| A presented approval whose fingerprint differs from the recheck | `ApprovalMismatch(reason="precondition_changed")`; nothing reserved; approval `granted` (§6.3) |
| A fingerprint on one side only | `ApprovalMismatch(reason="precondition_missing")`; never a skip (§6.4) |
| The provider raises, returns a non-mapping, or returns what `canonical_bytes` refuses | Presenting pass: `ApprovalMismatch(reason="precondition_unavailable")`, nothing reserved. Request pass: `ActionDenied(reason="precondition_unavailable")`, no request created (§6.5) |
| A receipt document with no `schema`, or one this binary does not know | `InvalidArgument` from `Receipt.from_dict`, naming it (§6.11) |
| An 0.6 binary opening a database migrated by 0.7 | `SchemaMismatch` at open, naming `0005` and both versions (§6.11) |

---

## 11. Explicitly out of scope for v0.7

Everything in `v0.1 §9`, `v0.2 §12`, `v0.3 §13`, `v0.4 §11`, `v0.5 §11` and `v0.6 §11` that v0.7 does not
deliver, and specifically the milestone's *Do not build* list, each with its reason:

- **Generic fencing tokens.** Fencing works only where the resource validates the token, and the resources here,
  Stripe, the Kubernetes API, an SMTP server, accept no CTRLRun fence. The only enforceable point is the gateway,
  and for `@protect` a fence degrades to "refuse to start under a stale lease", which `plan_reservation` already
  does. A fence would be an elaborate mechanism whose guarantee is the one already held.
- **Consequence budgets.** The metric, scope and window shape is right and the hard part is unwritten: consume on
  reserve and effects that failed are overcharged; consume on commit and an agent burns unlimited authority by
  generating ambiguity. The fail-closed answer is probably *consume on reserve, hold until reconciled*, and until
  that is specified a budget is a rate limiter with a correctness hole. v0.9, which uses this milestone's attempt
  number to make "until reconciled" computable.
- **Issuing anything**: token minting, OAuth flows, an authorization server, dynamic client registration, token
  exchange, introspection, revocation lists. This project verifies what it is handed (`v0.3 §1.1`).
- **Matching a grant on a claim.** It needs an answer to "what does a missing claim mean" that nothing has yet
  (`v0.3 §13`).
- **A consequence taxonomy, separation of duties, multi-approver workflows, M-of-N, break-glass, authenticating
  the approver.** v0.8's question, not this one's.
- **A2A and authority propagation across agent hops.** v0.10, because propagation needs authority that can be
  bounded (v0.9) and a yes that can be attributed (v0.8) underneath it.
- **A delegation browser, and unrevoking.** Unchanged since `v0.3 §13`.
- **OPA and Cedar providers, and any ACS compliance claim.** Unchanged.
- **The deprecated 2024-11-05 HTTP+SSE transport, and more than one upstream per gateway.** Unchanged since
  `v0.2 §12`.
- **Compensation and sagas.** An effect is recorded and refused; it is never undone by the kernel.
- **Signed receipts.** The chain detects alteration and does not prove authorship, and signing brings key
  generation, rotation and revocation, which is issuing (`v0.6 §11`).
- **Dashboards, a web UI, a management plane, and anything in `VISION.md`.** Receipts are portable JSON.
- **An emergency-stop command**, `ctrlrun suspend principal|action|environment`. An operator edits the policy,
  and since `v0.6 §7.1` the policy is hashed and every receipt records which one decided, so the change is
  evidenced. A second way to do one thing is a second thing to keep correct.
- **A reorganisation of `control.py`.** It is 2331 lines and that is a real problem, and the proposed splits
  reproduce module boundaries that already exist. Not this milestone, and not as a side effect of one: items 3, 4
  and 5 add to `control.py` in place.
- **Retrying the effect on ambiguity.** Reconciliation retries the observation. §4 gives it a handle to observe
  with; it gives nothing permission to act twice.
- **Compliance or standards claims of any kind**, in this document, the README, docstrings or CLI output.
- **Anything a marketing surface sells.** ctrlrun Pro and Enterprise are their own track; a promise on a sales
  page is never a reason to add a module here, and a primitive a commercial product needs arrives as its own
  specification amendment on a kernel version line.

And v0.7's own:

- **Lease evaluation on the store's clock.** A change to `v0.1 §5.3` (§3.2).
- **A classifier for `requests`, `aiohttp` or any other client.** Each would need its own honest observation of
  what reached the wire, and `urllib`, `http.client` and httpx are the three this project already depends on or
  ships with.
- **Provider-specific `NotExecuted`**: parsing Stripe's error types, or any provider's, into non-execution. That is
  the executor's knowledge and the executor's claim (§2.4).
- **The gateway sending the idempotency token upstream.** MCP defines no idempotency header, and inventing one
  would be the gateway speaking a protocol nobody agreed to.
- **A timeout on the precondition provider** (§6.5), **a policy-wide `max_attempts` default** (§5.3), and
  **showing fingerprints to a human** in `ctrlrun inspect`, the webhook document or the approval CLI (§6.10).
- **Correcting a skewed clock**, or refusing to run on one (§3.9).

---

## 12. What building v0.7 settled

*One subsection per question the drafting could not close, each stating what the code decided and which section
carries it. `SPEC-v0.4.md` §12, `SPEC-v0.5.md` §12 and `SPEC-v0.6.md` §12 are the format.*

**This section is empty on purpose, and the items fill it.** `v0.5`'s item 6 could tell which parts of that
document had been stress-tested by somebody other than their author by looking for a §12 entry behind them, and
all four of its most serious findings sat in sections that had none. The arguments are written down as they are
decided, not afterwards.

### 12.1 Item 1: clock skew

### 12.2 Item 2: the transport classifier

### 12.3 Item 3: the idempotency token

### 12.4 Item 4: the attempt ceiling

### 12.5 Item 5: precondition fingerprints

### 12.6 Item 6: the release
