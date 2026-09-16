<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/CTRLRun/ctrlrun/main/docs/assets/wordmark-dark.svg">
    <img src="https://raw.githubusercontent.com/CTRLRun/ctrlrun/main/docs/assets/wordmark-light.svg" alt="ctrlrun" width="300">
  </picture>
</p>

<p align="center">
  <strong>ctrlrun stops AI agents from taking wrong, restricted, or malicious actions in your workflows.</strong><br>
  Every action is checked against your rules before it runs. Allowed actions go through.<br>
  Sensitive ones wait for a person. Forbidden ones are blocked.<br>
  <br>
  Execution safety for AI agents. A Python library that sits between the decision to act and the call that acts.<br>
  A consequential action happens at most once, exactly as approved, and leaves a receipt.<br>
  When the outcome is unknown, ctrlrun says so instead of guessing.<br>
  <br>
  Runs in production on a single file, or on Postgres across hosts. Apache-2.0.
</p>

<!-- generated from tools/docs_audit/render_badges.py (readme) — edit the list, not this -->
<p align="center">
  <a href="https://github.com/CTRLRun/ctrlrun/blob/badges/clones-history.json"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/CTRLRun/ctrlrun/badges/clones-badge.json" alt="Clones"></a>
  <a href="https://pypi.org/project/ctrlrun/"><img src="https://img.shields.io/pypi/v/ctrlrun?color=B8730A&label=pypi" alt="PyPI"></a>
  <a href="https://pypistats.org/packages/ctrlrun"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/CTRLRun/ctrlrun/badges/downloads-badge.json" alt="Downloads"></a>
  <a href="https://docs.ctrlrun.dev/"><img src="https://img.shields.io/badge/docs-docs.ctrlrun.dev-B8730A" alt="Docs"></a>
  <a href="https://github.com/CTRLRun/ctrlrun/actions/workflows/ci.yml"><img src="https://github.com/CTRLRun/ctrlrun/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
  <a href="https://github.com/CTRLRun/ctrlrun/actions/workflows/codeql.yml"><img src="https://github.com/CTRLRun/ctrlrun/actions/workflows/codeql.yml/badge.svg?branch=main" alt="CodeQL"></a>
  <a href="https://github.com/CTRLRun/ctrlrun/actions/workflows/fuzz.yml"><img src="https://github.com/CTRLRun/ctrlrun/actions/workflows/fuzz.yml/badge.svg?branch=main" alt="Fuzz"></a>
  <a href="https://docs.ctrlrun.dev/how-this-is-built"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/CTRLRun/ctrlrun/badges/tests-badge.json" alt="Tests"></a>
  <a href="https://docs.ctrlrun.dev/security/verify-guarantees"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/CTRLRun/ctrlrun/badges/verify-badge.json" alt="ctrlrun verified"></a>
  <a href="https://scorecard.dev/viewer/?uri=github.com/CTRLRun/ctrlrun"><img src="https://api.scorecard.dev/projects/github.com/CTRLRun/ctrlrun/badge" alt="OpenSSF Scorecard"></a>
  <a href="https://www.bestpractices.dev/projects/14615"><img src="https://www.bestpractices.dev/projects/14615/badge" alt="OpenSSF Best Practices"></a>
  <a href="https://github.com/CTRLRun/ctrlrun/blob/main/LICENSE"><img src="https://img.shields.io/pypi/l/ctrlrun?color=B8730A" alt="License"></a>
</p>
<!-- end generated -->

<p align="center">
  <img src="https://raw.githubusercontent.com/CTRLRun/ctrlrun/main/docs/assets/demo.gif" alt="A terminal recording of the three steps below. A policy file gives refunds up to €500 to the agent, sends refunds up to €10,000 to a human, and denies the rest. The agent refunds €500 on its own; a €5,000 refund stops and names the request a human answers. ctrlrun approve grants it against the hash of that exact action. The agent runs the €5,000 refund, then tries €9,000 on the same approval, and that is refused: calls that reached the provider, 1, the €9,000 never left." width="800">
</p>

```bash
pip install ctrlrun && ctrlrun demo
```

## What it does

**The model guesses. ctrlrun does not.** The ticket says refund €500. The agent asks for
€5,000, one extra zero. The tool is in its list, the arguments are well formed, and the model
is completely confident. Nothing above the call disagrees, because nothing above the call is a
check: a tool being callable is not permission to call it with those arguments.

