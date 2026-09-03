"""Request parsing and header-body validation. Build-list item 6b; SPEC-v0.2 §6.2, §6.4, §6.6.

**The headers are checked; the body is believed.** The revision mirrors `method`,
`params.name` and annotated tool parameters into `Mcp-Method`, `Mcp-Name` and
`Mcp-Param-{Name}` so intermediaries can route without parsing bodies — and taking that
shortcut is exactly the hazard the mirroring rule exists to prevent, a load balancer routing
on the header while the server executes on the body. So the gateway parses every body it
forwards, validates every header against it, and decides what to intercept from the body.

Pure: bytes and headers in, a parsed request or a refusal out. No sockets and no store, so
§6.11's refusal table can be read against this module line by line.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

#: SPEC-v0.2 §6.2 — the revision this gateway is written against.
CURRENT_REVISION: Final = "2026-07-28"

#: And the 2025 revisions it accepts in passthrough mode. The deprecated two-endpoint
#: HTTP+SSE transport of 2024-11-05 is not accepted, on either side (§6.2, §12).
LEGACY_REVISIONS: Final = frozenset({"2025-11-25", "2025-06-18", "2025-03-26"})
ACCEPTED_REVISIONS: Final = frozenset({CURRENT_REVISION}) | LEGACY_REVISIONS

#: An absent `MCP-Protocol-Version` is treated as this, which is that era's own
#: backwards-compatibility rule. It costs nothing: a modern upstream will reject the
#: forwarded request itself, and §6.8 maps that to `FAILED`.
LEGACY_DEFAULT_REVISION: Final = "2025-03-26"

#: §6.4 — a body the gateway will not read is a body it cannot decide about.
DEFAULT_MAX_BODY_BYTES: Final = 1024 * 1024

#: §6.3 — `tools/call` and nothing else. Every other method is relayed unchanged.
INTERCEPTED_METHOD: Final = "tools/call"

_PARAM_PREFIX: Final = "mcp-param-"
_SENTINEL_OPEN: Final = "=?base64?"
_SENTINEL_CLOSE: Final = "?="

#: JSON-RPC codes this module emits, from §6.11's table.
PARSE_ERROR: Final = -32700
INVALID_REQUEST: Final = -32600
HEADER_MISMATCH: Final = -32020
UNSUPPORTED_PROTOCOL_VERSION: Final = -32022


@dataclass(frozen=True)
class Refusal:
    """A request the gateway will not forward (SPEC-v0.2 §6.11).

    Everything this module produces is refused *before* an Action exists, so it leaves no
    receipt and no events — only a log line. `code` is `None` where the refusal has no
    JSON-RPC body at all, which is the size limit.
    """

    http_status: int
    code: int | None
    message: str


@dataclass(frozen=True)
class ParsedRequest:
    """One JSON-RPC message the gateway is willing to forward (SPEC-v0.2 §6.4, §6.6)."""

    revision: str
    document: Mapping[str, Any]
    method: str | None
    intercept: bool
    is_response: bool = False
    tool_name: str | None = None
    arguments: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_legacy(self) -> bool:
        return self.revision in LEGACY_REVISIONS


def parse_request(
    body: bytes,
    headers: Mapping[str, str],
    *,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
) -> ParsedRequest | Refusal:
    """Parse and validate one request body against its headers (SPEC-v0.2 §6.4).

    The order is the order of §6.11: size, then JSON, then JSON-RPC shape, then the protocol
    version, then the mirrored headers. Each refusal happens before anything is forwarded.
    """
    if len(body) > max_body_bytes:
        return Refusal(
            413,
            None,
            f"request body is {len(body)} bytes, over the {max_body_bytes}-byte limit",
        )

    try:
        document = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return Refusal(400, PARSE_ERROR, f"request body is not valid JSON: {exc}")

    if isinstance(document, list):
        # The revision is explicit that the body is a single request or notification, so
        # there is no batch to attribute — and a batch the gateway cannot attribute to one
        # principal and one action is exactly the thing it must not pass through.
        return Refusal(400, INVALID_REQUEST, "a JSON-RPC batch has no single action to decide")
    if not isinstance(document, dict) or document.get("jsonrpc") != "2.0":
        return Refusal(400, INVALID_REQUEST, "request body is not a JSON-RPC 2.0 message")

    revision = _revision_of(headers)
    if revision is None:
        named = ", ".join(sorted(ACCEPTED_REVISIONS))
        return Refusal(
            400,
            UNSUPPORTED_PROTOCOL_VERSION,
            f"unsupported MCP-Protocol-Version; this gateway accepts {named}",
        )

    method = document.get("method")
    if method is None:
        return _parse_response(document, revision, headers)
    if not isinstance(method, str) or not method:
        return Refusal(400, INVALID_REQUEST, "'method' must be a non-empty string")

    params = document.get("params")
    params = params if isinstance(params, Mapping) else {}
    intercept = method == INTERCEPTED_METHOD
    tool_name = params.get("name") if intercept else None
    if intercept and (not isinstance(tool_name, str) or not tool_name):
        return Refusal(400, INVALID_REQUEST, "tools/call needs a 'params.name' string")

    arguments = params.get("arguments")
    arguments = dict(arguments) if isinstance(arguments, Mapping) else {}

    mismatch = _validate_headers(headers, revision, method, tool_name, arguments)
    if mismatch is not None:
        return mismatch

    return ParsedRequest(
        revision=revision,
        document=document,
        method=method,
        intercept=intercept,
        tool_name=tool_name if isinstance(tool_name, str) else None,
        arguments=arguments,
    )


def _parse_response(
    document: Mapping[str, Any], revision: str, headers: Mapping[str, str]
) -> ParsedRequest | Refusal:
    """A JSON-RPC *response* in a POST body: legacy only (SPEC-v0.2 §6.2's table)."""
    if "result" not in document and "error" not in document:
        return Refusal(
            400, INVALID_REQUEST, "a JSON-RPC message needs 'method', 'result' or 'error'"
        )
    if revision not in LEGACY_REVISIONS:
        return Refusal(
            400,
            INVALID_REQUEST,
            f"a JSON-RPC response body is not permitted on {revision}; it is a legacy mechanic",
        )
    mismatch = _validate_headers(headers, revision, method=None, tool_name=None, arguments={})
    if mismatch is not None:
        return mismatch
    return ParsedRequest(
        revision=revision, document=document, method=None, intercept=False, is_response=True
    )


def _revision_of(headers: Mapping[str, str]) -> str | None:
    """The revision this request declares, or `None` if it is outside the accepted set."""
    declared = _header(headers, "mcp-protocol-version")
    if declared is None:
        return LEGACY_DEFAULT_REVISION
    return declared if declared in ACCEPTED_REVISIONS else None


def _validate_headers(
    headers: Mapping[str, str],
    revision: str,
    method: str | None,
    tool_name: str | None,
    arguments: Mapping[str, Any],
) -> Refusal | None:
    """Check every mirrored header against the body (SPEC-v0.2 §6.4).

    Conditional on the headers existing, and that is the whole of §6.2's amendment. On
    `2026-07-28` the mirrored headers are part of the protocol and their absence is a
    refusal; on the 2025 revisions they are not, so absence is not a refusal — but a header
    that *is* present is validated either way, because a header disagreeing with the body is
    the hazard §6.4 is about whatever the revision says about mirroring.
    """
    required = revision == CURRENT_REVISION and method is not None

    declared_method = _header(headers, "mcp-method")
    if declared_method is None:
        if required:
            return _mismatch("Mcp-Method is required on " + revision)
    else:
        decoded = _decoded(declared_method)
        if decoded is None:
            return _mismatch("Mcp-Method carries a base64 sentinel that does not decode")
        if method is not None and decoded != method:
            return _mismatch(f"Mcp-Method is {decoded!r} and the body's method is {method!r}")

    declared_name = _header(headers, "mcp-name")
    if declared_name is None:
        if required and tool_name is not None:
            return _mismatch("Mcp-Name is required on " + revision)
    else:
        decoded = _decoded(declared_name)
        if decoded is None:
            return _mismatch("Mcp-Name carries a base64 sentinel that does not decode")
        if tool_name is not None and decoded != tool_name:
            return _mismatch(f"Mcp-Name is {decoded!r} and params.name is {tool_name!r}")

    for name, value in headers.items():
        if not name.lower().startswith(_PARAM_PREFIX):
            continue
        argument = name[len(_PARAM_PREFIX) :]
        if argument not in arguments:
            return _mismatch(f"{name} names no argument in the body")
        decoded = _decoded(value)
        if decoded is None:
            return _mismatch(f"{name} carries a base64 sentinel that does not decode")
        if not _agrees(decoded, arguments[argument]):
            return _mismatch(f"{name} is {decoded!r} and the body's {argument} is not")
    return None


def _mismatch(detail: str) -> Refusal:
    return Refusal(400, HEADER_MISMATCH, f"header does not match the body: {detail}")


def _header(headers: Mapping[str, str], name: str) -> str | None:
    """HTTP header names are case-insensitive; the caller's mapping may not be."""
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None


def _decoded(value: str) -> str | None:
    """Decode the revision's `=?base64?…?=` sentinel, or `None` if it will not decode.

    A sentinel the gateway cannot decode is a header it cannot validate, and an unvalidatable
    header on a request it is about to forward is a refusal — never a comparison against the
    wrapper, which would pass or fail for the wrong reason.
    """
    if not (value.startswith(_SENTINEL_OPEN) and value.endswith(_SENTINEL_CLOSE)):
        return value
    payload = value[len(_SENTINEL_OPEN) : -len(_SENTINEL_CLOSE)]
    try:
        return base64.b64decode(payload, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None


def encode_header_value(value: str) -> str:
    """Wrap a value in the revision's base64 sentinel where it is not ASCII-safe (§6.4)."""
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
        return f"{_SENTINEL_OPEN}{encoded}{_SENTINEL_CLOSE}"
    return value


def _agrees(declared: str, value: object) -> bool:
    """Whether a header value agrees with the body's, comparing integers numerically (§6.4).

    `2000` in a header is text and the body's is an `int`; comparing them as strings would
    refuse every well-formed request that annotated a numeric parameter.
    """
    if isinstance(value, bool):
        return declared.lower() == str(value).lower()
    if isinstance(value, int):
        try:
            return int(declared) == value
        except ValueError:
            return False
    if isinstance(value, str):
        return declared == value
    return declared == json.dumps(value, separators=(",", ":"))


def find_unrepresentable(arguments: object, pointer: str = "") -> str | None:
    """The JSON pointer to the first `float` in `arguments`, or `None` (SPEC-v0.2 §6.6).

    MCP tool schemas routinely use `"type": "number"`, and v0.1 §2.3 rejects `float` at
    construction. The gateway MUST NOT round, truncate or coerce one: `0.1` and `0.10` are
    the same money and different hashes, and a gateway that quietly picked a spelling would
    break the approval binding for every action that touches the value. The fix belongs in
    the tool's schema — integer minor units, or decimal strings.
    """
    if isinstance(arguments, bool):
        return None
    if isinstance(arguments, float):
        return pointer or "/"
    if isinstance(arguments, Mapping):
        for key, value in arguments.items():
            found = find_unrepresentable(value, f"{pointer}/{key}")
            if found is not None:
                return found
        return None
    if isinstance(arguments, list):
        for index, value in enumerate(arguments):
            found = find_unrepresentable(value, f"{pointer}/{index}")
            if found is not None:
                return found
    return None
