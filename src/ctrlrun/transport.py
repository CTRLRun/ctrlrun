"""The `NotExecuted` classifier for `http.client` and `urllib`. SPEC-v0.7 §2.

`v0.1 §5.5` gives an executor the one decision the kernel does not take: whether the remote side
acted. `NotExecuted` means it definitely did not, and it is the only exception an agent may read as
permission to retry. This module makes the transport half of that decision **from evidence**, and
claims `NotExecuted` for a failure to connect only where it observed all three of:

- **the connection never held a socket it did not open itself**;
- **the connection never handed a request byte to its socket**, counted above TLS;
- **no request byte was offered in this executor run at all**, by any connection of this module or
  by `ctrlrun.gateway.transport.request`. `Control` opens that register around each executor call
  (a private context variable), and every send marks it before the first byte is handed over.
  Outside an executor run there is no register, and nothing is claimed.

The third is what makes the claim about the effect rather than about one connection. An executor
whose first connection delivered the request and whose second was refused has not proven that
nothing happened: an xmlrpc client's retry, a redirect followed by an opener, and an executor's own
retry-once loop all make exactly that pair, and each is the original exception here.

**The register sees only this module's own sends.** An executor that sends any part of the effect
through another transport (`requests`, httpx used directly, a raw socket) and then uses this module
can receive a `NotExecuted` that is true of this module's connections and false of the effect. The
claim holds only where every request of the effect goes through `ctrlrun.transport` or
`ctrlrun.gateway.transport.request`. A send through either on a thread that did not copy the
executor's context is still seen: it belongs to no register, so it marks every open one, which
costs claims in unrelated concurrent runs and never safety (§12.2.13). What is not seen is a
sibling thread that sends **after** a claim was already decided: the claim is about the run up to
the moment of the failure.

Where the three are observed, the answer is `NotExecuted`, chained from the original exception.
**Everywhere else the original exception propagates untouched**, and the kernel records it
`AMBIGUOUS`. A classifier that cannot observe does not claim: `ConnectionResetError` arrives both
before the peer read the request and after it acted on it, so no exception type is ever evidence.

The rule itself is `effect_state`, and it is the one implementation: `ctrlrun.gateway.outcome` and
`ctrlrun.gateway.transport` call it rather than keeping copies (§2.1).

There is no parameter, attribute or environment variable that widens what counts as `FAILED`, and
no HTTP status is ever `NotExecuted` here (§2.4, §2.6). An executor may still raise `NotExecuted` on
its own provider-specific evidence, as `v0.1 §5.5` has always let it; that is then the executor's
claim, not this module's.

Core and standard library only. **Not re-exported from `ctrlrun`**: an executor imports it by name,
and `import ctrlrun` does not load `http.client`, `urllib` or `ssl` for callers who never use it.
"""

from __future__ import annotations

import http.client
import socket
import urllib.error
import urllib.request
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final

from .effect import _EXECUTOR_RUN, EffectState, _offered
from .errors import NotExecuted

if TYPE_CHECKING:
    import ssl

__all__ = ["HTTPConnection", "HTTPSConnection", "Transport", "effect_state", "urlopen"]


class Transport(StrEnum):
    """What a transport observed, where no answer came back (SPEC-v0.2 §6.8, SPEC-v0.7 §2.1).

    Moved here from `ctrlrun.gateway.outcome`, members and values unchanged; the gateway's name is
    this object.
    """

    #: Name resolution, refusal, a connect timeout, a TLS handshake failure: the only member that
    #: asserts non-execution, and only because no request byte was handed to the socket. A pooled
    #: connection the peer closed while idle fails on *write*, which is indistinguishable from a
    #: request that arrived, so a connection that has written is never this.
    NEVER_CONNECTED = "never_connected"

    #: Write timeout, read timeout, reset, protocol error, a TLS failure after the request was
    #: offered.
    AFTER_REQUEST_SENT = "after_request_sent"

    #: A body that is not valid JSON, or not a JSON-RPC message, or whose `id` does not match; and
    #: any HTTP status with no parseable JSON-RPC body at all.
    UNREADABLE_RESPONSE = "unreadable_response"

    #: An SSE stream that closed before delivering a final response.
    STREAM_ENDED_EARLY = "stream_ended_early"

    #: The client went away mid-stream. The upstream may already have committed.
    CLIENT_DISCONNECTED = "client_disconnected"


def effect_state(observed: Transport) -> EffectState:
    """The rule (SPEC-v0.7 §2.1): `FAILED` for `NEVER_CONNECTED`, `AMBIGUOUS` for everything else.

    Decided by identity, so a string that merely equals a member's value is `AMBIGUOUS`.
    """
    if observed is Transport.NEVER_CONNECTED:
        return EffectState.FAILED
    return EffectState.AMBIGUOUS


def _named(exc: BaseException) -> str:
    """The `NotExecuted` message: what `EXECUTION_FAILED.data.error` records, so it names the
    evidence the claim rests on (§2.2)."""
    return (
        f"ctrlrun.transport: no request byte was offered before the connection failed: "
        f"{type(exc).__name__}: {exc}"
    )


