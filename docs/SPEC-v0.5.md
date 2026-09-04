# CTRLRun v0.5 Specification

This is a **delta over [`SPEC-v0.1.md`](SPEC-v0.1.md), [`SPEC-v0.2.md`](SPEC-v0.2.md),
[`SPEC-v0.3.md`](SPEC-v0.3.md) and [`SPEC-v0.4.md`](SPEC-v0.4.md)**. Everything in all four
still holds; this document states only what v0.5 adds or changes. A reference to an earlier
contract is written `v0.1 §5.4`, `v0.2 §6.9`, `v0.3 §4.3.1` or `v0.4 §1.2`; a bare `§5` is a
section of this document. Section numbers exist in all five, so the prefix is not decoration —
an unprefixed reference to an earlier spec is a defect.

Tests are derived from §8. Public names added here are frozen in §9. Anything not in this
document or in v0.1–v0.4 is out of scope for v0.5.

Words: MUST / MUST NOT / SHOULD are used in the RFC 2119 sense.

v0.4 asked *does it hold in **my** setup?* v0.5 asks a narrower and harder question: **can
somebody else implement this?** The adapter contract is one of the six things v1.0 freezes —
Action schema, Receipt schema, effect semantics, Policy API, StateStore API, **Adapter API** —
so it is written to be lived with rather than revised once somebody tries it. What this
milestone owns is the **contract**, the **conformance kit**, and the requirement that two
adapters exist and that the contract survived writing them.

Three rules govern everything below.

**An adapter exists for exactly one reason.** To route an `APPROVE` through a framework's own
interrupt instead of raising past it. `@protect` already covers anything running in this
process and the gateway covers anything reaching its tools over MCP, so a framework with no
human-in-the-loop primitive of its own has nothing for an adapter to reuse and **does not need
one**. That sentence is the answer to "what about X" for every X, and it is why §1.1's list is
short rather than growing by one each time a framework is named.

**Never a second approval path beside the framework's own.** An adapter reuses `interrupt()`, a
tool-approval interruption, whatever the framework already has. An adapter that grew its own
prompt, its own queue or its own resume token would be a second place a human says yes, and two
places to say yes is one place nobody is watching. §2 enforces this structurally: an adapter
implements a Protocol with one method and **never writes the grant itself** — a single core
provider does, for every adapter (§2.4).

**Adapters ship on their own version line and gate no kernel release.**
`adapters-langgraph-1.0`, never `0.5.1`. An adapter answers to two upstreams and neither is the
kernel roadmap: it breaks when its framework makes a breaking release, on that project's
schedule, for reasons that have nothing to do with what the kernel is doing (§6).

---

## 1. Scope

v0.5 delivers seven things, one build-list item each, plus a release. The `#` column is the
build-list position.

| # | Deliverable | Ships in | Section |
|---|---|---|---|
| 1 | The framework probe, executed against LangGraph and the Agents SDK | not packaged | `v0.4 §7` |
| 2 | The adapter surface `ctrlrun.adapter`, and the `v0.3 §4.3.1` rows | core | §2, §4 |
| 3 | The conformance kit, `ctrlrun[conformance]` | an extra | §5 |
| 4 | The LangGraph reference adapter, `ctrlrun-langgraph` | a separate distribution | §6 |
| 5 | The OpenAI Agents SDK reference adapter, `ctrlrun-openai-agents` | a separate distribution | §6 |
| 6 | A third adapter, written against this document alone | disposable | §8 |
| 7 | Release 0.5.0 | — | — |

The dependency rule of `v0.2 §1.1`, `v0.3 §1` and `v0.4 §1` is unchanged and binding: `pip
install ctrlrun` MUST continue to install nothing but `pyyaml` and `click`. **The adapter
surface itself is core** — it is in the action path, it is stdlib, and an adapter is a separate
distribution that depends on `ctrlrun` rather than the other way round. The **conformance kit**
is not core: it is test machinery, it needs `pytest`, and `v0.4 §1`'s argument for putting
verify in core does not transfer. Verify is a tool an *operator* runs against a deployment;
the kit is a tool an *adapter author* runs against an adapter, and there is exactly one of
those per adapter.

### 1.1 What an adapter is not, stated before anything else

`v0.4 §1.2` put its list of non-guarantees before its list of guarantees, and this document
does the same, for the same reason: the list matters more than the feature does.

- **Not a second approval path.** The framework's own primitive is the approval path. An
  adapter that prompted, queued, or minted a resume token of its own would be a second place a
  human says yes. §2.4 makes this structural rather than advisory — the adapter returns the
  answer and a single core provider records it.
- **Not a second composer of the kernel.** `Control` is the only module that composes the
  others (`ARCHITECTURE.md` §6). An adapter that reserved, committed or granted for itself
  would be a second implementation of `v0.1 §5.5`'s asymmetry, which is the one rule in this
  codebase that must not drift. §2.3 lists what an adapter may never call.
- **Not a way to reach past `Control`.** Every check `v0.3 §4.3.1` requires runs, in the order
  that table fixes, because the adapter's only route to an effect is `Control.execute` (§4).
- **Not required for a framework with no HITL primitive.** There is nothing to reuse, and
  `@protect` already covers it.
- **Not a place where an adapter supplies a principal.** It **sees** one and never supplies one
  (§4.2).

**Three ways in, and only one of them is an adapter.**

| Way in | The problem it solves | When you need it |
|---|---|---|
| `@protect` | Anything running in this Python process, today, with no framework support at all: a raw OpenAI call, a LangChain tool, a Celery task — a decorated function. | The default. Most readers arriving at this document need this and nothing else. |
| `ctrlrun gateway` | Anything reaching its tools over MCP, in any language, with no agent change. | The agent is not Python, or is not yours to edit. |
| An adapter | Routing an `APPROVE` through the framework's own interrupt instead of raising `ApprovalRequired` past it. | The framework has a human-in-the-loop primitive and you want the human to answer where its users already answer. |

An adapter buys **one** thing over `@protect`: where the framework's interrupt exists, the
human answers inside it. Everything else — the policy, the authority evaluation, the exact
binding, the reservation, the receipt — is identical, because it is the same `Control` doing it.

### 1.2 What was read

Read on **2026-09-04** unless a document states otherwise.

- `v0.1 §4.3` (`ApprovalProvider`), `§5.5` (the outcome asymmetry), `§7`, `§8`;
  `v0.2 §6.9` (`Suspended` / `Control.resume`), `§10`, `§11`; `v0.3 §2.3`, `§3`, `§4.3.1`,
  `§5.6.1`, `§6.5`, `§10`, `§11`; `v0.4 §1.2`, `§7`, `§12`.
