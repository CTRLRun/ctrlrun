"""The gateway's HTTP server. Build-list item 6c; SPEC-v0.2 §6.1, §6.3, §6.5-§6.8, §6.10.

An MCP client points here instead of at the tool server. `tools/call` becomes an Action -
decided, reserved, executed and recorded exactly as `@protect` does it — and every other
method is relayed unchanged. No agent changes.

The shape worth noticing is how §6.8 reaches the kernel. It is not a second outcome model:
the executor translates the table into v0.1 §5.5's own vocabulary — return for `COMMITTED`,
raise `NotExecuted` for `FAILED`, raise anything else for `AMBIGUOUS` — and `Control` then
applies the rules it has always applied. The gateway does not get its own way of deciding
what happened.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Final, Protocol, TypeAlias

from ..action import Action, Principal
from ..control import Control
from ..effect import EffectState, resolve_effect_key, resolve_resource
from ..errors import (
    ActionDenied,
    AmbiguousEffect,
    ApprovalMismatch,
    ApprovalRequired,
    CTRLRunError,
    DuplicateEffect,
    EffectKeyError,
    InvalidArgument,
    NotExecuted,
    Suspended,
)
from ..receipt import Receipt
from .mcp import DEFAULT_MAX_BODY_BYTES, ParsedRequest, Refusal, parse_request
from .outcome import (
    GatewayOutcome,
    Observed,
    Transport,
    UpstreamError,
    UpstreamResult,
    UpstreamStatus,
    classify,
)

_LOG = logging.getLogger("ctrlrun.gateway")

#: A JSON-RPC id is a string, a number or null, and the gateway echoes back whatever it was
#: given rather than normalizing it.
JsonRpcId: TypeAlias = str | int | float | None

#: SPEC-v0.2 §6.1 — loopback unless an operator says otherwise, per the transport's own
#: "when running locally, servers SHOULD bind only to localhost".
DEFAULT_LISTEN: Final = ("127.0.0.1", 8900)
DEFAULT_PATH: Final = "/mcp"

#: Reserved for the webhook approval endpoint (§7.2). The MCP path may not overlap it.
CTRLRUN_PREFIX: Final = "/ctrlrun/"
APPROVALS_PATH: Final = "/ctrlrun/approvals/"

DEFAULT_UPSTREAM_TIMEOUT: Final = 30.0
DEFAULT_APPROVAL_TIMEOUT: Final = 900.0

#: SPEC-v0.2 §6.9.2's two bounds. A held reservation is a resource an upstream must not be
#: able to pin forever.
DEFAULT_ELICITATION_TIMEOUT: Final = 300.0
DEFAULT_MAX_ELICITATION_ROUNDS: Final = 8

#: §6.6 — the alias boundary in an action name must be unambiguous even though tool names
#: may contain dots, so the alias may not.
ALIAS_PATTERN: Final = r"^[a-z0-9][a-z0-9_-]*$"

#: SPEC-v0.2 §6.10, frozen in §11.
DENIED: Final = (-41001, "ctrlrun.denied", 403)
APPROVAL_REQUIRED: Final = (-41002, "ctrlrun.approval_required", 403)
APPROVAL_DENIED: Final = (-41003, "ctrlrun.approval_denied", 403)
DUPLICATE_EFFECT: Final = (-41004, "ctrlrun.duplicate_effect", 409)
AMBIGUOUS_EFFECT: Final = (-41005, "ctrlrun.ambiguous_effect", 409)
BLOCKED: Final = (-41006, "ctrlrun.blocked", 409)
NO_PRINCIPAL: Final = (-41007, "ctrlrun.no_principal", 403)
UNREPRESENTABLE: Final = (-41008, "ctrlrun.unrepresentable_argument", 400)
UNKNOWN_CONTINUATION: Final = (-41009, "ctrlrun.unknown_continuation", 400)
UPSTREAM_AMBIGUOUS: Final = (-41010, "ctrlrun.upstream_ambiguous", 502)

#: §6.8 — the `_meta` key every intercepted response carries, so a client is not left
#: guessing what CTRLRun recorded. `com.ctrlrun/` is a legal prefix under the revision's
#: key-naming rules, and `_meta` on a result is not validated against a tool's outputSchema.
RECEIPT_META_KEY: Final = "com.ctrlrun/receipt"

#: §6.6 — the decision reason recorded when an argument cannot be represented.
UNREPRESENTABLE_REASON: Final = "unrepresentable_argument"

#: §6.9.3 — an upstream that elicits without a `requestState` cannot be fronted for a
#: consequential tool without a `ctrlrun resolve` per call. Stated as a cost, not a feature:
#: the protocol minted no identity for that exchange, and the only thing distinguishing a
#: continuation from a fresh duplicate call is a field the agent controls completely.
_STATELESS_ELICITATION: Final = GatewayOutcome(
    EffectState.AMBIGUOUS, code=-41010, token="ctrlrun.upstream_ambiguous", http_status=502
)


def _request_state(payload: bytes | None) -> str | None:
    """The `requestState` an `input_required` result carried, if it carried one (§6.9.1)."""
    if payload is None:
        return None
    try:
        document = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return None
    result = document.get("result") if isinstance(document, dict) else None
    state = result.get("requestState") if isinstance(result, Mapping) else None
    return state if isinstance(state, str) and state else None


class UpstreamAmbiguous(CTRLRunError):
    """What the executor raises where §6.8 says the outcome is unknown.

    Any exception that is not `NotExecuted` is an `AMBIGUOUS` outcome to `Control` (v0.1
    §5.5), so this needs no special handling anywhere — it exists to carry the synthesized
    response back to the client, not to change what the kernel does with it.
    """

    def __init__(self, outcome: GatewayOutcome) -> None:
        super().__init__(str(outcome.token))
        self.outcome = outcome


@dataclass(frozen=True)
class GatewayConfig:
    """Everything `ctrlrun gateway` was started with (SPEC-v0.2 §11)."""

    upstream: str
    alias: str
    host: str = DEFAULT_LISTEN[0]
    port: int = DEFAULT_LISTEN[1]
    path: str = DEFAULT_PATH
    principal: str | None = None
    principal_header: str | None = None
    user_header: str | None = None
    wait_approvals: bool = False
    approval_timeout: float = DEFAULT_APPROVAL_TIMEOUT
    upstream_timeout: float = DEFAULT_UPSTREAM_TIMEOUT
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    allow_origins: tuple[str, ...] = ()
    allow_remote: bool = False
    elicitation_timeout: float = DEFAULT_ELICITATION_TIMEOUT
    max_elicitation_rounds: int = DEFAULT_MAX_ELICITATION_ROUNDS
    webhook_secret: str | None = None
    replay_window: float = 300.0

    def __post_init__(self) -> None:
        import re

        if not re.match(ALIAS_PATTERN, self.alias):
            raise InvalidArgument(
                f"--alias {self.alias!r} must match {ALIAS_PATTERN} — no dots, so the alias "
                "boundary in 'mcp.<alias>.<tool>' stays unambiguous (SPEC-v0.2 §6.6)"
            )
        if not self.path.startswith("/") or self.path.rstrip("/").startswith(
            CTRLRUN_PREFIX.rstrip("/")
        ):
            raise InvalidArgument(
                f"--path {self.path!r} must start with '/' and must not overlap "
                f"{CTRLRUN_PREFIX!r}, which is reserved for the approvals endpoint"
            )
        sources = [self.principal is not None, self.principal_header is not None]
        if sum(sources) != 1:
            raise InvalidArgument(
                "exactly one of --principal or --principal-header is required; there is no "
                "default, because an Action cannot exist without a principal (SPEC-v0.2 §6.5)"
            )
        if self.user_header is not None and self.principal_header is None:
            # SPEC-v0.3 §8.2 — a flag that cannot take effect is a flag the operator believes
            # took effect.
            raise InvalidArgument(
                "--user-header is only meaningful with --principal-header; with --principal "
                "there is no request to read a user from"
            )
        if self.host not in ("127.0.0.1", "localhost", "::1") and not self.allow_remote:
            raise InvalidArgument(
                f"--listen {self.host} is not loopback; pass --allow-remote to mean it"
            )


@dataclass
class _Response:
    """What goes back to the client."""

    status: int
    body: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)


def json_rpc_error(rpc_id: Any, code: int, token: str, message: str, **data: Any) -> dict[str, Any]:
    """One JSON-RPC error object in §6.10's shape.

    A refusal by CTRLRun is not an outcome of the tool; it is the statement that the tool did
    not run. `isError: true` would be indistinguishable from the tool's own failure, and it
    reaches the model as text — which is not where a policy denial belongs.
    """
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "error": {"code": code, "message": message, "data": {"error": token, **data}},
    }


class Forwarder(Protocol):
    """How a gateway reaches its upstream. A Protocol so tests can substitute one."""

    def __call__(
        self, body: bytes, headers: Mapping[str, str], *, fresh: bool
    ) -> tuple[Observed, bytes | None, int, Mapping[str, str]]: ...


class Gateway:
    """One upstream, one alias, one `Control` (SPEC-v0.2 §6.1).

    Several upstreams means several processes: a single process multiplexing them would have
    to choose an upstream per request from something in the request, and every candidate for
    that something is agent-controlled.
    """

    def __init__(self, config: GatewayConfig, control: Control, forwarder: Forwarder) -> None:
        self._config = config
        self._control = control
        self._forward = forwarder

    @property
    def config(self) -> GatewayConfig:
        return self._config

    # --- the request path ---------------------------------------------------------------

    def handle_approval(
        self, request_id: str, body: bytes, headers: Mapping[str, str]
    ) -> _Response:
        """`POST /ctrlrun/approvals/<request_id>` (SPEC-v0.2 §7.2).

        The gateway serves it because it is the one server this release ships. Everything the
        endpoint decides lives in `webhook.handle_inbound`, which is core: this is the socket
        and nothing else.
        """
        from ..webhook import SIGNATURE_HEADER, handle_inbound

        if self._config.webhook_secret is None:
            _LOG.warning("an inbound approval arrived but no webhook secret is configured")
            return _Response(404)
        status, message = handle_inbound(
            self._control.store,
            request_id,
            body,
            _header(headers, SIGNATURE_HEADER) or "",
            secret=self._config.webhook_secret,
            replay_window=timedelta(seconds=self._config.replay_window),
        )
        return _json(status, {"status": "ok" if status == 200 else "refused", "detail": message})

    def handle(self, body: bytes, headers: Mapping[str, str]) -> _Response:
        """Decide one POST. Returns what the client gets, and records what happened."""
        origin = _header(headers, "origin")
        if origin is not None and origin not in self._config.allow_origins:
            # §6.1 — the transport requires Origin validation against DNS rebinding, and an
            # empty allowlist that accepted every origin would be validation in name only.
            _LOG.warning("refused a request from origin %r", origin)
            return _Response(403)

        parsed = parse_request(body, headers, max_body_bytes=self._config.max_body_bytes)
        if isinstance(parsed, Refusal):
            _LOG.warning("refused a request: %s", parsed.message)
            return self._refusal(parsed, _request_id(body))
        if not parsed.intercept:
            # §6.3 — every other method is relayed, and has no CTRLRun outcome at all. No
            # Action, no policy, no reservation, no receipt. `tools/list` is not an action.
            return self._relay(parsed, headers)
        return self._intercept(parsed, headers)

    def _refusal(self, refusal: Refusal, request_id: JsonRpcId) -> _Response:
        if refusal.code is None:
            return _Response(refusal.http_status)
        document = {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": refusal.code, "message": refusal.message},
        }
        return _json(refusal.http_status, document)

    def _relay(self, parsed: ParsedRequest, headers: Mapping[str, str]) -> _Response:
        observed, payload, status, response_headers = self._forward(
            json.dumps(parsed.document).encode(), headers, fresh=False
        )
        if payload is None:
            _LOG.warning("relaying %s failed: %s", parsed.method, observed)
            return _Response(502)
        return _Response(status, payload, response_headers)

    def _intercept(self, parsed: ParsedRequest, headers: Mapping[str, str]) -> _Response:
        request_id = parsed.document.get("id")
        principal = self._principal(parsed, headers)
        if principal is None:
            # §6.5 — no principal to attribute the refusal to, so it does not belong in the
            # evidence log; and it must not be silent either.
            _LOG.warning("refused %s: no principal derivable from the request", parsed.tool_name)
            code, token, status = NO_PRINCIPAL
            return _json(
                status,
                json_rpc_error(request_id, code, token, "no principal could be derived"),
            )

        action_name = f"mcp.{self._config.alias}.{parsed.tool_name}"
        try:
            action = self._action(action_name, parsed, principal)
        except _Unrepresentable as refused:
            self._record_unrepresentable(action_name, parsed, principal, refused.pointer)
            code, token, status = UNREPRESENTABLE
            return _json(
                status,
                json_rpc_error(request_id, code, token, str(refused), pointer=refused.pointer),
            )
        except (InvalidArgument, EffectKeyError) as refused:
            _LOG.warning("refused %s: %s", action_name, refused)
            code, token, status = DENIED
            return _json(status, json_rpc_error(request_id, code, token, str(refused)))

        return self._execute(action, parsed, headers, request_id)

    def _principal(self, parsed: ParsedRequest, headers: Mapping[str, str]) -> Principal | None:
        """§6.5 — exactly one source, configured at startup, and no default."""
        agent: str | None = None
        if self._config.principal is not None:
            agent = self._config.principal
        else:
            assert self._config.principal_header is not None
            agent = _header(headers, self._config.principal_header.lower())
        if not isinstance(agent, str) or not agent:
            return None
        user = (
            _header(headers, self._config.user_header.lower())
            if self._config.user_header is not None
            else None
        )
        return Principal(agent=agent, user=user or None)

    def _action(self, action_name: str, parsed: ParsedRequest, principal: Principal) -> Action:
        """§6.6 — the Action a `tools/call` becomes.

        `params._meta` is deliberately not part of it: it carries the protocol version,
        clientInfo, a progress token and trace context, all volatile, and including it would
        change the action hash between two identical tool calls and void every approval.
        """
        from .mcp import find_unrepresentable

        pointer = find_unrepresentable(dict(parsed.arguments))
        if pointer is not None:
            raise _Unrepresentable(pointer)
        resource_template = self._control.policy.resource_template(action_name)
        return Action(
            name=action_name,
            arguments=dict(parsed.arguments),
            principal=principal,
            resource=(
                None
                if resource_template is None
                else resolve_resource(resource_template, parsed.arguments)
            ),
            # SPEC-v0.3 §2.5 — the Control's, never a second copy of the gateway's own.
            environment=self._control.environment,
        )

    def _record_unrepresentable(
        self, action_name: str, parsed: ParsedRequest, principal: Principal, pointer: str
    ) -> None:
        """§6.6 — recorded like v0.1 §5.1's effect_key_error: there is a principal, so the
        refusal belongs in the evidence log, and it never reaches the policy."""
        from ..policy import Decision, Evaluation
        from ..receipt import EventType, ReceiptResult

        arguments = {key: str(value) for key, value in parsed.arguments.items()}
        action = Action(
            name=action_name,
            arguments=arguments,
            principal=principal,
            environment=self._control.environment,
        )
        control = self._control
        control._append(EventType.ACTION_PROPOSED, action, {"action_hash": action.action_hash})
        control._append(
            EventType.ACTION_DENIED,
            action,
            {"reason": UNREPRESENTABLE_REASON, "pointer": pointer},
        )
        control._record(
            action,
            Evaluation(Decision.DENY, UNREPRESENTABLE_REASON),
            ReceiptResult.DENIED,
            control._clock(),
            error=f"{pointer} is a float, which an Action cannot represent",
        )

    def _execute(
        self,
        action: Action,
        parsed: ParsedRequest,
        headers: Mapping[str, str],
        request_id: JsonRpcId,
    ) -> _Response:
        """Decide, forward and record — through `Control`, not around it."""
        effect_template = self._control.policy.effect_template(action.name)
        try:
            effect_key = (
                None if effect_template is None else resolve_effect_key(effect_template, action)
            )
        except EffectKeyError as refused:
            _LOG.warning("refused %s: %s", action.name, refused)
            code, token, status = DENIED
            return _json(status, json_rpc_error(request_id, code, token, str(refused)))

        options = self._control.policy.mcp_options(action.name)
        held: dict[str, Any] = {}
        presented = parsed.document.get("params", {})
        presented = presented.get("requestState") if isinstance(presented, Mapping) else None

        def executor() -> Any:
            # §6.7 — the request the gateway sends is built from the action's *canonical*
            # arguments, never copied from the received body: what a human approved, and
            # what was hashed, reserved and recorded, is byte-for-byte what the upstream
            # receives. Every other member is carried through unchanged.
            forwarded = dict(parsed.document)
            params = dict(forwarded.get("params", {}))
            params["arguments"] = action.canonical_arguments
            forwarded["params"] = params
            observed, payload, status, response_headers = self._forward(
                json.dumps(forwarded, separators=(",", ":")).encode(), headers, fresh=True
            )
            outcome = classify(observed, not_executed_on_error=options.not_executed_on_error)
            held["payload"] = payload
            held["status"] = status
            held["headers"] = response_headers
            held["outcome"] = outcome
            if outcome.elicitation:
                # §6.9 — neither leg is an outcome. Where the upstream gave a `requestState`
                # the reservation is held across the round trip; where it did not, the
                # protocol minted no identity for the exchange and §6.9.3's floor applies.
                state = _request_state(payload)
                if state is None:
                    raise UpstreamAmbiguous(_STATELESS_ELICITATION)
                raise Suspended(state)
            if outcome.effect is EffectState.COMMITTED:
                return payload
            if outcome.effect is EffectState.FAILED:
                raise NotExecuted(str(outcome.token or observed))
            raise UpstreamAmbiguous(outcome)

        if isinstance(presented, str) and presented:
            return self._continue(action, executor, held, presented, request_id)
        return self._through_control(action, executor, effect_key, held, request_id)

    def _continue(
        self,
        action: Action,
        executor: Callable[[], Any],
        held: dict[str, Any],
        presented: str,
        request_id: JsonRpcId,
    ) -> _Response:
        """A continuation: the same action, the same reservation (SPEC-v0.2 §6.9.2).

        `Control.resume` consumes the continuation atomically and rehydrates the action from
        the store, so a gateway that restarted mid-round can still finish one. The hash check
        is this layer's: a continuation must be the same action, and `resume` cannot know what
        the client just sent.
        """
        code, token, status = UNKNOWN_CONTINUATION
        try:
            receipt = self._control.resume(presented, executor)
        except Suspended:
            key = self._control.policy.effect_template(action.name)
            resolved = None if key is None else resolve_effect_key(key, action)
            over = self._over_the_round_bound(action, resolved, request_id)
            return over or self._upstream_response(action, held, resolved, suspended=True)
        except InvalidArgument as refused:
            _LOG.warning("refused a continuation for %s: %s", action.name, refused)
            return _json(
                status,
                json_rpc_error(request_id, code, token, str(refused), tool=action.name),
            )
        except AmbiguousEffect as refused:
            code, token, status = AMBIGUOUS_EFFECT
            return _json(
                status,
                json_rpc_error(
                    request_id, code, token, str(refused), effect_key=refused.effect_key
                ),
            )
        except (NotExecuted, UpstreamAmbiguous):
            return self._upstream_response(action, held, None)
        return self._upstream_response(action, held, receipt.effect_key, receipt)

    def _through_control(
        self,
        action: Action,
        executor: Callable[[], Any],
        effect_key: str | None,
        held: dict[str, Any],
        request_id: JsonRpcId,
    ) -> _Response:
        from ..control import with_approval

        approval = None
        if self._control.policy.evaluate(action).decision.value == "approve":
            approval = self._control.store.find_granted_approval(action.action_hash)
            if approval is None:
                refusal = self._before_asking_a_human(action, effect_key, request_id)
                if refusal is not None:
                    return refusal
        try:
            if approval is not None:
                with with_approval(approval.approval_id):
                    receipt = self._control.execute(action, executor, effect_key)
            else:
                receipt = self._control.execute(action, executor, effect_key)
        except ActionDenied as refused:
            code, token, status = DENIED
            return _json(
                status,
                json_rpc_error(
                    request_id,
                    code,
                    token,
                    str(refused),
                    reason=refused.reason,
                    action_id=action.action_id,
                ),
            )
        except ApprovalRequired as pending:
            return self._awaiting(action, pending, request_id)
        except DuplicateEffect as refused:
            code, token, status = DUPLICATE_EFFECT
            return _json(
                status,
                json_rpc_error(
                    request_id,
                    code,
                    token,
                    str(refused),
                    effect_key=refused.effect_key,
                    state=refused.state,
                ),
            )
        except AmbiguousEffect as refused:
            code, token, status = AMBIGUOUS_EFFECT
            return _json(
                status,
                json_rpc_error(
                    request_id, code, token, str(refused), effect_key=refused.effect_key
                ),
            )
        except ApprovalMismatch as refused:
            code, token, status = BLOCKED
            return _json(
                status,
                json_rpc_error(request_id, code, token, str(refused), reason=refused.reason),
            )
        except Suspended:
            # §6.9.2 step 5 — the InputRequiredResult is relayed unchanged, plus the receipt
            # meta. No outcome was written and no receipt exists; the reservation is held.
            over = self._over_the_round_bound(action, effect_key, request_id)
            return over or self._upstream_response(action, held, effect_key, suspended=True)
        except (NotExecuted, UpstreamAmbiguous):
            return self._upstream_response(action, held, effect_key)
        return self._upstream_response(action, held, effect_key, receipt)

    def _before_asking_a_human(
        self, action: Action, effect_key: str | None, request_id: JsonRpcId
    ) -> _Response | None:
        """Two reasons not to create another approval request (SPEC-v0.2 §6.10).

        The first is the spec's: an unexpired *denied* request for this hash already exists.
        "No" is an answer, and without this an agent that dislikes a denial resends the call
        and gets a fresh notification to a human, until one of them clicks the wrong button.

        The second the spec leaves open, and this is the fail-closed reading. `# SPEC: §6.10`
        — if the effect key would refuse the action anyway, asking a human to approve it
        wears the same approver down for the same reason, and the answer could not be acted
        on if they said yes. So the refusal the reservation would give is given now, before
        anyone is troubled by it. It only ever refuses more.
        """
        denied = self._control.store.find_denied_request(action.action_hash)
        if denied is not None:
            code, token, status = APPROVAL_DENIED
            return _json(
                status,
                json_rpc_error(
                    request_id,
                    code,
                    token,
                    "a human already refused this exact action; the denial holds until the "
                    "request expires",
                    request_id=denied.request_id,
                ),
            )
        if effect_key is None:
            return None
        record = self._control.store.get_effect(effect_key)
        if record is None:
            return None
        if record.state is EffectState.COMMITTED:
            code, token, status = DUPLICATE_EFFECT
            return _json(
                status,
                json_rpc_error(
                    request_id,
                    code,
                    token,
                    f"effect {effect_key!r} was already committed by {record.action_id}",
                    effect_key=effect_key,
                    state="committed",
                ),
            )
        if record.state is EffectState.AMBIGUOUS:
            code, token, status = AMBIGUOUS_EFFECT
            return _json(
                status,
                json_rpc_error(
                    request_id,
                    code,
                    token,
                    f"effect {effect_key!r} has an unknown outcome from {record.action_id}",
                    effect_key=effect_key,
                ),
            )
        return None

    def _over_the_round_bound(
        self, action: Action, effect_key: str | None, request_id: JsonRpcId
    ) -> _Response | None:
        """§6.9.2's first bound. A held reservation is a resource an upstream must not be able
        to pin forever, and the lease bound only stops a client that stops answering.

        Exceeding it records the effect `AMBIGUOUS` and returns `-41010`: the gateway has sent
        that many tool calls and knows the outcome of none of them.
        """
        if effect_key is None:
            return None
        rounds = self._control.store.continuation_rounds(effect_key)
        if rounds <= self._config.max_elicitation_rounds:
            return None
        record = self._control.store.get_effect(effect_key)
        if record is not None:
            self._control.store.mark_ambiguous(
                effect_key, record.action_id, f"elicitation exceeded {rounds - 1} rounds"
            )
        _LOG.warning("%s: elicitation exceeded the round bound at %s", action.name, rounds)
        code, token, status = UPSTREAM_AMBIGUOUS
        return _json(
            status,
            json_rpc_error(
                request_id,
                code,
                token,
                f"the upstream elicited more than {self._config.max_elicitation_rounds} times "
                "and the outcome of none of those calls is known",
                effect_key=effect_key,
                action_id=action.action_id,
            ),
        )

    def _awaiting(self, action: Action, pending: ApprovalRequired, request_id: Any) -> _Response:
        """§6.10 — "no" is an answer, and re-asking is not free."""
        record = self._control.store.get_approval(pending.request_id)
        code, token, status = APPROVAL_REQUIRED
        data: dict[str, Any] = {"request_id": pending.request_id, "action_hash": action.action_hash}
        if record is not None:
            data["expires_at"] = record.expires_at.isoformat()
        return _json(status, json_rpc_error(request_id, code, token, str(pending), **data))

    def _upstream_response(
        self,
        action: Action,
        held: dict[str, Any],
        effect_key: str | None,
        receipt: Receipt | None = None,
        *,
        suspended: bool = False,
    ) -> _Response:
        """Relay the upstream's own answer, or synthesize one where none arrived (§6.8)."""
        outcome = held.get("outcome")
        meta = {
            "action_id": action.action_id,
            "receipt_id": receipt.receipt_id if receipt is not None else None,
            "effect_key": effect_key,
            "result": (
                "suspended"
                if suspended
                else str(outcome.effect)
                if outcome and outcome.effect
                else "ambiguous"
            ),
            "attempt": receipt.attempt if receipt is not None else 1,
        }
        if outcome is not None and outcome.relay and held.get("payload") is not None:
            document = json.loads(held["payload"])
            document.setdefault("_meta", {})[RECEIPT_META_KEY] = meta
            return _Response(held["status"], _dump(document), held.get("headers", {}))
        if outcome is None:
            code, token, status = AMBIGUOUS_EFFECT
            return _json(status, json_rpc_error(None, code, token, "no upstream outcome"))
        document = json_rpc_error(
            None,
            outcome.code or -41010,
            outcome.token or "ctrlrun.upstream_ambiguous",
            "the upstream's outcome is not known to this gateway",
            effect_key=effect_key,
            action_id=action.action_id,
        )
        document["_meta"] = {RECEIPT_META_KEY: meta}
        return _Response(outcome.http_status or 502, _dump(document))


