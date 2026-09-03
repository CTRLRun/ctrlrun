"""SPEC-v0.2 §6.8, and nothing else. Build-list item 6b; acceptance test T24.

This is the table the gateway exists for, and its asymmetry MUST NOT be inverted: the only
observation that asserts non-execution is one where no request byte can have been written.
Everything a peer says *after* dispatch is an outcome, and outcomes it does not describe are
`AMBIGUOUS`.

Pure functions here, so the table can be read against the spec line by line without an HTTP
server in the way. Item 6c wires it to real sockets and completes T24's other half — that
the upstream's own response reaches the client unchanged.
"""

from __future__ import annotations

import pytest

from ctrlrun import EffectState
from ctrlrun.gateway.outcome import (
    AMBIGUOUS_CODE,
    NOT_EXECUTED_CODE,
    PRE_DISPATCH_ERROR_CODES,
    Transport,
    UpstreamError,
    UpstreamResult,
    UpstreamStatus,
    classify,
)

COMPLETE = "complete"


# --- T24: the transport rows -----------------------------------------------------------


def test_T24_a_connection_never_established_is_the_only_transport_FAILED():
    """The one claim in the table that asserts non-execution, and it is only provable if no
    request byte can have been written (§6.8). Connection reuse is disabled for intercepted
    calls precisely so this row stays true."""
    outcome = classify(Transport.NEVER_CONNECTED)

    assert outcome.effect is EffectState.FAILED
    assert outcome.code == NOT_EXECUTED_CODE == -41011
    assert outcome.http_status == 502
    assert not outcome.relay


@pytest.mark.parametrize(
    "observed",
    [
        Transport.AFTER_REQUEST_SENT,
        Transport.UNREADABLE_RESPONSE,
        Transport.STREAM_ENDED_EARLY,
        Transport.CLIENT_DISCONNECTED,
    ],
)
def test_T24_every_other_transport_observation_is_AMBIGUOUS(observed):
    outcome = classify(observed)

    assert outcome.effect is EffectState.AMBIGUOUS
    assert outcome.code == AMBIGUOUS_CODE == -41010


def test_T24_a_client_disconnection_returns_nothing_because_the_stream_is_gone():
    """Cancellation under this revision. The upstream may already have committed, so the
    effect is AMBIGUOUS exactly as a timeout is — and there is nobody left to tell."""
    outcome = classify(Transport.CLIENT_DISCONNECTED)

    assert outcome.effect is EffectState.AMBIGUOUS
    assert outcome.http_status is None


# --- T24: the result rows --------------------------------------------------------------


def test_T24_a_complete_result_with_no_error_is_COMMITTED():
    outcome = classify(UpstreamResult(result_type=COMPLETE, is_error=False))

    assert outcome.effect is EffectState.COMMITTED
    assert outcome.relay
    assert outcome.code is None


def test_T24_is_error_true_is_AMBIGUOUS_by_default():
    """The revision's own examples of a tool execution error are "API failures · Input
    validation errors · Business logic errors" — the first says nothing about whether a side
    effect landed."""
    outcome = classify(UpstreamResult(result_type=COMPLETE, is_error=True))

    assert outcome.effect is EffectState.AMBIGUOUS
    assert outcome.relay


def test_T24_is_error_true_is_FAILED_where_the_operator_asserted_it():
    """`not_executed_on_error: true` is the `NotExecuted` of v0.1 §5.5 in YAML: the same
    assertion, made by the person who knows, with the same consequences if they are wrong."""
    outcome = classify(
        UpstreamResult(result_type=COMPLETE, is_error=True), not_executed_on_error=True
    )

    assert outcome.effect is EffectState.FAILED
    assert outcome.relay


def test_T24_not_executed_on_error_does_not_touch_a_successful_result():
    outcome = classify(
        UpstreamResult(result_type=COMPLETE, is_error=False), not_executed_on_error=True
    )

    assert outcome.effect is EffectState.COMMITTED


@pytest.mark.parametrize("result_type", [None, "partial", "streaming", "COMPLETE", ""])
def test_T24_an_unrecognized_or_absent_result_type_is_AMBIGUOUS(result_type):
    """Deliberately against the client rule (§6.8). The revision tells *clients* to treat an
    absent `resultType` as complete for compatibility with older servers; the gateway refuses
    those servers at the version check, so on a response it accepts, absence means malformed
    — and a malformed answer about a consequential action is an unknown outcome."""
    outcome = classify(UpstreamResult(result_type=result_type, is_error=False))

    assert outcome.effect is EffectState.AMBIGUOUS
    assert outcome.relay


def test_T24_an_unrecognized_result_type_is_AMBIGUOUS_even_when_it_claims_no_error():
    assert classify(UpstreamResult("who-knows", is_error=False)).effect is EffectState.AMBIGUOUS


def test_T24_an_unrecognized_result_type_outranks_not_executed_on_error():
    """The operator asserted something about *errors*, not about responses it cannot parse."""
    outcome = classify(UpstreamResult(None, is_error=True), not_executed_on_error=True)

    assert outcome.effect is EffectState.AMBIGUOUS


def test_T24_input_required_is_handed_to_section_6_9():
    """Neither naive reading is right: treating the first leg as completed records an outcome
    the upstream never reported, and refusing the continuation makes eliciting tools
    unusable. §6.9 holds the reservation open instead."""
    outcome = classify(UpstreamResult(result_type="input_required", is_error=False))

    assert outcome.effect is None
    assert outcome.elicitation


