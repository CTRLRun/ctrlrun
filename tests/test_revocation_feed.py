"""T340 to T352: a credential revoked before its `exp` is refused (SPEC-v0.8 §6).

The whole suite is behind `ctrlrun[identity]`, as `test_jwt_identity.py` is: consuming a
Security Event Token needs JWT verification, which is why the feed ships beside the provider it
serves and not in core.
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import UTC, datetime, timedelta

import pytest

jwt = pytest.importorskip("jwt")
rsa = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.rsa")
serialization = pytest.importorskip("cryptography.hazmat.primitives.serialization")

from ctrlrun.errors import IdentityError, InvalidArgument  # noqa: E402
from ctrlrun.identity import IdentityContext  # noqa: E402
from ctrlrun.jwt_identity import JWTIdentityProvider  # noqa: E402
from ctrlrun.revocation import (  # noqa: E402
    FEED_STALE,
    REVOKING_EVENTS,
    FileRevocationFeed,
    PollingRevocationFeed,
)

ISSUER = "https://issuer.example"
AUDIENCE = "https://ctrlrun.example/api"
TOKEN_TYPE = "at+jwt"
SESSION_REVOKED = "https://schemas.openid.net/secevent/caep/event-type/session-revoked"


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, by: timedelta) -> None:
        self.now += by


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture(scope="module")
def keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public = (
        private.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return pem, public


def _sign(private_pem, clock, **overrides):
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "finance-agent",
        "email": "alice@example.com",
        "exp": int((clock.now + timedelta(minutes=10)).timestamp()),
        "iat": int(clock.now.timestamp()),
    }
    claims.update(overrides)
    return jwt.encode(claims, private_pem, algorithm="RS256", headers={"typ": TOKEN_TYPE})


def _provider(public_pem, clock, **overrides):
    options = {
        "public_key": public_pem,
        "algorithms": ["RS256"],
        "issuer": ISSUER,
        "audience": AUDIENCE,
        "token_type": TOKEN_TYPE,
        "user_claim": "email",
        "claim_names": ["email"],
        "clock": clock,
    }
    options.update(overrides)
    return JWTIdentityProvider(**options)


def _context(token):
    return IdentityContext(
        action="stripe.refund",
        environment="production",
        headers={"authorization": f"Bearer {token}"},
    )


def _set(subject="finance-agent", *, event=SESSION_REVOKED, issuer=ISSUER, fmt="iss_sub", **extra):
    """One Security Event Token, as a bare JSON object (the shape a file feed accepts)."""
    if fmt == "iss_sub":
        sub_id = {"format": "iss_sub", "iss": issuer, "sub": subject}
    elif fmt == "opaque":
        sub_id = {"format": "opaque", "id": subject}
    elif fmt == "jwt_id":
        sub_id = {"format": "jwt_id", "jti": subject}
    else:
        sub_id = {"format": fmt, "id": subject}
    document = {
        "iss": issuer,
        "jti": "set-1",
        "iat": 1,
        "aud": AUDIENCE,
        "events": {event: {"subject": sub_id}},
    }
    document.update(extra)
    return json.dumps(document)


def _feed(tmp_path, *lines, issuers=(ISSUER,), clock=None, **options):
    path = tmp_path / "revocations.jsonl"
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return FileRevocationFeed(
        path, issuers=issuers, clock=clock or (lambda: datetime.now(UTC)), **options
    )


# --- T340, T341: THE test, and its positive control -------------------------------------------


def test_T340_a_revoked_credential_with_a_future_exp_is_refused(keypair, clock, tmp_path, caplog):
    """§6.4. The sentence this deletes: *a verified token is valid until its `exp`*.

    And **nothing is written**: no event, no receipt. `resolve` is called before an `Action`
    exists, so there is no `action_id` to attribute a refusal to, and §6.4 argues that
    asymmetry rather than papering over it -- an expired credential leaves a receipt, a revoked
    one leaves a log line. This asserts the log line, because it is the whole of the evidence.
    """
    private, public = keypair
    token = _sign(private, clock)
    feed = _feed(tmp_path, _set("finance-agent"), clock=clock)
    provider = _provider(public, clock, revocations=feed)

    with caplog.at_level(logging.WARNING, logger="ctrlrun"), pytest.raises(IdentityError):
        provider.resolve(_context(token))

    assert any("revoked this credential before its exp" in r.message for r in caplog.records), (
        f"nothing said why: {[r.message for r in caplog.records]}"
    )
    # The token is not expired: this is the point of the test, not a precondition of it.
    assert (
        datetime.fromtimestamp(jwt.decode(token, options={"verify_signature": False})["exp"], UTC)
        > clock.now
    )


def test_T341_an_unrevoked_credential_from_the_same_issuer_is_admitted(keypair, clock, tmp_path):
    """§6.4's positive control, **in the same run**. A feed that refuses everything is not a
    feed, and a test that only asserted a refusal could not tell the two apart."""
    private, public = keypair
    feed = _feed(tmp_path, _set("finance-agent"), clock=clock)
    provider = _provider(public, clock, revocations=feed)

    with pytest.raises(IdentityError):
        provider.resolve(_context(_sign(private, clock, sub="finance-agent")))

    principal = provider.resolve(_context(_sign(private, clock, sub="billing-agent")))

    assert principal is not None
    assert principal.agent == "billing-agent"


# --- T342: matched against the token, never against Principal.agent ---------------------------


def test_T342_the_match_is_against_the_tokens_own_sub_not_the_agent_claim(keypair, clock, tmp_path):
    """§6.3, and this is the one that decides whether the feature works at all.

    `agent` is whatever `agent_claim` names. A deployment setting `agent_claim: client_id`
    produces a principal whose `agent` is a client id, and matching an `iss_sub` identifier
    against that compares two different things -- so the feed would admit exactly the
    deployment it was bought for.
    """
    private, public = keypair
    token = _sign(private, clock, sub="finance-agent", client_id="app-7f3")
    feed = _feed(tmp_path, _set("finance-agent"), clock=clock)
    provider = _provider(public, clock, agent_claim="client_id", revocations=feed)

    # The control: without the feed this same token resolves, and its agent is the client id.
    admitted = _provider(public, clock, agent_claim="client_id").resolve(_context(token))
    assert admitted is not None and admitted.agent == "app-7f3"
    assert admitted.agent != "finance-agent", "the two names must differ or this proves nothing"

    with pytest.raises(IdentityError):
        provider.resolve(_context(token))


def test_T342_a_jti_named_by_an_event_refuses_that_token_alone(keypair, clock, tmp_path):
    """§6.3: `jti` is read where it exists and never stored on the `Principal`."""
    private, public = keypair
    revoked = _sign(private, clock, jti="tok-1")
    other = _sign(private, clock, jti="tok-2")
    feed = _feed(tmp_path, _set("tok-1", fmt="jwt_id"), clock=clock)
    provider = _provider(public, clock, revocations=feed)

    with pytest.raises(IdentityError):
        provider.resolve(_context(revoked))

    assert provider.resolve(_context(other)) is not None


# --- T343: no feed is 0.7.0 -------------------------------------------------------------------


def test_T343_with_no_feed_configured_nothing_changes(keypair, clock):
    """R1. A deployment that names no feed behaves exactly as 0.7.0 did."""
    private, public = keypair
    provider = _provider(public, clock)

    principal = provider.resolve(_context(_sign(private, clock)))

    assert principal is not None
    assert principal.agent == "finance-agent"
    assert principal.issuer == ISSUER


# --- T344, T345: what is consumed and changes nothing -----------------------------------------


@pytest.mark.parametrize(
    ("line", "why"),
    [
        ("{not json at all", "malformed"),
        (json.dumps({"iss": ISSUER, "events": {"https://example/unknown": {}}}), "unknown event"),
        (_set("finance-agent", fmt="phone_number"), "unknown subject format"),
        (_set("finance-agent", issuer="https://other.example"), "an issuer no provider uses"),
        (json.dumps({"iss": ISSUER, "jti": "x"}), "no events claim"),
        ("a.b", "not a compact JWT"),
    ],
)
def test_T344_everything_unrecognised_is_consumed_and_decides_nothing(
    keypair, clock, tmp_path, line, why
):
    """§6.3. Consumed, logged, and changing no decision -- asserted by a control principal that
    stays admitted, because "no exception was raised" is not the same as "nothing changed"."""
    private, public = keypair
    feed = _feed(tmp_path, line, clock=clock)
    provider = _provider(public, clock, revocations=feed)

    principal = provider.resolve(_context(_sign(private, clock)))

    assert principal is not None, f"{why} changed a decision"


def test_T345_an_event_naming_a_subject_the_feed_cannot_map_refuses_nobody(
    keypair, clock, tmp_path
):
    """§6.3. A feed that guessed would refuse a principal on a subject it could not identify."""
    private, public = keypair
    unmappable = json.dumps(
        {"iss": ISSUER, "jti": "s", "events": {SESSION_REVOKED: {"subject": {"format": "iss_sub"}}}}
    )
    feed = _feed(tmp_path, unmappable, clock=clock)
    provider = _provider(public, clock, revocations=feed)

    assert provider.resolve(_context(_sign(private, clock))) is not None


# --- T346: replay and ordering ----------------------------------------------------------------


def test_T346_the_same_event_twice_revokes_once_and_nothing_un_revokes(keypair, clock, tmp_path):
    """§6.6, and `v0.3 §5.7`'s rule one layer down: **there is no un-revoke**, in any costume.

    Replay is idempotent by construction, a set being a set. Ordering cannot matter for the
    same reason: an entry is added and never removed, so an event arriving late cannot restore
    a credential. Asserted as an absence of API as well as a behaviour.
    """
    private, public = keypair
    path = tmp_path / "revocations.jsonl"
    path.write_text(_set("finance-agent") + "\n", encoding="utf-8")
    feed = FileRevocationFeed(path, issuers=[ISSUER], clock=clock)
    provider = _provider(public, clock, revocations=feed)

    with pytest.raises(IdentityError):
        provider.resolve(_context(_sign(private, clock)))

    # The same event again, plus an older one for the same subject.
    path.write_text(
        _set("finance-agent")
        + "\n"
        + json.dumps(json.loads(_set("finance-agent")) | {"iat": 0})
        + "\n",
        encoding="utf-8",
    )
    clock.advance(timedelta(seconds=1))
    with pytest.raises(IdentityError):
        provider.resolve(_context(_sign(private, clock)))

    for name in dir(feed):
        assert "unrevoke" not in name.lower().replace("_", "")
        assert "restore" not in name.lower()


# --- T347, T348, T349: staleness --------------------------------------------------------------


def test_T347_past_the_bound_every_covered_principal_is_refused(keypair, clock, tmp_path, caplog):
    """§6.5. A security check whose answer is unavailable is fail closed."""
    private, public = keypair
    feed = _feed(tmp_path, _set("somebody-else"), clock=clock, max_staleness=timedelta(minutes=5))
    provider = _provider(public, clock, revocations=feed)

    assert provider.resolve(_context(_sign(private, clock))) is not None

    clock.advance(timedelta(minutes=6))
    with caplog.at_level(logging.WARNING, logger="ctrlrun"), pytest.raises(IdentityError):
        provider.resolve(_context(_sign(private, clock)))

    assert any(FEED_STALE in record.message for record in caplog.records), (
        f"the refusal did not name its reason: {[r.message for r in caplog.records]}"
    )


def test_T347_an_uncovered_issuers_principals_are_unaffected_by_staleness(keypair, clock, tmp_path):
    """§6.5's other half. A feed covering one issuer must not decide for another."""
    private, public = keypair
    feed = _feed(
        tmp_path,
        _set("finance-agent"),
        issuers=("https://other.example",),
        clock=clock,
        max_staleness=timedelta(minutes=5),
    )
    provider = _provider(public, clock, revocations=feed)
    clock.advance(timedelta(hours=2))

    assert provider.resolve(_context(_sign(private, clock))) is not None


