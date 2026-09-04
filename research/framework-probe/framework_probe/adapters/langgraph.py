"""LangGraph. SPEC-v0.4 §7.3; version and defaults in `../../README.md`.

A prebuilt agent with one tool, run once on the scenario prompt. No retry policy is attached
to the tool node and no `RetryPolicy` is passed to the graph: §7.3 rule 3 says framework
defaults, and what LangGraph does with a tool that raised is precisely the finding.

The entry point is `langchain.agents.create_agent`, not `langgraph.prebuilt.create_react_agent`.
Running this adapter is what settled that: on langgraph 1.2.11 the prebuilt raises
`LangGraphDeprecatedSinceV10` naming its replacement and saying it goes in V2.0. Measuring an
entry point the framework is retiring would report a path no reader would write today, and the
row would still say "langgraph" — so the adapter follows the framework's own instruction, which
is what §7.3 rule 3 means by defaults.

**Never run against a model.** Not run in this repository's CI and not run against a
chat model anywhere else. On 2026-09-04 it was executed against a real installation —
`langgraph` 1.2.11 with `langchain` 1.4.0 — and reached the model call, so its documented entry
points are known to resolve; no scenario completed and no effect reached the remote.
Nothing this adapter would report is a finding until somebody runs it with a key.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..scenarios import APPROVAL_MUTATION, Scenario
from ._framework import PROBE_MODEL, call_remote, record_approval
from .base import Attempt, approve_endpoint, is_installed, read_version, tool_endpoint

#: The one model every framework adapter drives, so the table compares frameworks and not
#: models. Overridable by environment because a maintainer running this owns that choice.
#:
#: `PROBE_MODEL` is shared and unprefixed. It said `openai:gpt-4o-mini` here and `gpt-4o-mini`
#: in the Agents SDK adapter until the two were run side by side: one `CTRLRUN_PROBE_MODEL`
#: cannot be both, so setting it broke whichever adapter did not match its own default, and
#: §7.3 rule 2 asks for the same text everywhere the framework's API admits it.
#: `init_chat_model` resolves a bare `gpt-*` to `ChatOpenAI`, which was checked against
#: langchain 1.4.0 rather than assumed.
MODEL = PROBE_MODEL


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
        from langchain.agents import create_agent
        from langchain_core.tools import StructuredTool

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
        agent = create_agent(MODEL, [tool])
        try:
            agent.invoke({"messages": [{"role": "user", "content": scenario.prompt}]})
        except Exception as failure:
            return Attempt(error=f"{type(failure).__name__}: {failure}")
        return Attempt()


ADAPTER = LangGraphAdapter()
