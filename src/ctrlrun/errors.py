"""Exception hierarchy for the public API. SPEC-v0.1 §8."""


class CTRLRunError(Exception):
    """Base class for every error raised by CTRLRun."""


class InvalidArgument(CTRLRunError):
    """An Action field or argument cannot be canonicalized (SPEC-v0.1 §2.3)."""


class PolicyError(CTRLRunError):
    """The policy is missing, unreadable, or malformed. Raised at load time (SPEC-v0.1 §3.4)."""


class ActionDenied(CTRLRunError):
    """The action may not run. `reason` says why, e.g. `unknown_action` (SPEC-v0.1 §3.4)."""

    def __init__(
        self, message: str | None = None, *, reason: str, action_id: str | None = None
    ) -> None:
        super().__init__(message if message is not None else reason)
        self.reason = reason
        self.action_id = action_id


class ApprovalRequired(CTRLRunError):
    """The action needs a human. `request_id` is what `ctrlrun approve` takes (SPEC §4.3).

    Raised instead of blocking, so an agent loop can surface the request and come back with
    `ctrlrun.with_approval(request_id)` in context.
    """

    def __init__(
        self, message: str | None = None, *, request_id: str, action_id: str | None = None
    ) -> None:
        super().__init__(message if message is not None else request_id)
        self.request_id = request_id
        self.action_id = action_id


class ApprovalTimeout(CTRLRunError):
    """Nobody answered the approval request in time (SPEC-v0.1 §4.3)."""

    def __init__(self, message: str | None = None, *, request_id: str) -> None:
        super().__init__(message if message is not None else request_id)
        self.request_id = request_id


class ApprovalMismatch(CTRLRunError):
    """The presented approval does not authorize this action (SPEC-v0.1 §4.2).

    `reason` is one of `unknown`, `mismatch`, or the status the record was in — `consumed`,
    `expired`, `pending`, `denied`.
    """

    def __init__(
        self, message: str | None = None, *, reason: str, approval_id: str | None = None
    ) -> None:
        super().__init__(message if message is not None else reason)
        self.reason = reason
        self.approval_id = approval_id


class NotExecuted(CTRLRunError):
    """Raised by an executor to assert the remote side did nothing (SPEC-v0.1 §5.5).

    This is the *only* exception that maps to `FAILED` and therefore permits a retry.
    Every other exception is an `AMBIGUOUS` outcome.
    """
