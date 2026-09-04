"""JWT verification as an `IdentityProvider`. Build-list item 5; SPEC-v0.3 §3.4.

Ships in `ctrlrun[identity]` and is imported by naming it: `import ctrlrun` must not pull in
`jwt` (§1.1, T92). The dependency is resolved at construction, so an operator who has not
installed the extra gets `MissingDependency` naming the install command rather than a
`ModuleNotFoundError` from halfway down an import chain.

**CTRLRun consumes identities; it issues none.** This is not an OAuth client. It performs no
authorization-code flow, no refresh, no token exchange, no introspection and no dynamic client
registration. It verifies a token somebody else obtained and maps the verified claims onto a
`Principal`. Everything beyond verification belongs to the deployment.

Two things it deliberately cannot do, stated here rather than discovered later (§3.4):

- **A tenant-templated issuer cannot be configured correctly.** `issuer` is an exact string.
  Pointing it at a multi-tenant endpoint without pinning the tenant makes every tenant on that
  platform a valid issuer for this process, and that is the fail-open direction.
- **There is no revocation channel.** A verified token is valid until its `exp`, which is why
  one without an `exp` is refused. Nothing polls, subscribes or introspects. Short lifetimes
  are the whole of the story.
"""

from __future__ import annotations

import json
import logging
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from .action import ClaimValue, Principal
from .errors import IdentityError, InvalidArgument, MissingDependency
from .identity import IdentityContext

_LOG = logging.getLogger("ctrlrun")

#: The module this provider needs, and the extra that carries it.
_JWT_MODULE: Final = "jwt"
_EXTRA: Final = "identity"

#: RFC 6750 §2.1 — the scheme is matched case-insensitively, and a header with no scheme is
#: refused rather than guessed. "Bearer" is the only one this provider reads.
_BEARER: Final = "bearer"

#: RFC 7519 §5.1 — a `typ` may carry the `application/` prefix, which is stripped before the
#: case-insensitive comparison. `at+jwt` (RFC 9068 §4) and `JWT` are the two seen in practice.
_MEDIA_PREFIX: Final = "application/"

#: RFC 7517 §4.3 / §4.4 — a key that says what it is for. A JWK Set commonly carries
#: encryption keys, and verifying a signature against one is a defect.
_SIGNATURE_USE: Final = "sig"
_VERIFY_OP: Final = "verify"

DEFAULT_HEADER: Final = "authorization"
DEFAULT_AGENT_CLAIM: Final = "sub"
DEFAULT_LEEWAY: Final = timedelta(seconds=60)
DEFAULT_JWKS_MIN_REFRESH: Final = timedelta(seconds=30)
DEFAULT_HTTP_TIMEOUT: Final = timedelta(seconds=5)

#: The reason on every `IdentityError` this module raises, for a caller that wants to branch.
#: The *message* stays deliberately coarse where it reaches a client (§8.2): a caller whose
#: credential was rejected learns that, and nothing about why.
_REFUSED: Final = "the credential was rejected"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _jwt() -> Any:
    """The verifier, or `MissingDependency` naming the install command (§1.1, §3.4).

    `# SPEC:` the item-5 brief said "ImportError". The house rule of `v0.2 §1.1` says
    `MissingDependency`, which is what an operator can act on.
    """
    try:
        import jwt
    except ImportError as exc:  # pragma: no cover - exercised in a subprocess by T92
        raise MissingDependency(_JWT_MODULE, _EXTRA) from exc
    return jwt


