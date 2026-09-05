"""AutoGen (AgentChat). SPEC-v0.4 §7.3; version and defaults in `../../README.md`.

One `AssistantAgent` with one tool, run once through `run()`. AutoGen's documented behaviour
is a conversational retry — the agent reflects on the failure and tries again — and whether
that reaches the remote a second time is exactly the row.

**Never run against a model, and never executed at all.** Not run in this repository's
CI and not run anywhere else: written from the framework's documented entry points and
unverified against a real installation, which is a weaker position than the LangGraph and
Agents SDK adapters are in — those two at least reached a model call against a real
install. Nothing this adapter would report is a finding until somebody runs it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ..scenarios import APPROVAL_MUTATION, Scenario
from ._framework import PROBE_MODEL, call_remote, record_approval
from .base import Attempt, approve_endpoint, is_installed, read_version, tool_endpoint

#: One `CTRLRUN_PROBE_MODEL` for every adapter, unprefixed — see `_framework.PROBE_MODEL`.
MODEL = PROBE_MODEL


@dataclass
class AutoGenAdapter:
    name: str = "autogen"
    distribution: str = "autogen-agentchat"
    config_deviation: str | None = None

    #: Every distribution `run()` imports, beyond `distribution` itself (§7.3 rule 5).
    #: `run()` imports `autogen_ext.models.openai`, which is a different distribution.
    #: An adapter whose `available()` did not name them all reports a missing dependency as a
    #: framework that broke.
    requires: tuple[str, ...] = ("autogen-ext",)

    def available(self) -> bool:
        return is_installed(self.distribution, *self.requires)

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
