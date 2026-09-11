<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/CTRLRun/ctrlrun/main/docs/assets/wordmark-dark.svg">
    <img src="https://raw.githubusercontent.com/CTRLRun/ctrlrun/main/docs/assets/wordmark-light.svg" alt="CTRLRun" width="300">
  </picture>
</p>

<p align="center">
  <strong>Execution safety for AI agents.</strong><br>
  The model guesses. CTRLRun does not.<br>
  <br>
  A Python library that sits between the decision to act and the call that acts.<br>
  A consequential action happens at most once, exactly as approved, and leaves a receipt.<br>
  When the outcome is unknown, CTRLRun says so instead of guessing.<br>
  <br>
  Runs in production on a single file, or on Postgres across hosts. Apache-2.0.
</p>

<!-- generated from tools/docs_audit/render_badges.py (readme) — edit the list, not this -->
<p align="center">
  <a href="https://github.com/CTRLRun/ctrlrun/blob/badges/clones-history.json"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/CTRLRun/ctrlrun/badges/clones-badge.json" alt="Clones"></a>
  <a href="https://pypi.org/project/ctrlrun/"><img src="https://img.shields.io/pypi/v/ctrlrun?color=B8730A&label=pypi" alt="PyPI"></a>
  <a href="https://pypistats.org/packages/ctrlrun"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/CTRLRun/ctrlrun/badges/downloads-badge.json" alt="Downloads"></a>
  <a href="https://ctrlrun.dev"><img src="https://img.shields.io/badge/docs-ctrlrun.dev-B8730A" alt="Docs"></a>
  <a href="https://github.com/CTRLRun/ctrlrun/actions/workflows/ci.yml"><img src="https://github.com/CTRLRun/ctrlrun/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
  <a href="https://github.com/CTRLRun/ctrlrun/actions/workflows/codeql.yml"><img src="https://github.com/CTRLRun/ctrlrun/actions/workflows/codeql.yml/badge.svg?branch=main" alt="CodeQL"></a>
  <a href="https://github.com/CTRLRun/ctrlrun/actions/workflows/fuzz.yml"><img src="https://github.com/CTRLRun/ctrlrun/actions/workflows/fuzz.yml/badge.svg?branch=main" alt="Fuzz"></a>
  <a href="https://ctrlrun.dev/docs/how-this-is-built"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/CTRLRun/ctrlrun/badges/tests-badge.json" alt="Tests"></a>
  <a href="https://ctrlrun.dev/docs/security/verify-guarantees"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/CTRLRun/ctrlrun/badges/verify-badge.json" alt="CTRLRun verified"></a>
  <a href="https://scorecard.dev/viewer/?uri=github.com/CTRLRun/ctrlrun"><img src="https://api.scorecard.dev/projects/github.com/CTRLRun/ctrlrun/badge" alt="OpenSSF Scorecard"></a>
  <a href="https://github.com/CTRLRun/ctrlrun/blob/main/LICENSE"><img src="https://img.shields.io/pypi/l/ctrlrun?color=B8730A" alt="License"></a>
</p>
<!-- end generated -->

<p align="center">
  <img src="https://raw.githubusercontent.com/CTRLRun/ctrlrun/main/docs/assets/demo.gif" alt="ctrlrun demo: a refund commits at the remote, the response is lost, the agent retries, and the retry is refused, remote refund calls: 1. Then a human approves a €2,000 refund, the agent executes €5,000, and that is refused too." width="800">
</p>

```bash
pip install ctrlrun && ctrlrun demo
```

## What it does

A ticket asks for a €500 refund. The agent calls the refund tool with €5,000, one extra zero.
The tool is in its list, the arguments are well formed, and the model is completely confident.
Nothing above the call disagrees, because nothing above the call is a check: a tool being
callable is not permission to call it with those arguments.

CTRLRun is that check. It reads the arguments about to leave your process and answers what may
happen to them. Four rules do the work, and each one is a test in this repository before it is
a sentence here.

| | |
|---|---|
| **Exact means exact** | Changed arguments need a new approval. |
| **Once stays once** | Same effect key, shared store, no repeat. |
| **Unknown means wait** | Confirm the outcome before retrying. |
| **Every answer is kept** | Requests, decisions and results, refusals included. |

The third one is the half people forget. A correct €500 refund commits at the provider and the
reply is lost coming back, so the agent retries. Retry libraries, agent frameworks and tool
loops collapse *this failed* into *I do not know what happened*. CTRLRun keeps them apart: a
lost reply is `AMBIGUOUS`, never `FAILED`, and a retry against an `AMBIGUOUS` effect is refused
until a human, or a `reconcile` hook, says what happened.

