# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""The LangChain middleware adapter. SPEC-v0.5 §6, §7.

Every test here drives `wrap_tool_call` with a handler that **records whether it ran**, because
that is the whole claim: a refusal is the statement that the tool did not run, and a test that
only asserted the returned message would pass just as well against a middleware that refused
*and then ran it anyway*.

Skipped **by name** where `langchain` is not installed, so a green run with the framework
missing cannot look like a pass (`v0.4 §7` T123's rule, applied here).
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

import ctrlrun
from ctrlrun import Control
from ctrlrun.policy import Policy
from ctrlrun.state import InMemoryStateStore

pytest.importorskip("langchain", reason="langchain is not installed")
pytest.importorskip("ctrlrun_langchain", reason="ctrlrun-langchain is not installed")

from ctrlrun_langchain import CTRLRunMiddleware

ADAPTER = Path(__file__).resolve().parents[1] / "adapters" / "langchain"

POLICY = """
schema: ctrlrun.policy/v2
actions:
  lookup:
    decision: allow
  refund:
    effect: "refund:{payment_id}"
    rules:
      - when: { amount_gte: 0, amount_lte: 5000 }
        decision: allow
      - decision: deny
  escalate:
    decision: approve
"""


class Request:
    """The two fields of `ToolCallRequest` this middleware reads."""

    def __init__(self, name: str, args: dict[str, Any]) -> None:
        self.tool_call = {"id": "call_1", "name": name, "args": args}


@pytest.fixture
def control(tmp_path) -> Control:
    source = tmp_path / "ctrlrun.yaml"
    source.write_text(POLICY, encoding="utf-8")
    return Control(Policy.from_file(source), InMemoryStateStore())


@pytest.fixture
def ran() -> list[str]:
    return []


@pytest.fixture
def handler(ran):
    def _handler(request: Any) -> str:
        ran.append(request.tool_call["name"])
        return "TOOL-RAN"

    return _handler


def _content(result: Any) -> str:
    return getattr(result, "content", str(result))


# --- the decision reaches the tool, or does not ----------------------------------------------


def test_an_allowed_call_runs_and_returns_what_the_handler_returned(control, handler, ran):
    with ctrlrun.context(agent="langchain-agent"):
        assert (
            CTRLRunMiddleware(control).wrap_tool_call(Request("lookup", {}), handler) == "TOOL-RAN"
        )
    assert ran == ["lookup"]


def test_a_denied_call_never_reaches_the_handler(control, handler, ran):
    """The claim is non-execution, so the assertion is on `ran`, not on the message."""
    with ctrlrun.context(agent="langchain-agent"):
        result = CTRLRunMiddleware(control).wrap_tool_call(
            Request("refund", {"payment_id": "p1", "amount": 900_000}), handler
        )
    assert ran == []
    assert "did not run" in _content(result)


def test_an_unnamed_tool_is_refused_because_nothing_is_default_allow(control, handler, ran):
    with ctrlrun.context(agent="langchain-agent"):
        result = CTRLRunMiddleware(control).wrap_tool_call(Request("rm_rf", {}), handler)
    assert ran == []
    assert "unknown_action" in _content(result)


def test_the_refusal_names_the_rule_rather_than_reporting_an_error_the_tool_never_produced(
    control, handler
):
    with ctrlrun.context(agent="langchain-agent"):
        result = CTRLRunMiddleware(control).wrap_tool_call(
            Request("refund", {"payment_id": "p1", "amount": 900_000}), handler
        )
    assert "rule[" in _content(result)
    assert getattr(result, "status", None) == "error"


# --- once stays once -------------------------------------------------------------------------


def test_the_same_effect_key_does_not_run_twice(control, handler, ran):
    middleware = CTRLRunMiddleware(control)
    call = Request("refund", {"payment_id": "p2", "amount": 1000})
    with ctrlrun.context(agent="langchain-agent"):
        assert middleware.wrap_tool_call(call, handler) == "TOOL-RAN"
        second = middleware.wrap_tool_call(
            Request("refund", {"payment_id": "p2", "amount": 1000}), handler
        )
    assert ran == ["refund"], "the second attempt reached the tool"
    assert "already committed" in _content(second)


# --- an approval is a refusal here, and says how to answer it --------------------------------


def test_an_approve_decision_refuses_and_names_the_request(control, handler, ran):
    """This middleware has no interrupt to route through, so it refuses and hands back the id.

    `ctrlrun-langgraph` is the adapter for answering inside the run; the README says so, and
    this test is what keeps the two distinguishable rather than half-implementing an interrupt.
    """
    with ctrlrun.context(agent="langchain-agent"):
        result = CTRLRunMiddleware(control).wrap_tool_call(Request("escalate", {}), handler)
    assert ran == []
    assert "ctrlrun approve" in _content(result)


# --- what a failing tool does, which is not what a refused one does --------------------------


def test_a_tool_that_raises_leaves_the_outcome_unknown_rather_than_failed(control, ran):
    """Anything that is not `NotExecuted` is AMBIGUOUS (v0.1 §5.5), so the retry is refused.

    This is the property the observation-hook integrations cannot offer, and the reason the
    handler is run as the executor rather than reported on afterwards.
    """
    middleware = CTRLRunMiddleware(control)

    def explodes(request: Any) -> str:
        ran.append("tried")
        raise RuntimeError("the provider timed out")

    with ctrlrun.context(agent="langchain-agent"):
        with pytest.raises(RuntimeError):
            middleware.wrap_tool_call(
                Request("refund", {"payment_id": "p3", "amount": 1000}), explodes
            )
        retry = middleware.wrap_tool_call(
            Request("refund", {"payment_id": "p3", "amount": 1000}), explodes
        )

    assert ran == ["tried"], "the retry reached the tool while the outcome was unknown"
    assert "never established" in _content(retry)
    assert "ctrlrun resolve" in _content(retry)


# --- the principal is read, never supplied ---------------------------------------------------


def test_without_a_principal_the_call_is_refused_before_the_policy_is_consulted(
    control, handler, ran
):
    """SPEC-v0.3 §4.2. A principal taken from agent state is the one input authority may not
    accept, so the absence of one is a refusal rather than a default."""
    result = CTRLRunMiddleware(control).wrap_tool_call(Request("lookup", {}), handler)
    assert ran == []
    assert "no principal is available" in _content(result)


# --- §7's README requirements, and §6.3's two ranges -----------------------------------------


def _needs_the_source_tree() -> None:
    if not ADAPTER.is_dir():  # pragma: no cover - running from an unpacked sdist
        pytest.skip("adapters/ is not in this distribution, which SPEC-v0.5 §6.1 requires")


def readme() -> str:
    _needs_the_source_tree()
    return (ADAPTER / "README.md").read_text(encoding="utf-8")


def declared() -> dict[str, str]:
    _needs_the_source_tree()
    with (ADAPTER / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]
    return {name.split(">")[0].split("<")[0].strip(): name for name in project["dependencies"]}


def test_the_readme_and_the_metadata_state_the_same_two_ranges():
    """SPEC-v0.5 §6.3. A README that says one thing and metadata another is the version
    somebody typed."""
    text = readme()
    for specifier in declared().values():
        assert specifier in text, f"{specifier!r} is in pyproject.toml and not in the README"
    assert "Supported kernel range" in text
    assert "Supported framework range" in text


def test_the_declared_framework_range_contains_the_version_ci_installed():
    from importlib.metadata import version as installed

    from packaging.specifiers import SpecifierSet

    specifier = declared()["langchain"].removeprefix("langchain")
    assert installed("langchain") in SpecifierSet(specifier), (
        f"CI ran against langchain {installed('langchain')}, which the declared range "
        f"{specifier!r} excludes"
    )


def test_the_readme_names_the_primitive_it_reuses_and_where_it_is_documented():
    """§7 item 2: the name, a link and a date, so a reader can check what was true when it
    was written."""
    text = readme()
    assert "wrap_tool_call" in text
    # The whole URL, not the host. A bare hostname reads as a URL-sanitization check to
    # CodeQL (py/incomplete-url-substring-sanitization) and is the weaker assertion anyway:
    # what §7 asks for is the page, so that is what this pins.
    assert "https://docs.langchain.com/oss/langchain/middleware/custom" in text
    assert "Read 2026-09-16" in text


def test_the_readme_says_you_may_not_need_it():
    """§7. `@protect` covers any callable; an adapter that did not say so would be selling
    itself over the simpler thing that already works."""
    assert "You may not need this" in readme()
