"""The invariants the fuzz targets assert, and the decoders that turn bytes into inputs.

**No Atheris import lives here on purpose.** Atheris is one driver for these properties and
`tests/test_fuzzing.py` is another, so they run on every commit whether or not anybody has a
fuzzing toolchain -- and a contributor can reproduce a finding with nothing but the standard
library. `fuzz/fuzz_*.py` are the Atheris entry points and contain no assertions of their own.

Two properties, chosen because each is a promise the project has written down:

* **Canonicalization** (`v0.1 §2.3`, which calls it security-critical).
  Two distinct actions sharing a canonical form share an approval, and nothing in a receipt
  would look wrong. This repository has already had one such bug -- `str(key)` folding `{"1":
  "a", 1: "b"}` into a single key, with the survivor decided by insertion order -- and a review
  found it. These are the properties that would have found it without one.

* **`Policy.from_yaml`**, whose docstring says "anything malformed raises `PolicyError`". That
  is the fail-closed rule in one sentence. A loader raising `KeyError` still denies, but through
  a crash nobody classified, and a caller catching `PolicyError` would not catch it.
"""

from __future__ import annotations

import json
import math
from typing import Any

from ctrlrun import canonical_bytes
from ctrlrun.errors import InvalidArgument, PolicyError
from ctrlrun.policy import Policy


#: Indirection so the harness -- and the positive controls in `tests/test_fuzzing.py` -- can
#: substitute a deliberately broken loader. A property that cannot fail proves nothing.
def policy_from_yaml(text: str, *, source: str = "<string>") -> Policy:
    return Policy.from_yaml(text, source=source)


# --- known findings ---------------------------------------------------------------------------

#: Findings that are real, reported, and not yet fixed -- recorded here rather than worked
#: around in the decoder, because a fuzzer whose corpus is pruned to avoid its own findings
#: reports zero forever.
#:
#: **Empty, and the machinery stays.** It held `lone-surrogate-in-a-string` --
#: `canonical_bytes` raising `UnicodeEncodeError` instead of `InvalidArgument` for an unpaired
#: UTF-16 surrogate -- until that was fixed in `action.py`. The entry's own test asserted the
#: finding *still reproduced*, so the fix turned it red and the entry had to go. That is the
#: mechanism working, and it is why the dict is left in place for the next one.
#:
#: `test_the_seed_corpus_reproduces_every_known_finding` compares this to what the corpus
#: actually produces, in both directions, so an entry cannot be added without a reproducer and
#: a reproducer cannot start failing unnoticed.
KNOWN_FINDINGS: dict[str, str] = {}


# --- decoders ---------------------------------------------------------------------------------

_MAX_DEPTH = 5
_MAX_ITEMS = 6

# Deliberately includes values canonicalization must *refuse*. A property saying "only
# InvalidArgument escapes" is vacuous if nothing is ever rejected.
_INTERESTING_STRINGS = (
    "",
    "a",
    "0",
    "\x00",
    "\\",
    '"',
    " ",
    "é",
    "中文",
    "\U0001f511",
    "\ud800",  # unpaired surrogate -- see KNOWN_FINDINGS
    "﻿",
    "1e309",
)


class _Reader:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._i = 0

    def byte(self) -> int:
        if self._i >= len(self._data):
            return 0
        value = self._data[self._i]
        self._i += 1
        return value

    def spent(self) -> bool:
        return self._i >= len(self._data)


def _value(reader: _Reader, depth: int) -> Any:
    kind = reader.byte() % (10 if depth < _MAX_DEPTH else 8)
    if kind == 0:
        return None
    if kind == 1:
        return reader.byte() % 2 == 0
    if kind == 2:
        return reader.byte() - 128
    if kind == 3:
        return (reader.byte() << 56) * (10 ** (reader.byte() % 4))
    if kind == 4:
        return _INTERESTING_STRINGS[reader.byte() % len(_INTERESTING_STRINGS)]
    if kind == 5:
        return bytes([reader.byte(), reader.byte()]).decode("latin-1")
    if kind == 6:
        # float: canonicalization must refuse it at any depth (v0.1 §2.3).
        return (reader.byte() / 7.0, float("inf"), math.nan)[reader.byte() % 3]
    if kind == 7:
        # a type that is not JSON at all
        return (b"bytes", {1, 2}, object())[reader.byte() % 3]
    if kind == 8:
        return [_value(reader, depth + 1) for _ in range(reader.byte() % _MAX_ITEMS)]
    return _mapping(reader, depth + 1)


def _mapping(reader: _Reader, depth: int) -> dict[Any, Any]:
    out: dict[Any, Any] = {}
    for _ in range(reader.byte() % _MAX_ITEMS):
        # Non-string keys included on purpose: `yaml.safe_load` produces them from `1:` and
        # `true:`, which is how §7.1's policy hash reaches this path.
        key: Any = (
            _INTERESTING_STRINGS[reader.byte() % len(_INTERESTING_STRINGS)]
            if reader.byte() % 4
            else (reader.byte(), True, None)[reader.byte() % 3]
        )
        out[key] = _value(reader, depth)
    return out


