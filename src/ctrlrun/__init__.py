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
from .effect import EffectRecord, EffectState, ReconcileOutcome
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
    MissingDependency,
    NotExecuted,
    PolicyError,
    Suspended,
)
from .policy import Decision, Policy
from .receipt import Event, EventSink, JSONLEventSink, Receipt
from .state import InMemoryStateStore, SQLiteStateStore, StateStore
from .webhook import WebhookApprovalProvider

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
    "EventSink",
    "InMemoryStateStore",
    "InvalidArgument",
    "JSONLEventSink",
    "LocalApprovalProvider",
    "MissingDependency",
    "NotExecuted",
    "Policy",
    "PolicyError",
    "Principal",
    "Receipt",
    "ReconcileOutcome",
    "SQLiteStateStore",
    "ScriptedApprovalProvider",
    "StateStore",
    "Suspended",
    "WebhookApprovalProvider",
    "action_hash",
    "canonicalize",
    "context",
    "protect",
    "with_approval",
]
