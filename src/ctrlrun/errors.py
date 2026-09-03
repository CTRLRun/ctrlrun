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


class NotExecuted(CTRLRunError):
    """Raised by an executor to assert the remote side did nothing (SPEC-v0.1 §5.5).

    This is the *only* exception that maps to `FAILED` and therefore permits a retry.
    Every other exception is an `AMBIGUOUS` outcome.
    """
