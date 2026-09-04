"""Request parsing and header-body validation. Build-list item 6b; SPEC-v0.2 §6.2, §6.4, §6.6.

The headers are checked; the body is believed. That is the whole of §6.4: the revision mirrors
`method` and `params.name` into headers so intermediaries can route without parsing bodies,
and taking that shortcut is exactly the hazard the mirroring rule exists to prevent — a load
balancer routing on the header while the server executes on the body.

Pure functions, so T20's refusal table can be read against §6.11 line by line. Item 6c gives
them a socket and completes T20's other half: that the upstream's request count stays zero.
"""

from __future__ import annotations

import base64
import json

import pytest

from ctrlrun.gateway.mcp import (
    ACCEPTED_REVISIONS,
    CURRENT_REVISION,
    DEFAULT_MAX_BODY_BYTES,
    LEGACY_DEFAULT_REVISION,
    Refusal,
    encode_header_value,
    find_unrepresentable,
    parse_request,
)

TOOLS_CALL = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {"name": "create_refund", "arguments": {"payment_id": "txn_1", "amount": 2000}},
}


def _body(document: object = None) -> bytes:
    return json.dumps(TOOLS_CALL if document is None else document).encode()


def _headers(**overrides: str | None) -> dict[str, str]:
    headers = {
        "MCP-Protocol-Version": CURRENT_REVISION,
        "Mcp-Method": "tools/call",
        "Mcp-Name": "create_refund",
    }
    for key, value in overrides.items():
        name = key.replace("__", "-").replace("_", "-")
        if value is None:
            headers.pop(name, None)
        else:
            headers[name] = value
    return headers


def _parsed(**overrides):
    result = parse_request(_body(), _headers(**overrides))
    assert not isinstance(result, Refusal), result
    return result


# --- the accepted revisions (§6.2) -----------------------------------------------------


def test_the_accepted_set_is_the_current_revision_and_the_2025_ones():
    assert frozenset({"2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26"}) == ACCEPTED_REVISIONS
    assert CURRENT_REVISION == "2026-07-28"


def test_the_deprecated_2024_transport_is_not_accepted_on_either_side():
    """The two-endpoint HTTP+SSE transport of 2024-11-05 is out of scope for v0.2 (§12)."""
    assert "2024-11-05" not in ACCEPTED_REVISIONS


def test_T20_an_unaccepted_revision_is_refused_with_32022_listing_the_accepted_ones():
    refusal = parse_request(_body(), _headers(MCP_Protocol_Version="1999-01-01"))

    assert isinstance(refusal, Refusal)
    assert (refusal.http_status, refusal.code) == (400, -32022)
    for revision in ACCEPTED_REVISIONS:
        assert revision in refusal.message


def test_an_absent_version_header_is_treated_as_the_2025_03_26_era():
    """That era's own backwards-compatibility rule. It costs nothing: a modern upstream will
    reject the forwarded request itself, and §6.8 maps that to FAILED."""
    parsed = _parsed(MCP_Protocol_Version=None, Mcp_Method=None, Mcp_Name=None)

    assert parsed.revision == LEGACY_DEFAULT_REVISION == "2025-03-26"


# --- T20: what the gateway refuses (§6.11) ---------------------------------------------


def test_T20_a_body_that_is_not_valid_json_is_refused_with_32700():
    refusal = parse_request(b"{not json", _headers())

    assert isinstance(refusal, Refusal)
    assert (refusal.http_status, refusal.code) == (400, -32700)


def test_T20_a_json_array_body_is_refused_with_32600():
    """No batch to attribute, and a batch the gateway cannot attribute to one principal and
    one action is exactly the thing it must not pass through."""
    refusal = parse_request(json.dumps([TOOLS_CALL]).encode(), _headers())

    assert isinstance(refusal, Refusal)
    assert (refusal.http_status, refusal.code) == (400, -32600)
    # The generic "not a JSON-RPC message" guard would also catch a list, with the same
    # status and code. The message is the only observable difference, and it is the one an
    # operator needs: a batch is refused for what it is, not for being malformed.
    assert "batch" in refusal.message


