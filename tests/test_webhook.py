"""The webhook approval provider. Build-list item 7; SPEC-v0.2 §7, tests T27 and T28.

Outbound is core: one signed POST over stdlib `urllib.request`, so `pip install ctrlrun` does
not grow for it. The inbound endpoint is the gateway's, because that is the one server this
release ships.

The signature is over the **exact bytes sent**, and every inbound condition must hold or the
request is rejected with no state change. An undelivered notification is not an approval, and
a replayed one must not resurrect anything.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ctrlrun import (
    Action,
    ApprovalRequired,
    Control,
    InvalidArgument,
    Policy,
    Principal,
    SQLiteStateStore,
    WebhookApprovalProvider,
    context,
    protect,
)
from ctrlrun.approval import ApprovalStatus
from ctrlrun.webhook import (
    REQUEST_SCHEMA,
    SIGNATURE_HEADER,
    sign,
    verify,
)

SECRET = "a" * 32

POLICY = """
schema: ctrlrun.policy/v1
actions:
  stripe.refund:
    decision: approve
"""


class Receiver:
    """The other end: it records what it was sent, and answers however it was told to."""

    def __init__(self) -> None:
        self.posts: list[tuple[dict[str, str], bytes]] = []
        self.status = 200
        self.redirect_to: str | None = None
        self.redirect_status = 302


@pytest.fixture
def receiver_factory():
    servers = []

    def make() -> Receiver:
        state = Receiver()

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                state.posts.append((dict(self.headers.items()), self.rfile.read(length)))
                if state.redirect_to is not None:
                    self.send_response(state.redirect_status)
                    self.send_header("Location", state.redirect_to)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(state.status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self):
                # urllib converts a POST to a GET when it follows 301/302/303, so a server
                # that only recorded POSTs could not see a redirect it was sent.
                state.posts.append((dict(self.headers.items()), b""))
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        state.url = f"http://127.0.0.1:{server.server_address[1]}/hook"
        servers.append(server)
        return state

    yield make
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.fixture
def receiver(receiver_factory):
    return receiver_factory()


@pytest.fixture
def elsewhere(receiver_factory):
    """Somewhere the operator did not name, for the redirect test."""
    return receiver_factory()


@pytest.fixture
def store(tmp_path):
    store = SQLiteStateStore(tmp_path / "state.db")
    yield store
    store.close()


def _provider(store, receiver, **overrides):
    return WebhookApprovalProvider(
        store,
        url=receiver.url,
        secret=SECRET,
        allow_insecure=True,
        **overrides,
    )


def _action(amount: int = 2000) -> Action:
    return Action(
        name="stripe.refund",
        arguments={"payment_id": "txn_1", "amount": amount},
        principal=Principal(agent="refund-agent"),
    )


def _sent(receiver):
    """HTTP header names are case-insensitive, and urllib normalizes what it sends."""
    headers, body = receiver.posts[-1]
    folded = {name.lower(): value for name, value in headers.items()}
    return folded, body, json.loads(body)


# --- T27: the outbound signature -------------------------------------------------------


def test_T27_the_outbound_post_carries_a_signature_over_the_exact_bytes_sent(store, receiver):
    """Over the bytes, not over a re-serialization of the payload: two encoders that agree
    on meaning and differ on whitespace would produce a signature the receiver cannot
    verify, and a receiver that re-serialized to check would be verifying its own guess."""
    _provider(store, receiver).request(_action())

    headers, body, _ = _sent(receiver)

    assert verify(headers[SIGNATURE_HEADER.lower()], body, SECRET, timedelta(seconds=300))


def test_T27_a_signature_over_different_bytes_does_not_verify(store, receiver):
    _provider(store, receiver).request(_action())
    headers, body, _ = _sent(receiver)

    assert not verify(
        headers[SIGNATURE_HEADER.lower()], body + b" ", SECRET, timedelta(seconds=300)
    )


def test_T27_the_signature_is_hmac_sha256_over_timestamp_dot_body(store, receiver):
    """§7.1's construction, spelled out so a receiving system can be written against it."""
    _provider(store, receiver).request(_action())
    headers, body, _ = _sent(receiver)

    parts = dict(part.split("=", 1) for part in headers[SIGNATURE_HEADER.lower()].split(","))
    expected = hmac.new(
        SECRET.encode(), f"{parts['t']}.{body.decode()}".encode(), hashlib.sha256
    ).hexdigest()
    assert parts["v1"] == expected


