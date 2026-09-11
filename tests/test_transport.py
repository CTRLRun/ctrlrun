"""`ctrlrun.transport`, the `NotExecuted` classifier. SPEC-v0.7 §2; T220 to T229b, T231.

T230, G12 in verify under the amended network guard, is at the end of this file.

Every peer in this file is a real socket on the loopback interface, and every TLS server is a
real `ssl` server holding a certificate this module generates. **There is no fake socket here**,
and that is the point of the file: a double that reports "no bytes written" where a real socket
could not tell would make every test that used it pass for a reason that is not true in
production (`CONTRIBUTING.md`, the third shape of a false green).

**Every negative test states its precondition in its docstring and asserts it.** A test that
says "this was not `NotExecuted`" proves nothing unless the classifier would otherwise have been
in a position to claim it, so each one either shows the peer received the byte or runs the same
failure through a connection that *does* claim (a control), and asserts that first.

Every wait is bounded: each socket has a timeout of `WAIT` seconds and each peer thread is
joined with a bound, so a broken check fails red instead of hanging.
"""

from __future__ import annotations

import ast
import contextvars
import datetime as dt
import functools
import http.client
import inspect
import io
import json
import os
import socket
import ssl
import struct
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import warnings
import xmlrpc.client
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

import ctrlrun
import ctrlrun.effect
import ctrlrun.transport as transport
from ctrlrun import (
    Action,
    AmbiguousEffect,
    Control,
    EffectState,
    InMemoryStateStore,
    NotExecuted,
    Policy,
    Principal,
    SQLiteStateStore,
    Suspended,
    context,
    protect,
)
from ctrlrun.receipt import ReceiptResult

LOOPBACK = "127.0.0.1"
WAIT = 5.0
TRANSPORT_SOURCE = Path(ctrlrun.__file__).parent / "transport.py"

POLICY = """
schema: ctrlrun.policy/v2
actions:
  refund.create:
    effect: "refund:{payment_id}"
    decision: allow
"""


# --- real loopback peers --------------------------------------------------------------------


def _linger_reset(conn: socket.socket) -> None:
    """`SO_LINGER` with a zero timeout: `close()` sends a reset rather than a FIN."""
    conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))


class Peer:
    """A real listener on `127.0.0.1`, running `handler` on each accepted socket in a thread.

    `received` is every application byte the handler read, `accepted` how many connections reached
    it. Nothing here is simulated: a byte in `received` crossed a real loopback socket.
    """

    def __init__(
        self,
        handler: Callable[[Peer, socket.socket], None],
        *,
        connections: int = 1,
        rcvbuf: int | None = None,
        wrap: ssl.SSLContext | None = None,
        one_shot: bool = False,
    ) -> None:
        self.handler = handler
        self._one_shot = one_shot
        self.received = bytearray()
        self.accepted = 0
        self.errors: list[BaseException] = []
        self.handshake_errors: list[BaseException] = []
        self._wrap = wrap
        self._connections = connections
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if rcvbuf is not None:
            self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
        self.listener.bind((LOOPBACK, 0))
        self.listener.listen(8)
        self.listener.settimeout(WAIT)
        self.port: int = self.listener.getsockname()[1]
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        for _ in range(self._connections):
            try:
                conn, _ = self.listener.accept()
            except OSError as exc:
                self.errors.append(exc)
                return
            self.accepted += 1
            if self._one_shot:
                # The server dies with its first connection: every later connect is refused.
                self.listener.close()
            conn.settimeout(WAIT)
            if self._wrap is not None:
                try:
                    conn = self._wrap.wrap_socket(conn, server_side=True)
                except (OSError, ssl.SSLError) as exc:
                    self.handshake_errors.append(exc)
                    conn.close()
                    continue
            try:
                self.handler(self, conn)
            except OSError as exc:
                self.errors.append(exc)
            finally:
                with suppress(OSError):
                    conn.close()

    def join(self) -> None:
        self._thread.join(WAIT * 2)
        assert not self._thread.is_alive(), "the peer thread did not finish within its bound"

    def queued(self) -> int:
        """Connections waiting unaccepted: a redirect followed to this listener would be one."""
        self.listener.setblocking(False)
        waiting = 0
        while True:
            try:
                extra, _ = self.listener.accept()
            except (BlockingIOError, OSError):
                return waiting
            extra.close()
            waiting += 1

    def close(self) -> None:
        with suppress(OSError):
            self.listener.close()
        self._thread.join(WAIT * 2)


@contextmanager
def peer(handler: Callable[[Peer, socket.socket], None], **options: Any) -> Iterator[Peer]:
    running = Peer(handler, **options)
    try:
        yield running
    finally:
        running.close()


def read_then_reset(state: Peer, conn: socket.socket) -> None:
    """Read at least one request byte, record it, and reset: the peer "killed" mid-exchange."""
    data = conn.recv(65536)
    state.received += data
    _linger_reset(conn)


def read_then_hang(state: Peer, conn: socket.socket) -> None:
    """Read the request and never answer, so the client's read times out; wait for it to go."""
    state.received += _read_request(conn)
    with suppress(OSError):
        while conn.recv(65536):
            pass


def _read_request(conn: socket.socket) -> bytes:
    """One HTTP/1.x request: headers, then a `Content-Length` or chunked body."""
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = conn.recv(65536)
        if not chunk:
            return data
        data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    lowered = head.lower()
    if b"transfer-encoding: chunked" in lowered:
        while not body.endswith(b"0\r\n\r\n"):
            chunk = conn.recv(65536)
            if not chunk:
                break
            body += chunk
    else:
        length = 0
        for line in head.split(b"\r\n")[1:]:
            name, _, value = line.partition(b":")
            if name.strip().lower() == b"content-length":
                length = int(value.strip())
        while len(body) < length:
            chunk = conn.recv(65536)
            if not chunk:
                break
            body += chunk
    return head + b"\r\n\r\n" + body


def answering(
    status: int = 200, headers: dict[str, str] | None = None, body: bytes = b"ok"
) -> Callable[[Peer, socket.socket], None]:
    def handler(state: Peer, conn: socket.socket) -> None:
        state.received += _read_request(conn)
        lines = [f"HTTP/1.1 {status} X".encode()]
        for name, value in {"Content-Length": str(len(body)), "Connection": "close"}.items():
            lines.append(f"{name}: {value}".encode())
        for name, value in (headers or {}).items():
            lines.append(f"{name}: {value}".encode())
        conn.sendall(b"\r\n".join(lines) + b"\r\n\r\n" + body)

    return handler


def _refused_port() -> int:
    """A loopback port whose listener has closed: a connect to it is refused, and nothing reads.

    Not "bound and not listening", which is what §8.2 names: macOS drops a SYN to a port that is
    bound and not listening, so the connect times out rather than being refused (Linux answers with
    a reset). A closed listener is refused on both. G12's control keeps the bound socket, which
    holds the port, and accepts either answer (§12.2).
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind((LOOPBACK, 0))
    listener.listen(1)
    port: int = listener.getsockname()[1]
    listener.close()
    return port


@pytest.fixture
def refused() -> int:
    return _refused_port()


@contextmanager
def silent_listener() -> Iterator[tuple[int, Callable[[], int]]]:
    """A live listener nothing accepts from, and a count of the connections that reached it.

    For the tests whose claim is that nothing was sent: a connection would sit in the queue, and
    the count drains it without blocking.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind((LOOPBACK, 0))
    listener.listen(8)

    def arrived() -> int:
        listener.setblocking(False)
        count = 0
        while True:
            try:
                extra, _ = listener.accept()
            except BlockingIOError:
                return count
            extra.close()
            count += 1

    try:
        yield listener.getsockname()[1], arrived
    finally:
        listener.close()


@pytest.fixture(autouse=True)
def no_proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    """`urlopen` honours proxies (§2.3), so every test states the proxy environment it runs in.

    `no_proxy=*` also keeps urllib from consulting the host's system proxy configuration, which it
    does on macOS only when no `*_proxy` variable is set at all.
    """
    for name in list(os.environ):
        if name.lower().endswith("_proxy"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("no_proxy", "*")


def _proxy_environment(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setenv("http_proxy", url)
    monkeypatch.setenv("https_proxy", url)


# --- the two surfaces -------------------------------------------------------------------------

SURFACES = ("urlopen", "http.client")


def call(
    surface: str,
    port: int,
    *,
    body: bytes = b'{"payment_id":"txn_1"}',
    tls: ssl.SSLContext | None = None,
    host: str = LOOPBACK,
    timeout: float = WAIT,
) -> int:
    """One POST through one surface, answering the response status."""
    if surface == "urlopen":
        scheme = "https" if tls is not None else "http"
        request = urllib.request.Request(
            f"{scheme}://{host}:{port}/refunds", data=body, method="POST"
        )
        with transport.urlopen(request, timeout=timeout, context=tls) as response:
            response.read()
            return int(response.status)
    connection = (
        transport.HTTPSConnection(host, port, timeout=timeout, context=tls)
        if tls is not None
        else transport.HTTPConnection(host, port, timeout=timeout)
    )
    try:
        connection.request("POST", "/refunds", body=body)
        response = connection.getresponse()
        response.read()
        return int(response.status)
    finally:
        connection.close()


def _caught(thunk: Callable[[], Any]) -> BaseException:
    """What `thunk` raised, run as it is: outside any executor run unless it makes one itself."""
    try:
        thunk()
    except BaseException as exc:
        return exc
    raise AssertionError("the call returned; the test needed it to fail")


_RUN_POLICY = """
schema: ctrlrun.policy/v2
actions:
  transport.call:
    decision: allow
"""


def in_run(thunk: Callable[[], Any]) -> Any:
    """Run `thunk` as the executor of one real `Control.execute`, and answer what it returned.

    The classifier claims only inside an executor run, where `Control` has opened the register
    of what this run offered (§2.3, §12.2.9). Every test below that expects a claim, or asserts
    the absence of one, therefore runs its call here: a "never `NotExecuted`" asserted outside a
    run would be true of a classifier that could not claim at all (mutation pattern 3).
    """
    control = Control(Policy.from_yaml(_RUN_POLICY), InMemoryStateStore())
    action = Action(
        name="transport.call", arguments={}, principal=Principal(agent="transport-tests")
    )
    returned: list[Any] = []

    def executor() -> Any:
        returned.append(thunk())
        return returned[-1]

    control.execute(action, executor)
    return returned[0]


def _raised(thunk: Callable[[], Any]) -> BaseException:
    """What `thunk` raised, run inside one executor run (see `in_run`)."""
    return _caught(lambda: in_run(thunk))


def _cause_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    seen: BaseException | None = exc
    while seen is not None and len(chain) < 10:
        chain.append(seen)
        seen = seen.__cause__ or getattr(seen, "reason", None)
        if not isinstance(seen, BaseException):
            seen = None
    return chain


# --- TLS: a certificate generated here, and a server that holds it ----------------------------


@pytest.fixture(scope="module")
def certificate(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """A self-signed certificate for `127.0.0.1`, generated for this run. Never checked in."""
    pytest.importorskip("cryptography", reason="the identity extra carries cryptography")
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, LOOPBACK)])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(LOOPBACK))]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    directory = tmp_path_factory.mktemp("tls")
    cert_path, key_path = directory / "cert.pem", directory / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


@pytest.fixture
def server_tls(certificate: tuple[Path, Path]) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(*certificate)
    return context


@pytest.fixture
def trusting(certificate: tuple[Path, Path]) -> ssl.SSLContext:
    return ssl.create_default_context(cafile=str(certificate[0]))


@pytest.fixture
def untrusting() -> ssl.SSLContext:
    """The default context: it does not trust a certificate generated a moment ago."""
    return ssl.create_default_context()


# --- the kernel around the classifier ---------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SQLiteStateStore]:
    opened = SQLiteStateStore(tmp_path / "state.db")
    yield opened
    opened.close()


@pytest.fixture
def control(store: SQLiteStateStore) -> Control:
    return Control(Policy.from_yaml(POLICY), store)


def _protected(control: Control, target: Callable[[], int]) -> Callable[[str], int]:
    @protect("refund.create", control=control)
    def refund(payment_id: str) -> int:
        return target()

    return refund


# === T220: a byte written and the peer killed is AMBIGUOUS, never FAILED =======================


@pytest.mark.parametrize("surface", SURFACES)
def test_T220_a_byte_written_and_the_peer_killed_is_AMBIGUOUS(surface, control, store):
    """**The test this item exists for.**

    Precondition, asserted first: the loopback peer received at least one request byte before it
    reset the connection. Without it, "not `NotExecuted`" could be true of a request that never
    left, and the test would prove nothing.
    """
    with peer(read_then_reset) as server:
        refund = _protected(control, lambda: call(surface, server.port))
        with context(agent="refund-agent"):
            raised = _caught(lambda: refund("txn_1"))
        server.join()

        assert len(server.received) >= 1, "precondition: the peer received a request byte"
        assert server.received.startswith(b"POST /refunds")
        assert any(
            isinstance(item, (ConnectionResetError, BrokenPipeError))
            for item in _cause_chain(raised)
        ), f"precondition: the client saw the reset, not {raised!r}"
        assert not isinstance(raised, NotExecuted), raised
        assert store.receipts()[-1].result is ReceiptResult.AMBIGUOUS
        assert store.get_effect("refund:txn_1").state is EffectState.AMBIGUOUS

        with context(agent="refund-agent"), pytest.raises(AmbiguousEffect):
            refund("txn_1")
        assert server.accepted == 1, "the refused retry never reached the peer"


@pytest.mark.parametrize("surface", SURFACES)
def test_T220_a_read_timeout_after_the_request_is_the_original_exception(surface):
    """Precondition: the peer read the request, so the timeout fired after a byte was offered."""
    with peer(read_then_hang) as server:
        raised = _raised(lambda: call(surface, server.port, timeout=0.3))
        server.join()

    assert len(server.received) >= 1, "precondition: the peer received a request byte"
    assert not isinstance(raised, NotExecuted), raised
    assert any(isinstance(item, TimeoutError) for item in _cause_chain(raised)), raised