class _Unrepresentable(CTRLRunError):
    def __init__(self, pointer: str) -> None:
        super().__init__(f"{pointer} is a float, which an Action cannot represent")
        self.pointer = pointer


def _repeated_identity_header(
    config: GatewayConfig, pairs: Iterable[tuple[str, str]]
) -> str | None:
    """The name of an identity header that appeared more than once, or `None` (§3.1).

    Only the headers this gateway's identity configuration actually reads. Every other
    repeated field is the upstream's business, and RFC 9110 says an intermediary forwards what
    it does not recognize.
    """
    watched = {
        name.lower() for name in (config.principal_header, config.user_header) if name is not None
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


def _header(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return None


def _request_id(body: bytes) -> JsonRpcId:
    try:
        document = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    return document.get("id") if isinstance(document, dict) else None


def _dump(document: Mapping[str, Any]) -> bytes:
    return json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()


def _json(status: int, document: Mapping[str, Any]) -> _Response:
    return _Response(status, _dump(document), {"Content-Type": "application/json"})


# --- the transport ----------------------------------------------------------------------


def httpx_forwarder(config: GatewayConfig) -> Any:
    """Forward to the upstream, mapping the client's exceptions onto §6.8's `Transport`.

    **A fresh connection for every intercepted call**, and this is not an optimization
    oversight. "The connection was never established" is the only claim in §6.8 that asserts
    non-execution, and it is provable only if no request byte can have been written — a
    pooled connection the upstream closed while idle fails on *write*, which is
    indistinguishable from a request that arrived. It costs a handshake per consequential
    action, and it buys the only `FAILED` in the table that comes from the transport.
    """
    from . import http_client

    httpx = http_client()
    pooled = httpx.Client(timeout=config.upstream_timeout)

    def forward(
        body: bytes, headers: Mapping[str, str], *, fresh: bool
    ) -> tuple[Any, bytes | None, int, Mapping[str, str]]:
        relayed = {
            key: value
            for key, value in headers.items()
            if key.lower() not in _HOP_BY_HOP and key.lower() != "content-length"
        }
        relayed["Content-Type"] = "application/json"
        client = httpx.Client(timeout=config.upstream_timeout) if fresh else pooled
        try:
            response = client.post(config.upstream, content=body, headers=relayed)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            return Transport.NEVER_CONNECTED, None, 502, {}
        except httpx.TransportError:
            return Transport.AFTER_REQUEST_SENT, None, 502, {}
        finally:
            if fresh:
                client.close()
        return (*_observe(response), response.status_code, dict(response.headers))

    def _observe(response: Any) -> tuple[Observed, bytes | None]:
        challenge = "www-authenticate" in response.headers
        if response.status_code == 401 or (response.status_code == 403 and challenge):
            # §6.8 — the resource server validates the token before dispatch, so nothing
            # reached the tool. This is checked ahead of the body because a peer may answer
            # 401 with any payload it likes, and the status is the part that is load-bearing.
            return UpstreamStatus(
                response.status_code, has_www_authenticate=challenge
            ), response.content
        try:
            document = json.loads(response.content)
        except (ValueError, UnicodeDecodeError):
            document = None
        if not isinstance(document, dict) or document.get("jsonrpc") != "2.0":
            return UpstreamStatus(response.status_code, has_www_authenticate=challenge), None
        if "error" in document:
            code = document["error"].get("code") if isinstance(document["error"], dict) else None
            if not isinstance(code, int):
                return Transport.UNREADABLE_RESPONSE, None
            return UpstreamError(code), response.content
        result = document.get("result")
        if not isinstance(result, Mapping):
            return Transport.UNREADABLE_RESPONSE, None
        return (
            UpstreamResult(
                result_type=result.get("resultType"), is_error=bool(result.get("isError"))
            ),
            response.content,
        )

    forward.close = pooled.close  # type: ignore[attr-defined]
    return forward


_HOP_BY_HOP: Final = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
    }
)


