"""The operator MCP server. SPEC-mcp-operator.md.

An approver answers from the assistant they are already talking to. `ctrlrun approve` answers
a request at a terminal, the webhook endpoint answers one from Slack, an adapter answers one
through a framework's interrupt, and this answers one from an MCP client -- all four through
the same two store calls, against the same record, with the same hash binding, the same single
use and the same expiry (§1).

Three properties shape the file, and each is asserted by a test rather than left to a reader:

- **It composes nothing.** `Control` is the only module that composes the others
  (`ARCHITECTURE §6`). This holds a `Control` and reads `store`, `policy` and `environment`
  off it. It never executes, resumes, reserves, commits, delegates or revokes (T189).
- **Reads never consult the identity provider; writes always do.** A provider that ran on
  every read would make an expired credential turn `receipts` into a refusal (§4.1).
- **A refused write leaves the store byte-identical** (§11, T190).

It is an origin server, not an intermediary, so `SPEC-v0.2 §6.4`'s header-body validation
applies in full -- through the gateway's own `parse_request`, not a second copy -- and §6.3's
relaying does not apply at all, there being nowhere to relay to (§2).
"""

from __future__ import annotations

import json
import logging
import socket
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Final

from ..action import Principal
from ..approval import (
    ApprovalRecord,
    ApprovalStatus,
    _granting_principal,
    entitled_controls,
    roles_held,
    unsatisfied,
)
from ..control import Control
from ..effect import RESOLVED_BY_HUMAN, EffectState
from ..errors import CTRLRunError, IdentityError, InvalidArgument
from ..identity import (
    HeaderIdentityProvider,
    IdentityContext,
    IdentityProvider,
)
from ..receipt import Event, EventType, iso_timestamp
from ..reporting import effect_document, inspection_for, since_boundary, stats_document
from ..state import RESOLUTIONS, StateStore
from .mcp import DEFAULT_MAX_BODY_BYTES, ParsedRequest, Refusal, parse_request
from .wire import (
    _header,
    _json,
    _Response,
    check_jwt_flags,
    json_rpc_error,
    printable,
)

_LOG = logging.getLogger("ctrlrun.mcp_operator")

#: §2.2 — not the gateway's 8900. Both processes are plausibly running on one host against one
#: store, and two servers whose default ports collide produce a bind error at the worst
#: moment, or a client pointed at the wrong one.
DEFAULT_LISTEN: Final = ("127.0.0.1", 8901)
DEFAULT_PATH: Final = "/mcp"

#: §2.1 — the only hosts this server will bind. There is no flag that adds to this list, and
#: T183 asserts the absence by name: the read tools answer without a credential (§4.1), so this
#: process must not be the one that opens a port to a network.
#:
#: `[::1]` is here as well as `::1` because that is the form an operator types into a
#: `HOST:PORT` argument, and the CLI's `rpartition(":")` hands the brackets through. A review
#: found `::1` accepted by the config and then unable to bind at all: `ThreadingHTTPServer`
#: inherits `AF_INET`, so the socket family has to be chosen from the host (`_family`).
LOOPBACK: Final = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

#: The IPv6 spellings of loopback, and the ones that need `AF_INET6`.
_IPV6_LOOPBACK: Final = frozenset({"::1", "[::1]"})

#: §3.3 — what `IdentityContext.action` carries. Never an action name a policy could match:
#: this server proposes no action, and `mcp.<alias>.<tool>` is the gateway's namespace (§9.3).
ACTION_PREFIX: Final = "mcp-operator."

#: §5.2 — the channel, on every event this server appends. A new key in an existing event's
#: open data mapping (`v0.1 §6.2`), not a new event type: `approver` already says who, and this
#: says through what, which is the question an incident review asks.
VIA: Final = "mcp-operator"

#: §5.4 — `mcp-operator:<user>`. `principal.agent` is deliberately not in it: a string that
#: sometimes names a person and sometimes names a service is not evidence.
_ATTRIBUTION: Final = "mcp-operator:{user}"

SERVER_NAME: Final = "ctrlrun-mcp-operator"

#: §9.3 — two codes added to `v0.2 §6.10`'s table, both in the `-410xx` range that release
#: reserved, neither reachable from the gateway.
NOT_A_HUMAN: Final = (-41013, "ctrlrun.not_a_human", 403)
PRINCIPAL_EXPIRED: Final = (-41014, "ctrlrun.principal_expired", 403)

#: Reused unchanged from `v0.2 §6.10`.
NO_PRINCIPAL: Final = (-41007, "ctrlrun.no_principal", 403)

#: SPEC-v0.8 §3.8 — the credential is verified and does not carry the role the request pinned.
#: A 403 and not a 400: the caller is who they say they are, and the answer is that this is not
#: theirs to give.
_NOT_ENTITLED: Final = -41015
STORE_REFUSED: Final = (-41003, "ctrlrun.approval_denied", 200)

_METHOD_NOT_FOUND: Final = -32601
_INVALID_PARAMS: Final = -32602
_INTERNAL_ERROR: Final = -32603

#: §4.4 — bounds on the two listings. A tool that could be asked for the whole store is a tool
#: an assistant will ask for the whole store.
_MAX_LIMIT: Final = 200
_DEFAULT_PENDING_LIMIT: Final = 50
_DEFAULT_RECEIPT_LIMIT: Final = 20


