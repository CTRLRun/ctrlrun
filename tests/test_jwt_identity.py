"""`JWTIdentityProvider`. Build-list item 5; SPEC-v0.3 §3.4.

The acceptance tests are T88, T88b, T89, T90 and T92. Keys are generated in the test and
nothing reaches the network: the JWKS is served from a fixture that counts its fetches, and
the static-key cases assert no outbound connection is attempted.

Three sentences run through all of them:

**Identity is consumed, never invented.** The provider verifies a token somebody else issued
and maps the verified claims onto a `Principal`. It mints nothing, and the two limits it
cannot work around — an exact `issuer` and no revocation channel — are stated rather than
configured around.

**Configuration is refused before any token is seen** (T88b). Key confusion has a
configuration end and a token end; the token end is already covered by the `algorithms`
allow-list, so a test that only exercised it would prove nothing the library was not already
doing. T88b is the configuration end.

**Every refusal is asserted by cause** (T89). Twelve ways a token can be wrong all raise
`IdentityError` with a message that tells a client nothing, so a test asserting only the type
cannot tell twelve guards apart. Each case here asserts a distinguishing observable: the log
the provider wrote, and a fetch or upstream count of zero.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from ctrlrun import IdentityContext, IdentityError, InvalidArgument, Principal
from ctrlrun.jwt_identity import JWTIdentityProvider

ISSUER = "https://issuer.example"
AUDIENCE = "https://ctrlrun.example"
TOKEN_TYPE = "at+jwt"
KID = "key-1"


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 3, 10, 12, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@pytest.fixture
def clock():
    return _Clock()


@pytest.fixture(scope="module")
def keypair():
    """One RSA key for the module: generating one per test is seconds of pure waiting."""
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


@pytest.fixture(scope="module")
def other_keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return pem


def _sign(private_pem, clock, *, typ=TOKEN_TYPE, kid=None, algorithm="RS256", **overrides):
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "finance-agent",
        "email": "alice@example.com",
        "exp": int((clock.now + timedelta(minutes=10)).timestamp()),
        "iat": int(clock.now.timestamp()),
    }
    claims.update(overrides)
    claims = {key: value for key, value in claims.items() if value is not _ABSENT}
    # PyJWT drops a header whose value is None, so `typ=None` really produces a token with no
    # `typ` at all rather than the library's default of "JWT". `kid` is not the same: PyJWT
    # validates it as a string before the drop, so it is only added when there is one.
    headers = {"typ": typ}
    if kid is not None:
        headers["kid"] = kid
    return jwt.encode(claims, private_pem, algorithm=algorithm, headers=headers)


class _Absent:
    def __repr__(self) -> str:
        return "<absent>"


#: A sentinel for "this claim is not in the token at all", which `None` cannot express.
_ABSENT = _Absent()


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


def _context(token=None, headers=None):
    return IdentityContext(
        action="stripe.refund",
        environment="production",
        headers=headers if headers is not None else ({"authorization": f"Bearer {token}"}),
    )


# --- T88: a valid token becomes a Principal (§3.4) -------------------------------------


def test_T88_a_valid_token_becomes_a_principal(keypair, clock):
    private, public = keypair
    provider = _provider(public, clock)

    principal = provider.resolve(_context(_sign(private, clock)))

    assert isinstance(principal, Principal)
    assert principal.agent == "finance-agent"
    assert principal.user == "alice@example.com"
    assert principal.issuer == ISSUER
    assert principal.expires_at == clock.now + timedelta(minutes=10)


def test_T88_only_the_named_claims_reach_the_principal(keypair, clock):
    """§3.4 — an allow-list, not everything: a token carries fields nobody meant to publish
    into a receipt. Asserted by the mapping's key set, not by spot-checking one absence."""
    private, public = keypair
    provider = _provider(public, clock, claim_names=["email"])

    token = _sign(private, clock, scope="refunds:write", tenant="acme", secret_note="do not log")
    principal = provider.resolve(_context(token))

    assert set(principal.claims) == {"email"}


def test_T88_a_claim_whose_value_is_not_scalar_is_dropped(keypair, clock, caplog):
    private, public = keypair
    provider = _provider(public, clock, claim_names=["email", "roles", "level", "active"])

    token = _sign(private, clock, roles=["a", "b"], level=3, active=True)
    with caplog.at_level(logging.DEBUG, logger="ctrlrun"):
        principal = provider.resolve(_context(token))

    assert set(principal.claims) == {"email", "level", "active"}
    assert principal.claims["level"] == 3
    assert principal.claims["active"] is True


