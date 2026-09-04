"""The OpenAI Agents SDK reference adapter. SPEC-v0.5 §3.5, §6, §7; T135, T135b, T137.

Every test here drives a **real `Runner`** with a real `function_tool`, a real
`needs_approval` predicate, a real `ToolApprovalItem` and a real `state.approve(...)`. What is
not real is the model: a deterministic `FakeModel` emits one tool call and then one message, so
the run is exactly reproducible and costs nothing.

That is legitimate here in a way it would not be in `research/framework-probe/`. The probe
measures *a framework's* behaviour and its fairness rules demand framework defaults and a real
model; this measures *the adapter*, and what a model would have decided is not the subject.
The framework's own approval machinery is exercised in full either way.

Skipped **by name** where `openai-agents` is not installed.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from ctrlrun import ActionDenied, Control, InterruptApprovalProvider, protect
from ctrlrun.conformance import SuiteStatus, run
from ctrlrun.conformance.suites import CallRequest
from ctrlrun.identity import StaticIdentityProvider
from ctrlrun.policy import Policy
from ctrlrun.state import InMemoryStateStore

pytest.importorskip("agents", reason="openai-agents is not installed")
pytest.importorskip("ctrlrun_openai_agents", reason="ctrlrun-openai-agents is not installed")

import ctrlrun_openai_agents
from agents import Agent
from agents.items import ModelResponse
from agents.models.interface import Model
from agents.usage import Usage
from ctrlrun_openai_agents import (
    CHANNEL,
    AgentsInterrupt,
    ApprovalNotAsked,
    _bound_call,
    approval_gate,
    protected_tool,
    unwrap,
)
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)

pytestmark = pytest.mark.authority

ADAPTER = Path(__file__).resolve().parents[1] / "adapters" / "openai-agents"

APPROVE = """
schema: ctrlrun.policy/v1
actions:
  stripe.refund:
    decision: approve
"""

ALLOW = """
schema: ctrlrun.policy/v1
actions:
  stripe.refund:
    decision: allow
