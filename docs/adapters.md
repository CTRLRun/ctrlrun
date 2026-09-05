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
read the kernel or either reference adapter. **The adapter was disposable; this list is what it
was for.** A contract only its author can implement is not a contract, so the list had to be
emptied before v0.5 could ship — every row below is answered by an edit to `SPEC-v0.5.md`.

It chose the **decided-before-invocation** shape deliberately, because that shape must touch
`needs_approval`, `banner`, `refuses_before_invoking` and `Control.policy` and so loads more of
the contract. Six of the fourteen exist only because of that choice, and that is the finding
rather than an artefact of it: **this document was written from one shape and read from the
other, and that is where it broke.**

| # | The question | Severity | Answered by |
|---|---|---|---|
| Q1 | Is `ctrlrun.context()` on the never-list? It supplies a principal without naming the type, and passes T129 because that test runs against a `Control` **with** an identity provider | **security** | §2.3 never-list row |
| Q2 | May an adapter construct an `InterruptApprovalProvider`? §9 rested a security argument on "it does not", and nothing forbade it — a `build_provider()` helper could inject a frozen clock and defeat §2.4 | **security** | §2.3 never-list row |
| Q3 | §3.6's "never interrupts in observe mode" is false for this shape: the primitive is reached from the *predicate*, and a human's *no* then **stops** an action observe mode promises to run | **correctness** | §3.6 now requires it; §12.9 |
| Q4 | §3.4 rebuilds the request's `Action` "with those arguments" — replace or overlay? Under replace, a tool with a defaulted parameter can never match, so the honest adapter with the honest declaration is the one that breaks | **correctness** | §3.5, with a README requirement |
| Q5 | Nothing in five specifications addresses `async` — may `interrupt()` be called inside a running loop? Does `@protect` wrap a coroutine? | **correctness** | §3.5.1 |
| Q6 | What does `invoke` do when the framework refuses *before* invoking? Returning a value is `never-executes`; returning `None` is undefined | correctness | §5.2 code block |
| Q7 | `needs_approval` cannot see the effect key, so an unresolvable template asks a human and *then* fails — the waste `v0.1 §5.1` exists to prevent | correctness | §3.5, stated as a cost |
| Q8 | Is `resource` a template core resolves, or a literal? The whole contract answered this once, in a parenthesis of `ARCHITECTURE.md` | correctness | §3.5 |
| Q9 | The kit's executor signature is an unstated contract — must arguments be spread as keywords? | correctness | §5.2 |
| Q10 | `@protect(wait=True)` is mandatory and §2.3's *must call* paragraph did not say so | correctness | §2.3 |
| Q11 | Six names the may-call list is written in are frozen nowhere, including `ApprovalStore` in a §9 signature | correctness | §2.3 |
| Q12 | `Case` is public in `SUITES`' type and defined nowhere | style | §5.2 |
| Q13 | A Protocol class-attribute default cannot reach a non-inheriting adapter, so `refuses_before_invoking`'s default must mean `getattr(..., False)` | style | §5.2 |
| Q14 | "May raise its own type" — of its own choosing, or one it defined? It decides whether `except CTRLRunError` catches it | style | §10 |

`SPEC-v0.5.md` §12.10 records what the exercise says about the document as a whole, and §12.9 is
the observe-mode defect it found in a **shipping** adapter without reading a line of it.

If you write an adapter and the contract cannot answer something, that is a defect in the
contract. Please open an issue saying what you were writing when you got stuck.