def test_T27_the_payload_carries_what_the_spec_names(store, receiver):
    request = _provider(store, receiver).request(_action())
    _, _, payload = _sent(receiver)

    assert payload["schema"] == REQUEST_SCHEMA == "ctrlrun.approval_request/v1"
    assert payload["request_id"] == request.request_id
    assert payload["action_hash"] == request.action_hash
    assert payload["action"]["name"] == "stripe.refund"
    assert payload["created_at"] and payload["expires_at"]


def test_respond_to_is_absent_without_a_public_url(store, receiver):
    """How the receiving system knows the answer has to come from the CLI (§7.2)."""
    _provider(store, receiver).request(_action())

    assert "respond_to" not in _sent(receiver)[2]


def test_respond_to_names_the_gateways_endpoint_when_a_public_url_is_given(store, receiver):
    request = _provider(store, receiver, public_url="https://gw.example").request(_action())

    respond_to = _sent(receiver)[2]["respond_to"]
    assert respond_to == f"https://gw.example/ctrlrun/approvals/{request.request_id}"


def test_the_content_type_is_json(store, receiver):
    _provider(store, receiver).request(_action())

    assert _sent(receiver)[0]["content-type"] == "application/json"


# --- T27: the inbound conditions -------------------------------------------------------


def _inbound(store, request, decision="grant", *, approver="slack:U123", **overrides):
    from ctrlrun.webhook import handle_inbound

    body = {
        "request_id": overrides.get("body_request_id", request.request_id),
        "action_hash": overrides.get("action_hash", request.action_hash),
        "decision": decision,
        "approver": approver,
    }
    raw = json.dumps(body).encode()
    signature = overrides.get(
        "signature", sign(raw, SECRET, at=overrides.get("at", datetime.now(UTC)))
    )
    return handle_inbound(
        store,
        overrides.get("path_request_id", request.request_id),
        raw,
        signature,
        secret=SECRET,
        replay_window=timedelta(seconds=300),
    )


def _pending(store, receiver):
    return _provider(store, receiver).request(_action())


def test_T27_a_valid_grant_moves_the_request_to_granted(store, receiver):
    request = _pending(store, receiver)

    status, _ = _inbound(store, request)

    assert status == 200
    record = store.get_approval(request.request_id)
    assert record.status is ApprovalStatus.GRANTED
    assert record.approver == "slack:U123"


def test_T27_a_valid_deny_moves_the_request_to_denied(store, receiver):
    request = _pending(store, receiver)

    status, _ = _inbound(store, request, "deny")

    assert status == 200
    assert store.get_approval(request.request_id).status is ApprovalStatus.DENIED


#: Each way an inbound approval can be broken, as a *factory*. Not a literal `overrides`
#: dict, because two of these are timestamps: a parametrize list is evaluated at **collection**
#: time, so `datetime.now(UTC) + timedelta(seconds=400)` is 400 seconds ahead of collection
#: and only `400 - <however long the suite has been running>` seconds ahead by the time this
#: test executes. Past the replay window's 300 seconds of runtime it lands *inside* the window
#: and the refusal under test stops happening — a time bomb whose fuse is the suite's own
#: duration, which duly fired the first time an item made the suite slower.
_BROKEN_CONDITIONS: dict[str, Callable[[], dict[str, object]]] = {
    "bad-signature": lambda: {"signature": "t=1,v1=deadbeef"},
    "unparseable-signature": lambda: {"signature": "nonsense"},
    "no-signature": lambda: {"signature": ""},
    "too-old": lambda: {"at": datetime.now(UTC) - timedelta(seconds=400)},
    "too-far-ahead": lambda: {"at": datetime.now(UTC) + timedelta(seconds=400)},
    "wrong-request-id": lambda: {"body_request_id": "apr_somethingelse"},
    "wrong-action-hash": lambda: {"action_hash": "sha256:" + "0" * 64},
}


@pytest.mark.parametrize("condition", sorted(_BROKEN_CONDITIONS))
def test_T27_every_broken_condition_is_400_and_changes_nothing(store, receiver, condition):
    request = _pending(store, receiver)

    status, _ = _inbound(store, request, **_BROKEN_CONDITIONS[condition]())

    assert status == 400
    assert store.get_approval(request.request_id).status is ApprovalStatus.PENDING


