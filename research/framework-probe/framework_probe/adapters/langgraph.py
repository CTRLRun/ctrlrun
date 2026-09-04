"""LangGraph. SPEC-v0.4 §7.3; version and defaults in `../../README.md`.

A prebuilt ReAct agent with one tool, run once on the scenario prompt. No retry policy is
attached to the tool node and no `RetryPolicy` is passed to the graph: §7.3 rule 3 says
framework defaults, and what LangGraph does with a tool that raised is precisely the finding.

**Never executed.** Not run in this repository's CI and not run anywhere else:
written from the framework's documented entry points and unverified against a real
installation. Nothing this adapter would report is a finding until somebody runs it,
and running it needs `langgraph`, a chat model and a key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ..scenarios import APPROVAL_MUTATION, Scenario
from ._framework import call_remote, record_approval
from .base import Attempt, approve_endpoint, is_installed, read_version, tool_endpoint

#: The one model every framework adapter drives, so the table compares frameworks and not
#: models. Overridable by environment because a maintainer running this owns that choice.
MODEL = os.environ.get("CTRLRUN_PROBE_MODEL", "openai:gpt-4o-mini")


@dataclass
class LangGraphAdapter:
    name: str = "langgraph"
    distribution: str = "langgraph"
    config_deviation: str | None = None

    def available(self) -> bool:
        return is_installed(self.distribution)

    def version(self) -> str:
        return read_version(self.distribution)

    def run(self, scenario: Scenario, url: str) -> Attempt:
        from langchain_core.tools import StructuredTool
        from langgraph.prebuilt import create_react_agent

        if scenario.name == APPROVAL_MUTATION:
            assert scenario.approved is not None
            record_approval(approve_endpoint(url), scenario.approved)

        endpoint = tool_endpoint(url)

        def issue_refund(payment_id: str, amount: int) -> str:
            return call_remote(endpoint, {"payment_id": payment_id, "amount": amount})

        tool = StructuredTool.from_function(
            func=issue_refund,
            name=scenario.tool_name,
            description=scenario.tool_description,
        )
        agent = create_react_agent(MODEL, [tool])
        try:
            agent.invoke({"messages": [{"role": "user", "content": scenario.prompt}]})
        except Exception as failure:
            return Attempt(error=f"{type(failure).__name__}: {failure}")
        return Attempt()


ADAPTER = LangGraphAdapter()
