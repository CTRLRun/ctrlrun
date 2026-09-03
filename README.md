# CTRLRun

**Transaction safety for AI-agent actions.**

> Agents can retry. Your refund shouldn't.

CTRLRun is open-source infrastructure for controlling consequential AI-agent actions.

You decide, per action, what an agent can do autonomously, what requires human approval, and what is blocked.

CTRLRun binds approvals to the exact action, blocks duplicate execution attempts for the same logical effect, stops blind retries when an execution outcome is uncertain, and records what actually happened.

**Autonomy belongs to the action, not the agent.**

---

> **Status: v0.1 in development.** Nothing is published to PyPI yet. This README describes the v0.1 target exactly as specified in [`docs/SPEC-v0.1.md`](docs/SPEC-v0.1.md). Every claim above maps to a v0.1 primitive; nothing above describes future work. Future work lives in [`VISION.md`](VISION.md) and [`docs/ROADMAP.md`](docs/ROADMAP.md).

---

## The problem

Agents are getting write access to the real world: refunds, emails, deploys, permission grants, record changes. Frameworks already let you approve or deny a tool call. That is not the hard part.

The hard part is what happens at the boundary between *intention* and *effect*:

- A refund commits at Stripe, the response times out, the agent retries. **Did you just refund twice?**
- A human approves a €500 refund. The agent changes it to €5,000 before executing. **Should that approval still count?**
- Two agents pick up the same task and issue the same refund. **Which one wins?**
- A call timed out. Your framework marks it *failed* and retries. **It wasn't failed. It was unknown.**

CTRLRun owns that boundary.

## Quick start (v0.1 target)

```bash
pip install ctrlrun
ctrlrun demo
```

`ctrlrun demo` runs the four failure scenarios below in about a minute, with no external services.

Protect your first action:

```python
import ctrlrun

@ctrlrun.protect("stripe.refund", effect="refund:{payment_id}")
def refund(payment_id: str, amount: int, currency: str = "EUR"):
    return stripe.refunds.create(payment_intent=payment_id, amount=amount)
```

Configure autonomy per action in `ctrlrun.yaml`:

```yaml
actions:
  customer.read:
    decision: allow

  stripe.refund:
    rules:
      - when: { amount_lte: 500 }
        decision: allow
      - when: { amount_lte: 5000 }
        decision: approve
      - decision: deny
```

Now the same agent can refund €100 on its own, must get a human to approve €2,000, and cannot refund €20,000 at all. Unknown actions are denied. CTRLRun fails closed.

## What `ctrlrun demo` shows

**1. Duplicate effect after a lost response**

```
refund €2,000  →  remote commits  →  response lost  →  effect: AMBIGUOUS
agent retries the same refund
✗ BLOCKED — effect may already have committed; blind retry refused
```

**2. Approval mutation**

```
agent proposes refund €500     →  human approves (bound to action hash)
agent executes  refund €5,000  →  ✗ BLOCKED — approved action ≠ requested action
```

**3. Concurrent agents, same effect**

```
Agent A  reserve refund:txn_123  →  ACQUIRED  →  executes
Agent B  reserve refund:txn_123  →  ✗ BLOCKED — already reserved
```

**4. Approval replay**

```
approval apr_9918 used once     →  consumed
same approval presented again   →  ✗ BLOCKED — single-use approval already consumed
```

Every executed action produces a portable JSON receipt: who, what, arguments, decision, approval, effect key, and result (`committed`, `failed`, or `ambiguous`).

## What CTRLRun is not

CTRLRun does not host models, plan, prompt, retrieve, route, remember, or orchestrate. It is not a guardrail library, an IAM system, a workflow engine, or a compliance product. If an agent only reads and answers, you don't need CTRLRun. The moment it can **send, pay, refund, delete, deploy, grant, revoke, approve, submit, purchase, or cancel**, you do.

CTRLRun cannot guarantee exactly-once execution against external systems it doesn't control. It guarantees that it will not *knowingly* execute the same logical effect twice, and that it will never treat an unknown outcome as a failure.

## Documentation

| Doc | Purpose |
|---|---|
| [`docs/SPEC-v0.1.md`](docs/SPEC-v0.1.md) | The v0.1 contract: models, invariants, acceptance tests |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Kernel design and key decisions |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | v0.1 → v1.0 |
| [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md) | What CTRLRun defends against and what it doesn't |
| [`docs/POSITIONING.md`](docs/POSITIONING.md) | Frozen copy, terms to avoid, competitive one-liners |
| [`VISION.md`](VISION.md) | Where this can go — not a build spec |

## License

Apache-2.0. The enforcement kernel is and will remain fully open source.