"""

DENY = """
schema: ctrlrun.policy/v1
actions: {}
"""


class FakeModel(Model):
    """One tool call, then a final message. Deterministic, offline and free.

    It stands in for a model that has already decided to call the tool -- which is the state
    every question here is about. Nothing in the SDK's approval machinery is stubbed: the
    `needs_approval` predicate, the `ToolApprovalItem`, the interruption and
    `RunState.approve` are all the SDK's own.
    """

    def __init__(self, name: str, arguments: str) -> None:
        self._name = name
        self._arguments = arguments
        self.turns = 0

    async def get_response(self, *args: Any, **kwargs: Any) -> ModelResponse:
        self.turns += 1
        if self.turns == 1:
            call = ResponseFunctionToolCall(
                arguments=self._arguments,
                call_id="call_1",
                name=self._name,
                type="function_call",
                id="fc_1",
            )
            return ModelResponse(output=[call], usage=Usage(), response_id=None)
        message = ResponseOutputMessage(
            id="msg_1",
            content=[ResponseOutputText(annotations=[], text="done", type="output_text")],
            role="assistant",
            status="completed",
            type="message",
        )
        return ModelResponse(output=[message], usage=Usage(), response_id=None)

    def stream_response(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - unused
        raise NotImplementedError


def build(document: str = APPROVE):
    store = InMemoryStateStore()
    control = Control(
        Policy.from_yaml(document, source="<test>"),
        store,
        InterruptApprovalProvider(store, AgentsInterrupt()),
        identity=StaticIdentityProvider("refund-agent"),
        environment="production",
    )
    return control, store


def agent_for(control: Control, calls: list[Any], *, name: str = "issue_refund"):
    @protect("stripe.refund", effect="refund:{payment_id}", wait=True, control=control)
    def issue_refund(payment_id: str, amount: int) -> str:
        calls.append({"payment_id": payment_id, "amount": amount})
        return "committed"

    async def tool_body(payment_id: str, amount: int) -> str:
        """Issue a refund for a payment. Amounts are in integer minor units."""
        return issue_refund(payment_id=payment_id, amount=amount)

    tool = protected_tool(control, "stripe.refund", tool_body, name_override=name)
    model = FakeModel(name, '{"payment_id": "txn_1", "amount": 2000}')
    return Agent(
        name="refunds", instructions="You handle refunds.", tools=[tool], model=model
    ), model


# --- the round trip, through the framework's own primitive ---------------------------------


def test_an_approve_stops_the_run_with_the_sdks_own_interruption():
    """SPEC-v0.5 §3.5's decided-before-invocation shape. The predicate says a human is needed,
    the SDK stops with a `ToolApprovalItem`, and the tool body is never entered."""
    control, store = build()
    calls: list[Any] = []
    agent, _ = agent_for(control, calls)

    result = ctrlrun_openai_agents.run_sync(agent, "refund txn_1")

    assert result.interruptions, "the APPROVE did not reach the SDK's approval interruption"
    assert [item.tool_name for item in result.interruptions] == ["issue_refund"]
    assert calls == [], "the executor ran before a human answered"
    assert store.receipts() == (), "an action awaiting approval has no receipt (v0.1 §6.1)"


def test_approving_through_the_sdk_runs_the_action_and_records_the_channel():
    control, store = build()
    calls: list[Any] = []
    agent, _ = agent_for(control, calls)
    result = ctrlrun_openai_agents.run_sync(agent, "refund txn_1")

    state = result.to_state()
    for item in result.interruptions:
        state.approve(item)
    resumed = ctrlrun_openai_agents.run_sync(agent, state)

    assert calls == [{"payment_id": "txn_1", "amount": 2000}]
    assert [(r.result, r.approver) for r in store.receipts()] == [("committed", CHANNEL)]
    assert not resumed.interruptions


def test_rejecting_through_the_sdk_never_reaches_ctrlrun_at_all():
    """§7's "where the framework's behaviour is visible through the contract", and the one place
    this adapter's evidence differs from `@protect`'s.

    The SDK does not invoke a tool whose approval was refused, so no CTRLRun action is ever
    proposed: there is no `ACTION_DENIED`, no `APPROVAL_DENIED` and no receipt. The refusal is
    real and it is in the SDK's own output; it is simply not in CTRLRun's evidence log, because
    CTRLRun was never asked about it. The README says so rather than leaving an operator to
    discover an empty log.
    """
    control, store = build()
    calls: list[Any] = []
    agent, _ = agent_for(control, calls)
    result = ctrlrun_openai_agents.run_sync(agent, "refund txn_1")

    state = result.to_state()
    for item in result.interruptions:
        state.reject(item)
    ctrlrun_openai_agents.run_sync(agent, state)

    assert calls == []
    assert store.receipts() == ()
    assert [str(e.type) for e in store.events()].count("APPROVAL_DENIED") == 0


def test_an_allowed_action_is_never_put_to_a_human():
    """The predicate returns `False`, so the SDK invokes the tool directly -- no interruption,
    no request, one execution."""
    control, store = build(ALLOW)
    calls: list[Any] = []
    agent, _ = agent_for(control, calls)

    result = ctrlrun_openai_agents.run_sync(agent, "refund txn_1")

    assert not result.interruptions
    assert calls == [{"payment_id": "txn_1", "amount": 2000}]
    assert [str(e.type) for e in store.events()].count("APPROVAL_REQUESTED") == 0


def test_a_denied_action_is_invoked_and_denied_with_evidence():
    """SPEC-v0.5 §3.5: a `DENY` returns `False` from the predicate **on purpose**, so the tool
    is invoked and `Control.execute` denies it with a receipt. Refusing inside the predicate
    would refuse without evidence."""
    control, store = build(DENY)
    calls: list[Any] = []
    agent, _ = agent_for(control, calls)

    with pytest.raises(ActionDenied) as raised:
        ctrlrun_openai_agents.run_sync(agent, "refund txn_1")

    assert raised.value.reason == "unknown_action"
    assert calls == []
    assert [r.result for r in store.receipts()] == ["denied"]
    assert "ACTION_DENIED" in [str(e.type) for e in store.events()]


def test_the_predicate_writes_nothing_however_often_the_sdk_asks():
    """A framework may call its predicate more than once, and one that left a proposal in the
    log for every time the SDK wondered would be unusable."""
    control, store = build()
    agent_for(control, [])
    from ctrlrun.adapter import needs_approval

    before = (len(store.events()), len(store.receipts()))
    for _ in range(5):
        assert needs_approval(control, "stripe.refund", {"payment_id": "t", "amount": 1}) is True
    assert (len(store.events()), len(store.receipts())) == before


# --- the conformance kit, through a real Runner --------------------------------------------


class AgentsConformanceAdapter:
    """Drives the kit's `CallRequest` through a real `Runner`, so every suite crosses the SDK's
    own `needs_approval` / `ToolApprovalItem` / `state.approve` machinery."""

    framework = "openai-agents"
    #: SPEC-v0.5 §5.2. This SDK does not invoke a tool whose approval was refused, so a
    #: rejection proposes no CTRLRun action and there is nothing to deny. The `denial` suite is
    #: `not_applicable` with that reason, and never a pass.
    refuses_before_invoking = True

    def __init__(self) -> None:
        self.interrupt = AgentsInterrupt()

    def invoke(self, request: CallRequest) -> Any:
        import asyncio
        import json

        agent, _ = self._agent(request)
        return asyncio.run(self._run(agent, request, json.dumps(dict(request.arguments))))

    async def _run(self, agent: Agent[Any], request: CallRequest, _payload: str) -> Any:
        result = await ctrlrun_openai_agents.run(agent, "do it")
        if not result.interruptions:
            return _returned(result)
        if request.answer is None:
            raise AssertionError("the SDK interrupted when no human was expected")
        state = result.to_state()
        for item in result.interruptions:
            if request.answer.granted:
                state.approve(item)
            else:
                state.reject(item)
        resumed = await ctrlrun_openai_agents.run(agent, state)
        return _returned(resumed)

    def _agent(self, request: CallRequest):
        parameters = ", ".join(request.arguments)
        forwarded = ", ".join(f"{name}={name}" for name in request.arguments)
        namespace: dict[str, Any] = {"_executor": request.executor}
        exec(f"def tool({parameters}):\n    return _executor({forwarded})", namespace)
        protected = protect(
            request.action,
            effect=request.effect,
            resource="payment:{payment_id}",
            wait=True,
            control=request.control,
        )(namespace["tool"])

        signature = ", ".join(
            f"{name}: {type(value).__name__}" for name, value in request.arguments.items()
        )
        body: dict[str, Any] = {"_protected": protected}
        exec(
            f"async def tool_body({signature}) -> str:\n"
            f'    """Run the protected action."""\n'
            f"    return _protected({forwarded})",
            body,
        )
        import json

        # `protected_tool` and not a bare `function_tool`: it sets `failure_error_function=None`
        # so a CTRLRun refusal reaches the caller instead of the model (§12.6). Without it every
        # `kernel` case reports "did not raise", which is what this SDK's default does to an
        # exception -- and it is why the helper exists.
        tool = protected_tool(
            request.control,
            request.action,
            body["tool_body"],
            resource="payment:{payment_id}",
            name_override="run_it",
        )
        model = FakeModel("run_it", json.dumps(dict(request.arguments)))
        return Agent(name="kit", instructions="do it", tools=[tool], model=model), model


def _returned(result: Any) -> Any:
    """What the executor returned, out of the SDK's run output.

    The SDK reports a tool that raised through `failure_error_function` rather than propagating,
    so a CTRLRun refusal would be swallowed into a model-readable message -- which is precisely
    what the kit must see. The tool here is built without one, so the exception propagates out
    of `Runner.run`.
    """
    return result.final_output


def test_T135_the_agents_adapter_passes_the_conformance_kit():
    report = run(AgentsConformanceAdapter())

    assert report.ok is True, report.to_text()


def test_T135_a_refusal_before_invocation_makes_the_denial_suite_not_applicable():
    """The second thing this adapter forced into the contract. A framework that never invokes a
    refused tool proposes no CTRLRun action, so there is nothing to deny -- and a `denial` case
    sitting inside `kernel` would have reported `pass` for an adapter that cannot exercise it."""
    report = run(AgentsConformanceAdapter())

    denial = next(s for s in report.suites if s.name == "denial")
    assert denial.status is SuiteStatus.NOT_APPLICABLE
    assert "refuses before it invokes" in (denial.reason or "")


def test_T135_binding_is_not_applicable_and_the_report_says_why():
    """SPEC-v0.5 §3.4's other half, and the reason two reference adapters exist: they land on
    opposite sides of it. LangGraph carries the arguments back and gets prevention; this SDK
    cannot, and gets attribution -- reported `not_applicable` with the reason, never `pass`."""
    report = run(AgentsConformanceAdapter())

    binding = next(s for s in report.suites if s.name == "binding")
    assert binding.status is SuiteStatus.NOT_APPLICABLE
    assert "carries_approved_arguments=False" in (binding.reason or "")
    assert report.to_text().endswith("openai-agents: 4/4 (2 not applicable)")
    assert "6/6" not in report.to_text()


def test_T135b_the_adapter_reuses_the_sdks_primitive_and_reimplements_nothing():
    import ast
    import inspect

    import ctrlrun_openai_agents

    source = inspect.getsource(ctrlrun_openai_agents)
    tree = ast.parse(source)
    called = {
        node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }

    assert "_needs_approval" in called, "the predicate is not core's"
    for reimplemented in (
        "grant_approval",
        "deny_approval",
        "reserve_effect",
        "commit_effect",
        "put_approval_request",
        "append_event",
        "Control",
        "Principal",
        "sleep",
        "input",
    ):
        assert reimplemented not in called, reimplemented
    # A **polling** loop, not any loop: `unwrap` walks an exception's `__cause__` chain, which
    # is a `while` and is not a second approval path. What is forbidden is waiting for an answer
    # -- an unbounded loop, or one that sleeps -- because that is a queue of the adapter's own
    # with the serial numbers filed off.
    unbounded = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.While)
        and isinstance(node.test, ast.Constant)
        and node.test.value is True
    ]
    assert not unbounded, "an unbounded loop is an adapter waiting for an answer of its own"


def test_T135b_the_answer_arrives_through_the_sdk_and_no_other_channel():
    """Behavioural. First half: the run is left un-resumed -- no answer arrives, nothing is
    granted, the request stays pending, the executor is never called. Second half: the same run
    with `state.approve` invoked. Together they are what no second channel can satisfy."""
    control, store = build()
    calls: list[Any] = []
    agent, _ = agent_for(control, calls)
    result = ctrlrun_openai_agents.run_sync(agent, "refund txn_1")

    assert calls == []
    assert [str(e.type) for e in store.events()].count("APPROVAL_GRANTED") == 0
    assert store.receipts() == ()

    state = result.to_state()
    for item in result.interruptions:
        state.approve(item)
    ctrlrun_openai_agents.run_sync(agent, state)

    assert calls == [{"payment_id": "txn_1", "amount": 2000}]


# --- §7's README requirements, and §6.3's two ranges ----------------------------------------


def _needs_the_source_tree() -> None:
    """§7's documentation tests read `adapters/<name>/`, which the sdist does not carry."""
    if not ADAPTER.is_dir():  # pragma: no cover - running from an unpacked sdist
        pytest.skip("adapters/ is not in this distribution, which SPEC-v0.5 §6.1 requires")


