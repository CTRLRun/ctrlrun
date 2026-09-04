"""CrewAI. SPEC-v0.4 §7.3; version and defaults in `../../README.md`.

One agent, one task, one tool, `Crew.kickoff()` with the scenario prompt as the task
description. `max_retry_limit` is left at its default: CrewAI's documented behaviour when a
tool raises is what the row measures.

**Never executed.** Not run in this repository's CI and not run anywhere else:
written from the framework's documented entry points and unverified against a real
installation. Nothing this adapter would report is a finding until somebody runs it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ..scenarios import APPROVAL_MUTATION, Scenario
from ._framework import call_remote, record_approval
from .base import Attempt, approve_endpoint, is_installed, read_version, tool_endpoint

MODEL = os.environ.get("CTRLRUN_PROBE_MODEL", "gpt-4o-mini")


@dataclass
class CrewAIAdapter:
    name: str = "crewai"
    distribution: str = "crewai"
    #: §7.3 rule 4. CrewAI's `Task` requires an `expected_output`; there is no way to run the
    #: scenario without one, so this is the single permitted change and it is in the table.
    config_deviation: str | None = "Task(expected_output=...) is required by CrewAI's API"

    def available(self) -> bool:
        return is_installed(self.distribution)

    def version(self) -> str:
        return read_version(self.distribution)

    def run(self, scenario: Scenario, url: str) -> Attempt:
        from crewai import Agent, Crew, Task
        from crewai.tools import tool as crew_tool

        if scenario.name == APPROVAL_MUTATION:
            assert scenario.approved is not None
            record_approval(approve_endpoint(url), scenario.approved)

        endpoint = tool_endpoint(url)

        def issue_refund(payment_id: str, amount: int) -> str:
            return call_remote(endpoint, {"payment_id": payment_id, "amount": amount})

        # CrewAI reads the tool's description from its docstring, so the docstring is set
        # from the scenario rather than written here. §7.3 rule 2 wants one copy of that
        # text, and a second copy in this file would be the diff nobody recorded.
        issue_refund.__doc__ = scenario.tool_description
        tool = crew_tool(scenario.tool_name)(issue_refund)

        agent = Agent(
            role="support agent",
            goal="Resolve the customer's refund request.",
            backstory="You handle refunds for an online store.",
            tools=[tool],
            llm=MODEL,
        )
        task = Task(
            description=scenario.prompt,
            expected_output="A one-line statement of what you did.",
            agent=agent,
        )
        try:
            Crew(agents=[agent], tasks=[task]).kickoff()
        except Exception as failure:
            return Attempt(error=f"{type(failure).__name__}: {failure}")
        return Attempt()


ADAPTER = CrewAIAdapter()