@pytest.mark.parametrize(
    "document",
    [
        {"id": 1, "method": "tools/call"},
        {"jsonrpc": "1.0", "id": 1, "method": "tools/call"},
        {"jsonrpc": "2.0", "id": 1},
        {"jsonrpc": "2.0", "id": 1, "method": 7},
        "a string",
        42,
        None,
    ],
)
def test_T20_a_body_that_is_not_a_jsonrpc_message_is_refused_with_32600(document):
    refusal = parse_request(json.dumps(document).encode(), _headers())

    assert isinstance(refusal, Refusal)
    assert (refusal.http_status, refusal.code) == (400, -32600)


def test_T20_a_well_formed_call_with_the_wrong_jsonrpc_version_is_still_refused():
    """The `jsonrpc` check on its own, isolated.

    Every other malformed body in the table above is also caught by a later guard with the
    same status and code, so removing this check changed nothing any of them could see. This
    body is well-formed in every other respect: only the version is wrong.
    """
    document = {**TOOLS_CALL, "jsonrpc": "1.0"}

    refusal = parse_request(json.dumps(document).encode(), _headers())

    assert isinstance(refusal, Refusal)
    assert (refusal.http_status, refusal.code) == (400, -32600)


def test_T20_a_well_formed_call_with_no_jsonrpc_member_is_still_refused():
    document = {key: value for key, value in TOOLS_CALL.items() if key != "jsonrpc"}

    refusal = parse_request(json.dumps(document).encode(), _headers())

    assert isinstance(refusal, Refusal)
    assert (refusal.http_status, refusal.code) == (400, -32600)


def test_T20_a_body_over_the_size_limit_is_refused_with_413():
    """A body the gateway will not read is a body it cannot decide about."""
    oversized = json.dumps(
        {**TOOLS_CALL, "params": {"name": "create_refund", "arguments": {"pad": "x" * 200}}}
    ).encode()

    refusal = parse_request(oversized, _headers(), max_body_bytes=64)

    assert isinstance(refusal, Refusal)
    assert refusal.http_status == 413
    assert refusal.code is None


def test_the_default_body_limit_is_one_mebibyte():
    assert DEFAULT_MAX_BODY_BYTES == 1024 * 1024


def test_T20_a_jsonrpc_response_body_is_refused_on_the_current_revision():
    response = {"jsonrpc": "2.0", "id": 1, "result": {"resultType": "complete"}}

    refusal = parse_request(json.dumps(response).encode(), _headers(Mcp_Method=None, Mcp_Name=None))

    assert isinstance(refusal, Refusal)
    assert (refusal.http_status, refusal.code) == (400, -32600)


def test_T20_a_jsonrpc_response_body_is_relayed_on_a_legacy_revision():
    """Permitted only on legacy revisions (§6.2's passthrough table)."""
    response = {"jsonrpc": "2.0", "id": 1, "result": {"resultType": "complete"}}

    parsed = parse_request(json.dumps(response).encode(), {"MCP-Protocol-Version": "2025-11-25"})

    assert not isinstance(parsed, Refusal)
    assert parsed.is_response
    assert not parsed.intercept


# --- T20: header-body validation (§6.4) ------------------------------------------------


def test_T20_a_missing_Mcp_Method_is_refused_on_the_current_revision():
    refusal = parse_request(_body(), _headers(Mcp_Method=None))

    assert isinstance(refusal, Refusal)
    assert (refusal.http_status, refusal.code) == (400, -32020)


def test_T20_a_missing_Mcp_Name_is_refused_on_the_current_revision():
    refusal = parse_request(_body(), _headers(Mcp_Name=None))

    assert isinstance(refusal, Refusal)
    assert (refusal.http_status, refusal.code) == (400, -32020)


