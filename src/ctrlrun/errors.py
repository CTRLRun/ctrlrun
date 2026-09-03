"""Exception hierarchy for the public API. SPEC-v0.1 §8."""


class CTRLRunError(Exception):
    """Base class for every error raised by CTRLRun."""


class InvalidArgument(CTRLRunError):
    """An Action field or argument cannot be canonicalized (SPEC-v0.1 §2.3)."""


class PolicyError(CTRLRunError):
    """The policy is missing, unreadable, or malformed. Raised at load time (SPEC-v0.1 §3.4)."""