# === T221: refused, DNS failure, connect timeout, TLS handshake failure are NotExecuted ==========


def _assert_not_executed(raised: BaseException, cause: type[BaseException]) -> None:
    assert isinstance(raised, NotExecuted), f"{type(raised).__name__}: {raised}"
    assert isinstance(raised.__cause__, cause), raised.__cause__
    # The message is what EXECUTION_FAILED.data.error records, so it names the evidence.
    assert type(raised.__cause__).__name__ in str(raised)


@pytest.mark.parametrize("surface", SURFACES)
def test_T221_a_refused_connection_is_NotExecuted_and_a_retry_is_admitted(surface, control, store):
    """Precondition: the port's listener has closed, so no peer exists to read a byte."""
    target = {"port": _refused_port()}
    with peer(answering(200)) as server:
        refund = _protected(control, lambda: call(surface, target["port"]))
        with context(agent="refund-agent"):
            raised = _caught(lambda: refund("txn_1"))

        _assert_not_executed(raised, ConnectionRefusedError)
        assert store.receipts()[-1].result is ReceiptResult.FAILED
        assert store.get_effect("refund:txn_1").state is EffectState.FAILED

        target["port"] = server.port
        with context(agent="refund-agent"):
            assert refund("txn_1") == 200
        server.join()
    assert store.get_effect("refund:txn_1").state is EffectState.COMMITTED
    assert store.get_effect("refund:txn_1").attempt == 2


@pytest.mark.parametrize("surface", SURFACES)
def test_T221_a_DNS_failure_is_NotExecuted(surface, monkeypatch):
    """A name that does not resolve, reproduced by making `getaddrinfo` raise for this one host.

    The only case here a test cannot produce on a real network without a network; the path is the
    real one (`http.client` resolves through `socket.getaddrinfo` inside `connect()`), and the claim
    rests on no byte having been offered, not on the exception's type (§2.3). Precondition: there is
    no address, so nothing can have received a byte.
    """
    real = socket.getaddrinfo

    def resolving(host, *args, **kwargs):
        if host == "unresolvable.ctrlrun.invalid":
            raise socket.gaierror(socket.EAI_NONAME, "nodename nor servname provided")
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolving)
    raised = _raised(lambda: call(surface, 80, host="unresolvable.ctrlrun.invalid"))

    _assert_not_executed(raised, socket.gaierror)


def _full_backlog() -> tuple[socket.socket, list[socket.socket]]:
    """A listener whose accept queue is full, so a further SYN is dropped rather than answered.

    Mechanism: fill the queue of a listener that never accepts until one connect times out. Linux
    and macOS both drop a SYN on a full queue by default (macOS after 128 here, Linux after the
    backlog); a platform that refused instead would end the fill with `ConnectionRefusedError`,
    and the test says so rather than passing on the refusal row by accident.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind((LOOPBACK, 0))
    listener.listen(0)
    port = listener.getsockname()[1]
    fillers: list[socket.socket] = []
    for _ in range(1024):
        filler = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        filler.settimeout(0.5)
        try:
            filler.connect((LOOPBACK, port))
        except TimeoutError:
            # A filler may also time out because this machine is busy, which would leave a
            # listener that still accepts and a test asserting nothing. Confirm with a probe
            # of its own, and keep filling while it connects.
            filler.close()
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.settimeout(0.5)
            try:
                probe.connect((LOOPBACK, port))
            except TimeoutError:
                probe.close()
                return listener, fillers
            fillers.append(probe)
            continue
        except ConnectionRefusedError:  # pragma: no cover - depends on the platform
            filler.close()
            for opened in fillers:
                opened.close()
            listener.close()
            pytest.fail(
                "this platform refuses on a full backlog; the connect-timeout mechanism is absent"
            )
        fillers.append(filler)
    pytest.fail("the backlog never filled within 1024 connections")  # pragma: no cover


@pytest.mark.parametrize("surface", SURFACES)
def test_T221_a_connect_timeout_is_NotExecuted(surface):
    """Mechanism: a full loopback backlog that drops the SYN (see `_full_backlog`).

    Precondition, asserted after: every connection the listener holds is drained and none carries a
    byte, so the timed-out attempt never reached a socket that could read one.
    """
    listener, fillers = _full_backlog()
    try:
        raised = _raised(lambda: call(surface, listener.getsockname()[1], timeout=0.5))
        _assert_not_executed(raised, TimeoutError)
        listener.setblocking(False)
        drained = 0
        while True:
            try:
                accepted, _ = listener.accept()
            except BlockingIOError:
                break
            with accepted:
                accepted.setblocking(False)
                with suppress(BlockingIOError):
                    assert accepted.recv(1) == b"", (
                        "precondition: no queued connection carries a byte"
                    )
            drained += 1
        assert drained >= 1
    finally:
        for filler in fillers:
            filler.close()
        listener.close()


@pytest.mark.parametrize("surface", SURFACES)
def test_T221_a_TLS_handshake_failure_is_NotExecuted(surface, server_tls, untrusting):
    """A loopback TLS server presenting a certificate the client's context rejects.

    Precondition, asserted: the server accepted the TCP connection (so this is the TLS stage, not a
    refusal) and its handshake failed, so it decrypted zero application bytes.
    """
    with peer(answering(200), wrap=server_tls) as server:
        raised = _raised(lambda: call(surface, server.port, tls=untrusting))
        server.join()

    assert server.accepted == 1, "precondition: the TCP connection reached the TLS server"
    assert server.handshake_errors, "precondition: the server's handshake failed"
    assert server.received == b"", "precondition: no application byte was decrypted"
    _assert_not_executed(raised, ssl.SSLCertVerificationError)


def test_T221_a_BaseException_from_connect_propagates_untouched(monkeypatch):
    """§2.3: an interrupt is never turned into a retry permission, although no byte was offered.

    Precondition: the same path with an `Exception` claims `NotExecuted` (the control below), so
    the classifier was in a position to claim and did not.
    """
    real = socket.getaddrinfo
    raising: dict[str, BaseException] = {}

    def resolving(host, *args, **kwargs):
        if host == "interrupted.ctrlrun.invalid":
            raise raising["exc"]
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolving)

    raising["exc"] = socket.gaierror(socket.EAI_NONAME, "control")
    control_raised = _raised(lambda: call("http.client", 80, host="interrupted.ctrlrun.invalid"))
    assert isinstance(control_raised, NotExecuted), "control: an Exception here is claimed"

    raising["exc"] = KeyboardInterrupt()
    raised = _raised(lambda: call("http.client", 80, host="interrupted.ctrlrun.invalid"))
    assert type(raised) is KeyboardInterrupt


def test_T221_an_exception_in_the_classifiers_own_bookkeeping_is_never_NotExecuted(
    monkeypatch, refused
):
    """The bookkeeping row of §2.3's table: an exception raised while deciding is that exception.

    Precondition: the same refused connect, with the bookkeeping intact, claims `NotExecuted`.
    """
    control_raised = _raised(lambda: call("http.client", refused))
    assert isinstance(control_raised, NotExecuted), "control: this connect is claimed"

    class Broken:
        def get(self) -> None:
            raise RuntimeError("the classifier's bookkeeping failed")

    monkeypatch.setattr(transport, "_EXECUTOR_RUN", Broken())
    raised = _raised(lambda: call("http.client", refused))

    assert type(raised) is RuntimeError, raised


# === T222: a sendall that raises part way is AMBIGUOUS =========================================


def peek_then_reset(state: Peer, conn: socket.socket) -> None:
    """Read nothing: peek to learn that bytes arrived, then reset with them unread."""
    peeked = conn.recv(65536, socket.MSG_PEEK)
    state.received += peeked
    _linger_reset(conn)


def test_T222_a_sendall_that_raises_part_way_is_the_original_exception():
    """A peer with a small receive buffer that reads nothing and resets, mid-body.

    Preconditions, asserted: the peer's socket received at least one byte, and fewer than the body,
    so `sendall` really did transfer some bytes and then raise; and the failure came out of
    `request()` (the send), not out of `getresponse()`.
    """
    body = b"x" * (32 * 1024 * 1024)
    with peer(peek_then_reset, rcvbuf=4096) as server:
        connection = transport.HTTPConnection(LOOPBACK, server.port, timeout=WAIT)
        try:
            raised = _raised(lambda: connection.request("POST", "/refunds", body=body))
        finally:
            connection.close()
        server.join()

    assert len(server.received) >= 1, "precondition: the peer's socket received a byte"
    assert len(server.received) < len(body), "precondition: the transfer was partial"
    assert isinstance(raised, OSError), raised
    assert not isinstance(raised, NotExecuted)


def test_T222_a_connection_that_raised_part_way_never_claims_again():
    """The mark is set before the first byte and never cleared (§1.4 item 9).

    After the partial `sendall` raised, the caller closes the connection and tries again on the
    same object, and the peer has gone, so the reconnect is refused. Preconditions, asserted: the
    first attempt delivered a byte, and a fresh connection to the same port claims `NotExecuted`
    (the control). A mark counted only after a successful call would have missed the first
    attempt, and this retry would claim that nothing happened.
    """
    # A header section larger than both socket buffers, so the *first* `send` is the one that
    # raises part way: a mark set only after a successful call would never have been set.
    large = {"X-Large": "x" * (32 * 1024 * 1024)}
    with peer(peek_then_reset, rcvbuf=4096) as server:
        connection = transport.HTTPConnection(LOOPBACK, server.port, timeout=WAIT)
        first = _raised(lambda: connection.request("POST", "/refunds", b"{}", large))
        server.join()
        port = server.port
    assert len(server.received) >= 1, "precondition: the first attempt delivered a byte"
    assert not isinstance(first, NotExecuted)
    assert isinstance(_raised(lambda: call("http.client", port)), NotExecuted), "control"

    connection.close()
    raised = _raised(lambda: connection.request("POST", "/refunds", body=b"{}"))
    connection.close()
    assert isinstance(raised, ConnectionRefusedError), raised
    assert not isinstance(raised, NotExecuted)


def test_T222_through_urlopen():
    """The same, through `urlopen`, which wraps the socket error as `urllib` does."""
    body = b"x" * (32 * 1024 * 1024)
    with peer(peek_then_reset, rcvbuf=4096) as server:
        raised = _raised(lambda: call("urlopen", server.port, body=body))
        server.join()

    assert len(server.received) >= 1, "precondition: the peer's socket received a byte"
    assert not isinstance(raised, NotExecuted), raised


# === T223: a connection the classifier did not open never claims ================================


def test_T223_a_socket_the_caller_set_never_claims(refused):
    """A caller's own socket on the connection, then a connect the classifier would claim.

    Precondition (the control): the identical `connect()` to the identical refused port, on a
    connection that never held a caller's socket, raises `NotExecuted`.
    """
    clean = transport.HTTPConnection(LOOPBACK, refused, timeout=WAIT)
    assert isinstance(_raised(clean.connect), NotExecuted), "control: a clean connect is claimed"

    caller = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        dirty = transport.HTTPConnection(LOOPBACK, refused, timeout=WAIT)
        dirty.sock = caller
        raised = _raised(dirty.connect)
        assert not isinstance(raised, NotExecuted), raised
        assert isinstance(raised, ConnectionRefusedError)

        # Set and then cleared: the connection still held a socket it did not open.
        cleared = transport.HTTPConnection(LOOPBACK, refused, timeout=WAIT)
        cleared.sock = caller
        cleared.sock = None
        raised = _raised(lambda: cleared.request("POST", "/refunds", body=b"{}"))
        assert not isinstance(raised, NotExecuted), raised
    finally:
        caller.close()


def test_T223_a_caller_socket_that_fails_on_write_never_claims():
    """A caller's socket that never connected: the write fails and no peer received a byte.

    Precondition: no byte can have reached any peer (the socket has none), which is exactly where a
    classifier that trusted the caller's socket would claim. It does not open the socket, so it
    does not claim.
    """
    caller = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        connection = transport.HTTPConnection(LOOPBACK, 9, timeout=WAIT)
        connection.sock = caller
        raised = _raised(lambda: connection.request("POST", "/refunds", body=b"{}"))
    finally:
        caller.close()

    assert isinstance(raised, OSError), raised
    assert not isinstance(raised, NotExecuted)


def test_T223_a_reused_connection_never_claims():
    """A second request on a connection whose first request succeeded and whose server went away.

    Precondition (the control): a fresh connection to the same, now refused, port raises
    `NotExecuted`, so the only difference is that this object already offered bytes.
    """
    with peer(answering(200)) as server:
        connection = transport.HTTPConnection(LOOPBACK, server.port, timeout=WAIT)
        connection.request("POST", "/refunds", body=b"{}")
        response = connection.getresponse()
        assert response.status == 200
        response.read()
        server.join()
        port = server.port
    # The listener is closed now; a connect to its port is refused.
    control_raised = _raised(lambda: call("http.client", port))
    assert isinstance(control_raised, NotExecuted), "control: a fresh connection is claimed"

    raised = _raised(lambda: connection.request("POST", "/refunds", body=b"{}"))
    connection.close()
    assert isinstance(raised, ConnectionRefusedError), raised
    assert not isinstance(raised, NotExecuted)


class _TheTestsOwnHandler(urllib.request.HTTPHandler):
    """A handler the test built, driving the classifier's own connection class."""

    def http_open(self, req):  # type: ignore[no-untyped-def]
        return self.do_open(transport.HTTPConnection, req)


def test_T223_an_opener_the_test_built_is_judged_by_the_run_not_by_who_built_it(refused):
    """An opener built with `urllib` handlers of the test's own, around the classifier's class.

    Its only connection, refused before any byte of the run was offered, is claimed, because the
    claim is true: nothing was sent (§12.2.2). What makes a foreign opener dangerous is a second
    connection after a delivered first, and the redirect test below opens that window.
    """
    opener = urllib.request.build_opener(_TheTestsOwnHandler)
    raised = _raised(lambda: opener.open(f"http://{LOOPBACK}:{refused}/", data=b"{}", timeout=WAIT))

    _assert_not_executed(raised, ConnectionRefusedError)


