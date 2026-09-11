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

v0.7 delivers six things, one build-list item each, plus a release. The `#` column is the
build-list position.

| # | Deliverable | Ships in | Section |
|---|---|---|---|
| 1 | Clock-skew detection: a measurement, one event type, a verify guarantee, a store conformance case | core; the measurement itself in `ctrlrun[postgres]` | §3 |
| 2 | `ctrlrun.transport`, the `NotExecuted` classifier for `http.client` and `urllib` | core, stdlib; the httpx variant in `ctrlrun[gateway]` | §2 |
| 3a | Attempt numbers never repeat: the Postgres renewal compare-and-set, and the attempt a lost `COMMIT`'s re-issue returns | `ctrlrun[postgres]`; SQLite for defence in depth | §5.6 |
| 3 | The provider idempotency token, derived from `(effect_key, attempt)` | core | §4 |
| 4 | The attempt ceiling, `max_attempts`, and the amendment to `v0.1 §5.4` | core | §5 |
| 5 | Precondition fingerprints, rechecked before the reservation | core | §6, §7 |
| 6 | Release 0.7.0 | none | none |

**Item 3a is its own build item**, a separate pull request stacked immediately before item 3, and it
gets an **independent review** because it changes a store: items 3 and 4 both rest on the attempt
number being unique per key, and on Postgres it is not yet (§5.6). Independent review is therefore
required for items 2, 3a, 4 and 5.

The dependency rule of `v0.2 §1.1` and every release since is unchanged and binding:
`pip install ctrlrun` MUST continue to install `pyyaml` and `click` and nothing else. **Everything
here is core and stdlib** except the pieces that exist only because their client lives in an extra:
the skew measurement and item 3a's fix, which live in the Postgres store (`ctrlrun[postgres]`), and
the httpx variant of the classifier (`ctrlrun[gateway]`). `urllib` and `http.client` are stdlib, which is
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
  adds a column through the migration runner (§6.11) and one optional, read-only store attribute
  that no store is required to have (§3.6), and says why the second is not the first, and where it
  comes close.
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
2. **One granted approval does not buy unlimited dispatches, in enforce mode.** The roadmap's v0.7
   bullet says *"one human approval plus an executor that always reports 'nothing happened' is
   unlimited dispatches"*. For an `APPROVE` action in enforce mode it is one dispatch: the first
   reservation consumes the approval, and every renewal needs a new **granted** approval (§5.2).
   "Granted" is not "a human said yes": a `ScriptedApprovalProvider`, a `@protect(wait=True)` loop
   answered by automation, the operator server's write tools and a gateway or ACS hook spending
   approvals granted in advance all grant without a human deciding each one. In observe mode nothing
   is consumed and an `APPROVE` action runs with no approval at all (`control.py:821-827`,
   `899-925`). The unbounded case is real and is the `ALLOW` action with an effect key, which renews
   with no approval at all. §5 is written against the code's version, and item 6 reconciles the
   roadmap sentence. One path contradicts §5.5's claim that the ceiling's fast path spares a human:
   an adapter asks `ctrlrun.adapter.needs_approval`, which calls `Control.evaluate`, which does not
   see the ceiling (`adapter.py:408-450`), so a framework can put an approval in front of a human
   for an attempt the fast path will then refuse (§5.5).
3. **On Postgres the attempt number is not yet unique per key.** The renewal `UPDATE` is
   conditioned on `state = 'failed'` and not on the attempt it planned from
   (`postgres.py:787-800`), so under `READ COMMITTED` a renewal planned against attempt *k* can land
   after another process has renewed to *k+1* and failed again, and write *k+1* a second time.
   Through 0.6.1 that was harmless. v0.7 makes the attempt number load-bearing twice, in the token
   and in the ceiling, and a reused number would give two dispatches one token and let a ceiling of
   N admit N+1 executions. A second path returns a stale number: a lost `COMMIT` on a reservation is
   resolved by re-issuing it, and the re-issue's result is discarded (`postgres.py:602-622`,
   `660-663`, `696-699`), so `Control` can be handed attempt *k+1* while the store wrote *k+2*.
   **Item 3a**, a build item of its own with an independent review, fixes both (§5.6): the `UPDATE`
   is conditioned on the planned-from attempt as well, and the re-issue's reservation is the one
   returned. Both are changes inside existing methods; neither is a new method.
4. **A receipt schema bump breaks every chained receipt unless the rehash rule changes.**
   `Receipt.chain_hash()` recomputes over `to_dict()` (`receipt.py:314`), and `to_dict()` stamps the
   binary's current schema and its current key set (`receipt.py:318-345`). `v0.6 §6.4`'s last bullet
   records exactly this and calls it *"a durable property of the design"*. Adding the `v4` fields
   the ordinary way would report every receipt a released 0.6 wrote as `content_altered`. Item 5
   makes a receipt render under the schema it was written with (§6.11), which amends that bullet.
5. **Verify's network guard refuses loopback, and "no network" is already not quite true.**
   `v0.4 §3.7` says no scenario opens a socket, and T107's guard (`tests/test_verify.py:535-557`)
   refuses `socket.socket.connect`, `connect_ex`, `socket.create_connection` and
   `socket.getaddrinfo`; `bind`, `listen` and `socketpair` pass it. G12 needs a loopback peer it
   controls. And `--store-url postgresql://remote-host/…` already connects off the host, through
   libpq's own sockets, which the guard never sees (`verify/scenarios.py:546-553`). The rule becomes
   *verify opens no connection except to the store `--store-url` names and to loopback listeners
   it bound itself*, and T107's guard admits a connect only to a `127.0.0.1` port the run itself
   bound, refuses any other bind, and refuses `localhost` and `::1` (§8.9, G12). Item 6
   reconciles the README's *"with no network"* (`README.md:258`), which is already inaccurate
   under `--store-url`.
6. **Verify and the test suite inject clocks into Postgres stores.** `verify/scenarios.py:553`
   builds every Postgres scratch store with verify's injected clock, which is anchored to the
   document rather than to now, and the Postgres tests pass frozen clocks throughout. Every one of
   those stores will measure a large skew at open, and the measurement will be true. §3.8 says what
   item 1 does about it, which is not to switch the detector off.
7. **`max_attempts` needs `ctrlrun.policy/v5`.** Action-entry key sets are closed and every key
   added since v0.2 has been gated on a schema version (`policy.py:118-127`). The milestone's plan
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

Three conditions, **all** required, and the classifier claims `NotExecuted` only when it has
observed all three. The third was added after the first independent review (§12.2.9):

1. **The classifier opened the connection itself, fresh.** Not reused, not pooled, and never
   holding a socket the caller supplied.
2. **Zero request bytes were handed to the socket**, counted by the classifier's own connection
   *above* TLS: application bytes offered to the socket object `http.client` writes to, after the
   TLS layer where there is one.
3. **Zero request bytes were offered in this executor run at all**, by any of the classifier's
   connections or by `ctrlrun.gateway.transport.request`. `Control` opens a register, a private
   context variable, around each `executor()` call, and every classifier send marks it before the
   first byte is handed over; a send that belongs to no register marks every register open in the
   process, because a thread that did not copy the executor's context is the common case and its
   request must not be invisible (§12.2.13). **Outside an executor run there is no register, and
   nothing is claimed**: on a thread the executor started without copying its context, and in code
   `Control` is not running, where the kernel records nothing anyway.
4. **This is not a continuation leg.** A resumed run starts marked and never claims, whatever
   happens to the continuation's own request: a continuation exists only because the remote
   answered and is holding the exchange, so nothing on that leg can say the remote did nothing
   (§12.2.12). The gateway applies the same rule to every `FAILED` it can reach on a continuation,
   the pre-dispatch JSON-RPC codes and the `401` rule included (§2.4, §2.5).

The third condition is what makes the claim about the effect and not about one connection object.
An xmlrpc client that retries once on a new connection, an opener that follows a redirect, and an
executor's own retry-once-on-reset loop all open a second connection after the first delivered the
request, and the second, refused and judged alone, is a connection that offered nothing. Under the
third condition each is the original exception.

**The register sees only the classifier's own sends, and that is its limit.** An executor that sends
any part of the effect through another transport (`requests`, httpx used directly rather than
through `ctrlrun.gateway.transport.request`, a raw socket) and then uses the classifier can receive
a `NotExecuted` that is true of the classifier's connections and false of the effect. So can one
that raises the classifier's `NotExecuted` while another request of the effect is still in flight
on a second thread, which the register cannot see because the claim is decided first. **The claim
holds only where every request of the effect goes through the classifier.** A send through it on a
thread that did not copy the executor's context *is* seen, at the cost of marking every open run
(§12.2.13). The module's and the class's docstrings say so in the same words.

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
`connect()`, inside an executor run whose register is unmarked, on a connection object whose mark
is unset and whose socket its own `connect()` created, for an `Exception` raised by the
`http.client` connect it wraps: name resolution, the TCP
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
| A second connection, of any kind, failing after a byte of the same executor run was offered | at least one byte, on another connection | fresh | the original exception |
| Any failure outside an executor run, or on a thread without the executor's context | unknown | any | the original exception |
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
executor decides what it means. An opener somebody else built may follow one, with the classifier's
connections inside it; the register then makes the second connection's failure the original
exception, whoever built the opener (§12.2.2). **Proxies are honoured**, since the count is taken on the socket
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

**On a continuation leg, none of this claims `FAILED`** (§12.2.12): the pre-dispatch codes and the
`401` rule below answer for the continuation's own request, and the upstream already has the
original and is holding the exchange. The upstream's answer is relayed unchanged; the record is
`AMBIGUOUS`.

**The gateway's `401` / challenged-`403` rule is the product's one path from an HTTP status to
`FAILED`, and it is said plainly rather than explained away.** On an intercepted call the fresh
forwarder returns `UpstreamStatus` for a `401`, or a `403` carrying `WWW-Authenticate`
(`gateway/transport.py:214-220`); `outcome._status` maps it to `FAILED` (`outcome.py:191-192`); and the
gateway's executor raises `NotExecuted` (`server.py:615-616`). It rests on the MCP peer's word, as the
JSON-RPC pre-dispatch codes do: the MCP authorization specification puts the token check before the
method, and an upstream that violated that would win a retry it should not have, the residual
`THREAT_MODEL.md` already records for the pre-dispatch codes. It stays in `outcome.py`, it is not part
of `ctrlrun.transport`, and **`ctrlrun.gateway.transport.request` does not apply it**: an executor
calling an HTTP API with httpx is not talking to an MCP peer, and a `401` from its provider is a
status like any other (§2.5).

**`http.client` routes every request byte through `HTTPConnection.send`** on CPython 3.12
(`http/client.py`: `_send_output` and `_tunnel` both call `self.send`), which is what lets the mark
live there. That is a fact about one standard library, and the classifier stakes a safety claim on it,
so T229b pins it on every Python this project supports, 3.11 to 3.14: every byte a real loopback peer
receives was first handed to `send`.

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

Two amendments from the first independent review. **Behind a proxy it claims nothing** (§12.2.10):
httpx reports an unreachable proxy and a TLS failure with the target after the proxy answered the
`CONNECT` line with the same `ConnectError`, and §2.3's tunnel row counts that line as written, so
where httpx's environment names a proxy both are `AFTER_REQUEST_SENT`. This applies to the
gateway's forwarder as well, whose behaviour behind a proxy is therefore stricter than at 0.6.1.
**`request()` shares the classifier's register** (§12.2.9): it claims only inside an executor run
whose register is unmarked, and every call that may have written a byte marks it, so a request
delivered through httpx and a refused `HTTPConnection` after it, or the other way round, are
judged as one run. `HTTPForwarder` marks the run it writes in as well, and only that one: the
relayed traffic it also carries is never an effect (`v0.2 §6.3`), so marking every open run from a
listener thread would let `tools/list` suppress the claim of an intercepted call beside it
(§12.2.13). Both read whether a proxy is in use **when the call begins**, where the client takes
its own proxies, rather than when it fails (§12.2.14).

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
`from` it. For a connection never established, a gateway `failed` receipt then carries the same
evidence a `@protect` one does. For the `FAILED` that comes from the upstream's own answer, a
pre-dispatch JSON-RPC code or the `401` rule above, there is no transport exception to chain: the
evidence is the upstream's response, which the gateway relays unchanged and the receipt's `error`
names.

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
`NotExecuted` from `connect()` where §2.3 proves it, which is only ever inside an executor run.

**Nothing public was added for the register.** It is a private context variable in `ctrlrun.effect`,
set by `Control` and read by the two classifiers; no name in §9 changes.

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

