# SPDX-FileCopyrightText: 2026 The ctrlrun contributors
# SPDX-License-Identifier: Apache-2.0
"""SPEC-v0.10 §4, item 3: upstream identity pinning.

The honest slice of `ASI04` and nothing more: this decides actions, and it never inspects a
package, a model, a registry or a build. What it adds is that an action entry may say **which
server** it authorises itself against.

Three checks, one rule (§4.3). These tests cover check 2, the one that produces a `DENY`, and
check 3's mechanism against a real TLS listener. Check 1 is the gateway's startup refusal.
"""

from __future__ import annotations

import contextlib
import http.server
import socket
import ssl
import subprocess
import threading
from pathlib import Path

import pytest

from ctrlrun.action import Action, Principal
from ctrlrun.control import Control
from ctrlrun.errors import ActionDenied, InvalidArgument, PolicyError
from ctrlrun.policy import UPSTREAM_MISMATCH, UPSTREAM_UNVERIFIED, Policy
from ctrlrun.state import SQLiteStateStore
from ctrlrun.upstream import (
    cert_hash,
    check,
    forget,
    observe_certificate,
    observe_tool_schema,
    tool_schema_hash,
)

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64

DOC = """
schema: ctrlrun.policy/v8
actions:
  stripe.refund:
    decision: allow
    upstream:
      tls_cert_sha256: {pins}
"""


def _policy(pins: str = f'["{DIGEST_A}"]') -> Policy:
    return Policy.from_yaml(DOC.format(pins=pins), source="test_upstream")


def _action() -> Action:
    return Action(
        name="stripe.refund",
        resource=None,
        arguments={"amount": 10},
        principal=Principal(agent="worker"),
        environment="production",
    )


@pytest.fixture(autouse=True)
def _clean_register():
    forget()
    yield
    forget()


# --- T489, T490, T492, T493: check 2 -------------------------------------------------------


def test_T489_the_pinned_upstream_admits_the_action(tmp_path):
    """The negative control for every row below. Without it a kernel that refused every pinned
    action whatever would pass them all, which is `v0.4 §2.2`'s guarantee that could not fail."""
    store = SQLiteStateStore(str(tmp_path / "s.db"))
    observe_certificate("mcp.example", b"the-pinned-cert")
    pinned = _policy(f'["{cert_hash(b"the-pinned-cert")}"]')
    control = Control(pinned, store, upstream="mcp.example")

    receipt = control.execute(_action(), lambda: "ok")

    assert receipt.result.value == "committed"


def test_T490_a_swapped_server_behind_the_same_name_is_refused(tmp_path):
    """§4.3's check 2, and the sharp case of §4.1: everything else still matches. The grant
    matches, the constraints hold, the receipt would still say `stripe.refund`."""
    store = SQLiteStateStore(str(tmp_path / "s.db"))
    pinned = _policy(f'["{cert_hash(b"the-pinned-cert")}"]')
    control = Control(pinned, store, upstream="mcp.example")
    observe_certificate("mcp.example", b"a-different-cert")
    ran = []

    with pytest.raises(ActionDenied) as refused:
        control.execute(_action(), lambda: ran.append(1))

    assert refused.value.reason == UPSTREAM_MISMATCH
    assert ran == [], "the upstream was called for an action pinned to another server"


def test_T493_a_pin_with_nothing_observed_is_refused_and_never_admitted(tmp_path):
    """**The fail-closed half, and the one to get right** (§4.5). A pin that does nothing when
    nothing was observed is a pin an upstream can switch off by never being seen."""
    store = SQLiteStateStore(str(tmp_path / "s.db"))
    control = Control(_policy(), store, upstream="mcp.example")
    ran = []

    with pytest.raises(ActionDenied) as refused:
        control.execute(_action(), lambda: ran.append(1))

    assert refused.value.reason == UPSTREAM_UNVERIFIED
    assert ran == []


def test_T492_rotation_admits_either_certificate_and_refuses_a_third(tmp_path):
    """§4.2. The key is a **list** so an operator can carry the current and the next certificate
    across a rotation; a single-valued pin makes every renewal an outage, which is how a pin gets
    switched off permanently."""
    store = SQLiteStateStore(str(tmp_path / "s.db"))
    current, following = cert_hash(b"current"), cert_hash(b"next")
    control = Control(_policy(f'["{current}", "{following}"]'), store, upstream="mcp.example")

    for blob in (b"current", b"next"):
        observe_certificate("mcp.example", blob)
        assert control.execute(_action(), lambda: "ok").result.value == "committed"

    observe_certificate("mcp.example", b"a-third")
    with pytest.raises(ActionDenied) as refused:
        control.execute(_action(), lambda: "ok")
    assert refused.value.reason == UPSTREAM_MISMATCH


