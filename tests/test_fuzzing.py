"""The fuzz targets under `fuzz/`, and the invariants they assert.

`SPEC-v0.1.md` §2.3 calls canonicalization security-critical, and it is the one place where a
defect is silent: two distinct actions sharing a canonical form share an approval, and nothing in a
receipt would look wrong. `Policy.from_yaml`'s docstring makes the other promise -- "anything
malformed raises `PolicyError`" -- which is the fail-closed rule in a single sentence, and a
loader that raised `KeyError` instead would still deny, but through a crash nobody classified.

Both are properties rather than examples, so both are fuzzed. The invariants live in
`fuzz/properties.py` and **carry no dependency on Atheris**: the fuzzer is one driver for them
and this suite is another, so they are exercised on every commit whether or not anybody has a
fuzzing toolchain installed. `fuzz/fuzz_*.py` are the Atheris entry points.

A property that cannot fail proves nothing, so every invariant here has a positive control
that breaks it deliberately and requires the check to notice.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FUZZ = REPO_ROOT / "fuzz"

# Same narrowness as `test_soak.py`: `fuzz/` is not in a distribution, and the packaging test
# is what asserts it should not be. In a checkout this module runs; in an sdist it skips and
# `test_the_fuzz_corpus_is_not_packaged` is what proves the absence was deliberate.
if not FUZZ.is_dir():  # pragma: no cover - running from a distribution
    pytest.skip("fuzz/ is not in this distribution", allow_module_level=True)
if str(FUZZ) not in sys.path:
    sys.path.insert(0, str(FUZZ))

import properties  # noqa: E402

# --- the decoders are total ------------------------------------------------------------------


@pytest.mark.parametrize("size", [0, 1, 2, 7, 33, 200, 1024])
def test_the_document_decoder_accepts_any_bytes(size):
    """A fuzzer hands the target arbitrary bytes. A decoder that raised on some of them would
    spend the campaign reporting its own crashes instead of the library's."""
    for seed in range(16):
        data = bytes((seed * 31 + i * 17) % 256 for i in range(size))
        properties.document_from_bytes(data)


@pytest.mark.parametrize("size", [0, 1, 5, 64, 512])
def test_the_policy_text_decoder_accepts_any_bytes(size):
    for seed in range(16):
        data = bytes((seed * 13 + i * 7) % 256 for i in range(size))
        assert isinstance(properties.policy_text_from_bytes(data), str)


# --- the invariants hold ----------------------------------------------------------------------


def _corpus(count: int = 400) -> list[bytes]:
    return [
        bytes((n * 2654435761 >> (8 * i)) % 256 for i in range(n % 48 + 1)) for n in range(count)
    ]


def test_canonicalization_holds_over_the_corpus():
    """The campaign this suite can afford to run on every commit."""
    for data in _corpus():
        properties.check_canonical(properties.document_from_bytes(data))


def test_the_policy_loader_holds_over_the_corpus():
    for data in _corpus():
        properties.check_policy(properties.policy_text_from_bytes(data))


def test_the_seed_corpus_on_disk_passes_both_checks():
    """The seeds are what a fresh campaign starts from. One that already fails is a finding
    nobody filed, and one that cannot be decoded is a file the fuzzer will ignore."""
    seeds = sorted((FUZZ / "corpus").rglob("*"))
    assert [p for p in seeds if p.is_file()], "the seed corpus is empty"
    for path in seeds:
        if not path.is_file():
            continue
        data = path.read_bytes()
        if path.parent.name == "canonical":
            properties.check_canonical(properties.document_from_bytes(data))
        else:
            properties.check_policy(properties.policy_text_from_bytes(data))


# --- the positive controls --------------------------------------------------------------------


def _plain_documents() -> list[dict]:
    """String keys, no floats, at least two keys in non-sorted order, some nesting.

    The generated corpus is full of non-string keys, so `must_refuse` fires on it first and
    every control -- whatever it was named for -- was really testing that one branch. These
    documents reach the invariants further down.
    """
    return [
        {"b": 1, "a": 2},
        {"z": {"y": 1, "x": 2}, "a": [1, 2, {"q": None}]},
        {"A": 1, "a": 2},
        {"b": "two", "a": True, "c": None},
        {"nested": {"deep": {"deeper": ["x", "y"]}}, "alpha": 0},
    ]


