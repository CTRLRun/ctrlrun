# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Public API names are frozen in `docs/SPEC-v0.1.md` §8. Before 1.0 they may still change, and
any change to one appears here.

## [Unreleased]

### Added

- **A credential revoked before its `exp` is refused** (`docs/SPEC-v0.8.md` §6). `jwt_identity.py`
  used to say, in as many words, that *a verified token is valid until its `exp`* and that nothing
  polls. Both sentences are gone.

  ```python
  from ctrlrun.revocation import FileRevocationFeed

  JWTIdentityProvider(..., revocations=FileRevocationFeed(path, issuers=[ISSUER]))
  ```

  Security Event Tokens are consumed, from a file the operator's own transmitter writes or by RFC
  8936 poll delivery. **Nothing subscribes and nothing introspects**: a subscription needs an
  endpoint this project serves and an introspection call is a question it asks nobody. Consuming an
  event is reading it.

  **The match is against the token's own `iss`, `sub` and `jti`, never against
  `Principal.agent`.** `agent` is whatever `agent_claim` names, which a deployment may set to
  `client_id`, so matching an `iss_sub` identifier against it would compare two different things
  and admit exactly the deployment the feature was bought for. The check runs inside the provider,
  where the raw verified claims are still in hand, and nothing new is stored on `Principal`.

  **Two things this closes less than it sounds, both stated wherever the feature is described.** A
  revoked credential leaves a **log line and no receipt**: resolution happens before an action
  exists, so there is no `action_id` to attribute a refusal to, where an *expired* credential
  leaves a receipt. And a feed is worth what its source is worth: whoever can write the file can
  refuse the operator's own agents, which is a denial of service against them and is fail-closed.
  They cannot **admit** a principal the issuer revoked, because the feed is only ever consulted to
  refuse. That asymmetry is the security property.

  **`max_staleness` is the operator's call.** Unset means no bound, which is 0.7.0's availability.
  Set, and every principal of a covered issuer is refused past it with `revocation_feed_stale`,
  because "has this been revoked" is exactly the question a stale feed cannot answer. Configuring
  it makes the feed's availability part of the deployment's, and a kernel choosing that for an
  operator would be choosing their outage budget.

  Behind `ctrlrun[identity]`, beside the provider it serves. `import ctrlrun` imports no part of
  it. `ctrlrun verify` grades **G20** against a feed verify supplies, with a note saying so rather
  than an `N/A` claiming something about a document that is silent on the subject.

  No standards claim. RFC 8935, RFC 8936, RFC 9493 and CAEP are consumed as code, and the words
  compatible, conformant, aligned and certified appear nowhere.

- **Break-glass is a grant, and there is no flag** (`docs/SPEC-v0.8.md` §5). An incident needs
  authority nobody was granted in advance. The wrong answer is a setting: a setting leaves no
  record, expires never, cannot be revoked and cannot be narrowed. `authority.py` already has
  grants that are all five, so break-glass is a delegation beneath an **envelope** the policy
  declared in advance.

  ```yaml
  authority:
    break_glass:
      incident-payments:
        subject: {agent: "oncall-*"}     # who a grant opened here may be FOR
        actions: ["payments.*"]
        constraints: {amount_lte: 50000}
        max_ttl: PT4H                    # the longest expiry a grant beneath it may carry
        controls: [incident-response]    # whose approver_role gates who may OPEN it
  ```

  ```
  ctrlrun break-glass --envelope incident-payments --file grant.yaml --reason "INC-4412"
  ```

  **The envelope decides nothing, by construction.** It lives in `Authority.envelopes`, a mapping
  separate from `grants`, because the candidate set is every entry of `grants` unconditionally: an
  envelope living there would decide actions, which is the opposite of what it is for. The test
  asserts it is absent from the candidate set rather than merely unmatched.

  **It is covered by the policy hash, `max_ttl` included.** The argument for declaring the widest
  authority an incident can reach in a file is that somebody reviewed it *before* the incident, and
  that argument is only true if widening it moves every receipt.

  **There is no `--as`.** Whoever opens one is the principal the deployment's approver identity
  resolves, gated by the envelope's `controls:`; a deployment that names no approver identity
  cannot open one at all. An assertion typed at a shell is exactly what break-glass must not
  accept.

  **What it is afterwards**: recorded, with `created_via: break-glass`; expiring, and bounded by
  `max_ttl` from the moment it was opened; revocable, and revoking it stops everything beneath it;
  attenuable, obeying `child ⊆ parent` on every dimension. A setting has none of those.

  **`Receipt.authority_grant_id`** now names the grant that decided the action, for **every**
  action decided by authority and not only under break-glass. A field exercised only on the rare
  path is one nobody notices breaking.

  `created_via` gains its third value, which is a public change rather than an addition: the
  vocabulary is a closed set and a record carrying an unknown value is unreadable, which answers
  `authority_unreadable` for every action in the deployment. It moves with every reader in one
  commit.

  No guarantee id. The roadmap assigned five to v0.8 and G22 to G24 to v0.9, so inventing a sixth
  would collide or renumber, and a renumber is the maintainer's change. Its evidence is its tests,
  and one of them greps the shipped package for sixteen names a flag would be spelled as.

- **M-of-N approvals** (`docs/SPEC-v0.8.md` §4). An action may require more than one yes, and what
  the threshold counts is **distinct verified principals**: a second answer from a principal that
  already answered is recorded, moves that entry's `granted_at`, and does not move the count.

  ```yaml
  schema: ctrlrun.policy/v6
  actions:
    payments.refund:
      decision: approve
      approvals_required: 2
  ```

  **The count is decided where the row is written, on all three stores**, and never by a read
  followed by a write: SQLite counts inside its `BEGIN IMMEDIATE` transaction, Postgres
  compare-and-sets on the approver list it read and retries, and the in-memory store holds its
  lock. Two processes answering at the same instant produce two approvers or one, never a
  threshold reached twice.

  **A yes that cannot be attributed does not count.** `approvals_required` above 1 in a deployment
  that names no approver identity is a denial, not a silent downgrade to one approval: the kernel
  cannot tell two anonymous yeses apart, so it refuses rather than counting them. `ctrlrun approve`
  records no verified approver and therefore never counts toward a threshold, which the CLI says
  at the moment it is used rather than leaving to be discovered.

  `ApprovalStore.grant_approval` now returns `Approval | None`, where `None` means **recorded and
  still short of N**. Nothing is granted, no `APPROVAL_GRANTED` event is written, and a consume
  attempted below the threshold is refused as `pending` with nothing reserved.

  Needs `ctrlrun.policy/v6`. `ctrlrun verify` grades **G19** under `ctrlrun.guarantees/v4`, `N/A`
  where every action in the document takes one approval.

- **Entitlement from the control registry** (`docs/SPEC-v0.8.md` §3). A control may now name the
  role that answers for it, and an approval whose recorded entitlement does not cover the roles
  the request pinned is refused, with the control named in the message, the exception and the
  `APPROVAL_INVALIDATED` event.

  ```yaml
  schema: ctrlrun.policy/v6
  controls:
    card-data-handling:
      title: Cardholder data changes are approved by a named owner
      approver_role: payments-owner
  ```

  **CTRLRun does not interpret the role.** It does not know what `payments-owner` means, does not
  check that such a role exists anywhere, and makes no compliance claim on the strength of one,
  exactly as it does not interpret `source:`. What changed about `SPEC-v0.6.md` §7.3's
  "attribution, not prevention" is one sentence: a control still decides no *action*, and now
  decides **who may answer an approval the decision already required**.

  **Omission is not entitlement, and a control naming no role gates nobody.** Two sentences that
  mean opposite things: a principal whose claims lack the role is not entitled, because a missing
  claim is a statement about a person and the kernel refuses to invent one; a control with no
  `approver_role` gates nobody, because a missing role is a statement about the operator's
  document and inventing one there would refuse every approval in every deployment that has
  controls and has not heard of v0.8.

  **Roles are matched byte for byte**, in both claim shapes. No case folding, no trimming, no
  prefix matching and no pattern grammar: a wildcard in a role would be an entitlement nobody
  wrote. Where an evaluation cites several controls, **every** required role must be held, because
  any-of lets the weakest control in the set decide who may answer.

  **`ClaimValue` gains a tuple of strings**, amending `SPEC-v0.3.md` §2.1. A roles claim is a JSON
  array at every issuer anybody deploys, and the old rule meant such a claim arrived *absent*, so
  its holder was silently unentitled. `JWTIdentityProvider` carries an array-of-strings claim now
  instead of dropping it, and says so at WARNING rather than DEBUG when it drops anything else it
  was asked to carry. Safe for hashes: `v0.3 §2.2` keeps claims out of an action's canonical form.

  **The check is bounded and the bound is stated.** What the kernel refuses is an approval whose
  *recorded* entitlement does not cover the role; what entitled it was decided where the credential
  was verified, which is the operator MCP server (`ctrlrun mcp-operator --approver-roles-claim`)
  and an embedding application. `docs/SPEC-mcp-operator.md` §4.3 and §10 are amended to say that,
  and §4.3 now carries a three-row table instead of one sentence, because the sentence covered
  two unconfigured cases that behave in opposite ways: a control naming no role admits any
  verified human, and a control naming a role in a deployment with no claim to read roles from
  refuses **everyone**. The server warns about the second at startup rather than at the first
  refusal.

  Needs `ctrlrun.policy/v6`. `ctrlrun verify` grades **G17** under `ctrlrun.guarantees/v4`, `N/A`
  with a reason that is true of a document naming no approver role.

- **The approver is a principal** (`docs/SPEC-v0.8.md` §2, §4.1). `Approval.approver` is a string
  whose only check is that it is not empty, and `adapter.py` has always conceded what that string
  often is: a channel, wherever the framework's primitive does not identify a person. A deployment
  may now name an **`ApproverIdentity`**, and where one is named an approval is consumable only if
  the store holds a **`VerifiedApprover`** for it: a principal the granting surface resolved,
  recorded on the approval row, and carried onto the receipt.

  **Opt in, then fail closed**, which is `SPEC-v0.3.md` §1.2's rule for authority applied to the
  approver. A `Control` built without one behaves exactly as 0.7.0 did, asserted field by field on
  the whole approve-and-execute path. One built with it gets no partial mode: an approval whose row
  carries no verified approver is refused with `approver_unverified`, **including one granted
  before the provider was configured**, including one granted through a surface that cannot
  resolve, and including one held by a store that ignores the column.

  **`ctrlrun verify` grades seventeen guarantees now**, G18 among them under
  `ctrlrun.guarantees/v4`: an approval granted by the principal that requested the action is
  refused, compared on the resolved principal and never on the string. Verify supplies the approver
  identity it grades against, so what it reports is the kernel's refusal and never whether an
  operator configured anything, which is a fact about their application and not about their
  document.

  **Which surfaces can produce a verified approver, stated plainly because it is narrower than the
  feature's name suggests.** The operator MCP server can, and now does: it has resolved a principal
  for every request since it shipped and then discarded it into `mcp-operator:<user>`. An embedding
  application can. **`ctrlrun approve`, the webhook and the adapters cannot**, and the approvals
  they grant are refused wherever an approver identity is configured. A deployment whose approvals
  arrive through one of those three turns its approval path off by configuring this, which is the
  rule working rather than a defect, and §2.6's table is the thing to read before configuring.

  **Observe mode now records a mismatch's own reason where it recorded one constant for all of
  them.** `would_have.blocked_reason` said `approval_mismatch` for every `ApprovalMismatch`, so a
  moved precondition and an approver who may not answer were one word in a report. Recording the
  specific reason for the approver refusals alone would have left a vocabulary nobody can explain,
  so every mismatch records its own. This reaches refusals that have nothing to do with v0.8, and
  it is listed here rather than left for an operator to notice in a diff.

  Needs `ctrlrun.receipt/v5`, which adds `approvers` and `authority_grant_id`, and migration
  `0006_verified_approver`. Every reader upgrades before any writer switches (`SPEC-v0.3.md`
  §12.2).

- **`ctrlrun revoke --created-by PRINCIPAL` and `--under ID`** (`docs/SPEC-v0.8.md` §7). During an
  incident the operation an operator reaches for is *everything this principal issued* or
  *everything under this grant*, and until now that was a script over the events file, written
  under pressure. Both are queries over rows that already exist: no new `StateStore` method, no
  bulk statement, and no transaction over the set. **Each match is revoked exactly as one id is**,
  one at a time, so a run that stops halfway leaves the rows it reached revoked and the rest
  untouched, and a second run finishes. `--created-by` takes `AGENT` or `AGENT/USER`, splitting on
  the first `/` as `ctrlrun delegate --as` does; `--under` reaches the subtree at every depth and
  is strictly beneath, so it leaves the id it names alone. A selector that matches nothing **exits
  non-zero** and says what it searched for, because during an incident a mistyped name that exits 0
  reads as a finished job. Still no `unrevoke`, in any costume.

  **`--by` is unchanged and still means who performed the revocation.** The roadmap called the new
  selector `--by <principal>`, which is the opposite meaning on an option that already exists, so
  the selector is `--created-by` and every script written against 0.7.0 keeps working.

  **What a killed run can leave, stated because the tests bound it rather than assume it:** a
  revoked row whose `DELEGATION_REVOKED` event was never written. `Control.revoke` writes the row
  and then appends the event, with no transaction over the pair, so a `SIGKILL` between them leaves
  one row unaccounted for in the log. That is 0.3 behaviour for a single `ctrlrun revoke` too; a
  selector only makes the window easy to land in.

### Documentation

- **`docs/SPEC-v0.8.md`**: the v0.8 "Oversight" contract, a delta over v0.1 to v0.7. No code lands
  with it. It asks one question: *who may say yes, and can the kernel tell?* Seven milestones have
  verified the principal that acts, and nothing has ever been asked of the principal that permits:
  `approver` is a non-empty string, `ctrlrun delegate --as` is an assertion typed at a shell, and
  the operator server authenticates who answered without checking that they were entitled to.
  Seven items answer that: revocation by selector, the approver resolved as a principal,
  entitlement from the control registry, M-of-N on distinct verified principals, break-glass as a
  recorded expiring grant rather than a flag, credential revocation consumed from Shared Signals
  and CAEP events, and a policy change as a protected action with a diff replay beside it. Tests
  come from §10 (T272 onward); public names are frozen in §11; guarantees G17 to G21 join
  `ctrlrun.guarantees/v4`, and `ctrlrun.receipt/v5` and `ctrlrun.policy/v6` each move once.

  **The rule the whole document is built on is opt in, then fail closed**, which is
  `SPEC-v0.3.md` §1.2's rule for authority applied to the approver: a deployment that names no
  approver identity behaves exactly as 0.7.0, and one that names one gets no partial mode, no
  fallback to the string, and no setting that turns a check off.

  **Reading the code changed nine things the plan had assumed**, and §1.4 lists them: five from the
  drafting and four from the independent review, which found the first draft unbuildable in four
  places and is recorded rather than quietly fixed. Five matter beyond this document.

  `Control` never grants an approval, so the check that matters lives at the consumption and not at
  the grant. `Control._recheck` returns early on the default path, so a check added after it would
  have been dead there, green, and invisible to a mutation table. A `Principal` could not carry a
  list, and every issuer's roles claim is one, so `ClaimValue` gains a tuple of strings. The
  Postgres grant's compare-and-set was on a status that does not change at N-1, which is a lost
  update and one principal filling two slots. And a reserved action name no document may declare
  is a name every proposal is denied for, so the policy-change action is reserved *and* declarable.

  Smaller, and worth knowing before anyone scripts against it: `ctrlrun revoke --by` already means
  who performed the revocation, so the new selector is `--created-by` and every script written
  against 0.7.0 keeps working.

  **What it does not close is in §1.1, before anything else**: a persuaded approver gives a valid
  approval and the receipt records it as one, and an administrator with write access to the policy
  file, the store or the code is outside every guard here.

## [0.7.0] - 2026-09-11 - Execution boundary

Every milestone before this one asked what holds *inside* CTRLRun. v0.7 asks whether it holds at
the edges the kernel does not control. The kernel does not decide whether the remote acted, an
executor does. It does not own the clock its leases are measured against, once the store is on
another host. It does not know whether the world still looks the way it did when a human said
yes. Six items answer those edges:

| What it adds | Where |
|---|---|
| **`ctrlrun.transport`**, the `NotExecuted` classifier, in core and stdlib only. One rule, promoted out of `ctrlrun[gateway]` rather than copied, reachable from `@protect`. | §2 |
| **Clock-skew detection.** `PostgresStateStore` measures its server's clock against this host's and names divergence with a new event. It observes and reports, and changes no decision. | §3 |
| **Attempt numbers that never repeat.** Three Postgres defects that could hand one attempt number out twice, or move it backwards, fixed before v0.7 made the number load-bearing. | §5.6 |
| **The provider idempotency token**, `ctrlrun.idempotency_token()`, derived from `(effect_key, attempt)`: a deterministic handle for reconciliation, and one that changes on a renewal. | §4 |
| **The attempt ceiling**, `max_attempts`, a policy key bounding renewal after `FAILED`. Needs `ctrlrun.policy/v5`. | §5 |
| **Precondition fingerprints**, an approval bound to the resource state it was granted against and rechecked strictly before the reservation. Needs `ctrlrun.receipt/v4`. | §6, §7 |