def test_T489b_an_entry_that_pins_nothing_is_unchanged(tmp_path):
    """Opt in, then fail closed. Every action entry written before v0.10 pins nothing, and an
    empty pin is satisfied by anything, which is why they all upgrade untouched."""
    store = SQLiteStateStore(str(tmp_path / "s.db"))
    plain = Policy.from_yaml(
        "schema: ctrlrun.policy/v8\nactions:\n  stripe.refund:\n    decision: allow\n",
        source="t",
    )
    control = Control(plain, store, upstream="mcp.example")

    assert control.execute(_action(), lambda: "ok").result.value == "committed"


# --- T491: check 3, against a real listener ------------------------------------------------


def _ca_signed(tmp: Path, name: str) -> tuple[Path, Path]:
    """A tiny CA and one leaf it signs, so the leaf is NOT self-signed: the realistic shape, and
    the one that needs `VERIFY_X509_PARTIAL_CHAIN` to be a trust anchor at all."""
    ca_key, ca_crt = tmp / f"{name}-ca.key", tmp / f"{name}-ca.crt"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(ca_key),
            "-out",
            str(ca_crt),
            "-days",
            "1",
            "-subj",
            f"/CN={name}-ca",
        ],
        check=True,
        capture_output=True,
    )
    key, csr, crt = tmp / f"{name}.key", tmp / f"{name}.csr", tmp / f"{name}.crt"
    subprocess.run(
        [
            "openssl",
            "req",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(csr),
            "-subj",
            "/CN=localhost",
        ],
        check=True,
        capture_output=True,
    )
    ext = tmp / f"{name}.ext"
    ext.write_text("subjectAltName=DNS:localhost\n")
    subprocess.run(
        [
            "openssl",
            "x509",
            "-req",
            "-in",
            str(csr),
            "-CA",
            str(ca_crt),
            "-CAkey",
            str(ca_key),
            "-CAcreateserial",
            "-out",
            str(crt),
            "-days",
            "1",
            "-extfile",
            str(ext),
        ],
        check=True,
        capture_output=True,
    )
    return key, crt


def _serve(key: Path, crt: Path) -> int:
    """A TLS listener on a port the OS picks **and we never let go of**.

    Binding an ephemeral port, closing it, and rebinding is a race: another process can take the
    port in the gap, and this file stands up two servers so it runs the gap twice. One flaky gate
    run is what found it. `HTTPServer` binds for us and `server_port` reports what it got, so
    there is no gap to lose.
    """

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    # `PROTOCOL_TLS_SERVER` still admits TLS 1.0 and 1.1, which CodeQL flags high and is right
    # to: a listener in a test for a *pinning* feature that negotiates a protocol the product
    # would refuse is testing something the product does not do.
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(crt), str(key))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return int(server.server_port)


