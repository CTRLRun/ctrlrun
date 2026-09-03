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
    from .server import Gateway, GatewayConfig, httpx_forwarder, serve_forever

    public_url = options.pop("public_url", None)
    webhook_url = options.pop("webhook_url", None)
    secret_file = options.pop("webhook_secret_file", None)
    allow_insecure = bool(options.pop("allow_insecure_webhook", False))
    secret = webhook_secret(secret_file) if secret_file else webhook_secret()

    config = GatewayConfig(upstream=upstream, alias=alias, webhook_secret=secret, **options)
    control = Control.from_file()
    if webhook_url:
        # §7 — the provider notifies; the gateway's endpoint receives. Both need the same
        # secret, and neither takes it from a command-line argument (§7.3).
        control = Control(
            control.policy,
            control.store,
            WebhookApprovalProvider(
                control.store,
                url=webhook_url,
                secret=secret,
                public_url=public_url,
                allow_insecure=allow_insecure,
            ),
            sinks=[],
        )
    forwarder = httpx_forwarder(config)
    try:
        serve_forever(Gateway(config, control, forwarder))
    finally:
        forwarder.close()
        control.store.close()