def test_T348_no_bound_is_0_7_0s_availability(keypair, clock, tmp_path):
    """§6.5. Absent means no bound: the operator sees the gap in verify rather than having a
    number chosen for them."""
    private, public = keypair
    feed = _feed(tmp_path, _set("somebody-else"), clock=clock)
    provider = _provider(public, clock, revocations=feed)
    # Signed before the clock moves: PyJWT checks `iat` against the real wall clock, so a token
    # minted thirty fake days from now is "not yet valid" for a reason that has nothing to do
    # with what this test is about.
    token = _sign(private, clock, exp=int((clock.now + timedelta(days=60)).timestamp()))
    clock.advance(timedelta(days=30))

    assert provider.resolve(_context(token)) is not None
    assert feed.read_at is not None, "the last read is still reported, for verify and the log"
    assert feed.max_staleness is None


def test_T349_a_feed_that_cannot_be_read_does_not_raise_out_of_an_action(keypair, clock, tmp_path):
    """§6.9. It is a stale feed from that moment, and §6.5 decides the rest.

    With no bound set that means admitted, which is the operator's choice; with one set the
    test above shows it refuses. What must not happen either way is an `OSError` surfacing
    from somebody's `control.execute`.
    """
    private, public = keypair
    feed = _feed(tmp_path, _set("somebody-else"), clock=clock)
    (tmp_path / "revocations.jsonl").unlink()
    clock.advance(timedelta(minutes=1))

    assert provider_resolves(_provider(public, clock, revocations=feed), private, clock)

    bounded = _feed(tmp_path, _set("x"), clock=clock, max_staleness=timedelta(minutes=5))
    (tmp_path / "revocations.jsonl").unlink()
    clock.advance(timedelta(minutes=10))
    with pytest.raises(IdentityError):
        _provider(public, clock, revocations=bounded).resolve(_context(_sign(private, clock)))


