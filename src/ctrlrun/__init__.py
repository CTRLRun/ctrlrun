"""CTRLRun: make consequential AI-agent actions safe to execute.

Public API re-exports land with build-list item 1 onward; SPEC-v0.1 §8 freezes the names.
"""

from .action import Action, Principal, action_hash, canonicalize
from .adapter import (
    ApprovalAnswer,
    FrameworkInterrupt,
    InterruptApprovalProvider,
    PendingApproval,
    banner,
    needs_approval,
)
from .approval import (
    Approval,
    ApprovalProvider,
    ApprovalRequest,
    LocalApprovalProvider,
    ScriptedApprovalProvider,
)
from .authority import Authority, AuthorityResult, Delegation, Grant, Subject
from .control import Control, context, protect, with_approval
from .effect import EffectRecord, EffectState, ReconcileOutcome
from .errors import (
    ActionDenied,
    AmbiguousEffect,
    ApprovalMismatch,
    ApprovalRequired,
    ApprovalTimeout,
    AuthorityDenied,
    AuthorityEscalation,
    CTRLRunError,
    DuplicateEffect,
    EffectKeyError,
    IdentityError,
    InvalidArgument,
    MissingDependency,
    NotExecuted,
    PolicyError,
    Suspended,
)
from .identity import (
    HeaderIdentityProvider,
    IdentityContext,
    IdentityProvider,
    StaticIdentityProvider,
)
from .policy import Condition, Decision, Policy, parse_conditions
from .receipt import Event, EventSink, JSONLEventSink, Receipt
from .state import DelegationRecord, InMemoryStateStore, SQLiteStateStore, StateStore
from .webhook import WebhookApprovalProvider

__all__ = [
    "Action",
    "ActionDenied",
    "AmbiguousEffect",
    "Approval",
    "ApprovalAnswer",
    "ApprovalMismatch",
    "ApprovalProvider",
    "ApprovalRequest",
    "ApprovalRequired",
    "ApprovalTimeout",
    "Authority",
    "AuthorityDenied",
    "AuthorityEscalation",
    "AuthorityResult",
    "CTRLRunError",
    "Condition",
    "Control",
    "Decision",
    "Delegation",
    "DelegationRecord",
    "DuplicateEffect",
    "EffectKeyError",
    "EffectRecord",
    "EffectState",
    "Event",
    "EventSink",
    "FrameworkInterrupt",
    "Grant",
    "HeaderIdentityProvider",
    "IdentityContext",
    "IdentityError",
    "IdentityProvider",
    "InMemoryStateStore",
    "InterruptApprovalProvider",
    "InvalidArgument",
    "JSONLEventSink",
    "LocalApprovalProvider",
    "MissingDependency",
    "NotExecuted",
    "PendingApproval",
    "Policy",
    "PolicyError",
    "Principal",
    "Receipt",
    "ReconcileOutcome",
    "SQLiteStateStore",
    "ScriptedApprovalProvider",
    "StateStore",
    "StaticIdentityProvider",
    "Subject",
    "Suspended",
    "WebhookApprovalProvider",
    "action_hash",
    "banner",
    "canonicalize",
    "context",
    "needs_approval",
    "parse_conditions",
    "protect",
    "with_approval",
]
