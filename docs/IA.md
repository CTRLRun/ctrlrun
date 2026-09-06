# Information architecture for the documentation site

The target shape of the docs site, one line per page: what it is for and the query it should
answer. Later sessions write the pages; this file is what they write against, and
`docs/capabilities.yaml` names pages by the paths below.

**The fixed copy**, quoted here so every page quotes the same words:

| Slot | Text | Where it goes |
|---|---|---|
| Tagline | The last check before an AI agent does something it can't undo. | README line 1, Home hero, social preview |
| Principle | Autonomy belongs to the action, not the agent. | Second sentence everywhere; the line people quote |
| Category | The execution safety layer for AI agents. | GitHub About, PyPI summary, site `<title>`, directory listings |
| Opener (long-form only) | Everyone is rushing to ship AI agents without thinking about consequences. | First line of Why and of launch posts; never a heading |
| Promise | A consequential action happens at most once, exactly as approved, and leaves a receipt — and when the outcome is unknown, CTRLRun says so instead of guessing. | Hero subline, README paragraph 2 |
| Hook (posts) | Agents can retry. The real world can't. | Social, talk titles; not the README header |

The rules every page is held to are in `docs/STYLE.md`. The tools that hold them are in
`tools/docs_audit/`.

## Conventions

- Paths are root-relative on the site: `concepts/effect-keys` is `docs/concepts/effect-keys.mdx`.
- **Purpose** is the page's first-paragraph answer, compressed. **Query** is the search a
  stranger types, and the first sentence of the page is written to answer it.
- The existing Markdown documents that stay where they are — the specifications,
  `ARCHITECTURE.md`, `THREAT_MODEL.md`, the OWASP and ACS readings — are linked from the pages
  that own them and are not rewritten.
- Sessions are named where a page belongs to a later session, so a session knows what it owes.

## The tree

```
Home                                   index
Why                                    why
Get started
  ├─ Install                           get-started/install
  ├─ 60-second quickstart              get-started/quickstart
  ├─ Three ways in                     get-started/three-ways-in
  └─ Choosing between them             get-started/choosing
Production
  ├─ Run it in production               production/index
  ├─ SQLite or Postgres                 production/postgres
  ├─ How reservation works              production/how-reservation-works
  ├─ Migrations and schema versions     production/migrations
  ├─ Recovery after a crash             production/recovery
  ├─ Receipt integrity in practice      production/receipt-integrity
  ├─ The soak                           production/soak
  ├─ Operations                         production/operations
  └─ Running on Postgres (reference)    postgres
MCP
  ├─ Overview                          mcp/overview
  ├─ The gateway in five minutes       mcp/gateway-in-5-minutes
  └─ Use the docs from your editor     mcp/use-the-docs-from-your-editor
Concepts
  ├─ Action and hash                   concepts/action-and-hash
  ├─ Decisions                         concepts/decisions
  ├─ Approval binding                  concepts/approval-binding
  ├─ Effect keys                       concepts/effect-keys
  ├─ Outcomes and AMBIGUOUS            concepts/outcomes-and-ambiguous
  ├─ Receipts and evidence             concepts/receipts-and-evidence
  ├─ Authority and delegation          concepts/authority-and-delegation
  ├─ Observe mode                      concepts/observe-mode
  └─ Fail closed                       concepts/fail-closed
Guides
  ├─ Protect a function                guides/protect-a-function
  ├─ Put the gateway in front of MCP   guides/gateway-in-front-of-mcp
  ├─ Approve in Slack                  guides/approvals-in-slack
  ├─ Resolve an AMBIGUOUS effect       guides/resolve-an-ambiguous-effect
  ├─ Reconcile automatically           guides/reconcile-automatically
  ├─ Roll out observe, then enforce    guides/observe-to-enforce
  ├─ Run on Postgres                   guides/run-on-postgres
  ├─ Verify in CI                      guides/verify-in-ci
  ├─ Export to OpenTelemetry           guides/export-to-opentelemetry
  ├─ Use the LangGraph adapter         guides/langgraph-adapter
  └─ Use the OpenAI Agents SDK adapter guides/openai-agents-adapter
Cookbook                               cookbook/index  (session 4; recipes listed below)
Reference
  ├─ Policy YAML                       reference/policy-yaml
  ├─ Authority YAML                    reference/authority-yaml
  ├─ CLI                               reference/cli
  ├─ Python API                        reference/api/index  (+ one page per public name)
  ├─ Receipt and event schemas         reference/receipt-and-event-schemas
  ├─ Exit codes                        reference/exit-codes
  └─ Errors                            reference/errors
Compare
  ├─ vs framework human-in-the-loop    compare/framework-hitl
  ├─ vs guardrail libraries            compare/guardrail-libraries
  ├─ vs agent governance toolkits      compare/governance-toolkits
  ├─ vs durable workflow engines       compare/durable-workflows
  └─ vs idempotency keys               compare/idempotency-keys
FAQ                                    faq
Security
  ├─ Threat model                      security/threat-model
  ├─ What verify guarantees            security/verify-guarantees
  ├─ The receipt chain                 security/receipt-chain
  ├─ How this is built                 security/how-this-is-built  (session 1b)
  └─ Reporting a vulnerability         security/disclosure
Architecture and specifications
  ├─ Architecture                      architecture/overview
  └─ Specifications                    architecture/specifications
Changelog                              changelog
Try it in your browser                 try-it
Research: does your framework
  double-execute?                      study/does-your-framework-double-execute
Get the badge                          verify/get-the-badge
```

