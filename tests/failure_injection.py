"""A Postgres connection an experiment can break on purpose. Build-list item 4; SPEC-v0.6 §4.5.

Test infrastructure. It is never in the wheel -- `pyproject.toml`'s `packages.find` is `src/`
only -- and it *is* in the sdist, because `MANIFEST.in`'s `recursive-include tests *.py` puts
every test file there, which is correct and true of all of them.

**Why a proxy and not a container.** §4.5's most important test is *the connection dies during
`COMMIT`* -- the case where a naive port reports `FAILED` for an effect that committed. Killing a
container reproduces that only if the timing happens to land there; a proxy that forwards the
`COMMIT` to the server and *then* drops the client reproduces it every time, which is what
"opens its window on purpose" means, and this project's standing rule is that a test which
"usually" reproduces an interleaving proves nothing about the run where it did not.

What a proxy cannot do is put the two ends on different hosts. §4.5 asks for that separately and
the PR says which was run.

**The frontend protocol is parsed, not grepped.** The first version triggered on
`b"COMMIT" in data`, which fires on any chunk in the client-to-server direction carrying those
six bytes -- including a Bind message whose *parameter* is an effect key containing the word. An
independent review demonstrated it: a key named `refund:COMMIT-me` killed the client on the
`SELECT`'s Bind, which is §4.3's Table A row 1 (an exception *before* `COMMIT`, a clean `FAILED`)
and not the ambiguous window at all -- and `clients_killed >= 1`, the guard added specifically to
catch that class of false green, still passed. `_Frontend` below reads the length-prefixed
messages properly, so a `COMMIT` means the simple-Query message `COMMIT`.
"""

from __future__ import annotations

import contextlib
import socket
import threading
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

#: How long `stop()` waits for a relay thread to notice `_stop` and exit. The relay's *pumps* are
#: not bounded by this and must not be: a timeout that abandons a live pump and closes the socket
#: under it delivers a reset to the client, which silently changes what a partition test observes.
#: The first version did exactly that, and its 40-second "partition" was this constant twice over.
SHUTDOWN_TIMEOUT = 10.0

#: libpq's SSLRequest, which precedes the startup packet and carries no message-type byte.
_SSL_REQUEST = 80877103


class _Frontend:
    """Just enough of the Postgres v3 frontend protocol to recognise one message.

    A client stream is: an optional SSLRequest and then the startup packet, both `int32 length`
    with no type byte, followed by typed messages -- `byte1 type`, `int32 length` (counting
    itself), body. Messages split across `recv` boundaries, so this buffers.
    """

    def __init__(self) -> None:
        self._buffer = b""
        self._started = False

    def feed(self, data: bytes) -> list[tuple[bytes, bytes, int]]:
        """Complete messages in `data`, as `(type, body, offset)`.

        `type` is empty for the untyped startup messages. `offset` is where the message begins
        **relative to `data`**, negative if it began in an earlier chunk -- which is what
        `drop_before_commit` needs to know how much of the chunk precedes the `COMMIT`.
        """
        pending = len(self._buffer)
        self._buffer += data
        consumed = 0
        messages: list[tuple[bytes, bytes, int]] = []
        while True:
            if not self._started:
                if len(self._buffer) < 8:
                    break
                length = int.from_bytes(self._buffer[:4], "big")
                if length < 8 or len(self._buffer) < length:
                    break
                body = self._buffer[4:length]
                self._buffer = self._buffer[length:]
                if int.from_bytes(body[:4], "big") != _SSL_REQUEST:
                    self._started = True
                messages.append((b"", body, consumed - pending))
                consumed += length
                continue
            if len(self._buffer) < 5:
                break
            kind = self._buffer[0:1]
            length = int.from_bytes(self._buffer[1:5], "big")
            if length < 4 or len(self._buffer) < 1 + length:
                break
            body = self._buffer[5 : 1 + length]
            self._buffer = self._buffer[1 + length :]
            messages.append((kind, body, consumed - pending))
            consumed += 1 + length
        return messages


