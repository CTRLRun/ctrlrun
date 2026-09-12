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

**It is not a new entry point.** `v0.3 §4.3.1`'s table grows two columns (§9) and no rows.

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
approver. A `Control` built with no `approver_identity` behaves exactly as 0.7.0 did, and G17 to
G19 report `N/A` with that reason. A `Control` built with one gets no partial mode: an approval
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

Five things, each of which moved a decision this document was handed.

1. **`Control` never grants an approval.** Every grant goes through `ApprovalStore.grant_approval`,
   called by a surface outside `Control`: the CLI, the operator server, `handle_inbound`, or a
   provider. So the check that *matters* cannot live at the grant. It lives at the consumption,
   where `Control` already reads the record (`_recheck`'s `get_approval`) and already raises before
   `_take`. §2.4 is built on that seam, and every grant-side check is defence in depth with its own
   test, because defence in depth hides mutations (`CONTRIBUTING.md`).
2. **There is already a route for per-request data that the provider protocol cannot carry.**
   `approval.policy_in_force` and `approval._precondition_at_request` are context variables
   `Control` sets around `self._approvals.request(...)`, read by `build_request`. `v0.6 §7.1` and
   `v0.7 §6.2` both used it rather than widening a frozen protocol. §3.3 and §4.4 use it for the
   third and fourth time, and §2.5 uses the same shape in the other direction, for the grant.
3. **`webhook.handle_inbound` takes no headers.** Its signature is
   `(store, path_request_id, body, signature, *, secret, replay_window)`; its HMAC authenticates
   the sending *system* and its `approver` is a string in the signed body. So the webhook is a
   surface that cannot produce a verified approver without a signature change, and `v0.2 §11`
   freezes that name. §2.6 records it as such rather than assuming headers it does not have.
4. **The operator MCP server has done half of item 2 already.** It builds a header or JWT identity
   provider, resolves a principal for every request, and refuses `--principal` because a static
   provider "answers with one name for every request, so every approval it produced would carry an
   identical approver". Then it throws the principal away into the string `mcp-operator:<user>`.
   §2.6's work there is to stop throwing it away.
5. **`ctrlrun revoke --by` is already taken and means the opposite thing.** It names who performed
   the revocation, defaulting to `CLI_APPROVER`. The roadmap's `--by <principal>` names whose
   delegations to revoke. §7.2 resolves the collision by naming the new selector `--created-by`,
   and the old option keeps its meaning.

---

## 2. The approver is a principal

### 2.1 What is wrong today

`Approval.approver` is a `str` whose only check is non-emptiness. It is written by
`grant_approval(approval_id, approver)` and read back onto the receipt. `adapter.py`'s
`ApprovalAnswer` docstring concedes what that string often is: it "names a **channel** wherever the
framework's primitive does not identify a human". The CLI writes `cli:local`. The scripted provider
writes `cli:scripted`. The ACS hook writes `cli:local`. The operator server writes
`mcp-operator:<user>`, which is the only one of the five with a verified human behind it, and even
there the verification is discarded at the boundary.

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

`Control._recheck` already reads the record it is about to present, applies `check_consumable`, and
raises before the store call that consumes the approval (`v0.7 §6.2`). The approver check joins it,
in this order, and the order is normative:

1. `get_approval(approval_id)`, the read that already happens.
2. `check_consumable`, unchanged (`v0.1 §4.2`).
3. **The approver checks of §2.7, §3.6, §4.1 and §4.2**, in that order, each raising
   `ApprovalMismatch` with its own reason.
4. The precondition comparison of `v0.7 §6.2`, unchanged, which is the last thing before `_take`
   because it is the only one that makes a network call and the window it narrows is measured from
   its own fetch.
5. `_take`.

**Why the approver checks go before the precondition fetch.** A refused approval must not cost a
provider call (`v0.7 §6.6` argues the same for a refused verdict), and an approver who was never
entitled does not become entitled because a balance is unchanged.

**Nothing is written by a refusal.** Not the approval, which stays granted, on `v0.6 §7.2`'s
precedent: the action is refused, the human's yes is not spent on a question it did not answer, and
the approval still expires. Not the effect, because nothing is reserved. The refusal produces an
`APPROVAL_INVALIDATED` event and a `BLOCKED` receipt, exactly as a precondition refusal does.

### 2.5 How a verified approver reaches the row, without a new store method

`ApprovalStore.grant_approval(approval_id, approver)` is part of the frozen protocol and cannot
grow a parameter. The route is the one `v0.6 §7.1` and `v0.7 §6.2` already use in the other
direction, a context variable set around the call:

```python
# ctrlrun.approval
@contextmanager
def granting_principal(principal: Principal | None, *, entitled: Sequence[str] = ()) -> Iterator[None]: ...
```

A surface that has resolved an approver wraps its `grant_approval` or `deny_approval` call in it.
The shipped stores read it inside those methods and write the row. A third-party store that ignores
it records nothing, and §2.7's consume-side check then refuses every approval it grants, which is
the fail-closed direction and is the whole reason the check is at consumption.

**What is recorded**, as `VerifiedApprover`, canonical JSON in one column:

```python
@dataclass(frozen=True)
class VerifiedApprover:
    agent: str
    user: str | None
    issuer: str | None
    entitled: tuple[str, ...]     # the control ids this approver satisfied (§3)
    granted_at: datetime
```

**No claim value is ever stored.** `v0.3 §2.4`'s rule: evidence carries claim *names* where values
are withheld, and here it carries neither, because what an entitlement decision means is *which
control this approver satisfied*, which is exactly what `entitled` says. A row that stored the role
value would put an identity provider's payload in an evidence table for no gain.

### 2.6 Every surface that can answer, and what each one does

`grant_approval` and `deny_approval` are reachable from six places. This table is normative and
`R3` is why it exists.

| Surface | Has a credential? | With an `ApproverIdentity` configured |
|---|---|---|
| `gateway/operator.py`'s `approve` / `deny` tools | Yes: HTTP headers, already resolved per request | Resolves through `ApproverIdentity.provider` with the request's headers, records a `VerifiedApprover`, and refuses the call where the provider declines or raises. It already refuses `--principal`, so a static approver identity cannot reach it. |
| `ctrlrun approve` / `ctrlrun deny` | No headers | Resolves with an empty header map (§2.8). A provider that can answer without headers answers; `HeaderIdentityProvider` and `JWTIdentityProvider` decline, and the command **refuses, exits non-zero, and names the reason**. It never grants an unverified approval and never falls back to `cli:local`. |
| `webhook.handle_inbound` | No: an HMAC over the body, and `approver` as a string in it | Cannot produce a verified approver. `v0.2 §11` freezes the signature and v0.8 does not reopen it. The handler records the string as it always did, and the consume-side check refuses the approval. §2.9 says what an operator does about that. |
| `ScriptedApprovalProvider` | No | Cannot. It is a test and demo surface and §2.9's note applies to it. |
| `LocalApprovalProvider` | It does not grant; it polls | Unaffected. |
| `adapter.py`'s `InterruptApprovalProvider` (`ApprovalAnswer`) | No | Cannot. The v0.5 contract is frozen (`v0.5 §9`) and an answer arriving from a framework interrupt carries no credential. Adding a field to `ApprovalAnswer` was rejected: the adapters are separately versioned distributions and a frozen contract is not reopened for one deployment shape. |

**The honest summary, which every page describing this feature must carry:** with an approver
identity configured, the surface that produces verified approvals is the operator MCP server, and
the CLI where the operator's provider can answer without HTTP headers. Every other surface can
still *ask*, and the approvals it grants are refused when they are presented.

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
  - id: card-data-handling
    title: Cardholder data changes are approved by a named owner
    source: PCI DSS 7.2.1
    approver_role: payments-owner        # new in ctrlrun.policy/v6
```

`approver_role` is a single opaque string. **CTRLRun does not interpret it**, exactly as `v0.6 §7.3`
says it does not interpret `source:`: it does not know what `payments-owner` means, does not check
that such a role exists anywhere, and makes no compliance claim on the strength of one.

**What changed about `v0.6 §7.3`'s "attribution, not prevention", stated precisely because it is
the sentence a reviewer will check.** A control still does not decide an *action*: citing one
causes no approval, and `Evaluation.controls` still does not participate in the decision. What a
control now decides is **who may answer an approval the decision already required**. Those are
different questions, and the second one is the first thing a control has ever decided. Every page
that carries the old sentence gains the second half in the same edit (item 8).

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

The approver's roles are read from `ApproverIdentity.roles_claim` on the resolved principal's
claims. The rules, and each is its own test:

- The claim is absent: the principal holds no roles. **Not entitled** wherever any role is
  required. This is the answer `v0.3 §5.4` said it did not have when it left claim matching out of
  grants, and it is the fail-closed one.
- The claim is a string: one role, matched exactly, byte for byte. No case folding, no trimming, no
  prefix matching, no pattern grammar. `v0.3 §4.4`'s pattern grammar is for actions and resources
  and is not extended here; a role is an identifier and a wildcard in one would be an entitlement
  nobody wrote.
- The claim is a list of strings: that set of roles, each matched exactly.
- The claim is anything else, or a list containing anything else, or an empty string: **refused**,
  never coerced, with `approver_unentitled` and a log line naming the claim. `v0.3`'s handling of
  `agent_claim` is the precedent: "a missing, empty, or non-string `agent_claim` is an
  `IdentityError`, never coerced".
- `ApproverIdentity.roles_claim` is `None`: no role can be read, so an approver satisfies no role,
  so any required role refuses. A deployment that names roles in its policy and no claim to read
  them from has configured half a check, and half a check fails closed.

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
type: mutation pattern 1 names this section by name.

### 3.8 Where entitlement is decided twice, on purpose

The grant surface (§2.6, rows one and two) computes `entitled` from the request's `required_roles`
and refuses on the spot where the approver satisfies none of them, so a human learns at the moment
they answer rather than at the moment an agent retries. The consumption check of §3.6 is the
**guarantee**; the grant-side refusal is a courtesy.

Defence in depth hides mutations (`CONTRIBUTING.md`): §10 gives each its own test, and the
consume-side test presents a row whose `entitled` was written by a store that did not check, which
is the case the grant-side refusal cannot reach.

---

## 4. Requester is not approver, and M-of-N

### 4.1 Self-approval, on the resolved principal

**G18.** With an `ApproverIdentity` configured, an approval is refused where a verified approver's
`(agent, user)` equals the requesting principal's `(agent, user)`.

**On agent and user, and nothing else.** `v0.3 §4.2` gives the reason: those two are what stay
stable under the token rotation `§2.2` describes, and they are what `Subject` matching already
addresses. Not the issuer, which is a property of the credential; not a claim, which §3 already
governs.

**Never on the string.** Two grants whose `approver` strings differ and whose resolved principals
are the same are one principal, and §10's test for G18 is exactly that case. A check on the string
would be defeated by typing a different word.

**Where only one side resolves.** The requester's principal is on the action (`Action.principal`,
always present: `v0.3` refuses an action with no principal once authority is loaded). The
approver's is on the row, or the approval was already refused by §2.7. So there is no half-resolved
case here; there is one in §2.7 and it refuses.

**Observe mode.** `v0.3 §6` records what enforce mode would have done. A self-approval in observe
mode is recorded on `would_have.blocked_reason` as `approver_is_requester` and the action runs, as
every other observe-mode refusal does.

### 4.2 M-of-N

```yaml
actions:
  payments.refund:
    decision: approve
    approvals_required: 2        # new in ctrlrun.policy/v6
```

Integer, at least 1. `0`, negatives, `true`, `1.0` and `"2"` are refused at load, naming the key
and the line, exactly as `v0.7 §5.3` refuses a malformed `max_attempts`: a malformed threshold
fails the policy and never the action. Absent means 1, which is 0.7.0.

**The threshold is pinned on the request**, `ApprovalRequest.approvals_required`, by the same route
as §3.3 and for the same reason. This is also what lets the store enforce it: the store has no
policy, and a store that had to ask one what N is would be a store that loads policy files.

**Distinct means distinct resolved principals**, `(agent, user)`, as §4.1 compares them. A second
grant from the same principal is **not an error and not a duplicate row**: it updates that
approver's entry (its `granted_at` moves) and the count does not. "Counted once" is the requirement
and rejecting the second answer would make a human think their answer was lost.

**What does not count**, each refused before the count moves, and each with its own test asserting
the count did not move:

- the requester's own yes (§4.1);
- an approver who satisfies none of the required roles (§3.8);
- an approver the provider could not verify (§2.7).

**A denial denies the request, whole.** One `deny_approval` moves the record to `denied` however
many grants it holds. A request that absorbs a no while it waits for enough yeses is a request that
asked the wrong question, and the fail-closed reading is the one this repository takes when a spec
is ambiguous (`CONTRIBUTING.md`).

**Expiry is the request's.** Grants collected before the expiry do not extend it. An expiring
request with 2 of 3 expires, and `check_consumable` refuses it with `expired` as it does today.

### 4.3 Where the count is decided

**In the store's write, never in a read followed by a write.** This is `v0.7 §5.5`'s rule for the
attempt ceiling, applied here for the same reason: two callers who both read "1 of 2" both get
through, which is attribution and not prevention.

- SQLite: `grant_approval` already opens `BEGIN IMMEDIATE` before reading the record, which
  serialises the read and the write. The append, the distinctness test and the status transition
  all happen inside it.
- Postgres: the same three inside one transaction, with the row locked by the update's own
  `WHERE approval_id = ? AND status = 'pending'` and a row count checked, on the pattern
  `v0.7 §5.6` established for the stale renewal.
- In-memory: under the existing lock.

The store conformance suite (`v0.6 §2`) gains a case: a store that reports it records several
approvers is driven concurrently and must not let one principal fill two slots. A store that
reports it does not is `not_applicable`, and §4.5 says what such a store does at consumption.

### 4.4 What the record holds, without a new store method

`ApprovalStore.grant_approval` returns `Approval`, which cannot describe "recorded, still short of
N". Its return type widens:

```python
def grant_approval(self, approval_id: str, approver: str) -> Approval | None: ...
```

`None` means *recorded and still pending*. This is a **public API change**, listed in §11.1, and
not a new method: `v0.6 §9.2` freezes the protocol's shape and this widens a return, which every
existing implementation already satisfies (an implementation that only ever grants at N=1 returns
an `Approval` every time, as it does today). Every shipped caller is updated to handle `None`, and
§10 asserts each one does something useful with it rather than crashing: the CLI prints how many
more are needed, the operator server returns that in its tool result, and the webhook answers 200
with the same fact.

Rejected: a new method (`grant_approval_partial`), because it is the maintainer's call and because
two methods that write the same row are two transitions to keep correct. Rejected: a sentinel
`Approval` with a falsy field, because a caller that forgot to check it would have a usable-looking
grant object.

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
  grants: [...]
  break_glass:
    - id: incident-payments
      subject: {agent: "oncall-*"}
      actions: ["payments.*"]
      environments: ["prod"]
      constraints: {amount_lte: 50000}
      max_ttl: PT4H
```

An envelope is a `Grant` in every respect the parser already knows, plus `max_ttl`, and with one
difference that is the whole point: **an envelope never decides an action.** It is loaded into
`Authority` as a parent-only grant, excluded from the candidate set `Authority.evaluate` walks, and
present only so a delegation may name it as a parent. A deployment with an envelope and no
break-glass grant beneath it behaves exactly as one with no envelope at all, and §10 asserts that
by comparing an evaluation against 0.7.0's.

**Why the envelope is in the policy.** It is covered by the policy hash, so the widest authority an
incident can reach was evidenced *before* the incident, by a document somebody reviewed, rather
than by a command somebody typed at 3am. Rejected: an unbounded runtime grant, which is a flag with
a record attached.

### 5.3 Opening it

```
ctrlrun break-glass --envelope incident-payments --file grant.yaml --reason "INC-4412" --as oncall/ada
```

The grant in `--file` is an ordinary one-grant document, as `ctrlrun delegate --file` takes. What
happens is `Control._delegate` with the envelope as parent, so:

- **containment is checked by the code that already checks it**, `contained_dimension`, on every
  dimension including subject, actions, resources, environments and constraints. A grant wider than
  the envelope on any dimension is refused at creation with `AuthorityEscalation`, and §10 drives
  one refusal per dimension;
- **an expiry is required**, and one longer than the envelope's `max_ttl` is refused. This is the
  one rule §5 adds to `v0.3 §5`: an ordinary delegation may carry no expiry, and a break-glass
  grant that outlives the incident is the thing this section exists to prevent;
- **the creating principal is resolved and entitled**: the same `ApproverIdentity` (§2) resolves
  who is opening it, and where the envelope cites a control with a role, that role is required.
  Break-glass anyone can open is the flag again;
- `created_via` records what it was, so the evidence distinguishes it from an ordinary delegation;
- `--reason` is a free-text string recorded on the `DELEGATION_CREATED` event. The kernel does not
  interpret it, exactly as it does not interpret `source:`.

### 5.4 What it looks like afterwards

- **Every action taken under it names the grant on its receipt.** `ctrlrun.receipt/v5` gains
  `authority_grant_id`, the id of the grant that decided the action, **for every action decided by
  authority and not only for break-glass**. `AuthorityResult.grant_id` already carries it and
  nothing recorded it. A field that existed only under break-glass would be a field nothing
  exercises on the ordinary path, and an operator asking "what did this grant let through" would
  have to join events by hand.
- **It expires**, and after its expiry the action it covered is denied on the next proposal, by
  `Authority.evaluate`'s existing expiry check.
- **It is revocable**, by id today and by §7's selectors, and revoking it revokes everything
  beneath it, transitively, by the mechanism `v0.3 §5.7` already describes.
- **It attenuates**: a delegation beneath a break-glass grant obeys `child ⊆ parent` on every
  dimension and cannot outlive it, because that is what containment and expiry already do.

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
introspects."* `THREAT_MODEL.md` says the same. v0.8 deletes the first sentence and keeps the
second half of the third: nothing *subscribes*, because a subscription needs an endpoint this
project serves, and serving one is not consuming.

### 6.2 The shape

```python
# ctrlrun.revocation: ctrlrun[identity], lazy, never imported by `import ctrlrun`
class RevocationFeed(Protocol):
    def revoked(self, *, issuer: str, subject: str, token_id: str | None) -> bool: ...
    @property
    def read_at(self) -> datetime | None: ...
    @property
    def issuers(self) -> frozenset[str]: ...
```

`JWTIdentityProvider(..., revocations: RevocationFeed | None = None)`. Absent means 0.7.0, exactly
as R1 requires.

Two feeds ship (O3, decided here):

- **`FileRevocationFeed(path, ...)`**: a file of Security Event Tokens, one per line, that the
  operator's own transmitter writes. Re-read when its mtime moves.
- **`PollingRevocationFeed(url, ...)`**: RFC 8936 poll delivery, over stdlib `urllib` through the
  same hardened opener `jwt_identity.py` already uses for JWKS (no redirects, an allow-listed
  scheme, a bounded body).

**Push (RFC 8935) is not built.** It needs an HTTP endpoint this project serves and a session to
serve it on, which is delivery work, and delivery work is on the do-not-build list beside
notification delivery. An operator with a push transmitter writes received SETs to the file feed,
which is four lines of their code and none of ours.

### 6.3 What is consumed

A SET whose `events` claim carries a CAEP event type this feed knows, naming a subject in one of
the RFC 9493 formats the feed knows (`iss_sub`, and `opaque` matched against the principal's
subject claim). Everything else is **consumed without changing any decision** and logged: an
unknown event type, an unknown subject format, an issuer no configured provider uses, a malformed
token. A revocation feed that guessed at a subject it did not recognise would refuse a principal it
does not name, and §6.6 is why that direction matters.

**The SET's own signature is verified** where the feed is given a key source, through the key
handling `jwt_identity.py` already has. Where it is not, the feed's trust is the file's, and §6.6
states what that means.

### 6.4 What is refused

A principal whose `(issuer, subject)` or `jti` the feed reports revoked is refused at resolution,
**before** authority and before policy, on the path `v0.3 §2.3` already uses for an expired
credential: `IdentityError`, an `ACTION_DENIED` event with reason `credential_revoked`, and a
`DENIED` receipt. Not a new path, not a new event type, not a new error.

**G20** grades exactly this: a credential with a future `exp`, revoked, refused, with a positive
control in the same run where an unrevoked credential from the same issuer is admitted. A feed that
refuses everything is not a feed.

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

A policy has a canonical form (`canonical_bytes` over the loaded document, folded with authority by
`hash_with_authority`, which is what `policy_hash` already is). So a change has an action:

| | |
|---|---|
| name | `ctrlrun.policy.change` (reserved: a document that declares an action of this name fails to load) |
| arguments | `{"from": <policy hash or null>, "to": <policy hash>}` |
| resource | `policy` |
| effect key | `policy:<to>` |
| principal | whoever proposes, resolved as any other principal is |

It is an ordinary `Action`: ordinary action hash, ordinary effect key, ordinary events, ordinary
receipt. **That is why §8 adds no event type.** `v0.1 §6.2`'s vocabulary already describes a
proposal, an approval request, a grant, a consumption and a commit, which is the whole life of a
policy change.

**Stable across formatting.** The hash is over the canonical form and not the text, so comments,
key order and whitespace do not move it, and two hosts computing it agree byte for byte. §10 pins
one value as a literal so a change to the derivation is a red test and not a silent one.

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

- **Where it is false**, nothing changes, and G21 reports `N/A` with that reason.
- **Where it is true**, the first decision this `Control` makes asks the store whether a committed
  receipt exists for `ctrlrun.policy.change` with `to == self._policy_hash`, and caches the answer.
  Where there is none, **every evaluation is a denial** with reason `policy_unapproved`, recorded
  as `ACTION_DENIED` and a `DENIED` receipt, and raised as `ActionDenied`. That is what "decides
  nothing" means.
- **Lazily, not in the constructor**, because a constructor that queried the store would make
  building a `Control` a database call, and `@protect` builds one per process at import time.
- **The requirement lives in code, not in the policy file.** A policy that could switch off its own
  approval requirement would be switched off in the same edit that removes everything else. §8.6
  states what that does and does not buy.
- **`ctrlrun.policy.change` itself is exempt**, and it is the only exemption. Without it the first
  change under a fresh deployment could never be approved, because the proposal would be denied by
  the very rule it exists to satisfy. The exemption is narrow: the action still passes principal
  expiry, authority, policy and the approval gate, and it is refused like anything else where those
  refuse.

**The bootstrap is recorded as a bootstrap.** Where no policy hash has ever been approved, the
first `ctrlrun policy propose` records `from: null`, and the receipt says so. An empty ledger is
never read as an approval of whatever is on disk: there is no path on which "nothing recorded"
means "approved". §10 asserts that a bootstrap receipt is distinguishable from an ordinary approval
by its `from`, and that a deployment with `require_approved_policy=True` and an empty store refuses
every action that is not the policy change.

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

- **An administrator with write access to the policy file** proposes under a policy they wrote,
  including one whose controls require no role. What they cannot manufacture is the *approving
  principal*: the approver's credential is verified by the provider configured in code, and §4.1
  refuses their own. So the property is **"a policy change no verified principal other than the
  proposer approved decides nothing"**, and not "a policy cannot be changed by whoever holds the
  file".
- **An administrator with write access to the store** can delete the receipts that record the
  approval, at which point every action is refused: that is a denial of service, fail closed, and
  the receipt chain records the deletion as a break (`v0.6 §6`).
- **An administrator with write access to the code** switches `require_approved_policy` off.
  `THREAT_MODEL.md`'s malicious-administrator line is unchanged and names this.
- **A persuaded approver** approves a policy change as they would approve anything else (§1.1).

---

## 9. The `v0.3 §4.3.1` columns

Two columns, no rows, and the rows whose answer is "no" carry their reason, because the rule that
puts every entry point in one table exists for a hole that was a missing enumeration and not a
missing check (`v0.3 §4.3.1`).

| Entry point | Checks the approver at consumption (§2.4) | Refuses under an unapproved policy (§8.4) |
|---|---|---|
| `Control.execute` | **Yes**, for every `APPROVE` decision, before `_take` | **Yes** |
| `Control.evaluate` | No: it consumes no approval and writes nothing | **Yes**, because an evaluation is a decision and this is the milestone that says an unapproved policy makes none |
| `Control.resume` | No: the approval was consumed on the suspended leg and the remote may already be acting. Refusing here strands a reservation, which is `v0.6 §7.2.3`'s reason for not rechecking preconditions there either | No, same reason: the continuation exists because a remote is holding an exchange |
| `Control.delegate` | No: it consumes no approval | **Yes**: it creates authority, which is the one thing an unapproved policy must not be able to widen |
| `Control.revoke` | No: it consumes no approval | **No, deliberately.** Revocation only ever narrows authority, and an incident is exactly when the policy may be unapproved. A kernel that refused to revoke because its policy was unapproved would fail closed into being unable to close anything |
| The MCP gateway | Through `Control.execute`: **yes** | Through `Control.execute`: **yes** |
| The ACS hook | Through `Control.execute`: **yes** | **Yes** |
| Both adapters | Through `Control.execute`: **yes**. `needs_approval` reads and decides nothing, so it neither checks nor refuses | **Yes** for the executing path; `needs_approval` reports what the policy says, including that it is unapproved |
| `gateway/operator.py`'s write tools | It **grants**, it does not consume: it resolves and records a `VerifiedApprover` (§2.6) | No: it writes approvals and resolves effects, and neither is an evaluation |
| `ctrlrun approve` / `deny` | Grants, as above (§2.6) | No: they load no policy today and §8 does not make them start |
| `ctrlrun receipts` / `inspect` / `effects` / `stats` | No | **No**: they load no policy at all, and making an evidence command load one would turn a malformed or unapproved policy into a failure of the evidence. `cli/main.py` already states this rule for `_loaded_policy` |

---

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
  the approval is refused with `approver_is_requester`. The case where the strings differ is the
  one that proves the check is not cosmetic.
- **T286:** G18's positive control: a different principal approves, and the action runs.
- **T287:** a provider that raises is never backfilled from anything the calling code said
  (`v0.3 §3.2` applied to this door), and a provider that declines is a refusal here rather than a
  fallback, because there is no context to fall back to.
- **T288:** every surface in §2.6's table, one test each, asserting which half of the table it is
  in: the operator server resolves and records; `ctrlrun approve` refuses with a provider that
  needs headers and succeeds with one that does not; `handle_inbound`, the scripted provider and
  `ApprovalAnswer` record a string, and the approvals they grant are refused at consumption.
- **T289:** `ApproverIdentity` with a `StaticIdentityProvider` warns once, and the warning names
  the provider type. It does not refuse (§2.3).
- **T290:** the `IdentityContext` an approval resolution receives carries the **stored request's**
  action and environment and `agent=None, user=None` (§2.8). Asserted by a recording provider.
- **T291:** the migration, both directions, on SQLite and Postgres, from a database built by
  0.7.0's own code and not a hand-written fixture: rows with no approver columns open, migrate and
  keep every value; an 0.7.0 binary against the migrated database refuses and names both versions.
- **T292:** a v3, v4 and v5 receipt chain verifies end to end, each receipt hashed by the rule its
  own version wrote (`v0.7 §6.11`).
- **T293:** `ctrlrun.guarantees/v3` becomes `v4` here, and `verify` reports the catalogue version
  and G18 with its positive control and its `N/A` reason.
- **T294:** the upgrade case of §2.9: an approval granted at 0.7.0, still pending, presented after
  an `ApproverIdentity` is configured, is refused with `approver_unverified`.
- **T295:** observe mode records `approver_is_requester` on `would_have.blocked_reason` and runs
  the action (§4.1).

### 10.3 Item 3: entitlement (§3)

- **T296:** an approver who does not hold the required role is refused with `approver_unentitled`,
  and the refusal **names the control id and the role**, asserted by value in the message, the
  exception and `APPROVAL_INVALIDATED.data`.
- **T297:** the positive control: an approver who holds it approves, and the action runs.
- **T298:** omission A. A principal whose claims lack the role claim entirely is not entitled
  (§3.4).
- **T299:** omission B. A control naming no `approver_role` gates nothing, and an approval citing
  only such controls runs with any verified approver (§3.5). T298 and T299 together are the pair
  R2 exists for, and neither may be deleted without the other failing.
- **T300:** a claim that is a number, a list containing a number, an empty string, or a dict is
  refused and never coerced, one case each.
- **T301:** a claim that is a list of strings entitles for each of them, matched byte for byte:
  `payments-owner ` with a trailing space does not satisfy `payments-owner`.
- **T302:** `roles_claim=None` with a policy that requires a role refuses (§3.4's last bullet).
- **T303:** several cited controls: the approver holds one role and not the other, and is refused.
  The test would pass under an any-of reading if the assertion were only "the approval ran", so it
  asserts the refusal and the control named (§3.6).
- **T304:** the roles are pinned at request time: the policy's roles change between the request and
  the grant, and the roles in force at the request are the ones applied (§3.3, `v0.6 §7.1`).
- **T305:** the grant-side refusal and the consume-side refusal are separate defences with separate
  tests: the consume-side test presents a row whose `entitled` a store wrote without checking
  (§3.8), which the grant-side refusal cannot reach.
- **T306:** policy load refuses a malformed `approver_role` (empty, a list, a number), naming the
  key and the line, and a document using it without `schema: ctrlrun.policy/v6`.
- **T307:** G17 in `verify`, with its positive control and an `N/A` reason that is true of a
  document naming no approver role.

### 10.4 Item 4: M-of-N (§4.2)

- **T308:** N distinct verified principals grant; the approval is consumable only after the Nth.
  A consume attempted at N-1 is refused with `pending`, nothing is reserved and the executor was
  not called.
- **T309:** G19. Two grants from the same resolved principal with different approver strings count
  **once**: the count does not move, the second grant is not an error, and the record's
  `granted_at` for that approver moves.
- **T310:** the concurrency case, deterministic. Separate OS processes against Postgres, with the
  window between the count's read and its write opened on purpose; N grants never become N+1, and
  one principal never fills two slots. Two processes "granting at the same time" without the window
  opened is not this test.
- **T311:** the requester's own yes does not count, and the count is asserted unmoved (§4.2).
- **T312:** an unentitled yes does not count, and the count is asserted unmoved.
- **T313:** an unverifiable yes does not count, and the count is asserted unmoved.
- **T314:** one denial denies a request holding N-1 grants.
- **T315:** expiry: a request with N-1 grants expires as a whole, and the grants do not extend it.
- **T316:** `approvals_required: 1` is 0.7.0 exactly, driven through the whole path and compared.
- **T317:** policy load refuses `0`, `-1`, `true`, `1.0` and `"2"`, naming the key and the line.
- **T318:** `grant_approval` returns `None` at N-1 and an `Approval` at N, and every shipped caller
  does something useful with `None`: the CLI says how many more are needed, the operator server
  returns it, `handle_inbound` answers 200 with it (§4.4).
- **T319:** a store that records one approver only never reaches N and never behaves as N=1, and
  its conformance case reports `not_applicable` with that reason (§4.5).
- **T320:** G19 in `verify`, with its positive control and its `N/A` reason; and the store
  conformance case of §4.3 on all three shipped stores.

### 10.5 Item 5: break-glass (§5)

- **T321:** a break-glass grant is created beneath its envelope, recorded, and the action it covers
  is allowed while it lives.
- **T322:** after its expiry the same action is denied on the next proposal, by the existing expiry
  check and with the existing reason.
- **T323:** a grant with no expiry is refused at creation; one longer than the envelope's `max_ttl`
  is refused (§5.3).
- **T324:** containment, one refusal per dimension: subject, actions, resources, environments and
  each constraint operand. Driven through `contained_dimension`, so a new dimension added later
  cannot silently escape this test.
- **T325:** the envelope alone decides nothing: a deployment with an envelope and no grant beneath
  it evaluates exactly as 0.7.0, compared decision by decision (§5.2).
- **T326:** the receipt of an action decided under it carries `authority_grant_id`, and so does the
  receipt of an action decided by an ordinary grant: the field is not break-glass-specific (§5.4).
- **T327:** creating one without a resolved, entitled principal is refused (§5.3), one test per
  refusal.
- **T328:** revoking it revokes everything beneath it, and §7's selectors reach it.
- **T329:** a delegation beneath it attenuates and cannot outlive it.
- **T330:** THE absence test. No flag, environment variable or CLI option anywhere in the tree
  skips a check: the tree is grepped for the names the milestone's plan lists, and the assertion
  is part of the suite rather than a claim in a PR body, because a claim about the environment
  becomes a test of it.
- **T331:** both backends.

### 10.6 Item 6: credential revocation (§6)

- **T332:** THE test. A real verified token with a future `exp`, revoked by a consumed SET, is
  refused with `credential_revoked`, before authority and before policy, with a `DENIED` receipt.
- **T333:** the positive control: an unrevoked credential from the same issuer is admitted **in the
  same run**.
- **T334:** with no feed configured, 0.7.0 exactly, and G20 `N/A` with its reason.
- **T335:** a malformed SET, an unknown event type, an unknown subject format, and an event from an
  issuer no provider uses: each consumed, logged, and changing no decision. Asserted by a control
  principal that stays admitted.
- **T336:** an event naming a subject the feed cannot map refuses nobody (§6.3).
- **T337:** replay and ordering: the same event consumed twice revokes once; an out-of-order event
  does not un-revoke anything. There is no un-revoke path at all, and the test asserts its absence.
- **T338:** staleness past the bound refuses every principal of a covered issuer with
  `revocation_feed_stale`, and leaves an uncovered issuer's principals admitted (§6.5).
- **T339:** staleness with no bound set is 0.7.0's availability, with the feed's last read in the
  log and no refusal.
- **T340:** the feed's transport failing does not raise out of an action: it is a stale feed from
  that moment and §6.5 decides the rest.
- **T341:** the file feed and the poll feed both, with the poll feed's opener asserted to refuse a
  redirect and a non-allow-listed scheme, as `jwt_identity.py`'s JWKS opener is.
- **T342:** `import ctrlrun` still imports nothing from an extra, `ctrlrun.revocation` is absent
  without `ctrlrun[identity]` and raises `MissingDependency` with the install command.
- **T343:** G20 in `verify`, with its positive control and its `N/A` reason.

### 10.7 Item 7: the policy change (§8)

- **T344:** the canonical form: two policies differing only in comments, key order and whitespace
  produce the same `to` hash; a semantic change moves it. One value is pinned as a literal.
- **T345:** G21. Under `require_approved_policy=True` with no approval recorded, an action the
  policy would have allowed is denied with `policy_unapproved`, with a `DENIED` receipt.
- **T346:** the positive control: with the hash approved, the same action is decided exactly as
  0.7.0 decided it, compared field by field.
- **T347:** with `require_approved_policy=False`, 0.7.0 exactly, and G21 `N/A` with that reason.
- **T348:** the bootstrap: `from: null` on the first proposal, a receipt distinguishable from an
  ordinary approval by that field, and an empty store never read as an approval (§8.4).
- **T349:** the exemption is exactly one action: `ctrlrun.policy.change` is evaluable under an
  unapproved policy and every other action, including one whose name merely starts with
  `ctrlrun.policy`, is not.
- **T350:** a document declaring an action named `ctrlrun.policy.change` fails to load, naming the
  reserved name and the line.
- **T351:** the approval path applies: an unverifiable approver, an unentitled one, and the
  proposer approving their own change are each refused, one test per reason (§8.3).
- **T352:** M-of-N applies to a policy change where the policy requires it.
- **T353:** the replay reports which decisions change on a fixture where some change and some do
  not, and prints the ones that do.
- **T354:** the replay writes nothing: the store is byte identical afterwards, receipts, events,
  approvals and effects included.
- **T355:** the replay's output contains no verdict vocabulary. Asserted **by word**: safe,
  unsafe, risky, permissive, secure, insecure, score, grade, percentage, pass, fail.
- **T356:** a receipt the replay cannot rebuild is named and skipped, never counted as unchanged
  (§8.5).
- **T357:** a policy that fails to load is a load failure and not an unapproved policy, and the two
  reasons are distinct on the receipt and in the exception.

### 10.8 Across the milestone

- **T358:** `ctrlrun.policy/v5` becomes `v6` once; `v1` to `v5` documents still load unchanged, and
  a `v6` key in a `v5` document is refused, naming the key and the required schema.
- **T359:** `ctrlrun.receipt/v4` becomes `v5` once, and every field §11.1 freezes is written by
  something before the release (item 8 asserts this; this test asserts the shape is parseable with
  the later items' fields absent).
- **T360:** `ctrlrun.guarantees/v4` is G1 to G21, with no stub rows: a guarantee whose check does
  not exist yet is absent from the catalogue rather than reporting anything (`v0.7 §9.4`'s D27).
- **T361:** every v0.1 to v0.7 acceptance test still passes, and `ctrlrun verify` against the
  shipped examples reports what 0.7.0 reported plus G17 to G21.
- **T362:** the subprocess `sys.modules` assertions still hold: `import ctrlrun` pulls in no
  `httpx`, no `opentelemetry`, no `jwt`, no `psycopg`, and neither `ctrlrun.verify` nor
  `ctrlrun.conformance`.
- **T363:** `pip install ctrlrun` installs `pyyaml` and `click` and nothing else.
- **T364:** `ctrlrun demo` runs every scenario in under 60 seconds **with the network taken away**,
  not assumed away.

---

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
| One configuration object | 2 | `ctrlrun.approval.ApproverIdentity(provider, roles_claim=None)`, and `Control(approver_identity=...)` | Opt in, then fail closed, needs one switch. A provider without a roles claim cannot answer §3, so the two travel together or a deployment has a silent half-check (§2.3). |
| One context manager | 2 | `ctrlrun.approval.granting_principal(principal, *, entitled=())` | `grant_approval` is frozen and cannot grow a parameter; this is the route `policy_in_force` and the precondition fingerprint already take, for the third time (§2.5). |
| One record type | 2 | `ctrlrun.approval.VerifiedApprover(agent, user, issuer, entitled, granted_at)` | `ApprovalRecord` is rebuilt from columns, so a value the consume-side check reads back must be one; and the shape is what a receipt renders (§2.5). |
| Three columns | 2, 3, 4 | `approvals.approvers`, `approvals.required_roles`, `approvals.approvals_required`; migration `0006_verified_approver` | One holds what the grant recorded, two hold what the request pinned. Named for what they hold, beside `policy_hash_at_approval` and `precondition_fingerprint`. |
| Two request fields | 3, 4 | `ApprovalRequest.required_roles: tuple[RequiredRole, ...]`, `ApprovalRequest.approvals_required: int` | The store has no policy, so the threshold and the roles must be pinned where `policy_hash` is pinned and for the same reason (§3.3, §4.2). |
| One record type | 3 | `ctrlrun.approval.RequiredRole(control, role)` | The refusal must name the control (§3.7), so the pair has to survive to the refusal; a role alone would leave an operator grepping a registry. |
| One widened return | 4 | `ApprovalStore.grant_approval(...) -> Approval \| None` | M-of-N needs "recorded, still short of N", and a widened return is a public API change every existing implementation already satisfies, where a new method would be a second transition writing one row (§4.4). |
| Two policy keys | 3, 4 | `approver_role` on a control entry; `approvals_required` on an action entry | An operator-set rule where there is none. Named for what they are: a role an approver holds, and how many approvals are required, counting the first. |
| One policy block | 5 | `authority.break_glass`, entries with `max_ttl` | The widest authority an incident can reach, covered by the policy hash before the incident (§5.2). |
| One CLI command | 5 | `ctrlrun break-glass --envelope --file --reason --as` | Opening one is an act and acts get commands; it reuses `delegate`'s parsing exactly. |
| One receipt field | 5 | `Receipt.authority_grant_id` | `AuthorityResult.grant_id` exists and nothing recorded it. For every grant, not only break-glass (§5.4). |
| One receipt field | 2 | `Receipt.approvers` | What §2 verified has to reach the evidence, or the milestone records nothing. |
| One module | 6 | `ctrlrun.revocation`: `RevocationFeed`, `FileRevocationFeed`, `PollingRevocationFeed`; `JWTIdentityProvider(revocations=...)` | Consuming a SET needs JWT verification, which is why it is in `ctrlrun[identity]` beside the provider it serves and not in core. |
| One CLI group | 7 | `ctrlrun policy propose`, `ctrlrun policy replay` | A change is an action, and proposing one is how a human starts it. There is no `policy approve`: that is `ctrlrun approve` (§8.3). |
| One constructor flag | 7 | `Control(require_approved_policy=False)` | In code and not in the file it governs, or the file switches off its own governance (§8.4). |
| One reserved action name | 7 | `ctrlrun.policy.change` | A document that could declare it could impersonate a policy change. Reserved at load (T350). |
| Three schema bumps | 2 to 7 | `ctrlrun.receipt/v5`, `ctrlrun.policy/v6`, `ctrlrun.guarantees/v4` | Fields and keys a reader must be able to see, and a version is how a reader knows to look. |

**Reason strings**, each a value of an existing field and each asserted by name in §10:

| Field | Value | Where |
|---|---|---|
| `ApprovalMismatch.reason`, `APPROVAL_INVALIDATED.data.reason` | `approver_unverified`, `approver_unentitled`, `approver_is_requester` | §2.7, §3.7, §4.1 |
| `ActionDenied.reason`, `ACTION_DENIED.data.reason` | `credential_revoked`, `revocation_feed_stale`, `policy_unapproved` | §6.4, §6.5, §8.4 |
| `would_have.blocked_reason` | `approver_is_requester`, `policy_unapproved` | §4.1, §8.4 |

**And no other public name.** No new `Control` method, no new `StateStore` method, no new error
type, no new event type, no new approval provider and no new sink.

### 11.2 What is not added, and where it was tempting

- **No `StateStore` method.** M-of-N records through an existing method whose return widens, and
  the verified approver arrives by context variable (§2.5, §4.4). `v0.6 §9.2` holds.
- **No new event type.** A policy change is an ordinary action; a revoked credential is an
  `ACTION_DENIED`; a break-glass grant is a `DELEGATION_CREATED` whose `created_via` says what it
  was.
- **No new error type.** Four refusals share `ApprovalMismatch` and are told apart by `reason`,
  which is why every test asserts the reason.
- **No change to the v0.5 adapter contract.** `ApprovalAnswer` keeps its shape (§2.6).
- **No change to `webhook.handle_inbound`'s signature.** `v0.2 §11` freezes it (§1.4 item 3).

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
to the roadmap, not a side effect of a build item. Their evidence is §10's tests, T276 and T330 in
particular.

### 11.6 The six open questions, and where each is decided

Handed to this document open; each is decided here with its argument, and all six are in the PR
body for the maintainer.

| | Question | Decided in |
|---|---|---|
| O1 | How the CLI and the webhook resolve an approver, having no headers | §2.6, §2.8: the CLI resolves with an empty header map and refuses where the provider declines; the webhook cannot, and `v0.2 §11` is why its signature does not change |
| O2 | Where M-of-N's approvers are recorded with `StateStore` frozen | §4.4: a column, written through the existing method, whose return widens to `Approval \| None` |
| O3 | Which SSF delivery shapes ship | §6.2: a file feed and a poll feed; push is delivery work and is not built |
| O4 | What a stale feed does past the bound | §6.5: refuses every principal of a covered issuer, loudly; absent bound means no bound |
| O5 | An approval granted before the roles were configured | §2.9 and §3.3: the roles are pinned at request time, so it carries none; the verified-approver requirement still refuses it, and the changelog says so |
| O6 | The names | §11.1, and §7.2 for the `--by` collision |

---

## 12. Fail-closed table for v0.8

| Situation | Outcome |
|---|---|
| `ApproverIdentity` configured, row carries no verified approver | `ApprovalMismatch(approver_unverified)`; nothing reserved; approval left granted |
| Approver provider raises | Refused; never backfilled from the calling code (`v0.3 §3.2`) |
| Approver provider declines | Refused at the answering surface; no approval granted |
| Roles claim absent, empty, or not a string or list of strings | `approver_unentitled`; never coerced |
| `roles_claim` unset and a role is required | `approver_unentitled` |
| Several controls with roles, one unsatisfied | `approver_unentitled`, naming that control |
| Approver equals requester | `approver_is_requester` |
| Fewer than `approvals_required` distinct approvers | Record stays `pending`; consumption refused with `pending` |
| Store that does not record several approvers, N > 1 | Never reaches N; never behaves as N = 1 |
| Break-glass grant wider than its envelope, or with no expiry, or past `max_ttl` | `AuthorityEscalation` at creation; nothing recorded |
| Break-glass envelope with no grant beneath it | Decides nothing; identical to 0.7.0 |
| Revocation feed reports revoked | `IdentityError`, `credential_revoked`, before authority and policy |
| Revocation feed stale past its bound | Every principal of a covered issuer refused with `revocation_feed_stale` |
| Revocation feed unreachable, malformed, or naming an unknown subject | Consumed, logged, no decision changed; stale from that moment if it could not be read |
| `require_approved_policy` and no approval for the loaded hash | Every evaluation denied with `policy_unapproved`, except `ctrlrun.policy.change` |
| Policy fails to load | `PolicyError`, and distinct from `policy_unapproved` |
| Replay cannot rebuild a receipt's action | Named and skipped; never counted as unchanged |
| Selector matches nothing | Non-zero exit, naming what was searched for |
| Selector run interrupted | Rows reached stay revoked; a second run finishes |

---

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

### 14.2 Item 2: the approver is a principal

### 14.3 Item 3: entitlement from the control registry

### 14.4 Item 4: M-of-N

### 14.5 Item 5: break-glass as a grant

### 14.6 Item 6: credential revocation, consumed

### 14.7 Item 7: a policy change is a protected action

### 14.8 Item 8: the release
