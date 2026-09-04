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
| 3 | The conformance kit, `ctrlrun.conformance` | core, no new dependency (§12.1) | §5 |
| 4 | The LangGraph reference adapter, `ctrlrun-langgraph` | a separate distribution | §6 |
| 5 | The OpenAI Agents SDK reference adapter, `ctrlrun-openai-agents` | a separate distribution | §6 |
| 6 | A third adapter, written against this document alone | disposable | §8 |
| 7 | Release 0.5.0 | — | — |

The dependency rule of `v0.2 §1.1`, `v0.3 §1` and `v0.4 §1` is unchanged and binding: `pip
install ctrlrun` MUST continue to install nothing but `pyyaml` and `click`. **The adapter
surface itself is core** — it is in the action path, it is stdlib, and an adapter is a separate
distribution that depends on `ctrlrun` rather than the other way round. The **conformance kit**
is core too, and adds no dependency. It was planned as an extra on the premise that a kit
needs `pytest`; building it showed the premise was wrong, and §12.1 records the correction.

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

One qualification, made here rather than left for §3.4 to spring on a reader who has already
been told "identical": the exact binding is identical **within a leg**. `@protect` has no
interrupt and nothing to replay across, and an adapter does — so whether the payload a human
*read* describes the action that then runs is prevention only where the framework carries back
what was answered, and attribution where it does not. §3.4 splits it and says which is which.

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
    #: The arguments the human's answer was given against, as the adapter recovered them from
    #: its framework. Required where `FrameworkInterrupt.carries_approved_arguments` is True,
    #: and `None` only where it is False (§3.4). Never the adapter's copy of
    #: `PendingApproval.arguments`: that is manufacturing the check.
    approved_arguments: Mapping[str, Any] | None = None


@runtime_checkable
class FrameworkInterrupt(Protocol):
    """One framework's human-in-the-loop primitive, and nothing else (§2.1)."""

    #: The framework's name, as it appears in a conformance report. Non-empty.
    framework: str

    #: Does this framework's resumption carry back what the human answered against (§3.4)?
    #: A **declaration about the framework**, not a setting: `False` makes the kit report
    #: `binding: not_applicable` with this adapter's reason, permanently and visibly, and it
    #: can never make a check that would otherwise have run not run. `True` makes the
    #: `approved_arguments` check mandatory, and an answer that omits them is refused.
    carries_approved_arguments: bool

    def interrupt(self, pending: PendingApproval) -> ApprovalAnswer: ...


class InterruptApprovalProvider:
    """The one place an adapter's answer becomes a grant (§2.4). Core, not per adapter.

    **The operator constructs it and hands it to `Control`**, exactly as they would a
    `WebhookApprovalProvider`. An adapter never constructs a `Control` and never chooses its
    `identity=`, its `clock=` or its `authority=` (§2.3, §3.8).
    """

    def __init__(
        self,
        store: ApprovalStore,
        interrupt: FrameworkInterrupt,
        *,
        clock: Callable[[], datetime] = ...,
    ) -> None: ...

    def request(self, action: Action, ttl: timedelta) -> ApprovalRequest: ...
    def wait(self, request_id: str, timeout: timedelta | None) -> Approval | None: ...


def needs_approval(
    control: Control,
    action: str,
    arguments: Mapping[str, Any],
    *,
    resource: str | None = None,
) -> bool:
    """Does this call need a human? For a framework that asks before it invokes (§3.5).

    Core does it so no adapter has to: it resolves the principal from `control`, builds the
    Action, and returns True iff `Control.evaluate` says `APPROVE`. It writes nothing and
    creates no request.
    """


def banner(control: Control) -> None:
    """Log the `v0.3 §6.5` observe banner once per `Control`. An adapter MUST call it (§3.6)."""