## Home and Why

| Path | Purpose | Query |
|---|---|---|
| `index` | The tagline, the principle, the promise; the demo; the capability grid rendered from `capabilities.yaml`; the three ways in; three start-here cards; the one line that adds this site as an MCP server to a coding tool. | *ctrlrun* · *AI agent safety layer* |
| `why` | Opens with the opener line. Five sections, one principle each: FAILED is not UNKNOWN · an approval is bound to what the human saw · autonomy belongs to the action · unknown means no · evidence leaves the building. Ends at How this is built. | *why do AI agents double execute actions* · *AI agent consequential actions* |
| `not-only-agents` | For the reader who runs a task queue, a webhook handler or a cron job rather than an agent: the same failure with no model in it, the three examples under `examples/without-an-agent/`, what an agent actually changes, and the two guarantees that do the work when nobody is delegating authority to a worker. | *celery task retried twice* · *webhook delivered twice duplicate* · *retry safe background job python* |

## Get started

| Path | Purpose | Query |
|---|---|---|
| `get-started/install` | `pip install ctrlrun`; what it installs (pyyaml, click, nothing else); the extras and what each adds; Python 3.11+. | *install ctrlrun* |
| `get-started/quickstart` | Protect one function end to end in sixty seconds, with the real output: a policy, a decorator, a refused mutation, a receipt. | *ctrlrun quickstart* · *protect an AI agent action python* |
| `get-started/three-ways-in` | Decorator, gateway, adapter: what each covers and what each needs. The negative sentence: most readers need the decorator and should not look for an adapter. | *ctrlrun langgraph* · *ctrlrun mcp* · *do I need an adapter* |
| `get-started/choosing` | The decision table: in-process Python → decorator; tools behind MCP → gateway; a framework with its own approval UI → adapter. What you do not need for the single-host case: a server, a database, a dashboard. | *ctrlrun decorator vs gateway* |

## Production

The question a stranger asks after the demo convinces them, and the one the rest of this site
answered only in pieces: **can I run this for real, and what happens when the parts that fail,
fail?** It sits third, above MCP and Concepts, because a reader deciding whether to adopt asks
it before they ask what an effect key is.

**The first line of the section is load-bearing.** SQLite is the default and is production-grade
on one host; Postgres is for many hosts. Written in that order, because a reader with one host
must not be told they are not really in production.