def test_T88_a_claim_name_is_a_flat_key_and_no_nesting_is_traversed(keypair, clock):
    """§3.4 — a claim called `a.b` is the key `"a.b"`. Without this, a provider that split on
    dots would read a nested value the issuer never meant to expose under that name."""
    private, public = keypair
    provider = _provider(public, clock, claim_names=["a.b"], agent_claim="a.b")

    flat = provider.resolve(_context(_sign(private, clock, **{"a.b": "flat-agent"})))

    assert flat.agent == "flat-agent"
    with pytest.raises(IdentityError):
        provider.resolve(_context(_sign(private, clock, a={"b": "nested-agent"})))


@pytest.mark.parametrize(
    "aud",
    [
        AUDIENCE,
        [AUDIENCE],
        ["https://other.example", AUDIENCE, "https://third.example"],
    ],
    ids=["string", "one-element-array", "array-among-others"],
)
def test_T88_aud_is_accepted_on_either_wire_shape(keypair, clock, aud):
    """RFC 7519 §4.1.3 permits both, and real issuers emit both."""
    private, public = keypair

    principal = _provider(public, clock).resolve(_context(_sign(private, clock, aud=aud)))

    assert principal.agent == "finance-agent"


def test_T88_an_absent_header_is_a_decline_not_a_refusal(keypair, clock):
    """§3.2 — a decline leaves the v0.1 `context()` path intact; in-process there are no
    headers at all, and a provider with nothing to say must not break existing code."""
    _, public = keypair

    assert _provider(public, clock).resolve(_context(headers={})) is None


@pytest.mark.parametrize("raw", ["Bearer", "Bearer ", "eyJhbGciOi.x.y", "Basic abc", "bearer"])
def test_T88_a_header_with_no_bearer_scheme_is_refused_not_guessed(keypair, clock, caplog, raw):
    """A bare token is as likely to be an API key pasted into the wrong variable, and reading
    it as a JWT would report the wrong failure.

    Asserted on **which** refusal, not merely that one happened: strip the scheme check and
    every one of these still raises — an API key is not a readable JWT header either — so a
    test asserting only `IdentityError` would be a negative test against behaviour the library
    refuses anyway.
    """
    _, public = keypair
    provider = _provider(public, clock)

    with caplog.at_level(logging.WARNING, logger="ctrlrun"), pytest.raises(IdentityError):
        provider.resolve(_context(headers={"authorization": raw}))

    assert "carries no 'Bearer <token>'" in caplog.text, caplog.text


@pytest.mark.parametrize("raw", ["", "   "])
def test_T88_a_blank_header_names_nobody_and_is_a_decline(keypair, clock, raw):
    """§3.1 — a header set to an empty or whitespace value names nobody, and treating it as a
    principal would be inventing one. `IdentityContext.header` counts it as absent."""
    _, public = keypair

    assert _provider(public, clock).resolve(_context(headers={"authorization": raw})) is None


def test_T88_the_bearer_scheme_is_matched_case_insensitively(keypair, clock):
    """The control for the test above: `Bearer` is a scheme name, and RFC 9110 §11.1 makes
    scheme names case-insensitive. Without this the refusal could be "any casing but one"."""
    private, public = keypair
    provider = _provider(public, clock)

    for spelling in ("Bearer", "bearer", "BEARER", "BeArEr"):
        token = _sign(private, clock)
        context = _context(headers={"authorization": f"{spelling} {token}"})
        assert provider.resolve(context) is not None, spelling


def test_T88_the_static_key_path_opens_no_socket(keypair, clock, monkeypatch):
    """§3.4 — a `public_key` deployment reaches nothing. A claim about the environment is a
    claim until something takes the network away."""
    import socket

    private, public = keypair
    for name in ("socket", "create_connection", "getaddrinfo"):
        monkeypatch.setattr(
            socket, name, lambda *a, **k: pytest.fail("the provider opened a socket")
        )

    assert _provider(public, clock).resolve(_context(_sign(private, clock))) is not None


def test_T88_public_key_accepts_a_path_as_well_as_pem(keypair, clock, tmp_path):
    """§3.4 — stated because the gateway flag is `--identity-jwt-public-key PATH` and the
    constructor takes both."""
    private, public = keypair
    pem_file = tmp_path / "key.pem"
    pem_file.write_text(public)

    provider = _provider(str(pem_file), clock)

    assert provider.resolve(_context(_sign(private, clock))).agent == "finance-agent"


