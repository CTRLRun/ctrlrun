# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Public API names are frozen in `docs/SPEC-v0.1.md` §8. Before 1.0 they may still change, and
any change to one appears here.

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

- **The GitHub Action, the badge and `docs/verify.md`** (SPEC-v0.4 §5). `action.yml` at the
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
  least privilege; `docs/verify.md` shows the one-job publishing pattern once, with its cost
  visible. Rendered, it reads exactly `CTRLRun verified N/M`, where `M` is **applicable**
  guarantees and never the catalogue size. A partial run and a run that exited 2 or 3 write no
  badge at all.

  The badge means **"declared guarantees pass"** — that phrase, on the badge's link target, and
  no other. Not secure, not safe, not compliant, not certified, not audited.
  `docs/verify.md#what-the-badge-means` says it in its first sentence and, on the same screen,
  what verify cannot see: the operator's executors, their `reconcile` hooks, where they put the
  decorator, their deployment, and whether the policy is the right policy.

  This repository's CI runs the action against `examples/authority/payments.yaml` (10/10) and
  against `examples/policies/payments.yaml` (5/5, 5 not applicable), asserting **both shapes** —
  so a change that made verify silently count N/As as passes is caught in CI rather than in a
  badge.

- **`docs/OWASP-AGENTIC-TOP10.md`** (SPEC-v0.4 §6) — a reading of the OWASP Top 10 for Agentic
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

- **`docs/SPEC-v0.4.md` gains a §12**, recording the four readings the implementation had to
  take where the specification could not be satisfied as written. A specification that
  disagrees with the code it describes is worse than one that admits a gap: G6 drives a
  `Control` composed from the policy alone, because authority is evaluated first and its
  observable would otherwise be unreachable in every configuration with grants; G7 is `N/A`
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
- `docs/ARCHITECTURE.md` §6's module map gains `verify/`, above `control.py` and beside `cli/`.

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
- **`docs/authority.md`** — grants, delegation and the omission rule in plain language,
  including the two things it is worth knowing before you need them: an `Authority` is built at
  load time and is not hot-reloaded, and there is no way to list delegations, so cutting a
  chain of unknown width means `delegable: false` on the root and a restart.
- **`docs/THREAT_MODEL.md` gains v0.3's boundary.** In scope: delegation escalation, omission
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
  what a provider reads. `docs/ACS.md`'s mapping table is amended in the same change.
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

## [0.2.0] — unreleased

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
  `docs/ACS.md` for the mapping and for the four places ACS is silent. **No compliance
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
- `SECURITY.md` and `docs/CLAIMS.md`, which maps every README claim to its code and test.

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
- Receipts are not signed. A database administrator can alter history (v0.6).
- Approver identity is free text and is not authenticated (v0.3).
- Generated ids (`act_`, `apr_`, `ctr_`) are 128 bits. An approval id is not a bearer token
  in v0.1 — consuming one needs write access to the store — but it becomes one with the
  webhook provider in v0.2, and an id format cannot be widened after records exist.
- Effect key templates do not escape placeholder values, so a crafted argument can make two
  distinct effects share one key. The result is a refusal rather than a double execution;
  `docs/THREAT_MODEL.md` states the limit and the workaround.
- Policy conditions address an action's arguments only. Scoping a rule by environment,
  resource or principal arrives with the authority model in v0.3.

[Unreleased]: https://github.com/CTRLRun/ctrlrun/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/CTRLRun/ctrlrun/releases/tag/v0.4.0
[0.1.0]: https://github.com/CTRLRun/ctrlrun/releases/tag/v0.1.0