- `docs/ROADMAP.md` — the v0.5 bullet and the "Adapters — their own version line" section.
  **The v0.5 bullet is wrong about the mechanism and is corrected in the same commit as this
  document** (§3.1).
- `docs/ARCHITECTURE.md` §6, `docs/THREAT_MODEL.md`.
- **LangGraph** — `interrupt()`, `Command(resume=...)` and checkpointers
  (<https://langchain-ai.github.io/langgraph/>). **OpenAI Agents SDK** — `needs_approval`,
  `RunResult.interruptions`, `RunState.approve` / `reject`
  (<https://openai.github.io/openai-agents-python/>). Both are read for *shape*: what each
  framework's primitive hands the adapter and what it hands back. Item 1 measures the
  behaviour, and §7 requires each adapter's README to record where its framework's behaviour
  is visible through this contract.

**No compliance, conformance, certification or alignment claim** is made in this document or in
any adapter's README. "Conformance kit" names a suite of this repository's own acceptance
tests, run against an adapter, and §5.1 says so on its first line.

---

## 2. The adapter surface

### 2.1 A Protocol, and the argument for one

The surface is a `typing.Protocol` and not a base class, and the reason is not style.

A base class is a **channel for the kernel to change an adapter's behaviour later without its
author's consent**. A method added to it with a default implementation runs in every adapter
that inherited it, at the next `pip install --upgrade ctrlrun`, in a distribution the kernel
does not version and did not test. That is precisely the thing an adapter's supported-kernel
range (§6.3) exists to make visible, and a base class routes around it.

A Protocol cannot do that. It is a shape checked statically; nothing is inherited, so nothing
arrives uninvited. When the shape must change, the change is visible as a type error in the
adapter's own CI against the kernel version it declares — which is where an adapter author can
act on it.

This is one of v1.0's six frozen contracts, so the thing that gets frozen is the narrower one.

### 2.2 The names

```python
# ctrlrun/adapter.py — core (stdlib only), re-exported from `ctrlrun`

@dataclass(frozen=True)
class PendingApproval:
    """What the framework's interrupt is handed (§3.2). JSON-safe by construction."""

    request_id: str
    action_id: str
    action: str                      # the action name, e.g. "stripe.refund"
    action_hash: str
    arguments: Mapping[str, Any]     # the action's canonical arguments
    resource: str | None
    environment: str
    agent: str
    user: str | None
    created_at: datetime
    expires_at: datetime

    def to_dict(self) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ApprovalAnswer:
    """What the framework's interrupt returns: a human's answer, and who gave it (§3.3)."""

    granted: bool
    approver: str
    action_hash: str | None = None   # where the framework can carry it back; §3.4


@runtime_checkable
class FrameworkInterrupt(Protocol):
    """One framework's human-in-the-loop primitive, and nothing else (§2.1)."""

    #: The framework's name, as it appears in a conformance report. Non-empty.
    framework: str

    def interrupt(self, pending: PendingApproval) -> ApprovalAnswer: ...


class InterruptApprovalProvider:
    """The one place an adapter's answer becomes a grant (§2.4). Core, not per adapter."""

    def __init__(
        self,
        store: ApprovalStore,
        interrupt: FrameworkInterrupt,
        *,
        clock: Callable[[], datetime] = ...,
    ) -> None: ...

    def request(self, action: Action, ttl: timedelta) -> ApprovalRequest: ...
    def wait(self, request_id: str, timeout: timedelta | None) -> Approval | None: ...


def banner(control: Control) -> None:
    """Log the `v0.3 §6.5` observe banner once per `Control`. An adapter MUST call it (§3.6)."""
```

`InterruptApprovalProvider` satisfies `v0.1 §4.3`'s `ApprovalProvider`. That is the whole
mechanism: **an adapter is an approval provider whose `wait()` routes through the framework's
own primitive**, and everything else about the round trip is the kernel doing what it already
does.

`PendingApproval` MUST be JSON-safe: `to_dict()` returns only `str`, `int`, `bool`, `None`,
`list` and `dict[str, ...]`, with the two timestamps as ISO-8601 strings. A framework
checkpoints the interrupt payload, and a payload that would not serialize is one that works
until the first restart.

`ApprovalAnswer.approver` MUST be a non-empty string, and it is what lands on the receipt's
`approver` field and in `APPROVAL_GRANTED`. An adapter that cannot name who answered writes
what it does know — `"langgraph:interrupt"` — and never `""` and never `None`: an approval with
no approver is evidence that answers the wrong question.

### 2.3 What an adapter is handed, what it must call, what it may never call

**Handed.** A `PendingApproval`, and nothing else. It is a value: it carries the action's name,
its canonical arguments, its resource, its environment, the principal's `agent` and `user`, the
`action_hash`, and the request's expiry. It does **not** carry the `Action` object, the
`Control`, the store, or the executor — nothing an adapter could act on rather than display.

**Must call.** `Control.execute` (or `@protect`, which calls it) for every action, and
`Control.evaluate` where the framework needs the decision *before* it invokes the tool (§3.5).
Nothing else in the kernel is required of an adapter.

**May never call.** Directly or through any object it can reach:

| Never | Because |
|---|---|
| `StateStore.reserve_effect`, `consume_approval`, `consume_approval_and_reserve`, `begin_execution`, `commit_effect`, `fail_effect`, `mark_ambiguous`, `resolve_effect`, `extend_lease` | Reservation and outcome are `v0.1 §5.5`'s asymmetry. A second implementation of it is the one drift this codebase must not have. |
| `StateStore.grant_approval`, `deny_approval` | The grant is written in exactly one place, and it is core's (§2.4). |
| `StateStore.put_delegation`, `revoke_delegation`, `Control.delegate`, `Control.revoke` | An adapter creates no authority. |
| `Control.resume`, and raising `Suspended` | That is the *elicitation* round trip, and an approval is not one (§3.1). |
| Constructing a `Principal` | It sees one and never supplies one (§4.2). |
| `Policy.evaluate` in isolation | `v0.3 §11` — the combined `§4.6` decision or nothing. `Control.evaluate` is that. |

**May call.** `Control.evaluate`, `control.policy.mode`, `ctrlrun.adapter.banner`, and the
read-only accessors `Control.policy`, `Control.store`, `Control.environment`. Reading is not
the problem; writing is.

### 2.4 One place the grant is written, and it is not the adapter

`InterruptApprovalProvider.wait(request_id, timeout)`:

1. Reads the record. Absent → `ApprovalMismatch(reason="unknown")`, exactly as
   `LocalApprovalProvider` does. Not `pending` → the request has already been answered or
   consumed; return per `v0.1 §4.3` (`None` for anything that will never be granted).
2. Checks the request's own expiry against the clock **before** interrupting. An expired
   request is `ApprovalTimeout`: interrupting a human about a request that can no longer be
   granted spends the one scarce resource in the system on nothing.
3. Builds a `PendingApproval` from the record and calls `interrupt.interrupt(pending)`.
4. Applies §3.4's binding check to the returned `ApprovalAnswer`.
5. Records the answer: `store.grant_approval(request_id, answer.approver)` on a grant,
   `store.deny_approval(request_id, answer.approver)` on a refusal — **the same two calls
   `ctrlrun approve` and `ctrlrun deny` make**, and the same two `WebhookApprovalProvider`
   makes when an answer arrives out of band.
6. Re-checks expiry after the interrupt returns, before recording. A human who deliberated past
   the request's expiry has answered a dead request: `ApprovalTimeout`, and nothing is written.
   `ScriptedApprovalProvider` has this rule for the same reason (`v0.1 §4.3`) — a double that
   could grant what the real provider would refuse invalidates the tests that use it.

Step 5 is why rule 2 is structural. No adapter writes a grant, so no adapter can grow a second
approval path by accident, by copy-paste, or by an author who read §1.1 and forgot it. The
adapter's whole contribution is step 3.

`interrupt()` **may exit non-locally.** LangGraph's `interrupt()` raises `GraphInterrupt`, which
the graph catches and checkpoints; the resumed run re-enters `interrupt()` and it returns. The
provider MUST NOT catch it: an exception out of `wait()` propagates through `@protect`'s
`wait=True` handler and out of the wrapped call, no receipt is written, and the action is left
exactly where `v0.1 §6.1` leaves an action awaiting a human — with `APPROVAL_REQUESTED` as its
evidence and nothing else. Catching it would turn a checkpointed interrupt into a swallowed one.

An `interrupt()` that raises anything else is that framework's failure, not a decision: it
propagates, and no grant, no denial and no receipt is written. **An adapter MUST NOT convert a
framework error into an answer.** There is no default, and in particular there is no default
`granted=False`: a denial is a human's answer (`v0.1 §6.2` — `APPROVAL_DENIED` then
`ACTION_DENIED`, not the `APPROVAL_INVALIDATED` umbrella), and manufacturing one from a crashed
interrupt puts a refusal in the evidence log that nobody made.

---

## 3. The approval round trip

### 3.1 `ApprovalRequired` + `with_approval`, not `Suspended` + `resume`

This is the heart of the contract and the decision most likely to be re-litigated, so the
argument is here rather than in a commit message.

**An approval has not started executing, so there is nothing to hold.** `v0.1 §6.1` already
says it: an action awaiting approval has no receipt, and `v0.1 §4.2 A4` says the approval is
consumed in the same store transaction as the reservation — so before a human answers, no
reservation exists. A human deliberating for an hour must pin nothing.

`Suspended` exists for a different shape entirely. `v0.2 §6.9` introduced it for the remote
asking a question **mid-execution**, where the reservation is *already taken* and must stay
taken: the effect record is `EXECUTING`, the lease is extended each round, concurrent
duplicates stay blocked throughout, and `Control.resume` re-evaluates the policy **for the
receipt, not to re-decide**, because refusing there would strand a reservation the remote may
already be acting on. Every one of those sentences is false of an approval gate.

Mapping an approval onto `Suspended` would therefore:

- reserve the effect key **before** a human had answered, holding it for the length of the
  deliberation and blocking every other attempt on that key — including the legitimate one an
  operator makes after denying this one;
- require the lease to be extended across an interval nobody can bound, or let it lapse and
  turn a pending human decision into an `AMBIGUOUS` effect that only a human can now resolve —
  a self-inflicted `v0.1 §5.3 E3`;
- put the resumed leg on `v0.3 §5.6.1`'s "evaluated and recorded, not re-decided" row, so an
  authority revoked while the human deliberated would not refuse the action.

So: **the adapter surfaces `ApprovalRequired` through the framework's interrupt, the resumption
re-presents the same proposal under `with_approval`, and reservation happens only then.** In
mechanism this is `@protect(wait=True)` with an `InterruptApprovalProvider` — the kernel path
that has shipped since v0.1, with the framework's primitive in the provider's `wait()`.

**`docs/ROADMAP.md` is wrong and is corrected in the same commit as this document.** Its v0.5
bullet reads "mapped onto `Suspended` / `Control.resume`, which v0.2 already ships for exactly
this shape". It does not ship for this shape: `Suspended` holds a reservation open and an
approval gate has none to hold. The sentence becomes `ApprovalRequired` / `with_approval`. This
is the treatment `v0.4 §9.4` gave the threat model's sentence about a check verify could not
deliver, and for the same reason — a roadmap that describes a mechanism the spec rejects is a
document that will be believed by the next session to read it.

### 3.2 The round trip, step by step

The framework-independent shape. §3.5 says how each of the two framework shapes reaches it.

```
1.  the agent calls the protected tool
2.  @protect builds the Action        — principal from Control's IdentityProvider (§4.2)
3.  Control.execute                   — principal expiry, authority, policy (v0.3 §4.3.1)
4.    policy says APPROVE
5.    the provider records the request, APPROVAL_REQUESTED is appended
6.    ApprovalRequired is raised      — no receipt, no reservation (v0.1 §6.1)
7.  @protect(wait=True) catches it and calls provider.wait(request_id)
8.  InterruptApprovalProvider builds a PendingApproval and calls the adapter's interrupt()
9.    ─── the framework's own primitive: the human answers where its users already answer ───
10. the answer comes back as an ApprovalAnswer
11. the provider checks §3.4's binding, then grants or denies through the store
12. @protect re-presents the same proposal inside with_approval(request_id)
13. Control.execute decides it again  — expiry, authority, policy, all of it, now
14.   the approval is consumed atomically with the reservation (v0.1 §4.2 A4)
15.   the executor runs; the outcome maps by v0.1 §5.5; one receipt is written
```

Step 13 is not redundant and MUST NOT be optimized away. The decision that governs execution is
the one taken at execution time: an authority revoked, a delegation escalated or a principal
expired while the human deliberated refuses the action here, and `v0.3 §5.6.1`'s "not
re-decided" row does not apply because nothing is held. Re-deciding is cheap and it is the
whole difference between an approval gate and a suspension.

Step 12 re-presents **the same `Action` object** — same `action_id`, same `action_hash` — which
is what `@protect(wait=True)` already does. An adapter that rebuilt the action between the two
legs would be re-deriving the hash from whatever the framework replayed, which is exactly the
mutation §3.4 is about.

### 3.3 What crosses the interrupt

Out: a `PendingApproval`. In: an `ApprovalAnswer`. Nothing else, in either direction.

In particular, **no continuation token, no resume id, no correlation key of the adapter's
own**. `v0.2 §6.9.1` had to reason at length about `requestState` because MRTR spans two HTTP
requests with no identity between them; an approval round trip has an identity already, and it
is the `request_id` in `PendingApproval`. Minting a second one would be the "own resume token"
that §1.1 forbids, and would be a second thing to keep single-use.

### 3.4 What the approval binds to, and where that is prevention rather than attribution

`v0.1 §4.2 A1` is unconditional and the adapter does not weaken it: the approval consumed at
step 14 is the one created at step 5, from the action executing at step 15, and a mismatch is
`ApprovalMismatch` with the approval left `granted`. That much holds for every adapter, on every
framework, with no cooperation from either.

What crossing an interrupt adds is a second question: **does the payload the human read describe
the action that then runs?** The framework replays the call from its own checkpoint, and whether
that replay is faithful is the framework's property and not the kernel's.

So the rule is split, and each half says which kind of guard it is:

- **Where the framework's resumption returns a value the adapter can inspect**, the adapter MUST
  put the `action_hash` in the interrupt payload — it is a field of `PendingApproval`, so this
  costs nothing — and the provider MUST refuse an `ApprovalAnswer` whose `action_hash` is set
  and does not equal the request's: `ApprovalMismatch(reason="mismatch")`, `APPROVAL_INVALIDATED`,
  nothing granted. **This is prevention**, and it is checked in core (§2.4 step 4) rather than
  in each adapter.
- **Where the framework's resumption carries no value the adapter can inspect**,
  `ApprovalAnswer.action_hash` is `None` and the binding across the interrupt is **the
  framework's checkpoint, not CTRLRun's hash**. That is *attribution*: evidence will show what
  was approved and what ran, and a divergence is findable afterwards, not refused beforehand.
  The adapter's README MUST say so, in the §7 section that names every place its framework's
  behaviour is visible through this contract, and MUST NOT describe it as prevention.

A `None` is therefore a declaration, not an omission, and the conformance kit treats it as one:
the mutation suite is reported `not_applicable` with the reason `the framework's resumption
carries no action_hash`, never `pass`. `v0.4 §1`'s rule holds one level up — **not applicable
is not a pass** — and a kit that reported a green mutation suite for an adapter that cannot
check it would be the false-green problem in its purest form.

An `ApprovalAnswer` whose `action_hash` is set and *matches* is the ordinary case and needs no
comment. An adapter MUST NOT set it to the request's hash when the framework did not carry one
back: that is manufacturing the check, and it is the one way to turn attribution into a lie
about prevention.

### 3.5 The two framework shapes

Both reference adapters exist to find out whether one contract covers both, and it does — but
the two shapes reach the round trip differently, and the difference is in the contract rather
than worked around in one of the adapters.

**Resumed in place** (LangGraph). The tool runs, the interrupt raises out of it, the framework
checkpoints, and the resumed run re-enters the same code path with the answer available. Steps 1
to 15 happen twice: the first pass exits at step 9, the second returns from it. `ApprovalRequired`
is raised on both passes and a request is created on both — the first is orphaned and expires,
which costs a row and nothing else. The adapter needs nothing but the provider.

**Decided before invocation** (OpenAI Agents SDK). The framework asks whether a tool call needs
approval *before* it invokes the tool, surfaces its own approval item, and invokes the tool only
after a human answers. The adapter answers that question with **`Control.evaluate(action)` and
nothing else**: `APPROVE` → yes, anything else → no. `evaluate` is the right call and the only
one — it returns the combined `v0.3 §4.6` decision, it reads the store, and it **writes
nothing**, so a predicate the framework may call more than once leaves no events behind and
creates no request.

A `DENY` at that predicate returns "no approval needed" and lets the tool be invoked, so that
`Control.execute` denies it with a receipt, an `ACTION_DENIED` and the exception the caller
catches. Refusing inside the predicate would refuse without evidence, and `v0.3 §4.3` is
explicit that a denial with a principal to attribute it to belongs in the evidence log.

Nothing in either shape needs a new kernel entry point, and that is a deliberate outcome rather
than a happy accident: the surface v1.0 freezes is one Protocol, one dataclass pair and one
provider, and every check that matters is `Control.execute`'s, unchanged.

### 3.6 `mode: observe`

The question the build brief left open, settled here with its argument.

**An adapter never interrupts in observe mode.** `v0.3 §6.2` runs every real decision and
executes anyway, recording what *would* have been blocked; `ApprovalRequired` is never raised,
so `wait()` is never called and the framework's primitive is never reached. There is nothing for
an adapter to do differently, and an adapter that synthesized an interrupt in observe mode would
be asking a human to approve something that is going to run either way.

**An adapter MUST NOT print.** `v0.3 §6.5` puts the banner on stderr for every CLI command that
loads the operator's policy, and an adapter is not a CLI command: it is inside somebody else's
loop, and it may have no stream that reaches a human at all. Printing into a framework's channel
is at best noise and at worst a corrupted stream.

**An adapter MUST log the banner once per `Control`**, on the `ctrlrun` logger at `WARNING`,
when it attaches. A deployment that has been observing for six months is exactly the one that
line is for, and an adapter that said nothing would be the quietest place in the system to
forget. `ctrlrun.adapter.banner(control)` does it — once per `Control` object, with the wording
`v0.3 §6.5` fixed, so no adapter has to remember the sentence and no two adapters say it
differently. It is a no-op under `mode: enforce`.

**The conformance kit refuses an observing `Control`** and reports every approval-routing suite
`not_applicable — mode: observe`, with the reason on the report. Running the suites in a
synthetic enforce mode would report guarantees about a configuration nobody deployed —
`v0.4 §3.8`'s decision, one level up — and running them as-is would report a wall of failures
that are true and useless.

### 3.7 An adapter reaches no network of its own

An adapter opens no socket. The framework does, the operator's executor does, and neither is the
adapter's. The conformance kit runs with sockets refused (`v0.4 §3.7`'s discipline, T133), so an
adapter that phoned anywhere — a telemetry ping, a version check, a hosted approval service —
fails the kit rather than being noticed later.