def test_T223_a_redirect_in_an_opener_the_test_built_never_claims(refused):
    """`build_opener` follows a `303` with a second connection, after the first request arrived.

    Preconditions, asserted: the first server received the request (the effect may have happened),
    and the second connection's target refuses, which on a fresh connection is claimed (the
    control). A classifier that judged each connection alone would say `NotExecuted` here about an
    effect that was delivered.
    """
    assert isinstance(_raised(lambda: call("http.client", refused)), NotExecuted), "control"
    location = f"http://{LOOPBACK}:{refused}/created"
    with peer(answering(303, {"Location": location})) as server:
        opener = urllib.request.build_opener(_TheTestsOwnHandler)
        raised = _raised(
            lambda: opener.open(
                f"http://{LOOPBACK}:{server.port}/refunds", data=b"{}", timeout=WAIT
            )
        )
        server.join()

    assert server.received.startswith(b"POST /refunds"), "precondition: the first request arrived"
    assert not isinstance(raised, NotExecuted), raised


# === The register: one executor run, every classifier send (§2.3, §12.2.9) ======================
#
# The first review found that every false `NotExecuted` it could produce came from *two*
# connections in one effect: the first delivered the request, the second was refused, and the
# second was judged alone. Each test below reproduces one of its cases against a real peer that
# receives the whole request first, and asserts that before anything else.


def act_then_reset(state: Peer, conn: socket.socket) -> None:
    """Read the whole request (the remote acts on it), then reset without answering."""
    state.received += _read_request(conn)
    _linger_reset(conn)


def act_then_close(state: Peer, conn: socket.socket) -> None:
    """Read the whole request (the remote acts on it), then close without answering."""
    state.received += _read_request(conn)


class _XMLRPCThroughTheClassifier(xmlrpc.client.Transport):
    """`make_connection` is xmlrpc's documented override point; its `request` retries once,
    on a new connection, after a reset or a peer that closed without answering."""

    def make_connection(self, host):  # type: ignore[no-untyped-def]
        chost, self._extra_headers, _ = self.get_host_info(host)
        return transport.HTTPConnection(chost, timeout=WAIT)


@pytest.mark.parametrize("dying", [act_then_reset, act_then_close], ids=["reset", "close"])
def test_register_xmlrpcs_retry_after_a_delivered_request_never_claims(dying, control, store):
    """Precondition, asserted first: the peer received the whole POST, then died, so the retry's
    connection is refused. Judged alone, that refusal is a connection that offered nothing; the
    register knows this run already offered the request."""
    with peer(dying, one_shot=True) as server:

        @protect("refund.create", control=control)
        def refund(payment_id: str) -> Any:
            proxy = xmlrpc.client.ServerProxy(
                f"http://{LOOPBACK}:{server.port}/RPC2", transport=_XMLRPCThroughTheClassifier()
            )
            return proxy.refunds.create(payment_id, 100)

        with context(agent="refund-agent"):
            raised = _caught(lambda: refund("txn_1"))
        server.join()

        assert b"refunds.create" in server.received, "precondition: the request was delivered"
        assert not isinstance(raised, NotExecuted), raised
        assert store.receipts()[-1].result is ReceiptResult.AMBIGUOUS
        assert store.get_effect("refund:txn_1").state is EffectState.AMBIGUOUS
        with context(agent="refund-agent"), pytest.raises(AmbiguousEffect):
            refund("txn_1")


def test_register_an_executors_own_retry_around_urlopen_never_claims(control, store):
    """The most common composition there is: retry once on a reset. Precondition, asserted: the
    first attempt's POST reached the peer before it reset and died."""
    with peer(act_then_reset, one_shot=True) as server:

        @protect("refund.create", control=control)
        def refund(payment_id: str) -> int:
            for attempt in range(2):
                try:
                    return call("urlopen", server.port)
                except (ConnectionResetError, urllib.error.URLError):
                    if attempt:
                        raise
            raise AssertionError("unreachable")

        with context(agent="refund-agent"):
            raised = _caught(lambda: refund("txn_1"))
        server.join()

    assert server.received.startswith(b"POST /refunds"), "precondition: the request was delivered"
    assert not isinstance(raised, NotExecuted), raised
    assert store.get_effect("refund:txn_1").state is EffectState.AMBIGUOUS


def _redirecting_to(refused: int) -> Callable[[Peer, socket.socket], None]:
    return answering(303, {"Location": f"http://{LOOPBACK}:{refused}/created"})


@pytest.mark.skipif(
    not hasattr(urllib.request, "FancyURLopener"), reason="FancyURLopener was removed in 3.14"
)
def test_register_FancyURLopener_following_a_303_never_claims(refused):
    """The legacy opener follows a `303` through `URLopener.open`, which no stack heuristic knew.
    Preconditions: the POST arrived, and the refused target claims on a fresh run (the control)."""
    assert isinstance(_raised(lambda: call("http.client", refused)), NotExecuted), "control"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)

        class Opener(urllib.request.FancyURLopener):  # type: ignore[misc]
            def open_http(self, url, data=None):  # type: ignore[no-untyped-def]
                return self._open_generic_http(transport.HTTPConnection, url, data)

        opener = Opener()
    with peer(_redirecting_to(refused)) as server:
        raised = _raised(
            lambda: opener.open(f"http://{LOOPBACK}:{server.port}/refunds", data=b"amount=100")
        )
        server.join()

    assert server.received.startswith(b"POST /refunds"), "precondition: the request was delivered"
    assert not isinstance(raised, NotExecuted), raised


class _ThreadHoppingHandler(urllib.request.HTTPHandler):
    """A handler of the caller's that runs each connection on a worker thread."""

    pool = ThreadPoolExecutor(1)

    def http_open(self, req):  # type: ignore[no-untyped-def]
        return self.pool.submit(self.do_open, transport.HTTPConnection, req).result()


def test_register_an_opener_that_hops_threads_never_claims(refused):
    """A worker thread that did not copy the executor's context has no register, and a
    connection with no register never claims. Precondition: the POST arrived before the 303."""
    opener = urllib.request.build_opener(_ThreadHoppingHandler)
    with peer(_redirecting_to(refused)) as server:
        raised = _raised(
            lambda: opener.open(
                f"http://{LOOPBACK}:{server.port}/refunds", data=b"{}", timeout=WAIT
            )
        )
        server.join()

    assert server.received.startswith(b"POST /refunds"), "precondition: the request was delivered"
    assert not isinstance(raised, NotExecuted), raised


def test_register_a_callers_opener_around_the_classifiers_own_handler_never_claims(refused):
    """The classifier's private handler plus a redirect handler, assembled by a caller: the code
    that built the opener is irrelevant now, only what the run offered. Precondition: the POST
    arrived, and the refused target claims on a fresh run (the control)."""
    assert isinstance(_raised(lambda: call("http.client", refused)), NotExecuted), "control"
    opener = urllib.request.OpenerDirector()
    for handler in (
        transport._HTTPHandler(),
        urllib.request.HTTPRedirectHandler(),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    with peer(_redirecting_to(refused)) as server:
        raised = _raised(
            lambda: opener.open(f"http://{LOOPBACK}:{server.port}/refunds", b"{}", WAIT)
        )
        server.join()

    assert server.received.startswith(b"POST /refunds"), "precondition: the request was delivered"
    assert not isinstance(raised, NotExecuted), raised


def test_register_a_second_connection_in_one_run_after_the_first_delivered_never_claims(refused):
    """The shape every case above reduces to, with no library in between: two connection
    objects, the first delivered and answered, the second refused. Precondition (the control):
    the second connection alone, in a run of its own, is claimed."""
    assert isinstance(_raised(lambda: call("http.client", refused)), NotExecuted), "control"

    def two_connections() -> int:
        call("http.client", server.port)
        return call("http.client", refused)

    with peer(answering(200)) as server:
        raised = _raised(two_connections)
        server.join()

    assert server.received.startswith(b"POST /refunds"), "precondition: the first was delivered"
    assert isinstance(raised, ConnectionRefusedError), raised


def test_register_outside_any_executor_run_nothing_is_claimed(refused):
    """No register, no claim: outside `Control` the kernel records nothing, and a thread that did
    not copy the executor's context cannot be seen. Precondition (the control): the identical
    call inside a run is claimed."""
    assert isinstance(_raised(lambda: call("http.client", refused)), NotExecuted), "control"

    raised = _caught(lambda: call("http.client", refused))

    assert isinstance(raised, ConnectionRefusedError), raised


def test_register_a_thread_claims_only_with_the_executors_context(refused):
    """A thread started plainly has no register and never claims; one run under a copy of the
    executor's context shares it, and its refused first connection is claimed."""
    outcomes: dict[str, BaseException] = {}

    def on_threads() -> None:
        def attempt(label: str) -> None:
            outcomes[label] = _caught(lambda: call("http.client", refused))

        plain = threading.Thread(target=attempt, args=("plain",))
        plain.start()
        plain.join(WAIT)
        copied = contextvars.copy_context()
        shared = threading.Thread(target=copied.run, args=(attempt, "copied"))
        shared.start()
        shared.join(WAIT)

    in_run(on_threads)

    assert isinstance(outcomes["plain"], ConnectionRefusedError), outcomes["plain"]
    assert isinstance(outcomes["copied"], NotExecuted), outcomes["copied"]


def test_register_a_send_on_a_thread_without_the_register_still_marks_its_connection():
    """The per-object mark is not subsumed by the register. A thread with no register delivers a
    request on a connection; the executor's own thread then reuses that connection, which
    reconnects and is refused. The run's register saw nothing; the connection did.

    Preconditions, asserted: the peer received the request, and a fresh connection to the same
    refused port in the same kind of run is claimed (the control).
    """
    with peer(answering(200)) as server:
        connection = transport.HTTPConnection(LOOPBACK, server.port, timeout=WAIT)

        def deliver() -> None:
            connection.request("POST", "/refunds", body=b"{}")
            connection.getresponse().read()

        worker = threading.Thread(target=deliver)
        worker.start()
        worker.join(WAIT)
        server.join()
        port = server.port
    assert server.received.startswith(b"POST /refunds"), "precondition: the request was delivered"
    assert isinstance(_raised(lambda: call("http.client", port)), NotExecuted), "control"

    raised = _raised(lambda: connection.request("POST", "/refunds", body=b"{}"))
    connection.close()

    assert isinstance(raised, ConnectionRefusedError), raised


def test_register_a_nested_protected_call_marks_the_run_that_contains_it(control, store, refused):
    """An executor that calls another protected function which delivers a request, then fails to
    connect on its own: the outer run offered bytes through the inner one. Preconditions: the
    inner request arrived, and the same refused connect in a run of its own is claimed."""
    assert isinstance(_raised(lambda: call("http.client", refused)), NotExecuted), "control"
    inner_policy = Control(Policy.from_yaml(_RUN_POLICY), InMemoryStateStore())

    with peer(answering(200)) as server:

        @protect("transport.call", control=inner_policy)
        def debit() -> int:
            return call("http.client", server.port)

        @protect("refund.create", control=control)
        def refund(payment_id: str) -> int:
            debit()
            return call("http.client", refused)

        with context(agent="refund-agent"):
            raised = _caught(lambda: refund("txn_1"))
        server.join()

    assert server.received.startswith(b"POST /refunds"), "precondition: the inner call delivered"
    assert isinstance(raised, ConnectionRefusedError), raised
    assert store.get_effect("refund:txn_1").state is EffectState.AMBIGUOUS


def test_register_an_instrumented_opener_no_longer_suppresses_a_true_claim(monkeypatch, refused):
    """A wrapper on `OpenerDirector.open`, the shape `opentelemetry-instrumentation-urllib`
    installs, made `urlopen`'s own opener look foreign to the stack heuristic this replaced, and
    a refused connect that sent nothing came back `AMBIGUOUS`. Nothing was sent here either."""
    original = urllib.request.OpenerDirector.open

    @functools.wraps(original)
    def instrumented(opener, fullurl, data=None, timeout=None):  # type: ignore[no-untyped-def]
        def wrapped():  # type: ignore[no-untyped-def]
            return original(opener, fullurl, data=data, timeout=timeout)

        return wrapped()

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", instrumented)

    raised = _raised(lambda: call("urlopen", refused))

    _assert_not_executed(raised, ConnectionRefusedError)


def test_register_no_stack_heuristic_remains():
    """§12.2.2: the stack walk is gone, not bypassed. No frame is inspected anywhere in the
    module, and the opener is `urllib`'s own class."""
    source = TRANSPORT_SOURCE.read_text(encoding="utf-8")
    names = {
        node.attr if isinstance(node, ast.Attribute) else node.id
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.Attribute, ast.Name))
    }
    assert not names & {"_getframe", "f_back", "f_code", "f_locals", "stack", "currentframe"}
    assert "_Opener" not in names and "_inside_an_opener_it_did_not_build" not in names


# === T224: no HTTP status is NotExecuted ========================================================

STATUSES = (301, 303, 400, 401, 409, 429, 500, 503)


@pytest.mark.parametrize("status", STATUSES)
def test_T224_no_status_is_NotExecuted_through_urlopen(status):
    """Precondition: the server received and answered the request, so a status really came back.

    The `30x` is not followed: the server counts one connection, and a redirect handler would have
    made a second.
    """
    headers = {"Location": "/elsewhere"} if status in (301, 303) else {}
    with peer(answering(status, headers)) as server:
        raised = _raised(lambda: call("urlopen", server.port))
        server.join()
        followed = server.queued()

    assert server.received.startswith(b"POST /refunds"), "precondition: the request arrived"
    assert followed == 0, "a redirect was followed"
    assert isinstance(raised, urllib.error.HTTPError), raised
    assert raised.code == status
    assert not isinstance(raised, NotExecuted)