# --- T88b: configuration is refused before any token is seen (§3.4) --------------------


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"public_key": None}, "exactly one"),
        ({"secret": "s"}, "exactly one"),
        ({"jwks_url": "https://issuer.example/jwks"}, "exactly one"),
        ({"public_key": None, "secret": "s", "jwks_url": "https://x/jwks"}, "exactly one"),
        ({"algorithms": ["HS256"]}, "key confusion"),
        (
            {
                "public_key": None,
                "jwks_url": "https://issuer.example/jwks",
                "algorithms": ["HS256"],
            },
            "key confusion",
        ),
        ({"public_key": None, "secret": "s", "algorithms": ["RS256"]}, "asymmetric"),
        ({"algorithms": []}, "may not be empty"),
        ({"algorithms": ["none"]}, "not an algorithm"),
        ({"algorithms": ["NONE"]}, "not an algorithm"),
        ({"algorithms": ["RS256", "None"]}, "not an algorithm"),
        ({"issuer": ""}, "issuer"),
        ({"audience": ""}, "audience"),
    ],
    ids=[
        "no-key-source",
        "two-key-sources-pem-and-secret",
        "two-key-sources-pem-and-jwks",
        "three-key-sources",
        "HS256-with-public-key",
        "HS256-with-jwks",
        "RS256-with-secret",
        "empty-algorithms",
        "algorithms-none",
        "algorithms-NONE",
        "algorithms-None-among-others",
        "empty-issuer",
        "empty-audience",
    ],
)
def test_T88b_configuration_is_refused_before_any_token_is_seen(
    keypair, clock, overrides, expected
):
    _, public = keypair

    with pytest.raises(InvalidArgument) as refused:
        _provider(public, clock, **overrides)

    assert expected in str(refused.value)


def test_T88b_token_type_has_no_default_and_must_be_passed(keypair, clock):
    """§3.4 — a required argument is how the operator is made to choose. A default would make
    the cross-JWT confusion defence of RFC 8725 §3.11 something they never saw."""
    _, public = keypair

    with pytest.raises(TypeError, match="token_type"):
        JWTIdentityProvider(
            public_key=public,
            algorithms=["RS256"],
            issuer=ISSUER,
            audience=AUDIENCE,
            clock=clock,
        )


def test_T88b_token_type_none_is_permitted_and_warns_about_what_it_gives_up(keypair, clock, caplog):
    _, public = keypair

    with caplog.at_level(logging.WARNING, logger="ctrlrun"):
        _provider(public, clock, token_type=None)

    assert "ID token" in caplog.text
    assert "RFC 8725" in caplog.text


# --- T89: every invalid token is refused, by cause (§3.4) ------------------------------


@pytest.fixture(scope="module")
def refusals(keypair, other_keypair):
    """T89's whole token table, built once.

    Module-scoped because every entry is an RS256 signature and PyJWT re-parses the PEM for
    each one: built per test, the table cost 1.3 seconds a case and 24 seconds a run, which is
    how a suite stops being run before a push. The clock is static and starts at the same
    instant every time, so one table is byte-identical to nineteen.
    """
    return _refusal_cases(keypair[0], other_keypair, _Clock())


def _refusal_cases(private, other_private, clock):
    """One entry per way a token can be wrong, with the log fragment that tells it apart."""
    future = clock.now + timedelta(hours=1)
    return {
        "expired": (
            _sign(private, clock, exp=int((clock.now - timedelta(hours=1)).timestamp())),
            "ExpiredSignature",
        ),
        "nbf-in-the-future": (
            _sign(private, clock, nbf=int(future.timestamp())),
            "ImmatureSignature",
        ),
        "wrong-aud": (_sign(private, clock, aud="https://other.example"), "aud"),
        "aud-substring-not-element": (
            _sign(private, clock, aud="https://ctrlrun.example.attacker.test"),
            "aud",
        ),
        "aud-contains-the-audience-as-a-substring": (
            _sign(private, clock, aud=["prefix-https://ctrlrun.example-suffix"]),
            "aud",
        ),
        "wrong-iss": (_sign(private, clock, iss="https://other.example"), "Issuer"),
        "bad-signature": (_sign(other_private, clock), "Signature verification failed"),
        "alg-none": (
            jwt.encode(
                {
                    "iss": ISSUER,
                    "aud": AUDIENCE,
                    "sub": "finance-agent",
                    "exp": int((clock.now + timedelta(minutes=10)).timestamp()),
                },
                key="",
                algorithm="none",
                headers={"typ": TOKEN_TYPE},
            ),
            "alg 'none' is not in the configured list",
        ),
        "alg-outside-the-list": (
            _sign(private, clock, algorithm="RS512"),
            "alg 'RS512' is not in the configured list",
        ),
        "wrong-typ": (_sign(private, clock, typ="JWT"), "typ 'JWT' is not"),
        "no-typ-at-all": (_sign(private, clock, typ=None), "typ None is not"),
        "exp-that-is-not-a-number": (
            _sign(private, clock, exp="soon"),
            "exp is 'soon', not a number",
        ),
        "nbf-that-is-not-a-number": (
            _sign(private, clock, nbf="later"),
            "nbf is 'later', not a number",
        ),
        # The cross-JWT confusion case of RFC 8725 §2: an OIDC ID token from the same issuer,
        # signed with the same key, whose `aud` is the configured audience. It passes `iss`,
        # `aud`, `exp` and the signature; `typ` is the only thing that tells it apart.
        "an-oidc-id-token-from-the-same-issuer-and-key": (
            _sign(private, clock, typ="JWT", nonce="n-0S6_WzA2Mj", auth_time=1),
            "typ 'JWT' is not",
        ),
        "no-exp": (_sign(private, clock, exp=_ABSENT), "MissingRequiredClaim"),
        "missing-agent-claim": (_sign(private, clock, sub=_ABSENT), "claim 'sub' is missing"),
        "empty-agent-claim": (_sign(private, clock, sub="   "), "claim 'sub' is missing"),
        # PyJWT's own `sub` check catches a non-string subject before the mapping does. The
        # provider's check still stands for every other `agent_claim` — the case below.
        "non-string-sub": (_sign(private, clock, sub=42), "InvalidSubject"),
        "non-string-agent-claim": (
            _sign(private, clock, actor=42, sub="finance-agent"),
            "claim 'actor' is missing",
        ),
    }