`production/soak` is a **render** of `research/soak/results/*.json` and is never hand-edited, on
`study/does-your-framework-double-execute`'s precedent: the duration on the page is the measured
one, and the page says in the same paragraph that the roadmap's exit criterion is not met by it.

| Path | Purpose | Query |
|---|---|---|
| `production/index` | The section's front door: which store and why, what this section answers, the generated readiness block with its **Not yet** list, and the four things the store holds. | *is ctrlrun production ready* |
| `production/postgres` | The choice, in one table: SQLite until a second host writes, Postgres after. What changes (a URL and a schema) and what does not (everything else). | *ctrlrun sqlite vs postgres* |
| `production/how-reservation-works` | One winner per effect key, and the two rows nobody merges: an exception before `COMMIT` is a failed write to retry; one during it is unknown and is re-read. | *lost commit ambiguous* · *exactly once database* |
| `production/migrations` | Five shapes, three of them a refusal; nothing half-applies; the backward direction that corrupts; what a rolling deploy can and cannot do. | *ctrlrun schema migration* |
| `production/recovery` | A restarted process repairs nothing and cannot know the holder is dead. Nothing sweeps; an expired lease is a refusal, not a reclaim. | *agent crashed mid action* |
| `production/receipt-integrity` | The runbook: run `--verify-chain`, read the six names, know what each means. `security/receipt-chain` keeps *what it proves*; this page is *what to do*. | *verify receipt chain* |
| `production/soak` | Generated. One published run: how long, how many actions, how many unattributed ambiguous outcomes, and what it is not evidence of. | *ctrlrun soak test* |
| `production/operations` | What to watch, what to page on, what to back up, and the fact that there is nothing to run. | *ctrlrun monitoring* |
| `postgres` | The operational reference that already existed: connection strings, grants, pooling, failover, the throughput ceiling. Moved here from Architecture, because this is where a reader looks for it. | *ctrlrun postgres connection* |

## MCP

Four things are true today and the pages say exactly that. Added by session 3b; the fourth
row landed with `docs/SPEC-mcp-operator.md`, which is when it stopped being planned.

| Path | Purpose | Query |
|---|---|---|
| `mcp/overview` | CTRLRun works with MCP in four ways: enforcement (the gateway in front of any MCP server), answering (the operator server, for approvers), learning (this site is an MCP server), discovery (the registries, once listed). | *MCP gateway* · *MCP server human approval* |
| `mcp/gateway-in-5-minutes` | For a reader who already runs an MCP server: before/after, the two commands, what the agent sees on deny and on approval-required, the supported revisions, the principal-flag choice and its security note. | *protect MCP server* · *MCP tool call approval gateway* |
| `mcp/approve-from-your-assistant` | For the person who answers approvals rather than the one who deploys: what `ctrlrun mcp-operator` is, the two flags, a client configuration, a real transcript ending in the receipt that names the approver, and the five things it will not do. | *approve MCP tool call from an assistant* · *MCP human approval server* |
| `mcp/use-the-docs-from-your-editor` | The exact configuration for this site's MCP server, three questions an assistant can then answer, a screenshot spec. | *ctrlrun mcp docs* |

## Concepts

Each Concepts page opens with one definitional sentence an assistant can quote standalone,
has one diagram or code block, names the guarantee it supports and what it does not do, and
ends with Next links. Every claim on a Concepts page has a `docs/CLAIMS.md` row.