# --- the listening side (stdlib, per §6.11) ---------------------------------------------


def build_server(gateway: Gateway) -> ThreadingHTTPServer:
    """A `ThreadingHTTPServer` bound to the configured address (SPEC-v0.2 §6.11)."""
    config = gateway.config

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:
            if self.path.rstrip("/") != config.path.rstrip("/"):
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > config.max_body_bytes:
                self._respond(_Response(413))
                return
            body = self.rfile.read(length)
            repeated = _repeated_identity_header(gateway.config, self.headers.items())
            if repeated is not None:
                # SPEC-v0.3 §3.1 — `dict(...)` collapses a repeated field to one value, and
                # under an authority model that collapse picks the principal. A proxy that
                # appends rather than overwrites is a common default, so joining its value to
                # the client's is how a client chooses its own identity. Refuse instead.
                _LOG.warning("refused: the identity header %r appeared more than once", repeated)
                code, token, status = NO_PRINCIPAL
                self._respond(
                    _json(
                        status,
                        json_rpc_error(
                            None, code, token, f"the {repeated!r} header appeared more than once"
                        ),
                    )
                )
                return
            self._respond(gateway.handle(body, dict(self.headers.items())))

        def _respond(self, response: _Response) -> None:
            self.send_response(response.status)
            for key, value in response.headers.items():
                if key.lower() in _HOP_BY_HOP or key.lower() == "content-length":
                    continue
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(response.body)))
            self.end_headers()
            if response.body:
                self.wfile.write(response.body)

        def log_message(self, format: str, *args: object) -> None:
            _LOG.debug("%s - %s", self.address_string(), format % args)

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    return Server((config.host, config.port), Handler)


def serve_forever(gateway: Gateway) -> None:
    """Run until interrupted. `ctrlrun gateway` calls this."""
    server = build_server(gateway)
    if gateway.config.host not in ("127.0.0.1", "localhost", "::1"):
        _LOG.warning("listening on %s, which is not loopback", gateway.config.host)
    if gateway.config.principal_header is not None:
        # SPEC-v0.3 §8.3 — the identity in force, on the line that starts the process, with
        # what it costs. A header is worth what the proxy that sets it is worth.
        _LOG.warning(
            "principal from the %r header: it is worth what the proxy that sets it is worth, "
            "and that proxy must authenticate the caller and overwrite the header on every "
            "request (SPEC-v0.3 §3.3)",
            gateway.config.principal_header,
        )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        thread.join()
    finally:
        server.shutdown()
        server.server_close()
