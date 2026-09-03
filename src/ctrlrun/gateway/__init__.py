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
from typing import Final, NoReturn

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


def serve(*, upstream: str, alias: str, **options: object) -> NoReturn:
    """Run a gateway in front of one upstream MCP server (SPEC-v0.2 §6.1).

    The dependency check happens first and unconditionally, so an operator who has not
    installed the extra learns it from one clear line rather than from a stack trace.
    """
    http_client()
    raise NotImplementedError(
        "the gateway's request handling arrives with build-list items 6b-6d; "
        "this release ships the StateStore half it stands on (SPEC-v0.2 §6.9.4, §6.10)"
    )