| Path | Purpose | Query |
|---|---|---|
| `concepts/action-and-hash` | An action is a named operation with canonical arguments, and its hash is what everything binds to: sorted keys, no whitespace, no floats. | *ctrlrun action hash* · *canonical action hash AI agent* |
| `concepts/decisions` | Three decisions — allow, approve, deny — decided per action by a policy that cannot see who is asking. First matching rule wins; unknown is denied. | *AI agent action policy allow approve deny* |
| `concepts/approval-binding` | An approval is bound to the exact action hash a human saw, is single-use, expires, and is consumed atomically with the reservation. A mutated action is refused. | *human-in-the-loop approval bound to action* · *approval mutation AI agent* |
| `concepts/effect-keys` | An effect key names the real-world consequence, so the same intent from a retry, a second agent or a second host is one effect. This is the page that says *idempotency* once. | *idempotency key AI agent* · *prevent duplicate execution agent tool call* |
| `concepts/outcomes-and-ambiguous` | Three outcomes — COMMITTED, FAILED, AMBIGUOUS — and why a timeout is not a failure. Only `NotExecuted` means failed; everything else after the first byte is unknown, and unknown blocks a blind retry. The page that explains the product. | *what happens when an agent tool call times out* · *double execution AI agent retry* |
| `concepts/receipts-and-evidence` | Every executed action leaves a portable JSON receipt: who, what, decision, approval, effect key, outcome, and the policy hash that decided it. Chained, not signed. | *AI agent audit trail receipts* |
| `concepts/authority-and-delegation` | Authority is the second axis: opt-in, then fail-closed. A grant says who may ask; the policy says how much autonomy the action has. Delegation only narrows, at creation and at every evaluation. Identity is consumed, never issued. | *AI agent authorization delegation* · *least privilege AI agents* |
| `concepts/observe-mode` | One line runs every real decision against real traffic and records what enforcement would have blocked, without blocking. It executes; it is not a dry run. | *AI agent policy shadow mode* |
| `concepts/fail-closed` | Unknown action, missing policy, malformed policy, missing principal, missing or mismatched approval, inconsistent state: all deny. No flag makes a consequential action permissive by default. | *fail closed AI agent* |

## Guides

Each guide is a task with a verb in the title: prerequisites, numbered steps with runnable
blocks, expected output, and *if it didn't work* with the two or three real failure messages.
The gateway guide and the observe-to-enforce guide get the most room; they are the two that
turn readers into users.

| Path | Purpose | Query |
|---|---|---|
| `guides/protect-a-function` | Decorate a function, declare an effect key, write the policy, see a refund refused and a Kubernetes delete sent for approval. | *protect python function AI agent approval* |
| `guides/gateway-in-front-of-mcp` | Two commands put every guarantee in front of an existing MCP server with no agent changes: the alias, the principal, the effect templates, the banner that names writes with no effect key. | *MCP gateway human approval* · *MCP server tool call approval* |
| `guides/approvals-in-slack` | `WebhookApprovalProvider`: the request goes to a webhook, a human answers in Slack, the answer comes back through the same grant calls the CLI uses. | *slack approval AI agent actions* |
| `guides/resolve-an-ambiguous-effect` | Reading `ctrlrun effects --state ambiguous`, asking the remote, `ctrlrun resolve --committed` or `--failed`, and what the receipt then says, including who resolved it. | *ctrlrun resolve ambiguous* |
| `guides/reconcile-automatically` | `@protect(reconcile=...)`: a hook that asks the remote what happened, and the only thing besides a human that moves a record out of AMBIGUOUS. | *reconcile AI agent action stripe kubernetes* |
| `guides/observe-to-enforce` | `mode: observe` for a week, `ctrlrun stats` to read the cost of enforcement, then `mode: enforce`. | *roll out AI agent policy without breaking production* |
| `guides/run-on-postgres` | `pip install "ctrlrun[postgres]"`, the connection string, what to grant, migrations at open, the lost-COMMIT case. | *ctrlrun postgres* |
| `guides/verify-in-ci` | The GitHub Action, the two shapes of report, the N/A line and what it means, the badge. | *verify AI agent safety configuration CI* |
| `guides/export-to-opentelemetry` | `OTelEventSink`: one span per action, one event per step, argument values opt-in. | *opentelemetry AI agent actions* |
| `guides/langgraph-adapter` | Route an approval through `interrupt()`: the operator builds the `Control`, `wait=True`, `Command(resume=...)`, and prevention versus attribution. | *langgraph interrupt human approval tool call* |
| `guides/openai-agents-adapter` | Route an approval through the SDK's tool-approval interruption: `protected_tool`, `gate.run`, why a rejection leaves no CTRLRun evidence. | *openai agents sdk tool approval* |