class JWTIdentityProvider:
    """Verify a bearer JWT and map its verified claims onto a `Principal` (SPEC-v0.3 §3.4).

    Absent header → `None`, a **decline** (§3.2): in-process there are no headers at all, and
    a provider with nothing to say must not break code that already uses `context()`. Present
    and invalid → `IdentityError`, a **refusal**, which `Control` never backfills from.

    Every configuration mistake that can be caught before a token is seen is caught at
    construction, because that is the end that can be refused: the token end is already
    covered by the `algorithms` allow-list, and a check that only existed there would be a
    negative test against behaviour the library refuses anyway.
    """

    def __init__(
        self,
        *,
        jwks_url: str | None = None,
        public_key: str | None = None,
        secret: str | None = None,
        algorithms: Sequence[str],
        issuer: str,
        audience: str,
        token_type: str | None,
        header: str = DEFAULT_HEADER,
        agent_claim: str = DEFAULT_AGENT_CLAIM,
        user_claim: str | None = None,
        claim_names: Sequence[str] = (),
        leeway: timedelta = DEFAULT_LEEWAY,
        jwks_min_refresh_interval: timedelta = DEFAULT_JWKS_MIN_REFRESH,
        http_timeout: timedelta = DEFAULT_HTTP_TIMEOUT,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._jwt = _jwt()
        self._algorithms = _checked_algorithms(algorithms)
        _check_key_source(jwks_url, public_key, secret, self._algorithms)
        if not issuer:
            raise InvalidArgument("JWTIdentityProvider(issuer=...) is required and exact")
        if not audience:
            raise InvalidArgument("JWTIdentityProvider(audience=...) is required")
        if not header.strip():
            raise InvalidArgument("JWTIdentityProvider(header=...) must be a header name")
        if not agent_claim:
            raise InvalidArgument("JWTIdentityProvider(agent_claim=...) must be a claim name")
        self._jwks_url = jwks_url
        self._key = None if public_key is None else _read_pem(public_key)
        self._secret = secret
        self._issuer = issuer
        self._audience = audience
        self._token_type = None if token_type is None else _normalized_typ(token_type)
        self._header = header
        self._agent_claim = agent_claim
        self._user_claim = user_claim
        self._claim_names = tuple(claim_names)
        self._leeway = leeway
        self._min_refresh = jwks_min_refresh_interval
        self._http_timeout = http_timeout.total_seconds()
        self._clock = clock
        self._keys: dict[str, _Key] = {}
        self._fetched_at: datetime | None = None
        if token_type is None:
            # §3.4 — permitted, and it gives up the cross-JWT confusion defence of RFC 8725
            # §3.11: an OIDC ID token from the same issuer, signed with the same key, whose
            # `aud` is this audience, passes every remaining check.
            _LOG.warning(
                "JWTIdentityProvider(token_type=None) accepts a token of any type from %s. An "
                "ID token from that issuer, signed with the same key and carrying aud=%r, is "
                "then indistinguishable from an access token (RFC 8725 §2, SPEC-v0.3 §3.4)",
                issuer,
                audience,
            )

    def resolve(self, context: IdentityContext) -> Principal | None:
        """The principal this call's bearer token names, or `None` where there is no token."""
        raw = context.header(self._header)
        if raw is None:
            return None
        token = _bearer(raw)
        claims = self._verified(token)
        return _principal(
            claims,
            agent_claim=self._agent_claim,
            user_claim=self._user_claim,
            claim_names=self._claim_names,
            issuer=self._issuer,
        )

    # --- verification (SPEC-v0.3 §3.4) ---------------------------------------------------

    def _verified(self, token: str) -> Mapping[str, Any]:
        """Decode and check one token, or raise `IdentityError`.

        The order is deliberate: the algorithm and `typ` are read from the *unverified* header
        only to decide whether to proceed and which key to use, never to choose the
        verification algorithm — that comes from this provider's own list, which is RFC 8725
        §3.1's rule that a library "MUST NOT use any other algorithms".
        """
        header = self._unverified_header(token)
        algorithm = header.get("alg")
        if not isinstance(algorithm, str) or algorithm not in self._algorithms:
            # `alg: none` is refused here with everything else outside the list, before any
            # signature check and before a key is even selected.
            _LOG.warning("refused a token: alg %r is not in the configured list", algorithm)
            raise IdentityError(_REFUSED)
        self._check_typ(header)
        key = self._key_for(header, algorithm)
        try:
            claims = self._jwt.decode(
                token,
                key,
                algorithms=[algorithm],
                issuer=self._issuer,
                # Two checks are deliberately taken back from the library:
                #
                # `aud`, because §3.4 requires normalize-then-compare on either wire shape
                # rather than a raw `in`, which is substring matching on the string shape.
                #
                # `exp` and `nbf`, because they must be compared against **this provider's
                # clock**. Every timed component in this codebase takes one so an expiry
                # boundary can be tested exactly rather than raced, and a verifier reading
                # `time.time()` behind the injection would make that untestable — the one
                # place a test would silently start measuring the machine instead of the
                # boundary. `require` still makes a token with no `exp` a refusal.
                options={
                    "require": ["exp", "iss"],
                    "verify_aud": False,
                    "verify_exp": False,
                    "verify_nbf": False,
                    "verify_iss": True,
                },
            )
        except Exception as exc:
            _LOG.warning("refused a token: %s: %s", type(exc).__name__, exc)
            raise IdentityError(_REFUSED) from exc
        if not isinstance(claims, Mapping):  # pragma: no cover - PyJWT always returns a dict
            raise IdentityError(_REFUSED)
        self._check_window(claims)
        self._check_audience(claims)
        return claims

    def _check_window(self, claims: Mapping[str, Any]) -> None:
        """`exp` and `nbf` against this provider's clock, with `leeway` (§3.4).

        `leeway` is a bound on clock skew and nothing more: it widens the window at both ends
        by the same amount, and it is not a grace period a deployment can lean on.
        """
        now = self._clock()
        expires_at = claims.get("exp")
        if not isinstance(expires_at, int) or isinstance(expires_at, bool):
            _LOG.warning("refused a token: exp is %r, not a number", expires_at)
            raise IdentityError(_REFUSED)
        if datetime.fromtimestamp(expires_at, UTC) + self._leeway < now:
            _LOG.warning("refused a token: ExpiredSignature: it expired at %s", expires_at)
            raise IdentityError(_REFUSED)
        not_before = claims.get("nbf")
        if isinstance(not_before, int) and not isinstance(not_before, bool):
            if datetime.fromtimestamp(not_before, UTC) - self._leeway > now:
                _LOG.warning("refused a token: ImmatureSignature: nbf is %s", not_before)
                raise IdentityError(_REFUSED)
        elif not_before is not None:
            _LOG.warning("refused a token: nbf is %r, not a number", not_before)
            raise IdentityError(_REFUSED)

    def _unverified_header(self, token: str) -> Mapping[str, Any]:
        try:
            header = self._jwt.get_unverified_header(token)
        except Exception as exc:
            _LOG.warning("refused a token: its header is unreadable: %s", exc)
            raise IdentityError(_REFUSED) from exc
        if not isinstance(header, Mapping):  # pragma: no cover - PyJWT always returns a dict
            raise IdentityError(_REFUSED)
        return header

    def _check_typ(self, header: Mapping[str, Any]) -> None:
        """RFC 8725 §3.11 — explicit typing, and the whole of the cross-JWT defence (§3.4)."""
        if self._token_type is None:
            return
        declared = header.get("typ")
        if not isinstance(declared, str) or _normalized_typ(declared) != self._token_type:
            _LOG.warning(
                "refused a token: typ %r is not %r; a token of another kind from the same "
                "issuer is not an access token for this audience",
                declared,
                self._token_type,
            )
            raise IdentityError(_REFUSED)

    def _check_audience(self, claims: Mapping[str, Any]) -> None:
        """RFC 7519 §4.1.3 — `aud` is a string or an array, and membership is exact (§3.4).

        Never `self._audience in claims["aud"]`: on the string shape that is substring
        matching, and it accepts `https://ctrlrun.example` for a configured `ctrl`.
        """
        raw = claims.get("aud")
        audiences = [raw] if isinstance(raw, str) else list(raw) if isinstance(raw, list) else []
        if self._audience not in [value for value in audiences if isinstance(value, str)]:
            _LOG.warning("refused a token: aud %r does not contain the configured audience", raw)
            raise IdentityError(_REFUSED)

    # --- keys (SPEC-v0.3 §3.4) -----------------------------------------------------------

    def _key_for(self, header: Mapping[str, Any], algorithm: str) -> Any:
        """The verification key for this token: the static one, or the JWKS entry by `kid`."""
        if self._secret is not None:
            return self._secret
        if self._key is not None:
            return self._key
        assert self._jwks_url is not None
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            _LOG.warning("refused a token: a JWKS deployment needs a 'kid' and this has none")
            raise IdentityError(_REFUSED)
        found = self._keys.get(kid)
        if found is None:
            self._refresh(kid)
            found = self._keys.get(kid)
        if found is None:
            _LOG.warning("refused a token: kid %r is unknown after a refresh", kid)
            raise IdentityError(_REFUSED)
        if found.algorithm is not None and found.algorithm != algorithm:
            # §3.4 — a key carrying its own `alg` constrains itself: the configured allow-list
            # and the key's `alg` must both admit the token.
            _LOG.warning(
                "refused a token: key %r is for %s, and the token declares %s",
                kid,
                found.algorithm,
                algorithm,
            )
            raise IdentityError(_REFUSED)
        return found.material

    def _refresh(self, kid: str) -> None:
        """Fetch the JWK Set at most once per `jwks_min_refresh_interval` (§3.4).

        Rate-limited so a stream of tokens carrying unknown `kid`s cannot turn this process
        into a load generator aimed at the issuer. A token arriving inside the window is
        refused without a fetch, by the caller, which finds the `kid` still absent.
        """
        now = self._clock()
        if self._fetched_at is not None and now - self._fetched_at < self._min_refresh:
            _LOG.warning(
                "not refreshing the JWK Set for unknown kid %r: the last fetch was %s ago and "
                "the minimum interval is %s",
                kid,
                now - self._fetched_at,
                self._min_refresh,
            )
            return
        self._fetched_at = now
        # A failed fetch is a refusal and never a fallback to an empty key set: the cache is
        # replaced only when a whole, well-formed document arrived.
        self._keys = _parse_jwks(self._fetch(), self._jwt)

    def _opener(self) -> Any:
        """An opener that speaks HTTPS and follows nothing (SPEC-v0.3 §3.4)."""
        return urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl.create_default_context()), _NoRedirects()
        )

    def _checked_url(self) -> str:
        """The configured JWKS URL, which must be HTTPS (SPEC-v0.3 §3.4).

        Its own method so `_fetch` can be exercised against a plaintext test server without
        this refusing first: the *redirect* guard is a separate defence, and a test that could
        not reach it would be a negative test against a refusal that had already happened.
        """
        assert self._jwks_url is not None
        if not self._jwks_url.lower().startswith("https://"):
            _LOG.warning("the JWK Set url %s is not HTTPS", self._jwks_url)
            raise IdentityError(_REFUSED)
        return self._jwks_url

    def _fetch(self) -> Mapping[str, Any]:
        request = urllib.request.Request(
            self._checked_url(), headers={"Accept": "application/json"}
        )
        try:
            with self._opener().open(request, timeout=self._http_timeout) as response:
                # Belt and braces over `_NoRedirects`: whatever URL actually answered has to be
                # the HTTPS one that was asked for. A handler is a policy; this is the fact.
                final = response.geturl()
                if not final.lower().startswith("https://"):
                    _LOG.warning("the JWK Set fetch ended at %s, which is not HTTPS", final)
                    raise IdentityError(_REFUSED)
                document = json.loads(response.read())
        except (OSError, urllib.error.URLError, ValueError) as exc:
            _LOG.warning("the JWK Set at %s could not be fetched: %s", self._jwks_url, exc)
            raise IdentityError(_REFUSED) from exc
        if not isinstance(document, Mapping):
            _LOG.warning("the JWK Set at %s is not a JSON object", self._jwks_url)
            raise IdentityError(_REFUSED)
        return document


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect handler that redirects nowhere (SPEC-v0.3 §3.4).

    `urllib.request.build_opener` does **not** drop `HTTPRedirectHandler` when it is handed an
    `HTTPSHandler` — the default classes it removes are only the ones an argument is an
    instance or subclass of, and the two are unrelated. An opener built that way still follows
    a 302, and `HTTPRedirectHandler` permits `http`, `https` and `ftp` targets: an open
    redirect on the issuer's domain would make this process fetch its signing keys, in
    cleartext, from wherever the redirect pointed. Those keys are cached for the life of the
    process, so every token the attacker then signs verifies, with an arbitrary `agent` and
    `user`. That is the whole authority model, bypassed at the one input that decides who
    everybody is.

    Subclassing and refusing is the reliable way to say "no redirects": passing an instance of
    a subclass *does* displace the default, which passing an unrelated handler does not.
    """

    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        _LOG.warning("the JWK Set at %s redirected to %s; refusing to follow", req.full_url, newurl)
        return None


class _Key:
    """One usable verification key, and the algorithm it constrains itself to, if any."""

    __slots__ = ("algorithm", "material")

    def __init__(self, material: Any, algorithm: str | None) -> None:
        self.material = material
        self.algorithm = algorithm


def _parse_jwks(document: Mapping[str, Any], jwt: Any) -> dict[str, _Key]:
    """Turn a JWK Set into keys by `kid`, refusing the whole set on a duplicate (§3.4).

    RFC 7517 §4.5 says only that distinct keys *SHOULD* use distinct `kid` values, so a
    duplicate is legal — and "take the first match" would be a silent trust decision about
    which of two keys signs for this issuer. The whole document is refused instead.
    """
    entries = document.get("keys")
    if not isinstance(entries, list):
        _LOG.warning("the JWK Set has no 'keys' array")
        raise IdentityError(_REFUSED)
    keys: dict[str, _Key] = {}
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        kid = entry.get("kid")
        if not isinstance(kid, str) or not kid:
            continue
        if not _is_for_verification(entry):
            continue
        # §3.4's reason for the refusal — "take the first match" is a silent trust decision
        # about which key signs — applies to a `kid` claimed twice, whether or not both
        # entries parse. Seen *before* the parse, so an unreadable duplicate cannot leave the
        # readable one looking unambiguous.
        if kid in seen:
            _LOG.warning(
                "the JWK Set carries two keys with kid %r; refusing rather than choosing one",
                kid,
            )
            raise IdentityError(_REFUSED)
        seen.add(kid)
        try:
            material = jwt.PyJWK(dict(entry)).key
        except Exception as exc:
            _LOG.warning("a JWK with kid %r could not be read and was skipped: %s", kid, exc)
            continue
        algorithm = entry.get("alg")
        keys[kid] = _Key(material, algorithm if isinstance(algorithm, str) else None)
    return keys


def _is_for_verification(entry: Mapping[str, Any]) -> bool:
    """RFC 7517 §4.2, §4.3 — a key that says it is not for signatures is not used for one."""
    use = entry.get("use")
    if isinstance(use, str) and use != _SIGNATURE_USE:
        return False
    operations = entry.get("key_ops")
    return not (isinstance(operations, list) and _VERIFY_OP not in operations)


# --- construction-time refusals (SPEC-v0.3 §3.4) ----------------------------------------


def _checked_algorithms(algorithms: Sequence[str]) -> tuple[str, ...]:
    named = tuple(algorithms)
    if not named:
        raise InvalidArgument(
            "JWTIdentityProvider(algorithms=...) is required and may not be empty; there is no "
            "default and no wildcard, because the provider MUST NOT take the verification "
            "algorithm from the token (RFC 8725 §3.1)"
        )
    for algorithm in named:
        if not isinstance(algorithm, str) or algorithm.strip().lower() == "none":
            raise InvalidArgument(
                f"JWTIdentityProvider(algorithms=...): {algorithm!r} is not an algorithm; "
                "'none' means an unsigned token, which is not a credential"
            )
    return named


def _check_key_source(
    jwks_url: str | None, public_key: str | None, secret: str | None, algorithms: tuple[str, ...]
) -> None:
    """Exactly one key source, and it must match the algorithms (§3.4).

    The `HS*`-with-a-public-key case is key confusion in its plainest form: RS256→HS256 is
    literally "HMAC the token using the PEM of the public key as the secret", and the public
    key is public. It is refused at the *configuration* end, which is the end that can be
    refused — the token end is already covered by the allow-list.
    """
    sources = [jwks_url is not None, public_key is not None, secret is not None]
    if sum(sources) != 1:
        raise InvalidArgument(
            "JWTIdentityProvider needs exactly one of jwks_url, public_key or secret; "
            f"{sum(sources)} were given"
        )
    symmetric = [name for name in algorithms if name.upper().startswith("HS")]
    asymmetric = [name for name in algorithms if not name.upper().startswith("HS")]
    if symmetric and (jwks_url is not None or public_key is not None):
        raise InvalidArgument(
            f"JWTIdentityProvider: {symmetric} is symmetric and the key source is a public one. "
            "That is key confusion: an attacker HMACs a token with the PEM of the public key, "
            "which is public. A deployment that genuinely uses HS* puts its key in secret="
        )
    if asymmetric and secret is not None:
        raise InvalidArgument(
            f"JWTIdentityProvider: {asymmetric} is asymmetric and the key source is secret=. "
            "An asymmetric algorithm verifies against a public key, not a shared secret"
        )


def _read_pem(public_key: str) -> str:
    """PEM text where it begins `-----BEGIN`, and a filesystem path otherwise (§3.4)."""
    if public_key.lstrip().startswith("-----BEGIN"):
        return public_key
    try:
        return Path(public_key).read_text(encoding="utf-8")
    except OSError as exc:
        raise InvalidArgument(
            f"JWTIdentityProvider(public_key={public_key!r}) is neither PEM text nor a readable "
            f"file: {exc}"
        ) from exc


def _normalized_typ(value: str) -> str:
    """RFC 7519 §5.1 — case-insensitive, with an optional `application/` prefix stripped."""
    lowered = value.strip().lower()
    return lowered[len(_MEDIA_PREFIX) :] if lowered.startswith(_MEDIA_PREFIX) else lowered


def _bearer(raw: str) -> str:
    """The token out of `Authorization: Bearer <jwt>`, or a refusal (§3.4).

    A header with no scheme is refused rather than guessed: a bare token is as likely to be an
    API key somebody pasted into the wrong variable, and treating it as a JWT would report the
    wrong failure.
    """
    scheme, _, token = raw.partition(" ")
    if scheme.lower() != _BEARER or not token.strip():
        _LOG.warning("refused a credential: the header carries no 'Bearer <token>'")
        raise IdentityError(_REFUSED)
    return token.strip()


def _principal(
    claims: Mapping[str, Any],
    *,
    agent_claim: str,
    user_claim: str | None,
    claim_names: Sequence[str],
    issuer: str,
) -> Principal:
    """The verified claims as a `Principal` (§3.4).

    Claim names are **flat keys**, not dotted paths: a claim called `a.b` is the key `"a.b"`,
    and no nesting is traversed. Only the claims named in `claim_names` reach
    `Principal.claims` — an allow-list, because a token carries fields nobody meant to publish
    into a receipt.
    """
    agent = claims.get(agent_claim)
    if not isinstance(agent, str) or not agent.strip():
        _LOG.warning("refused a token: claim %r is missing, empty or not a string", agent_claim)
        raise IdentityError(_REFUSED)
    user = claims.get(user_claim) if user_claim else None
    selected: dict[str, ClaimValue] = {}
    for name in claim_names:
        value = claims.get(name)
        if isinstance(value, str | int | bool):  # bool is a subclass of int
            selected[name] = value
        elif value is not None:
            _LOG.debug("claim %r is %s, which a Principal cannot carry", name, type(value).__name__)
    expires_at = claims.get("exp")
    return Principal(
        agent=agent.strip(),
        user=user.strip() if isinstance(user, str) and user.strip() else None,
        claims=selected,
        issuer=issuer,
        expires_at=datetime.fromtimestamp(expires_at, UTC) if isinstance(expires_at, int) else None,
    )