@dataclass(frozen=True)
class OperatorConfig:
    """Everything `ctrlrun mcp-operator` was started with (SPEC-mcp-operator §9.4).

    There is no `principal` and no `allow_remote`, and their absence is asserted by name
    (T183): §2.1 and §3.1 are each a pair of sentences that hold together or not at all, and a
    flag added later must fail a test rather than a review.

    `frozen=True`, as `GatewayConfig` is, so §2.1's loopback rule is an invariant and not a
    construction-time check: `build_operator_server` re-reads `host` when it binds, and a
    mutable config handed out by `OperatorServer.config` would put that read after anything.
    """

    host: str = DEFAULT_LISTEN[0]
    port: int = DEFAULT_LISTEN[1]
    path: str = DEFAULT_PATH
    principal_header: str | None = None
    user_header: str | None = None
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    allow_origins: tuple[str, ...] = ()
    identity_jwt: bool = False
    identity_jwt_jwks_url: str | None = None
    identity_jwt_public_key: str | None = None
    identity_jwt_secret_file: str | None = None
    identity_jwt_algorithms: tuple[str, ...] = ()
    identity_jwt_issuer: str | None = None
    identity_jwt_audience: str | None = None
    identity_jwt_token_type: str | None = None
    identity_jwt_header: str = "authorization"
    #: SPEC-v0.8 §3.4, §11.1 — which claim this deployment's issuer puts roles in. `None` means
    #: no role can be read, so any control naming one refuses: a deployment naming roles in its
    #: policy and no claim to read them from has configured half a check.
    approver_roles_claim: str | None = None
    identity_jwt_agent_claim: str = "sub"
    identity_jwt_user_claim: str | None = None
    identity_jwt_claims: tuple[str, ...] = ()
    identity_jwt_leeway: float = 60.0
    identity_jwt_jwks_min_refresh: float = 30.0
    identity_jwt_http_timeout: float = 5.0

    def __post_init__(self) -> None:
        if self.host not in LOOPBACK:
            # §2.1 — and there is deliberately no flag that permits one. A deployment that
            # needs this reachable from elsewhere puts a proxy in front of it on this host,
            # terminating authentication there, which is what --principal-header already
            # requires of anyone using it (`v0.3 §3.3`).
            raise InvalidArgument(
                f"--listen {self.host} is not loopback, and this server has no --allow-remote. "
                "Its read tools answer without a credential, so it must not be the process "
                "that opens a port to a network; put a proxy in front of it on this host "
                "(SPEC-mcp-operator §2.1)"
            )
        if not self.path.startswith("/"):
            raise InvalidArgument(f"--path {self.path!r} must start with '/'")
        sources = [self.principal_header is not None, self.identity_jwt]
        if sum(sources) != 1:
            # §3.1 — `--principal` is not among them. `StaticIdentityProvider` answers with the
            # same principal for every request, so every approval it produced would carry an
            # approver that distinguishes nobody, and §5.4 makes attribution a MUST.
            raise InvalidArgument(
                "exactly one of --principal-header or --identity-jwt is required. There is no "
                "--principal here: a static provider answers with one name for every request, "
                "and an approval whose approver distinguishes nobody has no attribution "
                "(SPEC-mcp-operator §3.1)"
            )
        if self.principal_header is not None and self.user_header is None:
            # §3.2 — a server whose write tools could never succeed is a server that will be
            # discovered to be broken by an approver at the moment they are trying to stop
            # something. Refused here, where it can still be fixed.
            raise InvalidArgument(
                "--principal-header needs --user-header: a write tool refuses a principal "
                "whose user is None, because an agent with no user is a machine credential "
                "(SPEC-mcp-operator §3.2)"
            )
        if self.identity_jwt and self.user_header is not None:
            # §3.2 — with --identity-jwt the human comes from --identity-jwt-user-claim, so a
            # --user-header here is a flag that cannot take effect, which the gateway refuses
            # by name for the same reason (`v0.3 §8.2`).
            raise InvalidArgument(
                "--user-header is only meaningful with --principal-header; with --identity-jwt "
                "the human comes from --identity-jwt-user-claim"
            )
        # §3.1 — the gateway's own `--identity-jwt-*` checks, shared rather than copied: every
        # such flag needs `--identity-jwt`, and `--identity-jwt` needs the four settings that
        # have no safe default. A copy would have drifted, and the half that would have gone
        # missing is the one that matters — an unpinned `typ` accepts an ID token.
        check_jwt_flags(self)
        if self.identity_jwt and not self.identity_jwt_user_claim:
            raise InvalidArgument(
                "--identity-jwt needs --identity-jwt-user-claim, for the reason in "
                "SPEC-mcp-operator §3.2: a write tool refuses a principal whose user is None"
            )


def operator_identity_provider(config: OperatorConfig) -> IdentityProvider:
    """The provider this server's flags name (SPEC-mcp-operator §3.1).

    Two constructors, not the gateway's three. The JWT import is deferred so that `import
    ctrlrun` never pulls in `jwt` and an operator who selected the extra without installing it
    gets `MissingDependency` naming the command.
    """
    if config.principal_header is not None:
        return HeaderIdentityProvider(
            agent_header=config.principal_header, user_header=config.user_header
        )
    from ..jwt_identity import JWTIdentityProvider

    # `check_jwt_flags` (§3.1) has already refused a configuration missing any of these, and it
    # is an `InvalidArgument` rather than an `assert` deliberately: `python -O` removes asserts,
    # and a guard that a runtime flag can delete is not a guard. These three are `or ""` rather
    # than asserted for the same reason — the provider refuses an empty issuer or audience at
    # construction, so an impossible state stays a refusal on every interpreter.
    issuer = config.identity_jwt_issuer or ""
    audience = config.identity_jwt_audience or ""
    secret = None
    if config.identity_jwt_secret_file is not None:
        secret = Path(config.identity_jwt_secret_file).read_text(encoding="utf-8").strip()
    return JWTIdentityProvider(
        jwks_url=config.identity_jwt_jwks_url,
        public_key=config.identity_jwt_public_key,
        secret=secret,
        algorithms=config.identity_jwt_algorithms,
        issuer=issuer,
        audience=audience,
        token_type=config.identity_jwt_token_type or None,
        header=config.identity_jwt_header,
        agent_claim=config.identity_jwt_agent_claim,
        user_claim=config.identity_jwt_user_claim,
        claim_names=config.identity_jwt_claims,
        leeway=_seconds(config.identity_jwt_leeway),
        jwks_min_refresh_interval=_seconds(config.identity_jwt_jwks_min_refresh),
        http_timeout=_seconds(config.identity_jwt_http_timeout),
    )


