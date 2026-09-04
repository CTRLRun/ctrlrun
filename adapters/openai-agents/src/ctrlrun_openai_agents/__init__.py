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

import dataclasses
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from ctrlrun import ApprovalAnswer, PendingApproval
from ctrlrun.adapter import banner
from ctrlrun.adapter import needs_approval as _needs_approval
from ctrlrun.errors import CTRLRunError, InvalidArgument

if TYPE_CHECKING:  # pragma: no cover - an adapter constructs no Control (SPEC-v0.5 §2.3)
    from agents.tool_context import ToolContext

    from ctrlrun import Control

__all__ = [
    "CHANNEL",
    "AgentsInterrupt",
    "ApprovalNotAsked",
    "approval_gate",
    "protected_tool",
    "run",
    "run_sync",
    "unwrap",
]


class ApprovalNotAsked(RuntimeError):
    """`interrupt()` was reached for a call the SDK never put to a human.

    Not a denial: nobody said no, and nobody said yes. SPEC-v0.5 §10's first row is the
    behaviour -- `interrupt()` raises, nothing is written, no grant and no denial, and the
    request is left `pending` so `ctrlrun approve` can still answer it out of band.

    It is raised on the two paths where this adapter cannot see an answer:

    * The call reached `Control.execute` without going through `protected_tool`, so no SDK tool
      call is in scope at all -- a plain `function_tool`, a background job, or any other
      `@protect(wait=True)` on the same `Control`. The provider hangs off the `Control` and not
      off the tool, so nothing else links the two.
    * The SDK's own gate never asked about this call, which `is_tool_approved` reports as
      `None`. That happens whenever `needs_approval` and the kernel disagree -- the predicate
      sees the arguments the model sent, `Control.execute` sees the arguments the function was
      called with after Python applied its defaults (§3.5).
    """


#: The SDK tool call currently being invoked through `protected_tool`, or `None`.
#:
#: `FunctionTool.on_invoke_tool` receives the `ToolContext` -- which carries the tool name, the
#: `call_id` and the SDK's own record of what the human answered -- and a tool *body* does not.
#: `protected_tool` wraps the former and binds it here for the duration of the call, so
#: `interrupt()` can ask the SDK rather than assume it.
#:
#: A `ContextVar` rather than a module global: `Runner` runs tool calls concurrently, and a
#: global would let one call read another's answer. Bound and reset around each invocation, so
#: it is empty everywhere else -- which is what makes the "no SDK call in scope" path detectable
#: rather than silently stale.
_CURRENT_CALL: ContextVar[ToolContext | None] = ContextVar(
    "ctrlrun_openai_agents_current_call", default=None
)


