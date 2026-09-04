"""The shape every third-party adapter shares. SPEC-v0.4 §7.3.

Four frameworks, four files, and one thing in common: none of them is installed by `ctrlrun`
or by any of its extras, so each is skipped **by name** where its distribution is absent, and
the skip reaches the results file rather than being a silent absence (T123).

Each adapter builds its framework's own agent with its own defaults, gives it the scenario
text of `scenarios.py` byte for byte, and exposes one tool that posts to the fake remote. The
tool body is shared here so that the only thing differing between adapters is the framework's
own wiring — which is the whole subject of the table.

**These four have not been run in this repository's CI**, and cannot be: they need the
framework installed and a model to drive it. That is why item 6 ships the harness and no
results (§7.4), and why `README.md` records the version each adapter was written against.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

REQUEST_TIMEOUT = 30.0

#: What a lost response surfaces as. An adapter must let its framework see these rather than
#: swallowing them: whether the framework retries is the measurement.
LOST = (urllib.error.URLError, OSError, TimeoutError)


def call_remote(url: str, arguments: dict[str, Any]) -> str:
    """The tool body every adapter exposes to its framework, unchanged between them.

    It does not catch anything. A framework's retry policy only shows if the framework sees
    the failure, and an adapter that handled it would be measuring the adapter.
    """
    request = urllib.request.Request(
        url,
        data=json.dumps(arguments).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        return response.read().decode("utf-8")


def record_approval(url: str, approved: dict[str, Any]) -> None:
    call_remote(url, approved)