def test_the_canonical_check_catches_a_nondeterministic_encoder(monkeypatch):
    """Two hosts disagreeing about a hash is the failure that makes an approval unbindable."""
    calls = {"n": 0}

    def wobbly(payload):
        calls["n"] += 1
        return json.dumps(payload, sort_keys=calls["n"] % 2 == 0, separators=(",", ":")).encode()

    monkeypatch.setattr(properties, "canonical_bytes", wobbly)
    # The message, not just the type. Several invariants can each catch a broken encoder, so a
    # bare `raises(AssertionError)` stays green when the one being tested is deleted.
    with pytest.raises(AssertionError, match="not deterministic"):
        for document in _plain_documents():
            properties.check_canonical(document)


def test_the_canonical_check_catches_an_order_dependent_encoder(monkeypatch):
    """Two mappings that compare equal must encode identically, or the hash depends on how the
    caller happened to build the dict -- and the same action, assembled twice, hashes twice."""
    monkeypatch.setattr(
        properties,
        "canonical_bytes",
        lambda payload: json.dumps(payload, sort_keys=False, separators=(",", ":")).encode(),
    )
    with pytest.raises(AssertionError, match="depends on insertion order"):
        for document in _plain_documents():
            properties.check_canonical(document)


def test_the_canonical_check_catches_a_lossy_encoder(monkeypatch):
    """The bug shape this exists for, and one this repository has already had once: a
    canonicalizer that folded two distinct keys into one. A review found that one; this is what
    would have found it without a review.

    **Order-independence is the invariant that catches it**, not the round trip: the fold is
    invisible to a re-encode of the already-folded output, but it makes the surviving value
    depend on which key was written last. That is stated rather than left to the reader,
    because a control whose docstring names a different invariant than the one that fires is
    how a subsumed guard gets recorded as load-bearing."""

    def lossy(payload):
        return json.dumps(
            {str(k).lower(): v for k, v in payload.items()}, sort_keys=True, separators=(",", ":")
        ).encode()

    monkeypatch.setattr(properties, "canonical_bytes", lossy)
    with pytest.raises(AssertionError, match="depends on insertion order"):
        for document in _plain_documents():
            properties.check_canonical(document)


def test_the_canonical_check_catches_a_non_idempotent_encoder(monkeypatch):
    """The invariant the other three do not reach: an encoder that is deterministic and
    order-independent, and still disagrees with itself one round trip later."""
    monkeypatch.setattr(
        properties,
        "canonical_bytes",
        lambda payload: json.dumps({"v": payload}, sort_keys=True, separators=(",", ":")).encode(),
    )
    with pytest.raises(AssertionError, match="not stable through a round trip"):
        for document in _plain_documents():
            properties.check_canonical(document)


def test_the_canonical_check_catches_an_encoder_that_admits_a_float(monkeypatch):
    """`float` is rejected because a binary float is not portable between two hosts. An encoder
    that let one through would produce a hash that verifies on the machine that made it."""
    monkeypatch.setattr(
        properties, "canonical_bytes", lambda payload: json.dumps(payload, default=str).encode()
    )
    with pytest.raises(AssertionError, match="a float"):
        properties.check_canonical({"amount": 0.1})


def test_the_canonical_check_catches_an_encoder_that_admits_a_non_string_key(monkeypatch):
    """`yaml.safe_load` produces non-string keys from `1:` and `true:`, which is how the policy
    hash reaches this path. `json.dumps` coerces them to strings silently, so two distinct
    documents get one canonical form and nothing raises.

    `sort_keys=False` here on purpose: with it on, `json.dumps` refuses to order a `str`
    against an `int` and the control would pass on a `TypeError` instead of on the guard."""
    monkeypatch.setattr(
        properties,
        "canonical_bytes",
        lambda payload: json.dumps(payload, sort_keys=False, separators=(",", ":")).encode(),
    )
    with pytest.raises(AssertionError, match="a non-string key"):
        properties.check_canonical({"1": "a", 1: "b"})