Section numbers are `docs/SPEC-v0.7.md`, which is the contract; §9 freezes every public name
added here. `ctrlrun verify` now grades sixteen guarantees under `ctrlrun.guarantees/v3`, G12 to
G16 being new, each with a positive control and each `N/A` only for a reason that is true of the
document it was handed. `pip install ctrlrun` still installs `pyyaml` and `click` and nothing
else, `import ctrlrun` still imports no module from an extra, and `ctrlrun demo` still runs every
scenario in under a minute with no network.

### Stricter than 0.6.1, with what 0.6.1 did

Everything here can refuse, or record as unknown, something 0.6.1 accepted or recorded as
settled. Nothing here is a flag, and no setting relaxes any of it.

- **A continuation leg never records `FAILED`.** A continuation exists only because the remote
  answered and is holding the exchange, so nothing on that leg can say the remote did nothing.
  At 0.6.1 a refused connection on a continuation, a pre-dispatch JSON-RPC code, the `401` rule
  of `v0.2 §6.8` and a tool error under an operator's `not_executed_on_error: true` each recorded
  `FAILED`, and the gateway answered the client `-41011` "not executed", **which permitted a
  retry** of an effect the upstream may have been part-way through. The gateway now records
  `AMBIGUOUS` and answers `-41010`, and the effect needs `ctrlrun resolve`. The upstream's own
  response is relayed unchanged, tool error included.
- **Behind a proxy the gateway claims nothing.** httpx reports an unreachable proxy and a TLS
  failure with the *target* after the proxy answered the `CONNECT` line with the same
  `ConnectError`, and a written `CONNECT` line is a written byte. At 0.6.1 the forwarder mapped
  every `ConnectError` to `NEVER_CONNECTED`, therefore to a `failed` receipt and `-41011`. Where
  `urllib.request.getproxies()` names a proxy, `ConnectError` and `ProxyError` are now an unknown
  outcome: `AMBIGUOUS`, `-41010`, and a `ctrlrun resolve`. `NO_PROXY=*` is honoured and a
  narrower `NO_PROXY` is not consulted, so a bypassed host is judged as if it were proxied, which
  costs a claim and never makes a false one. With no proxy configured nothing changes.
- **`ctrlrun.policy/v5`.** A document that declares `max_attempts` must declare `v5`; 0.6.1's
  newest schema was `v4`, and a `v4` document naming the key is a `PolicyError` at load, with the
  key, the action and the line. Every document that loaded at 0.6.1 loads unchanged and renews
  without bound, because there is no default ceiling and no value of the key means "unlimited".
  An 0.6.1 reader refuses a `v5` document, as it should.
- **`ctrlrun.receipt/v4`.** Two new fields, `precondition_at_request` and
  `precondition_at_recheck`. **Upgrade every reader before any writer**: a `v4` JSONL line handed
  to 0.6.1 rehashes wrongly and reads as altered. Rendering is stricter in the other direction
  too, and visibly: `to_dict()`, `ctrlrun receipts --json` and `ctrlrun inspect` render each
  receipt under the schema it was written with, so a pre-v0.6 receipt shows its own `v1` or `v2`
  label and keys where 0.6.1 showed `v3`. A key added to a stored receipt, a relabelled `schema`,
  a removed one or an unknown one is `content_altered` at its `seq`, where a reader could have
  missed it before.
- **Migration `0005_precondition_fingerprint`, and a store no 0.6 process may still hold.** A
  database built by 0.6.1's own code migrates keeping every row, and 0.6.1 then refuses it at
  open, naming `0005`. **Stop every 0.6 process before any 0.7 process opens the store**: a store
  checks migrations only at open, so a 0.6.1 process already running would consume a fingerprinted
  approval with no comparison, *and* would rehash every `v4` receipt under `v3`'s keys and report
  a correct chain as altered. The trigger is the first receipt a 0.7 process writes, not the first
  caller that passes `preconditions=`.
- **A precondition, once one exists, is never skipped.** An approval that carries a fingerprint,
  presented by a call that names no provider, is refused rather than consumed: that includes the
  gateway and the ACS hook, which name none. A provider that raises, hangs or returns something
  with no canonical form refuses the action and reserves nothing. There is no
  `skip_preconditions` and no timeout parameter.
- **The attempt ceiling is stricter only where an operator asks for it**, and then it is
  absolute: above the ceiling the executor is not called, on any route, and `ActionDenied` names
  `attempt_ceiling`.
- **`ctrlrun verify` opens loopback sockets it bound itself.** `v0.4 §3.7`'s "no scenario opens a
  socket" becomes *no connection except to the store `--store-url` names and to loopback
  listeners verify bound itself*, because G12 needs a peer that can receive a byte. The old
  sentence was already untrue under `--store-url postgresql://remote-host/…`. The test suite's
  network guard admits exactly that and no more: IPv4 to the `127.0.0.1` literal, at a port this
  process bound through a stream socket that is still open. `localhost`, `::1`, `0.0.0.0`, every
  `AF_UNIX` path and every datagram send are refused.

### What this release does not close

Stated here, and not only in the specification, because each one is a limit somebody operating
this will meet.

- **A precondition fingerprint narrows the window between a human's approval and the action's
  execution, and does not close it.** The recheck is a network call, so it runs outside the atomic
  reservation write, and a change that lands after the comparison and before the reservation is
  not refused. It takes the exposure from minutes of human deliberation down to milliseconds,
  which is worth having and is not prevention. T261b opens that residual window and asserts
  exactly that.
- **On the reconcile route a doomed attempt still costs a human answer and three provider
  calls.** Under `max_attempts: 1` on an `approve` action whose retry carries a `reconcile` hook,
  the approval gate runs before the ceiling's check: a new approval request can be created, a
  human can grant it, and the reservation that consumes it is then refused with
  `attempt_ceiling`. One wasted answer, never an execution. On that same route the precondition
  provider is called **three times**, once on the request pass and twice on the retry, and a
  provider that is down there makes the refusal `ApprovalMismatch(reason="precondition_unavailable")`
  rather than `attempt_ceiling`, writes no effect record and never runs the `reconcile` hook, so
  the operator is told the wrong reason for an attempt that could never have run. The ordinary
  sequential route calls the provider zero times and refuses before any human is asked. Closing
  either needs a seam that would make the ceiling's check unreachable from any public route, and
  a guarantee that could not have failed is not a pass.
- **`max_attempts` bounds attempts, not executor invocations.** A `Suspended` executor holds its
  reservation and every `Control.resume` runs on that same attempt, so an elicitation loop is one
  dispatch however many rounds it takes. The gateway bounds those with `max_elicitation_rounds`;
  a direct `Control.resume` caller has no bound, and this release adds none.
- **A refused attempt number is spent.** Raising `max_attempts` from 2 to 4 after a refusal buys
  one further dispatch, not two.
- **The classifier's register sees only this library's own sends.** A claim is about the executor
  run, not about one connection, and `Control` marks a register on every send through
  `ctrlrun.transport` or `ctrlrun.gateway.transport.request`. An executor that sends part of the
  effect through `requests`, through httpx directly, or on a raw socket, and then uses the
  classifier, can be handed a `NotExecuted` that is true of these connections and false of the
  effect. So can one that raises a claim while a sibling thread's request is still in flight. The
  claim holds where every request of the effect goes through the classifier on the executor's
  context, and that sentence is in the module docstring, the class docstring and §2.3.
- **A reused `action_id` still leaves the attempt a late write is about undecided**, on every
  backend. The attempt number is now monotonic, so no two reservations of one key carry the same
  number; what is not closed is attempt *identity*. A transition names its holder by `action_id`
  alone, so attempt 1's write, arriving after its lease lapsed and after the same `Action` was
  retried, can read attempt 2's record, find its own id in a state it expects, and land there. A
  late `fail_effect` is the sharp case: it writes `FAILED` over an executing attempt 2, and
  `FAILED` is the state that permits a renewal. A `mark_ambiguous` reaches the same place in one
  more step, through a `reconcile` hook that is asked about the effect key and not about the
  attempt, so it needs no person. `ctrlrun resolve` carries no attempt number either, so someone
  who inspected attempt 1 can resolve attempt 2's ambiguity. The fix inside the frozen
  `StateStore` protocol is a store-side memo of the attempt each reservation wrote, which is a
  schema change of its own; §12.3a states the argument so the next milestone inherits it.
- **An approval granted and presented inside `request()` is spent before `Control` knows the
  request exists** (§6.4). What would take that away is a store call recording a request and its
  fingerprint in one write, and `StateStore` is frozen.
- **A malformed *value* of a key a receipt schema declares still raises out of `from_dict`.** One
  `UPDATE` putting a float among a receipt's `controls` blinds `ctrlrun receipts`,
  `receipts --verify-chain`, `inspect`, `stats` and G11 together, where the schema-level and
  added-key cases are each reported at their `seq` and leave every other row readable. 0.6.1
  behaves the same and v0.7 neither introduces nor widens it. Fixing it needs a new name in
  `CHAIN_BREAKS`, a closed set on a `v0.6 §6.5` surface, or a reader that walks raw rows; it is a
  named item on the roadmap before v1.0.

### Added

- **`ctrlrun.transport`, the `NotExecuted` classifier, in core** (SPEC-v0.7 §2, build-list item
  2). `v0.1 §5.5` leaves the one decision the product exists to get right, `FAILED` or
  `AMBIGUOUS`, to the executor, and until now the correct rule was reachable only through
  `ctrlrun[gateway]`. `ctrlrun.transport.urlopen`, `HTTPConnection` and `HTTPSConnection` are
  `urllib` and `http.client` with a counter: they raise `NotExecuted`, chained from the original
  exception, **only** where the connection they opened fresh failed before a single request byte
  was handed to its socket (DNS failure, refusal, connect timeout, a TLS handshake failure). Every
  other failure is the original exception, which the kernel records `AMBIGUOUS`: a reset or a
  timeout after the request was offered, a `sendall` that raised part way, a reused connection, a
  socket the caller set, an opener the classifier did not build, a proxy that refused a tunnel
  after its `CONNECT` line was sent. The count is taken from evidence, never from an exception's
  type, and above TLS. No redirect is followed, no HTTP status is ever `NotExecuted`, and no
  parameter, attribute or environment variable changes a classification. The module is stdlib
  only and is not imported by `import ctrlrun`.

  The rule itself, `ctrlrun.transport.effect_state`, is the one implementation: the gateway's
  `Transport` is now the core one, and `gateway/outcome.py` asks the core rule rather than keeping
  a copy. `ctrlrun.gateway.transport.request` offers the gateway's httpx mapping to an executor
  that uses httpx, on a client built for the one call. The gateway's own `NotExecuted`, for an
  upstream it never reached, is now chained from the httpx exception and its receipt names it.
  `ctrlrun verify` gains **G12**, "a byte written is ambiguous", under
  `ctrlrun.guarantees/v3`, with the refused connection as its positive control. G12 needs a
  loopback peer, so verify's rule becomes *no connection except to the store `--store-url` names
  and to loopback listeners verify bound itself*, and the test suite's network guard admits
  exactly that: IPv4 on the `127.0.0.1` literal, to a port the process bound through a stream
  socket that is still open, and nothing else.

  **The claim is about the executor run, not about one connection.** An independent review showed
  that every false `NotExecuted` it could produce came from two connections in one effect: the
  first delivered the request, the second was refused, and a per-connection classifier judged the
  second alone. `xmlrpc.client`'s retry, `FancyURLopener` following a `303`, an opener whose
  handler runs on a worker thread, and an executor's own retry-once-on-reset loop all make that
  pair. `Control` now opens a register around each executor call; every send through
  `ctrlrun.transport` or `ctrlrun.gateway.transport.request` marks it before the first byte, and a
  claim needs it unmarked as well as the connection's own evidence. Outside an executor run
  nothing is claimed. **The limit is stated in the module, the class and the specification**: the
  register sees only this library's own sends, so an executor that sends part of the effect
  through another transport and then uses the classifier can be handed a claim that is true of
  these connections and false of the effect. A send through this library on a thread that did not
  copy the executor's context **is** seen: it belongs to no register, so it marks every register
  open in the process, which costs claims in unrelated concurrent runs and never safety.

  **A continuation leg never records `FAILED`, and 0.6.1 did.** A continuation exists only
  because the remote answered and is holding the exchange, so nothing on that leg can say the
  remote did nothing. `Control.resume` now runs with the register already marked, and the gateway
  refuses to record `FAILED` for anything a continuation meets: a refused connection, a
  pre-dispatch JSON-RPC code, the `401` rule of `v0.2 §6.8`, and a tool error under an
  operator's `not_executed_on_error: true`, which asserts that *that tool* reports errors before
  acting and cannot speak for a call it did not answer. At 0.6.1 each of those recorded
  `FAILED` and, for a connection never established, answered the client `-41011` "not executed",
  which permitted a retry of an effect the upstream may have been part-way through. The upstream's
  own response is still relayed unchanged; what changes is the record, which is now `AMBIGUOUS`
  and needs `ctrlrun resolve`.

  **Behind a proxy the gateway is stricter than 0.6.1.** httpx reports an unreachable proxy and a
  TLS failure with the target after the proxy answered the `CONNECT` line with the same
  `ConnectError`, and `ctrlrun.transport` counts a written `CONNECT` line as a byte. Where the
  environment names a proxy, `ConnectError` and `ProxyError` are now an unknown outcome: an
  intercepted call that would have been recorded `FAILED` with `-41011` is recorded `AMBIGUOUS`
  with `-41010`, and needs `ctrlrun resolve`. With no proxy configured nothing changes.

- **Clock-skew detection** (SPEC-v0.7 §3, item 1). `PostgresStateStore` measures its server's
  clock against the application's at open, and again when an expired lease is declared
  `AMBIGUOUS` (at most once per `DEFAULT_LEASE`), in one round trip whose half is the
  measurement's bound, so latency alone is never reported as skew. It keeps its latest
  measurement as the optional, read-only `clock_skew` attribute, a `ctrlrun.state.ClockSkew`;
  `Control` reads it at the start of every `execute` and `resume` and after an `AmbiguousEffect`,
  and appends one new event type, `CLOCK_SKEW_DETECTED`, for a measurement past
  `clock_skew_threshold` (default one second, at most `DEFAULT_LEASE`, and no value switches it
  off). **It observes and reports, and changes no decision**: every lease is still evaluated
  against the application clock exactly as at 0.6.1, no reservation outcome changes, and a
  measurement that fails is logged and changes nothing. Verify gains G13, graded against a
  Postgres `--store-url` and `N/A` on SQLite, and the catalogue moves to
  `ctrlrun.guarantees/v3`; the store conformance suite gains a `clock` case, `not_applicable`
  on SQLite and the in-memory store because neither has a clock of its own.
- **The attempt ceiling, `max_attempts`** (SPEC-v0.7 §5, item 4, and the amendment to
  `docs/SPEC-v0.1.md` §5.4). A new action-entry policy key, an integer of at least 1, bounding
  the **attempts** that may execute on one effect key, the first included: `max_attempts: 3` is
  the first attempt and two renewals. It needs `schema: ctrlrun.policy/v5`, a new schema version
  that is a superset of `v4` as `v4` is of `v3`; `0`, a negative, a `bool`, a float, a string and
  a mapping are each a `PolicyError` at load, naming the key, the action and the line. The ceiling
  is inside the policy hash, so a receipt records which one refused an attempt.
  **An attempt, not an executor invocation**: a `Suspended` executor holds its reservation and
  every `Control.resume` runs on that same attempt, so an elicitation loop is one dispatch however
  many rounds it takes. The gateway bounds those with `max_elicitation_rounds`; a direct
  `Control.resume` caller has no bound, and this adds none.
  **The decision is taken on the attempt number the store assigned**, after the reservation and
  before the executor, because two callers that both read attempt *N-1* would both pass a read
  taken before reserving. Above the ceiling the executor is not called, the record is released as
  `FAILED` with an error naming the ceiling, `EFFECT_RESERVATION_REFUSED` carries
  `reason: "attempt_ceiling"` with the attempt and the ceiling, a `blocked` receipt is written,
  and `ActionDenied(reason="attempt_ceiling")` is raised. The refused attempt number is **spent**:
  raising `max_attempts` from 2 to 4 after a refusal buys one further dispatch, not two. A read of
  the record before the approval gate refuses the ordinary sequential case earlier, writing
  nothing, spending no presented approval and creating no approval request; it refuses only a
  `FAILED` record and is never the guarantee. **On any other route the approval gate comes
  first**, so on an `APPROVE` action a human can be asked, and answer, for an attempt that is then
  refused: a wasted answer, never an execution, and `docs/SPEC-v0.7.md` §5.2 and §5.5 say so
  rather than closing it. In observe mode the refusal is recorded as `would_have.blocked_reason:
  "attempt_ceiling"` and the action runs. Verify gains **G15**, and G5 and G14 now select only an
  action whose ceiling permits a renewal, reporting `N/A` where the ceiling is the only reason
  they cannot, because each one's control *is* a renewal and `max_attempts: 1` would otherwise
  report a correct kernel as a failure. No new error type, no new event type, no new `StateStore`
  method, no new `Control` method, and no CLI change.