### 3.8 An adapter has no flag that relaxes a check

`v0.4 §3.9`'s rule, restated because it is the same rule and an adapter is a more tempting place
to break it. No argument, no environment variable, no constructor keyword that makes an
adapter's `Control` behave differently from the operator's: no `skip_approval`, no
`auto_approve`, no `dry_run`, no "development mode" that grants. The moment one exists, the thing
being verified is not the thing that ships.

The one thing an adapter may configure is **how it reaches its framework's primitive** — which
node, which context key, which callback — because that is the framework's shape and not a
CTRLRun check.

---

## 4. The entry-point rows

### 4.1 `v0.3 §4.3.1` gains two rows

`v0.3 §4.3.1` exists because the expired-credential hole was a **missing enumeration**, not a
missing check, and it says so: *"A new entry point is a specification amendment before it is
code."* An adapter is a new way in. Two adapter kinds, two rows, each stating what it does about
each column **before** a line of either is written.

The rule the table fixes is restated as binding here, unchanged: **principal validity and
expiry, then authority, then policy.**

| Entry point | Builds an `Action` | Resolves identity | Evaluates authority |
|---|---|---|---|
| An adapter's protected tool → `@protect` → `Control.execute` | yes — `@protect` does, from the bound call | yes (`v0.3 §3.2`), from the `Control`'s provider | yes, before the approval gate |
| An adapter's pre-invocation predicate → `Control.evaluate` | no — the adapter builds it, or `@protect` does | no; it is the in-process trust boundary (`v0.3 §3.1`), exactly as a directly-built `Action` is | yes — the combined `v0.3 §4.6` decision, and it writes nothing |