@contextmanager
def _bound_call(context: ToolContext) -> Iterator[None]:
    token = _CURRENT_CALL.set(context)
    try:
        yield
    finally:
        _CURRENT_CALL.reset(token)


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
        """Return the answer **the SDK holds** for this call, and never one of this adapter's.

        There is no call out to the framework here, and that is this shape: the SDK asked before
        it invoked, so the answer already exists by the time a tool body reaches
        `Control.execute`. The adapter's job is to *read* it — asking again would be a second
        approval path.

        Reading it is the whole of the job, and this method used to skip it. It returned
        `granted=True` unconditionally, on the premise that *a tool body that runs is the
        approval*. That premise holds only when the SDK's gate actually asked and was told yes,
        and there are reachable paths where it did not:

        * A `@protect(wait=True)` call on the same `Control` that never went through
          `protected_tool`. The provider hangs off the `Control`, not off the tool.
        * A call the SDK's gate passed without asking, because `needs_approval` and the kernel
          saw different arguments — the predicate sees what the model sent, `Control.execute`
          sees what the function was called with after Python applied its defaults. §3.5 says
          that divergence is harmless *because the human is asked anyway*; a self-granting
          `interrupt()` is what made it unsafe, and the fix belongs here rather than in §3.5.

        On both, an `APPROVE` executed with no human and wrote a receipt naming an approver
        nobody was — a grant fabricated into the evidence log, which is what §2.3's ban on
        `StateStore.append_event` exists to prevent, reached through the sanctioned door.

        `RunContextWrapper.is_tool_approved(tool_name, call_id)` is the SDK's own record and has
        exactly the three states this needs: `True` (a human approved), `False` (a human
        refused) and `None` (this call was never put to anyone). Only the first two are answers.

        A **rejection** normally never reaches here: the SDK does not invoke a tool whose
        approval was refused, so the run ends with the rejection in its own output and no
        CTRLRun action is proposed. §7 of `README.md` records that, because it is the one place
        this adapter's evidence differs from `@protect`'s. `False` is still returned as a
        denial rather than assumed unreachable — a human's *no* is an answer, and §2.4 says it
        is recorded by the provider like any other.
        """
        context = _CURRENT_CALL.get()
        if context is None:
            raise ApprovalNotAsked(
                f"{pending.action} reached the approval interrupt outside a tool call driven by "
                "protected_tool, so the OpenAI Agents SDK was never asked and holds no answer. "
                "Nothing was granted and the request is still pending: answer it with "
                "`ctrlrun approve`, or drive the call through protected_tool()."
            )

        answered = context.is_tool_approved(context.tool_name, context.tool_call_id)
        if answered is None:
            raise ApprovalNotAsked(
                f"{pending.action} needs approval, but the OpenAI Agents SDK never gated this "
                f"call ({context.tool_name}/{context.tool_call_id}) and so holds no answer. "
                "The needs_approval predicate and Control.execute disagreed about the "
                "arguments (SPEC-v0.5 §3.5). Nothing was granted and the request is still "
                "pending: answer it with `ctrlrun approve`."
            )

        return ApprovalAnswer(granted=answered, approver=CHANNEL)


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

    if "needs_approval" in options:
        # `setdefault` here let a caller replace the policy gate with `lambda *a: False` -- one
        # keyword that turns every APPROVE into an ungated call. SPEC-v0.5 §3.8: an adapter has
        # no flag that relaxes a check, and a keyword that silently wins over the policy is one.
        raise InvalidArgument(
            "protected_tool() sets needs_approval= from the policy and will not take one: a "
            "predicate that overrode it would be an approval gate the policy does not control. "
            "Build the tool with agents.function_tool() yourself if that is what you want."
        )
    options.setdefault("failure_error_function", None)
    options["needs_approval"] = approval_gate(control, action, resource=resource)
    tool = function_tool(function, **options)

    # Bind the SDK's own record of this call so `AgentsInterrupt.interrupt()` can read the
    # answer instead of assuming one. `on_invoke_tool` is where the `ToolContext` -- tool name,
    # `call_id`, and what the human answered -- is in scope; a tool *body* never sees it. This
    # wraps that one call and touches neither the schema the model is shown nor the body.
    invoke = tool.on_invoke_tool

    async def _invoke_bound(context: ToolContext, arguments: str) -> Any:
        with _bound_call(context):
            return await invoke(context, arguments)

    return dataclasses.replace(tool, on_invoke_tool=_invoke_bound)


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

    **The predicate and `@protect` can disagree**, and §3.5 says why: the decorator applies the
    function's defaults and reads its own `resource=` template, and this sees neither. A wrong
    `True` asks a human about something harmless. A wrong `False` means the SDK does not
    pre-ask, `Control.execute` raises `ApprovalRequired`, and `AgentsInterrupt.interrupt()`
    finds the SDK holds no answer for a call it never gated -- so the action is **refused** with
    `ApprovalNotAsked`, nothing is written, and the request is left `pending`.

    In neither direction does an action execute that a human did not approve. That sentence was
    not true while `interrupt()` granted unconditionally: a wrong `False` executed. It is the
    interrupt's reading of `is_tool_approved` that makes it true, not this predicate.

    Pass the same `resource=` template the decorator has, and give the tool no defaulted
    parameters, and the two agree and no call is refused this way.
    """
    if not action:
        raise InvalidArgument("approval_gate(action=...) must be a non-empty action name")

    # SPEC-v0.5 §3.6: logged once per `Control`, never printed, a no-op under `mode: enforce`.
    # Here rather than in `interrupt()` because observe mode never raises `ApprovalRequired`, so
    # the interrupt is exactly the place that is never reached in the mode the banner is for.
    # This is the adapter's attach point -- the first place it is handed the operator's Control.
    banner(control)

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

    **`__cause__` only, never `__context__`.** `__cause__` is explicit chaining -- `raise X from
    Y` -- and is the only link that asserts *this error is behind that one*. `__context__` is
    what the interpreter sets whenever any exception is raised while another is being handled,
    and it asserts nothing about causation.

    Following it here was a hole rather than a nicety. `@protect(wait=True)` runs its approved
    leg **inside** `except ApprovalRequired as pending:`, so every exception on that leg carries
    `__context__ = ApprovalRequired`. An executor that raised a `TimeoutError` after the request
    went out -- which the kernel records as AMBIGUOUS and re-raises unchanged, `v0.1 §5.5` --
    was walked back to that `ApprovalRequired` and handed to the caller as *"requires approval,
    then retry"*. An ambiguous outcome reported as a definite did-not-run, with instructions to
    do it again, on the one path this adapter exists for.
    """
    seen: BaseException | None = error
    while seen is not None:
        if isinstance(seen, CTRLRunError):
            return seen
        seen = seen.__cause__

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