| Without ctrlrun | With ctrlrun |
|---|---|
| Nothing checks the amount. The call goes through. **€4,500 too much.** | Your rule checks the amount. The call never leaves. **€0 wrongly paid.** |

ctrlrun is that check. It reads the arguments about to leave your process and answers what may
happen to them: let it run, ask a human, or stop it cold. Four rules do the work, and each one
is a test in this repository before it is a sentence here.

| | |
|---|---|
| **Exact means exact** | Changed arguments need a new approval. |
| **Once stays once** | Same effect key, shared store, no repeat. |
| **Unknown means wait** | Confirm the outcome before retrying. |
| **Every answer is kept** | Requests, decisions and results, refusals included. |

The third one is the half people forget. A correct €500 refund commits at the provider and the
reply is lost coming back, so the agent retries. Retry libraries, agent frameworks and tool
loops collapse *this failed* into *I do not know what happened*. ctrlrun keeps them apart: a
lost reply is `AMBIGUOUS`, never `FAILED`, and a retry against an `AMBIGUOUS` effect is refused
until a human, or a `reconcile` hook, says what happened.

<details>
<summary>What <code>ctrlrun demo</code> shows: five failures and five refusals, byte for byte</summary>

```console
$ ctrlrun demo
ctrlrun demo — five ways an agent action goes wrong, and what stops it.
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
prints, and a test fails if the two drift apart. No network, no external service, under a
second. `pip install ctrlrun && ctrlrun demo` runs it locally in about the same time.

</details>

**Where it stops.** It does not detect prompt injection: it contains the consequence rather
than reading the cause. It cannot promise exactly-once against a remote it does not control, it
refuses to *knowingly* act twice, and it rolls nothing back. Receipts are chained, so an alteration
is detected; a truncation at the end and a forged append are not, because the head that would catch
them is a row in the same database, and closing that is what `ctrlrun anchor` is for. They are not
signed: alteration is not authorship. The badge above means the
**declared guarantees pass** in the setup they ran against, and it does not mean secure, safe,
compliant, certified or audited:
[what the badge means](https://docs.ctrlrun.dev/verify#what-the-badge-means)
· [`OWASP-AGENTIC-TOP10.md`](https://docs.ctrlrun.dev/OWASP-AGENTIC-TOP10)
names the four entries this does not address.

If an agent only reads and answers, you do not need ctrlrun. The moment it can **send, pay,
refund, delete, deploy, grant, revoke, approve, submit, purchase or cancel**, you do.

## Use it in three steps

The animation above is this section, recorded against the real library: one policy file, two
short programs, four commands, nothing staged.

**1. Install it.**

```bash
pip install ctrlrun
```

**2. Write down what the agent may do.** One file, `ctrlrun.yaml`. Amounts are integer minor
units, so `50000` is €500. Both ends of every band are bound, because an upper bound alone lets
a negative amount through, and a refund of a negative amount is a charge. Anything not listed is
denied; there is no default-allow.

```yaml runnable
schema: ctrlrun.policy/v2

actions:
  stripe.refund:
    effect: "refund:{payment_id}"
    rules:
      - when: { amount_gte: 0, amount_lte: 50000 }
        decision: allow      # up to €500: the agent acts alone
      - when: { amount_gte: 0, amount_lte: 1000000 }
        decision: approve    # up to €10,000: a human decides
      - decision: deny       # above that: never
```

**3. Wrap the call that has the consequence.** The decorator names the action, the effect key
names the consequence it has in the world, and the context names who is acting. `stripe` here
is a stand-in that records calls instead of making them.

```python runnable file=agent.py
import sys

import ctrlrun