def readme() -> str:
    """The adapter's README, or a skip where the adapter's source is not on disk.

    `adapters/` is pruned from the sdist (SPEC-v0.5 §6.1), and the sdist job unpacks the
    distribution and runs this suite from inside it. The §7 documentation tests read files that
    are deliberately not there, so they skip -- and only they do: everything that drives the
    adapter through a framework needs the installed distribution, which `importorskip` above
    has already established, and keeps running.
    """
    _needs_the_source_tree()
    return (ADAPTER / "README.md").read_text(encoding="utf-8")


def declared() -> dict[str, str]:
    _needs_the_source_tree()
    with (ADAPTER / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]
    return {name.split(">")[0].split("<")[0].strip(): name for name in project["dependencies"]}


def test_T137_the_readme_and_the_metadata_state_the_same_two_ranges():
    text = readme()

    for specifier in declared().values():
        assert specifier in text, f"{specifier!r} is in pyproject.toml and not in the README"
    assert "Supported kernel range" in text
    assert "Supported framework range" in text


def test_T137_the_declared_framework_range_contains_the_version_ci_installed():
    from importlib.metadata import version as installed

    from packaging.specifiers import SpecifierSet

    specifier = declared()["openai-agents"].removeprefix("openai-agents")
    assert installed("openai-agents") in SpecifierSet(specifier)


