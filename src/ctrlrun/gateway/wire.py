"""What both servers in this package speak, and neither owns.

`ctrlrun gateway` (`server.py`) and `ctrlrun mcp-operator` (`operator.py`) are two HTTP servers
with the same response shape, the same JSON-RPC error shape and the same `--identity-jwt-*`
flags. The operator server was written second and reached into the gateway for all of it, and
the gateway then needed the operator's config type to say what `check_jwt_flags` accepts -- a
cycle, and a layering inversion: `ARCHITECTURE.md` §6 has dependencies pointing downward, and
the gateway has no business naming the operator even in a type annotation.

CodeQL's `py/unsafe-cyclic-import` found it, six times, before anyone read the diff that way.

So the shared half lives here, below both, and neither server imports the other. Nothing in this
module knows what a gateway or an operator console is: it is response bytes, JSON-RPC envelopes,
a case-insensitive header lookup, and the flag validation that has no safe default.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..errors import InvalidArgument


@dataclass
class _Response:
    """What goes back to the client."""

    status: int
    body: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)


def json_rpc_error(rpc_id: Any, code: int, token: str, message: str, **data: Any) -> dict[str, Any]:
    """One JSON-RPC error object in `v0.2 §6.10`'s shape.

    A refusal by CTRLRun is not an outcome of the tool; it is the statement that the tool did
    not run. `isError: true` would be indistinguishable from the tool's own failure, and it
    reaches the model as text — which is not where a policy denial belongs.
    """
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "error": {"code": code, "message": message, "data": {"error": token, **data}},
    }


def _header(headers: Mapping[str, str], name: str) -> str | None:
    """HTTP header names are case-insensitive; the caller's mapping may not be."""
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return None


def printable(text: str) -> str:
    """One line of somebody else's bytes, made safe to put in a log (`v0.1 §5.3`).

    `BaseHTTPRequestHandler` hands its access log the client's request line, and `send_error`
    hands it a message built from it. Neither is escaped, and `state.py`'s `_approver` refuses
    control characters for exactly this reason: a newline in a value that reaches a line-per-
    record output forges a whole record, and an operator reading the log cannot tell.

    A store field is *refused* there because a forged evidence row is the worst case and the
    value is the caller's own. A log line cannot be refused -- it is already what happened -- so
    it is escaped instead, which is the same judgment applied where refusing is not available.
    """
    return text.encode("unicode_escape").decode("ascii")


def _dump(document: Mapping[str, Any]) -> bytes:
    return json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()


def _json(status: int, document: Mapping[str, Any]) -> _Response:
    return _Response(status, _dump(document), {"Content-Type": "application/json"})


class JwtFlags(Protocol):
    """The `--identity-jwt-*` surface, as `GatewayConfig` and `OperatorConfig` both carry it.

    A Protocol rather than a base class: the two are frozen dataclasses in different modules
    with different other fields, and what they share is a flag set, not an ancestry. Read-only
    members, because both are frozen and a Protocol declaring a bare annotation would demand a
    settable attribute neither has.
    """

    @property
    def identity_jwt(self) -> bool: ...
    @property
    def identity_jwt_jwks_url(self) -> str | None: ...
    @property
    def identity_jwt_public_key(self) -> str | None: ...
    @property
    def identity_jwt_secret_file(self) -> str | None: ...
    @property
    def identity_jwt_algorithms(self) -> tuple[str, ...]: ...
    @property
    def identity_jwt_issuer(self) -> str | None: ...
    @property
    def identity_jwt_audience(self) -> str | None: ...
    @property
    def identity_jwt_token_type(self) -> str | None: ...
    @property
    def identity_jwt_header(self) -> str: ...
    @property
    def identity_jwt_agent_claim(self) -> str: ...
    @property
    def identity_jwt_user_claim(self) -> str | None: ...
    @property
    def identity_jwt_claims(self) -> tuple[str, ...]: ...
    @property
    def identity_jwt_leeway(self) -> float: ...
    @property
    def identity_jwt_jwks_min_refresh(self) -> float: ...
    @property
    def identity_jwt_http_timeout(self) -> float: ...


def check_jwt_flags(config: JwtFlags) -> None:
    """Every `--identity-jwt-*` flag is accepted only with `--identity-jwt` (SPEC-v0.3 §8.2).

    And when it *is* given, the four settings that have no safe default must be there: the
    algorithms, the issuer, the audience and the token type. The provider refuses the same
    things at construction; this refuses them before the extra is even imported, so an operator
    who has not installed it still learns what they got wrong — and, unlike the provider's own
    checks, it is not an `assert`, so `python -O` cannot remove it.

    One function for both servers. A copy in the second config would be a copy that drifts, and
    the half that would have gone missing is the one that matters: an unpinned `typ` accepts an
    ID token (`SPEC-mcp-operator.md` §3.1).
    """
    given = {
        "--identity-jwt-jwks-url": config.identity_jwt_jwks_url is not None,
        "--identity-jwt-public-key": config.identity_jwt_public_key is not None,
        "--identity-jwt-secret-file": config.identity_jwt_secret_file is not None,
        "--identity-jwt-algorithms": bool(config.identity_jwt_algorithms),
        "--identity-jwt-issuer": config.identity_jwt_issuer is not None,
        "--identity-jwt-audience": config.identity_jwt_audience is not None,
        "--identity-jwt-token-type": config.identity_jwt_token_type is not None,
        "--identity-jwt-user-claim": config.identity_jwt_user_claim is not None,
        "--identity-jwt-claim": bool(config.identity_jwt_claims),
        "--identity-jwt-header": config.identity_jwt_header != "authorization",
        "--identity-jwt-agent-claim": config.identity_jwt_agent_claim != "sub",
        "--identity-jwt-leeway": config.identity_jwt_leeway != 60.0,
        "--identity-jwt-jwks-min-refresh": config.identity_jwt_jwks_min_refresh != 30.0,
        "--identity-jwt-http-timeout": config.identity_jwt_http_timeout != 5.0,
    }
    if not config.identity_jwt:
        stray = sorted(name for name, present in given.items() if present)
        if stray:
            raise InvalidArgument(
                f"{', '.join(stray)} needs --identity-jwt; a flag that cannot take effect "
                "is a flag the operator believes took effect (SPEC-v0.3 §8.2)"
            )
        return
    required = (
        "--identity-jwt-algorithms",
        "--identity-jwt-issuer",
        "--identity-jwt-audience",
        "--identity-jwt-token-type",
    )
    missing = sorted(name for name in required if not given[name])
    if missing:
        raise InvalidArgument(
            f"--identity-jwt needs {', '.join(missing)}. There is no default for any of "
            "them: an unpinned algorithm, issuer or audience is a token from somewhere "
            'else, and an unpinned type is an ID token (pass "" to mean "this issuer sets '
            'no typ")'
        )