Neither row is a new *check*. Both are existing rows of that table reached through a framework,
and that is the point: an adapter's only route to an effect is `Control.execute`, so an adapter
that skipped a check would have to have reimplemented the kernel to do it. §8's T126–T128 assert
the order **by observation** — the events an action leaves — and not by reading the source.

`InterruptApprovalProvider` is deliberately **not** a row. It creates no action and evaluates
nothing; it records a human's answer, which is what `ctrlrun approve` does, and `ctrlrun approve`
is not a row either.

### 4.2 An adapter sees the principal and never supplies it

The principal comes from the `Control`'s `IdentityProvider`, exactly as at every other entry
point. **No name on the adapter surface takes a principal, an agent, a user, or a claim**, and
that is checkable rather than advisory: `PendingApproval` carries `agent` and `user` as
**strings, for display**, and there is no constructor, keyword or callback anywhere in §2.2 that
puts one back in.

An adapter that accepted a principal argument is `--principal-from-client-info` reborn, which
`v0.3 §8.1` removed by name and `v0.3 §8.4` then removed again from the ACS hook after it turned
up there wearing different clothes. A framework's session object knows a user id; that is the
framework's word for it, and a self-reported name cannot be an authorization input. An operator
who wants the framework's identity to count wires an `IdentityProvider` that reads it —
explicitly, at the one seam that exists for it, where it is visible in the deployment.

