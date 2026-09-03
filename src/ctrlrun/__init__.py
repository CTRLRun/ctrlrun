"""CTRLRun: make consequential AI-agent actions safe to execute.

Public API re-exports land with build-list item 1 onward; SPEC-v0.1 §8 freezes the names.
"""

from .action import Action, Principal, action_hash, canonicalize
from .approval import (
    Approval,
    ApprovalProvider,
    ApprovalRequest,
    LocalApprovalProvider,
    ScriptedApprovalProvider,
)
from .control import Control, context, protect, with_approval
from .errors import (
    ActionDenied,
    ApprovalMismatch,
    ApprovalRequired,
    ApprovalTimeout,
    CTRLRunError,
    EffectKeyError,
    InvalidArgument,
    NotExecuted,
    PolicyError,
)
from .policy import Decision, Policy
from .receipt import Event, Receipt
from .state import InMemoryStateStore, StateStore

__all__ = [
    "Action",
    "ActionDenied",
    "Approval",
    "ApprovalMismatch",
    "ApprovalProvider",
    "ApprovalRequest",
    "ApprovalRequired",
    "ApprovalTimeout",
    "CTRLRunError",
    "Control",
    "Decision",
    "EffectKeyError",
    "Event",
    "InMemoryStateStore",
    "InvalidArgument",
    "LocalApprovalProvider",
    "NotExecuted",
    "Policy",
    "PolicyError",
    "Principal",
    "Receipt",
    "ScriptedApprovalProvider",
    "StateStore",
    "action_hash",
    "canonicalize",
    "context",
    "protect",
    "with_approval",
]