def test_T137b_the_readme_says_the_binding_is_attribution_and_why():
    """§7 item 4, and the sentence a security reviewer reads first. This adapter is the one that
    has to say the harder word."""
    text = readme()

    assert "carries_approved_arguments" in text
    assert "attribution" in text
    assert "call_id" in text, "the README does not say what closes the gap instead"


def test_the_readme_names_the_primitive_it_reuses_and_where_it_is_documented():
    text = readme()

    assert "needs_approval" in text
    assert "ToolApprovalItem" in text
    assert "openai.github.io/openai-agents-python" in text
    assert "Read " in text


def test_the_readme_says_when_you_do_not_need_this_adapter():
    text = readme()

    assert "@protect" in text
    assert "do not need" in text


def test_the_readme_records_that_a_rejection_leaves_no_ctrlrun_evidence():
    """The one place this adapter's evidence differs from `@protect`'s, and an operator finding
    an empty log after a refusal should find the sentence before the log."""
    text = readme()

    assert "reject" in text.lower()
    assert "no receipt" in text.lower() or "never asked" in text.lower()


def test_the_readme_carries_the_conformance_results_and_not_a_bare_conformant():
    text = readme()
    lowered = text.lower()

    assert "4/4" in text
    assert "not applicable" in lowered
    assert "certified" not in lowered
    assert "compliant" not in lowered
    assert "is conformant" not in lowered


def test_the_readme_records_the_measured_retry_behaviour():
    """§7 item 5. The probe measured this SDK landing the effect three to four times on a lost
    response, and an adapter's README is where an operator will look for it."""
    text = readme()

    assert "failure_error_function" in text
    assert "2026-09-05" in text


# --- the declaration is a fact about the framework, not a setting ---------------------------


def test_carries_approved_arguments_is_not_a_constructor_argument():
    """SPEC-v0.5 §3.4. For LangGraph it is a deployment's choice; here it is a property of the
    SDK, so there is nothing to pass and nothing an operator could get wrong."""
    import inspect

    assert AgentsInterrupt.carries_approved_arguments is False
    assert inspect.signature(AgentsInterrupt).parameters == {}


def test_the_adapter_never_hands_back_the_arguments_it_was_given():
    """§3.4's `echoes-the-payload` defect, which this adapter is structurally unable to commit:
    `interrupt()` takes a `PendingApproval` and returns an answer that mentions none of it."""
    from datetime import UTC, datetime

    from ctrlrun.adapter import PendingApproval

    now = datetime(2026, 1, 1, tzinfo=UTC)
    pending = PendingApproval(
        "apr_1",
        "act_1",
        "stripe.refund",
        "sha256:x",
        {"payment_id": "t", "amount": 1},
        None,
        "production",
        "agent",
        None,
        now,
        now,
    )

    # A real approved call in scope: `interrupt()` reads the SDK's answer and no longer
    # invents one, so there has to be one to read.
    with _bound_call(_tool_context(None, approved=True)):
        answer = AgentsInterrupt().interrupt(pending)

    assert answer.approved_arguments is None
    assert answer.granted is True
    assert answer.approver == CHANNEL