class FakeStripe:
    """Stands in for the provider: it records calls instead of making them."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def refund(self, payment_id: str, amount: int) -> dict:
        self.calls.append((payment_id, amount))
        return {"id": f"re_{payment_id}", "amount": amount, "status": "succeeded"}


stripe = FakeStripe()


@ctrlrun.protect("stripe.refund", effect="refund:{payment_id}")
def refund(payment_id: str, amount: int) -> dict:
    return stripe.refund(payment_id, amount)


if __name__ == "__main__":
    with ctrlrun.context(agent="support-agent"):
        print("€500   ->", refund(payment_id="txn_1", amount=50_000)["status"])
        try:
            refund(payment_id="txn_2", amount=500_000)
        except ctrlrun.ApprovalRequired as pending:
            print("€5,000 -> a human decides:", pending.request_id)
            with open("request_id.txt", "w") as handle:
                handle.write(pending.request_id)
        else:
            sys.exit("the €5,000 refund ran without a human; the policy is not in force")
    print("calls that reached the provider:", len(stripe.calls))
```

The €500 refund runs on its own. The €5,000 one stops and names the request a human answers:

```text
€500   -> succeeded
€5,000 -> a human decides: apr_63e80076f2cccfee52b17491a4b2e125
calls that reached the provider: 1
```

**A human answers from the shell.** The grant names the hash of the exact action the human
read, and when it lapses. Ids, hashes and dates are generated per run; yours differ.

```bash runnable
ctrlrun approve "$(cat request_id.txt)"
```

```text
granted apr_63e80076f2cccfee52b17491a4b2e125 for sha256:22ec1c398e4b93d080b6cba61e5e11b0e21879552ac5dbf63c192d2b2e6af752
expires 2026-09-13T20:10:11.367Z
```

**The agent presents it, then tries to spend it on something else.** The first call is exactly
what the human approved, and it runs. The second is the same approval with one digit changed,
and it matches nothing:

```python runnable file=approved.py
import sys

import ctrlrun

from agent import refund, stripe

with open("request_id.txt") as handle:
    request_id = handle.read().strip()

with ctrlrun.context(agent="support-agent"), ctrlrun.with_approval(request_id):
    # Exactly what the human read: €5,000 on txn_2.
    print("€5,000 with the approval ->", refund(payment_id="txn_2", amount=500_000)["status"])

    # The same approval, one digit changed.
    try:
        refund(payment_id="txn_2", amount=900_000)
    except ctrlrun.ApprovalMismatch:
        print("€9,000 on that same approval -> refused")
    else:
        sys.exit("a mutated action ran on a human's approval; that is the bug this exists to stop")

print("calls that reached the provider:", len(stripe.calls), "(the €9,000 never left)")
```

```text
€5,000 with the approval -> succeeded
€9,000 on that same approval -> refused
calls that reached the provider: 1 (the €9,000 never left)
```

Every attempt, refusals included, left a receipt, and `ctrlrun receipts` lists them. That is the
whole integration: a policy file, a decorator, a context, and `with_approval` to present a grant.
Money is the example, not the scope. A condition is `<argument>_<op>`, so the same policy
language reads `role_in: [reader, viewer]` or `replicas_lte: 10` as easily as `amount_lte`, and
[nine domains](#the-same-shape-in-nine-domains) below have one policy each.
[Protect your first action](https://docs.ctrlrun.dev/get-started/quickstart) walks the same path
with every output explained ·
[Policy YAML reference](https://docs.ctrlrun.dev/reference/policy-yaml) ·
[Cookbook](https://docs.ctrlrun.dev/cookbook/index): refunds, deploys, IAM, deletions, email, MCP.

## Three ways to use it

**You probably do not need an adapter.** `@protect` covers anything running in this process: a
raw model call, a LangChain tool, a hand-rolled loop, a cron job. The gateway covers anything
that reaches its tools over MCP, in any language.

| You have | Use | Needs |
|---|---|---|
| Python in this process | the `@protect` decorator, shown above | nothing beyond `pip install ctrlrun` |
| Tools behind an MCP server, in any language | the gateway: `pip install "ctrlrun[gateway]"` | one command, no change to agent or server code |
| A framework with its own approval interrupt | an adapter | the framework to have a human-in-the-loop primitive |

**It works with agents you can and can't modify.** WhatsApp, Slack and Teams bots, ChatGPT,
Cursor, Codex, OpenAI Agents: any AI agent you have. ctrlrun checks the action, not the
agent, so if the agent acts through a tool server or an API you run, the action is checked, and
the agent is not rebuilt, redeployed or told.
[Agents you can't modify](https://docs.ctrlrun.dev/agents-you-cant-modify) says where the
boundary goes for each kind.

An adapter exists for one reason: to route an `approve` decision through the framework's own
interrupt, so a human answers where they already answer. There is never a second place to say
yes. [`ctrlrun-langgraph`](https://github.com/CTRLRun/ctrlrun/blob/main/adapters/langgraph/README.md)
gives **prevention**, because the resumption carries the arguments and core re-checks them
against the hash.
[`ctrlrun-openai-agents`](https://github.com/CTRLRun/ctrlrun/blob/main/adapters/openai-agents/README.md)
gives **attribution**, because that SDK records *that* a call was approved and not what its
arguments were. None of the three is only for agents: a worker, a webhook handler and a
scheduled job cannot tell a first attempt from a retry either.

## How it works

Every protected call, whichever way it arrives, goes through the same seven steps. Only then
does it reach your systems.

```text
  normalize  →  decide  →  approve  →  reserve  →  execute  →  resolve  →  record
```

1. **Normalize: one action, one id.** The call becomes an `Action`: a name, canonical arguments
   (sorted keys, no floats), a resource, the principal. Its SHA-256 is the action hash.
2. **Decide: allow, ask or block.** Authority first (may *this principal* propose this at all,
   and within what bounds?), then policy (how much autonomy does *this action* get?). Unknown
   action, missing policy or missing principal is `deny`. Silence is never permission.
3. **Approve: bound to this action.** A human answers against the action hash. The approval is
   single-use, expires, and matches nothing but that exact action, so arguments changed after
   the answer void it and a person answers again. Name a `preconditions=` provider and the
   approval is also bound to the resource state it was granted against, rechecked strictly
   before the reservation: that **narrows** the window between the answer and the execution,
   from minutes of deliberation to milliseconds. It does not close it, because the recheck is a
   network call and cannot run inside the atomic write.
4. **Reserve: claimed once.** The effect key, `refund:txn_1` or `namespace:prod-eu:checkout`, is
   taken in one atomic write. A second caller, in another process or on another host, is refused.
5. **Execute: your code runs.** Only `NotExecuted`, raised by you, means `FAILED`; every other
   exception and every timeout means `AMBIGUOUS`. Deciding which one you are looking at is the
   hard part, so `ctrlrun.transport` does it for you: `urlopen`, `HTTPConnection` and
   `HTTPSConnection` from stdlib `urllib` and `http.client`, which raise `NotExecuted` only where
   the connection they opened was handed no request byte. After one byte, every failure stays the
   exception it was, and the outcome is `AMBIGUOUS`. No setting widens that.
6. **Resolve: unknown is not failed.** An `AMBIGUOUS` effect keeps its key and refuses a retry
   until `ctrlrun resolve`, or a `reconcile` hook that asked the remote, says what happened.
   Nothing runs twice on a guess.
7. **Record: a receipt either way.** A portable JSON receipt: who, what, decision, approval,
   effect key, outcome, and the hash of the policy that decided it, chained to the receipt
   before it. Refusals get one too.

**Who may ask, and how much.** The policy decides the action and cannot see who is asking. Who
may ask at all is a second axis, authority: every principal needs a grant, a delegation cannot
widen one, and an action needs both axes, the stricter of the pair. Since 0.9 a grant can also
carry a **budget**, a metric with a limit over a rolling window, consumed on reserve inside the
same write, so a thousand refunds that each pass `amount_lte` cannot add up to more than the
grant allows. An `AMBIGUOUS` effect holds its budget until it is resolved, because otherwise an
agent that can manufacture ambiguity could manufacture authority. A budget bounds what the next
reservation may do; it cannot recall an action already in flight.
[Authority](https://docs.ctrlrun.dev/authority) has the whole model.

State lives in SQLite by default, a file with no server and no ops, and the reservation holds
across processes rather than merely across threads. Point it at Postgres when more than one
host writes: `pip install "ctrlrun[postgres]"`, one URL, the same guarantees graded by the same
suite. Prove it in your own setup with `ctrlrun verify`, which runs the kernel's own failure
scenarios against *your* policy in a scratch store. It reaches no network: the only sockets it
opens are to the store you named and to loopback listeners it bound itself, which is how it
grades the transport classifier.

<!-- generated from capabilities.yaml (readme) — edit the YAML, never this table -->
| Guarantee | `@protect` | Gateway | Adapter |
|---|---|---|---|
| **Approval binding** — An approval is bound to the exact action; a mutated or replayed one is refused. | yes | yes | prevention or attribution, per adapter |
| **One effect, once** — One logical effect happens at most once, across threads, processes and hosts. | yes | yes | yes |
| **Unknown is not failed** — An unknown outcome is AMBIGUOUS, never FAILED, and blocks a blind retry. | yes | yes | yes |
| **Fail closed** — An unknown action, a missing policy or a missing principal is denied. | yes | yes | yes |
| **Authority and delegation** — Every principal needs a grant, delegation cannot widen one, and a grant bounds the total. | yes | yes | yes |
| **Receipts** — Every executed action leaves a portable JSON receipt of who, what and outcome. | yes | yes | yes |
<!-- end generated -->

## The same shape in nine domains

Nothing in ctrlrun knows what a refund is. An action is a **name**, **canonical arguments**, an
**effect key** and a **resource**, and the three questions asked of it are the same whichever
domain it came from: how much autonomy does *this action* get, did a human approve *this exact*
action, and has this effect already happened. Two things carry your domain, and you write both.

- **The effect key is the only domain knowledge in the system.** It is the string that says two
  calls are the same real-world consequence: `refund:{payment_id}`,
  `namespace:{cluster}:{name}`, `grant:{user_id}:{role}`, `prescription:{patient_id}:{drug}`.
  Name it well and a retry cannot act twice; leave it out and there is nothing for *at most
  once* to be about.
- **Conditions are arguments, not amounts.** The language is `<argument>_<op>`, so the same
  operators read `replicas_lte: 10`, `role_in: [reader, viewer]` and `to_domain_eq: acme.com`
  as easily as `amount_lte`. A band is available to a domain that has never issued an invoice.

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

Read any row left to right and it is one rule wearing different nouns. The security row is the
one to read twice: adding a **deny** rule to a firewall is autonomous and adding an **allow**
rule is not, which no amount threshold would have told you. The policy is where your judgement
about your domain gets written down; ctrlrun is what makes it hold.

## Documentation

**[docs.ctrlrun.dev](https://docs.ctrlrun.dev/)** is the documentation: concepts, guides, a cookbook, the
full reference.

| | |
|---|---|
| Start here | [Why](https://docs.ctrlrun.dev/why) · [Protect your first action](https://docs.ctrlrun.dev/get-started/quickstart) |
| The ideas, and doing something with them | [Concepts](https://docs.ctrlrun.dev/concepts/outcomes-and-ambiguous) · [Guides](https://docs.ctrlrun.dev/guides/protect-a-function) · [Cookbook](https://docs.ctrlrun.dev/cookbook/index) |
| Agents and MCP | [Agents you can't modify](https://docs.ctrlrun.dev/agents-you-cant-modify) · [MCP overview](https://docs.ctrlrun.dev/mcp/overview) · [The gateway in five minutes](https://docs.ctrlrun.dev/mcp/gateway-in-5-minutes) · [Approve from your assistant](https://docs.ctrlrun.dev/mcp/approve-from-your-assistant) |
| Running it for real | [Production](https://docs.ctrlrun.dev/production/index) · [Postgres](https://docs.ctrlrun.dev/production/postgres) · [Recovery](https://docs.ctrlrun.dev/production/recovery) · [Operations](https://docs.ctrlrun.dev/production/operations) |
| Every key, flag and error | [Reference](https://docs.ctrlrun.dev/reference/policy-yaml) · [FAQ](https://docs.ctrlrun.dev/faq) |
| Compared with | [Idempotency keys](https://docs.ctrlrun.dev/compare/idempotency-keys) · [Framework human-in-the-loop](https://docs.ctrlrun.dev/compare/framework-hitl) · [Guardrail libraries](https://docs.ctrlrun.dev/compare/guardrail-libraries) · [Durable workflows](https://docs.ctrlrun.dev/compare/durable-workflows) · [Governance toolkits](https://docs.ctrlrun.dev/compare/governance-toolkits) |
| What holds, and what does not | [Threat model](https://docs.ctrlrun.dev/THREAT_MODEL) · [What `verify` proves](https://docs.ctrlrun.dev/verify) · [`CLAIMS.md`](https://docs.ctrlrun.dev/CLAIMS), every sentence mapped to its test · [How this is built](https://docs.ctrlrun.dev/how-this-is-built) |

## Contributing

Issues and pull requests are welcome:
[`CONTRIBUTING.md`](https://github.com/CTRLRun/ctrlrun/blob/main/CONTRIBUTING.md) and
[`CODE_OF_CONDUCT.md`](https://github.com/CTRLRun/ctrlrun/blob/main/CODE_OF_CONDUCT.md) have the
working agreement, and
[`SECURITY.md`](https://github.com/CTRLRun/ctrlrun/blob/main/SECURITY.md) is how to report a
vulnerability. Every claim in this file has a test behind it, so a change to the prose usually
means a change to the suite.
[`CHANGELOG.md`](https://github.com/CTRLRun/ctrlrun/blob/main/CHANGELOG.md) and
[`https://docs.ctrlrun.dev/ROADMAP`](https://docs.ctrlrun.dev/ROADMAP) say
where it is going. Releases carry PyPI provenance attestations from GitHub Actions.

## License

Apache-2.0. The enforcement kernel is and will remain fully open source.

<!-- mcp-name: io.github.CTRLRun/ctrlrun-mcp-operator -->
