# The CTRLRun framework probe

**This table reports behaviour, not quality.** A framework that retries a lost response is
doing what its documentation says it does. The finding here is about what an agent stack does
*without* an effect-level guard — and it would be dishonest to present it as a judgment on any
of these projects, none of which claims to solve this problem.

It answers a question CTRLRun has so far only asserted: *what actually happens when the
response is lost and the framework retries?*

> **Two of the four framework adapters have now been run against a model. Two have not.**
>
> On **2026-09-05**, the LangGraph and OpenAI Agents SDK adapters were run against real
> installations — `langgraph` 1.2.11 with `langchain` 1.4.0 and `langchain-openai`, and
> `openai-agents` 0.22.0 — driving `gpt-4o-mini`, five repetitions of each scenario. Every cell
> agreed with itself five times out of five. `results/2026-09-05.json` is that run and
> `results/2026-09-05.md` is rendered from it.
>
> **CrewAI and AutoGen were not installed and remain unexecuted in every sense.** Their rows say
> so by name. Nothing in this file is a finding about either of them.
>
> **This is behaviour, not quality.** Neither LangGraph nor the OpenAI Agents SDK claims to solve
> duplicate execution, and neither is doing anything its documentation does not describe. The
> finding is about what an agent stack does *without* an effect-level guard — which is the
> question this repository exists to answer, asked of somebody else's code so that the answer is
> not ours to assert.

## What the run found

| | double-refund | approval-mutation |
|---|---|---|
| **LangGraph** 1.2.11 | `executed_once`, 5/5 | `executed_once`, 5/5 |
| **OpenAI Agents SDK** 0.22.0 | `executed_twice`, 5/5 — **3 to 4 effects per run** | `executed_once`, 5/5 |
| `stub-retrying` | `executed_twice` | `executed_once` |
| `stub-not-retrying` | `executed_once` | `executed_once` |
| plain MCP client | `executed_once` | `executed_once` |

**Read the `approval-mutation` column carefully.** `executed_once` there does **not** mean the
scenario was handled well. It means the *mutated* action — the €50 refund the human never
approved — reached the remote and committed. Every row shows it, including the two stubs, and
that is the scenario working as designed: nothing in any of these stacks binds a human's approval
to the exact action that then runs, because nothing in them is trying to. `outcome` is a closed
set fixed by SPEC-v0.4 §7.4 and has no value for "the mutation landed", so this paragraph is where
that is said.

**The double-refund column is the finding.** The remote commits the refund and then drops the
connection without a response. LangGraph's prebuilt agent surfaced the failure and stopped: one
effect, one request, every time. The Agents SDK's default handling of a tool that raised is
`failure_error_function`, which turns the exception into a message the model can act on — and
the model acted on it, retrying until the refund had landed **three or four times** in a single
run. `executed_twice` is the closed set's word for "more than once"; the count is in `notes`.

That is not a defect in either project. It is what an agent loop does when the only thing that
knows an effect already happened is a remote that cannot say so. Both stub rows are there to
prove the harness can tell the two behaviours apart: without the pair, a harness that reported
`executed_twice` unconditionally would have said the same thing about both frameworks.

**What this run does not establish.** One model, one prompt, one remote, five repetitions. A
different model, a longer prompt or a tool description that mentioned idempotency could move
either row, and none of that was varied. The claim is about these versions on this day, and the
version each row carries is read at runtime for that reason.

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
$ pip install langgraph langchain langchain-openai crewai openai-agents \
      autogen-agentchat autogen-ext[openai]
