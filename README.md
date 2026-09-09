<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/CTRLRun/ctrlrun/main/docs/assets/wordmark-dark.svg">
    <img src="https://raw.githubusercontent.com/CTRLRun/ctrlrun/main/docs/assets/wordmark-light.svg" alt="CTRLRun" width="300">
  </picture>
</p>

<p align="center">
  <strong>The last check before an AI agent does something it can't undo.</strong><br>
  Autonomy belongs to the action, not the agent.<br>
  A consequential action happens at most once, exactly as approved, and leaves a receipt — and when the outcome is unknown, CTRLRun says so instead of guessing.<br>
  <br>
  A Python library that sits between the decision to act and the call that acts.<br>
  Runs in production on a single file, or on Postgres across hosts. Apache-2.0.
</p>

<!-- generated from tools/docs_audit/render_badges.py (readme) — edit the list, not this -->
<p align="center">
  <a href="https://pypi.org/project/ctrlrun/"><img src="https://img.shields.io/pypi/v/ctrlrun?color=B8730A&label=pypi" alt="PyPI"></a>
  <a href="https://pypi.org/project/ctrlrun/"><img src="https://img.shields.io/pypi/pyversions/ctrlrun?color=B8730A" alt="Python versions"></a>
  <a href="https://ctrlrun.dev"><img src="https://img.shields.io/badge/docs-ctrlrun.dev-B8730A" alt="Docs"></a>
  <a href="https://github.com/CTRLRun/ctrlrun/actions/workflows/ci.yml"><img src="https://github.com/CTRLRun/ctrlrun/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
  <a href="https://github.com/CTRLRun/ctrlrun/actions/workflows/codeql.yml"><img src="https://github.com/CTRLRun/ctrlrun/actions/workflows/codeql.yml/badge.svg?branch=main" alt="CodeQL"></a>
  <a href="https://ctrlrun.dev/docs/how-this-is-built"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/CTRLRun/ctrlrun/badges/tests-badge.json" alt="Tests"></a>
  <a href="https://ctrlrun.dev/docs/security/verify-guarantees"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/CTRLRun/ctrlrun/badges/verify-badge.json" alt="CTRLRun verified"></a>
  <a href="https://scorecard.dev/viewer/?uri=github.com/CTRLRun/ctrlrun"><img src="https://api.scorecard.dev/projects/github.com/CTRLRun/ctrlrun/badge" alt="OpenSSF Scorecard"></a>
  <a href="https://github.com/astral-sh/ruff"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json" alt="Ruff"></a>
  <a href="https://github.com/CTRLRun/ctrlrun/blob/main/scripts/check.sh"><img src="https://img.shields.io/badge/mypy-strict-B8730A" alt="Checked with mypy --strict"></a>
  <a href="https://github.com/CTRLRun/ctrlrun/blob/main/LICENSE"><img src="https://img.shields.io/pypi/l/ctrlrun?color=B8730A" alt="License"></a>
</p>
<!-- end generated -->

<p align="center">
  <img src="https://raw.githubusercontent.com/CTRLRun/ctrlrun/main/docs/assets/demo.gif" alt="ctrlrun demo: a refund commits at the remote, the response is lost, the agent retries, and the retry is refused, remote refund calls: 1. Then a human approves a €2,000 refund, the agent executes €5,000, and that is refused too." width="800">
</p>

<p align="center">
  <em>A refund is the example, not the scope. The same boundary goes in front of a deployment,
  a deletion, an IAM grant, a message that leaves the building — any action an agent takes that
  the world remembers. <a href="#the-same-shape-in-nine-domains">Nine domains, and how it
  transfers</a>.</em>
</p>

## The refund nobody approved

An agent reads a ticket and decides the customer is owed €50,000. The tool is in its list, the
arguments are well-formed, and the model is completely confident. Nothing above the call
disagrees, because nothing above the call is a check: a tool being callable is not permission to
call it with those arguments.

CTRLRun is that check. It reads the arguments about to leave your process and answers whether
this agent may send them. €50,000 is past the ceiling policy gives the agent, so the call never
leaves. Have a human approve €2,000 and then execute €5,000, and the approval authorises
nothing: it was bound to the action the human actually read.