@pytest.mark.parametrize("status", STATUSES)
def test_T224_no_status_is_NotExecuted_through_http_client(status):
    """Precondition: as above. `http.client` returns every status; nothing is raised at all."""
    with peer(answering(status, {"Location": "/elsewhere"})) as server:
        assert call("http.client", server.port) == status
        server.join()
    assert server.received.startswith(b"POST /refunds")


# === T225: proxies ===============================================================================


def test_T225_an_unreachable_proxy_is_NotExecuted(monkeypatch, refused):
    """Precondition: the proxy's listener has closed, so nothing received a byte."""
    _proxy_environment(monkeypatch, f"http://{LOOPBACK}:{refused}")

    raised = _raised(lambda: call("urlopen", 80, host="target.ctrlrun.invalid"))

    _assert_not_executed(raised, ConnectionRefusedError)


def refusing_tunnel(state: Peer, conn: socket.socket) -> None:
    state.received += _read_request(conn)
    conn.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")


@pytest.mark.parametrize("surface", SURFACES)
def test_T225_a_refused_CONNECT_after_the_line_was_sent_is_the_original_exception(
    surface, monkeypatch, trusting
):
    """Precondition, asserted: the proxy received the `CONNECT` line. The target received nothing,
    and §2.3 counts the line as written anyway: conservative, never a false `NotExecuted`."""
    with peer(refusing_tunnel) as proxy:
        if surface == "urlopen":
            _proxy_environment(monkeypatch, f"http://{LOOPBACK}:{proxy.port}")
            raised = _raised(
                lambda: call("urlopen", 443, host="target.ctrlrun.invalid", tls=trusting)
            )
        else:
            connection = transport.HTTPSConnection(
                LOOPBACK, proxy.port, timeout=WAIT, context=trusting
            )
            connection.set_tunnel("target.ctrlrun.invalid", 443)
            raised = _raised(lambda: connection.request("POST", "/refunds", body=b"{}"))
            connection.close()
        proxy.join()

    assert proxy.received.startswith(b"CONNECT target.ctrlrun.invalid:443"), "precondition"
    assert not isinstance(raised, NotExecuted), raised


def test_T225_TLS_to_the_target_failing_after_the_tunnel_opened_is_the_original_exception(
    server_tls, untrusting
):
    """§2.3's tunnel row, second half: the `CONNECT` was answered `200`, then the handshake with
    the target failed. Preconditions, asserted: the proxy received the `CONNECT` line, and the
    target's handshake failed, so no application byte was decrypted. The line counts as written."""
    state: dict[str, Any] = {"decrypted": b"", "handshake": None}

    def tunnel_then_tls(proxy: Peer, conn: socket.socket) -> None:
        proxy.received += _read_request(conn)
        conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        try:
            wrapped = server_tls.wrap_socket(conn, server_side=True)
        except (OSError, ssl.SSLError) as exc:
            state["handshake"] = exc
            return
        state["decrypted"] = wrapped.recv(65536)

    with peer(tunnel_then_tls) as proxy:
        connection = transport.HTTPSConnection(
            LOOPBACK, proxy.port, timeout=WAIT, context=untrusting
        )
        connection.set_tunnel("target.ctrlrun.invalid", 443)
        raised = _raised(lambda: connection.request("POST", "/refunds", body=b"{}"))
        connection.close()
        proxy.join()

    assert proxy.received.startswith(b"CONNECT target.ctrlrun.invalid:443"), "precondition"
    assert state["handshake"] is not None and state["decrypted"] == b"", "precondition"
    assert isinstance(raised, ssl.SSLError), raised
    assert not isinstance(raised, NotExecuted)


def test_T225_a_proxy_that_accepted_the_request_returns_its_status(monkeypatch):
    """A forward proxy that took the request and failed upstream answers with a status (§2.3)."""
    with peer(answering(502)) as proxy:
        _proxy_environment(monkeypatch, f"http://{LOOPBACK}:{proxy.port}")
        raised = _raised(lambda: call("urlopen", 80, host="target.ctrlrun.invalid"))
        proxy.join()

    assert proxy.received.startswith(b"POST http://target.ctrlrun.invalid:80/refunds")
    assert isinstance(raised, urllib.error.HTTPError) and raised.code == 502


def test_T225_an_ftp_URL_never_reaches_a_proxy(monkeypatch):
    """`urlopen` accepts `http` and `https` and nothing else (§2.8), before its opener runs.

    Precondition: `ftp_proxy` names a live listener, and urllib's proxy handler would route an
    `ftp:` URL to it over HTTP, so a missing scheme check would be visible there as a request.
    """
    with silent_listener() as (port, arrived):
        monkeypatch.delenv("no_proxy")
        monkeypatch.setenv("ftp_proxy", f"http://{LOOPBACK}:{port}")
        raised = _raised(lambda: transport.urlopen("ftp://target.ctrlrun.invalid/f", timeout=1))
        assert arrived() == 0, "a connection reached the proxy"

    assert isinstance(raised, urllib.error.URLError), raised
    assert not isinstance(raised, NotExecuted)


def test_T225_a_proxy_of_a_scheme_the_opener_cannot_speak_refuses_before_any_byte(monkeypatch):
    """`http_proxy=socks5://...`: urllib cannot speak SOCKS, and without its unknown-scheme
    handler it would send the HTTP request to the SOCKS port as if it were an HTTP proxy.

    Precondition: the named port is a live listener, so a request sent there would be seen.
    """
    with silent_listener() as (port, arrived):
        _proxy_environment(monkeypatch, f"socks5://{LOOPBACK}:{port}")
        raised = _raised(lambda: call("urlopen", 80, host="target.ctrlrun.invalid", timeout=1))
        assert arrived() == 0, "a connection reached the SOCKS port"

    assert isinstance(raised, urllib.error.URLError), raised
    assert not isinstance(raised, NotExecuted)


def test_T225_urlopen_passes_the_TLS_context_through(server_tls, trusting):
    """The positive half of the TLS rows: with a context that trusts the server, the request
    goes through, so the handshake failures above are the context's doing and nothing else."""
    with peer(answering(200), wrap=server_tls) as server:
        assert call("urlopen", server.port, tls=trusting) == 200
        server.join()
    assert server.received.startswith(b"POST /refunds")


def test_T225_an_exception_before_any_connection_is_the_original_exception():
    """A URL nothing can be sent to: the classifier opened no connection, so it claims nothing.

    Precondition: no socket exists, so no byte was offered; the fail-closed direction costs the
    `NotExecuted` a human will have to supply (§2.3).
    """
    for url in ("ftp://127.0.0.1/file", "file:///etc/hosts", "data:,x", "http:///no-host"):
        raised = _raised(lambda url=url: transport.urlopen(url, timeout=WAIT))
        assert isinstance(raised, urllib.error.URLError), (url, raised)
        assert not isinstance(raised, NotExecuted)
    raised = _raised(lambda: transport.urlopen("not a url", timeout=WAIT))
    assert isinstance(raised, ValueError)


# === T226: the httpx variant, and the gateway uses it ===========================================


def test_T226_the_httpx_variant_claims_only_a_connection_never_established(refused):
    """Precondition for the second half: the peer received the request bytes before it reset."""
    httpx = pytest.importorskip("httpx", reason="the gateway extra is not installed")
    from ctrlrun.gateway.transport import request

    raised = _raised(
        lambda: request("POST", f"http://{LOOPBACK}:{refused}/", content=b"{}", timeout=WAIT)
    )
    assert isinstance(raised, NotExecuted), raised
    assert isinstance(raised.__cause__, httpx.ConnectError)
    assert "ConnectError" in str(raised)

    with peer(read_then_reset) as server:
        raised = _raised(
            lambda: request(
                "POST", f"http://{LOOPBACK}:{server.port}/", content=b"{}", timeout=WAIT
            )
        )
        server.join()
    assert len(server.received) >= 1, "precondition: the peer received a request byte"
    assert not isinstance(raised, NotExecuted), raised
    assert isinstance(raised, httpx.HTTPError)


def test_T226_the_httpx_variant_returns_a_status_and_follows_no_redirect():
    pytest.importorskip("httpx", reason="the gateway extra is not installed")
    from ctrlrun.gateway.transport import request

    with peer(answering(303, {"Location": "/elsewhere"})) as server:
        response = request("POST", f"http://{LOOPBACK}:{server.port}/", content=b"{}", timeout=WAIT)
        server.join()
        followed = server.queued()
    assert response.status_code == 303
    assert followed == 0, "a redirect was followed"


def test_T226_no_client_can_be_passed():
    pytest.importorskip("httpx", reason="the gateway extra is not installed")
    from ctrlrun.gateway.transport import request

    assert list(inspect.signature(request).parameters) == [
        "method",
        "url",
        "content",
        "headers",
        "timeout",
    ]
    with pytest.raises(TypeError):
        request("GET", "http://127.0.0.1:9/", timeout=1.0, client=object())  # type: ignore[call-arg]


def test_T226_the_forwarders_fresh_path_calls_the_same_observation_function(monkeypatch, refused):
    """By identity: a spy on `ctrlrun.gateway.transport._observed` is the one both paths reach."""
    httpx = pytest.importorskip("httpx", reason="the gateway extra is not installed")
    from ctrlrun.gateway import transport as gateway_transport

    seen: list[str] = []
    original = gateway_transport._observed

    def spy(exc, client, **options):
        seen.append(type(exc).__name__)
        return original(exc, client, **options)

    monkeypatch.setattr(gateway_transport, "_observed", spy)

    forwarder = gateway_transport.HTTPForwarder(f"http://{LOOPBACK}:{refused}/mcp", WAIT, httpx)
    try:
        observed, payload, status, _ = forwarder(b'{"jsonrpc":"2.0","id":1}', {}, fresh=True)
    finally:
        forwarder.close()
    assert observed is transport.Transport.NEVER_CONNECTED
    assert payload is None and status == 502
    assert seen == ["ConnectError"], "the forwarder's fresh path reached the spy"

    _raised(
        lambda: gateway_transport.request("POST", f"http://{LOOPBACK}:{refused}/", timeout=WAIT)
    )
    assert seen == ["ConnectError", "ConnectError"], "request() reached the same spy"


def _gateway_for(tmp_path: Path, upstream: str, timeout: float = WAIT) -> tuple[Any, Any, Any]:
    """A gateway in front of `upstream`, its store, and its forwarder (to close)."""
    from ctrlrun.gateway.server import Gateway, GatewayConfig, httpx_forwarder

    policy = """
schema: ctrlrun.policy/v2
actions:
  mcp.acme.create_refund:
    effect: "refund:{payment_id}"
    decision: allow
"""
    opened = SQLiteStateStore(tmp_path / "gateway.db")
    config = GatewayConfig(
        upstream=upstream, alias="acme", principal="refund-agent", upstream_timeout=timeout
    )
    forwarder = httpx_forwarder(config)
    return Gateway(config, Control(Policy.from_yaml(policy), opened), forwarder), opened, forwarder


def _tools_call(gateway: Any) -> Any:
    import json

    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "create_refund", "arguments": {"payment_id": "txn_1"}},
    }
    headers = {
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": "tools/call",
        "Mcp-Name": "create_refund",
    }
    return json.loads(gateway.handle(json.dumps(body).encode(), headers).body)


def test_T226_a_read_timeout_after_the_request_is_the_httpx_exception_everywhere(tmp_path):
    """`httpx.ReadTimeout` after a delivered request: `request()` raises it untouched, the
    forwarder's fresh path observes `AFTER_REQUEST_SENT`, and the gateway answers `-41010`.

    Precondition, asserted each time: the peer received the request before the read timed out. A
    mapping that caught `httpx.TimeoutException` where it means `httpx.ConnectTimeout` would claim
    `NotExecuted` here, and until this test nothing exercised the difference.
    """
    httpx = pytest.importorskip("httpx", reason="the gateway extra is not installed")
    from ctrlrun.gateway import transport as gateway_transport

    with peer(read_then_hang) as server:
        url = f"http://{LOOPBACK}:{server.port}/refunds"
        raised = _raised(lambda: gateway_transport.request("POST", url, content=b"{}", timeout=0.3))
        server.join()
    assert server.received.startswith(b"POST /refunds"), "precondition: the request was delivered"
    assert isinstance(raised, httpx.ReadTimeout), raised

    with peer(read_then_hang) as server:
        forwarder = gateway_transport.HTTPForwarder(
            f"http://{LOOPBACK}:{server.port}/mcp", 0.3, httpx
        )
        try:
            observed, _, _, _ = forwarder(b'{"jsonrpc":"2.0","id":1}', {}, fresh=True)
        finally:
            forwarder.close()
        server.join()
    assert server.received.startswith(b"POST /mcp"), "precondition: the request was delivered"
    assert observed is transport.Transport.AFTER_REQUEST_SENT

    with peer(read_then_hang) as server:
        gateway, opened, forwarder = _gateway_for(
            tmp_path, f"http://{LOOPBACK}:{server.port}/mcp", timeout=0.3
        )
        try:
            answer = _tools_call(gateway)
        finally:
            forwarder.close()
        server.join()
    assert server.received.startswith(b"POST /mcp"), "precondition: the request was delivered"
    assert answer["error"]["code"] == -41010
    assert opened.get_effect("refund:txn_1").state is EffectState.AMBIGUOUS
    opened.close()