def provider_resolves(provider, private, clock) -> bool:
    return provider.resolve(_context(_sign(private, clock))) is not None


# --- T350: both feeds, and the poll feed's opener ---------------------------------------------


def test_T350_the_file_feed_re_reads_when_the_file_moves(keypair, clock, tmp_path):
    private, public = keypair
    path = tmp_path / "revocations.jsonl"
    path.write_text("", encoding="utf-8")
    feed = FileRevocationFeed(path, issuers=[ISSUER], clock=clock)
    provider = _provider(public, clock, revocations=feed)

    assert provider.resolve(_context(_sign(private, clock))) is not None

    path.write_text(_set("finance-agent") + "\n", encoding="utf-8")
    import os

    os.utime(path, (clock.now.timestamp() + 60, clock.now.timestamp() + 60))
    with pytest.raises(IdentityError):
        provider.resolve(_context(_sign(private, clock)))


def test_T350_the_poll_feed_refuses_a_non_https_url():
    """The same three rules the JWKS fetch has, for the same reason: an open redirect at this
    input decides which credentials are refused."""
    with pytest.raises(InvalidArgument) as refused:
        PollingRevocationFeed("http://issuer.example/poll", issuers=[ISSUER])

    assert "HTTPS" in str(refused.value)


def test_T350_the_poll_feeds_opener_follows_nothing():
    """The opener is `jwt_identity`'s, reused deliberately: two copies of this handler would be
    two things to keep correct at the one input that decides who everybody is."""
    from ctrlrun.jwt_identity import _NoRedirects

    feed = PollingRevocationFeed("https://issuer.example/poll", issuers=[ISSUER])
    handlers = feed._opener().handlers

    assert any(isinstance(handler, _NoRedirects) for handler in handlers), (
        "the poll opener would follow a redirect"
    )
    # Cleartext is refused by the constructor rather than by the opener, exactly as the JWKS
    # url check is a separate defence from the redirect handler: `build_opener` always carries
    # a default `HTTPHandler`, and asserting its absence would be asserting something untrue
    # about the JWKS fetch too.
    with pytest.raises(InvalidArgument):
        PollingRevocationFeed("http://issuer.example/poll", issuers=[ISSUER])