`PendingApproval.agent` and `.user` are there so a human sees who is asking. They are outputs.

---

## 5. The conformance kit

### 5.1 What it is, and what it is not

`ctrlrun.conformance` runs **this repository's own acceptance tests** — the suites of `v0.1 §7`
and `v0.3 §10` — against an adapter, through the surface §2 defines. It is not a conformance
programme, it certifies nothing, and passing it is not a claim about an adapter's quality. It
answers one question: *does an action driven through this adapter get the same refusals as an
action driven through `@protect`?*

It ships as **`ctrlrun[conformance]`**, in this repository. The arguments, since a build prompt
is not where a decision should live:

- **Core must not carry test machinery.** The kit needs `pytest`; `pip install ctrlrun` stays
  `pyyaml` and `click` (`v0.2 §1.1`).
- **A third distribution would be a third thing to version.** The kit tracks the kernel's
  semantics — it is the kernel's acceptance tests — so it belongs to the kernel's version, and
  an adapter author installs one extra rather than tracking a separate release line.
- **`v0.4 §1`'s argument for core does not transfer.** Verify is run by every operator against
  every deployment, so an extra would mean half of them never run it. The kit is run by an
  adapter author, once per adapter, in that adapter's CI. There is no population of people who
  need it and will not install it.

`import ctrlrun` MUST NOT import `ctrlrun.conformance` (T134), for `v0.4 §1`'s reason: a testing
tool in the execution path is a dependency nobody meant to take.

### 5.2 What an adapter hands the kit

```python
# ctrlrun/conformance/__init__.py — ctrlrun[conformance]

@dataclass(frozen=True)
class CallRequest:
    """One protected call the kit wants driven through the framework."""

    control: Control
    action: str
    arguments: Mapping[str, Any]
    effect: str | None                       # the effect template, or None for a read
    executor: Callable[..., Any]             # the kit's, never the adapter's
    answer: ApprovalAnswer | None            # what the human says if the framework interrupts


@runtime_checkable
class ConformanceAdapter(Protocol):
    """What an adapter implements so the suites can drive it (§5.3)."""

    framework: str

    def invoke(self, request: CallRequest) -> Any: ...


def run(adapter: ConformanceAdapter) -> ConformanceReport: ...
```

`invoke` runs **one** protected call end to end through the framework and returns the executor's
value. Every CTRLRun exception propagates: the kit asserts on `ApprovalMismatch`,
`DuplicateEffect`, `AmbiguousEffect`, `ActionDenied`, `AuthorityDenied` and `NotExecuted` by
type and by `reason`, so an adapter that swallowed one fails rather than passing quietly.

`request.executor` is **the kit's function**, and an adapter that did not call it has not run the
action. The kit counts calls: `v0.4 §1.3`'s positive-control rule holds here too, one level up —
a refusal is satisfied just as well by an adapter that never reached the executor at all.

`request.answer` is `None` for every suite that must not reach a human. An adapter whose
framework interrupts anyway, with `answer=None`, fails: it asked about something the policy
allowed outright.

### 5.3 The suites

Each is a named group, reported per suite and per test.

