# CTRLRun

[![CTRLRun verified](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/CTRLRun/ctrlrun/badges/verify-badge.json)](https://github.com/CTRLRun/ctrlrun/blob/main/docs/verify.md#what-the-badge-means)

**Transaction safety for AI-agent actions.**

> Agents can retry. Your refund shouldn't.

CTRLRun is open-source infrastructure for controlling consequential AI-agent actions.

You decide, per action, what an agent can do autonomously, what requires human approval, and what is blocked.

CTRLRun binds approvals to the exact action, blocks duplicate execution attempts for the same logical effect, stops blind retries when an execution outcome is uncertain, and records what actually happened.

**Autonomy belongs to the action, not the agent.**

---

## The problem

Agents are getting write access to the real world: refunds, emails, deploys, permission grants, record changes. Frameworks already let you approve or deny a tool call. That is not the hard part.

The hard part is what happens at the boundary between *intention* and *effect*:

- A refund commits at Stripe, the response times out, the agent retries. **Did you just refund twice?**
- A human approves a €500 refund. The agent changes it to €5,000 before executing. **Should that approval still count?**
- Two agents pick up the same task and issue the same refund. **Which one wins?**
- A call timed out. Your framework marks it *failed* and retries. **It wasn't failed. It was unknown.**

CTRLRun owns that boundary.

## Quick start

```bash
pip install ctrlrun
ctrlrun demo
```

`ctrlrun demo` runs the five failure scenarios below in well under a second, with no external services.

Protect your first action:

```python
import ctrlrun


@ctrlrun.protect("stripe.refund", effect="refund:{payment_id}")
def refund(payment_id: str, amount: int, currency: str = "EUR"):
    return stripe.refunds.create(payment_intent=payment_id, amount=amount)
```

Configure autonomy per action in `ctrlrun.yaml`:

```yaml
schema: ctrlrun.policy/v1

actions:
  customer.read:
    decision: allow

  stripe.refund:
    rules:
      - when: { amount_gte: 0, amount_lte: 50000 }      # up to €500.00
        decision: allow
      - when: { amount_gte: 0, amount_lte: 500000 }     # up to €5,000.00
        decision: approve
      - decision: deny
```

Amounts are integer minor units — cents, not euros. Floats are rejected outright, because `0.1` and `0.10` are the same money and different hashes.

Now the same agent can refund €100 on its own, must get a human to approve €2,000, and cannot refund €20,000 at all. Neither can it refund a negative amount, which is a charge wearing a refund's name — an upper bound alone is not a range. Unknown actions are denied. CTRLRun fails closed.

## Protect an existing MCP server

No agent changes. Point the client at the gateway instead of at the tool server:

```bash
pip install "ctrlrun[gateway]"
ctrlrun gateway --upstream http://localhost:8000/mcp --alias acme --principal refund-agent
```

The gateway prints, on the line that starts it, every action in your policy that has no
`effect:` template — because a write with no effect key is exactly the configuration this
exists to prevent, and it should not be discovered in a receipt three weeks later:

```text
1 action(s) have no effect: template and get no reservation:
  mcp.acme.list_payments
That is right for a read, and wrong for anything that changes the world.
```

Tools become actions named `mcp.<alias>.<tool>`, decided by the same `ctrlrun.yaml`. Declare
their effect and resource templates there, since a tool call has no decorator to carry them:

```yaml
schema: ctrlrun.policy/v2

actions:
  mcp.acme.create_refund:
    effect: "refund:{payment_id}"
    resource: "payment:{payment_id}"
    decision: approve
```

Everything but `tools/call` is relayed untouched. A lost response over the wire blocks the
retry exactly as it does in-process — that is the whole point of putting it here.

## Who is acting, and what are they entitled to?

A policy decides how much autonomy an *action* has. It cannot see who is asking — deliberately,
since v0.1. That answers "may a €50,000 refund run without a human" and not "may *this agent*
propose a €50,000 refund at all", and a system that can only ask the first will eventually
answer the second by accident.

`authority:` is the second axis. It is **opt-in, and then fail-closed**: a policy with no
`authority:` section behaves exactly as it did before, and the moment one exists every
principal needs a grant and no grant means denied.

```yaml
schema: ctrlrun.policy/v3

authority:
  grants:
    - id: head-of-support
      subject: { agent: "head-of-support", user: "dana@example.com" }
      actions: ["stripe.refund"]
      constraints: { amount_lte: 10000000 }   # €100,000.00
      delegable: true
      expires_at: "2027-01-01T00:00:00Z"
```

A grant carries no `decision:`. How much autonomy `stripe.refund` has is the same for
everybody; what differs is whether they may ask. The two axes are evaluated separately,
authority first, and combine as the **stricter of the two** — so neither can loosen the other.

A principal holding a `delegable` grant can narrow it at runtime with `ctrlrun delegate`, and
a delegated grant is valid only if it is **provably a subset of its parent on every dimension,
at creation and again at every evaluation**. A check performed only at creation would leave
every delegation exactly as wide as the file used to be, which is the shape of every
stale-permission incident there has ever been. **Omitting a dimension the parent constrains is
rejected, not inherited** — a child that drops `resources:` would authorize resources its
parent never could. `ctrlrun revoke` cuts a chain of any depth with one write.

**Roll it out with `mode: observe` first.** One top-level line runs every real decision against
real traffic and records what *would* have been blocked, without blocking anything — then
`ctrlrun stats` gives you the numbers before you enforce them. It is not a dry run: it
executes, and effects land at remotes. It is a way to learn what enforcement will cost.

**Identity is consumed, never invented.** `pip install "ctrlrun[identity]"` adds a
`JWTIdentityProvider` that verifies a bearer token against a JWKS or a pinned key and maps the
verified claims onto a principal. CTRLRun issues no credential and defines no identity format.

## Does it hold in *your* setup?

Everything above is proven by this repository's tests against this repository's configurations.
That is the right place to start and the wrong place to stop, because what you deploy is *your*
policy, *your* grants and *your* store.

```console
$ ctrlrun verify
CTRLRun verify — ctrlrun 0.4.0, catalogue ctrlrun.guarantees/v1
policy     examples/authority/payments.yaml (ctrlrun.policy/v3, mode: enforce)
authority  same document, 3 grants
store      sqlite, scratch (created and destroyed for this run)

G1   mutated approval refused         PASS  stripe.refund
G2   replayed approval refused        PASS  stripe.refund
G3   duplicate effect refused         PASS  stripe.refund
G4   one winner under concurrency     PASS  stripe.refund (8 processes)
G5   ambiguous blocks a blind retry   PASS  stripe.refund
G6   unknown action refused           PASS
G7   no principal refused             PASS  stripe.refund
G8   expired authority refused        PASS  head-of-support
G9   delegation cannot escalate       PASS  head-of-support (6 of 6 dimensions)
G10  unknown exception is ambiguous   PASS  stripe.refund

10/10 declared guarantees pass. 0 not applicable.
```

It runs the kernel's own failure scenarios against the configuration in front of it, in a
scratch store, with fake executors, and no network. Your `.ctrlrun/state.db` is byte-identical
before and after.

**Not applicable is not a pass.** A policy with no `approve` rule cannot exercise the
approval-binding guarantees, so they are reported `N/A` with the reason, excluded from the
denominator and listed separately — `5/5 (5 not applicable)`, never `10/10`. There is no flag
that folds one into the count.

The badge means the **declared guarantees pass** — every guarantee this configuration can
exercise was exercised, and none of them failed. It does not mean secure, safe, compliant,
certified or audited, and [`docs/verify.md`](https://github.com/CTRLRun/ctrlrun/blob/main/docs/verify.md#what-the-badge-means)
says on the same screen what verify cannot see: your executors, your `reconcile` hooks, where
you put the decorator, your deployment, and whether your policy is the right policy.

There is a [GitHub Action](https://github.com/CTRLRun/ctrlrun/blob/main/docs/verify.md#in-ci):

```yaml
      - uses: CTRLRun/ctrlrun@main
        with:
          policy: ctrlrun.yaml
```

## Using it inside an agent framework

**You probably do not need an adapter.** `@protect` covers anything running in this process
today, with no adapter and no framework support: a raw OpenAI call, a LangChain tool and a
hand-rolled loop are all just decorated functions. The gateway covers anything reaching its
tools over MCP, in any language. An adapter buys exactly one thing — routing an `approve`
decision through **the framework's own interrupt** instead of raising past it — so a framework
with no human-in-the-loop primitive has nothing for an adapter to reuse and does not need one.

Where the framework does have one, the adapter reuses it and reimplements nothing. There is
never a second place to say yes: two places to approve is one place nobody is watching.

```console
$ pip install ctrlrun-langgraph
```

```python
from ctrlrun import Control, InterruptApprovalProvider
from ctrlrun_langgraph import LangGraphInterrupt

control = Control(
    policy, store,
    approvals=InterruptApprovalProvider(store, LangGraphInterrupt()),
    identity=..., authority=...,
)
```

You build the `Control` — with your policy, your store, your identity provider and your
authority document — and hand it over. An adapter never constructs one, and never supplies a
principal: an approval routed through a framework must not become a way for that framework's
session object to say who is acting.

A human answers where they already answer — `Command(resume=...)` for LangGraph,
`state.approve(item)` for the OpenAI Agents SDK — and the adapter returns that answer. **One
core provider writes the grant**, through the same calls `ctrlrun approve` makes, so the
evidence is identical whichever way the answer arrived.

| | reuses | binding |
|---|---|---|
| [`ctrlrun-langgraph`](https://github.com/CTRLRun/ctrlrun/blob/main/adapters/langgraph/README.md) | `interrupt()` and the checkpointer | **prevention** — the resumption carries the arguments and core re-checks them |
| [`ctrlrun-openai-agents`](https://github.com/CTRLRun/ctrlrun/blob/main/adapters/openai-agents/README.md) | the SDK's tool-approval interruption | **attribution** — the SDK records *that* a call was approved, not what its arguments were |

That column is the sentence to read first, and each adapter's README says which it is and why,
in that word. Prevention means CTRLRun refuses a mutated action itself. Attribution means the
binding across the interrupt is the framework's, and CTRLRun records who answered without being
able to re-check it.

**Adapters ship on their own version line** — `adapters-langgraph-1.0`, never `0.5.1`. An
adapter breaks when its framework makes a breaking release, which is somebody else's schedule
and not a kernel event. Each states its supported kernel range and framework range.

[`docs/adapters.md`](https://github.com/CTRLRun/ctrlrun/blob/main/docs/adapters.md) has the
three ways in, and how to write one for a framework not listed here.

## Two more things

**Resolving an unknown outcome without a human.** `@protect(..., reconcile=...)` takes a
function that asks the remote what happened to an effect, and it is the only thing besides a
human permitted to move a record out of `AMBIGUOUS` — and only in the direction its answer
points.

**Exporting to your tracing backend.** `pip install "ctrlrun[otel]"` adds an
`OTelEventSink`: one OpenTelemetry span per action, one span event per step. Argument values
stay out of it unless you ask for them.

## What `ctrlrun demo` shows

```console
$ ctrlrun demo
CTRLRun demo — five ways an agent action goes wrong, and what stops it.
Policy: refunds up to €1,000 are autonomous, up to €10,000 need a human, above that are denied.

1. Duplicate effect after a lost response

   refund €500  →  remote commits  →  response lost  →  effect: AMBIGUOUS
   agent retries the same refund
   ✗ BLOCKED — effect may already have committed; blind retry refused
   remote refund calls: 1
   only a human moves it on:  ctrlrun resolve refund:txn_1 --committed|--failed

2. Approval mutation

   agent proposes refund €2,000  →  human approves apr_0aa78e0380ba55d77a601dc782f57095 (bound to the action hash)
   agent executes refund €5,000  →
   ✗ BLOCKED — approved action ≠ requested action (mismatch)

3. Concurrent agents, same effect

   Agent A  reserve refund:txn_123  →  ACQUIRED  →  executes
   Agent B  reserve refund:txn_123  →
   ✗ BLOCKED — already reserved (in_progress)

4. Approval replay

   approval apr_dbc8bc6f06690cdf2e2c55a4e591ef3b used once  →  consumed
   same approval presented again                            →
   ✗ BLOCKED — single-use approval already consumed

5. Authority escalation

   human €100,000 delegable  →  finance agent €25,000  →  support agent €2,000
   support agent's grant: dlg_5f8d41938a3f29972d5489d676cd9edb
   support agent requests €50,000  →
   ✗ BLOCKED — outside the delegated grant (authority_constraint)
   remote refund calls: 0
   finance agent tries to delegate €50,000 under its own €25,000  →  refused (containment: constraints)
   support agent requests €1,500  →  authority permits it, and the policy asks a human (apr_f86eca24dd80206ab5189ccb1b62aa55)
   two axes, and an action needs both: the stricter of the pair wins

Receipts (8): .ctrlrun/demo/receipts.jsonl
Events:       .ctrlrun/demo/events.jsonl

Read them:    CTRLRUN_STATE=.ctrlrun/demo/state.db ctrlrun receipts
```

Approval ids are generated per run; everything else is byte-for-byte what the demo prints.

Note scenario 1: **`remote refund calls: 1`**. The refund committed at the remote, the response was lost, and the retry was refused — so the customer was refunded once, not twice. Nothing but a human resolving the effect moves it on.

Every executed action produces a portable JSON receipt: who, what, arguments, decision, approval, effect key, and result (`committed`, `failed`, or `ambiguous`).

## What CTRLRun is not

CTRLRun does not host models, plan, prompt, retrieve, route, remember, or orchestrate. It is not a guardrail library, an IAM system, a workflow engine, or a compliance product. It issues no credential: `authority:` decides what an identity somebody else vouched for is entitled to do, and CTRLRun runs no authorization server, mints no token, and performs no OAuth flow. If an agent only reads and answers, you don't need CTRLRun. The moment it can **send, pay, refund, delete, deploy, grant, revoke, approve, submit, purchase, or cancel**, you do.

CTRLRun cannot guarantee exactly-once execution against external systems it doesn't control. It guarantees that it will not *knowingly* execute the same logical effect twice, and that it will never treat an unknown outcome as a failure.

## Documentation

| Doc | Purpose |
|---|---|
| [`docs/SPEC-v0.1.md`](https://github.com/CTRLRun/ctrlrun/blob/main/docs/SPEC-v0.1.md) | The v0.1 contract: models, invariants, acceptance tests |
| [`docs/SPEC-v0.2.md`](https://github.com/CTRLRun/ctrlrun/blob/main/docs/SPEC-v0.2.md) | The v0.2 delta: gateway, sinks, reconciliation, webhooks |
| [`docs/SPEC-v0.3.md`](https://github.com/CTRLRun/ctrlrun/blob/main/docs/SPEC-v0.3.md) | The v0.3 delta: identity, authority, delegation, observe mode |
| [`docs/SPEC-v0.4.md`](https://github.com/CTRLRun/ctrlrun/blob/main/docs/SPEC-v0.4.md) | The v0.4 delta: the guarantee catalogue, the scenario engine, the badge |
| [`docs/verify.md`](https://github.com/CTRLRun/ctrlrun/blob/main/docs/verify.md) | `ctrlrun verify`, the guarantees, the N/A rule, and what the badge means |
| [`docs/OWASP-AGENTIC-TOP10.md`](https://github.com/CTRLRun/ctrlrun/blob/main/docs/OWASP-AGENTIC-TOP10.md) | A reading of the OWASP Top 10 for Agentic Applications against the guarantees |
| [`docs/authority.md`](https://github.com/CTRLRun/ctrlrun/blob/main/docs/authority.md) | Grants, delegation and the omission rule, in plain language |
| [`docs/ACS.md`](https://github.com/CTRLRun/ctrlrun/blob/main/docs/ACS.md) | The OWASP Agent Control Standard: what maps, and where it is silent |
| [`docs/ARCHITECTURE.md`](https://github.com/CTRLRun/ctrlrun/blob/main/docs/ARCHITECTURE.md) | Kernel design and key decisions |
| [`docs/ROADMAP.md`](https://github.com/CTRLRun/ctrlrun/blob/main/docs/ROADMAP.md) | v0.1 → v1.0 |
| [`docs/THREAT_MODEL.md`](https://github.com/CTRLRun/ctrlrun/blob/main/docs/THREAT_MODEL.md) | What CTRLRun defends against and what it doesn't |
| [`docs/CLAIMS.md`](https://github.com/CTRLRun/ctrlrun/blob/main/docs/CLAIMS.md) | Every claim above, mapped to the code and the test that proves it |
| [`SECURITY.md`](https://github.com/CTRLRun/ctrlrun/blob/main/SECURITY.md) | Reporting a vulnerability |
| [`VISION.md`](https://github.com/CTRLRun/ctrlrun/blob/main/VISION.md) | Where this can go — not a build spec |

## License

Apache-2.0. The enforcement kernel is and will remain fully open source.