- **The provider idempotency token** (SPEC-v0.7 §4, item 3). `ctrlrun.idempotency_token()`, a new
  zero-argument accessor re-exported at package import, answers inside an executor with the token
  of the attempt it is running: `ctrlrun.effect.idempotency_token_for(effect_key, attempt)`, a
  SHA-256 over the canonical form of `(effect_key, attempt)` under the domain tag
  `ctrlrun.idempotency/v1`, rendered as a 36-character UUID of version 8. Send it to a provider as
  its idempotency key. **Derived from the attempt and not from the effect key alone**: the effect
  key is stable across `SPEC-v0.1.md` §5.4's renewal, so a provider given it would answer the one
  retry the kernel permits, permitted *because the executor proved nothing happened*, with the
  cached failure of the attempt that failed. It is stable within one attempt, including across a
  `Control.resume` of a suspended one, and different after a renewal. **What it is for is
  reconciliation**: a deterministic handle to ask a provider what became of an attempt whose
  outcome is unknown, by a key the provider already indexes. It does not make a retry safe, and
  after an `AMBIGUOUS` outcome the kernel still refuses one. Nothing is stored: the token is a pure
  function of two fields every receipt of an attempt that ran already carries, so a receipt
  re-derives it and a `reconcile` hook reads the attempt off the record. The executor signature is
  unchanged, and an executor that never calls the accessor runs exactly as it did at 0.6.1. Outside
  an executor, for an action with no effect key, for an observe-mode attempt whose reservation was
  refused, and on a thread started without a copy of the executor's context, it raises
  `InvalidArgument`. Verify gains G14, with a note beneath the table: a token is unique only as far
  as the operator's effect keys are, and a kernel that sees one store cannot check that two stores
  sharing a provider account never produce one effect-key string for two different effects.
- **Precondition fingerprints** (`docs/SPEC-v0.7.md` §6, §7). `@protect(..., preconditions=provider)`
  and `Control.execute(..., preconditions=provider)`, where the provider takes the `Action` and
  returns a mapping of the state an approval depends on. A precondition fingerprint **narrows**
  the window between a human's approval and the action's execution; it does not close it.
  Under `APPROVE` the provider is called when the approval is requested, and the result is kept only
  as a `sha256:` fingerprint on the request (`ApprovalRequest.precondition_fingerprint`, stored in the
  new `approvals.precondition_fingerprint` column). On the presenting pass it is called again,
  strictly before the store call that consumes the approval, and the action is refused with
  `ApprovalMismatch` and a reason of its own: `precondition_changed` where the two fingerprints
  differ, `precondition_missing` where only one side has one (a store that lost the column, or the
  gateway and the ACS hook, which name no provider), and `precondition_unavailable` where the
  provider raises or returns something that is not a canonicalizable mapping. Every refusal reserves
  nothing and leaves the approval granted. On the request pass a provider that fails refuses the
  action with `ActionDenied(reason="precondition_unavailable")` before any human is asked, and a
  fingerprint that is computed and then **not recorded** (a store without the column, a third-party
  `ApprovalProvider` building its own request) refuses with
  `ActionDenied(reason="precondition_missing")` and **withdraws the request it left behind** where
  this call can reach it: denied while it is pending, spent where a grant landed inside the window,
  and `not_withdrawn:<status>` in the evidence where neither write was possible (the provider raised
  after recording, or the store refused). §6.4 states that bound and its residual. **A withdrawal is
  a `deny_approval`**, so `find_denied_request` returns it and the gateway's "no is an answer"
  pre-check refuses every call for that action hash until the request expires, as though a human had
  said no: fail-closed, bounded by the TTL, and traceable through the approver
  `ctrlrun:precondition-not-recorded`. `ALLOW`, `DENY` and `Control.resume` never call the provider;
  observe mode compares, records and runs.
  The comparison is a network call, so it runs outside the atomic reservation write, and a change
  that lands after the comparison and before the reservation is not refused: T261b opens that
  window and asserts exactly that. Raw provider output reaches no receipt, event, log line or
  table, and a provider's exception is recorded by its type name only. No `skip_preconditions`,
  and no timeout parameter: a provider that hangs holds the call and reserves nothing.
- **Migration `0005_precondition_fingerprint`** adds `approvals.precondition_fingerprint`, `NULL` on
  every existing row, on SQLite and Postgres. A database built by 0.6.1's own code migrates keeping
  every row, and 0.6.1 refuses the migrated database at open naming `0005`. **Stop every 0.6 process
  before any 0.7 process opens the store**: a store checks migrations only at open, so a 0.6.1
  process already running would consume a fingerprinted approval with no comparison, *and* would
  rehash every `v4` receipt under `v3`'s keys and report a correct chain as altered. The trigger is
  the first receipt a 0.7 process writes, not the first caller that passes `preconditions=`, and
  nothing in the new process can see the old one.
- **G16 in `ctrlrun verify`**, "a moved fingerprint is refused" before the reservation, under
  `ctrlrun.guarantees/v3`. Verify supplies its own provider, because a provider is named in code
  that verify does not read, and the report says so beneath the table; `not applicable` only where
  no action requires approval. The store conformance suite gains a `precondition-fingerprint` case
  and a broken-store fixture that fails it by name.
- **Python 3.13 and 3.14 are tested and declared.** CI's `check` job runs the full suite on
  3.11, 3.12, 3.13 and 3.14, and the package classifiers name all four. The floor is unchanged:
  `requires-python` stays `>=3.11`, and mypy and ruff still check against 3.11. No library code
  changed; the one test fix is below.

### Changed

- **An action entry may declare `max_attempts`, and a renewal over `FAILED` can now be bounded.**
  This is stricter than 0.6.1 only where an operator asks for it: an action that declares no
  `max_attempts` renews without bound, exactly as before, and every document that loaded at 0.6.1
  loads unchanged. There is no default ceiling, and no value of the key means "unlimited".