# --- `unwrap` restores the taxonomy and must not invent one (§12.7) -------------------------


def test_unwrap_does_not_mistake_the_pending_approval_for_the_outcome():
    """An AMBIGUOUS outcome must not reach the caller wearing `ApprovalRequired`.

    `@protect(wait=True)` runs its approved leg **inside** `except ApprovalRequired as pending:`
    (`control.py`), so every exception raised on that leg carries `__context__ =
    ApprovalRequired` -- implicit chaining, which Python sets whenever one exception is raised
    during the handling of another. It says nothing about causation.

    `unwrap` walking `__context__` therefore finds that `ApprovalRequired` and hands it back for
    an executor that raised a `TimeoutError` after the request went out: the kernel recorded
    AMBIGUOUS and re-raised, and the caller is told the action needs approving and then
    retrying. That is `v0.1 §5.5` inverted -- an ambiguous outcome presented as a definite
    did-not-run, with retry instructions attached -- and it fires on the approval path, which is
    the only path this adapter exists for.

    §12.7 says `__cause__`, which is the SDK's explicit chaining and the only link that means
    "this is the error behind that one".
    """
    from ctrlrun import ApprovalRequired

    pending = ApprovalRequired(request_id="apr_1", action_id="act_1")
    lost = TimeoutError("the response was lost")
    lost.__context__ = pending  # what the interpreter does on the approved leg

    assert unwrap(lost) is lost


def test_unwrap_still_finds_a_ctrlrun_error_the_sdk_wrapped_as_a_cause():
    """The other half: the case §12.7 is actually about must keep working."""
    from agents.exceptions import UserError

    denied = ActionDenied(reason="over the ceiling", action_id="act_1")
    wrapped = UserError("Error running tool issue_refund: over the ceiling")
    wrapped.__cause__ = denied

    assert unwrap(wrapped) is denied


def _pending():
    """A `PendingApproval` standing in for the one `wait()` builds; its content is irrelevant
    to these tests, which are about where the *answer* comes from."""
    from datetime import UTC, datetime

    from ctrlrun.adapter import PendingApproval

    now = datetime(2026, 1, 1, tzinfo=UTC)
    return PendingApproval(
        "apr_1",
        "act_1",
        "stripe.refund",
        "sha256:x",
        {"payment_id": "txn_1", "amount": 2000},
        None,
        "production",
        "refund-agent",
        None,
        now,
        now,
    )


def _tool_context(_control, *, approved):
    """A real `ToolContext` with the SDK's own approval state set the way the SDK sets it.

    `approved=None` is a call the SDK never gated -- which is the state that made the old
    `interrupt()` grant on nobody's behalf.
    """
    from agents import Agent
    from agents.items import ToolApprovalItem
    from agents.tool_context import ToolContext

    raw = ResponseFunctionToolCall(
        arguments='{"payment_id": "txn_1"}',
        call_id="call_1",
        name="issue_refund",
        type="function_call",
        id="fc_1",
    )
    context = ToolContext(
        context=None,
        tool_name="issue_refund",
        tool_call_id="call_1",
        tool_arguments='{"payment_id": "txn_1"}',
    )
    item = ToolApprovalItem(agent=Agent(name="a"), raw_item=raw, tool_name="issue_refund")
    if approved is True:
        context.approve_tool(item)
    elif approved is False:
        context.reject_tool(item)
    return context


def _approval_item():
    from agents import Agent
    from agents.items import ToolApprovalItem

    raw = ResponseFunctionToolCall(
        arguments='{"payment_id": "txn_1"}',
        call_id="call_1",
        name="issue_refund",
        type="function_call",
        id="fc_1",
    )
    return ToolApprovalItem(agent=Agent(name="a"), raw_item=raw, tool_name="issue_refund")


def _approve_always(context) -> None:
    """What a human does when they tick *always approve* rather than answering one call."""
    context.approve_tool(_approval_item(), always_approve=True)


def _rebind(context, call_id: str):
    """The same run context, seen from a **different** tool call of the same tool."""
    from agents.tool_context import ToolContext

    rebound = ToolContext(
        context=None,
        tool_name=context.tool_name,
        tool_call_id=call_id,
        tool_arguments='{"payment_id": "txn_2", "amount": 100000000}',
    )
    rebound._approvals = context._approvals
    return rebound


# --- the answer comes from the SDK, and is never assumed (§2.4, §3.8) -----------------------
#
# `interrupt()` returned `granted=True` unconditionally. Its premise -- "a tool body that runs
# *is* the approval" -- holds only when the SDK's pre-invocation gate actually asked and was
# told yes. These are the paths on which it did not.


