# CTRLRun and the Agent Control Standard

What was read, what maps, what does not, and how the adapter is built.

## What was read

The [Agent Control Standard](https://agentcontrolstandard.org/), a project of the OWASP GenAI
Security Project, in
[`GenAI-Security-Project/agent-control-standard`](https://github.com/GenAI-Security-Project/agent-control-standard)
at commit **`c7ad162f69386daac94b89073e3b751e8cdf28b2`** (2026-08-11, *"Raise the Python floor
to unblock Dependabot security updates"*). The repository was at release **v0.1.1**, with the
specification version decoupled from the release version at commit `1af1f92` (2026-08-11) and
tracked separately at **v0.1.0**. Code is Apache-2.0; documentation is CC BY-SA 4.0.

Specifically:

| File | What it gave |
|---|---|
| `specification/ACS/acs_schema.json` | the v0.1.0 aggregator; 22 hooks; `oneOf` a RequestEnvelope or a ResponseEnvelope |
| `specification/v0.1.0/request-envelope.json` | JSON-RPC 2.0; method namespaces; `AcsParams` and its `metadata` identity fields |
| `specification/v0.1.0/response-envelope.json` | `AcsResult`, and the five decisions |
| `specification/v0.1.0/hooks/tool-call-request.json` | `steps/toolCallRequest` — `tool`, `arguments`, `operation`, `capability`, `intent` |
| `specification/v0.1.0/hooks/tool-call-result.json` | `steps/toolCallResult` — `exit_status`, `outputs`, `request_id_ref`, `duration_ms` |
| `specification/v0.1.0/ask-details.json` | what an `ask` decision must carry |
| `CONTRIBUTING.md` | DCO required (`git commit -s`); spec changes open a Discussion first; no CLA |

Two things the repository does **not** have at that commit, both of which shaped this work:

- **No reference implementation.** The README's roadmap places a Guardian Agent sample and
  FastMCP instrumentation at v1. There is a `pyproject.toml`, but it serves the docs and
  version-sync tooling. So there is nothing to conform *to* except the schemas, and this
  adapter is written against them directly.
- **No `examples/` directory**, and so no house format for a community example. `examples/acs/`
  therefore follows CTRLRun's own convention.

## What ACS defines

Three layers: **Instrument** (runtime hooks and the Guardian Agent pattern), **Trace**
(OpenTelemetry and OCSF with agent-specific conventions), and **Inspect** (CycloneDX, SPDX and
SWID for a dynamic Agent BOM). Only Instrument is relevant here.

The wire format is JSON-RPC 2.0. A host fires a hook as a request whose `method` is
`steps/<hookName>`; a Guardian answers with an `AcsResult` carrying one of five decisions:

```text
allow · deny · modify · ask · defer
```

`deny` requires `reasoning`. `modify` requires `reasoning` and `modifications`. `ask` requires
`reasoning` and `ask_details`. `defer` requires `reasoning` and `defer_details`.

Of the 22 hooks, CTRLRun answers **two**, and it is worth being explicit that it declines the
other twenty: `SessionStart`, `SessionEnd`, `AgentTrigger`, `TurnStart`, `TurnEnd`,
`UserMessage`, `AgentResponse`, `KnowledgeRetrieval`, `MemoryContextRetrieval`, `MemoryStore`,
`PreCompact`, `PostCompact`, `SubagentStart`, `SubagentStop`, `SkillRegister`, `SkillLoad`,
`SkillUnload`, `SystemPing`, `AgbomSnapshot`, `AgbomChanged`. Those are checkpoints about what
the model is thinking, remembering or composed of. CTRLRun's product rule is that it decides
actions that can affect the real world, and nothing else — so an unanswered method returns a
JSON-RPC error in ACS's reserved range rather than an opinion.

## The mapping, hook by hook

### `steps/toolCallRequest` → build the Action, decide it, take the reservation

| ACS field | CTRLRun |
|---|---|
| `params.metadata.agent_id` | `Principal.agent` — **only where no `identity` provider is configured**. With one it is **ignored**: not merged, not a fallback, not compared (SPEC-v0.3 §8.4) |
| `params.metadata.user_context.user_id` | `Principal.user`, under the same rule |
| `params.metadata.environment` | **ignored.** `Action.environment` is the hook's own configuration (SPEC-v0.3 §2.5, §8.4) |
| the transport's request headers | `IdentityContext.headers`, which is what an `identity` provider reads. `AcsControlHook.handle(envelope, headers=...)` |
| `payload.tool.name`, `payload.tool.provider`, `payload.operation` | `Action.name`, as `<prefix>.<provider>.<tool>[.<operation>]` |
| `payload.arguments` | `Action.arguments`, unwrapped from `{name: {value, provenance}}` to `{name: value}` |
| — *(ACS has no resource field)* | `Action.resource`, from the policy's `resource:` template (SPEC-v0.2 §3) |
| `params.request_id` | the continuation that joins this hook to its result |

**Two fields stopped being read, and it is worth saying why.** Under v0.2 both `agent_id` and
`environment` came off the envelope, on exactly the argument `v0.2 §6.5` made for MCP's
`clientInfo`: a policy could not address the principal, so a self-reported one misattributed a
receipt and could not widen an outcome. SPEC-v0.3 §4 ends that — the principal is an
authorization input now, and a grant may scope to an environment — so a value the caller sets
would let the caller choose what it is authorized as and where. §8.1 removes
`ctrlrun gateway --principal-from-client-info` over the same sentence; the ACS hook was that
flag in a different module.

The consequence is a **required argument**: an `AcsControlHook` built against a `Control` that
holds an `Authority`, with no `identity` provider, raises `InvalidArgument` at construction.
A hook with **no provider** still reads `agent_id`, unchanged, because there is then no
authorization decision for it to widen — and a hook holding an `Authority` cannot be in that
state. The branch is on the *provider*, not on the authority section: configuring one without
an `authority:` section also stops `agent_id` being read, which is the less surprising of the
two possible rules and the one that makes "with a provider, `agent_id` is display data" true
without a footnote.

A configured provider that names nobody is a **refusal** — `deny`,
`reason_codes: ["no_principal"]`, no receipt and no events — and never a fall back to the
envelope: falling back would reach `agent_id` by an easier route than forging a credential. A
credential that was verified and has since **lapsed** is a different refusal and says so:
`reason_codes: ["principal_expired"]`, matching the `decision_reason` on the receipt
`Control` has already written. The two are told apart structurally — one is raised while the
Action is being built, the other by `Control.execute` — and never by reading a message.

**One rule of §8.2 the hook does not import.** §3.1's repeated-identity-header refusal is the
gateway's, and it lives in `do_POST` because that is where a repeated HTTP field is still
visible. `AcsControlHook.handle(envelope, headers=...)` is handed a `Mapping[str, str]`, which
holds one value per name — so whatever built that mapping already chose. A deployment putting
this hook behind an HTTP server is responsible for that check, exactly as it is responsible
for the transport. Stated because it is a real gap rather than an argued omission.

`operation` is part of the name because two verbs on one tool are two actions, and a policy has
to be able to say different things about `create` and `void`.

The decision maps out:

| CTRLRun | ACS |
|---|---|
| `ALLOW` | `allow` |
| `DENY` | `deny` + `reasoning` + `reason_codes` |
| `APPROVE` (`ApprovalRequired`) | `ask` + `reasoning` + `ask_details` |
| `DuplicateEffect` | `deny`, `reason_codes: ["ctrlrun.duplicate_effect", <state>]` |
| `AmbiguousEffect` | `deny`, `reason_codes: ["ctrlrun.ambiguous_effect"]` |
| `ApprovalMismatch` | `deny`, `reason_codes: ["ctrlrun.blocked", <reason>]` |
| `AuthorityDenied` | `deny`, `reason_codes: [<§4.3 reason>]` — it subclasses `ActionDenied`, and the reason is what tells the two apart |
| `IdentityError` | `deny`, `reason_codes: ["no_principal"]`. Deliberately a decision and not a protocol `error`: an error envelope says "the Guardian could not answer", and a platform is free to decide what to do with that |

`ask_details` carries `approver` (`{type: "human", id}`), a `question` naming the request id a
human answers with `ctrlrun approve`, and `timeout_seconds`. All three are required by
`ask-details.json`.

`ask_details.intent_extension` is **not** used. It grants capabilities for `this_request` or
`session`, which is an authority model — CTRLRun has none until v0.3, and a grant it cannot
represent is one it must not claim to honour.

### `steps/toolCallResult` → close the reservation

ACS describes this hook as *"fires after tool execution, before results reach the agent,
serving as an output redaction checkpoint"*. CTRLRun redacts nothing, so it always answers
`allow`; the work is the outcome it records.

`exit_status` is `success | failure | timeout | blocked`. **ACS does not say what any of them
means for the side effect.** This is the fail-closed reading, and it is the same one
SPEC-v0.2 §6.8 applies to MCP:

| `exit_status` | Effect state | Why |
|---|---|---|
| `success` | `COMMITTED` | the only status that asserts the effect happened |
| `blocked` | `FAILED` | a control refused it before dispatch — this is what `NotExecuted` means |
| `failure` | `AMBIGUOUS` | a tool that failed *after* acting and one that failed *before* send the same string |
| `timeout` | `AMBIGUOUS` | the same, and the case this library exists for |
| `failure` **with** `mcp.not_executed_on_error: true` | `FAILED` | an operator's per-tool assertion (SPEC-v0.2 §3.1) |

A result whose `request_id_ref` matches no held reservation — a restarted Guardian, a result
fired twice, one arriving out of order — is logged and nothing is written about an effect.
Guessing which call it meant is how a duplicate gets committed.

## Where ACS is silent

These are not criticisms of ACS; they are the seam. ACS governs *whether an action may
proceed*. It has almost nothing to say about *what happened afterwards*, because that is not
what the Instrument layer was built for.

**1. No effect identity.** `params.request_id` is a per-hook-invocation UUID and
`request_id_ref` links a result to its request. Neither identifies the *effect*: two calls that
would refund the same payment get two unrelated UUIDs. There is no idempotency key, no
`capability`-plus-argument identity, nothing an implementer could use to recognise that a retry
is a repeat. CTRLRun supplies one from the policy's `effect:` template.

**2. `exit_status` is a status of the call, not an outcome of the effect.** Four values, and
the vocabulary itself carries the confusion: `timeout` sits alongside `failure` as though both
were kinds of not-working. The distinction that matters — *did the side effect land?* — has no
representation. A conformant Guardian reading `timeout` has no way to say "unknown, and a
retry is unsafe until a human resolves it".

**3. Nothing binds an approval to the exact action.** `ask_details` carries a question and an
approver. What comes back is a decision on *the request*, and `intent_extension` can widen a
capability for the session. Neither pins the approval to a canonical form of the arguments, so
nothing in ACS prevents an agent from getting `refund(2000)` approved and then calling
`refund(5000)`. CTRLRun binds to `action_hash`, which covers the principal, the arguments, the
resource and the environment.

**4. No terminal unknown state.** ACS's decisions are about the future of a call. There is no
way for a Guardian to record that an effect's outcome is unresolved and that *no* further call
on that effect may proceed until a human says which way it went. `AMBIGUOUS` has no ACS
counterpart, and it is the state most of CTRLRun's design exists to protect.

**5. The Guardian does not execute.** ACS is advisory by construction — the platform runs the
tool. That is a reasonable separation, but it means a Guardian cannot make reserve-and-execute
atomic. The best available is what this adapter does: reserve at the request hook, close at the
result hook, and accept that a platform which never fires the result hook leaves a reservation
to lease-expire into `AMBIGUOUS` by the ordinary path of v0.1 §5.3 E3.

## The adapter's design

`ctrlrun.acs.AcsControlHook`, in `ctrlrun[gateway]` — it needs no new dependency, and it is
kept out of core for the same reason the gateway is: `import ctrlrun` must not grow.

The shape is forced by the seam above. ACS is advisory and CTRLRun is executing, so one action
is split across two hooks and the reservation is held between them. That is exactly the shape
`Suspended` and `Control.resume` were built for in SPEC-v0.2 §6.9 — a reservation held across a
round trip the kernel does not control — so the adapter reuses them rather than reaching for
the store itself:

```text
steps/toolCallRequest
  → Action ← envelope metadata + payload + policy templates
  → Control.execute(action, executor=raise Suspended(request_id), effect_key)
      · policy decided, approval consumed, effect reserved, EXECUTION_STARTED
      · executor suspends: no outcome, no receipt, lease extended, continuation held
  → allow | deny | ask

steps/toolCallResult
  → Control.resume(request_id_ref, executor=report(exit_status))
      · the same outcome mapping execute uses (v0.1 §5.5)
      · commit / fail / ambiguous, one receipt, same action_id and attempt
  → allow
```

`Control` remains the only module that composes the others (ARCHITECTURE §6). The adapter
translates two vocabularies and decides nothing.

**What it does not do.** It does not use `modify` — CTRLRun refuses or permits an action as
proposed, and rewriting an agent's arguments is a different product. It does not use `defer`.
It does not answer the other twenty hooks. It makes no claim of conformance: the schemas are
`v0.1.0`, the repository is a public preview, there is no reference implementation to test
against, and there is no conformance suite. **The words "ACS-compatible" do not appear in this
repository's README, docstrings or CLI output**, and should not until something exists to be
compatible *with*.

## What the OTel attributes would have to become

SPEC-v0.2 §8's sink emits `ctrlrun.*` attributes. ACS's Trace layer extends OpenTelemetry with
its own agent conventions and maps security events to OCSF; `acs_schema.json` carries
`TraceOtelMapping` and `TraceOcsfMapping` definitions for that purpose.

Aligning would mean renaming `ctrlrun.action.name`, `ctrlrun.principal.agent`,
`ctrlrun.decision` and the rest onto ACS's conventions. The cost is not the rename: it is that
receipts already written carry the old names, and a receipt is evidence that has to outlive the
tool that wrote it. Any alignment is therefore additive — emit both, deprecate neither — or it
is a schema version bump on the receipt, which SPEC-v0.2 §11 explicitly does not do.

That work is not in this release, and the mapping table is deliberately not written yet: it
would be a compliance claim with nothing behind it.
