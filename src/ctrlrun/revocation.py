"""Credential revocation, consumed (SPEC-v0.8 §6).

A verified token used to be valid until its `exp`, and this module ends that. It consumes
Security Event Tokens -- OpenID Shared Signals, the CAEP event types -- and answers one
question for `JWTIdentityProvider`: has this credential been revoked?

**Consuming only** (`v0.3 §1.1`). Nothing here issues a token, mints a key, serves an endpoint,
publishes a list or asks an introspection question. A feed reads what somebody else wrote.

**The feed can only ever refuse.** There is no path on which a feed's answer makes an
otherwise-invalid credential valid, which is the security property (§6.6): somebody who can
write the file can deny the operator's own agents, which is fail-closed and in the threat model,
and cannot admit a principal the issuer revoked.

Behind `ctrlrun[identity]`, beside the provider it serves, and never imported by
`import ctrlrun`: a feed that reached core would put a network client in the wheel every user
installs.
"""

from __future__ import annotations

import json
import logging
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .errors import InvalidArgument

_LOG = logging.getLogger("ctrlrun.revocation")


def _utc_now() -> datetime:
    return datetime.now(UTC)


#: SPEC-v0.8 §6.3 — the CAEP event types this consumes. Every other type is consumed, logged
#: and changes no decision: an event vocabulary grows, and a reader that refused on an unknown
#: type would turn somebody else's new event into an outage.
#:
#: `session-revoked` and `credential-change` are the two that mean "this credential is no longer
#: good". `token-claims-change` is deliberately **not** here: it says a claim moved, not that the
#: credential died, and treating it as a revocation would refuse a principal whose department
#: changed.
REVOKING_EVENTS: frozenset[str] = frozenset(
    {
        "https://schemas.openid.net/secevent/caep/event-type/session-revoked",
        "https://schemas.openid.net/secevent/caep/event-type/credential-change",
        "https://schemas.openid.net/secevent/risc/event-type/account-credential-change-required",
        "https://schemas.openid.net/secevent/risc/event-type/account-disabled",
    }
)

#: The reason a principal is refused because the feed could not answer (§6.5). Its own value,
#: not folded into the generic refusal, because "revoked" and "we cannot tell" are different
#: facts and an operator acts on them differently.
FEED_STALE = "revocation_feed_stale"


@runtime_checkable
class RevocationFeed(Protocol):
    """What `JWTIdentityProvider` asks before admitting a verified token (SPEC-v0.8 §6.2)."""

    def revoked(self, *, issuer: str, subject: str, token_id: str | None) -> bool:
        """Has the issuer revoked this credential?

        Matched against the token's own `iss`, `sub` and `jti` and **never** against
        `Principal.agent` (§6.3): `agent` is whatever `agent_claim` names, which an operator
        may set to `client_id` or anything else, so matching an `iss_sub` identifier against it
        would compare two different things and admit a principal the issuer revoked.
        """
        ...

    @property
    def read_at(self) -> datetime | None:
        """When this feed last read its source, or `None` if it never has."""
        ...

    @property
    def issuers(self) -> frozenset[str]:
        """The issuers this feed covers. A principal from any other is unaffected by it."""
        ...

    @property
    def max_staleness(self) -> timedelta | None:
        """How old this feed's last read may be before it refuses (§6.5).

        `None` means no bound, which is 0.7.0's availability: the operator sees the gap in
        `verify` rather than having a number chosen for them. Setting one makes the feed's
        availability part of the deployment's availability, and that trade is the operator's.
        """
        ...


