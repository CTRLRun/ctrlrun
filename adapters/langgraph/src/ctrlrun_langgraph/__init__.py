"""Route a CTRLRun `APPROVE` through LangGraph's own `interrupt()`. SPEC-v0.5 §2, §3.

**An adapter exists for exactly one reason**, and this is the whole of it: when a policy says a
refund needs a human, the human answers *where LangGraph users already answer* — through
`interrupt()` and `Command(resume=...)`, in whatever queue or console the deployment already
routes those to — instead of `ApprovalRequired` being raised past the graph.

Everything else is the kernel's, unchanged. The policy, the authority evaluation, the exact
binding, the reservation, the receipt: all of it is `Control` doing what it does under
`@protect`, because this module reserves nothing, commits nothing, grants nothing and
constructs no `Control`. What it contributes is the two lines in `interrupt()` below.

**You probably do not need this.** `@protect` already covers anything running in this process,
including a LangChain tool and a raw model call, with no adapter and no framework support. This
buys one thing over it: the interrupt. If your graph has nowhere for a human to answer, or you
are happy for `ApprovalRequired` to reach your own code, use `@protect` and stop here.

Supported kernel range: `ctrlrun>=0.5,<0.6`. Supported framework range: `langgraph>=1.0,<2.0`.
`README.md` states both, and what this adapter's binding check is and is not.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ctrlrun import ApprovalAnswer, PendingApproval
from ctrlrun.errors import InvalidArgument

__all__ = ["RESUME_SHAPE", "LangGraphInterrupt"]

#: The name that reaches a receipt's `approver` when the resume value does not carry one. It
#: names a **channel** and never a person -- SPEC-v0.3 §13 keeps authenticating the approver out
#: of scope, and `v0.1`'s `"cli:local"` is the same register. Do not read it as who approved.
CHANNEL = "langgraph:interrupt"

RESUME_SHAPE = """\
Command(resume=True)                       # granted, approver "langgraph:interrupt"
Command(resume=False)                      # refused
Command(resume={"approved": True,
                "approver": "ada@example.com",
                "arguments": {...}})       # the arguments the human answered against\
"""


class LangGraphInterrupt:
    """LangGraph's `interrupt()`, and nothing else (SPEC-v0.5 §2.1).

    The operator wires it, on the line where they choose the policy and the store::

        control = Control(
            policy, store,
            approvals=InterruptApprovalProvider(
                store, LangGraphInterrupt(carries_approved_arguments=True)
            ),
            identity=..., authority=...,
        )

        @protect("stripe.refund", effect="refund:{payment_id}", wait=True, control=control)
        def issue_refund(payment_id: str, amount: int) -> str: ...

    and then calls `issue_refund` from a graph node, on a graph compiled with a checkpointer.
    `wait=True` is what routes the `APPROVE` here instead of raising past the graph.

    **This adapter never constructs the `Control`** (SPEC-v0.5 §2.3). Everything an adapter must
    not choose -- the identity provider, the authority document, the environment, the mode -- is
    chosen on the line above, by the person who deployed it.

    ``carries_approved_arguments`` has **no default**, and that is deliberate. It says whether
    your resume value carries back the arguments the human answered against, and therefore
    whether the binding across the interrupt is prevention or attribution (SPEC-v0.5 §3.4). The
    default somebody assumes is the one that does not check, so there isn't one. `README.md`
    argues both settings; `True` is right for almost every deployment, and it is what the
    conformance results in that file were produced with.
    """

    framework = "langgraph"

    def __init__(self, *, carries_approved_arguments: bool) -> None:
        if not isinstance(carries_approved_arguments, bool):
            raise InvalidArgument(
                "carries_approved_arguments must be True or False, and it must be stated: it "
                "declares whether this deployment's resume value carries back what the human "
                "answered against (SPEC-v0.5 §3.4)"
            )
        self.carries_approved_arguments = carries_approved_arguments

    def interrupt(self, pending: PendingApproval) -> ApprovalAnswer:
        """Hand the pending approval to LangGraph and return what came back.

        The two lines that are this adapter. `interrupt()` raises `GraphInterrupt` on the first
        pass, which LangGraph catches and checkpoints; the resumed run re-enters here and it
        returns the value from `Command(resume=...)`. Neither the raise nor the return is caught
        or converted: an exception out of here reaches `InterruptApprovalProvider`, which writes
        nothing and lets it propagate (SPEC-v0.5 §2.4).

        `pending.to_dict()` is what a human sees, and it is JSON by construction because
        LangGraph persists it: the payload survives a checkpoint, a restart and a different
        process.
        """
        from langgraph.types import interrupt as langgraph_interrupt

        return self.answer(langgraph_interrupt(pending.to_dict()))

    def answer(self, resume: Any) -> ApprovalAnswer:
        """Read a resume value. Public so a deployment can unit-test its own answer shape.

        A bare `True`/`False` is accepted for a deployment whose console has only a button.
        Anything else must be a mapping with `approved`; `approver` is optional and defaults to
        the channel name; `arguments` is **required** where this interrupt declares it carries
        them, because a declaration is not a hint (SPEC-v0.5 §3.4).

        This is not a resume token and not a second approval path. It is a payload shape for
        LangGraph's own resumption channel: nothing is minted, nothing is stored, and there is
        no id here that this adapter invented.
        """
        if isinstance(resume, bool):
            answered: Mapping[str, Any] = {"approved": resume}
        elif isinstance(resume, Mapping):
            answered = resume
        else:
            raise InvalidArgument(
                f"the resume value is {type(resume).__name__}; LangGraphInterrupt reads a bool "
                f"or a mapping:\n{RESUME_SHAPE}"
            )

        if "approved" not in answered:
            raise InvalidArgument(
                f"the resume mapping has no 'approved' key, so it states no verdict:\n"
                f"{RESUME_SHAPE}"
            )
        verdict = answered["approved"]
        if not isinstance(verdict, bool):
            # A truthy string is not a yes. The kernel refuses this too (SPEC-v0.5 §2.2); it is
            # refused here as well so the message names LangGraph's resume value, which is where
            # an operator can fix it.
            raise InvalidArgument(
                f"resume['approved'] is {type(verdict).__name__}, and only True or False is a "
                f"verdict -- a truthy value is not a yes:\n{RESUME_SHAPE}"
            )

        approver = answered.get("approver") or CHANNEL
        arguments = answered.get("arguments")
        if self.carries_approved_arguments and verdict and arguments is None:
            raise InvalidArgument(
                "this LangGraphInterrupt declares carries_approved_arguments=True, so a grant "
                "must carry the arguments the human answered against; without them the binding "
                "across the interrupt would be the checkpoint rather than the action hash "
                f"(SPEC-v0.5 §3.4):\n{RESUME_SHAPE}"
            )
        return ApprovalAnswer(granted=verdict, approver=approver, approved_arguments=arguments)
