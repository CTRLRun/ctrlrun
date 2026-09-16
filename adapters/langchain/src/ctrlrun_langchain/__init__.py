# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""Gate a LangChain agent's tool calls with a ctrlrun policy, through `wrap_tool_call`.

**This is not the LangGraph adapter, and it is not an adapter at all in SPEC-v0.5 §2's sense.**
`ctrlrun-langgraph` exists to route an `APPROVE` through `interrupt()`; it reuses a framework's
human-in-the-loop primitive and contributes two lines. This is the other thing entirely: a
`wrap_tool_call` middleware, where the framework hands over the call itself.

The distinction matters because of what `wrap_tool_call` is. LangChain's own documentation:

    Intercept execution and control when the handler is called. You decide if the handler is
    called zero times (short-circuit), once (normal flow), or multiple times (retry logic).

So `handler` **is** the tool call. That makes it the executor `Control.execute` has always
wanted, and it closes the gap every observation-hook integration has to live with: there is no
separate outcome report to arrive late, be swallowed, or never fire. What the tool did is what
`handler` returned or raised, in the same stack frame, and the receipt says so.

Three consequences worth stating, because they are the reason to use this over a log-and-hope
callback:

- **A denial never reaches the tool.** The handler is not called, and the model gets a
  `ToolMessage` saying the call was refused and why.
- **`once stays once` is real here.** The effect is reserved before `handler` runs and committed
  from its return, so two agents sharing a store cannot both execute the same effect key.
- **An unknown outcome stays unknown.** If `handler` raises something that is not `NotExecuted`,
  the effect is `AMBIGUOUS` and the next attempt is refused until a human resolves it, rather
  than being retried into a double charge.

**You may not need this.** `@protect` already covers any Python callable, including a LangChain
tool, with no middleware and no framework support. This buys one thing: the gate applies to
*every* tool the agent can reach, including tools you did not write and cannot decorate.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Final

from langchain.agents.middleware import AgentMiddleware

from ctrlrun import (
    Action,
    ActionDenied,
    AmbiguousEffect,
    ApprovalRequired,
    Control,
    DuplicateEffect,
)
from ctrlrun.effect import resolve_resource

__all__ = ["CTRLRunMiddleware"]

#: What the model is told when ctrlrun refuses. A refusal is the statement that the tool did
#: not run, which is not the same as the tool failing, so it names the rule rather than
#: reporting an error the tool never produced.
_REFUSED: Final = "ctrlrun refused this call: {reason}. The tool did not run."


def _tool_message(request: Any, content: str) -> Any:
    """The refusal, in the shape LangChain's own limit middleware uses."""
    from langchain_core.messages import ToolMessage

    call = request.tool_call
    return ToolMessage(
        content=content,
        tool_call_id=call["id"],
        name=call.get("name"),
        status="error",
    )


class CTRLRunMiddleware(AgentMiddleware):
    """`AgentMiddleware` that runs every tool call through a `Control`.

    The **operator** constructs it, on the line where the policy, the store and the identity
    provider are chosen. This class never constructs a `Control`: everything it must not
    decide is decided by the person deploying it (SPEC-v0.5 §2.3).

        control = Control(policy, store, identity=..., authority=...)
        agent = create_agent(model, tools=[...], middleware=[CTRLRunMiddleware(control)])

    `resource` and `effect` are templates over the tool's arguments, exactly as `@protect`'s
    are, and the policy's own entries are used where none is given here.
    """

    def __init__(
        self,
        control: Control,
        *,
        resource: str | None = None,
        effect: str | None = None,
        task: str | None = None,
    ) -> None:
        super().__init__()
        self._control = control
        self._resource = resource
        self._effect = effect
        self._task = task

    # -- the hook ---------------------------------------------------------------------

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        """Decide, then run the handler as the executor, then record what it did."""
        call = request.tool_call
        name = call.get("name") or ""
        arguments: Mapping[str, Any] = call.get("args") or {}

        try:
            action = self._action(name, arguments)
        except Exception as exc:  # a policy that cannot name this tool is a refusal
            return _tool_message(request, _REFUSED.format(reason=f"could not be evaluated: {exc}"))

        returned: list[Any] = []

        def executor() -> Any:
            # `handler` is the tool call. Its return value is the outcome, and anything it
            # raises that is not `NotExecuted` leaves the effect AMBIGUOUS, which is the
            # honest state for a call whose result nobody established.
            result = handler(request)
            returned.append(result)
            return result

        try:
            self._control.execute(action, executor, self._effect_key(name, arguments))
        except ActionDenied as denied:
            return _tool_message(request, _REFUSED.format(reason=denied.reason))
        except ApprovalRequired as pending:
            return _tool_message(
                request,
                f"ctrlrun is holding this call for a human. Approve it with "
                f"'ctrlrun approve {pending.request_id}', then ask again. The tool did not run.",
            )
        except DuplicateEffect as duplicate:
            return _tool_message(
                request,
                f"ctrlrun refused this call: this effect is already {duplicate.state} "
                f"({duplicate.effect_key}). The tool did not run.",
            )
        except AmbiguousEffect as ambiguous:
            return _tool_message(
                request,
                f"ctrlrun refused this call: the outcome of {ambiguous.effect_key} was never "
                f"established, so a retry is unsafe. Resolve it with "
                f"'ctrlrun resolve {ambiguous.effect_key}'. The tool did not run.",
            )

        return returned[0] if returned else None

    async def awrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        """Async agents reach the same decision through the same `Control`.

        Deliberately not a parallel implementation. `Control.execute` is synchronous and owns
        the reservation, so a second async path would be a second place the once-only rule is
        enforced, and a second place to get it wrong.
        """
        import anyio

        result: list[Any] = []

        def run() -> None:
            result.append(self.wrap_tool_call(request, lambda r: anyio.from_thread.run(handler, r)))

        await anyio.to_thread.run_sync(run)
        return result[0]

    # -- internals --------------------------------------------------------------------

    def _action(self, name: str, arguments: Mapping[str, Any]) -> Action:
        """The action this tool call proposes.

        The principal comes from `Control.resolve_principal`, never from the agent's state: a
        principal supplied by the caller is not an authorization input (SPEC-v0.3 §4.2).
        """
        principal = self._control.resolve_principal(name)
        template = (
            self._resource
            if self._resource is not None
            else (self._control.policy.resource_template(name))
        )
        return Action(
            name=name,
            arguments=dict(arguments),
            principal=principal,
            resource=None if template is None else resolve_resource(template, arguments),
            environment=self._control.environment,
        )

    def _effect_key(self, name: str, arguments: Mapping[str, Any]) -> str | None:
        template = (
            self._effect
            if self._effect is not None
            else (self._control.policy.effect_template(name))
        )
        return None if template is None else resolve_resource(template, arguments)