def test_the_canonical_check_catches_an_encoder_that_sorts_the_wrong_way(monkeypatch):
    """Sortedness is what makes the form canonical rather than merely consistent, and it is the
    one invariant the other controls cannot reach: a reverse-sorting encoder is deterministic,
    order-independent and stable through a round trip, and only this check sees it."""
    monkeypatch.setattr(
        properties,
        "canonical_bytes",
        lambda payload: json.dumps(
            dict(sorted(payload.items(), reverse=True)), separators=(",", ":")
        ).encode(),
    )
    with pytest.raises(AssertionError, match="keys are not sorted"):
        for document in _plain_documents():
            properties.check_canonical(document)


def test_the_canonical_check_catches_an_error_outside_the_closed_set(monkeypatch):
    """`InvalidArgument` is the contract. Anything else reaching a caller is a crash nobody
    classified, and `errors.py` calls its set closed."""

    def raising(payload):
        raise TypeError("not JSON serializable")

    monkeypatch.setattr(properties, "canonical_bytes", raising)
    with pytest.raises(AssertionError, match="outside the closed error set"):
        properties.check_canonical({"a": 1})


def test_the_closed_set_check_covers_every_call_and_not_just_the_first(monkeypatch):
    """The finding a positive control turned up while this file was being written: an encoder
    broken only on its *second* call raised `TypeError` straight out of `check_canonical`,
    because the later calls were bare. Every call goes through `_encode` now, and this is what
    holds it there."""
    calls = {"n": 0}

    def late(payload):
        calls["n"] += 1
        if calls["n"] > 1:
            raise TypeError("broken on every call but the first")
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    monkeypatch.setattr(properties, "canonical_bytes", late)
    with pytest.raises(AssertionError, match="outside the closed error set"):
        properties.check_canonical({"a": 1, "b": 2})


def test_the_policy_check_catches_a_loader_that_raises_something_else(monkeypatch):
    """`PolicyError` is the closed contract. A `KeyError` reaching a caller still denies, but
    through a crash nobody classified -- and a caller catching `PolicyError` would not catch it."""

    def raising(text, *, source="<string>"):
        raise KeyError("actions")

    monkeypatch.setattr(properties, "policy_from_yaml", raising)
    with pytest.raises(AssertionError, match="KeyError"):
        properties.check_policy("schema: ctrlrun.policy/v3")


def test_the_policy_check_catches_an_unstable_policy_hash(monkeypatch):
    """Two parses of one document disagreeing about the hash would make `policy_hash` useless
    as the answer to "what decided this action"."""
    real = properties.policy_from_yaml
    calls = {"n": 0}

    def drifting(text, *, source="<string>"):
        policy = real(text, source=source)
        calls["n"] += 1
        if calls["n"] % 2 == 0:
            object.__setattr__(policy, "_canonical", {"drifted": calls["n"]})
        return policy

    monkeypatch.setattr(properties, "policy_from_yaml", drifting)
    with pytest.raises(AssertionError, match="two policy hashes"):
        properties.check_policy((REPO_ROOT / "ctrlrun.example.yaml").read_text(encoding="utf-8"))


# --- the findings that are real and not yet fixed -----------------------------------------------


def test_the_known_findings_still_reproduce():
    """`KNOWN_FINDINGS` records a defect that is real, reported, and unfixed. This asserts each
    one **still happens**.

    The day it is fixed this goes red and the entry must be deleted. That is the point: a
    recorded limit that quietly starts passing is exactly the false green the rest of this file
    exists to prevent, and an excuse list nobody re-checks becomes permanent."""
    assert properties.KNOWN_FINDINGS, "no known findings; delete this test with the last entry"

    assert properties.check_canonical({"note": "\ud800"}) == "lone-surrogate-in-a-string", (
        "canonical_bytes no longer raises UnicodeEncodeError for an unpaired surrogate -- "
        "if it now raises InvalidArgument, delete the KNOWN_FINDINGS entry and this assertion"
    )