def test_an_interrupt_outside_a_gated_tool_call_grants_nothing():
    """The provider hangs off the `Control`, not off the tool. Any `@protect(wait=True)` call on
    the same `Control` that is not behind `protected_tool` -- a plain `function_tool`, a
    background job, a webhook handler in the same process -- reaches `interrupt()` with no SDK
    call in scope and therefore with nobody having been asked.

    §10 row 1: `interrupt()` raises, nothing is written, the request stays `pending`.
    """
    control, store = build()
    calls: list[Any] = []

    @protect("stripe.refund", effect="refund:{payment_id}", wait=True, control=control)
    def issue_refund(payment_id: str, amount: int) -> str:
        calls.append({"payment_id": payment_id, "amount": amount})
        return "committed"

    with pytest.raises(ApprovalNotAsked):
        issue_refund(payment_id="txn_1", amount=2000)

    assert calls == [], "the executor ran on an approval nobody gave"
    events = [event.type.value for event in store.events()]
    assert "APPROVAL_GRANTED" not in events, events
    assert "APPROVAL_DENIED" not in events, events
    assert store.list_effects() == (), "an effect was reserved for an action nobody approved"


def test_a_call_the_sdk_never_gated_grants_nothing():
    """The divergence §3.5 says is harmless, which with a self-granting `interrupt()` is not.

    `needs_approval` sees the arguments the model sent; `@protect` sees the arguments the
    function was called with, after Python applied its defaults. When the predicate says "no
    approval needed" and the kernel then decides APPROVE, the SDK never asked -- and the old
    `interrupt()` answered on the absent human's behalf.

    `is_tool_approved` returns `None` for a call the SDK never gated, which is what makes the
    two distinguishable at all.
    """
    control, _ = build()

    tool_context = _tool_context(control, approved=None)
    with _bound_call(tool_context), pytest.raises(ApprovalNotAsked) as refused:
        AgentsInterrupt().interrupt(_pending())

    assert "never gated this call" in str(refused.value)


def test_the_sdks_no_is_returned_as_a_denial_and_never_as_a_grant():
    """A rejection recorded on the run context is a human's answer, and it is `granted=False`."""
    with _bound_call(_tool_context(None, approved=False)):
        answer = AgentsInterrupt().interrupt(_pending())

    assert answer.granted is False
    assert answer.approver == CHANNEL


def test_the_sdks_yes_is_the_grant_and_names_the_channel():
    """The path that always worked, now for the reason it claims: the SDK said yes."""
    with _bound_call(_tool_context(None, approved=True)):
        answer = AgentsInterrupt().interrupt(_pending())

    assert answer.granted is True
    assert answer.approver == CHANNEL


BANDS = """
schema: ctrlrun.policy/v1
actions:
  stripe.refund:
    rules:
      - when: { amount_gte: 0, amount_lte: 50000 }
        decision: allow
      - when: { amount_gte: 0, amount_lte: 500000 }
        decision: approve
      - decision: deny
"""


def test_the_predicate_and_the_kernel_disagreeing_does_not_execute_unapproved():
    """§3.5's divergence, end to end through a real `Runner`, with the whole SDK in the path.

    The model calls the tool without `amount`, and the tool body's default supplies it. So the
    `needs_approval` predicate evaluates `{"payment_id": "txn_1"}` -- no band matches, the rules
    fall through to `deny`, `needs_approval` is `False` and the SDK does not ask -- while
    `Control.execute` evaluates `{"payment_id": "txn_1", "amount": 100000}`, which is a $1,000
    refund and lands squarely in the `approve` band.

    §3.5 argues this direction is harmless *because* "`Control.execute` raises
    `ApprovalRequired`, `@protect(wait=True)` routes it through the interrupt, and the human is
    asked anyway". That argument is only true of an `interrupt()` that asks. When it answered
    itself, this executed a $1,000 refund with no human and wrote a receipt naming
    `openai-agents:tool-approval` as the approver -- a grant nobody made, in the evidence log.

    Now the SDK holds no answer for a call it never gated, so the refusal is `ApprovalNotAsked`
    and the request is left `pending` for `ctrlrun approve`.
    """
    from agents import Agent, Runner

    control, store = build(BANDS)
    calls: list[Any] = []

    @protect("stripe.refund", effect="refund:{payment_id}", wait=True, control=control)
    def issue_refund(payment_id: str, amount: int) -> str:
        calls.append({"payment_id": payment_id, "amount": amount})
        return "committed"

    async def tool_body(payment_id: str, amount: int = 100000) -> str:
        """Issue a refund for a payment. Amounts are in integer minor units."""
        return issue_refund(payment_id=payment_id, amount=amount)

    tool = protected_tool(control, "stripe.refund", tool_body, name_override="issue_refund")
    model = FakeModel("issue_refund", '{"payment_id": "txn_1"}')
    agent = Agent(name="refunds", instructions="You handle refunds.", tools=[tool], model=model)

    # The refusal reaches the caller rather than the model: `protected_tool` sets
    # `failure_error_function=None` so a CTRLRun refusal is not turned into text an agent can
    # retry against (§12.7). `unwrap` gives back what was actually raised.
    with pytest.raises(Exception) as raised:
        Runner.run_sync(agent, "refund txn_1")
    assert isinstance(unwrap(raised.value), ApprovalNotAsked), raised.value

    assert calls == [], f"a $1,000 refund executed with no human: {calls}"
    events = [event.type.value for event in store.events()]
    assert "APPROVAL_GRANTED" not in events, events
    assert [record.approver for record in store.receipts() if record.approver] == [], (
        "a receipt names an approver for an approval nobody gave"
    )


