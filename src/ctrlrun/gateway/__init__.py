"""The MCP gateway. Build-list item 6; SPEC-v0.2 §6. Ships in `ctrlrun[gateway]`.

Nothing here is imported by `import ctrlrun` (SPEC-v0.2 §1.1): the package is reachable only
by naming it, and the HTTP client it needs is resolved at call time, so a core install that
never runs a gateway never pays for one — and never fails at import to say so.

The listening side is stdlib `http.server` (§6.11). The extra's only dependency is an HTTP
client with a connect/write/read exception taxonomy precise enough to feed §6.8's first row:
"the connection was never established" is the one claim in that table that asserts
non-execution, and it is only provable if no request byte can have been written.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType
from typing import Any, Final

from ..errors import MissingDependency

#: The extra's HTTP client, imported by name so a missing one is a `MissingDependency`
#: rather than a `ModuleNotFoundError` from halfway down an import chain.
_HTTP_CLIENT: Final = "httpx"

#: The extra that carries it, for the install command in the error.
_EXTRA: Final = "gateway"

__all__ = ["serve"]


def http_client() -> ModuleType:
    """The HTTP client the gateway forwards with, or `MissingDependency` (SPEC-v0.2 §1.1)."""
    try:
        return importlib.import_module(_HTTP_CLIENT)
    except ImportError as exc:
        raise MissingDependency(_HTTP_CLIENT, _EXTRA) from exc


def serve(*, upstream: str, alias: str, **options: Any) -> None:
    """Run a gateway in front of one upstream MCP server (SPEC-v0.2 §6.1).

    The dependency check happens first and unconditionally, so an operator who has not
    installed the extra learns it from one clear line rather than from a stack trace.

    Blocks until interrupted. `Control.from_file()` finds the policy and the store, so a
    gateway and a decorator-based worker sharing a policy share reservations (§6.1).
    """
    http_client()
    from ..control import Control
    from ..webhook import WebhookApprovalProvider, webhook_secret
    from .server import (
        Gateway,
        GatewayConfig,
        httpx_forwarder,
        serve_forever,
    )

    otel = bool(options.pop("otel", False))
    otel_arguments = bool(options.pop("otel_arguments", False))
    public_url = options.pop("public_url", None)
    webhook_url = options.pop("webhook_url", None)
    secret_file = options.pop("webhook_secret_file", None)
    allow_insecure = bool(options.pop("allow_insecure_webhook", False))
    secret = webhook_secret(secret_file) if secret_file else webhook_secret()
    authority_path = options.pop("authority", None)

    # SPEC-v0.3 §2.5 — `--environment` is rank 1 on the Control, not a second value the
    # gateway stamps for itself. Where it is absent the Control resolves ranks 2 to 4, so
    # $CTRLRUN_ENVIRONMENT still means what it says.
    environment = options.pop("environment", None)
    config = GatewayConfig(upstream=upstream, alias=alias, webhook_secret=secret, **options)
    control = Control.from_file(environment=environment)
    authority = _authority(control, authority_path)
    sinks = list(control.sinks)
    approvals = control.approvals
    if otel:
        # §8 — one more sink beside the JSONL one `from_file` installed. It never blocks and
        # never raises into the kernel (§4.2), so adding it changes no decision.
        from ..otel import OTelEventSink

        sinks.append(OTelEventSink(arguments=otel_arguments))
    if webhook_url:
        # §7 — the provider notifies; the gateway's endpoint receives. Both need the same
        # secret, and neither takes it from a command-line argument (§7.3).
        approvals = WebhookApprovalProvider(
            control.store,
            url=webhook_url,
            secret=secret,
            public_url=public_url,
            allow_insecure=allow_insecure,
        )
    # SPEC-v0.3 §8.3 — one rebuild, carrying **everything**. Two successive rebuilds each
    # naming a subset is how `authority` and the JSONL sink went missing from a gateway
    # started with `--otel` and a webhook: each was correct about the field it was changing
    # and silent about the ones it dropped.
    control = Control(
        control.policy,
        control.store,
        approvals,
        sinks=sinks,
        authority=authority,
        environment=control.environment,
    )
    forwarder = httpx_forwarder(config)
    gateway = Gateway(config, control, forwarder)
    _announce(control, config, gateway.identity, authority_path)
    try:
        serve_forever(gateway)
    finally:
        forwarder.close()
        control.store.close()


def _authority(control: Any, path: str | None) -> Any:
    """The grants this gateway enforces, from the policy file or from `--authority` (§8.3).

    `--authority` exists because the person who writes grants and the person who writes
    per-action autonomy are often not the same person, and a gateway is where that separation
    shows up first. The document it names is **not** a policy document: its top-level key set
    is closed at `schema` and `authority`, so `actions:` or `mode:` in it is a load error.

    Where both the policy file and `--authority` declare a section, the gateway refuses to
    start naming both paths. Two sources for one section is the ambiguity `v0.1 §5.1` refuses
    for `{resource}`: pick one, or say which. Silently preferring either would mean an
    operator who edited the wrong file saw no effect and no error.
    """
    from ..authority import Authority
    from ..errors import InvalidArgument

    if path is None:
        return control.authority
    loaded = Authority.from_yaml(
        Path(path).read_text(encoding="utf-8"), source=path, standalone=True
    )
    if control.authority is not None:
        raise InvalidArgument(
            f"authority is declared twice: in the policy at {control.policy.source} and in "
            f"--authority {path}. Two sources for one section is an ambiguity nobody can "
            "resolve later; remove the 'authority:' key from one of them (SPEC-v0.3 §8.3)"
        )
    return loaded


def _announce(control: Any, config: Any, identity: Any, authority_path: str | None) -> None:
    """The startup block of SPEC-v0.3 §8.4, printed before the socket opens.

    Everything here is something an operator can get wrong in a way that is invisible until an
    incident: an action with no effect key, an environment they did not choose, a header they
    trust more than they meant to, an `authority:` section that is not loaded at all. It goes
    on the line that starts the process rather than into a receipt three weeks later.

    The observe banner is **not** printed here: `ctrlrun gateway` prints it before anything
    else, along with every other command that loads the operator's policy (§6.5), and a second
    copy would read as two switches.

    `print` rather than the logger, because this is the CLI's own output and a logger with no
    configured handler would swallow it — which is the failure mode the block exists to
    prevent, in miniature.
    """
    lines: list[str] = []
    lines.append(f"environment  {control.environment}")
    lines.append(f"identity     {type(identity).__name__}")
    if config.principal_header is not None:
        lines.append(
            f"             trusts the header {config.principal_header!r}: it is worth what "
            "the proxy that sets it is worth, and must be overwritten on every request"
        )
    if control.authority is None:
        lines.append("no authority: section — every principal is unrestricted")
    else:
        where = authority_path or control.policy.source
        lines.append(f"authority    {len(control.authority.grants)} grant(s) from {where}")
    keyless = sorted(
        name for name in control.policy.actions if control.policy.effect_template(name) is None
    )
    if keyless:
        lines.append(f"{len(keyless)} action(s) have no effect: template and get no reservation:")
        lines += [f"  {name}" for name in keyless]
        lines.append("That is right for a read, and wrong for anything that changes the world.")
    for line in lines:
        print(line)