def test_the_surrogate_finding_is_reachable_from_a_json_payload():
    """Severity, asserted rather than asserted-in-prose. `json.loads` produces a lone surrogate
    from a six-character escape, so an MCP tool call can carry one into the action path, and
    what comes back out is not in the closed error set that `errors.py` defines."""
    from ctrlrun import Action, Principal
    from ctrlrun.errors import CTRLRunError

    arguments = json.loads('{"note": "\\ud800"}')
    assert arguments == {"note": "\ud800"}

    action = Action(
        name="pay", environment="prod", principal=Principal(agent="a"), arguments=arguments
    )
    with pytest.raises(UnicodeEncodeError):
        _ = action.action_hash
    try:
        _ = action.action_hash
    except CTRLRunError:  # pragma: no cover - the finding is that this does not happen
        raise AssertionError("the finding is fixed; update KNOWN_FINDINGS") from None
    except UnicodeEncodeError:
        pass


def test_the_seed_corpus_reproduces_every_known_finding():
    """A finding whose reproducer is not in the corpus is a finding the next campaign has to
    rediscover by luck."""
    reproduced = set()
    for path in sorted((FUZZ / "corpus" / "canonical").iterdir()):
        if path.is_file():
            found = properties.check_canonical(properties.document_from_bytes(path.read_bytes()))
            if found:
                reproduced.add(found)
    assert reproduced == set(properties.KNOWN_FINDINGS), (
        f"corpus reproduces {reproduced}, recorded findings are {set(properties.KNOWN_FINDINGS)}"
    )


# --- the Atheris entry points -------------------------------------------------------------------


@pytest.mark.parametrize("name", ["fuzz_canonical.py", "fuzz_policy.py"])
def test_each_target_is_an_atheris_harness(name):
    """Scorecard detects Python fuzzing by finding `import atheris` in a `.py` file, which is a
    thing a decorative file could also do. These assert the harness is real: it instruments,
    it feeds the decoder, and it hands control to the fuzzer."""
    source = (FUZZ / name).read_text(encoding="utf-8")
    assert "import atheris" in source
    assert "atheris.Setup(" in source and "atheris.Fuzz()" in source
    assert "instrument_all" in source or "instrument_imports" in source
    assert "properties." in source, "the harness asserts nothing of its own"


@pytest.mark.parametrize("name", ["fuzz_canonical.py", "fuzz_policy.py"])
def test_each_target_runs_its_property_without_atheris_installed(name):
    """The targets are importable and runnable as plain scripts against the seed corpus, so a
    contributor with no fuzzing toolchain -- and this suite -- can still execute them. A target
    that only runs under Atheris is a target that runs in one place, once a week."""
    result = subprocess.run(
        [sys.executable, str(FUZZ / name), "--corpus"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert "inputs" in result.stdout


# --- packaging and CI ---------------------------------------------------------------------------


def test_the_fuzz_corpus_is_not_packaged():
    """`fuzz/` is a development tree like `research/`. The wheel and the sdist carry neither."""
    manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    # Line-exact: `prune fuzz-disabled` contains `prune fuzz`, and a substring check passed it.
    assert "prune fuzz" in manifest.splitlines()


def test_ci_actually_runs_the_fuzzers():
    """A fuzz target nothing executes is a file that satisfies a scanner. This asserts a job
    exists, that it runs both targets, and that it bounds them -- an unbounded `atheris.Fuzz()`
    in CI hangs until the job timeout, which reads as a broken build and not as a finding."""
    import yaml

    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "fuzz.yml").read_text())
    job = workflow["jobs"]["fuzz"]
    steps = {str(step.get("name", "")): str(step.get("run", "")) for step in job["steps"]}

    # The gate runs both targets without Atheris, so a toolchain problem cannot silence them.
    gate = steps["The seed corpus still holds"]
    # The campaign is the Atheris run, and is where a missing bound or a dropped target hides:
    # both names also appear in the gate, so searching the whole job proves nothing.
    campaign = steps["Campaign"]
    for target in ("fuzz_canonical.py", "fuzz_policy.py"):
        assert f"python fuzz/{target} --corpus" in gate, target
        bounded = [
            line for line in campaign.splitlines() if target in line and "-max_total_time=" in line
        ]
        assert bounded, f"{target} is not run under a time bound in the campaign"
    assert "atheris" in steps["Install Atheris"], "the campaign would run without Atheris"
    assert workflow["permissions"] == {"contents": "read"}