def is_commit(kind: bytes, body: bytes) -> bool:
    """A simple-Query message whose statement is exactly `COMMIT`."""
    if kind != b"Q":
        return False
    return body.split(b"\x00", 1)[0].strip().upper() == b"COMMIT"


@dataclass
class Proxy:
    """A TCP relay in front of Postgres, with a switch for each way it can break.

    Four modes, each named for the row of §4.3's Table A it produces:

    - `kill_on_commit` -- forward the `COMMIT` to the server, let it land, then drop the client.
      The server very likely committed and the client will never know: **ambiguous**, and the
      re-read finds the write already there (Table A1 row 1 / A2 row 1).
    - `drop_before_commit` -- an integer: swallow the next N `COMMIT`s and drop the client each
      time, **without** forwarding them. Equally ambiguous from the client's side, and the write
      is *not* there: this is what drives A1's "no record → retry the insert" and A2's "unchanged
      and still ours → re-issue", the two rows nothing reached before. A count rather than a flag
      because those rows *re-issue* -- a flag would swallow the retry's `COMMIT` too, and the test
      could never observe the row completing. Set it high to drive the unbounded case instead.
    - `refuse_connections` -- accept and immediately close, which is a reset rather than an
      `ECONNREFUSED`. A store's re-read then fails, which §4.3.2 says must refuse to let
      execution proceed rather than guess.
    - `partition` -- **hold** traffic in both directions rather than discarding it, and release
      it when the partition is lifted. The client sees a stall, not a reset, and `partition =
      False` genuinely restores the connection, which is §4.5's "and restore it". Discarding
      instead would make the restore unexercisable: the query the server never received cannot
      arrive late.
    """

    upstream_host: str
    upstream_port: int
    kill_on_commit: bool = False
    drop_before_commit: int = 0
    refuse_connections: bool = False
    partition: bool = False

    port: int = 0
    _server: socket.socket | None = None
    _thread: threading.Thread | None = None
    _stop: threading.Event = field(default_factory=threading.Event)
    #: `COMMIT` messages the proxy parsed out of the client stream, in every mode. Distinct from
    #: `clients_killed`, which counts drops -- the two were incremented on adjacent lines under
    #: one condition before, so a PR body claiming to assert one "rather than" the other was
    #: describing a distinction that did not exist.
    commits_seen: int = 0
    #: Client connections dropped, by either kill mode.
    clients_killed: int = 0
    #: `COMMIT` messages the proxy swallowed rather than forwarded (`drop_before_commit`).
    commits_dropped: int = 0
    #: Set if the server ever accepted an SSLRequest. Everything after that is ciphertext and no
    #: `COMMIT` would ever be recognised -- a test asserting `commits_seen` would fail loudly, but
    #: this says why. Local connections in CI negotiate no TLS.
    tls_negotiated: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def start(self) -> Proxy:
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(32)
        self.port = self._server.getsockname()[1]
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._server is not None:
            with_suppress(self._server.close)
        if self._thread is not None:
            self._thread.join(timeout=SHUTDOWN_TIMEOUT)

    def reset_counters(self) -> None:
        """Zero the counters, so a test asserts a delta rather than a total.

        Constructing a store issues a `COMMIT` of its own -- the migration -- and reserving before
        the interesting call issues more, all before any injection is armed. A test that asserted
        `commits_seen == 1` would be counting those too.
        """
        with self._lock:
            self.commits_seen = 0
            self.clients_killed = 0
            self.commits_dropped = 0

    def url(self, template: str) -> str:
        """`template` with its host and port pointed at this proxy."""
        parts = urlsplit(template)
        userinfo = parts.netloc.split("@")[0] if "@" in parts.netloc else ""
        netloc = f"{userinfo}@127.0.0.1:{self.port}" if userinfo else f"127.0.0.1:{self.port}"
        return urlunsplit(parts._replace(netloc=netloc))

    # --- the relay ----------------------------------------------------------------------

    def _accept(self) -> None:
        assert self._server is not None
        self._server.settimeout(0.2)
        while not self._stop.is_set():
            try:
                client, _ = self._server.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            if self.refuse_connections:
                with_suppress(client.close)
                continue
            threading.Thread(target=self._relay, args=(client,), daemon=True).start()

    @staticmethod
    def _drop(client: socket.socket, killed: threading.Event) -> None:
        killed.set()
        with_suppress(client.shutdown, socket.SHUT_RDWR)
        with_suppress(client.close)

    def _relay(self, client: socket.socket) -> None:
        try:
            server = socket.create_connection(
                (self.upstream_host, self.upstream_port), timeout=SHUTDOWN_TIMEOUT
            )
        except OSError:
            with_suppress(client.close)
            return
        killed = threading.Event()

        def pump(source: socket.socket, sink: socket.socket, watching: bool) -> None:
            source.settimeout(0.2)
            frontend = _Frontend() if watching else None
            held: list[bytes] = []
            while not self._stop.is_set() and not killed.is_set():
                if not self.partition and held:
                    # The partition lifted: release what it held, in order, before anything new.
                    try:
                        for chunk in held:
                            sink.sendall(chunk)
                    except OSError:
                        break
                    held = []
                try:
                    data = source.recv(65536)
                except TimeoutError:
                    continue
                except OSError:
                    break
                if not data:
                    break
                if self.partition:
                    held.append(data)  # held, not discarded: a stall the restore can undo
                    continue
                commit_at = -1
                if frontend is not None:
                    for kind, body, offset in frontend.feed(data):
                        if not is_commit(kind, body):
                            continue
                        with self._lock:
                            self.commits_seen += 1
                        if commit_at < 0:
                            commit_at = max(0, offset)
                elif data == b"S":
                    # The server accepted an SSLRequest. Everything after is ciphertext.
                    with self._lock:
                        self.tls_negotiated = True

                dropping = False
                if commit_at >= 0:
                    with self._lock:
                        if self.drop_before_commit > 0:
                            self.drop_before_commit -= 1
                            self.commits_dropped += 1
                            self.clients_killed += 1
                            dropping = True
                if dropping:
                    # Everything before the COMMIT goes; the COMMIT itself never does. The server
                    # rolls the transaction back when the connection dies, so the write is
                    # genuinely absent -- and the client cannot tell that from the case where it
                    # landed, which is what makes both rows of Table A reachable.
                    with_suppress(sink.sendall, data[:commit_at])
                    self._drop(client, killed)
                    return

                try:
                    sink.sendall(data)
                except OSError:
                    break

                if commit_at >= 0 and self.kill_on_commit:
                    # The server has the COMMIT and will apply it. The client's connection goes
                    # **immediately**, so the response never gets back: the write very likely
                    # landed and the client will never be told, which is §4.3's ambiguous store
                    # write exactly.
                    #
                    # The first version slept 50ms here "to let the server apply it", which on a
                    # local socket is long enough for the reply to arrive -- so `commit()`
                    # returned normally and the store never took the ambiguous path at all. The
                    # test asserting that path passed anyway. Closing at once is the mechanism.
                    with self._lock:
                        self.clients_killed += 1
                    self._drop(client, killed)
                    return

        forward = threading.Thread(target=pump, args=(client, server, True), daemon=True)
        backward = threading.Thread(target=pump, args=(server, client, False), daemon=True)
        forward.start()
        backward.start()
        # No timeout. A join that gives up and closes these sockets underneath its own live pumps
        # turns a stall into a reset at a deadline the *proxy* owns, which is how the partition
        # test came to measure `TIMEOUT * 2` and call it a network property. The pumps exit on
        # `_stop`, which `stop()` sets, so teardown still ends them.
        forward.join()
        backward.join()
        with_suppress(client.close)
        with_suppress(server.close)


def with_suppress(call, *arguments) -> None:  # type: ignore[no-untyped-def]
    """Closing a socket that is already gone is not an error here; it is the expected case."""
    with contextlib.suppress(OSError):
        call(*arguments)


def upstream_of(url: str) -> tuple[str, int]:
    parts = urlsplit(url)
    return parts.hostname or "127.0.0.1", parts.port or 5432