def test_T20_an_Mcp_Name_disagreeing_with_params_name_is_refused():
    refusal = parse_request(_body(), _headers(Mcp_Name="something_else"))

    assert isinstance(refusal, Refusal)
    assert (refusal.http_status, refusal.code) == (400, -32020)


def test_T20_an_Mcp_Method_disagreeing_with_the_body_is_refused():
    refusal = parse_request(_body(), _headers(Mcp_Method="tools/list"))

    assert isinstance(refusal, Refusal)
    assert (refusal.http_status, refusal.code) == (400, -32020)


def test_T20_an_Mcp_Param_disagreeing_with_the_body_is_refused():
    headers = _headers()
    headers["Mcp-Param-payment_id"] = "txn_9"

    refusal = parse_request(_body(), headers)

    assert isinstance(refusal, Refusal)
    assert (refusal.http_status, refusal.code) == (400, -32020)


def test_an_Mcp_Param_agreeing_with_the_body_is_accepted():
    headers = _headers()
    headers["Mcp-Param-payment_id"] = "txn_1"

    assert not isinstance(parse_request(_body(), headers), Refusal)


def test_an_Mcp_Param_integer_agrees_with_its_canonical_spelling():
    """§6.4 — `2000` in a header is text and the body's is an `int`, so the comparison is
    against what JSON writes for it. This is also the control for the parametrization below:
    without it, an implementation that refused every integer header would pass those."""
    headers = _headers()
    headers["Mcp-Param-amount"] = "2000"

    assert not isinstance(parse_request(_body(), headers), Refusal)


def test_an_Mcp_Param_integer_that_differs_is_refused():
    headers = _headers()
    headers["Mcp-Param-amount"] = "2001"

    assert isinstance(parse_request(_body(), headers), Refusal)


#: Spellings that Python's `int()` accepts and that no JSON serializer produces. Each is a
#: header whose text differs from the body's value, so an intermediary that parses it with a
#: different set of rules reads a different number — the split-brain §6.4 exists to prevent.
#: JavaScript's `parseInt("4_2")` is 4, Go's `strconv.Atoi("4_2")` is an error, and Python's
#: `int("4_2")` is 42.
_LENIENT_INTEGER_SPELLINGS = [
    "2_000",  # PEP 515 underscore; JS parseInt reads 2
    "+2000",  # leading plus
    " 2000",  # leading whitespace
    "2000 ",  # trailing whitespace
    "2000\n",  # trailing newline
    "02000",  # leading zero
    "\u0662\u0660\u0660\u0660",  # arabic-indic digits for 2000
    "\uff12\uff10\uff10\uff10",  # fullwidth digits for 2000
]


@pytest.mark.parametrize("declared", _LENIENT_INTEGER_SPELLINGS)
def test_an_Mcp_Param_integer_must_be_the_body_value_as_text(declared):
    """§6.4 — the header must be the body value's canonical rendering, not merely something
    Python's `int()` maps onto it. Each of these parses to 2000 under `int()` and to something
    else, or to nothing, under another parser."""
    headers = _headers()
    headers["Mcp-Param-amount"] = declared

    refusal = parse_request(_body(), headers)

    assert isinstance(refusal, Refusal), f"{declared!r} was accepted"
    assert (refusal.http_status, refusal.code) == (400, -32020)


@pytest.mark.parametrize("declared", ["TRUE", "True", "tRuE", "1", "yes"])
def test_an_Mcp_Param_boolean_must_be_its_json_spelling(declared):
    """§6.4 — `true` and `false`, as JSON writes them. A case-insensitive match accepts four
    spellings of one value, and an intermediary comparing bytes sees four different headers."""
    document = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "create_refund", "arguments": {"partial": True}},
    }
    headers = _headers()
    headers["Mcp-Param-partial"] = declared

    refusal = parse_request(_body(document), headers)

    assert isinstance(refusal, Refusal), f"{declared!r} was accepted"
    assert (refusal.http_status, refusal.code) == (400, -32020)