def _httpx_through(proxy_url: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    httpx = pytest.importorskip("httpx", reason="the gateway extra is not installed")
    _proxy_environment(monkeypatch, proxy_url)
    return httpx


def test_T226_behind_a_proxy_a_refused_CONNECT_is_the_httpx_exception(monkeypatch):
    """§2.3's tunnel row, for httpx: the `CONNECT` line was written, so nothing is claimed.
    Precondition, asserted: the proxy received the `CONNECT` line."""
    from ctrlrun.gateway import transport as gateway_transport

    with peer(refusing_tunnel) as proxy:
        httpx = _httpx_through(f"http://{LOOPBACK}:{proxy.port}", monkeypatch)
        raised = _raised(
            lambda: gateway_transport.request(
                "POST", "https://target.ctrlrun.invalid/refunds", content=b"{}", timeout=WAIT
            )
        )
        proxy.join()
    assert proxy.received.startswith(b"CONNECT target.ctrlrun.invalid:443"), "precondition"
    assert isinstance(raised, httpx.HTTPError) and not isinstance(raised, NotExecuted), raised


def test_T226_behind_a_proxy_a_TLS_failure_after_the_tunnel_opened_is_never_claimed(
    monkeypatch, server_tls
):
    """The review's case: the `CONNECT` was answered `200`, then the handshake with the target
    failed, and httpx reports that as `httpx.ConnectError`, the same type as a refusal. Behind a
    proxy httpx cannot say which of the two it was, so neither is claimed (§12.2.10).

    Preconditions, asserted: the proxy received the `CONNECT` line and the target's handshake
    failed, so no application byte was decrypted.
    """
    from ctrlrun.gateway import transport as gateway_transport

    state: dict[str, Any] = {"handshake": None}

    def tunnel_then_tls(proxy: Peer, conn: socket.socket) -> None:
        proxy.received += _read_request(conn)
        conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        try:
            server_tls.wrap_socket(conn, server_side=True)
        except (OSError, ssl.SSLError) as exc:
            state["handshake"] = exc

    for surface in ("request", "forwarder"):
        with peer(tunnel_then_tls) as proxy:
            httpx = _httpx_through(f"http://{LOOPBACK}:{proxy.port}", monkeypatch)
            target = "https://target.ctrlrun.invalid/refunds"
            if surface == "request":
                raised = _raised(
                    lambda target=target: gateway_transport.request(
                        "POST", target, content=b"{}", timeout=WAIT
                    )
                )
                assert isinstance(raised, httpx.ConnectError), raised
            else:
                forwarder = gateway_transport.HTTPForwarder(target, WAIT, httpx)
                try:
                    observed, _, _, _ = forwarder(b"{}", {}, fresh=True)
                finally:
                    forwarder.close()
                assert observed is transport.Transport.AFTER_REQUEST_SENT, surface
            proxy.join()
        assert proxy.received.startswith(b"CONNECT target.ctrlrun.invalid:443"), surface
        assert state["handshake"] is not None, "precondition: the target's handshake failed"


def test_T226_behind_a_proxy_even_an_unreachable_proxy_is_not_claimed(monkeypatch, refused):
    """Stricter than core, on purpose: httpx reports an unreachable proxy and a failed tunnel as
    the same type, so behind a proxy it claims neither. `urlopen` claims the unreachable proxy
    (T225) because it can see that no byte was offered; httpx cannot."""
    from ctrlrun.gateway import transport as gateway_transport

    httpx = _httpx_through(f"http://{LOOPBACK}:{refused}", monkeypatch)
    raised = _raised(
        lambda: gateway_transport.request("POST", "http://target.ctrlrun.invalid/", timeout=WAIT)
    )

    assert isinstance(raised, httpx.ConnectError), raised


def test_T226_a_proxy_the_environment_bypasses_entirely_still_claims(monkeypatch, refused):
    """`NO_PROXY=*` is how httpx is told to ignore every proxy, and then there is no proxy in the
    way: a refused connection is claimed as it would be with nothing configured at all.

    Precondition (the control): with the same proxy named and `NO_PROXY` unset, the identical call
    is not claimed, so the bypass is what decides and not the absence of a proxy variable.
    """
    from ctrlrun.gateway import transport as gateway_transport

    target = f"http://{LOOPBACK}:{refused}/refunds"
    _httpx_through(f"http://{LOOPBACK}:{refused}", monkeypatch)
    assert not isinstance(
        _raised(lambda: gateway_transport.request("POST", target, timeout=WAIT)), NotExecuted
    ), "control: behind a proxy nothing is claimed"

    monkeypatch.setenv("no_proxy", "*")
    raised = _raised(lambda: gateway_transport.request("POST", target, timeout=WAIT))

    assert isinstance(raised, NotExecuted), raised


def test_T226_the_proxy_is_read_when_the_call_starts_not_when_it_fails(monkeypatch, refused):
    """The client takes its proxies when it is built, so the answer must be read there too.

    A `CONNECT` line is written, the environment loses its proxy while the call is in flight, and
    the tunnel then fails. Read at the moment of the exception, the answer would be "no proxy" and
    the written line would be forgotten. Preconditions, asserted: the proxy received the `CONNECT`
    line, and the environment really was cleared before the call failed.
    """
    pytest.importorskip("httpx", reason="the gateway extra is not installed")
    from ctrlrun.gateway import transport as gateway_transport

    cleared: dict[str, Any] = {}

    def connect_then_vanish(proxy: Peer, conn: socket.socket) -> None:
        proxy.received += _read_request(conn)
        for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
            os.environ.pop(name, None)
        cleared["at"] = dict(os.environ)
        conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        _linger_reset(conn)

    with peer(connect_then_vanish) as proxy:
        _httpx_through(f"http://{LOOPBACK}:{proxy.port}", monkeypatch)
        raised = _raised(
            lambda: gateway_transport.request(
                "POST", "https://target.ctrlrun.invalid/refunds", content=b"{}", timeout=WAIT
            )
        )
        proxy.join()

    assert proxy.received.startswith(b"CONNECT target.ctrlrun.invalid:443"), "precondition"
    assert not any(name.lower().endswith("_proxy") for name in cleared["at"]), "precondition"
    assert not isinstance(raised, NotExecuted), raised


def test_T226_the_forwarder_marks_the_run_when_its_write_fails_part_way(refused):
    """The other half of the forwarder's mark: the request was going out when it failed.

    A peer that reads nothing and resets, and a body larger than the buffers, so httpx raises
    while writing. Preconditions, asserted: the peer's socket received a byte, the forwarder
    reports `AFTER_REQUEST_SENT`, and the same refused connection alone in a run is claimed.
    """
    httpx = pytest.importorskip("httpx", reason="the gateway extra is not installed")
    from ctrlrun.gateway import transport as gateway_transport

    assert isinstance(_raised(lambda: call("http.client", refused)), NotExecuted), "control"
    observations: list[Any] = []

    with peer(peek_then_reset, rcvbuf=4096) as server:
        forwarder = gateway_transport.HTTPForwarder(
            f"http://{LOOPBACK}:{server.port}/mcp", WAIT, httpx
        )

        def write_then_connect() -> int:
            observed, _, _, _ = forwarder(b"x" * (32 * 1024 * 1024), {}, fresh=True)
            observations.append(observed)
            return call("http.client", refused)

        try:
            raised = _raised(write_then_connect)
        finally:
            forwarder.close()
        server.join()

    assert len(server.received) >= 1, "precondition: the peer's socket received a byte"
    assert observations == [transport.Transport.AFTER_REQUEST_SENT], observations
    assert isinstance(raised, ConnectionRefusedError), raised


def test_T226_the_forwarder_reads_the_proxy_when_the_call_starts(monkeypatch):
    """The forwarder's half of the same rule: its client takes its proxies when it is built.

    Preconditions, asserted: the proxy received the `CONNECT` line, and the environment lost its
    proxy while the call was in flight, which read at the moment of the exception would make the
    written line invisible.
    """
    httpx = pytest.importorskip("httpx", reason="the gateway extra is not installed")
    from ctrlrun.gateway import transport as gateway_transport

    cleared: dict[str, Any] = {}

    def connect_then_vanish(proxy: Peer, conn: socket.socket) -> None:
        proxy.received += _read_request(conn)
        for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
            os.environ.pop(name, None)
        cleared["at"] = dict(os.environ)
        conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        _linger_reset(conn)

    with peer(connect_then_vanish) as proxy:
        _httpx_through(f"http://{LOOPBACK}:{proxy.port}", monkeypatch)
        forwarder = gateway_transport.HTTPForwarder(
            "https://target.ctrlrun.invalid/mcp", WAIT, httpx
        )
        try:
            observed, _, _, _ = forwarder(b'{"jsonrpc":"2.0","id":1}', {}, fresh=True)
        finally:
            forwarder.close()
        proxy.join()

    assert proxy.received.startswith(b"CONNECT target.ctrlrun.invalid:443"), "precondition"
    assert not any(name.lower().endswith("_proxy") for name in cleared["at"]), "precondition"
    assert observed is transport.Transport.AFTER_REQUEST_SENT


def test_T226_the_httpx_variant_marks_the_run_and_consults_it(refused):
    """One register for both variants: a request delivered through httpx, then a refused
    `HTTPConnection` in the same run, is not claimed; and a request delivered through the
    classifier, then a refused httpx connection, is not claimed either. Preconditions: each
    first request arrived, and each refused call alone is claimed (the controls)."""
    httpx = pytest.importorskip("httpx", reason="the gateway extra is not installed")
    from ctrlrun.gateway import transport as gateway_transport

    refused_url = f"http://{LOOPBACK}:{refused}/"
    assert isinstance(_raised(lambda: call("http.client", refused)), NotExecuted), "control"
    assert isinstance(
        _raised(lambda: gateway_transport.request("POST", refused_url, timeout=WAIT)), NotExecuted
    ), "control"

    with peer(answering(200)) as server:
        url = f"http://{LOOPBACK}:{server.port}/refunds"

        def httpx_then_core() -> int:
            gateway_transport.request("POST", url, content=b"{}", timeout=WAIT)
            return call("http.client", refused)

        raised = _raised(httpx_then_core)
        server.join()
    assert server.received.startswith(b"POST /refunds"), "precondition"
    assert isinstance(raised, ConnectionRefusedError), raised

    with peer(answering(200)) as server:

        def core_then_httpx() -> Any:
            call("http.client", server.port)
            return gateway_transport.request("POST", refused_url, timeout=WAIT)

        raised = _raised(core_then_httpx)
        server.join()
    assert server.received.startswith(b"POST /refunds"), "precondition"
    assert isinstance(raised, httpx.ConnectError), raised


# === The continuation leg: nothing claims FAILED on a resume (§2.3, §2.5, §12.2.12) ============
#
# A continuation exists only because the remote spoke: it comes from the remote's own answer, and
# the remote is holding the exchange. So a resumed leg can never truthfully say the remote did
# nothing, whatever happens to the continuation's own request.

MCP_REVISION = "2026-07-28"
MCP_POLICY = """
schema: ctrlrun.policy/v2
actions:
  mcp.acme.create_refund:
    effect: "refund:{payment_id}"
    decision: allow
"""


def test_resume_a_continuation_leg_never_claims(control, store, refused):
    """Preconditions, asserted: the first leg delivered the request and the remote answered by
    asking for more, which is the only reason a continuation exists; and the identical refused
    connect, in a first leg, is claimed (the control)."""
    assert isinstance(_raised(lambda: call("http.client", refused)), NotExecuted), "control"

    with peer(answering(200), one_shot=True) as server:

        @protect("refund.create", control=control)
        def refund(payment_id: str) -> str:
            call("http.client", server.port)
            raise Suspended("continuation-1")

        with context(agent="refund-agent"), pytest.raises(Suspended):
            refund("txn_1")
        server.join()
        port = server.port

    assert server.received.startswith(b"POST /refunds"), "precondition: the first leg delivered"
    assert store.get_effect("refund:txn_1").state is EffectState.EXECUTING

    # The remote is gone, so the continuation's connection is refused before any byte.
    raised = _caught(lambda: control.resume("continuation-1", lambda: call("http.client", port)))

    assert not isinstance(raised, NotExecuted), raised
    assert isinstance(raised, ConnectionRefusedError)
    assert store.receipts()[-1].result is ReceiptResult.AMBIGUOUS
    assert store.get_effect("refund:txn_1").state is EffectState.AMBIGUOUS
    with context(agent="refund-agent"), pytest.raises(AmbiguousEffect):
        refund("txn_1")


class _McpUpstream:
    """A real MCP upstream on a real socket. The first `tools/call` is answered `input_required`
    with a `requestState`, so the upstream is holding the exchange; the continuation is answered
    by whatever the case under test installed."""

    def __init__(self, continuation_reply: Callable[[Any], None]) -> None:
        self.calls: list[Any] = []
        state = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                document = json.loads(raw)
                state.calls.append(document)
                if len(state.calls) == 1:
                    _mcp_reply(
                        self,
                        {
                            "jsonrpc": "2.0",
                            "id": document.get("id"),
                            "result": {
                                "resultType": "input_required",
                                "requestState": "server-state-1",
                                "isError": False,
                            },
                        },
                    )
                else:
                    continuation_reply(self)

            def log_message(self, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer((LOOPBACK, 0), Handler)
        self.port: int = self.server.server_address[1]
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def die(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(WAIT)

    def close(self) -> None:
        with suppress(Exception):
            self.die()


def _mcp_reply(handler: Any, document: Any, status: int = 200, headers: Any = None) -> None:
    payload = json.dumps(document).encode()
    handler.send_response(status)
    for name, value in (headers or {}).items():
        handler.send_header(name, value)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def _mcp_call(gateway: Any, *, state: str | None = None) -> Any:
    params: dict[str, Any] = {
        "name": "create_refund",
        "arguments": {"payment_id": "txn_1", "amount": 200},
    }
    if state is not None:
        params["requestState"] = state
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params}
    headers = {
        "MCP-Protocol-Version": MCP_REVISION,
        "Mcp-Method": "tools/call",
        "Mcp-Name": "create_refund",
    }
    response = gateway.handle(json.dumps(body).encode(), headers)
    return json.loads(response.body) if response.body else None


@contextmanager
def _mcp_gateway(tmp_path: Path, upstream: _McpUpstream) -> Iterator[tuple[Any, Any]]:
    from ctrlrun.gateway.server import Gateway, GatewayConfig, httpx_forwarder

    opened = SQLiteStateStore(tmp_path / "gateway.db")
    config = GatewayConfig(
        upstream=f"http://{LOOPBACK}:{upstream.port}/mcp",
        alias="acme",
        principal="refund-agent",
        upstream_timeout=WAIT,
    )
    forwarder = httpx_forwarder(config)
    try:
        yield Gateway(config, Control(Policy.from_yaml(MCP_POLICY), opened), forwarder), opened
    finally:
        forwarder.close()
        opened.close()


def _pre_dispatch(handler: Any) -> None:
    _mcp_reply(
        handler,
        {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "no such method"}},
    )


def _unauthorized(handler: Any) -> None:
    _mcp_reply(
        handler,
        {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "token expired"}},
        status=401,
        headers={"WWW-Authenticate": 'Bearer realm="upstream"'},
    )


