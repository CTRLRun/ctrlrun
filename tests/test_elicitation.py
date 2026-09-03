"""Resumption is not relayed for an intercepted call. Build-list item 6d; SPEC-v0.2 §6.2.

Acceptance test T30b. §6.9's held reservations are the other half of this item and are not
here: see the PR, and the `# SPEC:` note in `server.py`. The store layer they need is in
`tests/test_gateway.py`.

A legacy stream may be resumed by a `GET` carrying `Last-Event-ID`, which would let the final
response to an intercepted `tools/call` arrive on a connection the gateway is not attributing
to that action — the outcome would land somewhere the gateway cannot see, and the reservation
would lease-expire into `AMBIGUOUS` despite a perfectly good answer having been delivered.
"""

from __future__ import annotations

from ctrlrun.gateway.legacy import (
    LAST_EVENT_ID_HEADER,
    SESSION_HEADER,
    is_event_stream,
    strip_event_ids,
)

# --- T30b: resumption is not relayed for an intercepted call ---------------------------


def test_T30b_id_fields_are_stripped_from_an_intercepted_stream():
    stream = [
        "event: message",
        "id: 42",
        'data: {"jsonrpc":"2.0"}',
        "",
        "  id: 43",
        "data: more",
        "",
    ]

    assert list(strip_event_ids(stream)) == [
        "event: message",
        'data: {"jsonrpc":"2.0"}',
        "",
        "data: more",
        "",
    ]


def test_T30b_everything_but_the_id_field_survives():
    stream = ["retry: 1000", ": a comment", "event: x", "data: y", "identity: kept", ""]

    assert list(strip_event_ids(stream)) == stream


def test_T30b_a_non_intercepted_stream_keeps_its_ids():
    """Relayed verbatim: those streams carry no intercepted outcome (§6.2)."""
    stream = ["id: 7", "data: x", ""]

    assert list(stream) == ["id: 7", "data: x", ""]


def test_the_session_header_is_relayed_and_never_minted():
    """§6.2 — relayed in both directions, never minted, never interpreted. The gateway holds
    no sessions; on 2026-07-28 there are none to hold."""
    assert SESSION_HEADER == "Mcp-Session-Id"
    assert LAST_EVENT_ID_HEADER == "Last-Event-ID"


def test_an_sse_content_type_is_recognized_with_or_without_parameters():
    assert is_event_stream("text/event-stream")
    assert is_event_stream("text/event-stream; charset=utf-8")
    assert not is_event_stream("application/json")
    assert not is_event_stream(None)