@pytest.mark.parametrize(
    "case",
    [
        "expired",
        "nbf-in-the-future",
        "wrong-aud",
        "aud-substring-not-element",
        "aud-contains-the-audience-as-a-substring",
        "wrong-iss",
        "bad-signature",
        "alg-none",
        "alg-outside-the-list",
        "wrong-typ",
        "no-typ-at-all",
        "exp-that-is-not-a-number",
        "nbf-that-is-not-a-number",
        "an-oidc-id-token-from-the-same-issuer-and-key",
        "no-exp",
        "missing-agent-claim",
        "empty-agent-claim",
        "non-string-sub",
        "non-string-agent-claim",
    ],
)
def test_T89_every_invalid_token_is_refused_by_cause(keypair, refusals, clock, caplog, case):
    _, public = keypair
    token, fragment = refusals[case]
    # The one case that needs a differently-configured provider: `agent_claim` is `sub` by
    # default, and PyJWT checks `sub`'s own type before the mapping is reached.
    provider = _provider(
        public, clock, agent_claim="actor" if case == "non-string-agent-claim" else "sub"
    )

    with caplog.at_level(logging.WARNING, logger="ctrlrun"), pytest.raises(IdentityError):
        provider.resolve(_context(token))

    assert fragment in caplog.text, caplog.text


def test_T89_the_message_a_client_sees_says_nothing_about_why(keypair, clock):
    """§8.2 — a caller whose credential was rejected learns that, and learns nothing about
    why, which is the correct amount to tell an unauthenticated caller."""
    private, public = keypair
    token = _sign(private, clock, aud="https://other.example")

    with pytest.raises(IdentityError) as refused:
        _provider(public, clock).resolve(_context(token))

    assert str(refused.value) == "the credential was rejected"


def test_T89_the_oidc_id_token_passes_every_check_but_typ(keypair, clock):
    """The control for the cross-JWT case: without it, the ID token might be refused for some
    other reason and `typ` would be a guard nothing exercises."""
    private, public = keypair
    id_token = _sign(private, clock, typ="JWT", nonce="n-0S6_WzA2Mj")
    permissive = _provider(public, clock, token_type=None)

    assert permissive.resolve(_context(id_token)).agent == "finance-agent"


def test_T89_typ_is_matched_case_insensitively_with_the_media_prefix_stripped(keypair, clock):
    """RFC 7519 §5.1 — `application/at+jwt` and `AT+JWT` are the same type."""
    private, public = keypair
    provider = _provider(public, clock)

    for spelling in ("at+jwt", "AT+JWT", "application/at+jwt", "Application/At+JWT"):
        assert provider.resolve(_context(_sign(private, clock, typ=spelling))) is not None


def test_T89_leeway_bounds_clock_skew_and_nothing_more(keypair, clock):
    private, public = keypair
    provider = _provider(public, clock, leeway=timedelta(seconds=60))
    token = _sign(private, clock, exp=int((clock.now + timedelta(seconds=10)).timestamp()))

    clock.advance(timedelta(seconds=40))
    assert provider.resolve(_context(token)) is not None

    clock.advance(timedelta(seconds=60))
    with pytest.raises(IdentityError):
        provider.resolve(_context(token))


# --- T90: JWKS handling (§3.4) ---------------------------------------------------------


