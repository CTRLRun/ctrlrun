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
    protected_tool,
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
                arguments=self._arguments, call_id="call_1", name=self._name,
                type="function_call", id="fc_1",
            )
            return ModelResponse(output=[call], usage=Usage(), response_id=None)
        message = ResponseOutputMessage(
            id="msg_1",
            content=[ResponseOutputText(annotations=[], text="done", type="output_text")],
            role="assistant", status="completed", type="message",
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
    return Agent(name="refunds", instructions="You handle refunds.", tools=[tool],
                 model=model), model


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
        exec(
            f"def tool({parameters}):\n    return _executor({forwarded})", namespace
        )
        protected = protect(
            request.action,
            effect=request.effect,
            resource="payment:{payment_id}",
            wait=True,
            control=request.control,
        )(namespace["tool"])

        signature = ", ".join(f"{name}: {type(value).__name__}"
                              for name, value in request.arguments.items())
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
            request.control, request.action, body["tool_body"],
            resource="payment:{payment_id}", name_override="run_it",
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
        "grant_approval", "deny_approval", "reserve_effect", "commit_effect",
        "put_approval_request", "append_event", "Control", "Principal", "sleep", "input",
    ):
        assert reimplemented not in called, reimplemented
    # A **polling** loop, not any loop: `unwrap` walks an exception's `__cause__` chain, which
    # is a `while` and is not a second approval path. What is forbidden is waiting for an answer
    # -- an unbounded loop, or one that sleeps -- because that is a queue of the adapter's own
    # with the serial numbers filed off.
    unbounded = [
        node for node in ast.walk(tree)
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


def readme() -> str:
    return (ADAPTER / "README.md").read_text(encoding="utf-8")


def declared() -> dict[str, str]:
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
        "apr_1", "act_1", "stripe.refund", "sha256:x", {"payment_id": "t", "amount": 1},
        None, "production", "agent", None, now, now,
    )

    answer = AgentsInterrupt().interrupt(pending)

    assert answer.approved_arguments is None
    assert answer.granted is True
    assert answer.approver == CHANNEL
