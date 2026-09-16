# ctrlrun-langchain

Gate a LangChain agent's tool calls with a ctrlrun policy, through **LangChain's own
`wrap_tool_call`** middleware hook.

- **Supported kernel range:** `ctrlrun>=0.12,<0.13`
- **Supported framework range:** `langchain>=1.0,<2.0`
- **Primitive reused:** [`AgentMiddleware.wrap_tool_call`](https://docs.langchain.com/oss/langchain/middleware/custom), whose contract is *"Intercept execution and control when the handler is called. You decide if the handler is called zero times (short-circuit), once (normal flow), or multiple times."* Read 2026-09-16.
- **Framework shape:** the framework hands over the call itself.

## This is not the LangGraph adapter

`ctrlrun-langgraph` routes an `APPROVE` through `interrupt()`, reusing a human-in-the-loop
primitive. This is a different thing on a different surface: LangChain's middleware gives the
tool call itself to the middleware, so `handler` **is** the executor.

That closes the gap every observation-hook integration lives with. There is no separate outcome
report to arrive late, be swallowed, or never fire. What the tool did is what `handler` returned
or raised, in the same stack frame, and the receipt says so.

Three consequences, which are the reason to use this over a log-and-hope callback:

- **A denial never reaches the tool.** `handler` is not called, and the model gets a
  `ToolMessage` saying the call was refused and which rule refused it.
- **Once stays once.** The effect is reserved before `handler` runs and committed from its
  return, so two agents sharing a store cannot both execute the same effect key.
- **An unknown outcome stays unknown.** Anything `handler` raises that is not `NotExecuted`
  leaves the effect `AMBIGUOUS`, and the next attempt is refused until a human resolves it,
  rather than being retried into a double charge.

## You may not need this

`@protect` already covers any Python callable, including a LangChain tool, with no middleware
and no framework support at all. This buys one thing over it: the gate applies to **every** tool
the agent can reach, including tools you did not write and cannot decorate.

There is a third way in that is not an adapter at all: `ctrlrun gateway` puts the same
guarantees in front of an MCP tool server, in any language, with no agent change.

## Install

```console
$ pip install ctrlrun-langchain
```

## Use

The **operator** wires it, on the line where the policy, the store and the identity provider are
chosen. This middleware never constructs a `Control` (SPEC-v0.5 §2.3), so everything it must not
decide — the identity provider, the authority document, the environment, the mode — is chosen by
the person deploying it.

```python
from langchain.agents import create_agent
from ctrlrun import Control
from ctrlrun_langchain import CTRLRunMiddleware

control = Control.from_file("ctrlrun.yaml")

agent = create_agent(
    model="gpt-5.5",
    tools=[lookup, issue_refund],
    middleware=[CTRLRunMiddleware(control)],
)
```

With a policy that says refunds up to €50 are autonomous and the rest are denied:

```yaml
schema: ctrlrun.policy/v2
actions:
  lookup:
    decision: allow
  issue_refund:
    effect: "refund:{payment_id}"
    rules:
      - when: { amount_gte: 0, amount_lte: 5000 }
        decision: allow
      - decision: deny
```

the agent's own tool calls are decided before they run:

```text
lookup                              the tool runs
issue_refund  amount=900000         ctrlrun refused this call: rule[1]. The tool did not run.
rm_rf                               ctrlrun refused this call: unknown_action. The tool did not run.
issue_refund  amount=1000           the tool runs
issue_refund  amount=1000  (again)  ctrlrun refused this call: this effect is already committed
```

Nothing is default-allow: a tool the policy does not name is refused, which is why `rm_rf` above
never reaches `handler`.

**Every protected call needs a principal.** In production that is an identity provider that
verifies a credential; in development it is `with ctrlrun.context(agent="support-agent"):`
around the agent invocation. Without one the action is denied before the policy is consulted:

```text
ActionDenied: lookup: no principal is available; wrap the call in
'with ctrlrun.context(agent=...)', or install an identity provider that answers
```

That is fail-closed and deliberate: who is acting is an authorization input, and a library that
accepted a self-asserted principal would be accepting the agent's word for its own authority.

## Approvals

Where the policy says `approve`, this middleware refuses the call and tells the model the
request id, rather than blocking the agent while a human deliberates:

```text
ctrlrun is holding this call for a human. Approve it with 'ctrlrun approve apr_...',
then ask again. The tool did not run.
```

If you want the human answered *inside* the run instead, that is what `ctrlrun-langgraph` is
for: LangGraph's `interrupt()` suspends the graph, and the resumed run re-presents the same
proposal under the granted approval.

## What this does not do

- It does not decide anything. The policy does, and the policy is the operator's file.
- It does not grant approvals. `InterruptApprovalProvider`, `ctrlrun approve` and the webhook
  are the only places a grant is written, and this is not one of them.
- It does not supply a principal, and it never reads one from agent state.

Apache-2.0, same as the kernel.
