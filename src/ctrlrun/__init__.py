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
from .effect import EffectRecord, EffectState
from .errors import (
    ActionDenied,
    AmbiguousEffect,
    ApprovalMismatch,
    ApprovalRequired,
    ApprovalTimeout,
    CTRLRunError,
    DuplicateEffect,
    EffectKeyError,
    InvalidArgument,
    NotExecuted,
    PolicyError,
)
from .policy import Decision, Policy
from .receipt import Event, Receipt
from .state import InMemoryStateStore, SQLiteStateStore, StateStore

__all__ = [
    "Action",
    "ActionDenied",
    "AmbiguousEffect",
    "Approval",
    "ApprovalMismatch",
    "ApprovalProvider",
    "ApprovalRequest",
    "ApprovalRequired",
    "ApprovalTimeout",
    "CTRLRunError",
    "Control",
    "Decision",
    "DuplicateEffect",
    "EffectKeyError",
    "EffectRecord",
    "EffectState",
    "Event",
    "InMemoryStateStore",
    "InvalidArgument",
    "LocalApprovalProvider",
    "NotExecuted",
    "Policy",
    "PolicyError",
    "Principal",
    "Receipt",
    "SQLiteStateStore",
    "ScriptedApprovalProvider",
    "StateStore",
    "action_hash",
    "canonicalize",
    "context",
    "protect",
    "with_approval",
]
