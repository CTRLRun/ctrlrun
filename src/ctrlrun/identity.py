"""Where a `Principal` comes from. Build-list item 1; SPEC-v0.3 §3.

CTRLRun **consumes** identities. It verifies what it is handed and maps the result onto a
`Principal`; it issues nothing, mints nothing, and defines no identity format. That is the whole
of this module's remit, and it is why the two providers here are so small: one asserts a fixed
identity for development, the other reads a header a proxy was supposed to have set.

The rule that makes them matter is in `control.py`: once an `authority:` section exists, the
principal is an authorization input, so a provider's answer wins over anything the calling code
says, and a provider that *raises* is never backfilled from a `context()` (§3.2). A declining
provider is a different thing from a refusing one, and the distinction is the point.

`JWTIdentityProvider` — the one that actually verifies a credential — lands with build-list item
5 in `ctrlrun[identity]`, because it needs a third-party library and this module must not.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from .action import NO_CLAIMS, ClaimValue, Principal
from .errors import InvalidArgument

_LOG = logging.getLogger("ctrlrun")

#: An `IdentityContext` with no headers. Shared, immutable, and the in-process case.
_NO_HEADERS: Mapping[str, str] = MappingProxyType({})


@dataclass(frozen=True)
class IdentityContext:
    """What a provider is told about the call it is resolving a principal for (§3.1).

    `headers` carries **lowercased** header names, because HTTP field names are
    case-insensitive and a provider that had to guess the casing would be one that sometimes
    worked. It is empty in-process; the gateway fills it.

    `agent` and `user` are what an active `context()` supplied. They are a **hint**, not an
    identity: a provider may ignore them, and both providers here do. They are passed so a
    custom provider can implement "look up the token for this agent" without a second channel.
    """

    action: str
    environment: str
    headers: Mapping[str, str] = _NO_HEADERS
    agent: str | None = None
    user: str | None = None

    def header(self, name: str) -> str | None:
        """The named header, matched case-insensitively, or `None` if absent or blank.

        Blank counts as absent: a header set to an empty or whitespace value names nobody, and
        treating it as a principal would be inventing one.
        """
        wanted = name.lower()
        for key, value in self.headers.items():
            # §3.1 says the mapping arrives lowercased, and the gateway does that. Scanning
            # case-insensitively anyway costs nothing and means a hand-built IdentityContext
            # with real HTTP casing resolves rather than silently declining.
            if key.lower() == wanted:
                return value.strip() if value and value.strip() else None
        return None


@runtime_checkable
class IdentityProvider(Protocol):
    """Resolves the principal for one action (SPEC-v0.3 §3.1).

    Returning `None` is a **decline** — "I have nothing to say about this call" — and leaves
    the v0.1 `context()` path intact, unless an `authority:` section is loaded, in which case
    §3.2 refuses rather than backfilling.

    Raising is a **refusal** — "I was given something and rejected it". `Control` never falls
    back from one: doing so would turn a rejected token into a successful action, which is the
    outcome §3 exists to prevent. Raise `IdentityError` to say so directly; anything else is
    logged and re-raised as one with the original chained.
    """

    def resolve(self, context: IdentityContext) -> Principal | None: ...


@dataclass(frozen=True)
class StaticIdentityProvider:
    """A fixed principal, for development, tests and single-tenant demonstrations (§3.3).

    It asserts an identity nobody verified, so it warns once — at construction, not per call.
    A library that warns on every action is a library whose warnings get filtered, and the
    operator who needs to see this needs to see it once, at the point they chose it.
    """

    agent: str
    user: str | None = None
    claims: Mapping[str, ClaimValue] = NO_CLAIMS
    issuer: str | None = None
    expires_at: datetime | None = None
    _principal: Principal = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_principal",
            Principal(
                agent=self.agent,
                user=self.user,
                claims=self.claims,
                issuer=self.issuer,
                expires_at=self.expires_at,
            ),
        )
        _LOG.warning(
            "StaticIdentityProvider asserts the identity %r, which nothing verified. It is for "
            "development and tests; a deployment where the principal is an authorization input "
            "wants a provider that checks a credential (SPEC-v0.3 §3.3)",
            self.agent,
        )

    def resolve(self, context: IdentityContext) -> Principal | None:
        return self._principal


@dataclass(frozen=True)
class HeaderIdentityProvider:
    """The principal named by a trusted HTTP header (§3.3).

    **A trusted header is worth exactly what the thing that sets it is worth.** This is correct
    behind a proxy that authenticates the caller and *overwrites* the header on every request,
    and worthless anywhere else: if the agent can set the header, the agent chooses its own
    authority. RFC 7239 §8.1 says the same of the header it standardizes — it "cannot be relied
    upon to be correct, as it may be modified ... by every node on the way to the server,
    including the client making the request".

    It carries no claims. A header is a name; manufacturing claims from one would be inventing
    verified data, which is the one thing this module must not do.
    """

    agent_header: str
    user_header: str | None = None
    issuer: str | None = None

    def __post_init__(self) -> None:
        if not self.agent_header.strip():
            raise InvalidArgument("agent_header must be a non-empty header name")
        _LOG.warning(
            "HeaderIdentityProvider trusts the header %r: it is worth what the proxy that sets "
            "it is worth, and must be overwritten on every request by something that "
            "authenticated the caller (SPEC-v0.3 §3.3)",
            self.agent_header,
        )

    def resolve(self, context: IdentityContext) -> Principal | None:
        agent = context.header(self.agent_header)
        if agent is None:
            # A decline, not a refusal (§3.2): in-process there are no headers at all, and a
            # provider with nothing to say must not break code that already uses context().
            return None
        user = context.header(self.user_header) if self.user_header else None
        return Principal(agent=agent, user=user, issuer=self.issuer)
