# Adapters

## You probably do not need one

There are **three ways to put CTRLRun in front of a consequential action**, and only one of them
is an adapter.

| | Covers | Needs |
|---|---|---|
| **`@protect`** | Anything running in this process — a raw OpenAI call, a LangChain tool, a hand-rolled loop, a cron job | Nothing. A decorator. |
| **The MCP gateway** | Anything reaching its tools over MCP, in any language | `pip install "ctrlrun[gateway]"` |
| **An adapter** | Routing an `approve` decision through the framework's **own interrupt** instead of raising past it | The framework to have a human-in-the-loop primitive |

An adapter exists for exactly one reason: so that a human answers where they already answer. It
buys nothing else. **A framework with no HITL primitive has nothing for an adapter to reuse and
does not need one** — `@protect` already covers it, and an adapter would be inventing a second
approval path, which is the one thing this contract forbids outright.

That is the answer to *"what about framework X?"* for every X, and it is why this list is short
rather than growing by one each time a framework is named.

## What ships

| Distribution | Framework | Shape | Reuses | Binding |
|---|---|---|---|---|
| `ctrlrun-langgraph` | LangGraph | resumed in place | `interrupt()` + `Command(resume=...)`, and the checkpointer | **prevention** |
| `ctrlrun-openai-agents` | OpenAI Agents SDK | decided before invocation | the tool-approval interruption | **attribution** |

Each has its own README with the two supported ranges, the primitive it reuses with a link and
the date read, and every place its framework's behaviour shows through the contract.

**Prevention or attribution** is the sentence to read first. `carries_approved_arguments = True`
means the framework's resumption carries the arguments a human answered against, and core
re-checks them against the proposal's `action_hash` — a mutated action is *refused*.
`False` means the framework carries nothing an adapter can inspect: CTRLRun records **who
answered** and cannot re-check **what they answered about**. Neither is a defect; they are
different frameworks. An adapter that blurred the two would be the false-green problem in prose.

## Versioning

Adapters ship on their own version line — `adapters-langgraph-1.0`, never `0.5.1`. An adapter
answers to two upstreams and neither is the kernel's roadmap: it breaks when its framework makes
a breaking release, on that project's schedule. Each README states a supported kernel range and
a supported framework range, and the major version tracks whichever forced the break.

An adapter gates no kernel release, and a kernel release does not re-cut adapters.

## Writing one

`docs/SPEC-v0.5.md` is the contract. The short version:

1. **Implement `FrameworkInterrupt`** — a `framework` name, `carries_approved_arguments`, and
   `interrupt(pending) -> ApprovalAnswer`. It is a Protocol; do not inherit from it.
2. **The operator wires it.** They build the `Control` with
   `approvals=InterruptApprovalProvider(store, YourInterrupt())` and hand it over. An adapter
   never constructs a `Control`, never constructs the provider, and never supplies a principal —
   §2.3 has the whole never-list with a reason on every row.
3. **Return the answer; write nothing.** `InterruptApprovalProvider` records it, through the same
   `grant_approval` / `deny_approval` calls `ctrlrun approve` makes. This is what makes "never a
   second approval path" structural rather than advisory.
4. **Run the kit.** `from ctrlrun.conformance import run; assert run(adapter).ok` inside your own
   pytest. It is core — nothing extra to install. It is **not** a certification and passing it is
   not a claim about quality: it answers one question, *does an action driven through this
   adapter get the same refusals as one driven through `@protect`?*
5. **Report every suite** in your README as `pass` or `not_applicable` **with the reason**. Not
   applicable is not a pass, and no adapter describes itself as "conformant".

### The two shapes, and which one you have

**Resumed in place.** The tool runs, the interrupt raises out of it, the framework checkpoints,
and the resumed run re-enters the same code with the answer available. You need the provider and
nothing else. Read §3.2.1: the node runs twice, so `action_id` is not continuous, the first
pass's request is orphaned, and the approval TTL does not bound the human's deliberation.

**Decided before invocation.** The framework asks whether a call needs approval *before* it
invokes the tool. Answer with `ctrlrun.adapter.needs_approval` and nothing else — never by
building an `Action` yourself, which would need a `Principal` you are not allowed to supply.
Three things bite here, and all three were found the hard way:

- **Observe mode.** Your predicate must return "no approval needed" when
  `control.policy.mode` is `observe` (§3.6). Otherwise a human is asked, and because your
  framework will not invoke a declined tool, their *no* **stops an action that observe mode
  promises to let run**.
- **The framework's answer is keyed to *its* unit, not to a CTRLRun action.** A tool body can
  raise `ApprovalRequired` more than once. Bind the answer to the action you gated, and to one
  request, or one human "yes" authorizes everything raised under that call.
- **Exceptions.** If your framework wraps or swallows what a tool raised, restore it (§12.7).
  A refusal that reaches the model as text invites the retry the refusal exists to prevent.

## What the contract could not answer

Item 6 of v0.5 wrote a third adapter against `SPEC-v0.5.md` alone, in a session that could not
read the kernel or either reference adapter. It produced fourteen questions the document could
not answer, four of which would have produced an insecure or unimplementable adapter. **Every one
of them became an edit to the contract** — `SPEC-v0.5.md` §12.10 lists them and what they
changed, and §12.9 is the observe-mode defect it found in a *shipping* adapter without reading
a line of it.

If you write an adapter and the contract cannot answer something, that is a defect in the
contract. Please open an issue saying what you were writing when you got stuck.