class HTTPConnection(http.client.HTTPConnection):
    """`http.client.HTTPConnection`, plus `NotExecuted` from `connect()` where it is proven.

    A drop-in subclass: the constructor and every method are `http.client`'s, and what reaches the
    wire is byte for byte what `http.client` sends. Two things are recorded for the life of the
    object, and neither is ever cleared:

    - **the mark**: set in `send`, immediately before the first byte is handed to the socket and
      after any connect `send` itself triggers, so a `sendall` that raises part way counts as having
      written. `http.client` writes every request byte, a tunnel's `CONNECT` line included, through
      `send` (T229b pins that on every supported Python). The same send marks the executor run's
      register, and where there is none, every register open in the process (§12.2.13);
    - **a foreign socket**: any socket assigned to `sock` other than by this object's own
      `connect()`.

    `connect()` raises `NotExecuted`, chained from the original exception, only for an `Exception`
    from the connect it wraps, inside an executor run whose register is unmarked, on an object
    whose mark is unset and which never held a foreign socket. Everything else propagates as it was
    raised: a reused connection, a second connection after any byte of the run was offered, a
    caller's socket, a call outside any executor run, a failure after a byte was offered, an
    exception in this code's own bookkeeping, and any `BaseException` (an interrupt is never turned
    into a retry permission).

    The object's own mark is not subsumed by the register: a thread that did not copy the
    executor's context has no register, and a request it delivers on this connection is still
    remembered here when the executor's thread reuses the object. Bytes a caller writes to `sock`
    itself, rather than through `send`, are outside both, exactly as bytes sent by another client
    are.
    """

    _ctrlrun_offered: bool = False
    _ctrlrun_foreign: bool = False
    _ctrlrun_connecting: bool = False
    _ctrlrun_sock: Any = None

    @property
    def sock(self) -> Any:  # noqa: ANN401 - http.client's own annotation: a socket, or None
        return self._ctrlrun_sock

    @sock.setter
    def sock(self, value: Any) -> None:  # noqa: ANN401 - as above
        if value is not None and not self._ctrlrun_connecting:
            self._ctrlrun_foreign = True
        self._ctrlrun_sock = value

    def connect(self) -> None:
        self._ctrlrun_connecting = True
        try:
            super().connect()
        except Exception as exc:
            # SPEC-v0.7 §2.3. The evidence is read after the attempt, not before: a proxy tunnel
            # offers its `CONNECT` line through `send` inside this very call, and no record is
            # ever cleared, so reading it here sees everything that happened before as well.
            run = _EXECUTOR_RUN.get()
            proven = (
                run is not None
                and not run.offered
                and not self._ctrlrun_offered
                and not self._ctrlrun_foreign
            )
            observed = Transport.NEVER_CONNECTED if proven else Transport.AFTER_REQUEST_SENT
            if effect_state(observed) is EffectState.FAILED:
                raise NotExecuted(_named(exc)) from exc
            raise
        finally:
            self._ctrlrun_connecting = False

    def send(self, data: http.client._DataType | str) -> None:
        if self.sock is None and self.auto_open:
            self.connect()
        self._ctrlrun_offered = True
        # A send on a thread that did not copy the executor's context belongs to no register, and
        # not copying is Python's default; it marks every open run instead (§12.2.13).
        _offered(_EXECUTOR_RUN.get())
        super().send(data)


class HTTPSConnection(HTTPConnection, http.client.HTTPSConnection):
    """`http.client.HTTPSConnection`, counting above TLS.

    `send` is `HTTPConnection.send`, inherited and not overridden, so the count is of application
    bytes offered to the TLS socket: the handshake's records are written below it and are not a
    request, which is why a handshake failure is `NotExecuted` (§2.3). CPython's `ssl` has no API
    for TLS 1.3 early data, so no application byte can leave inside the handshake.
    """


class _HTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(HTTPConnection, req)


class _HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(HTTPSConnection, req, context=self._context)  # type: ignore[attr-defined]


_SCHEMES: Final = frozenset({"http", "https"})


def urlopen(
    url: str | urllib.request.Request,
    data: bytes | None = None,
    *,
    timeout: float | None = socket._GLOBAL_DEFAULT_TIMEOUT,  # type: ignore[attr-defined]
    context: ssl.SSLContext | None = None,
) -> http.client.HTTPResponse:
    """`urllib.request.urlopen` for `http` and `https`, classified (SPEC-v0.7 §2.3).

    Raises `NotExecuted`, chained from the original exception, only where the connection it opened
    failed before any request byte was offered, in this call or earlier in the same executor run,
    and only inside an executor run (`HTTPConnection` says why). Every other failure is `urllib`'s
    own exception,
    which the kernel records `AMBIGUOUS`: a reset or a timeout after the request was offered, a
    proxy that refused a tunnel after its `CONNECT` line was sent, a malformed URL or an unknown
    scheme (nothing was connected, so nothing is claimed).

    **No redirect is followed.** A `POST` answered `303` may already have created what it points
    to, so a `30x` is `urllib.error.HTTPError` like any other status, and no status is ever
    `NotExecuted` (§2.4). Proxies from the environment are honoured, since the count is taken on
    whatever socket the connection writes to. The opener carries the proxy, default-error and
    error-processor handlers, the classifier's `http` and `https` handlers, and `urllib`'s
    unknown-scheme handler, which only raises; it has no redirect, authentication, `ftp:`, `file:`
    or `data:` handler, and no opener can be passed in.

    The executor may still raise `NotExecuted` on its own evidence, such as a provider's documented
    validation error; that is its claim, and the most dangerous integration bug there is an
    executor that raises it after the remote acted.
    """
    request = url if isinstance(url, urllib.request.Request) else urllib.request.Request(url)
    if request.type not in _SCHEMES:
        raise urllib.error.URLError(f"unknown url type: {request.type}")
    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.ProxyHandler(),
        urllib.request.UnknownHandler(),
        _HTTPHandler(),
        _HTTPSHandler(context=context),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    response: http.client.HTTPResponse = opener.open(request, data, timeout)
    return response
