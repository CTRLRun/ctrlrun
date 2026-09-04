"""An ACS control hook backed by CTRLRun. Ships in `ctrlrun[gateway]`.

Read against the Agent Control Standard v0.1.0 schemas in
`GenAI-Security-Project/agent-control-standard` at commit `c7ad162` (2026-08-11):
`specification/v0.1.0/request-envelope.json`, `response-envelope.json`,
`hooks/tool-call-request.json`, `hooks/tool-call-result.json` and `ask-details.json`.
`docs/ACS.md` records what was read and where the two models disagree.

**ACS is advisory; CTRLRun is executing.** A Guardian returns a decision and the *platform*
runs the tool, which is the opposite way round from `@protect`. So one action is split across
two hooks:

- `steps/toolCallRequest` builds the Action, decides it, takes the reservation, and suspends
  holding it — no outcome, no receipt;
- `steps/toolCallResult` closes that reservation with what actually happened.

The two are joined by `Suspended` and `Control.resume` (SPEC-v0.2 §6.9), which exist for
exactly this shape: a reservation held across a round trip the kernel does not control. The
adapter adds no second implementation of v0.1 §5.5's asymmetry; it translates ACS's
`exit_status` vocabulary into the kernel's and lets `Control` decide.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Final

from .action import Action, Principal
from .control import Control, with_approval
from .effect import resolve_effect_key, resolve_resource
from .errors import (
    ActionDenied,
    AmbiguousEffect,
    ApprovalMismatch,
    ApprovalRequired,
    CTRLRunError,
    DuplicateEffect,
    EffectKeyError,
    IdentityError,
    InvalidArgument,
    NotExecuted,
    Suspended,
)
from .identity import IdentityContext, IdentityProvider

_LOG = logging.getLogger(__name__)

#: The ACS revision these mappings were read against.
ACS_VERSION: Final = "0.1.0"

#: The two `steps/*` hooks CTRLRun answers. Every other method is somebody else's checkpoint.
TOOL_CALL_REQUEST: Final = "steps/toolCallRequest"
TOOL_CALL_RESULT: Final = "steps/toolCallResult"

#: `response-envelope.json` — the five decisions ACS defines.
ALLOW: Final = "allow"
DENY: Final = "deny"
ASK: Final = "ask"

#: `tool-call-result.json` — the four statuses ACS defines, and nothing about what any of
#: them means for the side effect. See `docs/ACS.md`.
SUCCESS: Final = "success"
FAILURE: Final = "failure"
TIMEOUT: Final = "timeout"
BLOCKED: Final = "blocked"

#: ACS reserves -32000 to -32099 for itself (`response-envelope.json`).
METHOD_NOT_ANSWERED: Final = -32001
MALFORMED_ENVELOPE: Final = -32002

#: SPEC-v0.3 §8.4 — the reason code on a denial where no principal could be resolved. The
#: same string `v0.1 §2.1` uses, so one vocabulary covers both paths.
NO_PRINCIPAL_CODE: Final = "no_principal"

#: And the reason code where a credential was produced and had already lapsed (§2.3). It is
#: the string `Control` writes on the receipt, so the answer and the evidence agree.
PRINCIPAL_EXPIRED_CODE: Final = "principal_expired"

#: How long an `ask` says a human has. `ask-details.json` requires a positive integer.
DEFAULT_ASK_TIMEOUT_SECONDS: Final = 900


class AcsControlHook:
    """Answer ACS `steps/*` hooks with CTRLRun's decisions and outcomes.

    One `Control`, one prefix. `prefix` names the tool namespace in the action name, the way
    the gateway's `--alias` does: `<prefix>.<provider>.<tool>` — so a policy addresses one
    stable string and two providers exposing the same tool name stay distinguishable.
    """

    def __init__(
        self,
        control: Control,
        *,
        prefix: str = "acs",
        approver_id: str = "cli:local",
        ask_timeout_seconds: int = DEFAULT_ASK_TIMEOUT_SECONDS,
        identity: IdentityProvider | None = None,
    ) -> None:
        if control.authority is not None and identity is None:
            # SPEC-v0.3 §8.4 — without a provider this hook reads `params.metadata.agent_id`
            # straight off the inbound envelope, and §4 makes the principal an authorization
            # input. A self-reported name cannot be one, which is the same sentence that
            # removes the gateway's `--principal-from-client-info` (§8.1); leaving the ACS hook
            # alone would make that removal a gesture. Refused at construction rather than per
            # call: a deployment finds out at startup, not during an incident.
            raise InvalidArgument(
                "AcsControlHook(identity=...) is required for a Control that holds an "
                "Authority. Without one this hook reads params.metadata.agent_id off the "
                "envelope, and an authorization decision may not be made against a principal "
                "the caller asserted (SPEC-v0.3 §8.4)"
            )
        self._control = control
        self._prefix = prefix
        self._approver_id = approver_id
        self._ask_timeout = max(1, ask_timeout_seconds)
        self._identity = identity

    # --- the ACS surface ----------------------------------------------------------------

    def handle(
        self, envelope: Mapping[str, Any], *, headers: Mapping[str, str] | None = None
    ) -> dict[str, Any]:
        """One ACS request envelope in, one response envelope out.

        `headers` is the transport's, and it is what an `identity` provider reads (§8.4): an
        ACS envelope arrives over HTTP, and the credential is in the request rather than in
        the JSON. Optional, because a hook with no provider has nothing to read them with —
        and a hook *with* one and no headers resolves nobody, which is a refusal.
        """
        if (
            not isinstance(envelope, Mapping)
            or envelope.get("jsonrpc") != "2.0"
            or not isinstance(envelope.get("method"), str)
            or not isinstance(envelope.get("params"), Mapping)
        ):
            return _error(None, MALFORMED_ENVELOPE, "not an ACS request envelope")

        method = str(envelope["method"])
        params = envelope["params"]
        rpc_id = envelope.get("id")
        request_id = params.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            return _error(rpc_id, MALFORMED_ENVELOPE, "params.request_id is required")

        try:
            if method == TOOL_CALL_REQUEST:
                return self._on_request(rpc_id, request_id, params, headers or {})
            if method == TOOL_CALL_RESULT:
                return self._on_result(rpc_id, request_id, params)
        except InvalidArgument as refused:
            # A malformed payload is not a decision about an action: there is no action.
            return _error(rpc_id, MALFORMED_ENVELOPE, str(refused))
        except IdentityError as refused:
            # SPEC-v0.3 §8.4 — a credential offered and rejected, or a provider that named
            # nobody. Answered as `deny` rather than as a protocol `error`: an error envelope
            # says "the Guardian could not answer", and a platform is free to decide what to
            # do with that. A denial says what CTRLRun means, which is that the tool must not
            # run.
            #
            # The reason code is **not** always `no_principal`. This clause spans the whole of
            # `_on_request`, so it also catches §2.3's expired-principal refusal from
            # `Control.execute` — which has already written a receipt saying
            # `principal_expired`. Answering `no_principal` there would make the ACS answer
            # and the evidence disagree about the same action, so the code follows the
            # refusal: `principal_expired` where the credential lapsed, `no_principal` where
            # the provider named nobody (and *that* path writes no receipt and no events, for
            # `v0.1 §2.1`'s reason — there is nobody to attribute one to).
            _LOG.warning("refused an ACS envelope: %s", refused)
            return _final(
                rpc_id, request_id, DENY, reasoning=str(refused), codes=[NO_PRINCIPAL_CODE]
            )
        return _error(
            rpc_id,
            METHOD_NOT_ANSWERED,
            f"{method} is not a checkpoint CTRLRun answers; it decides tool calls only",
        )

    # --- steps/toolCallRequest ----------------------------------------------------------

    def _on_request(
        self,
        rpc_id: Any,
        request_id: str,
        params: Mapping[str, Any],
        headers: Mapping[str, str],
    ) -> dict[str, Any]:
        """Decide the call, take the reservation, and hold it for the result hook."""
        payload = _mapping(params.get("payload"), "params.payload")
        action = self._action(params, payload, headers)
        effect_key = self._effect_key(action)

        # SPEC-v0.2 §6.10's rule, in ACS's shape: a client comes back by re-sending the
        # identical call, and the newest granted, unexpired approval for this action's hash
        # is what authorizes it. Matching on the hash is safe for the reason v0.1 §4.2 A1
        # exists — the hash covers the principal, the arguments, the resource and the
        # environment — and it is still single-use, consumed atomically with the reservation.
        # SPEC-v0.3 §8.3 — the **combined** decision of §4.6, not the policy axis alone. One
        # of the three places in shipped v0.2 code that read `Policy.evaluate` directly, named
        # in the spec so this item could not miss it.
        granted = (
            self._control.store.find_granted_approval(action.action_hash)
            if self._control.evaluate(action).decision.value == "approve"
            else None
        )

        def suspend_holding_the_reservation() -> Any:
            # The platform executes, not CTRLRun. `Suspended` is how an executor says the
            # outcome is not known yet *and no outcome should be recorded* (§6.9): the
            # record stays EXECUTING, the lease is extended, and this request_id is what the
            # result hook presents to close it.
            raise Suspended(request_id)

        try:
            if granted is not None:
                with with_approval(granted.approval_id):
                    self._control.execute(action, suspend_holding_the_reservation, effect_key)
            else:
                self._control.execute(action, suspend_holding_the_reservation, effect_key)
        except Suspended:
            return _final(rpc_id, request_id, ALLOW)
        except IdentityError as refused:
            # SPEC-v0.3 §2.3 — the credential lapsed between being verified and being acted
            # on. `Control` has already written a receipt saying `principal_expired`, so the
            # answer says that too: an ACS decision and the evidence for the same action must
            # not disagree about why it was refused.
            #
            # Told apart from "the provider named nobody" **structurally**, not by reading the
            # message: that one is raised by `_action` above, outside this `try`, and is
            # answered by `handle`'s own clause. Two refusals, two places, one code each.
            return _final(
                rpc_id,
                request_id,
                DENY,
                reasoning=str(refused),
                codes=[PRINCIPAL_EXPIRED_CODE],
            )
        except ActionDenied as refused:
            return _final(rpc_id, request_id, DENY, reasoning=str(refused), codes=[refused.reason])
        except ApprovalRequired as pending:
            return self._ask(rpc_id, request_id, action, pending)
        except DuplicateEffect as refused:
            return _final(
                rpc_id,
                request_id,
                DENY,
                reasoning=str(refused),
                codes=["ctrlrun.duplicate_effect", refused.state],
            )
        except AmbiguousEffect as refused:
            return _final(
                rpc_id,
                request_id,
                DENY,
                reasoning=str(refused),
                codes=["ctrlrun.ambiguous_effect"],
            )
        except ApprovalMismatch as refused:
            return _final(
                rpc_id,
                request_id,
                DENY,
                reasoning=str(refused),
                codes=["ctrlrun.blocked", refused.reason],
            )
        # An action with no effect key has nothing to hold: `execute` ran the executor,
        # which suspended, but with no reservation there is nothing for a result hook to
        # close. It is allowed, and it is recorded as unheld (§6.9).
        return _final(rpc_id, request_id, ALLOW)

    def _ask(
        self, rpc_id: Any, request_id: str, action: Action, pending: ApprovalRequired
    ) -> dict[str, Any]:
        """CTRLRun's APPROVE in ACS's shape (`ask-details.json`).

        `approver`, `question` and `timeout_seconds` are all required by the schema, so all
        three are present or the response is not conformant. The question carries the
        request id, because that is what `ctrlrun approve` answers.
        """
        return _final(
            rpc_id,
            request_id,
            ASK,
            reasoning=f"{action.name} requires a human decision under this policy",
            codes=["ctrlrun.approval_required"],
            ask_details={
                "approver": {"type": "human", "id": self._approver_id},
                "question": (
                    f"Approve {action.name} for {action.principal.agent}? "
                    f"Answer with 'ctrlrun approve {pending.request_id}' "
                    f"or 'ctrlrun deny {pending.request_id}'."
                ),
                "timeout_seconds": self._ask_timeout,
                "context": _describe(action),
            },
        )

    # --- steps/toolCallResult -----------------------------------------------------------

    def _on_result(self, rpc_id: Any, request_id: str, params: Mapping[str, Any]) -> dict[str, Any]:
        """Close the reservation the request hook took, with what actually happened.

        ACS describes this hook as an output redaction checkpoint. CTRLRun redacts nothing —
        it records — so the decision is always `allow`; the work is the outcome it writes.
        """
        payload = _mapping(params.get("payload"), "params.payload")
        exit_status = payload.get("exit_status")
        if not isinstance(exit_status, str):
            raise InvalidArgument("payload.exit_status is required")
        held = payload.get("request_id_ref")
        if not isinstance(held, str) or not held:
            # Nothing links this result to a call, so there is no reservation to close and
            # nothing may be written about an effect. Guessing which one it meant is how a
            # duplicate gets committed.
            _LOG.warning("an ACS toolCallResult arrived with no request_id_ref; ignoring")
            return _final(rpc_id, request_id, ALLOW)

        tool = _mapping(payload.get("tool"), "payload.tool")
        name = self._action_name(tool, payload.get("operation"))
        not_executed_on_error = self._control.policy.mcp_options(name).not_executed_on_error

        def report_what_happened() -> Any:
            outcome = _outcome(exit_status, not_executed_on_error)
            if outcome is True:
                return payload.get("outputs")
            if outcome is False:
                raise NotExecuted(f"ACS exit_status={exit_status!r} before dispatch")
            raise _Unknown(f"ACS exit_status={exit_status!r} says nothing about the effect")

        try:
            self._control.resume(held, report_what_happened)
        except (NotExecuted, _Unknown):
            pass
        except InvalidArgument:
            # No held suspension matches. A restarted Guardian, a result fired twice, or one
            # arriving out of order. There is nothing to close, and inventing an outcome for
            # an effect nobody reserved would be worse than recording none.
            _LOG.warning("no held CTRLRun reservation matches ACS request_id_ref %s", held)
        except CTRLRunError as refused:
            _LOG.warning("could not close the reservation for %s: %s", held, refused)
        return _final(rpc_id, request_id, ALLOW)

    # --- building the Action (tool-call-request.json) -----------------------------------

    def _action(
        self,
        params: Mapping[str, Any],
        payload: Mapping[str, Any],
        headers: Mapping[str, str],
    ) -> Action:
        tool = _mapping(payload.get("tool"), "payload.tool")
        name = self._action_name(tool, payload.get("operation"))
        arguments = _arguments(payload.get("arguments"))
        resource_template = self._control.policy.resource_template(name)
        return Action(
            name=name,
            arguments=arguments,
            principal=self._principal(params, name, headers),
            resource=(
                None
                if resource_template is None
                else resolve_resource(resource_template, arguments)
            ),
            # SPEC-v0.3 §2.5, §8.4 — the deployment's, never the envelope's. `environment`
            # is an ACS enum the *caller* sets, and v0.3 makes it an authorization input: a
            # grant may scope to it, so a value off the wire would let the caller choose the
            # environment it is authorized in. §8.4 assigns the rest of this hook's identity
            # amendment to item 5; the environment half lands here because `Control.execute`
            # now refuses an Action from another deployment, and without it a
            # `Control(environment="staging")` fronting ACS would refuse every call.
            environment=self._control.environment,
        )

    def _principal(
        self, params: Mapping[str, Any], action_name: str, headers: Mapping[str, str]
    ) -> Principal:
        """Who is acting, from the provider where there is one (SPEC-v0.3 §8.4).

        Where a provider is configured, `params.metadata.agent_id` is **ignored**: not merged,
        not used as a fallback, and not compared. It is display data, like MCP's `clientInfo`
        — and a value that is only sometimes authoritative is one nobody can reason about.
        `params.metadata.environment` is ignored for the same reason, in `_action`.

        Where none is configured the envelope's own `agent_id` is read, exactly as v0.2 did.
        That is survivable only because a `Control` holding an `Authority` cannot be given to
        this hook without a provider (checked at construction): a self-reported name still
        misattributes a receipt, and still cannot widen an outcome.

        A declining provider is refused rather than backfilled from the envelope. Falling back
        there would reach `agent_id` by an easier route than forging a credential, which is
        the hole §3.2 closes for `context()` and the same hole in a different module.
        """
        metadata = _mapping(params.get("metadata"), "params.metadata")
        if self._identity is not None:
            context = IdentityContext(
                action=action_name,
                environment=self._control.environment,
                headers={name.lower(): value for name, value in headers.items()},
            )
            resolved = self._identity.resolve(context)
            if resolved is None:
                raise IdentityError(
                    f"{action_name}: the identity provider named nobody for this envelope, and "
                    "params.metadata.agent_id is not a fallback (SPEC-v0.3 §8.4)"
                )
            return resolved
        agent = metadata.get("agent_id")
        if not isinstance(agent, str) or not agent:
            raise InvalidArgument("params.metadata.agent_id is required")
        user_context = metadata.get("user_context")
        user = user_context.get("user_id") if isinstance(user_context, Mapping) else None
        return Principal(agent=agent, user=user if isinstance(user, str) else None)

    def _action_name(self, tool: Mapping[str, Any], operation: object) -> str:
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            raise InvalidArgument("payload.tool.name is required")
        provider = tool.get("provider")
        parts = [self._prefix]
        if isinstance(provider, str) and provider:
            parts.append(provider)
        parts.append(name)
        if isinstance(operation, str) and operation:
            # Two verbs on one tool are two actions: a policy must be able to say different
            # things about `create` and `void`.
            parts.append(operation)
        return ".".join(parts)

    def _effect_key(self, action: Action) -> str | None:
        template = self._control.policy.effect_template(action.name)
        if template is None:
            return None
        try:
            return resolve_effect_key(template, action)
        except EffectKeyError as refused:
            raise InvalidArgument(str(refused)) from refused


class _Unknown(CTRLRunError):
    """Anything that is not `NotExecuted` is an AMBIGUOUS outcome to `Control` (v0.1 §5.5)."""


def _outcome(exit_status: str, not_executed_on_error: bool) -> bool | None:
    """ACS's `exit_status` under v0.1 §5.5's asymmetry. `True` committed, `False` provably
    not executed, `None` unknown.

    ACS names four statuses and says nothing about what any of them means for a side effect.
    This is the fail-closed reading, and it is the same one SPEC-v0.2 §6.8 applies to MCP:

    - `success` is the only status that asserts the effect happened;
    - `blocked` is the only one that asserts it did not — a control refused it before
      dispatch, which is what `NotExecuted` means;
    - `failure` and `timeout` are both unknown, because a tool that failed *after* acting and
      one that failed *before* acting report the same string. `not_executed_on_error` (§3.1)
      is where an operator asserts otherwise for a tool they know.
    """
    if exit_status == SUCCESS:
        return True
    if exit_status == BLOCKED:
        return False
    if exit_status == FAILURE and not_executed_on_error:
        return False
    return None


def _arguments(raw: object) -> dict[str, Any]:
    """`{name: {value, provenance}}` → `{name: value}` (`tool-call-request.json`).

    An Action built from the envelopes rather than the values would hash something no policy
    can address and no human can read.
    """
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise InvalidArgument("payload.arguments must be an object")
    arguments: dict[str, Any] = {}
    for name, envelope in raw.items():
        if not isinstance(name, str):
            raise InvalidArgument("argument names must be strings")
        if isinstance(envelope, Mapping) and "value" in envelope:
            arguments[name] = envelope["value"]
        else:
            raise InvalidArgument(f"argument {name!r} must be an object with a 'value'")
    return arguments


def _describe(action: Action) -> str:
    parts = ", ".join(f"{key}={value!r}" for key, value in action.canonical_arguments.items())
    return f"{action.name}({parts}) in {action.environment}"


def _mapping(value: object, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise InvalidArgument(f"{where} must be an object")
    return value


def _final(
    rpc_id: Any,
    request_id: str,
    decision: str,
    *,
    reasoning: str | None = None,
    codes: list[str] | None = None,
    ask_details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One `AcsResult` in `response-envelope.json`'s shape."""
    result: dict[str, Any] = {
        "type": "final",
        "acs_version": ACS_VERSION,
        "request_id": request_id,
        "decision": decision,
    }
    if reasoning is not None:
        result["reasoning"] = reasoning
    if codes:
        result["reason_codes"] = [code for code in codes if code]
    if ask_details is not None:
        result["ask_details"] = dict(ask_details)
    result["metadata"] = {"evaluator": "ctrlrun", "version": _version()}
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _error(rpc_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


def _version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("ctrlrun")
    except PackageNotFoundError:  # pragma: no cover - source checkout without an install
        return "unknown"