## Cookbook (session 4)

One recipe per situation: the situation in two sentences, the policy, the code, what the agent
sees when refused or asked, the receipt, and what to do when an AMBIGUOUS appears. Every block
runs offline against a fake remote, and every recipe is also a directory under
`examples/cookbook/<name>/` with the examples' guard: a refusal that stops being refused exits
non-zero.

| Path | Situation |
|---|---|
| `cookbook/refund-agent` | money — a refund agent with amount tiers |
| `cookbook/payout-maker-checker` | money — a payout agent with maker/checker via delegation |
| `cookbook/deploy-agent` | infrastructure — restart auto, apply-prod approve, delete-namespace deny |
| `cookbook/database-migration-agent` | infrastructure — a migration agent |
| `cookbook/iam-agent` | permissions — grant read, never admin |
| `cookbook/credential-rotation-agent` | permissions — rotate, with approval to revoke |
| `cookbook/crm-update-agent` | records — update a record, approve a merge |
| `cookbook/data-deletion-agent` | records — deletion under a retention rule |
| `cookbook/outbound-email-agent` | communications — external recipients need approval |
| `cookbook/customer-notification-agent` | communications — batch notifications, one effect each |
| `cookbook/manager-and-worker` | multi-agent — a manager delegates bounded authority to a worker |
| `cookbook/protect-an-mcp-server` | integrations — an existing MCP server in five minutes |
| `cookbook/langgraph-interrupt` | integrations — LangGraph with `interrupt()` |
| `cookbook/openai-agents-tool-approval` | integrations — the OpenAI Agents SDK's tool approval |
| `cookbook/slack-approvals` | integrations — approvals in Slack via webhook |
| `cookbook/receipts-to-opentelemetry` | integrations — receipts into a tracing backend |
| `cookbook/observe-then-enforce` | operations — observe for a week, then enforce |
| `cookbook/resolve-an-ambiguous-effect` | operations — a human resolves an unknown outcome |
| `cookbook/reconcile-against-the-remote` | operations — reconcile against Stripe or Kubernetes automatically |
| `cookbook/verify-in-github-actions` | operations — the composite action in a workflow |
| `cookbook/sqlite-to-postgres` | operations — move the store |

## Reference

Reference pages are generated from the code where the code can say it, and a drift test fails
CI where it cannot.

| Path | Purpose | Query |
|---|---|---|
| `reference/policy-yaml` | Every key of `ctrlrun.policy/v1`–`v4`: type, default, example, and the fail-closed behaviour when omitted. `controls:` and `data:` documented as registry primitives; the word *pack* does not appear. | *ctrlrun.yaml reference* · *ctrlrun policy schema* |
| `reference/authority-yaml` | Every key of `authority:`: grants, subjects, constraints, `delegable`, `expires_at`, `max_delegation_depth`, and the omission rule. | *ctrlrun authority grants yaml* |
| `reference/cli` | Every command and flag, generated from click's help; a test asserts the page matches `--help`. | *ctrlrun cli* · *ctrlrun resolve* |
| `reference/api/index` | Every frozen public name from SPEC §8 and §11 across versions, generated from docstrings into one page per name. | *ctrlrun Control* · *ctrlrun protect decorator* |
| `reference/receipt-and-event-schemas` | `ctrlrun.receipt/v3` and the event types, field by field, from the code. | *ctrlrun receipt json schema* |
| `reference/exit-codes` | Every CLI exit code and what it means, from the code. | *ctrlrun verify exit code* |
| `reference/errors` | The closed error hierarchy: `ActionDenied`, `ApprovalRequired`, `ApprovalMismatch`, `DuplicateEffect`, `AmbiguousEffect`, `NotExecuted` and the rest, with when each is raised. | *ctrlrun ApprovalMismatch* · *ctrlrun AmbiguousEffect* |

## Compare