def test_the_canonical_boolean_spelling_is_still_accepted():
    document = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "create_refund", "arguments": {"partial": True}},
    }
    headers = _headers()
    headers["Mcp-Param-partial"] = "true"

    assert not isinstance(parse_request(_body(document), headers), Refusal)


@pytest.mark.parametrize("value", [None, [1, 2], {"a": 1}], ids=["null", "list", "object"])
def test_an_Mcp_Param_naming_a_non_primitive_argument_is_refused(value):
    """§6.4 — the revision defines an encoding for exactly three types: string, integer and
    boolean. It permits `x-mcp-header` on nothing else, and a `null` parameter omits the header
    entirely. So no header value can agree with any of these, and comparing against a rendering
    CTRLRun invented would certify an agreement under nobody's rules but its own.

    The header carries the compact rendering the old code compared against, so each case fails
    on the rule rather than on a stray space. A float is not here: §6.6 refuses one in the body
    outright with `-41008`, so it never reaches this comparison.
    """
    document = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "create_refund", "arguments": {"extra": value}},
    }
    headers = _headers()
    headers["Mcp-Param-extra"] = json.dumps(value, separators=(",", ":"))

    refusal = parse_request(_body(document), headers)

    assert isinstance(refusal, Refusal), f"{value!r} was accepted"
    assert (refusal.http_status, refusal.code) == (400, -32020)


def test_a_float_spelling_of_an_integer_is_refused():
    """§6.4 declines the revision's numeric-leniency SHOULD (`42.0` equals `42`). v0.1 §2.3
    refuses a float in the body outright, so the leniency has no legitimate case here, and
    honouring it would mean accepting a header spelling other parsers read differently."""
    headers = _headers()
    headers["Mcp-Param-amount"] = "2000.0"

    assert isinstance(parse_request(_body(), headers), Refusal)


def test_an_Mcp_Param_naming_an_argument_the_body_does_not_have_is_refused():
    headers = _headers()
    headers["Mcp-Param-nothing"] = "x"

    assert isinstance(parse_request(_body(), headers), Refusal)


def test_the_base64_sentinel_is_decoded_before_comparing():
    """§6.4 — a header value that is not ASCII-safe arrives wrapped, and comparing the
    wrapper against the body would refuse a request that agrees perfectly."""
    document = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "créer", "arguments": {"payment_id": "txn_ü"}},
    }
    encoded = base64.b64encode("créer".encode()).decode()
    headers = {
        "MCP-Protocol-Version": CURRENT_REVISION,
        "Mcp-Method": "tools/call",
        "Mcp-Name": f"=?base64?{encoded}?=",
        "Mcp-Param-payment_id": encode_header_value("txn_ü"),
    }

    assert not isinstance(parse_request(json.dumps(document).encode(), headers), Refusal)


def test_a_base64_sentinel_that_decodes_to_something_else_is_still_refused():
    encoded = base64.b64encode(b"not_the_tool").decode()

    refusal = parse_request(_body(), _headers(Mcp_Name=f"=?base64?{encoded}?="))

    assert isinstance(refusal, Refusal)
    assert refusal.code == -32020


def test_a_malformed_base64_sentinel_is_refused_rather_than_compared_raw():
    """Fail closed: a sentinel the gateway cannot decode is a header it cannot validate."""
    refusal = parse_request(_body(), _headers(Mcp_Name="=?base64?not-base-64!!?="))

    assert isinstance(refusal, Refusal)
    assert refusal.code == -32020
    # Comparing the wrapper against the body would also refuse this one, for the wrong
    # reason — and would *accept* a body whose value happened to equal the wrapper text.
    assert "does not decode" in refusal.message


# --- T20's mirror: the 2025 revisions (§6.2) -------------------------------------------


@pytest.mark.parametrize("revision", ["2025-03-26", "2025-06-18", "2025-11-25"])
def test_T20_absent_mirrored_headers_are_not_a_refusal_on_a_legacy_revision(revision):
    """They are not part of the protocol in that era, so their absence is not a refusal —
    and the call is still decided from the body and forwarded."""
    parsed = parse_request(_body(), {"MCP-Protocol-Version": revision, "Mcp-Session-Id": "abc"})

    assert not isinstance(parsed, Refusal)
    assert parsed.intercept
    assert parsed.tool_name == "create_refund"


