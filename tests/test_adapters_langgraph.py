"""The LangGraph reference adapter. SPEC-v0.5 §3.5, §6, §7; T135, T135b, T137.

Every test here drives a **real compiled graph with a real checkpointer**, and the round trip
goes out through `interrupt()` and back through `Command(resume=...)`. No model is involved:
the graph has one node that calls the protected tool, which is what a tool node does once the
model has chosen a call, and the question these tests ask is about the adapter rather than
about what a model would decide.

Skipped **by name** where `langgraph` is not installed, so a green run with the framework
missing cannot look like a pass (`v0.4 §7` T123's rule, applied here).
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest

from ctrlrun import (
    Action,
    ApprovalAnswer,
    Control,
    InterruptApprovalProvider,
    PendingApproval,
    Principal,
    protect,
)
from ctrlrun.conformance import SuiteStatus, run
from ctrlrun.conformance.suites import CallRequest
from ctrlrun.identity import StaticIdentityProvider
from ctrlrun.policy import Policy
from ctrlrun.state import InMemoryStateStore

langgraph = pytest.importorskip("langgraph", reason="langgraph is not installed")
pytest.importorskip("ctrlrun_langgraph", reason="ctrlrun-langgraph is not installed")

from ctrlrun_langgraph import CHANNEL, LangGraphInterrupt  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.graph import END, START, StateGraph  # noqa: E402
from langgraph.types import Command  # noqa: E402

#: Every `run()` drives the kit's `authority` suite, which builds an `authority:` section.
pytestmark = pytest.mark.authority

ADAPTER = Path(__file__).resolve().parents[1] / "adapters" / "langgraph"

APPROVE = """
schema: ctrlrun.policy/v1
actions:
  stripe.refund:
    decision: approve
