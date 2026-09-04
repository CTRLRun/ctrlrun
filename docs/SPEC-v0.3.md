# CTRLRun v0.3 Specification

This is a **delta over [`SPEC-v0.1.md`](SPEC-v0.1.md) and [`SPEC-v0.2.md`](SPEC-v0.2.md)**.
Everything in both still holds; this document states only what v0.3 adds or changes. A
reference to an earlier contract is written `v0.1 §5.4` or `v0.2 §6.5`; a bare `§5` is a
section of this document. Section numbers exist in all three, so the prefix is not decoration
— an unprefixed reference to an earlier spec is a defect.

Tests are derived from §10. Public names added here are frozen in §11. Anything not in this
document or in v0.1/v0.2 is out of scope for v0.3.

Words: MUST / MUST NOT / SHOULD are used in the RFC 2119 sense.

v0.3 answers the question v0.1 and v0.2 could not: **who is acting, and what are they entitled
to?** Until now a policy could see the action and nothing else — `v0.1 §3.2` refuses
`agent_eq` and every other principal-addressing condition at load, deliberately, and that rule
survives this release unchanged (§4.7). The principal was attribution on a receipt. It becomes
an authorization input here, in a second and separate axis that policy never learns to read.

Three rules govern everything below.

**Authority is opt-in, and then fail-closed.** A configuration with no `authority:` section
behaves exactly as v0.2 did. The moment an `authority:` section exists, every principal needs
a grant and every action must pass *both* authority and policy. There is no half-way, no
`default: allow`, and no flag that restores one.

**Attenuation is structural.** A delegated grant is valid only if it is provably a subset of
its parent — at the moment it is created *and* again every time it is evaluated. Omitting a
dimension the parent constrains is not "unconstrained"; it is a rejected delegation (§5.4).

**Identity is consumed, never invented.** Principals come from an `IdentityProvider`. CTRLRun
verifies tokens it is handed and maps verified claims onto a `Principal`. It defines no
identity format, issues no credential, and runs no authorization server.

---

## 1. Scope

v0.3 delivers seven things, one build-list item each, plus a release. The `#` column is the
build-list position.

| # | Deliverable | Ships in | Section |
|---|---|---|---|
| 1 | Extended `Principal`, `IdentityProvider`, static and header providers | core | §2, §3 |
| 2 | The `authority:` section: `Grant`, `Authority`, evaluation, the AND with policy | core | §4 |
| 3 | Delegation with attenuation, revocation, the `delegations` table | core | §5 |
| 4 | Observe mode and `ctrlrun stats` | core | §6 |
| 5 | `JWTIdentityProvider`; gateway identity and authority wiring | `ctrlrun[identity]` | §3.4, §8 |
| 6 | Demo scenario 5, `examples/authority*`, `docs/authority.md` | core | §1.2 |
| 7 | Release 0.3.0 | — | — |

The dependency rule of `v0.2 §1.1` is unchanged and binding: `pip install ctrlrun` MUST
continue to install nothing but `pyyaml` and `click`. The whole authority model — grants,
delegation, containment, observe mode — is core, because it is stdlib plus the YAML parser
already present. Only token verification needs a third party, so only `JWTIdentityProvider`
lives in an extra (`ctrlrun[identity]`), imported lazily, raising `MissingDependency` naming
the install command when absent. `import ctrlrun` MUST NOT import `jwt`, `httpx` or any
`opentelemetry` module. T30 already asserts this against `sys.modules`; §10's T92 extends it.

### 1.1 What this release opts into, and what it refuses to claim

v0.3 consumes identities that other systems issue. It is worth being exact about which, because
"aligns with" is the kind of sentence that ages into a lie.

**What CTRLRun consumes.**

- A **JSON Web Token** (RFC 7519) presented in an HTTP header, verified against a **JWKS**
  (RFC 7517) or a static public key, with `exp`, `nbf`, `aud` and `iss` checked, and with
  configurable claim names mapped onto `Principal.agent` and `Principal.user` (§3.4). This is
  the only credential format v0.3 verifies.
- Anything that has already been verified by something else and handed over in a trusted
  header, via `HeaderIdentityProvider` — with the threat that implies stated in full (§3.3).

**What CTRLRun does not do.** It issues no token, mints no identity, runs no authorization
server, performs no OAuth flow, and defines no new identity format or claim name. There is no
`ctrlrun` claim, no CTRLRun identity document, and no registry of agent identifiers. A
`Principal` is a *reading* of somebody else's credential.

**What CTRLRun does not claim.** No conformance, compliance, certification or alignment with
any standard, in this document, in the README, in a docstring, or in CLI output. `ROADMAP.md`'s
standards rule holds: *integrate first, map second, never claim compliance* — a standard
appears in a mapping doc only after code touches it and a test proves the guarantee. §1.3
records what was read while writing this, so the reading is auditable and its limits visible.

### 1.2 Examples, demo and documentation (item 6)

- A fifth `ctrlrun demo` scenario, **authority escalation**: the chain of §5.6, in-process, no
  network, printing a fifth `BLOCKED` line.
- `examples/authority-escalation/` — a standalone script telling the same story, with the
  `else: raise SystemExit(...)` guard every example carries: a demonstration that quietly
  starts succeeding is worse than none, because it keeps printing the line that says the guard
  worked.
- `examples/authority/` — two configurations: a payments delegation chain and a DevOps chain.
- `docs/authority.md` — grants, delegation and the omission rule (§5.4) in plain language.
- `docs/THREAT_MODEL.md` gains v0.3's boundary: delegation escalation, expired and revoked
  authority, and token forgery are in scope; a compromised identity provider is not.

The nine sector templates under `examples/policies/` stay on v0.1 primitives, declare
`schema: ctrlrun.policy/v1`, and say so on their face. They gain no `authority:` section: a
grant names real principals in a real organization, and a template that ships plausible ones
invites an operator to adopt them.

### 1.3 What was read

Read on **2026-09-04** unless a document states otherwise. Dates are the dates the documents
themselves carry.

#### NIST — the questions, not the answers