def _public_pem(private_pem):
    """The public half of a PEM private key, as PEM."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    return (
        load_pem_private_key(private_pem.encode(), password=None)
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )


def _jwk(public_pem, kid, **overrides):
    """One JWK for a PEM public key, so a test can describe a key set by what it says."""
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    entry = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(load_pem_public_key(public_pem.encode())))
    entry["kid"] = kid
    entry.update(overrides)
    return entry


class _Jwks:
    """A JWK Set endpoint that counts its fetches and never touches the network.

    A double for `urllib.request.build_opener().open`, so the provider's own scheme check,
    timeout and JSON parsing all still run. It refuses nothing the real endpoint would not:
    it serves a document or raises, which is the whole of what a fetch can do.
    """

    def __init__(self, document) -> None:
        self.document = document
        self.fetches = 0
        self.raises: Exception | None = None

    def install(self, monkeypatch) -> None:
        import urllib.request

        class _Response:
            def __init__(self, payload) -> None:
                self._payload = payload

            def read(self):
                return self._payload

            def geturl(self):
                # The double answers from the URL it was asked for, which is what a fetch that
                # followed nothing does. The redirect guard has its own test, on real sockets.
                return JWKS_URL

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def opener(*handlers):
            served = self

            class _Opener:
                def open(self, request, timeout=None):
                    served.fetches += 1
                    if served.raises is not None:
                        raise served.raises
                    return _Response(json.dumps(served.document).encode())

            return _Opener()

        monkeypatch.setattr(urllib.request, "build_opener", opener)


JWKS_URL = "https://issuer.example/.well-known/jwks.json"


def _jwks_provider(clock, **overrides):
    options = {
        "jwks_url": JWKS_URL,
        "algorithms": ["RS256"],
        "issuer": ISSUER,
        "audience": AUDIENCE,
        "token_type": TOKEN_TYPE,
        "clock": clock,
    }
    options.update(overrides)
    return JWTIdentityProvider(**options)


def test_T90_an_unknown_kid_triggers_exactly_one_refresh_then_refuses(keypair, clock, monkeypatch):
    """§3.4 — at most one refresh, then a refusal; and inside the rate-limit window, no fetch
    at all, so a stream of unknown kids cannot make this process a load generator."""
    private, public = keypair
    jwks = _Jwks({"keys": [_jwk(public, KID)]})
    jwks.install(monkeypatch)
    provider = _jwks_provider(clock, jwks_min_refresh_interval=timedelta(seconds=30))

    # An unknown kid against a cold cache: exactly one refresh, still unknown, refused.
    with pytest.raises(IdentityError):
        provider.resolve(_context(_sign(private, clock, kid="rotated")))
    assert jwks.fetches == 1

    # More of the same inside the window: no fetch at all, and still refused. The loop is
    # bounded so a broken rate limit fails red rather than hanging CI.
    for _ in range(5):
        clock.advance(timedelta(seconds=5))
        with pytest.raises(IdentityError):
            provider.resolve(_context(_sign(private, clock, kid="rotated")))
    assert jwks.fetches == 1

    # Past the window: exactly one more.
    clock.advance(timedelta(seconds=6))
    with pytest.raises(IdentityError):
        provider.resolve(_context(_sign(private, clock, kid="rotated")))
    assert jwks.fetches == 2

    # And the kid that *is* published verifies off the cache the refresh populated, with no
    # further fetch — without this the counts above could be satisfied by never caching.
    assert provider.resolve(_context(_sign(private, clock, kid=KID))) is not None
    assert jwks.fetches == 2


def test_T90_a_rotated_key_is_picked_up_by_the_one_refresh(keypair, clock, monkeypatch):
    """The control: without it, a provider that never populated its cache would satisfy every
    "still refused" assertion above."""
    private, public = keypair
    jwks = _Jwks({"keys": []})
    jwks.install(monkeypatch)
    provider = _jwks_provider(clock)

    with pytest.raises(IdentityError):
        provider.resolve(_context(_sign(private, clock, kid=KID)))
    jwks.document = {"keys": [_jwk(public, KID)]}
    clock.advance(timedelta(seconds=31))

    assert provider.resolve(_context(_sign(private, clock, kid=KID))) is not None
    assert jwks.fetches == 2


@pytest.mark.parametrize("signing_key_first", [True, False], ids=["good-first", "good-second"])
def test_T90_two_keys_sharing_a_kid_refuse_the_token(
    keypair, other_keypair, clock, monkeypatch, signing_key_first
):
    """RFC 7517 §4.5 makes distinct `kid`s a SHOULD, so a duplicate is legal — and "take the
    first match" would be a silent trust decision about which key signs for this issuer.

    **Both orders**, and that is the whole test. With the duplicate guard removed one of the
    two entries wins, and in exactly one of these orders it is the key that *does* verify the
    token — so a single-order test proves nothing: the token fails its signature check either
    way and the refusal looks identical.
    """
    private, public = keypair
    entries = [_jwk(public, KID), _jwk(_public_pem(other_keypair), KID)]
    jwks = _Jwks({"keys": entries if signing_key_first else list(reversed(entries))})
    jwks.install(monkeypatch)
    provider = _jwks_provider(clock)

    with pytest.raises(IdentityError):
        provider.resolve(_context(_sign(private, clock, kid=KID)))


def test_T90_the_same_two_keys_under_distinct_kids_both_work(
    keypair, other_keypair, clock, monkeypatch
):
    """The control: the keys themselves are fine and the set parses. It is the shared `kid`
    that refuses it, not anything else about the document."""
    private, public = keypair
    jwks = _Jwks({"keys": [_jwk(public, KID), _jwk(_public_pem(other_keypair), "key-2")]})
    jwks.install(monkeypatch)

    assert _jwks_provider(clock).resolve(_context(_sign(private, clock, kid=KID))) is not None


@pytest.mark.parametrize(
    "overrides",
    [{"use": "enc"}, {"key_ops": ["encrypt"]}, {"key_ops": []}],
    ids=["use-enc", "key_ops-without-verify", "key_ops-empty"],
)
def test_T90_a_key_that_is_not_for_signatures_is_ignored(keypair, clock, monkeypatch, overrides):
    """RFC 7517 §4.2, §4.3 — a JWK Set commonly carries encryption keys, and verifying a
    signature against one is a defect."""
    private, public = keypair
    jwks = _Jwks({"keys": [_jwk(public, KID, **overrides)]})
    jwks.install(monkeypatch)

    with pytest.raises(IdentityError):
        _jwks_provider(clock).resolve(_context(_sign(private, clock, kid=KID)))


def test_T90_the_same_key_marked_sig_is_accepted(keypair, clock, monkeypatch):
    """The control for the three cases above: the key itself is fine, and it is the `use` and
    `key_ops` fields that refuse it."""
    private, public = keypair
    jwks = _Jwks({"keys": [_jwk(public, KID, use="sig", key_ops=["verify"])]})
    jwks.install(monkeypatch)

    assert _jwks_provider(clock).resolve(_context(_sign(private, clock, kid=KID))) is not None


def test_T90_a_key_whose_own_alg_excludes_the_token_is_refused(keypair, clock, monkeypatch):
    """§3.4 — the configured allow-list *and* the key's `alg` must both admit the token."""
    private, public = keypair
    jwks = _Jwks({"keys": [_jwk(public, KID, alg="RS512")]})
    jwks.install(monkeypatch)
    provider = _jwks_provider(clock, algorithms=["RS256", "RS512"])

    with pytest.raises(IdentityError):
        provider.resolve(_context(_sign(private, clock, kid=KID, algorithm="RS256")))

    assert provider.resolve(_context(_sign(private, clock, kid=KID, algorithm="RS512"))) is not None