| Suite | From | What it drives through the adapter |
|---|---|---|
| `kernel` | `v0.1 §7` T1, T4, T5, T6, T8 | Lost response blocks a blind retry · replayed approval · expired approval · unknown action fails closed · `FAILED` permits retry |
| `binding` | `v0.1 §7` T2 | An approval granted for one action does not authorize a mutated one (§3.4; `not_applicable` where the framework carries no `action_hash` back) |
| `concurrency` | `v0.1 §7` T3 | Two attempts on one effect key: exactly one commits |
| `authority` | `v0.3 §10` T66, T70, T74, T78 | No grant · expired grant · a denial by authority leaves no pending approval · delegation escalation |
| `identity` | `v0.3 §10` T60, T63 | No principal, no action · the provider wins over anything the calling code says (§4.2) |
| `observe` | `v0.3 §10` T82 | Refused: every suite `not_applicable — mode: observe` (§3.6) |

A suite reports `pass`, `fail` or `not_applicable` **with a reason**, and the report's
denominator counts applicable suites only. `v0.4 §4.1`'s output rules are the model, and there
is no flag that folds a `not_applicable` into the count.

### 5.4 The broken-adapter fixtures, and why they are written first

A kit that only ever passes is a kit nothing exercises. So the kit's own tests drive it against
adapters that are broken **in one named way each**, and each MUST fail the suite that covers it,
**by name** (T130):

| Fixture | What it does wrong | Fails |
|---|---|---|
| `never-executes` | Returns a plausible value without calling `request.executor` | every suite whose control counts executor calls |
| `swallows-not-executed` | Catches `NotExecuted` and returns `None` | `kernel` — T8's retry, and the whole of `v0.1 §5.5`'s asymmetry |
| `replays-approval` | Keeps the `request_id` and presents it again on the next call | `kernel` — T4 |
| `self-asserts-principal` | Builds a `Principal` from the framework's session and passes it | `identity` — §4.2 |
| `swallows-denial` | Catches `ActionDenied` and returns a value | `kernel` — T6 |
| `interrupts-on-allow` | Routes every call through the interrupt, `APPROVE` or not | `kernel` — an approval nobody asked for |

These are written **before** the reference adapters (build-list order: item 3 precedes items 4
and 5), because "two adapters pass the suites" is this milestone's exit criterion and it means
nothing until the suite can fail.

### 5.5 What the kit does not check

Verify's list (`v0.4 §1.2`) with an adapter's name on it, and it is stated here for the same
reason.

- **Not the framework.** The kit drives the adapter, and a framework that retries a lost
  response is doing what its documentation says. That is item 1's measurement, not a kit failure.
- **Not the operator's executor.** The kit supplies its own, always.
- **Not where the adapter was wired.** An agent that calls the unprotected function bypasses
  CTRLRun entirely, and no amount of driving the adapter finds that.
- **Not whether the adapter is a good one.** No score, no grade, no ranking.

---

## 6. Packaging and versioning

### 6.1 Same repository, separate distributions

`adapters/<framework>/` in this repository, each with its own `pyproject.toml`, published as its
own distribution:

```
adapters/
├── langgraph/            pyproject.toml → ctrlrun-langgraph      → ctrlrun_langgraph/
└── openai-agents/        pyproject.toml → ctrlrun-openai-agents  → ctrlrun_openai_agents/
```

One CI, one review process, and the tag scheme already carries the separate version line. A
repository split waits until an outside maintainer owns an adapter — at which point the split is
the thing that gives them commit rights, and doing it earlier buys nothing and costs a
cross-repository CI matrix.

`pip install ctrlrun` MUST NOT grow. An adapter's distribution depends on `ctrlrun`, never the
reverse, and the wheel `ctrlrun` publishes MUST NOT contain `adapters/` (T136 — `v0.4 §7`'s
`research/` rule, applied again).

### 6.2 Tags

`adapters-<framework>-MAJOR.MINOR` — `adapters-langgraph-1.0`, `adapters-openai-agents-1.0` —
and **never** a kernel version. An adapter answers to two upstreams and neither is the kernel
roadmap: it breaks when its framework makes a breaking release, on that project's schedule.

An adapter's major version tracks whichever of its two upstreams forced the break. Neither
reference adapter gates the 0.5.0 release, and 0.5.0 does not gate either of them.

### 6.3 The two ranges every adapter declares

Each adapter's README states both, as version specifiers, in its first section:

- **Supported kernel range** — e.g. `ctrlrun >=0.5,<0.6`. It is a range and not a floor: this
  contract is frozen at v1.0 and not before, and an adapter that claimed `>=0.5` would be
  claiming compatibility with a surface that has not been written yet.
- **Supported framework range** — e.g. `langgraph >=0.2,<0.3`. Read from what its CI actually
  ran against, never from what its author expects to work.

Both appear in the distribution's `dependencies` as well, so `pip` enforces what the README
states. A README that says one thing and metadata that says another is the version somebody
typed, and `v0.4 §7.3` rule 5 already refused that in the other direction.

---

## 7. What an adapter must document

Each adapter's README, and this list is normative:

1. **The supported kernel range and the supported framework range** (§6.3).
2. **Which HITL primitive it reuses**, by name, with a link to that framework's documentation
   for it, and the date read. Not "LangGraph's interrupt support" but `interrupt()` and
   `Command(resume=...)`.
3. **Which framework shape it is** — resumed in place, or decided before invocation (§3.5).
4. **Whether the framework's resumption carries an `action_hash` back**, and therefore whether
   §3.4's mutation check is prevention or attribution **in this adapter**. If attribution, the
   README says so in those words. This is the sentence a security reviewer reads first, and an
   adapter that buried it would be the false-green problem in prose.
5. **Every place the framework's behaviour is visible through the contract.** Retry defaults,
   what the framework does with a tool that raised, whether it replays a checkpointed call with
   identical arguments, whether the interrupt payload is persisted and where. Item 1 measures
   the first two for the two reference adapters; an adapter written later cites its framework's
   documentation and says what it did not establish, exactly as the probe's README does.
6. **What it does not do**: it is not a second approval path, it grants nothing, and it is not
   required for a framework with no HITL primitive (§1.1).
7. **`ctrlrun.conformance` results** — which suites pass, and which are `not_applicable` with
   the reason. Never a bare "conformant".

---

## 8. Acceptance tests

Each MUST exist as a pytest test carrying the given ID in its name, as `v0.1 §7`, `v0.2 §10`,
`v0.3 §10` and `v0.4 §8` require. All MUST pass for v0.5, and every test from those four
documents MUST still pass.

### Item 2 — The adapter surface and the entry-point rows (§2, §4)