def document_from_bytes(data: bytes) -> dict[Any, Any]:
    """Total and deterministic: every byte string maps to a document, and the same bytes always
    map to the same one. A decoder that raised would spend a campaign reporting its own crashes."""
    return _mapping(_Reader(data), 0)


def policy_text_from_bytes(data: bytes) -> str:
    """Bytes as a policy document. Undecodable input becomes text the loader must still refuse
    in words rather than crash on."""
    return data.decode("utf-8", "replace")


# --- the properties -----------------------------------------------------------------------------


def _contains_float(value: object) -> bool:
    if isinstance(value, float):
        return True
    if isinstance(value, dict):
        return any(_contains_float(k) or _contains_float(v) for k, v in value.items())
    if isinstance(value, list | tuple):
        return any(_contains_float(item) for item in value)
    return False


def _has_non_string_key(value: object) -> bool:
    if isinstance(value, dict):
        return any(not isinstance(k, str) or _has_non_string_key(v) for k, v in value.items())
    if isinstance(value, list | tuple):
        return any(_has_non_string_key(item) for item in value)
    return False


def _encode(document: object, what: str) -> bytes:
    """Every call goes through here, not just the first.

    A positive control found this: an encoder broken only on its *second* call raised
    `TypeError` straight out of `check_canonical`, because the later calls were bare. A
    property claiming "nothing but InvalidArgument escapes" has to hold that for every call it
    makes, or it holds for the one the author happened to wrap.
    """
    try:
        return canonical_bytes(document)  # type: ignore[arg-type]
    except (InvalidArgument, UnicodeEncodeError):
        raise
    except Exception as exc:
        raise AssertionError(
            f"canonicalization raised {type(exc).__name__} on {what}, which is outside the "
            f"closed error set in errors.py: {exc}"
        ) from exc


def check_canonical(document: dict[Any, Any]) -> str | None:
    """Assert every canonicalization invariant. Returns the name of a known finding if the
    input reproduces one, else None. Raises AssertionError on anything else."""
    # Stated before the call, so that *accepting* one of these is a failure. Checking only
    # what comes back cannot tell "refused correctly" from "never refused at all".
    must_refuse = []
    if _contains_float(document):
        must_refuse.append("a float, which is not portable between two hosts (v0.1 §2.3)")
    if _has_non_string_key(document):
        must_refuse.append("a non-string key, which two distinct documents can share")

    try:
        first = _encode(document, "the first pass")
    except InvalidArgument:
        return None  # a refusal in words is the contract working
    except UnicodeEncodeError as exc:
        # Was `KNOWN_FINDINGS["lone-surrogate-in-a-string"]`. `action.py` refuses an
        # unencodable string with `InvalidArgument` now, so this escaping again is a
        # regression and not a recorded limit.
        raise AssertionError(
            f"canonicalization raised UnicodeEncodeError, which is outside the closed error "
            f"set in errors.py: {exc}"
        ) from exc

    assert not must_refuse, "canonicalization accepted a document holding " + " and ".join(
        must_refuse
    )
    assert isinstance(first, bytes)

    # 1. Deterministic.
    assert _encode(document, "a repeat pass") == first, "canonicalization is not deterministic"

    # 2. Independent of insertion order. Two dicts that compare equal must encode identically,
    #    or the hash depends on how the caller happened to build the mapping.
    reordered = dict(reversed(list(document.items())))
    assert _encode(reordered, "a reordered mapping") == first, (
        "canonicalization depends on insertion order: two equal mappings, two canonical forms"
    )

    # 3. Valid UTF-8 JSON.
    decoded = json.loads(first.decode("utf-8"))

    # 4. Stable through a round trip. A lossy encoding -- two distinct keys folding into one --
    #    shows up here as a second pass that disagrees with the first.
    assert _encode(decoded, "a round trip") == first, (
        "canonicalization is not stable through a round trip"
    )

    # 5. Keys sorted at every level, which is what makes the form canonical rather than merely
    #    consistent.
    _assert_sorted(decoded)
    return None


def _assert_sorted(value: object) -> None:
    if isinstance(value, dict):
        keys = list(value)
        assert keys == sorted(keys), f"keys are not sorted: {keys}"
        for item in value.values():
            _assert_sorted(item)
    elif isinstance(value, list):
        for item in value:
            _assert_sorted(item)


def check_policy(text: str) -> str | None:
    """`Policy.from_yaml` either returns a policy or raises `PolicyError`. Nothing else."""
    try:
        policy = policy_from_yaml(text)
    except PolicyError:
        return None
    except RecursionError:
        # Out of scope and said so rather than caught silently: a document nested past the
        # interpreter's limit is a stack-depth property, not a policy one.
        return None
    except Exception as exc:
        raise AssertionError(
            f"Policy.from_yaml raised {type(exc).__name__} for a document it should have "
            f"refused with PolicyError: {exc}"
        ) from exc

    # A policy that loaded must hash, and hash the same way twice: `policy_hash` is the answer
    # to "what decided this action", and two parses disagreeing would make it useless.
    first = policy.policy_hash
    assert policy.policy_hash == first, "policy_hash is not stable within one policy"
    assert policy_from_yaml(text).policy_hash == first, (
        "two parses of one document produced two policy hashes"
    )
    return None