```

`langchain-openai` is not optional and was missing from this line until the LangGraph adapter
was actually run: `create_react_agent` resolves its model string through
`langchain.chat_models.init_chat_model`, which imports the provider package lazily and raises
`ImportError` at agent construction — after the harness has already started a fake remote. It is
declared in that adapter's `requires` too, so an absent one is a skip by name rather than a row
that reads as a framework failure.

`CTRLRUN_PROBE_MODEL` picks the model. One model for every framework, so the table compares
frameworks and not models — and it is **one unprefixed string**, shared by every adapter in
`adapters/_framework.py`. It was `openai:gpt-4o-mini` in the LangGraph adapter and
`gpt-4o-mini` in the Agents SDK's until the two were run side by side, which meant a single
`CTRLRUN_PROBE_MODEL` could not satisfy both and setting it broke whichever adapter did not
match its own default. `init_chat_model` resolves a bare `gpt-*` to `ChatOpenAI` — checked
against langchain 1.4.0, not assumed — so one string serves both.

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
| LangGraph | `langgraph` | <https://langchain-ai.github.io/langgraph/> | Retry is explicit and opt-in: a node takes a `RetryPolicy`, and failures are routed in the graph. No policy is attached here, so the row measures `langgraph.prebuilt.create_react_agent`'s default. On langgraph 1.2.11 that prebuilt *warns* `LangGraphDeprecatedSinceV10`, naming `langchain.agents.create_agent` and V2.0; it still constructs and runs, and the adapter stays on it (see "What running it found"). |
| CrewAI | `crewai` | <https://docs.crewai.com/> | An agent retries a failed tool call as part of its own loop, bounded by `max_retry_limit`. Left at its default. |
| OpenAI Agents SDK | `openai-agents` | <https://openai.github.io/openai-agents-python/> | A tool that raises is surfaced to the model through `failure_error_function`, which defaults to a message the model can act on. None is supplied here. |
| AutoGen (AgentChat) | `autogen-agentchat` | <https://microsoft.github.io/autogen/stable/> | Retry is conversational: the agent sees the failure and may try again. Nothing is configured here. |
| A plain MCP client | — | <https://modelcontextprotocol.io/> | The control row: no framework at all, no retry, so a reader can tell what a framework contributed from what the protocol does on its own. |

## What running it found

Four defects, each found by executing an adapter against a real installation rather than by
reading it, and each fixed above. They are listed because the point of running the harness
before publishing anything is to find out what the documented APIs got wrong, and a fix with
no record of what it fixed is a fix the next reader cannot check.

| # | Found | Fix |
|---|---|---|
| 1 | `langchain-openai` was missing from the install line. `create_react_agent` resolves its model string through `init_chat_model`, which imports the provider package lazily and raises `ImportError` at agent construction. | Named in the install line, with the reason. |
| 2 | Two different model defaults for one env var: `openai:gpt-4o-mini` in the LangGraph adapter, `gpt-4o-mini` in the Agents SDK's, and a third and fourth copy of the `os.environ.get` in CrewAI's and AutoGen's. A maintainer setting `CTRLRUN_PROBE_MODEL` broke whichever adapter did not match its own default, and §7.3 rule 2 asks for the same text everywhere the API admits it. | One unprefixed `PROBE_MODEL`, shared in `adapters/_framework.py` by all four. `init_chat_model` resolves a bare `gpt-*` to `ChatOpenAI` — checked against langchain 1.4.0, not assumed. |
| 3 | An `error` row said nothing about what had gone wrong: the exception decided `outcome` and was then discarded. | The exception is kept in `notes` for an `error` row, and only for one — and every table cell is escaped and bounded, because `notes` now carries a measured framework's exception text and a `\|` in one of those does not make an ugly cell, it makes a different table. |
| 4 | **`available()` did not name every distribution `run()` imports.** With `langchain` absent, the LangGraph adapter said it was available, raised `ImportError`, and the table carried a row named `langgraph`, with LangGraph's version, whose outcome was `error` — the harness's own environment reported as a framework that broke, in the same closed-set value. §7.3 rule 5's "skipped **by name**" is the correct outcome and was unreachable. | Each adapter declares `requires` beside `distribution`, and `is_installed` is all of them rather than one. |

**And one finding that is not a defect, recorded rather than acted on.**
`langgraph.prebuilt.create_react_agent` emits `LangGraphDeprecatedSinceV10` on langgraph 1.2.11,
naming `langchain.agents.create_agent` as its replacement and V2.0 as its removal. It is a
**warning**, not an exception: the prebuilt constructs and runs exactly as before. The adapter
therefore stays on it. §7.3 rule 4 permits a configuration change *only where the framework
cannot run the scenario at all without it*, and LangGraph can — so moving would be elective, and
an elective change of the code path under measurement is what that rule exists to surface. It
would also change what is measured: `create_react_agent` is LangGraph's own implementation,
while `create_agent` lives in a different distribution, so a row labelled `langgraph` and
carrying a version read from `langgraph` would be reporting langchain's agent loop and its
middleware defaults. The deprecation belongs here, under rule 7, where a reader can see it.

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

An `error` row carries the exception that produced it in `notes`. It did not until the harness
was run: the exception decided `outcome` and was then discarded, so a reader saw `error` beside
an empty notes column and could not tell a missing credential from a framework that had broken.
It is appended for an `error` row only — a stub whose response was deliberately lost has an
exception too, and that one is the scenario working.

**The results checked in are the maintainer's**, read before they were committed. A commit
carrying findings about other projects that nobody had reviewed is not a commit this repository
makes — so `results/` holds a run somebody looked at, and the Markdown beside it is rendered from
the JSON and never written by hand.
