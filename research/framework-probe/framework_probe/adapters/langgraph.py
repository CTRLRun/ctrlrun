"""LangGraph. SPEC-v0.4 §7.3; version and defaults in `../../README.md`.

A prebuilt agent with one tool, run once on the scenario prompt. No retry policy is attached
to the tool node and no `RetryPolicy` is passed to the graph: §7.3 rule 3 says framework
defaults, and what LangGraph does with a tool that raised is precisely the finding.

The entry point stays `langgraph.prebuilt.create_react_agent`, and running this adapter is what
settled that rather than settling the opposite. On langgraph 1.2.11 the prebuilt emits
`LangGraphDeprecatedSinceV10` — a **warning**, not an exception — naming
`langchain.agents.create_agent` as its replacement and V2.0 as its removal, and it then
constructs and runs exactly as before. So the framework **can** run the scenario without a
change, which is the condition §7.3 rule 4 attaches to permitting one: the change would be
elective, and an elective change of the code path under measurement is what rule 4 exists to
surface.

Two reasons beyond the letter of the rule. `create_react_agent` is LangGraph's own
implementation, while `create_agent` lives in `langchain/agents/factory.py` — a different
distribution — so a row labelled `langgraph`, carrying a version read from `langgraph`, would be
reporting langchain's agent loop and its middleware defaults. And the deprecation is itself a
finding: it belongs in the README's documentation table under §7.3 rule 7, where a reader can
see it, rather than in an adapter that quietly moved.

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

    #: Every distribution `run()` imports, beyond `distribution` itself (§7.3 rule 5).
    #: `create_react_agent` resolves its model string through langchain's
    #: `init_chat_model`, which imports the provider package lazily.
    #: An adapter whose `available()` did not name them all reports a missing dependency as a
    #: framework that broke.
    requires: tuple[str, ...] = ("langchain", "langchain-core", "langchain-openai")

    def available(self) -> bool:
        return is_installed(self.distribution, *self.requires)

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