Each: what the other thing is good at, honestly; what it does not do; when to use both; a
short table. No vendor name in a heading.

| Path | Purpose | Query |
|---|---|---|
| `compare/framework-hitl` | A framework's interrupt lets a human say yes; it does not bind the yes to the arguments that execute, refuse a retry after a lost response, or leave a receipt. Use both: the adapter routes through the interrupt. | *langgraph human in the loop vs* · *agent framework approval limitations* |
| `compare/guardrail-libraries` | Guardrails inspect inputs and outputs; CTRLRun sits at the boundary between intention and effect. Different layer; use both. | *AI guardrails vs execution control* |
| `compare/governance-toolkits` | Governance toolkits catalogue, monitor and report; CTRLRun refuses, in the execution path, per action. | *AI agent governance vs runtime enforcement* |
| `compare/durable-workflows` | Durable workflow engines retry until success and make that safe with idempotent activities; CTRLRun refuses to retry an unknown outcome and binds approvals. Complementary. | *temporal vs ctrlrun* · *durable execution AI agents idempotency* |
| `compare/idempotency-keys` | An idempotency key deduplicates at one remote that supports it; an effect key deduplicates at the agent side across remotes, refuses on unknown, and is bound to an approval. The page that says *idempotency* precisely. | *idempotency keys AI agents* · *stripe idempotency key vs* |

## FAQ

`faq` — the twelve questions engineers ask, answer-first, at most eighty words each, marked up
as FAQ structured data: isn't this idempotency keys · why not a workflow engine · do I need an
adapter · is it exactly-once · what happens on timeout · can I bypass it · does it phone home ·
what if the human takes an hour · single host or many · what's in a receipt · is the chain a
signature · what is not covered. Query: *ctrlrun faq* and each question verbatim.

## Security

| Path | Purpose | Query |
|---|---|---|
| `security/threat-model` | What CTRLRun defends against, what it does not, and the fail-closed rules that follow; renders `docs/THREAT_MODEL.md`. | *ctrlrun threat model* |
| `security/verify-guarantees` | The guarantee catalogue G1–G11, what each exercises, what N/A means, what verify cannot see. | *ctrlrun verify guarantees* |
| `security/receipt-chain` | Each receipt carries the hash of the one before; what the chain detects, what it does not prove, and the two statements that erase the end of the log. Alteration, not authorship. | *tamper evident audit log AI agent* |
| `security/how-this-is-built` | Spec-first, every MUST mutation-tested with the real numbers, independent review sessions, CLAIMS.md, N/A is not a pass, AI coding agents used throughout with the constraints that make that safe, and what has not been done: no external audit yet. Session 1b. | *is ctrlrun trustworthy* · *how ctrlrun is tested* |
| `security/disclosure` | How to report a vulnerability and what happens to the report; renders `SECURITY.md`. | *ctrlrun security report* |

## Architecture and specifications

| Path | Purpose | Query |
|---|---|---|
| `architecture/overview` | The boundary CTRLRun owns, the canonical flow (normalize · decide · approve · reserve · execute · record), the module map; renders `docs/ARCHITECTURE.md`. | *ctrlrun architecture* |
| `architecture/specifications` | The six specifications, unchanged, with one line each on what the version asked; plus the OWASP and ACS readings. | *ctrlrun spec* |

## Changelog

`changelog` — renders `CHANGELOG.md`. Query: *ctrlrun changelog* · *ctrlrun 0.6*.

## The three things that travel

| Path | Purpose | Query |
|---|---|---|
| `try-it` | `ctrlrun demo` in the browser via Pyodide, the same five refusals the README shows, a run-again button, and one line to install it for real. Any scenario the browser cannot run says so by name. | *try ctrlrun* |
| `study/does-your-framework-double-execute` | The framework-probe results, rendered from `research/framework-probe/results/*.json` by script; *No published results yet* until a file exists. Behaviour, not quality. | *does langgraph retry tool calls* · *agent framework double execution study* |
| `verify/get-the-badge` | The two-minute version of adding the verified badge: the workflow, what *declared guarantees pass* means, what N/A means. No gallery until a repo carries it. | *ctrlrun verified badge* |