The store sits below `Control` and has no sink; `v0.6 §9.7`'s module-map row says `postgres.py` must
not know about sinks (policy, decorator and sinks, `state.py`'s row unchanged), and `v0.6 §4.3.4`'s
branch reports go to the store's own logger for that reason. (`ARCHITECTURE.md` §6 does not yet carry
that row; item 6 adds it with `transport.py`'s.)
With `StateStore` frozen, the measurement has to reach `Control` some other way.

**Decided: the store retains, `Control` pulls.**

- `PostgresStateStore` keeps its most recent measurement as a **read-only attribute**,
  `clock_skew: ClockSkew | None`, whether or not it exceeded the threshold. `None` means no
  measurement succeeded. It also logs every reported measurement on `ctrlrun.postgres` at `WARNING`.
- `ClockSkew` is a frozen dataclass in `ctrlrun.state` (core): `skew`, `bound` and `threshold` as
  `timedelta`, `measured_at` (the application's midpoint), `trigger` (`"open"` or
  `"lease_expired"`), and the derived `exceeded`.
- `Control` reads `getattr(store, "clock_skew", None)` at the start of every `execute` and `resume`,
  and again immediately after a reservation is refused with `AmbiguousEffect`. **It uses the value
  only if `isinstance(value, ClockSkew)`**, where `ClockSkew` is `ctrlrun.state.ClockSkew` itself: **a
  third-party store must construct that class**, and a look-alike type with the same fields is ignored.
  Anything that is neither `None` nor a `ClockSkew`, and an exception raised by the read, is logged at
  `WARNING` **once per store per kind of error** (not once per action, which would flood a log exactly
  as the event's rate limit exists to avoid), and never raised, because an observation must not be able
  to fail an action (§3.5). Where the value is exceeded and is not the measurement it last reported, `Control` appends **`CLOCK_SKEW_DETECTED`**
  through `_append`, so the store writes it and every sink receives it with the store-assigned
  `event_id` (`v0.2 §4.1`). The at-open report carries no `action_id`, like the three `DELEGATION_*`
  types (`v0.3 §7`), because it is about the deployment and not about an action. The `E3` report
  carries the `action_id` and `effect_key` of the attempt whose refusal it accompanies, so a reader
  sees that the record went `AMBIGUOUS` while this host's clock disagreed with the store's. It does
  not say skew was the cause; it puts the fact beside the refusal, where a human resolving it looks.
- `data`: `skew_us`, `bound_us`, `threshold_us` (integer microseconds, exact, so the numbers a reader
  sees are the numbers the decision used), `direction` (`"ahead"` or `"behind"`), `trigger`, and
  `measured_at`.

**Why this is not a new `StateStore` method, and where it comes close.** The protocol does not
change; `Control` reads the attribute with a `None` default, and a store without it (SQLite, the
in-memory store, any third-party backend) reports nothing. That is correct rather than convenient:
only a store with its own clock has anything to report. But it is honest to say what it is in effect:
**an optional member of the store contract.** `Control` reads it from any store, this section invites
a third-party store with a clock to expose it, and T214 grades its presence. §9.2 lists it as an
optional store attribute, not as a `PostgresStateStore` detail. Two consequences follow and are
stated rather than discovered: a **wrapper** around a store (the conformance suite's fixtures, an
operator's instrumentation proxy) that does not forward the attribute silently drops skew reporting
while every decision is unchanged; and whether an optional attribute counts as "a new store method"
under `v0.6 §9.2` is a judgment this document puts to the maintainer rather than makes.

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
- **`bool` is refused by `idempotency_token_for` itself.** `canonical_bytes` accepts a `bool`, since it
  is a subclass of `int` and JSON has `true`; left to the canonicalizer, `attempt=True` would derive a
  token nobody's attempt has. So the function checks `attempt` is an `int`, not a `bool`, and at least 1,
  and `effect_key` a non-empty `str`, before canonicalizing, and T235's refusal of a `bool` is the test
  that makes that check load-bearing rather than decorative.

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

**The attempt number must be unique per key for this to hold, and the number `Control` is handed
must be the number the store wrote.** On Postgres neither is true yet (§1.4 item 3). **Item 3a**, stacked
immediately before item 3, fixes both (§5.6), and item 3 is built on it. T232 cannot see the defect on
its own: two processes renewing concurrently do not open the window, which needs one renewal stalled
between its read and its write while another process renews, runs and fails. T246 and T246b open it
deterministically, and they are item 3a's tests.

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

### 4.6 Two stores, one provider account

The token is a function of `(effect_key, attempt)` and nothing else, so **two stores that share one
provider account derive identical tokens wherever their effect-key strings coincide.** Whether that hurts
depends on whether the two keys name the same effect.

- **Different effects, one key string: a false `COMMITTED`.** Two tenants' stores both declare
  `effect="order:{order_id}"`, and both have an order 1001 that is a different order. Both reserve
  `order:1001` at attempt 1 against one Stripe account. The second to send carries the first's token, the
  provider deduplicates it and returns the first tenant's cached success, and the second tenant's executor
  sees a success for a charge that never happened. It records `COMMITTED`, and that is the worst outcome in
  this document.
- **The same effect, one key string: harmless.** A staging and a production store both reserving
  `refund:txn_1` for the same real transaction are duplicates of one refund, and the provider's
  deduplication stops the second, which is what it is for; the `COMMITTED` is true.

**Decided: an effect key must name its effect uniquely across every store that shares a provider account,
and no discriminator is added to the derivation.** `v0.1 §5.1` already asks for namespaced effect keys; this
section adds that the namespace must include the deployment wherever a placeholder is not unique at the
provider, as in `effect="tenant-{tenant_id}:order:{order_id}"` or `effect="acme-prod:order:{order_id}"`. The
docstring, the README and `THREAT_MODEL.md` say so (item 6). The argument against a discriminator:

- **Nothing available is both stable and sufficient.** The environment is stable at execution time, but the
  case that bites is two stores in the *same* environment sharing an account, which it does not separate, and
  no store carries an identity to add.
- **The derivation is pinned to two inputs so that anyone holding a receipt can re-derive it** (§4.5); a third
  input would have to be carried to every place that re-derives, and one that is usually the same would make
  "usually" the operative word in a safety argument.
- **The operator already owns the namespace.** The effect key is the operator's template, and it is the one
  identifier that is supposed to be unique across whatever shares the effect.

**The kernel cannot check this**, since it sees one store. Verify prints a note beneath G14 saying so (§8.9).

### 4.7 What it is for

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

**How a renewal after `FAILED` is authorised today, in enforce mode.** By the policy decision the new
proposal reaches, and by nothing the first attempt left behind. `plan_reservation` admits the renewal with
`attempt + 1` and consults no approval; the first attempt's approval was consumed in the transaction
that reserved it (`state.py:1553-1556`, `postgres.py:584-587`), so presenting it again is refused by
`check_consumable` with `reason="consumed"` (`approval.py:229-236`), and with nothing presented
`_presented` creates a new request and raises `ApprovalRequired` (`control.py:1620-1648`). **An
`APPROVE` action therefore needs a new granted approval for every renewal, and an `ALLOW` action
renews with no approval at all.** "Granted" is the word, not "a human said yes": a
`ScriptedApprovalProvider` grants on a script (`approval.py:486-487`), `@protect(wait=True)` re-presents
whatever answer arrives (`control.py:2116-2132`), the operator server's write tools grant from a
chat, and the gateway and the ACS hook present the newest granted, unexpired approval for the action's
hash (`server.py:709`, `acs.py:203`), which may have been granted several times over in advance; each
still buys one dispatch. **In observe mode none of this holds**: nothing is consumed, and an `APPROVE`
action with no approval presented runs anyway (`control.py:821-827`, `899-925`). (This corrects the
roadmap, §1.4 item 2.)

**What happens to an approval on a refused attempt.** Where the ceiling is found by §5.5's fast path,
before the approval gate, nothing has been written and a presented approval stays `granted`; the refusal
records it (`v0.6 §7.2.1`) and does not spend it. Where it is found by the check after the reservation,
the approval was consumed in the same transaction that assigned the attempt number, and **it stays
consumed**: the store has no way to un-consume an approval, `v0.1 §4.2 A2` is that consumption is
single-use and atomic, and v0.7 adds no method.

**And an approval that did not exist yet can be created and granted on the way to that refusal.** Where
the record is not `FAILED` the fast path answers nothing, so the approval gate is reached first: on §5.5's
reconcile route an `APPROVE` action with nothing presented has a **new** request created and raises
`ApprovalRequired`, a human grants it, and the retry consumes it in the reservation the check then refuses.
So the cost is not only "an already-granted approval stays consumed"; it is that a human can be asked, and
answer, for an attempt that could never run. §5.5 states both, and neither is closed. What is bounded is
what it costs: every later attempt on that key is also over the ceiling, so no approval spent here opens
anything until the operator raises `max_attempts`, and nothing ever executes.

**What a crash between the reservation and the release leaves, and what a failed release leaves with
it.** The record is `RESERVED`, or `EXECUTING` if the crash fell after `begin_execution`, under a live
lease. **A release the store refuses lands in exactly the same place** (§5.5 step 4): the record stays
`RESERVED` at the refused attempt under its lease, and nothing distinguishes it from a process that
stopped there. Either way, when the lease lapses the next reservation attempt declares it `AMBIGUOUS`
(`v0.1 §5.3 E3`), and a human or a `reconcile` hook must resolve it **although nothing ran**. That is
exactly what a crash between the reservation and the executor call leaves today, and it costs a human,
never an execution. The fast path makes it rare, since only a race reaches the check after the
reservation.

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
- **`v5` is a superset of `v4`**, as `v4` is of `v3`: a `v5` document may use every key any earlier
  version allows. Three gates in the loader compare for equality and would refuse exactly that, and
  item 4 changes each to "this version or later": `require_v3` accepts only `v3` and `v4`
  (`policy.py:912`, for `environment`, `authority` and `mode`, and its own comment at `910-911`
  predicts this); `require_v4` accepts only `v4` (`policy.py:931`, for `version` and `controls`); and
  the entry-key check refuses `data` unless the schema is exactly `v4` (`policy.py:1171`). T244 loads a
  `v5` document using every earlier key. **A standalone authority document labelled `v5` is accepted**
  (`authority.py:1194` checks membership of the supported set), deliberately: `v5` adds nothing to that
  document's closed key set, so it means exactly what it means at `v3`, and a deployment that moves its
  policy to `v5` may move both files together without being refused for it.
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

Above the ceiling, **in the order the code does them**, because the order is the argument:

1. **The executor is not called.**
2. **`EFFECT_RESERVATION_REFUSED` is appended** with `data.reason = "attempt_ceiling"`,
   `data.attempt` and `data.max_attempts`, on the existing event type, so the history says why.
3. **A `blocked` receipt** is written, keeping the decision the policy reached (`v0.1 §4.2 A1`'s
   precedent) and carrying the approval where one was consumed or presented.
4. **The record is released as `FAILED`, where the release succeeds**, through `begin_execution`
   then `fail_effect`, with an `error` naming the ceiling:
   `attempt 4 refused: max_attempts is 3 (SPEC-v0.7 §5)`. `FAILED` is true: nothing ran.
   `begin_execution` here is a state transition that `fail_effect` requires, and
   `EXECUTION_STARTED` is **not** appended, because that event is the claim that something started.
5. **`ActionDenied(reason="attempt_ceiling")` is raised**, unless step 4 refused, in which case the
   store's own exception propagates instead.

**Steps 2 and 3 come before step 4, and that is the whole of why the order is written down.**
`begin_execution` and `fail_effect` can refuse: with `DuplicateEffect` or `AmbiguousEffect` where the
record moved (§5.7), and with `InvalidArgument` where it moved under a different `action_id` with a
dead lease. The caller then gets the store's exception, as `v0.1 §5.5` requires, and the event and the
receipt exist either way, because they were written first: a refusal that took its own evidence with it
would leave a `RESERVED` record nothing in the history explains. **Step 4 may therefore not happen at
all**, and the record is then `RESERVED` at the refused attempt under a live lease while the receipt
says `blocked` at that same attempt. What happens next is what §5.2's last paragraph describes, and it
costs a human rather than an execution.

**The refused number is spent.** The store assigned attempt *n* + 1 before the check could look at it,
and releasing the record `FAILED` does not give it back: the next renewal is *n* + 2. So an operator
who raises `max_attempts` from 2 to 4 after a refusal buys **one** further dispatch, not two, because
attempt 3 is gone. §5.7's reason for counting a resolved attempt is that *it was dispatched*, and this
one was not, which makes the arithmetic worth stating rather than leaving to be discovered. Giving the
number back would mean a second write on the refusal path and a record whose attempt number moves
backwards, which `v0.7 §5.6` spent a whole build item making impossible. The released record's `error`
also carries the ceiling text rather than the last executor outcome, so `ctrlrun effects` shows why the
key stopped rather than what the previous attempt's remote said; the events and the receipts still carry
that, in order.

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
already at or above the ceiling, the call is refused the same way. **The fast path refuses only a
`FAILED` record**, normatively: a record in any other state, `AMBIGUOUS` above all, passes it untouched
to the reservation, which refuses or reconciles it exactly as at 0.6.1, and T245's route and G15 both
depend on that. It refuses with `EFFECT_RESERVATION_REFUSED` (`data.reason = "attempt_ceiling"`), a
`blocked` receipt and `ActionDenied(reason="attempt_ceiling")`, with nothing reserved, nothing released and
nothing consumed, and **no approval request is created**. A presented approval is recorded against the
refusal it met, on `v0.6 §7.2.1`'s rule, and is not spent. It saves a write, it saves a
presented approval from being spent, and **on the sequential `FAILED` route** it saves a human from being
asked about an attempt that could never run, which `v0.3 §4.3`'s first reason says a denial must never do.

**On the other routes a human is still asked, and this list is not exhaustive.** The fast path answers
only for a record that is already `FAILED` at the ceiling. Wherever the record is in any other state, the
approval gate is reached first and the saving above does not apply. Two such routes are known, and a
reader should assume there are others rather than read this as a closed set:

- **The reconcile route, which is the kernel's own primary public route** and the one T245 and G15 are
  built on. Attempt N ends `AMBIGUOUS`; attempt N+1 carries a `reconcile` hook and presents nothing; the
  fast path correctly lets the `AMBIGUOUS` record through; `_secure` reaches `_presented` **before** any
  reconcile runs (`control.py`'s `_secure`, first `_take`), so a **new** approval request is created and
  `ApprovalRequired` is raised. A human grants it, the agent retries, the hook moves the record to `FAILED`
  at N, the second `_take` renews to N+1 **and consumes the approval**, and only then does the check refuse.
  One human answer spent, and nothing run. **This is stated and not closed**: re-reading the record between
  the reconcile and the second `_take` would save the approval and would destroy "the check alone is
  reachable through a public route, with no seam" below, leaving the check with no seamless route and G15
  with nothing to grade it through. The cost is one wasted answer; the alternative is an unexercised
  guarantee, and `v0.4 §1.3`'s rule is that a guarantee that could not have failed is not a pass.
  **And §6's precondition provider runs in front of the ceiling on this route, measured.** `_recheck`
  sits immediately before each `_take` (§6.2), and this route takes twice, so under `max_attempts: 1` a
  doomed attempt calls the operator's provider **three times** before it is refused: once on the request
  pass that creates the approval, and twice on the retry. §6.6 says the provider is spent only where its
  answer can matter, and here its answer cannot: the ceiling refuses whatever it returns. **Worse, a
  provider that raises on that attempt changes the reason the operator is told**: the refusal is
  `ApprovalMismatch(reason="precondition_unavailable")` and not `attempt_ceiling`, no effect record is
  written, the record stays `AMBIGUOUS` at N, and the `reconcile` hook never runs. Both are consequences
  of the ordering `v0.3 §4.3.1` fixes and §5.5 amends, not of either mechanism alone, and both are
  stated here rather than closed: moving the ceiling in front of the fetch would mean resolving the
  record before the approval gate on a route whose whole point is that it does not, which is the same
  seam this bullet declines above. **The sequential route is clean**: where the record is already
  `FAILED` at the ceiling the fast path refuses before the approval gate and the provider is called
  **zero** times, which is §6.6's principle holding wherever the fast path can answer.
- **`Control.evaluate` does not see the ceiling** (§7). An adapter decides whether to interrupt for a human
  through `ctrlrun.adapter.needs_approval`, which calls it (`adapter.py:408-450`); it takes an `Action`, not
  an effect key, and it writes nothing, so it cannot resolve a record to count on. A framework can therefore
  put an approval in front of a human for an attempt `execute` then refuses. Teaching `evaluate` the ceiling
  would mean resolving an effect template and reading the store inside a method whose contract is a decision
  about an action, and that is not this milestone's change to make.

§1.4 item 2's cost line covers both: **one wasted answer per refused attempt**, never an execution.

It is a fast path and **never the guarantee**. Two callers who both read attempt N−1 both pass it, and
only the check on the assigned number stops the second. The two defences are independent, so each gets
its own deterministic test, because two defences against one failure hide each other's mutations, and
they are told apart by what they leave: the fast path writes nothing and the record keeps its attempt
number; the check after the reservation leaves the record `FAILED` at the refused number, with
`EFFECT_RESERVED` before the refusal.

**The check alone is reachable through a public route, with no seam.** Attempt N's executor raises
`TimeoutError`, so the record is `AMBIGUOUS` at attempt N. Attempt N+1 carries a `reconcile` hook that
answers `not_executed`. The fast path reads an `AMBIGUOUS` record, which is not its business, and lets
the call through; `_secure`'s first `_take` is refused with `AmbiguousEffect`, the hook moves the
record to `FAILED` at N, and the second `_take` renews it to N+1 (`control.py:1331-1351`). Only the check
after the reservation can refuse that, and it must. T245 and G15 use exactly this route; T245b shows
the fast path by the evidence it leaves on a record already `FAILED` at the ceiling.

**Every reservation method MUST return the attempt number it actually wrote.** The check compares the
number `Control` is handed, and the token (§4) is derived from it, so a store that returns the number it
planned while writing a different one defeats both. §5.6 says where Postgres does that today.

**The order of `Control.execute`, amended.** `v0.3 §4.3.1` fixes it as `principal_expired` →
authority → policy → approval → reservation → execution. It becomes `principal_expired` → authority →
policy → **the ceiling's fast path** → approval, **with §6's precondition fetch** → reservation →
**the ceiling's check** → execution.

**Rejected: a new keyword on `reserve_effect`**, because it changes the frozen protocol. **Rejected: a
read before reserving as the only check**, because two callers who both read N−1 both get through,
which is attribution and not prevention. **Rejected: counting receipts or events**, because the record
already carries the number and the store is the only thing that assigns it atomically.

### 5.6 Attempt numbers never repeat: item 3a

Two properties, and on Postgres neither holds yet: **no two reservations of one key carry the same
attempt number**, and **the number a reservation method returns is the number it wrote**. v0.7 makes the
attempt number load-bearing twice (the token and the ceiling), so both are fixed first, in a build item
of their own: **item 3a, stacked immediately before item 3, with an independent review because it
changes a store.**

**The stale renewal.** On Postgres the plan is read with a plain `SELECT` under `READ COMMITTED`
(`postgres.py:744-748`, `476-484`) and the renewal is `UPDATE … WHERE effect_key = %s AND state =
'failed'` (`postgres.py:787-800`). Between the read and the `UPDATE`, another process can renew to *k+1*,
run, fail and commit, leaving the record `FAILED` again; the stale `UPDATE` then matches and writes *k+1*
a second time. Item 3a conditions it on the attempt it planned from:
`… WHERE effect_key = %s AND state = 'failed' AND attempt = %s`. A stale renewal then matches no row and is
refused exactly as a lost renewal race is refused today (`postgres.py:802-807`).

**That closes the race only if the attempt number is monotonic, and on Postgres it was not.** Only a renewal
changes the number, and every other transition computes the same number (`state.py:281-342`, `_transitioned`
and `_resolved`); but on Postgres each of them *writes* that number back, from its own earlier read, under a
`WHERE` on `effect_key`, `action_id` and `state` alone (`_write_effect`, under `resolve_effect`, `extend_lease`,
`hold_continuation` and §4.2.2's kept `AMBIGUOUS` write; `_transition`, under `begin_execution`,
`commit_effect`, `fail_effect` and `mark_ambiguous`). A caller that retries one `Action` object reuses its
`action_id`, so `(action_id, state)` can come round again at a newer attempt between the read and the write,
and the stale write then puts the older number back: a human's `resolve_effect` decided on `AMBIGUOUS` at 1
lands on `AMBIGUOUS` at 2 and writes `FAILED` at **1**, and the next renewal hands out 2 a second time. Item 3a's
independent review reproduced it. So every `UPDATE` on `effects` is conditioned on the attempt it read as well,
with the row count checked as before, and a write whose row moved matches nothing and is re-read. With that, and
only with it, the number moves only by a renewal, and the renewal's condition closes the race rather than
narrowing it. There are three `UPDATE`s on the table and one `INSERT`, which lands only where there is no row,
and all four are covered (§12.3a).

**What the re-read then does is not the same answer for all of them, and the difference is an outcome.**
`commit_effect` and `mark_ambiguous` carry what the executor did, or the fact that nobody knows; refusing them
writes nothing, and the effect record, which is what gates the next renewal, is then silent about an attempt
that may have acted. They are **re-issued once** against the re-read where it says the record is still this
attempt's and still in a state the write may be made from, so the outcome lands on the newer attempt.
`begin_execution` and `fail_effect` are refused there, because `FAILED` asserts that nothing happened and
attempt 2 is running. Everything else, `resolve_effect` and §4.2.2's kept `AMBIGUOUS` write among them, is
refused with the type the re-read's record earns and a message naming the move (§12.3a, T246c).

**And this is a store-level race, not the human's.** Conditioning `resolve_effect` on the attempt it read closes
the milliseconds between that read and its own write. The window a person actually stands in is longer: they
inspect, they decide, they run `ctrlrun resolve`, and the attempt can move between the inspection and the
command. `resolve_effect` carries no attempt number and does not look at `action_id`, so a human who read
attempt 1 can still resolve attempt 2's ambiguity with no `action_id` reuse anywhere. An attempt argument on the
CLI and on the operator tool would close that one, and it is a follow-up rather than part of item 3a (§12.3a).

**The lost `COMMIT`.** Where the reservation's `COMMIT` is lost, `_authorize_and_reserve` re-reads and,
where the write did not land, re-issues the operation through `_authorize_and_reserve(..., retrying=True)`
from `_resolve_lost_renewal` and `_resolve_lost_insert` (`postgres.py:602-622`, `660-663`, `696-699`).
**The re-issue's result is discarded** and the original plan's reservation is returned. The two paths
open at different moments. On a lost **renewal**, another process renews and fails between the lost
commit and the re-read; the re-read finds `FAILED` and re-issues (`A2_REISSUE`), the re-issue writes *k+2*,
and `Control` is handed *k+1*. On a lost **insert**, the re-issue runs only when the re-read finds no
record at all (`A1_REINSERT`, `postgres.py:690-699`); a record already there takes `A1_REFUSE` and raises
`DuplicateEffect` (`705-713`) before any re-issue. So the insert's window is later, between that re-read
and the re-issue's own planning read: another process inserts attempt 1 and fails there, the re-issue
renews to 2, and `Control` is handed 1. Either way two dispatches share a token, the ceiling
undercounts, and `Control.resume`, which reads the stored number (`control.py:992`), later derives a
different token inside the same attempt, which breaks T233. Item 3a returns the re-issue's reservation on both paths, so the number
`Control` holds is the one on the record.

**On SQLite and the in-memory store** the renewal reads and writes inside one `BEGIN IMMEDIATE`
(`state.py:1543-1560`) or under one lock (`state.py:846-857`), and neither has a lost-commit path, so
neither defect exists there. SQLite's `UPDATE` gains the same `AND attempt = %s` for defence in depth,
and it is stated as what it is: **an equivalent mutant on SQLite**, since `BEGIN IMMEDIATE` makes the
stale case unreachable, so removing it there fails no test and T246 on Postgres is its only test.

Both are changes inside existing methods: a tighter `WHERE` clause and a different return value of the
same type. Neither adds a method, changes a signature or changes a decision `plan_reservation` makes.

**The tests open the windows on purpose.** Two processes renewing concurrently, which an earlier draft of
T232 leaned on, do not: the window needs one renewal stalled between its `SELECT` and its `UPDATE` long enough for another process to
renew, run and fail. T246 holds the stale `UPDATE` with the proxy the tests own until that has
happened; T246b swallows a reservation's `COMMIT` with the same proxy and opens each path's own window,
the renewal's before the re-read and the insert's between the re-read and the re-issue's planning read, and
asserts the attempt returned equals the attempt stored. The store
conformance suite states both properties and gains a case for them if its barrier can reach between a
store's read and its write; `v0.6 §2.4` says the in-process case cannot open windows inside a store,
and if that holds here §12 says so and T246 and T246b are the only tests of the defence.

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
  **The consequence, stated: `max_attempts` bounds attempts, not executor invocations.** One attempt can
  invoke the executor many times, because a `Suspended` executor holds its reservation and every `resume`
  runs on that same attempt (`v0.2 §6.9`). Under `max_attempts: 1` an executor that suspends every round
  can be resumed indefinitely, with the approval consumed once and the record still `EXECUTING` at attempt
  1. That is not a hole in the ceiling: an elicitation round is a continuation of one dispatch, not a
  second one, and what a ceiling exists to bound is dispatches. It is bounded where it matters by
  `max_elicitation_rounds` on the gateway (`v0.2 §6.9.2`); **a direct `Control.resume` caller has no such
  bound, and v0.7 does not add one.**
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
| The fingerprint is computed and the store hands the request back without it | **refused**: `ActionDenied(reason="precondition_missing")`, and the request is **withdrawn** where this call can reach it, so no later presentation of it compares nothing (§6.4, and its residual); a `denied` receipt keeping `decision: approve` |

Every refusal on the presenting pass appends `APPROVAL_INVALIDATED` with `data.reason` naming which, and
the two fingerprints it compared, hashes only. The three reasons are distinct because a mismatch and an
ordinary `ApprovalMismatch` share a type, and a test that asserted only the type could not tell which
guard fired (`CONTRIBUTING.md`, the first of the four shapes of a false green). Every test of this section asserts the `reason`.

### 6.3 Why a mismatch leaves the approval granted

On `v0.6 §7.2.1`'s precedent, for its three reasons applied to the world rather than to the policy.

- **A human's yes is not spent where the provider reports a world different from the one they saw.**
  They approved the action against the state they looked at. Consuming the approval on a refusal
  would make the operator ask again for an action that was refused by a reported fact, not by the
  human. (It can still be spent on a world that moved after the comparison: §6.7's segments 2 and 3.
  This row is about the refusal, not about that.)
- **The approval authorizes nothing on its own.** It is bound to one `action_hash`, and every
  presentation is rechecked against what the provider reports. While the provider reports a different
  state the approval is refused at every presentation; if it reports the state the human saw again,
  the approval is accepted for exactly the action it was granted for.
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

**And a fingerprint that is computed and then not recorded is refused on the request pass, with the
request withdrawn.** Refusing only at presentation was not enough, and an independent review showed
why: where the fingerprint never reaches the record, *neither* side has one at presentation, which is
the first row of §6.2's table, so any call naming no provider consumed the approval with nothing
compared. That is the skip this section forbids, reached through the very causes it lists. So the
request pass reads its own request back, through the object the provider returned **and** through
`get_approval`, and where the fingerprint is not there:

- the action is refused, `ActionDenied(reason="precondition_missing")`, before any human answers;
- the request the provider already recorded is **withdrawn**, through `deny_approval`, an existing
  store method (`v0.6 §9.2` is not amended): `check_consumable` then refuses it for ever, by a
  denial's own reason. A grant that landed inside the window is withdrawn by being **spent** instead,
  `consume_approval` on an approval nothing reserved for and nothing ran on, because `deny_approval`
  answers only a pending request;
- `APPROVAL_INVALIDATED` records the reason, which of the two withdrawals was used, and the
  fingerprint that was computed and not recorded.

**The residual, stated, and it is wider than one race.** `Control` learns that a request exists only
when the provider returns, so anything that happens to the row before that is beyond this refusal:

- an approval granted **and presented** inside `request()` is spent before there is anything to
  withdraw;
- **a provider that records a request and then raises** leaves the same orphan with no race at all,
  and `Control` never learns its id. The exception is the provider's and propagates; the kernel logs
  a warning naming the action and saying a fingerprint was computed, which is all it can do;
- a store that refuses the withdrawal itself leaves the request answerable. The action is still
  refused, with its receipt and its `ACTION_DENIED`, and `APPROVAL_INVALIDATED` says
  `not_withdrawn:<status>` rather than claiming a write (§6.2's table, and the reason strings of §9.2).

So the claim this section makes is bounded: **a request this call can reach is withdrawn, and the
evidence names what was done to it.** Closing the rest needs a store call that records the request and
its fingerprint together, and `StateStore` is frozen (`v0.6 §9.2`); §12.5 records the alternative that
would remove the *cause* rather than close the window, and why v0.7 does not take it.

**What a withdrawal looks like to everything that reads denials.** It is a `deny_approval`, so
`find_denied_request` returns it and the gateway's *"no is an answer"* pre-check (`v0.2 §6.10`) refuses
every call for that action hash until the request expires, as though a human had said no. That is
fail-closed, bounded by the TTL and traceable through the approver
(`ctrlrun:precondition-not-recorded`), and it exports an in-process misconfiguration to a path that
never asked for a fingerprint, which is the cost of using an existing store method rather than adding
one.

**What that costs at the gateway and the ACS hook, stated with its bound.** Both present the newest
granted approval for the action's hash and create a new request only when they find none
(`server.py:709-725`, `acs.py:202-218`). Where the newest granted approval carries a fingerprint, every
identical call through that path is refused `precondition_missing` and no fresh request is ever
created, **until that approval expires** (fifteen minutes by default, `v0.1 §4.1`) or is consumed by the
path that names the provider. That is a denial of service on that one action through that one path,
bounded by the approval's `expires_at`, and it is the fail-closed direction: the alternative is a path
that cannot recheck spending an approval that was granted conditional on a recheck. An operator who
wants an action reachable through both paths declares no provider for it.

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

**The refusal is raised from this read, and nothing is written for it.** Two things follow, and an
independent review found both wanting in the first build. The refusal is not left to the store call:
a `pending` record a human grants between this read and that call would be consumed there with nothing
compared, which is the skip §6.4 forbids, so the verdict this read found is the verdict that is raised.
And **no write goes to the store on this path at all**, not even the lapse of an expired grant: whose
clock decides expiry is `v0.1 §4.2 A3`'s question and the answer stays the **store's**. A `Control`
whose clock runs ahead of its store's would otherwise send a grant the store still calls live to
`consume_approval`, the store would spend it, and the row would say `consumed` while the events said
expired and the receipt said blocked. The row keeps what the store gave it; `APPROVAL_EXPIRED` records
the lapse this clock saw; nothing is reserved and nothing runs; and `check_consumable` refuses that
grant at every later presentation, on whichever clock. Where no precondition is in play this read
decides nothing, and the store call is 0.6.1's, on the store's clock, including its lapse write.

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

The fingerprint goes to four places, all of them hashes: `approvals.precondition_fingerprint`,
`APPROVAL_INVALIDATED`'s data on a refusal, `APPROVAL_CONSUMED`'s data where a comparison was made
(§6.11, which is how a resumed leg records the comparison its first leg made), and the receipt's two
fields. This enumeration exists to be complete, so a fifth place is a change to this paragraph. It is not added to the
webhook document (`ctrlrun.approval_request/v1` is unchanged), to `ctrlrun inspect`, or to anything a
human is shown to decide with: a hash tells a human nothing about the world they are approving, and the
human reads the world in their own systems.

### 6.11 `ctrlrun.receipt/v4`, the migration, and the rehash rule

**Two receipt fields**, `precondition_at_request` and `precondition_at_recheck`: the approval's stored
fingerprint and the one computed on the presenting pass, `null` where there was none. On a refusal they
say which side moved or was missing; on a committed action they are equal, and the receipt records that
the world was checked. **A resumed leg's receipt says the same**, and that needs one more write: the leg
that consumed the approval records what it compared on its `APPROVAL_CONSUMED` (hashes only, and nothing
at all where nothing was compared), and `Control.resume` reads it back. A suspended action writes no
receipt on its first leg, so the resumed leg's is the only receipt it ever gets, and without this the one
comparison that did happen left no trace in it. The schema becomes **`ctrlrun.receipt/v4`**, and it moves once, in item 5, which
is why item 5 is last.

**One column**, through migration **`0005_precondition_fingerprint`**: `approvals.precondition_fingerprint
TEXT NULL` on SQLite, `COLLATE "C"` on Postgres as `0004_policy_provenance`'s column is. It is a column and
not a field in some JSON because `ApprovalRecord` is rebuilt from columns (`v0.3 §5.2`, `v0.6 §3.7`).
Existing rows keep `NULL`, which is exactly what an approval requested without a provider means. The
migration is forward-only, runs in one transaction, and is proved against a database **built by 0.6.1's
own code** (`v0.6 §3.5`), in both directions: 0.7 opens and migrates it keeping every row; 0.6.1 refuses the
migrated database at open with `SchemaMismatch` naming `0005` and both versions (T264). The runner is why a
column is possible at all, and that makes the change safe, not cheap.

**The rehash rule, which amends `v0.6 §6.4`'s last bullet: hash what was stored.** `chain_hash()`
recomputes over `to_dict()`, and `to_dict()` stamps the current schema and the current key set
(`receipt.py:314`, `318-345`). A `v4` binary rendering a `v3` receipt as `v4` would recompute a different
document and report every receipt a released 0.6 wrote as `content_altered`. So, from item 5:

- **A receipt read from a store keeps the document it was read from**, in a private field filled on read
  exactly as `hash` already is (`state.py:1211`, and Postgres the same), and **`chain_hash()` hashes that
  document when it is present**, through `canonical_bytes` as always. A receipt this binary is writing has
  no stored document, and `chain_hash()` hashes `to_dict()`, as today. The stored JSON is `to_json()` of
  the dictionary that was hashed at write time, so the recomputation over the stored document equals the
  stored hash for every receipt nobody touched, whatever schema wrote it. (The in-memory store keeps the
  objects it was handed rather than documents, so it has nothing to re-read and nothing a row-writer
  could reach.)
- **The stored document survives nothing but the read that set it**, and three rules make that exact,
  because each of the two obvious dataclass answers breaks shipped code:
  - **(a) The field is not an `__init__` parameter and is not carried through `dataclasses.replace()`.**
    If `replace()` copied it, G11's own tamper, `replace(target, decision_reason=...)` on a read-back
    receipt handed to `verify_chain` (`verify/scenarios.py:2097-2103`), would hash the untouched stored
    document, the chain would verify, and G11 would report a correct kernel as failing. And a read-back
    receipt written again (`verify/scenarios.py:2084`) would reach `put_receipt`, whose
    `replace(receipt, seq=..., prev_hash=...)` (`state.py:1153-1154`, `postgres.py:1513-1514`) would carry
    the old document into `chain_hash()`, store that stale digest and advance the head with it while the
    row's JSON is the new `to_dict()`, so the row would read back `content_altered`. Because it is not
    carried, a modified copy has no stored document and is hashed from what it now says.
  - **The store read path therefore sets the field after its own `replace(..., hash=...)`**, not before
    (`state.py:1211`, `postgres.py:1561` both build the receipt with `replace(Receipt.from_json(...),
    hash=...)`, which under (a) would otherwise discard the document it had just been given).
  - **(b) `put_receipt` hashes the exact dictionary it serializes, and never takes the stored-document
    branch**, so the hash written to the column and the document written beside it come from one
    dictionary, and the read-time hash of that document is the write-time hash.

  **A store outside this package does not get this**, and saying so here is better than leaving an
  implementer to find it: the field is private, `_stored_receipt` is its only writer, and §9.2 adds no
  public name for either. A third-party store's read-back receipts are hashed from `to_dict()`, as at
  0.6.1: an untouched `v3` or `v4` row still verifies, and a key added to one of its stored documents
  does not show. Whether that setter should be public is the maintainer's to decide on its own merits,
  not this item's to settle by adding a name.

  Nothing else is affected: no other reader calls `chain_hash()`. The in-memory store keeps the objects it
  was handed; the JSONL sink and the OTel sink receive `put_receipt`'s fresh return; `ctrlrun receipts`,
  the reporting payloads, the operator server's tools and verify's counterexample only display; a resumed
  action's receipt is built fresh by `_record`; and the store conformance suite checks `put_receipt`'s
  return (`conformance/store/suites.py:1506`). Write-time and read-time hashes agree for every schema:
  SQLite stores `to_json()` of the hashed dictionary and Postgres `json.dumps(to_dict(), sort_keys=True)`
  (`postgres.py:1524`), both as `TEXT` (`migrations.py:143`, `239`), and `canonical_bytes` refused a float,
  a non-string key and a lone surrogate at write, so parsing and canonicalizing reproduces the write-time
  bytes. `v1` and `v2` documents carry no `seq` and are never hashed.
- **Every tamper the schema bump could hide is then a hash mismatch by construction.** A key added to a
  stored document, a relabelled `schema`, a removed `schema`, a `schema` this binary has never heard of: each
  changes the stored document, so each is `content_altered` at its `seq`, with no rule about key sets for
  the reader to get wrong. Without this, a row-writer could add `precondition_at_recheck` to a receipt
  0.6.1 wrote; a reader rendering it under `v3`'s keys would leave the key out of the hash, the chain would
  verify, and a reader that parsed it would show a fabricated field.

  **Including a key whose value has no canonical form.** A float or a lone surrogate made `chain_hash()`
  raise out of the walk, so one such row stopped the whole read: `ctrlrun receipts --verify-chain` exited
  with no report at all, and a forged field at another `seq` went unnamed. A document this reader cannot
  canonicalize is a document nothing here wrote, since `put_receipt` hashes what it serializes, so it is
  `content_altered` at its `seq` like any other altered document, named by the canonicalizer's exception
  **type** and never its message, which quotes what it refused. The rows that link to it are told that it
  has no computable hash. A malformed value of a key a schema *declares*, a float among `controls` say, is
  a document that cannot be parsed at all and behaves as it does at 0.6.1, which the next bullet covers.
- **`from_dict` never raises inside a store read over the *schema* or over an added *key*. Over a
malformed **value** of a key a schema declares, it still does, exactly as at 0.6.1**, and that is
stated rather than smoothed over: a float among `controls` makes `_controls_of` raise out of
`receipts()`, so one `UPDATE` blinds `ctrlrun receipts`, `--verify-chain`, `inspect`, `stats` and G11
at once. v0.7 neither introduces nor widens it, and fixing it needs either a new name in
`CHAIN_BREAKS` -- a closed set and a `v0.6 §6.5` surface -- or a reader that can walk raw rows, which
is a shape this milestone does not have. It is deferred with that blast radius written down, and §12.5
records it for the roadmap. `receipts()` builds
  every row with `Receipt.from_json` (`state.py:1211`), so a `from_dict` that raised on one tampered row
  would raise out of `receipts()` and blind every reader at once: the chain walk, `ctrlrun receipts`,
  `inspect`, `stats` and G11. An absent or unknown `schema`, or an extra key, is left to the hash to
  report. `from_dict` reads only the fields the document's declared schema has (the precondition fields
  only from a `v4` document), so no reader ever surfaces an undeclared key's value. A row that cannot be
  parsed at all, not a JSON object or missing a field every schema has, behaves as it does at 0.6.1; if
  item 5 changes that, §12 says how.
- **Each schema's key set is exactly its released writers'**, counted at the top level of the document:
  `v1`, **19** keys, written by 0.1.0 and 0.2.0; `v2`, **21**, by 0.3.0rc1, 0.4.0 and 0.5.0 (`v1`'s plus
  `execution` and `would_have`); `v3`, **26**, by 0.6.0 and 0.6.1 (`v2`'s plus `seq`, `prev_hash`,
  `policy_hash`, `policy_version`, `controls`); `v4`, **28**, `v3`'s plus the two precondition fields.
  "Extra" means a top-level key outside that set, never an omitted one. A document that *lacks* a key its
  schema has is the unreleased item-6-era `v3` database `v0.6 §6.4` already describes, and it already
  breaks as that section records.
- **For display, every schema renders under its own label and key set.** `to_dict()` of a `v4` receipt
  renders `v4`; of a `v3` receipt, the `v3` document; of a `v2` or `v1` receipt, that version's label and
  keys. 0.6.1 rendered `v1` and `v2` receipts under the `v3` label; that ends, and `ctrlrun receipts
  --json` shows a pre-v0.6 receipt's own label, which item 6 lists as a visible change. The hash no longer
  depends on this rendering at all.
- **`unchained` is decided by `seq` alone**, as the chain reader decides it today (`receipt.py:570-571`),
  never by the label. That misclassifies nothing 0.6.1 wrote: every store assigns `seq` before it
  serializes the receipt (`state.py:1153-1163`, Postgres's `put_receipt`, the in-memory store at
  `state.py:673-674`), and migration `0002` added the chain columns without rewriting any stored JSON
  (`migrations.py:181-183`), so a pre-chain document has no `seq` and a chained one has its own. A chained
  receipt relabelled `v1` or `v2` is caught by the first bullet, as a changed document.

**Rejected: refusing, or parsing only the declared keys and comparing key sets.** An earlier draft did
both. Refusing blinds every reader, as above. Parsing only the declared keys and rendering under them
means an added key is neither parsed nor rendered, so the recomputed hash matches and nothing reports
anything, unless `Receipt` carried a marker saying "this document had extra keys", which would be a public
name added to a frozen record for a check the stored document makes for free.

**No 0.6 process may be running when a 0.7 process opens the store.** A store checks migrations only at
open (`postgres.py:259`), so a 0.6.1 process already running when `0005` is applied keeps running
against the migrated database, and it meets the schema bump in two ways. It reads approvals through the
columns it knows, never sees the fingerprint, and consumes a fingerprinted approval with no recheck.
And it rehashes every `v4` receipt a 0.7 process writes under `v3`'s keys, so `verify_chain` in that
process reports a correct chain as `content_altered` and its head as mismatched.

**The trigger is the first receipt a 0.7 process writes, not the first caller that passes
`preconditions=`**, and an independent review measured it: a 0.6.1 reader held open across the
migration misreports a chain written by a 0.7 process that named no provider at all. The kernel cannot
detect that process from the new one, so the rule is operational and stated as one, here and in the
upgrade notes item 6 writes: **stop every 0.6 process before any 0.7 process opens the store.**

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
| The gateway's `tools/call` | no | **no provider**, and it **refuses** a presented approval that carries a fingerprint (`precondition_missing`) | it goes through `Control.execute`, and a policy document cannot name a Python callable; an approval requested with a fingerprint was granted against a world this path cannot recheck, and the path keeps refusing until that approval expires, with no fresh request created (§6.4) |
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

### 7.1 The same column for the attempt ceiling

Item 4 amends `v0.3 §4.3.1`'s **order** (§5.5) and so it owes the same enumeration, for the reason
`v0.3 §4.3.1` exists at all: `Control.delegate` let an expired credential mint permanent authority not
because a check was wrong but because nothing listed the other paths. The "no" rows are written down as
deliberately as the "yes" ones, and two of them are where this milestone's review found its findings.

| Entry point | Applies the ceiling | Why |
|---|---|---|
| `@protect` → `Control.execute` | **yes**, both defences: the fast path before the approval gate, and the check on the assigned attempt number before the executor | the only path that reserves and then dispatches (§5.5) |
| `Control.execute` called directly | **yes**, the same two | the same method |
| `Control.execute` in observe mode | **records, does not enforce**: both defences write `would_have.blocked_reason = "attempt_ceiling"` and the action runs | `v0.3 §6.2`: observe mode suppresses CTRLRun's decisions, not the record of an effect that happened |
| `Control.evaluate` | **no** | it takes an `Action` and not an effect key, and it writes nothing, so it can resolve no record to count on. A caller can therefore be told `approve` for an attempt `execute` will refuse (§5.5). Its docstring says so |
| `Control.resume` | **no** | it reserves nothing, so there is no new number to compare. One attempt can invoke the executor many times through it, which §5.7 states |
| `Control.delegate` / `Control.revoke` | **no** | they reserve no effect |
| The gateway's `tools/call` | **yes**, through `Control.execute`; `ActionDenied(reason="attempt_ceiling")` maps to `-41001` with the reason in the error data, unchanged | it is `Control.execute` behind a transport (§5.5) |
| The gateway's approval pre-check | **no** | it reads `Control.evaluate`, and inherits that row exactly |
| `ctrlrun.acs`'s request hook | **yes**, through `Control.execute`; the refusal becomes `deny` with the reason in `codes`, unchanged | the same shape |
| An adapter's protected tool → `@protect` → `Control.execute` | **yes**, the same two | the `@protect` row reached through a framework (`v0.5 §4.1`) |
| `ctrlrun.adapter.needs_approval` → `Control.evaluate` | **no** | `Control.evaluate`'s row; it can interrupt for a human on an attempt that will then be refused (§5.5) |
| `ctrlrun.verify.run` | drives the first row | G15 grades the check through §5.5's public route, and G5 and G14 select only where a ceiling permits a renewal (§8.9) |
| `ctrlrun approve` / `ctrlrun deny` / `WebhookApprovalProvider`'s callback / the operator server's write tools | **no** | they record an answer to a request; nothing reserves, and `Control.execute` applies the ceiling when the answer is presented |
| `ctrlrun resolve` → `resolve_effect` | **no**, and the attempt it resolved still counts | it moves a record out of `AMBIGUOUS` and reserves nothing; §5.7 argues why a resolved attempt counts |

**No row reserves without passing through the check**: `reserve_effect` and `consume_approval_and_reserve`
are called from `Control._take` and `Control._observe_take` and from nowhere else in the package, and both
are reached only from `Control.execute` (enforce) and `Control._observed` (observe), which are the first
three rows.

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
Graded against Postgres: an injected skew is reported and an aligned clock is not. **A backend whose
`clock_skew` is present but is neither `None` nor a `ctrlrun.state.ClockSkew`, or whose read raises, fails the
case** by name: `Control` would ignore it silently in production, so the suite is where its author finds out.
`not_applicable` is reserved for a backend on which the attribute is **absent**. On SQLite and the in-memory
store it is `not_applicable` with the reason *this backend exposes no clock measurement; SQLite
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
raise; a log record names the failure. And from `Control`'s side: a store whose `clock_skew` is not a
`ClockSkew` (a string, a `timedelta`, a look-alike dataclass with the same fields), and one whose `clock_skew`
property raises, each leave the action's outcome, receipt and events exactly as without it, with one `WARNING`
per store per kind of error across ten actions, not ten.

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
Each inside an executor run, against a real target where one can be made: a refused or timed-out connect
to a loopback port with no listener (`ConnectionRefusedError` on Linux; macOS drops a SYN to a port that is
bound and not listening, so there the test closes the listener instead, §12.2.1); a connect that times out, bounded (a full loopback backlog where the platform drops rather than refuses,
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
succeeded and the server closed it; a redirect followed by an opener the test built with `urllib` handlers
of its own. Whatever each raises, inside an executor run, it is never `NotExecuted`. An opener the test
built whose only connection is refused before any byte of the run was offered **is** claimed, because
the claim is then true (§12.2.2).

#### T223b: A second connection in one executor run never claims after the first delivered
The register of §2.3's third condition, against every shape the first review produced, each with a real
peer that receives the whole request first: xmlrpc's retry-once on a new connection, `FancyURLopener`
following a `303` (where the Python still has it), a `build_opener` handler that runs its connection on a
worker thread, a caller's opener around the classifier's own handler and a redirect handler, an
executor's own retry-once loop around `urlopen`, a nested protected call that delivered, and two plain
`HTTPConnection`s. Each is the original exception, and under `@protect` the record is `AMBIGUOUS`. Also:
outside any executor run nothing is claimed; a thread claims only under a copy of the executor's context;
a request delivered on a connection by a thread with no register still marks that connection; a wrapper
on `OpenerDirector.open` does not suppress a true claim; and no stack inspection remains in the module.
**A thread that did not copy the context** delivers the effect and its run no longer claims, and the cost
is asserted with it: a send belonging to no run suppresses the claims of every run open at that moment.
The sibling-thread race of §2.3 is pinned as the disclosure describes it. `HTTPForwarder` marks the run it
writes in, and the httpx variant reads its proxies when the call starts, not when it fails.

#### T224: No HTTP status is `NotExecuted`
Responses of 301, 303, 400, 401, 409, 429, 500 and 503: none becomes `NotExecuted`; the `30x` is not
followed (the server counts one request); `urllib` raises `HTTPError` as it would.

#### T225: `urllib` and `http.client` both, proxies included
Every case of T220 to T224 through both surfaces. Through a loopback HTTP proxy the test owns: an
unreachable proxy is `NotExecuted`; a refused `CONNECT` after the line was sent is the original exception.

#### T226: The httpx variant, and the gateway uses it
`ctrlrun.gateway.transport.request`: a refused connection is `NotExecuted` chained from `httpx.ConnectError`;
a reset after the request is the httpx exception; a pooled or caller-supplied client cannot be passed at all.
`HTTPForwarder`'s fresh path calls the same private observation function, asserted by identity. **A read
timeout after a delivered request** is `httpx.ReadTimeout` from `request()`, `AFTER_REQUEST_SENT` from the
forwarder's fresh path and `-41010` through the gateway, each with the peer's receipt of the request asserted:
the one-token mutation that maps `httpx.TimeoutException` where `httpx.ConnectTimeout` is meant survived the
whole suite until this row. **Behind a proxy** (§2.5, §12.2.10), a refused `CONNECT`, a TLS failure with the
target after the tunnel opened, and an unreachable proxy are each the httpx exception and
`AFTER_REQUEST_SENT`, never a claim. And `request()` shares the classifier's register: a request delivered
through one and a refused connection through the other, in either order, is not claimed.

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

#### T229b: `http.client` writes only through `send`, on every supported Python
On 3.11, 3.12, 3.13 and 3.14: a real loopback peer records every byte it receives for a request with a body,
headers and a proxy tunnel; the bytes handed to `HTTPConnection.send` over the same exchange are recorded by a
subclass; the two are equal. §2.3's mark lives in `send` on the strength of this, and a Python that wrote a
byte by another route would fail here before it failed anywhere that mattered.

**And over TLS**, because the claim that the count is taken above TLS rests on `HTTPSConnection` inheriting
`send` rather than overriding it: against a loopback TLS server the test owns, the **decrypted** application
bytes the server received equal the bytes handed to `HTTPSConnection.send`, and the test asserts
`HTTPSConnection.send is HTTPConnection.send` on each Python, so an override arriving in a later release is a
red test rather than a count taken below the record layer.

#### T230: G12 in verify, under the amended network guard
§8.9's G12 entry, under the guard G12's "What it amends" describes. The same subprocess asserts the guard is live
and exactly as wide as the rule: a connect to `192.0.2.1` (TEST-NET-1) is refused; a connect to `127.0.0.1` on a
port the run did not bind (a listener the test opened before installing the guard) is refused; a `bind` to
`0.0.0.0` is refused; a lookup of `localhost`, and a `connect(("localhost", port))` to a bound port, are refused;
`::1` is refused; an `AF_UNIX` bind is refused; a UDP bind to a port admits no TCP connect to it and a
datagram may not be connected or sent; a port whose socket has closed is refused; and a listener bound to port
0 is admitted at the port `getsockname()` reports. And G12 was graded, not `N/A` and not skipped.

#### T231b: A continuation leg never records `FAILED`
The kernel's row: an executor delivers a request, the remote asks for more, the executor suspends, the
remote goes away, and the continuation's connection is refused. The answer is the original exception, the
record `AMBIGUOUS` and the next attempt refused. The gateway's rows, against a real MCP upstream that
answered `input_required` with a `requestState` and is holding the exchange: a transport failure on the
continuation is `-41010` and `AMBIGUOUS`, a pre-dispatch JSON-RPC code and a `401` are relayed unchanged
and `AMBIGUOUS`. **Three controls**: the same three answers on a first leg still record `FAILED`, so a
gateway that recorded everything `AMBIGUOUS` fails.

#### T231: The gateway's `NotExecuted` carries its cause
An intercepted `tools/call` against an upstream port that refuses: the effect is `FAILED`, the client gets
`-41011`, and the `NotExecuted` the gateway's executor raised has the httpx exception as its `__cause__`,
where 0.6.1's had none (`server.py:616`).

### 8.3a Item 3a: Attempt numbers never repeat (§5.6)

Numbered T246 and T246b because they were drafted under item 4; they are item 3a's, and they land in its
pull request, which is stacked before item 3's.

#### T246: A stale renewal on Postgres never lands
With the proxy the tests own, a renewal planned against attempt *k* is held between its `SELECT` and its
`UPDATE` until another process has renewed to *k+1*, run and failed. The held renewal is refused; the record is
`FAILED` at *k+1*; no attempt number was written twice. Mutating the `WHERE` clause back to 0.6.1's makes this
test fail with two reservations carrying *k+1*. On SQLite the same condition is an equivalent mutant (§5.6), and
the table says so rather than claiming a row.

#### T246b: A lost `COMMIT` returns the attempt it wrote
Two variants, each opening its own path's window, because the two paths re-issue at different moments.
**Renewal:** the proxy swallows a renewal's `COMMIT`; before the store re-reads, another process renews the key
and fails; the re-read finds `FAILED` and re-issues. **Insert:** the proxy swallows an insert's `COMMIT`; the
re-read finds no record and takes `A1_REINSERT`; the proxy then holds the re-issue's own planning `SELECT` while
another process inserts attempt 1 and fails, and releases it, so the re-issue renews to 2. (Interleaving before
the re-read instead would send the insert down `A1_REFUSE` and never reach the re-issue, which would be a test
of a window nobody opened.) In both, the attempt the reservation method returns equals the attempt on the stored
record, and mutating that path's resolve function back to returning the original plan's reservation makes its
variant fail with the two numbers differing.

#### T246c: No stale write moves an attempt number backwards, and no outcome is dropped instead
The shape of T246, for every Postgres write that is not a renewal, under an `action_id` the caller reuses. With
the proxy the tests own, the write's `UPDATE` is held after its read while another process brings the record
back to the same `action_id` and a state the write may be made from, at attempt 2. Released, it never rewinds
the record, and what it does instead depends on what it carries: `commit_effect` and `mark_ambiguous` are
re-issued once against the re-read, so the outcome lands at attempt 2; `begin_execution` and `fail_effect` are
refused, because `FAILED` asserts that nothing happened and attempt 2 is running; `resolve_effect` and the
§4.2.2 `AMBIGUOUS` write by a contender are refused, typed by what the re-read found and naming the move. Each
variant ends by asserting that no further attempt may be taken. Every one was red before its fix: first with
the record rewound to attempt 1, and then, for the two that carry an outcome, with the outcome dropped and a
further attempt permitted. Added by item 3a after its reviews (§12.3a).

#### T246d: An unknown outcome the store refuses to record is never lost
Through `Control.execute`, on **both backends**, with no proxy: the executor runs past its lease, a contender
makes the lapsed record `AMBIGUOUS`, a human resolves it `FAILED` while the attempt is still running, and the
executor then raises `TimeoutError`. `mark_ambiguous` is refused with `InvalidArgument`, which is not a refusal
`Control` used to catch. The caller gets its own `TimeoutError`, one `ambiguous` receipt is written naming both
the executor's exception and the store's refusal, and the `EXECUTION_AMBIGUOUS` event is appended. Red before
the fix with no receipt, no event and `InvalidArgument` reaching the caller (§12.3a).

### 8.3 Item 3: The idempotency token (§4)

#### T232: A `FAILED` renewal changes the token
**The test this item exists for.** An executor reads `idempotency_token()` and raises `NotExecuted`; the
renewal's executor reads it again and commits. The two differ, and each equals
`idempotency_token_for(receipt.effect_key, receipt.attempt)` of its own receipt. On both backends. It does
not open the stale-read window on Postgres, and does not claim to: that is item 3a's T246 and T246b.

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
an empty key are `InvalidArgument`. **The `bool` row is load-bearing**: `canonical_bytes` accepts `True`, so
without the function's own check `idempotency_token_for(key, True)` would return a token (§4.2).

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

#### T245: The check on the assigned number, alone, through the public route
§5.5's route, with no seam: attempts 1 to N−1 raise `NotExecuted`; attempt N raises `TimeoutError`, so the
record is `AMBIGUOUS` at N; attempt N+1 carries a `reconcile` hook answering `not_executed`. The fast path
lets it through (the record it reads is `AMBIGUOUS`), the hook moves the record to `FAILED` at N, the second
take renews it to N+1, and the check refuses: `ActionDenied(reason="attempt_ceiling")`, `EFFECT_RESERVED`
before an `EFFECT_RESERVATION_REFUSED` whose `data.reason == "attempt_ceiling"` (the reason, not only the event's
position, since a duplicate or ambiguous refusal shares the type), the record `FAILED` at N+1, the executor called
N times. Deleting the check makes this test fail with N+1 executions.

#### T245b: The fast path, alone, by the evidence it leaves
A record already `FAILED` at the ceiling, and three calls, because the four facts need three:
1. **an `APPROVE` action with nothing presented**: refused `attempt_ceiling`, **no approval request created**;
2. **the same action with a granted approval presented**: refused, the approval **still `granted`**;
3. **an `ALLOW` action on a key at its ceiling**: refused, **no `EFFECT_RESERVED`** and the record's attempt
   **unchanged**.

Each refusal's `EFFECT_RESERVATION_REFUSED` has `data.reason == "attempt_ceiling"`. Those facts can only come from
the fast path, since the check after the reservation always leaves a reservation behind. Deleting the fast path
turns each of them over, and the three do not fail alike, which the test asserts rather than hides: calls 2 and 3
are then refused by the check, leaving a consumed approval and a reservation; call 1 is not refused at all, and
raises `ApprovalRequired` with a request created, since the check runs only after an approval is presented.

#### T247: Both backends, and the v0.6 multi-process standard
T240 to T245b on SQLite and on Postgres. On Postgres, under `v0.6`'s multi-process standard: more processes
than the ceiling allows race renewals of one key across the boundary, each executor counting its calls in a file
the parent reads, and the total never exceeds N. It depends on item 3a but cannot see item 3a missing: racing
processes do not reliably stall between a `SELECT` and an `UPDATE`, which is why item 3a's T246 opens that window
deterministically and this test does not claim to.

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
`deny` with `["ctrlrun.blocked", "precondition_missing"]` respectively. Never a skip. Through the gateway, a
second and a third identical call are refused the same way with no new request created, and once the approval
has expired the next call creates one (§6.4's bound).

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
`v4`. Five tampers on stored rows, each `content_altered` at its `seq`,
none surfacing a value, and **none raising out of `receipts()`**: a `v3` row given a `precondition_at_recheck`
key; a chained `v3` row relabelled `v1`; one relabelled `v2`; one with its `schema` key removed; one saying
`ctrlrun.receipt/v9`. With any of them present, `ctrlrun receipts` still lists every other row and
`ctrlrun stats` still counts them. **The mutation this catches**, `chain_hash()` hashing `to_dict()` for a stored
receipt instead of its stored document, leaves untouched `v3` rows verifying (a `v3` receipt renders its document
byte for byte) and relabelled or schema-removed rows failing (each renders under its own label); it is caught by
exactly one tamper, the added `precondition_at_recheck` key, which then verifies cleanly. And a sixth case, in
memory rather than on a row: a read-back receipt altered with `replace()`, as G11 does, and handed to
`verify_chain` is `content_altered` at its `seq`, and the same receipt written again reads back clean, which
fails if the stored document survives `replace()` (§6.11, rule (a)).

#### T266: The store conformance suite covers the column
A case asserting a store persists and returns `precondition_fingerprint`; a broken-store fixture that drops it
fails that case by name (`v0.6` T140's rule: every fixture fails its named case).

#### T267: §7's column, row by row
Every row whose answer is executable is driven: `@protect` and `Control.execute` recheck; `Control.evaluate`,
`Control.resume`, `needs_approval`, `InterruptApprovalProvider.wait` and the operator server's write tools
never call the provider (counted); the gateway and the ACS hook refuse a fingerprinted approval.

#### T268: The documentation says narrows
`README.md`, `CHANGELOG.md`, this document's §6 and the docstrings of every public name §6 adds are scanned for
*prevent*, *close*, *closes*, *guarantee*, *ensure*, *opens nothing*, *cannot* and *blocks* in any sentence
that also mentions a precondition or a fingerprint, against an allow-list of exact lines that disclaim, on `v0.6` T180's design: a new occurrence
fails, and whoever adds it says in the test which kind it is. A positive control asserts the pattern fires on
*"the recheck prevents a stale approval"*.

#### T269: G16 in verify
§8.9's G16 entry, with its control and its note.

### 8.6 Item 6: Release

#### T270: Verify against the shipped examples
`ctrlrun verify` against `examples/policies/payments.yaml` and `examples/authority/payments.yaml` reports what
0.6.1 reported for G1 to G11, plus G12 to G16 each graded or `N/A` with its sentence, under
`ctrlrun.guarantees/v3`. All sixteen ids are present in the registry before the release PR opens. And against
a document whose only effect-bearing action declares `max_attempts: 1`: G5 and G14 are `N/A` with §8.9's
ceiling sentence, never `fail`; G12 passes on its two separate keys; G15 is graded. And against a document with
action A (an `effect:`, `max_attempts: 1`, allowed) and action B (an `effect:`, no ceiling, deny-only): G5 and
G14 are `N/A` with the ceiling sentence, because the ceiling is the only reason nothing is selectable; the same
with B allowed by the policy but covered by no grant in the document's `authority:` section; and with B allowed
and granted, G5 and G14 select B and are graded.

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

**Invariant.** `ctrlrun.transport` claims `NotExecuted` only for a connection it opened, that was handed no
request byte, in an executor run that had offered none; after one byte, every failure is the original
exception and the effect is `AMBIGUOUS`.

**Descends from.** `v0.1 §5.5`, `v0.2 §6.8`, T220, T221, T223b.

**Requires.** One action the configuration can drive to `allow` or `approve`, as G10 requires (verify grants its
own approval, `v0.4 §3.5`).

**N/A when.** No such action can be selected. The reason is built the way G10's is, through `unselected()`
(`verify/scenarios.py:868-879`, `1924`): `every action in the policy is denied` where that is why, and the
grant-coverage sentence `NO_GRANT_COVERS_SELECTION` where an action reaches a decision and no grant covers what
verify can build. Hardcoding G10's sentence would print "every action in the policy is denied" about a document
whose actions are allowed and merely ungranted, which is a false `N/A`. **Never `N/A` because of the
environment.** A sandbox that will not let verify bind a loopback socket is an internal error, exit 3
(`v0.4 §3.8`), because it is a fact about the machine and not about the document.

**Observable, four rows.** Verify binds each listener on the literal `127.0.0.1`, at an ephemeral port, through
the socket class the guard patches, so the guard records it. The executor drives
`ctrlrun.transport.HTTPConnection("127.0.0.1", port)` directly, **not** `urlopen`: `urlopen` honours
`HTTP_PROXY`, and on a host that sets one the loopback request would go to the proxy, and G12 would fail for a
reason that has nothing to do with the kernel. Every row asserts first that its listener **received a request
byte**, then that the exception is not `NotExecuted`, the receipt is `ambiguous`, and where the action has an
effect key the record is `AMBIGUOUS`.

1. **`byte_written`**: the listener reads at least one byte and resets.
2. **`read_timeout`**: the listener reads the request and never answers, and the connection's read times out.
3. **`reused`**: one connection delivers a request and is answered, before the attempt and outside any run,
   and is closed; its next request, inside the attempt, reconnects to a socket verify bound and never
   listened on, and fails. No port is re-bound after being served: that is not portable (§12.2.11).
4. **`second_connection`**: one connection delivers a request and is answered; a second connection, in the same
   executor run, fails to connect to a held, unlistening port.

The last three were added after the first independent review, which found that a classifier with no evidence at
all passed G12: the reset row fails inside `getresponse()`, and a claim can only originate in `connect()`, so
nothing in G12 depended on the classifier's evidence. Each of the three is where a different wrong classifier is
wrong: one that maps `TimeoutError` to `NotExecuted` fails row 2, one that ignores the connection's own byte mark
fails row 3, and one that judges each connection alone with no register of the run fails row 4 (§12.2.11).

**Control.** A loopback socket verify bound and did not listen on: the call raises `NotExecuted` chained from
the connect's own exception, **a refusal on Linux and a timeout on macOS**, which drops a SYN to a port that is
bound and not listening (§12.2.1); the receipt is `failed`, and where there is a key the record is `FAILED`. A
classifier that never claimed would pass the observable rows and fail this; one that claimed where it should not
fails one of them. The guarantee is the asymmetry, so both directions are asserted or neither is, as G10's are.
**Every row uses its own effect key**, as G10's rows do (`verify/scenarios.py:1954-1958`), so each is a first
attempt and an operator's `max_attempts: 1` cannot turn the control into a ceiling refusal.

**What it amends.** `v0.4 §3.7`'s "no scenario opens a socket" becomes **verify opens no connection except to
the store `--store-url` names and to loopback listeners it bound itself.** The old sentence was already untrue
under `--store-url postgresql://remote-host/…`, whose libpq sockets T107's guard never sees
(`verify/scenarios.py:546-553`). T107's guard (`tests/test_verify.py:535-557`) is amended so that it admits
exactly what the rule says and no more:

- **It records every `(host, port)` bound through its patched socket class**, taken from `getsockname()`
  after the `bind`, not from the requested address, since verify binds port 0 and the kernel chooses the
  port. **Only a stream socket's bind is recorded, and a pair is forgotten when the last socket holding it
  closes, detaches or is collected**: TCP and UDP are separate port spaces, so a UDP bind must admit nothing
  on the TCP port of the same number, and a released port may be handed to another process at once. A
  datagram socket may neither connect nor send. It admits `connect` and `connect_ex` only to a recorded pair. Admitting any port on loopback would admit a local forwarding proxy, an
  SSH tunnel or a container's published port, each of which leaves the host.
- **It refuses any `bind` to an address other than `127.0.0.1`**, so a listener on `0.0.0.0` fails the run
  instead of passing it, and **it refuses every `AF_UNIX` bind and connect, on purpose**: verify needs none,
  and a Unix socket can reach a local daemon that leaves the host as surely as a TCP port can. The guard is
  IPv4 on the one literal, and everything else is refused.
- **It matches the literal string `"127.0.0.1"`**, not `ipaddress.ip_address(...).is_loopback`, whose handling
  of IPv4-mapped addresses varies across the supported Pythons, and it checks the host string inside the patched
  `connect` itself, because a C-level `connect(("localhost", port))` resolves without calling the patched
  `getaddrinfo`. `create_connection` and `getaddrinfo` are admitted for that literal only; `localhost` is
  refused everywhere.
- **`::1` is not admitted.** Verify never uses it, and admitting what nothing uses is how a guard grows holes.

T230 asserts each of those. Item 6 reconciles `README.md:258`'s *"with no network"*.

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

**Requires.** As G5, as amended below: one action declaring an `effect:` template whose placeholders the
synthesized arguments resolve, **and whose ceiling allows a renewal** (no `max_attempts`, or at least 2).

**N/A when.** As G5, with G5's reason and note, built through `unselected()`: `no action declares an effect:
template`, and *in a `ctrlrun.policy/v1` document the template lives in the `@protect` decorator, which verify
does not read*. True, because the token is defined only for an attempt that holds a reservation, and without a
key there is no reservation and no attempt to name. And the new case: `every action with an effect: template
that verify can select (a decision of allow or approve under a grant that covers it) declares max_attempts: 1,
so no renewal can happen`. **Precedence:** this sentence is printed only where the ceiling is the *only* reason
nothing is selectable, which verify establishes by selecting again with the ceiling filter removed; where that
selection also finds nothing, the reason is `unselected()`'s, as before. "Select" covers both axes on purpose:
an earlier wording, "can drive to allow or approve", read on the policy axis alone, was false about a document
whose uncapped action is allowed by the policy and covered by no grant, and before that a wording without the
qualifier was false about one whose uncapped action is deny-only.

**Observable.** Attempt 1's executor reads the token and raises `NotExecuted`; attempt 2's reads it and commits.
The two differ, and each equals the derivation from its own receipt's `effect_key` and `attempt`. That last
clause is what a kernel returning a fresh random string on every read would fail.

**Control.** Inside attempt 1 the accessor read twice returns one string, and **the accessor called outside any
executor raises `InvalidArgument`**. What the control catches is a context variable that leaks: a kernel that
set the token and never reset it would pass the observable, because each executor would still read its own
attempt's value, and would fail here, where a caller outside any attempt was handed the last attempt's token.
The two-reads half catches a token recomputed from something that moves within one attempt.

**Note, printed once beneath the table:** *a token is unique only as far as your effect keys are: two stores
sharing a provider account must not produce the same effect-key string for different effects, and nothing here
can check that (§4.6).*

**Why G14 depends on the document at all.** It does not depend on what the operator wrote the way G15 and G16
do; it depends on there being an effect key a renewal can reach, which is the same thing G5 depends on, and a
guarantee graded where no renewal could exist would be one that could not have failed.

---

#### G15: A renewal past the operator's ceiling is refused

**Invariant.** An action whose policy entry declares `max_attempts: N` executes at most N attempts on one effect
key, the first included, and the refusal names the ceiling.

**Descends from.** `v0.1 §5.4` as amended (§5.8), T240 to T245b.

**Requires.** One action verify can drive to `allow` or `approve` that declares both an `effect:` template and
`max_attempts`, with `max_attempts` no greater than 100.

**N/A when.** No such action can be selected. Reason, through `unselected()`: `no action verify can drive to
allow or approve declares both effect: and max_attempts`, with the grant-coverage sentence in its place where the
miss was on the authority axis. The earlier draft's `no action with an effect: template declares max_attempts`
was false about a document with such an action that is deny-only, ungranted or not synthesizable. Or every such
action's ceiling is above verify's bound. Reason: `every declared max_attempts is above verify's bound of 100
attempts`, true of the document and of verify's stated bound, on the precedent of G4's `GRANT_ALREADY_EXPIRED`:
every loop verify runs is bounded (`v0.4 §3.6`).

**Observable.** §5.5's public route, so that the check after the reservation is what refuses and the fast path
cannot: attempts 1 to N−1 raise `NotExecuted`; attempt N raises `TimeoutError`, leaving the record `AMBIGUOUS`
at N; attempt N+1 carries a `reconcile` hook answering `not_executed`, passes the fast path, is reconciled and
renewed to N+1, and raises `ActionDenied` with `reason == "attempt_ceiling"`. The executor was called exactly N
times; `EFFECT_RESERVED` precedes an `EFFECT_RESERVATION_REFUSED` whose `data.reason == "attempt_ceiling"` for
attempt N+1; the record is `FAILED` at N+1; the refused attempt's receipt is `blocked`. A kernel with the check deleted executes attempt N+1 and fails here. An
earlier draft drove N+1 sequential `NotExecuted` attempts, which the fast path alone refuses, so that G15 passed
with the guarantee's own mechanism deleted.

**Control.** Every attempt up to N executed: the call count reached N, so for N of 2 or more the renewals were
admitted. A kernel that refused every renewal would fail this, **except at N = 1**, where there is no renewal to
admit and this control cannot tell such a kernel from a correct one; the G5 amendment below says what that
leaves ungraded.

---

#### G16: A precondition whose fingerprint moved is refused before the reservation

**Invariant.** An approval requested with a precondition fingerprint is refused, before any reservation, when the
fingerprint computed at presentation differs; the approval is left `granted`.

**Descends from.** `v0.1 §4.2`, T253, T254.

**Requires.** One action the configuration can drive to `approve`, as G1 requires.

**N/A when.** No such action can be selected. The reason is built as G1's is, through `unselected()`
(`verify/scenarios.py:1025`): `no action requires approval` where that is why, and the grant-coverage sentence
where it is not. It is true in the first case because a precondition binds only where an approval is consumed
(§6.8). **G16 inherits G1's one weakness**, stated rather than hidden: an approval action that exists but that
verify cannot synthesize within its candidate bound (`v0.4 §3.3`) is reported through the same sentence G1 uses
for it, which says less than it could.

**Observable.** Verify supplies its own provider. The request is created while it returns one state and granted;
at presentation it returns another. `ApprovalMismatch` with `reason == "precondition_changed"`; the executor's
count is zero; **the approval is still `granted`**, which is what proves the refusal came before the store call
that consumes it; and where the action has an effect key, no `EFFECT_RESERVED` exists for it and no record was
written. For an action with no effect key the reservation assertion is vacuous, and the approval's status
carries the proof alone.

**Control.** The same, with the provider returning the original state at presentation: the action executes and
commits.

**Note, printed once beneath the table** as G3's is: *verify supplies its own precondition provider; whether your
`@protect` declares one is in your code, which verify does not read. The gateway and the ACS hook cannot name a
provider at all, and refuse an approval that carries a fingerprint.* The roadmap's exit sentence says G16 is
`N/A` where the configuration names no fingerprint. A fingerprint is named in code (§6.2), not in any document
verify reads, so that sentence cannot be made true; G16 is instead graded as G5 and G10 are, against the kernel
in this configuration with verify's own stand-in for the operator's code, and the note says so. Item 6
reconciles the roadmap sentence.

---

**G5 is amended in the same way as G14**, because an operator's ceiling can make its control impossible: G5's
control renews the selected action's key, `NotExecuted` then a retry that must commit
(`verify/scenarios.py:1344-1377`), and under `max_attempts: 1` that retry is refused, which would report a
correct kernel as `fail`. **Item 4 makes the change for both G5 and G14**, since item 3 lands G14 before
`max_attempts` exists: each selects only an action whose ceiling allows a renewal, and where the ceiling is the
only reason nothing is selectable each is `N/A` with the sentence and the precedence above. T270 runs two
documents shaped that way.

**What that leaves ungraded, stated.** For a document whose only drivable effect-bearing action declares
`max_attempts: 1`, nothing in verify grades "`FAILED` permits a retry" (`v0.1 §5.4`): G5 is `N/A`, and G15's
control at N = 1 cannot tell a kernel that refuses every renewal from a correct one, because the correct one
refuses it too. That is true of the document rather than a gap in verify: the operator has forbidden the retry
the guarantee is about.

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

# An OPTIONAL store attribute (§3.6). Not on the StateStore protocol; any store may expose it, and
# Control reads it with getattr and uses it only if it is a ClockSkew. PostgresStateStore exposes it.
clock_skew: ClockSkew | None                                        # read-only

# ctrlrun.postgres (§3.7)
class PostgresStateStore:
    def __init__(self, url, *, clock=..., schema="public",
                 clock_skew_threshold: timedelta = DEFAULT_CLOCK_SKEW_THRESHOLD) -> None: ...

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
| `ActionDenied.reason` (request pass) | `precondition_unavailable`, `precondition_missing` | §6.2, §6.4 |
| `would_have.blocked_reason` | `attempt_ceiling` | §5.5 |

The two `preconditions=` keywords and `clock_skew_threshold=` are keywords on existing callables, not new
methods. `ClockSkew`, `Receipt.schema` and `ApprovalRequest.precondition_fingerprint` are fields and a record
type. `idempotency_token_for` exists because §4.5's re-derivation needs a public definition to re-derive from,
and `request` because the httpx variant is the gateway's rule offered to an executor using httpx (§2.5).
`clock_skew` is the one that most resembles a store method: it is an **optional store attribute**, read from
any store, and §3.6 argues why it is not a `StateStore` method, says that a wrapper which does not forward it
silently drops skew reporting, and leaves to the maintainer whether `v0.6 §9.2`'s bar should cover it.

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
   gateway's `NotExecuted` is chained (item 2). **On a continuation leg none of `v0.2 §6.8`'s `FAILED`
   rows records `FAILED`**, the pre-dispatch codes and the `401` rule included: the upstream is holding
   the original request, so the effect's state is unknown and the record is `AMBIGUOUS` (§12.2.12).
4. **`v0.3 §4.3.1`** gains two columns and one reordering: §7's precondition column and §7.1's attempt
   ceiling column, and §5.5's order (items 4 and 5). Item 4 owes §7.1 because it amends the order, and
   because a missing enumeration is how `Control.delegate`'s hole arrived.
5. **`v0.4 §3.7`** becomes *verify opens no connection except to the store `--store-url` names and to loopback
   listeners it bound itself*, and T107's guard admits only the `127.0.0.1` ports the run bound (item 2). **G5's selection**
   (`v0.4 §2.2`) skips an action whose ceiling forbids a renewal (item 4, §8.9).
6. **`v0.6 §6.4`'s last bullet**: an additive receipt field no longer breaks the rehash of an older receipt,
   because a receipt renders under its own schema; the unreleased builds that bullet describes are unchanged
   (item 5).
7. **`v0.6 §8` T141**'s "no other N/A is accepted" admits the skew case's `not_applicable` on SQLite and the
   in-memory store (item 1).
8. **`v0.6 §4.2`'s renewal compare-and-set** is conditioned on the planned-from attempt as well as the state,
   and **`v0.6 §4.3.2`'s lost-commit re-issue** returns the reservation it wrote rather than the one first
   planned (**item 3a**, its own pull request with an independent review, stacked before item 3; §5.6).
   **Table A2 row 1 on a renewal** applies `v0.6 §4.3.3`'s whole-row identity check, which only the insert
   path applied before, and **every other compare-and-set on `effects`** is conditioned on the attempt it read
   (item 3a, §5.6, §12.3a). `SPEC-v0.6.md` §4.2 and §4.3.4 carry pointers here.

---

## 10. Fail-closed table for v0.7

`v0.1 §3.4`, `v0.2 §6.11`, `v0.3 §9`, `v0.4 §10`, `v0.5 §10` and `v0.6 §10` hold in full. These rows are v0.7's
own, and none of them is configurable.

| Condition | Result |
|---|---|
| A connection the classifier opened fresh fails in `connect()` with no request byte offered, in an executor run that had offered none | `NotExecuted`, chained from the original; the record `FAILED` (§2.3) |
| Any failure after one request byte was offered, including a `sendall` that raised part way | The original exception; `AMBIGUOUS` (§2.3) |
| Any failure after a byte of the same executor run was offered, on any other connection or through `gateway.transport.request` | Never `NotExecuted` (§2.3, §12.2.9) |
| Any failure outside an executor run, or on a thread without the executor's context | Never `NotExecuted` (§2.3) |
| A connection the classifier did not open, or reused | Never `NotExecuted` (§2.3) |
| An httpx connect error or a proxy error where the environment named a proxy when the call began | Never `NotExecuted`; `AMBIGUOUS` (§2.5, §12.2.10, §12.2.14) |
| Anything on a continuation leg: a refused connection, a pre-dispatch JSON-RPC code, a `401` | Never `FAILED`; the upstream's answer relayed and the record `AMBIGUOUS` (§12.2.12) |
| A request byte offered by code that belongs to no executor run | Every open run is marked; none of them claims (§12.2.13) |
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
| A Postgres renewal planned against a stale attempt number | Matches no row; refused (§5.6, item 3a) |
| A Postgres `commit_effect` or `mark_ambiguous` whose record moved since its read, still this attempt's and still in a state it may be written from | Matches no row; re-read; **re-issued once** against the re-read, so the outcome lands on the newer attempt and is never dropped (§5.6, T246c, item 3a) |
| A Postgres `begin_execution` or `fail_effect` in the same position | Matches no row; re-read; refused, typed by what the re-read found, naming the move; nothing written, and the running attempt is left alone (§5.6, T246c) |
| Any other Postgres write to an effect record whose attempt moved since its read (`resolve_effect`, `extend_lease`, `hold_continuation`, the kept `AMBIGUOUS` write) | Matches no row; re-read; refused, typed by what the re-read found; nothing written (§5.6, T246c, item 3a) |
| A store refusal to an executor's outcome write, of any type | The `EXECUTION_AMBIGUOUS` event and the `ambiguous` receipt are written anyway and name the refusal; the caller's own exception propagates; nothing is reconciled on a record this attempt could not mark (§12.3a, item 3a) |
| A Postgres approval expiry whose approval was answered or consumed since its read | Matches no row; the approval keeps what the other writer wrote; the refusal that asked for the expiry is raised as before (§12.3a, item 3a) |
| A lost `COMMIT` on a reservation, resolved by re-issuing it | The attempt the re-issue wrote is the one returned, never the one first planned (§5.6, item 3a) |
| A lost `COMMIT` on a Postgres renewal whose re-read finds our `action_id` on a row that is not our own write | Not ours: `a2.row3.refuse`, refused through `plan_reservation`; nothing returned (§12.3a, item 3a) |
| A lost `COMMIT` on a Postgres insert whose re-read finds another attempt's record the planner would renew over | `a1.row3.refuse`; `DuplicateEffect(state=in_progress)`; nothing returned (§12.3a, item 3a) |
| A store whose `clock_skew` is not a `ClockSkew`, or whose read raises | Ignored with a log line; the action is unaffected (§3.6) |
| A stored receipt document with an added key, a changed or removed `schema`, or an unknown one | A hash mismatch: `content_altered` at its `seq`. `receipts()` does not raise, and no reader surfaces an undeclared key (§6.11) |
| A presented approval whose fingerprint differs from the recheck | `ApprovalMismatch(reason="precondition_changed")`; nothing reserved; approval `granted` (§6.3) |
| A fingerprint on one side only | `ApprovalMismatch(reason="precondition_missing")`; never a skip (§6.4) |
| A fingerprint computed on the request pass and not recorded on the request | `ActionDenied(reason="precondition_missing")`; the request is withdrawn, denied where it is still pending and spent where it was granted inside the window; no human is asked to answer it (§6.4) |
| An approval the read finds unusable where a precondition is in play | The refusal that read found, raised by `Control`, with **nothing written to the store**; expiry stays the store's to decide and to record (§6.6) |
| A stored receipt document whose value has no canonical form | `content_altered` at its `seq`, named by the refusal's type; the walk continues and every other break is still reported (§6.11) |
| The provider raises, returns a non-mapping, or returns what `canonical_bytes` refuses | Presenting pass: `ApprovalMismatch(reason="precondition_unavailable")`, nothing reserved. Request pass: `ActionDenied(reason="precondition_unavailable")`, no request created (§6.5) |
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

**Item 1 observes and reports.** No lease is evaluated differently, no reservation outcome changes and no
store write changes: the measurement is one `SELECT clock_timestamp()` and one attribute assignment, and T213
compares every decision and every record afterwards with both `plan_reservation` and a SQLite store driven
through the same steps.

**Where the `E3` re-measurement lives.** In `_ambiguate`, after the kept `AMBIGUOUS` write commits and before
`_plan` raises the refusal, on that write's own fresh connection, which the commit has just left outside any
transaction. So `_plan`, the reservation transaction and the lost-commit paths are untouched (item 3a changes
those), and a measurement that fails cannot abort the transaction the refusal belongs to. A kept write that
itself fails raises as at 0.6.1 and measures nothing, because it is no longer §3.5's moment.

**The rate limit counts attempts, on the application clock.** A re-measurement is due unless the last one was
at or before `now` and less than `DEFAULT_LEASE` ago. The attempt counts, not the success, so a failing query is
not retried on every refusal either; a clock that moved backwards makes one due, since that is itself worth a
reading. The application clock and not a monotonic one, because it is the clock the lease that just expired was
judged by, and it is the only one a test can move (T215).

**A round trip the application clock measured as negative** (an injected or stepped clock) is taken by its size:
`bound = |t1 - t0| / 2`, `midpoint = min(t0, t1) + bound`. The doubt is the same whichever way the reads came.

**`ClockSkew` checks its fields at construction**: `timedelta` for the three durations, a non-negative `bound`, a
positive `threshold`, an aware `measured_at` and a `trigger` in the closed pair. A third-party store that builds a
malformed one then fails where it built it, rather than inside the `Control` that would report it. `Control` still
treats any exception from reading or rendering the value as §3.6's "read raised", because a subclass can override
`exceeded`. The pair is private (`_CLOCK_SKEW_TRIGGERS`), so no public name is added beyond §9.2's.

**"Not the measurement it last reported" is equality, not identity.** A store whose property builds a fresh
`ClockSkew` on every read with the same fields is then reported once, not once per action. Two measurements that
differ in any field are two reports, which is what T217's third step asserts.

**"Once per store per kind" is two kinds**: the value is not a `ClockSkew` (whatever its type), and the read or
its rendering raised. They are keyed on the store object, weakly, so a process that builds a `Control` per request
around one store still logs each kind once; a store that cannot be weakly referenced falls back to the reading
`Control`'s own set.

**`getattr(store, "clock_skew", None)` treats a property that raises `AttributeError` as absent**, which is
§3.6's literal read and is kept. The conformance case is where that store's author finds out: it asks for the
attribute with `inspect.getattr_static` first, so a present property whose read raises `AttributeError` fails the
case by name rather than earning the `not_applicable` reserved for an absent one. A forwarding wrapper whose
`__getattr__` reaches a real attribute counts as exposing it.

**The pull is the first statement of `execute` and of `resume`**, before argument checks and before
`take_continuation`, so an at-open report precedes the first `ACTION_PROPOSED` (G13's observable). The pull after
an `AmbiguousEffect` is the first statement of `_secure`'s handler, before reconciliation and before the refusal's
own event, and observe mode's reservation refusal pulls too, because observe mode reserves and so meets `E3`.
`evaluate`, `delegate` and `revoke` do not pull: §3.6 names `execute` and `resume`, and none of the three meets a
lease.

**`clock_timestamp()` against `now()` is an equivalent mutant as built**, and the mutation table says so rather
than claiming it closed. Both measurements run outside any transaction (the store's connections are autocommit, and
the `E3` one runs after its commit), so `now()` is the single statement's start and agrees with
`clock_timestamp()` to within the statement. `clock_timestamp()` stays, per §3.4, so that a later caller who
measures inside a transaction does not inherit an error the tests cannot see.

**`data.measured_at` uses the event-data timestamp convention** (`iso_timestamp`, milliseconds, `Z`), as
`lease_expires_at` does. The three numbers are integer microseconds, exact, as §3.6 requires.

**A defect the event exposed, fixed here.** `PostgresStateStore.events()` read a NULL `action_id` back as the
string `"None"`, so the three `DELEGATION_*` events have named a proposal called "None" on Postgres since that
store shipped.
T217's comparison of what a sink was handed with what `events()` returns found it. The fix is on the read path
only; nothing is written differently.

**The conformance case.** Suite `clock`, case `skew-measured`. It aligns by a first measurement against the
host's real clock, as G13 does, and grades a store's retained measurement (`exceeded`), because the suite grades
stores and not `Control`. Four broken-store fixtures keep each check live: a look-alike type, a read that raises, a
detector that never fires and one that always fires.

**G13 needs nothing from the document but the store.** It proposes an action no document names, so the policy
denies it with `unknown_action`; the report under test is taken at the start of `execute`, before any decision, so
a denial reaches it as surely as an allow. On Postgres it is therefore never `N/A`, which is what §8.9's
*Requires* line says. Its catalogue title is *clock divergence is named*, short enough for the report's column.

**What §3.8 predicted, measured.** Every verify scenario on Postgres now appends one `CLOCK_SKEW_DETECTED` per
scratch store, because verify's clocks are anchored to the document. No scenario counted events, and all three
shipped examples still pass every applicable guarantee under `--store-url postgresql://…`. No existing Postgres
test asserted a complete event sequence against an injected clock, so none needed changing. SQLite runs of verify
report G13 `N/A`, which moves the counts T113 and T116 pin by one.

**An injection is sized against the bound, never fixed (review of #136).** G13 and the conformance case both
inject a skew and ask whether it was reported, and both first used a fixed margin past the threshold. The bound is
half the round trip to the store, so on a link whose half exceeds that margin a *conforming* store reports nothing
and the fixed margin calls that silence a defect: verify would have graded the link and blamed the kernel. Both now
widen the injection from the bound the shifted store measured, until a store honest within its bound would have to
report it (`threshold + 2 * bound + alignment`, one definition in `state.py` so the two cannot drift apart), and
both retry a bounded number of times. A report on the clock they meant to align, where the aligning measurement's
own doubt could explain it, is met by aligning again rather than by a FAIL. A link that cannot be outrun is
**verify's internal error, exit 3** (`v0.4 §3.8`: a fact about the machine, never a verdict on the kernel) and, in
the suite, a failure whose reason names the link and says it is not a report the store failed to make. The test for
each injects real latency rather than simulating it. Whether the alignment's own doubt excuses a report is decided
by recomputing the rule from the measurement's fields rather than by reading `exceeded`, so a store whose
`exceeded` always answers true is still caught by the control.

**A report the store cannot store changes nothing (review of #136).** `append_event` can fail, and it sat
unguarded, so a locked database would have raised out of `execute` before the action was decided, and out of the
`AmbiguousEffect` handler in place of the refusal the caller was owed: an observation deciding an outcome, which is
the one thing §3 says it never does. The append is now guarded like the read, logged once per store per kind, and
`_skew_reported` moves only after the store accepted the event, so a report that was lost is made by the next
action that can store it and a sink is handed only an event that was stored.

**`v0.1 §6.2`'s list is not edited in place.** v0.2 and v0.3 added nine event types without touching it, and
§9.6 item 2 records this one where the others are recorded. `v0.6 §8` T141 is amended in place, as §8 T214 asks.

### 12.2 Item 2: the transport classifier

#### 12.2.1 A port bound and not listening is refused on Linux and dropped on macOS

**Closed: the tests refuse with a closed listener; G12's control keeps the bound socket and accepts
either answer.** T221 and G12's control both name "a loopback port bound and not listening", and G12
says the call is `NotExecuted` "chained from `ConnectionRefusedError`". Linux answers a SYN to such a
port with a reset. macOS drops it, so the connect **times out**. Nothing is offered either way, so
the claim is equally true, but the cause's type depends on the platform.

The acceptance tests get a real refusal on every platform from a listener that is closed before the
connect. G12 does not do that. A closed listener frees its port, and in the gap before the connect
another local process could bind it. The guard would admit the connect, because verify recorded
the pair, and the classifier would then send a synthetic request to a service verify did not
start. So G12 keeps the socket bound, which holds the port. It gives the control's connect a
timeout of one second, and it asserts `NotExecuted` chained from the connect's own exception,
`ConnectionRefusedError` or `TimeoutError`. `detail.control_cause` records which one, so a report
says which mechanism the host used, and two runs on one host are identical. The cost is one
second per verify run on macOS.

**This is the mechanism G12 uses wherever it needs a connect to fail**: the control, the
second-connection row and the reused row alike (§12.2.11). It is the only one that behaves the
same on Linux and macOS and cannot race another process for the port.

#### 12.2.2 "An opener the classifier did not build" was the wrong question

**Closed: the stack heuristic is gone, and the register of §12.2.9 replaced it.** §2.3's first
condition once said a connection opened by an opener the classifier did not build is not the
classifier's to claim, and the first implementation answered that by walking the stack for
`urllib.request.OpenerDirector.open` frames. The independent review took it apart. The walk sees
only `urllib`'s own opener, on the current thread, so every one of these was a `NotExecuted`
chained from `ConnectionRefusedError` after a real peer had received the whole request:
`xmlrpc.client` with a `make_connection` returning the classifier's connection, whose `request`
retries once on a new one; `FancyURLopener`, which follows a `303` through `URLopener.open`;
a `build_opener` handler that runs its connection on a worker thread; the classifier's own private
opener class plus a redirect handler, since a code object is not a capability; and an executor's
own retry-once-on-reset loop around `urlopen`, which is the commonest shape there is and involves
no opener at all. The heuristic also produced false `AMBIGUOUS`: any wrapper on
`OpenerDirector.open`, such as `opentelemetry-instrumentation-urllib`, made `urlopen`'s own path
look foreign.

The question "who built the opener" was never the right one. **What matters is whether a request
byte was offered in this executor run**, which is what the register answers, whoever opened the
connection and on whichever thread. The condition in §2.3 is now that, the walk is deleted, and
T223b drives every case above. An opener somebody else built whose *only* connection is refused
before any byte is now claimed, because that claim is true.

#### 12.2.3 A socket the connection did not open disqualifies it for life

**Closed: `sock` is a property, and any assignment outside the connection's own `connect()` marks it
foreign.** The first draft checked only that `sock` was empty when `connect()` began. That missed a
caller who set a socket and then cleared it: the object had held a socket it did not open, and the
next `connect()` looked fresh. The mark is never cleared, like the byte mark, and `http.client`
assigns `sock` only in `__init__`, `close` and the two `connect`s, all of which the property
admits. T223 drives the set-and-cleared case against the control.

#### 12.2.4 The core connection asks `effect_state` too

**Closed: three paths, one function.** §2.1 required the gateway to call
`ctrlrun.transport.effect_state`. The core connections now reach it as well. `connect()` turns its
evidence into a `Transport` member, and `effect_state` decides whether that member is `FAILED`. So
the spy of T227 is reached by `gateway/outcome.py`, by `ctrlrun.gateway.transport.request` and by
`HTTPConnection`. A copy of the rule anywhere would leave one of the three unmoved by the spy.

#### 12.2.5 `urlopen` refuses a scheme before its opener can reroute it

**Closed: `http` and `https` only, checked on the `Request` before the opener runs, and `urllib`'s
unknown-scheme handler kept as the backstop.** `ProxyHandler` sends an `ftp:` URL through an HTTP
proxy when `ftp_proxy` is set, so "no `ftp:` handler" alone would not have kept an `ftp:` URL out.
A refused scheme raises `urllib.error.URLError`, `urllib`'s own exception for it, and the kernel
records it `AMBIGUOUS`, as §2.3's "exception before any connection" row says. The unknown-scheme
handler is not one of the handlers §2.8 names, and it only raises. It is load-bearing:
`http_proxy=socks5://...` names a proxy scheme `urllib` cannot speak, and without that handler
the proxy handler's nested open finds nothing, the `http` chain carries on, and the request is
sent in plain HTTP to the SOCKS port. With it, the answer is `URLError` and nothing is sent. T225
drives both.

#### 12.2.6 The gateway's cause travels beside the forwarder's answer, not inside it

**Closed: a context variable in `gateway/transport.py`.** §2.5 says the forwarder keeps the exception
beside the enum. `Forwarder`'s return is a four-tuple that a custom forwarder also returns, so the
shape was not changed. `HTTPForwarder` stores the exception in a private context variable. The
gateway's executor clears it before forwarding and reads it after, and only for a `Transport`
observation. A context variable is right because the listener serves each request on its own
thread and one forwarder is shared. A custom forwarder never sets it, and its `NotExecuted` stays
unchained, as at 0.6.1. The receipt's `error` for a connection never established now reads
`ctrlrun.upstream_not_executed: ConnectError: ...` rather than the bare token.

#### 12.2.7 One network guard, and why the examples' guard moved with verify's

**Closed: `tests/conftest.py`'s `_NO_NETWORK_GUARD` is the one definition, used by T107, T230, the
examples and the cookbook.** The cookbook's `verify-in-github-actions` recipe runs `ctrlrun verify`
under the examples' guard. Once G12 existed, that guard, which refused every connect, turned the
recipe into an exit 3. Amending one copy and not the other would have left two guards with
different widths, which is the drift the fixture's docstring was written to prevent. The shared
guard admits what §8.9 admits and nothing more: an IPv4 connect to the `127.0.0.1` literal at a
port the process bound, recorded from `getsockname()`. A self-bound loopback listener is not a
network, so the examples' claim is unchanged.

G12 also checks, without the classifier, that it can connect to a listener it bound, before the
scenario runs. §8.9 names only a refused `bind` as an internal error. A sandbox that allows the
bind and refuses the connect would otherwise have turned into a `control failed`, a failure blamed
on the kernel for a fact about the machine.

#### 12.2.8 A listener that received no byte is a failed control

**Closed: `control failed`, never a pass and never an internal error.** The observable's precondition
is that the peer received a byte before it reset. If it received nothing, "not `NotExecuted`" proves
nothing, so it cannot pass. Once the preflight above has shown the machine can reach its own
listener, it is not the machine's fault either. T230 takes the byte away and asserts the result.

#### 12.2.9 The register: one executor run, and what it cannot see

**Closed: `Control` opens a private register around each `executor()` call, and the classifiers
mark and read it.** A claim about one connection is not a claim about the effect. The register is a
context variable in `ctrlrun.effect`, holding one small object per run; every classifier `send`
marks it immediately before the first byte, exactly where the connection's own mark is set, and
`ctrlrun.gateway.transport.request` marks it for any call that may have written. `connect()` claims
only where the register exists and is unmarked, on top of the per-object checks. A run opened inside
another (an executor calling a protected function) marks the one that contains it, so the outer
effect knows that bytes went out through the inner one.

**Outside a run there is no register and nothing is claimed.** That covers a thread the executor
started without copying its context, and any use of the classifier outside `Control`, where no
record is written and the claim would be read by nobody. It is the fail-closed direction, and it
costs a true `NotExecuted` in scripts that call the classifier directly.

**A resumed leg starts marked**, and §12.2.12 argues it. The first version of this section said the
opposite, that a continuation gets a fresh register because the suspended leg's remote had not
finished; the second independent review showed what that records, and it was wrong.

**The per-object mark is not subsumed by it.** A thread with no register can deliver a request on a
connection; when the executor's own thread reuses that connection and its reconnect is refused, the
register saw nothing and the connection's own mark is what refuses the claim. T223b drives that.

**The limit, stated in §2.3, in the module docstring and in the class docstring.** The register sees
only the classifier's own sends. An executor that sends part of the effect through `requests`, or
through httpx directly, or on a raw socket, or on a thread that did not copy the context, and then
uses the classifier, can be handed a `NotExecuted` that is true of the classifier's connections and
false of the effect. So can one that raises a claim while a sibling thread's request is still in
flight. The claim holds where every request of the effect goes through the classifier on the
executor's context, and that sentence is now in the three places a reader would look.

**Nothing public was added.** The register is private, `Control` sets it, the two classifiers read
it; `§9` is unchanged. A public name for it would be a §9 amendment and would have stopped the item.

#### 12.2.10 Behind a proxy, the httpx variant claims nothing

**Closed: `ConnectError` and `ConnectTimeout` are `AFTER_REQUEST_SENT` where a proxy is configured.**
The review found `ctrlrun.gateway.transport.request` answering `NotExecuted` after a proxy had
answered a `CONNECT` line and the TLS handshake with the *target* had then failed: httpx reports
that with the same `ConnectError` as an unreachable proxy, and §2.3's tunnel row counts the
`CONNECT` line as written. Two implementations of one rule disagreeing is what item 2 exists to
remove, so the httpx side now fails closed: where a proxy may be in use, neither is claimed.

"May be in use" is read from `urllib.request.getproxies()`, which is httpx's own source, with
`NO_PROXY=*` honoured and a narrower `NO_PROXY` not consulted: a bypassed host is judged as if
proxied, which costs a claim and never makes a false one. This is stricter than 0.6.1, where the
gateway's forwarder mapped every `ConnectError` to `NEVER_CONNECTED` and therefore to a `failed`
receipt and `-41011`; behind a proxy it is now `AMBIGUOUS` and `-41010`. The changelog says so.

#### 12.2.11 G12 needed rows the evidence decides

**Closed: three more observable rows, each with a mutant that fails only there.** G12 as first
written passed a classifier that ignored every piece of evidence it had. Its reset row fails inside
`getresponse()`, and a claim can only originate in `connect()`, so no row touched the byte mark, the
foreign-socket record or the register. The review demonstrated it with a classifier that maps
`TimeoutError`, `ConnectionRefusedError`, `socket.gaierror` and `ssl.SSLError` to `NotExecuted`
wherever they arise, which passed G12 and turned a read timeout after 97 delivered bytes into a
`FAILED` record.

The rows are in §8.9: a read timeout, a reused connection, and a second connection in one run. T230
carries one mutant per row.

**The reused row needed a second mechanism, and the first one was not portable.** It needs a
connection that has already delivered a request and whose *next* connect fails, on a port no other
process can take. The first attempt closed the listener and re-bound its port with `SO_REUSEADDR`,
not listening. On macOS that works. **On Linux it does not**: while the connection it served is
still closing, the port is held by that connection and `bind` answers `EADDRINUSE` whatever
`SO_REUSEADDR` says, so `ctrlrun verify` exited 3 on every Linux run, for every document, on a
correct kernel. Nobody saw it because the reasoning and the measurement were both done on macOS;
**CI found it on Linux after a clean macOS run and a clean review round**, which is the argument
for the matrix and against a mechanism measured on one platform.

**A second platform lesson, from the same CI.** Where a peer's reset surfaces is not the same on
both: macOS raises it from the response read, Linux from the send. The kernel does not care, since
the classifier re-raises whatever it was and the mark is already set either way, and G12's reset row
passes on both. What it changed was a **mutant**: T230's "a classifier that always claims" wrapped
only the response read, so on Linux the reset row was unmutated and the read-timeout row caught the
mutant instead, under a different sentence. The double now claims from the send as well. A test
double that models a wrong classifier has to be wrong everywhere the failure can land, or it is
testing the platform.

There is no second mechanism now. The reused row reconnects to **the socket §12.2.1 already
describes**: bound by verify, never listened on, refused at once on Linux and dropped on macOS, and
held for the row's duration so no other process can take it. The connection's target has nothing to
do with what the row asserts, which is that an object that has already offered a byte does not
claim when its next connect fails, so pointing the reconnect at that socket costs the row nothing
and removes both the race and the platform dependency. No port is ever re-bound after being
served.

**And the network guard was wider than its sentence** (the review's finding 7). It recorded any
bind, so a UDP bind to `127.0.0.1:P` admitted a TCP connect to another process's listener on `P`,
and a port stayed admitted after its socket closed and another process rebound it. It now records
only stream sockets, forgets a pair when the last socket holding it closes, detaches or is
collected, and refuses a datagram connect or send. T230 asserts each.

#### 12.2.12 Nothing claims `FAILED` on a continuation leg

**Closed: a resumed run starts marked, and the gateway records `AMBIGUOUS` for every `FAILED` it
could reach on a continuation.** §12.2.9's first version gave a resumed leg a fresh register and
argued that a leg which ended in `input_required` is the remote saying it had not finished. The
second independent review showed what that records. In the kernel: an executor delivers a request,
the remote answers by asking for more, the executor suspends, the remote dies, and the
continuation's refused connection is `NotExecuted`, so the record is `FAILED` at attempt 1 and the
next call is dispatched again, to a remote that had the request. Through the gateway the same shape
answers `-41011`.

The argument was about the leg; the record is about the effect. **A continuation exists only
because the remote spoke.** `server.py` takes it from the upstream's own response, and
`control.py`'s lease extension already says the rest in the kernel's voice: the remote may already
be acting on this reservation. So a resumed leg can never truthfully claim the remote did nothing,
and `Control.resume` opens its register already marked.

**The rule is general, and wider than the register.** On a continuation leg the gateway refuses to
record `FAILED` for **every** path that reaches it: a connection never established, a pre-dispatch
JSON-RPC code, the `401` rule of §2.4, and the operator's own `not_executed_on_error` assertion of
`v0.2 §3.1`. Each answers for the *continuation's* request; the upstream is holding the original,
and a rejection of the second says nothing about what it did with the first.

`not_executed_on_error` is worth naming rather than leaving to "every path", because `v0.2 §3.1`
makes it the operator's claim, made by the person who knows the tool, and this overrides it. It
overrides it in one direction only: the operator asserted that *this tool* reports errors before
acting, which is true of the call it answers, and on a continuation the call it answers is not the
one that carries the effect. The upstream's own response is still relayed unchanged, the tool's
error included, so a client sees exactly what the tool said and CTRLRun records that the outcome is
unknown. The price is a `ctrlrun resolve` where 0.6.1 permitted a retry, and the alternative is a
retry of an effect the remote may be part-way through.

#### 12.2.13 A thread that did not copy the context, and the price of seeing it

**Closed: a send that belongs to no register marks every open one.** Not copying the context is
Python's default: `threading.Thread` and `ThreadPoolExecutor.submit` both leave it behind, and only
`asyncio.to_thread` carries it. An executor that hands its request to a worker thread and then
fails to connect on its own thread was claiming that nothing happened, with the request delivered.
§12.2.9 had this as a documented limit; the review was right that a limit this ordinary is a hole.

The register is now a set of open runs, guarded by a lock, and a send with no register in context
marks all of them. **The cost is real and it is the safe direction**: a stray send suppresses the
claims of runs it has nothing to do with, turning a provable `FAILED` into `AMBIGUOUS`, never the
other way round. The docstrings and §2.3 say so, and a test asserts the cost as well as the fix.

**`HTTPForwarder` is the one exception, and marks only its own run.** It carries the gateway's
relayed traffic too (`tools/list`, `GET`, `DELETE`), which `v0.2 §6.3` says is never an effect, on
listener threads that have no register of their own. Marking every open run from there would let a
`tools/list` beside an intercepted call suppress that call's claim, for no safety: those bytes
cannot be part of anybody's effect.

**What is still not seen**, and §2.3 says it: a sibling thread that copied the context and sends
*after* a claim was decided. The claim is about the run up to the moment of the failure, and the
race is pinned by a test so the disclosure cannot drift.

#### 12.2.14 The proxy answer belongs to the start of the call

**Closed: `_through_a_proxy()` is read where the client is built, and passed to the observation.**
httpx takes its proxies when the client is constructed, and the first version read the environment
again at the moment of the exception. A process that cleared `HTTPS_PROXY` on another thread while
a call was in flight would then have a `CONNECT` line on the wire and an answer that said no proxy
was involved, which is the one direction that produces a false claim. Both surfaces read it once,
beside the register, and `_observed` takes it as an argument.

### 12.3a Item 3a: attempt numbers never repeat

Both defects §5.6 names were reproduced before they were fixed, each by a test that was red on 0.6.1's
store for the reason it names: T246 with two reservations carrying attempt 2; T246b's renewal variant with the
method returning 2 while the record held 3; T246b's insert variant with 1 returned and 2 stored. All of them run
in separate OS processes against a local Postgres, with the parent holding the proxy, and every wait bounded.
Both reservation methods, `reserve_effect` and `consume_approval_and_reserve`, run through every window,
because §5.5 says *every* reservation method returns the number it wrote.

**The independent review found the premise of the renewal fix false, and it was.** §5.6's first draft argued
that conditioning the renewal on its planned-from attempt closed the race *because the attempt number is
monotonic*. On Postgres it was not: `_write_effect` and `_transition` wrote back the attempt they had read,
under a `WHERE` on `effect_key`, `action_id` and `state`, and a caller that retries one `Action` object reuses
its `action_id`, so that pair comes round again at a newer attempt. The reviewer held a `resolve_effect`'s
`UPDATE` with this item's own proxy while a second human resolved attempt 1, the owner retried, and attempt 2
timed out; released, the stale resolution wrote `FAILED` at 1 over `AMBIGUOUS` at 2, and the next renewal handed
out attempt 2 again. That is two dispatches under one number and one token, a ceiling of N admitting N+1, and
attempt 2's unknown outcome settled by a human who had read attempt 1's record: a double-execution risk present
since 0.6. SQLite and the in-memory store never had it, since their read and write are one critical section.
Every `UPDATE` on `effects` in `postgres.py` was checked, and there are three: the renewal (conditioned by the
first fix), `_write_effect` (under `resolve_effect`, `extend_lease`, `hold_continuation` and §4.2.2's kept
`AMBIGUOUS` write) and `_transition` (under `begin_execution`, `commit_effect`, `fail_effect`, `mark_ambiguous`
and Table A2's re-issue). The one `INSERT` lands only where no row exists. Both compare-and-sets are now
conditioned on the attempt they read; a moved row matches nothing and is re-read. T246c holds each shape that
reaches them, and each was red on the store before this fix with the record rewound to attempt 1.
`extend_lease` and `hold_continuation` share `_write_effect`'s statement, so its mutant is killed by the
`resolve_effect` and contender-`AMBIGUOUS` variants; the four transitions share `_transition`'s.

**And the refusal that replaced the rewind dropped outcomes, which the second review round caught.** A write
whose row has moved is re-read and run through `_checked`, and where the record is still this attempt's and
still in a state the write may be made from, the predicate *passes*: only the attempt moved. The first fix then
raised and wrote nothing, and for `commit_effect` and `mark_ambiguous` that is a lost outcome, not a refusal.
The reviewer measured it: attempt 1's `commit_effect` refused, the record left `EXECUTING` at attempt 2, attempt
2 reporting `NotExecuted`, and a renewal to attempt 3 with attempt 1's commit recorded on no effect record at
all. For a refund that had landed, attempt 3 is a second refund. The effect record is what gates the next
renewal, so a write that carries an outcome may not simply decline to land. Both outcome transitions are now
**re-issued once** against the re-read, bounded by a `restaged` flag as Table A2's re-issue is bounded by
`retrying`, so the stale write lands exactly where the same call a moment later would have landed it: on the
newer attempt. `begin_execution` and `fail_effect` are still refused, and the asymmetry is the argument.
`FAILED` asserts that *nothing happened*, so re-issuing attempt 1's over attempt 2 in flight would permit a
retry beside a running dispatch; `begin_execution` claims a reservation this attempt no longer holds. Attributing
an outcome to the newer attempt is the residual below; dropping it is a lost outcome, and between the two the
fail-closed direction is to land it.

**Two bounds that reset each other are not a bound, and that took a third round to see.** `restaged` bounds the
stale re-issue and `retrying` bounds `v0.6 §4.3.2` Table A2's lost-commit re-issue. Each was tested alone and
each held alone. Composed, they cleared each other: the restage re-issued without passing `retrying` on, and the
lost-commit re-issue re-issued without passing `restaged` on, so a `COMMIT` lost inside a restage reset the
restage bound and a restage inside a lost-commit re-issue reset the lost-commit bound. A review drove both
halves at once, every `COMMIT` lost and a record that keeps moving, and measured `RecursionError` at 113 deep,
which is T155f's failure mode arriving by another door and outside the closed set of errors, so no caller can
classify it. Both flags now travel through both re-issues. The composition is the property, not either flag, and
its test drives both halves the way each is driven alone: the real proxy at `drop_before_commit = 1000` for the
lost `COMMIT`, and the seam below for the record that moves.

**Two things about that test were measured rather than assumed, and both would have made it a false green.**
The first is the interleaving: the obvious every-other-read pattern ends bounded even when the flags do not
compose, so the patterns come from a search over this proxy, and there are two, because `(0, 0, 1, 0)` is the
one that catches the restage failing to pass `retrying` on and `(1, 0, 0, 0)` is the one that catches the
lost-commit re-issue failing to pass `restaged` on. The second is the assertion. Dropping **both** flags
recurses; dropping **one** does not recurse at all on any pattern of six reads or fewer, measured rather than
sampled, it simply permits one
re-issue more than the bound allows, and that extra level terminates. So a test asserting only *"this did not
blow the stack"* would be green for each flag taken alone, which is precisely the pair of mutants that say each
is load-bearing. Each bound permits one re-issue, so three is the deepest nesting any interleaving may reach,
and the test asserts that: a fourth level means a bound was cleared, whether or not that pattern went on
forever.

**The re-issue is asked for at the call site rather than inferred from the state it writes.** It was keyed on
the target state, `COMMITTED` or `AMBIGUOUS`, which is right for every path there is today and wrong the moment
a transition to one of those states is somebody's *decision* rather than an executor's outcome. A human's
resolution is exactly that, and it reaches `_write_effect` today, but `_transition` is generic and the next
transition to land there would have inherited the re-issue silently. `commit_effect` and `mark_ambiguous` now
pass `carries_outcome=True`, and the state set survives as an assertion: a caller that claims to carry an
outcome into any other state is a wiring bug rather than a re-issue.

**A restage that then refuses says so.** The line naming it was written after the nested call returned, so it
appeared only when the re-issue succeeded, and the case an operator would actually go looking for, a stale
outcome that was re-issued and still refused, left no line at all. It is written before the re-issue now, which
is `v0.6 §4.3.4`'s rule applied to a branch that section does not enumerate: which branch ran is observable.

**The bound has its own test, at the store's own read, because no proxy can drive it.** A second move under the
re-issue needs a rival interleaved *inside* the re-issue, and a hold fires once. `v0.6`'s T155f is the precedent
for what an unbounded re-issue costs: driven by a proxy that swallowed every `COMMIT`, the lost-commit path
recursed 96 deep and escaped as `RecursionError`, outside the error taxonomy. So the stale re-issue's bound is
driven by subclassing the store's own `_read_effect` to report the record one attempt further on every read,
which is a rival that never stops moving and makes every conditional `UPDATE` miss. Bounded, the call refuses
and writes nothing; unbounded, it recurses until Python stops it, which is what removing the flag produces and
what the mutation table records.

**The refusal now takes its type from what the re-read found.** Every one of these was
`DuplicateEffect(state=in_progress)`, which `errors.py` defines as *another attempt holds a live reservation*.
After a stale `resolve_effect` the record is `AMBIGUOUS` at the newer attempt, which is nobody's reservation, so
the type was false and a caller reading `in_progress` would wait for a dispatch that is not running. `_moved`
picks `AmbiguousEffect` for an `AMBIGUOUS` record, `DuplicateEffect(committed)` for a committed one and
`in_progress` otherwise, and the message names the move: *moved from attempt 1 (ambiguous) to attempt 2
(ambiguous) since it was read; nothing was written*.

**An unknown outcome is never lost, whatever the store answers.** The same review drove the case through
`Control` on SQLite, where none of this item's races exist, and found something older and worse. `Control`
caught `DuplicateEffect` and `AmbiguousEffect` around its outcome writes; a record a human resolved `FAILED`
while the attempt was still running answers `InvalidArgument`, which escaped. The result was an executor's
`TimeoutError` producing **no receipt, no `EXECUTION_AMBIGUOUS` event**, and the caller handed a store error
about its own effect key instead of its executor's exception: the unknown outcome existed nowhere. The store's
answer to an outcome write may be a refusal; the evidence may not. `Control` now catches every `CTRLRunError`
from the three outcome writes, writes the event and the receipt whatever the answer was, and **names the
refusal in them**, with what the executor did beside it: an executor that returned leaves a remote that very
likely acted and one that raised `NotExecuted` leaves a remote that very likely did not, and recording only the
refusal made those two receipts identical for whoever runs `ctrlrun resolve`, because where the store would not take the outcome the effect record does not carry it and
the receipt is the only place it exists. The caller's own exception propagates, and eager reconciliation stays
gated on the write having landed, so nothing is retried on the strength of a refused outcome write. What the
record says afterwards is the human's claim, not this attempt's, and that is the one thing the evidence can
still contradict. T246d is the test, on both backends and for all three outcomes.

**What the wider catch also absorbs, said plainly.** Each of those three `except` clauses wraps exactly one
store call, so no approval, authority or reservation refusal passes through it. But `CTRLRunError` includes
`InvalidArgument`, and a mis-wired `held_key` raises `InvalidArgument("no reservation for effect ...")`, which
now becomes an `ambiguous` receipt naming the refusal rather than an exception at the caller. That is the right
trade and it is a trade: a wiring bug on this path surfaces in the evidence instead of at the call site, and it
surfaces as an unknown outcome, which is the fail-closed reading of *the store would not record what the
executor did*. The receipt names the exception type, so the bug is legible; what it no longer does is stop the
attempt from being recorded at all.

**What that does not close, stated without softening, because the first draft of this paragraph softened it in
three places.** The attempt number is now monotonic, so no two reservations of one key carry the same number.
What a reused `action_id` still leaves open is which attempt a *late* write is about, on **every backend**: a
transition names its holder by `action_id` alone, so attempt 1's write, arriving after its lease lapsed, a
contender ambiguated the record and the same `Action` was retried, reads attempt 2's record, finds its own id
in a state it expects, and lands there. Three corrections to how that was stated:

- **It is not only misattribution.** A late `commit_effect` records the wrong attempt's outcome, which is bad
  evidence. A late `fail_effect` is worse: it writes `FAILED` over attempt 2 **while attempt 2 is executing**,
  and `FAILED` is the one state that permits a renewal, so attempt 3 may be dispatched beside a dispatch that is
  still running. That is a concurrent double execution, and calling it attribution would be this repository's
  own prevention-versus-attribution error.
- **And `mark_ambiguous` reaches the same place in one more step, which is why the asymmetry above is argued on
  this ground and not on the state's name.** A late or re-issued `mark_ambiguous` lands `AMBIGUOUS` on attempt
  2; the write succeeded, so `Control` counts the outcome as recorded and eager reconciliation runs; a hook
  answering `not_executed` resolves the record `FAILED`, because the hook is asked about the **effect key** and
  not about the attempt; and attempt 3 may then be reserved while attempt 2 is still executing. The step that
  `fail_effect` takes in one, `mark_ambiguous` takes in two, and the second is taken by a hook that cannot see
  which attempt it is answering about. Recording the unknown outcome is still right, and the store must still
  never drop it; what the pair shows is that the residual is about attempt *identity* and is not closed by
  refusing one transition.
- **It does not need a human.** A reconcile hook answering `not_executed` moves an `AMBIGUOUS` record to
  `FAILED` with no person involved (`control.py`, `_reconciled`), so the sequence runs unattended.
- **There is a fix inside the frozen protocol, and it is declined here rather than unavailable.** A store-side
  memo of the attempt each reservation wrote, keyed by `(effect_key, action_id)` and seeded by `reserve_effect`,
  `consume_approval_and_reserve` and `take_continuation`, would let every transition condition on the attempt
  the caller actually holds without touching a frozen signature. It is declined in item 3a because it is a
  schema change: a table or a column, a migration, the store conformance suite, and a rule for what a memo that
  is missing means, which is a specification amendment of its own and is the shape v0.9's budgets will need
  anyway. Item 3a's subject is the attempt number, and this is the attempt *identity*. Stated here so the next
  milestone inherits the argument rather than the surprise.

**A third path, found while building: the renewal's "landed" row trusted `action_id`.** Table A2 row 1 on a
renewal (`_resolve_lost_renewal`) concluded that a lost `COMMIT` had landed from two facts: the record was
`RESERVED`, and it carried the renewal's `action_id`. `v0.6 §4.3.3` explains why that is not enough and applied
the fix to the insert path only: `action_id` is caller-supplyable, and a caller that rebuilt the same `Action`
after a restart renews under the same one. So a renewal whose `COMMIT` was lost, followed by a second process
renewing the key under the same `action_id`, left the first concluding the row was its own write: two
dispatches holding one attempt, and a number returned that the method never wrote, which is §5.5's MUST
failing on the third branch of the same function. The row now applies §4.3.3's check, `_is_our_own_write`,
against the row the renewal would have written, so a rival's lease or `updated_at` makes it `a2.row3.refuse`.
The identical-in-every-column residual §4.3.3 states is unchanged, and it needs a clock frozen across processes.
The test is T246b's third variant, `landed`. It was red on 0.6.1 in both methods, and a check on `attempt`
alone would not pass it, because the rival renewed to the same number. §9.6 item 8 and §10 carry the row.

**The planned-from attempt is the plan's, and T246 opens two windows to show it.** `_reserve_locked` reads the
record a second time, for `created_at`, between `_plan`'s read and the `UPDATE`. A condition taken from that
second read would pass a test that holds only the `UPDATE`, because both reads precede the hold and see the same
record. So T246 runs at two points: holding the `UPDATE` (§8.3a's window) and holding the second read, where
that read sees the rival's attempt. The condition is `reservation.attempt - 1`, which is the attempt
`plan_reservation` renewed from (`effect.py`, `record.attempt + 1`). Taking it from the second read fails the
second window and passes the first.

**The store conformance suite gains no case: `v0.6 §2.4` holds here.** Its barrier releases contenders
together and cannot stop one between its `SELECT` and its `UPDATE`, because that is inside `reserve_effect`
and reaching it needs a hook in shipping code (`v0.6 §2.5`). It cannot lose a `COMMIT` either. The suite states
both properties in the `retry-table` case, which already asserts that the returned attempt and the stored
attempt agree on the path it can reach. T246 and T246b are the only tests of the defence, as §5.6 anticipated.

**Two lines no test reaches, stated as such, and one that a test now does.** SQLite's `AND attempt = ?` is an
equivalent mutant: with it removed the whole suite passes, Postgres included, because `BEGIN IMMEDIATE` holds the
read and the write together (§5.6). So is `carries_outcome` at the re-issue's guard: reverting it to the older
`state in _OUTCOMES` condition fails no test, because the only two callers reaching those states pass
`carries_outcome=True` and nothing else transitions to `COMMITTED` or `AMBIGUOUS` through `_transition`. It is
kept for the reason the paragraph above gives, that a future outcome-carrying transition must opt in deliberately
rather than inherit the re-issue, and it is declared here as an equivalent mutant rather than counted as a
mutation row, because a row that cannot fail is the false green this list exists to prevent. And `_resolve_lost_renewal` ends in a refusal after `a2.row3.refuse` for the
case where `plan_reservation` grants. That case is unreachable, because the only record the planner grants over
that a store writes is `FAILED`, which re-issued a line earlier; it is there because the function must return a
reservation it wrote or raise. Replacing it with `return reservation` fails no test, and the mutation table says
so. The first draft of this paragraph said the insert path "ends the same way", and the review showed that its
line is **reachable**: a rival inserts attempt 1 and fails it between the lost `COMMIT` and the re-read, the
re-read finds `FAILED` at 1 under the rival, and the planner grants a renewal over it, so the refusal is that
last line's alone. Replaced with `return reservation`, it handed the held process attempt 1 on a record it never
wrote and failed none of 112 Postgres tests. T246b's `insert-refuse` variant is that interleaving, and it kills
the mutant.

**One write on `approvals` was not a compare-and-set, and the round-2 survey found it.** `_expire` marks a
lapsed approval `expired` from a read that saw it `granted` past `expires_at`, and matched on `approval_id`
alone. A consumption committing between that read and that write was overwritten, so an approval that
authorised a real effect read `expired` and the evidence said a human's yes had never been spent. `v0.6 §4.2.2`
keeps that write deliberately, as evidence; evidence that overwrites newer evidence is worse than none. It now
carries the status it read, as `_consume_locked`, `grant_approval` and `deny_approval` already did, and a row
count of zero needs no refusal because the caller is being refused anyway by the verdict that asked for the
write. Its test holds the expiring `UPDATE` while another process, whose clock is still inside the approval's
life, consumes it. SQLite's expiry is inside the same `BEGIN IMMEDIATE` as its read and was never exposed.

**The proxy grew a mode, not a sibling.** `failure_injection.Proxy` gained `arm(predicate)`: a predicate over
each parsed client message, which holds the first match (one statement, on one connection) until `release()`.
`partition` already held traffic, but on every connection at once and at a moment the test chose rather than at
a statement. The predicate reads SQL from the Parse message, never from a Bind parameter, for the reason
`is_commit` parses frames, and a fixture test pins that. psycopg sends a query's text only until it has run
that query five times on one connection and then executes a prepared statement with none, so a predicate sees
only a query's first few executions; every held statement here is within them, and every test that holds one
asserts the hold fired. The first version was an attribute, `hold_when`, and one-shot without saying so: its
events were never cleared, so a second hold on one proxy reported itself held at once and forwarded its
statement, the review found. Items 3 and 4 will reuse it, so `arm()` clears `holding` in place, gives each
arming its own release event, and refuses to arm while a hold is armed or held; a server-free fixture test arms
it twice over real loopback sockets. Round 2 found the hole that was left: `release()` called while a hold was
armed but had not fired pre-released it, so the statement went straight through with `holding` set and `holds`
counting a window that never opened, which is the same lie in a narrower form and exactly what a `finally` that
releases would have produced. It now releases only a hold that has actually fired, under the lock `_holds` uses,
and the fixture test releases into an armed-but-unfired hold and then asserts the statement is still held. The
same round measured the prepare threshold rather than reading it off the default: a query's text travels on its
first six executions on one connection, executions 1 to 5 as unnamed Parses and 6 as the named Parse that
prepares it, and `statement_of`'s docstring says so.

### 12.3 Item 3: the idempotency token

**The derivation is §4.2's, byte for byte, and the three worked values are the code's.**
`idempotency_token_for("refund:txn_1", 1)` is `382ee448-97da-8107-b674-8c253650d93f`, attempt 2 is
`28bb40af-814c-8fb6-ba63-e8663b1c036d` and `refund:txn_2` at attempt 1 is
`89977bc9-d128-8ae8-871f-f5265155e60f`. T235 pins the first as a literal and the other two beside
it, so a change to the domain tag, the truncation, the version nibble or the canonical form is a
red test rather than a silent change of every token a deployment has ever sent.

**The domain tag is a module constant, `effect.IDEMPOTENCY_SCHEMA`, not a literal and not a private
name.** Every schema string in this codebase is a public `Final` beside the code that stamps it, as
`ACTION_SCHEMA`, `RECEIPT_SCHEMA` and `INSPECTION_SCHEMA` are, and none of them is in §9's frozen
list either; item 1's `_CLOCK_SKEW_TRIGGERS` is private because it is a closed vocabulary a caller
would otherwise be tempted to extend, which a schema tag is not.

**The binding is a context manager around `executor()` and nothing else.** `_attempt_token` sets the
variable where `held_key is not None` and resets it in a `finally`, so the value is gone whether the
executor returned, raised, or suspended. `_outcome` is the single place `execute`, `_observed` and
`resume` all reach, which is why a resumed leg reads the token of the attempt it resumes without a
second binding site: `resume` passes `held.record.attempt`, unchanged (`control.py:992`), and T233
asserts the number and the token together, so a change to either is visible.

**`held_key`, not `effect_key`.** The two differ for an observe-mode attempt whose reservation was
refused, where `effect_key` names the key and `held_key` is `None` because another attempt owns the
record. Binding on `effect_key` would hand that executor attempt 1's token, which names the real
holder's attempt, and it would do so on the one path where nothing can be written to say so. T236
drives it, and the test beside it, an observe-mode attempt that *does* hold its key and is answered,
is the control that keeps it from passing against a kernel that simply never answers in observe mode.

**The context variable takes a default of `None` rather than being left unset.** `_CONTEXT` and
`_PRESENTED_APPROVAL` are read with `.get(None)`; this one is read with `.get()` against a declared
default, which is the same refusal and lets a test undo a deliberately leaking mutant without a
reset token. The accessor refuses `None` and never guesses.

**G14 runs its control first.** The two reads and the refusal outside any executor are asserted
before the renewal, so a kernel whose answer moves within an attempt, and a kernel that sets the
variable and never resets it, both report `control failed` rather than a violation of the
observable, which is the distinction §1.3 draws. The leaking kernel is the sharper of the two: it
passes the observable, because each executor still reads its own attempt's value.

**The note beneath the table is not an N/A reason and not a finding.** §4.6's sentence is printed
under the table whenever G14 is graded, once, from `EFFECT_KEY_SCOPE_NOTE`, rather than in G14's
`detail.note`: `detail.note` is rendered under the guarantee's own row and only for the first
result carrying one, so a run where an earlier N/A already printed a note would have swallowed it.
A guarantee silent about the one thing a single-store run cannot check would read as having checked
it.

**No `reconcile` hook signature changed, and none needed to.** The hook is handed the effect key
(`v0.2 §11`), and the attempt is on the record, so `idempotency_token_for(key,
store.get_effect(key).attempt)` is the whole of what §4.5 asks for. Making the accessor answer inside
a hook was rejected in §4.3 and nothing in the code argued for it: the blocking call really does run
under a different attempt from the eager one.

**Its catalogue title is *token changes across a renewal*,** thirty characters, because the report's
title column is thirty-two and a longer one pushes every status on that row out of line. G13's entry
made the same choice for the same reason.

**The counts verify pins moved by one, as item 1's did.** G14 joins G3, G4 and G5 wherever the
effect template lives in a `@protect` decorator verify does not read, so `V1_PAYMENTS` reports six
over six with seven not applicable; the authority example reports twelve over twelve. The
catalogue-size guard in T101 was the literal `"8/8"`, which is the real summary now that eight
guarantees are applicable there, and it is computed from `len(GUARANTEES)` instead.

**Item 3a is what makes this sound on Postgres, and it is not merged.** The token is unique per
dispatch only where the attempt number is unique per key and the number `Control` is handed is the
number the store wrote, and on Postgres neither holds until item 3a lands (§1.4 item 3, §4.4, §5.6).
This item is built on `main` and cherry-picks nothing; T232 cannot see that defect and does not
claim to.

### 12.4 Item 4: the attempt ceiling

**The three equality gates became one ordering, and there is now a place to put the next one.** §5.3 named
`policy.py:912`, `931` and `1171` and said each becomes "this version or later". Writing three more membership
tuples would have made the fourth schema's item write three more again, so the comparison is one function,
`_at_least(schema, minimum)`, reading `SUPPORTED_SCHEMAS`'s order. `require_v3` and `require_v4` stay separate
functions, for `require_v4`'s own reason: the *consequences* differ per key and the sentence an operator reads
is the point. A name not in `SUPPORTED_SCHEMAS` is treated as too old, which is the fail-closed direction and is
unreachable from `Policy._from_document`, where an unknown schema is refused before any gate runs.

**Naming the line needed the document's marks back, and they are gone by the time an entry is parsed.**
`strict_load` hands `_parse_entry` a plain mapping, and PyYAML drops a node's marks the moment it constructs one:
`construct_yaml_map` builds a bare `dict` and copies into it, so even a mapping subclass returned from
`construct_mapping` would not survive. Two designs were rejected before the one that shipped. Carrying marks
through the parse means a mapping type every caller of `strict_load` inherits, `authority.py` included, for a
message on a path that refuses the document anyway. Searching the text for `max_attempts:` finds the wrong action
in a document with two. What ships instead is `yaml.compose` **on the refusal path only**: the text is threaded
from `from_yaml` to `_from_document` to `_parse_entry` as a `line_of` callable, and the second parse happens once,
for a document that is about to be refused, and asks the loader for the one mark the message needs. Where the text
is not available, which is only `_from_document`'s own default, the message omits the line rather than inventing
one.

**One comparison for both defences, so they cannot drift.** `Control._over_the_ceiling(ceiling, attempt)` is the
whole of "past the ceiling", and the fast path and the check both call it. Two spellings of the same comparison
would be a second definition to keep right, and §5.5's argument is that the two defences are *independent in what
they read* (a record before reserving, a reservation's assigned number) and identical in what they conclude. The
tests keep them apart by the evidence each leaves, which is what §5.5 asks for, rather than by patching one of two
comparisons.

**What the fast path may refuse is normative and is now a single `if`.** `_ceiling_fast_path` returns `None` for
any record that is not `FAILED`. An earlier draft refused at or above the ceiling whatever the state, which reads
as stricter and is worse: it would refuse an `AMBIGUOUS` record with `attempt_ceiling` instead of letting the
reservation raise `AmbiguousEffect`, it would take T245's and G15's only route to the check away, and it would put
a `blocked` receipt saying `attempt_ceiling` on an effect whose outcome nobody knows.

**The observe-mode half is in two places because enforce mode's order is.** The fast path's `would_have` entry is
recorded in `execute`, before `_observed` is called, because in enforce mode the fast path runs before the approval
gate and `_Observation.block` keeps the **first** reason; recording it inside `_observed` would let
`approval_required` win a race enforce mode does not have. The check's half is in `_observed`, after
`_observe_secure` returns, and reads `Policy.max_attempts` directly rather than taking `_ceiling`'s warning path:
a reservation exists there, so an effect key exists, so the warning cannot apply.

**G15's two `N/A` reasons are decided by selecting twice, and so is G5's one.** `select()` gained
`needs_renewal`, `needs_ceiling` and `ceiling_bound`, and the precedence §8.9 states is implemented as a second
`select()` with the ceiling filter removed: G5 prints `CEILING_FORBIDS_RENEWAL` only where that second selection
finds something, and otherwise `unselected()`'s sentence, which is what keeps a deny-only or ungranted document
from being told its ceilings took the guarantee away. The same shape gives G15 `CEILING_ABOVE_BOUND` only where an
action does declare a ceiling and every one is above 100.

**G14 was amended after the fact, because it did not exist when item 4 was built.** §8.9 says item 4 makes the
change for both G5 and G14 "since item 3 lands G14 before `max_attempts` exists"; item 3 was a parallel lane and
had not merged, so there was no `g14` to amend and the first commit wired only G5. The mechanism was built to be
reused rather than copied, and when `main` arrived carrying items 3 and 5 it was: `g14` now selects with
`needs_effect=True, needs_renewal=True` and reports through `_renewal_unselected(...)`, unchanged, in two lines.
**Four merge points, not two**, and the two extra are the ones a textual merge gets wrong quietly. The catalogue
tuple is the first: items 3, 4 and 5 each appended after G13, so `GUARANTEES` had to be reordered by hand to
G13, G14, G15, G16, because `BY_ID`'s insertion order *is* the report's order and a report listing G15 before
G14 would be the first one that did. The N/A reason constants and `__all__` come from both sides and both were
kept. And `ci.yml`'s `AUTHORITY_NA` and `TEMPLATES_NA` were **measured from a run of the merged catalogue**
rather than taken from either branch: 13/13 with 2 not applicable (G13, G15) and 7/7 with 8 (G3, G4, G5, G8, G9,
G13, G14, G15). Taking either side's number would have been a green CI assertion about a catalogue that no
longer existed.

**The refusal's `ActionDenied` yields to the store's.** Where the record moved on between the reservation and the
release, `begin_execution` or `fail_effect` refuses, and §5.7 says that refusal propagates after the `blocked`
receipt. So `_refuse_ceiling` keeps the store's exception and raises it in place of `ActionDenied`, after
appending the event and writing the receipt: the caller is told the truer thing, which is that the key is not
theirs any more, and `v0.1 §5.5`'s rule that a store's refusal propagates is not weakened by a path that refuses
for a different reason.

**T247 asserts a property, not an interleaving, and says so.** Six OS processes race renewals of one key on
Postgres under a ceiling of three. Racing processes do not reliably stall between a `SELECT` and an `UPDATE`, so
the test cannot claim to open item 3a's window and does not: it asserts that the total number of executor calls
never exceeds the ceiling, that at least one was made, and that at least one process was refused with
`attempt_ceiling`. **The independent review measured what that is worth**, and the numbers belong here rather
than in a claim: the children start within 8 ms of each other with overlapping windows, and with the check
deleted T247 goes red on 3 runs in 12, with the fast path deleted on 0 in 10. So T247 grades the property and
`T245`'s deterministic window grades the check. The review also showed the first version of this paragraph was
false: every assertion it listed is satisfied by six processes running one after another, which is what the test
did before the feed-all-then-wait loop, and the reviewer proved it by putting the serialisation back and watching
it pass. T247 now asserts that at least two children's execution windows **overlap**, which is the one thing a
serialised run cannot produce; the serialisation mutant is red 3 runs in 3 against it.

**The review's two blocking findings were both sentences that were not true, and neither was fixed with code.**

*§5.5 claimed the fast path saves a human from being asked, with one stated exception, and the exception was the
wrong one.* The reviewer reproduced the real case on the kernel's own primary route, not the adapter's: under
`max_attempts: 1` with `decision: approve`, attempt 1 times out, attempt 2 carries a `reconcile` hook and
presents nothing, the fast path correctly lets the `AMBIGUOUS` record through, and `_secure` reaches `_presented`
**before** any reconcile runs. A new approval request is created, a human grants it, the retry consumes it in the
reservation the check then refuses. One dispatch under a ceiling of 1 and one human answer spent. **Not closed,
deliberately.** Re-reading the record between `_reconciled(...)` and the second `_take` would save the approval
and would destroy "the check alone is reachable through a public route, with no seam", which is the only route
T245 and G15 have to the check: a guarantee that could not have failed is not a pass (`v0.4 §1.3`). So §5.5 stops
claiming exhaustiveness and lists the routes it knows with a warning that the list is not closed, §5.2 says a
**new** request can be created and granted on the way to a refusal rather than only that an existing one stays
consumed, and a test pins the behaviour so the claim cannot drift back.

*G15's `CEILING_ABOVE_BOUND` was false of a document with a low ceiling on an action verify cannot drive.* The
fallback selection re-applies the effect and ceiling filters and drops only the bound, so it can only ever
describe the selectable actions, while the sentence said "every declared". A deny-only action with
`max_attempts: 3` made it a lie. It is now worded on `NO_CEILING_DECLARED`'s shape, scoped to what verify can
drive, which is the identical fix §8.9 had already made to the sibling sentence: **the same defect, in the
sentence written next to the one that documented it.**

**Nine smaller findings, and what each changed.** The fast path's refusal now records the presented approval, on
`v0.6 §7.2.1`'s rule, which a previous review had already applied to the `DENY` path and which this path had
missed the same way. `_refuse_ceiling` writes the event and the receipt **before** attempting the release and
catches any `CTRLRunError` from it, because `_checked` raises `InvalidArgument` for a record that moved under a
different `action_id` with a dead lease, and that escaped before either was written, leaving a `RESERVED` record
nothing explained. T240 to T245b run on Postgres as well as SQLite and in-memory, which §8.4's first sentence
asked for and the fixture did not do; the positive control drops `CLOCK_SKEW_DETECTED` from its comparison,
because every Postgres scratch store here is opened with a frozen clock and truthfully reports days of skew
(§1.4 item 6). §5.5 says the refused number is spent and what it costs an operator who later raises the ceiling;
§5.7 says `max_attempts` bounds attempts and not executor invocations, since a `Suspended` executor can be
resumed indefinitely on one attempt and only the gateway's `max_elicitation_rounds` bounds that; §7.1 is the
per-entry-point enumeration item 4 owed for amending `v0.3 §4.3.1`'s order, with every "no" row written down,
and `Control.evaluate`'s docstring now says it does not see the ceiling. G15's title was 48 characters against
`report._TITLE_WIDTH`'s 32, the only one over, so it is "renewal past the ceiling refused" and a test asserts
no title ever exceeds the width again.

**Round two found nothing blocking, and its seven notes are mostly about this document being wrong about the
code.** Three were.

*A guard that was an equivalent mutant, reported as a red row.* Round one's fix for the escaping
`InvalidArgument` did two things: it moved the evidence above the release, and it wrapped the release in
`try/except CTRLRunError: raise refused from None`. Only the first is load-bearing. The reviewer deleted the
whole `try`/`except` and got 114 of 114 green, because it caught only to re-raise the same object and nothing
observes `__suppress_context__` (which is `None` there in any case). The clause is **deleted**: a store refusal
now propagates by not being caught, which is what §5.5's prose said all along, and the mutation table loses the
row that claimed to grade it. **A red row for an equivalent mutant is a false green in the table**, which is
worse than no row, and this one survived a round of review by looking like the fix for a real finding.

*§5.5's numbered list was in the wrong order and too certain.* It put the release second, before the event and
the receipt; the code does the event, the receipt, then the release, which is the fix round one made and the list
did not follow. And the release **may not happen at all**: measured, after an `InvalidArgument` release the
record sits `RESERVED` at the refused attempt under a live lease while the receipt says `blocked` at that same
attempt. The list is renumbered to the code's order, step 4 is qualified "where the release succeeds", and
§5.2's crash paragraph now says that a failed release lands in the same place a crash does, because it does.

*§6's provider runs in front of the ceiling, and no section said so.* The merge put item 5's precondition
recheck immediately before each `_take`, and the reconcile route takes twice, so under `max_attempts: 1` a
doomed attempt calls the operator's provider **three times** before the check refuses: once on the request pass
and twice on the retry. Worse, a provider that raises on that attempt makes the refusal
`ApprovalMismatch(reason="precondition_unavailable")` rather than `attempt_ceiling`, writes no effect record, and
never runs the `reconcile` hook, so the operator is told the wrong reason for an attempt that could never run.
Both are measured, both are in §5.5's reconcile bullet, and both are stated rather than closed for the same
reason the human's answer is: moving the ceiling in front of the fetch needs the seam that bullet already
declines. The sequential route calls the provider **zero** times, which is §6.6's principle holding wherever the
fast path can answer, and that is said too. Three tests pin all three numbers.

The other four were smaller and all four were true. T247's overlap assertion is real, but the docstring claiming
"nothing but this would notice" the catalogue swap was not: `tests/test_verify.py`'s catalogue test has asserted
the same ordering since before this branch, and the reviewer showed both tests failing on the swap. The test
stays for locality and for `BY_ID`'s own order, which the other one does not assert, and the docstring says so.
T248's "the approval gate ran before the reconcile" was **inferred**: the hook answered the same whenever it ran,
so the assertion held either way. It counts now, and asserts zero calls at the moment the human is asked.
`_projection` **normalises** `CLOCK_SKEW_DETECTED`'s three volatile fields rather than dropping the event, so the
comparison still counts them and still fixes where they fall.

### 12.5 Item 5: precondition fingerprints

It narrows; the residual window of §6.7 is T261b's, and nothing written for this item says otherwise.

**§6.6's read is where a refusal is raised from, and not only where it is found, and it writes
nothing.** The first build read the record, skipped the provider on a refusal verdict, and let
`_take` raise the store's own refusal. That had a hole: a `pending` approval a human grants between
the read and the store call was then consumed with no comparison, which is a skip reached by timing.
So where the read's verdict is a refusal and the precondition question is live (a provider is named,
or the record carries a fingerprint), `Control` raises that verdict itself, from the same pure
`check_consumable` every store applies, with the reason and message the store would give. T262's
pending-race case opens that window with a grant that lands inside the read.

**The first build made one exception, sending an expired grant to `consume_approval` so the lapse
would be recorded as 0.6.1 records it, and the independent review measured what that costs.** With
`Control`'s clock two minutes ahead of the store's and a minute of life left by the store's, the
store consumed the grant: the row said `consumed`, the events said `APPROVAL_EXPIRED` and
`APPROVAL_INVALIDATED` with no `APPROVAL_CONSUMED`, and the receipt said blocked. Safe, and untrue,
and it quietly moved `v0.1 §4.2 A3`'s question of whose clock decides expiry from the store to
`Control`. So this path now writes nothing at all: the row keeps the status the store gave it, the
lapse this clock saw is in `APPROVAL_EXPIRED`, and `check_consumable` refuses the grant at every
later presentation. Where neither side has a fingerprint the store call is 0.6.1's exactly, on the
store's clock, including its lapse write, and the divergent-clock test pins that too.

**The read runs on every presenting pass under `APPROVE`**, provider or not: only the record says
whether an approval carries a fingerprint, and one that does, presented by a call naming no
provider, is refused (§6.4). The cost is one `get_approval` per approved action.

**`precondition_missing` on a call that names a provider fetches first**, so the event's
`precondition_at_recheck` is set and §6.4's "one of which is null" holds for both cases. A provider
that fails there is `precondition_unavailable`: the outage is what an operator fixes first.

**What `error` holds**, in the event, the receipt's `error` and the log line alike: the provider's
exception by type name; `returned <type>, not a mapping`; or the type name of what `canonical_bytes`
raised, whose message can quote the value it refused. T260 plants a sentinel in a provider's
exception message and finds it nowhere.

**A fingerprint that is computed and not recorded is refused on the request pass, and the request is
withdrawn.** The review's blocking finding: a store that drops the column and a third-party
`ApprovalProvider` that builds its own `ApprovalRequest` both leave an approval requested with a
fingerprint carrying none, and at presentation *neither* side has one, which is §6.2's first row and
0.6.1's path. Every call naming no provider consumed it with nothing compared, which is exactly the
skip §6.4 forbids. The request pass reads its own request back, through the returned object and
through `get_approval`, and where the fingerprint is not there it refuses and withdraws the request
with the store methods that exist: `deny_approval` while it is pending, `consume_approval` for a
grant that landed inside the window. **The residual is in §6.4**: an approval granted *and presented*
inside `request()` is spent before `Control` knows the request exists, and closing that needs a store
call that records the request and its fingerprint together. `StateStore` is frozen (`v0.6 §9.2`), so
that is a finding for the maintainer and not a method this item adds. The honest test names it.

**A withdrawal reports what happened to the request, not what was read before trying.** The first
build of it returned the status from the read *before* its own failed `consume_approval`, and said
`consumed` whether it had spent the grant or another caller had. The review drove a presentation that
won that race: it **ran the action**, and the evidence said the request had been withdrawn `granted`
while the row said `consumed`. So the row is read back after a failed write and the answers are
distinct -- `denied`, `spent`, `already_consumed`, `not_withdrawn:<status>` -- and the refusal calls
itself a withdrawal only for the first two, which are this call's own writes.

**Every exception in the withdrawal is caught, on `_spend_unneeded_approval`'s argument pointed the
other way.** A `sqlite3.OperationalError` out of `deny_approval` used to leave `_presented` with no
`ACTION_DENIED`, no receipt and an answerable unfingerprinted request. There, catching everything is
safe because the action proceeds and there is nothing to protect; here it is safe because the action is
refused whatever the store does, so a wider catch can only add a refusal and its evidence. No other
handler in this file may widen on either argument without making it again.

**The object-side half of the recorded check was subsumed, and is gone.** It compared the
`ApprovalRequest` the provider returned as well as the record read back. A presentation reads the
store, so a returned object that differs from the row changes nothing a later pass sees, and both
reachable causes (a store without the column, a provider that builds its own request) are visible in
the read-back. Collapsed rather than kept as documentation, which is what `CONTRIBUTING.md`'s first
shape asks for.

**Rejected for v0.7: making the fingerprint recordable by a third-party provider.** The reachable cause
of §6.4's residual is as much the frozen `ApprovalProvider` protocol as `StateStore`'s method set:
`build_request` is package-internal and the context variable it reads is private, so a provider outside
this package cannot record a fingerprint however careful it is, and the kernel can only detect that
afterwards. Publishing either would remove the cause rather than close the window, which is the better
shape of fix. It is not taken here because it is a public name on a frozen surface (`v0.1 §8`,
`v0.5 §9`) and because the case is **unreachable with all three shipped providers**, which build their
requests through `build_request`; v0.7 refuses the reachable symptom instead, and the decision is
recorded so a later milestone can weigh the name rather than rediscover the argument.

**Deferred, with its blast radius: a malformed value of a key a schema declares.** `_controls_of`
raises out of `from_dict`, so one `UPDATE` putting a float among a receipt's `controls` blinds
`ctrlrun receipts`, `receipts --verify-chain`, `inspect`, `stats` and G11 together, where the
schema-level and added-key cases are each reported at their `seq` and leave every other row readable.
v0.7 neither introduces nor widens it: 0.6.1 behaves the same. Fixing it needs a new name in
`CHAIN_BREAKS`, which is a closed set on a `v0.6 §6.5` surface, or a reader that walks raw rows, and
neither belongs in an item about preconditions. Item 6 carries it to the roadmap as a named item before
v1.0, with this paragraph as its statement.

**A provider outage in observe mode costs the duplicate refusal too.** Observe mode records a failed
comparison and runs holding nothing, which is 0.6.1's observe path for any approval mismatch, so
while a provider is down every observed action under `APPROVE` with an effect key runs without
reserving its key: two of them are two unreserved attempts, and neither `would_have.blocked_reason`
says `duplicate`. Enforce mode refuses instead, so nothing is lost there.

**Everything a provider hands back is inside one `try`.** `isinstance(state, Mapping)` sat outside
it, and `isinstance` reads `__class__`: an object whose `__class__` raises carried its own message
out of `Control` as a raw exception, with no refusal reason and no receipt.

**`ctrlrun inspect` adds no field.** Its approval entries, the webhook document and the operator
server's pending listing carry no fingerprint (§6.10); `inspect --json` embeds the receipt and the
events, and those carry the fields §6.10 and §6.11 place there, as evidence rather than as the
question put to a human.

**The three reason strings are private constants in `control.py`.** §9.2 adds no public name for
them and says so; every test asserts the string.

**Observe mode records a failed comparison the way it records any presented approval that does not
match**: `APPROVAL_INVALIDATED` with the reason and both fields, `would_have.blocked_reason =
"approval_mismatch"`, and the action runs holding no reservation, which is 0.6.1's observe path for
an approval mismatch. No grant is spent.

**A resumed leg's receipt records the comparison its first leg made.** The first build left
`precondition_at_recheck` null there, on the ground that this leg compares nothing, and the review
found the consequence: the first leg of a suspended action writes no receipt, so the one comparison
that happened left no trace anywhere and §6.11's *on a committed action they are equal* was false of
every resumed leg. The leg that consumes the approval now records what it compared on its
`APPROVAL_CONSUMED` (hashes only, and nothing at all where nothing was compared), and `resume` reads
it back from that event. Where no such event exists, the record's own fingerprint fills
`precondition_at_request` and the recheck field stays null, which is what a leg that compared
nothing should say.

**Rendering a label this binary does not know.** §6.11 fixes the four known schemas. An unknown
label renders under `v3`'s keys with its own label; an absent one renders with no `schema` key. The
two `v4` fields are rendered only for a `v4` label, since they are read only from a `v4` document.
A `v1` receipt's principal renders as `v1` wrote it, `agent` and `user` only.

**`put_receipt` writes `v4` whatever schema the receipt was read under.** A `v1` or `v2` key set has
no `seq`, so a read-back receipt written again under its own label would carry no position in its own
document. A receipt written again is a new row this binary writes.

**The stored document is excluded from equality**, and the store conformance suite's field-by-field
comparison skips fields excluded from equality, because the receipt read back has a stored document
and the one written has none by design.

**A stored row whose document has no canonical form is `content_altered`, not a raised walk.** The
first build let the canonicalizer's refusal out of `verify_chain`, and the review showed what that
costs: an added key holding a float or a lone surrogate ended the walk, `ctrlrun receipts
--verify-chain` exited with no report at all, and a forged `decision_reason` at another `seq` went
unnamed. The claim that 0.6.1 behaved the same was false for an added key, which 0.6.1 ignored when
hashing. `put_receipt` hashes what it serializes, so a stored document this reader cannot
canonicalize is one nothing here wrote: it is reported at its `seq`, by the refusal's type and never
its message, and the rows that link to it are told it has no computable hash. A malformed value of a
key a schema declares, a float among `controls` say, still raises out of `from_dict` as it did at
0.6.1, and that case is the one §6.11's last bullet already covers.

**`ApprovalRequest` does not validate the fingerprint's shape.** A malformed stored value compares
unequal and is refused `precondition_changed`; validating at construction would make a tampered row
raise out of `get_approval` and blind every reader of that approval.

**G16's title is "a moved fingerprint is refused".** "A moved precondition is refused" is
unqualified and §6.7 says why: a precondition that moves after the comparison is not refused. A title
is the shortest sentence this project writes about a guarantee, so the titles are scanned by T268
with everything else this item writes.

**`PRECONDITION_NOTE` is not a public name.** It went into `verify.guarantees.__all__` and not into
§9.2, and §9.2 is the list of what v0.7 adds; `scenarios.py` reads it as an attribute, as it reads
every other reason in that module.

**Verify prints each distinct note once**, where it printed only the first. G16's note is a
different sentence from G3's, and the first rule dropped it on every document that also lacked an
`effect:` template.

**G16 grades a change before the comparison.** A change after it is not refused by a correct kernel,
so there is nothing there for verify to grade; T261b is where that residual is kept honest.

**CI's `verify` job now expects `verified 12/12` and `verified 7/7`.** Item 1's G13 moves both again,
and whichever lands second rebases the two lines.

### 12.6 Item 6: the release
