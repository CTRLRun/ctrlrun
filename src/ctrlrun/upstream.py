"""What was observed about an upstream, and whether it matches the pin. SPEC-v0.10 §4.

**Per process, by construction.** §4.3's check 2 answers from what *this* process has seen, which
is why the register is a module-level mapping and not a store table: an observation is a fact about
a connection this process made, and a second process that has made none must refuse rather than
inherit somebody else's. That is `UPSTREAM_UNVERIFIED`, and it is the fail-closed half of §4.5.

**The comparison is a pure function over two strings**, which is what lets `ctrlrun verify` grade
G27 with no TLS listener and no certificate to generate (§7): it seeds an observation and asserts
the refusal.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:  # `ssl` is stdlib but not needed unless a deployment pins (SPEC-v0.10 §4.3).
    import ssl

from .action import canonical_bytes
from .policy import UPSTREAM_MISMATCH, UPSTREAM_UNVERIFIED, UpstreamPin

#: SPEC-v0.10 §4.2 — the tool-schema hash's domain tag. `canonical_bytes` is the one
#: canonicalizer (`v0.9 §5.5` uses it for the scope hash), and a domain tag is what stops a hash
#: over a tool's advertised entry ever equalling one over some other mapping with the same keys.
_TOOL_SCHEMA_DOMAIN: Final = "ctrlrun.upstream.tool_schema/v1"

_LOCK: Final = threading.Lock()
#: upstream name -> the SHA-256 of the leaf certificate last observed for it.
_CERTS: dict[str, str] = {}
#: (upstream name, tool name) -> the hash of the tool's last advertised schema.
_TOOLS: dict[tuple[str, str], str] = {}


def tool_schema_hash(entry: Mapping[str, Any]) -> str:
    """`"sha256:" + hex(SHA-256(canonical_bytes({domain, entry})))` (SPEC-v0.10 §4.2).

    Over the **whole** advertised entry: name, description and input schema together, because a
    description that changed is a tool whose behaviour an operator has not reviewed.
    """
    payload = canonical_bytes({"schema": _TOOL_SCHEMA_DOMAIN, "entry": entry})
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def cert_hash(der: bytes) -> str:
    """The digest §4.2 pins: SHA-256 over the leaf certificate's DER bytes."""
    return "sha256:" + hashlib.sha256(der).hexdigest()


def observe_certificate(upstream: str, der: bytes) -> str:
    """Record the leaf certificate this process saw for `upstream`, and return its digest."""
    digest = cert_hash(der)
    with _LOCK:
        _CERTS[upstream] = digest
    return digest


def observe_tool_schema(upstream: str, tool: str, entry: Mapping[str, Any]) -> str:
    """Record the schema `upstream` advertised for `tool`, and return its hash."""
    digest = tool_schema_hash(entry)
    with _LOCK:
        _TOOLS[upstream, tool] = digest
    return digest


def forget(upstream: str | None = None) -> None:
    """Drop observations. Whole-register with no argument, which tests and `verify` use."""
    with _LOCK:
        if upstream is None:
            _CERTS.clear()
            _TOOLS.clear()
            return
        _CERTS.pop(upstream, None)
        for key in [key for key in _TOOLS if key[0] == upstream]:
            del _TOOLS[key]


def check(pin: UpstreamPin, upstream: str, tool: str | None = None) -> str | None:
    """§4.3's check 2: the reason this action is refused, or `None` where the pin is satisfied.

    **An empty pin is satisfied by anything**, which is every action entry written before v0.10
    and why they all upgrade untouched.

    **A pin with nothing observed is `upstream_unverified`, never admitted.** That is the row the
    whole section turns on: an upstream that is never observed would otherwise switch the pin off
    by being absent, and §4.5's fail-closed half exists to stop exactly that.
    """
    if not pin:
        return None
    with _LOCK:
        seen_cert = _CERTS.get(upstream)
        seen_tool = _TOOLS.get((upstream, tool)) if tool is not None else None
    if pin.cert_sha256:
        if seen_cert is None:
            return UPSTREAM_UNVERIFIED
        if seen_cert not in pin.cert_sha256:
            return UPSTREAM_MISMATCH
    if pin.tool_schema_sha256 is not None:
        if tool is None or seen_tool is None:
            return UPSTREAM_UNVERIFIED
        if seen_tool != pin.tool_schema_sha256:
            return UPSTREAM_MISMATCH
    return None


def pinned_context(certs: tuple[str, ...]) -> ssl.SSLContext:
    """An `ssl.SSLContext` whose only trust anchors are the pinned certificates (§4.3, check 3).

    **The check that prevents.** Checks 1 and 2 compare an observation, which is a decision about
    the past; this one refuses the handshake, so a swapped server never receives a request byte
    and `v0.7 §2.3`'s `NotExecuted` claim is true of it without anything new.

    `VERIFY_X509_PARTIAL_CHAIN` is what makes a **leaf** a valid anchor. A real upstream's leaf is
    signed by a CA, so loading it into the trust store is not enough on its own: OpenSSL wants the
    chain to terminate at a self-signed certificate unless told that a trusted non-root may end
    it. Without the flag a pinned CA-signed leaf refuses every connection, including the right
    one.

    **A TLS floor, set explicitly.** `PROTOCOL_TLS_CLIENT` still admits TLS 1.0 and 1.1, and
    CodeQL flags that high on a test file of this feature's own. A context built for a pinning
    check that then negotiates a protocol the rest of the product would not is the wrong shape to
    ship from a security library, so the floor is here rather than inherited.
    """
    import ssl

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    for path in certs:
        context.load_verify_locations(cafile=path)
    return context


__all__ = [
    "cert_hash",
    "check",
    "forget",
    "observe_certificate",
    "observe_tool_schema",
    "pinned_context",
    "tool_schema_hash",
]