- **`ctrlrun.receipt/v4`**, with `precondition_at_request` and `precondition_at_recheck`, and the
  first receipt-schema bump that does not report older receipts as altered. A receipt read from a
  store is now hashed as the document it was read from (`docs/SPEC-v0.7.md` §6.11, amending
  `SPEC-v0.6.md` §6.4's last bullet), so every `v3` receipt a released 0.6 wrote still rehashes to its
  stored hash and a chain spanning `v3` and `v4` verifies end to end. A key added to a stored
  receipt, a relabelled `schema`, a removed one or an unknown one is `content_altered` at its `seq`,
  and no longer something a reader could miss. **Visible**: `to_dict()`, `ctrlrun receipts --json`
  and `ctrlrun inspect` render each receipt under its own schema, so a pre-v0.6 receipt shows its
  own `v1` or `v2` label and keys where 0.6.1 showed `v3`. Upgrade every reader before any writer:
  a `v4` JSONL line handed to 0.6.1 rehashes wrongly.
- **`ctrlrun verify` prints each distinct note once**, where it printed only the first note in the
  report, which would have dropped G16's beneath G3's. CI's `verify` job expects `verified 13/13`
  with two not applicable, and `verified 7/7` with eight, measured from a run of the merged
  catalogue rather than carried over from either branch.
- **A receipt chain reader no longer stops at a row it cannot hash.** A stored document holding a
  value with no canonical form (a float, a lone surrogate) made `verify_chain` raise, so one
  tampered row ended the walk: `ctrlrun receipts --verify-chain` exited with no report and a forged
  field at another `seq` went unnamed. Such a row is `content_altered` at its `seq`, named by the
  refusal's type and never its message.
- **`APPROVAL_CONSUMED` carries what the presenting pass compared**, where a precondition was
  compared, so a suspended action's resumed leg, whose receipt is the only one it gets, records the
  comparison its first leg made.

### Fixed

- **`PostgresStateStore.events()` read a missing `action_id` back as the string `"None"`.** An
  event about no action (the three `DELEGATION_*` types, and now `CLOCK_SKEW_DETECTED` reported at
  open) named a proposal called "None" on Postgres alone; SQLite and the in-memory store returned
  `None`. It now returns `None` on all three.

- **The migration tests' release fixtures could not build a venv on some interpreters.**
  `venv.create` copies the interpreter by default, and a copied binary from a shared-libpython
  build (uv's CPython 3.14 on macOS) aborted inside `ensurepip`, so all five release fixtures
  errored before a release was installed. The fixture now symlinks, as `python -m venv` does on
  POSIX.
- **The Postgres store could hand out one attempt number twice** (`docs/SPEC-v0.7.md` §5.6).
  0.6.1's renewal after `FAILED` read the record with a plain `SELECT` and then updated it on
  `effect_key` and `state = 'failed'` alone. So a renewal planned against attempt *k* could land
  after another process had renewed to *k+1*, run and failed, and write *k+1* a second time: two
  dispatches, and two receipts, under one attempt number. The `UPDATE` is now also conditioned on
  the attempt it was planned from, with the row count checked, and a stale renewal is refused with
  `DuplicateEffect`. SQLite carries the same condition, where it was already unreachable because
  `BEGIN IMMEDIATE` holds the read and the write together.
- **A stale Postgres write could put an older attempt number back** (`docs/SPEC-v0.7.md` §5.6).
  0.6.1's compare-and-set under `resolve_effect`, `extend_lease`, `hold_continuation`, the kept
  `AMBIGUOUS` write and the four transitions matched `effect_key`, `action_id` and `state`, and
  wrote back the attempt number it had read. A caller that retries one `Action` reuses its
  `action_id`, so the same `action_id` and state could come round again at a newer attempt between
  the read and the write. A human's `ctrlrun resolve` decided on attempt 1 then wrote `FAILED` at
  1 over attempt 2's unknown outcome, and the next renewal handed out attempt 2 a second time.
  **On Postgres**, every write to an effect record is now also conditioned on the attempt it read,
  and a write whose record moved is re-read rather than landed (SQLite's writes are already inside
  the `BEGIN IMMEDIATE` that holds their read, and are unchanged). The fix for the renewal above
  depends on this one: it holds only because the number can no longer move backwards. What this
  closes is the race between a write's own read and its write. The window a *human* stands in is
  longer, because `ctrlrun resolve` carries no attempt number: someone who inspected attempt 1 can
  still resolve attempt 2's ambiguity. An attempt argument on the CLI would close that, and it is
  not in this change.
- **A Postgres write that carried an outcome could be refused and drop it.** With the condition
  above in place, a `commit_effect` or `mark_ambiguous` whose record moved under it wrote nothing
  and raised, so the effect record, which is what gates the next renewal, said nothing about an
  attempt that may have acted: a review measured a renewal to a third attempt with a committed
  refund recorded on no record at all. Both are now re-issued once against the re-read, so the
  outcome lands on the record as it stands. `begin_execution` and `fail_effect` are still refused
  there, because `FAILED` asserts that nothing happened and the newer attempt may be running.
- **A refused write on a moved record claimed the wrong thing.** All of these were
  `DuplicateEffect(state="in_progress")`, which means *another attempt holds a live reservation*,
  and after a stale `resolve_effect` the record is `AMBIGUOUS` at a newer attempt, which is nobody's
  reservation. The refusal now takes its type from what the re-read found, `AmbiguousEffect` or
  `DuplicateEffect` with `committed` or `in_progress`, and its message names the move.
- **An unknown outcome could vanish when the store refused to record it**, on both backends and
  since before 0.6. `Control` caught two store refusals around its outcome writes, and a record a
  human resolved `FAILED` while the attempt was still running answers a third: an executor that
  raised `TimeoutError` then produced no receipt and no `EXECUTION_AMBIGUOUS` event, and the caller
  was handed a store error about its own effect key instead of its executor's exception. `Control`
  now catches every `CTRLRunError` from an outcome write, writes the receipt and the event whatever
  the store answered, names the refusal in both, and re-raises the caller's own exception. Nothing
  is reconciled on a record the attempt could not mark.
- **Two bounds on the Postgres store's re-issues reset each other.** The stale re-issue and the
  lost-commit re-issue of `docs/SPEC-v0.6.md` §4.3.2 each carry a flag that permits one attempt,
  and neither passed the other's flag on, so a lost `COMMIT` inside a re-issue and a re-issue
  inside a lost `COMMIT` alternated without end: a review drove both halves at once and reached
  `RecursionError`, which is outside this library's closed set of errors, so no caller can
  classify it and the record is left stranded. Both flags now travel through both re-issues.
- **A receipt for an outcome the store refused did not say what the executor had done.** An
  executor that returned normally and one that raised `NotExecuted` produced identical receipts,
  naming the store's refusal and nothing else. For the first the remote very likely acted and for
  the second it very likely did not, and with the effect record carrying neither, that is the one
  fact whoever runs `ctrlrun resolve` has to go on. The receipt now carries both.
- **A lapsed Postgres approval could be marked expired over a consumption.** `_expire` wrote
  `status = expired` on `approval_id` alone, from a read that saw `granted` past `expires_at`, so a
  consumption committing in between was overwritten and an approval that authorised a real effect
  read `expired`. It is now conditioned on the status it read, as every other write on that table
  already was.
- **After a lost `COMMIT`, a Postgres reservation could return an attempt number it did not
  write.** Where a reservation's `COMMIT` was lost and the re-read found the write absent, 0.6.1
  re-issued it and then returned the reservation it had first planned, discarding the re-issue's.
  If another process had renewed, or inserted, and failed in between, the caller and its receipt
  held attempt *k+1* while the record held *k+2*, and *k+1* was a number another dispatch had
  already been handed. The reservation methods now return what the re-issue wrote.
- **A lost `COMMIT` on a Postgres renewal could take another process's reservation for its own.**
  0.6.1's re-read accepted any `RESERVED` record carrying the renewal's `action_id` as proof the
  commit had landed. `docs/SPEC-v0.6.md` §4.3.3 had already ruled that out on the insert path,
  because `action_id` is caller-supplyable, and a caller that rebuilt the same `Action` renews
  under the same one. Two processes then held one attempt. The renewal's re-read now applies the
  same whole-row identity check, and a row that is not its own write is refused.

### Documentation

- **`docs/SPEC-v0.7.md`**: the v0.7 "Execution boundary" contract, a delta over v0.1 to v0.6. No
  code lands with it. It asks one question: *does it hold at the edges the kernel does not
  control?* The kernel does not decide whether the remote acted, an executor does; it does not own
  the clock its leases are measured against once the store is on another host; and it does not know
  whether the world still looks the way it did when a human said yes. Five items answer those
  edges: the `NotExecuted` classifier promoted from the gateway into core as `ctrlrun.transport`,
  clock-skew detection, a provider idempotency token derived from `(effect_key, attempt)`, an
  operator-set ceiling on renewal after `FAILED` (`max_attempts`, written as an amendment to
  `SPEC-v0.1.md` §5.4), and precondition fingerprints, which **narrow** the window between a
  human's approval and the action's execution and do not close it. Tests come from §8
  (T209 to T271, because T182 to T208 already belong to `SPEC-mcp-operator.md` and `SPEC-scan.md`);
  public names are frozen in §9; guarantees G12 to G16 join `ctrlrun.guarantees/v3`.

  **Reading the code changed nine things the plan had assumed**, and §1.4 lists them. Four matter
  beyond this document. On Postgres a renewal could reuse an attempt number, and a lost `COMMIT`'s
  re-issue could return a number other than the one it wrote; v0.7 makes the number load-bearing,
  so both are fixed first, in a new **item 3a, "attempt numbers never repeat"**, its own pull
  request stacked before item 3 and independently reviewed because it changes a store. Bumping the
  receipt schema the ordinary way would have reported every receipt a released 0.6 wrote as
  altered, so a receipt now renders under the schema it was written with. In enforce mode one
  granted approval buys one dispatch, not unlimited ones, because every renewal of an approved
  action needs a new granted approval; "granted" is not always a human (a scripted provider, an
  automated `wait=True` loop and approvals granted ahead of a gateway all count), observe mode needs
  none, and an adapter can still put a human in front of an attempt the ceiling will refuse, which
  §5.5 records. The unbounded case the ceiling exists for is the action the policy allows outright.
  And `max_attempts` needs `ctrlrun.policy/v5`.

  **An independent review of the draft found seven blocking defects and nine smaller ones**, and
  every one became an edit: among them the two store defects above, a G15 that passed with the
  ceiling's own check deleted, a G5 that would have failed a correct kernel under `max_attempts: 1`,
  a receipt rule that let a fabricated field verify, three false `N/A` reasons, and a verify
  network rule that was already untrue under `--store-url`.

- **`README.md`** now says what `ctrlrun.transport` is for beside the sentence that names
  `NotExecuted`, says that `ctrlrun verify` binds loopback listeners of its own rather than
  claiming it opens no socket at all, and says that a precondition fingerprint narrows the window
  between a human's answer and the execution.

- **The documentation site**, `CTRLRun/ctrlrun-docs`, carries the generated pages for this
  release: every `ctrlrun.transport` name in the Python API reference, `ctrlrun.policy/v5` and
  `ctrlrun.receipt/v4` in the schema references, G12 to G16 in `verify.md` and in the readiness
  block, the classifier and the residual precondition window in `THREAT_MODEL.md`, and
  `CLAIMS.md` regenerated so every cited line number resolves. `ROADMAP.md` marks v0.7 shipped and
  carries two items named before v1.0: a malformed value of a schema-declared key blinding every
  receipt reader, and the `state -> receipt -> policy -> authority -> state` import cycle, which
  contradicts `ARCHITECTURE.md` §6.

## [0.6.1] - 2026-09-07 — The audit's fixes, and the gateway's transport

Everything found after `v0.6.0` was tagged: twenty-nine defects from an audit of the shipped
code, the gateway's transport behaviour, and the documentation's first screen. No public API
name changes and no schema change. Two behaviours become **stricter** and could refuse input
that 0.6.0 accepted silently — `@protect` on an `async def`, and a duplicated mapping key in a
policy or authority document — and both are listed below with what they did before.

### Fixed

- **`@protect` on an `async def` recorded a consequential action as done that never happened.**
  The wrapper is synchronous, so "the return value" was an un-awaited coroutine: the effect was
  committed and a `committed` receipt written before the body ran, and the legitimate retry was
  then refused with `DuplicateEffect` for ever. Async, generator and async-generator functions
  are refused at decoration time.
- **One human approval could authorise two effects on Postgres.** A lost `COMMIT` on a
  *renewal* — a reservation over a `FAILED` record, v0.1 §5.4's one automatic retry — re-issued
  the approval without consuming it, so `find_granted_approval` would hand the same "yes" out
  again for a different effect key. `v0.1 §4.2 A2` requires single use consumed atomically with
  the reservation. SQLite has no lost-commit resolution, so this was also a backend-switch
  regression.
- **`ctrlrun verify` reported false N/A reasons, which is a false green.** N/A is excluded from
  the denominator, so a run that could exercise one guarantee reported "1/1 declared guarantees
  pass" beside ten reasons that were each untrue of the operator's document. A miss on the
  authority axis is now distinguished from a miss on the policy axis and named. Verify also
  crashed, exit 1, on an `effect:` template containing `{resource}` — a code path `--help`
  documents as "a guarantee FAILED" — because it invented an argument for a placeholder that
  names the action's `resource` field. Every shipped example under `examples/` is verified in
  CI now.
- **Webhook approvals were never routed.** `WebhookApprovalProvider` advertises
  `POST /ctrlrun/approvals/<id>` as `respond_to` in every `APPROVAL_REQUESTED` notification, and
  `Gateway.handle_approval` had no caller: the approver's system posted its answer to that URL,
  read an HTML 404, and the approval sat pending until it expired.
- **A lone surrogate in an executor's exception message stranded the effect.**
  `mark_ambiguous` raised `UnicodeEncodeError` from inside the transaction — not a
  `CTRLRunError` — and left the record `EXECUTING`, which is neither outcome and blocks the
  retry until the lease expires. Executor text is escaped with `backslashreplace` where it
  enters, so the row, the event and the receipt carry the same value and the chain hashes.
- **The SQLite store leaked two file descriptors per thread.** Connections were pinned in a set
  only `close()` emptied, so a host whose threads come and go eventually failed every store
  access with "unable to open database file". Measured on the gateway: 200 connections, 410
  descriptors; now flat.
- **A duplicated mapping key in a policy or authority document failed open.** `yaml.safe_load`
  resolves one to the last silently, so a grant with `actions:` written twice during a narrowing
  edit loaded as `("**",)`. Refused now, naming the key and the line, by the one loader policy
  and authority share.
- **`ctrlrun delegate` and `ctrlrun revoke` ignored the store.** Both called `Control.from_file()`,
  which always opens `.ctrlrun/state.db` beside the policy, so on Postgres a delegation went
  into a local file no agent reads and `revoke` reported success while the delegation stayed
  live. Both take `--store-url` now.
- **Read commands created the store they were reporting on.** `ctrlrun receipts` in a directory
  without a `ctrlrun.yaml` created and migrated `.ctrlrun/state.db` and answered "no receipts
  yet" — telling an operator looking for evidence that there was none, from a store the command
  had just made. Five commands also reached the terminal as tracebacks where the identical input
  printed one clean line elsewhere.
- **The gateway dropped a client's connection with no reply**, which an agent reads as a
  transport error and retries blind: a 401 or 403 whose body is not a JSON object (an RFC 6750
  bearer challenge, or a CDN's HTML), a malformed `Content-Length` — where `-1` bypassed
  `--max-body-bytes` entirely — and `httpx.DecodingError` and `httpx.InvalidURL`, which inherit
  from `RequestError` and so matched neither `except`.
- **The gateway relayed `Content-Encoding: gzip` with the decompressed bytes.** httpx sends
  `Accept-Encoding: gzip` by default, so an upstream doing nothing but honouring content
  negotiation made the gateway unusable with `DecodingError: incorrect header check`.
- **`ctrlrun scan` failed the policy `ctrlrun init` had just written**, exit 1, on the two
  actions the starter's own comment says need no effect. The rule asked "does the policy permit
  this?" where it meant "does this have a consequence to reserve?".
- **`data_scope_eq: [[phi]]` raised `TypeError` on every evaluation** of that action rather than
  a `CTRLRunError`, so an application catching the kernel's errors did not catch it. Refused at
  load.
- **The gateway's startup block never reached a pipe.** Python block-buffers a non-tty stdout,
  so SPEC-v0.3 §8.4's block — which identity provider, which store, which environment — was
  still buffered when the process was signalled. Visible only at an interactive terminal, which
  is the one place nobody runs a server.
- **Both adapters pinned `ctrlrun>=0.5,<0.6` beside a 0.6.0 kernel**, so `pip install
  ctrlrun-langgraph` either refused to resolve or silently downgraded `ctrlrun`. The framework
  range was checked against the version CI installed; the kernel range was checked against
  nothing.
- **Two reference pages promised a `ctrlrun[conformance]` extra that does not exist** and a
  `MissingDependency` that could not be raised. SPEC-v0.5 §12.1 reversed that extra and
  `pyproject.toml` never declared it, so both halves of the sentence were false.
- Validate upstream JSON-RPC response IDs before changing effect state. Missing, mismatched,
  and malformed responses remain ambiguous and cannot make an executed action retryable.
- Forward MCP SSE progress incrementally, record the matching final response, and keep
  interrupted streams ambiguous. Client cancellation closes the upstream connection.
- Relay empty HTTP acknowledgements and MCP GET/DELETE requests, including session headers
  and standalone streams. Recognize successful responses from accepted legacy revisions.
- Preserve the original request ID in synthesized gateway errors and support IPv6 listeners.
- Restore consumed approval attribution and original attempt timing on resumed receipts,
  including across database reopenings and multiple suspension rounds.

### Changed

- The README's first integration example runs end to end. It stopped at `ApprovalRequired` and
  left the approval and the resumption in prose, so no reader could reach a completed protected
  action by copying it; it now covers the policy, the decorator, `ctrlrun approve` from the
  shell, `with_approval`, a mutated €5,000 refused and three receipts, in one domain throughout.
- The README states each guarantee once. It stated the same six of them six times — a table
  after the first example, the problem table, the pipeline steps, the generated matrix, the
  bullet list and the readiness block. Prose is down from 3,205 words to 2,662, with the demo
  transcript, the guarantee matrix, both receipt-chain disclaimers and the whole *It can't*
  section untouched.
- The documentation home page leads with what CTRLRun is rather than with its own name, and
  says the promise once instead of twice above the fold. Its `title` is the category line and
  its `description` the tagline, which is what `docs/IA.md` assigns to each; the browser tab no
  longer reads *CTRLRun - CTRLRun*.
- `try-it` puts its controls above its explanation, in a wide column, with the policy below
  them rather than between the reader and the button.
- Four badges: CodeQL, the documentation site, Ruff and `mypy --strict`. Downloads and stars
  are deliberately absent — `docs/STYLE.md` forbids social proof that does not exist, and a
  count published four days after the first release measures mirrors.

## [0.6.0] - 2026-09-07 — Durable runtime

**The soak criterion was amended on 2026-09-07, and it was amended downwards.** It read *a soak
of at least one week with no unexplained `AMBIGUOUS`*; the week was removed rather than waited
out, and the criterion is now a published run with no unattributed `AMBIGUOUS` and a positive
control that fired — the two things a harness is allowed to decide about itself. `SPEC-v0.6.md`
§8.1 carries the reasoning, what it costs and what did not change; `docs/docs/ROADMAP.md` records it
in the milestone's own reconciliation. The short version: elapsed hours were a proxy for a
question the injection ledger already answers, and what a week would actually have bought —
whether anything **accumulates** over days — is unestablished by anything in this repository and
is now claimed by nothing rather than owed by a gate.

The published run is twenty minutes, 889,735 actions, 133,393 ambiguous outcomes all attributed,
0 unattributed, positive control fired. **The duration is printed on every surface that quotes
the run** so a reader can discount it: the README's readiness block, the docs home, the
production index, and `docs/docs/production/soak.mdx`, which now recomputes the criterion from the
published counts instead of reading `exit_criterion_met` out of the same file.

v0.5 asked *can somebody else implement this?* v0.6 asks: **does it still hold when the process
dies, the host goes away, and the database is somewhere else?**

Every guarantee shipped so far was a guarantee about one process holding one SQLite file.
`BEGIN IMMEDIATE` is a whole-database write lock on a local file; take the file away, put the
store on another host, and E1 — *at most one caller per effect key* — has to be re-earned with a
different mechanism. That is the milestone.

**The suite was written before the backend it grades.** `ctrlrun.conformance.store` runs this
repository's own acceptance tests — the cases of `v0.1 §7`, `v0.2 §10` and `v0.3 §10` that are
statements about `StateStore` rather than about `Control` — against any backend, and it landed
two items before Postgres existed. A backend measured against a suite written for it has marked
its own homework. The ordering paid for itself on the first three runs, which found three real
bugs in the Postgres store before a single test in its own file existed.

**And the distinction that made it tractable: the store is reconcilable by re-reading; the remote
is not.** A Postgres transaction is atomic, so an ambiguous *store write* has exactly one truth
and the store can go and look at it. An ambiguous *remote effect* has no such move, which is why
`AMBIGUOUS` is terminal there. Two ambiguities, one word, different remedies — and an
implementation that collapsed them would look correct while either refusing work one query could
have recovered or retrying work nothing can.

### Added

- **`ctrlrun.conformance.store`** — the store conformance suite (v0.6 item 1). This repository's
  own acceptance tests, runnable against any `StateStore`, with fourteen deliberately-broken
  stores proving the suite can fail. It found that **`events()` and `receipts()` were never
  declared on the `StateStore` protocol** while both shipped stores implement them and four
  callers depend on them; both are now declared.
- **Schema version and forward-only migrations** (v0.6 item 2). A `schema_version` table records
  applied migration ids — recorded, never inferred — and a store refuses a database it does not
  recognise in **both** directions. Six places across v0.2–v0.5 said *"there is still no
  migration story — that is v0.6."*
- **`SchemaMismatch`**, exported from `ctrlrun`. Raised at open when a store meets a database it
  does not recognise. Its own type because *"your database is from the future"* and *"your lease
  is negative"* have entirely different remedies.
- **`PostgresStateStore`** — `ctrlrun[postgres]`, lazily imported (v0.6 item 3). The frozen
  `v0.1 §5.3` protocol, extended by **nothing**, with `UNIQUE(effect_key)` plus
  `INSERT … ON CONFLICT DO NOTHING` under `READ COMMITTED` where SQLite had `BEGIN IMMEDIATE`.
  The guarantee is the unique index and not the isolation level, which the store does not set.
  Every later transition is a compare-and-set with **the row count checked**. It passes item 1's
  suite 23/23, with no N/A. `import ctrlrun` imports no `psycopg` module.

  The decisions did not move: `plan_reservation`, `plan_lease_extension`, `check_consumable` and
  `check_answerable` stay pure functions, and all three backends decide with them and then only
  write — so `v0.1 §5.4`'s retry table has one implementation rather than three, and a backend
  cannot drift into permitting something SQLite refuses.
- **`--store-url` accepts a `postgresql://` URL**, and is now on every command that reads or
  resolves the operator's own store — `receipts`, `effects`, `inspect`, `resolve`, `approve`,
  `deny` — reading `CTRLRUN_STORE_URL`. CTRLRun's own `?ctrlrun_schema=` parameter selects the
  schema and is peeled off before the URL reaches the driver.

  **It creates nothing and migrates nothing.** A review found the first version doing both: a
  `ctrlrun effects` against an empty schema printed "no effects yet", exited 0 and left eight
  tables behind, and against a database one migration short, a `ctrlrun receipts` applied it.
  On the milestone that first shares a store across hosts, that is one reader altering a table
  every other process is still running against. A schema that is missing, behind or ahead is now
  refused with an instruction.
- **`ctrlrun receipts --control ID`** — shows only the receipts citing that control. A **filter
  and not a lookup**: it does not consult the policy, so an id no document defines matches
  nothing rather than erroring, which is the right answer for a reader running against a store
  whose policy has since changed. A dangling *citation* is still a load error, in the place that
  can see the registry.
- **`ctrlrun receipts --verify-chain`** — reads the chain in the operator's own store and reports
  every break by `seq` and by name: `content_altered`, `hash_missing`, `link_broken`, `missing`,
  `head_mismatch`, `unchained`. Six names rather than one boolean, because *"receipt 41 was
  edited"* and *"the last nine were deleted"* are different incidents.
- **Receipts carry `seq`, `prev_hash` and `hash`** (v0.6 item 6). One chain per store — not one
  per effect key, which would not detect the deletion of every receipt for one key, and not one
  per process, which is not a chain. `seq` is **inside** the hashed content, so two adjacent
  receipts swapped with their `seq` values change both documents; `hash` is a column, because a
  document cannot contain its own hash. `put_receipt` takes the head row's lock **first** and
  advances it in the same transaction.
- **`policy_hash`, `policy_version` and `controls` on every receipt** (v0.6 item 7). A receipt
  from six months ago says what the rules were, not what they are now. `policy_hash` is over the
  **parsed decision inputs** — schema, actions and rules in document order, `mode`, `environment`,
  the authority grants — and not the file's bytes, so a comment or a reordering of keys does not
  change it. `version:` is a free string the operator chooses, recorded and **never
  authoritative**: two documents sharing a `version:` and differing in content are two different
  policies, and the hash is what says so.
- **`ctrlrun.policy.PolicyControl`** — a registry entry: an id, a title, and an optional
  `source`. Named `PolicyControl` and not `Control`, because a second `Control` in a package
  whose central object is `Control` is a collision every call site would have to disambiguate,
  and one this milestone would have frozen for a long time.
- **`ctrlrun.policy/v4`**, with three new top-level keys and a closed key set, so a typo is still
  a load error:
  - **`controls:`** — a registry of ids, each with a `title` and an optional `source`. An action
    cites some, a rule may narrow or add, and the receipt carries the union of the action's and
    the **matched rule's** in registry order. **CTRLRun does not interpret a control**: `source:`
    is a string the operator wrote and the registry records and never enforces. It maps to no
    standard, and citing one is not a claim about it.
  - **`data:`** — an action declares which of its arguments carry which class of data.
    `data_scope` is the set of labels present in **the arguments actually supplied**, not the
    whole declared map: an action that carries no PHI is not a PHI action because some other call
    of it would be. `data_scope_in: [phi]` reuses the membership `_in` already expresses and adds
    no operator, deliberately — `_OPERATORS` is shared with authority `constraints:`, so an
    operator added here would become available to grants.
  - **`version:`** — see above.
- **`resolved_by` on every effect record** (v0.6 item 5). Out of `AMBIGUOUS` there are exactly
  two authorities — a human and a reconcile hook — and the record now says which one acted.
  `ctrlrun effects` prints it, and prints `executing (lease expired)` for a lease that lapsed and
  was never contended, because nothing sweeps and the state alone hid it.
- **`research/soak/`** (v0.6 item 8) — a soak harness, outside `src/` and packaged nowhere, on
  `research/framework-probe/`'s precedent. It defines *unexplained* **before** the run starts —
  an `AMBIGUOUS` caused by an injected failure is explained, one with no corresponding injection
  is not — records every injection **before** causing it, and carries a positive control that
  runs in its own store: a deliberately unrecorded ambiguity that the table must report. A soak
  with no unexplained `AMBIGUOUS` is a result; a soak whose harness could not have detected one
  is not.
- **`docs/docs/postgres.md`** — the operator's page: connection strings, what to grant, what happens
  on failover, the one row every receipt write serializes on, and what the store does **not** do
  for you.

### Changed

- **`ctrlrun init` writes a `ctrlrun.policy/v2` starter, with an `effect:` on the refund and on
  the namespace delete.** The starter was a v1 document headed "(v0.1)" with a comment about a
  feature that "arrives later", six releases on; the first file a new user reads should not be
  the oldest one in the repository. The actions and decisions are unchanged, so a policy written
  from the old starter evaluates the same way, and `ctrlrun verify` against the new one exercises
  the effect guarantees instead of reporting them not applicable.

- **Observe mode no longer spends a presented approval, and that is a change to shipped v0.3
  behaviour.** `_observe_secure` routed a presented approval through the same consuming path
  enforce mode uses, so an operator evaluating a policy in observe mode was silently burning
  their humans' single-use answers on actions observe mode was never going to gate. It now
  **checks** the grant — with the same pure predicate every store applies, so the refusals it
  records are the ones enforce mode would have raised — and writes nothing.

  The **reservation is still taken**, and the asymmetry is deliberate: in observe mode the action
  genuinely executes, so the effect record has to exist or the duplicate refusal has nothing to
  refuse with. Observe mode suppresses CTRLRun's *decisions*; it does not suppress the record of
  an effect that really happened. The `APPROVAL_CONSUMED` event on that path is **gone rather
  than renamed** — v0.6 adds no event type, and an event naming a write that did not happen is
  worse than no event.
- **A denial now names the approval that was presented**, on the `ACTION_DENIED` event and on the
  receipt. Where a policy denies an action a human had already approved, the approval stays
  `granted` and unspent — that is deliberate, and `SPEC-v0.6.md` §7.2.1 argues it — but nothing
  previously connected the live grant to the refusal it met.
- **`data_scope` is now refused as an argument name**, at every place an argument is named: a
  `data:` key, an `effect:` or `resource:` template placeholder, and a `@protect`-ed function's
  parameter. §7.4 said it always was; no such check existed. A document or a decorated function
  using that name stops loading, with the reason.
- **`data_scope_eq:` and `data_scope_neq:` compare the set and not its order.** The derived value
  is sorted, and list equality is order-sensitive, so `data_scope_eq: [phi, internal]` silently
  never matched while `[internal, phi]` did — an operator writing the labels in their own
  declaration order got a rule that never fired, and where that rule was the `deny` or the
  `approve`, that is fail-open. Narrowed to derived subjects: an ordinary list argument still
  means *that exact list*.
- **`policy_hash` covers what §7.1 said it covered.** Four things were missing and each is now
  in: an authority document loaded separately from the policy (the gateway's shape, and
  `verify --authority`'s — two deployments with different grants produced byte-identical
  provenance on every receipt); an action's `data:` labels; per-action and per-rule `controls:`
  citations; and the `controls:` registry itself, whose titles are what a receipt's control ids
  *mean*. The **effective** environment is hashed rather than the document's, since
  `$CTRLRUN_ENVIRONMENT` and `Control(environment=...)` outrank it.
- **A control's citations are recorded in registry order**, not in the order the action and the
  matched rule cite them, so two receipts citing the same set list it the same way whichever
  rule matched.
- **A relaxed policy now closes the approval it made unnecessary, and this is a change to shipped
  behaviour.** A policy relaxed between a human granting an approval and an agent presenting it
  left that approval `granted` for its full TTL, bound to a hash a later edit could make
  `APPROVE`-requiring again — a live bearer token for an action a human already answered, and
  `v0.1 §4.1` calls a request id a bearer token in as many words. The approval is now spent, in a
  write of its own **after** the reservation, so that an allowed action's success never depends on
  the approval store.
- **Every entry point re-checks the policy in force at execution**, and the receipt says which
  policy that was. Where the policy changed between a grant and its consumption, `SPEC-v0.6.md`
  §7.2's table decides by observation: a policy that now denies leaves the approval granted (the
  action is refused on the policy axis, and spending the approval would destroy the evidence a
  human answered); a policy that now allows invalidates rather than consumes it.

### Fixed

- **A store failure closing an unneeded approval no longer refuses the action.** Where a policy
  had been relaxed to `allow`, a locked database or a dropped connection while closing the
  now-unnecessary approval propagated to the caller — **after** the effect was reserved and
  **before** execution began. No receipt was written at all, and the effect key was left
  `RESERVED` until its lease lapsed: an ambiguity manufactured by the *permissive* decision path.
- **Concurrent store opens no longer fail.** Switching a database to WAL takes a brief exclusive
  lock that SQLite's busy handler does not cover, and `busy_timeout` was being set *after* the
  switch — so simultaneous opens raced and lost. It was survivable while opening a store was a
  read; v0.6 makes every open a potential write, which is the fleet restarting after an upgrade.
  Measured at 38 of 60 concurrent opens succeeding before, 120 of 120 after.

### Documentation

- **The browser page is a playground.** `/try-it` runs one protected refund on the released
  wheel in the tab: an amount, a payment id, a *lose the reply* switch, a *Refund* button and
  an *Approve* button that is the human. Every line it prints is `ctrlrun`'s own — the page's
  Python is a module over the public API, read out of the JavaScript by the same regex the
  Node harness uses, and `tests/test_docs_travelling.py` runs it natively through the six
  steps the page suggests on every commit. The harness records what it ran in
  `docs/assets/browser-demo.verified.json`, and the page's quoted versions are held to it.
- **The front door leads somewhere.** The docs home shows `@protect` and a policy before it
  shows anything else, the capability grid shows the six guarantees and folds the other twenty,
  the quickstart is titled for what it takes and opens with `pip install`, the cookbook sidebar
  is grouped the way its index is, and the README puts *Protect your first action* ahead of the
  problem statement and the release notes, with the long policy and the verify transcripts
  collapsed. The capability *One effect, once* now reads "happens at most once", which is the
  hero's phrase and the one the limitations section had been contradicting. The policy reference
  states the version rule: declare the lowest schema that has every key you use.

- **`docs/SPEC-v0.6.md`** — the v0.6 "Durable runtime" contract, a delta over v0.1–v0.5. No code
  lands with it. It asks one question: *does it still hold when the process dies, the host goes
  away, and the database is somewhere else?* Every guarantee shipped so far is a guarantee about
  one process holding one SQLite file, and `BEGIN IMMEDIATE` is a whole-database write lock on a
  local file; take the file away and the promise has to be re-earned with a different mechanism.
  Tests come from §8 (T140–T181); public names are frozen in §9.

  **An independent review in a session that did not write it found twenty-one defects, four of
  them blockers**, and every one became an edit. Three of the four were invisible from the diff
  and visible only from the shipped code: a re-read that concluded *"it carries our `action_id`,
  so we hold it"* — which another process's live reservation satisfies, giving a double execution
  through the storage layer; a claim that a failed receipt write is *"logged, not raised"*, which
  is `v0.1 §6.1`'s rule about the **JSONL file** and not about the store, and would have turned
  the evidence-integrity section into silent evidence loss; and a migration that adopted a
  pre-v0.6 database *without running its baseline DDL*, which would have left every v0.1 and v0.2
  database with no `continuations` and no `delegations` table. `docs/SPEC-v0.6.md` §9.6.1 records
  all twenty-one with where each landed.

- **`docs/docs/ROADMAP.md`'s v0.6 bullet said "receipt integrity (hash chain / signatures)", and the
  slash was the problem.** A chain detects **alteration**; a signature proves **origin**, and
  proving origin brings key generation, rotation and revocation with it — which is issuing, and
  this project verifies what it is handed. Signing is out of scope for v0.6 (`SPEC-v0.6.md` §11).
  Corrected in the same commit as the specification, on the rule `SPEC-v0.4.md` §9.4 set.
- **`docs/docs/THREAT_MODEL.md`'s "Receipts are not signed; a database admin can alter history
  (v0.6)"** promised something v0.6 does not deliver. Rewritten to say which half v0.6 closes —
  the partial tamper: an `UPDATE` on one row, a `DELETE` from the middle, a reordering — and
  which half it does not: **truncation at the end**, authorship, an adversary who can rewrite
  every row including the chain head, and the question of whether every action wrote a receipt
  at all. Two drafts of that line claimed truncation, and a review measured it: deleting a
  suffix and rewinding the head is two statements, undetected.

### Shipped in this release, and not part of the v0.6 milestone

**Two subcommands landed after v0.6's code was complete, and ride along in this release.** They
are recorded under their own heading rather than mixed into the milestone, because v0.6's claim
is that *the surface did not grow* — and it did not. These grew it, afterwards, each under its
own specification, and neither gated the release nor was gated by it. Both are subcommands of
the `ctrlrun` distribution rather than separate ones, so unlike an adapter neither carries a
version line of its own.

#### `ctrlrun scan` — the command that says what is **not** covered

`docs/SPEC-scan.md` is the contract; it was written first and its §8 tests were red before any
of it existed. `scan` reads a Python tree and a policy document and reports the consequential
call sites and policy entries CTRLRun is not covering — the gap between *installed* and *in the
path*, which until now had no command.

- **The honest half is the load-bearing half.** A scanner reports what it found where it
  looked, and a clean result is not a verdict. §4 enumerates what it misses by construction —
  dynamic dispatch, reachability, anything outside the tree, and a deployment whose protection
  is entirely the gateway — and the report says so on **every** run, including the run with no
  findings.
- **There is no score, no percentage and no badge** (§10). A number that improves when the
  vocabulary is shortened is a number that will be.
- **It adds no entry point at all** (§9.2), stated as a rule rather than as a fact about the
  first implementation: the tempting version of this tool builds an `Action` for each call site
  and asks the policy what would happen to it, which would be a principal invented by a tool
  from a source file.
- Exit codes: `0` nothing found, `1` a finding or a call whose name could not be resolved, `2`
  the scan could not run.

Five sections of the specification carry a paragraph beginning *Found by*, recording what
writing the tests changed about the design: the plural rule separating `stripe.refunds.create`
from `refunds_report`; `execute` dropped from the vocabulary, because `cursor.execute` was 90 of
208 findings against this repository's own `src/`; a policy action whose decorator supplies its
own effect template no longer reported as missing one; a call on an expression matched rather
than filed as undetermined, which took that list from 216 entries to 10; and `undetermined`
removed from the finding kinds it was listed among and contradicted by.

#### `ctrlrun mcp-operator` — answer an approval from the assistant you already use

`docs/SPEC-mcp-operator.md` is the contract. It authenticates *who* answered and records it; it
does not check that they were entitled to, which is separation of duties and is still not built.

**Added**

- **`ctrlrun mcp-operator`** — an MCP server exposing the operator's own commands as tools, so
  the person who has to answer an approval can answer it from the assistant they are already
  talking to. Read tools `list_pending_approvals`, `inspect_action`, `receipts`, `effects` and
  `stats`; write tools `approve`, `deny` and `resolve`. Ships in `ctrlrun[gateway]`, imports
  nothing from an extra, and `import ctrlrun` imports none of it (T192).
- **`ctrlrun.reporting`** — core and stdlib. The `ctrlrun.inspection/v2` and `ctrlrun.stats/v1`
  document builders, moved out of `ctrlrun/cli/main.py` unchanged so that the CLI and the
  operator server have **one producer each**. T193 asserts the two agree by equality rather
  than by shape, which is the only version of that claim worth having.
- Two entry-point rows in `docs/SPEC-v0.3.md` §4.3.1, written before the code, as that section
  requires of every new way in.
- Two JSON-RPC codes in `v0.2 §6.10`'s reserved `-410xx` range: `-41013 ctrlrun.not_a_human`
  and `-41014 ctrlrun.principal_expired`, neither reachable from the gateway.

**What it deliberately does not do**

- **It is not a second approval path.** `approve` and `deny` are the two store calls
  `ctrlrun approve` and `ctrlrun deny` make, against the same record, with the same hash
  binding, single use and expiry. There is one approval record and one place its state changes.
- **It cannot make an agent act.** No tool proposes, executes or resumes; no `Control` method
  but `store`, `policy` and `environment` is referenced, and T189 asserts that against the
  source rather than against behaviour.
- **It has no `--allow-remote` and no `--principal`.** Its read tools answer without a
  credential, so it binds loopback and there is no flag that changes that; and a static
  principal would attribute every approval to one name whoever gave it. Both absences are
  asserted by name (T183, T185), so adding either fails a test rather than a review.
- **It does not authorize the approver, only authenticate them.** Any human whose credential
  the provider verifies can answer any pending request, exactly as any human who can run
  `ctrlrun approve` can. `docs/SPEC-mcp-operator.md` §10 says so in the place a reader would
  otherwise assume otherwise; separation of duties is still not built.

**What the independent review changed**

An authorization surface gets a review in a session that did not write it. Ten findings, all
ten accepted. Four changed the contract; `docs/SPEC-mcp-operator.md` §9.5 has the table.

- **The `--identity-jwt-*` validation was missing**, with three `assert`s in its place. A
  server could start with an unpinned issuer, audience, algorithm or `typ` — and under
  `python -O` the asserts vanish. An unpinned `typ` accepts an ID token, so an OIDC login
  would have approved a payment. The gateway's `check_jwt_flags` is now shared rather than
  copied, every check is an `InvalidArgument`, and one test runs under `-O`.
- **"Every refusal leaves the store byte-identical" was false.** Answering a lapsed request
  moves it `pending → expired` and commits before refusing — the kernel's own rule, *a lapsed
  approval is evidence* — and the test table had omitted that row, so the claim was asserted
  nowhere. The spec carves it out and the test asserts the delta.
- **The composes-nothing test checked 81% of the file.** It split the source on a marker
  comment; the excluded fifth was the part that handles the socket, and a `Control.execute`
  inside `do_POST` passed it. It scans the whole file now.
- **`::1` was accepted and could not bind.** `ThreadingHTTPServer` inherits `AF_INET`, so
  `--listen ::1:8901` exited with a traceback while the test asserted only that the string had
  been stored. The socket family follows the host, `[::1]` is accepted, and the test binds.

Also: `--otel` was inert and is gone, a `Content-Length` of `-1` reached an unbounded read, the
repeated-identity-header check lived only in the stdlib handler, the startup block omitted the
store, and `serve_operator` leaked a connection under `--store-url`.

**Changed**

- `ctrlrun.cli.main` no longer defines `INSPECTION_SCHEMA`, `STATS_SCHEMA`, `_stats_document`
  or `_since`; they are `ctrlrun.reporting`'s. `ctrlrun inspect --json` and `ctrlrun stats
  --json` emit byte-identical documents to before, and a `--since` that does not parse is
  still a usage error with exit code 2.

## [0.5.0] - 2026-09-05 — Adapter contract

v0.4 asked *does it hold in my setup?* v0.5 asks a narrower and harder question: **can somebody
else implement this?** The adapter contract is one of the six things v1.0 freezes, so it is
written to be lived with rather than revised once somebody tries it.

**The milestone's own answer is yes, with caveats.** A session that could read the five
specifications and nothing else — no kernel source, no reference adapter, no test — wrote a third
adapter against the contract and returned fourteen questions it could not answer. Every one
became an edit, and one of them was a defect in a *shipping* adapter that no test caught. That
exercise, not the two adapters, is what v0.5 is for.

### Added

- **Two reference adapters**, on their own version line and in their own distributions:
  `ctrlrun-langgraph` (`adapters-langgraph-1.0`) reuses `interrupt()`, `Command(resume=...)` and
  the checkpointer; `ctrlrun-openai-agents` (`adapters-openai-agents-1.0`) reuses the SDK's
  tool-approval interruption. Neither is in the `ctrlrun` wheel or sdist (T136). LangGraph
  passes the conformance kit 6/6; the Agents SDK 4/4 with two suites `not_applicable`, each with
  its reason on the report and in the README.

  **Prevention or attribution**, in that word, is the sentence each README leads with.
  LangGraph's resumption carries the arguments a human answered against and core re-checks them;
  the Agents SDK records *that* a call was approved and not what its arguments were.

- **`ctrlrun.adapter`** — the surface: `FrameworkInterrupt`, `PendingApproval`,
  `ApprovalAnswer`, `InterruptApprovalProvider`, `needs_approval`, `banner`, and
  `Control.resolve_principal` promoted from private. Core, stdlib, in the action path. An
  adapter returns an answer and **one core provider writes the grant**, through the same calls
  `ctrlrun approve` makes — which is what makes "never a second approval path" structural.

- **`ctrlrun.conformance`** — this repository's own `v0.1 §7` and `v0.3 §10` acceptance tests,
  runnable against any adapter, in **core**. It is not a certification and passing it is not a
  claim about quality: it answers one question, *does an action driven through this adapter get
  the same refusals as one driven through `@protect`?* Fifteen deliberately broken fixtures were
  written **first**, and each fails the suite named for it and no other.

- **`docs/docs/adapters.md`**, and a README section that opens by saying when you do **not** need an
  adapter (T139) — `@protect` covers anything in this process and the gateway anything over MCP.

- **The framework probe was run** against LangGraph 1.2.11 and openai-agents 0.22.0, five
  repetitions each, and the results are published. Read the `approval-mutation` column carefully:
  `executed_once` there does not mean the scenario went well.

### Changed

- **`ctrlrun demo --help` said four scenarios and ran five**, stale since v0.3 added the
  authority escalation.

### Security

Three independent reviews and item 6 found five authorization defects in
`ctrlrun-openai-agents` before it shipped. All are fixed, mutation-tested, and recorded here
because the pattern matters more than any one of them: **the SDK's approval record is keyed to a
tool call, and a CTRLRun grant binds to an action hash**, so every defect was the same shape —
reading a coarser answer as though it answered a finer question.

- `interrupt()` returned `granted=True` unconditionally.
- It then accepted a **sticky** per-tool decision, so `always_approve=True` answered for later
  calls no human saw.
- It then answered for **every action raised under one tool call**: a human approving a $5 refund
  authorized a $1,000,000 wire raised beside it, with a receipt naming the channel as approver.
- `unwrap()` returned the first `CTRLRunError` anywhere in the chain, so a nested `NotExecuted`
  masked an AMBIGUOUS refund — *safe to retry* reported for an effect that may have landed.
- **Observe mode interrupted and blocked the action.** Found by item 6 without reading the
  adapter. §3.6's rule followed for one framework shape and had to be *required* of the other;
  a deployment evaluating CTRLRun in the mode built for evaluating it would have had its agent
  halted.

### Added

- **`docs/SPEC-v0.5.md`** — the v0.5 contract, a delta over v0.1, v0.2, v0.3 and v0.4. It fixes
  the adapter surface (§2), the approval round trip (§3), the `SPEC-v0.3.md` §4.3.1 rows an
  adapter adds (§4), the conformance kit (§5), packaging and versioning (§6), what an adapter
  must document (§7), the acceptance tests T126–T139 (§8) and the public names v1.0 will freeze
  (§9). No implementation lands with it.

  Five decisions are written into it **with their arguments**, because a decision whose
  reasoning lives only in a build note is one the next session re-litigates:
  `ApprovalRequired` + `with_approval` rather than `Suspended` + `Control.resume`; a Protocol
  rather than a base class; the conformance kit in this repository rather than a third distribution;
  one repository with separate distributions; and an adapter that **sees** the principal and
  never supplies one.

  A sixth was open and is settled in §3.6: an adapter **never interrupts in observe mode**,
  **never prints** — it is inside somebody else's loop and may have nowhere to print — and
  **logs the `SPEC-v0.3.md` §6.5 banner once per `Control`**, which `ctrlrun.adapter.banner`
  does so no two adapters word it differently. The conformance kit **refuses** an observing
  `Control` — a refused report with no suites, which is not a success — rather than producing
  an all-`not_applicable` report with a zero denominator, because `0/0` reported as a pass is
  the false green `SPEC-v0.4.md` §3.8 refuses by name.

  **An independent review in a session that did not write the document found five defects that
  would each have produced an insecure or unimplementable adapter, and §9.1 records them.** The
  worst was `--principal-from-client-info`'s third costume: §3.5 told an adapter to answer its
  framework's pre-invocation predicate with `Control.evaluate(action)`, and `Action.principal`
  has no default — so the only way to obey was to build a principal from the framework's
  session. `ctrlrun.adapter.needs_approval` is core's because of that, and
  `Control.resolve_principal` is promoted from private for it: the identity seam an adapter may
  **read** and may not supply.

- **`ctrlrun.adapter`** — the adapter surface: the `FrameworkInterrupt` Protocol,
  `PendingApproval`, `ApprovalAnswer`, `InterruptApprovalProvider`, `needs_approval` and
  `banner`. Core and stdlib, re-exported from `ctrlrun`, and `Control.resolve_principal` is
  promoted from private for it — the identity seam an adapter may **read** and may not supply.

- **`ctrlrun.conformance`** — the adapter conformance kit: the `SPEC-v0.1.md` §7 and
  `SPEC-v0.3.md` §10 acceptance suites runnable against any adapter through that surface, and
  **eleven adapters broken in one named way each**, written before the reference adapters because
  "two adapters pass the suites" means nothing until the suite can fail.

  It is **core and stdlib-only**, not the extra `SPEC-v0.5.md` §5.1 originally specified. The
  premise there was that a kit needs `pytest`; building it showed otherwise, and an extra with
  no dependency behind it is an install line that installs nothing. §12.1 records the change.
  `dependencies` is unchanged: `pyyaml` and `click`.

### Changed

- **`docs/docs/ROADMAP.md`'s v0.5 bullet was wrong and is corrected here**, not silently. It said
  the reference adapters map their frameworks' interrupts onto `Suspended` / `Control.resume`,
  "which v0.2 already ships for exactly this shape". It does not: `Suspended` exists for the
  remote asking a question *mid-execution*, where the reservation is already taken and must
  stay taken, and an approval gate has none to hold — v0.1 consumes the approval in the same
  transaction as the reservation, so a human deliberating for an hour pins nothing.
  `SPEC-v0.5.md` §3.1 argues it in full. This is the treatment `SPEC-v0.4.md` §9.4 gave the
  threat model's sentence about a check verify could not deliver.

- Version is `0.5.0.dev0`.

## [0.4.0] - 2026-09-04

**Does it hold in *your* setup?** Everything CTRLRun guarantees was proven, until now, by this
repository's tests against this repository's configurations. That is the right place to start
and the wrong place to stop: what an operator deploys is *their* policy, *their* grants and
*their* store, and a guarantee that has never been exercised against those is a guarantee
nobody has checked.

`ctrlrun verify` runs the failure scenarios of v0.1 §7, v0.2 §10 and v0.3 §10 against the
configuration in front of it and reports what passed, what failed, and — the part that makes
the number mean anything — what could not be tested at all.

Three rules govern it, and each has a test that would go red if it stopped holding. **Not
applicable is not a pass.** **Verify never touches the operator's store.** **The badge means
"declared guarantees pass"**, and nothing else. A fourth keeps verify honest about itself:
**every guarantee carries a positive control**, because a refusal asserted against a scenario
in which nothing ran passes on a kernel with the guard deleted.

No schema changes: `ctrlrun.policy/v3`, `ctrlrun.receipt/v2`, `ctrlrun.action/v1` and
`ctrlrun.inspection/v2` are untouched, and **no store gains a table or a column** — verify
writes only to a scratch store it created. Three new schema strings belong to documents rather
than to storage: `ctrlrun.verify/v1`, `ctrlrun.guarantees/v1` and `ctrlrun.framework-probe/v1`.

### Added

- **`ctrlrun verify`** — the guarantee catalogue, the scenario engine and all ten guarantees
  (SPEC-v0.4 §2, §3). `ctrlrun.verify` is **core**: stdlib, `pyyaml` and `click`, because a
  verification tool that needed an extra installed is one half the deployments never run. It is
  not re-exported from `ctrlrun` and `import ctrlrun` does not import it.

  It reads the operator's policy document, and the authority document beside it where
  `--authority` names one, derives concrete actions, principals and delegations the
  configuration actually admits, and runs the failure scenarios of `v0.1 §7`, `v0.2 §10` and
  `v0.3 §10` against them — in a scratch store, with in-process fake executors, reaching no
  network. `G1` mutated approval refused · `G2` replayed approval refused · `G3` duplicate
  effect refused · `G4` one winner under concurrency, across real OS processes · `G5` ambiguous
  blocks a blind retry · `G6` unknown action refused · `G7` no principal refused · `G8` expired
  authority refused · `G9` delegation cannot escalate, on every dimension the parent constrains
  including the omission case · `G10` unknown exception is ambiguous, never failed.

  **Every guarantee carries a positive control.** A refusal is satisfied just as well by a
  scenario in which nothing ever ran, and that scenario passes against a kernel with the guard
  deleted — so each scenario runs a companion establishing that the observable would have been
  visible had the guard not fired. A control that does not behave as specified makes the
  guarantee `fail` with `reason: "control failed"`: never a pass, and never an N/A.

  There is no randomness anywhere — not seeded randomness, none. Selection is sorted by
  codepoint, values come from a fixed table, the candidate search is bounded at 64, and two runs
  against one document produce byte-identical JSON once the timestamps are removed.

- `ctrlrun verify [--authority PATH] [--json] [--junit PATH] [--only G1,G3] [--store-url URL]`,
  replacing the v0.3 stub. Exit codes: 0 every applicable guarantee passed and at least one was
  applicable, 1 a guarantee failed, 2 the configuration was refused or is unusable, 3 an
  internal error in verify itself.

- **Reporting** (SPEC-v0.4 §4). The human report is one line per guarantee in catalogue order,
  every N/A carrying the reason that made it one, with the summary as the last line so a
  `tail -1` is meaningful. `--json` emits one `ctrlrun.verify/v1` document carrying the SHA-256
  of both documents verify read — a report and a policy that do not hash the same are a report
  about something else — and a `counterexample` **only** on a `fail`, because a counterexample
  on a pass would be evidence of a failure that did not happen. `--junit PATH` writes a JUnit
  XML file in which an N/A is `<skipped>` and never a pass, which is the same rule as
  everywhere else expressed in the vocabulary a CI dashboard already has.

  JUnit XML has no normative schema, and the report says so rather than implying one: T115
  validates against `tests/data/junit-10.xsd`, a checked-in copy of the de-facto Windy Road
  schema with its provenance and Apache-2.0 licence recorded beside it, and asserts the
  document structurally as well — a permissive schema is not a check. `xmlschema` joins the
  **dev** extra for that test and for nothing else.

- **The GitHub Action, the badge and `docs/docs/verify.md`** (SPEC-v0.4 §5). `action.yml` at the
  repository root is a composite action: it installs `ctrlrun`, runs
  `ctrlrun verify --json --junit`, renders the job summary and the badge **from that report**
  rather than from a second run — so the badge, the summary and the uploaded artifact can never
  disagree about what happened — and uploads the three files as one artifact.

  It fails the job when a guarantee failed and when the configuration was refused, and succeeds
  when guarantees are N/A. **There is no input that makes a failure not fail the job**: a
  `continue-on-error`-shaped flag here would be a flag that makes a consequential thing
  permissive by default, and a workflow that wants to tolerate a failure has
  `continue-on-error` on the step already, where it is visible.

  The badge is a Shields endpoint JSON the action **writes and never publishes**. Committing it
  would need `contents: write` in every consumer's workflow, and asking for write access to a
  repository as the price of a verification badge is a bad trade for a tool whose subject is
  least privilege; `docs/docs/verify.md` shows the one-job publishing pattern once, with its cost
  visible. Rendered, it reads exactly `CTRLRun verified N/M`, where `M` is **applicable**
  guarantees and never the catalogue size. A partial run and a run that exited 2 or 3 write no
  badge at all.

  The badge means **"declared guarantees pass"** — that phrase, on the badge's link target, and
  no other. Not secure, not safe, not compliant, not certified, not audited.
  `docs/docs/verify.md#what-the-badge-means` says it in its first sentence and, on the same screen,
  what verify cannot see: the operator's executors, their `reconcile` hooks, where they put the
  decorator, their deployment, and whether the policy is the right policy.

  This repository's CI runs the action against `examples/authority/payments.yaml` (10/10) and
  against `examples/policies/payments.yaml` (5/5, 5 not applicable), asserting **both shapes** —
  so a change that made verify silently count N/As as passes is caught in CI rather than in a
  badge.

- **`docs/docs/OWASP-AGENTIC-TOP10.md`** (SPEC-v0.4 §6) — a reading of the OWASP Top 10 for Agentic
  Applications (2026 edition, announced 2025-12-09) against the ten guarantees. Its first line,
  before any table, says what it is not: not a compliance claim, not a conformance claim, not a
  certification, and not a statement that CTRLRun covers the Top 10.

  Two tables, and the second is what makes the first credible. `ASI04` supply chain, `ASI05`
  code execution and `ASI06` memory and context poisoning are **not CTRLRun's subject** —
  nothing here inspects a package, sandboxes an interpreter or reads a model's memory — and
  `ASI07` inter-agent communication waits on v0.7. `ASI01` agent goal hijack and `ASI09`
  human-agent trust exploitation appear in **both** tables, because CTRLRun constrains what a
  hijacked agent can do without detecting the hijack, and binds an approval to one action
  without authenticating the approver or noticing that they were misled.

  The document records how its codes and titles were derived, because the published PDF sits
  behind a download form and could not be retrieved: they come from the OWASP-owned
  `OWASP/secure-agent-playbook` repository, corroborated against two independent summaries, and
  the four places where a third summary disagreed are named. That correction is what SPEC-v0.4
  §6.2 marked its own provisional list as needing.

- **`research/framework-probe/`** (SPEC-v0.4 §7) — a research harness that drives the
  double-refund and approval-mutation scenarios through third-party agent frameworks against a
  fake remote, and emits a table. It lives outside `src/`, is never imported by `ctrlrun`, and
  its per-framework dependencies are never installed by `ctrlrun` or by any of its extras.

  Its README's first paragraph says what the table is: **behaviour, not quality**. A framework
  that retries a lost response is doing what its documentation says it does; the finding is
  about what an agent stack does *without* an effect-level guard.

  One fake remote for every framework, with three behaviours — commit-then-drop,
  commit-then-timeout, capture-what-was-approved — counting **effects by identity, not by
  request**, so "executed twice" means two effects and not two HTTP calls. Every outcome is
  derived from what the remote saw and never from anything an adapter reports about itself.

  Two stub frameworks run by default and disagree: one retries and reports `executed_twice`,
  one does not and reports `executed_once`. Without the pair, a harness hard-coded to say
  `executed_twice` would pass its own tests and say the same thing about every real framework
  it ever ran.

  **No results are checked in**, and a test asserts it. The runs are made and published by the
  maintainer; a commit carrying findings about other projects that nobody had reviewed is not
  one this repository makes.

- **The badge this repository shows is published**, by a `badge` job scoped as narrowly as the
  thing it does: `contents: write` at **job level** (the workflow is `contents: read`, so
  nothing else in it can write), running on a push to `main` and on nothing else — a pull
  request from a fork must not be able to write the badge, and a read-only fork token is a
  default rather than a refusal — and publishing the badge the `verify` job already produced,
  downloaded as an artifact rather than regenerated, so §5.1's one-run rule holds across the
  job boundary. `docs/docs/verify.md` shows the job and names the permission it costs.

- **`docs/SPEC-v0.4.md` gains a §12**, recording the readings the implementation had to take
  where the specification could not be satisfied as written. A specification that
  disagrees with the code it describes is worse than one that admits a gap: G6 drives a
  a guarantee's invariant is the **behaviour** and not one reason string — G6 asserts that an
  unlisted action never executes, against the `Control` the operator's configuration actually
  composes, accepting any reason that configuration can produce and reporting which one fired,
  because under an `authority:` section the refusal correctly arrives from the authority axis
  before policy is reached; G7 is `N/A`
  where no action in the policy can run, because §2.2 said "never" and §1.3 requires a control
  that such a policy cannot supply; G8 gains a fourth N/A reason for a layered document; G9's
  control names the delegation only where the parent's subject does not also match it; and
  G4's children are subprocesses rather than `multiprocessing`, which would re-import the
  caller's `__main__` in every child.

### Changed

- **`docs/SPEC-v0.3.md` §10 T85 is amended**, as SPEC-v0.4 §9.4 item 2 requires and in the
  commit that made it true: `ctrlrun verify` exits 2 under `mode: observe` with a message naming
  the mode, and runs under `mode: enforce`. The banner assertions for every other command are
  untouched. A frozen test whose subject was explicitly temporary is amended rather than
  deleted.
- **`docs/SPEC-v0.3.md` §4.3.1 gains an informational row** for `ctrlrun.verify.run` (SPEC-v0.4
  §3.9, §9.4 item 3). Verify is not a new entry point: it proposes no action of its own and
  drives the rows already there. The row exists because a reader will look for one.
- `docs/docs/ARCHITECTURE.md` §6's module map gains `verify/`, above `control.py` and beside `cli/`.

- **`docs/SPEC-v0.4.md`** — the v0.4 contract, a delta over v0.1, v0.2 and v0.3. v0.4 answers
  the question the first three releases could not: *does it hold in **my** setup?* Everything
  CTRLRun guarantees is proven today by this repository's tests against this repository's
  configurations, which is the right place to start and the wrong place to stop.
  `ctrlrun verify` runs those failure scenarios against the operator's own policy, grants and
  store type, and reports what passed, what failed, and what could not be tested at all.

  Three rules govern it. **Not applicable is not a pass**: a configuration with no `approve`
  rule cannot exercise the approval-binding guarantees, so they are reported `N/A` with the
  reason, excluded from the denominator, and listed separately — `3/3 (5 not applicable)`,
  never `8/8`. **Verify never touches the operator's store**: every scenario runs against a
  scratch store created and destroyed with the run, and `.ctrlrun/state.db` is byte-identical
  before and after. **The badge means "declared guarantees pass"** — that phrase, on the
  badge's link target, and never "secure" or "compliant".

  Nothing is implemented yet. The specification is the contract the seven build-list items are
  written against.

## [0.3.0] — unreleased — Authority

`0.3.0rc1` is this section, published to TestPyPI only, so the wheel and the sdist can be
installed from a real index before a version number that can never be reused is spent on
PyPI. The date lands when `0.3.0` is cut.

**Who is acting, and what are they entitled to?** v0.1 built the kernel and v0.2 put it in the
network path; both could see the action and nothing else. This release adds a second axis —
identity, grants and delegation — that policy never learns to read, plus the mode you roll it
out in and the command that tells you what it would have cost.

Three sentences govern everything below.

**Authority is opt-in, then fail-closed.** No `authority:` section is v0.2 behaviour, exactly.
An `authority:` section means every principal needs a grant and no grant means denied. There is
no half-way and no flag that makes a missing grant permissive.

**Attenuation is structural.** A delegated grant is valid only if it is provably a subset of
its parent, on every dimension, at creation *and* at every evaluation. Omission never means
unlimited: a child that drops a dimension its parent constrains is rejected.

**Identity is consumed, not invented.** CTRLRun verifies tokens it is handed and maps verified
claims onto a `Principal`. It issues nothing and defines no identity format. Claims are receipt
data rather than action identity — they are not in the canonical form, so an approval survives
a token rotation.

### Added

- **A fifth demo scenario, `ctrlrun demo` — authority escalation** (build-list item 6,
  SPEC-v0.3 §1.2). The first four ask whether an *action* is safe to run; this one asks
  whether a *principal* is entitled to propose it. A human's €100,000 delegable grant narrows
  to a finance agent's €25,000 and then to a support agent's €2,000, and the two ways out of
  the chain fail at two different moments: asking for €50,000 is refused at **evaluation**
  (`authority_constraint`), and minting €50,000 under a €25,000 parent is refused at
  **creation** (`containment`, naming the dimension). Asserting one would hide the other. The
  scenario ends with an amount the grant *does* permit, which the policy still stops to ask a
  human about — the two axes, combining as the stricter of the pair, in one line.
- **`examples/authority-escalation/`** — the same story as a standalone script, with the
  `else: raise SystemExit(...)` guard on every refusal. A demonstration that quietly starts
  succeeding is worse than none, because it keeps printing the line that says the guard worked.
- **`examples/authority/`** — a payments delegation chain and a DevOps chain, as complete
  documents to read rather than run, with a README that says in its first paragraph that every
  principal in them is invented.
- **`docs/docs/authority.md`** — grants, delegation and the omission rule in plain language,
  including the two things it is worth knowing before you need them: an `Authority` is built at
  load time and is not hot-reloaded, and there is no way to list delegations, so cutting a
  chain of unknown width means `delegable: false` on the root and a restart.
- **`docs/docs/THREAT_MODEL.md` gains v0.3's boundary.** In scope: delegation escalation, omission
  as widening, expired and revoked authority, token forgery, cross-JWT confusion, and signing
  keys fetched from somewhere else. Out of scope, and stated rather than implied: a compromised
  identity provider, a `HeaderIdentityProvider` behind a proxy that does not overwrite, a
  revoked token before its `exp`, a tenant-templated issuer, and authority across an
  agent-to-agent hop.
- **`MANIFEST.in` ships `examples/**/README.md`, and a test now keeps it honest.** The file
  has claimed for two releases that a test named `test_the_sdist_carries_everything_the_tests_need`
  keeps it in step, and there was no such test — so the first README it forgot was found by
  the CI job that builds an sdist and runs its tests, one push after it could have been found
  locally. That is v0.2's four `.gitignore`d policy files in a different costume: setuptools
  resolves `MANIFEST.in` against the working tree, so a local run is green either way. The
  test now checks every **git-tracked** file under `examples/` and `docs/` against the
  manifest's include patterns, and it found `examples/acs/README.md` had been missing since
  v0.2 as well.
- **Fixed a test whose fuse was the suite's own duration.** T27's parametrize list built two
  timestamps at **collection** time, so the future-dated one was 400 seconds ahead of
  collection and only `400 - <however long the suite had been running>` seconds ahead by the
  time the test executed. Past the replay window's 300 seconds it landed *inside* the window
  and the refusal under test stopped happening. It had been latent since v0.2 and fired the
  first time an item made the suite slower. The timestamps are built when the test runs, and
  the fix is verified against a `conftest` that stalls 120 seconds between collection and the
  body — the condition that failed CI.
- **The nine sector templates stay on v0.1 and now say why.** They gain no `authority:`
  section: a grant names a real principal in a real organization, and a template that shipped
  plausible ones would invite an operator to adopt them.
- **`JWTIdentityProvider`** (build-list item 5, SPEC-v0.3 §3.4), in `ctrlrun[identity]`
  (`pyjwt[crypto]`), imported by naming it. It reads a bearer token from a header, verifies it
  against a JWKS or a static key, and maps the verified claims onto a `Principal`. **It issues
  nothing**: no OAuth flow, no refresh, no token exchange, no introspection, no dynamic client
  registration. An absent header is a *decline*; a present and invalid one is a *refusal*, and
  `Control` never backfills from one.
- **Configuration is refused before any token is seen.** More or fewer than one key source; an
  `HS*` algorithm with `jwks_url` or `public_key`; an asymmetric algorithm with `secret`; an
  empty `algorithms`; `none` in any casing; `token_type` not passed at all. The
  `HS*`-with-a-public-key case is key confusion in its plainest form — RS256→HS256 is literally
  "HMAC the token using the PEM of the public key as the secret" — and it is refused at the
  *configuration* end, which is the end that can be refused.
- **`token_type` is required, and it is the whole cross-JWT defence.** Without it an OIDC ID
  token from the same issuer, signed with the same key, carrying the configured `aud`, passes
  every other check (RFC 8725 §2). Passing `None` explicitly is permitted and warns at
  construction naming exactly what it gives up.
- **`aud` by membership on either wire shape**, never a raw `in`: on the string shape that is
  substring matching, and it accepts `https://ctrlrun.example` for a configured `ctrl`. `exp`
  is **required** — a credential with no expiry cannot be revoked by waiting, and v0.3 has no
  revocation channel — and `exp`/`nbf` are compared against the provider's injected clock, so
  an expiry boundary is tested exactly rather than raced.
- **JWKS handling that cannot be turned into a load generator.** An unknown `kid` triggers at
  most one refresh and then a refusal; a second such token inside `jwks_min_refresh_interval`
  is refused with no fetch at all. A set with two entries sharing a `kid` is refused rather
  than resolved by first match; a key whose `use` is not `sig` or whose `key_ops` excludes
  verification is ignored; a key carrying its own `alg` constrains itself. A failed fetch is a
  refusal that discards nothing, and **nothing is followed**: the fetch refuses a redirect
  outright and re-checks the scheme of the URL that actually answered. `urllib`'s
  `build_opener` keeps its `HTTPRedirectHandler` unless an argument subclasses it, so an
  opener built the obvious way follows a 302 — including one to plaintext `http://` on
  another host. An open redirect on the issuer's domain would otherwise have this process
  fetch its signing keys in cleartext from wherever it pointed, cache them for the life of
  the process, and verify every token the attacker then signed.
- **Two limits stated rather than configured around.** `issuer` is an exact string, so a
  tenant-templated issuer cannot be configured correctly here — pointing it at a multi-tenant
  endpoint without pinning the tenant makes every tenant on that platform a valid issuer. And
  there is no revocation channel: short token lifetimes are the whole of the story.
- **Gateway identity selection** (§8.2). `--principal` and `--principal-header` are understood
  as constructors now — `StaticIdentityProvider` and `HeaderIdentityProvider` — and
  `--identity-jwt` is a third, with its own flags. Exactly one is required, still with no
  default; every `--identity-jwt-*` flag is an error without `--identity-jwt`, because a flag
  that cannot take effect is a flag the operator believes took effect — and the test for that
  reads the flag list off the command itself rather than off the check, because a list copied
  from the code under test cannot fail for the flag the code forgot. The shared secret is
  read from a **file**, never from a flag value: a secret on a command line is in every process
  listing on the host. `--identity-jwt-http-timeout` bounds the JWKS fetch and is **its own
  flag**: the fetch runs on the request thread before any decision is made, so borrowing
  `--upstream-timeout` would have coupled two unrelated knobs in the fail-slow direction.
- **`ctrlrun gateway --authority PATH`** (§8.3), because the person who writes grants and the
  person who writes per-action autonomy are often not the same person. That document is not a
  policy document — its top-level key set is closed at `schema` and `authority`, so `actions:`
  or `mode:` in it is a load error naming the file and the key. Declaring `authority:` in both
  the policy and `--authority` refuses to start, naming both paths.
- **`-41012` `ctrlrun.unauthorized`** (§8.4). A tool call outside the principal's grant returns
  it, HTTP 403, upstream untouched; `v0.2 §6.10`'s `-41001` now means a **policy** denial. The
  gateway catches `AuthorityDenied` **before** `ActionDenied`, which it subclasses — the other
  order makes the new code unreachable while every test asserting only "it was refused" stays
  green, so both halves are pinned by name.
- **No refusal reaches a client as a dropped connection.** `do_POST` has no top-level
  handler, and an escaping exception makes `socketserver` close the socket with nothing
  written — which a client reads as a transport failure, and a transport failure is the one
  signal it retries on. Three v0.3 paths landed there and each now answers: an authority
  denial or an expired credential on a **continuation** (`-41012` / `-41007`), an expired
  principal in `Control.execute` (`-41007`, and `IdentityError` is deliberately not an
  `ActionDenied`, so it needs its own clause), and an identity provider raising anything at
  all, which §3.2 makes an `IdentityError` in-process and now does at the gateway too. The
  expired-principal path is routine rather than adversarial: the JWT provider admits a token
  up to `leeway` past its `exp`, so every token in that 60-second window was affected.
- **A startup block that says what is in force** (§8.4): the environment, the identity provider
  by name and the header it trusts, whether an `authority:` section is loaded and how many
  grants it holds — or the single line `no authority: section — every principal is
  unrestricted`, so an operator who believes they configured authority finds out on the line
  that starts the process.
- **Observe mode** (build-list item 4, SPEC-v0.3 §6). A top-level `mode: observe` runs every
  real decision against real traffic and records what *would* have been blocked, without
  blocking anything: no `ActionDenied`, no `AuthorityDenied`, no `ApprovalRequired`, no
  `DuplicateEffect`. `Control.execute` returns a receipt whose `result` is `observed`, whose
  `execution` is what the executor actually did, and whose `would_have` says what enforce mode
  would have reached and what it would have done with it. It is the rollout path, and it is
  **not a dry run**: it executes, effects land at remotes, and the records of them are real.
- **One switch, and it governs the process.** `mode:` is top level and nothing else — inside an
  action, a rule, a grant or the `authority:` section it is a load error naming the rule, and
  so is a value that is not exactly `observe` or `enforce`. A partially-enforced configuration
  is the failure mode that rule exists to prevent. Absent means `enforce`; `mode:` needs
  `schema: ctrlrun.policy/v3`, because a reader that ignored it would enforce a configuration
  that was deployed to observe.
- **Observe mode still refuses what it cannot describe.** A missing principal, a provider that
  raises, an unresolvable effect key, an argument an Action cannot represent, and a delegation
  that would escalate are refused in both modes: the first four are wiring bugs that would run
  an action CTRLRun could not describe, and the fifth is an act of authority rather than a
  decision about an action. It asks no human either — a policy reaching `approve` records
  `approval_required` and runs, creating no request and appending no `APPROVAL_REQUESTED` —
  and it never calls the `reconcile` hook, whose `"committed"` answer would move a record a
  human may still be adjudicating.
- **A refused reservation writes no effect record.** The record belongs to the attempt that
  holds the key, and observe mode does not make a second attempt its owner: a record already
  `AMBIGUOUS` stays `AMBIGUOUS`. The refusal is on the receipt instead, as
  `would_have.blocked_reason`. The gateway's v0.2 §6.10 pre-check is skipped in observe mode
  for the same reason — it refuses calls before `Control.execute` is reached, and a gateway
  that kept it would enforce three rows in the one mode that enforces none of them.
- **`ctrlrun stats`**, counting from the local store and nothing else — no network, no
  aggregation service, no upload. Would-have-denied broken down by reason, would-have-needed-
  approval, would-have-been-blocked broken down by duplicate and ambiguous, and ambiguous
  outcomes. `--since` takes an ISO-8601 timestamp with an offset or `30m`/`24h`/`7d` and
  compares `finished_at` inclusively; `--json` emits the same numbers under
  `schema: ctrlrun.stats/v1`. In enforce mode it reports what actually happened and **reports
  less**, saying so in its footer rather than printing a line the receipts cannot substantiate.
- **The observe banner.** `OBSERVE MODE — nothing is enforced`, to stderr, before anything
  else, on every invocation of every command that loads the operator's policy — `gateway`,
  `stats`, `delegate`, `revoke`, `verify`. To stderr so a `--json` stdout stays parseable. The
  evidence commands (`receipts`, `effects`, `inspect`, `resolve`, `approve`, `deny`) do not
  print it and do not load a policy at all: reading evidence must not depend on the
  configuration that produced it.
- **`ctrlrun verify`** as a stub that prints to stderr that verification lands in 0.4 and exits
  **2**. It runs nothing, checks nothing, and claims nothing. It exists because observe mode's
  whole purpose is to lead somewhere, and the command an operator reaches for next should not
  be a `No such command` error that suggests they mistyped.
- **Delegation with attenuation** (build-list item 3, SPEC-v0.3 §5). A principal who holds a
  `delegable` grant can create a narrower one at runtime — `Control.delegate`, `ctrlrun
  delegate` — and revoke it — `Control.revoke`, `ctrlrun revoke`. A delegated grant is valid
  only if it is provably a subset of its parent on every dimension, checked **at creation and
  again at every evaluation**: a check performed only at creation would leave every delegation
  exactly as wide as the file used to be, which is the shape of every stale-permission incident
  there has ever been.
- **Omission never means unlimited.** A child that drops a dimension its parent constrains is
  rejected, not treated as inheriting the parent's limit and certainly not as unconstrained —
  including a child subject that carries a wildcard, omits `agent`, or drops the parent's
  `user`, each of which hands the grant to a wider population rather than a narrower one.
- **Revocation is transitive by structure.** Nothing is rewritten and no children are visited:
  a chain of any depth is cut by one write, because every evaluation walks to the root. Chain
  depth is **recomputed, never read** from the stored column, the walk is bounded and refuses a
  chain that revisits an id, and a stored delegation that cannot be read denies the action
  outright rather than being skipped in favour of a broader grant that happens to match.
- **A `delegations` table in both stores**, and `DelegationRecord` with it. A **new** table, so
  `CREATE TABLE IF NOT EXISTS` adds it to a database that already exists and v0.3 still needs
  no migration story; no existing table gains a column. `put_delegation` is an `INSERT` and
  never an upsert — an upsert on an existing id would clear `revoked_at`, which is `unrevoke`
  by another door in a release that says there is no such thing.
- **Three delegation event types**, `DELEGATION_CREATED`, `DELEGATION_REVOKED` and
  `DELEGATION_REJECTED`. They are about an authority record rather than an action, so
  `Event.action_id` becomes `str | None` and they carry `null`; `OTelEventSink` emits a
  standalone span for each rather than dropping them, because the highest-privilege operations
  in the release must not be the only ones missing from the export path.
- **An expired credential mints nothing.** `Control.delegate` refuses a `by` whose credential
  has lapsed, before it looks at the parent at all — a delegation is the most durable thing a
  principal can create, and it is the last place a stale credential should still work.
- **The `authority:` section: grants, patterns and evaluation** (build-list item 2,
  SPEC-v0.3 §4). A second axis, and it is **opt-in and then fail-closed**: a document with no
  `authority:` key behaves exactly as v0.2, and the moment one exists every principal needs a
  grant — including for actions the policy allows outright, including reads, including actions
  with no effect key. There is no `default: allow` and no flag that makes a missing grant
  permissive. `Authority`, `Grant`, `Subject` and `AuthorityResult` are public;
  `Control(authority=...)` takes one, and `Control.from_file` reads it from the same document.
- **Authority is evaluated before policy, and the two combine as the stricter of the pair.**
  A denial there appends `AUTHORITY_DENIED` and **never** `POLICY_EVALUATED`, so it cannot
  leave a pending approval request behind for an action that could never run. Neither axis
  loosens the other: authority cannot make a denied action allowed, and policy cannot make an
  unauthorized one permitted. **A grant carries no `decision:`** — how much autonomy an action
  has stays the same for every principal, and what differs is whether they may propose it at
  all.
- **A pattern grammar small enough that containment is decidable** (§4.4, §5.5): a literal, a
  `prefix*` that cannot cross a separator, and a final `**` whose preceding segments are all
  literals. `stripe.*` matches `stripe.refund` and not `stripe.refund.partial`; there is no
  `?`, no character class, and no short spelling for "everything" — granting the whole surface
  of a system is spelled `**`, one token, greppable, and impossible to write by accident.
- **Two new event types and one new error.** `AUTHORITY_RESOLVED` is appended for *every*
  action that passes authority, not only a delegated one, so a deployment with a permissive
  grant is distinguishable from one with no section at all. `AuthorityDenied` subclasses
  `ActionDenied` — an authority denial *is* the action being denied — and carries a reason from
  §4.3's closed set, never a grant id: a grant may legally be named `no_authority`, and
  evidence that can be spoofed by naming a grant is not evidence.
- **Extended `Principal`, `IdentityProvider`, and the two core providers** (build-list item 1,
  SPEC-v0.3 §2, §3). `Principal` gains `claims`, `issuer` and `expires_at`, validated the way
  arguments are — no `float`, no containers, a timezone-aware expiry — and **none of the three
  is in the action hash**, so an approval survives a token rotation. `StaticIdentityProvider`
  and `HeaderIdentityProvider` ship in core; the JWT one lands with item 5. A provider's answer
  wins over `context()`, a `None` is a decline that leaves the v0.1 path intact, and a provider
  that *raises* is never backfilled — falling back there would turn a rejected credential into
  a successful action.
- **An expired principal is refused before authority and before policy**, with a `denied`
  receipt and no approval request. `Control.evaluate` returns `deny`/`principal_expired`
  instead, because it may not write.
- **`ctrlrun inspect`** shows the issuer, the expiry and the claim *names*; the values reach
  `--json`. The OTel sink exports the issuer and the names only — a span goes to a third party
  by default, a receipt is evidence meant to be read.

- **`docs/SPEC-v0.3.md`** — the v0.3 contract, a delta over v0.1 and v0.2. Seven deliverables:
  an extended `Principal` and the `IdentityProvider` protocol; the `authority:` section with
  grants, patterns and constraints; delegation with structural attenuation; observe mode and
  `ctrlrun stats`; a JWT identity provider and the gateway wiring for both; examples and docs;
  the release. Tests come from §10 (T60–T93).
- **The `identity` extra**, empty until build-list item 5 adds the JWT verifier and the `pyjwt`
  line it needs. `pip install ctrlrun` still installs nothing but `pyyaml` and `click`.

### Changed

- **BREAKING: a condition naming `claims`, `issuer` or `expires_at` is refused at load**, in a
  document of *every* schema version (§4.5, §12.1). The reservation lives in the condition-key
  splitter, which runs for every condition in every document, and gating it on `v3` would leave
  the same name meaning two things in two files. A `v1` policy whose protected function takes an
  argument called `claims` stops loading until the argument is renamed; the load error says so.
- **BREAKING: `AcsControlHook` takes an `identity` provider, and ignores the envelope's
  `agent_id` and `environment` when it has one** (§8.4). Under v0.2 both came off the wire, on
  exactly the argument `v0.2 §6.5` made for `clientInfo`: a policy could not address the
  principal. §4 ends that, and §8.1 removes `--principal-from-client-info` over the same
  sentence — the ACS hook was that flag in a different module. A hook built against a `Control`
  holding an `Authority` with no provider raises `InvalidArgument` at construction. With a
  provider, `agent_id` is ignored — not merged, not a fallback, not compared — and a provider
  that names nobody is a denial with `reason_codes: ["no_principal"]`, never a fall back to the
  envelope. `handle()` gains an optional `headers=` for the transport's own headers, which is
  what a provider reads. `docs/docs/ACS.md`'s mapping table is amended in the same change.
- **All three v0.2 call sites now use the combined decision** (§8.3): the gateway's `tools/call`
  path, `ctrlrun.acs`'s request hook, and `Control.resume`. Left as `Policy.evaluate`, an action
  a grant forbids outright would still have its approval flow run, and a human would be paged
  about a call that could never happen.
- **`Control.evaluate` returns the combined decision**, not the policy axis alone. Its signature
  and `Evaluation`'s two fields are unchanged, and it still writes nothing — it would simply
  stop answering "what will happen to this action" if it reported one axis while `Control.execute`
  acted on both.
- **`Control.resume` records the authority axis on its receipt** (§5.6.1). Evaluated and
  recorded, not re-decided: the reservation is held and the remote may already be acting on it.
  A lease extension is the other way round — refused where authority no longer covers the
  action, so the lease lapses and the record becomes `AMBIGUOUS` by the ordinary path.
- **`policy._Condition` is now `policy.Condition`, and `policy.parse_conditions` is public.**
  A grant's `constraints:` is in exactly a rule's `when:` syntax and is parsed by the same code:
  a second condition evaluator would be a second place for `True` to start comparing equal to
  `1`. The top-level policy key set gains `authority`, which needs `ctrlrun.policy/v3`.
- **BREAKING: `context(environment=...)` is removed** — already recorded below, and item 1 is
  where it happens. `Control(environment=...)` replaces it.
- **BREAKING: `ctrlrun gateway --principal-from-client-info` is removed.** It exits non-zero
  naming `--principal-header`, rather than starting with a principal the operator did not ask
  for. `--environment` now defaults to unset so `$CTRLRUN_ENVIRONMENT` is not silently
  outranked, and `--user-header` without `--principal-header` is an error rather than a flag
  that cannot take effect.
- **A repeated identity header is refused** — `-41007`, HTTP 403, upstream untouched. A
  `Mapping` holds one value per name, so something had to decide what two become, and under
  authority that decision picks the principal.
- **BREAKING for a reader: `ReceiptResult` gains `observed`.** `Receipt.from_dict` parses
  `result` into a closed `StrEnum` and `SQLiteStateStore` reads every stored receipt through
  it, so a CTRLRun ≤ 0.2 process running `ctrlrun receipts` or `ctrlrun inspect` against a
  store an 0.3 **observe-mode** process wrote raises on the unknown value. Two processes
  sharing one store is the intended deployment: **upgrade every reader before switching any
  writer to `mode: observe`.** An enforce-mode 0.3 writer emits no `observed` receipt and is
  safe to mix. An external reader that keys on `result` must read `execution` too, or it
  counts every observed execution as if nothing ran.
- **The demo's scenario 5 shares the store *and the sinks* of the other four.** A second
  `Control` that quietly dropped the JSONL sink left the demo printing a receipt count from
  the store that the file it points the reader at did not match — a false green, in a
  transcript people paste into issues.
- **`ctrlrun.receipt/v2` and `ctrlrun.inspection/v2`.** The receipt carries the whole principal
  and reserves `execution` and `would_have` as `null` until observe mode fills them, so the
  shape a reader parses settles once rather than changing twice under one version string. The
  stored Action carries the whole principal too — without it every receipt written by
  `Control.resume` reported no claims and no expiry, on the only receipt an MCP
  multi-round-trip action ever gets.

- **Four edits claimed by the previous change were never in the file.** A batch that wrote only
  at the end discarded every successful replacement before the one that raised, and only the
  failing one was re-run — so `authority_unreadable` was referenced by §4.6, three rows of §9 and
  T77d while §4.3's closed reason set never defined it; the precedence order was never reordered;
  the `pyyaml>=6.0` rationale was missing from §4.2; and `authority_grant` still had no route into
  evidence. A later edit then deleted §4.2's `environments` paragraph believing it a duplicate,
  when it was the only copy. All five are restored, and the tooling now writes after each edit so
  a later failure cannot undo an earlier success.
- **BREAKING: `context(environment=...)` is removed; `Control` takes `environment=` instead.**
  v0.3 makes the environment an authorization input — a grant may scope to
  `environments: ["staging"]` — and a dimension the subject sets is not one. On the decorator path
  the value came from the agent's own call site, so a grant scoped to staging was a real
  restriction through the gateway and decoration in-process.

  It is now set once per `Control`, from its `environment=` argument, else
  `$CTRLRUN_ENVIRONMENT`, else the policy document's top-level `environment:`, else
  `"production"`. The gateway's `--environment` and the ACS hook's configuration are unchanged —
  they were already this rule. A deployment that ran several environments from one process runs
  one `Control` each. `SPEC-v0.1.md` §8's frozen signature is amended in the same change, as the
  rule for a frozen name requires.

- **`docs/SPEC-v0.3.md` amended against an independent review of §4 and §5.** The contract was
  read by two reviewers who had not written it, and they found two authorization holes the
  author's own pass had missed.

  **An expired credential could mint permanent authority.** `Control.delegate`'s checks matched
  the creating principal on `agent` and `user`, which do not stop being equal when a token stops
  being valid — and §2.3's expiry refusal is scoped to `Control.execute`, which a delegation is
  not. A principal whose every action was being refused could still write an unexpiring,
  re-delegable grant for an agent of its choosing. §5.3 gains a rule 0.

  **The ACS hook read its principal off the wire.** `ctrlrun.acs` takes `agent_id` and
  `environment` from `params.metadata` on the inbound envelope. Under v0.2 that was survivable on
  the argument `v0.2 §6.5` makes for `clientInfo`; §4 ends it, and §8.1 removes
  `--principal-from-client-info` over exactly this. Removing the flag while the same pattern
  lived in another module would have made the removal a gesture. §8.4 gives the hook an
  `IdentityProvider` and refuses to construct one without it against a `Control` holding an
  `Authority`.

  Also: an unreadable stored delegation is now `authority_unreadable` and denies outright rather
  than being skipped in favour of a broader grant that happens to match; `Control.resume` joins
  the list of call sites that must use the combined decision, and §5.6.1 says what authority does
  across a lease extension, a commit and a resume; a `delegable` grant must declare `expires_at`,
  which is the only thing bounding the grantee population a compromised holder can reach; and
  `pyyaml>=6.0` becomes a floor, because PyYAML 5 returns naive datetimes and would reject the
  specification's own example.

### Notes on what the contract decides

Recorded here because each is a decision a reader of the code would otherwise have to
reconstruct:

- **Authority is opt-in as a section and fail-closed once present.** A file with no
  `authority:` key behaves exactly as v0.2. With one, every principal needs a grant; no grant
  means DENY.
- **Claims are receipt data, not action identity.** The canonical form of an Action is
  unchanged, so an approval survives a token rotation.
- **Omission never means unlimited.** A delegated grant that drops a dimension its parent
  constrains is rejected, not treated as unconstrained.
- **Observe mode is top-level only.** A partially-enforced configuration is the failure mode
  that rule exists to prevent.
- **Policy still cannot see the principal, and a grant carries no decision.** Authority is a
  second, independent axis that permits or denies; the two results combine as the stricter of
  the pair. How much autonomy an action has stays the same for every principal.
- **A delegation may change who acts, not how many.** Its subject must name a concrete agent —
  present, no wildcard — and a user its parent's pattern admits wherever the parent names one.
  Otherwise one delegation hands the grant to every agent, or strips the human it was bound to,
  and omitting the key reaches the same place as an asterisk.
- **Revocation and expiry are live; an edit to the document is not.** Grants are read when the
  document is loaded. `ctrlrun revoke` is the runtime kill switch and it covers delegations
  only; there is no hot reload and no runtime revoke for a root grant.

### Deprecated

- Nothing new. `--principal-from-client-info`, deprecated in 0.2.0, is **removed** in 0.3.0 by
  build-list item 1; SPEC-v0.3 §8.1 has the replacement.

### Fixed

- **`ruff format` reformatted Python code blocks inside the specs; `ruff check` never looked
  at them.** Whether a spec's examples were rewritten therefore turned on whether they happened
  to parse: `SPEC-v0.1.md` and `SPEC-v0.2.md` keep their aligned comments only because theirs
  carry placeholders like `<generated>`, and `SPEC-v0.3.md`'s parse, so a CI check that had
  never fired before went red on them and the alignment was flattened to make it green.

  A specification's code blocks are illustration rather than code — they elide, they annotate,
  they line comments up so a reader can compare down the column — and the formatter has no way
  to know that. `[tool.ruff.format] exclude` now tells it, `force-exclude` makes the exclusion
  mean the same thing however ruff is invoked, and SPEC-v0.3's alignment is restored.
  `test_the_formatter_leaves_markdown_alone` keeps it that way, with a control test that fails
  if the probe it relies on is not actually unformatted.

- **The gateway compared mirrored header values by re-parsing them, and Python's parser is
  lenient.** `Mcp-Param-{Name}` validation ran the header through `int()`, so a body carrying
  `amount: 2000` was certified as agreeing with headers spelling it `2_000`, `+2000`, `02000`,
  ` 2000`, `2000 `, and the Arabic-indic and fullwidth digit forms; booleans were matched
  case-insensitively, so `TRUE`, `True` and `tRuE` all agreed with `true`. None of those is
  what a JSON serializer writes, and each is read differently — or refused — by another
  parser: `parseInt("2_000")` is 2 in JavaScript and `strconv.Atoi` errors in Go.

  That is the exact hazard SPEC-v0.2 §6.4 exists to prevent. The gateway's job there is to
  certify that a routing intermediary and CTRLRun are looking at the same value, and it was
  certifying agreement that held only under Python's rules. CTRLRun's own decisions were never
  affected — the action is built from the body, and the headers are only checked — so this
  costs an intermediary's correctness rather than an approval binding.

  A header value must now be the body value's canonical rendering, compared as text, with no
  parser in the comparison — and only a string, an integer or a boolean has one. The revision
  permits `x-mcp-header` on those three types alone and omits the header for a `null`, so a
  header naming an argument of any other type is refused rather than compared against a
  rendering CTRLRun invented for it. This also declines the revision's SHOULD that servers compare
  integers numerically (`42.0` equals `42`): v0.1 §2.3 refuses a float in the body outright, so
  the leniency has no legitimate case here. SPEC-v0.2 §6.4 states both rules and the reasoning.

## [0.2.0] - 2026-09-03

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
  `docs/docs/ACS.md` for the mapping and for the four places ACS is silent. **No compliance
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

### Deprecated

- **`ctrlrun gateway --principal-from-client-info` — removed in 0.3. Use
  `--principal-header`.** It takes the agent's name from `_meta["io.modelcontextprotocol/
  clientInfo"]`, which the MCP revision says is self-reported and *"SHOULD NOT"* be relied on
  for security decisions. It is offerable in 0.2 only because of a fact that stops being true:
  a v0.1 policy cannot address the principal at all (`SPEC-v0.1.md` §3.2 refuses `agent_eq`
  and every other reserved name at load), so an unauthenticated principal misattributes
  evidence and cannot widen an outcome. v0.3's authority model makes the principal an
  authorization input, at which point a self-reported name cannot be one. The flag warns at
  startup and its `--help` says so.

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
- `SECURITY.md` and `docs/docs/CLAIMS.md`, which maps every README claim to its code and test.

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
- Receipts are not signed. A database administrator can alter history. **This line read "(v0.6)" until v0.6 was built, and that was a promise v0.6 does not keep**: v0.6 adds a hash chain, which detects alteration and is not evidence of authorship, and it does not stop an administrator who can rewrite every row including the chain head. Signing is out of scope (`SPEC-v0.6.md` §11).
- Approver identity is free text and is not authenticated (v0.3).
- Generated ids (`act_`, `apr_`, `ctr_`) are 128 bits. An approval id is not a bearer token
  in v0.1 — consuming one needs write access to the store — but it becomes one with the
  webhook provider in v0.2, and an id format cannot be widened after records exist.
- Effect key templates do not escape placeholder values, so a crafted argument can make two
  distinct effects share one key. The result is a refusal rather than a double execution;
  `docs/docs/THREAT_MODEL.md` states the limit and the workaround.
- Policy conditions address an action's arguments only. Scoping a rule by environment,
  resource or principal arrives with the authority model in v0.3.

[0.6.0]: https://github.com/CTRLRun/ctrlrun/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/CTRLRun/ctrlrun/releases/tag/v0.5.0
[0.4.0]: https://github.com/CTRLRun/ctrlrun/releases/tag/v0.4.0
[0.2.0]: https://github.com/CTRLRun/ctrlrun/releases/tag/v0.2.0
[0.1.0]: https://github.com/CTRLRun/ctrlrun/releases/tag/v0.1.0
