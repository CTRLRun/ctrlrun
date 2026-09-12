# CTRLRun v0.8 Specification: Oversight

**Status:** draft, build-list item 0.
**Delta over:** `SPEC-v0.1.md`, `SPEC-v0.2.md`, `SPEC-v0.3.md`, `SPEC-v0.4.md`, `SPEC-v0.5.md`,
`SPEC-v0.6.md`, `SPEC-v0.7.md`. All seven bind in full, and nothing here relaxes one.
**Tests:** §10, numbered from T272 (v0.7 ended at T271).
**Names frozen:** §11.

One question: **who may say yes, and can the kernel tell?**

Seven milestones have verified the principal that *acts*. `G7` refuses an action whose requester
cannot be resolved; `v0.3 §2.3` refuses an expired credential before authority and before policy;
`v0.3 §5.4` refuses a delegation that widens what its parent held. Nothing whatever is asked of the
principal that *permits* an action. `Approval.approver` is a non-empty string
(`approval.py`, `Approval.__post_init__`). `ctrlrun delegate --as` is an assertion typed at a
shell, and the record keeps `created_via` so a reader can tell an act from an assertion, which is
the whole of it. `SPEC-mcp-operator.md` §10 states in as many words that the operator server
authenticates *who* is answering and does not check that they were allowed to.

v0.8 closes the part of that sentence a kernel can close, and §1.1 states, before anything else,
the part it does not.

---

## 1. Scope

Seven deliverables, in build-list order:

| # | Item | Section | Guarantee |
|---|---|---|---|
| 1 | Revocation by selector | §7 | none (§11.5) |
| 2 | The approver is a principal | §2, §4.1 | G18 |
| 3 | Entitlement from the control registry | §3 | G17 |
| 4 | M-of-N | §4.2 | G19 |
| 5 | Break-glass as a grant | §5 | none (§11.5) |
| 6 | Credential revocation, consumed | §6 | G20 |
| 7 | A policy change is a protected action | §8 | G21 |

`ctrlrun.guarantees/v4` is `G1` to `G21`. `ctrlrun.receipt/v4` becomes `v5`. `ctrlrun.policy/v5`
becomes `v6`. Each version moves **exactly once** (§11.4).

### 1.1 What this milestone is not, stated before anything else

On the pattern of `v0.4 §1.2`, `v0.5 §1.1`, `v0.6 §1.1` and `v0.7 §1.1`.

**It is not a defence against a persuaded approver.** A human misled into approving the right
action for the wrong reason gives a valid approval, and the receipt records it as one. Every
mechanism here answers *was this person allowed to answer*, and none of them answers *did they mean
it*. No document, docstring, CLI string or page may imply otherwise. This is the roadmap's own
"does not close" line and it is the ceiling on every claim v0.8 makes.

**It is not a defence against an administrator with write access.** Someone who can edit the policy
file, the code that constructs `Control`, or the rows in the store can defeat every check in §8 and
most of the checks in §2 to §5. `THREAT_MODEL.md`'s malicious-administrator line is unchanged, and
§8.6 says exactly which of this milestone's guards that line covers.

**It is not an approval UI, a notification channel, or an escalation timer.** The webhook is the
primitive (`v0.2 §7`) and the operator MCP server is the surface (`SPEC-mcp-operator.md`). An
approval expires; nothing re-asks, re-routes or escalates.

**It is not an issuer.** No token is minted, no key is held, no authorization server runs, no
introspection endpoint is answered and no revocation list is published. §6 consumes events and does
nothing else, exactly as `v0.3 §1.1` requires.

**It is not a second approval path.** Every refusal in §2 to §4 is raised on the path that already
consumes an approval, from the read that already happens, before the store call that already
exists. A deployment that verifies approvers and one that does not run the same code.

**It is not a new kind of entry point.** `v0.3 §4.3.1`'s table grows two columns and two rows
(§9), and the two rows are new *callers* of `Control.execute` and `Control._delegate`, not new
surfaces with rules of their own.

**It is not a new store method, a new error type or a new event type.** `StateStore` is frozen
(`v0.6 §9.2`); §11.2 shows how M-of-N is recorded without one. The closed set in `errors.py`
already names every refusal here. A policy change is an ordinary action whose whole life the
`v0.1 §6.2` vocabulary already describes.

**It is not a relaxation of anything.** There is no `skip_entitlement`, no `trust_approver`, no
`allow_self_approval`, no `break_glass=True`, no `ignore_revocations`, and no development setting
that admits an approver a configured provider could not resolve. A parameter that could turn a
check off does not exist.

### 1.2 The rules of v0.8

Four, and every section is measured against them.

**R1. Opt in, then fail closed.** This is `v0.3 §1.2`'s rule for authority, applied to the
approver. A `Control` built with no `approver_identity` behaves exactly as 0.7.0 did. What `verify`
reports about G17 to G19 is **not** an `N/A` for that reason, and §11.7 says why: verify configures
its own scenarios, so "nobody configured this" is a statement about verify's own construction and
never about the operator's document. A `Control` built with one gets no partial mode: an approval
whose record carries no verified approver is refused at consumption, including an approval granted
before the provider was configured, including one granted through a surface that cannot resolve,
and including one held by a store that does not persist the column. **A store that drops a column
must not turn a check off** is `v0.7 §6.4`'s rule and it governs here unchanged.

**R2. Omission is not entitlement, and it is not refusal either.** Two sentences that are easy to
conflate and that mean opposite things in a deployment. A principal whose claims do not carry the
role a control names **is not entitled** (§3.4). A control that names no role **gates nothing**
(§3.5). The reading that merges them either refuses every approval wherever one control has no
role, or admits every approver wherever one principal has no claim.

**R3. A yes is attribution until an entitlement check stands behind it.** §2.6 enumerates every
surface that can answer and says, for each, whether it can produce a verified approver. A reader
who assumes the new check is universal will be wrong about exactly those rows, which is why they
are a table and not a sentence.

**R4. Break-glass is a grant, not a flag.** Recorded, bounded by an envelope the policy hash
already covers, expiring, revocable and attenuable. `authority.py` implements all five today, so §5
adds an envelope and a command and no new authority mechanism.

### 1.3 What was read

Written against the code at `a88d741` (0.7.0, released and dated), not against its docstrings:
`approval.py` in full; `identity.py` and `jwt_identity.py`; `authority.py`'s `Grant`, `Subject`,
`contains`, `contained_dimension` and `Authority.evaluate`; `control.py`'s `__init__`,
`resolve_principal`, `_ask_provider`, `execute`, `_secure`, `_presented`, `_recheck`, `_compare`,
`_take`, `_approver_of`, `delegate`, `_delegate` and `revoke`; `policy.py`'s `PolicyControl`,
`Evaluation`, `_ActionPolicy.evaluate` and the loader's schema gating; `state.py`'s approval and
delegation methods on both shipped stores and `postgres.py`'s; `migrations.py`; `receipt.py`;
`gateway/operator.py`; `webhook.py`'s `handle_inbound`; `adapter.py`'s `ApprovalAnswer`;
`cli/main.py`'s `approve`, `deny`, `delegate` and `revoke`; `verify/guarantees.py` and one
scenario.

### 1.4 What reading the code changed

Nine things, each of which moved a decision this document was handed. The first five came from the
drafting; **the last four came from the independent review of this document**, which read the code
this section did not cite and found the first draft unbuildable in four places. They are recorded
here rather than quietly fixed, on the rule `v0.4 §9.4` set for a threat model's sentence about a
check `verify` could not deliver.

