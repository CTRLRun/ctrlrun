# The CTRLRun framework probe

**This table reports behaviour, not quality.** A framework that retries a lost response is
doing what its documentation says it does. The finding here is about what an agent stack does
*without* an effect-level guard — and it would be dishonest to present it as a judgment on any
of these projects, none of which claims to solve this problem.

It answers a question CTRLRun has so far only asserted: *what actually happens when the
response is lost and the framework retries?*

> **The four framework adapters have never been executed.** They are written against each
> framework's documented entry points and have not been run against a real installation or a
> real model, so nothing here is a finding about LangGraph, CrewAI, the OpenAI Agents SDK or
> AutoGen — and no README, changelog entry or post may say otherwise. The only true statement
> today is *"adapters written from documented APIs, unexecuted"*. What **has** run, in this
> repository's CI, is the harness itself: the fake remote, the two stubs and the plain MCP
> client, end to end over a loopback socket.
>
> Before any results are published, each adapter has to be run against a real model, its
> framework version recorded, and whatever the documented APIs got wrong fixed. Until then
> there are no results, and `results/` is empty on purpose.

---

## What it is not

- Not part of the `ctrlrun` package. It lives outside `src/`, is never imported by `ctrlrun`,
  and its per-framework dependencies are never installed by `ctrlrun` or by any of its extras.
  A test asserts that `research` is not importable after `pip install ctrlrun`.
- Not the v0.5 adapter contract. This is research: unsupported, not versioned with the kernel,
  and free to change.
- Not a benchmark. There is no score and no ranking. Two columns and a `config_deviation`.

## Running it

```console
$ python research/framework-probe/run.py --out results/$(date +%F).json --markdown TABLE.md
```

Adapters whose framework is not installed are skipped **by name**, and the skip appears in the
results file — a table with four rows where five were expected has to say which one is
missing. To measure a framework, install it and set `OPENAI_API_KEY`:

```console
$ pip install langgraph langchain crewai openai-agents autogen-agentchat autogen-ext[openai]
```

`CTRLRUN_PROBE_MODEL` picks the model. One model for every framework, so the table compares
frameworks and not models.

## The scenarios

Both are already in `examples/`, run here through somebody else's agent loop instead of
through CTRLRun.

**double-refund.** The remote commits the refund and then closes the connection without a
response. Does the framework retry, and does the effect land twice?

**approval-mutation.** A human approves a refund of 500 cents. The agent is then told the
customer wants 5000. Is the mutated action executed?

## The fake remote

One remote for every framework, with three behaviours: commit-then-drop, commit-then-timeout,
and capture-what-was-approved. It counts **effects by identity, not by request** — "executed
twice" means two effects, not two HTTP calls. A framework that retried and was rejected at the
remote did not execute twice; a framework that sent one request the remote committed twice did.

The outcome of every row is derived from what the remote saw and never from anything an
adapter reports about itself. An adapter that graded its own run would be the framework
marking its own homework.

## Fairness rules

These are normative — SPEC-v0.4 §7.3 — because the output has other projects' names in it.

1. **The same fake remote** for every framework, with the same behaviour, on the same port
   discipline. Each run gets a fresh instance, so nothing an earlier adapter did is visible to
   a later one.
2. **The same scenario text.** Prompt, tool name, tool description and tool schema are
   byte-identical across adapters wherever the framework's API admits it, and the diff is
   recorded where it does not.
3. **Framework defaults.** No retry setting changed, no timeout tuned, no guard added.
4. **At most one configuration change per framework**, permitted only where the framework
   cannot run the scenario at all without it — and it appears in the results table's
   `config_deviation` **column**. Not a footnote, not prose.
5. **The framework's version is read at runtime**, from the installed distribution. Never
   written down by hand: a version somebody typed is a version that was true once.
6. **The table reports behaviour, not quality.**
7. Each framework's documented retry and approval defaults are cited below, with links and the
   date read.

## The frameworks, and what their documentation says

Read **2026-09-04**. Every adapter is written against the framework's own documented entry
points and left at its defaults.

| Framework | Distribution | Documentation | What it says about a tool that raised |
|---|---|---|---|
| LangGraph | `langgraph` | <https://langchain-ai.github.io/langgraph/> | Retry is explicit and opt-in: a node takes a `RetryPolicy`, and failures are routed in the graph. No policy is attached here, so the row measures the prebuilt ReAct agent's default. |
| CrewAI | `crewai` | <https://docs.crewai.com/> | An agent retries a failed tool call as part of its own loop, bounded by `max_retry_limit`. Left at its default. |
| OpenAI Agents SDK | `openai-agents` | <https://openai.github.io/openai-agents-python/> | A tool that raises is surfaced to the model through `failure_error_function`, which defaults to a message the model can act on. None is supplied here. |
| AutoGen (AgentChat) | `autogen-agentchat` | <https://microsoft.github.io/autogen/stable/> | Retry is conversational: the agent sees the failure and may try again. Nothing is configured here. |
| A plain MCP client | — | <https://modelcontextprotocol.io/> | The control row: no framework at all, no retry, so a reader can tell what a framework contributed from what the protocol does on its own. |

**What could not be established from primary documentation**, said plainly rather than
implied: none of these projects publishes a single normative "this is the default retry count"
sentence that could be quoted here. The column above summarises each project's own
error-handling page. That gap is exactly why the harness measures rather than cites — a
documented default and an observed behaviour are different things, and the second is the one
an operator's refund depends on.

## The stubs

Two, and they are not decoration. One retries a lost response and one does not, and both run
by default:

| | double-refund |
|---|---|
| `stub-retrying` | `executed_twice` |
| `stub-not-retrying` | `executed_once` |

Without the pair, a harness that reported `executed_twice` unconditionally would pass its own
tests and would say the same thing about every real framework it ever ran. `--no-stubs` leaves
them out of a publishable table.

## The results file

`results/<YYYY-MM-DD>.json`, schema `ctrlrun.framework-probe/v1`. `outcome` is a closed set:
`executed_once`, `executed_twice`, `refused`, `error`. The Markdown table is rendered from the
JSON and never written by hand.

**No results are checked in.** The runs are made and published by the maintainer; a commit
carrying findings about other projects that nobody had reviewed is not a commit this
repository makes.
