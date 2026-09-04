"""Route a CTRLRun `APPROVE` through the OpenAI Agents SDK's own tool-approval interruption.
SPEC-v0.5 §2, §3.5.

**An adapter exists for exactly one reason**, and this is the whole of it: when a policy says a
refund needs a human, the SDK stops the run with a `ToolApprovalItem` and the human answers
through `state.approve(...)` / `state.reject(...)` — where this SDK's users already answer —
instead of `ApprovalRequired` being raised past the runner.

This is the **decided-before-invocation** shape (§3.5), and it is the other of the two the
contract covers. The SDK asks whether a tool call needs approval *before* it invokes the tool,
so the adapter answers that question with `ctrlrun.adapter.needs_approval` — which resolves the
principal from the `Control`, builds the Action and evaluates, and **writes nothing**, so a
predicate the SDK may call more than once leaves no events behind.

**You probably do not need this.** `@protect` covers anything in this process with no adapter
and no framework support. This buys the interrupt and nothing else.

Supported kernel range: `ctrlrun>=0.5,<0.6`.
Supported framework range: `openai-agents>=0.20,<1.0`.
`README.md` states both, and states why this adapter's binding is **attribution** where
LangGraph's is prevention.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from ctrlrun import ApprovalAnswer, PendingApproval
from ctrlrun.adapter import needs_approval as _needs_approval
from ctrlrun.errors import CTRLRunError, InvalidArgument

if TYPE_CHECKING:  # pragma: no cover - an adapter constructs no Control (SPEC-v0.5 §2.3)
    from ctrlrun import Control

__all__ = [
    "CHANNEL",
    "AgentsInterrupt",
    "approval_gate",
    "protected_tool",
    "run",
    "run_sync",
    "unwrap",
]

#: What reaches a receipt's `approver`. It names a **channel**, never a person: the SDK records
#: that a tool call was approved and not by whom, and inventing a name would be manufacturing
#: evidence. SPEC-v0.3 §13 keeps authenticating the approver out of scope, and `v0.1`'s
#: `"cli:local"` is the same register.
CHANNEL = "openai-agents:tool-approval"


class AgentsInterrupt:
    """The SDK's tool-approval interruption, seen from inside the tool.

    By the time a protected tool body runs, the SDK has already put the call to a human and been
    told yes: `needs_approval` said the call needed one, the run stopped with a
    `ToolApprovalItem`, somebody called `state.approve(item)`, and the SDK re-ran and invoked the
    tool. So this returns the grant the SDK already holds.

    **`carries_approved_arguments` is `False`, and it is not a setting.** It is a fact about this
    framework: the arguments a human answered against live on the `ToolApprovalItem`, which the
    *caller* holds in `RunResult.interruptions` and which is not reachable from a tool body — the
    run context records that a call was approved, keyed by tool name and `call_id`, and not what
    its arguments were. An adapter that handed back the tool's own parameters would be handing
    back what it was given, which SPEC-v0.5 §3.4 names as manufacturing the check.

    So the binding across this interrupt is **attribution**, and `README.md` says so in that
    word. What closes the gap in practice is the SDK's own binding rather than CTRLRun's: the
    approval item and the invocation are the *same tool call*, bound by `call_id`, and the SDK
    invokes with exactly that call's arguments. That is a real property of the framework and it
    is not one CTRLRun can verify, which is the whole distinction §3.4 draws.
    """

    framework = "openai-agents"
    #: A fact about the framework, not a choice. See the class docstring and `README.md`.
    carries_approved_arguments = False

    def interrupt(self, pending: PendingApproval) -> ApprovalAnswer:
        """Return the answer the SDK already has for this call.

        There is no call out to the framework here, and that is this shape: the SDK asked before
        it invoked, so by the time the tool body reaches `Control.execute` the human has already
        answered yes. A tool body that runs *is* the approval, and the adapter's job is to say so
        rather than to ask again — asking again would be a second approval path.

        A **rejection** never reaches here at all: the SDK does not invoke a tool whose approval
        was refused, so the run ends with the rejection in its own output and no CTRLRun action
        is ever proposed. §7 of `README.md` records that, because it is the one place this
        adapter's evidence differs from `@protect`'s.
        """
        return ApprovalAnswer(granted=True, approver=CHANNEL)


def protected_tool(
    control: Control,
    action: str,
    function: Callable[..., Any],
    *,
    resource: str | None = None,
    **options: Any,
) -> Any:
    """Build a `function_tool` that is gated by `control` and whose refusals reach the caller.

    Two things, and the second is the one this helper exists for.

    **`needs_approval=approval_gate(...)`**, so the SDK asks before it invokes.

    **`failure_error_function=None`**, so a CTRLRun refusal propagates out of `Runner.run`
    instead of being turned into text. This SDK's default is `default_tool_error_function`,
    which catches a tool's exception and returns *"An error occurred while running the tool.
    Please try again."* to the **model**. Under that default an `ActionDenied`, a
    `DuplicateEffect` or an `AmbiguousEffect` reaches an agent as a suggestion to retry — which
    is the exact failure `v0.2 §6.10` argues about in the gateway: a refusal by CTRLRun is not
    an outcome of the tool, it is the statement that the tool did not run, and putting it in a
    channel whose contents reach the model as text invites the retry the refusal exists to
    prevent.

    An adapter that left the default in place cannot pass the conformance kit, and should not:
    every `kernel` case asserts an exception the caller can see. `SPEC-v0.5.md` §12.6 records
    this as a difference between the two reference adapters that the contract had not
    anticipated — LangGraph propagates a tool's exception and this SDK does not.

    Pass `failure_error_function=` yourself if you have a reason to; you are then responsible
    for re-raising `ctrlrun.CTRLRunError`, and `README.md` says so.
    """
    from agents import function_tool

    options.setdefault("failure_error_function", None)
    options.setdefault("needs_approval", approval_gate(control, action, resource=resource))
    return function_tool(function, **options)


def approval_gate(
    control: Control,
    action: str,
    *,
    resource: str | None = None,
) -> Callable[[Any, Mapping[str, Any], str], Any]:
    """The `needs_approval=` callable for a `@function_tool` (SPEC-v0.5 §3.5).

    ``function_tool(needs_approval=approval_gate(control, "stripe.refund"))``

    It answers the SDK's pre-invocation question with `ctrlrun.adapter.needs_approval` and
    nothing else: `True` where the combined `v0.3 §4.6` decision is `APPROVE`, `False` otherwise.

    A `DENY` returns `False` **on purpose**, so the SDK invokes the tool and `Control.execute`
    denies it with a receipt, an `ACTION_DENIED` and the exception the caller catches. Refusing
    inside the predicate would refuse without evidence, and `v0.3 §4.3` is explicit that a denial
    with a principal to attribute it to belongs in the evidence log.

    The predicate is core's, not this adapter's, because writing it here would have meant
    building an `Action` — and `Action.principal` has no default, so the principal would have
    come from the SDK's session. That is `--principal-from-client-info` (`v0.3 §8.1`), and it is
    the hole SPEC-v0.5 §4.2 exists to close.

    **The predicate and `@protect` can disagree**, and §3.5 says why and why it is safe: the
    decorator applies the function's defaults and reads its own `resource=` template, and this
    sees neither. A wrong `False` means the SDK does not pre-ask and `Control.execute` raises
    `ApprovalRequired`, which the SDK surfaces as the tool's own failure; a wrong `True` asks a
    human about something harmless. In neither direction does an action execute that would not
    have. Pass the same `resource=` template the decorator has, and give the tool no defaulted
    parameters, and they agree.
    """
    if not action:
        raise InvalidArgument("approval_gate(action=...) must be a non-empty action name")

    async def gate(context: Any, params: Mapping[str, Any], call_id: str) -> bool:
        return _needs_approval(control, action, dict(params), resource=resource)

    return gate


def unwrap(error: BaseException) -> BaseException:
    """The `CTRLRunError` behind an SDK wrapper, or the error unchanged.

    This SDK wraps whatever a tool raises in `agents.exceptions.UserError` -- *"Error running
    tool run_it: ..."* -- and chains the original as `__cause__`. So an operator's
    `except DuplicateEffect` does not fire, and `except ActionDenied` does not fire, and the
    one interface this library has for saying *the tool did not run* is lost in transit.

    `v0.1 §8` prefers explicit exceptions over return codes for exactly this reason, and
    `v0.2 §6.10` argues the same point about the gateway: a refusal by CTRLRun is not an outcome
    of the tool, it is the statement that the tool did not run, and it must be distinguishable.
    So this walks the chain and gives it back.

    It changes no decision and grants nothing: it re-raises what already happened, in the type
    the kernel raised it as.
    """
    seen: BaseException | None = error
    while seen is not None:
        if isinstance(seen, CTRLRunError):
            return seen
        seen = seen.__cause__ or seen.__context__

    # No `CTRLRunError` in the chain, so this is the other half of `v0.1 §5.5`: an executor that
    # raised something of its own -- a `TimeoutError`, a connection error -- which the kernel
    # recorded as `AMBIGUOUS` and then re-raised unchanged, because the caller has to see what
    # actually happened. The SDK wrapped that too. A wrapper with a cause is one to unwrap; a
    # `UserError` the SDK raised about its own configuration has none, and is left alone.
    from agents.exceptions import UserError

    if isinstance(error, UserError) and error.__cause__ is not None:
        return error.__cause__
    return error


async def run(agent: Any, input: Any, **options: Any) -> Any:
    """`Runner.run`, with CTRLRun's exceptions arriving as themselves (see `unwrap`).

    Use it wherever you would use `Runner.run` and want `except DuplicateEffect` to work. It is
    a thin pass-through: it decides nothing, holds nothing and adds no behaviour of its own.
    """
    from agents import Runner

    try:
        return await Runner.run(agent, input, **options)
    except BaseException as raised:
        recovered = unwrap(raised)
        if recovered is not raised:
            raise recovered from raised
        raise


def run_sync(agent: Any, input: Any, **options: Any) -> Any:
    """`Runner.run_sync`, with the same unwrapping."""
    from agents import Runner

    try:
        return Runner.run_sync(agent, input, **options)
    except BaseException as raised:
        recovered = unwrap(raised)
        if recovered is not raised:
            raise recovered from raised
        raise
