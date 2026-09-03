# CTRLRun

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

`ctrlrun demo` runs the four failure scenarios below in well under a second, with no external services.

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

## What `ctrlrun demo` shows

```console
$ ctrlrun demo
CTRLRun demo — four ways an agent action goes wrong, and what stops it.
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

Receipts (7): .ctrlrun/demo/receipts.jsonl
Events:       .ctrlrun/demo/events.jsonl

Read them:    CTRLRUN_STATE=.ctrlrun/demo/state.db ctrlrun receipts
```

Approval ids are generated per run; everything else is byte-for-byte what the demo prints.

Note scenario 1: **`remote refund calls: 1`**. The refund committed at the remote, the response was lost, and the retry was refused — so the customer was refunded once, not twice. Nothing but a human resolving the effect moves it on.

Every executed action produces a portable JSON receipt: who, what, arguments, decision, approval, effect key, and result (`committed`, `failed`, or `ambiguous`).

## What CTRLRun is not

CTRLRun does not host models, plan, prompt, retrieve, route, remember, or orchestrate. It is not a guardrail library, an IAM system, a workflow engine, or a compliance product. If an agent only reads and answers, you don't need CTRLRun. The moment it can **send, pay, refund, delete, deploy, grant, revoke, approve, submit, purchase, or cancel**, you do.

CTRLRun cannot guarantee exactly-once execution against external systems it doesn't control. It guarantees that it will not *knowingly* execute the same logical effect twice, and that it will never treat an unknown outcome as a failure.

## Documentation

| Doc | Purpose |
|---|---|
| [`docs/SPEC-v0.1.md`](https://github.com/CTRLRun/ctrlrun/blob/main/docs/SPEC-v0.1.md) | The v0.1 contract: models, invariants, acceptance tests |
| [`docs/ARCHITECTURE.md`](https://github.com/CTRLRun/ctrlrun/blob/main/docs/ARCHITECTURE.md) | Kernel design and key decisions |
| [`docs/ROADMAP.md`](https://github.com/CTRLRun/ctrlrun/blob/main/docs/ROADMAP.md) | v0.1 → v1.0 |
| [`docs/THREAT_MODEL.md`](https://github.com/CTRLRun/ctrlrun/blob/main/docs/THREAT_MODEL.md) | What CTRLRun defends against and what it doesn't |
| [`docs/CLAIMS.md`](https://github.com/CTRLRun/ctrlrun/blob/main/docs/CLAIMS.md) | Every claim above, mapped to the code and the test that proves it |
| [`SECURITY.md`](https://github.com/CTRLRun/ctrlrun/blob/main/SECURITY.md) | Reporting a vulnerability |
| [`VISION.md`](https://github.com/CTRLRun/ctrlrun/blob/main/VISION.md) | Where this can go — not a build spec |

## License

Apache-2.0. The enforcement kernel is and will remain fully open source.