def test_a_legacy_request_still_validates_a_header_that_is_present():
    """Conditional on the headers existing, not absent altogether: a present header that
    disagrees with the body is the hazard §6.4 is about, whatever the revision."""
    refusal = parse_request(
        _body(), {"MCP-Protocol-Version": "2025-11-25", "Mcp-Name": "something_else"}
    )

    assert isinstance(refusal, Refusal)
    assert refusal.code == -32020


# --- §6.3: what is intercepted ---------------------------------------------------------


def test_only_tools_call_is_intercepted():
    assert _parsed().intercept


@pytest.mark.parametrize(
    "method",
    ["tools/list", "resources/read", "prompts/get", "server/discover", "subscriptions/listen"],
)
def test_every_other_method_is_relayed_not_intercepted(method):
    """`tools/list` is not an action; only calling a tool is (§6.3)."""
    document = {"jsonrpc": "2.0", "id": 1, "method": method}
    parsed = parse_request(
        json.dumps(document).encode(),
        {"MCP-Protocol-Version": CURRENT_REVISION, "Mcp-Method": method},
    )

    assert not isinstance(parsed, Refusal)
    assert not parsed.intercept


def test_interception_is_decided_from_the_body_never_from_the_header():
    """§6.4 — the headers are checked; the body is believed. A header claiming `tools/list`
    over a `tools/call` body is a refusal, not a bypass."""
    refusal = parse_request(_body(), _headers(Mcp_Method="tools/list"))

    assert isinstance(refusal, Refusal)
    assert refusal.code == -32020


def test_a_notification_carries_no_id_and_is_not_intercepted():
    document = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    parsed = parse_request(
        json.dumps(document).encode(),
        {"MCP-Protocol-Version": CURRENT_REVISION, "Mcp-Method": "notifications/initialized"},
    )

    assert not isinstance(parsed, Refusal)
    assert not parsed.intercept


def test_a_tools_call_with_no_arguments_gets_an_empty_mapping():
    """§6.6 — `params.arguments`, or `{}` if absent."""
    document = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "ping"}}
    parsed = parse_request(
        json.dumps(document).encode(),
        {"MCP-Protocol-Version": CURRENT_REVISION, "Mcp-Method": "tools/call", "Mcp-Name": "ping"},
    )

    assert parsed.arguments == {}


def test_a_tools_call_with_no_name_is_refused():
    document = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {}}
    refusal = parse_request(
        json.dumps(document).encode(),
        {"MCP-Protocol-Version": CURRENT_REVISION, "Mcp-Method": "tools/call"},
    )

    assert isinstance(refusal, Refusal)


# --- T22's half: an argument the Action model cannot represent (§6.6) ------------------


@pytest.mark.parametrize(
    ("arguments", "pointer"),
    [
        ({"amount": 20.0}, "/amount"),
        ({"a": {"b": 1.5}}, "/a/b"),
        ({"xs": [1, 2, 3.5]}, "/xs/2"),
        ({"a": [{"b": 0.1}]}, "/a/0/b"),
    ],
)
def test_a_float_anywhere_in_the_arguments_is_found_with_its_pointer(arguments, pointer):
    """`0.1` and `0.10` are the same money and different hashes; a gateway that quietly
    picked a spelling would break the approval binding for every action touching the value."""
    assert find_unrepresentable(arguments) == pointer


@pytest.mark.parametrize(
    "arguments",
    [{}, {"amount": 2000}, {"ok": True}, {"s": "0.1"}, {"n": None}, {"xs": [1, "2", None]}],
)
def test_representable_arguments_have_no_pointer(arguments):
    assert find_unrepresentable(arguments) is None


def test_a_bool_is_not_mistaken_for_a_float_or_an_int():
    assert find_unrepresentable({"flag": False}) is None
