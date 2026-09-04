# ctrlrun-langgraph

Route a CTRLRun `APPROVE` through **LangGraph's own `interrupt()`**, so the human answers where
your LangGraph users already answer.

- **Supported kernel range:** `ctrlrun>=0.5,<0.6`
- **Supported framework range:** `langgraph>=1.0,<2.0`
- **Primitive reused:** [`interrupt()` and `Command(resume=...)`](https://langchain-ai.github.io/langgraph/how-tos/human_in_the_loop/add-human-in-the-loop/), with a checkpointer. Read 2026-09-05.
- **Framework shape:** resumed in place (SPEC-v0.5 §3.5).
- **Conformance:** `6/6`, every suite, with `carries_approved_arguments=True`.

## You probably do not need this

`@protect` already covers anything running in your process — a LangChain tool, a raw model call,
a plain function — with no adapter and no framework support at all. **Most people reading this
need `@protect` and nothing else.**

This buys exactly one thing over it: when the policy says a human must approve, the request goes
out through LangGraph's interrupt instead of `ApprovalRequired` being raised past your graph. If
your deployment has nowhere for a human to answer, or you are happy handling `ApprovalRequired`
in your own code, stop here.

There is a third way in that is not an adapter at all: `ctrlrun gateway` puts the same guarantees
in front of an MCP tool server, in any language, with no agent change.

## Install

```console
$ pip install ctrlrun-langgraph
```

## Use

The **operator** wires it, on the line where the policy and the store are chosen. This adapter
never constructs a `Control` (SPEC-v0.5 §2.3), so everything it must not choose — the identity
provider, the authority document, the environment, the mode — is chosen by the person deploying
it, in the file they already look at.

```python
from ctrlrun import Control, InterruptApprovalProvider, protect
from ctrlrun_langgraph import LangGraphInterrupt

control = Control(
    policy, store,
    approvals=InterruptApprovalProvider(
        store, LangGraphInterrupt(carries_approved_arguments=True)
    ),
    identity=..., authority=...,
)

@protect("stripe.refund", effect="refund:{payment_id}", wait=True, control=control)
def issue_refund(payment_id: str, amount: int) -> str:
    return stripe.Refund.create(payment_intent=payment_id, amount=amount)
```

`wait=True` is what routes the `APPROVE` through the provider — and therefore through
`interrupt()` — instead of raising past your graph. It is the entire difference this adapter
makes.

Call `issue_refund` from a node, on a graph compiled with a checkpointer:

```python
graph = builder.compile(checkpointer=InMemorySaver())
config = {"configurable": {"thread_id": "..."}}

result = graph.invoke({"payment_id": "txn_1", "amount": 2000}, config)
if "__interrupt__" in result:
    pending = graph.get_state(config).tasks[0].interrupts[0].value
    # `pending` is JSON: the action, its arguments, the resource, the principal, the hash and
    # the request's expiry. Put it in front of a human however you already do.
    graph.invoke(
        Command(resume={
            "approved": True,
            "approver": "ada@example.com",
            "arguments": pending["arguments"],   # what they answered against
        }),
        config,
    )
```

### What you may send back

```
Command(resume=True)                       # granted, approver "langgraph:interrupt"
Command(resume=False)                      # refused
Command(resume={"approved": True,
                "approver": "ada@example.com",
                "arguments": {...}})       # the arguments the human answered against
```

`approved` must be `True` or `False`. A truthy string is not a yes, and is refused with a message
that names your resume value. This is a payload shape for LangGraph's own resumption channel, not
a token: nothing is minted, nothing is stored, and there is no id here this adapter invented.

## The binding: prevention or attribution

`carries_approved_arguments` has **no default**, because the default somebody assumes is the one
that does not check.

**`True` — prevention.** Your resume value must carry `arguments`, and CTRLRun rebuilds the
proposal with them and compares the action hash. An answer given against €5 that arrives for a
€5,000 action is refused with `ApprovalMismatch`, the approval is left grantable, and nothing
runs. This is the setting the conformance results above were produced with, and it is right for
almost every deployment: your console already knows what it showed the human.

**`False` — attribution.** You send back only a verdict. CTRLRun still binds the approval to the
action that executes — that is `v0.1 §4.2 A1` and it holds unconditionally — but **the binding
across the interrupt is LangGraph's checkpoint, not CTRLRun's hash**. If the checkpoint replayed a
different call than the one a human read, evidence will show it afterwards; nothing refuses it
beforehand. That is *attribution*, in that word, and the conformance kit reports
`binding: not_applicable` with the reason rather than a pass. Choose it only if your console
genuinely cannot echo what it displayed.

## Where LangGraph's behaviour shows through the contract

SPEC-v0.5 §7 item 5 asks every adapter to record this, and for a resumed-in-place framework there
are three (§3.2.1).

**The node runs twice, so the primitive is reached twice.** LangGraph replays the node from the
checkpoint, so `@protect` builds a **new `Action` with a new `action_id`** and creates a **new
approval request** on the resumed pass. `interrupt()` is therefore called once to ask and once to
receive. None of that is a defect and none of it is unsafe — the resumed pass re-runs principal
expiry, authority and policy at resumption time, so an authority revoked while the human
deliberated refuses the action then.

**`action_id` is not continuous.** The `action_id` in the payload a human saw is the first pass's;
the one on the receipt is the second's. Both are in the event log under their own
`ACTION_PROPOSED` and `APPROVAL_REQUESTED`, and correlating them is a reader's work.
**`action_hash` *is* continuous**, because `action_id` is excluded from the canonical form — which
is why the binding check above is about content and never about an id.

**The first pass's request is orphaned.** It stays `pending` and grantable by `ctrlrun approve`
for its full TTL, for the same `action_hash`. Not a hole — an approval is single-use and
hash-bound and is consumed atomically with the reservation — but an operator watching a queue
will see two requests for one refund.

**The approval TTL does not bound the human's deliberation.** They answer against the first
pass's request; the grant lands on the second pass's, created after they answered. What bounds
the interval is your checkpoint, which may hold it for a month. If that matters to you, expire
the thread.

**Retries.** LangGraph's retry is explicit and opt-in — a node takes a `RetryPolicy` — and this
adapter attaches none. Measured on `langgraph` 1.2.11 against a remote that commits and then
drops the connection, the prebuilt agent surfaced the failure and stopped: one effect, one
request, five runs out of five (`research/framework-probe/results/2026-09-05.json`). That is
behaviour, not quality, and it is not a promise about your graph.

## What this adapter does not do

It is **not a second approval path**: it reuses `interrupt()` and reimplements nothing — no
prompt, no queue, no polling loop, no resume token of its own. It **grants nothing**: the answer
it returns is recorded by `InterruptApprovalProvider`, in core, through the same two store calls
`ctrlrun approve` makes. It **constructs no `Control`** and **supplies no principal** — an
adapter sees one and never supplies one.

And it is **not a compliance claim**. "Conformance" here names a suite of the CTRLRun
repository's own acceptance tests, run against this adapter. It certifies nothing.

## Versioning

`adapters-langgraph-MAJOR.MINOR`, never a kernel version. This adapter answers to two upstreams
and neither is the CTRLRun roadmap: it breaks when LangGraph makes a breaking release, on that
project's schedule. Its major version tracks whichever of the two forced the break, and the two
ranges at the top are what its CI actually ran against.
