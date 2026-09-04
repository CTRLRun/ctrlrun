"""Two stub "frameworks", and they are not decoration. SPEC-v0.4 §8, T122.

One retries a lost response and one does not. Both are needed: a harness that reported
`executed_twice` unconditionally would satisfy a test that only had the retrying stub, and
would say the same thing about every real framework it ever ran.

They use `urllib` from the standard library, so the harness's own end-to-end path needs
nothing installed.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.client import IncompleteRead, RemoteDisconnected

from ..scenarios import APPROVAL_MUTATION, Scenario
from .base import Attempt, approve_endpoint, tool_endpoint

#: Bounded, like every loop in this repository: a stub that retried forever would hang CI
#: rather than fail it.
MAX_ATTEMPTS = 2
REQUEST_TIMEOUT = 2.0

#: What a dropped connection and a withheld response surface as. Named once, so "the response
#: was lost" means the same thing in both stubs.
LOST = (urllib.error.URLError, RemoteDisconnected, IncompleteRead, TimeoutError, OSError)


def _call(url: str, payload: dict[str, object]) -> None:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        response.read()


@dataclass
class _Stub:
    name: str
    retries: bool
    distribution: str = "ctrlrun"
    config_deviation: str | None = None

    def available(self) -> bool:
        return True

    def run(self, scenario: Scenario, url: str) -> Attempt:
        if scenario.name == APPROVAL_MUTATION:
            # The human's decision reaches the remote first, then the agent proposes the
            # mutated action. Neither stub has any notion of an approval binding, which is
            # the point: nothing here stops the mutation.
            assert scenario.approved is not None
            _call(approve_endpoint(url), scenario.approved)

        attempts = MAX_ATTEMPTS if self.retries else 1
        last: str | None = None
        for _ in range(attempts):
            try:
                _call(tool_endpoint(url), dict(scenario.arguments))
                return Attempt()
            except LOST as lost:
                # The response was lost. A framework that retries here retries here.
                last = f"{type(lost).__name__}: {lost}"
        return Attempt(error=last, notes="the response was lost on every attempt")


RETRYING = _Stub(name="stub-retrying", retries=True)
NOT_RETRYING = _Stub(name="stub-not-retrying", retries=False)