"""


def build(document: str = APPROVE, *, carries: bool = True):
    store = InMemoryStateStore()
    control = Control(
        Policy.from_yaml(document, source="<test>"),
        store,
        InterruptApprovalProvider(store, LangGraphInterrupt(carries_approved_arguments=carries)),
        identity=StaticIdentityProvider("refund-agent"),
        environment="production",
    )
    return control, store


def graph_for(control: Control, calls: list[Any]):
    @protect("stripe.refund", effect="refund:{payment_id}", wait=True, control=control)
    def issue_refund(payment_id: str, amount: int) -> str:
        calls.append({"payment_id": payment_id, "amount": amount})
        return "committed"

    def node(state: dict[str, Any]) -> dict[str, Any]:
        return {"result": issue_refund(payment_id=state["payment_id"], amount=state["amount"])}

    builder = StateGraph(dict)
    builder.add_node("refund", node)
    builder.add_edge(START, "refund")
    builder.add_edge("refund", END)
    return builder.compile(checkpointer=InMemorySaver())


def interrupts(graph, config) -> list[Any]:
    return [one.value for task in graph.get_state(config).tasks for one in task.interrupts]


# --- the round trip, through the framework's own primitive ---------------------------------


def test_an_approve_reaches_a_human_through_langgraphs_interrupt_and_not_past_it():
    """SPEC-v0.5 §3.2, driven. The first pass exits at the interrupt with the executor
    untouched; the resumed pass returns from it and the action commits."""
    control, store = build()
    calls: list[Any] = []
    graph = graph_for(control, calls)
    config = {"configurable": {"thread_id": "t1"}}

    first = graph.invoke({"payment_id": "txn_1", "amount": 2000}, config)

    assert "__interrupt__" in first, "the APPROVE was raised past the graph, not through it"
    assert calls == [], "the executor ran before a human answered"
    assert store.receipts() == (), "an action awaiting approval has no receipt (v0.1 §6.1)"

    pending = interrupts(graph, config)[0]
    assert pending["action"] == "stripe.refund"
    assert pending["arguments"] == {"amount": 2000, "payment_id": "txn_1"}
    assert pending["agent"] == "refund-agent"

    second = graph.invoke(
        Command(resume={"approved": True, "approver": "ada", "arguments": pending["arguments"]}),
        config,
    )

    assert second["result"] == "committed"
    assert calls == [{"payment_id": "txn_1", "amount": 2000}]
    assert [(r.result, r.approver) for r in store.receipts()] == [("committed", "ada")]


def test_a_refusal_through_the_interrupt_is_a_denial_and_not_an_error():
    from ctrlrun import ActionDenied

    control, store = build()
    calls: list[Any] = []
    graph = graph_for(control, calls)
    config = {"configurable": {"thread_id": "t2"}}
    graph.invoke({"payment_id": "txn_2", "amount": 2000}, config)

    with pytest.raises(ActionDenied):
        graph.invoke(Command(resume={"approved": False, "approver": "ada"}), config)

    assert calls == []
    assert [str(e.type) for e in store.events()].count("APPROVAL_DENIED") == 1


def test_an_answer_given_against_different_arguments_authorizes_nothing():
    """SPEC-v0.5 §3.4's prevention half, in this adapter. `carries_approved_arguments=True`, so
    the provider rebuilds the proposal with what came back and compares the action hash."""
    from ctrlrun import ApprovalMismatch

    control, store = build()
    calls: list[Any] = []
    graph = graph_for(control, calls)
    config = {"configurable": {"thread_id": "t3"}}
    graph.invoke({"payment_id": "txn_3", "amount": 2000}, config)

    with pytest.raises(ApprovalMismatch) as raised:
        graph.invoke(
            Command(
                resume={
                    "approved": True,
                    "approver": "ada",
                    # The human answered about 500. The action proposes 2000.
                    "arguments": {"payment_id": "txn_3", "amount": 500},
                }
            ),
            config,
        )

    assert raised.value.reason == "mismatch"
    assert calls == []
    assert store.receipts() == ()


def test_the_interrupt_payload_survives_a_checkpoint_as_json():
    """LangGraph persists the interrupt value, so a payload that would not serialize is one that
    works until the first restart. `PendingApproval.to_dict` is JSON by construction and this is
    the framework proving it needs to be."""
    control, _ = build()
    graph = graph_for(control, [])
    config = {"configurable": {"thread_id": "t4"}}
    graph.invoke({"payment_id": "txn_4", "amount": 2000}, config)

    payload = interrupts(graph, config)[0]

    assert json.loads(json.dumps(payload)) == payload


# --- §3.2.1's three costs, observed rather than asserted in prose ---------------------------


def test_the_replayed_pass_builds_a_new_action_id_and_a_new_request():
    """SPEC-v0.5 §3.2.1. LangGraph re-runs the node from the checkpoint, so `@protect` builds a
    new `Action` and `_presented` creates a new request: the `action_id` a human saw is not the
    one on the receipt, the first request is orphaned, and `action_hash` is what stays
    continuous — which is why §3.4's carried value is about content and never about an id.

    None of that is safe by accident. Step 13 is: the resumed pass re-runs principal expiry,
    authority and policy at resumption time. This test is what keeps §3.2.1 from becoming a
    paragraph nobody checked.
    """
    control, store = build()
    graph = graph_for(control, [])
    config = {"configurable": {"thread_id": "t5"}}
    graph.invoke({"payment_id": "txn_5", "amount": 2000}, config)
    first = interrupts(graph, config)[0]

    graph.invoke(
        Command(resume={"approved": True, "approver": "ada", "arguments": first["arguments"]}),
        config,
    )

    requested = [e for e in store.events() if str(e.type) == "APPROVAL_REQUESTED"]
    assert len(requested) == 2, "the replayed pass did not create its own request"

    receipt = store.receipts()[0]
    assert receipt.action_id != first["action_id"], "§3.2.1 says these differ; they did not"
    assert receipt.action_hash == first["action_hash"], (
        "the hash moved across the replay, which would mean the checkpoint did not replay the "
        "same call — and §3.4's whole argument rests on it not moving"
    )

    orphan = store.get_approval(first["request_id"])
    assert orphan is not None and str(orphan.status) == "pending", (
        "§3.2.1 says the first pass's request is orphaned and stays grantable; it is not a hole "
        "— single-use and hash-bound — but it is not nothing either"
    )


# --- the conformance kit, through a real graph ---------------------------------------------


class LangGraphConformanceAdapter:
    """Drives the kit's `CallRequest` through a compiled graph, so every suite crosses a real
    `interrupt()` / `Command(resume=...)` round trip rather than an in-process stand-in."""

    framework = "langgraph"

    def __init__(self, *, carries: bool = True) -> None:
        self.interrupt = LangGraphInterrupt(carries_approved_arguments=carries)
        self._thread = 0

    def invoke(self, request: CallRequest) -> Any:
        self._thread += 1
        config = {"configurable": {"thread_id": f"kit-{self._thread}"}}
        graph = self._graph(request)
        outcome = graph.invoke(dict(request.arguments), config)
        if "__interrupt__" not in outcome:
            return outcome.get("result")
        if request.answer is None:
            raise AssertionError("the graph interrupted when no human was expected")
        return graph.invoke(Command(resume=_resume(request.answer)), config).get("result")

    def _graph(self, request: CallRequest):
        parameters = ", ".join(request.arguments)
        forwarded = ", ".join(f"{name}={name}" for name in request.arguments)
        namespace: dict[str, Any] = {"_executor": request.executor}
        exec(f"def tool({parameters}):\n    return _executor({forwarded})", namespace)
        tool = protect(
            request.action,
            effect=request.effect,
            resource="payment:{payment_id}",
            wait=True,
            control=request.control,
        )(namespace["tool"])

        def node(state: dict[str, Any]) -> dict[str, Any]:
            return {"result": tool(**{name: state[name] for name in request.arguments})}

        builder = StateGraph(dict)
        builder.add_node("call", node)
        builder.add_edge(START, "call")
        builder.add_edge("call", END)
        return builder.compile(checkpointer=InMemorySaver())


def _resume(answer: ApprovalAnswer) -> dict[str, Any]:
    """The kit's answer, in this adapter's resume shape. A courier and nothing more: it carries
    what it was given and substitutes nothing (SPEC-v0.5 §5.2)."""
    resume: dict[str, Any] = {"approved": answer.granted, "approver": answer.approver}
    if answer.approved_arguments is not None:
        resume["arguments"] = dict(answer.approved_arguments)
    return resume


def test_T135_the_langgraph_adapter_passes_the_conformance_kit():
    report = run(LangGraphConformanceAdapter())

    assert report.ok is True, report.to_text()
    assert report.not_applicable == (), (
        "this adapter declares carries_approved_arguments=True, so nothing should be N/A"
    )
    assert report.to_text().endswith("langgraph: 6/6")


def test_T135_and_a_non_carrying_wiring_reports_binding_not_applicable():
    """The other declaration, and it is not free: `binding` becomes N/A with the reason, the
    denominator drops, and the README has to say "attribution" (§3.4, T137b)."""
    report = run(LangGraphConformanceAdapter(carries=False))

    assert report.ok is True
    binding = next(s for s in report.suites if s.name == "binding")
    assert binding.status is SuiteStatus.NOT_APPLICABLE
    assert report.to_text().endswith("langgraph: 5/5 (1 not applicable)")


def test_T135b_the_answer_arrives_through_the_framework_and_no_other_channel():
    """Behavioural, because the source-inspection version of this is unfalsifiable.

    First half: the graph is left un-resumed. No answer arrives, nothing is granted, the
    request stays pending, the executor is never called. An adapter with a second path — a
    prompt, a queue, a poll — would have to leave that path idle to pass this half.

    Second half: the same run with LangGraph's own resumption invoked. The answer arrives and
    the action commits. Together they are what no second channel can satisfy.
    """
    control, store = build()
    calls: list[Any] = []
    graph = graph_for(control, calls)
    config = {"configurable": {"thread_id": "t6"}}

    graph.invoke({"payment_id": "txn_6", "amount": 2000}, config)
    pending = interrupts(graph, config)[0]

    record = store.get_approval(pending["request_id"])
    assert record is not None and str(record.status) == "pending"
    assert calls == []
    assert [str(e.type) for e in store.events()].count("APPROVAL_GRANTED") == 0

    graph.invoke(
        Command(resume={"approved": True, "approver": "ada", "arguments": pending["arguments"]}),
        config,
    )

    assert calls == [{"payment_id": "txn_6", "amount": 2000}]


def test_T135b_the_adapter_reimplements_nothing_and_grants_nothing():
    """The structural half, kept narrow because the behavioural half above is the real test.

    Read from the module's **code**, not its text: the docstring shows an operator how to wire
    a `Control`, and a source scan that tripped over its own worked example would be a test
    somebody deletes rather than satisfies.
    """
    import ast
    import inspect

    import ctrlrun_langgraph

    source = inspect.getsource(ctrlrun_langgraph)
    tree = ast.parse(source)
    called = {
        node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }

    assert "from langgraph.types import interrupt" in source, (
        "the adapter does not use LangGraph's own primitive"
    )
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


# --- §7's README requirements, and §6.3's two ranges ----------------------------------------


def readme() -> str:
    return (ADAPTER / "README.md").read_text(encoding="utf-8")


def declared() -> dict[str, str]:
    with (ADAPTER / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]
    return {name.split(">")[0].split("<")[0].strip(): name for name in project["dependencies"]}


def test_T137_the_readme_and_the_metadata_state_the_same_two_ranges():
    """SPEC-v0.5 §6.3. A README that says one thing and metadata that says another is the
    version somebody typed, which `v0.4 §7.3` rule 5 already refused in the other direction."""
    text = readme()

    for specifier in declared().values():
        assert specifier in text, f"{specifier!r} is in pyproject.toml and not in the README"
    assert "Supported kernel range" in text
    assert "Supported framework range" in text


def test_T137_the_declared_framework_range_contains_the_version_ci_installed():
    """A range that excluded what the tests actually ran against would be a range nobody
    checked."""
    from importlib.metadata import version as installed

    from packaging.specifiers import SpecifierSet

    specifier = declared()["langgraph"].removeprefix("langgraph")
    assert installed("langgraph") in SpecifierSet(specifier), (
        f"CI ran against langgraph {installed('langgraph')}, which the declared range "
        f"{specifier!r} excludes"
    )


def test_the_readme_names_the_primitive_it_reuses_and_where_it_is_documented():
    """§7 item 2: not "LangGraph's interrupt support" but the two names, with a link and a
    date, so a reader can check what was true when it was written."""
    text = readme()

    assert "`interrupt()`" in text
    assert "`Command(resume=...)`" in text
    assert "langchain-ai.github.io/langgraph" in text
    assert "Read " in text


def test_T137b_the_readme_says_which_kind_of_guard_the_binding_is():
    """§7 item 4, and the sentence a security reviewer reads first."""
    text = readme()

    assert "carries_approved_arguments" in text
    assert "prevention" in text
    assert "attribution" in text


def test_the_readme_says_when_you_do_not_need_this_adapter():
    """§1.1. Most readers arriving here need `@protect`, and the paragraph that introduces the
    adapter is where they should be told so."""
    text = readme()

    assert "@protect" in text
    assert "do not need" in text or "probably do not need" in text


def test_the_readme_records_where_the_frameworks_behaviour_shows_through():
    """§7 item 5, and the three costs §3.2.1 names for a resumed-in-place adapter."""
    text = readme()

    assert "action_id" in text, "the id discontinuity is not recorded"
    assert "checkpoint" in text
    assert "TTL" in text or "expiry" in text


def test_the_readme_carries_the_conformance_results_and_not_a_bare_conformant():
    """§7 item 7. "Conformant" is a word this project does not use about itself or an adapter."""
    text = readme()
    lowered = text.lower()

    assert "6/6" in text
    assert "certified" not in lowered
    assert "compliant" not in lowered
    assert "is conformant" not in lowered


# --- the shape a deployment answers in -------------------------------------------------------


@pytest.mark.parametrize(
    "resume,granted,approver",
    [
        (True, True, CHANNEL),
        (False, False, CHANNEL),
        ({"approved": True, "approver": "ada", "arguments": {"a": 1}}, True, "ada"),
        ({"approved": False}, False, CHANNEL),
    ],
)
def test_the_resume_grammar_reads_what_a_console_can_send(resume, granted, approver):
    answer = LangGraphInterrupt(carries_approved_arguments=False).answer(resume)

    assert answer.granted is granted
    assert answer.approver == approver


@pytest.mark.parametrize(
    "resume",
    [None, "yes", 1, [], {}, {"approver": "ada"}, {"approved": "yes"}, {"approved": 1}],
)
def test_a_resume_value_that_states_no_verdict_is_refused(resume):
    """A truthy string is not a yes. Refused here as well as in the kernel, because the message
    that names LangGraph's resume value is the one an operator can act on."""
    from ctrlrun import InvalidArgument

    with pytest.raises(InvalidArgument):
        LangGraphInterrupt(carries_approved_arguments=False).answer(resume)