```

`needs_approval` exists because of a hole an independent review found in this section's first
draft, and the reason is recorded rather than the hole quietly patched. That draft told the
adapter to answer its framework's pre-invocation predicate "with `Control.evaluate(action)`",
and `Action.principal` is a required field with no default (`v0.1 §2.1`): the only way to obey
was `Principal(agent=<the framework's session user>)`, which §4.2 forbids by name and which
`v0.3 §8.1` removed from the gateway and `v0.3 §8.4` then removed again from the ACS hook. It
was the third costume. The predicate is core's now, and the principal never passes through an
adapter's hands.

`Control.resolve_principal(action_name) -> Principal` is promoted from private for it (§9): the
identity seam an adapter may **read** and may not supply. It is `v0.3 §3.2`'s resolution
unchanged — the provider wins where it answers, a decline is refused rather than backfilled once
an `authority:` section is loaded, and a refusal is never backfilled at all.

`InterruptApprovalProvider` satisfies `v0.1 §4.3`'s `ApprovalProvider`. That is the whole
mechanism: **an adapter is an approval provider whose `wait()` routes through the framework's
own primitive**, and everything else about the round trip is the kernel doing what it already
does.

`PendingApproval` MUST be JSON-safe: `to_dict()` returns only `str`, `int`, `bool`, `None`,
`list` and `dict[str, ...]`, with the two timestamps as ISO-8601 strings. A framework
checkpoints the interrupt payload, and a payload that would not serialize is one that works
until the first restart.

`ApprovalAnswer.approver` MUST be a non-empty string — `InvalidArgument` otherwise (T129e) —
and it is what `store.grant_approval` writes on the record, and from there what reaches
`APPROVAL_CONSUMED.data.approver` and the receipt's `approver` field, by the path `Control`
already has (`v0.1 §6.1`). An adapter that cannot name who answered writes what it does know
and never `""` and never `None`: an approval with no approver is evidence that answers the
wrong question.

**It names a channel, not a person**, wherever the framework's primitive does not identify one.
`"langgraph:interrupt"` says where the answer came from, in the same register as `v0.1`'s
`"cli:local"` and `"cli:scripted"`, and no document may describe `approver` as *who* approved —
that is `v0.3 §13`'s "authenticating the approver", which is still out of scope.

### 2.3 What an adapter is handed, what it must call, what it may never call

**Handed.** A `PendingApproval`, and nothing else. It is a value: it carries the action's name,
its canonical arguments, its resource, its environment, the principal's `agent` and `user`, the
`action_hash`, and the request's expiry. It does **not** carry the `Action` object, the
`Control`, the store, or the executor — nothing an adapter could act on rather than display.

**Must call.** `@protect` (which calls `Control.execute`) for every action, and
`ctrlrun.adapter.needs_approval` where the framework needs the decision *before* it invokes the
tool (§3.5). Nothing else in the kernel is required of an adapter.

**May never call.** Directly or through any object it can reach:

| Never | Because |
|---|---|
| `StateStore.reserve_effect`, `consume_approval`, `consume_approval_and_reserve`, `begin_execution`, `commit_effect`, `fail_effect`, `mark_ambiguous`, `resolve_effect`, `extend_lease` | Reservation and outcome are `v0.1 §5.5`'s asymmetry. A second implementation of it is the one drift this codebase must not have. |
| `StateStore.grant_approval`, `deny_approval` | The grant is written in exactly one place, and it is core's (§2.4). |
| `StateStore.put_approval_request` | Creating a request out of band is the first half of a second approval path, and the second half is answering it. |
| `StateStore.append_event`, `put_receipt` | These attack the **evidence** rather than the decision, which is the product's whole output. An adapter holding only `control` can reach `control.store.append_event(Event(type=APPROVAL_GRANTED, data={"approver": "the-cfo"}))` and put a grant nobody made into the log. Every other writer on the protocol was enumerated; these two were the omission, and an independent review found them by trying. |
| `Control.approvals`, and anything reached through it | `control.approvals.wait(<any pending request id>)` drives the provider for a request the adapter was **not handed** — one created by another agent's action, or waiting in an operator's queue — and an adapter's own `interrupt()` answers it. The `may call` list is read-only accessors; this one is not read-only, and it is named here rather than left to the general clause. |
| `StateStore.hold_continuation`, `take_continuation` | The elicitation machinery is reachable without `Control.resume`, so naming only `resume` would be the missing enumeration this project keeps finding (`v0.3 §4.3.1`). |
| `StateStore.put_delegation`, `revoke_delegation`, `Control.delegate`, `Control.revoke` | An adapter creates no authority. |
| `Control.resume`, and raising `Suspended` | That is the *elicitation* round trip, and an approval is not one (§3.1). |
| Constructing a `Principal` | It sees one and never supplies one (§4.2). |
| **Constructing a `Control`** | `Control(identity=..., authority=..., clock=..., environment=...)` is every hole in this table by one route, and the most tempting one: an adapter that built its own `Control` would choose the identity provider, which is §4.2 defeated in a constructor keyword. The **operator** builds the `Control` and hands it to the adapter. |
| `Policy.evaluate` in isolation | `v0.3 §11` — the combined `§4.6` decision or nothing. `needs_approval` and `Control.evaluate` are that. |

**May call.** `ctrlrun.adapter.needs_approval`, `ctrlrun.adapter.banner`, `Control.evaluate`,
`Control.resolve_principal`, and the accessors `Control.policy`, `Control.environment`,
`Control.store` **for reads only** — `get_effect`, `get_approval`, `receipts`, `events`.

`Control.store` is on this list and `StateStore` is a mutable object, so "reading is not the
problem; writing is" is a rule about *calls* and not about *reachability*: the never-list above
is what makes it checkable, and every writer on the protocol is on it. The general clause
—*directly or through any object it can reach*— is not enough on its own, because a rule that
names no methods is a rule the conformance kit cannot check and a reviewer cannot mutation-test.
That is the missing-enumeration shape §4.1 exists to prevent, one level down.

**Who wires it up.** The operator, in the same breath as the policy and the store:

```python
control = Control(policy, store, approvals=InterruptApprovalProvider(store, MyInterrupt()),
                  identity=..., authority=...)
agent = build_my_agent(control)          # the adapter is handed the Control, and builds none
```

This is not a convention, it is the boundary. Everything an adapter must not choose —
the identity provider, the authority document, the clock, the environment, the mode — is
chosen on the line above, by the person who deployed it, in the file they already look at.

### 2.4 One place the grant is written, and it is not the adapter

`InterruptApprovalProvider.wait(request_id, timeout)`:

1. Reads the record. Absent → `ApprovalMismatch(reason="unknown")`, exactly as
   `LocalApprovalProvider` does. Not `pending` → the request has already been answered or
   consumed; return per `v0.1 §4.3` (`None` for anything that will never be granted).
2. Checks the deadline **before** interrupting — the request's own `expires_at`, or `timeout`
   from now, whichever is sooner. Already past → `ApprovalTimeout`, and `interrupt()` is **not
   called**: putting a request to a human that can no longer be granted spends the one scarce
   resource in the system on nothing.
3. Builds a `PendingApproval` from the record and calls `interrupt.interrupt(pending)`.
4. Applies §3.4's binding check to the returned `ApprovalAnswer`, and checks `approver` is a
   non-empty string.
5. Re-checks the same deadline, now that the interrupt has returned. Past it →
   `ApprovalTimeout`, **nothing is written, and the record is left `pending`**. A human who
   deliberated past the request's expiry has answered a dead request.
   `ScriptedApprovalProvider` has this rule for the same reason (`v0.1 §4.3`) — a double that
   could grant what the real provider would refuse invalidates the tests that use it.
6. Records the answer: `store.grant_approval(request_id, answer.approver)` on a grant,
   `store.deny_approval(request_id, answer.approver)` on a refusal — **the same two calls
   `ctrlrun approve` and `ctrlrun deny` make**, and the same two `WebhookApprovalProvider`
   makes when an answer arrives out of band.

The order of 5 and 6 is the whole of step 5. `grant_approval` refuses an expired record on its
own (`v0.1 §4.2 A3`), so writing first and checking after would reach the same *decision* —
and would leave the record `expired` rather than `pending`, and would raise
`ApprovalMismatch(reason="expired")` rather than `ApprovalTimeout`. Both are observable, and
T129d asserts both, because a step whose removal changes nothing a test can see is
documentation rather than defence.

**`timeout`** is a deadline on the *provider*, not on the framework. `@protect(wait=True)`
passes `None`, so the request's own expiry is the bound — which is `LocalApprovalProvider`'s
rule unchanged. Where `interrupt()` blocks, the provider cannot interrupt it: the framework owns
that call and the adapter's README says how its primitive is bounded (§7 item 5). Where
`interrupt()` exits non-locally there is nothing to bound. So `timeout` is honoured at steps 2
and 5 and nowhere else, and this sentence exists because a parameter in a frozen signature that
no step mentions is a parameter somebody will implement differently.

**No event is appended by the provider, and none is claimed.** An `ApprovalProvider` is
constructed with an `ApprovalStore` (`v0.1 §5.3`) and has no sink and no `Control`; giving it
one would make `adapter.py` import `receipt.py` and become a second thing fanning out evidence
(`ARCHITECTURE.md` §6). So a refusal at step 4 appends **no `APPROVAL_INVALIDATED`** — it raises
`ApprovalMismatch`, writes nothing, and leaves the request `pending` with its
`APPROVAL_REQUESTED` standing as the only record that a human was asked. `WebhookApprovalProvider`
is in exactly this position and appends nothing either. It is a real limitation and it is stated
rather than papered over: a mutated answer is visible as an exception and as a request that was
never granted, and not as an event with a reason on it.

Step 6 is why rule 2 is structural. No adapter writes a grant, so no adapter can grow a second
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
2.  @protect builds the Action        — principal from Control's IdentityProvider, or from
                                      context() where none is installed (v0.3 §3.2); §4.2
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
is what `@protect(wait=True)` already does *within one Python call*. An adapter that rebuilt the
action between the two legs would be re-deriving the hash from whatever the framework replayed,
which is exactly the mutation §3.4 is about.

### 3.2.1 Where the framework replays, steps 1 to 15 happen twice, and what that costs

Stated here rather than discovered by an implementer, because §3.2's list reads as one pass and
the resumed-in-place shape (§3.5) is two.

On the second pass `@protect` builds a **new** `Action` with a **new `action_id`**, and
`_presented` creates a **new request**. So:

- **`action_id` is not continuous.** The `action_id` on the `PendingApproval` the human saw is
  the first pass's; the one on the receipt is the second's. Both are in the event log under
  their own `ACTION_PROPOSED` and `APPROVAL_REQUESTED`, and correlating them is the reader's
  work. `v0.1 §2.1` already says `action_id` identifies a *proposal* and a retry produces a new
  one; a replayed pass is a new proposal by that definition. **`action_hash` is continuous**,
  because `action_id` is excluded from the canonical form (`v0.1 §2.2`) — which is why §3.4's
  carried value is about content and never about an id.
- **The first pass's request is orphaned.** It stays `pending` and grantable by
  `ctrlrun approve` for its full TTL, for the same `action_hash`. Not a hole — an approval is
  single-use and hash-bound and is consumed atomically with the reservation — but not nothing
  either, and an operator watching a queue will see two requests for one refund.
- **The approval TTL does not bound the human's deliberation in this shape.** They answer
  against the first pass's request; the grant lands on the second pass's, created *after* they
  answered. What bounds the interval is the framework's checkpoint, which may hold it for a
  month. §2.4's steps 2 and 5 are not dead — they fire wherever `interrupt()` blocks rather than
  exiting non-locally, which is the other shape and every synchronous framework — but in a
  resumed-in-place adapter they guard a window that adapter never opens.

None of this is safe by accident. **Step 13 is what makes it safe**: the resumed pass re-runs
principal expiry, authority and policy at resumption time, so a credential that expired, an
authority revoked or a delegation escalated during the month refuses the action then. What the
human's consent does not carry across the interval is an expiry of its own, and §7 item 5
requires the adapter's README to say so in those words.

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
the action that then runs?** The framework replays the call from its own checkpoint (§3.2.1), and
whether that replay is faithful is the framework's property and not the kernel's.

**The check, and it is mandatory.** `FrameworkInterrupt.carries_approved_arguments` is a
required attribute of the Protocol, and it declares what the framework can do — not what the
adapter would like to check:

- **`True`.** The answer MUST carry `approved_arguments`: the arguments the human's answer was
  given against, recovered from the framework's own record of it — LangGraph's resume value, the
  Agents SDK's `ToolApprovalItem`. The provider **rebuilds the stored request's `Action` with
  those arguments and compares `action_hash`**, and refuses on any difference:
  `ApprovalMismatch(reason="mismatch")`, nothing granted, the request left `pending`. An answer
  that omits them when the adapter declared it would carry them is refused the same way — a
  declaration is not a hint. **This is prevention**, it is performed in core, and it is
  literally `v0.1 §4.2 A1`'s comparison — the same one `WebhookApprovalProvider` makes on an
  inbound answer, where it is mandatory too.

  Rebuilding rather than comparing the mappings directly is deliberate. `action_hash` excludes
  `action_id` (`v0.1 §2.2`), so the replica differs from the stored proposal in exactly the
  dimension under test and in no other; the comparison is over canonical bytes rather than over
  Python objects, so `True` cannot compare equal to `1`; and `Action.__post_init__` validates
  the answer's arguments on the way in, so a `float` or a `set` arriving from a framework's JSON
  is `InvalidArgument` at the gate rather than a silent inequality (`v0.1 §2.3`). A second
  equality implementation here would be a second place for the binding to drift.
- **`False`.** The framework's resumption carries nothing the adapter can inspect. The binding
  across the interrupt is then **the framework's checkpoint, not CTRLRun's**: that is
  *attribution*, evidence will show what was approved and what ran, and a divergence is findable
  afterwards rather than refused beforehand. The adapter's README MUST say so in those words
  (§7 item 4) and MUST NOT describe it as prevention, and the kit reports
  `binding: not_applicable` with the adapter's reason — **never `pass`**.

**Why arguments and not the hash.** The webhook's answerer echoes the `action_hash` it was shown,
because it was shown one. A framework's approval item was not: it records the arguments the model
proposed and knows nothing of CTRLRun's canonical form, and an adapter that computed a hash to
echo would have to build an `Action`, which needs a `Principal`, which §4.2 forbids — the same
hole `needs_approval` closed in §2.2. So the carried value is the one a framework actually holds.
It is not a weaker check, and the rebuild above is why: what is compared is the full
`action_hash`, and the arguments are simply the only component of it a framework can hand back —
within one framework call the action's name and environment come from the `Control`, its
`resource` is a template over its arguments (`v0.1 §5.1`), and its principal from the
`IdentityProvider`.

**The check applies to a grant and never to a refusal.** An answer of `granted=False`
authorizes nothing, so there is nothing to bind it to — and requiring `approved_arguments` on
one would turn a human's *no* into an `ApprovalMismatch`, when §2.4 is explicit that a denial is
an answer and gets `APPROVAL_DENIED` then `ACTION_DENIED`. The conformance kit found this by
driving a denial through a declared carrier (§12.2).

**An adapter MUST NOT synthesize `approved_arguments` from `pending.arguments`.** Handing back a
copy of what it was just given makes every comparison trivially pass; it is manufacturing the
check, and it is the one way to turn attribution into a lie about prevention. §5.4's
`echoes-the-payload` fixture is that adapter, and the kit fails it.

**Why the declaration is not a §3.8 flag.** `carries_approved_arguments` cannot make a check that
would otherwise have run not run: it states a fact about the framework, it is required rather
than defaulted, and `False` is not free — it puts `binding: not_applicable` in the kit's report
and in the adapter's README, permanently and where a reviewer reads first. A flag hides a
weakening; this publishes one.

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
after a human answers. The adapter answers that question with **`ctrlrun.adapter.needs_approval`
and nothing else**: `True` where the combined `v0.3 §4.6` decision is `APPROVE`, `False`
otherwise. The function is core's rather than each adapter's for the reason §2.2 records — the
only way to write it in an adapter was to invent a `Principal` — and it writes nothing and
creates no request, so a predicate the framework may call more than once leaves nothing behind.

A `DENY` at that predicate returns "no approval needed" and lets the tool be invoked, so that
`Control.execute` denies it with a receipt, an `ACTION_DENIED` and the exception the caller
catches. Refusing inside the predicate would refuse without evidence, and `v0.3 §4.3` is
explicit that a denial with a principal to attribute it to belongs in the evidence log.

**`needs_approval` and `@protect` do not always build the same `Action`, and the divergence is
stated rather than discovered.** `@protect` binds the call to the wrapped function's signature
and applies defaults (`v0.1 §8`), and its `resource` template comes from the decorator first and
the policy second; `needs_approval` gets the framework's raw arguments and cannot see the
decorator. So two things can differ: an argument the function defaults and the framework omitted,
and a `resource` declared only on the decorator.

Both directions are **safe**, and that is why this is a documented divergence rather than a
defect. A wrong `False` means the framework does not pre-ask, and `Control.execute` then raises
`ApprovalRequired`, `@protect(wait=True)` routes it through the interrupt, and the human is asked
anyway — the resumed-in-place shape, reached from the other one. A wrong `True` means a human is
asked about an action the policy would have allowed, which costs attention and authorizes
nothing. **In neither direction does an action execute that would not have.** An adapter whose
framework omits defaulted arguments should say so under §7 item 5.

`needs_approval` can also **raise** rather than return: `resolve_principal` refuses an
unattributable call (`ActionDenied(reason="no_principal")`) or a rejected credential
(`IdentityError`), and `evaluate` refuses an Action from another deployment's environment. An
adapter answering a framework predicate must let those propagate — a predicate that swallowed
them would answer `False` for an action the kernel is about to refuse anyway, which is harmless,
and `False` for a *credential* problem the operator needs to see, which is not.

Neither shape needs a new **entry point** — no new way to reach an effect, no path that reserves
or commits or grants outside `Control`. Both needed one new **reader**: `needs_approval`, which
resolves a principal and evaluates, and writes nothing. The distinction is the one `v0.3 §4.3.1`
draws, and §4.1 puts `needs_approval` in that table and says what it does about each column.

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

**The conformance kit refuses an observing `Control`**, and refusing is all it does: the report
carries `status: "refused"`, `reason: "mode: observe"`, no suites and no denominator, and it is
**not a success**. `v0.4 §3.8` is the model and this follows it exactly — verify refuses, prints
and exits 2 under `mode: observe`, and it names *zero applicable guarantees* as its own exit-2
condition because `0/0` reported as a pass is the same false green as `8/8` with five N/As. An
all-`not_applicable` report with an empty denominator is that degenerate report, so the kit does
not produce one. Running the suites in a synthetic enforce mode would report guarantees about a
configuration nobody deployed; running them as-is would report a wall of failures that are true
and useless.

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

### 4.1 `v0.3 §4.3.1` gains three rows

`v0.3 §4.3.1` exists because the expired-credential hole was a **missing enumeration**, not a
missing check, and it says so: *"A new entry point is a specification amendment before it is
code."* An adapter is a new way in. Three rows, each stating what it does about each column
**before** a line of code is written.

The rule the table fixes is restated as binding here, unchanged: **principal validity and
expiry, then authority, then policy.**

| Entry point | Builds an `Action` | Resolves identity | Evaluates authority |
|---|---|---|---|
| An adapter's protected tool → `@protect` → `Control.execute` | yes — `@protect` does, from the bound call | yes (`v0.3 §3.2`), from the `Control`'s provider | yes, before the approval gate |
| `ctrlrun.adapter.needs_approval` → `Control.evaluate` | yes — **core** builds it, from the `Control`'s principal and the framework's arguments; the adapter supplies neither a principal nor an `Action` | yes (`v0.3 §3.2`), by `Control.resolve_principal`; an expired credential is a `DENY` here, as it is for `evaluate` (`v0.3 §2.3`) | yes — the combined `v0.3 §4.6` decision, and it writes nothing |
| `InterruptApprovalProvider.wait` → `store.grant_approval` / `deny_approval` | no — it records an answer about an action that already exists | no — the principal was resolved when the request was created, and is on the stored action | **no, and here is why that is safe** |

The first row is `v0.3 §4.3.1`'s `@protect` row reached through a framework, and is listed
because a reader looking for "what does an adapter do" must find it in the table rather than
infer it. The second and third are new paths.

**Why the third row evaluates no authority, argued rather than asserted.** An independent review
declined this row's first draft — which omitted it entirely, on the grounds that "it records a
human's answer, which is what `ctrlrun approve` does, and `ctrlrun approve` is not a row either"
— and the decline is recorded here because the reasoning belongs in the document.

The argument was unsound in its comparison. `ctrlrun approve` takes its input from an operator's
shell; `wait()` takes its input from a framework checkpoint, and whatever influences the agent's
state can influence that. The comparable out-of-band path is `WebhookApprovalProvider`'s inbound
handler, which is guarded by a signature check **and** a **mandatory** `action_hash` check
against the stored request — and the adapter path's binding check is now mandatory too, on the
same rule, which is §3.4's `carries_approved_arguments`.

What makes evaluating authority in `wait()` unnecessary — rather than merely inconvenient — is
that a grant authorizes nothing on its own. The action is decided **again**, in full, at step 13:
`Control.execute` runs principal expiry, then authority, then policy, and only then consumes the
approval atomically with the reservation. An authority revoked while the human deliberated
refuses the action there. Evaluating it in `wait()` as well would be a guard that can only fire
where a later guard fires with the same observable result, which this project treats as
documentation rather than defence.

So the row is in the table with three explicit cells, which is what `v0.3 §4.3.1` asks for. An
omitted row is the failure mode; a row saying "no, and here is why" is not.

### 4.2 An adapter sees the principal and never supplies it

The principal comes from the `Control`'s `IdentityProvider`, exactly as at every other entry
point. **No name on the adapter surface takes a principal, an agent, a user, or a claim**, and
that is checkable rather than advisory: `PendingApproval` carries `agent` and `user` as
**strings, for display**, and there is no constructor, keyword or callback anywhere in §2.2 that
puts one back in.

An adapter that accepted a principal argument is `--principal-from-client-info` reborn, which
`v0.3 §8.1` removed by name and `v0.3 §8.4` then removed again from the ACS hook after it turned
up there wearing different clothes. **It turned up a third time in this document's first draft**,
in §3.5's instruction to answer the pre-invocation predicate with `Control.evaluate(action)`:
`Action.principal` has no default, so the only way to obey was to build one from the framework's
session, and an independent review found it there. `needs_approval` is core's because of that,
and this paragraph is the record.

A framework's session object knows a user id; that is the framework's word for it, and a
self-reported name cannot be an authorization input. An operator who wants the framework's
identity to count wires an `IdentityProvider` that reads it — explicitly, at the one seam that
exists for it, on the `Control` **they** construct (§2.3), where it is visible in the deployment.

`PendingApproval.agent` and `.user` are there so a human sees who is asking. They are outputs.

**What T129 actually asserts**, since the structural half is easy to write as theatre:

1. No **public callable** in `ctrlrun.adapter` — every function, and every `__init__` — accepts a
   parameter named `principal`, `agent`, `user` or `claims`, or annotated `Principal`. Scoped to
   callables, because `PendingApproval` has fields named `agent` and `user` on purpose and they
   are read-only fields of a frozen dataclass.
2. `ctrlrun.adapter` exposes no way to **construct a `Control`** and no parameter named
   `identity`, `authority` or `environment` — which is the route a `Principal` would actually
   take, and the one a signature scan over `ctrlrun.adapter` alone would miss.
3. Behaviourally: a `Control` whose `StaticIdentityProvider` resolves `A`, driven by an adapter
   whose framework session says `B`, produces receipts naming `A` — and the adapter's attempt to
   assert `B` is one of §5.4's fixtures, so the assertion has a subject.

---

## 5. The conformance kit

### 5.1 What it is, and what it is not

`ctrlrun.conformance` runs **this repository's own acceptance tests** — the suites of `v0.1 §7`
and `v0.3 §10` — against an adapter, through the surface §2 defines. It is not a conformance
programme, it certifies nothing, and passing it is not a claim about an adapter's quality. It
answers one question: *does an action driven through this adapter get the same refusals as an
action driven through `@protect`?*

It ships in **core**, in this repository, needing nothing `pip install ctrlrun` does not already install. §12.1 records why this changed during
item 3; the arguments as they now stand:

- **It needs nothing `pip install ctrlrun` does not already install.** The suites drive a
  `Control` and compare exceptions, which is `assert` and `try`, and they build their scratch
  policies with the YAML parser the kernel already depends on. An adapter author runs the report *inside* their own pytest —
  `assert run(adapter).ok` — rather than through one.
- **An extra with nothing behind it is worse than no extra.** `ctrlrun[conformance]` would
  install nothing, and the `MissingDependency` its install line promised could never fire.
- **A third distribution would be a third thing to version.** The kit tracks the kernel's
  semantics — it *is* the kernel's acceptance tests — so it belongs to the kernel's version.
- **`v0.4 §1`'s argument applies after all.** A check somebody has to remember to install is a
  check that does not run, and that is as true of an adapter author's CI as of an operator's.

`pip install ctrlrun` is unchanged: `pyyaml` and `click`. `import ctrlrun` MUST NOT import
`ctrlrun.conformance` (T134), for `v0.4 §1`'s reason: a testing tool in the execution path is a
dependency nobody meant to take.

### 5.2 What an adapter hands the kit

```python
# ctrlrun/conformance/__init__.py — core, stdlib (§12.1)

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
    #: The same `FrameworkInterrupt` an operator would wire into an
    #: `InterruptApprovalProvider`. **The kit wires it**, because §2.3 says an adapter never
    #: constructs a `Control` and a kit that let it would be testing a shape that does not ship.
    interrupt: FrameworkInterrupt

    def invoke(self, request: CallRequest) -> Any: ...


class SuiteStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class CaseResult:
    id: str                                  # "T1", "T66" — the acceptance test it comes from
    title: str
    status: SuiteStatus
    reason: str | None = None                # required for FAIL and NOT_APPLICABLE
    detail: Mapping[str, Any] = ...


@dataclass(frozen=True)
class SuiteResult:
    name: str                                # "kernel", "binding", ...
    status: SuiteStatus                      # FAIL if any case failed; N/A if every case is
    reason: str | None = None
    cases: tuple[CaseResult, ...] = ()


@dataclass(frozen=True)
class ConformanceReport:
    framework: str
    status: Literal["ok", "refused"] = "ok"  # "refused" is §3.6's observing Control
    reason: str | None = None                # why, when refused
    suites: tuple[SuiteResult, ...] = ()

    @property
    def applicable(self) -> tuple[SuiteResult, ...]: ...   # not NOT_APPLICABLE
    @property
    def not_applicable(self) -> tuple[SuiteResult, ...]: ...
    @property
    def passed(self) -> tuple[SuiteResult, ...]: ...
    @property
    def ok(self) -> bool: ...                # status "ok", applicable non-empty, all passed
    def to_dict(self) -> dict[str, Any]: ...
    def to_text(self) -> str: ...            # "4/4 (1 not applicable)", never "5/5"

#: The suites, in report order. Public because a name here is a name in an adapter's README.
SUITES: Mapping[str, tuple[Case, ...]]


def run(adapter: ConformanceAdapter, deployment: Control | None = None) -> ConformanceReport: ...
```

`ok` is `False` for a refused report and `False` for `applicable == 0`, on `v0.4 §3.8`'s rule:
`0/0` reported as success is the same false green as `6/6` with two N/As. `reason` is required
on every `FAIL` and every `NOT_APPLICABLE` — an N/A without one is an N/A nobody can check.

**`deployment` is the operator's `Control`, and the suites do not run against it.** They run
against scratch `Control`s the kit builds — its own policies, its own store, its own clock —
for the reason `v0.4 §3.5` gives about verify: a kit that reserved a real effect key would be a
defect of exactly the class it exists to find. The one thing read from `deployment` is the mode,
and an observing one is refused (§3.6). It is optional because an adapter author's CI has no
deployment to hand it, and passing one is how the observe-mode refusal reaches anybody at all.

**The kit swaps a counting proxy in for the adapter's `interrupt`** — replacing the attribute,
not merely wiring the proxy into the provider — and asserts the count on every case that expects
a human and on every case that does not. `run()` restores the original when it is done.

Replacing rather than wiring is the difference between a live guard and a dead one. A proxy
wired only into `InterruptApprovalProvider` counts the calls that arrive through `wait()`, and
`wait()` is reached only on `ApprovalRequired`, which an `ALLOW` policy never raises — so an
adapter that put an allowed call to a human by reaching its **own** primitive out of band was
invisible, and the case forbidding it was a negative test against behaviour the path already
prevented. An independent review found it by writing that adapter: it scored 5/5. That is not
belt-and-braces: `fixtures.GrantsForItself` grants the request before `@protect` reaches
`wait()`, so the provider finds the record already `granted` and returns it — correctly, since
`ctrlrun approve` grants out of band too — and the framework's primitive is never called. Every
suite passed. A suite asserting a refusal cannot tell *the human said no through the framework*
from *the framework was never asked*, and only the count can.

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

Each is a named group, reported per suite and per case.

| Suite | Cases | What it drives through the adapter |
|---|---|---|
| `kernel` | T1, T4, T6, T8, A1, B2, B3 | Lost response blocks a blind retry · a committed effect refuses a second attempt · unknown action fails closed · `NotExecuted` permits a retry · an allowed action is never put to a human · a matching answer authorizes · a refusal is a denial and not an error |
| `binding` | B1 | An answer given against different arguments authorizes nothing. `not_applicable` where `carries_approved_arguments` is `False`, with the adapter's reason |
| `duplicate` | D1 | Two `invoke` calls on one effect key: exactly one commits and one is refused |
| `authority` | T66, T70, T74 | No grant · expired grant · a denial by authority leaves no pending approval |
| `identity` | T60, T63 | No principal, no action · the provider wins over anything the framework's session says (§4.2) |

**`binding` is the mutation check alone**, and that is why B2 (its control) and B3 (a denial is
a denial) are in `kernel`. Both run whatever the framework carries back; a `binding` suite
containing them would report `pass` for an adapter that cannot perform the one check the suite
is named for. That is an N/A folded into the count, one level down.

**`binding` is sourced from §3.4 and not from `v0.1 §7` T2.** T2 mutates the action *between* a
human's grant and its presentation, which through `@protect(wait=True)` is unreachable: the
request is created and consumed inside one call. What an adapter can get wrong is the other half
— carrying back an answer that was given against a different proposal — and that is what this
drives. T2 itself is unchanged and still runs against the kernel, where it belongs.

**Three acceptance tests are deliberately absent, and the absences are stated rather than left
to be noticed.** `v0.1 §7` T5 (an approval answered after its expiry) is not reachable through a
single `invoke` for the same reason T2 is not; it is exercised at the provider (T129d, both
halves) and in the kernel. `v0.3 §10` T78 (delegation escalation) drives `Control.delegate`,
which §2.3 puts on an adapter's never-list — a suite driving it would test nothing about the
adapter. Both stay where they are.

**`duplicate` is not `v0.1 §7` T3**, and says so. T3 uses `multiprocessing` and is a statement
about SQLite's `BEGIN IMMEDIATE` across OS processes (`v0.1 §5.3 E1`); a framework
runtime driven across `spawn`ed processes measures the framework's process model and not the
adapter. What this suite catches is an adapter that cached a decision, reused a request id, or
memoized a receipt — all of which show up as two commits on one key in one process. Cross-process
reservation stays the kernel's guarantee and the kernel's test.

A suite reports `pass`, `fail` or `not_applicable` **with a reason**, and the report's
denominator counts applicable suites only. `v0.4 §4.1`'s output rules are the model, and there
is no flag that folds a `not_applicable` into the count.

**`mode: observe` is not a suite.** It is a precondition: `run()` on an observing `Control`
returns a refused report and no suites at all (§3.6).

### 5.4 The broken-adapter fixtures, and why they are written first

A kit that only ever passes is a kit nothing exercises. So the kit's own tests drive it against
adapters that are broken **in one named way each**, and each MUST fail the suite that covers it,
**by name** (T130):

| Fixture | What it does wrong | Fails |
|---|---|---|
| `never-executes` | Returns a plausible value without calling `request.executor` | `kernel` |
| `swallows-not-executed` | Catches `NotExecuted` and returns `None` | `kernel` |
| `swallows-denial` | Catches `ActionDenied` and returns a value | `kernel` |
| `replays-approval` | Keeps a `request_id` and presents it again on the next call | `kernel` |
| `interrupts-on-allow` | Routes every call through the interrupt, `APPROVE` or not | `kernel` |
| `interrupts-only-when-unasked` | Correct wherever a human is expected, and interrupts on exactly the calls where none is | `kernel` — **A1 alone** |
| `grants-for-itself` | Takes `ApprovalRequired` with `wait=False`, writes the grant itself, and re-presents. The framework's primitive is never reached | `kernel` |
| `ignores-authority` | Catches `AuthorityDenied` and returns a value | `authority` |
| `self-asserts-principal` | Builds a `Principal` from the framework's session and reaches a `Control` with it | `identity` |
| `echoes-the-payload` | Declares `carries_approved_arguments = True` and returns `pending.arguments` as the answer's `approved_arguments` | `binding` — §3.4's manufactured check |
| `caches-the-decision` | Memoizes the first call's result and returns it for the second | `duplicate` |

`grants-for-itself` and `echoes-the-payload` exist because the document's second rule and its
mutation check were the two claims with no fixture behind them. "Never a second approval path"
and "this is prevention, not attribution" are the sentences a security reviewer reads first, and
a rule with no broken fixture is a rule the kit cannot tell you about. `ignores-authority` exists
because `swallows-denial` failed the `authority` suite only incidentally — `AuthorityDenied`
subclasses `ActionDenied` — and a suite whose only fixture fails it by accident is a suite
nothing is aimed at. `interrupts-only-when-unasked` exists for the same reason one level down:
`interrupts-on-allow` bumps the interrupt count on T4, B2 and B3 as well, so deleting A1's own
count left the kit still failing it — a **subsumed** check, found by mutation. This one is
invisible to every other case.

**Every suite is named by at least one fixture, and every fixture names a suite that exists.**
Both directions are asserted (T130), because a fixture pointed at a renamed suite passes its own
test by never being checked against anything, and a suite no fixture fails would report `pass`
for every adapter ever written.

Each fixture fails **the suite named in its row**. Several also fail others incidentally — an
adapter that never reaches the executor fails anything whose control counts executor calls — and
the assertion is on the named suite rather than on an exclusive one, because "and no other" is
false of a fixture broken badly enough. What T130 forbids is a fixture that fails *nothing* and a
fixture whose named suite passed.

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
- **Not whether the adapter logged the observe banner.** §3.6 requires it, and the kit
  **refuses** an observing `Control` outright — so no case ever runs under one, and an adapter
  that never calls `ctrlrun.adapter.banner` passes every suite. The requirement is real and the
  kit is not what enforces it; saying so here is the alternative to a reader assuming a green
  report covered it.
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
4. **`carries_approved_arguments`, its value, and what that makes the binding.** `True` means
   §3.4's check runs in core on every answer: *prevention*. `False` means the framework's
   resumption carries nothing the adapter can inspect, the binding across the interrupt is the
   framework's checkpoint, and the README says **attribution** in that word — with the reason
   the kit will print beside `binding: not_applicable`. This is the sentence a security reviewer
   reads first, an adapter that buried it would be the false-green problem in prose, and T137b
   asserts it.
5. **Every place the framework's behaviour is visible through the contract.** Retry defaults;
   what the framework does with a tool that raised; whether it replays a checkpointed call with
   identical arguments; whether the interrupt payload is persisted and where; **how long its
   primitive can hold an interrupt open, and therefore whether the approval TTL bounds the
   human's deliberation at all** (§3.2.1); and, for a resumed-in-place adapter, that
   `PendingApproval.action_id` is not the `action_id` on the receipt. Item 1 measures the first
   two for the two reference adapters; an adapter written later cites its framework's
   documentation and says what it did not establish, exactly as the probe's README does.
6. **Whether the kernel's exceptions arrive as themselves**, and what an operator must call if
   they do not. A framework that swallows or wraps what a tool raised takes `v0.1 §8`'s explicit
   exceptions away from the call site, and an operator's `except DuplicateEffect` silently stops
   firing (§12.7). Where the adapter ships a helper for it, the README names it and says what
   happens without it.
7. **What it does not do**: it is not a second approval path, it grants nothing, and it is not
   required for a framework with no HITL primitive (§1.1).
8. **`ctrlrun.conformance` results** — which suites pass, and which are `not_applicable` with
   the reason. Never a bare "conformant".

---

## 8. Acceptance tests

Each MUST exist as a pytest test carrying the given ID in its name, as `v0.1 §7`, `v0.2 §10`,
`v0.3 §10` and `v0.4 §8` require. All MUST pass for v0.5, and every test from those four
documents MUST still pass.

### Item 2 — The adapter surface and the entry-point rows (§2, §4)

Every test below that asserts a refusal uses an interrupt **double that counts its calls**, and
asserts the count. A refusal is satisfied just as well by a run in which the adapter was never
reached, and that run passes against a surface with the guard deleted (`v0.4 §1.3`).

#### T126 — The order is asserted by observation, not by reading the source
A `Control` with an expired principal, an authority section that would deny, and a policy that
would deny, driven through an adapter. The events are `ACTION_PROPOSED` then `ACTION_DENIED`
with `reason="principal_expired"`, and there is **no** `AUTHORITY_DENIED` and **no**
`POLICY_EVALUATED`. Its **control**: the same `Control` with the principal's `expires_at` moved
into the future reaches `AUTHORITY_DENIED` instead — so the test distinguishes which guard ran
and not merely that something refused. And the interrupt double was called **zero** times, with
a mutant that proves the double can be called: the same adapter against a policy that approves.

#### T127 — An adapter that skips authority is refused
An adapter that reaches a `Control` with an `authority:` section gets `AuthorityDenied` for a
principal with no grant, and the receipt is `denied` with **no** `POLICY_EVALUATED`
(`v0.3 §4.3`). The adapter-specific half, without which this is a v0.3 regression relabelled:
the kit's executor was called **zero** times, and an adapter that reached the executor directly
— §5.4's `never-executes` inverted, one that runs the function without `Control` — fails this
test. There is no path through §2's surface that reaches an executor without `Control.execute`,
and this is what says so.

#### T128 — An authority denial through an adapter leaves no pending approval
`v0.3 §10` T74 driven through the surface: `AUTHORITY_DENIED`, no `APPROVAL_REQUESTED`,
`store.approvals_for(action_hash)` empty, and the interrupt double called zero times. A human is
never asked about an action that could not run.

#### T128b — `needs_approval` resolves the principal and evaluates both axes
`ctrlrun.adapter.needs_approval` returns `True` only where the combined `v0.3 §4.6` decision is
`APPROVE`; `False` for `ALLOW` and for `DENY` — including a `DENY` that came from **authority**,
so the tool is invoked and `Control.execute` denies it with a receipt and an `ACTION_DENIED`
rather than the predicate refusing silently. It **writes nothing**: the store's events, receipts
and effects are byte-identical before and after, across every decision. And the principal on the
Action it builds is `Control.resolve_principal`'s, asserted against a `Control` whose provider
resolves someone other than the ambient `context()`.

#### T129 — An adapter cannot supply a principal
The three assertions of §4.2, each separately: no public callable in `ctrlrun.adapter` takes a
`principal`, `agent`, `user` or `claims` parameter or a `Principal` annotation (by
`inspect.signature`, scoped to callables); `ctrlrun.adapter` exposes no way to construct a
`Control` and no `identity`, `authority` or `environment` parameter; and behaviourally, a
`Control` whose provider resolves `A`, driven by an adapter whose framework session says `B`,
produces receipts naming `A`.

#### T129b — The provider is the only thing that grants
§5.4's `grants-for-itself` fixture — an adapter that calls `store.grant_approval` rather than
returning an `ApprovalAnswer` — fails the `kernel` suite. Its control: the same adapter returning
the answer instead passes. Without the pair, the assertion is that something failed rather than
that this is what the kit catches.

#### T129c — A framework error is never an answer
An `interrupt()` that raises `RuntimeError` leaves the request `pending`, writes no receipt,
appends no `APPROVAL_GRANTED` and no `APPROVAL_DENIED`, and the exception propagates unwrapped.
A control-flow exception of the shape LangGraph's `GraphInterrupt` has does the same and is not
caught, asserted with a custom `BaseException` subclass so the test does not depend on the
framework being installed.

#### T129d — An expired request is not put to a human, and a late answer is not recorded
Two halves, and the second is what pins §2.4's step order. A request whose `expires_at` has
passed raises `ApprovalTimeout` from `wait()` with the interrupt double called **zero** times.
A request that expires *while* the double deliberates raises `ApprovalTimeout`, writes no grant,
**and leaves the record `pending`** — not `expired`, which is what `grant_approval`'s own refusal
would have left behind. Both the exception type and the record's status are asserted, so removing
step 5 fails rather than passing on the backstop.

#### T129e — An answer with no approver is refused
`ApprovalAnswer(granted=True, approver="")` raises `InvalidArgument` from `wait()`, and nothing
is written. Its control: a non-empty approver grants, and the string reaches
`APPROVAL_CONSUMED.data.approver` and the receipt's `approver` field.

#### T129f — `PendingApproval` survives a checkpoint
`to_dict()` round-trips through `json.dumps`/`json.loads` for an action carrying every argument
type `v0.1 §2.3` allows, at depth, and the result compares equal to the original. A framework
checkpoints this payload, and a payload that would not serialize is one that works until the
first restart.

#### T129g — The banner is logged once per Control, and printed never
`ctrlrun.adapter.banner` on a `Control` built from `mode: observe` logs `v0.3 §6.5`'s wording on
the `ctrlrun` logger at `WARNING`, **once** for ten calls with the same `Control` and again for a
second `Control`; it writes nothing to stdout or stderr (captured), and it is a no-op under
`mode: enforce`. `caplog` and `capsys` together, because "logs" and "does not print" are two
claims and one of them is the one an adapter inside somebody else's loop would break.

#### T129h — The surface has no flag that relaxes a check
No public callable in `ctrlrun.adapter` takes a parameter whose name matches
`auto_approve|skip|bypass|dry_run|force|insecure|allow_|disable_`, and no module-level name in
`ctrlrun.adapter` reads an environment variable. `v0.4`'s equivalent for verify, asserted the
same way (§3.8).

### Item 3 — The conformance kit (§5)

#### T130 — The kit fails a broken adapter, per suite and by name
Each of §5.4's eleven fixtures is driven through `conformance.run`, and each fails **the suite
named in its row**. A fixture that failed nothing, or whose named suite passed, is the failure
this test catches; incidental failures of other suites are permitted and asserted as such,
because "and no other" is false of an adapter broken badly enough.

#### T131 — The kit passes a correct adapter
A minimal in-process reference adapter — no framework, just the surface — passes every suite,
with `binding` a genuine `pass` rather than an N/A. Without it, T130 is satisfied by a kit that
fails everything.

#### T132 — Not applicable is not a pass, one level up
An adapter declaring `carries_approved_arguments = False` reports `binding: not_applicable` with
**its own reason**, is excluded from the denominator, is listed separately, and `report.ok`
remains `True` on the strength of the suites that did run. `to_text()` renders
`4/4 (1 not applicable)` and never `5/5`. There is no flag that folds it in.

#### T132b — The kit refuses an observing Control
`run()` against a `Control` built from `mode: observe` returns `status="refused"`,
`reason="mode: observe"`, **no suites**, and `report.ok is False`. Not an all-N/A report with a
zero denominator, which is the degenerate `0/0` `v0.4 §3.8` refuses by name.

#### T132c — A zero denominator is not a pass
A report whose every suite is `not_applicable` has `report.ok is False`. Asserted directly on
`ConformanceReport`, because it is the rule and not a consequence of any particular adapter.

#### T133 — Neither the kit nor an adapter reaches the network
The kit's own run and **each reference adapter's kit run** (T135) happen in a subprocess whose
`sitecustomize` replaces `socket.socket`, `create_connection` and `getaddrinfo` with a refusal
(`v0.4 §3.7`'s discipline). Its **precondition**, without which the negative test proves nothing:
the same subprocess is first shown to fail when a deliberate `urlopen` is added, so the guard is
known to be able to see a socket. The in-process adapter of T131 would open none either way; the
reference adapters, with a framework SDK loaded, are where this test has a subject.

#### T134 — `import ctrlrun` imports neither `verify` nor `conformance`
`v0.4`'s T125b, extended: a subprocess imports `ctrlrun` and asserts `ctrlrun.conformance` is
absent from `sys.modules`, beside `ctrlrun.verify`, `httpx`, `jwt` and every `opentelemetry`
module. `ctrlrun.adapter` **is** present: it is core and in the action path, and this test says
which side of that line each module is on.

#### T134b — The kit is core and needs no extra
`pip install ctrlrun` installs `pyyaml` and `click` and nothing else, **and declares no
`conformance` extra** — §12.1 records why the one this document originally specified was
removed while it was being built. The test asserts the extra's absence and that nothing under
`conformance/` imports from one, because an extra with no dependency behind it is an install
line that installs nothing and a `MissingDependency` that can never fire.

### Items 4 and 5 — The reference adapters (§3.5, §6, §7)

#### T135 — Each reference adapter passes the kit
`ctrlrun.conformance.run` against each, with every suite `pass` or `not_applicable` with a
reason, `report.ok is True`, and the results in each adapter's README (§7 item 8). Run with the
framework installed; skipped **by name** where it is not, so a green run with a missing framework
cannot look like a pass (`v0.4 §7` T123's rule).

#### T135b — The human's answer arrives through the framework's primitive and no other channel
Behavioural, because the source-inspection version of this is unfalsifiable. The adapter is
driven with the framework's resumption API **not** invoked: no answer arrives, no grant is
written, the request stays `pending`, and the executor is never called. Then the same run with
the framework's own resumption invoked: the answer arrives and the action commits. An adapter
that had grown a second path — a prompt, a queue, a poll — passes the first half only if that
path is also idle, and the two halves together are what no second channel can satisfy.

#### T136 — `adapters/` is packaged by neither the wheel nor the sdist
`python -m build` for `ctrlrun` produces a wheel **and an sdist** containing no `adapters/` path
and no `ctrlrun_langgraph` or `ctrlrun_openai_agents` module, and `research` stays unimportable
(`v0.4 §8` T124b, unchanged). The sdist half is not symmetry: v0.2 shipped four files it should
not have precisely because `MANIFEST.in` resolves against the working tree, and `prune adapters`
is added beside the prunes that are already there.

#### T137 — Each adapter's declared ranges are what its CI ran
The kernel range and framework range in each adapter's README parse as version specifiers, match
its `pyproject.toml` `dependencies`, and contain the versions its CI job installed. A version
somebody typed is a version that was true once.

#### T137b — Each adapter's README says which kind of guard its binding is
§7 item 4, asserted: the README states `carries_approved_arguments` and, where it is `False`,
contains the word "attribution" and does not describe the binding as prevention. The sentence a
security reviewer reads first is the one a test keeps honest.

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
    needs_approval,
)
# ctrlrun.conformance — core and stdlib (§12.1), NOT re-exported at package import:
#   ctrlrun.conformance.run
#   ctrlrun.conformance.CallRequest
#   ctrlrun.conformance.ConformanceAdapter
#   ctrlrun.conformance.ConformanceReport
#   ctrlrun.conformance.SuiteResult
#   ctrlrun.conformance.CaseResult
#   ctrlrun.conformance.SuiteStatus
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
class ApprovalAnswer:                        # §2.2, §3.4
    granted: bool
    approver: str
    approved_arguments: Mapping[str, Any] | None = None

class FrameworkInterrupt(Protocol):          # §2.1 — a Protocol, not a base class
    framework: str
    carries_approved_arguments: bool         # §3.4 — a declaration, not a setting
    def interrupt(self, pending: PendingApproval) -> ApprovalAnswer: ...

class InterruptApprovalProvider:             # satisfies v0.1 §4.3's ApprovalProvider
    def __init__(self, store: ApprovalStore, interrupt: FrameworkInterrupt, *, clock=...): ...
    def request(self, action: Action, ttl: timedelta) -> ApprovalRequest: ...
    def wait(self, request_id: str, timeout: timedelta | None) -> Approval | None: ...

def needs_approval(control: Control, action: str, arguments: Mapping[str, Any], *,
                   resource: str | None = None) -> bool: ...     # §3.5
def banner(control: Control) -> None: ...                        # §3.6
```

**Promoted.** `Control._resolve_principal` becomes **`Control.resolve_principal(action_name:
str) -> Principal`**. `needs_approval` builds an `Action` and `Action.principal` has no default,
so the principal has to come from somewhere; the only alternative was for an adapter to invent
one, which is the hole §4.2 records. Its behaviour is `v0.3 §3.2` unchanged — the provider wins
where it answers, a decline is refused rather than backfilled once an `authority:` section is
loaded, a refusal is never backfilled — and it is a **reader**: it writes nothing, appends
nothing, and creates no request. It is the seam an adapter may read and may not supply, and
§4.1's second row is where it appears in `v0.3 §4.3.1`'s enumeration.

`clock` on `InterruptApprovalProvider` is `LocalApprovalProvider`'s testing seam and nothing
more, and it is not a §3.8 relaxation for a structural reason rather than a promise: **an adapter
does not construct the provider**. The operator does (§2.3), on the line where they choose the
policy, the store and the identity provider.

**And no other public name.** No new event type, no new error type, no new schema, no new CLI
command, no new policy key, and no `Control` method beyond the promotion above — which adds no
capability, since `@protect` has resolved principals this way since v0.3.

**Nothing is removed and nothing changes meaning.** `v0.1 §8`, `v0.2 §11`, `v0.3 §11` and
`v0.4 §9` are untouched. No schema version changes: `ctrlrun.policy/v3`, `ctrlrun.receipt/v2`,
`ctrlrun.action/v1`, `ctrlrun.inspection/v2`, `ctrlrun.verify/v1` and `ctrlrun.guarantees/v1` all
stand, and **v0.5 adds no table and no column to any store** — an adapter writes through
`Control` and `Control` writes what it always wrote.

**Module map** (`ARCHITECTURE.md` §6) gains two rows, and the dependency direction is unchanged
— downward only, with `Control` the only module that composes the others:

| Module | Owns | Must not know about |
|---|---|---|
| `adapter.py` | `FrameworkInterrupt`, `PendingApproval`, `ApprovalAnswer`, `InterruptApprovalProvider`, `needs_approval`, `banner` | the policy evaluator, authority, effect state, executors, sinks, any framework |
| `conformance/` | the suites, the fixtures, the report — core, no new dependency | the gateway, `otel`, `jwt_identity`, `acs`, `webhook`; anything from an extra |

`adapter.py` imports `action.py`, `approval.py`, `effect.py` (`resolve_resource`), `errors.py`
and two names from `policy.py` — `OBSERVE` and `Decision`, which are the mode and the decision
vocabulary rather than the evaluator — and takes a `Control` as a *parameter* in
`needs_approval` and `banner` without importing it at module scope — the same shape `verify/`
has, one layer down. It appends no event and owns no sink: `Control` is still the only thing
that fans evidence out (§2.4). `conformance/` sits **above** `control.py`, beside `verify/` and
`cli/`: it composes the kernel the way an application does, and nothing in the kernel imports it.

**`docs/ROADMAP.md` is corrected** in the same commit as this document (§3.1): the v0.5 bullet's
`Suspended` / `Control.resume` sentence becomes `ApprovalRequired` / `with_approval`. This is the
`v0.4 §9.4` treatment, and it is recorded here so the correction is findable from the spec rather
than only from a diff.

### 9.1 What the independent review of this document changed

A review in a session that did not write this document found five defects that would each have
produced an insecure or unimplementable adapter. They are recorded because this project
requires a declined finding's reasoning to live in the document it declines a change to — and an
*accepted* finding that silently rewrote a section leaves the next reader unable to tell which
sentences were argued and which were repaired.

| Found | Where it is now |
|---|---|
| §3.5's predicate told an adapter to call `Control.evaluate(action)`, and `Action.principal` has no default — so the only way to obey was to build a `Principal` from the framework's session. `--principal-from-client-info`'s third costume, mandated by the spec. | `needs_approval` is core's, and `Control.resolve_principal` is promoted for it (§2.2, §3.5, §4.2, §9) |
| The provider was required to append `APPROVAL_INVALIDATED` and `APPROVAL_GRANTED`, which no `ApprovalProvider` can do: it is constructed with an `ApprovalStore` and has no sink. | Withdrawn and stated as a limitation; `approver` reaches evidence by `Control`'s existing path (§2.2, §2.4) |
| "Steps 1 to 15 happen twice" and "step 12 re-presents the same `Action`" cannot both be true: the replayed pass builds a new `action_id` and a new request, so the approval TTL bounds nothing in that shape. | §3.2.1, which states all three costs and why step 13 is what makes it safe |
| Nobody was named as the party that wires the provider into `Control`, so the adapter would — and would then choose `identity=`, which is §4.2 by another route. | §2.3: the operator constructs the `Control`, and constructing one is on the never-list |
| §4.1's two rows duplicated `v0.3 §4.3.1`'s, and the genuinely new path — `wait()` → `grant_approval` — was argued away by comparison to `ctrlrun approve`, whose input comes from an operator's shell rather than a framework checkpoint. | §4.1's third row, with the comparison corrected to `WebhookApprovalProvider`'s mandatory hash check, and §3.4's check made mandatory to match |

Twelve further findings tightened §2.4's step order, `timeout`'s meaning, the observe-mode
contradiction across four sections, the missing never-list entries, three fixtures, and eight
acceptance tests that would have proved nothing. The tests are where most of it landed:
`v0.4 §1.3`'s positive-control rule applies to every refusal in §8, and the first draft asserted
refusals without establishing that the adapter had been reached at all.

## 10. Fail-closed table for v0.5

| Condition | Result |
|---|---|
| `interrupt()` raises anything | Propagates unwrapped. No grant, no denial, no receipt. The request stays `pending` (§2.4) |
| `carries_approved_arguments` is `True` and the answer's differ from the proposal's | `ApprovalMismatch(reason="mismatch")`, nothing granted, request left `pending` (§3.4) |
| `carries_approved_arguments` is `True` and the answer omits them | Refused the same way. A declaration is not a hint (§3.4) |
| `carries_approved_arguments` is `False` | No check. `binding` is `not_applicable` with the adapter's reason, and **never `pass`** (§3.4, §5.3) |
| `ApprovalAnswer.granted` not a `bool` | `InvalidArgument`. A framework's raw resume value is not a verdict, and any truthy one would be a grant — a human's *no* becoming a yes is the worst failure available here (§2.2) |
| `ApprovalAnswer.approver` empty or not a string | `InvalidArgument`. An approval with no approver is evidence that answers the wrong question (§2.2) |
| The deadline has passed before the interrupt | `ApprovalTimeout`; `interrupt()` is not called (§2.4 step 2) |
| The deadline passes during the interrupt | `ApprovalTimeout`; nothing written, record left `pending` (§2.4 step 5) |
| The request is not `pending` | `v0.1 §4.3`'s rule, unchanged: `None` for anything that will never be granted |
| An adapter reaches an executor without `Control.execute` | Impossible through §2's surface; a broken fixture, and the kit fails it (§5.4) |
| An adapter constructs a `Control` | On §2.3's never-list. The operator constructs it and hands it over |
| `mode: observe` | The interrupt is never reached; the banner is logged once per `Control`; `run()` returns a **refused** report with no suites, and `ok` is `False` (§3.6) |
| Every suite `not_applicable` | `report.ok` is `False`. `0/0` reported as success is `v0.4 §3.8`'s false green |
| A framework is not installed | Its adapter's kit run is skipped **by name**, and the skip is in the report (T135) |
| An adapter tries to supply a principal | There is no name on the surface that takes one, and none that builds a `Control` (§4.2, T129) |

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
- **An expiry on the human's deliberation in a resumed-in-place shape.** §3.2.1 states the cost
  and does not fix it: the framework owns the checkpoint's lifetime, and a CTRLRun timer that
  refused a resumption would be refusing an action the kernel re-decides in full at step 13
  anyway.
- **Appending an event from an approval provider.** §2.4 states the limitation; giving
  `adapter.py` a sink would make it a second thing fanning out evidence.
- **Authority propagation across agent hops, A2A, or a task-bound delegation an adapter creates.**
  v0.7.
- **`Suspended` / `Control.resume` for an approval.** §3.1.
- **Any new `Control` method, store method, event, error, schema, policy key or CLI command.** §9.
- Publishing framework results in any item but item 1, and item 1 publishes only what the
  maintainer read first (`v0.4 §7.4`).
- Postgres, migrations, signed receipts, dashboards, anything in `VISION.md`.

---

## 12. What building v0.5 settled

*One subsection per question the drafting could not close, each stating what the code decided
and which section carries it. `SPEC-v0.4.md §12` is the format.*

### 12.1 The conformance kit is core, not an extra

§5.1's first draft made it `ctrlrun[conformance]`, on the stated premise that *"the kit needs
`pytest`"*. Building it showed the premise was false. The suites drive a `Control` and compare
exceptions, which is `assert` and `try`; there is nothing in the kit a third-party library does.
An adapter author runs the result **inside** their own pytest — `assert run(adapter).ok` — and
never through one.

That leaves an extra with no dependency behind it: an install line that installs nothing, and a
`MissingDependency` T134b would have asserted about a condition that can never arise. So the kit
is core, exactly as `verify/` is, and `v0.4 §1`'s argument turns out to apply after all — *a check somebody has to remember to install is a check that does not run* is as
true of an adapter author's CI as of an operator's deployment.

`pip install ctrlrun` is unchanged: `pyyaml` and `click`. T134b asserts the absence of the extra
rather than its behaviour, and walks the package's imports to assert that nothing under
`conformance/` reaches an extra or one of the three modules the module map forbids it. It does
**not** assert "stdlib only": the suites parse YAML, and a claim that is loosely true is the
kind this document keeps refusing.

### 12.2 The binding check applies to a grant and never to a refusal

§3.4's first draft made `approved_arguments` mandatory whenever the adapter declared it carried
them, without qualifying it by the answer's verdict. The kit's `B3` case — a human's *no*,
driven through a declared carrier — was refused with `ApprovalMismatch` before it could become a
denial.

That is the exact failure §2.4 forbids in the other direction: *a denial is a human's answer*,
and it gets `APPROVAL_DENIED` then `ACTION_DENIED`, never the `APPROVAL_INVALIDATED` umbrella. A
refusal authorizes nothing, so there is nothing for the hash to bind it to. §3.4 now says so, and
the case that found it is in the `kernel` suite where it will keep saying so.

### 12.3 An already-granted request is a hole the provider cannot close, and the kit must

`fixtures.GrantsForItself` writes the grant itself and then re-presents, so `wait()` finds the
record already `granted` and returns it without calling `interrupt()`. Every suite passed.

The provider is **right** to return an already-granted record: `ctrlrun approve` grants out of
band, so does a webhook, and `v0.1 §4.3` says a provider never invents an answer. Refusing there
would break the legitimate paths. So the hole is not in `wait()` — it is that a suite asserting a
*refusal* cannot distinguish "the human said no through the framework" from "the framework was
never asked".

The kit therefore wraps the adapter's `interrupt` in a counting proxy and asserts the count: `1`
on every case that expects a human, `0` on every case that does not. `v0.4 §1.3` one level up,
and the reason §5.2 now specifies the proxy rather than leaving it to an implementation.

### 12.4 `ConformanceAdapter` exposes its interrupt, and the kit plays operator

§2.3 says an adapter never constructs a `Control`, so the kit must — and to wire the provider it
needs the adapter's `FrameworkInterrupt`. The Protocol gained `interrupt` for that. A kit that
let the adapter build the `Control` would be testing a shape that does not ship, which is the
same defect as a test double that can grant where the real code would not.

### 12.5 Two acceptance tests are not reachable through a single `invoke`. A third is, and
the first draft of this subsection said it was not

`v0.1 §7` T2 (mutation between a grant and its presentation) needs an interval between the
request being created and being consumed that `@protect(wait=True)` has none of: both happen
inside one call. `v0.3 §10` T78 (delegation escalation) drives `Control.delegate`, which is on
§2.3's never-list, so a suite driving it would test nothing about the adapter. Both stay where
they are, and §5.3 names the absences rather than leaving a reader to wonder why a suite sourced
from `v0.1 §7` is missing one of its tests.

**T5 was on that list and should not have been.** This subsection said an approval answered
after its expiry was unreachable "without a hook into the adapter's own primitive" — and §12.3
had added exactly that hook two subsections earlier. The counting proxy runs immediately before
the adapter's primitive, so moving the kit's clock there reproduces §2.4 step 5 through one
`invoke`. An independent review pointed at the contradiction inside the same document.

It is a meaningful *adapter* case and not only a provider one: what it asks is whether the
adapter lets `ApprovalTimeout` propagate, or swallows it the way `swallows-denial` swallows
`ActionDenied` — an adapter that returned a value there would have executed nothing and told its
framework the refund went through. It is in the `kernel` suite.

The general lesson is worth more than the case. A claim that something is untestable is a claim
that ages badly, because the next thing built is often the thing that makes it testable — and
nobody re-reads a paragraph that says "not reachable" to check whether it still is.

### 12.6 The two reference adapters needed three different things from the contract, and each
was a defect in it

This is what having two is for. The first adapter is shaped by whoever wrote the contract; the
second is where the contract gets tested. All three changed `SPEC-v0.5.md` rather than being
worked around in an adapter.

**The interrupt count was `== 1` and had to be `>= 1`.** LangGraph replays the node, so
`@protect(wait=True)` raises `ApprovalRequired` again on the resumed pass and reaches the
framework's primitive a **second** time — once to ask, once to receive (§3.2.1 predicted the
replay and the kit did not follow it through). A correct adapter scored 4/5. Demanding an exact
count is asserting a *framework's* shape rather than an adapter's behaviour; what the count is
for is telling "the framework was asked" from "it was not", and `>= 1` does that. A1 keeps
`== 0`.

**`B2` asserted the kit's own approver, and the Agents SDK cannot carry one.** That SDK records
*that* a call was approved and not by whom, so its adapter writes the channel name —
`"openai-agents:tool-approval"` — which §2.2 explicitly blesses. The kit now asserts a
**non-empty** approver, and the exact-match check lives in the LangGraph adapter's own tests,
which is its right home: only they know the framework can carry it.

**A framework that refuses *before* invoking produces no denial to observe.** The Agents SDK
does not call a tool whose approval was declined, so no CTRLRun action is proposed: no
`APPROVAL_DENIED`, no `ACTION_DENIED`, no receipt. The refusal is real and it is in the
framework's own output; CTRLRun was simply never asked. `B3` is therefore its own suite,
`denial`, reported `not_applicable` for such a framework with the reason on the report and in
the adapter's README — never a pass, and never folded into the count. `ConformanceAdapter` gains
`refuses_before_invoking`, which defaults to `False` because that is the shape most frameworks
have and a declaration nobody needs to make is one nobody gets wrong.

### 12.7 An adapter may have to restore the kernel's exception taxonomy

The OpenAI Agents SDK does two things to an exception a tool raised, and an adapter that left
either alone cannot pass the `kernel` suite — nor should it.

**`failure_error_function` swallows it by default.** `default_tool_error_function` catches the
exception and returns *"An error occurred while running the tool. Please try again."* **to the
model**. Under that default an `ActionDenied`, a `DuplicateEffect` or an `AmbiguousEffect`
reaches an agent as a suggestion to retry — which is exactly the failure `v0.2 §6.10` argues
about in the gateway, one layer up: *a refusal by CTRLRun is not an outcome of the tool, it is
the statement that the tool did not run*, and a channel whose contents reach the model as text
invites the retry the refusal exists to prevent.

**What survives is wrapped.** With the default off, the SDK raises `UserError("Error running
tool ...")` and chains the original as `__cause__` — so `except DuplicateEffect` at a call site
does not fire, and `v0.1 §8`'s preference for explicit exceptions over return codes stops
holding across the framework boundary.

So the contract gains a sentence: **an adapter is responsible for the kernel's exceptions
arriving as themselves**, and where its framework prevents that, it ships whatever it takes —
`ctrlrun-openai-agents` ships `protected_tool` (which sets `failure_error_function=None`) and
`run` / `run_sync` / `unwrap` (which walk the chain). Neither decides anything, holds anything or
grants anything: they are couriers restoring what the framework obscured, and §7 item 5 requires
the README to say which one an operator must use and what happens if they do not.

The LangGraph adapter needs none of this: LangGraph propagates a node's exception unchanged. The
difference between the two is the finding, and it is the sort a single reference adapter would
never have produced.
