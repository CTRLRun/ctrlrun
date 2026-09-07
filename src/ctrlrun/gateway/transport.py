"""HTTP forwarding and incremental SSE decoding for the MCP gateway.

The listener supplies a request-local sink. Progress is sent immediately; an intercepted
final response is returned to Control first so its receipt exists before the client sees it.
The HTTP client is supplied lazily by server.py, keeping the gateway extra optional.
"""

from __future__ import annotations

import codecs
import json
import queue
import re
import socket
import threading
from collections.abc import Generator, Iterator, Mapping
from contextlib import suppress
from contextvars import ContextVar
from typing import Any, Protocol

from .legacy import is_event_stream, strip_event_ids
from .mcp import LEGACY_DEFAULT_REVISION, LEGACY_REVISIONS
from .outcome import Observed, Transport, UpstreamError, UpstreamResult, UpstreamStatus
from .wire import _header

HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
    }
)
BODY_HEADERS = frozenset({"content-length", "content-encoding"})


def forwarded_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Remove transfer metadata and fields nominated by the Connection header."""
    connection = _header(headers, "connection") or ""
    excluded = HOP_BY_HOP | BODY_HEADERS | {part.strip().lower() for part in connection.split(",")}
    return {key: value for key, value in headers.items() if key.lower() not in excluded}


class StreamSink(Protocol):
    def start(self, status: int, headers: Mapping[str, str], intercepted: bool) -> None: ...
    def send(self, chunk: bytes) -> None: ...
    def disconnected(self) -> bool: ...


STREAM: ContextVar[StreamSink | None] = ContextVar("ctrlrun_gateway_stream", default=None)


class _Disconnected(Exception):
    pass


def _chunks(response: Any, sink: StreamSink | None) -> Generator[bytes, None, None]:
    """Read with bounded buffering, checking client cancellation even on an idle stream."""
    if sink is None:
        yield from response.iter_bytes()
        return
    pending: queue.Queue[bytes | Exception | None] = queue.Queue(maxsize=1)
    stopped = threading.Event()

    def put(item: bytes | Exception | None) -> None:
        while not stopped.is_set():
            try:
                pending.put(item, timeout=0.1)
                return
            except queue.Full:
                pass

    def read() -> None:
        try:
            for chunk in response.iter_bytes():
                if stopped.is_set():
                    break
                put(chunk)
        except Exception as exc:
            put(exc)
        finally:
            put(None)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        while True:
            if sink.disconnected():
                raise _Disconnected
            try:
                item = pending.get(timeout=0.1)
            except queue.Empty:
                continue
            if item is None:
                return
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        stopped.set()
        # Closing a socket from another thread does not interrupt recv() on every OS.
        # Listener exchanges own their client, so shutting this socket down cannot touch
        # a connection concurrently reused by another request from a shared pool.
        network = response.extensions.get("network_stream")
        if network is not None and not response.is_closed:
            peer = network.get_extra_info("socket")
            if peer is not None:
                with suppress(OSError):
                    peer.shutdown(socket.SHUT_RDWR)
        response.close()
        reader.join(timeout=0.2)


def _events(chunks: Iterator[bytes]) -> Iterator[list[str]]:
    """SSE events, including split UTF-8 characters and CR/LF/CRLF line endings."""
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    buffered = ""
    lines: list[str] = []
    first = True
    for chunk in chunks:
        buffered += decoder.decode(chunk)
        if first and buffered:
            buffered = buffered.removeprefix("\ufeff")
            first = False
        while match := re.search(r"\r\n|\r|\n", buffered):
            if match.group() == "\r" and match.end() == len(buffered):
                break  # A CRLF may straddle two network reads.
            line, buffered = buffered[: match.start()], buffered[match.end() :]
            if line:
                lines.append(line)
            else:
                yield lines
                lines = []
    # A trailing CR terminates a line. An unterminated event is not dispatched by SSE.
    if buffered == "\r" and lines:
        yield lines


def _event_document(lines: list[str]) -> Any:
    data = []
    for line in lines:
        field, _, value = line.partition(":")
        if field == "data":
            data.append(value.removeprefix(" "))
    try:
        return json.loads("\n".join(data))
    except ValueError:
        return None


def _observe(document: Any, expected_id: Any, revision: str) -> Observed:
    # bool compares equal to int in Python, and must never pass an ID comparison.
    if (
        not isinstance(document, dict)
        or document.get("jsonrpc") != "2.0"
        or "method" in document
        or "id" not in document
        or type(document["id"]) is not type(expected_id)
        or document["id"] != expected_id
        or ("error" in document) == ("result" in document)
        or ("_meta" in document and not isinstance(document["_meta"], dict))
    ):
        return Transport.UNREADABLE_RESPONSE
    if "error" in document:
        error = document["error"]
        if not isinstance(error, dict) or type(error.get("code")) is not int:
            return Transport.UNREADABLE_RESPONSE
        if not isinstance(error.get("message"), str):
            return Transport.UNREADABLE_RESPONSE
        return UpstreamError(error["code"])
    result = document["result"]
    if not isinstance(result, dict) or type(result.get("isError", False)) is not bool:
        return Transport.UNREADABLE_RESPONSE
    result_type = result.get("resultType")
    if "resultType" not in result and revision in LEGACY_REVISIONS:
        result_type = "complete"
    return UpstreamResult(result_type=result_type, is_error=result.get("isError", False))


class HTTPForwarder:
    def __init__(self, upstream: str, timeout: float, httpx: Any) -> None:
        self.upstream = upstream
        self.timeout = timeout
        self.httpx = httpx
        self.pooled = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self.pooled.close()

    def __call__(
        self, body: bytes, headers: Mapping[str, str], *, fresh: bool
    ) -> tuple[Observed, bytes | None, int, Mapping[str, str]]:
        return self.request("POST", body, headers, fresh=fresh)

    def request(
        self, method: str, body: bytes, headers: Mapping[str, str], *, fresh: bool = False
    ) -> tuple[Observed, bytes | None, int, Mapping[str, str]]:
        relayed = forwarded_headers(headers)
        if method == "POST":
            relayed["Content-Type"] = "application/json"
        owned = fresh or STREAM.get() is not None
        client = self.httpx.Client(timeout=self.timeout) if owned else self.pooled
        try:
            with client.stream(method, self.upstream, content=body, headers=relayed) as response:
                status = response.status_code
                response_headers = dict(response.headers)
                challenge = "www-authenticate" in response.headers
                if fresh and (status == 401 or (status == 403 and challenge)):
                    return (
                        UpstreamStatus(status, challenge),
                        response.read(),
                        status,
                        response_headers,
                    )
                if is_event_stream(response.headers.get("content-type")):
                    return self._stream(response, body, headers, fresh)
                payload = response.read()
                if not fresh:
                    # A completed HTTP exchange is relayable even without a JSON body.
                    return UpstreamStatus(status, challenge), payload, status, response_headers
                request = json.loads(body)
                revision = _header(headers, "mcp-protocol-version") or LEGACY_DEFAULT_REVISION
                try:
                    document = json.loads(payload)
                except (ValueError, UnicodeDecodeError):
                    document = None
                observed = _observe(document, request.get("id"), revision)
                return (
                    observed,
                    (None if isinstance(observed, Transport) else payload),
                    status,
                    response_headers,
                )
        except (self.httpx.ConnectError, self.httpx.ConnectTimeout):
            return Transport.NEVER_CONNECTED, None, 502, {}
        except _Disconnected:
            return Transport.CLIENT_DISCONNECTED, None, 502, {}
        except Exception:
            # Every other failure may have happened after dispatch, including bad encoding.
            return Transport.AFTER_REQUEST_SENT, None, 502, {}
        finally:
            if owned:
                client.close()

    def _stream(
        self, response: Any, body: bytes, headers: Mapping[str, str], intercepted: bool
    ) -> tuple[Observed, bytes | None, int, Mapping[str, str]]:
        sink = STREAM.get()
        status, response_headers = response.status_code, dict(response.headers)
        if sink is not None:
            sink.start(status, response_headers, intercepted)
        chunks = _chunks(response, sink)
        try:
            if not intercepted:
                collected = []
                for chunk in chunks:
                    if sink is None:
                        collected.append(chunk)
                    else:
                        sink.send(chunk)
                return UpstreamStatus(status), b"".join(collected), status, response_headers
            request = json.loads(body)
            revision = _header(headers, "mcp-protocol-version") or LEGACY_DEFAULT_REVISION
            for lines in _events(chunks):
                document = _event_document(lines)
                if (
                    isinstance(document, dict)
                    and "method" not in document
                    and ("result" in document or "error" in document)
                ):
                    observed = _observe(document, request.get("id"), revision)
                    payload = (
                        None if isinstance(observed, Transport) else json.dumps(document).encode()
                    )
                    # Direct handle() callers receive JSON; the listener's sink wraps the
                    # final response as SSE after Control has persisted its outcome.
                    response_headers["content-type"] = "application/json"
                    return observed, payload, status, response_headers
                if sink is not None:
                    sink.send(("\n".join(strip_event_ids(lines)) + "\n\n").encode())
            return Transport.STREAM_ENDED_EARLY, None, 502, {}
        finally:
            chunks.close()