# --- T24: the JSON-RPC error rows ------------------------------------------------------


@pytest.mark.parametrize("code", sorted(PRE_DISPATCH_ERROR_CODES))
def test_T24_a_pre_dispatch_error_code_is_FAILED(code):
    """The closest thing MCP offers to v0.1's `NotExecuted`: the peer is saying, in band,
    that it rejected the request rather than running the method."""
    outcome = classify(UpstreamError(code=code))

    assert outcome.effect is EffectState.FAILED
    assert outcome.relay


def test_T24_the_pre_dispatch_set_is_exactly_the_seven_codes_the_spec_names():
    assert frozenset({-32700, -32600, -32601, -32602, -32020, -32021, -32022}) == (
        PRE_DISPATCH_ERROR_CODES
    )


@pytest.mark.parametrize("code", [-32603, -32000, -32099, -1, 0, 42, -41010])
def test_T24_every_other_error_code_is_AMBIGUOUS(code):
    """`-32603 Internal error` is not pre-dispatch and never will be. Nor is any code the
    gateway does not recognize."""
    outcome = classify(UpstreamError(code=code))

    assert outcome.effect is EffectState.AMBIGUOUS
    assert outcome.relay


def test_T24_internal_error_is_AMBIGUOUS_even_with_not_executed_on_error():
    """That flag is about a tool's `isError`, not about a JSON-RPC error."""
    outcome = classify(UpstreamError(code=-32603), not_executed_on_error=True)

    assert outcome.effect is EffectState.AMBIGUOUS


# --- T24: the HTTP rows ----------------------------------------------------------------


def test_T24_a_401_is_FAILED_so_a_retry_is_permitted_after_the_token_refreshes():
    """The most common thing that goes wrong between a gateway and an upstream. Recording it
    AMBIGUOUS would mean a routine token refresh left an effect key needing a human, which is
    how a guarantee turns into something people switch off."""
    outcome = classify(UpstreamStatus(status=401, has_www_authenticate=True))

    assert outcome.effect is EffectState.FAILED
    assert outcome.relay


def test_T24_a_401_without_a_challenge_is_still_FAILED():
    """The authorization spec puts the token check before the method either way."""
    assert classify(UpstreamStatus(401, has_www_authenticate=False)).effect is EffectState.FAILED


def test_T24_a_403_with_a_challenge_is_FAILED():
    """`insufficient_scope` is raised before the tool is reached."""
    outcome = classify(UpstreamStatus(403, has_www_authenticate=True))

    assert outcome.effect is EffectState.FAILED


def test_T24_a_403_without_a_challenge_is_AMBIGUOUS():
    """A bare 403 is not the authorization spec's pre-dispatch refusal; it is a status with
    no parseable JSON-RPC body, which is the last row of the table."""
    outcome = classify(UpstreamStatus(403, has_www_authenticate=False))

    assert outcome.effect is EffectState.AMBIGUOUS
    assert outcome.code == AMBIGUOUS_CODE


@pytest.mark.parametrize("status", [500, 502, 503, 429, 418, 404, 200])
def test_T24_any_other_status_with_no_parseable_body_is_AMBIGUOUS(status):
    outcome = classify(UpstreamStatus(status, has_www_authenticate=False))

    assert outcome.effect is EffectState.AMBIGUOUS
    assert outcome.code == AMBIGUOUS_CODE
    assert not outcome.relay


# --- the asymmetry itself --------------------------------------------------------------


def test_only_four_observations_in_the_whole_table_are_FAILED():
    """The inversion this product exists to prevent, asserted as a shape rather than a row.

    Anything after the request left the gateway is AMBIGUOUS. Only a failure to send, and a
    peer stating in band that it refused before dispatch, are FAILED.
    """
    failed = {
        classify(Transport.NEVER_CONNECTED).effect,
        classify(UpstreamError(-32602)).effect,
        classify(UpstreamStatus(401, has_www_authenticate=False)).effect,
        classify(UpstreamResult(COMPLETE, is_error=True), not_executed_on_error=True).effect,
    }
    assert failed == {EffectState.FAILED}

    after_dispatch = [
        classify(Transport.AFTER_REQUEST_SENT),
        classify(Transport.UNREADABLE_RESPONSE),
        classify(Transport.STREAM_ENDED_EARLY),
        classify(Transport.CLIENT_DISCONNECTED),
        classify(UpstreamError(-32603)),
        classify(UpstreamResult(COMPLETE, is_error=True)),
        classify(UpstreamResult(None, is_error=False)),
        classify(UpstreamStatus(500, has_www_authenticate=False)),
    ]
    assert {outcome.effect for outcome in after_dispatch} == {EffectState.AMBIGUOUS}


def test_no_observation_maps_to_a_state_outside_the_table():
    every = [
        *(classify(member) for member in Transport),
        classify(UpstreamResult(COMPLETE, is_error=False)),
        classify(UpstreamResult(COMPLETE, is_error=True)),
        classify(UpstreamResult("input_required", is_error=False)),
        classify(UpstreamResult(None, is_error=False)),
        *(classify(UpstreamError(code)) for code in (-32602, -32603)),
        *(classify(UpstreamStatus(code, has_www_authenticate=False)) for code in (401, 500)),
    ]
    permitted = {EffectState.COMMITTED, EffectState.FAILED, EffectState.AMBIGUOUS, None}

    assert {outcome.effect for outcome in every} <= permitted