class _Revocations:
    """The set a feed accumulates, and the matching rules of §6.3.

    Shared by both shipped feeds because the parsing is the same and only the transport
    differs. **There is no un-revoke** (§6.6, and `v0.3 §5.7`'s rule for delegations): entries
    are added and never removed, so an out-of-order or replayed event cannot restore a
    credential. Replay is idempotent by construction, a set being a set.
    """

    def __init__(self) -> None:
        self._subjects: set[tuple[str, str]] = set()
        self._token_ids: set[str] = set()

    def __len__(self) -> int:
        return len(self._subjects) + len(self._token_ids)

    def revoked(self, *, issuer: str, subject: str, token_id: str | None) -> bool:
        if (issuer, subject) in self._subjects:
            return True
        return token_id is not None and token_id in self._token_ids

    def consume(self, document: Mapping[str, Any], *, where: str) -> None:
        """One SET's claims. Anything unrecognised is consumed, logged and decides nothing."""
        events = document.get("events")
        if not isinstance(events, Mapping) or not events:
            _LOG.warning("%s: a security event token carries no 'events' claim; ignored", where)
            return
        for event_type, detail in events.items():
            if event_type not in REVOKING_EVENTS:
                # §6.3. An event vocabulary grows; refusing on an unknown type would turn
                # somebody else's new event into this deployment's outage.
                _LOG.info("%s: event type %r is not one this reads; ignored", where, event_type)
                continue
            subject = self._subject_of(document, detail, where=where)
            if subject is None:
                continue
            kind, identifier = subject
            if kind == "jti":
                self._token_ids.add(identifier)
            else:
                self._subjects.add((kind, identifier))

    def _subject_of(
        self, document: Mapping[str, Any], detail: object, *, where: str
    ) -> tuple[str, str] | None:
        """§6.3's subject formats, or `None` where this cannot map one.

        `iss_sub` and `opaque` from RFC 9493, plus a `jti` naming one token. A format this does
        not know refuses nobody, which is the §6.3 rule and the reason T345 exists: a feed that
        guessed would refuse a principal on a subject it could not actually identify.
        """
        claim = detail if isinstance(detail, Mapping) else {}
        found = claim.get("subject")
        if not isinstance(found, Mapping):
            found = document.get("sub_id") if isinstance(document.get("sub_id"), Mapping) else None
        if not isinstance(found, Mapping):
            _LOG.warning("%s: an event names no subject this reads; it refuses nobody", where)
            return None
        format_name = found.get("format")
        if format_name == "iss_sub":
            issuer, subject = found.get("iss"), found.get("sub")
            if isinstance(issuer, str) and issuer and isinstance(subject, str) and subject:
                return (issuer, subject)
        elif format_name == "opaque":
            identifier = found.get("id")
            if isinstance(identifier, str) and identifier:
                # §6.3: matched against the token's own `sub` under the document's issuer, and
                # against `jti`, because an opaque id is whatever the transmitter says it is.
                issuer = document.get("iss")
                if isinstance(issuer, str) and issuer:
                    return (issuer, identifier)
                return ("jti", identifier)
        elif format_name == "jwt_id":
            identifier = found.get("jti")
            if isinstance(identifier, str) and identifier:
                return ("jti", identifier)
        else:
            _LOG.warning(
                "%s: subject format %r is not one this reads; it refuses nobody",
                where,
                format_name,
            )
            return None
        _LOG.warning("%s: a %r subject is malformed; it refuses nobody", where, format_name)
        return None


class _Feed:
    """What both shipped feeds share: the set, the clock, the bound and the staleness rule."""

    def __init__(
        self,
        *,
        issuers: Iterable[str],
        max_staleness: timedelta | None,
        clock: Callable[[], datetime],
    ) -> None:
        covered = frozenset(issuer for issuer in issuers if issuer)
        if not covered:
            raise InvalidArgument(
                "a revocation feed must name the issuers it covers: a principal from an issuer "
                "no feed covers is unaffected by it, so a feed covering none affects nothing "
                "and would read as a check (SPEC-v0.8 §6.5)"
            )
        if max_staleness is not None and max_staleness <= timedelta(0):
            raise InvalidArgument("max_staleness must be a positive duration, or None")
        self._issuers = covered
        self._max_staleness = max_staleness
        self._clock = clock
        self._read_at: datetime | None = None
        self._revocations = _Revocations()

    @property
    def read_at(self) -> datetime | None:
        return self._read_at

    @property
    def issuers(self) -> frozenset[str]:
        return self._issuers

    @property
    def max_staleness(self) -> timedelta | None:
        return self._max_staleness

    def stale(self) -> bool:
        """Is this feed past its bound? (§6.5.)

        Never read before counts as stale where a bound is set: "we have no idea" is exactly
        the state the bound exists to refuse, and a feed that answered "not revoked" before its
        first successful read would admit on silence.
        """
        if self._max_staleness is None:
            return False
        if self._read_at is None:
            return True
        return self._clock() - self._read_at > self._max_staleness

    def revoked(self, *, issuer: str, subject: str, token_id: str | None) -> bool:
        self.refresh()
        return self._revocations.revoked(issuer=issuer, subject=subject, token_id=token_id)

    def refresh(self) -> None:  # pragma: no cover - each feed overrides this
        raise NotImplementedError