#### T126 — The order is asserted by observation, not by reading the source
A `Control` with an expired principal, an authority section that would deny, and a policy that
would deny, driven through an adapter. The events are `ACTION_PROPOSED` then `ACTION_DENIED`
with `reason="principal_expired"`, and there is **no** `AUTHORITY_DENIED` and **no**
`POLICY_EVALUATED`. Removing the expiry check makes the next reason appear, which is what the
test distinguishes.

#### T127 — An adapter that skips authority is refused
An adapter that builds an `Action` and hands it to a `Control` with an `authority:` section
gets `AuthorityDenied` for a principal with no grant, and the receipt is `denied` with **no**
`POLICY_EVALUATED` (`v0.3 §4.3`). There is no path through §2's surface that reaches an
executor without it.

#### T128 — An authority denial through an adapter leaves no pending approval
`v0.3 §10` T74 driven through the surface: `AUTHORITY_DENIED`, no `APPROVAL_REQUESTED`, and
`store.approvals_for(hash)` is empty. A human is never asked about an action that could not run.

#### T129 — An adapter cannot supply a principal
Asserted structurally: no name in `ctrlrun.adapter`'s public surface accepts a `Principal`, an
`agent`, a `user` or a `claims` argument, checked by `inspect.signature` over every public
callable and dataclass field of the module. `PendingApproval.agent` and `.user` are read-only
strings on a frozen dataclass. And behaviourally: a `Control` with a `StaticIdentityProvider`
resolving `A`, driven by an adapter whose framework session says `B`, produces receipts naming
`A`.

#### T129b — The provider is the only thing that grants
`ctrlrun.adapter`'s public surface contains no call to `grant_approval` or `deny_approval`
outside `InterruptApprovalProvider`, and an adapter that calls either directly is one of §5.4's
broken fixtures. Asserted by driving a fixture that grants for itself and confirming the
`replays-approval` suite fails.

#### T129c — A framework error is never an answer
An `interrupt()` that raises `RuntimeError` leaves the request `pending`, writes no receipt,
appends no `APPROVAL_GRANTED` and no `APPROVAL_DENIED`, and the exception propagates. An
`interrupt()` that raises `GraphInterrupt`-shaped control flow does the same and is not caught.

#### T129d — An expired request is not put to a human
A request whose `expires_at` has passed raises `ApprovalTimeout` from `wait()` **without**
calling `interrupt()` at all (the double counts calls), and one that expires *while* the human
deliberates raises `ApprovalTimeout` and writes no grant.

### Item 3 — The conformance kit (§5)

#### T130 — The kit fails a broken adapter, per suite and by name
Each of §5.4's six fixtures is driven through `conformance.run`, and each fails **the suite
named in that table** and no other. A fixture that failed everything would prove as little as
one that failed nothing.

#### T131 — The kit passes a correct adapter
A minimal in-process reference adapter — no framework, just the surface — passes every suite.
Without it, T130 is satisfied by a kit that fails everything.

#### T132 — Not applicable is not a pass, one level up
An adapter whose `ApprovalAnswer.action_hash` is always `None` reports `binding:
not_applicable` with the reason, is excluded from the denominator, and is listed separately.
There is no flag that folds it in.

#### T132b — The kit refuses an observing Control
A policy with `mode: observe` gives every suite `not_applicable — mode: observe`, the report's
denominator is zero, and the run does not report success.