def _seconds(value: float) -> Any:
    from datetime import timedelta

    return timedelta(seconds=value)


class _Refused(CTRLRunError):
    """A tool call this server will not perform, carrying the wire shape it becomes.

    One exception type, raised by every check, so the dispatch has one place that turns a
    refusal into a response and a refusal cannot accidentally skip §11's "store unchanged".

    `log` is what the operator's log gets where that must say more than the wire does. An
    identity refusal tells the caller only that its credential was rejected (§3.3) and tells
    the log which provider rejected it and why, and one raise site carrying both is how the
    two stay in step. It is logged and never serialized; `data` is the half that goes on the
    wire.
    """

    def __init__(
        self,
        code: int,
        token: str,
        http_status: int,
        message: str,
        *,
        log: str | None = None,
        **data: Any,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.token = token
        self.http_status = http_status
        self.log = log
        self.data = data


@dataclass(frozen=True)
class _Tool:
    """One entry of `tools/list` (SPEC-mcp-operator §4.6)."""

    name: str
    writes: bool
    description: str
    properties: Mapping[str, Any]
    required: tuple[str, ...] = ()

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": dict(self.properties),
            "required": list(self.required),
            "additionalProperties": False,
        }


_STRING: Final = {"type": "string"}


def _bounded(default: int) -> dict[str, Any]:
    return {"type": "integer", "minimum": 1, "maximum": _MAX_LIMIT, "default": default}


#: §4.6 — a write tool's description says in its first clause that it writes, that it needs an
#: authenticated human, and that the answer is recorded under their name. The assistant renders
#: it, and an approver who did not know their name was going into the evidence log should learn
#: it before they answer rather than after.
TOOLS: Final[tuple[_Tool, ...]] = (
    _Tool(
        "list_pending_approvals",
        False,
        "Read-only. The approval requests waiting for a human, oldest first, with the action, "
        "its arguments and when the request expires.",
        {"limit": _bounded(_DEFAULT_PENDING_LIMIT)},
    ),
    _Tool(
        "inspect_action",
        False,
        "Read-only. One action's whole history: what was proposed, what the policy decided, "
        "which approval was involved, what happened to the effect, and the receipt.",
        {"action_id": _STRING},
        ("action_id",),
    ),
    _Tool(
        "receipts",
        False,
        "Read-only. The evidence this store holds, as portable JSON, oldest last.",
        {"limit": _bounded(_DEFAULT_RECEIPT_LIMIT), "control": _STRING},
    ),
    _Tool(
        "effects",
        False,
        "Read-only. The logical effects this store knows about, optionally one state only.",
        {"state": {"type": "string", "enum": [str(state) for state in EffectState]}},
    ),
    _Tool(
        "stats",
        False,
        "Read-only. What this store's receipts say, over an optional window.",
        {"since": _STRING},
    ),
    _Tool(
        "approve",
        True,
        "WRITES. Grants one pending approval request, letting the agent run that exact "
        "action once. Requires an authenticated human; the answer is recorded under their "
        "name and is visible in the receipt the action leaves.",
        {"request_id": _STRING},
        ("request_id",),
    ),
    _Tool(
        "deny",
        True,
        "WRITES. Refuses one pending approval request. Requires an authenticated human; the "
        "answer is recorded under their name.",
        {"request_id": _STRING},
        ("request_id",),
    ),
    _Tool(
        "resolve",
        True,
        "WRITES. States what actually happened to an effect whose outcome is unknown. "
        "Requires an authenticated human and a reason; the answer is recorded under that "
        "person's name, and a 'failed' resolution permits a retry that is currently blocked.",
        {
            "effect_key": _STRING,
            "outcome": {"type": "string", "enum": sorted(RESOLUTIONS)},
            "reason": _STRING,
        },
        ("effect_key", "outcome", "reason"),
    ),
)

_BY_NAME: Final = {tool.name: tool for tool in TOOLS}


