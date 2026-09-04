"""OpenAI Agents SDK. SPEC-v0.4 §7.3; version and defaults in `../../README.md`.

One `Agent` with one `function_tool`, run once through `Runner.run_sync`. No
`failure_error_function` is supplied, so the SDK's default handling of a tool that raised is
what the row measures.

**Never run against a model.** Not run in this repository's CI and not run against a
chat model anywhere else. On 2026-09-04 it was executed against a real installation —
`openai-agents` 0.22.0 — and reached the model call, so its documented entry
points are known to resolve; no scenario completed and no effect reached the remote.
Nothing this adapter would report is a finding until somebody runs it with a key.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..scenarios import APPROVAL_MUTATION, Scenario
from ._framework import PROBE_MODEL, call_remote, record_approval
from .base import Attempt, approve_endpoint, is_installed, read_version, tool_endpoint

#: One `CTRLRUN_PROBE_MODEL` for every adapter, unprefixed — see `_framework.PROBE_MODEL`.
MODEL = PROBE_MODEL


@dataclass
class OpenAIAgentsAdapter:
    name: str = "openai-agents"
    distribution: str = "openai-agents"
    config_deviation: str | None = None

    #: Every distribution `run()` imports, beyond `distribution` itself (§7.3 rule 5).
    #: `run()` imports nothing outside `agents`.
    #: An adapter whose `available()` did not name them all reports a missing dependency as a
    #: framework that broke.
    requires: tuple[str, ...] = ()

    def available(self) -> bool:
        return is_installed(self.distribution, *self.requires)

    def version(self) -> str:
        return read_version(self.distribution)

    def run(self, scenario: Scenario, url: str) -> Attempt:
        from agents import Agent, Runner, function_tool

        if scenario.name == APPROVAL_MUTATION:
            assert scenario.approved is not None
            record_approval(approve_endpoint(url), scenario.approved)

        endpoint = tool_endpoint(url)

        def issue_refund(payment_id: str, amount: int) -> str:
            return call_remote(endpoint, {"payment_id": payment_id, "amount": amount})

        # The SDK reads the tool's description from its docstring, so the docstring comes
        # from the scenario. One copy of that text, in `scenarios.py` (§7.3 rule 2).
        issue_refund.__doc__ = scenario.tool_description
        tool = function_tool(issue_refund, name_override=scenario.tool_name)

        agent = Agent(
            name="support agent",
            instructions="You handle refunds for an online store.",
            tools=[tool],
            model=MODEL,
        )
        try:
            Runner.run_sync(agent, scenario.prompt)
        except Exception as failure:
            return Attempt(error=f"{type(failure).__name__}: {failure}")
        return Attempt()


ADAPTER = OpenAIAgentsAdapter()
