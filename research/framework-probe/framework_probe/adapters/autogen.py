"""AutoGen (AgentChat). SPEC-v0.4 §7.3; version and defaults in `../../README.md`.

One `AssistantAgent` with one tool, run once through `run()`. AutoGen's documented behaviour
is a conversational retry — the agent reflects on the failure and tries again — and whether
that reaches the remote a second time is exactly the row.

**Not run in this repository's CI.**
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

from ..scenarios import APPROVAL_MUTATION, Scenario
from ._framework import call_remote, record_approval
from .base import Attempt, approve_endpoint, is_installed, read_version, tool_endpoint

MODEL = os.environ.get("CTRLRUN_PROBE_MODEL", "gpt-4o-mini")


@dataclass
class AutoGenAdapter:
    name: str = "autogen"
    distribution: str = "autogen-agentchat"
    config_deviation: str | None = None

    def available(self) -> bool:
        return is_installed(self.distribution)

    def version(self) -> str:
        return read_version(self.distribution)

    def run(self, scenario: Scenario, url: str) -> Attempt:
        from autogen_agentchat.agents import AssistantAgent
        from autogen_ext.models.openai import OpenAIChatCompletionClient

        if scenario.name == APPROVAL_MUTATION:
            assert scenario.approved is not None
            record_approval(approve_endpoint(url), scenario.approved)

        endpoint = tool_endpoint(url)

        def issue_refund(payment_id: str, amount: int) -> str:
            return call_remote(endpoint, {"payment_id": payment_id, "amount": amount})

        # AutoGen reads the tool's description from its docstring, so the docstring comes
        # from the scenario. One copy of that text, in `scenarios.py` (§7.3 rule 2).
        issue_refund.__doc__ = scenario.tool_description

        agent = AssistantAgent(
            name="support_agent",
            model_client=OpenAIChatCompletionClient(model=MODEL),
            tools=[issue_refund],
            system_message="You handle refunds for an online store.",
        )
        try:
            asyncio.run(agent.run(task=scenario.prompt))
        except Exception as failure:
            return Attempt(error=f"{type(failure).__name__}: {failure}")
        return Attempt()


ADAPTER = AutoGenAdapter()