@pytest.mark.parametrize("case", ["transport", "pre_dispatch", "unauthorized"])
def test_T231b_a_gateway_continuation_never_records_FAILED(tmp_path, case):
    """The upstream answered `input_required`, so it has the request and is holding the exchange.
    Whatever the continuation itself meets, the effect's state is unknown.

    Preconditions, asserted: the upstream received the original `tools/call` and answered
    `input_required` with a `requestState` (so this is a continuation), and each answer is one
    that on a **first** leg is `FAILED` (the controls below).
    """
    pytest.importorskip("httpx", reason="the gateway extra is not installed")
    replies = {"pre_dispatch": _pre_dispatch, "unauthorized": _unauthorized}
    upstream = _McpUpstream(replies.get(case, _pre_dispatch))
    try:
        with _mcp_gateway(tmp_path, upstream) as (gateway, opened):
            first = _mcp_call(gateway)
            assert first["result"]["resultType"] == "input_required", first
            assert upstream.calls, "precondition: the upstream received the original call"
            assert opened.get_effect("refund:txn_1").state is EffectState.EXECUTING
            if case == "transport":
                upstream.die()
            answer = _mcp_call(gateway, state="server-state-1")
            record = opened.get_effect("refund:txn_1")
    finally:
        upstream.close()

    assert record.state is EffectState.AMBIGUOUS, (case, answer)
    if case == "transport":
        assert answer["error"]["code"] == -41010, answer
    else:
        # The upstream's own answer is relayed unchanged; only what CTRLRun records changes.
        assert answer["error"]["code"] in (-32601, -32000), answer


@pytest.mark.parametrize("case", ["transport", "pre_dispatch", "unauthorized"])
def test_T231b_the_control_a_first_leg_still_records_FAILED(tmp_path, case, refused):
    """The other half: on a first leg each of those answers is still `FAILED` and still permits a
    retry. Without it, a gateway recording everything `AMBIGUOUS` would pass the test above."""
    pytest.importorskip("httpx", reason="the gateway extra is not installed")

    replies = {"pre_dispatch": _pre_dispatch, "unauthorized": _unauthorized}
    upstream = _McpUpstream(replies.get(case, _pre_dispatch))
    # One entry already there, so the upstream's very first real call takes the second branch and
    # answers what this case is about, on a leg that is nobody's continuation.
    upstream.calls.append("the count starts at one")
    try:
        with _mcp_gateway(tmp_path, upstream) as (gateway, opened):
            if case == "transport":
                upstream.die()
            answer = _mcp_call(gateway)
            record = opened.get_effect("refund:txn_1")
    finally:
        upstream.close()

    assert record.state is EffectState.FAILED, (case, answer)
    if case == "transport":
        assert answer["error"]["code"] == -41011, answer


# === The forwarder marks the run too (§2.5) =====================================================


def test_T226_the_forwarder_marks_the_run_it_writes_in(refused):
    """`HTTPForwarder` writes request bytes like everything else here, so it marks the register.

    Preconditions, asserted: the peer received the forwarded request, and the same refused
    connection alone in a run is claimed (the control).
    """
    httpx = pytest.importorskip("httpx", reason="the gateway extra is not installed")
    from ctrlrun.gateway import transport as gateway_transport

    assert isinstance(_raised(lambda: call("http.client", refused)), NotExecuted), "control"

    with peer(answering(200)) as server:
        forwarder = gateway_transport.HTTPForwarder(
            f"http://{LOOPBACK}:{server.port}/mcp", WAIT, httpx
        )

        def forward_then_connect() -> int:
            forwarder(b'{"jsonrpc":"2.0","id":1}', {}, fresh=True)
            return call("http.client", refused)

        try:
            raised = _raised(forward_then_connect)
        finally:
            forwarder.close()
        server.join()

    assert server.received.startswith(b"POST /mcp"), "precondition: the forwarder delivered"
    assert isinstance(raised, ConnectionRefusedError), raised


# === A send with no register marks every open run (§12.2.13) ====================================


def test_register_a_plain_thread_that_delivers_stops_its_runs_claim(refused):
    """`threading.Thread` does not copy the context, and it is what an executor reaches for.

    The thread delivers the effect and the run's own next connection is refused: with the thread's
    send visible to no register, the run would claim that nothing happened. Preconditions: the
    peer received the request, and the same refused connect alone in a run is claimed (control).
    """
    assert isinstance(_raised(lambda: call("http.client", refused)), NotExecuted), "control"

    with peer(answering(200)) as server:

        def deliver_on_a_thread_then_connect() -> int:
            worker = threading.Thread(target=call, args=("http.client", server.port))
            worker.start()
            worker.join(WAIT)
            return call("http.client", refused)

        raised = _raised(deliver_on_a_thread_then_connect)
        server.join()

    assert server.received.startswith(b"POST /refunds"), "precondition: the thread delivered"
    assert isinstance(raised, ConnectionRefusedError), raised


def test_register_a_send_outside_any_run_marks_every_open_run(refused):
    """The cost of the rule above, asserted rather than described: a send that belongs to no run
    suppresses the claims of every run open at that moment, including ones it has nothing to do
    with. Fail-closed, and the docstring and §12.2.13 say so.

    Precondition (the control): the same run, with no stray send, claims.
    """
    assert isinstance(_raised(lambda: call("http.client", refused)), NotExecuted), "control"

    open_run, stray_sent = threading.Event(), threading.Event()
    outcome: dict[str, BaseException] = {}

    def hold_a_run_open() -> None:
        def body() -> None:
            open_run.set()
            assert stray_sent.wait(WAIT), "the stray send never happened"
            outcome["raised"] = _caught(lambda: call("http.client", refused))

        in_run(body)

    holder = threading.Thread(target=hold_a_run_open)
    holder.start()
    try:
        assert open_run.wait(WAIT), "the run never opened"
        with peer(answering(200)) as server:
            call("http.client", server.port)  # outside any run of its own
            server.join()
    finally:
        stray_sent.set()
        holder.join(WAIT * 2)

    assert not isinstance(outcome["raised"], NotExecuted), outcome["raised"]
    assert isinstance(outcome["raised"], ConnectionRefusedError)


def test_register_no_run_outlives_its_executor(refused):
    """The set of open runs is what a stray send marks, so a run left in it would go on being
    marked, and the set would grow for the life of the process. Precondition: it is empty before,
    holds exactly this run during, and is empty after, including when the executor raises."""
    from ctrlrun.effect import _OPEN_RUNS

    assert not _OPEN_RUNS, "precondition: no run is open before this test"
    seen: list[int] = []
    in_run(lambda: seen.append(len(_OPEN_RUNS)))
    assert seen == [1], seen
    assert not _OPEN_RUNS

    _raised(lambda: call("http.client", refused))

    assert not _OPEN_RUNS


def test_register_the_sibling_thread_race_is_what_the_docstring_says_it_is(refused):
    """§2.3's disclosed race, pinned so the disclosure cannot drift: a thread that **did** copy the
    context and sends *after* the claim was decided does not retract it. The claim is about the
    run up to the moment of the failure.

    Precondition: the sibling really did deliver its request, after the claim.
    """
    claimed = threading.Event()

    with peer(answering(200)) as server:

        def claim_then_let_the_sibling_send() -> int:
            def sibling() -> None:
                assert claimed.wait(WAIT)
                call("http.client", server.port)

            worker = threading.Thread(target=contextvars.copy_context().run, args=(sibling,))
            worker.start()
            try:
                return call("http.client", refused)
            finally:
                claimed.set()
                worker.join(WAIT)

        raised = _raised(claim_then_let_the_sibling_send)
        server.join()

    assert server.received.startswith(b"POST /refunds"), "precondition: the sibling delivered"
    assert isinstance(raised, NotExecuted), raised


# === T227: one implementation of the rule ========================================================


def test_T227_the_gateways_Transport_is_the_core_one():
    from ctrlrun.gateway import outcome

    assert outcome.Transport is transport.Transport


def test_T227_the_gateway_reaches_the_core_rule_by_identity(monkeypatch, refused):
    """A spy on `ctrlrun.transport.effect_state` is the one every classification path reaches.

    The spy inverts the rule; each path then follows the spy, which a copy of the rule in another
    module could not do. Comparing outputs would pass right up to the day two copies disagreed.
    """
    from ctrlrun.gateway import outcome

    calls: list[Any] = []

    def inverted(observed):
        calls.append(observed)
        return EffectState.AMBIGUOUS

    monkeypatch.setattr(transport, "effect_state", inverted)

    assert outcome.classify(transport.Transport.NEVER_CONNECTED).effect is EffectState.AMBIGUOUS
    assert calls == [transport.Transport.NEVER_CONNECTED]

    raised = _raised(lambda: call("http.client", refused))
    assert not isinstance(raised, NotExecuted), "the core connection asked the spied rule"
    assert calls[-1] is transport.Transport.NEVER_CONNECTED


def test_T227_the_gateway_path_to_httpx_request_reaches_the_core_rule(monkeypatch, refused):
    pytest.importorskip("httpx", reason="the gateway extra is not installed")
    from ctrlrun.gateway.transport import request

    calls: list[Any] = []

    def inverted(observed):
        calls.append(observed)
        return EffectState.AMBIGUOUS

    monkeypatch.setattr(transport, "effect_state", inverted)
    raised = _raised(lambda: request("POST", f"http://{LOOPBACK}:{refused}/", timeout=WAIT))

    assert calls == [transport.Transport.NEVER_CONNECTED]
    assert not isinstance(raised, NotExecuted)


def test_T227_the_rule_itself():
    """`FAILED` for the one member that proves nothing was offered, `AMBIGUOUS` for every other."""
    for member in transport.Transport:
        expected = (
            EffectState.FAILED
            if member is transport.Transport.NEVER_CONNECTED
            else EffectState.AMBIGUOUS
        )
        assert transport.effect_state(member) is expected, member
    assert [member.value for member in transport.Transport] == [
        "never_connected",
        "after_request_sent",
        "unreadable_response",
        "stream_ended_early",
        "client_disconnected",
    ]
    # A string equal to the member is not the member: the rule is decided by identity.
    assert transport.effect_state("never_connected") is EffectState.AMBIGUOUS  # type: ignore[arg-type]


# === T228: the import rules =======================================================================

EXTRAS = ("httpx", "psycopg", "jwt", "opentelemetry")


