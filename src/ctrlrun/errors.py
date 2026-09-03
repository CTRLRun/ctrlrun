"""Exception hierarchy for the public API. SPEC-v0.1 §8."""


class CTRLRunError(Exception):
    """Base class for every error raised by CTRLRun."""


class InvalidArgument(CTRLRunError):
    """An argument cannot be accepted as given.

    An Action field or argument that cannot be canonicalized (SPEC-v0.1 §2.3), and — the
    same kind of wiring bug — a StateStore transition no record can make, such as committing
    an effect nobody reserved.
    """


class PolicyError(CTRLRunError):
    """The policy is missing, unreadable, or malformed. Raised at load time (SPEC-v0.1 §3.4)."""


class EffectKeyError(CTRLRunError):
    """An effect template cannot be resolved to a key (SPEC-v0.1 §5.1).

    The action is refused rather than executed without an effect key: an action whose
    logical effect cannot be identified cannot be protected against duplication.
    """


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


class DuplicateEffect(CTRLRunError):
    """This logical effect already happened, or is happening now (SPEC-v0.1 §5.4).

    `state` is `committed` — the effect is done — or `in_progress`, meaning another attempt
    holds a live reservation on the key. Neither permits a second execution.
    """

    def __init__(
        self, message: str | None = None, *, state: str, effect_key: str | None = None
    ) -> None:
        super().__init__(message if message is not None else state)
        self.state = state
        self.effect_key = effect_key


class AmbiguousEffect(CTRLRunError):
    """The outcome of this effect is unknown; only a human may resolve it (SPEC-v0.1 §5.4).

    Raised for a record already in `AMBIGUOUS`, and for one whose lease expired mid-flight:
    the worker may have died after the remote committed. A retry is refused either way,
    until `ctrlrun resolve` says which it was.
    """

    def __init__(
        self, message: str | None = None, *, effect_key: str, action_id: str | None = None
    ) -> None:
        super().__init__(message if message is not None else effect_key)
        self.effect_key = effect_key
        self.action_id = action_id


class NotExecuted(CTRLRunError):
    """Raised by an executor to assert the remote side did nothing (SPEC-v0.1 §5.5).

    This is the *only* exception that maps to `FAILED` and therefore permits a retry.
    Every other exception is an `AMBIGUOUS` outcome.
    """