@pytest.mark.parametrize("side", ["path", "body"])
def test_T27_a_path_body_request_id_disagreement_is_refused(store, receiver, side):
    """Both directions. With only the path wrong, the lookup finds nothing and the refusal
    is right for the wrong reason; with only the body wrong, the lookup finds the *real*
    record and the comparison is the only thing standing between it and a grant."""
    request = _pending(store, receiver)
    where = {"path": {"path_request_id": "apr_other"}, "body": {"body_request_id": "apr_other"}}

    status, _ = _inbound(store, request, **where[side])

    assert status == 400
    assert store.get_approval(request.request_id).status is ApprovalStatus.PENDING


def test_T27_a_request_the_store_never_saw_is_refused(store, receiver):
    """Not the same as a request in the wrong state: there is no record at all."""
    request = _pending(store, receiver)
    unknown = type(request)(
        request_id="apr_nothing",
        action_hash=request.action_hash,
        action=request.action,
        created_at=request.created_at,
        expires_at=request.expires_at,
    )

    status, _ = _inbound(store, unknown)

    assert status == 400
    assert store.get_approval("apr_nothing") is None


@pytest.mark.parametrize("decision", ["approve", "GRANT", "", None, 1])
def test_T27_a_decision_that_is_not_grant_or_deny_is_refused(store, receiver, decision):
    """A body the endpoint half-understands is a body it must not act on."""
    request = _pending(store, receiver)

    status, _ = _inbound(store, request, decision)

    assert status == 400
    assert store.get_approval(request.request_id).status is ApprovalStatus.PENDING


def test_T27_an_unknown_request_is_refused(store, receiver):
    request = _pending(store, receiver)
    store.deny_approval(request.request_id, "cli:local")

    status, _ = _inbound(store, request)

    assert status == 400
    assert store.get_approval(request.request_id).status is ApprovalStatus.DENIED


def test_T27_a_replayed_valid_grant_is_idempotent(store, receiver):
    """No nonce store is needed: inside the window the record is already granted, and
    outside it the timestamp check rejects (§7.2)."""
    request = _pending(store, receiver)
    at = datetime.now(UTC)
    first, _ = _inbound(store, request, at=at)
    second, _ = _inbound(store, request, at=at)

    assert first == 200
    assert second == 200
    assert store.get_approval(request.request_id).status is ApprovalStatus.GRANTED


def test_T27_a_grant_replayed_after_consumption_does_not_resurrect_it(store, receiver):
    """Single use is enforced at consumption (v0.1 §4.2 A2), and this endpoint does not get
    to weaken it."""
    action = _action()
    request = _provider(store, receiver).request(action)
    _inbound(store, request)
    store.consume_approval(request.request_id, action.action_hash)

    status, _ = _inbound(store, request)

    assert status == 400
    assert store.get_approval(request.request_id).status is ApprovalStatus.CONSUMED


# --- T28: an undelivered webhook approves nothing --------------------------------------


def test_T28_the_provider_gives_up_after_its_bounded_retries(store, receiver, caplog):
    receiver.status = 500

    with caplog.at_level("WARNING", logger="ctrlrun"):
        request = _provider(store, receiver, retries=2, backoff=timedelta(0)).request(_action())

    assert len(receiver.posts) == 3  # the first attempt, then two retries
    assert store.get_approval(request.request_id).status is ApprovalStatus.PENDING
    assert [r for r in caplog.records if "webhook" in r.getMessage().lower()]


def test_T28_the_action_is_still_refused_and_the_request_id_still_answerable(
    store, receiver, caplog
):
    """The action is refused either way — an undelivered request is not an approval — and
    `ctrlrun approve` can still answer the request that was written."""
    receiver.status = 500
    control = Control(
        Policy.from_yaml(POLICY),
        store,
        _provider(store, receiver, retries=1, backoff=timedelta(0)),
    )

    @protect("stripe.refund", effect="refund:{payment_id}", control=control)
    def refund(payment_id: str, amount: int) -> str:
        return "re_1"

    with (
        caplog.at_level("WARNING", logger="ctrlrun"),
        context(agent="refund-agent"),
        pytest.raises(ApprovalRequired) as pending,
    ):
        refund(payment_id="txn_1", amount=2000)

    assert store.get_approval(pending.value.request_id).status is ApprovalStatus.PENDING
    assert store.receipts() == ()
    store.grant_approval(pending.value.request_id, "cli:local")
    assert store.get_approval(pending.value.request_id).status is ApprovalStatus.GRANTED


def test_T28_an_unreachable_endpoint_does_not_raise_into_the_kernel(store, tmp_path):
    provider = WebhookApprovalProvider(
        store,
        url="http://127.0.0.1:1/hook",
        secret=SECRET,
        allow_insecure=True,
        retries=0,
        backoff=timedelta(0),
    )

    request = provider.request(_action())

    assert store.get_approval(request.request_id).status is ApprovalStatus.PENDING