OBSERVING = """
schema: ctrlrun.policy/v3
mode: observe
actions:
  stripe.refund:
    decision: approve
"""


def test_the_adapter_logs_the_observe_banner_once_per_control(caplog):
    """SPEC-v0.5 §3.6. A deployment that has been observing for six months is what the line is
    for, and an adapter is the quietest place in the system to forget it.

    Logged, never printed: an adapter is inside somebody else's loop and may have no stream that
    reaches a human. `approval_gate` is the attach point -- the first place this adapter is
    handed the operator's `Control` -- and `interrupt()` is not, because observe mode never
    raises `ApprovalRequired` and so never reaches an interrupt at all.
    """
    import logging

    control, _ = build(OBSERVING)

    with caplog.at_level(logging.WARNING, logger="ctrlrun"):
        approval_gate(control, "stripe.refund")
        approval_gate(control, "stripe.refund")

    banners = [r for r in caplog.records if "OBSERVE MODE" in r.getMessage()]
    assert len(banners) == 1, [r.getMessage() for r in banners]


def test_no_banner_under_enforce(caplog):
    """A no-op under `mode: enforce`, or the warning is one nobody reads by the third run."""
    import logging

    control, _ = build()

    with caplog.at_level(logging.WARNING, logger="ctrlrun"):
        approval_gate(control, "stripe.refund")

    assert [r for r in caplog.records if "OBSERVE MODE" in r.getMessage()] == []


# --- T133: the adapter's kit run, with the network taken away -------------------------------


def _guarded(tmp_path, script: str):
    """Run `script` in a subprocess whose `sitecustomize` refuses every socket.

    SPEC-v0.5 §3.7 and T133. `tests/test_conformance.py` runs the in-process `Reference` under
    the same guard, and T133 says in as many words why that is not enough: *"The in-process
    adapter of T131 would open none either way; the reference adapters, with a framework SDK
    loaded, are where this test has a subject."* A framework SDK is exactly the thing that
    might phone home -- the Agents SDK's tracing exporter is the obvious candidate -- so this is
    the run the guard exists for.
    """
    import subprocess
    import sys

    from test_conformance import NO_SOCKETS

    (tmp_path / "sitecustomize.py").write_text(NO_SOCKETS, encoding="utf-8")
    (tmp_path / "script.py").write_text(script, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(tmp_path / "script.py")],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={
            "PYTHONPATH": f"{Path(__file__).resolve().parent}:{tmp_path}",
            "PATH": "/usr/bin:/bin",
            # The SDK prints "OPENAI_API_KEY is not set, skipping trace export" and skips the
            # exporter. Unset here too, so the subprocess cannot take a different path from
            # this one because of an environment variable the developer happens to have.
            "OPENAI_API_KEY": "",
        },
        timeout=300,
    )


_RUN_UNDER_GUARD = """
import logging
logging.disable(logging.CRITICAL)
import test_adapters_openai_agents as harness
from ctrlrun.conformance import run
report = run(harness.AgentsConformanceAdapter())
assert report.ok, report.to_text()
print("ok")
"""


def test_T133_the_adapters_kit_run_reaches_no_network(tmp_path):
    """The whole kit, through a real framework, with every socket refused."""
    result = _guarded(tmp_path, _RUN_UNDER_GUARD)

    assert result.returncode == 0, result.stderr[-3000:]
    assert "ok" in result.stdout


def test_T133_and_the_guard_can_see_a_socket_when_there_is_one(tmp_path):
    """T133's precondition. Without it this is a negative test
    against something the environment already prevented -- and it would pass just as well with
    the `sitecustomize` deleted."""
    opens_one = (
        _RUN_UNDER_GUARD
        + """
import urllib.request
urllib.request.urlopen("http://127.0.0.1:1/", timeout=1)
"""
    )
    result = _guarded(tmp_path, opens_one)

    assert result.returncode != 0, "the guard did not notice a deliberate socket"


