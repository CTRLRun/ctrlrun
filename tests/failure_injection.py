"""A Postgres connection an experiment can break on purpose. Build-list item 4; SPEC-v0.6 §4.5.

Test infrastructure, never packaged: `pyproject.toml`'s `packages.find` is `src/` only.

**Why a proxy and not a container.** §4.5's most important test is *the connection dies during
`COMMIT`* -- the case where a naive port reports `FAILED` for an effect that committed. Killing a
container reproduces that only if the timing happens to land there; a proxy that forwards the
`COMMIT` to the server and *then* drops the client reproduces it every time, which is what
"opens its window on purpose" means, and this project's standing rule is that a test which
"usually" reproduces an interleaving proves nothing about the run where it did not.

What a proxy cannot do is put the two ends on different hosts. §4.5 asks for that separately and
the PR says which was run.
"""

from __future__ import annotations

import contextlib
import socket
import threading
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

#: Every wait here is bounded, so a broken check fails red rather than hanging CI.
TIMEOUT = 20.0


@dataclass
class Proxy:
    """A TCP relay in front of Postgres, with a switch for each way it can break.

    Three modes, each named for the row of §4.3's Table A it produces:

    - `kill_on_commit` -- forward the `COMMIT` to the server, let it land, then drop the client.
      The server very likely committed and the client will never know: **ambiguous**.
    - `refuse_connections` -- accept nothing. A store's re-read then fails, which §4.3.2 says
      must refuse to let execution proceed rather than guess.
    - `partition` -- accept, then swallow traffic in both directions. The client sees a stall,
      not a reset, which is the shape a network partition actually has.
    """

    upstream_host: str
    upstream_port: int
    kill_on_commit: bool = False
    refuse_connections: bool = False
    partition: bool = False

    port: int = 0
    _server: socket.socket | None = None
    _thread: threading.Thread | None = None
    _stop: threading.Event = field(default_factory=threading.Event)
    #: Every commit the proxy has seen go past, so a test can assert its window opened.
    commits_seen: int = 0
    #: How many client connections were dropped mid-`COMMIT`. A test asserts this rather than
    #: assuming its window opened.
    clients_killed: int = 0
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
            self._thread.join(timeout=TIMEOUT)

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

    def _relay(self, client: socket.socket) -> None:
        try:
            server = socket.create_connection(
                (self.upstream_host, self.upstream_port), timeout=TIMEOUT
            )
        except OSError:
            with_suppress(client.close)
            return
        killed = threading.Event()

        def pump(source: socket.socket, sink: socket.socket, watching: bool) -> None:
            source.settimeout(0.2)
            while not self._stop.is_set() and not killed.is_set():
                try:
                    data = source.recv(65536)
                except TimeoutError:
                    continue
                except OSError:
                    break
                if not data:
                    break
                if self.partition:
                    continue  # swallowed: a stall, not a reset
                try:
                    sink.sendall(data)
                except OSError:
                    break
                if watching and self.kill_on_commit and b"COMMIT" in data:
                    with self._lock:
                        self.commits_seen += 1
                        self.clients_killed += 1
                    # The server has the COMMIT and will apply it. The client's connection goes
                    # **immediately**, so the response never gets back: the write very likely
                    # landed and the client will never be told, which is §4.3's ambiguous store
                    # write exactly.
                    #
                    # The first version slept 50ms here "to let the server apply it", which on a
                    # local socket is long enough for the reply to arrive -- so `commit()`
                    # returned normally and the store never took the ambiguous path at all. The
                    # test asserting that path passed anyway. Closing at once is the whole
                    # mechanism.
                    killed.set()
                    with_suppress(client.shutdown, socket.SHUT_RDWR)
                    with_suppress(client.close)
                    return

        forward = threading.Thread(target=pump, args=(client, server, True), daemon=True)
        backward = threading.Thread(target=pump, args=(server, client, False), daemon=True)
        forward.start()
        backward.start()
        forward.join(timeout=TIMEOUT)
        backward.join(timeout=TIMEOUT)
        with_suppress(client.close)
        with_suppress(server.close)


def with_suppress(call, *arguments) -> None:  # type: ignore[no-untyped-def]
    """Closing a socket that is already gone is not an error here; it is the expected case."""
    with contextlib.suppress(OSError):
        call(*arguments)


def upstream_of(url: str) -> tuple[str, int]:
    parts = urlsplit(url)
    return parts.hostname or "127.0.0.1", parts.port or 5432