---

## What was read, and what was decided

Read on **2026-09-06** unless stated. Everything below was checked against the source that
day; nothing here is from memory.

### (a) Mintlify's current configuration format

- **`docs.json` replaced `mint.json`.** Mintlify's blog post *Refactoring mint.json into
  docs.json* (7 February 2025) describes the change: navigation used to be spread across
  `navigation`, `tabs`, `anchors` and `versions`; in `docs.json` it is one recursive
  `navigation` object. The settings page states `mint.json` is deprecated and the CLI converts
  it. — <https://www.mintlify.com/blog/refactoring-mint-json-into-docs-json>,
  <https://www.mintlify.com/docs/organize/settings>
- **Required top-level fields:** `name`, `theme`, `colors.primary`, `navigation`. Optional:
  `logo`, `favicon`, `navbar`, `footer`, `banner`, `redirects` (entries of `source`,
  `destination`, `permanent`), `seo` (`metatags`, `indexing`), `search`, `integrations`, `api`
  (OpenAPI and AsyncAPI), `fonts`, `styling`. `$schema` is recommended for editor validation,
  and a `docs.json` may be split with `$ref`. — <https://www.mintlify.com/docs/organize/settings>
- **Navigation divisions:** `pages`, `groups`, `tabs`, `anchors`, `dropdowns`, `products`,
  `versions`, `languages`, nesting arbitrarily. A group is `{group, pages, icon?, tag?, root?,
  expanded?}`; a tab is `{tab, icon?, pages | groups | menu | href}`; `global.anchors` holds
  persistent external links. Pages left out of `navigation` are hidden but reachable. —
  <https://www.mintlify.com/docs/organize/navigation>
- **Page frontmatter:** `title`, `description`, `sidebarTitle`, `icon`, `iconType`, `tag`,
  `mode`, `url`, `keywords`, `noindex`, plus any meta tag as a quoted key
  (`"og:image": …`, `"twitter:card": …`). Global defaults live in `seo.metatags`; `canonical`
  can be set there. `sitemap.xml` and `robots.txt` are generated. —
  <https://www.mintlify.com/docs/organize/pages>, <https://www.mintlify.com/docs/optimize/seo>
- **Components** the pages will use: `Card` and `Columns` (`cols` 1–4), `Steps`/`Step`,
  `Accordion`, `Tabs`, callouts, `CodeGroup`, `Frame`, `Expandable`, fields, `Update`,
  `Tooltip`. `Columns` is the current name for the card grid; the generator in this PR emits
  it. — <https://www.mintlify.com/docs/components/cards>,
  <https://www.mintlify.com/docs/components/steps>
- **AI surfaces are automatic:** `/llms.txt` and `/llms-full.txt` (also under `/.well-known/`),
  a Markdown copy of every page at its URL with `.md` appended, `Link` and `X-Llms-Txt` HTTP
  headers, and MCP server discovery at `/.well-known/mcp/server-card.json`. Oversized indexes
  split under `/_llms/`. — <https://www.mintlify.com/docs/ai/llmstxt>
- **Monorepo deployment:** dashboard → Git Settings → *Set up as monorepo* → path `/docs`, no
  trailing slash; one `docs.json` per deployment. — <https://www.mintlify.com/docs/deploy/monorepo>
- **Local preview:** the CLI package is `mint` (`npm i -g mint`, Node 20.17+), `mint dev` to
  preview, `mint update` to upgrade. — <https://www.mintlify.com/docs/installation>

### (b) Python API reference: no native support; generate MDX with griffe