That is the half people expect. The other half is the same agent making a *correct* €500 refund
that commits at the provider while the reply is lost coming back. The agent sees an error and
retries, because retry libraries, agent frameworks and tool loops collapse *this failed* into *I
do not know what happened*. CTRLRun keeps them apart: a lost reply is `AMBIGUOUS`, never
`FAILED`, and a retry against an `AMBIGUOUS` effect is refused until a human, or a `reconcile`
hook, says what happened.

```bash
pip install ctrlrun && ctrlrun demo
```

No Python to hand? [Break a protected action in your browser](https://ctrlrun.dev/docs/try-it):
one refund under one policy on the released wheel, in the tab, with nothing sent anywhere.
Approve €2,000, execute €5,000, lose a reply, retry — and read what refused you.

The same boundary has five other faces: two workers running one `kubectl delete namespace`, an
approval for `grant reader` spent on `grant admin`, a `delete_customer` nobody put in the
policy, quarterly numbers mailed to a personal address, and a web page that talks the agent
into a refund. [Why](https://ctrlrun.dev/docs/why) is the 700-word version.

## What `ctrlrun demo` shows

Five ways an agent action goes wrong, and what stops each one, in process, in under a second,
with no network and no external service. The animation above is the first two. In the first,
the refund commits at the remote, the reply is lost, the agent retries, and the retry is
refused — **`remote refund calls: 1`**, so the customer was refunded once and not twice, and
nothing but a human resolving the effect moves it on. In the second, a human approves a €2,000
refund, the agent executes €5,000 under that approval, and the approval matches nothing but the
action the human saw.

<details>
<summary>The full transcript, byte for byte what the demo prints</summary>

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

Approval and delegation ids are generated per run; everything else is exactly what the demo
prints, and a test fails if the two drift apart.

</details>

## Protect your first action

Wrap the call that has the consequence, and let one YAML file say how much autonomy it gets.
The rule is the same in every domain: cheap to undo is autonomous, anything that hands out
power or leaves the building needs a human, and anything that destroys the evidence is not an
agent action at any size. Save this as `ctrlrun.yaml`.

```yaml runnable
schema: ctrlrun.policy/v2

actions:
  # Cheap to undo: the agent does it alone.
  crm.update_record:
    effect: "crm:{record_id}:{field}"
    decision: allow

  # Hands out power: read-only is autonomous, anything above it is a human's call.
  iam.grant_role:
    effect: "grant:{user_id}:{role}"
    rules:
      - when: { role_in: [reader, viewer] }
        decision: allow
      - decision: approve

  # Destroys the evidence: denied, whoever asks.
  audit.log.delete:
    decision: deny
```

Anything not listed here is denied; there is no default-allow. Now `agent.py`, where `directory`
is a stand-in that records calls instead of making them:

```python runnable file=agent.py
import sys

import ctrlrun


class FakeDirectory:
    calls: list[tuple[str, str]] = []

    def grant(self, user_id: str, role: str) -> dict:
        self.calls.append((user_id, role))
        return {"user": user_id, "role": role, "status": "granted"}


directory = FakeDirectory()


@ctrlrun.protect("iam.grant_role", effect="grant:{user_id}:{role}")
def grant_role(user_id: str, role: str) -> dict:
    return directory.grant(user_id, role)


if __name__ == "__main__":
    with ctrlrun.context(agent="onboarding-agent"):
        print("reader:", grant_role(user_id="u_412", role="reader")["status"])
        try:
            grant_role(user_id="u_412", role="admin")
        except ctrlrun.ApprovalRequired as pending:
            print("admin: a human decides:", pending.request_id)
            with open("request_id.txt", "w") as handle:
                handle.write(pending.request_id)
        else:
            sys.exit("the admin grant ran without a human; the policy is not in force")
```

The `reader` grant runs on its own. The `admin` one stops and names the request a human answers:

```text
reader: granted
admin: a human decides: apr_649156806800a3545de597c028c9dae5
```

The human answers from the shell. The grant names the action hash it authorizes, and when it
lapses:

```bash runnable
ctrlrun approve "$(cat request_id.txt)"
```

```text
granted apr_649156806800a3545de597c028c9dae5 for sha256:ade45e6f6f6d5ea32ca9ddc0a1806973a729c0c2b8b5da9d422f5501fd14f574
expires 2026-09-07T11:59:30.626Z
```

Now the agent presents that approval — and tries to spend it on a bigger role:

```python runnable file=approved.py
import sys

import ctrlrun

from agent import directory, grant_role

request_id = open("request_id.txt").read().strip()

with ctrlrun.context(agent="onboarding-agent"), ctrlrun.with_approval(request_id):
    print("admin with approval:", grant_role(user_id="u_412", role="admin")["status"])
    try:
        grant_role(user_id="u_412", role="owner")
    except ctrlrun.ApprovalMismatch:
        print("owner on the same approval: refused")
    else:
        sys.exit("a mutated action ran on a spent approval; that is the bug this exists to stop")

print("directory calls:", len(directory.calls))
```

```text
admin with approval: granted
owner on the same approval: refused
directory calls: 1
```

The approval was bound to the hash of the action the human saw, so it matched `admin` on `u_412`
and nothing else. One call reached the fake directory in that process; the `owner` grant never
did. Every one of them left a receipt:

```bash runnable
ctrlrun receipts --last 3
```

```text
2026-09-07T11:44:30.625Z  ctr_1faa628d5922874e8eb97f77f55c3d40  iam.grant_role  allow/committed  grant:u_412:reader  onboarding-agent
2026-09-07T11:44:38.246Z  ctr_73314d54e87cd150de07444e989b793b  iam.grant_role  approve/committed  grant:u_412:admin  onboarding-agent
2026-09-07T11:44:38.247Z  ctr_09a9fdec28dc034b23376ffc6a1a279d  iam.grant_role  approve/blocked  grant:u_412:owner  onboarding-agent
```

That is the whole integration: a policy file, a decorator, a context, and `with_approval` to
present a grant. Money is one more action with a rule — `amount_gte`/`amount_lte` in place of
`role_in`, and both ends of every band bound, because an upper bound alone lets a negative
amount through and a refund of a negative amount is a charge.
[Protect your first action](https://ctrlrun.dev/docs/get-started/quickstart) is the
same walkthrough with every output explained · [Try it in your browser](https://ctrlrun.dev/docs/try-it) ·
[Policy YAML reference](https://ctrlrun.dev/docs/reference/policy-yaml) ·
[Cookbook](https://ctrlrun.dev/docs/cookbook/index): refunds, deploys, IAM, deletions, email, MCP,
each a recipe that runs.

## How it works

Every protected call, whichever way it arrives, goes through the same six steps:

```text
  normalize  →  decide  →  approve  →  reserve  →  execute  →  record
```

1. **Normalize.** The call becomes an `Action` — a name, canonical arguments (sorted keys, no
   floats), a resource, the principal — and its SHA-256 is the action hash.
2. **Decide.** Authority first (may *this principal* propose this at all?), then policy (how
   much autonomy does *this action* get?). Unknown action, missing policy or missing principal
   is `deny`.
3. **Approve.** A human answers against the action hash. The approval is single-use, expires,
   and matches nothing but that exact action.
4. **Reserve.** The effect key — `refund:txn_1`, `namespace:prod-eu:checkout` — is taken in one
   atomic write. A second caller, in another process or on another host, is refused.
5. **Execute.** Your function runs. Only `NotExecuted`, raised by you, means `FAILED`; every
   other exception and every timeout means `AMBIGUOUS`, and an `AMBIGUOUS` effect blocks a
   blind retry until a human or a `reconcile` hook says what happened.
6. **Record.** A portable JSON receipt: who, what, decision, approval, effect key, outcome, and
   the hash of the policy that decided it, chained to the receipt before it.

## Three ways to use it

**You probably do not need an adapter.** `@protect` covers anything running in this process: a
raw model call, a LangChain tool, a hand-rolled loop, a cron job. The gateway covers anything
that reaches its tools over MCP, in any language.

| You have | Use | Needs |
|---|---|---|
| Python in this process | the `@protect` decorator, shown above | nothing beyond `pip install ctrlrun` |
| Tools behind an MCP server, in any language | the gateway | `pip install "ctrlrun[gateway]"` |
| A framework with its own approval interrupt | an adapter | the framework to have a human-in-the-loop primitive |

**The gateway** changes no agent code and no server code. Point the MCP client at it instead of
at the tool server, and tools become actions named `mcp.<alias>.<tool>`, decided by the same
policy:

```bash
pip install "ctrlrun[gateway]"
ctrlrun gateway --upstream http://localhost:8000/mcp --alias acme --principal support-agent
```

A tool call has no decorator to carry its effect and resource templates, so the policy declares
them:

```yaml runnable file=gateway.yaml
schema: ctrlrun.policy/v2

actions:
  mcp.acme.delete_document:
    effect: "document:{document_id}"
    resource: "document:{document_id}"
    decision: approve
  mcp.acme.search_documents:
    decision: allow
```

Everything but `tools/call` is relayed untouched, a lost response over the wire blocks the retry
exactly as it does in process, and the gateway prints on the line that starts it every action in
your policy with no `effect:` template — because a write with no effect key is the configuration
this exists to prevent. [`ctrlrun.dev/mcp/overview`](https://ctrlrun.dev/docs/mcp/overview) is the
whole section.

**An adapter** exists for one reason: to route an `approve` decision through **the framework's
own interrupt** instead of raising `ApprovalRequired` past your graph. A human answers where
they already answer, and one core provider writes the grant through the same calls `ctrlrun
approve` makes. There is never a second place to say yes.

| | reuses | binding |
|---|---|---|
| [`ctrlrun-langgraph`](https://github.com/CTRLRun/ctrlrun/blob/main/adapters/langgraph/README.md) | `interrupt()` and the checkpointer | **prevention** — the resumption carries the arguments and core re-checks them against the hash |
| [`ctrlrun-openai-agents`](https://github.com/CTRLRun/ctrlrun/blob/main/adapters/openai-agents/README.md) | the SDK's tool-approval interruption | **attribution** — the SDK records *that* a call was approved, not what its arguments were |

You build the `Control` with your policy, store, identity provider and authority document and
hand it over: an adapter never constructs one and never supplies a principal. Adapters ship on
their own version line, `adapters-langgraph-1.0` and never `0.6.1`, because an adapter breaks
when its framework makes a breaking release, which is not a kernel event.
[`docs/docs/adapters.md`](https://github.com/CTRLRun/ctrlrun/blob/main/docs/docs/adapters.md) has the three
ways in and how to write one for a framework not listed here.

## The same shape in nine domains

Nothing in CTRLRun knows what a refund is. An action is a **name**, **canonical arguments**, an
**effect key** and a **resource**, and the three questions asked of it are the same whichever
domain it came from: how much autonomy does *this action* get, did a human approve *this exact*
action, and has this effect already happened. A payout, a namespace and a change of dose are
the same shape to the kernel. Two things carry the domain, and you write both:

- **The effect key is the only domain knowledge in the system** — the string that says two calls
  are the same real-world consequence. `refund:{payment_id}`, `namespace:{cluster}:{name}`,
  `grant:{user_id}:{role}`, `prescription:{patient_id}:{drug}`. Name it well and a retry cannot
  act twice; leave it out and there is nothing for *at most once* to be about, which is why the
  gateway prints every action in your policy that has no `effect:` template on the line that
  starts it.
- **Conditions are arguments, not amounts.** `amount_lte` is not a money feature: the condition
  language is `<argument>_<op>`, so the same operators read `replicas_lte: 10`,
  `host_count_lte: 1`, `role_in: [reader, viewer]` and `to_domain_eq: acme.com`. Any integer
  argument can be bounded and any argument can be matched, so a band is available to a domain
  that has never issued an invoice.

The nine files under
[`examples/policies/`](https://github.com/CTRLRun/ctrlrun/tree/main/examples/policies) are that
applied, one per domain. Adapt them; none is a drop-in.

| Domain | Autonomous | A human decides | Never |
|---|---|---|---|
| [DevOps](https://github.com/CTRLRun/ctrlrun/blob/main/examples/policies/devops.yaml) | `k8s.scale_deployment` to 10 replicas | `terraform.apply` | `k8s.delete_namespace` |
| [Security operations](https://github.com/CTRLRun/ctrlrun/blob/main/examples/policies/security.yaml) | `firewall.add_deny_rule` | `firewall.add_allow_rule` | `edr.disable_protection` |
| [Healthcare](https://github.com/CTRLRun/ctrlrun/blob/main/examples/policies/healthcare.yaml) | `appointment.reschedule` | `patient.export_record` | `prescription.change_dose` |
| [Legal](https://github.com/CTRLRun/ctrlrun/blob/main/examples/policies/legal.yaml) | `document.draft_internal` | `document.file_with_court` | `contract.execute` |
| [HR](https://github.com/CTRLRun/ctrlrun/blob/main/examples/policies/hr.yaml) | `pto.approve` within a band | `payroll.run` | `employee.delete_record` |
| [Insurance](https://github.com/CTRLRun/ctrlrun/blob/main/examples/policies/insurance.yaml) | `claim.request_documents` | `claim.approve_payout` above a band | `policyholder.delete` |
| [E-commerce](https://github.com/CTRLRun/ctrlrun/blob/main/examples/policies/e-commerce.yaml) | `inventory.adjust` within a band | `price.update` | `customer.delete` |
| [Public services](https://github.com/CTRLRun/ctrlrun/blob/main/examples/policies/government.yaml) | `eligibility.precheck` | `benefit.terminate` | `record.delete` |
| [Payments](https://github.com/CTRLRun/ctrlrun/blob/main/examples/policies/payments.yaml) | `stripe.refund` under €500 | `stripe.refund` above it | `stripe.delete_customer` |

Read any row left to right and it is one rule wearing different nouns: cheap to undo is
autonomous, anything that hands out power or leaves the building needs a human, and anything
that destroys the evidence is not an agent action at any size. The security row is the one to
read twice — adding a **deny** rule to a firewall is autonomous and adding an **allow** rule is
not, which no amount threshold would have told you. The policy is where your judgement about
your domain gets written down; CTRLRun is what makes it hold.

## Write down what the agent may do

One file. The rule is the same in every domain: cheap to undo is autonomous, anything that
leaves the building needs a human, money is by amount with both ends bound, and anything that
destroys the evidence is not an agent action at any size. Unknown actions are denied; there is
no default-allow.

<details>
<summary>The whole file: CRM, Kubernetes, email, IAM, Stripe, the audit log</summary>

```yaml runnable
schema: ctrlrun.policy/v2

actions:
  # Cheap to undo: autonomous.
  crm.update_record:
    effect: "crm:{record_id}:{field}"
    decision: allow
  k8s.rollout_restart:
    effect: "restart:{cluster}:{deployment}"
    decision: allow

  # Leaves the building: a human, every time it reaches somebody or something outside.
  email.send:
    effect: "email:{message_id}"
    rules:
      - when: { to_domain_eq: "example.com" }
        decision: allow
      - decision: approve
  iam.grant_role:
    effect: "grant:{principal}:{role}"
    rules:
      - when: { role_in: [reader, viewer] }
        decision: allow
      - decision: approve
  k8s.delete_namespace:
    effect: "namespace:{cluster}:{name}"
    decision: approve

  # Money: by amount, in integer minor units, with both ends bound. An upper bound alone is
  # not a range, and a refund of a negative amount is a charge.
  stripe.refund:
    effect: "refund:{payment_id}"
    rules:
      - when: { amount_gte: 0, amount_lte: 50000 }       # up to €500.00: autonomous
        decision: allow
      - when: { amount_gte: 0, amount_lte: 500000 }      # up to €5,000.00: a human
        decision: approve
      - decision: deny

  # Destroys the evidence: denied, whoever asks.
  stripe.delete_customer:
    decision: deny
  audit.log.delete:
    decision: deny
```

</details>

Amounts are integer minor units; floats are rejected outright, because `0.1` and `0.10` are the
same money and different hashes. The policy cannot see who is asking — deliberately, since v0.1:
`agent_eq` and every other principal-addressing condition is refused at load. Who may ask is the
second axis, `authority:`, and it is **opt-in, then fail-closed**: a policy without one behaves
exactly as before, and the moment one exists every principal needs a grant and no grant means
denied. The two axes are evaluated separately, authority first, and combine as the **stricter of
the two**. A `delegable` grant can be narrowed at runtime with `ctrlrun delegate` and never
widened — a delegation must be provably a subset of its parent on every dimension, at creation
and again at every evaluation, and omitting a dimension the parent constrains is rejected rather
than inherited — and `ctrlrun revoke` cuts a chain of any depth with one write.
[`docs/docs/authority.md`](https://github.com/CTRLRun/ctrlrun/blob/main/docs/docs/authority.md) has it in
plain language.

**Roll it out with `mode: observe` first.** One top-level line runs every real decision against
real traffic and records what *would* have been blocked, without blocking anything, and
`ctrlrun stats` gives you the numbers before you enforce them. It is not a dry run: it executes.

## Prove it holds in your setup

Everything above is proven by this repository's tests against this repository's configurations.
What you deploy is *your* policy, *your* grants and *your* store, so `ctrlrun verify` runs the
kernel's own failure scenarios against the configuration in front of it, in a scratch store,
with fake executors, and no network. Your `.ctrlrun/state.db` is byte-identical before and after.

<details>
<summary><code>ctrlrun verify</code> against a policy with approvals, effects and grants: 11/11</summary>

```console
$ ctrlrun verify
CTRLRun verify — ctrlrun 0.6.1, catalogue ctrlrun.guarantees/v2
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
G11  an altered receipt is detected   PASS  stripe.refund

11/11 declared guarantees pass. 0 not applicable.
```

</details>

**Not applicable is not a pass.** A policy with no `approve` rule cannot exercise the
approval-binding guarantees, and one with no `effect:` templates cannot exercise the effect
guarantees. Each is reported `N/A` with the reason, excluded from the denominator and listed
separately — the same command against a `ctrlrun.policy/v1` document with no templates and no
grants ends `6/6 declared guarantees pass. 5 not applicable: G3, G4, G5, G8, G9.`, never
`11/11`. There is no flag that folds one into the count.

The badge at the top of this page means the **declared guarantees pass**: every guarantee this
configuration can exercise was exercised, and none failed. It does not mean secure, safe,
compliant, certified or audited, and [`docs/docs/verify.md`](https://github.com/CTRLRun/ctrlrun/blob/main/docs/docs/verify.md#what-the-badge-means)
says on the same screen what verify cannot see: your executors, your `reconcile` hooks, where
you put the decorator, your deployment, and whether your policy is the right policy. There is a
[GitHub Action](https://github.com/CTRLRun/ctrlrun/blob/main/docs/docs/verify.md#in-ci):

```yaml
      - uses: CTRLRun/ctrlrun@v0.6.1
        with:
          policy: ctrlrun.yaml
```

The ref pins the action's steps and **not** the package they install: `install` defaults to
`ctrlrun`, which is whatever PyPI has that day. Add `install: ctrlrun==0.6.1` to pin the tool
as well as the workflow.

## What it guarantees, and what it can't

The six guarantees, and which of the three ways in carries each:

<!-- generated from docs/capabilities.yaml (readme) — edit the YAML, never this table -->
| Guarantee | `@protect` | Gateway | Adapter |
|---|---|---|---|
| **Approval binding** — An approval is bound to the exact action; a mutated or replayed one is refused. | yes | yes | prevention or attribution, per adapter |
| **One effect, once** — One logical effect happens at most once, across threads, processes and hosts. | yes | yes | yes |
| **Unknown is not failed** — An unknown outcome is AMBIGUOUS, never FAILED, and blocks a blind retry. | yes | yes | yes |
| **Fail closed** — An unknown action, a missing policy or a missing principal is denied. | yes | yes | yes |
| **Authority and delegation** — With authority on, every principal needs a grant, and delegation cannot widen one. | yes | yes | yes |
| **Receipts** — Every executed action leaves a portable JSON receipt of who, what and outcome. | yes | yes | yes |
<!-- end generated -->

**It guarantees** what the matrix says, plus the mechanics behind it. Every line has a test in
[`docs/docs/CLAIMS.md`](https://github.com/CTRLRun/ctrlrun/blob/main/docs/docs/CLAIMS.md), and
[`docs/docs/how-this-is-built.md`](https://github.com/CTRLRun/ctrlrun/blob/main/docs/docs/how-this-is-built.md)
says how those tests came to exist:

- **One effect, once, across hosts.** `BEGIN IMMEDIATE` on SQLite; on Postgres a unique index on
  the effect key and compare-and-set updates whose row counts are checked.
- **Only `NotExecuted` means `FAILED`.** Every timeout, lost reply and unexpected exception is
  `AMBIGUOUS`, and a crashed worker's effect stays that way until a human runs `ctrlrun resolve`
  or a `reconcile` hook asks the remote — and then only in the direction that answer points.
- **Identity is consumed, never invented.** `ctrlrun[identity]` verifies a bearer token against a
  JWKS or a pinned key and maps the claims onto a principal. CTRLRun issues no credential.
- **Every receipt names the policy that decided it** and carries the hash of the receipt before
  it. Where a policy changes between the approval and the execution, the approval is re-checked
  against the policy in force at execution.
- **Releases carry PyPI provenance attestations from GitHub Actions**: trusted publishing, no API
  token anywhere, an attestation on every distribution naming the workflow that built it.
- **The operator works from the shell** — `approve`, `deny`, `resolve`, `inspect`, `receipts`,
  `effects`, `stats` against any store — or over MCP with `ctrlrun mcp-operator`. It records who
  answered; it does not check that they were entitled to.

**It can't**, and does not claim to:

- CTRLRun cannot guarantee exactly-once execution against external systems it doesn't control.
  It refuses to *knowingly* act twice; whether a remote acted is a fact only the remote holds.
  It is not a transaction manager: it rolls nothing back.
- The receipt chain detects alteration, and alteration is not authorship. Receipts are not
  signed, the chain is no evidence of who wrote one, and it is not tamper-proof: it does not
  survive an administrator who can rewrite every row including the chain head, and erasing the
  end of the log costs two statements. [`docs/docs/THREAT_MODEL.md`](https://github.com/CTRLRun/ctrlrun/blob/main/docs/docs/THREAT_MODEL.md) states
  what remains open.
- `ctrlrun verify` cannot see your executors. An executor that raises `NotExecuted` after the
  remote acted turns the one retryable exception into a licence to act twice, and nothing here
  can check that for you.
- **CTRLRun does not detect prompt injection**, and nothing here reads the agent's instructions
  to decide whether they were poisoned. The narrower claim is the one that holds: an injected
  instruction still has to get past a grant the agent does not hold, an amount that needs a
  human, and an approval bound to the recipient the human saw. Containment of the consequence,
  not detection of the cause.
- It does not host models, plan, prompt, retrieve, route, remember or orchestrate, and it makes
  no claim about any standard: [`docs/docs/OWASP-AGENTIC-TOP10.md`](https://github.com/CTRLRun/ctrlrun/blob/main/docs/docs/OWASP-AGENTIC-TOP10.md)
  is a reading of somebody else's taxonomy against the guarantees, and names the four entries it
  does not address.

If an agent only reads and answers, you don't need CTRLRun. The moment it can **send, pay,
refund, delete, deploy, grant, revoke, approve, submit, purchase or cancel**, you do.

## Running it in production

**SQLite is the default and is production-grade on one host.** A file, no server, no ops — and
the concurrency guarantee is held by `BEGIN IMMEDIATE` and a unique constraint, across processes
and not merely across threads. **Postgres is for many hosts**: `pip install "ctrlrun[postgres]"`,
one URL, the same `StateStore` protocol extended by nothing, graded by the suite written for
SQLite. Choose by how many machines write, not by how serious you are.

<!-- generated from the suite, pyproject and the soak (readme) — run the generator -->
- **Version 0.6.1**, on [PyPI](https://pypi.org/project/ctrlrun/), Python 3.11 and later.
- **4,404 tests**, every version specified before it was written and every requirement mutation-tested.
- **11 guarantees you can check in your own setup**, with `ctrlrun verify` against your policy, on your store's backend, in a scratch store it creates.
- **One host: a file.** SQLite, no server, no ops. **Many hosts: Postgres**, the same guarantees, graded by the same suite.
- **Soaked for 20m 0s on postgres**: 889,735 actions, 0 unattributed ambiguous outcomes, positive control fired. Nothing here establishes what only accumulates over days. [What it does not establish](https://ctrlrun.dev/docs/production/soak).
- **Each receipt carries the hash of the one before it**, so an alteration is detected and named.
- **Apache-2.0**, and the enforcement kernel stays open source. Releases carry PyPI provenance attestations from GitHub Actions.

**Not yet:**

- No external security audit. (planned for v0.8 or v0.9)
- No third-party review of the kernel. (every review so far was run inside this project)
- No sector packs. (the policy templates are starting points, not a product)
<!-- end generated -->

### Beyond one process

`BEGIN IMMEDIATE` is a whole-database write lock on a local file; take the file away and *one
effect, once* has to be re-earned. What 0.6 added to re-earn it:

- **Postgres**, **migrations** at open and forward-only with no flag that opens a database
  un-migrated, **recovery after a crash**, and **a store conformance suite**, so a second backend
  is graded rather than described.
- **Policy versioning** — a receipt from six months ago says what the rules were, not what they
  are now.
- **Receipt integrity** — each receipt carries the hash of the one before it, so an edit, a
  deletion from the middle or a reordering is detected and named by `seq`. It detects
  **alteration**, which is not authorship: receipts are not signed.

[ctrlrun.dev/production/index](https://ctrlrun.dev/docs/production/index) is the whole section:
choosing a store, what reservation does under a lost `COMMIT`, migrations, recovery after a
crash, the receipt chain, the soak, and what to watch once it is running.
[`CHANGELOG.md`](https://github.com/CTRLRun/ctrlrun/blob/main/CHANGELOG.md) has the entry,
including the two subcommands that ride along in this release without being part of the
milestone: `ctrlrun scan` and `ctrlrun mcp-operator`.

## Documentation

**[ctrlrun.dev](https://ctrlrun.dev)** is the documentation: concepts, guides, a
cookbook, the reference, and a browser demo that runs `ctrlrun demo` with no install.

| Section | Where |
|---|---|
| Start here | [Why](https://ctrlrun.dev/docs/why) · [Protect your first action](https://ctrlrun.dev/docs/get-started/quickstart) · [Try it in your browser](https://ctrlrun.dev/docs/try-it) |
| The ideas | [Concepts](https://ctrlrun.dev/docs/concepts/outcomes-and-ambiguous) |
| Doing something | [Guides](https://ctrlrun.dev/docs/guides/protect-a-function) · [Cookbook](https://ctrlrun.dev/docs/cookbook/index) |
| Running it for real | [Production](https://ctrlrun.dev/docs/production/index) · [Postgres](https://ctrlrun.dev/docs/production/postgres) · [Recovery](https://ctrlrun.dev/docs/production/recovery) · [Operations](https://ctrlrun.dev/docs/production/operations) |
| MCP | [Overview](https://ctrlrun.dev/docs/mcp/overview) · [The gateway in five minutes](https://ctrlrun.dev/docs/mcp/gateway-in-5-minutes) |
| Every key, flag and error | [Reference](https://ctrlrun.dev/docs/reference/policy-yaml) |
| Compared with other things | [Compare](https://ctrlrun.dev/docs/compare/idempotency-keys) · [FAQ](https://ctrlrun.dev/docs/faq) |
| Security | [Threat model](https://ctrlrun.dev/docs/THREAT_MODEL) · [What verify guarantees](https://ctrlrun.dev/docs/security/verify-guarantees) · [SECURITY.md](https://github.com/CTRLRun/ctrlrun/blob/main/SECURITY.md) |
| How this is built, and what is not done | [How this is built](https://ctrlrun.dev/docs/how-this-is-built) |
| Every sentence above, mapped to the code and the test that proves it | [`docs/docs/CLAIMS.md`](https://github.com/CTRLRun/ctrlrun/blob/main/docs/docs/CLAIMS.md) |
| The contract, per version | [`docs/SPEC-v0.1.md`](https://github.com/CTRLRun/ctrlrun/blob/main/docs/SPEC-v0.1.md) · [v0.2](https://github.com/CTRLRun/ctrlrun/blob/main/docs/SPEC-v0.2.md) · [v0.3](https://github.com/CTRLRun/ctrlrun/blob/main/docs/SPEC-v0.3.md) · [v0.4](https://github.com/CTRLRun/ctrlrun/blob/main/docs/SPEC-v0.4.md) · [v0.5](https://github.com/CTRLRun/ctrlrun/blob/main/docs/SPEC-v0.5.md) · [v0.6](https://github.com/CTRLRun/ctrlrun/blob/main/docs/SPEC-v0.6.md) |
| Contributing | [`CONTRIBUTING.md`](https://github.com/CTRLRun/ctrlrun/blob/main/CONTRIBUTING.md), [`CODE_OF_CONDUCT.md`](https://github.com/CTRLRun/ctrlrun/blob/main/CODE_OF_CONDUCT.md) |
| Changelog and roadmap | [`CHANGELOG.md`](https://github.com/CTRLRun/ctrlrun/blob/main/CHANGELOG.md), [`docs/docs/ROADMAP.md`](https://github.com/CTRLRun/ctrlrun/blob/main/docs/docs/ROADMAP.md) |

## License

Apache-2.0. The enforcement kernel is and will remain fully open source.