NIST has published **nothing normative on agent identity**. The one document dedicated to the
subject is a concept paper: *Accelerating the Adoption of Software and AI Agent Identity and
Authorization*, NCCoE, **Initial Public Draft, published 2026-02-05**, comments closed
2026-04-02 (<https://csrc.nist.gov/pubs/other/2026/02/05/accelerating-the-adoption-of-software-and-ai-agent/ipd>).
It contains no RFC 2119 keyword anywhere in its eleven pages, and its Note to Reviewers asks,
among others, *"What are the mechanisms for an agent to prove its authority to perform a
specific action?"* and *"How do we handle delegation of authority for 'on behalf of'
scenarios?"* — the questions this specification answers, still open on NIST's side. Its stated deliverable is a practice
guide, not a standard. It names MCP, OAuth 2.0/2.1, OpenID Connect, SPIFFE/SPIRE, SCIM and NGAC
as *candidates*, profiles none, and requires none.

The adjacent finals do not fill the gap. **SP 800-63-4** (final, 2025-07-31,
<https://csrc.nist.gov/pubs/sp/800/63/4/final>) is digital identity for natural persons.
**SP 800-207** (Zero Trust Architecture, August 2020,
<https://csrc.nist.gov/pubs/sp/800/207/final>) has a subject model but nothing on machine
delegation. **NIST IR 8587**, *Protecting Tokens and Assertions from Forgery, Theft, and Misuse*,
is an Initial Public Draft (published 2025-12-22,
<https://csrc.nist.gov/pubs/ir/8587/ipd>), and its verifier obligations informed §3.4 without
being citable as a requirement. Its §4.2.1.2 is the one NIST sentence that addresses a
component in CTRLRun's position — *"Policy enforcement points (e.g., at the application level)
that rely on access tokens and identity assertions MUST confirm the validity, scope, source,
and integrity of access tokens before granting access to resources"* — and it is draft
guidance, aimed at agencies and cloud service providers, that mentions neither AI agents nor
delegation. A NIST blog post of **2026-08-27**
(<https://www.nist.gov/blogs/cybersecurity-insights/back-future-why-agentic-ai-needs-strong-identity-foundation>)
observes that *"other specifications, such as Transaction Tokens, are looking at ways to
promulgate authorization context across human and agentic call chains to help ensure that
authorization is attenuated as it's delegated"* — a description of IETF activity, not a
requirement.

**What v0.3 takes from this:** the vocabulary, and the confirmation that the shape of the
problem is not yet settled by anyone. Nothing to align to, so nothing is claimed. `Principal`
does not become a NIST anything.

#### MCP — audience-bound tokens, and a warning label on the only fields that look like identity

Read at revision **2026-07-28** (<https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization>
and its `security-considerations` page), which is the revision `SPEC-v0.2` is written against.

MCP defines **no** principal, actor, delegation, attenuation or authority primitive. Its
authorization section makes the MCP server an OAuth 2.1 **resource server** and binds it with
four MUSTs a verifier inherits: validate the access token; validate that it was *issued for this
server as the audience*, per RFC 8707 §2; accept only tokens valid for its own resources; and
**never pass through the token it received** to an upstream API. Discovery is RFC 9728 protected
resource metadata (April 2025). MCP pins OAuth 2.1 `draft-ietf-oauth-v2-1-13` on that page —
inconsistently: its Refresh Tokens section links `-14`. The live document was
`draft-ietf-oauth-v2-1-16`, dated 2026-09-03, when this was read, so "OAuth 2.1 draft-N" is
stale the moment it is written down; every citation of it here carries a revision *and* a
date.

The two fields that look like identity are both dead ends, and the specification says so on
its reserved-`_meta` page
(<https://modelcontextprotocol.io/specification/2026-07-28/basic/index>):
`io.modelcontextprotocol/clientInfo` and `serverInfo` are *"self-reported by the sender and are
not verified by the protocol… Implementations SHOULD NOT use them to change the behavior of the
client or server, and SHOULD NOT rely on them for security decisions."* That sentence is why
`--principal-from-client-info` is removed here (§8.1). And there is no machine-readable
"tool X requires scope Y" anywhere: scopes are opaque strings challenged through
`WWW-Authenticate`, clients *"MUST NOT assume any particular set relationship"* between the
challenged scopes and `scopes_supported`, and the step-up flow computes the **union** of previous
and challenged scopes — the opposite of attenuation.

**What v0.3 takes from this:** nothing it can map an authority model onto. MCP supplies
audience-bound bearer tokens and a transport. Per-action authority is CTRLRun's own ground, which
is exactly why the gateway carries a policy and an `authority:` file rather than deriving
entitlements from the protocol.

#### OAuth and IETF — stable ground for verification, drafts for everything agent-shaped

Stable, and what §3.4 is built on: **RFC 7519** (JWT), **RFC 7517** (JWK/JWKS), **RFC 8725**
(BCP 225, JWT Best Current Practices — the algorithm-confusion and cross-JWT-confusion classes
§3.4 refuses), **RFC 9068** (JWT profile for OAuth access tokens, October 2021), **RFC 9700**
(BCP 240, Best Current Practice for OAuth 2.0 Security, 2025 — the current baseline for
audience restriction and bearer-token handling, and the reason this list is not the 2020-era
one), **RFC 8707** (Resource Indicators, February 2020), **RFC 9728** (Protected Resource
Metadata, April 2025). Also stable and deliberately *not* used: **RFC 8693** (Token Exchange,
January 2020), **RFC 9449** (DPoP, September 2023) and **RFC 8705** (mTLS-bound tokens,
February 2020) — all three are things an issuer or a client does, and v0.3 is neither.

RFC 8693 is worth one more sentence, because it is the RFC someone will expect §5 to be built
on. Its §4.1 requires a consumer to *"only consider the token's top-level claims and the party
identified as the current actor by the `act` claim"*, with nested prior actors informational.
CTRLRun's delegation chain is therefore **not** carried in a token and not read from one: it
lives in CTRLRun's own store (§5.2), and every link is re-checked there (§5.6). A library that
claimed to verify a delegation chain out of an `act` claim would be non-conformant to the one
Final RFC it would be citing.

Everything specific to *agent* identity is a draft, and most of it has no IETF standing at all.
`draft-ietf-oauth-transaction-tokens-11` (2026-07-30, OAuth WG, "waiting for write-up") and
`draft-ietf-oauth-identity-chaining` (rev 17, 2026-07-19, in the RFC Editor queue when read —
a state that acquires an RFC number without warning, so treat "there is no RFC" as an
observation dated 2026-09-04) are working-group documents;
`draft-ietf-oauth-identity-assertion-authz-grant-04` (2026-05-21, expires 2026-11-22) is the
ID-JAG that MCP's enterprise-managed-authorization extension builds on. Beyond those,
`draft-klrc-aiagent-auth-03` (2026-07-06),
`draft-niyikiza-oauth-attenuating-agent-tokens-01` (2026-06-15, expires 2026-12-17),
`draft-mcguinness-oauth-actor-profile-00` (2026-04-30) and
`draft-mishra-oauth-agent-grants-02` (2026-08-30) are **individual submissions**: no stream, no
working group, no intended RFC status. An `agentproto` BOF exists with group state `bof` and no
charter. OpenID's **Authorization API 1.0** reached Final
(2026-01-11, <https://openid.net/specs/authorization-api-1_0.html>); its Access Request and
Approval Profile (ARAP) is an AuthZEN **Working Group Draft**, retrieved 2026-09-04 — its title
page carries a build date that moves, so it is cited by stage and retrieval date rather than by
a version.

**What v0.3 takes from this:** JWT verification, and nothing else. A library that put a
six-month-old individual draft in its wire format would be shipping somebody's unreviewed idea to
production. `JWTIdentityProvider` verifies RFC 7519 tokens against RFC 7517 keys; the `authority:`
section is CTRLRun's own YAML, deliberately, and does not pretend to be a token profile.

#### SPIFFE — identity, and explicitly not authorization

Read from the `spiffe/spiffe` standards directory. **These documents carry no version numbers
and no dates** — each opens with a stability banner and nothing more — so the only durable
identifier is a commit, and this read is pinned at `JWT-SVID.md` and `X509-SVID.md` at commit
`21896ac` (2026-07-01), and `SPIFFE-ID.md` and `SPIFFE_Workload_API.md` at `665a28f`
(2026-07-01). All four are marked Stable. A SPIFFE ID is `spiffe://trust-domain/path`, an RFC 3986 URI. A JWT-SVID
(<https://github.com/spiffe/spiffe/blob/main/standards/JWT-SVID.md>) requires `sub` (the SPIFFE
ID), `aud` and `exp`; restricts `alg` to nine asymmetric values and requires validators to reject
anything else; and *"does not introduce any new claims."*

It carries **no scope, role, group, permission or entitlement claim**. Custom claims are
permitted, and SPIRE can mint them — but `SPIFFE-ID.md` §4.1.1 warns that role and access-policy
assertions are *"more likely to change between the time of SVID issuance and the time of
validation"* and that operators should *"err on the side of caution and exclude"* volatile
assertions; its §4.1.2 warns that the same attribute name means different things in different
trust domains; and `SPIFFE_Workload_API.md` §6.2.3 says a `ValidateJWTSVID` implementation *"MAY filter
non-SPIFFE claims"* before a consumer ever sees them. A grant that depended on a SPIFFE custom claim could be silently stripped
in transit.

**What v0.3 takes from this:** confirmation of the split this specification is built on. A
JWT-SVID is a fine `Principal` source — it is a signed JWT with `sub`, `aud` and `exp`, which is
exactly what §3.4 verifies — and it is not, and does not try to be, a statement of entitlement.
That is the `authority:` section's job. v0.3 ships no SPIFFE-specific code; it needs none.

#### Attenuated delegation — where §5 sits in the prior art

Six families were read: macaroons (Birgisson, Politz, Erlingsson, Taly, Vrable and Lentczner,
NDSS, February 2014, <https://theory.stanford.edu/~ataly/Papers/macaroons.pdf>); Biscuit (Eclipse project, Incubating,
creation review 2025-02-05); UCAN 1.0.0 (<https://github.com/ucan-wg/spec>, self-declared 1.0.0,
no dated release); ZCAP-LD (W3C CCG **Community Group draft** v0.4.0-draft, not Recommendation
track); SPKI/SDSI, **RFC 2693**, September 1999, **Experimental**; and
`draft-niyikiza-oauth-attenuating-agent-tokens-01`, an individual submission with no IETF
standing. None is a standard CTRLRun could implement; every one is a source of design pressure.

They split on one question — *does anything ever compare a child grant to its parent?*

| Family | Compared at creation | Compared at evaluation | Mechanism |
|---|---|---|---|
| Macaroons | no | no | HMAC chain; caveats are append-only, conjunction at the target |
| Biscuit | no | no | append-only signed blocks with block scoping |
| UCAN 1.0.0 | normatively required, **not verified** | conjunction, time intersection, command prefix | policy array |
| ZCAP-LD | yes | yes | field-by-field "not less restrictive" |
| SPKI/SDSI (RFC 2693) | yes | yes | 5-tuple reduction, delegation bit |
| `draft-niyikiza-…-01` | yes | yes | typed subsumption per link |

§5's rule — provably contained at creation **and** re-checked at evaluation — sits with
ZCAP-LD, SPKI and that draft. It is a **different** guarantee from the one macaroons and UCAN
give, not a strictly stronger one: they make widening unrepresentable, which is stronger, by
requiring a token that can only ever have restrictions appended; CTRLRun compares two grants
instead, which is weaker against a forged record and stronger against a *changing parent*. That
is the trade this codebase is forced into. A grant here is a YAML document an operator edits and
a delegation is a row in a store, so there is no signature chain to make widening
unrepresentable — and the parent can narrow after the child exists, which only a comparison can
catch (§5.6). The confused-deputy literature (Hardy, 1988; Miller, Yee and Shapiro, *Capability
Myths Demolished*, 2003) is where the rule that a delegation names its own limits rather than
inheriting them comes from.

Two of §5's dimensions have essentially no prior art: **environment containment** — macaroons
defer to application-defined caveats, UCAN policy sees only invocation arguments, ZCAP-LD has no
environment field — and **numeric containment**, whose nearest standards-adjacent expression is
RFC 2693's `(* range …)`, Experimental and from 1999. Those two are stated here as CTRLRun's own
invention, not as an implementation of anything.


---

## 2. Principal

### 2.1 Model

`v0.1 §2.1`'s `Principal` grows three optional fields. Nothing is removed and nothing is
renamed.

```python
_NO_CLAIMS: Final = MappingProxyType({})             # shared; a dataclass default must be immutable

@dataclass(frozen=True)
class Principal:
    agent: str                                       # required, e.g. "refund-agent"
    user: str | None = None                          # human on whose behalf, if any
    claims: Mapping[str, str | int | bool] = _NO_CLAIMS   # verified, from the provider (§3)
    issuer: str | None = None                        # who verified it, e.g. a JWT `iss`
    expires_at: datetime | None = None               # when the credential stops being valid
```

`Principal` stays `frozen=True` and stays **unhashable**, as it is today: `Mapping` is not
hashable, and a dataclass whose default were a bare `{}` would not even reach class definition.
Nothing in v0.1 or v0.2 hashes a `Principal` — `Action.__hash__` is `hash(action_id)` (`v0.1
§2.1`) — so this costs nothing; it is stated because a reader of the dataclass will wonder.

`agent` and `user` keep every rule of `v0.1 §2.1`: non-empty, `user` either `None` or
non-empty, empty string → `InvalidArgument`.

**`claims`** is what the identity provider verified and chose to carry. It is snapshotted at
construction into a read-only mapping, exactly as `Action.arguments` is (`v0.1 §2.2`), so a
caller holding the original object cannot change a constructed `Principal`. Keys MUST be
non-empty `str`. Values MUST be `str`, `int` or `bool` — **no `float`**, for the reason of
`v0.1 §2.3`; no `None`; no lists or mappings. A claim whose value is a structure is flattened
or dropped by the provider that read it, not stored here. Anything else → `InvalidArgument`.
`bool` is not `int` (`v0.1 §3.2`): `True` and `1` are different claim values.

**`issuer`** is `None` or a non-empty `str` — the provider's own name for whoever it verified
against, so a receipt records not just who was acting but who said so.

**`expires_at`** is `None` or a timezone-aware `datetime`. A naive `datetime` →
`InvalidArgument`: a credential whose expiry is in an unstated timezone is a credential whose
expiry cannot be checked, and guessing UTC would silently extend or shorten it. `None` means
the provider stated no expiry, which is not the same as "does not expire" — it is "this
provider does not know", and §2.3 says what follows.

### 2.2 Claims are not part of the action hash

**The canonical form of an Action is unchanged.** It remains exactly what `v0.1 §2.2` defines,
including `"principal": {"agent": ..., "user": ...}` and nothing more, under the same schema
tag `ctrlrun.action/v1`. `claims`, `issuer` and `expires_at` MUST NOT appear in it, at any
depth, under any key.

This is not an oversight to be tidied up later. An approval binds to an action hash (`v0.1
§4.2 A1`), and a hash that included claims would change when a token rotates — a new `jti`, a
refreshed `exp`, a claim the identity provider started emitting last Tuesday. The human
approved a €2,000 refund by `refund-agent` on behalf of `alice`; they did not approve a token
serial number. Putting claims in the hash would invalidate that approval for a reason no human
could see and no agent could fix, and the agent's correct response — propose it again with the
new token — would be indistinguishable from the mutation attack `v0.1 §4.2` exists to catch.

Claims are **receipt data** (§2.4) and **evidence**. They are not action identity. Any change
to this needs an explicit schema version bump on the canonical form (`v0.1 §2.2`) and a test
proving hashes recorded under the old one still verify.

The same reasoning applies to authority: §4 evaluates a grant against the principal's `agent`
and `user`, which are in the hash, and never against a claim (§4.2). An authority decision that
depended on a rotating claim would move under a pending approval.

### 2.3 An expired principal is refused before anything else

If `principal.expires_at` is not `None` and `now > principal.expires_at`, the action is
refused. Specifically, `Control.execute` MUST, before it evaluates authority and before it
evaluates policy:

- append `ACTION_PROPOSED`, then `ACTION_DENIED` with `data.reason = "principal_expired"`;
- write a receipt with `result: "denied"`, `decision: "deny"`,
  `decision_reason: "principal_expired"`;
- append **no** `POLICY_EVALUATED`, **no** `AUTHORITY_RESOLVED`, and create **no** approval
  request;
- raise `IdentityError`.

There is a principal here — an expired one is still a name to attribute the refusal to — so
unlike a call outside `context()` (`v0.1 §2.1`) this belongs in the evidence log. And it is
refused *before* the approval gate for the same reason authority is (§4.3): a decision that can
never be honoured must not spend a human's attention.

`expires_at is None` is not an expiry check that passes; it is the absence of one. A deployment
that requires every principal to carry an expiry enforces that in its provider, which is the
component that knows what it verified.

`IdentityError`, not `ActionDenied`: an agent loop's `except ActionDenied` is written to handle
a policy saying no, and a credential that stopped being valid is not that. This is the same
distinction `v0.1 §5.1` draws for `EffectKeyError`.

**In observe mode this check is evaluated and recorded, not enforced** (§6.2). §6.3 says what
the receipt then holds.

### 2.3.1 An expiry that falls inside an action already under way

`expires_at` is checked **once**, where §2.3 says, and the answer holds for the rest of that
attempt. Three moments could re-ask it, and each is decided here rather than left to an
implementer:

| Moment | Expired principal | Why |
|---|---|---|
| Committing, failing or marking ambiguous an outcome | **not refused** | The effect has already happened at the remote. Refusing the write would strand a real-world effect in `AMBIGUOUS` for a clock reason, which is the one mistake `v0.1 §5.5` exists to prevent |
| Extending a lease (`v0.2 §6.9.4`) | **refused** | An extension is a request to keep holding authority the credential no longer carries. Refusing lets the lease lapse, and a lapsed lease is `AMBIGUOUS` (`v0.1 §5.3 E3`) — the safe state, reached by the ordinary path |
| `Control.resume` (`v0.2 §6.9`) | **evaluated and recorded, not re-decided** | Exactly what `v0.2 §6.9.2` chose for policy on the same leg, for the same reason: the reservation is held and the remote may already be acting on it |

A refused lease extension appends `ACTION_DENIED` with `data.reason = "principal_expired"` and
raises `IdentityError`; it does not move the effect record, which the expiring lease will do on
its own. The resumed leg records the expiry in its receipt (`decision_reason` stays whatever the
resumption reached) and appends `ACTION_DENIED` for nothing — there is nothing to deny.

### 2.4 Where claims appear

- **Receipts.** `principal` gains `claims`, `issuer` and `expires_at` (§12).
- **The stored Action.** `StateStore`'s serialized Action — the one `Control.resume` rehydrates
  a continuation from, and the one an approval record carries — MUST carry the **full**
  principal, not just `{agent, user}`. Without this, every receipt written by `Control.resume`
  reports `claims: {}`, `issuer: null`, `expires_at: null`, and §2.1's load-bearing distinction
  between "the provider stated no expiry" and "the store dropped it" is silently lost — on the
  only receipt an MCP multi-round-trip or ACS action ever gets. This is a **value** change
  inside an existing `TEXT` column, not a schema migration: rows written by 0.2 carry the two
  old keys and parse back with `v0.1 §2.1`'s defaults.
- **Events.** No event carries claims. Events are a timeline; the receipt is the record.
- **`ctrlrun inspect`.** The header block prints the issuer and expiry, and the claim names —
  the *names*, sorted, not the values. Values reach `--json`.
- **The OTel sink.** Claim values are withheld exactly as argument values are (`v0.2 §8`):
  span attributes carry `ctrlrun.principal.issuer` and the sorted claim names, and nothing
  else, unless the operator asked for arguments. They stay in CTRLRun's own `ctrlrun.*`
  namespace rather than adopting OpenTelemetry's `user.*` registry, and the reason is better
  than preference: every attribute in that registry (`user.id`, `user.name`, `user.email`,
  `user.roles`, …) is marked **Development** stability, so adopting it would tie a receipt-
  adjacent export to names that may break.

A claim can hold an employee number, a case identifier, a licence, a customer id. Receipts are
evidence and are meant to be read; spans are exported to a third party by default. The two
places make opposite trade-offs deliberately.

The `ctrlrun inspect` and OTel changes above ship with **build-list item 1**, alongside the
`Principal` fields they display; `ctrlrun.inspection/v1` becomes `v2` there (§12.2).

---

## 3. `IdentityProvider`

### 3.1 The protocol

```python
@dataclass(frozen=True)
class IdentityContext:
    action: str                        # the action name about to be proposed
    environment: str
    headers: Mapping[str, str] = {}    # lowercased names; empty outside the gateway
    agent: str | None = None           # what context() supplied, if anything — a hint
    user: str | None = None


class IdentityProvider(Protocol):
    def resolve(self, context: IdentityContext) -> Principal | None: ...


Control(..., identity: IdentityProvider | None = None)
```

`resolve` is called once per action, before the `Action` is constructed — it has to be, since
an Action cannot exist without a principal (`v0.1 §2.1`).

**Where it runs, and where it does not.** There are exactly two places that build an `Action`,
and they are the two that resolve: `@protect`'s wrapper, and the gateway. `Control.execute` and
`Control.evaluate` take an already-constructed `Action` (`v0.1 §8`, unchanged), so a caller that
builds an `Action` by hand and passes it to `Control.execute` supplies its own `Principal` and
the provider never runs.

That is a real limit and it is stated rather than papered over: it is the same limit as calling
the wrapped function directly, which `docs/THREAT_MODEL.md` already records as out of scope
("Bypassing the decorator entirely"). Code inside the CTRLRun process is inside the trust
boundary. Code *outside* it — every gateway client, which is where an untrusted caller actually
is — has no such path, because the gateway builds the Action and the client never touches
`Control`. v0.3 does **not** add a re-resolution or a principal-disagreement check to
`Control.execute`: it would be a check against the wrong threat, and it would break every
existing caller that builds Actions for tests and tools.

**The environment**, when a provider answers and no `context()` is active, is `"production"` —
`v0.1 §2.1`'s default — for both `IdentityContext.environment` and the `Action`. The gateway
supplies `--environment` instead (`v0.2 §6.5`).

`IdentityContext.headers` is a mapping with **lowercased** header names, because HTTP header
names are case-insensitive and a provider that had to guess the casing would be a provider that
sometimes worked. In-process it is empty; the gateway fills it (§8).

**A repeated header is a refusal, never a collapse.** `Mapping[str, str]` holds one value per
name, so something must decide what a header appearing twice becomes — first, last, or
comma-joined — and under an authority model that decision picks the principal. It is not left
open: the gateway MUST refuse a request in which **any header its identity configuration reads**
(`--principal-header`, `--user-header`, `--identity-jwt-header`) appears more than once, before
`IdentityContext` is built — `-41007` `ctrlrun.no_principal`, HTTP 403, no receipt and no events,
and a warning naming the header. A proxy that *appends* rather than overwrites is a common
default (§3.3), and joining its value to the client's is how a client chooses its own
principal.

`agent` and `user` are what an active `context()` supplied. They are a **hint**, not an
identity: a provider is free to ignore them, and both shipped providers do. They are passed at
all so that a custom provider can implement "look up the token for this agent" without a second
channel.

### 3.2 Which wins: the provider, where it answers

An action's principal is decided as follows, and this order is not configurable.

| `identity` | `resolve()` | active `context()` | principal |
|---|---|---|---|
| not set | — | yes | the context's, exactly as v0.1 |
| not set | — | no | `no_principal` — refused (`v0.1 §2.1`) |
| set | returns a `Principal` | either | **the provider's** |
| set | returns `None` | yes, **no `authority:`** | the context's |
| set | returns `None` | yes, **`authority:` loaded** | `no_principal` — refused (see below) |
| set | returns `None` | no | `no_principal` — refused |
| set | raises | either | `IdentityError` — refused, and the context does **not** fill in |

**The provider wins where it answers.** A verified identity must not be overridable by a
statement in the code the agent influences, because §4 makes the principal an authorization
input and a self-asserted name cannot be one. This is the same sentence that removes
`--principal-from-client-info` (§8.1), applied in-process.

**`None` is a decline, not a refusal — until authority is switched on.** A provider that has
nothing to say leaves the v0.1 path intact, so installing a provider never breaks code that
already uses `context()`. A provider that *raises* has been given something and rejected it;
falling back to a self-asserted name there would turn a rejected token into a successful action,
which is the one outcome this whole section exists to prevent.

**Once an `authority:` section is loaded, a decline is not backfilled.** Omitting a credential
reaches the same destination as forging one, by an easier route: agent code already running
inside `with context("finance-agent")` that installs a `JWTIdentityProvider` would execute every
un-credentialed call as `finance-agent`, with `finance-agent`'s grants — the token becomes
optional in the release that makes the principal an authorization input. So where an
`authority:` section exists, `resolve()` returning `None` is `ActionDenied(reason="no_principal")`
whether or not a `context()` is active, and §9 carries the row.

This is not a flag and there is nothing to configure: it follows from the opt-in rule (§4.1). No
`authority:` section → v0.2 behaviour, decline included. An `authority:` section → the provider
is the only source of a principal. A deployment that wants both a provider and `context()`
principals is a deployment that has not decided who is acting, and under authority that is not a
question the library may answer on its own.

**A disagreement is a warning, not a silent substitution.** Where a provider answers and a
`context()` is also active with a different `agent` or `user`, `Control` MUST log a warning on
the `ctrlrun` logger naming both principals and which is in force — at most once per `Control`,
because a warning that repeats per call is a warning nobody reads. This is `v0.2 §3.2`'s
template-mismatch rule applied to identity.

`# SPEC:` The item-0 brief suggested that an explicitly nested `context()` should take
precedence over the provider, and asked for the choice to be documented. This document chooses
the other way, for the reason above; T65 tests every row of the table so the choice is pinned
rather than assumed.

**Exceptions.** A provider raising `IdentityError` propagates it unchanged. A provider raising
any other `Exception` is logged on the `ctrlrun` logger and re-raised as `IdentityError` with
the original chained (`raise ... from exc`), so a caller has one exception type to catch and
still has the cause. A `BaseException` that is not an `Exception` propagates untouched, for the
reason in `v0.1 §5.5`.

**No receipt, no events.** A refusal at the identity gate happens before an Action exists, so
there is nothing to attribute a receipt to. `no_principal` keeps `v0.1 §2.1`'s shape exactly —
a warning naming the action, nothing in the evidence log — and a raising provider is recorded
the same way. §2.3's `principal_expired` is the one identity refusal that *does* write a
receipt, because by then a `Principal` exists.

### 3.3 The two core providers

```python
StaticIdentityProvider(agent: str, user: str | None = None, *,
                       claims: Mapping[str, str | int | bool] = {},
                       issuer: str | None = None,
                       expires_at: datetime | None = None)

HeaderIdentityProvider(agent_header: str, *, user_header: str | None = None,
                       issuer: str | None = None)
```

**`StaticIdentityProvider`** answers with the same `Principal` every time. It is for
development, tests and single-tenant demonstrations. It MUST log a warning on the `ctrlrun`
logger at construction — **once per instance**, not per call — saying that it asserts an
identity nobody verified and naming the agent it asserts. A library that prints on every action
is a library whose warnings get filtered.

**`HeaderIdentityProvider`** reads `agent_header` from `IdentityContext.headers` (lowercased
comparison). An absent header, or one whose value is empty or whitespace, → `None`: a decline,
so §3.2's table applies. `user_header` is optional and follows the same rule, contributing
`user=None` when absent. It carries no claims: a header is a name, and a provider that
manufactured claims from headers would be inventing verified data.

**The threat, stated plainly.** *A trusted header is worth exactly what the thing that sets it
is worth.* `HeaderIdentityProvider` is correct behind a proxy that authenticates the caller and
**overwrites** the header on every request. It is worthless anywhere else: if the agent can set
the header, the agent chooses its own authority, and §4 is decoration. Two specific failures:

- A proxy that *adds* the header when absent but passes it through when present lets a client
  supply its own. The proxy MUST overwrite unconditionally. A proxy that *appends* — a common
  default for header-injection directives — produces two values, which §3.1 refuses outright
  rather than collapsing.
- A gateway reachable directly, bypassing the proxy, has no header discipline at all. `v0.2
  §6.1`'s loopback default and `--allow-remote` exist for this; they are necessary and not
  sufficient.

RFC 7239 §8.1 says it about the header it standardizes, and it is the same sentence: a
forwarding header *"cannot be relied upon to be correct, as it may be modified, whether
mistakenly or for malicious reasons, by every node on the way to the server, including the
client making the request"*, with the only mitigation being a whitelist of trusted proxies.

`HeaderIdentityProvider` MUST log a warning at construction naming the header and saying the
above in one line. `docs/THREAT_MODEL.md` carries the long form.

### 3.4 `JWTIdentityProvider` (item 5, `ctrlrun[identity]`)

```python
# ctrlrun.jwt_identity — lazily importable, not re-exported at package import
JWTIdentityProvider(*,
    # exactly one key source
    jwks_url: str | None = None,
    public_key: str | None = None,          # PEM text, or a path to a PEM file
    secret: str | None = None,              # the shared secret, for an HS* deployment
    algorithms: Sequence[str],              # required; no default, no wildcard
    issuer: str,                            # required, matched exactly
    audience: str,                          # required
    token_type: str | None,                 # required to be passed; see below
    header: str = "authorization",
    agent_claim: str = "sub",
    user_claim: str | None = None,
    claim_names: Sequence[str] = (),        # which verified claims reach Principal.claims
    leeway: timedelta = timedelta(seconds=60),
    jwks_min_refresh_interval: timedelta = timedelta(seconds=30),
    http_timeout: timedelta = timedelta(seconds=5),
    clock: Callable[[], datetime] = ...)
```

It reads a bearer token from `header` (`Authorization: Bearer <jwt>`; the scheme is matched
case-insensitively, and a header with no scheme is refused rather than guessed), verifies it,
and returns a `Principal`. Absent header → `None` (a decline, §3.2). Present and invalid →
`IdentityError` (a refusal).

`clock` is injected for the same reason every other timed component in this codebase takes one:
so `exp`, `nbf` and the JWKS refresh window can be tested exactly rather than raced.

**Configuration is refused before any token is seen.** Each of these is `InvalidArgument` at
construction:

- More or fewer than one of `jwks_url`, `public_key`, `secret`.
- A symmetric algorithm (`HS*`) with `jwks_url` **or** `public_key`. This is key confusion in its
  plainest form — RS256→HS256 is literally "HMAC the token using the PEM of the public key as
  the secret" — and it is refused at the *configuration* end, which is the end that can be
  refused. The token end is already covered by the `algorithms` allow-list, and a test that only
  exercised the token end would be a negative test against behaviour the library refuses anyway.
  A deployment that genuinely uses `HS*` puts its key in `secret`, where it cannot be confused
  with a public one.
- An asymmetric algorithm with `secret`.
- An empty `algorithms`, or one containing `none` in any casing.
- `token_type` not passed at all. It has no default: see below.

`public_key` is PEM text when it begins `-----BEGIN`, and a filesystem path otherwise. Stated,
because the gateway flag is `--identity-jwt-public-key PATH` and the constructor takes both.

**Verification MUST:**

- **Reject `alg: none` and every unlisted algorithm.** The provider selects the verification
  algorithm from its own configured list and MUST NOT read it from the token header for that
  purpose; a token whose `alg` is not in the list is refused before any signature check. This is
  the algorithm-confusion class of RFC 8725 §3.1, whose rule is that a library "MUST enable the
  caller to specify a supported set of algorithms and MUST NOT use any other algorithms".
- **Check `typ`.** `token_type` is a required argument with no default. A string requires the
  token's `typ` header to equal it, compared case-insensitively with a leading `application/`
  stripped (RFC 7519 §5.1) — `at+jwt` for an OAuth access token per RFC 9068 §4, `JWT` for a
  JWT-SVID. Passing `None` explicitly means "this issuer sets no `typ`" and is permitted, and
  MUST log a warning at construction naming what it gives up.

  This is not ceremony. Without it, an **OIDC ID token** from the same issuer, signed with the
  same key, carrying `aud = <client id>`, is accepted by a provider that checks only `iss`,
  `aud` and `exp` — whenever the operator configured `audience` to that client id. That is the
  cross-JWT confusion attack of RFC 8725 §2, and that document's §3.11 and §3.12 answer it with
  explicit typing
  and validation rules "written such that they are mutually exclusive, rejecting JWTs of the
  wrong kind". A required argument is how the operator is made to choose.
- **Check `exp` and `nbf`** with `leeway` (default 60 s, a bound on clock skew and nothing
  more), and **`iss`** exactly.
- **Check `aud` by membership, on either wire shape.** RFC 7519 §4.1.3 permits `aud` to be a
  single string *or* an array of strings, and real issuers emit both — SPIFFE's own JWT-SVID
  prose says "one or more values" while its published JSON Schema types it as a string. The
  provider MUST normalize to a list of strings and require the configured `audience` to be an
  element of it, compared exactly. It MUST NOT compare the configured value against a raw `aud`
  with `in`: on the string shape that is substring matching, and it accepts
  `https://ctrlrun.example` for a configured `ctrl`.
- **Refuse a token with no `exp`.** A credential with no expiry cannot be revoked by waiting,
  and v0.3 has no revocation channel (below).
- Set `Principal.expires_at` from `exp`, `Principal.issuer` from `iss`, `Principal.agent` from
  `agent_claim` and `Principal.user` from `user_claim` when configured. Claim names are **flat
  keys**, not dotted paths: a claim called `a.b` is the key `"a.b"`, and no nesting is
  traversed. A missing, empty, or non-string `agent_claim` → `IdentityError`, never coerced.
- Copy only the claims named in `claim_names` into `Principal.claims`, dropping any whose value
  is not `str`, `int` or `bool` (§2.1) with a debug log. An allow-list, not everything: a token
  carries fields nobody meant to publish into a receipt.

**JWKS handling.** Keys are fetched from `jwks_url` over HTTPS and cached in memory.

- A token whose `kid` is not in the cache triggers **at most one** refresh; if the `kid` is still
  unknown after it, the token is refused.
- The refresh is rate-limited so a stream of tokens carrying unknown `kid`s cannot turn this
  process into a load generator aimed at the issuer: one refresh per
  `jwks_min_refresh_interval` (default 30 s), and a token arriving inside that window is refused
  without a fetch.
- **A JWK Set containing two entries with the same `kid` is refused**, and the token with it.
  RFC 7517 §4.5 says only that distinct keys *SHOULD* use distinct `kid` values, so a duplicate
  is legal and "take the first match" would be a silent trust decision about which key signs.
- **A key whose `use` is present and is not `sig` is ignored**, as is one whose `key_ops`
  excludes verification. A JWK Set commonly carries encryption keys, and verifying a signature
  against one is a defect.
- **A key carrying its own `alg` constrains itself**: the configured allow-list *and* the key's
  `alg` must both admit the token.
- A failed fetch is `IdentityError`, never a fallback to an empty key set and never a
  cached-forever key. `http_timeout` bounds the fetch, and the provider MUST NOT follow a
  redirect to a non-HTTPS URL.

**Two limits, stated so nobody configures around them.**

*Multi-tenant issuers are out of scope.* `issuer` is matched as an exact string. The largest
real-world issuers publish a **templated** issuer — `https://login.microsoftonline.com/{tenantid}/v2.0`
— where a relying party is required to substitute the token's own tenant claim into the metadata
issuer and then compare, and to check that tenant against an allow-list. v0.3 does not do that,
so a tenant-templated issuer cannot be configured correctly here. This is said plainly because
the fail-open is inviting: pointing `issuer` at a common multi-tenant endpoint without pinning
the tenant makes every tenant on that platform a valid issuer for this gateway.

*There is no revocation channel.* A token this provider verified stays valid until its `exp`.
Nothing polls, subscribes, or introspects — which is exactly why a token with no `exp` is
refused. A deployment that needs faster propagation than its token lifetime uses a shared-signals
mechanism at the identity layer (the OpenID Foundation's Shared Signals Framework, CAEP and RISC
reached Final Specification on 2025-09-02); v0.3 implements none of it and does not pretend to.
Short lifetimes are the whole of the story here.

The extra is `identity`. `import ctrlrun` MUST NOT import `jwt`; constructing
`JWTIdentityProvider` without the extra installed MUST raise `MissingDependency` naming
`pip install 'ctrlrun[identity]'` (T92). `# SPEC:` the item-5 brief said "ImportError"; the
house rule of `v0.2 §1.1` says `MissingDependency`, which is what an operator can act on.

**What it is not.** It is not an OAuth client. It performs no authorization-code flow, no
refresh, no token exchange, no introspection, no dynamic client registration. It verifies a
token somebody else obtained. Anything beyond verification belongs to the deployment.

---

## 4. Authority

### 4.1 The section, and what its presence means

Authority lives in the same `ctrlrun.yaml` as policy, under a top-level `authority:` key, and
needs `schema: ctrlrun.policy/v3` (§12).

```yaml
schema: ctrlrun.policy/v3

authority:
  max_delegation_depth: 3            # optional, default 3 (§5.5)
  grants:
    - id: head-of-support
      subject: { agent: "support-agent", user: "alice@example.com" }
      actions: ["stripe.refund", "stripe.refund_partial"]
      resources: ["payment:EU-*"]
      constraints: { amount_gte: 0, amount_lte: 200000 }
      environments: ["production"]
      expires_at: 2026-12-31T23:59:59Z
      delegable: true

actions:
  stripe.refund:
    rules:
      - when: { amount_gte: 0, amount_lte: 50000 }
        decision: allow
      - decision: approve
```

**No `authority:` key at all → v0.2 behaviour, exactly.** No authority evaluation, no
`AUTHORITY_*` events, no new receipt fields, no change to any decision, and §3.2's decline row
keeps its v0.2 meaning. Every v0.1 and v0.2 acceptance test MUST pass unchanged against such a
configuration (T66).

**An `authority:` key present → every action needs a grant.** Including actions the policy
allows outright, including reads, including actions with no effect key. There is no
`default: allow`, no `unlisted: permit`, and no per-action opt-out. An operator who wants an
action ungoverned by authority writes a grant that says so, in the file, where it can be read.

This is the same shape as `v0.1 §3.4`'s `actions: {}`: an empty `grants: []` is valid and denies
every action. `authority:` present without a `grants` key, or with a `grants` that is not a
list, is a `PolicyError` at load — a section that governs everything must state what it permits,
and inferring "nothing" from a missing key would make a truncated edit look deliberate.

### 4.2 The grant

| Key | Required | Type | Meaning |
|---|---|---|---|
| `id` | yes | `str` | Unique within the document; names this grant to `ctrlrun delegate` |
| `subject` | yes | mapping | `agent` and/or `user`, at least one; patterns (§4.4) |
| `actions` | yes | non-empty list | Action-name patterns (§4.4) |
| `resources` | no | non-empty list | Resource patterns (§4.4); omitted → any resource |
| `constraints` | no | mapping | Conditions in policy's `when:` syntax (§4.5); omitted → none |
| `environments` | no | non-empty list | Exact environment names; omitted → any environment |
| `expires_at` | no | timestamp | ISO-8601 with an offset; omitted → does not expire |
| `delegable` | no | `bool` | May a delegation be created from it (§5); default `false` |

Key sets are **closed** at every level, as `v0.1 §3.1` requires: `authority` accepts
`max_delegation_depth` and `grants`; a grant accepts exactly the keys above; `subject` accepts
`agent` and `user`. Anything else → `PolicyError` at load, so `action:` for `actions:` or
`resource:` for `resources:` fails loudly instead of silently denying everything at runtime.

**A grant carries no decision.** Authority answers one question — may this principal do this at
all — and policy answers the other, how much autonomy the action has. `# SPEC:` an earlier draft
of this document gave a grant an optional `decision: allow | approve`, so that a delegation could
say "may refund, but always with a human", and so that T70's "3×3" was literally a 3×3. It is
removed, for three reasons that arrived together. It put the approval requirement in two places,
so the gateway's approval pre-check (`v0.2 §6.10`) — which keys off the policy decision — would
have deadlocked on a combination no client could satisfy. It needed a `decision` row in §5.4's
containment table whose default is the *loosest* value, which is the one shape §5.4 exists to
refuse. And it made whether an action needs a human depend on who proposed it, which is the exact
sentence `docs/CLAIMS.md` maps to code (§4.7). An operator who wants "this delegate always needs
a human" gives that delegate its own action name, or writes the rule in policy, which is where
per-action autonomy lives. Making authority able to *raise* a decision is a v0.4 question with a
gateway story attached.

`id` MUST match `^[A-Za-z0-9][A-Za-z0-9._-]*$` and be unique across the document; a duplicate
is a `PolicyError`, because a delegation names its parent by id and two grants answering to one
name is an ambiguity nobody can resolve later. A grant `id` MUST NOT begin `dlg_`: delegation
ids are minted in that namespace (§5.2) and one namespace addresses both kinds.

That rule is about a grant **in a document**. A `Grant` handed to `Control.delegate` carries
`id=""` and is assigned one (§5.2); the empty id is legal only on that path and only until the
call returns, and a `Grant` with a non-empty `id` on it is refused.

**Subject.** `subject.agent` and `subject.user` are §4.4 patterns over a **single segment** — an
agent name and a user name are opaque strings, not dotted paths, so they carry no separator and a
`*` in them matches any run of characters. `subject` MUST declare at least one of `agent` and
`user`. A grant matching every principal is a grant nobody means to write, and `subject: {}` is
far more likely a truncated edit than an intent — the same reading `v0.1 §3.2` gives `when: {}`.
A subject that really does mean "any agent" writes `agent: "*"`, which is legible in review.

**`subject.user` semantics.** Omitted, it matches any principal including one with no user.
Present, the principal's `user` MUST be non-`None` and match the pattern; a principal with no
user does not match a grant that names one. Fail-closed: a grant scoped to a human is not
satisfied by an agent acting alone. This rule is the whole of who holds a grant, so it is tested
by name in T67b rather than left to a grant that happens not to match.

**Subject matching addresses `agent` and `user`, and nothing else.** Not claims, not the issuer.
Both fields are part of the action's canonical form (`v0.1 §2.2`), so an authority decision is
stable under the token rotation §2.2 describes; a claim is not. Matching a grant on a claim is
out of scope for v0.3 (§13) and needs an answer to "what does a missing claim mean" that this
release does not have.

`expires_at` MUST parse as ISO-8601 **with an explicit offset**. A naive timestamp is a
`PolicyError`, for §2.1's reason: an expiry in an unstated timezone cannot be checked. YAML
parses an unquoted `2026-12-31T23:59:59Z` into a `datetime` and an unquoted
`2026-12-31 23:59:59` into a naive one, so both the string form and the parsed form must be
accepted on input and both must be rejected when they carry no offset. The comparison is
`now > expires_at → expired`; a grant is valid up to and including its expiry instant.

### 4.3 Evaluation, and where it sits

An action **passes authority** iff at least one grant matches on **all four** of subject, action
name, resource and environment, has **all** its constraints hold, is **unexpired**, and — if it
is a delegation — has a chain that is still valid under §5.

Authority is evaluated **before** policy. Two reasons, and the second is the load-bearing one:

1. A denial must not leave a pending approval request behind. Policy reaching `approve` creates
   a request (`v0.1 §4.3`); an authority denial afterwards would leave a human staring at a
   request for an action that could never run, and a request in the store that something might
   later present.
2. It is the cheaper check, and it is the one that can refuse the most actions in a
   misconfigured deployment.

So: an action denied by authority produces `AUTHORITY_DENIED` and **no** `POLICY_EVALUATED`
(T74). It is recorded the way an `effect_key_error` is recorded (`v0.1 §5.1`) — there is a
principal, so the refusal belongs in the evidence log:

- `ACTION_PROPOSED`, then `AUTHORITY_DENIED` with `data.reason`, then `ACTION_DENIED` with the
  same reason;
- a receipt with `result: "denied"`, `decision: "deny"`, `decision_reason` = the reason;
- **no** `POLICY_EVALUATED`, **no** approval request, **no** reservation;
- `AuthorityDenied` raised, carrying `reason` and, where one grant was implicated, `grant_id`.

An action that **passes** authority appends `AUTHORITY_RESOLVED` with `data.grant_id`, and — for
a delegated grant — `data.delegation_id` and `data.depth`. Policy then runs as it always has, and
the two results combine per §4.6.

**Order within `Control.execute`,** stated once so it can be tested: `principal_expired` (§2.3)
→ authority → policy → approval → reservation → execution. The effect key resolves earlier still,
before `Control.execute` is entered, exactly as `v0.1 §5.1` requires; an unresolvable template
therefore refuses before authority does, and both are refusals with their own reason, so nothing
is lost.

**Reasons are a closed set**, and each is distinguishable in evidence. This matters: a guard that
can only fire where a later guard would also fire, with the same observable result, is
documentation rather than defence — removing it changes nothing a caller or a test can see. Every
reason below is asserted **by name** in §10, so removing the check that produces it fails a test
rather than passing one.

| Reason | Meaning |
|---|---|
| `authority_grant` | **Passed.** A grant matched; `AuthorityResult.grant_id` names which |
| `no_authority` | No grant matched subject, action, resource and environment |
| `authority_constraint` | A grant matched the shape, but a constraint did not hold |
| `authority_expired` | Every grant that matched the shape had passed its own `expires_at` |
| `authority_escalation` | A delegated grant is no longer contained in its chain, or an ancestor has expired or is missing (§5.6) |
| `authority_revoked` | A delegation in the chain has been revoked (§5.7) |

`authority_grant` exists because a *passing* result also needs a reason: §4.6 records the axis
that produced the decision, and there was otherwise no value to record for the cell where
authority is the one that spoke. `decision_reason` **never** holds a grant id — a grant may
legally be named `no_authority`, and evidence that could be spoofed by naming a grant is not
evidence. The id travels in `AuthorityResult.grant_id` and in `AUTHORITY_RESOLVED.data.grant_id`.

**Reason precedence, within a grant and across grants.** A single grant may fail more than one
way — expired *and* excluded by a constraint. Evaluation MUST collect every reason a grant failed
for rather than short-circuiting on the first, and then report, across all grants, the first
reason present in this fixed order:

`authority_escalation` → `authority_revoked` → `authority_constraint` → `authority_expired` →
`no_authority`

Fixed and collected rather than short-circuited, so the evidence for one configuration does not
depend on the order grants appear in the document or the order an implementation happens to
check them in. T71b pins it by permuting the document.

**A delegation's own expiry is `authority_expired`**, like any other grant's. §5.6's
`authority_escalation` covers its *ancestors* — an expired or missing parent — which is a
different fact about a different record, and the two are asserted separately in §10.

### 4.4 Patterns: globs that cannot cross a separator

Action names and resources are matched by pattern. The grammar is deliberately smaller than
`fnmatch`, for the same reason `v0.1 §5.1`'s template grammar is smaller than `str.format`: a
pattern here decides who may move money, and containment between two patterns (§5.5) has to be
**decidable**, not approximated.

**Grammar.** A pattern is a non-empty sequence of segments joined by the type's separator. Each
segment is one of:

- a **literal** — any run of characters containing no `*`;
- a **prefix wildcard** — a literal followed by exactly one trailing `*` (`EU-*`, or bare `*`,
  which is the empty prefix).

The final segment may instead be `**`, the **deep wildcard**, which matches one or more remaining
segments, separators included. Where a pattern ends in `**`, **every preceding segment MUST be a
literal**: `stripe.**` and `payment:EU:**` are well formed, `stripe.a*.**` and `*.**` are
`PolicyError`s at load. A deep wildcard's prefix is literal so that containment (§5.5) is decided
by comparison rather than by reasoning about two kinds of wildcard at once.

`**` is permitted only as the last segment, only once, and never as part of a larger segment
(`a**` and `**a` are load errors). There is no `?`, there are no character classes, there is no
leading or infix `*`. A pattern that does not fit this grammar is a `PolicyError` at load.

**Separators.** Action names separate on `.`; resources separate on `:`. Both come from v0.1: an
action name is dotted (`v0.1 §2.1`), and a resource is an opaque `type:id` (`v0.1 §2.1`). A
subject pattern (§4.2) has no separator and is therefore always one segment.

**Matching** is segment-wise, case-sensitive, with no Unicode normalization — `v0.1 §3.1` and
`v0.1 §2.3` for both. A pattern with no deep wildcard matches only a value with the same number
of segments. Consequences, and they are the point:

| Pattern | Matches | Does not match |
|---|---|---|
| `stripe.*` | `stripe.refund` | `stripe.refund.partial`, `stripe` |
| `stripe.**` | `stripe.refund`, `stripe.refund.partial` | `stripe`, `stripes.refund` |
| `payment:EU-*` | `payment:EU-42` | `payment:EU-42:leg2`, `payment:US-42` |
| `*` | `refund` | `stripe.refund` |
| `**` | every action name | — |

**There is no short spelling for "everything".** `*` matches one segment. Granting every action
is spelled `**`, one token, greppable, and impossible to write by accident. This is deliberate:
the pattern that grants an agent the whole surface of a system should be the one that looks like
it does.

An action whose `resource` is `None` does **not** match a grant that declares `resources:`. To
grant an action that carries no resource, omit `resources:` from the grant. (Under §5.4 a
*delegation* may not omit it where its parent declares it — omission never widens.)

### 4.5 Constraints

`constraints` is a mapping in exactly the syntax of a policy rule's `when:` (`v0.1 §3.2`), and it
MUST be parsed by the same code: `<argument>_<op>` where op is one of `eq`, `neq`, `lt`, `lte`,
`gt`, `gte`, `in`; longest-suffix split; operands validated against `v0.1 §2.3`'s types at load;
`float` operands refused; `bool` refused for numeric ops on either side; equality type-strict and
recursive; the reserved names of `v0.1 §3.2` refused. All constraints must hold (AND). A
`constraints: {}` or `constraints:` (null) is a `PolicyError`, as `when: {}` is.

One shared implementation, not two. A second condition evaluator would be a second place for
`True` to start comparing equal to `1`. §11 names the surface that is promoted from private to
public so the sharing is real rather than aspirational.

**Constraints address the action's arguments**, and only arguments — the same rule and the same
reserved-name refusal as policy. v0.3 adds `claims`, `issuer` and `expires_at` to
`RESERVED_ARGUMENTS`, so `claims_eq:` is a load error rather than a condition that silently
addresses an argument nobody has. Before v0.3 those names meant nothing; now they name fields of
a `Principal`, and `v0.1 §3.2`'s rule is that a name with two candidate meanings is refused until
the author renames. This is a **breaking change**, it is **not** gated on the schema version, and
§12.1 says exactly what it breaks.

**An absent argument makes a constraint false, and logs a warning** — `v0.1 §3.2` unchanged. In
policy a false condition falls through to the next rule; in authority it means this grant does
not match, and if no other does, the action is denied with `authority_constraint`. The direction
is the same: a condition that cannot be evaluated never matches.

**Only integer arguments can be bounded, and that decides how money must be represented.**
`v0.1 §2.3` blesses two representations for money — integer minor units (`amount=200000`) or
decimal strings (`"2000.00"`) — and `v0.1 §3.2` restricts `lt`, `lte`, `gt` and `gte` to `int`
operands. Authority inherits both rules, and the consequence is worth stating rather than leaving
to be rediscovered: **a deployment that represents money as decimal strings cannot express an
amount ceiling in a grant at all**, and therefore cannot attenuate the one dimension this whole
model is sold on. Integer minor units are the only representation authority can bound.

The fail-open here is already closed, which is why this is a documentation duty and not a change:
a decimal-string operand is a `PolicyError` at load (`v0.1 §3.2`), so lexicographic comparison —
under which `"1000.00" < "500.00"` and a child would be judged *stricter* than its parent — can
never reach §5.4's containment check. What is left is an operator who writes `amount_lte:
"2000.00"` and gets a load error whose message should name the reason. It does.

### 4.6 The AND with policy: the stricter of the two

Authority yields `pass` or `deny`. Policy yields `allow`, `approve` or `deny`, exactly as it
always has. Order the decisions by strictness — **allow < approve < deny** — and the action's
decision is the **stricter** of the two:

| | policy `allow` | policy `approve` | policy `deny` |
|---|---|---|---|
| **authority passes** | allow | approve | deny |
| **authority denies** | deny | deny | deny |

Neither axis can loosen the other. Authority cannot make a denied action allowed; policy cannot
make an unauthorized action permitted. The bottom row is reached without evaluating policy at all
(§4.3), so its three cells share an outcome and differ in their event trace. T70 parametrizes the
outcomes; T74 pins the trace.

`# SPEC:` the item-2 brief asks T70 to "parametrize the 3×3". With a grant carrying no decision
(§4.2) authority is binary and the table is 2×3, so T70 parametrizes six cells and asserts the
recorded reason in each. The brief's third row was the grant-level `decision:` this document
removed, and the reasoning is in §4.2.

**`decision_reason`** names the axis that produced the decision: an authority reason from §4.3
where authority is the stricter axis, and a policy reason (`rule[1]`, `decision`,
`no_matching_rule`, `unknown_action`) otherwise. Where the two tie — authority passes and policy
allows, or authority passes and policy approves — the **policy** reason is recorded, because it
is the one an operator edits per action; `authority_grant` and the grant id are in
`AUTHORITY_RESOLVED` either way.

**`Control.evaluate` returns the combined decision.** `v0.1 §8` freezes
`Control.evaluate(action) -> Evaluation` as the public "what will happen to this action" query,
and it would stop answering that question if it reported the policy axis alone while
`Control.execute` acted on the combination. Its signature and `Evaluation`'s two fields are
unchanged; it reads the store to resolve delegations and still writes nothing, so "no side
effects" holds. §11 records the change.

Everything that pre-checks a decision MUST use the combined one — `Control.evaluate`, the
gateway's approval path (§8.3), and `ctrlrun.acs`'s hook. Reading `Policy.evaluate` directly for
a decision is a defect after v0.3, and the two places in shipped code that do it are named in
§8.3 so item 5 cannot miss them.

**Where several grants match**, the action passes; a grant is a permission, and holding two
permissions must never be worse than holding one. Denial arises from the absence of a matching
grant, never from the presence of a stricter one.

Which grant is *named* then needs a rule, because any of them authorizes the action and evidence
must not turn on incidental file order — the same standard §4.3 applies to deny reasons.
`AuthorityResult.grant_id` is therefore the matching grant whose `id` sorts first by simple
codepoint order, which is a property of the set rather than of the document. Reordering the
grants in the file changes neither the decision nor the id, and T70b pins both by permuting it.

### 4.7 What does not change

**Policy still cannot see the principal.** `Policy.evaluate(action)` passes the action's name and
arguments to rule evaluation and nothing else; `agent_eq`, `user_eq` and every other reserved
name are still refused at load (`v0.1 §3.2`). `docs/CLAIMS.md` maps the README's *"Autonomy
belongs to the action, not the agent"* to exactly that fact, and after §4.2 removed the
grant-level decision the sentence stays true of behaviour as well as of a function signature:
**how much autonomy an action has is the same for every principal.** What differs per principal
is whether they may propose it at all.

That separation is not tidiness. Per-action autonomy is a property of the action's consequence —
a €20,000 refund needs a human whoever proposes it. Entitlement is a property of the actor.
Collapsing them into one rule table produces a file where nobody can answer either question, and
it is what makes the README sentence a claim about the product rather than about an argument
list.

### 4.8 The `Authority` object

```python
@dataclass(frozen=True)
class Subject:
    agent: str | None = None              # a §4.4 single-segment pattern
    user: str | None = None               # a §4.4 pattern; None matches any user, including none

    def __post_init__(self) -> None: ...  # both None → InvalidArgument (§4.2)

@dataclass(frozen=True)
class Grant:
    id: str
    subject: Subject
    actions: tuple[str, ...]                      # §4.4 patterns, separator "."
    resources: tuple[str, ...] | None = None      # §4.4 patterns, separator ":"; None → any
    constraints: Mapping[str, Condition] = _NO_CONSTRAINTS   # keyed by the raw condition key
    environments: tuple[str, ...] | None = None   # exact names; None → any
    expires_at: datetime | None = None
    delegable: bool = False


@dataclass(frozen=True)
class AuthorityResult:
    passed: bool
    reason: str                           # a §4.3 reason — never a grant id
    grant_id: str | None = None
    delegation_id: str | None = None
    depth: int = 0


class Authority:
    @classmethod
    def from_yaml(cls, text: str, *, source: str = "<string>") -> Authority: ...
    grants: Mapping[str, Grant]
    max_delegation_depth: int
    def evaluate(self, action: Action, *, now: datetime, store: StateStore) -> AuthorityResult: ...
    def plan_delegation(self, parent_id: str, grant: Grant, *, by: Principal,
                        store: StateStore, now: datetime) -> Delegation: ...   # §5.3
    def plan_revocation(self, delegation_id: str, *, by: str | None,
                        store: StateStore, now: datetime) -> Delegation: ...   # §5.7


Control(..., authority: Authority | None = None)
Control.delegate(parent_id: str, grant: Grant, *, by: Principal) -> Delegation
Control.revoke(delegation_id: str, *, by: str | None = None) -> None
```

`Subject.__post_init__` refuses both fields `None`, so the constructor refuses exactly what the
loader refuses. `Control.delegate` is public API and takes a `Grant` built in Python, not YAML;
without this a holder of a narrow grant could mint a child addressed to every principal in the
deployment — widening the population rather than the powers. "Any agent" is spelled
`Subject(agent="*")`, which is greppable — and §5.4 forbids it in a *delegation* regardless.

`Grant.constraints` is keyed by the **raw condition key** (`"amount_lte"`), which is injective
given `v0.1 §3.2`'s longest-suffix split, and valued by the `Condition` §11 promotes from
`policy.py`. §5.4 needs exactly that keyed lookup. `_NO_CONSTRAINTS` is a shared immutable empty
mapping, for §2.1's reason.

`store` is **required** on `evaluate`. §5.6 re-checks a delegation's whole chain on every
evaluation and that walk reads the store; an optional store would give an implementation a
fail-open reading in which a revoked delegation evaluates as valid, and "a chain of any depth is
cut by one write" would stop being true. An `Authority` with no delegations reads nothing from
it, which costs nothing and says so.

**`Authority` is pure, and `Control` writes.** `evaluate`, `plan_delegation` and
`plan_revocation` read the store and never write to it; each `plan_*` returns the record it would
write, or raises `AuthorityEscalation`. `Control.delegate` and `Control.revoke` perform the write,
append the events of §7, and fan out to every registered `EventSink` — because `v0.2 §4.1`
requires sinks to be called by `Control` with the `event_id` the store assigned, and
`ARCHITECTURE.md` §6 makes `Control` the only module that composes the others. `ctrlrun delegate`
and `ctrlrun revoke` (§5.7) go through `Control`, not around it.

The alternative — `Authority` writing and appending for itself — would make `authority.py` a
second module composing storage and evidence, and would leave the highest-privilege operations in
the release as the only ones invisible to the export path. T75 and T78 assert that the delegation
events reached a registered sink, not merely the `events` table.

---

## 5. Delegation with attenuation

### 5.1 What a delegation is

A **delegation** is a grant created at runtime, by a principal who already holds one, naming a
parent it must not exceed. Human grants €100,000 to a finance agent; the finance agent grants
€25,000 of it to a support agent; the support agent asks for €50,000 and is denied. That chain
is §5.6, it is `ctrlrun demo` scenario 5, and it is the reason this section exists.

A grant in the YAML file is a **root grant**: written by an operator, in a file the operator
controls, at depth 0. A delegation is created by `Control.delegate` or by `ctrlrun delegate`, at
depth `parent.depth + 1`, and lives in the store.

### 5.2 The record and where it lives

A new `delegations` table, in both stores. It is a **new table**, so `CREATE TABLE IF NOT EXISTS`
adds it to a database that already exists and v0.3 needs no migration story — the same reason
`v0.2 §2.2` refused to add a column to `effects`. No existing table gains a column in v0.3; T87
depends on that.

```sql
delegations(
  delegation_id TEXT PRIMARY KEY,     -- "dlg_" + 32 hex
  parent_id     TEXT NOT NULL,        -- a root grant's id, or another delegation_id
  depth         INTEGER NOT NULL,     -- recorded, never trusted (§5.5)
  grant_json    TEXT NOT NULL,        -- the child grant, serialized per below
  created_by    TEXT NOT NULL,        -- "agent" or "agent/user"
  created_via   TEXT NOT NULL,        -- "api" | "cli" (§5.7)
  created_at    TEXT NOT NULL,
  revoked_at    TEXT,                 -- NULL while live
  revoked_by    TEXT
);
CREATE INDEX IF NOT EXISTS delegations_by_parent ON delegations(parent_id);
```

**The store persists rows, not grants.** `StateStore` reads and writes a `DelegationRecord` whose
fields are the columns above — `grant_json` stays a string — and `Authority` is what parses a
`Grant` out of it. This keeps `ARCHITECTURE.md` §6's dependency direction intact: `state.py` does
not import `authority.py`, and therefore does not transitively acquire `policy.py`. `Delegation`
is the parsed form and lives in `authority.py`:

```python
@dataclass(frozen=True)
class Delegation:
    delegation_id: str
    parent_id: str
    depth: int
    grant: Grant
    created_by: str
    created_via: Literal["api", "cli"]
    created_at: datetime
    revoked_at: datetime | None = None
    revoked_by: str | None = None
```

**Who mints the id.** `Control.delegate` mints `"dlg_" + secrets.token_hex(16)` — the same shape
as `act_` and `ctr_` (`v0.1 §2.1`, §6.1) — and returns a `Delegation` whose `grant.id` is set to
it. A `Grant` passed to `Control.delegate` MUST carry `id=""`, and an `id:` key in a
`ctrlrun delegate --file` document is a `PolicyError` naming the rule: *the id is assigned, not
chosen*. Creation is deliberately **not** idempotent — two identical calls make two delegations,
each revocable on its own — because a delegation is an act, and collapsing two acts into one
record would lose which one a receipt refers to.

`grant.id` for a delegation therefore **is** its `delegation_id`: one namespace addresses both
kinds and `--parent` takes either. §4.2 keeps root grant ids out of the `dlg_` namespace.

**`grant_json`** is the grant as JSON with `sort_keys=True`, `separators=(",", ":")`,
`ensure_ascii=False`: `subject` as an object of two nullable strings, `actions`/`resources`/
`environments` as arrays of strings or `null`, `constraints` as an object mapping each raw
condition key to its operand, `delegable` as a boolean, and `expires_at` as ISO-8601 **retaining
the offset it was written with** — not normalized to UTC, so a grant round-trips to an equal
`Grant` and T71's "exactly `expires_at` still passes, one microsecond later does not" does not
depend on which path wrote it.

The word *canonical* is deliberately not used: this is a storage encoding, nothing hashes it, and
`v0.1 §2.3`'s canonical form is a versioned security primitive that a change here must not be
read as touching. Reading a grant back MUST validate it exactly as loading one from YAML does — a
store is not more trusted than a file, and a delegation whose stored form no longer parses is an
`AuthorityDenied` naming it, never a grant with a field quietly defaulted.

### 5.3 Who may delegate

`Control.delegate(parent_id, grant, *, by)` MUST refuse unless **all** hold. The six checks are
performed **in the order listed**, and the first that fails is the refusal's reason — fixed, for
§4.3's reason: the evidence for one attempted creation must not depend on the order an
implementation happens to check things in.

1. `parent_id` names a root grant or a live delegation. Otherwise `unknown_parent`.
2. The parent's `delegable` is `true`. Otherwise `parent_not_delegable`.
3. The parent is unexpired at `now`, and no delegation in its chain is revoked or expired.
   Otherwise `parent_not_valid`.
4. `by` — the principal creating the delegation — **matches the parent grant's subject** (§4.2's
   matching, including the `user` rule). Otherwise `not_the_subject`. You may only delegate
   authority you hold; a process that could delegate from a grant addressed to someone else would
   make the subject field decorative.
5. `parent.depth + 1 <= max_delegation_depth` (§5.5). Otherwise `max_depth`.
6. The child grant is contained in the parent on every dimension of §5.4. Otherwise
   `containment`, and `data.dimension` names the row.

Every refusal raises `AuthorityEscalation(reason=...)`, writes no delegation record, and appends
`DELEGATION_REJECTED` with `data.reason` and `data.parent_id`. **`data.dimension` is present only
for a rule-6 refusal**, because it names a §5.4 row and the other five are not rows; §7 says the
same. A successful creation appends `DELEGATION_CREATED` with `data.delegation_id`,
`data.parent_id`, `data.depth`, `data.created_by` and `data.created_via`.

`AuthorityEscalation`'s reasons are creation-time reasons and are a **different vocabulary** from
§4.3's evaluation-time deny reasons. A test asserting `authority_escalation` on a creation is
asserting a value that creation never produces.

### 5.4 Containment: what child ⊆ parent means, dimension by dimension

Checked at creation, and checked again at every evaluation (§5.6). Each row is separately
testable and separately mutable; T76 breaks each one alone.

| Dimension | Child is contained iff |
|---|---|
| `subject` | The child MUST declare a concrete `agent` — present, and containing no wildcard — and, where the parent declares `user`, a concrete `user` the parent's pattern matches. Which concrete agent it names is otherwise unconstrained |
| `actions` | **Every** child pattern is contained in **some** parent pattern (§5.5) |
| `resources` | Parent omits → child may declare anything, or omit. Parent declares → child MUST declare, and every child pattern is contained in some parent pattern |
| `constraints` | For **every** `(argument, op)` the parent declares, the child declares the **same** `(argument, op)` with an operand at least as strict. The child may add constraints the parent does not have |
| `environments` | Parent omits → child may declare anything, or omit. Parent declares → child MUST declare, and the child's set is a subset of the parent's (exact strings) |
| `expires_at` | Parent has none → child may have any, including none. Parent has one → child MUST have one, no later than the parent's |
| `delegable` | Not a containment row. §5.3 rule 2 governs creation and §5.6 rule 6 governs evaluation |

**Why `subject` is a row at all, given that delegation exists to change who acts.** It is a row
because two things that look like "naming the grantee" are actually widening:

- *Dropping the parent's `user`.* A parent `{agent: finance-agent, user: alice}` is authority to
  act **for alice**. A child `{agent: finance-agent}` matches finance-agent acting alone, with no
  human attached — §4.2 spends a paragraph making that impossible for the parent, and one
  delegation would undo it. So where the parent names a user, the child must too, and the child's
  user pattern must be one the parent's admits. Omission is rejection, like every other omission.
- *An unbounded grantee.* `subject: {agent: "*"}` in a delegation hands the parent's authority
  to every agent in the deployment — and, since §5.3 rule 4 checks the *child's* subject for the
  next hop, hands every one of them the right to delegate it onward. `max_delegation_depth`
  bounds chain length, not grantee population, and nothing else would bound it. A wildcard
  subject is an operator's decision in a reviewed file (§4.2); it is exactly what a
  runtime-created grant must not be able to write.

  **Omitting `agent` is the same escalation spelled differently**, and it is why the row demands
  the key rather than merely banning the character. §4.2 makes an absent `subject.agent` match
  *any* agent, so a child of `{agent: finance-agent, user: alice}` declaring only
  `{user: alice@example.com}` carries no wildcard, satisfies the `user` rule, and still hands
  the grant to every agent acting for alice. §5.4's own rule decides it: omission is never
  "unlimited".

What is *not* constrained is *which* concrete agent the child names. It may be any of them,
which is the whole point of the chain in §5.6.

**Operand strictness**, per operator, for the `constraints` row:

| Op | Child is at least as strict iff |
|---|---|
| `lt`, `lte` | child operand ≤ parent operand |
| `gt`, `gte` | child operand ≥ parent operand |
| `eq` | child operand equals the parent's, type-strictly (`v0.1 §3.2`) |
| `neq` | child operand equals the parent's, type-strictly |
| `in` | the child's list is a subset of the parent's, compared type-strictly |

A `when:` mapping can carry at most one condition per `(argument, op)` — it is a mapping — so
"the same `(argument, op)`, at least as strict" is well defined and there is nothing to combine.
A child that constrains `amount_lt` where the parent constrains `amount_lte` has not discharged
the parent's constraint: it may hold it *as well*, but the parent's key must be present.
Requiring the same operator keeps containment decidable and keeps the failure legible in a
rejection message; a solver that reasoned across operators would be a second policy engine.

**On `neq`, before somebody flags it as inverted.** The classic error in this area is that a
*deny-list* attenuates by growing, so a child's exclusion set must be a **superset** of its
parent's — the direction opposite to every other row. It does not arise here: `v0.1 §3.2`'s
operator set has no deny-list operator (there is no `not_in`, no `contains`), and a `when:`
mapping holds at most one `neq` per argument, so a child cannot express "the parent's exclusions
plus more" even in principle. Equality is the correct and only expressible rule. This is stated
because a reviewer who knows the prior art will otherwise read the row as an oversight.

**Omission is never "unlimited".** This is the rule the whole section exists for, and it is T81.
A parent constrained to `amount_lte: 25000`, and a child that simply does not mention `amount`,
is **rejected** — not treated as inheriting the parent's limit, and certainly not as
unconstrained. Inheritance would be worse than rejection, because a child that silently inherits
looks, in the file and in the receipt, like a child that was authorized for what it says. A
delegation states its own limits or it is not a delegation.

The asymmetry with §4.2 — where a *root* grant that omits `resources` is unconstrained on
resources — is deliberate and worth saying out loud. A root grant is written by an operator, in a
file under review, and its omissions are that operator's decision. A delegation is created at
runtime by a principal the threat model does not trust, and its omissions are exactly what an
attacker would write.

**Where containment cannot be decided, it fails.** The pattern grammar of §4.4 is small enough
that every pair is decidable, and a pattern outside it is refused at load. If a future grammar
extension makes some pair undecidable, the answer is "not contained".

### 5.5 Pattern containment, chain depth, and the shape of the walk

Pattern `C` is contained in pattern `P` (same separator) iff:

- `P` is `**` — it contains every pattern; or
- `P` ends in a deep wildcard, `P = Q.**` (where §4.4 makes every segment of `Q` a literal): then
  `C` is contained iff `C` has **more segments than `Q`** and its first `len(Q)` segments equal
  `Q`'s. `Q.**` does **not** contain `Q` itself — the deep wildcard matches one or more further
  segments; or
- otherwise `C` has no deep wildcard, `C` and `P` have the **same number of segments**, and each
  segment of `C` is contained in the corresponding segment of `P`, where:
  - literal ⊆ literal iff equal;
  - literal `L` ⊆ prefix wildcard `A*` iff `L` starts with `A`;
  - prefix wildcard `B*` ⊆ prefix wildcard `A*` iff `B` starts with `A`;
  - prefix wildcard ⊆ literal: never.

**`**` counts as one segment for every count above.** So `stripe.**` has two segments, and
`stripe.**` ⊆ `stripe.**` holds by the second clause (2 > 1). This is load-bearing rather than
pedantic: **every well-formed pattern contains itself**, and T76's whole construction — vary one
dimension, hold the rest identical — silently depends on it. A containment relation under which
`stripe.**` failed to contain `stripe.**` would fail eight of T76's rows on the dimension that
was *not* under test, and the false red would be debugged as a containment bug. T72b asserts
`C ⊆ C` directly.

So `stripe.refund` ⊆ `stripe.*` ⊆ `stripe.**` ⊆ `**`, and `payment:EU-42` ⊆ `payment:EU-*` ⊆
`payment:*`, and `stripe.*` ⊄ `stripe.refund`, and `stripe.refund.partial` ⊄ `stripe.*`, and
`stripe` ⊄ `stripe.**`.

**Chain depth is recomputed, never trusted.** `authority.max_delegation_depth` is a non-negative
`int`, default **3**. A root grant is depth 0; a delegation of it is depth 1. A creation whose
resulting depth exceeds the maximum is refused with `max_depth`. The `depth` column (§5.2) is
recorded for reading, and evaluation MUST derive the depth by **walking to the root**, so a row
edited directly in the database cannot assert its way to a shorter chain.

The walk MUST be bounded at `max_delegation_depth + 1` steps and MUST refuse a chain that revisits
a `delegation_id` it has already seen. A cycle is not reachable through `Control.delegate` — a
parent exists before its child — but it is reachable with `sqlite3` and a text editor, and an
unbounded walk over one would hang the process rather than deny the action. Exceeding the bound,
or revisiting an id, is `authority_escalation`.

`max_delegation_depth: 0` is valid and means no delegation may be created at all — the legible way
to switch the feature off in a deployment that only wants root grants. A negative value is a
`PolicyError`.

### 5.6 Re-checking at evaluation, and the signature scenario

At evaluation, a delegated grant is valid only if, walking from it to the root:

1. every ancestor still exists → else `authority_escalation`, with `data.missing_parent_id`
   naming the one that does not;
2. no ancestor, and not itself, is revoked (§5.7) → else `authority_revoked`;
3. no ancestor has expired at `now` → else `authority_escalation` (the delegation's *own* expiry
   is `authority_expired`, §4.3);
4. every parent→child step still satisfies §5.4 → else `authority_escalation`, with
   `data.dimension`;
5. the chain's depth is still within `max_delegation_depth`, and the walk is acyclic (§5.5) →
   else `authority_escalation`;
6. every ancestor is still `delegable` → else `authority_escalation`.

Rule 6 exists because `delegable` is not a §5.4 row and rule 4 would therefore never see it. An
operator who sets `delegable: false` on a root grant is shutting down a chain they believe is
compromised; without rule 6 that edit would have no effect on any existing delegation beneath it,
and the prose would be promising containment the table did not deliver.

Re-checking is not belt-and-braces. It is what makes revocation transitive, what makes an expiring
parent stop authorizing without anyone finding its children, and what makes a narrowed root grant
narrow everything beneath it. A containment check performed only at creation would leave every
delegation exactly as wide as the file used to be, which is the shape of every stale-permission
incident there has ever been.

**What re-checking cannot do, stated plainly.** An `Authority` is built when the document is
loaded — `Authority.from_yaml`, `Control.from_file()` — and v0.3 does **not** hot-reload it.
Revocation and expiry are live, because they are read from the store and from the clock on every
evaluation. An **edit to the file** is not: narrowing a root grant's `amount_lte`, bringing its
`expires_at` forward, removing `delegable`, lowering `max_delegation_depth` or deleting a grant
takes effect when the process next loads the document, which for `ctrlrun gateway` means a
restart. Every "MUST take effect" in this section is scoped to that.

The operational consequence is the one worth knowing: **the runtime kill switch is
`ctrlrun revoke`, and it covers delegations only.** There is no runtime revocation for a root
grant. A compromised root grant is answered by editing the file and restarting the process, or by
revoking the delegations beneath it. Hot reload, `SIGHUP` and a root-grant revoke are all recorded
as out of scope in §13 rather than implied by prose here.

T77b and T79 therefore each run against **one long-lived `Control`** for the live half — parent
expiry, revocation, depth — and reload explicitly for the file half, so neither can pass by
rebuilding the world between the two halves.

**The signature scenario.**

```
root grant   head-of-finance   amount_lte 10000000   delegable: true       depth 0
  └─ dlg_1   finance-agent     amount_lte  2500000   delegable: true       depth 1
       └─ dlg_2 support-agent  amount_lte   200000   delegable: false      depth 2
```

`support-agent` requesting a €50,000 refund (`amount=5000000`) matches `dlg_2` on subject,
action, resource and environment, and fails its `amount_lte` — `authority_constraint`. Requesting
€1,500 (`amount=150000`) passes authority, and policy then decides how much autonomy it has.

The creation half of the story needs two different attempts, because two different guards refuse
them and a single one would hide whichever ran second:

- `finance-agent` attempting to delegate €50,000 to `support-agent` under its own €25,000 parent
  is refused at §5.3 rule 6 — `containment`, `data.dimension = "constraints"`. This is the guard
  the containment machinery exists for.
- `support-agent` attempting to delegate anything at all is refused at §5.3 rule 2 —
  `parent_not_delegable` — whatever the amount, because `dlg_2` is not delegable.

T80 asserts both, by reason, so neither can stand in for the other. An escalating creation never
becomes a record that a later evaluation has to catch.

### 5.7 Revocation, and the two CLI commands

```
ctrlrun delegate --parent <grant_id> --file <grant.yaml> --as <agent>[/<user>] [--json]
ctrlrun revoke <delegation_id> [--by <who>]
```

Both go through `Control` (§4.8), so both write through the store and fan out to every registered
sink.

`ctrlrun revoke` sets `revoked_at` and `revoked_by` and appends `DELEGATION_REVOKED`. Revocation
is **not** reversible in v0.3 — there is no `unrevoke` — because the operation whose safety
matters is the one taken in a hurry, and an operator who revoked the wrong delegation creates a
new one, which leaves a record of both acts.

Revocation is **transitive by structure**: a revoked delegation breaks the chain for everything
beneath it, because §5.6 rule 2 walks to the root on every evaluation. Nothing is rewritten and no
children are visited at revoke time; a chain of any depth is cut by one write.

Revoking an already-revoked delegation is idempotent: it logs, appends no second event, and exits
0. Revoking an unknown id exits non-zero with a message on stderr and prints nothing on stdout, as
`ctrlrun inspect` does for an unknown action (`v0.2 §5`).

`ctrlrun delegate` reads a one-grant YAML document with the keys of §4.2 minus `id` (§5.2),
validates it, runs every check of §5.3, and prints the new `delegation_id`. Its refusals exit
non-zero and name the rule that failed.

**`--as` is an assertion, and the record says so.** It supplies the creating principal for §5.3
rule 4, and there is no default, because the default would be "whoever the store belongs to",
which is not a principal. But it is free text typed by whoever runs the command, checked against a
grant that same person can read — so at the CLI, rule 4 is satisfied by naming the right principal
rather than by being it. That is why the record carries `created_via` (§5.2): `api` for
`Control.delegate`, where `by` came from wherever the application's identity came from, and `cli`
for this command, where it came from a shell. A reader of the evidence can tell an act from an
assertion, which is the whole reason the field exists.

CLI delegation is an operator act inside the trust boundary, exactly as `ctrlrun approve` is
(`docs/THREAT_MODEL.md`: "A compromised approver … is out of scope"). §13 records that v0.3 does
not authenticate the delegator any more than it authenticates the approver, and item 6 puts the
same sentence in the threat model.

---

## 6. Observe mode

### 6.1 What it is for

Nobody puts an enforcement kernel in front of a live agent fleet and turns it on. Observe mode is
the rollout path: run the real decisions against real traffic, record what *would* have been
blocked, and read the numbers before anything is enforced.

```yaml
schema: ctrlrun.policy/v3
mode: observe          # or enforce; enforce is the default
```

`mode` is **top-level and nothing else**. A `mode:` key inside an action entry, inside a rule,
inside a grant, or anywhere in the `authority:` section is a `PolicyError` at load naming the path
(T84), and so is a `mode:` in a `--authority` document (§8.3). A partially-enforced configuration
— some actions observed, some enforced — is the failure mode this rule exists to prevent: it
produces a deployment where nobody can say whether an action was permitted or merely watched, and
where turning enforcement "on" is a per-action archaeology exercise. There is one switch and it
governs the process.

The value MUST be exactly `observe` or `enforce`. Anything else, including `true`, `off` and `0`,
is a `PolicyError`. Absent → `enforce`: the fail-closed default, so a document that predates this
key enforces, and a typo in the key name (`Mode:`) is caught by the closed top-level key set of
`v0.1 §3.1` rather than silently disabling every guarantee.

### 6.2 What observe mode does, and what it still refuses

**Everything is evaluated. Nothing about an action is enforced. Everything is recorded.**

In observe mode, an action that would have been denied, or would have needed a human, or would
have been refused as a duplicate, **executes**, and `Control.execute` **returns a receipt** rather
than raising: no `ActionDenied`, no `AuthorityDenied`, no `ApprovalRequired`, no `ApprovalMismatch`,
no `DuplicateEffect`, no `AmbiguousEffect`. A caller that saw an exception would not be observing.
Evaluation is otherwise identical to enforce mode, and that identity is the point: an observe-mode
deployment that took shortcuts would be measuring something other than what enforcement will do.

Specifically, in observe mode:

- The identity provider runs; `principal_expired` (§2.3) is evaluated and recorded, not enforced.
- Authority is evaluated in full — grants, constraints, chains, revocation — and recorded.
- Policy is evaluated in full and recorded. `AUTHORITY_RESOLVED` / `AUTHORITY_DENIED` and
  `POLICY_EVALUATED` are appended exactly as in enforce mode, including §4.3's rule that a denial
  by authority skips policy.
- The effect key is resolved and the reservation is **attempted**. A refusal — duplicate,
  in-progress, ambiguous — is recorded as `would_have.blocked_reason` and then ignored, and the
  executor runs anyway.
- A receipt is written for every action, with `result: "observed"` (§6.3).

**Where the reservation succeeded**, the executor's outcome maps through `v0.1 §5.5` unchanged and
this attempt's effect record is written: `COMMITTED`, `FAILED` or `AMBIGUOUS`. Observe mode records
what happened; it does not stop recording it.

**Where the reservation was refused**, the executor still runs and **no effect record is written**.
The record belongs to the attempt that holds the key, and observe mode does not make a second
attempt its owner. Two reasons, and the second is not negotiable:

- The store would refuse it anyway. `commit_effect`, `fail_effect` and `mark_ambiguous` admit only
  the `action_id` that holds the record (`v0.1 §5.3 E1`), so writing here would need a new
  transition that lets a non-holder write — in the one mode where the guard is already relaxed.
- A record already in `AMBIGUOUS` would be moved to `COMMITTED` by a machine. `v0.1 §5.2` reserves
  that for a human, and `v0.2 §2.2` amends it to admit exactly one other authority, the
  `reconcile` hook. Observe mode is neither, and a rollout mode that silently closes an incident a
  human is still adjudicating is worse than the enforcement it stands in for.

The reservation *attempt* keeps its own effects: an expired lease still moves a record to
`AMBIGUOUS` (`v0.1 §5.4`), because that is the reservation speaking rather than this attempt's
outcome. The refused attempt's outcome is not lost — it is on its own receipt, as `execution` and
`would_have.blocked_reason` (§6.3).

**No approval request is created.** A policy reaching `approve` is recorded as
`would_have.blocked_reason: "approval_required"` and the action runs. Observe mode MUST NOT page a
human about an action it is about to execute regardless of the answer, and a pending request
nobody answers is not evidence — it is a queue that fills up. No `APPROVAL_REQUESTED` event, no
record in the store, no `ApprovalRequired` raised.

**The `reconcile` hook is not called.** `v0.2 §2.3`'s blocking trigger fires "at the moment an
existing `AMBIGUOUS` record refuses a new attempt's reservation"; in observe mode that refusal is
ignored, so there is nothing being unblocked and nothing to ask about. Calling it would make a
real request to a remote on behalf of a mode that enforces nothing, and its `"committed"` answer
would move a record — the same collapse the previous paragraph refuses, arriving by another door.
Eager reconciliation (`reconcile_eagerly=True`) is likewise not called for an attempt that held no
reservation; for one that *did* reserve, both triggers behave exactly as in enforce mode, because
that attempt owns its record.

**A `Suspended` executor holds nothing it did not reserve.** If the executor raises `Suspended`
(`v0.2 §6.9`) on an attempt whose reservation was refused, there is no lease to extend and no
continuation to hold: the signal propagates to the caller unchanged and no continuation is
recorded, which is `v0.2 §6.9.3`'s no-effect-key path reached for a different reason. An attempt
that *did* reserve suspends exactly as in enforce mode.

**What observe mode does not suspend.** It suspends *decisions about an action* and *effect-state
refusals*. It does not suspend the refusals that mean CTRLRun cannot construct or identify the
action at all, and it does not suspend the creation of durable authority:

| Still refuses in observe mode | Why |
|---|---|
| `no_principal` (`v0.1 §2.1`) | There is no Action to observe, and nothing to attribute a receipt to |
| A raising identity provider (§3.2) | Same: no principal was produced |
| `effect_key_error` (`v0.1 §5.1`) | The action has no identity, so "would it have been a duplicate" has no answer |
| `unrepresentable_argument` (`v0.2 §6.6`) | The Action cannot be constructed |
| `AuthorityEscalation` at delegation creation (§5.3) | A delegation is a durable authority record, not a decision about an action — and T87 makes records created under observe survive the switch to enforce |
| `PolicyError` at load | There is no configuration to observe with |

The line is drawn where it is because everything above is either a wiring bug in the deployment or
an act of authority, not a decision about an action. Observing a wiring bug means running an
action CTRLRun could not describe, and a receipt that cannot say what ran is not evidence.

T82b asserts the first four rows and the delegation row by name. The last row is **unfalsifiable
and carries no test**: an implementation cannot know the mode without loading the configuration,
so counting it as coverage would be a negative test against something the environment already
prevents. It is in the table because a reader will look for it.

### 6.3 The receipt in observe mode

```json
{
  "schema": "ctrlrun.receipt/v2",
  "result": "observed",
  "execution": "committed",
  "would_have": {
    "decision": "deny",
    "reason": "authority_constraint",
    "blocked_reason": "authority_constraint"
  }
}
```

- **`result`** is `"observed"` for every receipt an observe-mode run *observed* — including
  actions nothing would have blocked, so a reader never has to infer from the absence of a field
  that a receipt describes an unenforced run. A receipt written by one of §6.2's **still-refuses**
  rows is **not** such a receipt: it says `result: "denied"`, with `execution: null` and
  `would_have: null`, because the action genuinely was stopped and there is nothing counterfactual
  to record. `would_have` being present on every observed run and absent on every refused one is
  what keeps the "never infer from absence" property true in both directions.
- **`execution`** is what the executor actually did — `committed`, `failed` or `ambiguous` — or
  `null` where it never ran. In enforce mode `execution` is always `null`; `result` already
  carries it, and duplicating it would give two fields that can disagree.
- **`would_have.decision`** is the combined decision of §4.6 — what enforce mode would have
  reached.
- **`would_have.reason`** is the `decision_reason` that decision came with.
- **`would_have.blocked_reason`** is `null` when the action would have run unimpeded, and
  otherwise names what would have stopped it: `approval_required`, `duplicate`, `in_progress`,
  `ambiguous`, `approval_mismatch`, `principal_expired`, `unknown_action`, `no_matching_rule`, or
  one of §4.3's five **deny** reasons — never `authority_grant`, which records a pass. It is separate from `decision` because "the policy said allow
  and the effect was already committed" is a real and common answer, and a single field could not
  hold both halves.

`decision` and `decision_reason` at the top level keep their enforce-mode meaning: the decision
that was *reached*. `would_have` says what would have been *done* with it. The pair is not a
duplicate — one is the combined §4.6 result, the other is the consequence — and where they look
alike it is because nothing intervened between them.

### 6.4 `ctrlrun stats`

```
ctrlrun stats [--since <ISO-8601 | 30m | 24h | 7d>] [--json]
```

Counts, from the **local store and nothing else** — no network, no aggregation service, no upload.
This is a command that reads receipts out of the SQLite file the process it is diagnosing has been
writing.

```
CTRLRun — 2026-09-01T00:00:00Z .. 2026-09-04T09:11:00Z   (observe mode)

actions                       1284
would have been denied          37   (2.9%)
   no_authority                 21
   authority_constraint          9
   unknown_action                7
would have needed approval      64
would have been blocked         12
   duplicate                     9
   ambiguous                     3
ambiguous outcomes               3
```

Every number comes from one field: **`would_have.blocked_reason`**, plus `would_have.decision` for
the denial count and `execution` for the ambiguous count. That is why §6.3 defines
`blocked_reason` as a closed vocabulary rather than free text — a bucketed count over a string
nobody constrained is a report that quietly stops adding up.

**In enforce mode the same command reports what actually happened**, and it reports less. There is
no `would_have` object on an enforce-mode receipt, so `ctrlrun stats` counts `denied` receipts by
`decision_reason` and `ambiguous` receipts by `result`, and it **omits the duplicate/ambiguous
breakdown**, because an enforce-mode `blocked` receipt carries the policy reason and keeps the
distinction only inside `error` as exception text (`v0.1 §6.1`). The command says so in its footer
rather than printing a line it cannot substantiate. `# SPEC:` adding a structured `blocked_reason`
to enforce-mode receipts would close this, and it is deliberately not done in v0.3: it would put a
counterfactual field on a receipt describing something that was not counterfactual.

`--since` compares **`finished_at`**, inclusive at the boundary, and accepts an absolute ISO-8601
timestamp with an offset or a relative `<n><unit>` where unit is exactly one of `m` (minutes), `h`
(hours) or `d` (days). No months, no weeks, no bare numbers; anything else exits non-zero naming
the accepted forms. Absent → everything in the store.

`--json` emits one object under `schema: "ctrlrun.stats/v1"` with the same numbers, so the output
can be diffed between runs.

An action is counted once, by its receipt. Actions still awaiting a human have no receipt (`v0.1
§6.1`) and are not counted; the command says so in its footer rather than leaving the arithmetic
unexplained. An empty store prints zeros and exits 0.

### 6.5 The banner, and `ctrlrun verify`

**Every CLI command that loads the operator's policy MUST print, to stderr, before anything
else:**

```
OBSERVE MODE — nothing is enforced
```

To stderr, so a `--json` stdout stays machine-readable and a pipeline cannot silently swallow the
warning. On every invocation, not once per day and not behind a flag: a deployment that has been
in observe mode for six months is exactly the deployment this line is for.

`# SPEC:` the item-0 brief says "on every command". A command that loads no policy cannot know the
mode, so the rule is "every command that loads the operator's policy", and the two sets are named
here so the carve-out cannot silently grow:

| Loads the policy in v0.3, and prints the banner | Does not load it, and does not print |
|---|---|
| `gateway`, `stats`, `delegate`, `revoke`, `verify` | `init`, `demo`, `approve`, `deny`, `receipts`, `effects`, `resolve`, `inspect` |

The right-hand column is not an oversight, and v0.3 does **not** move any command across it.
`state_path()` exists so the CLI can find the store an agent is using *without loading the policy*
(`v0.1 §8`); making `ctrlrun receipts` or `ctrlrun inspect` load one would turn a missing or
malformed policy into a failure of the evidence commands, and break every deployment that points
`$CTRLRUN_STATE` at a store with no policy beside it. Reading evidence must not depend on the
configuration that produced it.

The commands in the left-hand column already need the policy — the gateway to serve it, `stats` to
know which mode it is reporting, `delegate` and `revoke` to load the `authority:` section. Where
one of them cannot load it, that is a `PolicyError` and the command exits non-zero, exactly as
`Control.from_file()` does (`v0.1 §3.4`); the banner is not printed, because there is no mode to
report. T85 asserts both columns by name.

**`ctrlrun verify`** is added as a stub that refuses: it prints, to stderr, that verification
lands in v0.4 and exits **2**. It exists in v0.3 for one reason — observe mode's whole purpose is
to lead somewhere, and the command an operator will reach for next should not be a
`No such command` error that suggests they mistyped. It runs nothing, checks nothing, and claims
nothing.

### 6.6 Switching modes

Switching `observe` → `enforce` is a one-line edit and requires **no state migration**: the same
store, the same tables, the same effect records, the same delegations (T87). An effect that
reached `COMMITTED` while observing is a committed effect, and the first enforced action on that
key is refused as a duplicate — correctly, because it *was* one. Effect records that observe mode
declined to write (§6.2) were never this deployment's to write, so nothing is missing.

The corollary is worth stating: **observe mode is not a dry run.** It executes. Effects land at
remotes, and the records of them are real. It is a way to learn what enforcement would cost, not a
sandbox.

---

## 7. Events

Five types join the closed set of `v0.1 §6.2` and `v0.2 §2.5`:

```
AUTHORITY_RESOLVED
AUTHORITY_DENIED
DELEGATION_CREATED
DELEGATION_REVOKED
DELEGATION_REJECTED
```

| Type | `data` |
|---|---|
| `AUTHORITY_RESOLVED` | `grant_id`, and for a delegated grant `delegation_id` and `depth` |
| `AUTHORITY_DENIED` | `reason` (§4.3); `grant_id` / `delegation_id` where one grant was implicated; `dimension` for a §5.6 rule-4 failure; `missing_parent_id` for a §5.6 rule-1 failure |
| `DELEGATION_CREATED` | `delegation_id`, `parent_id`, `depth`, `created_by`, `created_via` |
| `DELEGATION_REVOKED` | `delegation_id`, `revoked_by` |
| `DELEGATION_REJECTED` | `reason` and `parent_id`; `dimension` **only** for a §5.3 rule-6 refusal |

`AUTHORITY_RESOLVED` is appended for **every** action that passes authority, not only for
delegated ones. Evidence has to record that CTRLRun checked and found a grant, or a deployment
with a permissive grant is indistinguishable from one with no authority section at all — the same
argument `v0.2 §2.5` makes for appending `RECONCILIATION_RESOLVED` on `"unknown"`.

`dimension` is present only where a §5.4 row failed. The other refusals of §5.3 — `unknown_parent`,
`parent_not_delegable`, `parent_not_valid`, `not_the_subject`, `max_depth` — name no row, and
inventing a dimension for them would make two distinguishable guards report the same shape.

**`Event.action_id` becomes `str | None`.** The three delegation events are not about an action:
they are about an authority record, created and revoked outside any action's life. They carry
`action_id: null` and name the delegation in `data.delegation_id`. The alternative — inventing a
synthetic `action_id` — would put a value in a field that every reader takes to name a real
proposal. No column is added to the `events` table for this; `action_id` is already nullable in
its DDL, and adding a column would need the migration story v0.3 does not have (§5.2).

**What a sink does with an action-less event.** `v0.2 §4.1` requires every `Event` to reach every
sink, and `Control` appends and fans out all five of these (§4.8), so they do. `JSONLEventSink`
writes them with `"action_id": null`, unchanged. `OTelEventSink` MUST emit a **standalone span**
named for the event type, carrying `ctrlrun.delegation_id` and `ctrlrun.parent_id`, rather than
looking for an open span it will never find and dropping the event — which is what the v0.2 sink
would do by default, silently, leaving an operator whose evidence pipeline is OTel to watch a
delegation chain appear and be revoked with nothing in the trace. The highest-privilege operations
in the release must not be the only ones missing from the export path.

`ctrlrun inspect <action_id>` is unaffected: it selects by `action_id`, and rows with `NULL` never
match. A delegation's history is read from the events file and `ctrlrun stats`; there is no
`ctrlrun delegations` in v0.3 (§13).

---

## 8. Gateway

### 8.1 `--principal-from-client-info` is removed

The flag is **gone**, not deprecated further. `ctrlrun gateway --principal-from-client-info` MUST
exit non-zero with a message naming its replacement:

```
--principal-from-client-info was removed in 0.3. Use --principal-header NAME, set by a
proxy that authenticates the caller and overwrites the header on every request.
```

A hard error, not a silent ignore and not a warning-and-continue. The flag chose the gateway's
principal; a gateway that started anyway would be running with a *different* principal than the
operator asked for, and under v0.3 the principal is an authorization input.

`v0.2 §6.5` recorded the expiry condition when it added the flag: it was survivable only while a
policy could not address the principal at all, so an unauthenticated one misattributed evidence
and could not widen an outcome. §4 ends that, exactly as `v0.2 §6.5` said it would.

### 8.2 Identity selection

`--principal AGENT` and `--principal-header NAME` remain, and are now understood as constructors:
`--principal` builds a `StaticIdentityProvider`, `--principal-header` builds a
`HeaderIdentityProvider` (with `--user-header`). Item 5 adds a third, `--identity-jwt`.

**Exactly one identity source MUST be given**, and there is still no default (`v0.2 §6.5`).
`--principal`, `--principal-header` and `--identity-jwt` are mutually exclusive; giving two, or
none, exits non-zero naming the three. `--user-header` is accepted only with `--principal-header`
(with `--identity-jwt`, the user comes from `--identity-jwt-user-claim`; with `--principal`, from
nowhere), and every `--identity-jwt-*` flag is accepted only with `--identity-jwt`. A flag that
cannot take effect is a flag the operator believes took effect, so it is an error rather than a
warning.

```
--identity-jwt                          select JWTIdentityProvider (ctrlrun[identity])
--identity-jwt-jwks-url URL             | exactly one of these three
--identity-jwt-public-key PATH          |
--identity-jwt-secret-file PATH         |
--identity-jwt-algorithms ALG           repeatable; required
--identity-jwt-issuer ISS               required
--identity-jwt-audience AUD             required
--identity-jwt-token-type TYP           required; pass "" for "this issuer sets no typ"
--identity-jwt-header NAME              default: authorization
--identity-jwt-agent-claim NAME         default: sub
--identity-jwt-user-claim NAME          optional
--identity-jwt-claim NAME               repeatable; which claims reach the receipt
--identity-jwt-leeway SECONDS           default: 60
--identity-jwt-jwks-min-refresh SECONDS default: 30
```

The secret is read from a **file**, never from a flag value: a shared secret on a command line is
in every process listing on the host.

The gateway fills `IdentityContext.headers` with the request's headers, names lowercased, and
`IdentityContext.action` with the action name it is about to propose. §3.1's repeated-header rule
applies before that mapping is built.

A refusal at the identity gate returns `-41007` `ctrlrun.no_principal`, HTTP 403, no receipt and
no events — `v0.2 §6.5` unchanged. A token that was present and invalid returns the same code with
a distinct message; the client learns that its credential was rejected and learns nothing about
why, which is the correct amount to tell an unauthenticated caller.

### 8.3 Authority at the gateway

```
--authority PATH        load the authority: section from a separate YAML document
```

Authority is normally the `authority:` section of the policy file. `--authority` loads it from a
separate document instead, because the person who writes grants and the person who writes
per-action autonomy are often not the same person, and a gateway is where that separation shows up
first.

**That document is not a policy document.** Its top-level key set is closed at exactly `schema`
and `authority`: `actions:` or `mode:` in it is a `PolicyError` at load naming the file and the
key, and `v0.1 §3.1`'s requirement that a policy document carry `actions:` does not apply to it.
Two sources for `mode:` would be two switches, which is the one thing §6.1 says there is not.

If **both** the policy file and `--authority` declare `authority:`, the gateway MUST refuse to
start, naming both paths. Two sources for one section is the ambiguity `v0.1 §5.1` refuses for
`{resource}`: pick one, or say which.

**The approval gate uses the combined decision.** `v0.2 §6.10`'s approval flow keys off the
action's decision, and since §4.6 that is the combined one. Two places in shipped v0.2 code read
`Policy.evaluate` directly to decide whether to look for a granted approval — the gateway's
`tools/call` path and `ctrlrun.acs`'s request hook — and both MUST be changed to use the combined
result, and to evaluate authority first so §4.3's ordering holds on the gateway path too. They are
named here so item 5 cannot miss them.

**A denial outside the grant is `-41012`.** A tool call outside the principal's grant returns
JSON-RPC error **`-41012` `ctrlrun.unauthorized`**, HTTP 403, with the same `data` shape as
`-41001` (`reason`, `action_id`) where `reason` is the §4.3 reason, and the **upstream is never
called** (T91). A denied receipt and the events of §4.3 are written, because there is a principal.

This narrows `v0.2 §6.10`'s table: its `DENY → -41001 ctrlrun.denied` row now means a **policy**
denial. Because §11 makes `AuthorityDenied` a subclass of `ActionDenied`, the gateway MUST catch
`AuthorityDenied` **before** `ActionDenied`, or the new code is unreachable and every authority
denial reports `-41001` while every test that asserts only "it was refused" stays green. T91
asserts both halves — an authority denial returning `-41012` and a policy denial under the same
`authority:` section still returning `-41001` — so the discrimination is pinned rather than
assumed.

`# SPEC:` the item-5 brief said "JSON-RPC error ctrlrun.denied reason no_authority", i.e. reuse
`-41001`. A distinct code earns its keep because the two refusals are answered differently by a
client: `-41001` means this action is not permitted to anyone in this configuration, and `-41012`
means it is not permitted to *you* — the second is worth a different message and, in a
multi-tenant deployment, a different alert.

**Startup output.** The gateway already prints every action in its policy with no `effect:`
(`v0.2 §3.2`). It now also prints, in the same startup block:

- the identity provider in force, by name, and for `HeaderIdentityProvider` the header it trusts
  and the one-line warning of §3.3;
- whether an `authority:` section is loaded and how many grants it holds — or, when there is none,
  the single line `no authority: section — every principal is unrestricted`, so an operator who
  believes they configured authority finds out on the line that starts the process;
- in observe mode, `OBSERVE MODE — nothing is enforced`, on its own line, before everything.

**In observe mode the gateway forwards everything.** It returns no `-41001`, no `-41012`, no
`-41002`; the client sees the upstream's response, and the would-have decision lives in the
receipt. A gateway that returned an error while claiming not to enforce would be enforcing.

---

## 9. Fail-closed table for v0.3

`v0.1 §3.4` and `v0.2 §6.11` hold in full, and so does every row below, **in enforce mode**.

`mode: observe` (§6) is the one switch that changes this, and it changes it wholesale rather than
per row: under `mode: observe` every row marked ⚠ is evaluated and recorded as
`would_have.blocked_reason` (§6.3) instead of refused, and the action executes. The rows **not**
marked ⚠ refuse in both modes, and they are §6.2's table. That is what "there is no half-way"
means — one switch, all of it or none of it, never a per-row or per-action opt-out. **No row here
is individually configurable**, and there is no flag that makes any single one of them permissive.

| Condition | Result | ⚠ |
|---|---|---|
| `identity` set, `resolve()` returns `None`, no `context()` | `ActionDenied(reason="no_principal")`; no receipt, no events (`v0.1 §2.1`) | |
| `identity` set, `resolve()` returns `None`, `context()` active, **`authority:` loaded** | `ActionDenied(reason="no_principal")`; the context does not fill in (§3.2) | |
| `identity` set, `resolve()` raises | `IdentityError`; no receipt, no events; the context does not fill in | |
| Any configured identity header repeated in one request | `-41007`, HTTP 403; no receipt, no events (§3.1) | |
| `principal.expires_at` in the past | `IdentityError`, `decision_reason="principal_expired"`; denied receipt; policy never runs | ⚠ |
| `principal.expires_at` passes during an action: lease extension | refused; the lease lapses and the record becomes `AMBIGUOUS` (§2.3.1) | ⚠ |
| JWT: more or fewer than one of `jwks_url` / `public_key` / `secret` | `InvalidArgument` at construction | |
| JWT: `HS*` with `jwks_url` or `public_key`, or an asymmetric alg with `secret` | `InvalidArgument` at construction | |
| JWT: `token_type` not passed | `InvalidArgument` at construction | |
| JWT: `alg` absent from the configured list, or `none` | `IdentityError`; the signature is never checked | |
| JWT: `typ` present and not the configured `token_type` | `IdentityError` | |
| JWT: `exp` absent, or expired beyond leeway; `nbf` in the future | `IdentityError` | |
| JWT: `iss` mismatch, or `audience` not among `aud` | `IdentityError` | |
| JWT: `kid` still unknown after one refresh, or inside the refresh window | `IdentityError` | |
| JWT: two JWKS entries share a `kid` | `IdentityError`; never first-match (§3.4) | |
| JWT: JWKS fetch fails | `IdentityError`; never an empty key set, never a cached-forever key | |
| `authority:` present, no grant matches | `AuthorityDenied(reason="no_authority")`; denied receipt; no approval request | ⚠ |
| `authority:` present, a constraint fails | `AuthorityDenied(reason="authority_constraint")` | ⚠ |
| `authority:` present, every matching grant expired | `AuthorityDenied(reason="authority_expired")` | ⚠ |
| Delegated grant no longer contained in its parent | `AuthorityDenied(reason="authority_escalation")` | ⚠ |
| A delegation in the chain is revoked | `AuthorityDenied(reason="authority_revoked")` | ⚠ |
| An ancestor of a delegated grant has expired | `AuthorityDenied(reason="authority_escalation")` (§5.6 rule 3) | ⚠ |
| A parent grant has been deleted from the document | `AuthorityDenied(reason="authority_escalation")`, `missing_parent_id` | ⚠ |
| An ancestor is no longer `delegable` | `AuthorityDenied(reason="authority_escalation")` (§5.6 rule 6) | ⚠ |
| A chain walk revisits a delegation, or exceeds the depth bound | `AuthorityDenied(reason="authority_escalation")` (§5.5) | ⚠ |
| Delegation creation: any §5.4 dimension violated | `AuthorityEscalation(reason="containment")`; no record; `DELEGATION_REJECTED` | |
| Delegation creation: parent unknown / not `delegable` / not valid | `AuthorityEscalation` naming which (§5.3) | |
| Delegation creation: creator is not the parent's subject | `AuthorityEscalation(reason="not_the_subject")` | |
| Delegation creation: depth would exceed the maximum | `AuthorityEscalation(reason="max_depth")` | |
| Delegation creation: child subject carries a wildcard, omits `agent`, or drops the parent's `user` | `AuthorityEscalation(reason="containment")`, `dimension="subject"` | |
| A stored delegation whose `grant_json` no longer parses | `AuthorityDenied`; never a grant with a field defaulted (§5.2) | ⚠ |
| `authority:` present but malformed, or `grants` missing | `PolicyError` at load; no `Control` | |
| A pattern outside §4.4's grammar | `PolicyError` at load | |
| `mode:` anywhere but the top level of the policy document | `PolicyError` at load | |
| `mode:` not exactly `observe` or `enforce` | `PolicyError` at load | |
| `authority:` or `mode:` in a `v1`/`v2` document | `PolicyError` at load, naming the key and `ctrlrun.policy/v3` | |
| `actions:` or `mode:` in an `--authority` document | `PolicyError` at load, naming the file and the key | |
| Gateway: `--principal-from-client-info` | exit non-zero, naming `--principal-header` | |
| Gateway: two identity sources, or none | exit non-zero, naming the three | |
| Gateway: authority declared in two places | refuses to start, naming both | |

---

## 10. Acceptance tests

Each MUST exist as a pytest test with the given ID in its name, as `v0.1 §7` and `v0.2 §10`
require. All MUST pass for v0.3, and every test of `v0.1 §7` and `v0.2 §10` MUST still pass.

The item-0 brief scoped this section as T60–T85; items 4 and 5 add T86–T92 and item 6 adds T93, so
the range is **T60–T93**. Numbers are stable and never reused; the range names its endpoints, and
suffixed ids (T65b, T74b, T82c…) fill in between them where a rule earned its own test after the
brief was written.

**Every test that asserts a refusal asserts it by name** — the `reason`, not merely the exception
class — wherever §4.3, §5.3 or §6.3 defines a vocabulary. Five deny reasons that all raise
`AuthorityDenied` are five guards a test asserting only the type cannot tell apart.

### Item 1 — Principal and identity (§2, §3)

#### T60 — Claims are receipt data, not action identity
Two Actions identical but for `principal.claims` — one with none, one with a full mapping — have
the **same** `action_hash`, and `canonicalize()` returns identical bytes. The claims appear in the
receipt's `principal` object. An approval granted against the first authorizes the second.
Changing `agent` or `user` still changes the hash (`v0.1 §7 T7`).

#### T60b — `Principal` validates what §2.1 says it validates
A `float` claim value, a `None` claim value, a list or mapping claim value, a non-string or empty
claim key, and a naive `expires_at` each raise `InvalidArgument`. `claims` is snapshotted: mutating
the mapping passed in does not change the constructed `Principal`, and `True` and `1` are distinct
claim values.

#### T60c — The stored Action carries the whole principal
An action whose principal carries claims, an issuer and an expiry is suspended (`v0.2 §6.9`) and
resumed. The receipt written by `Control.resume` carries all three, unchanged. A row written in
the v0.2 shape parses back with `v0.1 §2.1`'s defaults rather than raising.

#### T61 — An expired principal is refused before policy
A principal whose `expires_at` is in the past, on an action the policy would `approve`. Then
`IdentityError` is raised, the receipt is `denied` with `decision_reason="principal_expired"`, the
store holds **no** approval request, and the executor never ran. The `else` branch — the action
having executed — fails the test. Asserted as an ordering, not an absence: the same action with a
valid principal produces `AUTHORITY_RESOLVED` and `POLICY_EVALUATED`, and the expired one produces
neither, so the assertion is a difference between two runs rather than a claim that nothing
happened.

#### T61b — An expiry that falls mid-action
Parametrized over §2.3.1: a principal that expires after `EXECUTION_STARTED` still commits (the
record reaches `COMMITTED`, no exception); a lease extension under the same expired principal is
refused with `IdentityError` and the record is left for the lapsing lease to move; a
`Control.resume` under it records the expiry and does not re-decide, and the resumed leg produces a
receipt.

#### T62 — A provider returning `None` with no context is `no_principal`
`ActionDenied(reason="no_principal")`, a warning naming the action on the `ctrlrun` logger, and the
store holds no receipt and no events — `v0.1 §2.1` unchanged.

#### T63 — `StaticIdentityProvider` warns once
Constructing it logs one warning naming the agent. Resolving one hundred actions logs no further
warning. The count is asserted exactly, not as "≥ 1": a test that checked only for presence would
pass a provider that warned on every call.

#### T64 — `HeaderIdentityProvider` with a missing header declines
Missing header → `None`. Empty header → `None`. Whitespace-only header → `None`. Present header →
a `Principal` with that agent. Header name matching is case-insensitive. `user_header` present and
absent, and its construction warning, asserted the same way as T63.

#### T65 — Provider and `context()` precedence, every row
Parametrized over §3.2's table, including both `resolve() → None` rows: with an `authority:`
section loaded, the decline is `ActionDenied(reason="no_principal")` and the executor does not run;
without one, the context fills in and it does. The provider-answers row asserts the resulting
`Principal` **and** that a disagreeing `context()` logged one warning naming both. The
provider-raises row asserts `IdentityError`, that the executor's call count is 0, and that no
receipt was written — three behaviours, because asserting only the exception class cannot tell a
refusal from a refusal that already ran the action.

#### T65b — `--principal-from-client-info` is gone
`ctrlrun gateway --principal-from-client-info ...` exits non-zero, and stderr names
`--principal-header`. The gateway does not start; no socket is bound.

#### T65c — A repeated identity header is refused
Through the gateway, with `--principal-header X-Principal`: a request carrying that header twice
returns `-41007`, HTTP 403; the upstream request count is 0; no receipt and no event exist. Repeated
for `--user-header` and for the `--identity-jwt` header. The recorder is checked to prove it would
have seen a forwarded request had one been made.

### Item 2 — Authority (§4)

#### T66 — No `authority:` section is v0.2, exactly
`Control.from_file()` on a document with no `authority:` key leaves `Control.authority is None`,
and no `AUTHORITY_*` event is ever appended by any test in the suite. The mechanical half is a
session-scoped assertion, not a re-run of the suite: an autouse fixture records every event type
appended anywhere in the run, and this test asserts the five §7 types appear only in tests that
built an `authority:` section. That is what "every v0.2 test passes unchanged" can actually mean
inside one pytest process — the suite already runs, and a second copy of it would prove nothing the
first does not.

#### T67 — A principal with no grant is denied
`authority:` present, a grant for a different agent. Then `AuthorityDenied(reason="no_authority")`,
a `denied` receipt with `decision="deny"` and `decision_reason="no_authority"`, no approval request
in the store, and the executor never ran.

#### T67b — Subject matching, by name
Parametrized over §4.2: a grant naming a user against a principal with none → denied
`no_authority`; against a different user → denied; a grant omitting `user` against a principal that
has one → passes; `agent: "*"` against a dotted agent name; a `subject.agent` prefix wildcard
against a name sharing only the prefix → denied. Each asserts the reason, so deleting the
`user is not None` check fails a test rather than silently letting an agent inherit a human's
grant.

#### T68 — A matching grant passes to policy
Subject, action, resource and environment all match and the constraints hold. Then
`AUTHORITY_RESOLVED` names the grant, `POLICY_EVALUATED` follows it, and the action executes.

#### T69 — A failed constraint denies what policy would allow
Policy says `allow`. The grant's `amount_lte` excludes the action. Then
`AuthorityDenied(reason="authority_constraint")` — by reason, so removing the constraint check
cannot pass as `no_authority`.

#### T69b — Only integers can be bounded
A grant whose constraint operand is a decimal string (`amount_lte: "2000.00"`) is a `PolicyError`
at load whose message names the representation rule (§4.5). No `Control` is constructed.

#### T70 — Stricter wins, all six cells
Parametrized over authority ∈ {passes, denies} × policy ∈ {allow, approve, deny}, asserting the
combined decision of §4.6's table in every cell and asserting the recorded `decision_reason` names
the axis §4.6 says it should. `Control.evaluate` is asserted to return the same decision as the
one `Control.execute` acts on, in every cell.

#### T70b — Several matching grants, and order does not matter
Two grants match the same action; the action passes and `AUTHORITY_RESOLVED.grant_id` is the
one whose `id` sorts first (§4.6), not the one written first. Swapping the two in the document
yields the identical decision and the identical `grant_id` — the only assertion that can catch
an implementation that returned whichever grant it happened to reach first.

#### T71 — An expired grant denies
The only matching grant's `expires_at` is in the past under a fake clock.
`AuthorityDenied(reason="authority_expired")`. Advancing the clock to exactly `expires_at` still
passes; one microsecond later does not.

#### T71b — Reason precedence, collected and fixed
One configuration in which three grants match on shape and fail respectively for
`authority_expired`, `authority_constraint` and `authority_revoked`; the reported reason is
`authority_revoked`. A single grant that fails both by expiry and by constraint reports
`authority_constraint`. Permuting the grants in the document yields the identical reason and the
identical `AUTHORITY_DENIED` data — the only assertion that can catch a sort that fell back to
file order.

#### T72 — Pattern matching does not cross a separator
Parametrized over §4.4's table: `stripe.*` matches `stripe.refund` and not `stripe.refund.partial`;
`payment:EU-*` matches `payment:EU-42` and not `payment:EU-42:leg2` and not `payment:US-42`; `*`
matches `refund` and not `stripe.refund`; `**` matches everything; `stripe.**` matches
`stripe.refund.partial` and not `stripe`. Plus, each a `PolicyError` at load: `?`, `[abc]`, a
leading `*`, an infix `*`, `a**`, `**a`, a non-final `**`, and — the case §4.4's grammar has to
exclude explicitly — a wildcard segment before a deep wildcard (`stripe.a*.**`, `*.**`).

#### T72b — Pattern containment, in both directions
Parametrized directly over §5.5's relation, separately from T72's matching relation: `C ⊆ C` for
every well-formed pattern **including** `stripe.**` and `**`; `stripe.refund ⊆ stripe.* ⊆
stripe.** ⊆ **`; `stripe.* ⊄ stripe.refund`; `stripe.refund.partial ⊄ stripe.*`; `stripe ⊄
stripe.**`; `payment:EU-42 ⊆ payment:EU-* ⊆ payment:*`; `payment:EU-* ⊄ payment:EU-4*`; prefix
wildcard ⊄ literal. Containment is a different function from matching, with counterintuitive
clauses, and an implementation of it as `fnmatch` or `startswith` passes T75 and T76 while breaking
attenuation.

#### T73 — A malformed authority section fails at load
Parametrized: `authority:` with no `grants`; `grants` not a list; a grant with no `subject`;
`subject: {}`; a grant with no `actions`; `actions: []`; an unknown key at each of the three levels;
a duplicate `id`; an `id` beginning `dlg_`; a naive `expires_at`; `constraints: {}`; a condition
naming a reserved argument; a `float` operand; `max_delegation_depth: -1`. Each raises `PolicyError`
at load and **no `Control` is constructed** — asserted, not assumed.

#### T73b — `Subject` refuses in Python what the loader refuses in YAML
`Subject()` and `Subject(agent=None, user=None)` raise `InvalidArgument` (§4.8), so
`Control.delegate` cannot be handed a grant addressed to every principal.

#### T74 — Authority is evaluated before policy
An action denied by authority produces `ACTION_PROPOSED`, `AUTHORITY_DENIED`, `ACTION_DENIED` and
**no** `POLICY_EVALUATED`, in that order by `event_id`.

#### T74b — `claims`, `issuer` and `expires_at` are reserved in conditions
`claims_eq:`, `issuer_eq:` and `expires_at_gt:` in a policy rule and in a grant's `constraints` each
raise `PolicyError` at load, with a message telling the author to rename the argument — and in a
`ctrlrun.policy/v1` document as well as a `v3` one, because §4.5's reservation is not schema-gated
and §12.1 says so.

### Item 3 — Delegation (§5)

#### T75 — A contained child is accepted
Narrower on every dimension at once. `DELEGATION_CREATED` is appended **and reaches a registered
`EventSink`**, not merely the `events` table; the record is readable from both stores; the id is
the `dlg_`-prefixed one `Control.delegate` minted, and a `Grant` carrying an `id` is refused. An
action within the child's limits passes authority naming the delegation and its depth. Calling
`delegate` twice with the same grant makes two records with different ids.

#### T75b — Only the parent's subject may delegate
`Control.delegate(..., by=<a principal the parent's subject does not match>)` raises
`AuthorityEscalation(reason="not_the_subject")`, writes no record, and appends
`DELEGATION_REJECTED`. Including the `user` rule: a parent whose subject names a user is not
satisfied by the same agent acting with no user. Repeated through `ctrlrun delegate --as`, which
additionally records `created_via="cli"` while the API path records `"api"`.

#### T76 — Each containment dimension, violated alone
Parametrized, one dimension at a time, everything else contained: a wider `actions` pattern; a wider
`resources` pattern; each constraint operator loosened (`lte` raised, `gte` lowered, `eq` changed,
`neq` changed, `in` widened); an `environments` entry the parent lacks; an `expires_at` later than
the parent's; a child subject carrying a wildcard; a child subject omitting `agent` altogether;
a child subject dropping the parent's `user`.
Each → `AuthorityEscalation(reason="containment")`, `DELEGATION_REJECTED` whose `data.dimension`
names the row, and no record written.

#### T76b — The creation-time rules that are not containment
Parametrized over §5.3 rules 1–5, each asserting the reason **by name**, that
`store.get_delegation` returns nothing for the id that was not created, that exactly one
`DELEGATION_REJECTED` was appended, and that `data.dimension` is **absent**: an unknown
`parent_id` → `unknown_parent`; a parent with `delegable: false` → `parent_not_delegable`; an
expired parent, a parent whose own parent is revoked, and one whose own parent is expired →
`parent_not_valid`; a depth beyond the maximum → `max_depth`. Rule 3 has no other test, and
without this one it is a guard whose deletion §5.6 would silently cover.

#### T77 — Valid at creation, invalid later
A child valid at creation; then the parent's `expires_at` passes under the fake clock. The child
then denies with `authority_escalation`. The delegation record is untouched — the check is at
evaluation, not a rewrite.

#### T77b — A narrowed parent narrows its children, and a non-delegable one stops them
A child at `amount_lte: 25000` under a parent at `100000`; the document is edited to
`amount_lte: 5000` and reloaded. An action at `10000` then denies with `authority_escalation`, and
one at `4000` passes. Repeated for the parent's `delegable` going `true → false`, which denies via
§5.6 rule 6. Nothing rewrote the delegation record. The live half of T77/T78/T79 runs against one
long-lived `Control` that is never rebuilt, so a re-check that only happened at construction fails
red.

#### T77c — A deleted parent is not `no_authority`
A depth-2 chain whose root grant is removed from the reloaded document. `AuthorityDenied` with
`reason == "authority_escalation"`, asserted **not** to be `no_authority`, and the
`AUTHORITY_DENIED` event names the missing parent in `data.missing_parent_id`.

#### T77d — A cyclic or over-deep chain terminates
A `delegations` table mutated directly so that two rows are each other's parent. Evaluation denies
with `authority_escalation` within the §5.5 bound and does not hang; the test bounds its own
iteration so a broken bound fails red rather than timing out CI.

#### T78 — A revoked parent denies
`ctrlrun revoke` on a mid-chain delegation appends `DELEGATION_REVOKED` **and reaches a registered
sink**. A grandchild's action then denies with `AuthorityDenied(reason="authority_revoked")` — both
asserted: the event at revoke time, the reason at evaluation time. Re-revoking is idempotent and
appends no second event.

#### T79 — Depth beyond the maximum is rejected
With `max_delegation_depth: 3`, depths 1, 2 and 3 are created and depth 4 raises
`AuthorityEscalation(reason="max_depth")`. Then, with the document lowered to 2 and reloaded, the
existing depth-3 chain denies at evaluation. Depth is asserted to be derived by walking, not read
from the column: a row whose `depth` is edited to 0 still denies.
`max_delegation_depth: 0` refuses every creation.

#### T80 — The signature scenario
Human grant €100,000 `delegable` → finance agent €25,000 → support agent €2,000. The support agent
requesting €50,000 is denied with `authority_constraint` and the fake remote is never called;
requesting €1,500 passes authority and reaches policy. Two separate creation attempts, because two
different guards refuse them: `finance-agent` delegating €50,000 under its €25,000 parent →
`containment` with `data.dimension == "constraints"`; `support-agent` delegating anything at all →
`parent_not_delegable`. Asserting one would hide the other.

#### T81 — Omission is not "unlimited"
A parent with `amount_lte: 25000`; a child with **no** `amount` constraint. The delegation is
**rejected at creation** — not accepted-and-inherited, not accepted-and-unconstrained. Repeated for
`resources`, `environments`, `expires_at`, the parent's `user`, and the child's own `agent` —
the last two being the cases where an omitted key means "anyone" rather than "nothing". The `else` branch, in which the
delegation was created, fails the test with the delegation id it should not have got.

### Item 4 — Observe mode (§6)

#### T82 — Observe executes what enforce would deny
`mode: observe`, an action the policy denies. The executor runs, `Control.execute` returns rather
than raising, the receipt is `result="observed"`, `execution="committed"`,
`would_have.decision="deny"`. The same configuration under `mode: enforce` raises `ActionDenied` and
the executor does not run — both halves in one test, so "observe executes" cannot pass by the action
having been allowed.

#### T82b — What observe mode still refuses
Parametrized over §6.2's falsifiable rows under `mode: observe`: `no_principal`, a raising identity
provider, `effect_key_error`, `unrepresentable_argument` through the gateway, and an escalating
`Control.delegate`. Each raises its enforce-mode exception, the executor's call count is 0, and the
receipt-and-event shape matches the enforce-mode one; the delegation row additionally asserts no
row was written. Without this test, one `if observe:` wrapped around the execute path passes every
other observe test while violating the whole table.

#### T82c — Observe mode asks no human
`mode: observe` with a policy rule reaching `approve`. The executor ran exactly once,
`result == "observed"`, `would_have.blocked_reason == "approval_required"`, the store holds zero
approval requests, and no `APPROVAL_REQUESTED` was appended. The `else` branch — `ApprovalRequired`
having been raised — fails the test.

#### T83 — A duplicate is recorded and still runs
Two executions of the same effect key in observe mode. The second has
`would_have.blocked_reason="duplicate"` and still executes; the fake remote's call count is 2. The
effect record is unchanged by the second attempt — same `attempt`, same state, same `action_id`.
Repeated with the prior record in `AMBIGUOUS`: the second attempt executes,
`would_have.blocked_reason == "ambiguous"`, and the record is **still `AMBIGUOUS`** afterwards, with
the `else` branch failing the test. Repeated with a `reconcile` hook installed, asserting the hook
was never called.

#### T84 — `mode` is top-level only
`mode:` inside an action entry, inside a rule, inside a grant, inside `authority:`, and in an
`--authority` document each raise `PolicyError` at load naming the path. `mode: true`, `mode: "off"`
and `mode: 0` each raise `PolicyError`. An absent `mode` is `enforce`.

#### T85 — The banner, and `ctrlrun verify`
Parametrized over §6.5's two columns **by name**: each command in the left column prints
`OBSERVE MODE — nothing is enforced` to stderr under an observe configuration and prints nothing of
the sort under an enforce one; each command in the right column prints it under neither, and still
works when no policy file exists at all. `--json` stdout stays parseable. `ctrlrun verify` exits
**2** in both modes with a message naming v0.4.

#### T86 — `ctrlrun stats`
Counts would-have-denied (broken down by reason), would-have-needed-approval, would-have-been-
blocked (by duplicate and by ambiguous), and ambiguous outcomes, from a store seeded with known
receipts. In enforce mode the same store reports denials and ambiguous outcomes and omits the
duplicate/ambiguous breakdown, with the footer saying so. `--since` with an absolute timestamp and
with `24h` each filter on `finished_at`, and a receipt exactly on the boundary is included; `7d`
and `30m` parse, and `1w`, `2mo` and a bare `5` exit non-zero naming the accepted forms. `--json`
matches the human output number for number. An empty store prints zeros and exits 0. No network is
reachable during the test.

#### T87 — Observe → enforce needs no migration
A store written in observe mode, then the same store used by a `Control` built from the same file
with `mode: enforce`. Every table opens, the effect committed under observe is refused as a
duplicate under enforce, and the delegations created under observe still evaluate.

### Item 5 — JWT identity and the gateway (§3.4, §8)

Keys are generated in the test; nothing reaches the network. The JWKS is served from a local
fixture, and the static-key cases assert that no outbound connection is attempted.

#### T88 — A valid token becomes a Principal
Signed with a locally generated key, `exp` in the future, correct `iss`, `aud` and `typ`. The
resulting `Principal` carries the mapped `agent`, `user`, `issuer` and `expires_at`, and exactly
the claims named in `claim_names` — no more, asserted by the mapping's key set. Repeated with `aud`
as a single string and as an array containing the configured audience among others.

#### T88b — Configuration is refused before any token is seen
Parametrized over §3.4's construction rules, each `InvalidArgument`: zero, two and three key
sources; `algorithms=["HS256"]` with `public_key`; `algorithms=["HS256"]` with `jwks_url`;
`algorithms=["RS256"]` with `secret`; an empty `algorithms`; `algorithms=["none"]` and `["NONE"]`;
`token_type` not passed. The `HS*`-with-`public_key` case is the one that matters: it is the
configuration end of key confusion, and the token end is already covered by the allow-list, so a
test that only exercised the token end would prove nothing the library was not already doing.

#### T89 — Every invalid token is refused, by cause
Parametrized, each asserting `IdentityError` **and** a distinguishing observable — the message
token, and through the gateway an upstream request count of 0: expired; `nbf` in the future; wrong
`aud`; a configured audience that is a substring of an `aud` value but not an element of it; wrong
`iss`; bad signature; `alg: none`; an `alg` outside the configured list; a `typ` that is not the
configured `token_type`; an **OIDC ID token** from the same issuer and key whose `aud` is the
configured audience and whose `typ` is `JWT` rather than `at+jwt` — the cross-JWT confusion case,
which passes every other check; a token with no `exp`; a token whose `agent_claim` is missing,
empty, or not a string.

#### T90 — JWKS handling
A token with an unknown `kid` triggers exactly one fetch; still unknown → `IdentityError`. A second
such token inside `jwks_min_refresh_interval` triggers **no** fetch and is refused. After the
interval, one more fetch. The fetch count is asserted at every step against an injected clock, and
the loop is bounded so a broken rate limit fails red rather than hanging. Plus: a JWK Set with two
entries sharing a `kid` refuses the token; a key with `use: enc` is ignored and a token signed with
it is refused; a key whose own `alg` excludes the token's is refused even when the configured
allow-list admits it; a failed fetch is `IdentityError` and the previously cached keys are not
discarded.

#### T91 — The gateway enforces authority, distinguishably
A `tools/call` outside the principal's grant returns JSON-RPC `-41012` `ctrlrun.unauthorized` with
`data.reason="no_authority"`, HTTP 403, upstream request count 0, and a `denied` receipt. The same
gateway and the same `authority:` section, with a call that a grant matches but a **policy rule
denies**, returns `-41001` `ctrlrun.denied` — so widening the `AuthorityDenied` handler to
`ActionDenied`, or ordering the two `except` clauses the other way, fails a test rather than
returning a plausible code. A call inside the grant reaches the upstream.

#### T91b — The gateway's approval gate uses the combined decision
Policy `allow`, authority passing, a policy rule reaching `approve` for a larger amount: the
approval flow of `v0.2 §6.10` works unchanged through the gateway with an `authority:` section
loaded — first call `-41002`, `ctrlrun approve`, re-send executes exactly once — proving the
pre-check was not left reading `Policy.evaluate` in isolation. The ACS hook is asserted the same
way.

#### T91c — In observe mode the gateway forwards everything
The same gateway under `mode: observe`: a call outside the grant returns the upstream's response,
not `-41012`; the upstream request count is 1; the receipt carries
`would_have.blocked_reason="no_authority"`.

#### T92 — The extra says so when it is missing
In a subprocess whose `sys.modules` blocks `jwt`, `import ctrlrun` succeeds and no
`opentelemetry`, `httpx` or `jwt` module is imported; constructing `JWTIdentityProvider` raises
`MissingDependency` whose message contains `pip install 'ctrlrun[identity]'`. T30's assertion list
grows to include `jwt`.

### Item 6 — Demo, examples, docs

#### T93 — Five scenarios, five refusals
`ctrlrun demo` exits 0, prints five scenario headings and five `BLOCKED` lines, runs in under a
second with no network, and writes receipts for all five. Every example under `examples/`,
including the two new ones, exits 0 offline, keeps its state under its own directory, is
repeatable, and has every file it needs tracked by git (`v0.2 §10 T31` extended).

---

## 11. Public API and CLI additions (frozen for v0.3)

```python
# ctrlrun/__init__.py — added
from .identity import (
    IdentityContext,
    IdentityProvider,
    StaticIdentityProvider,
    HeaderIdentityProvider,
)
from .authority import Authority, Grant, Subject, Delegation, AuthorityResult
from .policy import Condition, parse_conditions       # promoted; see below
from .state import DelegationRecord
from .errors import AuthorityDenied, AuthorityEscalation, IdentityError
# lazily importable, not re-exported at package import:
#   ctrlrun.jwt_identity.JWTIdentityProvider   (ctrlrun[identity])
#   ctrlrun.otel.OTelEventSink                 (ctrlrun[otel])
#   ctrlrun.gateway.serve                      (ctrlrun[gateway])
```

```python
@dataclass(frozen=True)
class Principal:                       # extended, v0.1 §2.1
    agent: str
    user: str | None = None
    claims: Mapping[str, str | int | bool] = _NO_CLAIMS
    issuer: str | None = None
    expires_at: datetime | None = None

Control(..., identity: IdentityProvider | None = None,
             authority: Authority | None = None)
Control.authority -> Authority | None
Control.delegate(parent_id: str, grant: Grant, *, by: Principal) -> Delegation
Control.revoke(delegation_id: str, *, by: str | None = None) -> None

Authority.from_yaml(text, *, source="<string>") -> Authority
Authority.grants -> Mapping[str, Grant]
Authority.max_delegation_depth -> int
Authority.evaluate(action, *, now, store) -> AuthorityResult
Authority.plan_delegation(parent_id, grant, *, by, store, now) -> Delegation
Authority.plan_revocation(delegation_id, *, by, store, now) -> Delegation

Policy.mode -> Literal["observe", "enforce"]

StateStore.put_delegation(record: DelegationRecord) -> None
StateStore.get_delegation(delegation_id: str) -> DelegationRecord | None
StateStore.delegations_for(parent_id: str) -> tuple[DelegationRecord, ...]
StateStore.delegations(*, include_revoked: bool = False) -> tuple[DelegationRecord, ...]
StateStore.revoke_delegation(delegation_id: str, *, by: str | None, at: datetime) -> bool
```

**Promoted.** `policy._Condition` becomes `policy.Condition`, and the parser behind a `when:`
mapping becomes `policy.parse_conditions(mapping, *, where: str) -> Mapping[str, Condition]`,
keyed by the raw condition key. `Grant` is public and `Grant.constraints` is made of them, so the
types have to be nameable; and §4.5 requires authority and policy to share one condition
evaluator rather than growing a second. `Condition` keeps `argument`, `op`, `operand` and
`matches`; nothing about its behaviour changes.

**`DelegationRecord` is the store's row, `Delegation` is the parsed grant.** `state.py` reads and
writes the columns of §5.2 with `grant_json` as a string, and `authority.py` parses a `Grant` out
of it. Stated because the obvious alternative — typing the store on `Delegation` — would make
`state.py` import `authority.py` and transitively `policy.py`, which is the edge
`ARCHITECTURE.md` §6 puts in `state.py`'s "must not know about" column. `delegations()` exists
because §5.6 walks **upward** from a delegation to its root, and a chain whose root grant has been
deleted from the document cannot be found by walking downward from the ids that are still in it.

**Changed.**

- `Control.evaluate(action)` returns the **combined** §4.6 decision, not the policy axis alone.
  Its signature and `Evaluation`'s two fields are unchanged; it reads the store and still writes
  nothing. Everything that pre-checks a decision — the gateway's `tools/call` path,
  `ctrlrun.acs`'s request hook — MUST use it or the same combination, never `Policy.evaluate` in
  isolation (§8.3).
- `Event.action_id` becomes `str | None` (§7); the three delegation events carry `None`, and
  `OTelEventSink` emits a standalone span for them rather than dropping them.
- `Receipt` gains `execution` and `would_have`, and its `principal` object gains `claims`,
  `issuer` and `expires_at` (§12.2). `ReceiptResult` gains `OBSERVED = "observed"`.
- The Action serialized into the store carries the full principal (§2.4).
- `RESERVED_ARGUMENTS` gains `claims`, `issuer` and `expires_at` (§4.5) — a **breaking** change
  for a protected function taking an argument by one of those names, in documents of every
  schema version (§12.1).

**Removed.** `ctrlrun gateway --principal-from-client-info`, and the
`GatewayConfig.principal_from_client_info` field behind it (§8.1).

Five event types join the closed set: `AUTHORITY_RESOLVED`, `AUTHORITY_DENIED`,
`DELEGATION_CREATED`, `DELEGATION_REVOKED`, `DELEGATION_REJECTED` (§7).

One JSON-RPC error code joins the frozen list of `v0.2 §11`: **`-41012` `ctrlrun.unauthorized`**,
with `-41001`'s `data` shape, and `v0.2 §6.10`'s `DENY` row narrowed to policy denials (§8.3).

Errors:

```python
class IdentityError(CTRLRunError): ...           # a credential was offered and rejected
class AuthorityDenied(ActionDenied): ...         # reason: §4.3; grant_id, delegation_id
class AuthorityEscalation(CTRLRunError): ...     # reason: §5.3; a delegation that may not exist
```

`AuthorityDenied` subclasses `ActionDenied`: an authority denial *is* the action being denied, and
an agent loop's existing `except ActionDenied` should keep working. `IdentityError` and
`AuthorityEscalation` subclass `CTRLRunError` directly, for `v0.1 §5.1`'s reason: neither is a
policy saying no, and neither should be swallowed by a handler written for one. `IdentityError` is
raised where a credential was offered and found wanting; a *missing* principal stays
`ActionDenied(reason="no_principal")`, unchanged since v0.1. The two reason vocabularies —
`AuthorityDenied`'s evaluation reasons and `AuthorityEscalation`'s creation reasons — are disjoint
and are never used interchangeably (§5.3).

CLI:

```
ctrlrun stats [--since ISO|30m|24h|7d] [--json]
ctrlrun delegate --parent GRANT_ID --file GRANT_YAML --as AGENT[/USER] [--json]
ctrlrun revoke DELEGATION_ID [--by WHO]
ctrlrun verify                                   # exits 2 until v0.4

ctrlrun gateway ...
                [--authority PATH]
                [--identity-jwt (--identity-jwt-jwks-url URL
                               | --identity-jwt-public-key PATH
                               | --identity-jwt-secret-file PATH)]
                [--identity-jwt-algorithms ALG]... [--identity-jwt-issuer ISS]
                [--identity-jwt-audience AUD] [--identity-jwt-token-type TYP]
                [--identity-jwt-header NAME] [--identity-jwt-agent-claim NAME]
                [--identity-jwt-user-claim NAME] [--identity-jwt-claim NAME]...
                [--identity-jwt-leeway SECONDS] [--identity-jwt-jwks-min-refresh SECONDS]
                # --principal-from-client-info: REMOVED (§8.1)
```

Extras in `pyproject.toml` gain `identity`. `dependencies` is unchanged: `pyyaml` and `click`.

**Module map** (`ARCHITECTURE.md` §6) gains three rows, and the dependency direction is unchanged
— downward only, with `Control` the only module that composes the others:

| Module | Owns | Must not know about |
|---|---|---|
| `identity.py` | `IdentityProvider`, `IdentityContext`, static and header providers | policy, authority, storage |
| `authority.py` | `Grant`, `Subject`, `Authority`, matching, containment, delegation planning | approvals, effect state, executors, sinks |
| `jwt_identity.py` | `JWTIdentityProvider` — `ctrlrun[identity]`, lazy | everything but `identity.py` |

`authority.py` imports the condition parser and evaluator from `policy.py` (§4.5), which is the
same exception `v0.2` made for `policy.py` importing the template grammar from `effect.py`: the
alternative is a second copy of security-critical code. `policy.py` does not import
`authority.py`, so there is no cycle, and policy still cannot see a principal (§4.7).
`authority.py` reads the store through the `StateStore` protocol and **writes nothing and appends
nothing** — `Control` does both (§4.8) — so it does not become a second composer.

---

## 12. Schema versions

### 12.1 `ctrlrun.policy/v3`

`authority:` and `mode:` require `schema: ctrlrun.policy/v3`. A `v1` or `v2` document using either
is a `PolicyError` naming the key and the required schema — the exact shape `v0.2 §3.1` uses for
`effect:` under `v1`, and for the same reason: a reader that ignored an `authority:` section would
run every action with no authority check at all, and a reader that ignored `mode: observe` would
enforce a configuration that was deployed to observe. The schema string is the only thing standing
between those outcomes.

v0.3 loads `ctrlrun.policy/v1`, `v2` and `v3`. A `v1` file loads as it did in v0.1 and a `v2` file
as it did in v0.2, **with one exception, and it is not schema-gated**: a condition naming `claims`,
`issuer` or `expires_at` is refused at load in a document of any version (§4.5). The reservation
lives in the condition-key splitter, which runs for every condition in every document, and gating
it on `v3` would leave the same name meaning two things in two files — which is the ambiguity
`v0.1 §3.2` refuses in the first place. The cost is real and is stated rather than hedged: a
`v1` policy whose protected function takes an argument called `claims` stops loading under v0.3,
and `Control` cannot be constructed until the argument is renamed. The load error says so. T74b
asserts it against a `v1` document, so the breakage is a tested decision rather than a surprise.

The top-level key set grows to `schema`, `actions`, `mode`, `authority`, and stays closed. An
`--authority` document has its own, smaller closed set (§8.3).

`ctrlrun init` keeps writing `v1`: the smallest working policy is still the right starting point,
and adding authority means changing the schema line, which is a visible act of opting in.

### 12.2 `ctrlrun.receipt/v2`

The receipt schema bumps. `v0.2` deliberately did not bump it — nothing changed. v0.3 changes two
things a reader can trip over:

- **`result` gains a value.** `"observed"` is not in `v0.1 §6.1`'s set. That alone is worth a
  version.
- **New fields**: `execution`, `would_have`, and `principal.claims` / `principal.issuer` /
  `principal.expires_at`.

Every field of `ctrlrun.receipt/v1` is present in `v2` with the same meaning. Receipts already
written keep their `v1` tag; the schema string is per document, and nothing rewrites history.

**What actually breaks, stated without the hedge.** "A reader that tolerates unknown keys and
unknown `result` values needs no change" is true and nearly useless, because the most important
reader does not tolerate them: `Receipt.from_dict` parses `result` into a closed `StrEnum`, and
`SQLiteStateStore` reads every stored receipt through it. So:

- A CTRLRun **≤ 0.2** process running `ctrlrun receipts` or `ctrlrun inspect` against a store or
  JSONL file that a 0.3 **observe-mode** process wrote raises on the unknown `result` value. Two
  processes sharing one store is the intended deployment (`v0.2 §6.1`), so this is not a corner
  case: **upgrade every reader before switching any writer to `mode: observe`.** An enforce-mode
  0.3 writer emits no `observed` receipt and is safe to mix.
- An external reader that keys on `result` must read `execution` as well, or it counts every
  observed execution as if nothing ran.

`ctrlrun.action/v1` is **unchanged** (§2.2). `ctrlrun.inspection/v1` becomes `v2`, gaining the
principal's issuer, expiry and claim names and an `authority` block carrying the
`AUTHORITY_RESOLVED` / `AUTHORITY_DENIED` data for the action; it ships with item 1 and item 2
respectively. `ctrlrun.stats/v1` is new (§6.4).

---

## 13. Explicitly out of scope for v0.3

Everything in `v0.1 §9` and `v0.2 §12` that v0.3 does not deliver, and specifically:

- **Issuing anything.** No token minting, no OAuth flows, no authorization server, no dynamic
  client registration, no token exchange, no introspection, no revocation lists. v0.3 verifies
  what it is handed (§1.1).
- **A revocation channel for a credential.** A verified token is valid until its `exp`, which is
  why one without an `exp` is refused. Shared-signals mechanisms exist and v0.3 implements none of
  them (§3.4).
- **Multi-tenant, tenant-templated issuers.** `issuer` is an exact string (§3.4).
- **Matching a grant on a claim.** Subjects address `agent` and `user` (§4.2). Claim-based
  subjects need an answer to "what does a missing claim mean" and a claim-name convention;
  neither exists yet.
- **Authority raising a decision.** A grant permits or it does not; it cannot require an approval
  that policy did not (§4.2). That needs a gateway story (§8.3) and a second look at the README
  claim §4.7 defends.
- **Hot reload.** An `Authority` is built when the document is loaded. Editing the file takes
  effect on the next load; there is no `SIGHUP`, no re-stat, no TTL (§5.6).
- **Runtime revocation of a root grant.** `ctrlrun revoke` covers delegations. A root grant is
  withdrawn by editing the document and reloading (§5.6).
- **Unrevoking**, and **listing delegations**: no `ctrlrun delegations`, no UI, no dashboard
  (§5.7, §7).
- **Authenticating the delegator.** `ctrlrun delegate --as` is an assertion, recorded as one
  (§5.7) — the same position `v0.1 §4.1` takes on the approver, who is also still unauthenticated.
- **Data scope.** Authority governs *which actions on which resources*, not which records, fields
  or purposes. That is `VISION.md` §5's resource/data scope and it is not v0.3.
- **Consequence taxonomy, the CONTROL registry, separation of duties, multi-approver workflows,
  M-of-N, break-glass.** Unchanged from v0.2.
- **Authority propagation across agent hops / A2A.** A delegation is created deliberately and
  recorded; it is not derived from a protocol hop. That is v0.7.
- **`ctrlrun verify`.** A stub that exits 2 (§6.5), and nothing more, until v0.4.
- **A structured `blocked_reason` on enforce-mode receipts** (§6.4).
- **Migrations.** v0.3 adds one new table and no column to any existing one, on purpose (§5.2). A
  schema migration story is still v0.6.
- **Multi-host reservation, Postgres, signed receipts, framework adapters, compensation.**
  Unchanged.
- **Any compliance or standards claim**, including mapping tables, badges, and "aligned with"
  language, in this document, the README, docstrings or CLI output (§1.1).
- Anything in `VISION.md` beyond §5's authority-grant shape, which is design input here and
  nothing more.
