"""SPEC-v0.2 §6.8, and nothing else. Build-list item 6b.

This module is the reason the gateway is specified at all: it is v0.1 §5.5 for a network hop,
and **its asymmetry MUST NOT be inverted**. Only two kinds of observation assert that the
remote did nothing —

- the connection was never established, so no request byte can have been written;
- the peer said, in band and before dispatch, that it rejected the request;

— and everything else after the first byte is `AMBIGUOUS`. There is no option that relaxes
this, and adding one would remove the only guarantee this library makes.

Pure: it takes a description of what was observed and returns what to record and what to
send. No sockets, no store, no policy. The gateway's transport layer is responsible for
mapping its client's exceptions onto `Transport` honestly — in particular for using a fresh
connection per intercepted call, without which `NEVER_CONNECTED` is not provable (§6.8).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from ..effect import EffectState

#: SPEC-v0.2 §6.10 — the two codes this module synthesizes. Frozen in §11.
AMBIGUOUS_CODE: Final = -41010
NOT_EXECUTED_CODE: Final = -41011

AMBIGUOUS_TOKEN: Final = "ctrlrun.upstream_ambiguous"
NOT_EXECUTED_TOKEN: Final = "ctrlrun.upstream_not_executed"

#: The `resultType` values the gateway understands. Anything else — including its absence —
#: is a malformed answer about a consequential action, which is an unknown outcome.
COMPLETE: Final = "complete"
INPUT_REQUIRED: Final = "input_required"

#: JSON-RPC errors the specification defines as emitted *before* dispatch (SPEC-v0.2 §6.8).
#: The peer is telling you, in band, that it rejected the request rather than running the
#: method — the closest thing MCP offers to v0.1's `NotExecuted`.
#:
#: The set is closed and small, and `-32603 Internal error` is not in it and never will be:
#:   -32700 parse error          the body never became a request
#:   -32600 invalid request      rejected as a JSON-RPC message
#:   -32601 method not found     there is no method to run
#:   -32602 invalid params       JSON-RPC defines it as validation, which precedes invocation
#:   -32020 HeaderMismatch       the revision requires rejection before processing
#:   -32021 MissingRequiredClientCapability   raised from `_meta` before the method is reached
#:   -32022 UnsupportedProtocolVersion        refused for its version, not executed
PRE_DISPATCH_ERROR_CODES: Final = frozenset(
    {-32700, -32600, -32601, -32602, -32020, -32021, -32022}
)


class Transport(StrEnum):
    """What the transport observed, where no JSON-RPC message came back (SPEC-v0.2 §6.8)."""

    #: DNS failure, connection refused, TLS handshake failure, connect timeout. The only
    #: member that asserts non-execution, and only because no request byte can have been
    #: written: a pooled connection the upstream closed while idle fails on *write*, which is
    #: indistinguishable from a request that arrived, so intercepted calls never reuse one.
    NEVER_CONNECTED = "never_connected"

    #: Write timeout, read timeout, connection reset, protocol error, TLS failure after the
    #: request was sent.
    AFTER_REQUEST_SENT = "after_request_sent"

    #: A body that is not valid JSON, or not a JSON-RPC message, or whose `id` does not
    #: match; and any HTTP status with no parseable JSON-RPC body at all.
    UNREADABLE_RESPONSE = "unreadable_response"

    #: An SSE stream that closed before delivering a final response.
    STREAM_ENDED_EARLY = "stream_ended_early"

    #: The client went away mid-stream. Closing the stream is cancellation under this
    #: revision, and the upstream may already have committed.
    CLIENT_DISCONNECTED = "client_disconnected"


@dataclass(frozen=True)
class UpstreamResult:
    """A well-formed JSON-RPC *result* from the upstream (SPEC-v0.2 §6.8)."""

    result_type: str | None
    is_error: bool = False


@dataclass(frozen=True)
class UpstreamError:
    """A well-formed JSON-RPC *error* from the upstream."""

    code: int


@dataclass(frozen=True)
class UpstreamStatus:
    """An HTTP status carrying no JSON-RPC message the gateway could read."""

    status: int
    has_www_authenticate: bool = False


Observed = Transport | UpstreamResult | UpstreamError | UpstreamStatus


@dataclass(frozen=True)
class GatewayOutcome:
    """What to record for the effect, and what the client gets (SPEC-v0.2 §6.8, §6.10).

    `effect` is `None` only for `input_required`, where §6.9 takes over and no outcome is
    written at all: the reservation stays `EXECUTING` across the round trip.

    `relay` means the upstream's own response goes back untouched — rewriting a tool's result
    or error would corrupt the contract between the agent and the tool. The gateway
    synthesizes a response only when it never got one.
    """

    effect: EffectState | None
    relay: bool = False
    code: int | None = None
    token: str | None = None
    http_status: int | None = None
    elicitation: bool = False


_RELAY_COMMITTED: Final = GatewayOutcome(EffectState.COMMITTED, relay=True)
_RELAY_FAILED: Final = GatewayOutcome(EffectState.FAILED, relay=True)
_RELAY_AMBIGUOUS: Final = GatewayOutcome(EffectState.AMBIGUOUS, relay=True)
_ELICITATION: Final = GatewayOutcome(None, relay=True, elicitation=True)

_SYNTHESIZED_AMBIGUOUS: Final = GatewayOutcome(
    EffectState.AMBIGUOUS, code=AMBIGUOUS_CODE, token=AMBIGUOUS_TOKEN, http_status=502
)
_SYNTHESIZED_NOT_EXECUTED: Final = GatewayOutcome(
    EffectState.FAILED, code=NOT_EXECUTED_CODE, token=NOT_EXECUTED_TOKEN, http_status=502
)
#: Cancellation: the effect is unknown, and there is nobody left to tell.
_CANCELLED: Final = GatewayOutcome(
    EffectState.AMBIGUOUS, code=AMBIGUOUS_CODE, token=AMBIGUOUS_TOKEN, http_status=None
)


def classify(observed: Observed, *, not_executed_on_error: bool = False) -> GatewayOutcome:
    """Apply SPEC-v0.2 §6.8 to one observation.

    `not_executed_on_error` is the operator's per-tool assertion that this upstream reports
    errors only *before* acting (§3.1). It is the `NotExecuted` of v0.1 §5.5 in YAML: the
    same claim, made by the person who knows, with the same consequences if they are wrong.
    It applies to a tool-level `isError` and to nothing else — not to a JSON-RPC error, and
    not to a response the gateway could not parse.
    """
    if isinstance(observed, Transport):
        return _transport(observed)
    if isinstance(observed, UpstreamResult):
        return _result(observed, not_executed_on_error)
    if isinstance(observed, UpstreamError):
        return _RELAY_FAILED if observed.code in PRE_DISPATCH_ERROR_CODES else _RELAY_AMBIGUOUS
    return _status(observed)


def _transport(observed: Transport) -> GatewayOutcome:
    if observed is Transport.NEVER_CONNECTED:
        return _SYNTHESIZED_NOT_EXECUTED
    if observed is Transport.CLIENT_DISCONNECTED:
        return _CANCELLED
    return _SYNTHESIZED_AMBIGUOUS


def _result(observed: UpstreamResult, not_executed_on_error: bool) -> GatewayOutcome:
    if observed.result_type == INPUT_REQUIRED:
        return _ELICITATION
    if observed.result_type != COMPLETE:
        # Deliberately against the client rule: the revision tells *clients* to read an
        # absent `resultType` as complete, for compatibility with servers implementing
        # earlier versions. The gateway refuses those servers at the version check (§6.2), so
        # on a response it accepts, absence means the response is malformed — and a malformed
        # answer about a consequential action is an unknown outcome, not a success.
        return _RELAY_AMBIGUOUS
    if not observed.is_error:
        return _RELAY_COMMITTED
    return _RELAY_FAILED if not_executed_on_error else _RELAY_AMBIGUOUS


def _status(observed: UpstreamStatus) -> GatewayOutcome:
    # An authorization rejection is FAILED, and it matters more than it looks: an expired or
    # insufficiently scoped token is the most common thing that goes wrong between a gateway
    # and an upstream, and the authorization spec puts that check before the method. Nothing
    # reaches the tool. Recording it AMBIGUOUS would mean a routine token refresh left an
    # effect key needing a human, which is how a guarantee turns into something people
    # switch off. The challenge is relayed so the client can step up and retry, and the retry
    # is permitted because the record is FAILED (v0.1 §5.4).
    if observed.status == 401 or (observed.status == 403 and observed.has_www_authenticate):
        return _RELAY_FAILED
    return _SYNTHESIZED_AMBIGUOUS
