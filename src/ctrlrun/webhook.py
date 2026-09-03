"""The webhook approval provider. Build-list item 7; SPEC-v0.2 §7.

Core, not an extra, because the outbound half needs neither an HTTP server nor a third-party
SDK: it is one signed POST over stdlib `urllib.request`. Only the inbound endpoint needs a
server, and that is the gateway's (§7.2).

The signature is over the **exact bytes sent**. Two encoders that agree on meaning and differ
on whitespace would produce a MAC the receiver cannot verify, and a receiver that
re-serialized the payload to check would be verifying its own guess rather than what arrived.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:  # `urllib.request` drags in `ssl`; see `_deliver`.
    import urllib.request

from .action import Action
from .approval import (
    DEFAULT_APPROVAL_TTL,
    Approval,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalStore,
    LocalApprovalProvider,
)
from .errors import CTRLRunError, InvalidArgument
from .receipt import iso_timestamp

_LOG = logging.getLogger(__name__)

#: SPEC-v0.2 §7.1 — the schema of one outbound notification.
REQUEST_SCHEMA: Final = "ctrlrun.approval_request/v1"

#: And the header both directions carry it in.
SIGNATURE_HEADER: Final = "CTRLRun-Signature"

#: §7.2 — how far a timestamp may be from now. No nonce store is needed: inside the window a
#: replayed grant is idempotent because the record is already granted, and outside it this
#: check rejects.
DEFAULT_REPLAY_WINDOW: Final = timedelta(seconds=300)

DEFAULT_TIMEOUT: Final = timedelta(seconds=10)
DEFAULT_RETRIES: Final = 2
DEFAULT_BACKOFF: Final = timedelta(seconds=1)

#: §7.3 — read from here or from a file, never from a command-line argument, which every
#: process listing on the host can read.
SECRET_ENV_VAR: Final = "CTRLRUN_WEBHOOK_SECRET"

#: Shorter than this is not a secret. HMAC-SHA256's block is 64 bytes; 32 is the floor below
#: which the MAC is weaker than the hash it is built on.
MIN_SECRET_BYTES: Final = 32

#: §7.2 — the endpoint the gateway serves, and the prefix its path may not overlap.
APPROVALS_PATH: Final = "/ctrlrun/approvals/"

_LOOPBACK: Final = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


def sign(body: bytes, secret: str, *, at: datetime | None = None) -> str:
    """`t=<unix seconds>,v1=<hex>` over `f"{t}.{body}"` (SPEC-v0.2 §7.1)."""
    moment = at if at is not None else datetime.now(UTC)
    stamp = int(moment.timestamp())
    mac = hmac.new(
        secret.encode("utf-8"), f"{stamp}.{body.decode('utf-8')}".encode(), hashlib.sha256
    )
    return f"t={stamp},v1={mac.hexdigest()}"


def verify(header: str, body: bytes, secret: str, window: timedelta) -> bool:
    """Whether `header` signs `body` and is inside `window` (SPEC-v0.2 §7.2).

    `hmac.compare_digest`, because a byte-by-byte comparison of a MAC leaks its prefix to
    anyone who can time the answer — and the caller here is by definition untrusted.
    """
    parts: dict[str, str] = {}
    for part in header.split(","):
        name, _, value = part.partition("=")
        parts[name.strip()] = value.strip()
    if "t" not in parts or "v1" not in parts:
        return False
    try:
        stamp = int(parts["t"])
    except ValueError:
        return False
    if abs(time.time() - stamp) > window.total_seconds():
        return False
    expected = hmac.new(
        secret.encode("utf-8"), f"{stamp}.{body.decode('utf-8')}".encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, parts["v1"])


def webhook_secret(path: str | os.PathLike[str] | None = None) -> str | None:
    """The shared secret, from a file or `$CTRLRUN_WEBHOOK_SECRET` (SPEC-v0.2 §7.3).

    Set but empty is a misconfiguration, not a licence to run unsigned — the same reading
    `$CTRLRUN_CONFIG` gets in v0.1 §3.4.
    """
    if path is not None:
        return _checked_secret(Path(path).read_text(encoding="utf-8").strip())
    configured = os.environ.get(SECRET_ENV_VAR)
    if configured is None:
        return None
    return _checked_secret(configured.strip())


def _checked_secret(secret: object) -> str:
    if not isinstance(secret, str) or not secret.strip():
        raise InvalidArgument(
            f"the webhook secret is empty; set {SECRET_ENV_VAR} or --webhook-secret-file"
        )
    if len(secret.encode("utf-8")) < MIN_SECRET_BYTES:
        raise InvalidArgument(
            f"the webhook secret is shorter than {MIN_SECRET_BYTES} bytes; a MAC is only "
            "worth the key behind it"
        )
    return secret


class WebhookApprovalProvider:
    """Notify a human system on `APPROVAL_REQUESTED`, and let it answer (SPEC-v0.2 §7).

    An `ApprovalProvider` (v0.1 §4.3): `request()` records the request and posts it, `wait()`
    polls the store exactly as `LocalApprovalProvider` does — the answer arrives out of band,
    whether from the gateway's inbound endpoint or from `ctrlrun approve`.

    **An undelivered notification is not an approval.** If every attempt fails the request
    stays `pending`, `ApprovalRequired` is raised as usual with a real `request_id` that
    `ctrlrun approve` can still answer, and the failure is logged. The action is refused
    either way, which is the only outcome that is safe when nobody was told.
    """

    def __init__(
        self,
        store: ApprovalStore,
        *,
        url: str,
        secret: object,
        timeout: timedelta = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        backoff: timedelta = DEFAULT_BACKOFF,
        replay_window: timedelta = DEFAULT_REPLAY_WINDOW,
        public_url: str | None = None,
        allow_insecure: bool = False,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._secret = _checked_secret(secret)
        self._url = _checked_url(url, allow_insecure)
        self._timeout = timeout
        self._retries = max(0, retries)
        self._backoff = backoff
        self._replay_window = replay_window
        self._public_url = public_url.rstrip("/") if public_url else None
        self._store = store
        self._clock = clock
        self._local = LocalApprovalProvider(store, clock=clock)

    @property
    def replay_window(self) -> timedelta:
        return self._replay_window

    def request(self, action: Action, ttl: timedelta = DEFAULT_APPROVAL_TTL) -> ApprovalRequest:
        """Record the request, then tell somebody. The record is written first.

        A notification for a request the store never accepted would point at nothing, and a
        human who answered it would be answering a question CTRLRun cannot connect to an
        action.
        """
        request = self._local.request(action, ttl)
        self._deliver(request)
        return request

    def wait(self, request_id: str, timeout: timedelta | None = None) -> Approval | None:
        return self._local.wait(request_id, timeout)

    def _deliver(self, request: ApprovalRequest) -> None:
        # Imported here, not at module scope: `urllib.request` pulls in `ssl`, and nothing
        # that never sends a webhook should pay for a TLS stack at `import ctrlrun` (§1.1's
        # dependency rule, applied to the stdlib as well as to the extras).
        import urllib.error

        body = _payload(request, self._public_url)
        signature = sign(body, self._secret, at=self._clock())
        headers = {"Content-Type": "application/json", SIGNATURE_HEADER: signature}
        for attempt in range(self._retries + 1):
            try:
                self._post(body, headers)
                return
            except (urllib.error.URLError, OSError, CTRLRunError) as exc:
                # Never the secret, and never the body: the payload carries the action's
                # arguments, which are the customer identifiers and amounts (§8's rule for
                # the OTel sink, applied to a log line).
                _LOG.warning(
                    "webhook delivery for %s failed (attempt %d of %d): %s",
                    request.request_id,
                    attempt + 1,
                    self._retries + 1,
                    exc,
                )
            if attempt < self._retries and self._backoff.total_seconds() > 0:
                time.sleep(self._backoff.total_seconds() * (2**attempt))
        _LOG.warning(
            "webhook delivery for %s gave up; the request is still pending and "
            "'ctrlrun approve %s' can answer it",
            request.request_id,
            request.request_id,
        )

    def _post(self, body: bytes, headers: dict[str, str]) -> None:
        import urllib.request

        request = urllib.request.Request(self._url, data=body, headers=headers, method="POST")
        # `_NoRedirects` because a redirect to another host would deliver the signed payload,
        # and the action inside it, somewhere the operator did not name.
        opener = urllib.request.build_opener(_no_redirects())
        with opener.open(request, timeout=self._timeout.total_seconds()) as response:
            if response.status >= 400:
                raise CTRLRunError(f"the webhook endpoint answered {response.status}")


def _no_redirects() -> urllib.request.BaseHandler:
    """A handler that refuses every redirect (SPEC-v0.2 §7.1).

    A function rather than a class so `urllib.request` stays out of module scope: see
    `_deliver`. A redirect to another host would deliver the signed payload, and the action
    inside it, somewhere the operator did not name.
    """
    import urllib.request

    class Refuse(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args: object, **kwargs: object) -> None:
            return None

    return Refuse()


def _checked_url(url: str, allow_insecure: bool) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme == "https":
        return url
    if parsed.scheme != "http":
        raise InvalidArgument(f"webhook url {url!r} must be http or https")
    host = (parsed.hostname or "").lower()
    if host in _LOOPBACK and allow_insecure:
        return url
    raise InvalidArgument(
        f"webhook url {url!r} is not https; an unencrypted hop would put the action and its "
        "signature on the wire. Loopback is permitted with --allow-insecure-webhook."
    )


def _payload(request: ApprovalRequest, public_url: str | None) -> bytes:
    document: dict[str, Any] = {
        "schema": REQUEST_SCHEMA,
        "request_id": request.request_id,
        "action_hash": request.action_hash,
        "action": _action_document(request.action),
        "created_at": iso_timestamp(request.created_at),
        "expires_at": iso_timestamp(request.expires_at),
    }
    if public_url is not None:
        # §7.2 — present only when there is somewhere to POST back to. Its absence is how the
        # receiving system knows the answer has to come from the CLI.
        document["respond_to"] = f"{public_url}{APPROVALS_PATH}{request.request_id}"
    return json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _action_document(action: Action) -> dict[str, Any]:
    return {
        "name": action.name,
        "arguments": dict(action.canonical_arguments),
        "principal": {"agent": action.principal.agent, "user": action.principal.user},
        "resource": action.resource,
        "environment": action.environment,
    }


def handle_inbound(
    store: ApprovalStore,
    path_request_id: str,
    body: bytes,
    signature: str,
    *,
    secret: str,
    replay_window: timedelta = DEFAULT_REPLAY_WINDOW,
) -> tuple[int, str]:
    """Answer one `POST /ctrlrun/approvals/<request_id>` (SPEC-v0.2 §7.2).

    Every condition must hold or the request is rejected with HTTP 400, **no state change**,
    and a warning. They are checked before anything is written, and the failure message is
    the same shape for all of them: telling a caller which condition it missed is telling it
    what to try next.
    """
    if not verify(signature, body, secret, replay_window):
        return _refused(path_request_id, "the signature did not verify inside the replay window")
    try:
        document = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return _refused(path_request_id, "the body is not valid JSON")
    if not isinstance(document, dict):
        return _refused(path_request_id, "the body is not an object")

    if document.get("request_id") != path_request_id:
        return _refused(path_request_id, "the path and the body name different requests")
    record = store.get_approval(path_request_id)
    if record is None:
        return _refused(path_request_id, "there is no such request")
    if document.get("action_hash") != record.action_hash:
        return _refused(path_request_id, "the action hash does not match the stored request")

    decision = document.get("decision")
    if decision not in ("grant", "deny"):
        return _refused(path_request_id, "decision must be 'grant' or 'deny'")
    approver = document.get("approver")
    if not isinstance(approver, str) or not approver.strip():
        return _refused(path_request_id, "approver must be a non-empty string")

    already = _already_answered(record.status, decision)
    if already is not None:
        # §7.2 — inside the replay window a replayed answer is idempotent, which is why no
        # nonce store is needed: the record already says what this message is asking for.
        # Outside the window the timestamp check above rejected it.
        return 200, already

    try:
        # Single use is enforced at consumption (v0.1 §4.2 A2), and this endpoint does not
        # get to weaken it: `grant_approval` refuses a record that is consumed, denied or
        # expired, so a replay arriving *after* consumption cannot resurrect anything.
        if decision == "grant":
            store.grant_approval(path_request_id, approver)
        else:
            store.deny_approval(path_request_id, approver)
    except CTRLRunError as refused:
        return _refused(path_request_id, str(refused))
    return 200, "ok"


def _already_answered(status: ApprovalStatus, decision: str) -> str | None:
    """Whether this message asks for the answer the record already carries (§7.2).

    Only the *same* answer is idempotent. A grant replayed onto a denied record, or onto a
    consumed one, is not a replay — it is a different question, and it is refused below.
    """
    if decision == "grant" and status is ApprovalStatus.GRANTED:
        return "already granted"
    if decision == "deny" and status is ApprovalStatus.DENIED:
        return "already denied"
    return None


def _refused(request_id: str, why: str) -> tuple[int, str]:
    _LOG.warning("inbound approval for %s refused: %s", request_id, why)
    return 400, why