def test_T90_a_failed_fetch_is_a_refusal_and_keeps_the_cached_keys(keypair, clock, monkeypatch):
    """§3.4 — never a fallback to an empty key set, and never a cached-forever key either:
    the cache is replaced only when a whole, well-formed document arrived."""
    private, public = keypair
    jwks = _Jwks({"keys": [_jwk(public, KID)]})
    jwks.install(monkeypatch)
    provider = _jwks_provider(clock)
    assert provider.resolve(_context(_sign(private, clock, kid=KID))) is not None

    jwks.raises = OSError("connection refused")
    clock.advance(timedelta(seconds=31))
    with pytest.raises(IdentityError):
        provider.resolve(_context(_sign(private, clock, kid="rotated")))

    # The known kid still verifies: the failed fetch discarded nothing.
    assert provider.resolve(_context(_sign(private, clock, kid=KID))) is not None


@pytest.mark.parametrize(
    "document",
    [{}, {"keys": "not a list"}, [], "not an object"],
    ids=["no-keys", "keys-not-a-list", "list", "string"],
)
def test_T90_a_malformed_jwk_set_is_a_refusal(keypair, clock, monkeypatch, document):
    private, _ = keypair
    jwks = _Jwks(document)
    jwks.install(monkeypatch)

    with pytest.raises(IdentityError):
        _jwks_provider(clock).resolve(_context(_sign(private, clock, kid=KID)))


def test_T90_a_token_with_no_kid_is_refused_on_a_jwks_deployment(keypair, clock, monkeypatch):
    private, public = keypair
    jwks = _Jwks({"keys": [_jwk(public, KID)]})
    jwks.install(monkeypatch)

    with pytest.raises(IdentityError):
        _jwks_provider(clock).resolve(_context(_sign(private, clock)))
    assert jwks.fetches == 0