Mintlify's API reference support is OpenAPI and AsyncAPI, and the playground page names no
other source (<https://www.mintlify.com/docs/api-playground/overview>). The request for
docstring-generated pages, Mintlify discussion #1639, had no Mintlify response and only
community workarounds as of December 2025
(<https://github.com/orgs/mintlify/discussions/1639>).

**Decision: a script of this repository's own, `tools/docs_audit/render_api.py` (session 3),
loads the package with griffe and emits one MDX page per frozen public name into
`docs/reference/api/`.** Reasons:

- griffe parses Google-style docstrings into a data model with named sections (arguments,
  raises, returns, examples), which is what the Reference page needs to turn into fields; it
  is the extractor behind mkdocstrings, so the docstring conventions it wants are the common
  ones. — <https://mkdocstrings.github.io/griffe/>
- pdoc renders HTML with its own templates; its output is a site, not a set of fragments, and
  bending it to `docs.json` navigation is more work than emitting MDX from a data model.
- griffe2md exists but renders to plain Markdown for MkDocs; the Reference pages want
  Mintlify's field components and frontmatter, so the last mile is ours either way.
- A test asserts every frozen public name has a docstring and a page, so the generator cannot
  silently skip one.

griffe is a documentation-build dependency and goes in the `dev` extra, never in core; the
kernel's dependency rule is unchanged.

### (c) How three infrastructure docs sites are organised

Top-level navigation only, read 2026-09-06.

| Site | Top level |
|---|---|
| Temporal (<https://docs.temporal.io/>) | Quickstart · Developer guide · Production deployment · Cloud · Community and learning. Also `llms.txt` and raw Markdown per page. |
| Cerbos (<https://docs.cerbos.dev/cerbos/latest/index.html>) | Platform · Getting started (what it is, quickstart, tutorial, installation) · API · Policies (one page per policy concept, plus best practices and debugging) · Configuration · Deployment patterns · CLI · Recipes · Release notes and reference |
| Stripe (<https://docs.stripe.com/>) | Start here by use case · Browse by product · Development environment · Agent skills and a terminal reader for the docs |

What carried into the tree above: a task-first *Get started* separate from concept pages
(all three); a *Recipes* / cookbook section distinct from guides and from reference (Cerbos,
Stripe); a CLI page of its own (Cerbos); one page per policy concept rather than one long
policy page (Cerbos); and the docs being readable by an agent — `llms.txt`, per-page Markdown,
an agent-skills entry point — treated as a feature on the front page (all three).

### (d) What generative engine optimisation means for a docs site in 2026

Two sources with opposite emphases, and the tree above follows both:

- **Google's *AI features and your website* guide** (last updated 10 July 2026) says the
  fundamentals are what matter: unique, people-first content with clear structure; crawlable,
  indexable pages with good page experience and semantic HTML; and that `llms.txt`, content
  chunking, special writing styles and extra structured data are *not* needed for Google's
  generative features. —
  <https://developers.google.com/search/docs/fundamentals/ai-optimization-guide>
- **LLMrefs' GEO guide** (undated, read 2026-09-06) gives the practices that help pages get
  cited by assistants: lead each section with a direct answer, keep paragraphs to two or three
  sentences, keep entity names consistent, use lists and comparison tables, cite sources and
  name statistics, and refresh important pages. — <https://llmrefs.com/generative-engine-optimization>
- Mintlify's own position, for the assistants that do read them: `llms.txt`, `llms-full.txt`
  and an MCP server are generated for every site. — <https://www.mintlify.com/docs/ai/llmstxt>

What this means concretely for these pages, and what `docs/STYLE.md` enforces: **answer-first
first paragraphs** and one **definitional sentence** per Concepts page, written for a human;
**consistent entity naming** (CTRLRun, effect key, action hash, AMBIGUOUS); **comparison
tables** on every Compare page; **FAQ structured data** on the FAQ page; quotable, plain
claims with a `CLAIMS.md` row behind each; and the generated `llms.txt` left to Mintlify. What
it does not mean: keyword density, chunked pages, or a second writing style for machines.

## Next

- `docs/STYLE.md` — the rules every page follows.
- `docs/capabilities.yaml` — the single source the capability tables are rendered from.
- `tools/docs_audit/` — the three checks and the generator.