#### T133 — The kit reaches no network
The whole kit runs in a subprocess whose `sitecustomize` replaces `socket.socket`,
`create_connection` and `getaddrinfo` with a refusal (`v0.4 §3.7`'s discipline), and passes.

#### T134 — `import ctrlrun` imports neither `verify` nor `conformance`
`v0.4`'s T125b, extended: a subprocess imports `ctrlrun` and asserts `ctrlrun.conformance` is
absent from `sys.modules`, beside `ctrlrun.verify`, `httpx`, `jwt` and every `opentelemetry`
module.

#### T134b — Core installs no conformance dependency
`pip install ctrlrun` installs `pyyaml` and `click` and nothing else; `import
ctrlrun.conformance` without the extra raises `MissingDependency` naming
`pip install ctrlrun[conformance]` (`v0.2 §10` T30's shape).

### Items 4 and 5 — The reference adapters (§3.5, §6, §7)

#### T135 — Each reference adapter passes the kit
`ctrlrun.conformance.run` against each, with every suite `pass` or `not_applicable` with a
reason, and the results in each adapter's README (§7 item 7). Run with the framework installed;
skipped **by name** where it is not, so a green run with a missing framework cannot look like a
pass (`v0.4 §7` T123's rule).

#### T135b — Each reference adapter reuses its framework's primitive and reimplements none
Asserted by inspection of the adapter's own source: it imports the framework's primitive by
name, and it contains no prompt, no queue, no polling loop and no token of its own. And
behaviourally: the human's answer arrives through the framework's documented resumption API and
through no other channel.

#### T136 — `adapters/` is not packaged
`python -m build --wheel` for `ctrlrun` produces a wheel containing no `adapters/` path and no
`ctrlrun_langgraph` or `ctrlrun_openai_agents` module, and `research` stays unimportable
(`v0.4 §8` T124b, unchanged).

#### T137 — Each adapter's declared ranges are what its CI ran
The kernel range and framework range in each adapter's README parse as version specifiers, match
its `pyproject.toml` `dependencies`, and contain the versions its CI job installed. A version
somebody typed is a version that was true once.

### Item 6 — The blind implementation (§8)

#### T138 — A third adapter, written against this document alone
Exists, passes the kit, and its author's list of questions this document could not answer is
recorded — in `docs/adapters.md`, with each answered by an edit to `SPEC-v0.5.md` in the same
PR. **The list is the deliverable and the adapter is disposable**: a contract that only its
author can implement is not a contract. If the list cannot be emptied, v0.5 is not done.

### Item 7 — Release

#### T139 — The README's negative sentence exists and is true
The README's adapter section says, in the paragraph that introduces adapters, when you do **not**
need one, and the sentence names `@protect`. Asserted like every other README claim in this
repository: a test reads the file.

---

## 9. Public API additions (frozen for v0.5)

This is one of v1.0's six frozen contracts. Everything below is being promised for a long time,
which is why there is so little of it.

```python
# ctrlrun/__init__.py — added
from .adapter import (
    ApprovalAnswer,
    FrameworkInterrupt,
    InterruptApprovalProvider,
    PendingApproval,
    banner,
)
# ctrlrun[conformance], lazily importable, NOT re-exported at package import:
#   ctrlrun.conformance.run
#   ctrlrun.conformance.CallRequest
#   ctrlrun.conformance.ConformanceAdapter
#   ctrlrun.conformance.ConformanceReport
#   ctrlrun.conformance.SuiteResult
```

```python
@dataclass(frozen=True)
class PendingApproval:                       # §2.2
    request_id: str
    action_id: str
    action: str
    action_hash: str
    arguments: Mapping[str, Any]
    resource: str | None
    environment: str
    agent: str
    user: str | None
    created_at: datetime
    expires_at: datetime
    def to_dict(self) -> dict[str, Any]: ...

@dataclass(frozen=True)
class ApprovalAnswer:                        # §2.2
    granted: bool
    approver: str
    action_hash: str | None = None

class FrameworkInterrupt(Protocol):          # §2.1 — a Protocol, not a base class
    framework: str
    def interrupt(self, pending: PendingApproval) -> ApprovalAnswer: ...

class InterruptApprovalProvider:             # satisfies v0.1 §4.3's ApprovalProvider
    def __init__(self, store: ApprovalStore, interrupt: FrameworkInterrupt, *, clock=...): ...
    def request(self, action: Action, ttl: timedelta) -> ApprovalRequest: ...
    def wait(self, request_id: str, timeout: timedelta | None) -> Approval | None: ...

def banner(control: Control) -> None: ...    # §3.6
```

**And no other public name.** No `Control` method, no `StateStore` method, no event type, no
error type, no schema, no CLI command and no policy key. That an adapter needs none of those is
§3.5's finding and the strongest evidence the contract is the right size: a surface that had
required a new entry point would have been a surface that reached past `Control`.

**Nothing is removed and nothing changes meaning.** `v0.1 §8`, `v0.2 §11`, `v0.3 §11` and
`v0.4 §9` are untouched. No schema version changes: `ctrlrun.policy/v3`, `ctrlrun.receipt/v2`,
`ctrlrun.action/v1`, `ctrlrun.inspection/v2`, `ctrlrun.verify/v1` and `ctrlrun.guarantees/v1` all
stand, and **v0.5 adds no table and no column to any store** — an adapter writes through
`Control` and `Control` writes what it always wrote.

**Module map** (`ARCHITECTURE.md` §6) gains two rows, and the dependency direction is unchanged
— downward only, with `Control` the only module that composes the others:

| Module | Owns | Must not know about |
|---|---|---|
| `adapter.py` | `FrameworkInterrupt`, `PendingApproval`, `ApprovalAnswer`, `InterruptApprovalProvider`, `banner` | policy, authority, effect state, executors, any framework |
| `conformance/` | the suites, the fixtures, the report — `ctrlrun[conformance]` | the gateway, `otel`, `jwt_identity`; anything from another extra |

`adapter.py` imports `action.py` and `approval.py` and nothing else from the kernel; `banner`
takes a `Control` and reads `control.policy.mode`, which is a read. `conformance/` sits **above**
`control.py`, beside `verify/` and `cli/`: it composes the kernel the way an application does,
and nothing in the kernel imports it.

**`docs/ROADMAP.md` is corrected** in the same commit as this document (§3.1): the v0.5 bullet's
`Suspended` / `Control.resume` sentence becomes `ApprovalRequired` / `with_approval`. This is the
`v0.4 §9.4` treatment, and it is recorded here so the correction is findable from the spec rather
than only from a diff.

---

## 10. Fail-closed table for v0.5

| Condition | Result |
|---|---|
| `interrupt()` raises anything | Propagates. No grant, no denial, no receipt. The request stays `pending` (§2.4) |
| `ApprovalAnswer.action_hash` set and mismatched | `ApprovalMismatch(reason="mismatch")`, `APPROVAL_INVALIDATED`, approval untouched (§3.4) |
| `ApprovalAnswer.approver` empty | `InvalidArgument`. An approval with no approver is evidence that answers the wrong question (§2.2) |
| The request expired before the interrupt | `ApprovalTimeout`; `interrupt()` is not called (§2.4) |
| The request expired during the interrupt | `ApprovalTimeout`; nothing is written (§2.4) |
| The request is not `pending` | `v0.1 §4.3`'s rule, unchanged: `None` for anything that will never be granted |
| An adapter reaches an executor without `Control.execute` | Impossible through §2's surface; a broken fixture, and the kit fails it (§5.4) |
| `mode: observe` | The interrupt is never reached; the banner is logged once; the kit reports every suite `not_applicable` (§3.6) |
| The framework carries no `action_hash` back | `binding` is `not_applicable` with the reason. **Never `pass`** (§3.4, §5.3) |
| A framework is not installed | Its adapter's kit run is skipped **by name**, and the skip is in the report (T135) |
| An adapter tries to supply a principal | There is no name on the surface that takes one (§4.2, T129) |

---

## 11. Explicitly out of scope for v0.5

Everything in `v0.1 §9`, `v0.2 §12`, `v0.3 §13` and `v0.4 §11` that v0.5 does not deliver, and
specifically:

- **Adapters beyond the three.** Google ADK, the Microsoft Agent Framework,
  the Claude Agent SDK, PydanticAI, CrewAI, Strands and LlamaIndex are on the adapters track.
  They arrive on demand rather than in this list's order, and none gates a kernel release.
- **A LangChain adapter.** LangGraph owns the primitive and LangChain's agent path runs on
  LangGraph, so one adapter covers both. A separate one buys only the legacy `AgentExecutor`
  path.
- **A conformance programme, a badge, a certification, or the word "certified" anywhere.**
- **Binding an approval across a framework's checkpoint by any mechanism the framework does not
  already provide.** §3.4 says which half is prevention and which is attribution, and inventing
  a token to close the gap is the second approval path under another name.
- **Authority propagation across agent hops, A2A, or a task-bound delegation an adapter creates.**
  v0.7.
- **`Suspended` / `Control.resume` for an approval.** §3.1.
- **Any new `Control` method, store method, event, error, schema, policy key or CLI command.** §9.
- Publishing framework results in any item but item 1, and item 1 publishes only what the
  maintainer read first (`v0.4 §7.4`).
- Postgres, migrations, signed receipts, dashboards, anything in `VISION.md`.

---

## 12. What building v0.5 settled

*Filled in as items land, in the item that settles each. `SPEC-v0.4.md §12` is the format: one
subsection per question the drafting could not close, each stating what the code decided and
which section carries it.*