def _modules_after(statement: str) -> list[str]:
    finished = subprocess.run(
        [sys.executable, "-c", f"{statement}; import sys; print('\\n'.join(sorted(sys.modules)))"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return finished.stdout.split()


def test_T228_import_ctrlrun_imports_no_extra_and_not_the_transport():
    loaded = _modules_after("import ctrlrun")
    assert not [name for name in loaded if name.split(".")[0] in EXTRAS]
    for name in ("ctrlrun.verify", "ctrlrun.conformance", "ctrlrun.transport", "ctrlrun.gateway"):
        assert name not in loaded, name


def test_T228_import_ctrlrun_transport_imports_no_extra():
    loaded = _modules_after("import ctrlrun.transport")
    assert "ctrlrun.transport" in loaded
    assert not [name for name in loaded if name.split(".")[0] in EXTRAS]
    assert "ctrlrun.gateway" not in loaded


def test_T228_every_import_in_transport_py_is_the_standard_library_errors_or_effect():
    tree = ast.parse(TRANSPORT_SOURCE.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                imported.append("ctrlrun." + (node.module or ""))
            else:
                imported.append(node.module or "")
    assert imported, "the AST walk found nothing, so it asserted nothing"
    for name in imported:
        if name.startswith("ctrlrun"):
            assert name in ("ctrlrun.errors", "ctrlrun.effect"), name
        else:
            assert name.split(".")[0] in sys.stdlib_module_names, name


# === T229: no parameter widens FAILED =============================================================


def test_T229_the_public_signatures_are_the_ones_the_spec_lists():
    """§2.8. An unlisted parameter fails here before it is merged (§2.6)."""
    params = inspect.signature(transport.urlopen).parameters
    assert [(name, p.kind.name) for name, p in params.items()] == [
        ("url", "POSITIONAL_OR_KEYWORD"),
        ("data", "POSITIONAL_OR_KEYWORD"),
        ("timeout", "KEYWORD_ONLY"),
        ("context", "KEYWORD_ONLY"),
    ]
    # Drop-in subclasses: the constructor is http.client's, parameter for parameter.
    assert inspect.signature(transport.HTTPConnection) == inspect.signature(
        http.client.HTTPConnection
    )
    assert inspect.signature(transport.HTTPSConnection) == inspect.signature(
        http.client.HTTPSConnection
    )
    for cls in (transport.HTTPConnection, transport.HTTPSConnection):
        public = {name for name in vars(cls) if not name.startswith("_")}
        assert public <= {"connect", "send", "sock"}, (cls, public)
        for name in public - {"sock"}:
            ours = inspect.signature(getattr(cls, name)).parameters
            theirs = inspect.signature(getattr(http.client.HTTPConnection, name)).parameters
            assert [(p.name, p.kind) for p in ours.values()] == [
                (p.name, p.kind) for p in theirs.values()
            ], name
    assert "__init__" not in vars(transport.HTTPConnection)
    assert "__init__" not in vars(transport.HTTPSConnection)
    assert set(transport.__all__) == {
        "HTTPConnection",
        "HTTPSConnection",
        "Transport",
        "effect_state",
        "urlopen",
    }


def test_T229_no_CTRLRUN_environment_variable_is_read(monkeypatch, refused):
    """By source and by behaviour: nothing in the module reads the environment for itself.

    urllib's proxy handler reads `*_proxy` variables, which is `urllib`'s documented behaviour and
    the proxies §2.3 honours; nothing reads a `CTRLRUN_*` name.
    """
    source = TRANSPORT_SOURCE.read_text(encoding="utf-8")
    assert "CTRLRUN_" not in source
    names = {
        node.attr if isinstance(node, ast.Attribute) else node.id
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.Attribute, ast.Name))
    }
    assert not names & {"environ", "environb", "getenv", "getenvb"}, names

    read: list[str] = []

    class Recording(dict):  # type: ignore[type-arg]
        def __getitem__(self, key):
            read.append(key)
            return super().__getitem__(key)

        def get(self, key, default=None):
            read.append(key)
            return super().get(key, default)

    monkeypatch.setattr(os, "environ", Recording(os.environ))
    for surface in SURFACES:
        _caught(lambda surface=surface: call(surface, refused))
    assert not [key for key in read if str(key).upper().startswith("CTRLRUN")]


# === T229b: http.client writes only through send, on every supported Python ======================


class _RecordingHTTP(http.client.HTTPConnection):
    """Records every byte handed to `send`: the stdlib's own class, so this pins the stdlib."""

    offered: bytearray

    def send(self, data):  # type: ignore[no-untyped-def]
        # A send that opens the connection runs the tunnel's own send first; record in wire order.
        if self.sock is None and self.auto_open:
            self.connect()
        self.offered = getattr(self, "offered", bytearray())
        self.offered += bytes(data)
        super().send(data)


class _RecordingHTTPS(http.client.HTTPSConnection):
    offered: bytearray

    def send(self, data):  # type: ignore[no-untyped-def]
        # A send that opens the connection runs the tunnel's own send first; record in wire order.
        if self.sock is None and self.auto_open:
            self.connect()
        self.offered = getattr(self, "offered", bytearray())
        self.offered += bytes(data)
        super().send(data)


def tunnel_then_answer(state: Peer, conn: socket.socket) -> None:
    """A proxy: accept the `CONNECT`, then read the tunnelled request and answer it."""
    state.received += _read_request(conn)
    conn.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
    state.received += _read_request(conn)
    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")


BODIES: dict[str, Callable[[], Any]] = {
    "bytes": lambda: b'{"payment_id":"txn_1","amount":200}' * 50,
    "file": lambda: io.BytesIO(b"file-body " * 500),
    "chunked": lambda: iter([b"one", b"two", b"three"]),
}


@pytest.mark.parametrize("body", sorted(BODIES))
@pytest.mark.parametrize("tunnel", [False, True], ids=["direct", "tunnel"])
def test_T229b_every_byte_the_peer_receives_was_handed_to_send(body, tunnel):
    """§2.4: the mark lives in `send` on the strength of this, on every Python CI runs."""
    handler = tunnel_then_answer if tunnel else answering(200)
    with peer(handler) as server:
        connection = _RecordingHTTP(LOOPBACK, server.port, timeout=WAIT)
        if tunnel:
            connection.set_tunnel("target.ctrlrun.invalid", 80, headers={"X-Tunnel": "1"})
        # http.client chooses chunked framing itself for the file and iterable bodies.
        headers = {"X-Refund": "txn_1", "Content-Type": "application/json"}
        connection.request("POST", "/refunds", body=BODIES[body](), headers=headers)
        response = connection.getresponse()
        response.read()
        connection.close()
        server.join()

    assert server.received, "precondition: the peer received the exchange"
    assert bytes(server.received) == bytes(connection.offered)


def test_T229b_over_TLS_the_decrypted_bytes_equal_the_bytes_handed_to_send(server_tls, trusting):
    """The count is taken above TLS because `HTTPSConnection` inherits `send` rather than overriding
    it. Asserted by identity on this Python, and by the bytes: what the server decrypted is what
    `send` was handed."""
    assert http.client.HTTPSConnection.send is http.client.HTTPConnection.send
    assert transport.HTTPSConnection.send is transport.HTTPConnection.send

    with peer(answering(200), wrap=server_tls) as server:
        connection = _RecordingHTTPS(LOOPBACK, server.port, timeout=WAIT, context=trusting)
        connection.request("POST", "/refunds", body=b"x" * 5000, headers={"X-Refund": "txn_1"})
        response = connection.getresponse()
        response.read()
        connection.close()
        server.join()

    assert server.received.startswith(b"POST /refunds"), "precondition: TLS carried the request"
    assert bytes(server.received) == bytes(connection.offered)


def test_T229b_the_classifiers_connection_offers_exactly_what_http_client_offers():
    """The subclass changes nothing on the wire: the peer receives the same bytes either way."""
    received: list[bytes] = []
    for cls in (http.client.HTTPConnection, transport.HTTPConnection):
        with peer(answering(200)) as server:
            connection = cls(LOOPBACK, server.port, timeout=WAIT)
            connection.request("POST", "/refunds", body=b"{}", headers={"Host": "fixed"})
            connection.getresponse().read()
            connection.close()
            server.join()
        received.append(bytes(server.received))
    assert received[0] == received[1]


# === the TLS 1.3 client-certificate case (§2.3) ===================================================


def test_a_rejected_client_certificate_under_TLS_1_3_is_the_original_exception(
    certificate, trusting
):
    """Under TLS 1.3 the client learns of the rejection on its first read, after the request was
    offered, so it is the original exception by construction (§2.3).

    Precondition, asserted: the server's handshake failed (it required a certificate and got none),
    and the client's handshake completed, so the request was offered before the failure surfaced.
    """
    server_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_tls.load_cert_chain(*certificate)
    server_tls.verify_mode = ssl.CERT_REQUIRED
    server_tls.load_verify_locations(cafile=str(certificate[0]))
    server_tls.minimum_version = ssl.TLSVersion.TLSv1_3
    trusting.minimum_version = ssl.TLSVersion.TLSv1_3

    with peer(answering(200), wrap=server_tls) as server:
        connection = transport.HTTPSConnection(
            LOOPBACK, server.port, timeout=WAIT, context=trusting
        )
        try:
            connection.connect()  # the client's side of the handshake completes
            raised = _raised(
                lambda: (connection.request("POST", "/", body=b"{}"), connection.getresponse())
            )
        finally:
            connection.close()
        server.join()

    assert server.handshake_errors, "precondition: the server rejected the missing certificate"
    assert not isinstance(raised, NotExecuted), raised


# === T231: the gateway's NotExecuted carries its cause ============================================


def test_T231_a_cause_left_from_an_earlier_call_is_never_chained(tmp_path, monkeypatch):
    """A custom forwarder's `NEVER_CONNECTED` is its author's claim and carries no cause (§2.5).

    Precondition: a stale exception sits in this context's cause slot before the call, where a
    gateway that did not clear it would chain it to a `NotExecuted` it has nothing to do with.
    """
    import json

    from ctrlrun.gateway import transport as gateway_transport
    from ctrlrun.gateway.server import Gateway, GatewayConfig

    policy = "schema: ctrlrun.policy/v2\nactions:\n  mcp.acme.create_refund:\n    decision: allow\n"
    opened = SQLiteStateStore(tmp_path / "gateway.db")
    captured: list[BaseException] = []
    original = Control.execute

    def recording(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        try:
            return original(self, *args, **kwargs)
        except BaseException as exc:
            captured.append(exc)
            raise

    monkeypatch.setattr(Control, "execute", recording)

    def custom(body, headers, *, fresh):  # type: ignore[no-untyped-def]
        return transport.Transport.NEVER_CONNECTED, None, 502, {}

    config = GatewayConfig(upstream="http://127.0.0.1:9/mcp", alias="acme", principal="agent")
    gateway = Gateway(config, Control(Policy.from_yaml(policy), opened), custom)
    gateway_transport._CAUSE.set(RuntimeError("stale, from an earlier call"))
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "create_refund", "arguments": {"payment_id": "txn_1"}},
    }
    headers = {
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": "tools/call",
        "Mcp-Name": "create_refund",
    }
    try:
        response = gateway.handle(json.dumps(body).encode(), headers)
    finally:
        gateway_transport._CAUSE.set(None)
        opened.close()

    assert json.loads(response.body)["error"]["code"] == -41011
    assert len(captured) == 1 and isinstance(captured[0], NotExecuted), captured
    assert captured[0].__cause__ is None


def test_T231_the_gateways_NotExecuted_carries_the_httpx_exception(tmp_path, refused, monkeypatch):
    """0.6.1 raised it from the token string with no cause (`server.py:616`)."""
    httpx = pytest.importorskip("httpx", reason="the gateway extra is not installed")
    import json

    from ctrlrun.gateway.server import Gateway, GatewayConfig, httpx_forwarder

    policy = """
schema: ctrlrun.policy/v2
actions:
  mcp.acme.create_refund:
    effect: "refund:{payment_id}"
    decision: allow
"""
    opened = SQLiteStateStore(tmp_path / "gateway.db")
    control = Control(Policy.from_yaml(policy), opened)
    captured: list[BaseException] = []
    original = Control.execute

    def recording(self, *args, **kwargs):
        try:
            return original(self, *args, **kwargs)
        except BaseException as exc:
            captured.append(exc)
            raise

    monkeypatch.setattr(Control, "execute", recording)
    config = GatewayConfig(
        upstream=f"http://{LOOPBACK}:{refused}/mcp",
        alias="acme",
        principal_header="X-Agent",
        port=0,
    )
    forwarder = httpx_forwarder(config)
    gateway = Gateway(config, control, forwarder)
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "create_refund", "arguments": {"payment_id": "txn_1", "amount": 200}},
    }
    headers = {
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": "tools/call",
        "Mcp-Name": "create_refund",
        "X-Agent": "refund-agent",
    }
    try:
        response = gateway.handle(json.dumps(body).encode(), headers)
    finally:
        forwarder.close()

    assert json.loads(response.body)["error"]["code"] == -41011
    assert opened.get_effect("refund:txn_1").state is EffectState.FAILED
    assert len(captured) == 1 and isinstance(captured[0], NotExecuted), captured
    assert isinstance(captured[0].__cause__, httpx.ConnectError)
    assert "ConnectError" in (opened.receipts()[-1].error or "")
    opened.close()


# === T230: G12 in verify, under the amended network guard =========================================

REPO = Path(__file__).resolve().parent.parent
ALLOWED_WITH_EFFECT = """
schema: ctrlrun.policy/v2
actions:
  refund.create:
    effect: "refund:{payment_id}"
    decision: allow
"""
APPROVED_WITH_EFFECT = """
schema: ctrlrun.policy/v2
actions:
  refund.create:
    effect: "refund:{payment_id}"
    decision: approve
"""
ALL_DENIED = """
schema: ctrlrun.policy/v2
actions:
  refund.create:
    effect: "refund:{payment_id}"
    decision: deny
"""


def _g12(tmp_path: Path, document: str) -> Any:
    from ctrlrun.verify import run

    path = tmp_path / "ctrlrun.yaml"
    path.write_text(document, encoding="utf-8")
    report = run(path, only=("G12",))
    return next(result for result in report.guarantees if result.id == "G12")


@pytest.mark.parametrize(
    "document", [ALLOWED_WITH_EFFECT, APPROVED_WITH_EFFECT], ids=["allow", "approve"]
)
def test_T230_G12_passes_on_a_correct_kernel(tmp_path, document):
    from ctrlrun.verify import Status

    result = _g12(tmp_path, document)

    assert result.status is Status.PASS, (result.reason, result.counterexample)
    assert result.detail["rows"] == {
        "byte_written": "ambiguous",
        "read_timeout": "ambiguous",
        "reused": "ambiguous",
        "second_connection": "ambiguous",
        "never_connected": "failed",
    }
    assert result.effect_key == "refund:ctrlrun-verify-payment_id"


def test_T230_G12_passes_where_the_action_has_no_effect_key():
    """`examples/policies/payments.yaml` is v1 and declares no template: the receipt is graded."""
    from ctrlrun.verify import Status, run

    report = run(REPO / "examples" / "policies" / "payments.yaml", only=("G12",))
    result = next(item for item in report.guarantees if item.id == "G12")

    assert result.status is Status.PASS, (result.reason, result.counterexample)
    assert result.effect_key is None


def test_T230_G12_is_not_applicable_only_for_a_reason_about_the_document(tmp_path):
    from ctrlrun.verify import Status
    from ctrlrun.verify import guarantees as reg

    result = _g12(tmp_path, ALL_DENIED)

    assert result.status is Status.NOT_APPLICABLE
    assert result.reason == reg.EVERY_ACTION_DENIED


@pytest.mark.authority
def test_T230_G12_names_the_grant_miss_rather_than_a_policy_sentence():
    """Where an action reaches a decision and no grant covers it, G12 says so, as G10 does.

    Asserted against G10's own answer for the same document, so the two cannot drift apart: a
    hardcoded "every action is denied" about a document whose actions are allowed and ungranted is
    a false N/A (§8.9).
    """
    from ctrlrun.verify import Status, run

    document = REPO / "examples" / "authority" / "devops.yaml"
    report = run(document, only=("G10", "G12"))
    results = {item.id: item for item in report.guarantees}

    assert results["G12"].status is results["G10"].status
    assert results["G12"].reason == results["G10"].reason
    if results["G12"].status is Status.NOT_APPLICABLE:
        from ctrlrun.verify import guarantees as reg

        assert results["G12"].reason.startswith(reg.NO_GRANT_COVERS_SELECTION)