class OperatorServer:
    """One `Control`, one identity provider, eight tools (SPEC-mcp-operator §1).

    The provider is passed in rather than constructed here, exactly as the gateway takes its
    forwarder: `serve_operator` builds it from the operator's flags, and a test substitutes
    one. It is never built from anything in a request.
    """

    def __init__(
        self,
        config: OperatorConfig,
        control: Control,
        identity: IdentityProvider,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._config = config
        self._control = control
        self._identity = identity
        self._clock = clock
        self._warn_about_unreadable_roles()

    def _roles_claim(self) -> str | None:
        """Which claim roles are read from: `--approver-roles-claim`, else the `Control`'s.

        SPEC-v0.8 §3.4 puts the claim name on `ApproverIdentity` because it is a property of
        the issuer that verifies approvers, and this server is one surface reading it. The flag
        wins where both are set, because a flag is what an operator changes to debug a
        deployment, and the two disagreeing is itself worth the warning below.
        """
        if self._config.approver_roles_claim:
            return self._config.approver_roles_claim
        identity = self._control.approver_identity
        return None if identity is None else identity.roles_claim

    def _warn_about_unreadable_roles(self) -> None:
        """SPEC-mcp-operator §4.3: half a check, named at startup and not at the first refusal.

        A policy whose cited controls name an `approver_role` and a deployment with no claim to
        read roles from refuses **every** answer to those requests. That is the fail-closed
        direction and it is correct; what is not acceptable is discovering it when a human is
        told no at three in the morning.
        """
        if self._roles_claim():
            return
        policy = self._control.policy
        gated = sorted(
            identifier for identifier, control in policy.controls.items() if control.approver_role
        )
        if not gated:
            return
        _LOG.warning(
            "controls %s name an approver_role and no claim is configured to read roles from: "
            "every approval they gate will be refused. Set --approver-roles-claim, or pass "
            "roles_claim= on the Control's ApproverIdentity (SPEC-v0.8 §3.4)",
            ", ".join(gated),
        )

    @property
    def config(self) -> OperatorConfig:
        return self._config

    @property
    def identity(self) -> IdentityProvider:
        return self._identity

    @property
    def store(self) -> StateStore:
        """The operator's own store, reached through the `Control` they built (§1.1)."""
        return self._control.store

    # --- the request path ---------------------------------------------------------------

    def handle(self, body: bytes, headers: Mapping[str, str], *, raw: Any = None) -> _Response:
        """Decide one POST. Returns what the client gets.

        `raw` is the request's header pairs **before** they were collapsed into a mapping, where
        the caller has them. `v0.3 §3.1` refuses a repeated identity header rather than
        collapsing it, and a `Mapping[str, str]` has already made that choice — so the check has
        to see the pairs. It used to live only in the stdlib handler, which meant a deployment
        that embedded `OperatorServer` behind its own HTTP layer — the obvious way to satisfy
        §2.1's "put a proxy in front of it" in-process — lost the guard entirely and inherited
        whatever that framework's collapse rule happened to be. A review found it. Where `raw`
        is `None` the mapping's own items are checked, which catches nothing a mapping can
        express and is stated so that the residue is visible rather than assumed.
        """
        repeated = _repeated_identity_header(self._config, headers.items() if raw is None else raw)
        if repeated is not None:
            _LOG.warning("refused: the identity header %r appeared more than once", repeated)
            code, token, status = NO_PRINCIPAL
            return _json(
                status,
                json_rpc_error(
                    _request_id(body),
                    code,
                    token,
                    f"the {repeated!r} header appeared more than once",
                ),
            )
        origin = _header(headers, "origin")
        if origin is not None and origin not in self._config.allow_origins:
            # §2, and `v0.2 §6.1`: the transport requires Origin validation against DNS
            # rebinding, and an empty allowlist that accepted every origin would be validation
            # in name only.
            _LOG.warning("refused a request from origin %r", origin)
            return _Response(403)

        parsed = parse_request(body, headers, max_body_bytes=self._config.max_body_bytes)
        if isinstance(parsed, Refusal):
            # §2's first table row: the headers are checked and the body is believed, by the
            # gateway's own parser rather than a second copy of it (T191).
            _LOG.warning("refused a request: %s", parsed.message)
            if parsed.code is None:
                return _Response(parsed.http_status)
            return _json(
                parsed.http_status,
                {
                    "jsonrpc": "2.0",
                    "id": _request_id(body),
                    "error": {"code": parsed.code, "message": parsed.message},
                },
            )
        return self._dispatch(parsed, headers)

    def _dispatch(self, parsed: ParsedRequest, headers: Mapping[str, str]) -> _Response:
        rpc_id = parsed.document.get("id")
        method = parsed.method
        if method == "initialize":
            return _ok(rpc_id, self._initialize(parsed))
        if method is not None and method.startswith("notifications/"):
            # A notification has no id and gets no body. `initialized` is the only one a client
            # sends here, and answering it with a result would be a protocol error.
            return _Response(202)
        if method == "tools/list":
            # §4.6 — no credential. Which tools exist is not a secret, and refusing it would
            # leave a client unable to discover the read tools it can use.
            return _ok(rpc_id, {"tools": [_listed(tool) for tool in TOOLS]})
        if method != "tools/call":
            return _error(
                rpc_id,
                _METHOD_NOT_FOUND,
                "ctrlrun.method_not_found",
                200,
                f"{method!r} is not a method this server implements; it is an operator "
                "console, not a gateway (SPEC-mcp-operator §2)",
            )
        return self._call(rpc_id, parsed, headers)

    def _initialize(self, parsed: ParsedRequest) -> dict[str, Any]:
        return {
            "protocolVersion": parsed.revision,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": _version()},
            "instructions": (
                "CTRLRun operator console. The read tools show what is waiting and what "
                "happened. approve, deny and resolve write, need an authenticated human, and "
                "record the answer under that person's name. Nothing here can make an agent "
                "act."
            ),
        }

    def _call(self, rpc_id: Any, parsed: ParsedRequest, headers: Mapping[str, str]) -> _Response:
        tool = _BY_NAME.get(parsed.tool_name or "")
        if tool is None:
            return _error(
                rpc_id,
                _INVALID_PARAMS,
                "ctrlrun.unknown_tool",
                200,
                f"no tool named {parsed.tool_name!r}",
            )
        try:
            arguments = _checked(tool, parsed.arguments)
            if tool.writes:
                # §4.2 — resolved before the store is touched, and every row of §3.3's table is
                # a refusal that leaves the store byte-identical (T190).
                principal = self._resolve(tool, headers)
                result = self._write(tool, arguments, principal)
            else:
                # §4.1 — the provider is not consulted at all, even where the request carries a
                # credential. T182's negative half asserts it.
                result = self._read(tool, arguments)
        except _Refused as refused:
            # The one place a refusal is logged. Where the log must say more than the wire —
            # every identity refusal — the raise site carried the detail on `log`, so the two
            # cannot drift and neither is written twice.
            _LOG.warning("refused %s: %s", tool.name, refused.log or refused)
            return _error(
                rpc_id,
                refused.code,
                refused.token,
                refused.http_status,
                str(refused),
                **refused.data,
            )
        except CTRLRunError as refused:
            # Whatever the store said, unchanged. The request was well-formed and, for a write,
            # authenticated; the answer is no, and HTTP 200 says the exchange worked (§7).
            _LOG.warning("%s refused by the store: %s", tool.name, refused)
            code, token, status = STORE_REFUSED
            return _error(
                rpc_id,
                code,
                token,
                status,
                str(refused),
                reason=getattr(refused, "reason", None),
            )
        except Exception:
            # §7's last row. Logged with the traceback, and nothing about it on the wire: an
            # exception message can carry a path, a query or a row.
            _LOG.exception("%s raised", tool.name)
            return _error(
                rpc_id,
                _INTERNAL_ERROR,
                "ctrlrun.internal_error",
                500,
                f"{tool.name} could not be completed; see the server log",
            )
        return _ok(rpc_id, _content(result))

    # --- identity (§3.3) ------------------------------------------------------------------

    def _resolve(self, tool: _Tool, headers: Mapping[str, str]) -> Principal:
        """The human answering, or a refusal (SPEC-mcp-operator §3.3).

        The provider is called with the request's headers, exactly as the gateway calls it
        (`v0.3 §8.2`): a credential arrives in a header, and the in-process path that
        resolves a principal reads none. Every refusal below leaves the store untouched and
        writes nothing to the evidence log -- `v0.3 §3.2`'s rule, and §3.3 records the
        rejected alternative.
        """
        code, token, status = NO_PRINCIPAL
        context = IdentityContext(
            action=f"{ACTION_PREFIX}{tool.name}",
            environment=self._control.environment,
            headers={name.lower(): value for name, value in headers.items()},
        )
        try:
            principal = self._identity.resolve(context)
        except IdentityError as refused:
            raise _Refused(
                code,
                token,
                status,
                "the credential offered was rejected",
                log=f"the identity provider rejected the credential: {refused}",
            ) from refused
        except Exception as exc:
            # `v0.3 §3.2` — one exception type for a caller to catch. Without this a custom
            # provider hitting a transient error reaches `socketserver`, which closes the
            # socket with no response. A `BaseException` that is not an `Exception` propagates
            # (`v0.1 §5.5`).
            raise _Refused(
                code,
                token,
                status,
                "the credential offered was rejected",
                log=(
                    f"the identity provider {type(self._identity).__name__} raised "
                    f"{type(exc).__name__}: {exc}"
                ),
            ) from exc
        if principal is None:
            raise _Refused(code, token, status, "no principal could be derived from the request")
        if principal.expires_at is not None and self._clock() > principal.expires_at:
            # `v0.3 §2.3`, applied at the one entry point that resolves a principal and never
            # reaches `Control.execute` to have it applied for it (`v0.3 §4.3.1`). Its own code,
            # because "your credential expired" and "you presented none" are different problems
            # with different fixes (§3.3).
            expired_code, expired_token, expired_status = PRINCIPAL_EXPIRED
            raise _Refused(
                expired_code,
                expired_token,
                expired_status,
                "the credential offered has expired",
                log=(
                    f"the credential naming {principal.user or principal.agent!r} expired at "
                    f"{iso_timestamp(principal.expires_at)}"
                ),
            )
        if principal.user is None:
            # §3.2 — an agent with no user is a machine credential, and a machine approving an
            # action is the auto-approve §1.1 refuses, reached by configuration rather than by a
            # flag. The startup check (§3.2) does not make this redundant: a provider configured
            # with --user-header still returns user=None for a request that did not carry it.
            human_code, human_token, human_status = NOT_A_HUMAN
            raise _Refused(
                human_code,
                human_token,
                human_status,
                "this tool records an answer under a person's name, and the credential "
                "offered names an agent and no user",
                log=f"the credential names the agent {principal.agent!r} and no human",
            )
        return principal

    def _attribution(self, principal: Principal) -> str:
        """§5.4 — `mcp-operator:<user>`, which §3.2 guarantees is not `None`."""
        assert principal.user is not None
        return _ATTRIBUTION.format(user=principal.user)

    # --- the read tools (§4.4) ------------------------------------------------------------

    def _read(self, tool: _Tool, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if tool.name == "list_pending_approvals":
            return {"pending": self._pending(int(arguments.get("limit", _DEFAULT_PENDING_LIMIT)))}
        if tool.name == "inspect_action":
            return self._inspect(str(arguments["action_id"]))
        if tool.name == "receipts":
            return self._receipts(
                int(arguments.get("limit", _DEFAULT_RECEIPT_LIMIT)),
                arguments.get("control"),
            )
        if tool.name == "effects":
            return self._effects(arguments.get("state"))
        return self._stats(arguments.get("since"))

    def _pending(self, limit: int) -> list[dict[str, Any]]:
        """§4.4 — a filter over what the store already exposes, not a new store method.

        `StateStore.pending_approvals()` would be a new row on a protocol three backends
        implement and a new case in the store conformance suite (`v0.6 §2`), for a read that
        composes from `events()` and `get_approval()`. The cost is that the walk is linear in
        the event log, and that is stated rather than hidden.
        """
        now = self._clock()
        found: list[dict[str, Any]] = []
        seen: set[str] = set()
        for event in self.store.events():
            if event.type is not EventType.APPROVAL_REQUESTED or not event.approval_id:
                continue
            if event.approval_id in seen:
                continue
            seen.add(event.approval_id)
            record = self.store.get_approval(event.approval_id)
            if record is None or record.status is not ApprovalStatus.PENDING:
                continue
            if now > record.expires_at:
                # §4.4 — the store marks a request `expired` only when somebody tries to answer
                # it, so a listing that trusted the stored status would offer an approver a
                # request that refuses the moment they answer it (T186).
                continue
            found.append(self._pending_entry(record, now))
            if len(found) >= limit:
                break
        return found

    def _pending_entry(self, record: ApprovalRecord, now: datetime) -> dict[str, Any]:
        action = record.request.action
        entry: dict[str, Any] = {
            "request_id": record.approval_id,
            "action_id": action.action_id,
            "action": action.name,
            "action_hash": record.action_hash,
            "arguments": dict(action.canonical_arguments),
            "resource": action.resource,
            "environment": action.environment,
            # §4.4 — `agent` and `user`, and **not** claims. A claim can hold an employee
            # number, a case id or a licence, and this listing is rendered by a third-party
            # assistant into somebody's chat history. `inspect_action` carries them, because
            # that document is the evidence record and an approver has asked for it; the
            # listing is a queue.
            "principal": {"agent": action.principal.agent, "user": action.principal.user},
            "created_at": iso_timestamp(record.request.created_at),
            "expires_at": iso_timestamp(record.expires_at),
            "expires_in_seconds": max(0, int((record.expires_at - now).total_seconds())),
        }
        if record.request.policy_hash is not None:
            entry["policy_hash"] = record.request.policy_hash
        return entry

    def _inspect(self, action_id: str) -> dict[str, Any]:
        """§9.1 — the same producer `ctrlrun inspect --json` uses, choosing included. T193
        asserts equality, for an action with a receipt and for one still awaiting a human."""
        document = inspection_for(self.store, action_id)
        if document is None:
            raise _Refused(_INVALID_PARAMS, "ctrlrun.unknown_action", 200, f"no action {action_id}")
        return document

    def _receipts(self, limit: int, control_id: object) -> dict[str, Any]:
        found = self.store.receipts()
        if control_id is not None:
            # `v0.6 §7.3` — a filter and not a lookup, exactly as `ctrlrun receipts --control`
            # is: an id no document defines matches nothing rather than erroring.
            found = tuple(receipt for receipt in found if str(control_id) in receipt.controls)
        return {"receipts": [receipt.to_dict() for receipt in found[-limit:]]}

    def _effects(self, state: object) -> dict[str, Any]:
        wanted = None
        if state is not None:
            try:
                wanted = EffectState(str(state))
            except ValueError as exc:
                raise _Refused(
                    _INVALID_PARAMS,
                    "ctrlrun.invalid_argument",
                    200,
                    f"state must be one of {', '.join(str(one) for one in EffectState)}",
                ) from exc
        return {"effects": [effect_document(record) for record in self.store.list_effects(wanted)]}

    def _stats(self, since: object) -> dict[str, Any]:
        try:
            boundary = since_boundary(None if since is None else str(since))
        except InvalidArgument as exc:
            raise _Refused(_INVALID_PARAMS, "ctrlrun.invalid_argument", 200, str(exc)) from exc
        counted = [
            receipt
            for receipt in self.store.receipts()
            if boundary is None or receipt.finished_at >= boundary
        ]
        # §9.1 — one producer for `ctrlrun.stats/v1`. T193 asserts equality with the CLI's.
        return stats_document(counted, mode=self._control.policy.mode, boundary=boundary)

    # --- the write tools (§4.5) -----------------------------------------------------------

    def _write(
        self, tool: _Tool, arguments: Mapping[str, Any], principal: Principal
    ) -> dict[str, Any]:
        who = self._attribution(principal)
        if tool.name == "approve":
            return self._approve(str(arguments["request_id"]), who, principal)
        if tool.name == "deny":
            return self._deny(str(arguments["request_id"]), who, principal)
        return self._resolve_effect(arguments, who)

    def _approve(self, request_id: str, who: str, principal: Principal) -> dict[str, Any]:
        """The two calls `ctrlrun approve` makes, in the same order (§4.5).

        The record's `action_hash` is whatever was stored when the request was created (`v0.1
        §4.1`): there is no argument by which a caller could approve a different action than the
        one the request names, which is why the mutation case is a test of the kernel reached
        through this server (T187).
        """
        store = self.store
        record = store.get_approval(request_id)
        # SPEC-v0.8 §2.6: **this server is the surface that can do this**, and until now it
        # resolved a principal for every request and then discarded it into the string `who`.
        # The principal its own provider verified is recorded beside that string, so an
        # approval granted here is consumable in a deployment that checks (§2.7). It is recorded
        # whether or not the deployment checks, because it is true either way.
        #
        # SPEC-v0.8 §3.8: and the entitlement the request pinned is computed here, where the
        # credential is, and refused here where it is not held. This half is the **courtesy**: a
        # human learns at the moment they answer rather than at the moment an agent retries. The
        # guarantee is `Control`'s check at consumption, which reads what this recorded.
        required = () if record is None else record.request.required_roles
        held = roles_held(principal, self._roles_claim())
        entitled = entitled_controls(required, held)
        missing = unsatisfied(required, entitled)
        if missing is not None:
            # §3.7: named in the operator's log as well as in the answer, because the answer
            # goes to the person who was refused and this goes to whoever configured the claim.
            _LOG.warning(
                "%s answered %s without the role %r that control %r requires; roles were read "
                "from the claim %r and held %s",
                principal.agent,
                request_id,
                missing.role,
                missing.control,
                self._roles_claim(),
                sorted(held) or "none",
            )
            raise _Refused(
                _NOT_ENTITLED,
                "ctrlrun.not_entitled",
                403,
                f"answering this request needs the role {missing.role!r}, required by control "
                f"{missing.control!r}, and the credential presented does not carry it",
            )
        with _granting_principal(principal, entitled=entitled):
            approval = store.grant_approval(request_id, who)
        # `grant_approval` refuses an unknown id (`v0.1 §4.1`, `check_answerable`) and nothing
        # deletes an approval row, so the record exists by here. This is an invariant check and
        # not a guard against a caller: the previous spelling, `if record is not None:`, was a
        # branch that could not be False and so was documentation rather than defence.
        if record is None:  # pragma: no cover - grant_approval refused an unknown id above
            raise _Refused(
                _INTERNAL_ERROR,
                "ctrlrun.internal_error",
                500,
                f"{request_id} was granted and then could not be read back",
            )
        if approval is None:
            # SPEC-v0.8 §4.4: recorded, still short of N. No `APPROVAL_GRANTED`, because nothing
            # was granted; the answer is a fact the caller needs and not an event about a grant.
            after = store.get_approval(request_id)
            return {
                "status": "pending",
                "request_id": request_id,
                "approvals_required": record.request.approvals_required,
                "approvals_recorded": 0 if after is None else len(after.approvers),
            }
        store.append_event(
            self._event(
                EventType.APPROVAL_GRANTED,
                record.request.action.action_id,
                approval_id=request_id,
                approver=approval.approver,
                action_hash=approval.action_hash,
            )
        )
        return {
            "status": "granted",
            "request_id": request_id,
            "action_hash": approval.action_hash,
            "approver": approval.approver,
            "expires_at": iso_timestamp(approval.expires_at),
        }

    def _deny(self, request_id: str, who: str, principal: Principal) -> dict[str, Any]:
        store = self.store
        record = store.get_approval(request_id)
        with _granting_principal(principal):
            store.deny_approval(request_id, who)
        if record is None:  # pragma: no cover - deny_approval refused an unknown id above
            raise _Refused(
                _INTERNAL_ERROR,
                "ctrlrun.internal_error",
                500,
                f"{request_id} was denied and then could not be read back",
            )
        store.append_event(
            self._event(
                EventType.APPROVAL_DENIED,
                record.request.action.action_id,
                approval_id=request_id,
                approver=who,
            )
        )
        return {"status": "denied", "request_id": request_id, "approver": who}

    def _resolve_effect(self, arguments: Mapping[str, Any], who: str) -> dict[str, Any]:
        """§4.5 — and this one requires a reason, where `ctrlrun resolve` does not.

        `v0.1 §5.2` makes a resolution a human's claim about the real world, and the CLI's
        version is typed by somebody who has just looked at the remote system. A resolution
        arriving through an assistant has a conversation behind it and no record of it, so the
        reason is the record.
        """
        outcome = str(arguments["outcome"])
        if outcome not in RESOLUTIONS:
            raise _Refused(
                _INVALID_PARAMS,
                "ctrlrun.invalid_argument",
                200,
                f"outcome must be one of {', '.join(sorted(RESOLUTIONS))}",
            )
        reason = str(arguments["reason"]).strip()
        if not reason:
            raise _Refused(
                _INVALID_PARAMS,
                "ctrlrun.invalid_argument",
                200,
                "reason must say what was checked and what it showed; a resolution is a "
                "human's claim about the real world (SPEC-mcp-operator §4.5)",
            )
        effect_key = str(arguments["effect_key"])
        state = EffectState.COMMITTED if outcome == "committed" else EffectState.FAILED
        store = self.store
        record = store.resolve_effect(effect_key, state, who)
        store.append_event(
            self._event(
                EventType.EFFECT_RESOLVED,
                record.action_id,
                effect_key=effect_key,
                state=str(record.state),
                resolver=who,
                resolved_by=RESOLVED_BY_HUMAN,
                reason=reason,
            )
        )
        return {
            "effect_key": effect_key,
            "state": str(record.state),
            "resolver": who,
            "reason": reason,
            "retry_permitted": record.state is EffectState.FAILED,
        }

    def _event(
        self,
        type_: EventType,
        action_id: str,
        *,
        effect_key: str | None = None,
        approval_id: str | None = None,
        **data: object,
    ) -> Event:
        """One event for something a human did through this server (§5.2).

        `via` on every one. It is a key in an existing event's open data mapping (`v0.1 §6.2`),
        not a new event type: `approver` already says who, and this says through what.
        """
        return Event(
            type=type_,
            action_id=action_id,
            ts=self._clock(),
            data={**data, "via": VIA},
            effect_key=effect_key,
            approval_id=approval_id,
        )


def _checked(tool: _Tool, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Every argument present, of the declared type, and nothing else (§7).

    Hand-written rather than a JSON Schema validator, because the extra would be a third-party
    dependency in the one place this repository does not add them, and because eight tools with
    three scalar types between them is not a schema problem.
    """
    for name in tool.required:
        if name not in arguments:
            raise _Refused(
                _INVALID_PARAMS,
                "ctrlrun.invalid_argument",
                200,
                f"{tool.name} needs a {name!r} argument",
            )
    for name, value in arguments.items():
        declared = tool.properties.get(name)
        if declared is None:
            raise _Refused(
                _INVALID_PARAMS,
                "ctrlrun.invalid_argument",
                200,
                f"{tool.name} takes no argument named {name!r}",
            )
        expected = declared["type"]
        # `bool` is not an integer here, for `v0.1 §3.2`'s reason: True and 1 are different
        # values, and a limit of `true` is a limit nobody asked for.
        ok = (
            isinstance(value, str)
            if expected == "string"
            else isinstance(value, int) and not isinstance(value, bool)
        )
        if not ok:
            raise _Refused(
                _INVALID_PARAMS,
                "ctrlrun.invalid_argument",
                200,
                f"{tool.name}'s {name!r} must be a {expected}",
            )
        if expected == "integer" and not (declared["minimum"] <= value <= declared["maximum"]):
            raise _Refused(
                _INVALID_PARAMS,
                "ctrlrun.invalid_argument",
                200,
                f"{tool.name}'s {name!r} must be between {declared['minimum']} and "
                f"{declared['maximum']}",
            )
    return arguments


def _listed(tool: _Tool) -> dict[str, Any]:
    return {"name": tool.name, "description": tool.description, "inputSchema": tool.schema()}


def _content(result: Mapping[str, Any]) -> dict[str, Any]:
    """One `tools/call` result: the document, and the same document as text.

    `structuredContent` is what a client reads; the `content` block is what an assistant with
    no structured-output support renders. One object, serialized once, so the two cannot
    disagree.
    """
    text = json.dumps(result, ensure_ascii=False, indent=2)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": dict(result),
        "isError": False,
    }


def _ok(rpc_id: Any, result: Mapping[str, Any]) -> _Response:
    return _json(200, {"jsonrpc": "2.0", "id": rpc_id, "result": dict(result)})


def _error(rpc_id: Any, code: int, token: str, status: int, message: str, **data: Any) -> _Response:
    """A refusal is a JSON-RPC error, never a result with `isError: true` (`v0.2 §6.10`).

    `isError` reaches the model as text, and a refusal to let a human's assistant do something
    is not a tool result.
    """
    return _json(status, json_rpc_error(rpc_id, code, token, message, **data))


def _request_id(body: bytes) -> Any:
    try:
        document = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    return document.get("id") if isinstance(document, dict) else None


def _version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("ctrlrun")
    except PackageNotFoundError:  # pragma: no cover - not an installed distribution
        return "0"


# --- the listening side (stdlib) -----------------------------------------------------------


def build_operator_server(server: OperatorServer) -> ThreadingHTTPServer:
    """A `ThreadingHTTPServer` bound to the configured loopback address (§2.1)."""
    config = server.config

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:
            if self.path.rstrip("/") != config.path.rstrip("/"):
                self.send_error(404)
                return
            declared = self.headers.get("Content-Length")
            try:
                length = int(declared) if declared is not None else 0
            except ValueError:
                # A length this server cannot read is a body it cannot bound, and an unbounded
                # read on a surface whose read tools need no credential (§4.1) is a local
                # process able to exhaust the console at the moment approvals need answering.
                self._respond(_Response(400))
                return
            if length < 0:
                # `-1` used to pass the size comparison below and reach `rfile.read(-1)`, which
                # reads to EOF: the limit bounded the decision and not the allocation. A review
                # found it. 400 and not 413 — a negative length is malformed, not too large.
                self._respond(_Response(400))
                return
            if length > config.max_body_bytes:
                self._respond(_Response(413))
                return
            body = self.rfile.read(length)
            self._respond(
                server.handle(
                    body,
                    dict(self.headers.items()),
                    # `v0.3 §3.1` — the pairs, before `dict()` collapses a repeated field to one
                    # value. Under an authority model that collapse picks the principal, and a
                    # proxy that appends rather than overwrites is a common default.
                    raw=list(self.headers.items()),
                )
            )

        def _respond(self, response: _Response) -> None:
            self.send_response(response.status)
            for key, value in response.headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(response.body)))
            self.end_headers()
            if response.body:
                self.wfile.write(response.body)

        def log_message(self, format: str, *args: object) -> None:
            # Escaped, not interpolated raw: `format % args` is the client's request line, and
            # a newline in it forges a whole record in a line-per-record log (`wire.printable`).
            _LOG.debug("%s - %s", self.address_string(), printable(format % args))

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True
        # `ThreadingHTTPServer` inherits `AF_INET`, so an IPv6 host raises `gaierror` at bind —
        # an `OSError`, which the CLI's `except (ValueError, CTRLRunError)` does not catch, so
        # `--listen ::1:8901` exited with a traceback. A review found it: the config accepted
        # the host and a test asserted the acceptance, and nothing had ever bound it.
        address_family = _family(config.host)

    return Server((_bind_host(config.host), config.port), Handler)


def _family(host: str) -> int:
    """`AF_INET6` for the IPv6 loopback spellings, `AF_INET` otherwise (§2.1)."""
    return socket.AF_INET6 if host in _IPV6_LOOPBACK else socket.AF_INET


def _bind_host(host: str) -> str:
    """The host as `socket` wants it: `[::1]` is what an operator types, `::1` is what binds."""
    return host[1:-1] if host.startswith("[") and host.endswith("]") else host


def _repeated_identity_header(
    config: OperatorConfig, pairs: Iterable[tuple[str, str]]
) -> str | None:
    """The name of an identity header that appeared more than once, or `None` (`v0.3 §3.1`)."""
    watched = {
        name.lower()
        for name in (
            config.principal_header,
            config.user_header,
            config.identity_jwt_header if config.identity_jwt else None,
        )
        if name is not None
    }
    if not watched:
        return None
    seen: set[str] = set()
    for key, _ in pairs:
        lowered = key.lower()
        if lowered in watched and lowered in seen:
            return key
        seen.add(lowered)
    return None


def serve_operator_forever(server: OperatorServer) -> None:
    """Run until interrupted. `ctrlrun mcp-operator` calls this."""
    httpd = build_operator_server(server)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        thread.join()
    finally:
        httpd.shutdown()
        httpd.server_close()