class FileRevocationFeed(_Feed):
    """Security Event Tokens from a file the operator's own transmitter writes (§6.2).

    One compact-serialization SET or one JSON object per line. Re-read when the file's mtime
    moves, so a transmitter appending to it is picked up without this polling anything.

    **Push (RFC 8935) is not built.** It needs an HTTP endpoint this project serves and a
    session to serve it on, which is delivery work and is on the do-not-build list beside
    notification delivery. An operator with a push transmitter writes received SETs here, which
    is a few lines of their code and none of ours.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        issuers: Iterable[str],
        max_staleness: timedelta | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        super().__init__(issuers=issuers, max_staleness=max_staleness, clock=clock)
        self._path = Path(path)
        self._mtime: float | None = None
        self.refresh()

    def refresh(self) -> None:
        try:
            mtime = self._path.stat().st_mtime
        except OSError as unreadable:
            # §6.5, §6.9: a feed that cannot be read is a **stale** feed, not an exception out
            # of somebody's action. `read_at` is left where it was and the bound decides.
            _LOG.warning("the revocation feed %s could not be read: %s", self._path, unreadable)
            return
        if self._mtime is not None and mtime == self._mtime:
            return
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError as unreadable:  # pragma: no cover - stat succeeded and read did not
            _LOG.warning("the revocation feed %s could not be read: %s", self._path, unreadable)
            return
        self._mtime = mtime
        for number, line in enumerate(text.splitlines(), start=1):
            if line.strip():
                self._consume_line(line.strip(), f"{self._path}:{number}")
        self._read_at = self._clock()

    def _consume_line(self, line: str, where: str) -> None:
        document = _set_claims(line, where=where)
        if document is not None:
            self._revocations.consume(document, where=where)


class PollingRevocationFeed(_Feed):
    """RFC 8936 poll delivery, over the hardened opener `jwt_identity` already uses (§6.2).

    No redirects, HTTPS only, a bounded body. The same three rules the JWKS fetch has, for the
    same reason: an open redirect at this input decides who everybody is.
    """

    def __init__(
        self,
        url: str,
        *,
        issuers: Iterable[str],
        max_staleness: timedelta | None = None,
        poll_interval: timedelta = timedelta(seconds=60),
        http_timeout: timedelta = timedelta(seconds=5),
        max_body_bytes: int = 1_048_576,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        super().__init__(issuers=issuers, max_staleness=max_staleness, clock=clock)
        if not url.lower().startswith("https://"):
            raise InvalidArgument(
                f"a polling revocation feed must be HTTPS, got {url!r}. This input decides "
                "which credentials are refused, and cleartext means whoever is on the path "
                "decides it (SPEC-v0.8 §6.2)"
            )
        if poll_interval <= timedelta(0):
            raise InvalidArgument("poll_interval must be a positive duration")
        self._url = url
        self._poll_interval = poll_interval
        self._http_timeout = http_timeout.total_seconds()
        self._max_body_bytes = max_body_bytes
        self._polled_at: datetime | None = None

    def refresh(self) -> None:
        now = self._clock()
        if self._polled_at is not None and now - self._polled_at < self._poll_interval:
            return
        self._polled_at = now
        document = self._fetch()
        if document is None:
            return
        sets = document.get("sets")
        if not isinstance(sets, Mapping):
            _LOG.warning("the revocation feed at %s carries no 'sets' object", self._url)
            return
        for jti, token in sets.items():
            if isinstance(token, str):
                claims = _set_claims(token, where=f"{self._url}#{jti}")
                if claims is not None:
                    self._revocations.consume(claims, where=f"{self._url}#{jti}")
        self._read_at = now

    def _opener(self) -> Any:
        """HTTPS, and follows nothing. `jwt_identity._NoRedirects`, reused deliberately: two
        copies of this handler would be two things to keep correct at the one input that
        decides who everybody is."""
        from .jwt_identity import _NoRedirects

        return urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl.create_default_context()), _NoRedirects()
        )

    def _fetch(self) -> Mapping[str, Any] | None:
        request = urllib.request.Request(
            self._url,
            data=json.dumps({"maxEvents": 100, "returnImmediately": True}).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with self._opener().open(request, timeout=self._http_timeout) as response:
                final = response.geturl()
                if not final.lower().startswith("https://"):
                    _LOG.warning("the revocation poll ended at %s, which is not HTTPS", final)
                    return None
                body = response.read(self._max_body_bytes + 1)
        except (OSError, urllib.error.URLError, ValueError) as unreachable:
            # §6.9: a transport failure is a **stale feed from that moment**, never an
            # exception out of an action. `read_at` is not advanced, so the bound decides.
            _LOG.warning(
                "the revocation feed at %s could not be polled: %s", self._url, unreachable
            )
            return None
        if len(body) > self._max_body_bytes:
            _LOG.warning(
                "the revocation feed at %s returned more than %d bytes",
                self._url,
                self._max_body_bytes,
            )
            return None
        try:
            document = json.loads(body)
        except ValueError as malformed:
            _LOG.warning("the revocation feed at %s is not JSON: %s", self._url, malformed)
            return None
        return document if isinstance(document, Mapping) else None


def _set_claims(token: str, *, where: str) -> Mapping[str, Any] | None:
    """The claims of one SET, or `None` where this cannot read it (§6.3).

    A compact JWT or a bare JSON object. **The signature is not verified here**: §6.3 says a
    feed given a key source verifies through the key handling `jwt_identity` already has, and a
    feed without one is worth what its source is worth, which §6.6 states as the trade. Either
    way the feed can only refuse, so a forged SET is a denial of service against the operator's
    own agents and never an admission.
    """
    text = token.strip()
    if text.startswith("{"):
        try:
            document = json.loads(text)
        except ValueError as malformed:
            _LOG.warning("%s: not a readable security event token: %s", where, malformed)
            return None
        return document if isinstance(document, Mapping) else None
    parts = text.split(".")
    if len(parts) != 3:
        _LOG.warning("%s: not a readable security event token; ignored", where)
        return None
    import base64

    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        document = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, TypeError) as malformed:
        _LOG.warning("%s: not a readable security event token: %s", where, malformed)
        return None
    return document if isinstance(document, Mapping) else None


__all__: Sequence[str] = (
    "FEED_STALE",
    "REVOKING_EVENTS",
    "FileRevocationFeed",
    "PollingRevocationFeed",
    "RevocationFeed",
)