@pytest.mark.serial
def test_T491_the_pinned_certificate_is_the_connections_only_trust_anchor(tmp_path):
    """§4.3's check 3, the one that **prevents** rather than attributing.

    A digest cannot be a trust anchor, which is why §4.2 carries `tls_cert_file` beside
    `tls_cert_sha256`: `load_verify_locations` takes PEM. A CA-signed leaf becomes a valid anchor
    with `VERIFY_X509_PARTIAL_CHAIN`, and a swapped server then fails the handshake **before any
    request byte**, which is what makes `SPEC-v0.7 §2.3`'s `NotExecuted` claim true of it.
    """
    good_key, good_crt = _ca_signed(tmp_path, "good")
    evil_key, evil_crt = _ca_signed(tmp_path, "evil")
    good_port, evil_port = _serve(good_key, good_crt), _serve(evil_key, evil_crt)

    def context() -> ssl.SSLContext:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_verify_locations(cadata=good_crt.read_text())
        ctx.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
        return ctx

    def reach(port: int) -> str:
        try:
            with (
                socket.create_connection(("127.0.0.1", port), timeout=5) as raw,
                context().wrap_socket(raw, server_hostname="localhost") as tls,
            ):
                tls.send(b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n")
                tls.recv(16)
            return "handshake ok"
        except ssl.SSLCertVerificationError:
            return "refused"

    assert reach(good_port) == "handshake ok", "the pinned server was refused"
    assert reach(evil_port) == "refused", "a swapped server completed the handshake"


# --- T494, T495, T496, T497: the key, the gate, the surfaces -------------------------------


def test_T494_the_tool_schema_hash_covers_the_whole_advertised_entry():
    """§4.2. Name, description and input schema together, because a description that changed is
    a tool whose behaviour an operator has not reviewed."""
    entry = {"name": "refund", "description": "issue a refund", "inputSchema": {"type": "object"}}
    moved = {**entry, "description": "issue a refund, or a payout"}

    assert tool_schema_hash(entry) == tool_schema_hash(dict(reversed(list(entry.items()))))
    assert tool_schema_hash(entry) != tool_schema_hash(moved)


def test_T494b_a_tool_whose_schema_moved_under_an_approved_name_is_refused(tmp_path):
    """The second half of §4.1's sharp case: the server did not change, the tool's schema did."""
    entry = {"name": "refund", "inputSchema": {"type": "object"}}
    pinned = Policy.from_yaml(
        "schema: ctrlrun.policy/v8\nactions:\n  stripe.refund:\n    decision: allow\n"
        f'    upstream: {{ tool_schema_sha256: "{tool_schema_hash(entry)}" }}\n',
        source="t",
    )
    store = SQLiteStateStore(str(tmp_path / "s.db"))
    control = Control(pinned, store, upstream="mcp.example")

    observe_tool_schema("mcp.example", "stripe.refund", entry)
    assert control.execute(_action(), lambda: "ok").result.value == "committed"

    observe_tool_schema("mcp.example", "stripe.refund", {**entry, "inputSchema": {"type": "array"}})
    with pytest.raises(ActionDenied) as refused:
        control.execute(_action(), lambda: "ok")
    assert refused.value.reason == UPSTREAM_MISMATCH


def test_T496_upstream_in_a_v7_document_is_a_load_error():
    """§4.6. An older reader that ignored the key would authorise the action against any server
    at all, which is the whole of what the key restricts."""
    with pytest.raises(PolicyError) as refused:
        Policy.from_yaml(
            "schema: ctrlrun.policy/v7\nactions:\n  stripe.refund:\n    decision: allow\n"
            f'    upstream: {{ tls_cert_sha256: ["{DIGEST_A}"] }}\n',
            source="t",
        )

    assert "ctrlrun.policy/v8" in str(refused.value)
    assert "upstream" in str(refused.value)


def test_T497_the_acs_hook_refuses_a_pin_at_construction(tmp_path):
    """§4.4. ACS is advisory: the platform runs the tool and this hook holds no connection, so
    there is nothing to observe and nothing to pin.

    **At construction and not at load**, which §4.4 argues at length: one loader cannot know
    which surface will run an action, and a load error would stop `verify` and `scan` reading a
    document that pins, which §7.3's exit criterion requires them to do.
    """
    pytest.importorskip("httpx")
    from ctrlrun.acs import AcsControlHook

    store = SQLiteStateStore(str(tmp_path / "s.db"))
    control = Control(_policy(), store)

    with pytest.raises(InvalidArgument) as refused:
        AcsControlHook(control)

    assert "upstream" in str(refused.value)
    assert "ctrlrun gateway" in str(refused.value)


def test_T497b_a_pinned_document_still_loads_in_process(tmp_path):
    """The other half of §4.4, and the reason the refusal is not a load error: `verify` and
    `scan` load through the in-process path, so a document that pins must **read**. What refuses
    is the action, at decision time, under `upstream_unverified`."""
    store = SQLiteStateStore(str(tmp_path / "s.db"))
    control = Control(_policy(), store)  # no upstream: in-process

    assert control.policy.upstream_pin("stripe.refund").cert_sha256 == (DIGEST_A,)
    with pytest.raises(ActionDenied) as refused:
        control.execute(_action(), lambda: "ok")
    assert refused.value.reason == UPSTREAM_UNVERIFIED


def test_the_check_is_a_pure_function_over_two_strings():
    """Which is what lets `verify` grade G27 with no TLS listener and no certificate (§7)."""
    from ctrlrun.policy import UpstreamPin

    assert check(UpstreamPin(), "anything") is None
    assert check(UpstreamPin(cert_sha256=(DIGEST_A,)), "u") == UPSTREAM_UNVERIFIED
    observe_certificate("u", b"x")
    assert check(UpstreamPin(cert_sha256=(cert_hash(b"x"),)), "u") is None
    assert check(UpstreamPin(cert_sha256=(DIGEST_B,)), "u") == UPSTREAM_MISMATCH


def test_T491b_the_gateways_forwarder_pins_when_the_policy_does(tmp_path):
    """§4.3's check 3, wired: the forwarder's verification context is built from the certificates
    the policy pins, and is httpx's ordinary verification where it pins none."""
    pytest.importorskip("httpx")
    from ctrlrun.gateway.server import GatewayConfig, httpx_forwarder

    _, crt = _ca_signed(tmp_path, "pinned")
    config = GatewayConfig(upstream="https://mcp.example", alias="x", principal="worker")

    plain = httpx_forwarder(config, _policy())
    assert plain.verify is None, (
        "a pin by digest alone contributes no trust anchor; §4.2 states that as a limit"
    )

    with_file = Policy.from_yaml(
        "schema: ctrlrun.policy/v8\nactions:\n  stripe.refund:\n    decision: allow\n"
        f'    upstream: {{ tls_cert_file: "{crt}" }}\n',
        source="t",
    )
    pinning = httpx_forwarder(config, with_file)
    assert pinning.verify is not None
    assert pinning.verify.minimum_version is ssl.TLSVersion.TLSv1_2, (
        "a context built for a pinning check must not negotiate a protocol the product would "
        "refuse; CodeQL flagged exactly this, high, on this file's own listener"
    )
    assert pinning.verify.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN, (
        "without PARTIAL_CHAIN a pinned CA-signed leaf refuses every connection, the right one "
        "included"
    )


# --- T495: check 1, and the observation check 2 reads --------------------------------------


@pytest.mark.serial
def test_T495_the_startup_probe_records_what_check_2_then_compares(tmp_path):
    """**§4.3's check 1, and the finding that made it necessary.**

    A spec review round three established that nothing in the shipped product ever called
    `observe_certificate`: the register `check` reads was written only by tests and by `verify`'s
    own G27 scenario. So `check` answered `upstream_unverified` in every real process, a gateway
    that pinned refused every pinned action for ever, and §10's `upstream_mismatch` row described
    an outcome no shipped code path could produce.

    This is the fix, end to end against a real listener: the startup probe observes, and check 2
    then has something to compare.
    """
    from ctrlrun.upstream import check, observe_upstream

    key, crt = _ca_signed(tmp_path, "startup")
    port = _serve(key, crt)
    url = f"https://localhost:{port}"

    forget()
    from ctrlrun.policy import UpstreamPin

    assert check(UpstreamPin(cert_sha256=(DIGEST_A,)), url) == UPSTREAM_UNVERIFIED, (
        "before anything observes, a pin is unverified: the fail-closed half of §4.5"
    )

    observed = observe_upstream(url, verify=_pinned_context(crt))

    assert check(UpstreamPin(cert_sha256=(observed,)), url) is None, (
        "after the probe, check 2 compares against a real observation"
    )
    assert check(UpstreamPin(cert_sha256=(DIGEST_A,)), url) == UPSTREAM_MISMATCH, (
        "and a server whose certificate is in no pinned list is a mismatch, which is the §10 row "
        "that had no reachable code path"
    )


def _pinned_context(crt: Path):
    import ssl

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    context.load_verify_locations(cafile=str(crt))
    return context


@pytest.mark.serial
def test_T495b_a_swapped_server_stops_the_gateway_before_it_listens(tmp_path):
    """§4.3's check 1 is the **legible** one, and the only one where the operator is present: a
    pin an operator got wrong fails on a console rather than on production traffic."""
    from ctrlrun.errors import InvalidArgument
    from ctrlrun.upstream import observe_upstream

    _, good = _ca_signed(tmp_path, "good2")
    evil_key, evil_crt = _ca_signed(tmp_path, "evil2")
    port = _serve(evil_key, evil_crt)

    forget()
    with pytest.raises(Exception) as refused:
        observe_upstream(f"https://localhost:{port}", verify=_pinned_context(good))

    assert "CERTIFICATE_VERIFY" in str(refused.value) or isinstance(
        refused.value, (ssl.SSLError, OSError, InvalidArgument)
    ), refused.value


def test_T495c_an_http_upstream_presents_nothing_to_pin():
    """A pin is a claim about a server's identity and an unencrypted hop carries none, so it is
    refused rather than silently recorded as verified."""
    from ctrlrun.errors import InvalidArgument
    from ctrlrun.upstream import observe_upstream

    with pytest.raises(InvalidArgument) as refused:
        observe_upstream("http://mcp.example")

    assert "not https" in str(refused.value)


def test_T494c_the_two_pin_halves_must_agree_at_load(tmp_path):
    """**§4.2's correspondence check, whose absence a review found contradicting its own
    docstring.**

    §4.2 measured what a half-moved rotation costs and then the check was not written. A document
    whose `tls_cert_file` still held only the old certificate while `tls_cert_sha256` had both
    loaded cleanly and failed at the **handshake** — the outage §4.2 says the list prevents,
    arriving one layer down, on the day an operator believed they had prepared for.
    """
    _, crt = _ca_signed(tmp_path, "corr")
    real = cert_hash(crt.read_bytes() and ssl.PEM_cert_to_DER_cert(crt.read_text()))

    agreeing = Policy.from_yaml(
        "schema: ctrlrun.policy/v8\nactions:\n  stripe.refund:\n    decision: allow\n"
        f'    upstream: {{ tls_cert_sha256: ["{real}"], tls_cert_file: "{crt}" }}\n',
        source="t",
    )
    assert agreeing.actions["stripe.refund"].upstream.certs == (str(crt),)

    with pytest.raises(PolicyError) as disagreeing:
        Policy.from_yaml(
            "schema: ctrlrun.policy/v8\nactions:\n  stripe.refund:\n    decision: allow\n"
            f'    upstream: {{ tls_cert_sha256: ["{DIGEST_A}"], tls_cert_file: "{crt}" }}\n',
            source="t",
        )
    assert "does not name" in str(disagreeing.value)

    with pytest.raises(PolicyError) as missing:
        Policy.from_yaml(
            "schema: ctrlrun.policy/v8\nactions:\n  stripe.refund:\n    decision: allow\n"
            f'    upstream: {{ tls_cert_file: "{tmp_path}/nope.pem" }}\n',
            source="t",
        )
    assert "could not be read" in str(missing.value), (
        "a pin whose certificate is missing builds an empty trust store and refuses everything"
    )


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_T495d_the_probes_own_default_context_will_not_negotiate_below_tls_1_2(tmp_path):
    """`observe_upstream` called with no `verify` builds its own context, and **it states the
    floor rather than inheriting it**.

    The first version of this test asserted the floor on the context as built and passed with the
    line under test deleted, because `create_default_context` already sets TLS 1.2 on this
    interpreter. That is mutation pattern 3 exactly: the environment already prevented what the
    test forbade. So the double here hands back a context whose floor has been **lowered**, the
    way a system-wide OpenSSL configuration or a future default could, and the assertion is that
    the function raised it again. Deleting the line turns this red.

    It is structural rather than a handshake against a TLS 1.1 listener for a second reason from
    the same list: such a listener needs a certificate the default context does not trust, so it
    would be refused for the certificate and the version would never be reached.
    """
    import ssl as ssl_module

    from ctrlrun import upstream as pinning

    key, crt = _ca_signed(tmp_path, "localhost")
    port = _serve(key, crt)
    built: list[ssl_module.SSLContext] = []
    real = ssl_module.create_default_context

    def capture(*args: object, **kwargs: object) -> ssl_module.SSLContext:
        context = real(*args, **kwargs)  # type: ignore[arg-type]
        context.minimum_version = ssl_module.TLSVersion.TLSv1  # a permissive system default
        built.append(context)
        return context

    ssl_module.create_default_context = capture  # type: ignore[assignment]
    try:
        # Self-signed against a CA this context does not trust, so the handshake is refused; what
        # is under test is the context built to attempt it, not the attempt's outcome.
        with contextlib.suppress(Exception):
            pinning.observe_upstream(f"https://localhost:{port}")
    finally:
        ssl_module.create_default_context = real  # type: ignore[assignment]

    assert built, "observe_upstream built no default context, so nothing here was exercised"
    assert built[-1].minimum_version is ssl_module.TLSVersion.TLSv1_2, (
        "the probe's own context must not negotiate a protocol the product would refuse, and "
        "must not depend on the default to say so; SPEC-v0.10 §4.3's check 3 decides whether "
        "the gateway starts on this handshake"
    )
