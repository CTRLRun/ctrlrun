# ctrlrun-openai-agents

Route a CTRLRun `APPROVE` through the **OpenAI Agents SDK's own tool-approval interruption**, so
the human answers where this SDK's users already answer.

- **Supported kernel range:** `ctrlrun>=0.5,<0.6`
- **Supported framework range:** `openai-agents>=0.20,<1.0`
- **Primitive reused:** [`needs_approval`, `RunResult.interruptions`, `RunState.approve` / `reject`](https://openai.github.io/openai-agents-python/tools/). Read 2026-09-05.
- **Framework shape:** decided before invocation (SPEC-v0.5 §3.5).
- **Conformance:** `4/4 (2 not applicable)` — `binding` and `denial` are N/A, with the reasons below. **Never reported as 6/6.**

## You probably do not need this

`@protect` already covers anything running in your process — including a plain `@function_tool`
body — with no adapter and no framework support. **Most people reading this need `@protect` and
nothing else.** This buys one thing: when the policy says a human must approve, the SDK stops the
run with a `ToolApprovalItem` instead of `ApprovalRequired` being raised past the runner.

`ctrlrun gateway` is the third way in, and it is not an adapter: it puts the same guarantees in
front of an MCP tool server, in any language, with no agent change.

## Use

```python
from ctrlrun import Control, InterruptApprovalProvider, protect
import ctrlrun_openai_agents as gate
from ctrlrun_openai_agents import AgentsInterrupt, protected_tool

control = Control(
    policy, store,
    approvals=InterruptApprovalProvider(store, AgentsInterrupt()),
    identity=..., authority=...,
)

@protect("stripe.refund", effect="refund:{payment_id}", wait=True, control=control)
def issue_refund(payment_id: str, amount: int) -> str:
    return stripe.Refund.create(payment_intent=payment_id, amount=amount)

async def refund_tool(payment_id: str, amount: int) -> str:
    """Issue a refund for a payment. Amounts are in integer minor units."""
    return issue_refund(payment_id=payment_id, amount=amount)

agent = Agent(name="refunds", tools=[protected_tool(control, "stripe.refund", refund_tool)])

result = await gate.run(agent, "refund txn_1")
if result.interruptions:
    state = result.to_state()
    for item in result.interruptions:
        state.approve(item)          # or state.reject(item)
    result = await gate.run(agent, state)
```

**The operator constructs the `Control`** — this adapter never does (SPEC-v0.5 §2.3), so the
identity provider, the authority document, the environment and the mode are all chosen on the
line above, by the person deploying it.

### Two helpers, and why they are not optional

**`protected_tool(...)`** builds the `function_tool` with `needs_approval=` wired to the policy
**and `failure_error_function=None`**. This SDK's default is `default_tool_error_function`, which
catches a tool's exception and returns *"An error occurred while running the tool. Please try
again."* **to the model**. Under that default an `ActionDenied`, a `DuplicateEffect` or an
`AmbiguousEffect` reaches your agent as a suggestion to retry — which is the exact failure
`SPEC-v0.2 §6.10` argues about in the gateway: a refusal by CTRLRun is not an outcome of the
tool, it is the statement that the tool did not run, and putting it in a channel whose contents
reach the model as text invites the retry the refusal exists to prevent.

**`gate.run(...)` / `gate.run_sync(...)`** are `Runner.run` with CTRLRun's exceptions arriving as
themselves. The SDK wraps whatever a tool raises in `agents.exceptions.UserError` and chains the
original as `__cause__`, so a plain `except DuplicateEffect` at your call site never fires. These
walk the chain and give it back; they decide nothing and hold nothing. `unwrap(error)` is the
same thing if you would rather call `Runner` yourself.

## The binding: this adapter's is **attribution**

`carries_approved_arguments` is `False`, and unlike the LangGraph adapter it is **not a
constructor argument** — it is a fact about this SDK rather than a choice a deployment makes.

The arguments a human answered against live on the `ToolApprovalItem`, which the *caller* holds
in `RunResult.interruptions`. They are not reachable from a tool body: the run context records
that a call was approved, keyed by tool name and `call_id`, and not what its arguments were. An
adapter that handed back the tool's own parameters would be handing back what it was just given,
which SPEC-v0.5 §3.4 names as manufacturing the check.

So CTRLRun still binds the approval to the action that executes — that is `v0.1 §4.2 A1` and it
holds unconditionally — but **the binding across the interrupt is the SDK's, not CTRLRun's**. In
that word: *attribution*. The conformance kit reports `binding: not_applicable` with the reason,
never a pass.

**What closes the gap instead is real, and it is the SDK's.** The approval item and the
invocation are the **same tool call**, bound by `call_id`, and the SDK invokes with exactly that
call's arguments — it does not re-ask the model in between. That is a strong property. It is
simply not one CTRLRun can verify, which is the whole distinction §3.4 draws.

## A rejection leaves no CTRLRun evidence

The one place this adapter's evidence differs from `@protect`'s, and worth knowing before you go
looking for an empty log.

The SDK does **not invoke** a tool whose approval was refused. So no CTRLRun action is proposed:
there is no `APPROVAL_DENIED`, no `ACTION_DENIED` and **no receipt**. The refusal is real and it
is in the SDK's own run output; CTRLRun was never asked about it. The conformance kit reports
`denial: not_applicable` for the same reason.

If you need refusals in the evidence log, record them where you call `state.reject(item)`.

## Where this SDK's behaviour shows through the contract

SPEC-v0.5 §7 item 5.

**The predicate and `@protect` can disagree.** `approval_gate` answers the SDK's pre-invocation
question with `ctrlrun.adapter.needs_approval`, which sees the framework's raw arguments — not
the defaults `@protect` applies, and not a `resource=` template declared only on the decorator.

A wrong `True` asks a human about something harmless. A wrong `False` means the SDK does not
pre-ask, `Control.execute` raises `ApprovalRequired`, and the interrupt finds the SDK holds no
answer for a call it was never asked about — so **the action is refused** with
`ApprovalNotAsked`, nothing is written, and the approval request is left `pending` for
`ctrlrun approve` to answer out of band. In neither direction does an action execute that a
human did not approve.

That sentence is load-bearing and it was not always true here. `AgentsInterrupt.interrupt()`
originally returned `granted=True` unconditionally, reasoning that a tool body which runs *is*
the approval. It is — but only for a call the SDK's gate actually asked about, and on the wrong
`False` path it had not. An independent review found it: a $1,000 refund executing with no human
and a receipt naming `openai-agents:tool-approval` as the approver, which is a grant nobody made
written into the evidence log. `interrupt()` now reads
`RunContextWrapper.is_tool_approved(tool_name, call_id)` — `True`, `False`, or `None` for a call
nobody was asked about — and only the first two are answers.

**So `@protect(wait=True)` on this `Control` must go through `protected_tool`.** A plain
`function_tool`, a background job, or any other protected call on the same `Control` reaches the
interrupt with no SDK tool call in scope, and is refused the same way. The provider hangs off the
`Control` and not off the tool; nothing else links the two.

Pass the same `resource=` to `protected_tool`, give the tool no defaulted parameters, and the
two agree — and then no call is refused this way at all.

**Exceptions are wrapped**, and `failure_error_function` swallows them by default. See above;
this is the one thing an adapter for this SDK cannot leave alone.

**Retries.** The SDK's default handling of a tool that raised surfaces the error to the model,
which may act on it. Measured on `openai-agents` 0.22.0 against a remote that commits and then
drops the connection, with no effect-level guard, the model retried until the refund had landed
**three or four times in a single run**, five runs out of five
(`research/framework-probe/results/2026-09-05.json`). That is behaviour, not quality — it is what
the documentation says `failure_error_function` does — and it is the clearest argument for
declaring an `effect=` on anything consequential.

## What this adapter does not do

It is **not a second approval path**: it reuses the SDK's own approval interruption and
reimplements nothing — no prompt, no queue, no polling loop, no resume token of its own. It
**grants nothing**: the answer is recorded by `InterruptApprovalProvider`, in core, through the
same two store calls `ctrlrun approve` makes. It **constructs no `Control`** and **supplies no
principal**.

And it is **not a compliance claim**. "Conformance" names a suite of the CTRLRun repository's own
acceptance tests, run against this adapter. It certifies nothing.

## Versioning

`adapters-openai-agents-MAJOR.MINOR`, never a kernel version. This adapter answers to two
upstreams and neither is the CTRLRun roadmap. The two ranges at the top are what its CI actually
ran against.