def test_T230_a_classifier_that_never_claims_fails_the_control(tmp_path, monkeypatch):
    """`v0.4` T125's standard: the control is the refused connection, and it must be `FAILED`."""
    from ctrlrun.verify import Status
    from ctrlrun.verify import guarantees as reg

    monkeypatch.setattr(transport, "effect_state", lambda observed: EffectState.AMBIGUOUS)
    result = _g12(tmp_path, ALLOWED_WITH_EFFECT)

    assert result.status is Status.FAIL
    assert result.reason == reg.CONTROL_FAILED


def test_T230_a_classifier_that_guesses_from_the_exception_type_fails_the_read_timeout_row(
    tmp_path, monkeypatch
):
    """The classifier §2.3 rejects by name: `TimeoutError`, `ConnectionRefusedError`,
    `socket.gaierror` and `ssl.SSLError` mapped to `NotExecuted` wherever they arise. It passes
    the reset row and the control; the read-timeout row, a timeout after a delivered byte, is
    where it is wrong, and G12 says so there."""
    from ctrlrun.verify import Status

    guessed = (TimeoutError, ConnectionRefusedError, socket.gaierror, ssl.SSLError)

    def by_type(method):  # type: ignore[no-untyped-def]
        def wrapped(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            try:
                return method(self, *args, **kwargs)
            except NotExecuted:
                raise
            except guessed as exc:
                raise NotExecuted(f"guessed from {type(exc).__name__}") from exc

        return wrapped

    for name in ("connect", "send", "getresponse"):
        monkeypatch.setattr(
            transport.HTTPConnection, name, by_type(getattr(transport.HTTPConnection, name))
        )
    result = _g12(tmp_path, ALLOWED_WITH_EFFECT)

    assert result.status is Status.FAIL
    assert result.reason.startswith("the classifier raised NotExecuted on a read timeout"), (
        result.reason
    )


def test_T230_a_classifier_blind_to_its_own_evidence_fails_the_reused_row(tmp_path, monkeypatch):
    """Every connect failure claimed, the byte mark, the foreign-socket record and the register
    all ignored: the reset and read-timeout rows fail after `connect()` and cannot see it; the
    reused row, a reconnect by a connection that already delivered a request, does."""
    from ctrlrun.verify import Status

    def blind(self):  # type: ignore[no-untyped-def]
        try:
            http.client.HTTPConnection.connect(self)
        except Exception as exc:
            raise NotExecuted("claimed without evidence") from exc

    monkeypatch.setattr(transport.HTTPConnection, "connect", blind)
    result = _g12(tmp_path, ALLOWED_WITH_EFFECT)

    assert result.status is Status.FAIL
    assert result.reason.startswith(
        "the classifier raised NotExecuted on a connection that had already delivered"
    ), result.reason


def test_T230_a_classifier_with_no_byte_mark_fails_the_reused_row(tmp_path, monkeypatch):
    """The register alone is not enough. A classifier that keeps the run's register and the
    foreign-socket record, and drops the connection's own byte mark, is wrong exactly where a
    connection carries a delivered request into a run that has offered nothing itself, which is
    what the reused row drives."""
    from ctrlrun.verify import Status

    def no_byte_mark(self):  # type: ignore[no-untyped-def]
        self._ctrlrun_connecting = True
        try:
            http.client.HTTPConnection.connect(self)
        except Exception as exc:
            run = ctrlrun.effect._EXECUTOR_RUN.get()
            if run is not None and not run.offered and not self._ctrlrun_foreign:
                raise NotExecuted("the run offered nothing, so this connection claims") from exc
            raise
        finally:
            self._ctrlrun_connecting = False

    monkeypatch.setattr(transport.HTTPConnection, "connect", no_byte_mark)
    result = _g12(tmp_path, ALLOWED_WITH_EFFECT)

    assert result.status is Status.FAIL
    assert result.reason.startswith(
        "the classifier raised NotExecuted on a connection that had already delivered"
    ), result.reason


def test_T230_a_classifier_with_no_register_fails_the_second_connection_row(tmp_path, monkeypatch):
    """The first release's classifier: a byte mark and a foreign-socket record per connection
    object, and nothing about the run. Each connection judged alone passes every row but one: a
    second connection, after the first delivered, is refused and claimed."""
    from ctrlrun.verify import Status

    def per_object(self):  # type: ignore[no-untyped-def]
        self._ctrlrun_connecting = True
        try:
            http.client.HTTPConnection.connect(self)
        except Exception as exc:
            if not self._ctrlrun_offered and not self._ctrlrun_foreign:
                raise NotExecuted("judged on this connection alone") from exc
            raise
        finally:
            self._ctrlrun_connecting = False

    monkeypatch.setattr(transport.HTTPConnection, "connect", per_object)
    result = _g12(tmp_path, ALLOWED_WITH_EFFECT)

    assert result.status is Status.FAIL
    assert result.reason.startswith("the classifier raised NotExecuted on a second connection"), (
        result.reason
    )


def test_T230_a_classifier_that_always_claims_fails_the_observable(tmp_path, monkeypatch):
    """The other direction of the asymmetry: a byte written and still claimed is the violation."""
    from ctrlrun.verify import Status

    original = transport.HTTPConnection.getresponse

    def guessing(self):  # type: ignore[no-untyped-def]
        try:
            return original(self)
        except Exception as exc:
            raise NotExecuted("a classifier that guessed from the exception type") from exc

    monkeypatch.setattr(transport.HTTPConnection, "getresponse", guessing)
    result = _g12(tmp_path, ALLOWED_WITH_EFFECT)

    assert result.status is Status.FAIL
    # By its message: the kernel would also write a `failed` receipt here, and a check on the
    # receipt alone would report this under a different sentence (mutation pattern 1).
    assert result.reason.startswith("the classifier raised NotExecuted after the peer received")


def test_T230_a_kernel_that_records_every_failure_FAILED_fails_the_observable(
    tmp_path, monkeypatch
):
    """The classifier is correct and the kernel is not: every executor exception becomes `FAILED`.

    The observable's receipt check is what catches it, by its own sentence, since the exception
    the executor raised is rightly not `NotExecuted`.
    """
    from ctrlrun import control as control_module
    from ctrlrun.verify import Status

    monkeypatch.setattr(control_module, "NotExecuted", Exception)
    result = _g12(tmp_path, ALLOWED_WITH_EFFECT)

    assert result.status is Status.FAIL
    assert result.reason.startswith("the receipt is failed"), result.reason


def test_T230_an_unchained_claim_fails_the_control(tmp_path, monkeypatch):
    """A classifier that claims `NotExecuted` without the evidence it rests on.

    The receipt is `failed`, correctly, so only the control's check on the cause can see this.
    """
    from ctrlrun.verify import Status
    from ctrlrun.verify import guarantees as reg

    original = transport.HTTPConnection.connect

    def unchained(self):  # type: ignore[no-untyped-def]
        try:
            original(self)
        except NotExecuted as claim:
            raise NotExecuted(str(claim)) from None

    monkeypatch.setattr(transport.HTTPConnection, "connect", unchained)
    result = _g12(tmp_path, ALLOWED_WITH_EFFECT)

    assert result.status is Status.FAIL
    assert result.reason == reg.CONTROL_FAILED
    assert result.counterexample is not None
    assert "cause: None" in result.counterexample.observed


def test_T230_a_listener_that_received_no_byte_is_a_failed_control(tmp_path, monkeypatch):
    """The observable's precondition is asserted first: without the byte, "not `NotExecuted`"
    proves nothing, so a peer that read nothing is `control failed` and never a pass."""
    from ctrlrun.verify import Status, scenarios
    from ctrlrun.verify import guarantees as reg

    monkeypatch.setattr(scenarios, "_READ_AT_MOST", 0)
    result = _g12(tmp_path, ALLOWED_WITH_EFFECT)

    assert result.status is Status.FAIL
    assert result.reason == reg.CONTROL_FAILED


def test_T230_a_sandbox_that_binds_but_refuses_connect_is_an_internal_error(tmp_path, monkeypatch):
    """The preflight's own half: bind is allowed and connect is not. Without the preflight this
    would reach the classifier and come back `control failed`, a machine's fact blamed on the
    kernel (§12.2.7)."""
    from ctrlrun.verify import VerifyInternalError

    real = socket.socket

    class Unconnectable(real):  # type: ignore[misc, valid-type]
        def connect(self, address):  # type: ignore[no-untyped-def]
            raise RuntimeError("the sandbox refuses connect")

    monkeypatch.setattr(socket, "socket", Unconnectable)
    with pytest.raises(VerifyInternalError, match="could not connect"):
        _g12(tmp_path, ALLOWED_WITH_EFFECT)


def test_T230_a_sandbox_that_will_not_bind_loopback_is_an_internal_error(tmp_path, monkeypatch):
    """§8.9: never `N/A` because of the environment, and never a failure of the kernel either."""
    from ctrlrun.verify import VerifyInternalError

    real = socket.socket

    class Unbindable(real):  # type: ignore[misc, valid-type]
        def bind(self, address):  # type: ignore[no-untyped-def]
            raise PermissionError("the sandbox refuses bind")

    monkeypatch.setattr(socket, "socket", Unbindable)
    with pytest.raises(VerifyInternalError, match="G12"):
        _g12(tmp_path, ALLOWED_WITH_EFFECT)


_T230_SCRIPT = """
import os, socket, sys, tempfile

def refused_by_guard(thunk, what):
    try:
        thunk()
    except RuntimeError as exc:
        if "no network" not in str(exc):
            raise
        return
    except BaseException as exc:
        sys.exit(f"{what}: expected the guard to refuse, got {type(exc).__name__}: {exc}")
    sys.exit(f"{what}: the guard admitted it")

def tcp(family=socket.AF_INET):
    opened = socket.socket(family, socket.SOCK_STREAM)
    opened.settimeout(2)
    return opened

outside = int(sys.argv[2])
refused_by_guard(lambda: socket.getaddrinfo("example.invalid", 80), "a lookup")
refused_by_guard(lambda: tcp().connect(("192.0.2.1", 80)), "TEST-NET-1")
refused_by_guard(lambda: tcp().connect(("127.0.0.1", outside)), "a port the run did not bind")
refused_by_guard(lambda: tcp().connect_ex(("127.0.0.1", outside)), "connect_ex to it")
refused_by_guard(
    lambda: socket.create_connection(("127.0.0.1", outside), timeout=2), "create_connection to it"
)
refused_by_guard(lambda: tcp().bind(("0.0.0.0", 0)), "a bind to 0.0.0.0")
refused_by_guard(lambda: socket.getaddrinfo("localhost", 80), "a lookup of localhost")

listener = tcp()
listener.bind(("127.0.0.1", 0))
listener.listen(4)
port = listener.getsockname()[1]
refused_by_guard(lambda: tcp().connect(("localhost", port)), "localhost, to a bound port")
refused_by_guard(
    lambda: socket.create_connection(("localhost", port), timeout=2), "create_connection, localhost"
)
refused_by_guard(lambda: tcp(socket.AF_INET6).connect(("::1", port)), "::1")
refused_by_guard(lambda: tcp(socket.AF_INET6).bind(("::1", 0)), "a bind to ::1")
path = os.path.join(tempfile.mkdtemp(), "s")
refused_by_guard(lambda: tcp(socket.AF_UNIX).bind(path), "an AF_UNIX bind")
refused_by_guard(lambda: tcp(socket.AF_UNIX).connect(path), "an AF_UNIX connect")

# Only a stream socket's bind is recorded: a UDP bind at another process's TCP port admits
# nothing there, and a datagram socket connects and sends nowhere.
udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
udp.bind(("127.0.0.1", outside))
refused_by_guard(lambda: tcp().connect(("127.0.0.1", outside)), "a TCP connect a UDP bind admitted")
refused_by_guard(
    lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM).connect(("127.0.0.1", outside)),
    "a datagram connect",
)
# To a port this process did record, so only the socket's kind can refuse it: UDP datagrams to
# the TCP port of that number reach whoever holds the UDP port, which may be another process.
refused_by_guard(
    lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM).connect(("127.0.0.1", port)),
    "a datagram connect to a recorded port",
)
refused_by_guard(
    lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b"x", ("127.0.0.1", outside)),
    "a datagram send",
)
udp.close()

# A port is admitted while the socket that bound it is open, and forgotten when it closes: the
# kernel may hand the port to another process the moment it is released.
released = tcp()
released.bind(("127.0.0.1", 0))
released_port = released.getsockname()[1]
released.close()
refused_by_guard(lambda: tcp().connect(("127.0.0.1", released_port)), "a port bound and closed")

# Admitted: a listener bound to port 0, at the port getsockname() reports.
client = tcp()
client.connect(("127.0.0.1", port))
listener.accept()[0].close()
client.close()
socket.create_connection(("127.0.0.1", port), timeout=2).close()

import ctrlrun.verify as verify

report = verify.run(sys.argv[1])
g12 = next(result for result in report.guarantees if result.id == "G12")
if g12.status.value != "pass":
    sys.exit(f"G12 was {g12.status.value}: {g12.reason}")
sys.exit(report.exit_code)
"""


def test_T230_G12_is_graded_under_the_guard_and_the_guard_is_exactly_as_wide_as_the_rule(
    tmp_path, no_network
):
    """One subprocess: the guard is live and refuses everything the rule does not name, and G12,
    which needs a loopback listener, is graded under it rather than `N/A` or skipped."""
    policy = tmp_path / "ctrlrun.yaml"
    policy.write_text(ALLOWED_WITH_EFFECT, encoding="utf-8")
    script = tmp_path / "check.py"
    script.write_text(_T230_SCRIPT, encoding="utf-8")
    outside = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    outside.bind((LOOPBACK, 0))
    outside.listen(1)
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(no_network), environment.get("PYTHONPATH", "")) if part
    )
    try:
        finished = subprocess.run(
            [sys.executable, str(script), str(policy), str(outside.getsockname()[1])],
            capture_output=True,
            text=True,
            timeout=300,
            env=environment,
            cwd=tmp_path,
            check=False,
        )
    finally:
        outside.close()

    assert finished.returncode == 0, f"{finished.stdout}\n{finished.stderr}"