def test_T350_a_compact_set_is_read_as_well_as_a_json_one(keypair, clock, tmp_path):
    """A transmitter writes compact serialization; the file feed reads both."""
    private, public = keypair
    claims = json.loads(_set("finance-agent"))
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    compact = f"eyJhbGciOiJub25lIn0.{payload}.signature"
    feed = _feed(tmp_path, compact, clock=clock)
    provider = _provider(public, clock, revocations=feed)

    with pytest.raises(IdentityError):
        provider.resolve(_context(_sign(private, clock)))


# --- T351: the extra boundary ------------------------------------------------------------------


def test_T351_importing_ctrlrun_imports_no_feed():
    """The rule item 6 is the test of: a feed that reached core would put a network client in
    the wheel every user installs."""
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import ctrlrun, sys; "
            "print('revocation' in ''.join(sys.modules)); "
            "print([m for m in sys.modules if m.split('.')[0] in "
            "{'httpx', 'jwt', 'psycopg', 'opentelemetry'}])",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.splitlines() == ["False", "[]"], result.stdout


def test_a_feed_covering_no_issuer_is_refused(tmp_path):
    """A feed affecting nothing would read as a check. Fail closed on a misconfiguration."""
    path = tmp_path / "r.jsonl"
    path.write_text("", encoding="utf-8")

    with pytest.raises(InvalidArgument):
        FileRevocationFeed(path, issuers=[])


def test_the_revoking_event_vocabulary_excludes_a_claims_change():
    """`token-claims-change` says a claim moved, not that the credential died. Treating it as a
    revocation would refuse a principal whose department changed."""
    assert not any("token-claims-change" in event for event in REVOKING_EVENTS)
    assert SESSION_REVOKED in REVOKING_EVENTS