def test_a_declared_carrier_refuses_a_grant_with_no_arguments():
    from ctrlrun import InvalidArgument

    carrying = LangGraphInterrupt(carries_approved_arguments=True)

    with pytest.raises(InvalidArgument):
        carrying.answer({"approved": True, "approver": "ada"})

    # A refusal carries nothing and is not refused for it (SPEC-v0.5 §12.2).
    assert carrying.answer({"approved": False}).granted is False


def test_the_declaration_has_no_default():
    """SPEC-v0.5 §3.4: the default somebody assumes is the one that does not check."""
    with pytest.raises(TypeError):
        LangGraphInterrupt()  # type: ignore[call-arg]


def test_the_approver_names_a_channel_and_not_a_person():
    """SPEC-v0.3 §13 keeps authenticating the approver out of scope, so the fallback says where
    the answer came from and never who gave it."""
    assert CHANNEL == "langgraph:interrupt"
    assert ":" in CHANNEL

    store = InMemoryStateStore()
    control = Control(
        Policy.from_yaml(APPROVE, source="<test>"),
        store,
        InterruptApprovalProvider(store, LangGraphInterrupt(carries_approved_arguments=False)),
        identity=StaticIdentityProvider("refund-agent"),
        environment="production",
    )
    calls: list[Any] = []
    graph = graph_for(control, calls)
    config = {"configurable": {"thread_id": "t7"}}
    graph.invoke({"payment_id": "txn_7", "amount": 2000}, config)
    graph.invoke(Command(resume=True), config)

    assert [r.approver for r in store.receipts()] == [CHANNEL]


def test_the_pending_approval_the_human_sees_carries_no_object_it_could_act_on():
    """SPEC-v0.5 §2.3 — a value, not a handle. The payload crosses a checkpoint into whatever
    console a deployment routes interrupts to, and everything in it is a string, a number or
    `None`."""
    pending = PendingApproval.of(
        _record(
            Action(
                name="stripe.refund",
                arguments={"payment_id": "txn_1", "amount": 2000},
                principal=Principal(agent="refund-agent"),
            )
        )
    )

    payload = pending.to_dict()

    assert set(payload) == {
        "request_id",
        "action_id",
        "action",
        "action_hash",
        "arguments",
        "resource",
        "environment",
        "agent",
        "user",
        "created_at",
        "expires_at",
    }
    assert json.loads(json.dumps(payload)) == payload


def _record(action: Action):
    from datetime import timedelta

    store = InMemoryStateStore()
    provider = InterruptApprovalProvider(store, LangGraphInterrupt(carries_approved_arguments=True))
    request = provider.request(action, timedelta(minutes=15))
    record = store.get_approval(request.request_id)
    assert record is not None
    return record