<details>
<summary>What <code>ctrlrun demo</code> shows: five failures and five refusals, byte for byte</summary>

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
prints, and a test fails if the two drift apart. No network, no external service, under a
second. [Run it in your browser](https://ctrlrun.dev/docs/try-it) with nothing installed.

</details>

**Where it stops.** It does not detect prompt injection: it contains the consequence rather
than reading the cause. It cannot promise exactly-once against a remote it does not control, it
refuses to *knowingly* act twice, and it rolls nothing back. Receipts are chained, so an alteration
is detected. They are not signed: alteration is not authorship. The badge above means the
**declared guarantees pass** in the setup they ran against, and it does not mean secure, safe,
compliant, certified or audited:
[what the badge means](https://ctrlrun.dev/docs/verify#what-the-badge-means)
· [`OWASP-AGENTIC-TOP10.md`](https://ctrlrun.dev/docs/OWASP-AGENTIC-TOP10)
names the four entries this does not address.

If an agent only reads and answers, you do not need CTRLRun. The moment it can **send, pay,
refund, delete, deploy, grant, revoke, approve, submit, purchase or cancel**, you do.

## Use it in three steps

**1. Install it.**

```bash
pip install ctrlrun
```

**2. Write down what the agent may do.** One file, `ctrlrun.yaml`. Cheap to undo is autonomous,
handing out power needs a human, destroying the evidence is not an agent action at any size.
Anything not listed is denied; there is no default-allow.

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

**3. Wrap the call that has the consequence**, and name the effect it has in the world.

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

A human answers from the shell, and the grant names the exact action hash it authorizes:

```bash runnable
ctrlrun approve "$(cat request_id.txt)"
```

Present it with `ctrlrun.with_approval(request_id)` and the agent gets that action and nothing
next to it: the same approval spent on `owner` instead of `admin` raises `ApprovalMismatch`.
Every attempt, refusals included, leaves a receipt you can read with `ctrlrun receipts`.

That is the whole integration: a policy file, a decorator, a context, and `with_approval` to
present a grant. Everything else is in the documentation.
[Protect your first action](https://ctrlrun.dev/docs/get-started/quickstart) walks the same path
with every output explained ·
[Policy YAML reference](https://ctrlrun.dev/docs/reference/policy-yaml) ·
[Cookbook](https://ctrlrun.dev/docs/cookbook/index): refunds, deploys, IAM, deletions, email, MCP.

## How it works

Every protected call, whichever way it arrives, goes through the same six steps.

```text
  normalize  →  decide  →  approve  →  reserve  →  execute  →  record
```

1. **Normalize.** The call becomes an `Action`: a name, canonical arguments (sorted keys, no
   floats), a resource, the principal. Its SHA-256 is the action hash.
2. **Decide.** Authority first (may *this principal* propose this at all?), then policy (how
   much autonomy does *this action* get?). Unknown action, missing policy or missing principal
   is `deny`.
3. **Approve.** A human answers against the action hash. The approval is single-use, expires,
   and matches nothing but that exact action.
4. **Reserve.** The effect key, `refund:txn_1` or `namespace:prod-eu:checkout`, is taken in one
   atomic write. A second caller, in another process or on another host, is refused.
5. **Execute.** Your function runs. Only `NotExecuted`, raised by you, means `FAILED`; every
   other exception and every timeout means `AMBIGUOUS`.
6. **Record.** A portable JSON receipt: who, what, decision, approval, effect key, outcome, and
   the hash of the policy that decided it, chained to the receipt before it.

State lives in SQLite by default, a file with no server and no ops, and the reservation holds
across processes rather than merely across threads. Point it at Postgres when more than one
host writes: `pip install "ctrlrun[postgres]"`, one URL, the same guarantees graded by the same
suite. Prove it in your own setup with `ctrlrun verify`, which runs the kernel's own failure
scenarios against *your* policy in a scratch store, with no network.

<!-- generated from capabilities.yaml (readme) — edit the YAML, never this table -->
| Guarantee | `@protect` | Gateway | Adapter |
|---|---|---|---|
| **Approval binding** — An approval is bound to the exact action; a mutated or replayed one is refused. | yes | yes | prevention or attribution, per adapter |
| **One effect, once** — One logical effect happens at most once, across threads, processes and hosts. | yes | yes | yes |
| **Unknown is not failed** — An unknown outcome is AMBIGUOUS, never FAILED, and blocks a blind retry. | yes | yes | yes |
| **Fail closed** — An unknown action, a missing policy or a missing principal is denied. | yes | yes | yes |
| **Authority and delegation** — With authority on, every principal needs a grant, and delegation cannot widen one. | yes | yes | yes |
| **Receipts** — Every executed action leaves a portable JSON receipt of who, what and outcome. | yes | yes | yes |
<!-- end generated -->

### Three ways to use it

**You probably do not need an adapter.** `@protect` covers anything running in this process: a
raw model call, a LangChain tool, a hand-rolled loop, a cron job. The gateway covers anything
that reaches its tools over MCP, in any language.

| You have | Use | Needs |
|---|---|---|
| Python in this process | the `@protect` decorator, shown above | nothing beyond `pip install ctrlrun` |
| Tools behind an MCP server, in any language | the gateway: `pip install "ctrlrun[gateway]"` | one command, no change to agent or server code |
| A framework with its own approval interrupt | an adapter | the framework to have a human-in-the-loop primitive |

An adapter exists for one reason: to route an `approve` decision through the framework's own
interrupt, so a human answers where they already answer. There is never a second place to say
yes. [`ctrlrun-langgraph`](https://github.com/CTRLRun/ctrlrun/blob/main/adapters/langgraph/README.md)
gives **prevention**, because the resumption carries the arguments and core re-checks them
against the hash.
[`ctrlrun-openai-agents`](https://github.com/CTRLRun/ctrlrun/blob/main/adapters/openai-agents/README.md)
gives **attribution**, because that SDK records *that* a call was approved and not what its
arguments were. None of the three is only for agents: a worker, a webhook handler and a
scheduled job cannot tell a first attempt from a retry either.

## The same shape in nine domains

Nothing in CTRLRun knows what a refund is. An action is a **name**, **canonical arguments**, an
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
about your domain gets written down; CTRLRun is what makes it hold.

## Documentation

**[ctrlrun.dev](https://ctrlrun.dev)** is the documentation: concepts, guides, a cookbook, the
full reference, and a browser demo that runs with no install.

| | |
|---|---|
| Start here | [Why](https://ctrlrun.dev/docs/why) · [Protect your first action](https://ctrlrun.dev/docs/get-started/quickstart) · [Try it in your browser](https://ctrlrun.dev/docs/try-it) |
| The ideas, and doing something with them | [Concepts](https://ctrlrun.dev/docs/concepts/outcomes-and-ambiguous) · [Guides](https://ctrlrun.dev/docs/guides/protect-a-function) · [Cookbook](https://ctrlrun.dev/docs/cookbook/index) |
| MCP | [Overview](https://ctrlrun.dev/docs/mcp/overview) · [The gateway in five minutes](https://ctrlrun.dev/docs/mcp/gateway-in-5-minutes) |
| Running it for real | [Production](https://ctrlrun.dev/docs/production/index) · [Postgres](https://ctrlrun.dev/docs/production/postgres) · [Recovery](https://ctrlrun.dev/docs/production/recovery) · [Operations](https://ctrlrun.dev/docs/production/operations) |
| Every key, flag and error | [Reference](https://ctrlrun.dev/docs/reference/policy-yaml) · [FAQ](https://ctrlrun.dev/docs/faq) |
| What holds, and what does not | [Threat model](https://ctrlrun.dev/docs/THREAT_MODEL) · [What `verify` proves](https://ctrlrun.dev/docs/verify) · [`CLAIMS.md`](https://ctrlrun.dev/docs/CLAIMS), every sentence mapped to its test · [How this is built](https://ctrlrun.dev/docs/how-this-is-built) |

## Contributing

Issues and pull requests are welcome:
[`CONTRIBUTING.md`](https://github.com/CTRLRun/ctrlrun/blob/main/CONTRIBUTING.md) and
[`CODE_OF_CONDUCT.md`](https://github.com/CTRLRun/ctrlrun/blob/main/CODE_OF_CONDUCT.md) have the
working agreement, and
[`SECURITY.md`](https://github.com/CTRLRun/ctrlrun/blob/main/SECURITY.md) is how to report a
vulnerability. Every claim in this file has a test behind it, so a change to the prose usually
means a change to the suite.
[`CHANGELOG.md`](https://github.com/CTRLRun/ctrlrun/blob/main/CHANGELOG.md) and
[`https://ctrlrun.dev/docs/ROADMAP`](https://ctrlrun.dev/docs/ROADMAP) say
where it is going. Releases carry PyPI provenance attestations from GitHub Actions.

## License

Apache-2.0. The enforcement kernel is and will remain fully open source.