def test_the_readme_says_what_happens_when_the_predicate_and_the_kernel_disagree():
    """SPEC-v0.5 §7 item 5, and the sentence that was wrong. The README claimed both directions
    were safe while `interrupt()` granted unconditionally, so it printed the line that says the
    guard worked for a guard that did not -- which is worse than saying nothing."""
    text = readme()

    assert "ApprovalNotAsked" in text
    assert "is_tool_approved" in text
    assert "must go through `protected_tool`" in text


def test_a_sticky_always_approve_is_not_an_answer_for_a_later_call():
    """`state.approve(item, always_approve=True)` records a decision keyed by **tool name**,
    and `RunContextWrapper.is_tool_approved` then answers `True` for every later `call_id` of
    that tool (`run_context.py`: `if approval_entry.approved is True: return True`, reached
    after the exact-call lookup misses).

    A human who said *always approve refunds* has not seen this refund. CTRLRun's entire claim
    is that an approval binds to one action -- `v0.1 §4.2` consumes a grant against an
    `action_hash` -- and a blanket yes for a tool is the thing that claim exists to refuse. With
    `carries_approved_arguments=False` there is no binding check in core to catch it either, so
    this is the last line.

    Reading the sticky value would mean a $1,000,000 refund executing on the strength of a $5
    one a human waved through, with a receipt naming `openai-agents:tool-approval` as approver.
    """
    context = _tool_context(None, approved=None)
    _approve_always(context)

    # The SDK's own sticky answer, which is what the first fix read.
    assert context.is_tool_approved("issue_refund", "call_999") is True

    # What this adapter must do with it: nobody approved call_999.
    pending = _pending()
    with _bound_call(_rebind(context, "call_999")), pytest.raises(ApprovalNotAsked) as refused:
        AgentsInterrupt().interrupt(pending)
    assert "call_999" in str(refused.value)


def test_the_call_a_human_did_approve_is_still_granted():
    """The other half: narrowing to per-call answers must not refuse the approved call."""
    context = _tool_context(None, approved=True)

    with _bound_call(context):
        answer = AgentsInterrupt().interrupt(_pending())

    assert answer.granted is True and answer.approver == CHANNEL


# --- the two defences against a cyclic `__cause__`, opened one at a time ---------------------
#
# `unwrap` walking a cycle and `run_sync` building one are independent, and a test that only
# drove `run_sync` would pass with either fixed. So each window is opened on its own.


def test_unwrap_terminates_on_a_cyclic_cause_chain():
    """Defence one, with the cycle built by hand so the walk is the only thing under test.

    An exception chain is not guaranteed to be a chain: `raise X from Y` where `Y.__cause__` is
    already `X` closes a loop, and a walk with no seen-set never leaves it. Bounded by the
    exceptions already visited.
    """
    import threading

    from agents.exceptions import UserError

    lost = TimeoutError("the response was lost")
    wrapper = UserError("Error running tool run_it: ...")
    wrapper.__cause__ = lost
    lost.__cause__ = wrapper

    # Bounded, because the failure this guards against is a **hang**, and a hung test is not a
    # failing test -- it is a CI job that times out an hour later saying nothing. The walk runs
    # on a daemon thread so an unbounded one leaks rather than blocking the suite, and the
    # assertion is that it finished at all.
    done: list[Any] = []
    walker = threading.Thread(target=lambda: done.append(unwrap(lost)), daemon=True)
    walker.start()
    walker.join(timeout=10)

    assert not walker.is_alive(), "unwrap() did not terminate: the __cause__ chain is a cycle"
    # No CTRLRunError anywhere in the cycle, which is the case that used to spin: the AMBIGUOUS
    # outcome, where the kernel re-raised the executor's own exception unchanged.
    assert done == [lost]


def test_run_sync_does_not_build_a_cycle_in_the_first_place():
    """Defence two, checked at the raise rather than through `unwrap`, so it stands on its own.

    `raise recovered from raised` set `recovered.__cause__ = raised` -- but `recovered` was found
    by walking `raised.__cause__`, so the chain pointed back at itself.
    """
    control, _ = build(BANDS)

    @protect("stripe.refund", effect="refund:{payment_id}", wait=True, control=control)
    def issue_refund(payment_id: str, amount: int) -> str:
        return "committed"

    async def tool_body(payment_id: str, amount: int = 100000) -> str:
        """Issue a refund."""
        return issue_refund(payment_id=payment_id, amount=amount)

    tool = protected_tool(control, "stripe.refund", tool_body, name_override="issue_refund")
    model = FakeModel("issue_refund", '{"payment_id": "txn_1"}')
    agent = Agent(name="refunds", instructions="refunds", tools=[tool], model=model)

    with pytest.raises(Exception) as raised:
        ctrlrun_openai_agents.run_sync(agent, "refund txn_1")

    # Walk the chain to its end with a bound of our own, and assert it terminates.
    seen: set[int] = set()
    node: BaseException | None = raised.value
    while node is not None:
        assert id(node) not in seen, f"cyclic __cause__ chain at {node!r}"
        seen.add(id(node))
        node = node.__cause__
