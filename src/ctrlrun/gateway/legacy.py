"""The 2025 revisions' transport mechanics, relayed without being interpreted.

SPEC-v0.2 §6.2's passthrough table, and §6.2's one real cost: **resumption is not relayed for
an intercepted call**. A legacy stream may be resumed by a `GET` carrying `Last-Event-ID`,
which would let the final response to an intercepted `tools/call` arrive on a connection the
gateway is not attributing to that action — the outcome would land somewhere the gateway
cannot see, and the reservation would lease-expire into `AMBIGUOUS` despite a perfectly good
answer having been delivered.

So the gateway strips SSE `id:` fields from an intercepted response stream, making it
non-resumable for the client as it already is for the gateway. Non-intercepted streams are
relayed verbatim, `id:` fields included, because they carry no intercepted outcome.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Final

#: §6.2 — relayed in both directions, never minted and never interpreted. The gateway holds
#: no sessions; on `2026-07-28` there are none to hold.
SESSION_HEADER: Final = "Mcp-Session-Id"

#: §6.2 — relayed unchanged on a `GET`, because those streams carry no intercepted outcome.
LAST_EVENT_ID_HEADER: Final = "Last-Event-ID"

_ID_FIELD: Final = "id:"


def strip_event_ids(lines: Iterable[str]) -> Iterator[str]:
    """Drop SSE `id:` fields from an intercepted response stream (SPEC-v0.2 §6.2).

    Line-oriented rather than event-oriented on purpose: the gateway must relay each event as
    it arrives — a buffered progress notification is not a progress notification — so it
    cannot wait for an event to end before deciding what to forward.

    Only the `id:` field is removed. `event:`, `data:` and `retry:` pass through untouched,
    as do comments and the blank lines that terminate events, so what the client parses is
    the upstream's own stream minus the one field that would let it ask for a replay.
    """
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(_ID_FIELD) or stripped == "id":
            continue
        yield line


def is_event_stream(content_type: str | None) -> bool:
    """Whether a response is SSE rather than a single JSON body."""
    return content_type is not None and content_type.split(";")[0].strip() == "text/event-stream"