def test_T90_a_non_https_jwks_url_is_never_fetched(keypair, clock, monkeypatch):
    private, public = keypair
    jwks = _Jwks({"keys": [_jwk(public, KID)]})
    jwks.install(monkeypatch)
    provider = _jwks_provider(clock, jwks_url="http://issuer.example/jwks")

    with pytest.raises(IdentityError):
        provider.resolve(_context(_sign(private, clock, kid=KID)))
    assert jwks.fetches == 0


# --- T92: the extra says so when it is missing (§1.1, §3.4) ----------------------------


def test_T92_importing_ctrlrun_pulls_in_no_jwt_module():
    """T30's list grows by one. A subprocess, because in-process this would pass or fail on
    whatever pytest happened to import first."""
    import subprocess
    import sys

    finished = subprocess.run(
        [
            sys.executable,
            "-c",
            "import ctrlrun, sys;"
            "print(sorted(n for n in sys.modules"
            " if n.split('.')[0] in ('httpx', 'opentelemetry', 'jwt')"
            " or n in ('ctrlrun.gateway', 'ctrlrun.otel', 'ctrlrun.acs', 'ctrlrun.jwt_identity')))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert finished.stdout.strip() == "[]", finished.stdout


def test_T92_constructing_without_the_extra_names_the_install_command():
    """Never `ModuleNotFoundError`: an operator reads that as a broken package rather than as
    an option they did not select (`v0.2 §1.1`)."""
    import subprocess
    import sys

    script = (
        "import sys, importlib.abc\n"
        "class Block(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] == 'jwt':\n"
        "            raise ImportError(name)\n"
        "        return None\n"
        "sys.meta_path.insert(0, Block())\n"
        "from ctrlrun import MissingDependency\n"
        "from ctrlrun.jwt_identity import JWTIdentityProvider\n"
        "try:\n"
        "    JWTIdentityProvider(secret='s', algorithms=['HS256'], issuer='i',"
        " audience='a', token_type='at+jwt')\n"
        "except MissingDependency as exc:\n"
        "    print(str(exc))\n"
        "except BaseException as exc:\n"
        "    print('WRONG:', type(exc).__name__, exc)\n"
    )
    finished = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )

    assert "pip install 'ctrlrun[identity]'" in finished.stdout, finished.stdout
    assert "WRONG" not in finished.stdout


def test_T92_the_module_itself_imports_without_the_extra():
    """The check is at construction, not at import: `from ctrlrun.jwt_identity import ...` must
    reach the class before it reaches the missing dependency, or the error is an ImportError
    from halfway down an import chain rather than the one an operator can act on."""
    import subprocess
    import sys

    finished = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, importlib.abc\n"
            "class Block(importlib.abc.MetaPathFinder):\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        if name.split('.')[0] == 'jwt':\n"
            "            raise ImportError(name)\n"
            "        return None\n"
            "sys.meta_path.insert(0, Block())\n"
            "import ctrlrun.jwt_identity as m; print(m.JWTIdentityProvider.__name__)",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert finished.stdout.strip() == "JWTIdentityProvider"


# --- The JWKS fetch follows nothing (SPEC-v0.3 §3.4) ------------------------------------


@pytest.fixture
def redirecting_issuer():
    """A real HTTP server that 302s to a second real server, on a real socket.

    Deliberately **not** the `_Jwks` double: that one replaces `urllib.request.build_opener`
    wholesale, so the opener — which is where the redirect policy lives — is never built. A
    guard nothing constructs is a guard nothing tests, and this one was wrong for exactly that
    reason: `build_opener(HTTPSHandler(...))` keeps `HTTPRedirectHandler`, because the default
    classes it drops are only those an argument is an instance or subclass of.
    """
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    served: list[str] = []

    class Attacker(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            served.append(self.path)
            body = json.dumps({"keys": [{"kid": "ATTACKER-KEY"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    attacker = ThreadingHTTPServer(("127.0.0.1", 0), Attacker)
    threading.Thread(target=attacker.serve_forever, daemon=True).start()
    stolen = f"http://127.0.0.1:{attacker.server_address[1]}/stolen"

    class Issuer(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            served.append(self.path)
            self.send_response(302)
            self.send_header("Location", stolen)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    issuer = ThreadingHTTPServer(("127.0.0.1", 0), Issuer)
    threading.Thread(target=issuer.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{issuer.server_address[1]}/jwks", served
    issuer.shutdown()
    issuer.server_close()
    attacker.shutdown()
    attacker.server_close()


def test_a_jwks_redirect_is_never_followed(keypair, clock, redirecting_issuer, monkeypatch):
    """§3.4 — "the provider MUST NOT follow a redirect to a non-HTTPS URL".

    An open redirect on the issuer's domain would otherwise make this process fetch its
    signing keys, in cleartext, from wherever the redirect pointed — and cache them for the
    life of the process, so every token the attacker then signs verifies with an arbitrary
    `agent`. The scheme guard on the configured URL does not cover the URL actually fetched.

    The `https://` requirement is lifted for the length of this test, because the guard under
    test is the *redirect* one and the scheme guard would otherwise refuse before the socket
    is opened — which is mutation pattern 3, a negative test against a refusal that would have
    happened anyway.
    """
    private, _ = keypair
    url, served = redirecting_issuer
    # Only the scheme guard is lifted; `_fetch` itself — the opener, the redirect policy, the
    # final-URL check and the exception mapping — is the real code.
    monkeypatch.setattr("ctrlrun.jwt_identity.JWTIdentityProvider._checked_url", lambda self: url)
    provider = _jwks_provider(clock, jwks_url=url)

    with pytest.raises(IdentityError):
        provider.resolve(_context(_sign(private, clock, kid=KID)))

    assert served == ["/jwks"], f"the redirect was followed to {served}"


def test_the_plaintext_server_would_otherwise_be_read(
    keypair, clock, redirecting_issuer, monkeypatch
):
    """The control that makes the test above mean something.

    Point the provider straight at a plaintext server and the document **is** fetched. So the
    refusal above is the redirect policy doing its job, and not an environment in which a
    plaintext fetch could never have succeeded anyway — which is mutation pattern 3, and
    exactly how the old version of this guard shipped untested.
    """
    url, served = redirecting_issuer
    direct = url.replace("/jwks", "/direct")
    monkeypatch.setattr(
        "ctrlrun.jwt_identity.JWTIdentityProvider._checked_url", lambda self: direct
    )
    provider = _jwks_provider(clock, jwks_url=direct)

    with pytest.raises(IdentityError):  # the attacker's kid is not the token's; the fetch is
        provider.resolve(_context(_sign(keypair[0], clock, kid=KID)))

    assert "/direct" in served, f"the plaintext fetch never happened at all: {served}"


def test_the_opener_the_provider_builds_has_no_redirect_handler(clock):
    """The unit half, because the test above needs two sockets to say one thing.

    `build_opener` drops a default class only when an argument is an instance or subclass of
    it — so handing it an `HTTPSHandler` leaves `HTTPRedirectHandler` in place, which is the
    defect this asserts against. Subclassing is what displaces it.
    """
    import urllib.request

    handlers = {type(h).__name__ for h in _jwks_provider(clock)._opener().handlers}

    assert "HTTPRedirectHandler" not in handlers
    assert "_NoRedirects" in handlers
    assert issubclass(
        next(
            h
            for h in _jwks_provider(clock)._opener().handlers
            if type(h).__name__ == "_NoRedirects"
        ).__class__,
        urllib.request.HTTPRedirectHandler,
    )


def test_a_duplicate_kid_is_refused_even_when_one_of_the_pair_cannot_be_read(
    keypair, clock, monkeypatch
):
    """§3.4's reason for refusing a duplicate — "take the first match" is a silent trust
    decision about which key signs — does not stop applying because one entry is malformed.

    Recorded *before* the parse, so an unreadable duplicate cannot leave the readable one
    looking unambiguous. Written after an independent review found the guard sitting behind
    the parse, where a set claiming one `kid` twice quietly resolved to whichever half was
    well-formed.
    """
    private, public = keypair
    unreadable = {"kty": "RSA", "kid": KID, "n": "not-base64url", "e": "AQAB"}
    jwks = _Jwks({"keys": [unreadable, _jwk(public, KID)]})
    jwks.install(monkeypatch)

    with pytest.raises(IdentityError):
        _jwks_provider(clock).resolve(_context(_sign(private, clock, kid=KID)))


def test_an_unreadable_key_under_its_own_kid_is_skipped_not_fatal(keypair, clock, monkeypatch):
    """The control for the test above: an unparseable entry on its *own* `kid` is skipped and
    the rest of the set still works. Without this, "refuse the whole document on any bad
    entry" would satisfy the duplicate test for the wrong reason."""
    private, public = keypair
    unreadable = {"kty": "RSA", "kid": "broken", "n": "not-base64url", "e": "AQAB"}
    jwks = _Jwks({"keys": [unreadable, _jwk(public, KID)]})
    jwks.install(monkeypatch)

    assert _jwks_provider(clock).resolve(_context(_sign(private, clock, kid=KID))) is not None