# --- §7.1: what the provider refuses ---------------------------------------------------


@pytest.mark.parametrize("status", [301, 302, 303])
def test_a_redirect_is_never_followed(store, receiver, elsewhere, status):
    """A redirect to another host would deliver the request, and the signature header with
    it, somewhere the operator did not name.

    Parametrized over 301/302/303 deliberately: those are the codes `urllib` follows on a
    POST — converting it to a GET against the new host — and therefore the only ones where
    the handler is what stops it. It refuses 307 and 308 on its own, so a test using those
    would pass with the handler removed and prove nothing.
    """
    receiver.redirect_status = status
    receiver.redirect_to = elsewhere.url

    request = _provider(store, receiver, retries=0, backoff=timedelta(0)).request(_action())

    assert len(receiver.posts) == 1
    assert elsewhere.posts == []
    assert store.get_approval(request.request_id).status is ApprovalStatus.PENDING


def test_an_http_url_is_refused_unless_it_is_loopback_and_meant(store):
    with pytest.raises(InvalidArgument):
        WebhookApprovalProvider(store, url="http://example.com/hook", secret=SECRET)

    with pytest.raises(InvalidArgument):
        WebhookApprovalProvider(
            store, url="http://127.0.0.1:9/hook", secret=SECRET, allow_insecure=False
        )

    WebhookApprovalProvider(store, url="https://example.com/hook", secret=SECRET)


# --- §7.3: the secret ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("secret", "why"), [("", "empty"), ("   ", "empty"), ("a" * 31, "shorter"), (None, "empty")]
)
def test_a_weak_or_missing_secret_is_refused(store, secret, why):
    """The length check would refuse an empty one too; the message is the difference, and it
    is the one an operator needs — "empty" points at the configuration, "too short" at the
    value."""
    with pytest.raises(InvalidArgument) as refused:
        WebhookApprovalProvider(store, url="https://example.com/hook", secret=secret)

    assert why in str(refused.value)


def test_the_secret_never_appears_in_a_log_line(store, receiver, caplog):
    receiver.status = 500

    with caplog.at_level("DEBUG", logger="ctrlrun"):
        _provider(store, receiver, retries=1, backoff=timedelta(0)).request(_action())

    assert SECRET not in "\n".join(record.getMessage() for record in caplog.records)


def test_the_secret_is_read_from_the_environment(store, monkeypatch):
    from ctrlrun.webhook import webhook_secret

    monkeypatch.setenv("CTRLRUN_WEBHOOK_SECRET", SECRET)
    assert webhook_secret() == SECRET

    monkeypatch.setenv("CTRLRUN_WEBHOOK_SECRET", "")
    with pytest.raises(InvalidArgument):
        webhook_secret()

    monkeypatch.delenv("CTRLRUN_WEBHOOK_SECRET")
    assert webhook_secret() is None


def test_the_secret_can_come_from_a_file(store, tmp_path, monkeypatch):
    from ctrlrun.webhook import webhook_secret

    path = tmp_path / "secret"
    path.write_text(f"  {SECRET}\n", encoding="utf-8")
    monkeypatch.delenv("CTRLRUN_WEBHOOK_SECRET", raising=False)

    assert webhook_secret(path) == SECRET


def test_signing_is_stable_and_verification_is_not_a_prefix_match():
    body = b'{"a":1}'
    at = datetime.now(UTC)

    signature = sign(body, SECRET, at=at)

    assert verify(signature, body, SECRET, timedelta(seconds=300))
    assert not verify(signature[:-1], body, SECRET, timedelta(seconds=300))
    assert not verify(signature, body, "b" * 32, timedelta(seconds=300))


def test_a_timestamp_outside_the_window_is_refused_in_both_directions():
    body = b'{"a":1}'
    window = timedelta(seconds=300)

    old = sign(body, SECRET, at=datetime.now(UTC) - timedelta(seconds=400))
    future = sign(body, SECRET, at=datetime.now(UTC) + timedelta(seconds=400))

    assert not verify(old, body, SECRET, window)
    assert not verify(future, body, SECRET, window)


def test_verification_tolerates_a_little_clock_skew():
    body = b'{"a":1}'
    signature = sign(body, SECRET, at=datetime.now(UTC) - timedelta(seconds=5))

    assert verify(signature, body, SECRET, timedelta(seconds=300))
    assert time.time() > 0