1. **`Control` never grants an approval.** Every grant goes through `ApprovalStore.grant_approval`,
   called by a surface outside `Control`: the CLI, the operator server, `handle_inbound`, or a
   provider. So the check that *matters* cannot live at the grant. It lives at the consumption,
   where `Control` already reads the record (`_recheck`'s `get_approval`) and already raises before
   `_take`. §2.4 is built on that seam, and every grant-side check is a second defence with its own
   test, because two guards with one observable result are `CONTRIBUTING.md`'s first shape of a
   false green unless the tests assert which one fired.
2. **There is already a route for per-request data that the provider protocol cannot carry.**
   `approval.policy_in_force` and `approval._precondition_at_request` are context variables
   `Control` sets around `self._approvals.request(...)`, read by `build_request`. `v0.6 §7.1` and
   `v0.7 §6.2` both used it rather than widening a frozen protocol. §3.3 and §4.2 use it for the
   third and fourth time, and §2.5 uses the same shape in the other direction, for the grant.
3. **`webhook.handle_inbound` takes no headers.** Its signature is
   `(store, path_request_id, body, signature, *, secret, replay_window)`; its HMAC authenticates
   the sending *system* and its `approver` is a string in the signed body. So the webhook is a
   surface that cannot produce a verified approver without a signature change, and `v0.2 §11`
   freezes that name.
4. **The operator MCP server has done half of item 2 already.** It builds a header or JWT identity
   provider, resolves a principal for every request, and refuses `--principal` because "an approval
   whose approver distinguishes nobody has no attribution". Then it discards the principal into the
   string `mcp-operator:<user>`. §2.6's work there is to stop discarding it.
5. **`ctrlrun revoke --by` is already taken and means the opposite thing.** It names who performed
   the revocation, defaulting to `CLI_APPROVER`. The roadmap's `--by <principal>` names whose
   delegations to revoke. §7.2 names the new selector `--created-by`, and the old option keeps its
   meaning.
6. **`_recheck` returns early on every deployment that does not use `v0.7 §6`**, before anything
   this milestone would add. Checks added after that return would have been dead on the default
   path, green, and invisible to a mutation table. §2.4 lifts the return and says so in the
   sentence an implementer cannot skip.
7. **A `Principal` cannot carry a list, and every issuer's roles claim is one.** `ClaimValue` is
   `str | int | bool`, `Principal.claims` refuses containers, and `JWTIdentityProvider` drops a
   non-scalar claim at DEBUG, so a `roles` array arrives as *absent* and its holder is silently not
   entitled. §3.4 amends `v0.3 §2.1` to admit a tuple of strings rather than building entitlement
   on a claim model that cannot express a role set.
8. **The Postgres grant's compare-and-set is on `status`, which does not change at N-1.** Two
   concurrent grants both read `pending`, both update, both see `rowcount == 1`, and each writes an
   `approvers` value computed before the other wrote: a lost update, and one principal filling two
   slots. §4.3 moves the condition onto the value being changed, and §10's T313 is written to fail
   against the shape the store has today.
9. **A reserved action name no document may declare is a name every proposal is denied for.**
   `Policy.evaluate` answers `DENY unknown_action` for anything the document does not list, so the
   first draft's §8 could never have written the receipt it depended on. §8.2.1 makes the name
   reserved *and declarable*, and adds the rule that closes the hole this exposed: under
   `require_approved_policy`, a policy that does not send its own change action to approval decides
   nothing.

## 2. The approver is a principal

### 2.1 What is wrong today

`Approval.approver` is a `str` whose only check is non-emptiness. It is written by
`grant_approval(approval_id, approver)` and read back onto the receipt. `adapter.py`'s
`ApprovalAnswer` docstring concedes what that string often is: it "names a **channel** wherever the
framework's primitive does not identify a person". The CLI writes `cli:local`. The scripted
provider writes `cli:scripted`. `ctrlrun verify` grants its own approvals inside its scenarios. The
operator server writes `mcp-operator:<user>`, which is the only one of them with a verified human
behind it, and even there the verification is discarded at the boundary.

So a receipt that says `approver: "cli:local"` is a true statement that somebody with shell access
typed a command. It is not a statement about a person, and `ASI09`'s row in
`OWASP-AGENTIC-TOP10.md` says so.

### 2.2 What v0.8 adds, in one sentence

An operator may configure an **approver identity**, and where one is configured, an approval is
consumable only if the store holds a **verified approver** for it: a principal resolved by that
identity's provider at the moment the grant was made, recorded on the approval row, and carried
onto the receipt.

### 2.3 The configuration object, and why it is an object

```python
# ctrlrun.approval
@dataclass(frozen=True)
class ApproverIdentity:
    provider: IdentityProvider
    roles_claim: str | None = None
```

`Control(..., approver_identity: ApproverIdentity | None = None)`.

**Why a second provider and not the acting one.** The agent's provider reads what a proxy set for
the agent. The approver arrives at a different door with a different credential, and a deployment
where the same provider answers both would be one where the agent's own token can grant the agent's
own approvals. The protocol is the same (`IdentityProvider`), the object is not, and nothing
defaults one to the other.

**Why an object rather than two keywords.** `roles_claim` is meaningless without a provider and a
provider without it cannot answer §3, so a deployment that sets one and forgets the other is a
deployment with a silent gap. One object makes that a type error rather than a misconfiguration.
Rejected: a new `ApproverProvider` protocol, which would be `IdentityProvider` with the same single
method under a different name, and a second protocol is a second thing to keep correct.

**A static provider is warned about, not refused.** `StaticIdentityProvider` answers with one name
for every call, so every approval it produces carries an identical approver and G18 is the only one
of the three that can still bite. `ApproverIdentity.__post_init__` logs one warning naming the
provider type. It is a warning and not a refusal because a single-operator deployment where the
shell genuinely is the human is a real deployment and the record it produces is true. The operator
MCP server keeps its own outright refusal of `--principal` (`SPEC-mcp-operator.md` §3.1), which is
a stricter rule for a surface that serves several humans, and §2.6 says why the two differ.

### 2.4 Where the check lives: at consumption, before `_take`

`Control._recheck` already reads the record it is about to present (`get_approval`) and raises
before the store call that consumes the approval (`v0.7 §6.2`). The approver checks join it there,
and the order is normative:

1. `get_approval(approval_id)`, the read `_recheck` already performs.
2. **A gate, and it is load-bearing: the approver checks stand aside for a record the store will
   refuse for its own reason** (denied, consumed, hash-moved, pending, unknown), and run on every
   other, the lapsed row included. §2.4.1 and §2.4.2 carry the table and the argument.
3. **The approver checks of §2.7, §3.6, §4.1 and §4.2**, in that order, each raising
   `ApprovalMismatch` with its own reason.
4. The precondition comparison of `v0.7 §6.2`, unchanged, and last before the store call because it
   is the only one that makes a network call and the window it narrows is measured from its own
   fetch.
5. `_take`, whose store call applies `check_consumable` exactly as it does today.

**The early return is lifted, and this is the sentence an implementer must not miss.** `_recheck`
returns immediately today when no precondition provider is named and the record carries no
fingerprint (`control.py`, `_recheck`'s third statement), which is every deployment that does not
use `v0.7 §6`. Adding the approver checks after that return would leave them dead on the default
path, green, and mutation-invisible. With an `ApproverIdentity` configured, the read and the
approver checks run on **every** presenting pass under `APPROVE`.

**The gate is `check_consumable`'s own verdict, computed once from one clock read**, and reused by
`_recheck`'s existing precondition raise. Not a second implementation of a frozen rule, and not a
second clock read: two reads a tick apart could produce a gate that says "not lapsed" followed by a
raise that says `expired`, which is the divergence `v0.7 §12.5` reversed.

**The expiry *decision* stays the store's.** `Control` does not refuse for expiry and does not
write a lapse: a lapsed grant whose approver is fine falls through to `_take` untouched, which is
`v0.1 §4.2 A3`'s answer. What this clock is used for is knowing which rows the gate stands aside
for, and §2.4.2 is the argument for the one row where that distinction is load-bearing.

**A refusal mutates no approval and reserves no effect, and §2.4.2 is why that sentence
survived.** Not the approval, which stays granted, on `v0.6 §7.2`'s precedent: the action is
refused, the human's yes is not spent on a question it did not answer, and the approval still
expires. Not the effect, because nothing is reserved. **Evidence is written**, and the distinction
is the point: a refusal appends `APPROVAL_INVALIDATED` and writes a `BLOCKED` receipt, because a
refusal nobody can find afterwards is not a refusal this project ships. The refusal produces an `APPROVAL_INVALIDATED` event and a `BLOCKED` receipt,
exactly as a precondition refusal does. **Every refusal the approver checks make is raised before
`_take` for exactly this reason**: a check that refuses after the store call cannot say this, and
the design that tried was rejected for it (§2.4.2). Not every refusal in §2 and §4 is one of
theirs: §2.7's fifth row, fewer than `approvals_required` distinct approvers, is the store's own
`pending`, raised inside `_take`, which is where it belongs.

**Observe mode reaches these checks through a different path**, `_observe_take`, which does not
call `_recheck`, so §4.1's row is implemented there too. Two consequences, both stated because
neither is obvious. An `ApprovalMismatch` raised there returns before `reserve_effect`, so an
observed action refused on approver grounds takes no reservation: not new behaviour, but a new
member of a class every observe-mode `ApprovalMismatch` was already in, and one case short of
`_observe_take`'s own argument for reserving. And `_observe_secure` records the mismatch's **own
reason** where it recorded one constant for every mismatch, which §4.1 argues and which §11.1's
table and `BLOCKED_BY_STATE` both had to grow for.

### 2.4.1 Why the gate exists, and what it costs to omit it

Without it, this milestone would report the wrong reason for five refusals that have nothing to do
with approvers, because `Control` no longer applies `check_consumable` and the store applies it
only inside `_take`:

| The row | What 0.7.0 reports | What an ungated approver check would report |
|---|---|---|
| A human **denied** it | `ActionDenied(approval_denied)`, with `APPROVAL_DENIED`, `ACTION_DENIED` and a `DENIED` receipt | `ApprovalMismatch(approver_unverified)`, with `APPROVAL_INVALIDATED` and a `BLOCKED` receipt: a human's no stops appearing in the evidence as a no |
| Already **consumed** (a replayed approval, G2) | `consumed` | `approver_unverified` |
| The action **hash moved** (G1) | `mismatch`, which is `HASH_MISMATCH`'s value | `approver_unverified` |
| **Pending** | `pending` | `approver_unverified` |
| **No such approval** | `unknown` | `approver_unverified` |

So the gate stands aside for all five, the store's reason wins, and two shipped guarantees keep
theirs.

### 2.4.2 The lapsed row, which took three attempts

**A grant that is `granted`, whose hash matches, and that is past its expiry by *this* clock is the
one row where `check_consumable` refuses a record the approver checks can still read.** It is
`verdict.expire`. What to do with it is the hardest decision in §2, it was got wrong twice, and
both wrong answers are recorded here because the next reader will reach for one of them.

**The first answer was to skip it**, on the reasoning that refusing on approver grounds would cost
the lapse its `APPROVAL_EXPIRED` event and the store's own lapse write, both of which happen inside
`_take`. That is fail-open. The store keeps its own clock, so on a host running ahead the checks
stood aside, `consume_approval_and_reserve` consumed the grant by the store's clock, and the action
ran **with no approver check at all**: a self-approval committing under a twenty-minute skew. It is
`v0.7 §12.5`'s divergence inverted from "safe and untrue" into fail-open, on the path §2.4 calls
every deployment that does not use `v0.7 §6`.

**The second answer was to defer it past `_take`** and refuse once the store had disagreed and
consumed. That closes the hole and moves the damage: `consume_approval_and_reserve` does both
halves of `v0.1 §4.2 A4` in one transaction, so the refusal then left a **consumed grant**, an
effect key **`RESERVED` with a live lease and nothing to release it**, no `EFFECT_RESERVED` and no
`APPROVAL_CONSUMED` event, and a receipt whose error said the approval was left granted when it was
not. The key then lapses into `AMBIGUOUS`, which is a human resolving an action the kernel itself
refused and which provably never ran. `_spend_unneeded_approval`'s docstring records an earlier
review finding the same shape: *an ambiguity manufactured by the permissive decision path*. And
there is no clean cleanup: `fail_effect` requires `EXECUTING`, so `RESERVED → FAILED` does not
exist, and `mark_ambiguous` would assert "unknown" about an action known not to have run.

**The answer is to check it, before `_take`, like the others.** The gate stands aside only for the
five rows above; the lapsed row is checked.

| The row | What it reports | What it costs |
|---|---|---|
| Lapsed, approver **fine** | `expired`, with `APPROVAL_EXPIRED` and the store's own lapse write | nothing: the check passes, `_take` decides, and `v0.1 §4.2 A3` keeps the expiry decision with the store |
| Lapsed, approver **refused** | the approver reason | that row keeps no `APPROVAL_EXPIRED` and no lapse write |

The cost is the second row and it is the cheapest of the three answers. The grant is unusable
either way: `check_consumable` refuses it at every later presentation, and it expires for real.
What is bought is that the lapsed row cannot be a way past §2.7 on a host whose clock runs ahead,
and that **nothing is written by a refusal** stays literally true, which neither of the other two
answers could say.

T291b covers the five store-owned rows and the lapsed-with-a-good-approver row; T291c is the skew reproduction
and asserts the grant is still granted and nothing reserved; the lapsed-with-a-bad-approver row has
its own test saying what it gives up.

### 2.5 How a verified approver reaches the row, without a new store method

`ApprovalStore.grant_approval(approval_id, approver)` is part of the frozen protocol and cannot
grow a parameter. The route is the one `v0.6 §7.1` and `v0.7 §6.2` already use in the other
direction, a context variable set around the call:

```python
# ctrlrun.approval, PACKAGE-INTERNAL: the leading underscore is load-bearing (§2.5.1)
@contextmanager
def _granting_principal(principal: Principal, *, entitled: Sequence[str] = ()) -> Iterator[None]: ...
```

A shipped surface that has resolved an approver wraps its `grant_approval` or `deny_approval` call
in it. The shipped stores read it inside those methods and write the row. A third-party store that
ignores it records nothing, and §2.7's consume-side check then refuses every approval it grants,
which is the fail-closed direction and is the whole reason the check is at consumption.

**What is recorded**, as `VerifiedApprover`, canonical JSON in one column, and read back on
`ApprovalRecord.approvers`:

```python
@dataclass(frozen=True)
class VerifiedApprover:
    agent: str
    user: str | None
    issuer: str | None
    entitled: tuple[str, ...]     # the control ids this approver satisfied (§3)
    granted_at: datetime
```

**No claim value is stored.** `v0.3 §2.4`'s rule: evidence carries claim *names* where values are
withheld. `entitled` is not a claim value; it is a list of the operator's own control ids, already
in the policy document and already on receipts through `Receipt.controls`.

### 2.5.1 Why it is package-internal, and what that does not fix

A public `granting_principal` would be a public, unauthenticated way to assert a verified approver:
any in-process caller could wrap any `grant_approval` in it with a `Principal` it constructed, and
the store would write a `VerifiedApprover` that §2.7 accepts. That is `trust_approver` spelled as a
context manager, and §1.1 says there is no such thing.

Package-internal does not make it impossible. It makes it **not a supported interface**: the
shipped surfaces are its only callers, `__init__.py` does not export it, and §11.2 records that it
is deliberately not a public name.

**The residual, stated rather than discovered.** Anything running inside the application's own
process can call a private function, and a store is code the operator chose. So the kernel's claim
is not "this principal was verified"; it is **"the surface that granted this approval recorded a
principal its provider resolved, and the surfaces that ship do exactly that"**. §2.6's table is
what makes that sentence checkable, and §3.8 carries the same distinction for entitlement.

### 2.6 Every surface that can answer, and what each one does

`grant_approval` and `deny_approval` are reachable from more places than an approval surface: the
non-test callers in the tree are `cli/main.py`, `gateway/operator.py`, `webhook.py`, `adapter.py`,
`approval.py`'s scripted provider, `control.py`'s own `_withdraw`, `verify/scenarios.py`, and the
conformance fixtures and suites. This table is normative, and `R3` is why it exists.

| Surface | Credential it actually has | With an `ApproverIdentity` configured |
|---|---|---|
| `gateway/operator.py`'s `approve` / `deny` tools | HTTP headers, and a provider it already builds per deployment from `--principal-header` or `--identity-jwt` | **Resolves and records.** The server's existing provider *is* the approver identity, plus `--approver-roles-claim` (§11.1). It already refuses `--principal`, so a static approver cannot reach it. With `--principal-header` the principal carries no claims (`identity.py`), so it can satisfy **no** `approver_role`: that pairing is verified-but-unentitled, and §3.4's last bullet is the rule that catches it |
| An embedding application calling the store itself | Whatever it verified | **Resolves and records**, through the same internal route the shipped surfaces use (§2.5.1) |
| `ctrlrun approve` / `ctrlrun deny` | None. The command builds a store and nothing else: no policy, no `Control`, no provider | **Cannot produce a verified approver in the shipped CLI.** It records the string as it always did, and the approvals it grants are refused at consumption. Giving it one would mean loading a policy that cannot name a provider anyway (§2.6.1) |
| `webhook.handle_inbound` | An HMAC over the body, and `approver` as a string inside it | **Cannot.** `v0.2 §11` freezes the signature, and the MAC authenticates the sending *system*, not the person |
| `adapter.py`'s `InterruptApprovalProvider` (`ApprovalAnswer`) | None | **Cannot.** The v0.5 contract is frozen (`v0.5 §9`) and an answer from a framework interrupt carries no credential. Adding a field was rejected: the adapters are separately versioned distributions |
| `ScriptedApprovalProvider` | None | **Cannot.** A test and demo surface |
| `Control._withdraw` (`deny_approval`) | It is the kernel closing a request it created | Records no approver principal and needs none: it is not a human answering, and §2.7's check governs *consumption* of a granted approval, which a withdrawal never produces |
| `verify/scenarios.py` | It grants its own approvals inside its own scratch stores | **Grades, and must not go red.** Verify constructs its scenarios, so it configures the approver identity it grades against (§10.8, and §11.1's note on G17 to G19's `N/A` reasons). A verify that turned red because the kernel started checking approvers would be `v0.4 §3.8`'s "a fact about the machine reported as a verdict on the kernel" |
| `conformance/` fixtures and suites | They exercise stores | Unaffected: they grade store behaviour, and §4.5's case is the one that changes |

**The honest summary, which every page describing this feature carries.** With an approver identity
configured, the surfaces that produce verified approvals are **the operator MCP server and an
embedding application**. `ctrlrun approve`, the webhook and the adapters remain attribution
surfaces, and the approvals they grant are refused when they are presented. A deployment whose
approvals arrive through one of those three has turned its approval path off by configuring this,
which is R1 working and is the reason the table comes before the feature.

### 2.6.1 Why the CLI is not given a provider

An identity provider is code (`v0.3 §3`), and the policy file cannot name one: a policy that could
choose a credential source would be a policy that decides identity. `ctrlrun approve` loads no
policy today and §9's last rows say why that is deliberate. So giving the CLI a verified approver
means giving it a way to load Python from configuration, which is a larger decision than this
milestone, and one nothing here needs: the operator server exists for exactly this.

Rejected: an environment variable naming a token. It moves the question to "who set the variable"
and adds a credential channel with no verification story.

### 2.7 The refusal at consumption

| Condition | Reason | Raised as |
|---|---|---|
| No `approver_identity` configured | not checked; 0.7.0 behaviour | nothing |
| Record carries no `VerifiedApprover` | `approver_unverified` | `ApprovalMismatch` |
| Record carries one and §3 refuses it | `approver_unentitled` | `ApprovalMismatch` |
| The approver is the requester (§4.1) | `approver_is_requester` | `ApprovalMismatch` |
| Fewer than `approvals_required` distinct (§4.2) | handled by the status: the record is still `pending` | `ApprovalMismatch`, reason `pending` |

Each reason is a value of the existing `ApprovalMismatch.reason` field and of
`APPROVAL_INVALIDATED.data.reason`. No new error type (`v0.6 §9.2`, `v0.7 §9.2`), and the tests
assert the reason and never the type alone, because all four share it (`CONTRIBUTING.md`, the
first shape of a false green).

### 2.8 The `IdentityContext` an approval resolution gets

```python
IdentityContext(
    action=<the action name the request names>,
    environment=<the Control's environment>,
    headers=<whatever the answering surface has, empty where it has none>,
    agent=None,
    user=None,
)
```

`action` and `environment` come from the stored request, not from the answering call, because the
question being answered is "may this principal approve *this* action". `agent` and `user` are
`None` and never the answering surface's assertion: `v0.3 §3.1` calls them a hint a provider may
ignore, and a hint sourced from the caller of an approval command is the caller naming themselves.

### 2.8.1 What binds today, and what waits for item 3

**Item 2 does not call `ApproverIdentity.resolve`, and this section says so rather than reading as
though it did.** The one shipped surface that resolves an approver is the operator MCP server, and
it resolves through *its own* provider, built from `--principal-header` or `--identity-jwt`, with
the headers of the request being answered: that is what it has done since it shipped, and item 2
changes only what it does with the answer. On `Control`, `ApproverIdentity` is the switch that
turns the consumption check on (§2.3), and its `provider` is what item 3 reads the roles claim
through.

So the context above is the contract for a surface that resolves **through `ApproverIdentity`**,
and item 3 is where the operator server starts doing that, with `--approver-roles-claim` beside it.
Until then, `action` and `environment` on the server's own context name the *tool* being called,
which is `v0.3`'s shape for that server and not a promise this section made.

A test that builds the context itself and then asserts the fields it just wrote proves nothing, and
§10.2's T290 was exactly that until an independent review said so.

### 2.9 The upgrade note, stated because it will surprise somebody

An approval granted at 0.7.0 and still pending in the store, presented after an `ApproverIdentity`
is configured, is **refused** with `approver_unverified`, and the human is asked again. This is R1
working, not a defect: the row records that somebody typed a command, and the deployment has just
declared that it needs to know who. The changelog says it in these words, and item 8 puts it under
"stricter than 0.7.0, with what 0.7.0 did".

An operator whose approvals arrive through the webhook or an adapter, and who configures an
approver identity, has turned their approval path off. That is the correct failure: §2.6's table is
the thing to read before configuring, and the CLI's refusal names the surface.

---

## 3. Entitlement from the control registry

### 3.1 The sharp case

A policy cites `card-data-handling` on the rule that sends a payment to approval. Two humans can
run `ctrlrun approve`: the payments lead, and the intern who was given shell access to restart a
worker. Today they are the same principal to this kernel, and the receipt says `cli:local` for
both. The written expectation the control cites exists precisely to distinguish them.

### 3.2 What a control gains

```yaml
controls:
  card-data-handling:                       # the registry is a mapping of id to entry
    title: Cardholder data changes are approved by a named owner
    source: PCI DSS 7.2.1
    approver_role: payments-owner           # new in ctrlrun.policy/v6
```

`approver_role` is a single opaque string, and it joins the registry entry's closed key set
(`title`, `source`), gated on `ctrlrun.policy/v6` so an older reader refuses the document rather
than ignoring the key (§11.3, and `v0.6 §9.5`'s rule for `controls:` itself).

**CTRLRun does not interpret it**, exactly as `v0.6 §7.3` says it does not interpret `source:`: it
does not know what `payments-owner` means, does not check that such a role exists anywhere, and
makes no compliance claim on the strength of one.

**What changed about `v0.6 §7.3`'s "attribution, not prevention", stated precisely because it is
the sentence a reviewer will check.** A control still does not decide an *action*: citing one
causes no approval, and `Evaluation.controls` still does not participate in the decision. What a
control now decides is **who may answer an approval the decision already required**. Those are
different questions, and the second is the first thing a control has ever decided. Every page
carrying the old sentence gains the second half in the same edit, `docs/SPEC-mcp-operator.md` §4.3
and §10 included (§3.9).

### 3.3 Which controls apply, and when that is fixed

The controls cited by the evaluation that sent this action to approval, taken **at request time**
and recorded on the request, with the role each one required:

```python
@dataclass(frozen=True)
class RequiredRole:
    control: str
    role: str

class ApprovalRequest:
    required_roles: tuple[RequiredRole, ...] = ()
```

Captured on the same route as `policy_hash` and the precondition fingerprint: a context variable
`Control` sets around the provider call, read by `build_request` (§1.4 item 2).

**At request time and not at grant time**, for `v0.6 §7.1`'s reason and with `v0.6 §7.2`'s table
behind it: the approval binds to what the human was shown, the store has no policy, and a command a
human answers with must not fail because a policy file two hosts away became malformed. Where the
policy changed between the request and the answer, the roles the human was asked under are the ones
that bind, and the receipt's own `policy_hash` records that the policy moved.

**The pair and not the role alone**, because §3.7 requires the refusal to name the control, and a
refusal that named only a role would leave an operator grepping a registry to find out which
expectation they failed.

### 3.4 A missing claim is not a role

The approver's roles are read from the claim `ApproverIdentity.roles_claim` names, on the resolved
principal's claims.

**One surface may name it twice, and the flag wins.** The operator MCP server has its own
`--approver-roles-claim`, because it resolves the approver with its own provider and a deployment
may run it against an issuer the embedding application does not use. Where both are set the flag
wins; where only the `Control`'s is set the server reads that. This is stated rather than left to
be discovered because **it is a flag that changes the outcome of an entitlement decision**, and the
rule that no flag relaxes a check is only true if every flag that touches one is written down. It
cannot relax the check: naming a claim that carries no role refuses *more*, never less, and the
kernel's refusal at consumption reads what was recorded either way. The server warns at startup
where a cited control names a role and neither source names a claim, because that configuration
refuses every approval it gates.

Three facts about the code shape every rule below, and each was found by reading it rather than
assumed:

- `ClaimValue` is `str | int | bool` and `Principal.claims` **refuses containers**
  (`action.py`), whose stated reason is that "a provider flattens or drops a structured claim
  rather than storing one here";
- `JWTIdentityProvider` carries only the claims its `claim_names` allow-list names, and **silently
  drops a non-scalar one at DEBUG**, so a `roles` claim, which is a JSON array at every issuer
  anybody deploys, arrives as *absent*;
- `HeaderIdentityProvider` carries **no claims at all**, by design.

So v0.8 amends `v0.3 §2.1`: **`ClaimValue` gains `tuple[str, ...]`**, and `JWTIdentityProvider`
carries an array-of-strings claim as a tuple instead of dropping it. This is a public API change
and §11.1 carries it. It is safe where a hash is concerned: `v0.3 §2.2` keeps `claims` out of the
canonical form of an action, so no action hash and no approval binding moves. Rejected: flattening
an array into a delimited string, which is structure encoded in a string and a role named
`a,b` away from a defect.

**`list | tuple`, with `str` and `bytes` excluded by name**, in the amendment and in every
implementation of it. `str` is itself a `Sequence`, so a check written as
`isinstance(value, Sequence)` before the string branch turns the one-role claim `"payments-owner"`
into a tuple of twenty-one single characters, which breaks this section's own "the claim is a
string: one role" rule and T303's byte-for-byte matching.

**And the amendment has a second half, without which it breaks every reader.** A `Principal` is
written to JSON and rebuilt from it in four places: the `action_json` column both shipped stores
write and read, and `Receipt.to_dict`/`from_dict`. JSON has no tuple, so a tuple claim comes back a
**list**, and `_frozen_claims` refuses anything that is not `str | int`. Unamended, every stored
action and every receipt carrying an array claim would raise `InvalidArgument` on read, which for
receipts breaks a contract by name: `Receipt.from_dict` never raises over a key, because "a raise
on one tampered row would blind every reader at once" (`v0.7 §6.11`). So **`_frozen_claims` accepts
a sequence of strings and normalises it to a tuple on the way in**, with the element type checked
(strings only, no nesting, and `v0.1 §2.3`'s float rejection inherited). T299b writes and reads
back a receipt and a held continuation carrying an array claim, on both backends, and verifies the
chain across them.

The rules, and each is its own test:

- **The claim is absent**: the principal holds no roles. **Not entitled** wherever any role is
  required, with a **warning** naming the claim and the control, because the commonest cause is a
  provider that was never told to carry it, and a DEBUG line is where that goes to die.
- **The claim is a string**: one role, matched exactly, byte for byte. No case folding, no
  trimming, no prefix matching, no pattern grammar. `v0.3 §4.4`'s grammar is for actions and
  resources and is not extended here; a wildcard in a role would be an entitlement nobody wrote.
- **The claim is a tuple of strings**: that set of roles, each matched exactly.
- **The claim is an int or a bool** (the other things a `Principal` can carry): **not entitled**,
  never coerced, never stringified.
- **The claim is an array containing something that is not a string**: the provider does not carry
  it, so it is absent, and the absent rule applies. The provider's own drop moves from DEBUG to a
  **warning** where the dropped claim is the one an `ApproverIdentity` names, because a silently
  unentitled approver is the failure this rule exists to make visible.
- **The claim is a structure `Principal.__post_init__` refuses**: it never reaches this check at
  all, because resolution raised `IdentityError` before any approval existed, which is
  `v0.3 §3.2`'s behaviour unchanged.
- **`roles_claim` is `None`**: no role can be read, so no role is satisfied, so any required role
  refuses. A deployment naming roles in its policy and no claim to read them from has configured
  half a check, and half a check fails closed.
- **A provider that carries no claims**, `HeaderIdentityProvider` being the shipped one: identical
  to the absent rule, and §2.6 names the pairing because the operator server run with
  `--principal-header` is exactly it.

### 3.5 A control that names no role gates nothing

The other omission, and the opposite answer. A control with no `approver_role` contributes no
`RequiredRole`, so an approval citing only such controls requires no role and is entitled by anyone
the provider verified. It is 0.7.0's behaviour for that control.

**Why these two omissions differ, in one sentence each.** A missing *claim* is a statement about a
person, and the kernel refuses to invent one. A missing *role* is a statement about the operator's
document, and inventing one there would refuse every approval in every deployment that has controls
and has not heard of v0.8.

### 3.6 Every required role must be satisfied

Where an evaluation cites several controls with roles, the approver must hold **every** one.
Rejected: any-of, which lets the weakest control in the set decide who may answer, and which makes
adding a control to a rule a way of *widening* who may approve it.

The set recorded on the row is `entitled`: the control ids this approver satisfied. The check at
consumption is `{r.control for r in required_roles} ⊆ set(entitled)`, evaluated over the distinct
approvers of §4.2 as a whole: under M-of-N **each** approver satisfies every required role, because
a control that says who may answer is not satisfied by a committee in which one member could.

### 3.7 The refusal names the control

```
approval a1b2 was granted by an approver who does not hold the role
'payments-owner' required by control 'card-data-handling'; the approval is left granted
```

`ApprovalMismatch.reason` is `approver_unentitled`; `APPROVAL_INVALIDATED.data` carries
`control` and `role`. §10's tests assert the control id, not only the reason, and never only the
type, because a test asserting a type alone cannot tell which of §2.7's four refusals fired:
`CONTRIBUTING.md`'s first shape of a false green.

### 3.8 Where entitlement is decided twice, on purpose

The grant surface computes `entitled` from the request's `required_roles` and refuses on the spot
where the approver does not hold **every** one of them, so a human learns at the moment they answer
rather than at the moment an agent retries. Not "holds none of them", which was the earlier wording
here and is a different rule: §3.6 is all-of, an approver holding one of two required roles is
refused, and a surface applying any-of would admit an answer the kernel then refuses at
consumption, which is the worst of both halves.

**What each half is, said in the register `CONTRIBUTING.md` demands.** The consumption check
**prevents consumption** of an approval whose recorded entitlement does not cover the roles the
request pinned. It does **not** re-derive entitlement from a credential, and it cannot: the
credential existed at the grant and is gone by the time the approval is presented. So the recorded
`entitled` is the granting surface's verdict, and the kernel's claim is bounded accordingly:

> **G17: an approval whose recorded entitlement does not cover the control's required role is
> refused.** What entitled it was decided where the credential was verified, by the surfaces §2.6
> names.

That sentence is what `verify` grades and what every page says. A document claiming the kernel
re-checks a claim at consumption would be prevention claimed where the mechanism gives attribution,
which is the failure `CONTRIBUTING.md` names and which v0.7's precondition work had to be written
against from its first sentence.

**Two defences, two tests.** `CONTRIBUTING.md`'s first shape of a false green is a guard that can
only fire where a later one would, with the same observable result, so §10 gives the grant-side
refusal and the consume-side
refusal separate tests, and the consume-side test presents a row whose `entitled` a store wrote
without checking, which the grant-side refusal cannot reach.

### 3.9 What this makes true of `SPEC-mcp-operator.md`

That document's §4.3 and §10 say the operator server authenticates *who* is answering and does not
check that they were allowed to, and that entitlement "is not in scope". Item 3 rewrites both, in
the same PR, to say what is then true and no more than that: **where an approver identity and an
approver role are configured, that server checks entitlement, on the path the grant takes; where
they are not, the old sentence still holds and is still the one that describes the deployment.**
Both sentences belong there, because both describe real configurations.

## 4. Requester is not approver, and M-of-N

### 4.1 Self-approval, on the resolved principal

**G18.** With an `ApproverIdentity` configured, an approval is refused where a verified approver's
`(agent, user)` equals the requesting principal's `(agent, user)`.

**On agent and user, and nothing else.** `v0.3 §4.2` gives the reason: those two are what stay
stable under the token rotation `§2.2` describes, and they are what `Subject` matching already
addresses. Not the issuer, which is a property of the credential; not a claim, which §3 governs.

**Never on the string.** Two grants whose `approver` strings differ and whose resolved principals
are the same are one principal, and §10's test for G18 is exactly that case. A check on the string
would be defeated by typing a different word.

**Where only one side resolves.** The requester's principal is on the action (`Action.principal`,
no default, always present). The approver's is on the row, or §2.7 already refused the approval. So
there is no half-resolved case here.

**Observe mode**, and a behaviour change it forces. `v0.3 §6` records what enforce mode would have
done, so a self-approval in observe mode belongs on `would_have.blocked_reason`. Today
`_observe_secure` records the fixed constant `approval_mismatch` for **every** `ApprovalMismatch`
and discards `mismatch.reason`. Recording `approver_is_requester` therefore means recording the
specific reason for every mismatch, which changes what observe mode reports for refusals that have
nothing to do with v0.8. That is the right change, it is the only way this row is truthful, and it
is listed in §11.1's reason table and in the changelog under "stricter than 0.7.0" rather than
slipped in. The alternative, a special case for the three approver reasons, would leave observe
mode reporting `approval_mismatch` for a precondition change and `approver_unentitled` for an
entitlement one, which is a vocabulary nobody can explain.

### 4.2 M-of-N

```yaml
actions:
  payments.refund:
    decision: approve
    approvals_required: 2        # new in ctrlrun.policy/v6
```

Integer, at least 1. `0`, negatives, `true`, `1.0` and `"2"` are refused at load, naming the key
and the line, exactly as `v0.7 §5.3` refuses a malformed `max_attempts`: a malformed threshold
fails the policy and never the action. Absent means 1, which is 0.7.0. `Policy.approvals_required(
action_name)` reads it, on the precedent of `Policy.max_attempts`.

**The threshold is pinned on the request**, `ApprovalRequest.approvals_required`, by the same route
as §3.3 and for the same reason. This is also what lets the store enforce it: the store has no
policy, and a store that had to ask one what N is would be a store that loads policy files.

**Distinct means distinct resolved principals**, `(agent, user)`, as §4.1 compares them.

**A threshold above 1 with no `ApproverIdentity` configured is refused**, and this is the case a
first draft left undefined. `approvals_required` is a policy key, so an operator can set it in a
deployment that verifies nobody, where "distinct principals" has no referent: the count could never
move, or distinctness would silently fall back to the string §4.1 forbids. Neither is acceptable,
so the action is **denied** with reason `approvals_unverifiable`, at evaluation, naming the action
and the key. Not a load error: the policy is loadable and correct, and what is missing is the
`Control` it is deployed in, which the loader cannot see. §12 carries the row.

**A second grant from the same principal, while the record is still `pending`,** is not an error
and not a duplicate row: it updates that approver's entry and the count does not move. "Counted
once" is the requirement, and rejecting the second answer would make a human think their answer was
lost. Once the record has reached `granted`, a further grant is refused exactly as it is today, by
`check_answerable`, which refuses any record that is not `PENDING` and which `v0.1 §4.2` freezes.

**What does not count, and where it stops counting.** Three answers must not authorize an action,
and building item 4 established that only one of them can be excluded from the count itself. The
draft of this paragraph said all three were "refused before the count moves"; §14.4 records why
that was wrong and what replaced it.

| Answer | Where it is refused | Does the count move? |
|---|---|---|
| An approver the provider could not verify (§2.7) | In the count. `count_grant` records no approver for a grant carrying none | No. The row is unchanged |
| The requester's own yes (§4.1) | At consumption, `approver_is_requester` | **Yes**, and the row shows who gave it |
| An approver satisfying a required role they do not hold (§3.8) | At the granting surface, and again at consumption, `approver_unentitled` | **Yes**, at consumption; the grant surface refuses before the row is written at all |

The two that count are still refused, every time, on every path, and the receipt names which
refusal fired. What they do not do is make the record unreachable: a yes that never lands leaves
the request `pending` for ever, and at N=1 that turns a refusal a reader can act on into a silence
they cannot.

**A partial grant is visible in one place only, and that is deliberate.** `ctrlrun.inspection/v2`
is unchanged (§11.3) and §11.2 adds no event type, so a request that one of two humans has answered
reads identically to one nobody has answered in `ctrlrun inspect --json` and in the event log. What
reports it is the answer each grant surface returns, to the person who just answered. The reason is
that the alternative is an event type meaning "somebody answered and it changed nothing", which is
a row every reader of the chain has to learn to ignore, for a fact with a half-life of minutes. The
cost is stated here rather than left to be discovered: **an operator cannot currently see how many
of N a pending request holds without asking the store for the row.** If that turns out to matter it
is an inspection schema bump, on its own version line, not an event.

**A denial denies the request, whole.** One `deny_approval` moves the record to `denied` however
many grants it holds. A request that absorbs a no while it waits for enough yeses is a request that
asked the wrong question, and the fail-closed reading is the one this repository takes where a spec
leaves a question open.

**Expiry is the request's.** Grants collected before the expiry do not extend it. An expiring
request with 2 of 3 expires, and `check_consumable` refuses it with `expired` as it does today.

### 4.3 Where the count is decided

**In the store's write, never in a read followed by a write.** This is `v0.7 §5.5`'s rule for the
attempt ceiling, applied here for the same reason: two callers who both read "1 of 2" both get
through, which is attribution and not prevention.

- **SQLite** already opens `BEGIN IMMEDIATE` before reading the record in `grant_approval`, which
  serialises the read and the write. The append, the distinctness test and the status transition
  happen inside it, and nothing about the shape changes.
- **Postgres needs a different condition from the one it has, and this is the finding that matters
  most in §4.** Its `grant_approval` runs `BEGIN` under READ COMMITTED, reads with a plain `SELECT`
  and no `FOR UPDATE`, and updates `WHERE approval_id = %s AND status = %s`. That compare-and-set
  is on **`status`**, and at N-1 the status does not change: two concurrent grants both read
  `pending`, both update, both see `rowcount == 1`, and each writes an `approvers` value computed
  from the row it read before the other wrote. That is a lost update, and one principal fills two
  slots. The store's own `_consume_locked` documents the identical defect, measured, and says the
  condition has to be in the statement.

  So the condition moves to **the value being changed**: the update carries
  `AND approvers IS NOT DISTINCT FROM %s`, the previously read blob, with `rowcount` checked and a
  bounded retry on a miss. `IS NOT DISTINCT FROM` and not `=`, because the first grant compares
  against `NULL`. A miss means somebody else answered first, which is information, not an error to
  swallow; the retry converges because the store runs READ COMMITTED with an explicit `BEGIN`, so a
  re-read inside the transaction sees the winner's commit.

  **And the column is `COLLATE "C"` in the Postgres dialect**, which `migrations.py` already
  requires of every identity column: byte comparison, no locale. The moment `approvers` becomes a
  comparison column, a non-deterministic collation can make two distinct blobs compare equal, and
  here that fails the **unsafe** way: a compare-and-set that wrongly matches succeeds, and the lost
  update this rule exists to close comes straight back, on exactly the deployments whose
  `lc_collate` is an ICU locale. T313 runs against a database created with a non-C default
  collation.
- **In-memory**, under the existing lock.

The store conformance suite (`v0.6 §2`) gains a case: a store that reports it records several
approvers is driven concurrently and must not let one principal fill two slots. A store that
reports it does not is `not_applicable`, and §4.5 says what such a store does at consumption.

### 4.4 What the record holds, without a new store method

`ApprovalStore.grant_approval` returns `Approval`, which cannot describe "recorded, still short of
N". Its return type widens:

```python
def grant_approval(self, approval_id: str, approver: str) -> Approval | None: ...
```

`None` means *recorded and still pending*. A **public API change**, listed in §11.1, and not a new
method: `v0.6 §9.2` freezes the protocol's shape, and every existing implementation already
satisfies the widened type, because one that only ever grants at N=1 returns an `Approval` every
time, as it does today.

**Every caller, including the two that a first draft missed and that would have been silently
wrong.** `ScriptedApprovalProvider.wait` and `InterruptApprovalProvider.wait` both
`return self._store.grant_approval(...)` straight into `ApprovalProvider.wait`, whose contract
`v0.1 §4.3` fixes: **`None` means "answered, no", never "still waiting"**. Unchanged, a partial
grant at N-1 through an adapter or the demo provider would be reported to `Control` as a *denial*,
and `@protect(wait=True)` would raise `ActionDenied(approval_denied)` for a request two humans are
still answering. Both are type-correct under `mypy --strict`, so nothing but a test catches it.

| Caller | What it does with `None` |
|---|---|
| `ScriptedApprovalProvider.wait` | Keeps polling, and raises `ApprovalTimeout` when the script is exhausted, exactly as it does for a request nobody answered. Never returns `None` for a partial grant |
| `InterruptApprovalProvider.wait` | The same: the framework interrupt is still pending, so the answer is "not yet", which its `wait` already expresses by polling to its deadline |
| `ctrlrun approve` | Says the answer was recorded, and **that it will not count without a verified approver** where the deployment requires one, naming §2.6. It does not count down, because a CLI grant never counts under M-of-N (§2.6, §4.2) |
| `gateway/operator.py`'s `approve` tool | Returns `{"status": "pending", "approvals_required": N, "approvals_recorded": k}` |
| `webhook.handle_inbound` | Answers 200 with the same fact: the answer was recorded |

Rejected: a new method (`grant_approval_partial`), because it is the maintainer's call and two
methods writing one row are two transitions to keep correct. Rejected: a sentinel `Approval` with a
falsy field, because a caller that forgot to check it would hold a usable-looking grant.

### 4.5 A store that does not implement it

Fails closed, in two places. Such a store never writes more than one approver, so at N > 1 the
count never reaches N and the approval is never consumable: the action is refused with `pending`
and the operator sees it immediately rather than at an audit. And its conformance case reports
`not_applicable` with that reason, so `ctrlrun verify` against it says what it cannot do. There is
no path on which a store that ignores the column behaves as though N were 1.

---

## 5. Break-glass as a grant

### 5.1 What it is, and what it is not

An incident needs authority nobody was granted in advance. The wrong answer is a flag: a flag
leaves no record, expires never, cannot be revoked, and turns every "no setting skips a check"
sentence in this repository into a lie. The right answer is already built: `authority.py` has
grants that are recorded, bounded, expiring, revocable and attenuable, and `Control._delegate`
creates one beneath another under `v0.3 §5.4`'s containment.

So break-glass is **a delegation beneath an envelope**, and v0.8 adds the envelope, a command, and
nothing else about authority.

### 5.2 The envelope

```yaml
authority:
  grants:
    ...
  break_glass:
    incident-payments:
      subject: {agent: "oncall-*"}     # who a grant created here may be FOR
      actions: ["payments.*"]
      environments: ["prod"]
      constraints: {amount_lte: 50000}
      max_ttl: PT4H                    # the longest expiry a grant beneath it may carry
      controls: [incident-response]    # whose approver_role gates who may OPEN it (§5.3.1)
```

An envelope is a `Grant` in every respect the parser knows, plus `max_ttl` and the controls that
gate opening it. **An id declared in both `grants:` and `break_glass:` is a load error**, naming
both, because §5.3.1's rule depends on which mapping a parent came from and an id in two mappings
makes that undecidable.

Four things about `authority.py` decide the shape, and each was read rather than assumed:

1. **`Authority._candidates` returns every entry of `self._grants`, unconditionally.** So an
   envelope in `grants:` would decide actions, which is the opposite of what it is for. Envelopes
   live in a **separate mapping, `Authority.envelopes`**, and `_candidates` is not touched at all.
   That is what makes T330 true by construction rather than by a filter somebody could delete.
2. **`Authority._walk` resolves a delegation's root out of `self._grants` only.** A break-glass
   delegation names an envelope as its parent, so `_walk` and `_parent_for_creation` look in
   `_grants` and then in `envelopes`.
3. **`_parent_for_creation` refuses a parent that is not `delegable`, and `Grant.__post_init__`
   refuses `delegable: true` without `expires_at`.** An envelope carries neither key: the envelope
   branch supplies the meaning, because an envelope exists only to be a parent. The reason behind
   the `delegable` rule, that nothing else bounds the population a delegable grant can reach, is
   met by `max_ttl`, which bounds every child in time by construction and is **required**.
4. **`delegable` is read at three sites that decide something, and each has to be named.** It
   defaults to `False`, and an envelope carries no such key:

   | Site | What it decides | Unaddressed |
   |---|---|---|
   | `_parent_for_creation`'s root test | may this parent be delegated beneath at all | the break-glass grant is never created |
   | `_parent_for_creation`'s rule-3 chain scan, over `walk.ancestors` | may a delegation be created **beneath** a break-glass grant | `AuthorityEscalation(parent_not_valid)`, so §5.4's attenuation bullet and T337 cannot hold |
   | `_check_chain`'s rule 6, over `walk.ancestors`, on every evaluation | does the grant authorise anything | the grant is created and then authorises nothing, refused `authority_escalation` with no dimension named, which is the least diagnosable refusal in the file |

   **An envelope ancestor counts as delegable at all three**, and "counts as" is a rule applied at
   the read sites, **not** a `delegable=True` written onto the parsed `Grant`. Envelopes render
   through `_canonical_grant` like any grant, and its closed field list always emits `delegable`,
   so an envelope hashes with `delegable: false`, the parser default every grant omitting the key
   already hashes as. That is the point: the rendered value comes from the document and never from
   the runtime rule, so the hash cannot move because of a read-site decision. For the same reason
   **`delegable:` and `expires_at:` are refused keys on an envelope entry**, naming them, because
   the parser accepts both today and an operator writing `delegable: true` would then owe an
   `expires_at` an envelope does not carry. T326b asserts evaluation; T337 asserts the second
   level.

**Why the envelope is in the policy.** It is covered by the policy hash, so the widest authority an
incident can reach was evidenced *before* the incident by a document somebody reviewed, rather than
by a command somebody typed at 3am. That requires `canonical_grants` to render envelopes as well as
grants, including `max_ttl`: today it renders `max_delegation_depth` and `authority.grants` through
`_canonical_grant`'s closed field list, so an envelope outside it would be outside the hash and
widening `max_ttl` would move no receipt. §11.1 carries that change.

**A standalone authority document may not declare `break_glass:` at all**, refused by name at
load. `Authority.from_yaml(standalone=True)` closes its top-level keys at `schema` and `authority`,
which is the gateway's shape and `verify --authority`'s, so it has no control registry to resolve
an envelope's `controls:` against. Allowing envelopes there and making an unresolvable citation a
load error, which was the first answer, leaves that shape able to express only an **ungated**
envelope: the one deployment that cannot state the gate would be the one whose break-glass anyone
verified could open. So the whole block is refused there, and an operator who wants break-glass in
that shape moves the authority section into the policy document, where the registry is.

Rejected: an unbounded runtime grant, which is a flag with a record attached. Rejected: a flag on
`Grant` marking it envelope-only, which puts the exclusion inside `_candidates` where a deleted
line is a silent widening.

### 5.3 Opening it

```
ctrlrun break-glass --envelope incident-payments --file grant.yaml --reason "INC-4412"
```

The grant in `--file` is an ordinary one-grant document, as `ctrlrun delegate --file` takes. What
happens is `Control._delegate` with the envelope as parent, so:

- **containment is checked by the code that already checks it**, `contained_dimension`, on every
  dimension it knows, which is six and includes `expires_at`. A grant wider than the envelope on
  any dimension is refused at creation with `AuthorityEscalation`, and §10 drives one refusal per
  dimension through that function, so a dimension added later cannot silently escape the test;
- **an expiry is required**, and one beyond `max_ttl` is refused. This is the one rule §5 adds to
  `v0.3 §5`: an ordinary delegation may carry no expiry, and a break-glass grant that outlives the
  incident is what this section exists to prevent;
- **`created_via` records what it was.** That vocabulary is closed today, `{"api", "cli"}`, typed
  as a `Literal`, and a record carrying an unknown value is *unreadable*, which makes
  `_candidates` raise and returns `authority_unreadable` for **every action in the deployment**. So
  the third value is a public change that moves the `Literal`, the mapping and every reader
  together, and §11.1 lists it as one row for exactly that reason;
- `--reason` is free text recorded on the `DELEGATION_CREATED` event. The kernel does not interpret
  it, exactly as it does not interpret `source:`.

### 5.3.1 Who may open one, and the one `v0.3 §5.3` rule this changes

`plan_delegation`'s rule 4 checks `parent.subject.matches(by)`: the creating principal must be
inside the parent grant's subject. For an ordinary delegation that is right, because a holder is
narrowing authority they hold. **For an envelope it is wrong**, and a first draft of this section
missed it: an envelope's subject names the agents a break-glass grant may be *for*
(`agent: "oncall-*"`), while the principal opening it is a human the approver identity resolved.
Under rule 4 as written, either the human is refused `not_the_subject`, or `by` is an assertion and
nothing about the opener was verified.

So for an envelope, and only for an envelope, **rule 4 is replaced by entitlement**: the opener is
the principal `ApproverIdentity.provider` resolves, and they must hold the `approver_role` of every
control the envelope cites (§3.6's rule, on the envelope's controls rather than a request's). An
envelope citing no control with a role can be opened by any verified principal, and an envelope in
a deployment with no approver identity **cannot be opened at all**: `ctrlrun break-glass` refuses,
naming the missing configuration, because break-glass anyone can open is the flag again.

**The replacement keys off the mapping the parent was resolved from, never off the command.** This
is the second half of the rule and without it the first half is a hole: `_parent_for_creation`
resolves `_grants` before `envelopes` and a root grant wins any collision, so
`ctrlrun break-glass --envelope <an ordinary delegable grant id>` would otherwise reach a path
where rule 4 is skipped for a grant that has no `controls:` to gate it instead, which is strictly
weaker than what `ctrlrun delegate` requires beneath the same grant. Therefore:

- `--envelope` resolves **only** in `Authority.envelopes`, and an id that is not one is refused by
  name, whether or not it names a grant;
- the entitlement-for-rule-4 substitution applies **only** when the parent came from `envelopes`;
- an id in both mappings is a load error (§5.2), so "which mapping" is always answerable.

Rule 4 is unchanged everywhere else, and `v0.3 §5.3`'s other rules (unknown parent, expiry,
containment, depth) apply to an envelope exactly as to a grant.

### 5.4 What it looks like afterwards

- **Every action taken under it names the grant on its receipt.** `ctrlrun.receipt/v5` gains
  `authority_grant_id`, the id of the grant that decided the action, **for every action decided by
  authority and not only for break-glass**. `AuthorityResult.grant_id` already exists and already
  reaches the `AUTHORITY_RESOLVED` and `AUTHORITY_DENIED` events; what nothing does is put it on
  the receipt, so answering "what did this grant let through" means joining events by hand. A field
  that existed only under break-glass would be one nothing exercises on the ordinary path.
- **It expires**, and after its expiry the action it covered is denied on the next proposal, by
  `Authority.evaluate`'s existing expiry check.
- **It is revocable**, by id today and by §7's selectors, and revoking it revokes everything
  beneath it, transitively, by the mechanism `v0.3 §5.7` already describes.
- **It attenuates**: a delegation beneath a break-glass grant obeys `child ⊆ parent` on every
  dimension and cannot outlive it, because that is what containment and `expires_at` already do.

### 5.5 What it does not do

It does not notify anybody, does not page, does not open a ticket and does not close one. It does
not stop an operator opening a second one. It carries no guarantee id: the roadmap assigned five
ids to v0.8 and G22 to G24 to v0.9, so inventing a sixth would either collide with v0.9 or renumber
it, and a renumber is the maintainer's change and not a build item's (§11.5). Its evidence is
§10's tests and the receipt field.

---

## 6. Credential revocation, consumed

### 6.1 The sentence this deletes

`jwt_identity.py`'s module docstring: *"There is no revocation channel. A verified token is valid
until its `exp`, which is why one without an `exp` is refused. Nothing polls, subscribes or
introspects."* `THREAT_MODEL.md` says the same.

Clause by clause, because three of the four move: **"there is no revocation channel"** goes;
**"valid until its `exp`"** goes, and is what this section exists to end; **"nothing polls"** goes,
because `PollingRevocationFeed` polls; **"why one without an `exp` is refused"** stays, and so does
**"nothing subscribes or introspects"**, because a subscription needs an endpoint this project
serves and an introspection call is a question this project does not ask. Item 6 rewrites the
docstring to exactly that, and `THREAT_MODEL.md` with it.

### 6.2 The shape

```python
# ctrlrun.revocation: ctrlrun[identity], lazy, never imported by `import ctrlrun`
class RevocationFeed(Protocol):
    def revoked(self, *, issuer: str, subject: str, token_id: str | None) -> bool: ...
    @property
    def read_at(self) -> datetime | None: ...
    @property
    def issuers(self) -> frozenset[str]: ...
    @property
    def max_staleness(self) -> timedelta | None: ...
```

`JWTIdentityProvider(..., revocations: RevocationFeed | None = None)`. Absent means 0.7.0, exactly
as R1 requires.

Two feeds ship (O3, decided here):

- **`FileRevocationFeed(path, ...)`**: a file of Security Event Tokens, one per line, that the
  operator's own transmitter writes. Re-read when its mtime moves.
- **`PollingRevocationFeed(url, ...)`**: RFC 8936 poll delivery, over stdlib `urllib` through the
  same hardened opener `jwt_identity.py` already uses for JWKS: no redirects, an allow-listed
  scheme, a bounded body.

**Push (RFC 8935) is not built.** It needs an HTTP endpoint this project serves and a session to
serve it on, which is delivery work, and delivery work is on the do-not-build list beside
notification delivery. An operator with a push transmitter writes received SETs to the file feed,
which is a few lines of their code and none of ours.

### 6.3 What is consumed

A SET whose `events` claim carries a CAEP event type this feed knows, naming a subject in one of
the RFC 9493 formats it knows (`iss_sub`, and `opaque` matched against the token's own subject).
Everything else is **consumed without changing any decision** and logged: an unknown event type, an
unknown subject format, an issuer no configured provider uses, a malformed token.

**What the subject is matched against, stated precisely because the obvious answer is wrong.** Not
`Principal.agent`: that is whatever `agent_claim` names, which an operator may set to `client_id`
or anything else, so matching an `iss_sub` identifier against it would compare two different things
and **admit a principal the issuer revoked**. The feed is consulted **inside
`JWTIdentityProvider._verified`**, where the raw verified claims are still in hand, and the match is
against the token's own `iss` and `sub`, plus `jti` where the event names one. Nothing new is
retained on `Principal`: `jti` is read where it exists and never stored, which is why §6.4's refusal
happens at resolution and not later.

**The SET's own signature is verified** where the feed is given a key source, through the key
handling `jwt_identity.py` already has. Where it is not, the feed's trust is the file's, and §6.6
says what that means.

### 6.4 What is refused

A principal whose `(iss, sub)` or `jti` the feed reports revoked is refused **at resolution**,
inside the provider, as `IdentityError`, exactly as a token that fails verification is refused
today.

**What that means for the evidence, and it is less than a first draft claimed.** It writes nothing:
no event, no receipt. `Control.resolve_principal` is called before an `Action` exists, and
`@protect` calls it before building one, so there is no `action_id` to attribute a refusal to and
no receipt to write it on. This is **not** the path `v0.3 §2.3` uses for an expired credential:
that one is checked on `action.principal` inside `execute`, where an `Action` exists, and it
produces an `ACTION_DENIED` event and a `DENIED` receipt.

The asymmetry is deliberate and is stated wherever this feature is described: **an expired
credential leaves a receipt; a revoked one leaves a log line.** Closing it would mean giving
`Control` the feed as a new input, a new cell in `v0.3 §4.3.1`, and a check duplicated in two
places, which is a second thing to keep correct for evidence about an action that never began.
Rejected on that basis, and §14.6 is where the item records whether building it changed the
argument.

**G20** grades exactly what happens: a credential with a future `exp`, revoked, refused at
resolution with the reason in the log and the call raising `IdentityError`, with a positive control
in the same run where an unrevoked credential from the same issuer is admitted. A feed that refuses
everything is not a feed.

### 6.5 Staleness (O4, decided here)

`RevocationFeed` carries an operator-set `max_staleness: timedelta | None`.

- **Absent means no bound**, which is 0.7.0's availability and `v0.7 §5.4`'s rule for the attempt
  ceiling: the operator sees the gap in `verify` rather than having a number chosen for them.
- **Past the bound, every principal whose issuer the feed covers is refused**, with reason
  `revocation_feed_stale`, and principals from issuers the feed does not cover are unaffected. A
  security check whose answer is unavailable is fail closed: the question "has this been revoked"
  is exactly the question a stale feed cannot answer, and admitting on silence would make the
  bound decoration.
- The refusal is loud: one warning per feed per staleness episode, naming the feed, its `read_at`
  and the bound, so an operator reading logs during an outage learns why everything stopped.

This is the trade `ROADMAP.md` implies and it is stated here rather than discovered: **configuring
`max_staleness` makes the feed's availability part of the deployment's availability.** An operator
who will not accept that leaves it unset and accepts the window instead. Both are defensible; a
kernel choosing for them is not.

### 6.6 What this does not close

A feed is worth what its source is worth. Someone who can write the file, or stand in front of the
poll endpoint without a verified signature, can **refuse** principals at will: that is a denial of
service against the operator's own agents, it is fail-closed, and it is in the threat model under
this section's name. They cannot **admit** a principal the issuer revoked, because the feed is only
ever consulted to refuse: there is no path on which a feed's answer makes an otherwise-invalid
credential valid. That asymmetry is the security property, and every page describing this feature
states it in those terms.

A revoked credential also leaves **no receipt** (§6.4). An operator reconstructing an incident
finds it in the logs and not in the evidence chain, and `THREAT_MODEL.md` says so in the same
sentence that describes the feature.

### 6.7 No standards claim

RFC 8935, RFC 8936, RFC 9493 and CAEP are consumed as code. The words compatible, conformant,
aligned and certified do not appear in the code, the docstrings, the CLI, the changelog or the
README. `ROADMAP.md`'s Standards line for v0.8 says a mapping document comes only after a
conformance suite exists, and "SSF-compatible" is unearned until one says otherwise.

---

## 7. Revocation by selector

### 7.1 Why it is in the kernel

`ctrlrun revoke` takes one id, and `authority.md` says the ids are in the events file. During an
incident the operation an operator reaches for is *everything this principal issued* or *everything
under this grant*, and today that is a script over the events file written under pressure. The
alternative was building it in the product, and the kernel does not gain a primitive for the
dashboard's sake, so this one is the kernel's because it was missing from the kernel.

### 7.2 The collision, and the names

`ctrlrun revoke <id> --by <who>` exists and records **who performed the revocation**. The
roadmap's `--by <principal>` means **whose delegations to revoke**. Two meanings on one option is a
defect, so:

- `--by` keeps its meaning, unchanged, and every script written against 0.7.0 keeps working;
- the selector is **`--created-by AGENT`** or **`--created-by AGENT/USER`**, split on the first
  `/`, exactly as `delegate --as` splits, and with the same refusal of an agent name containing a
  `/`;
- the other selector is **`--under <grant id>`**.

`--created-by` and `--under` are mutually exclusive with each other and with a positional id, and
each combination is a usage error.

### 7.3 What it does

A query over rows that already exist. `store.delegations(include_revoked=True)` is read, the
matches are filtered above the store, and **each match is revoked exactly as one id is today**:
`Control.revoke`, one revocation, one `DELEGATION_REVOKED` event, transitive by structure. No new
`StateStore` method, no bulk statement, no transaction over the set.

- `--created-by` matches `DelegationRecord.created_by_agent` and, where a user is given,
  `created_by_user`, both exactly.
- `--under` matches the delegation subtree of that grant id at every depth. Revoking the root of a
  subtree already revokes it transitively, so the selector's own job is to reach the delegations
  whose parent chain includes the id even where the id is a policy grant with several children.

### 7.4 Idempotence, and a run that stops halfway

Revoking an already-revoked delegation is idempotent and exits 0 today, and a selector run inherits
that per row. So:

- a second run over the same selector revokes nothing further, exits 0, and says how many were
  already revoked;
- **a run that stops halfway leaves the rows it reached revoked and the rest untouched, and a
  second run finishes.** This is the property that makes it safe to repeat during an incident, and
  §10 proves it by killing the command between two rows rather than by reasoning about it.

### 7.5 An empty selector is an error

A selector matching nothing exits non-zero and names what it searched for. This is the only place
in v0.8 where an empty result is a failure, and the reason is the situation it is used in: a typo'd
principal name that exits 0 during an incident reads as "done", and the operator moves on.

### 7.6 Still no unrevoke

In any costume: no `--undo`, no restore, no `revoked_at = NULL`, no `--dry-run` that writes.
`v0.3 §5.7` gives the reason: the operation whose safety matters is the one taken in a hurry.

---

## 8. A policy change is a protected action

### 8.1 The sharp case

The policy is the one file that decides every other decision, and today it is changed by editing
it. v0.6 made the change *evidenced*: every receipt records the hash of the policy that decided it,
so a reader can see afterwards that the rules moved. v0.8 makes it *approved*: a policy nobody
approved decides nothing.

### 8.2 The change as an action

A policy has a canonical form, and `Control` already computes it:
`hash_with_authority(policy, authority, environment)`, which is what `policy_hash` is. So a change
has an action:

| | |
|---|---|
| name | `ctrlrun.policy.change`, **reserved**: only the policy-change flow may use it (§8.2.1) |
| arguments | `{"from": <policy hash or null>, "to": <policy hash>}` |
| resource | `policy` |
| effect key | `policy:<to>` |
| principal | whoever proposes, resolved as any other principal is |

It is an ordinary `Action`: ordinary action hash, ordinary effect key, ordinary events, ordinary
receipt. **That is why §8 adds no event type.** `v0.1 §6.2`'s vocabulary already describes a
proposal, an approval request, a grant, a consumption and a commit, which is the whole life of a
policy change.

**Stable across formatting, and per deployment.** The hash is over the canonical form and not the
text, so comments, key order and whitespace do not move it. It is **not** a property of the file
alone: `hash_with_authority` folds in the effective authority and the effective environment, so the
same file in `staging` and in `prod`, or with and without a separately loaded authority document,
hashes differently. `ctrlrun policy propose` therefore computes `to` **the way the `Control` that
will enforce it computes its own hash**, with that authority and that environment substituted, and
an approval is consequently **per environment**. That is the behaviour an operator wants, it is not
obvious, and §10's T353 pins it.

### 8.2.1 Reserved, and declarable, because otherwise nothing works

A first draft said a document declaring `ctrlrun.policy.change` fails to load. That is
unbuildable: `Policy.evaluate` answers `DENY unknown_action` for any action the document does not
list, so a name no document may declare is a name every proposal is denied for, no committed
receipt is ever written, and a deployment with `require_approved_policy=True` denies every action
for ever. The two rules were mutually exclusive.

So the name is **reserved and declarable**:

```yaml
schema: ctrlrun.policy/v6
actions:
  ctrlrun.policy.change:
    decision: approve
    approvals_required: 2
    controls: [change-management]
```

- A document **may** declare it, under `ctrlrun.policy/v6` and not before.
- Nothing else may **declare** it as a `resource:` or an `effect:` template's expansion, and the
  loader refuses a document that does.
- **A proposal of that name is gated by a marker the flow sets, and the honest statement is
  §2.5.1's.** `Control.execute(action, executor, effect_key)` takes an `Action` the caller built
  and carries no channel distinguishing `ctrlrun policy propose` from any other in-process caller,
  so what gates it is a package-internal context variable the flow sets, `_policy_change_in_flight`,
  named and listed in **§11.2** beside `_granting_principal`, not in §11.1, whose closing line is
  "and no other public name". It carries the same residual: **an application that calls the private
  interface is inside the process trust boundary**, as §2.5.1 already concedes. So the claim is not
  "nothing else can propose it"; it is **"nothing outside the flow proposes it by accident, and the
  shipped surfaces are the only ones that set the marker"**. `require_approved_policy`'s property
  in §8.6 does not rest on this gate, which is why narrowing it costs nothing.
- **Under `require_approved_policy`, the in-force policy must declare it with
  `decision: approve`.** A policy that declares it `allow`, or omits it, **decides nothing**, with
  the same `policy_unapproved` refusal. This is the rule that closes the hole §8.6 would otherwise
  have: an administrator can write a policy whose change rule is `allow`, and installing it still
  needs an approval under the *old* policy, and the moment it is installed the deployment stops
  deciding anything. The refusal names the key, so an operator reading the log sees why.

### 8.3 The commands

- **`ctrlrun policy propose --file new.yaml`** loads the candidate, computes `to`, and runs the
  action through `Control.execute` under the policy currently in force. Where that policy sends it
  to approval, the ordinary approval path applies, which means §2, §3 and §4 apply: an unverifiable
  approver is refused, an unentitled one is refused, a proposer approving their own change is
  refused, and M-of-N counts. A committed receipt for that action **is** the approval of that hash.
- **`ctrlrun policy approve`** is `ctrlrun approve` and is not a second command: the request is an
  ordinary approval request. The name exists in this spec only to say it does not exist in the CLI.
- **`ctrlrun policy replay --file new.yaml --last N`** is §8.5.

### 8.4 Enforcement

`Control(..., require_approved_policy: bool = False)`.

- **Where it is false**, nothing changes, and G21 is graded under a flag verify sets for its own
  scenario, with the note §11.7 requires rather than an `N/A` about a document that says nothing on
  the subject.
- **Where it is true**, a decision is made only if `store.get_effect(f"policy:{self._policy_hash}")`
  is a `COMMITTED` record. Where there is none, **every evaluation is a denial** with reason
  `policy_unapproved`, recorded as `ACTION_DENIED` and a `DENIED` receipt, and raised as
  `ActionDenied`. That is what "decides nothing" means.
- **A keyed read, not a scan.** `get_effect` is on the frozen protocol and is O(1) on both shipped
  stores. A first draft said "asks the store whether a committed receipt exists", which
  `StateStore.receipts()` can only answer by returning every receipt in the store, oldest first,
  parsed, on the first decision of every process. The effect key is what makes the question cheap,
  and it is the same key the proposal reserved, so the two cannot drift.
- **Cached only when the answer is yes, and the asymmetry is deliberate in both directions.** A
  negative answer is **not** cached, so a long-lived process that started before the approval
  landed begins working the moment it lands, with no restart, at the cost of one keyed read per
  decision while a deployment is unapproved, which is the state where nothing is running anyway.
  A positive answer **is** cached for the life of the `Control`, because a `COMMITTED` effect does
  not become uncommitted through any path this kernel offers.

  **What that cache costs, stated because §8.6 would otherwise overclaim.** It can be made to lie
  by a write this kernel does not perform: an administrator who deletes the effect row leaves a
  process that has already cached the yes still deciding, until it restarts. The deletion stops
  every `Control` built afterwards and every one that had not yet asked, and it is recorded as a
  chain break (`v0.6 §6`), but it is **not retroactive for a running process**. That is the shape
  of every cached authorization decision, it is bounded by the process lifetime, and §8.6 carries
  it as a residual rather than as a fail-closed claim.
- **Lazily, not in the constructor**, because a constructor that queried the store would make
  building a `Control` a database call, and `@protect` builds one per process at import time.
- **The requirement lives in code, not in the policy file.** A policy that could switch off its own
  approval requirement would be switched off in the same edit that removes everything else. §8.6
  states what that does and does not buy.
- **`ctrlrun.policy.change` is exempt from the refusal**, and it is the only exemption, matched by
  name and not by prefix. Without it the first change under a fresh deployment could never be
  approved. The action still passes principal expiry, authority, policy and the approval gate, and
  §8.2.1's rule means the policy must send it to approval or the deployment decides nothing.

**An approval binds a hash, not an ordering**, and §8.6 carries what that costs.

**The bootstrap is recorded as a bootstrap.** Where no policy hash has ever been approved, the
first `ctrlrun policy propose` records `from: null`, and the receipt says so. An empty store is
never read as an approval of whatever is on disk: there is no path on which "nothing recorded"
means "approved".

### 8.5 The diff replay

`ctrlrun policy replay --file new.yaml --last N` reads the last `N` receipts, rebuilds each action
from what the receipt records (name, arguments, principal, resource, environment), evaluates each
against the **proposed** policy, and prints the ones whose decision or reason changes.

- It **writes nothing, executes nothing and reserves nothing.** §10 asserts the store is byte
  identical afterwards.
- It reports **what changes**. Never safer, riskier, too permissive, a score, a percentage or a
  grade. `v0.4 §3.9`'s rule for `verify`, applied here: this kernel does not grade an operator's
  document, and a replay that scored one would be the same claim in a new costume. §10 asserts the
  absence of that vocabulary by word.
- A receipt whose action cannot be rebuilt (a schema the binary does not know) is **named and
  skipped**, not counted as unchanged, on the distinction `v0.6 §3.2` draws for an unknown
  `schema_version`.

### 8.6 What §8 does not close, stated in full

- **An administrator with write access to the policy file** proposes under a policy they wrote.
  §8.2.1 stops the obvious escape, a new policy whose change rule is `allow`: installing it still
  needs an approval under the policy in force, and once installed such a policy decides nothing.
  What they can still write is a policy whose `controls:` require no `approver_role`, which widens
  *who* may approve the next change to any verified principal. What they cannot manufacture is the
  approving principal itself: the approver's credential is verified by the provider configured in
  code, and §4.1 refuses their own. So the property is:

  > **A policy change that no verified principal other than the proposer approved decides
  > nothing.**

  Not "a policy cannot be changed by whoever holds the file", and no page may say the second.
- **An approval binds a hash and not an ordering, so any hash ever approved stays approved.** The
  marker is a `COMMITTED` effect at `policy:<to>`, and a committed effect does not become
  uncommitted. An administrator can therefore restore a superseded policy file, and the deployment
  decides normally with nothing in the evidence saying a rollback happened. This is a property of
  keying the approval by content rather than by sequence, which is what makes it cheap and
  deterministic; ordering would need a notion of "current" that two hosts could disagree about. It
  is stated here because it is exactly the kind of thing this section exists to state.
- **An administrator with write access to the store** deletes the effect record that marks the
  approval. Every `Control` built afterwards, and every one that had not yet asked, then refuses
  every action: a denial of service, fail closed, and the receipt chain records the surrounding
  deletion as a break (`v0.6 §6`). **A process that had already cached the yes keeps deciding until
  it restarts** (§8.4), so the refusal is not retroactive, and no page may say a deletion stops a
  running deployment.
- **An administrator with write access to the code** switches `require_approved_policy` off.
  `THREAT_MODEL.md`'s malicious-administrator line is unchanged and names this.
- **A persuaded approver** approves a policy change as they would approve anything else (§1.1).

## 9. The `v0.3 §4.3.1` columns

`v0.3 §4.3.1` lists every entry point and what each does about principal validity, authority and
policy. v0.8 adds **two columns**, and the table below is **not** that table with two rows appended:
it is the amended table, and the differences from `v0.3 §4.3.1` are stated here rather than left
for a reader to diff.

- **Two genuinely new rows**, `ctrlrun policy propose` and `ctrlrun break-glass`, both new callers
  of existing entry points (`Control.execute`, `Control._delegate`). Item 5 and item 7 add them to
  `v0.3 §4.3.1` itself, with their answers to that table's original columns as well as these two.
- **`Control.delegate` and `Control.revoke` are split into two rows**, because they now answer
  differently: delegation creates authority and refuses under an unapproved policy, revocation only
  narrows it and must not.
- **Three rows are added that `v0.3 §4.3.1` does not carry**, because these two columns are the
  first thing that makes them interesting: `ctrlrun approve` / `deny`, the evidence commands, and
  `ctrlrun.verify.run`.
- **`@protect` is folded into `Control.execute`**, which is what it calls, and the row says so.

Every row whose answer is "no" carries its reason, because the rule that puts every entry point in
one table exists for a hole that was a missing enumeration and not a missing check.

| Entry point | Checks the approver at consumption (§2.4) | Refuses under an unapproved policy (§8.4) |
|---|---|---|
| `Control.execute`, including through `@protect` | **Yes**, for every `APPROVE` decision, before `_take` | **Yes** |
| `Control.evaluate` | No: it consumes no approval and writes nothing | **Yes**: an evaluation is a decision, and this is the milestone that says an unapproved policy makes none |
| `Control.resume` | No: the approval was consumed on the suspended leg and the remote may already be acting. Refusing here strands a reservation, which is `v0.6 §7.2.3`'s reason for not rechecking preconditions there either | No, the same reason: the continuation exists because a remote is holding an exchange |
| `Control.delegate` | No: it consumes no approval | **Yes**: it creates authority, which is the one thing an unapproved policy must not widen |
| `Control.revoke` | No: it consumes no approval | **No, deliberately.** Revocation only ever narrows authority, and an incident is exactly when a policy may be unapproved. A kernel that refused to revoke because its policy was unapproved would fail closed into being unable to close anything |
| The MCP gateway | Through `Control.execute`: **yes** | Through `Control.execute`: **yes** |
| The ACS hook | Through `Control.execute`: **yes** | **Yes** |
| Both adapters' executing path | Through `Control.execute`: **yes** | **Yes** |
| `ctrlrun.adapter.needs_approval` | No: it reads and decides nothing | It reports what the policy says, `policy_unapproved` included, and refuses nothing itself |
| `gateway/operator.py`'s write tools | It **grants**; it does not consume. It resolves and records a `VerifiedApprover` (§2.6) | No: it writes approvals and resolves effects, and neither is an evaluation |
| `gateway/operator.py`'s read tools | No | No: they read evidence |
| `ctrlrun approve` / `ctrlrun deny` | Grants, and cannot produce a verified approver in the shipped CLI (§2.6) | No: they load no policy today and §8 does not make them start |
| **`ctrlrun policy propose`** (new) | Through `Control.execute`: **yes** | **Exempt**, and the only exemption, matched by name (§8.2.1) |
| **`ctrlrun break-glass`** (new) | No approval is consumed. The opener is resolved and entitled by §5.3.1, which is the same check in a different place | **Yes**: it creates authority, as `Control.delegate` does |
| `ctrlrun.verify.run` | It grants and consumes inside its own scratch stores, so **yes**, against the identity it configures for itself (§2.6) | It builds its own `Control`s and does not set the flag, except in G21's scenario, which sets it on purpose |
| `ctrlrun receipts` / `inspect` / `effects` / `stats` | No | **No**: they load no policy at all, and making an evidence command load one would turn a malformed or unapproved policy into a failure of the evidence. `cli/main.py` already states this rule |

## 10. Acceptance tests

Numbered from T272. Written before the implementation of their item: a red suite is the spec.
Every test that asserts a refusal asserts the **reason**, never the type alone, because §2.7's
four refusals share a type (`CONTRIBUTING.md`, the first of the four shapes of a false green).
Every test that claims to open a window opens it on purpose (the fourth shape). Every negative
test states its precondition, so it cannot pass because the environment already prevented what it
forbids (the third).

### 10.1 Item 1: revocation by selector (§7)

- **T272:** `--created-by AGENT` revokes every delegation that agent created and leaves every other
  row untouched. Asserts both directions on one store holding rows from three creators.
- **T273:** `--created-by AGENT/USER` matches on both fields; `--created-by AGENT` matches rows
  created by that agent with any user, and the test has rows of both shapes to tell the readings
  apart.
- **T274:** `--under <grant id>` revokes the subtree at every depth, including a grandchild, and
  nothing outside it.
- **T275:** each match produces one `DELEGATION_REVOKED` event and one revoked row, and a second
  run produces none: idempotent, exit 0, and the message says how many were already revoked.
- **T276:** the half-finished run. The command is killed between two rows (a real interruption, a
  real store, bounded); the rows it reached are revoked, the rest are untouched, and a second run
  finishes. A test that simulates the interruption by calling an internal function has not opened
  the window.
- **T277:** an empty selector exits non-zero and names what it searched for (§7.5).
- **T278:** `--created-by` with `--under`, either with a positional id, and `--created-by a/b/c`
  are usage errors, each with its own message.
- **T279:** `--by` keeps its 0.7.0 meaning, on a selector run and on a single-id run, and the
  revoked rows record it.
- **T280:** both backends, and the Postgres run under the multi-process standard of `v0.6 §8`.

### 10.2 Item 2: the approver is a principal (§2)

- **T281:** THE test. With an `ApproverIdentity` configured, an approval whose row carries no
  `VerifiedApprover` is refused at consumption with `approver_unverified`; nothing is reserved, the
  executor is not called, the approval is **still granted** afterwards, and the row is unchanged.
- **T282:** the positive control. With no `ApproverIdentity`, the whole approve-and-execute path is
  driven and the receipt is compared field by field against 0.7.0's. A milestone that broke every
  existing user on upgrade would have R1 backwards.
- **T283:** a grant through a surface that resolves records the principal, and the row's
  `VerifiedApprover` carries agent, user and issuer and **no claim value**: the test puts a
  sentinel in the claims and greps every written row, event and receipt for it (`v0.7 §6.10`'s
  shape).
- **T284:** the receipt carries the approvers under `ctrlrun.receipt/v5`, and the existing
  `approver` string still says what it said at 0.7.0.
- **T285:** G18. The requester's resolved principal equals the approver's, the strings differ, and
  the approval is refused with `approver_is_requester`. The differing strings are what prove the
  check is not cosmetic.
- **T286:** G18's positive control: a different principal approves, and the action runs.
- **T287:** a provider that raises is never backfilled from anything the calling code said
  (`v0.3 §3.2` applied to this door), and a provider that declines refuses the grant rather than
  falling back, because there is no context to fall back to.
- **T288:** §2.6's table, one test per row, asserting which half of the table the row is in. The
  operator server resolves and records, with `--identity-jwt`; **the same server with
  `--principal-header` produces a verified approver that satisfies no role**, because that provider
  carries no claims, and the test asserts `approver_unentitled` rather than a pass. `ctrlrun
  approve`, `handle_inbound`, the scripted provider and `ApprovalAnswer` record a string, and the
  approvals they grant are refused at consumption. `Control._withdraw` records no approver and is
  unaffected.
- **T289:** `ApproverIdentity` with a `StaticIdentityProvider` warns once, naming the provider
  type, and does not refuse (§2.3).
- **T290:** the `IdentityContext` an approval resolution receives carries the **stored request's**
  action and environment and `agent=None, user=None` (§2.8), asserted by a recording provider.
- **T291:** the early return is gone. A deployment with an `ApproverIdentity` and **no precondition
  provider anywhere** still reaches the approver checks: the test drives the 0.6-shaped path and
  asserts the refusal. Without this, every check in §2 to §4 is dead on the default path and every
  other test still passes (§2.4).
- **T291b:** the gate of §2.4.1, every row. With an `ApproverIdentity` configured and no
  `VerifiedApprover` on the row: a **denied** approval still raises `ActionDenied(approval_denied)`
  with `APPROVAL_DENIED` and a `DENIED` receipt; a **consumed** one still reports `consumed` (G2);
  a **mutated action** still reports `mismatch` (G1); a **pending** one reports `pending` and an
  **unknown** one `unknown`, which is the reason §2.7's fifth row and all of item 4's M-of-N
  depend on; and a **granted but lapsed** one whose approver is fine still reports `expired`,
  appends exactly one `APPROVAL_EXPIRED`, and leaves the row moved to `expired` by the store's own
  write. The lapsed row is the one a status-only gate gets wrong in both directions, and §2.4.2
  is its argument: its own test asserts what checking it costs, which is that a grant both lapsed
  **and** approver-refused keeps no `APPROVAL_EXPIRED` and no lapse write.
- **T291c:** the skew §2.4.2 exists for. A `Control` whose clock runs ahead of the store's presents
  a self-approved grant: refused `approver_is_requester`, the executor not called, **the approval
  still granted and nothing reserved**. Without the last two assertions the test cannot tell a
  refusal before the store call from one after, which is what let the deferral look correct.
- **T283b:** `entitled` is validated before it is converted. `str` is a `Sequence`, so
  `tuple("abc")` is three control ids: a corrupted column was accepted as an approver entitled for
  controls that do not exist, which made `_approvers_from_json`'s promise to raise false for the
  shape a corruption most easily takes.
- **T296b:** the report still adds up. An observe-mode approval refusal, and an observe-mode
  **denial by a human**, are each counted by `ctrlrun stats`. `would_have.blocked_reason` is a
  closed vocabulary because `v0.3 §6.4` buckets counts on it, and recording each mismatch's own
  reason put them in no bucket at all. Driven with no `ApproverIdentity` anywhere, because the
  receipts that stopped counting have nothing to do with v0.8, and the denial one was already
  uncounted before it.
- **T296c:** a resumed leg carries the approvers onto its receipt. That leg is the **only** receipt
  an MCP multi round-trip or an ACS action ever gets (`SPEC-mcp-operator.md` §8.3), so without it
  §2.5's "carried onto the receipt" is false for exactly the actions that get one receipt.
- **T292:** the migration ledger reaches `HEAD` and the column round-trips through a file-backed
  store. The 0.6.1-built upgrade in both directions is `test_preconditions.py`'s T264, and §14.2
  says why this one does not duplicate it.
- **T293:** a chain carrying a `v5` receipt verifies end to end, and a `v4` document still parses
  with neither `v5` key. The stored-v3-continued-by-this-binary chain is T265's.
- **T294:** `ctrlrun.guarantees/v3` becomes `v4` here, and `verify` reports the catalogue version
  and G18, graded, never `N/A` (§11.7).
- **T295:** the upgrade case of §2.9: an approval granted at 0.7.0, still pending, presented after
  an `ApproverIdentity` is configured, is refused with `approver_unverified`.
- **T296:** observe mode. A self-approval records `approver_is_requester` on
  `would_have.blocked_reason` through `_observe_take`, which does not call `_recheck` and therefore
  needs the checks added there too; **and** the test asserts the deliberate consequence, that every
  other `ApprovalMismatch` now records its own reason where it recorded the constant
  `approval_mismatch` before (§4.1).

### 10.3 Item 3: entitlement (§3)

- **T297:** an approver who does not hold the required role is refused with `approver_unentitled`,
  and the refusal **names the control id and the role**, asserted by value in the message, the
  exception and `APPROVAL_INVALIDATED.data`.
- **T298:** the positive control: an approver who holds it approves, and the action runs.
- **T299:** the claim carries a **tuple of strings** and entitles for each of them, which is the
  case `ClaimValue`'s amendment exists for; a `JWTIdentityProvider` given a token whose `roles`
  claim is a JSON array carries it as a tuple rather than dropping it (§3.4).
- **T299b:** the round trip the `ClaimValue` amendment needs. An action and a receipt carrying an
  array claim are written and read back on both backends, the JSON list is normalised to a tuple,
  `Receipt.from_dict` does not raise on it (`v0.7 §6.11`'s contract), a held continuation resumes,
  and a receipt chain spanning them verifies. Without this half of the amendment, every stored
  action and every receipt carrying an array claim raises on read.
- **T300:** omission A. A principal whose claims lack the role claim entirely is not entitled, and
  a **warning** is emitted naming the claim and the control (§3.4).
- **T301:** omission B. A control naming no `approver_role` gates nothing, and an approval citing
  only such controls runs with any verified approver (§3.5). T300 and T301 together are the pair R2
  exists for, and neither may be deleted without the other failing.
- **T302:** a claim that is an int, a bool, or an empty string: not entitled, never coerced, never
  stringified, one case each.
- **T303:** matching is byte for byte: `payments-owner ` with a trailing space does not satisfy
  `payments-owner`, and `PAYMENTS-OWNER` does not either.
- **T304:** `roles_claim=None` with a policy that requires a role refuses (§3.4's last bullet), and
  so does a provider that carries no claims at all.
- **T305:** several cited controls: the approver holds one role and not the other, and is refused.
  The test asserts the refusal and the control named, so it cannot pass under an any-of reading
  (§3.6).
- **T306:** the roles are pinned at request time: the policy's roles change between the request and
  the grant, and the roles in force at the request are the ones applied (§3.3, `v0.6 §7.1`).
- **T307:** the grant-side and consume-side refusals are separate defences with separate tests: the
  consume-side test presents a row whose `entitled` a store wrote without checking (§3.8), which
  the grant-side refusal cannot reach.
- **T308:** the registry loads in mapping form with `approver_role` in the entry's closed key set;
  a malformed value (empty, a list, a number) is refused naming the key and the line; and a
  document using the key without `schema: ctrlrun.policy/v6` is refused naming the schema.
- **T309:** G17 in `verify`, graded, with its positive control and an `N/A` reason that is true of
  a **document** naming no approver role (§11.7).

### 10.4 Item 4: M-of-N (§4.2)

- **T310:** N distinct verified principals grant; the approval is consumable only after the Nth. A
  consume attempted at N-1 is refused with `pending`, nothing is reserved, the executor is not
  called.
- **T311:** G19. Two grants from the same resolved principal with different approver strings count
  **once**: the count does not move, the second grant is not an error, and that approver's
  `granted_at` moves.
- **T312:** a grant arriving after the record reached `granted` is refused exactly as at 0.7.0, by
  `check_answerable` (§4.2).
- **T313:** the concurrency case, deterministic, and it is aimed at the defect §4.3 names. It
  lives in `tests/test_attempt_integrity.py` with the proxy and the child-process harness the
  window needs, and `tests/test_m_of_n.py` names it where a reader of the item's tests looks. Separate
  OS processes against Postgres, with the window opened **between the read of `approvers` and the
  update**; N grants never become N+1 and one principal never fills two slots. The test is written
  so that it **fails against a compare-and-set on `status`**, because that is the shape the store
  has today and the one a reviewer found: a test that passes against it has not opened the window.
- **T314:** the requester's own yes is refused at consumption with `approver_is_requester`, and
  the row shows the count *did* move, which §4.2's table and §14.4 explain.
- **T315:** an unentitled yes is refused with `approver_unentitled`, and the same about the count.
- **T316:** an unverifiable yes does not count, and the count is asserted unmoved: this is the one
  of the three that the count itself excludes.
- **T317:** one denial denies a request holding N-1 grants.
- **T318:** expiry: a request with N-1 grants expires as a whole, and the grants do not extend it.
- **T319:** `approvals_required: 1` is 0.7.0 exactly, driven through the whole path and compared.
- **T320:** policy load refuses `0`, `-1`, `true`, `1.0` and `"2"`, naming the key and the line.
- **T321:** `approvals_required: 2` with no `ApproverIdentity` denies the action with
  `approvals_unverifiable`, naming the action and the key, at evaluation and not at load (§4.2).
- **T322:** `grant_approval` returns `None` at N-1 and an `Approval` at N; and **no
  `ApprovalProvider.wait` ever returns `None` for a partial grant**, which `v0.1 §4.3` defines as a
  denial. One test each for `ScriptedApprovalProvider.wait` and `InterruptApprovalProvider.wait`,
  asserting that `@protect(wait=True)` does not raise `ActionDenied` while a second human is still
  answering (§4.4).
- **T323:** the other callers do something useful with `None`: the CLI says how many more are
  needed, the operator server returns it, `handle_inbound` answers 200 with it.
- **T324:** a store that records one approver only never reaches N and never behaves as N=1, and
  its conformance case reports `not_applicable` with that reason (§4.5).
- **T325:** G19 in `verify`, with its positive control and its `N/A` reason; and the store
  conformance case of §4.3 on all three shipped stores.

### 10.5 Item 5: break-glass (§5)

- **T326:** a break-glass grant is created beneath its envelope, recorded, and the action it covers
  is allowed while it lives.
- **T326b:** an action under a live break-glass grant is **allowed at evaluation**, not only
  created, on both backends. `_check_chain`'s rule 6 reads `delegable` over every ancestor
  including the root, and an envelope carries no such key, so without §5.2 point 4 the grant is
  created and then authorises nothing, refused `authority_escalation` with no dimension named.
- **T327:** after its expiry the same action is denied on the next proposal, by the existing expiry
  check and with the existing reason.
- **T328:** a grant with no expiry is refused at creation; one beyond the envelope's `max_ttl` is
  refused (§5.3).
- **T329:** containment, **one refusal per dimension `contained_dimension` knows**, which is six
  and includes `expires_at`. Driven through that function, so a dimension added later cannot
  silently escape the test.
- **T330:** the envelope decides nothing, **by construction**: it is not in `Authority._grants`, so
  `_candidates` cannot return it. The test asserts a deployment with an envelope and no grant
  beneath it evaluates exactly as 0.7.0, and separately asserts the envelope is absent from the
  candidate set rather than merely unmatched (§5.2).
- **T331:** the walk resolves a break-glass delegation's root out of `envelopes`, and a delegation
  naming an unknown envelope is `unknown_parent` as any unknown parent is.
- **T332:** the policy hash covers the envelope, `max_ttl` included: widening `max_ttl` moves
  `policy_hash`, and the test pins that it does. It also pins that the envelope renders
  `delegable: false`, the parser default, **and that the read-site rule of §5.2 point 4 does not
  move it**: a deployment where break-glass grants evaluate correctly hashes identically to one
  where the rule is absent, which is what keeps the hash a statement about the document.
- **T332b:** an envelope entry declaring `delegable:` or `expires_at:` is refused at load, naming
  the key; an id in both `grants:` and `break_glass:` is refused, naming both; and a **standalone
  authority document declaring `break_glass:` at all** is refused, naming the block and saying that
  its gate lives in a registry that document cannot see (§5.2).
- **T333:** the receipt of an action decided under it carries `authority_grant_id`, and so does the
  receipt of an action decided by an ordinary grant: the field is not break-glass-specific (§5.4).
- **T334:** §5.3.1's rule: the opener is the resolved principal and an envelope's subject does not
  gate them; an opener who lacks the envelope's control role is refused; and **opening one with no
  `ApproverIdentity` configured is refused**, naming the missing configuration.
- **T334b:** `--envelope` pointed at an **ordinary delegable grant id** is refused by name, and the
  rule-4 substitution never applies to a parent resolved from `grants:`. Without this,
  `ctrlrun break-glass` is a path on which `plan_delegation` rule 4 is skipped for a grant that has
  no `controls:` to gate it instead, which is weaker than what `ctrlrun delegate` requires beneath
  the same grant. And an id declared in both mappings is a load error, naming both (§5.2).
- **T335:** the third `created_via` value is readable: a record carrying it loads, and
  `Authority.evaluate` does not answer `authority_unreadable` for the deployment. This is the test
  for the failure mode a closed `Literal` produces (§5.3).
- **T336:** revoking it revokes everything beneath it, and §7's selectors reach it.
- **T337:** a delegation beneath it attenuates and cannot outlive it, **and is created at all**:
  `_parent_for_creation`'s rule-3 chain scan reads `delegable` over every ancestor including the
  envelope, so without §5.2 point 4's third site a second-level delegation is refused
  `parent_not_valid`. Asserted on both backends, creation and then evaluation.
- **T338:** THE absence test. No flag, environment variable or CLI option anywhere in the tree
  skips a check: the tree is grepped for the names the milestone's plan lists, and the assertion is
  part of the suite rather than a claim in a PR body.
- **T339:** both backends.

### 10.6 Item 6: credential revocation (§6)

- **T340:** THE test. A real verified token with a future `exp`, revoked by a consumed SET, is
  refused at resolution with `IdentityError`, the reason in the log, **and nothing written**: the
  test asserts no event and no receipt, which is the honest shape §6.4 argues for and the opposite
  of what a first draft claimed.
- **T341:** the positive control: an unrevoked credential from the same issuer is admitted **in the
  same run**.
- **T342:** the match is against the token's own `iss`, `sub` and `jti`, not against
  `Principal.agent`: a provider configured with `agent_claim: client_id` still refuses a principal
  the issuer revoked. Without this the feed admits exactly the deployment it was bought for (§6.3).
- **T343:** with no feed configured, 0.7.0 exactly.
- **T344:** a malformed SET, an unknown event type, an unknown subject format, and an event from an
  issuer no provider uses: each consumed, logged, and changing no decision, asserted by a control
  principal that stays admitted.
- **T345:** an event naming a subject the feed cannot map refuses nobody (§6.3).
- **T346:** replay and ordering: the same event consumed twice revokes once; an out-of-order event
  does not un-revoke anything, and the test asserts there is no un-revoke path at all.
- **T347:** staleness past the bound refuses every principal of a covered issuer with
  `revocation_feed_stale`, and leaves an uncovered issuer's principals admitted (§6.5).
- **T348:** staleness with no bound set is 0.7.0's availability, with the feed's last read in the
  log and no refusal.
- **T349:** the feed's transport failing does not raise out of an action: it is a stale feed from
  that moment and §6.5 decides the rest.
- **T350:** the file feed and the poll feed both, with the poll feed's opener asserted to refuse a
  redirect and a non-allow-listed scheme, as the JWKS opener is.
- **T351:** `import ctrlrun` still imports nothing from an extra; `ctrlrun.revocation` raises
  `MissingDependency` with the install command without `ctrlrun[identity]`.
- **T352:** G20 in `verify`, graded under a feed verify supplies, with the note §11.7 requires and
  no `N/A` claiming something about the operator's document.

### 10.7 Item 7: the policy change (§8)

- **T353:** the canonical form: two policies differing only in comments, key order and whitespace
  produce the same `to` hash; a semantic change moves it; and **the same file under a different
  environment, or with a separately loaded authority document, produces a different one**, which is
  what makes an approval per deployment (§8.2). One value is pinned as a literal.
- **T354:** G21. Under `require_approved_policy=True` with no committed `policy:<hash>` effect, an
  action the policy would have allowed is denied with `policy_unapproved` and a `DENIED` receipt.
- **T355:** the positive control: with the hash approved, the same action is decided exactly as
  0.7.0 decided it, compared field by field.
- **T356:** with `require_approved_policy=False`, 0.7.0 exactly.
- **T357:** the whole flow works end to end, which is the test §8.2.1 exists to make possible: a
  policy **declaring** `ctrlrun.policy.change` with `decision: approve` is proposed, approved by an
  entitled principal who is not the proposer, and the next `Control` under that hash decides
  normally.
- **T358:** a policy that does **not** declare the change action, or declares it with any decision
  but `approve`, decides nothing under `require_approved_policy`, and the refusal names the key
  (§8.2.1). This is the test that closes the "write a policy whose change rule is allow" hole.
- **T359:** `ctrlrun.policy.change` proposed through `Control.execute` **without the flow's
  marker** is refused, and the test asserts what the marker is and that the shipped surfaces are
  its only setters. It does not assert that a caller of the private interface is stopped, because
  nothing can (§8.2.1, §2.5.1).
- **T360:** the exemption is exactly one action, matched by name: an action called
  `ctrlrun.policy.changes` or `ctrlrun.policy.change.extra` is refused under an unapproved policy.
- **T361:** the bootstrap: `from: null` on the first proposal, a receipt distinguishable from an
  ordinary approval by that field, and an empty store never read as an approval (§8.4).
- **T362:** the enforcement asks `get_effect` and not `receipts()`: the test counts store calls and
  asserts the answer is a keyed read. **Both transitions**, on one long-lived `Control`: a negative
  answer is re-asked, so an approval landing later takes effect with no restart; and a positive
  answer is cached, so deleting the effect row underneath a running process does **not** stop it,
  which is §8.6's residual asserted rather than assumed. A test that drove only the first
  transition would leave §8.6's sentence unchecked in the direction that overclaims.
- **T363:** the approval path applies: an unverifiable approver, an unentitled one, and the
  proposer approving their own change are each refused, one test per reason (§8.3).
- **T364:** M-of-N applies to a policy change where the policy requires it.
- **T365:** the replay reports which decisions change on a fixture where some change and some do
  not, and prints the ones that do.
- **T366:** the replay writes nothing: the store is byte identical afterwards, receipts, events,
  approvals and effects included.
- **T367:** the replay's output contains no verdict vocabulary, asserted **by word**: safe, unsafe,
  risky, permissive, secure, insecure, score, grade, percentage, pass, fail.
- **T368:** a receipt the replay cannot rebuild is named and skipped, never counted as unchanged
  (§8.5).
- **T369:** a policy that fails to load is a load failure and not an unapproved policy, and the two
  reasons are distinct on the receipt and in the exception.
- **T370:** G21 in `verify`, graded under a flag verify sets, with §11.7's note.

### 10.8 Across the milestone

- **T371:** `ctrlrun.policy/v5` becomes `v6` once; `v1` to `v5` documents still load unchanged, and
  a `v6` key in a `v5` document is refused, naming the key and the required schema.
- **T372:** `ctrlrun.receipt/v4` becomes `v5` once, and a v5 receipt parses with the fields later
  items have not written yet absent or null (§11.4).
- **T373:** `ctrlrun.guarantees/v4` is G1 to G21, with no stub rows: a guarantee whose check does
  not exist yet is absent from the catalogue rather than reporting anything (`v0.7 §9.4`).
- **T374:** every v0.1 to v0.7 acceptance test still passes, and `ctrlrun verify` against the
  shipped examples reports what 0.7.0 reported plus G17 to G21.
- **T374b:** the documents this milestone promised to rewrite say what is then true.
  `docs/SPEC-mcp-operator.md` §4.3 and §10 carry both sentences of §3.9, and no page in this
  repository still says entitlement is unchecked without the qualifier. Asserted by grep over the
  tracked tree, because `docs/SPEC-mcp-operator.md` lives here and the `ctrlrun-docs` audit does
  not reach it.
- **T375:** `ctrlrun verify` does **not** go red because the kernel started checking approvers: its
  own scenarios grant and consume their own approvals, and §2.6's row for them is the one this test
  protects.
- **T376:** the subprocess `sys.modules` assertions still hold: `import ctrlrun` pulls in no
  `httpx`, no `opentelemetry`, no `jwt`, no `psycopg`, and neither `ctrlrun.verify` nor
  `ctrlrun.conformance`.
- **T377:** `pip install ctrlrun` installs `pyyaml` and `click` and nothing else.
- **T378:** `ctrlrun demo` runs every scenario in under 60 seconds **with the network taken away**,
  not assumed away.

## 11. Public API additions (frozen for v0.8)

v0.5 added no table, no column, no event, no error and no CLI command, and said so as evidence its
surface was the right size. v0.7 added six things and said so rather than pretending otherwise.
**v0.8 adds more than v0.7 did, and the table below is the honest count.** Every row is a
specification amendment first: the name and shape are here, what every caller does about it is in
the section it cites, and only then is there code.

### 11.1 The additions, one justification per row

| Addition | Item | Name | Why it clears the bar |
|---|---|---|---|
| Two CLI options | 1 | `ctrlrun revoke --created-by`, `--under` | The incident operation is a query over rows that already exist, and writing it under pressure is how a script revokes the wrong subtree. `--created-by` and not `--by`, because `--by` already means who performed the revocation (§7.2). |
| One configuration object | 2 | `ctrlrun.approval.ApproverIdentity(provider, roles_claim=None)`, and `Control(approver_identity=...)` with a read-only `Control.approver_identity` | Opt in, then fail closed, needs one switch. A provider without a roles claim cannot answer §3, so the two travel together or a deployment has a silent half-check (§2.3). |
| One server flag | 3 | `ctrlrun mcp-operator --approver-roles-claim`, and `OperatorConfig.approver_roles_claim` | The server resolves the approver with its own provider, against an issuer the application need not share. It takes precedence over `ApproverIdentity.roles_claim` and falls back to it; §3.4 says why a flag touching an entitlement decision is named here. |
| One record type | 2 | `ctrlrun.approval.VerifiedApprover(agent, user, issuer, entitled, granted_at)`, read back on `ApprovalRecord.approvers` | `ApprovalRecord` is rebuilt from columns, so a value the consume-side check reads must be one (`v0.6 §3.7`). |
| One amendment to `v0.3 §2.1` | 3 | `ClaimValue` gains `tuple[str, ...]`; `_frozen_claims` accepts a sequence of strings and normalises it to a tuple; `JWTIdentityProvider` carries an array-of-strings claim instead of dropping it | A roles claim is a JSON array at every issuer anybody deploys, and `Principal.claims` refuses containers today, so §3 is unbuildable without this. The normalisation is the half that keeps it readable: JSON has no tuple, so a stored claim returns as a list, and without it every receipt carrying one would raise on read, against `Receipt.from_dict`'s never-raises contract. Safe for hashes: `v0.3 §2.2` keeps claims out of an action's canonical form (§3.4). |
| One record type | 3 | `ctrlrun.approval.RequiredRole(control, role)` | The refusal must name the control (§3.7), so the pair survives to the refusal; a role alone leaves an operator grepping a registry. |
| One field | 3 | `PolicyControl.approver_role: str \| None` | A field on a public dataclass, listed for the reason `v0.6 §9.1.1` had to list `PolicyControl` itself after the fact. |
| One accessor | 4 | `Policy.approvals_required(action_name) -> int` | On the precedent of `Policy.max_attempts`: `Control` needs the threshold and the policy is the only thing that knows it. |
| Two request fields | 3, 4 | `ApprovalRequest.required_roles`, `ApprovalRequest.approvals_required` | The store has no policy, so the roles and the threshold are pinned where `policy_hash` is pinned and for the same reason (§3.3, §4.2). |
| Three columns | 2, 3, 4 | `approvals.approvers` (`COLLATE "C"` on Postgres), `approvals.required_roles`, `approvals.approvals_required`; migration `0006_verified_approver` | One holds what the grant recorded, two hold what the request pinned. Named for what they hold, beside `policy_hash_at_approval` and `precondition_fingerprint`. The collation is not decoration: `approvers` is a compare-and-set column, and a non-deterministic collation makes two distinct blobs compare equal, which fails the unsafe way (§4.3). |
| One widened return | 4 | `ApprovalStore.grant_approval(...) -> Approval \| None` | M-of-N needs "recorded, still short of N", and a widened return is a change every existing implementation already satisfies, where a new method would be a second transition writing one row (§4.4). |
| Two policy keys | 3, 4 | `approver_role` on a control entry; `approvals_required` on an action entry | An operator-set rule where there is none. Named for what they are, and `approvals_required` counts the first approval, as `max_attempts` counts the first attempt. |
| One policy block, and what loading it changes | 5 | `authority.break_glass`, entries with `max_ttl`; `Authority.envelopes`; `canonical_grants` renders envelopes | The envelope must be covered by the policy hash and must never decide an action, and `_candidates` returns every entry of `_grants` unconditionally. A separate mapping is the only shape that gives both without a filter somebody can delete (§5.2). |
| Two lookups in the walk | 5 | `Authority._walk` and `_parent_for_creation` resolve a root from `_grants`, then `envelopes` | A break-glass delegation names an envelope as its parent, and the walk resolves roots from `_grants` alone today (§5.2). |
| One `created_via` value | 5 | the third value beside `api` and `cli` | The vocabulary is a closed `Literal` and an unknown value makes a row unreadable, which makes `Authority.evaluate` answer `authority_unreadable` for every action in the deployment. The `Literal`, the mapping and every reader move together (§5.3). |
| One CLI command | 5 | `ctrlrun break-glass --envelope --file --reason` | Opening one is an act and acts get commands; it reuses `delegate`'s parsing. No `--as`: the opener is the resolved principal, never an assertion (§5.3.1). |
| Two receipt fields | 2, 5 | `Receipt.approvers`, `Receipt.authority_grant_id` | What §2 verified has to reach the evidence. `AuthorityResult.grant_id` reaches the events already and nothing puts it on the receipt (§5.4). |
| One module | 6 | `ctrlrun.revocation`: `RevocationFeed` (with `max_staleness`), `FileRevocationFeed`, `PollingRevocationFeed`; `JWTIdentityProvider(revocations=...)` | Consuming a SET needs JWT verification, which is why it is in `ctrlrun[identity]` beside the provider it serves and not in core. |
| Two operator-server options | 2, 3 | `ctrlrun mcp-operator --approver-roles-claim`, and the server's existing provider used as the approver identity | Without a configuration surface the only verifying surface in §2.6 could not be configured (§2.6). |
| One CLI group | 7 | `ctrlrun policy propose`, `ctrlrun policy replay` | A change is an action, and proposing one is how a human starts it. There is no `policy approve`: that is `ctrlrun approve` (§8.3). |
| One constructor flag | 7 | `Control(require_approved_policy=False)` | In code and not in the file it governs, or the file switches off its own governance (§8.4). |
| One reserved action name | 7 | `ctrlrun.policy.change`, declarable under `v6` and usable by nothing else | A name no document may declare is a name every proposal is denied for; a name anything may propose is a way to mint the receipt that marks a policy approved (§8.2.1). |
| Three schema bumps | 2 to 7 | `ctrlrun.receipt/v5`, `ctrlrun.policy/v6`, `ctrlrun.guarantees/v4` | Fields and keys a reader must be able to see, and a version is how a reader knows to look. |

**Reason strings**, each a value of an existing field and each asserted by name in §10:

| Field | Value | Where |
|---|---|---|
| `ApprovalMismatch.reason`, `APPROVAL_INVALIDATED.data.reason` | `approver_unverified`, `approver_unentitled`, `approver_is_requester` | §2.7, §3.7, §4.1 |
| `ActionDenied.reason`, `ACTION_DENIED.data.reason` | `approvals_unverifiable`, `policy_unapproved` | §4.2, §8.4 |
| Logged at resolution, on no event | `credential_revoked`, `revocation_feed_stale` | §6.4, §6.5 |
| `would_have.blocked_reason` | the mismatch's own reason, where it recorded the constant `approval_mismatch` for every mismatch before (§4.1), plus `policy_unapproved` and `approvals_unverifiable` | §4.1, §8.4 |

**And no other public name.** No new `Control` method, no new `StateStore` method, no new error
type, no new event type, no new approval provider and no new sink.

### 11.2 What is not added, and where it was tempting

- **No `StateStore` method.** M-of-N records through an existing method whose return widens, the
  verified approver arrives by a package-internal context variable, and §8.4's question is answered
  by `get_effect`, which is already on the protocol (§4.4, §2.5, §8.4).
- **No new event type.** A policy change is an ordinary action; a revoked credential writes nothing
  at all; a break-glass grant is a `DELEGATION_CREATED` whose `created_via` says what it was.
- **No new error type.** Four refusals share `ApprovalMismatch` and are told apart by `reason`,
  which is why every test asserts the reason.
- **Two package-internal markers, deliberately not public, each named here and each with its
  residual stated.** `ctrlrun.approval._granting_principal` (§2.5), because a public one would be
  an unauthenticated way to assert a verified approver, which is `trust_approver` spelled as a
  context manager; and `ctrlrun.control._policy_change_in_flight` (§8.2.1), because
  `Control.execute` carries no channel distinguishing one in-process caller from another. Neither
  is exported, and neither is claimed to stop an application that
  calls a private interface: §2.5.1 states that residual once, and §8.2.1 points at it rather than
  claiming a refusal it cannot deliver.
- **No change to the v0.5 adapter contract.** `ApprovalAnswer` keeps its shape (§2.6).
- **No change to `webhook.handle_inbound`'s signature.** `v0.2 §11` freezes it (§1.4 item 3).
- **No `--as` on `ctrlrun break-glass`.** An assertion is exactly what break-glass must not accept
  (§5.3.1).

### 11.3 Schemas

| Schema | Change |
|---|---|
| `ctrlrun.action/v1` | **unchanged** |
| `ctrlrun.policy/v6` | new: `approver_role` on a control entry, `approvals_required` on an action entry, `break_glass` in the authority section. `v1` to `v5` still load, unchanged |
| `ctrlrun.receipt/v5` | new: `approvers`, `authority_grant_id`. A receipt renders under its own schema, and v3, v4 and v5 all read (`v0.7 §6.11`) |
| `ctrlrun.guarantees/v4` | G1 to G21; G17 to G21 added |
| `ctrlrun.approval_request/v1` | **unchanged in name**, carrying two more fields, as it did for `policy_hash` and the fingerprint |
| `ctrlrun.verify/v1`, `ctrlrun.store-conformance/v1`, `ctrlrun.inspection/v2` | unchanged; the reports carry more rows under the same shape |

**A `ctrlrun.receipt/v5` writer and an 0.7 reader do not mix**, for `v0.3 §12.2`'s reason and with
its instruction: upgrade every reader before upgrading any writer. The migration makes the store
half automatic; the JSONL half is the operator's, and item 8 says so in the changelog.

### 11.4 Each version moves exactly once

`v0.7 §9.4`'s D27, applied to all three. The whole shape of `ctrlrun.receipt/v5` is frozen in §11.1
before item 2 starts; item 2 bumps the version and writes `approvers`; item 5 writes
`authority_grant_id` under the version already in place. `ctrlrun.policy/v6` bumps in item 3 and
items 4 and 5 add keys under it. `ctrlrun.guarantees/v4` bumps in item 2 with G18, and G17, G19,
G20 and G21 join with their items. Between items, unreleased `main` carries a partial v4 and a
partial v5; **item 8 asserts every field and every guarantee present before the release PR opens.**
No stub rows: a guarantee that reports anything before its check exists is a false green.

### 11.5 Two items ship without a guarantee id

Items 1 and 5. `ROADMAP.md` assigned G17 to G21 to v0.8 and G22 to G24 to v0.9, in version order,
on 2026-09-10, and both `ctrlrun verify` and the OWASP pages refer to guarantees by id. A sixth id
here would either collide with v0.9 or renumber it, and a renumber is a change the maintainer makes
to the roadmap, not a side effect of a build item. Their evidence is §10's tests, T276 and T338 in
particular.

### 11.6 The six open questions, and where each is decided

Handed to this document open; each is decided here with its argument, and all six are in the PR
body for the maintainer.

| | Question | Decided in |
|---|---|---|
| O1 | How the CLI and the webhook resolve an approver, having no headers | §2.6: **neither can** in the shipped launch path. The verifying surfaces are the operator MCP server and an embedding application; §2.6.1 argues why the CLI is not given a provider, and R1 means the approvals the other surfaces grant are refused at consumption |
| O2 | Where M-of-N's approvers are recorded with `StateStore` frozen | §4.4: a column, written through the existing method, whose return widens to `Approval \| None`; and §4.3 for the compare-and-set that actually serialises it on Postgres |
| O3 | Which SSF delivery shapes ship | §6.2: a file feed and a poll feed; push is delivery work and is not built |
| O4 | What a stale feed does past the bound | §6.5: refuses every principal of a covered issuer, loudly; absent bound means no bound |
| O5 | An approval granted before the roles were configured | §2.9 and §3.3: the roles are pinned at request time, so it carries none; the verified-approver requirement still refuses it, and the changelog says so |
| O6 | The names | §11.1, and §7.2 for the `--by` collision |

### 11.7 Three `N/A` reasons that had to change, and one note

`verify/guarantees.py` states that every `N/A` reason in it is a statement about the operator's
**document**, and `v0.7 §8.9` makes an untrue one a false green. "No approver identity configured",
"no revocation feed configured" and "`require_approved_policy` is false" are statements about a
Python constructor call that `verify` makes **itself**, so none of them may be an `N/A` reason.

G16's `PRECONDITION_NOTE` is the precedent for how this was handled last time, and v0.8 follows it:

- **G17, G18 and G19 are graded, always.** Verify builds its own scenarios, so it configures an
  approver identity for them and grades the refusals. Their `N/A` reasons are about the document:
  G17 is `N/A` where the document names no `approver_role`, G19 where no action names
  `approvals_required` above 1. G18 needs nothing from the document and is therefore never `N/A`,
  which is what G13 established for a guarantee whose subject is the deployment.
- **G20 and G21 are graded with a note, not an `N/A`.** Verify supplies a feed and sets the flag in
  their scenarios, and the note printed beneath the table says that what was graded is the kernel's
  behaviour under a feed and a flag verify configured, and that whether the operator's deployment
  configures either is not something verify can see. A note is honest where an `N/A` would be a
  claim about a document that says nothing on the subject.

## 12. Fail-closed table for v0.8

| Situation | Outcome |
|---|---|
| `ApproverIdentity` configured, row carries no verified approver | `ApprovalMismatch(approver_unverified)`; nothing reserved; approval left granted. True literally, because every refusal in §2 and §4 is raised before `_take` (§2.4.2) |
| Approver provider raises | Refused at the answering surface; never backfilled from the calling code (`v0.3 §3.2`) |
| Approver provider declines | Refused at the answering surface; no approval granted |
| Roles claim absent, or carried by a provider that carries no claims | `approver_unentitled`, with a warning naming the claim and the control |
| Roles claim an int, a bool, or an empty string | `approver_unentitled`; never coerced, never stringified |
| Roles claim an array containing a non-string | The provider does not carry it, so it is absent and unentitled; the drop is logged as a warning where the dropped claim is the one an `ApproverIdentity` names (§3.4) |
| `roles_claim` unset and a role is required | `approver_unentitled` |
| Several controls with roles, one unsatisfied | `approver_unentitled`, naming that control |
| Approver equals requester | `approver_is_requester` |
| A grant that is lapsed by this clock **and** refused on approver grounds | The approver reason, and that row keeps no `APPROVAL_EXPIRED` and no lapse write (§2.4.2) |
| A grant lapsed by this clock whose approver is fine | `expired`, with its event and the store's own lapse write: the check passes and `_take` decides |
| A corrupted `approvers` column | `InvalidArgument` from the store read, named and traceable to the row; a corrupted one on a *receipt* is dropped instead, because a receipt is evidence a reader walks past (§14.2) |
| Fewer than `approvals_required` distinct approvers | Record stays `pending`; consumption refused with `pending` |
| `approvals_required > 1` with no `ApproverIdentity` | Action denied with `approvals_unverifiable`, naming the action and the key (§4.2) |
| Store that does not record several approvers, N > 1 | Never reaches N; never behaves as N = 1 |
| Postgres grant losing a concurrent update | The compare-and-set is on the `approvers` value, so the loser is refused and retries; it never overwrites (§4.3) |
| A partial grant reaching `ApprovalProvider.wait` | Never returned as `None`, which `v0.1 §4.3` defines as a denial; the provider keeps waiting (§4.4) |
| Break-glass grant wider than its envelope, with no expiry, or past `max_ttl` | `AuthorityEscalation` at creation; nothing recorded |
| Break-glass opened with no `ApproverIdentity` configured | Refused, naming the missing configuration (§5.3.1) |
| Break-glass envelope with no grant beneath it | Decides nothing; identical to 0.7.0, by construction (§5.2) |
| Revocation feed reports revoked | `IdentityError` at resolution; no event and no receipt, and every page says so (§6.4) |
| Revocation feed stale past its bound | Every principal of a covered issuer refused with `revocation_feed_stale` |
| Revocation feed unreachable, malformed, or naming an unknown subject | Consumed, logged, no decision changed; stale from that moment if it could not be read |
| `require_approved_policy` and no committed `policy:<hash>` effect | Every evaluation denied with `policy_unapproved`, except `ctrlrun.policy.change`. The negative answer is never cached, so an approval that lands later takes effect without a restart (§8.4) |
| The effect row deleted under a running process that already cached the yes | It keeps deciding until it restarts. Bounded by the process lifetime, recorded as a chain break, and stated in §8.6 as a residual rather than claimed as fail-closed |
| `require_approved_policy` and a policy that does not declare the change action as `approve` | The same refusal, naming the key (§8.2.1) |
| A standalone authority document declaring `break_glass:` | Refused at load, naming the block: its gate lives in a control registry that document cannot see (§5.2) |
| An envelope entry declaring `delegable:` or `expires_at:`, or an id in both `grants:` and `break_glass:` | Refused at load, naming the key or both mappings (§5.2) |
| `ctrlrun.policy.change` proposed without the flow's marker | Refused. The marker is package-internal, so this stops an accident and not an application calling a private interface, which §2.5.1's residual already covers (§8.2.1) |
| Policy fails to load | `PolicyError`, and distinct from `policy_unapproved` |
| Replay cannot rebuild a receipt's action | Named and skipped; never counted as unchanged |
| Selector matches nothing | Non-zero exit, naming what was searched for |
| Selector run interrupted | Rows reached stay revoked; a second run finishes |

## 13. Explicitly out of scope

Each with its reason, from the milestone's do-not-build list for v0.8.

- **An approval UI**, a policy editor, a diff UI, a delegation browser. `ctrlrun receipts`,
  `inspect` and `--json` are the interface, and a management plane is the Pro track's, on its own
  roadmap, never on a kernel version line.
- **Notification delivery.** The webhook posts; each destination stays delivery work, and a library
  that grew connectors would maintain somebody else's API surface forever.
- **Escalation timers beyond expiry.** A second clock that re-asks or re-routes is a workflow
  engine, and what it would add to the evidence is a story about who was asked in what order.
- **Issuing approver credentials**, or anything else: no minting, no OAuth flow, no authorization
  server, no dynamic client registration, no token exchange, no introspection, no published
  revocation list (`v0.3 §1.1`).
- **Unrevoking**, in any costume (`v0.3 §5.7`).
- **A sixth guarantee id** (§11.5).
- **Matching a *grant* on a claim.** §3 matches an *approver's* role on a claim, which is a
  different question on a different principal at a different moment; `v0.3 §5.4`'s exclusion for
  grants is unchanged, and `v0.3 §4.2`'s reason for it (agent and user survive rotation, a claim
  may not) is exactly why a grant still matches on agent and user.
- **Consequence budgets, scope providers and task-bound authority**: v0.9.
- **A2A and authority propagation across hops**: v0.10.
- **An emergency-stop command.** v0.8 strengthens the argument rather than weakening it:
  break-glass is a grant, revocation reaches everything a principal issued, and a policy change is
  an approved action. A fourth way to stop something is a fourth thing to keep correct.
- **Signed receipts**, which bring key generation, rotation and revocation, which is issuing.
- **A reorganisation of `control.py`.** Not this milestone, and not as a side effect of one.
- **Any compliance, conformance, certification or alignment claim**, including "SSF-compatible"
  (§6.7).

---

## 14. What building v0.8 settled

*One subsection per question the drafting could not close, each stating what the code decided and
which section carries it. `SPEC-v0.4.md` §12, `SPEC-v0.5.md` §12, `SPEC-v0.6.md` §12 and
`SPEC-v0.7.md` §12 are the format.*

**This section is empty on purpose, and the items fill it.** v0.5's item 6 could tell which parts
of that document had been stress-tested by somebody other than their author by looking for a §12
entry behind them, and all four of its most serious findings sat in sections that had none. The
arguments are written down as they are decided, not afterwards.

### 14.1 Item 1: revocation by selector

**A killed run can leave a revoked row with no event, and that is v0.3 behaviour rather than
anything the selector adds.** `Control.revoke` writes the row and then appends the event: two
writes, no transaction over the pair, and `StateStore` is frozen (`v0.6 §9.2`). A `SIGKILL`
between them leaves the delegation revoked, correctly and durably, with no `DELEGATION_REVOKED`
behind it. A selector run does not create the window; it makes it easy to land in, because it
takes the same two writes two hundred times instead of once. T276 asserts the bound it can
assert, that **at most one** row is missing its event, and the first draft of that test asserted
equality, which is stricter than the code has ever promised. Closing it needs the row and the
event in one store call, which is a store method, which is the maintainer's call and not an
item's.

**Two guards were green under mutation, and both were subsumed rather than absent.** The
mutation table caught them, which is what it is for.

- Removing the refusal of `--created-by a/b/c` left T278 green, because `a/b/c` then parses as
  the agent `a` and the user `b/c`, matches nothing, and exits non-zero through §7.5's
  empty-selector error instead. The test asserted an exit code where it had to assert a message.
- Removing the skip over already-revoked rows left T275 green, because `Control.revoke` is
  idempotent and appends no second event whatever the loop does. What the skip is actually for
  is the terminal: without it a rerun prints `revoked <id>` for two hundred rows it did not
  revoke. The test now asserts that line's absence, which is the only thing that makes the skip
  load-bearing.

Both are `CONTRIBUTING.md`'s first shape of a false green, and both would have read as covered.

**`--under` is strictly beneath.** A row is not under itself, so `--under <a delegation id>`
revokes that delegation's descendants and leaves the delegation alone. The alternative reading
costs nothing to implement and is worse to use: an operator who wants the row as well already has
`ctrlrun revoke <id>`, and one who wanted only the subtree would have no way back.

**Polling the store to trigger the kill made T276 pass for the wrong reason.** Opening a
`StateStore` per poll costs more than the two hundred revocations it is watching, so the kill
landed after the child had finished and the test asserted its window had been opened when it had
not. It polls the JSONL event file instead, which is a read of a file the command is already
appending to.

**No new `StateStore` method, and none was tempting.** `delegations(include_revoked=True)` and
`revoke_delegation` already exist, the filter is twenty lines above the store, and the subtree
walk is bounded by the rows it has already seen because a cycle is reachable with `sqlite3` and a
text editor, which is the point `v0.3 §5.5` makes about evaluation.

### 14.2 Item 2: the approver is a principal

**The check is at the consumption because `Control` never grants**, which §1.4 recorded and which
building it confirmed: the only code that calls `grant_approval` outside a test is the CLI, the
operator server, `handle_inbound`, the scripted provider, the adapters and verify's own scenarios.
`Control._withdraw` calls `deny_approval`, which is the kernel closing a request it created rather
than a human answering one, and §2.6's table gives it its own row. None of them is `Control`
deciding anything.

**The gate took three attempts and §2.4.2 records all three**, because the next reader will reach
for one of the two that were wrong. Skipping the lapsed row is fail-open under clock skew: a
self-approval committed, reproduced by the review. Deferring it past `_take` closes that and
strands a reservation the kernel cannot release, which lapses into an `AMBIGUOUS` record a human
must resolve for an action the kernel itself refused; `fail_effect` requires `EXECUTING`, so there
is no `RESERVED → FAILED` to clean it up with, and `mark_ambiguous` would assert "unknown" about an
action known not to have run. Checking it before `_take` like every other row costs one thing, that
a grant which is both lapsed and approver-refused keeps no `APPROVAL_EXPIRED` and no lapse write,
and buys the sentence the other two answers could not say: **nothing is written by a refusal**.

**Two of those three were found by review rather than by the suite**, and the second was a defect
introduced by the fix for the first, which is the shape `CONTRIBUTING.md` asks a second review pass
for. The tests that exist now are the ones that would have caught them: T291c reproduces the skew
**and asserts the grant is still granted and nothing reserved**, which the first version of that
test did not, so it could not have told a refusal before `_take` from one after.

**The observe-mode vocabulary change had a consumer nobody had looked at.** `would_have.
blocked_reason` is a closed set because `ctrlrun stats` buckets counts on it, and `receipt.py` says
so in as many words: *a bucketed count over a string nobody constrained is a report that quietly
stops adding up*. Recording each mismatch's own reason made exactly that true, measured: an
observe-mode approval refusal landed in no bucket, `would_have_been_blocked` went from 1 to 0, and
the command whose whole job is "what changes if you turn enforcement on" under-reported it. The set
grew with the change, T296b asserts the count, and the receipt that stopped counting was a plain
hash mismatch with nothing to do with v0.8.

**The early return was exactly as dangerous as §2.4 said.** M6 restores it, every approver test in
the file goes green, and only T291 fails: a check placed after that return is dead on the path
every 0.6-shaped deployment takes, and nothing else in the suite notices.

**One test file covering one store is one store covered.** The first draft of `test_approver.py`
used the in-memory store alone, and the mutation table caught it: blanking the verified approver in
the **SQLite** write path left every test green, because none of them had ever executed that path.
The fixture now runs every test on in-memory, SQLite and Postgres, which is `v0.6 §2`'s argument
for the store conformance suite applied to a test file, and M7a and M7b are two rows rather than
one.

**G18's title is 28 characters because the report table is 32 wide**, which v0.7 had to discover
for G12 as well. It is "the requester cannot approve" and not "self-approval is refused", because
what is compared is the resolved principal on each side and "self" invites the reading that two
different approver strings are two different people, which is the reading §4.1 exists to refuse.

**What the two version bumps moved in the suite, listed rather than absorbed.** Twelve test files
outside this item's own changed, and every edit in them was a count, a name or a key set that was
true of 0.7.0 and is not now: the receipt's exact JSON key set, which `v5` widens by two; the
verify counts, because G18 is graded wherever a document sends an action to approval, so the
shipped examples move from 14/14 to 15/15 and from 8/8 to 9/9 and the catalogue pins move from `v3`
to `v4`; the last migration, pinned by name and now asserted as `HEAD`; and the receipt schema
label this binary writes. **The verify counts are also pinned in `.github/workflows/ci.yml`**,
which would have turned the `verify` job red on a branch whose suite was entirely green, and which
nothing in the local gate would have caught.

**`_granting_principal` stayed package-internal and the operator server is its first caller.** That
server has resolved a principal for every request since it shipped and then discarded it into
`mcp-operator:<user>`; item 2 is, on that surface, four lines that stop discarding it.

**And the bucket this item widened was already missing a human's no.** `check_consumable` catches a
denied record one branch before the generic status branch, so the reason recorded for it is
`approval_denied` and not `denied`: the set as first written carried a value nothing can produce
and missed the one that is, and an observe-mode run where a human refused was counted nowhere.
That predates v0.8. It is fixed here because this is the commit that writes the set and argues at
length for closing exactly this, and shipping it wrong would have made the argument false on its
own terms.

**Observe mode had to be implemented, not asserted.** §4.1's row said observe mode records
`approver_is_requester`, and `_observe_take` never called `_recheck`, so it recorded nothing: the
section described something that did not exist, and T296 presented no approval at all, which made
it a negative test against behaviour its own setup prevented. Both are fixed: the check runs on
that path, `_observe_secure` records the mismatch's own reason where it recorded one constant for
every mismatch, and T296 asserts the reason and the consequence. The vocabulary change is the one
§4.1 argued for, and it reaches refusals that have nothing to do with v0.8, which is why it is in
§11.1's table and in the changelog rather than left to a reader to notice.

**A resumed leg's receipt is the only receipt some actions ever get.** `_resumed_context`
recovers the precondition fingerprints from the record and did not recover the approvers, so
§2.5's "carried onto the receipt" was false for exactly the MCP multi round-trip and ACS actions
that get one receipt (`SPEC-mcp-operator.md` §8.3). One line, and the review found it.

**A corrupted `approvers` column is a `CTRLRunError` and a corrupted one on a receipt is not.**
The store read raises, named, because an approval is authority about to be spent and one bad row
must stop this action; `Receipt._approvers_of` drops a malformed entry instead, because a receipt
is evidence a reader walks past and one tampered row must not blind every reader at once
(`v0.7 §6.11`). The two rules look inconsistent and are the same rule applied to different
questions.

### 14.3 Item 3: entitlement from the control registry

**The tests were written after the implementation, and that is worth recording rather than
hiding.** Every other item in this milestone wrote a red suite first, as `CONTRIBUTING.md` asks;
this one did not, and all forty-eight passed on their first run. What carried the weight instead
was the mutation table, and it found within minutes what the ordering had cost: **case-folding the
*string* claim branch left every test green**, because T303 parametrised only the list shape and
nothing exercised the other branch. Both shapes are covered now, with a positive control, and the
lesson is the one the discipline exists for: a test written against code that already works tests
the code you wrote rather than the rule you meant.

**One mutation is an equivalent mutant, and it is declared rather than claimed.** Recording
`required` instead of the computed `entitled` at the grant is indistinguishable, because the
refusal fires first whenever any role is missing, so past that point `entitled` provably equals the
full required set. `CONTRIBUTING.md` says to say so in the table rather than dress it up as closed.

**`RequiredRole` lives in `approval.py` and `Control` builds the pairs.** The obvious home for the
lookup was `Policy.required_roles(controls)`, and it was written and then reverted: `policy.py`
imports `action` and `effect`, `approval.py` imports `action`, `errors` and `identity`, and neither
imports the other. Putting the accessor on `Policy` meant a new `policy` to `approval` edge for one
dataclass, and §11.1 freezes no such accessor. `Control` already holds both the evaluation and the
registry, which is where the join belongs.

**The entitlement refusal reaches its event by the carrier, not by a new keyword.** `§3.7` requires
`APPROVAL_INVALIDATED` to name the control and the role, and the first attempt added a `detail`
keyword to `ApprovalMismatch`: a public name §11 does not list, on the closed error set `v0.6 §9.2`
freezes. The precondition hashes already travel to that event on `_Compared`, so the pair travels
the same way.

**The grant-side refusal needed a policy of its own to be testable.** `test_mcp_operator.py`'s
document cites no control with a role, so the first version of that test asserted a 403 it could
never get: the server had nothing to refuse. The two §3.8 tests carry their own gated document, and
the request is created through `Control.execute` rather than through the provider, because
`_required_roles` is set around **that** call and a request built straight from the provider pins
nothing. A test that skipped the pinning would have asserted a refusal no deployment reaches.

**What G17 moved.** It is `N/A` on both shipped examples, because neither names an `approver_role`,
which is a true statement about those documents (§3.5). Nine tests and two pins in
`.github/workflows/ci.yml` moved by one as a result, which is the same class of change G18 forced
in item 2 and the second time that CI file has been the thing no local gate would catch.

### 14.4 Item 4: M-of-N

**"Does not count" cannot mean "is not counted", for two of the three.** §4.2's first draft said
the requester's own yes, an unentitled yes and an unverifiable yes were each *refused before the
count moved*. Building it showed that reading destroys G18. At `approvals_required: 1`, excluding
the requester's yes from the count leaves the record `pending` for ever: the consumption check
never reaches `approver_is_requester`, because there is nothing granted to check, and the refusal a
reader gets is `pending` — "nobody has answered yet" — about a request the requester answered. G18
is shipped, `verify` grades it, and it says a self-approval is *refused as one*.

So the store counts every verified approver, and all three refusals live at consumption where the
kernel can name which one fired. One rule, in one place, and the store stays a store rather than
acquiring a second opinion about who may answer. §4.2 now carries the table.

**The same argument, run the other way, is why an unverifiable yes *is* excluded from the count.**
There is nothing to refuse it by: a row with no verified approver records nothing that could be
compared against a requester or a role, and at N=1 it must still grant, because that is 0.7.0
unchanged and R1 promises a deployment naming no approver identity is untouched.

**The read-back had to grow, and an independent review found that it had not.** `v0.7 §6.4` reads
the stored request back after the provider returns, because a provider that builds its own
`ApprovalRequest` and a store that drops a column both produce a row missing what the kernel
pinned. Items 3 and 4 pin two more fields by exactly the same route and added no check, so both
were lost in both ways with nothing refused: a row pinning `required_roles=()` satisfies every
control trivially, and one pinning `approvals_required=1` grants on a single yes. An action ran
under a policy demanding two approvals from a named role, approved once, by somebody holding no
role. `§4.5`'s sentence that no such path exists was false when it was written; the read-back now
covers all three fields and `approval_unrecorded` is its refusal.

**The count's window is not the same thing as two processes answering at once.** Item 4's serial
tests all pass against a store with no compare-and-set whatever, which the mutation table caught by
being caught for the wrong reason: removing Postgres's condition turned a *sequential* test red.
T313 opens the real window, between the read of `approvers` and the update of it, and fails against
the compare-and-set on `status` that the store had. The store conformance case learned the same
lesson one layer down: it accepted a `processes` argument and never used it.

### 14.5 Item 5: break-glass as a grant

**`delegable` is read at three sites and they are not interchangeable.** §5.2 point 4 named all
three before the code was written, and building it confirmed each has a different failure: without
the creation-root site nothing is created; without the rule-3 chain scan nothing can be delegated
beneath a break-glass grant; without rule 6 the grant is created, looks right in every record, and
authorises nothing on every evaluation, refused `authority_escalation` with no dimension named.
Only the third is silent, which is why T326b asserts **evaluation** and not creation.

**The exemption is applied where the value is read, never written onto the grant.** A
`delegable=True` on the parsed envelope would have been three lines shorter and would have moved
the policy hash, because `_canonical_grant`'s closed field list always emits `delegable`. The hash
has to be a statement about the document: T332 pins that an envelope renders `delegable: false`,
the parser default, and that a deployment where break-glass evaluates correctly hashes identically
to one where the runtime rule is absent.

**A break-glass grant is not delegable unless it says so.** Writing item 5's tests found this and
it is not a defect: `delegable` on the grant in `--file` means what it means everywhere, and §5.2
point 4 exempts the **envelope**, which carries no such key, and changes nothing about the grant
opened beneath it. An operator who wants to sub-delegate during an incident writes `delegable: true`
in that file, and `max_ttl` still bounds the whole subtree in time.

**`resources:` is contained only where the parent constrains it**, which is `v0.3 §5.4` unchanged
and worth stating because an envelope is where it surprises: an envelope declaring no `resources:`
does not bound them, so an envelope intended to bound resources must say so. Omission is not
unlimited *for the child* — a child may not drop a dimension its parent constrains — and it is
also not a constraint the parent never expressed.

**Open, and the maintainer's call: `ctrlrun break-glass` cannot succeed in any configuration
the CLI can load.** `Control.from_file` wires no `ApproverIdentity` -- there is no configuration
key for one, and §2.6.1 rules out giving the CLI a provider of its own -- so the command §11.1 adds
for this item always exits 1 saying an approver identity is needed, which is advice the CLI cannot
act on. It fails closed, and the gated path is the only path: `delegate --parent <an envelope>` is
refused by name. But it means the shell example in §5.3 does not run today, and break-glass is
reachable only from an embedding application that built its own `Control`.

Three ways out, none of them a build item's to choose: a configuration key naming an approver
identity provider, which is surface §11.1 does not list; a credential option on this one command,
which is the same surface with a narrower blast radius; or striking the shell example and saying
the command serves deployments that configure one in code. **Asked rather than decided**, on the
rule that an item which disagrees with its specification stops.

**The absence test had to read code rather than text.** `approval.py` explains in a comment that a
public `_granting_principal` would be "`trust_approver` spelled as a context manager", which is
prose arguing the flag away. A grep that cannot tell that from a flag pushes the argument out of
the tree, so T338 tokenizes comments and strings out, and its control plants a flag and finds it.

### 14.6 Item 6: credential revocation, consumed

### 14.7 Item 7: a policy change is a protected action

### 14.8 Item 8: the release
