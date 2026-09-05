"""Action model, canonicalization, action_hash. Build-list item 1; SPEC-v0.1 §2."""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Final, TypeAlias

from .errors import InvalidArgument

ACTION_SCHEMA: Final = "ctrlrun.action/v1"

#: The argument value types allowed by SPEC-v0.1 §2.3. Note the absence of float.
PlainValue: TypeAlias = "str | int | bool | list[PlainValue] | dict[str, PlainValue] | None"

#: How those values are stored on an Action: deep-frozen (SPEC-v0.1 §2.2).
FrozenValue: TypeAlias = (
    "str | int | bool | tuple[FrozenValue, ...] | Mapping[str, FrozenValue] | None"
)

#: The claim value types SPEC-v0.3 §2.1 allows. `float` is absent for v0.1 §2.3's reason, and
#: containers are absent because a provider flattens or drops a structured claim rather than
#: storing one here.
ClaimValue: TypeAlias = "str | int | bool"

#: A `Principal` with no claims. Shared, because it is immutable — but it is handed out by a
#: `default_factory` rather than used as a bare default: Python 3.11 refuses *any* unhashable
#: dataclass default, `mappingproxy` included, and 3.11 is the floor this package supports.
#: 3.12 relaxed that check to `list`/`dict`/`set` only, so the bare form passes there and fails
#: on the minimum version — which is exactly the shape a local run cannot see.
NO_CLAIMS: Final[Mapping[str, ClaimValue]] = MappingProxyType({})

_ID_HEX_BYTES: Final = 16  # "act_" + 32 hex chars


def _new_action_id() -> str:
    return f"act_{secrets.token_hex(_ID_HEX_BYTES)}"


def _frozen_value(value: object, path: str) -> FrozenValue:
    """Validate an argument value and return a deep-frozen copy of it."""
    if isinstance(value, float):
        raise InvalidArgument(
            f"float is not an allowed argument type at {path}: "
            "use integer minor units (amount=200000) or a decimal string ('2000.00')"
        )
    if value is None or isinstance(value, str | int):  # bool is a subclass of int
        return value
    if isinstance(value, Mapping):
        return _frozen_mapping(value, path)
    if isinstance(value, list | tuple):
        # SPEC: §2.3 — a tuple canonicalizes to the same JSON array as the equivalent list,
        # so it is accepted on input and normalized here.
        return tuple(_frozen_value(item, f"{path}[{index}]") for index, item in enumerate(value))
    raise InvalidArgument(f"{type(value).__name__} is not an allowed argument type at {path}")


def _frozen_mapping(value: Mapping[Any, Any], path: str) -> Mapping[str, FrozenValue]:
    result: dict[str, FrozenValue] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise InvalidArgument(f"argument keys must be str, got {type(key).__name__} at {path}")
        result[key] = _frozen_value(item, f"{path}.{key}")
    return MappingProxyType(result)


def _frozen_claims(claims: object) -> Mapping[str, ClaimValue]:
    """Validate and snapshot a claim mapping (SPEC-v0.3 §2.1).

    Snapshotted for the reason `Action.arguments` is (v0.1 §2.2): a caller holding the original
    object must not be able to change a constructed Principal.
    """
    if not isinstance(claims, Mapping):
        raise InvalidArgument("principal.claims must be a mapping")
    result: dict[str, ClaimValue] = {}
    for key, value in claims.items():
        if not isinstance(key, str) or not key:
            raise InvalidArgument(f"principal.claims keys must be non-empty strings, got {key!r}")
        if isinstance(value, float):
            raise InvalidArgument(
                f"principal.claims[{key!r}] is a float; use an int or a decimal string, for the "
                "reason v0.1 §2.3 rejects one in an argument"
            )
        if not isinstance(value, str | int):  # bool is a subclass of int, and is allowed
            raise InvalidArgument(
                f"principal.claims[{key!r}] is {type(value).__name__}; a claim value must be "
                "str, int or bool — a structured claim is flattened or dropped by the provider "
                "that read it"
            )
        result[key] = value
    return MappingProxyType(result)


def _plain_value(value: FrozenValue) -> PlainValue:
    """Return the JSON-serializable counterpart of a stored (frozen) argument value."""
    if isinstance(value, Mapping):
        return _plain_mapping(value)
    if isinstance(value, tuple):
        return [_plain_value(item) for item in value]
    return value


def _plain_mapping(value: Mapping[str, FrozenValue]) -> dict[str, PlainValue]:
    return {key: _plain_value(item) for key, item in value.items()}


@dataclass(frozen=True)
class Principal:
    """Who is acting: an agent, optionally on behalf of a human.

    `claims`, `issuer` and `expires_at` are what an `IdentityProvider` verified (SPEC-v0.3 §2.1).
    None of the three is part of the canonical form (§2.2): an approval binds to an action hash,
    and a hash that moved when a token rotated would invalidate it for a reason no human could
    see and no agent could fix.
    """

    agent: str
    user: str | None = None
    claims: Mapping[str, ClaimValue] = field(default_factory=lambda: NO_CLAIMS)
    issuer: str | None = None
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        # SPEC: §2.1 does not say what an empty identity means; treat it as invalid.
        if not self.agent:
            raise InvalidArgument("principal.agent must be a non-empty string")
        if self.user is not None and not self.user:
            raise InvalidArgument("principal.user must be a non-empty string or None")
        if self.issuer is not None and not self.issuer:
            raise InvalidArgument("principal.issuer must be a non-empty string or None")
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            # SPEC-v0.3 §2.1 — an expiry in an unstated timezone cannot be checked, and
            # guessing UTC would silently extend or shorten it.
            raise InvalidArgument(
                "principal.expires_at must be timezone-aware; a naive datetime states an "
                "instant nobody can compare a clock against"
            )
        object.__setattr__(self, "claims", _frozen_claims(self.claims))

    @property
    def claim_names(self) -> tuple[str, ...]:
        """The claim names, sorted. What evidence shows where values are withheld (§2.4)."""
        return tuple(sorted(self.claims))


@dataclass(frozen=True, eq=False)
class Action:
    """A proposed agent action: what, with which arguments, by whom, on what.

    Equality and hashing follow the proposal, not the content: two Actions are equal iff
    their `action_id` matches (SPEC-v0.1 §2.1). Identical content shares an `action_hash`.
    """

    name: str
    arguments: Mapping[str, Any]
    principal: Principal
    resource: str | None = None
    environment: str = "production"
    action_id: str = field(default_factory=_new_action_id)

    def __post_init__(self) -> None:
        if not self.name:
            raise InvalidArgument("action.name must be a non-empty string")
        if not self.environment:
            raise InvalidArgument("action.environment must be a non-empty string")
        if self.resource is not None and not self.resource:
            raise InvalidArgument("action.resource must be a non-empty string or None")
        if not isinstance(self.arguments, Mapping):
            raise InvalidArgument("action.arguments must be a mapping")
        object.__setattr__(self, "arguments", _frozen_mapping(self.arguments, "arguments"))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Action):
            return NotImplemented
        return self.action_id == other.action_id

    def __hash__(self) -> int:
        return hash(self.action_id)

    @property
    def action_hash(self) -> str:
        """`"sha256:" + hex(SHA-256(canonical form))` (SPEC-v0.1 §2.3)."""
        return f"sha256:{hashlib.sha256(canonicalize(self)).hexdigest()}"

    @property
    def canonical_arguments(self) -> dict[str, Any]:
        """Arguments parsed back from the canonical form; what the executor is given.

        Plain, mutable containers, built fresh on each access, so an executor cannot
        reach back into the Action (SPEC-v0.1 §2.2).
        """
        arguments: dict[str, Any] = json.loads(canonicalize(self))["arguments"]
        return arguments


def canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    """The canonical form of an arbitrary mapping: UTF-8 JSON, sorted keys, no whitespace.

    **This is the encoding half of `v0.1 §2.3`, promoted rather than written** (SPEC-v0.6 §6.2,
    §9.1). `canonicalize(action)` builds a fixed six-key payload and calls this, so there is
    provably one implementation rather than two that agree today: the receipt chain (§6.2) and
    the policy hash (§7.1) both need the canonical form of a *document*, and the alternative was
    a private second canonicalizer, which §6.2 forbids by name.

    Sorted keys recursively, `separators=(",", ":")`, `ensure_ascii=False`, UTF-8, and **`float`
    rejected at any depth**. The rejection is not `json.dumps`'s doing -- `allow_nan=False` only
    catches `NaN` and the infinities -- so it is checked here, because a canonicalizer that
    silently encoded `0.1` would make two hosts with different libm disagree about a hash.

    `ctrlrun.action/v1` is unchanged by this promotion and T164b is the corpus that proves it.
    """
    return json.dumps(
        _no_floats(payload, "payload"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _no_floats(value: object, path: str) -> PlainValue:
    """Reject `float` at any depth, and return the value otherwise unchanged.

    Separate from `_frozen_value` because that one is about what an `Action` may *hold* and this
    is about what may be *encoded*. They agree about floats and about nothing else: this accepts
    a document whose keys and shapes an `Action` would never allow, which is the point of a
    canonicalizer that works on documents.
    """
    if isinstance(value, float):
        raise InvalidArgument(
            f"float is not encodable at {path}: canonicalization is security-critical and a "
            "binary float is not portable between two hosts (v0.1 §2.3)"
        )
    if isinstance(value, Mapping):
        for key in value:
            if not isinstance(key, str):
                # `str(key)` here was silently lossy and order-dependent: `{"1": "a", 1: "b"}`
                # and `{1: "b", "1": "a"}` both canonicalized to one key, and *which* value
                # survived depended on insertion order -- two distinct payloads, one canonical
                # form. It also admitted a Python `repr` into a canonical form, since a tuple key
                # rendered as `"(1, 2)"`, which plain `json.dumps` refuses outright. A review
                # found it, and §7.1's policy hash is what makes it reachable: `yaml.safe_load`
                # produces non-string keys from `1:` and `true:`.
                raise InvalidArgument(
                    f"a canonical form has string keys only, and {path} has a "
                    f"{type(key).__name__} key {key!r}: quote it in the document"
                )
        return {key: _no_floats(item, f"{path}.{key}") for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_no_floats(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if value is None or isinstance(value, str | int):  # bool is a subclass of int
        return value
    raise InvalidArgument(
        f"{type(value).__name__} is not encodable at {path}: the canonical form is JSON with "
        "no extensions (v0.1 §2.3)"
    )


def canonicalize(action: Action) -> bytes:
    """Return the canonical form of an Action: UTF-8 JSON, sorted keys, no whitespace.

    `action_id` and timestamps are excluded; the schema tag is included (SPEC-v0.1 §2.2). The
    encoding is `canonical_bytes`'s, and calling through is what makes that one implementation
    rather than two (SPEC-v0.6 §6.2).
    """
    return canonical_bytes(
        {
            "arguments": _plain_mapping(action.arguments),
            "environment": action.environment,
            "name": action.name,
            "principal": {"agent": action.principal.agent, "user": action.principal.user},
            "resource": action.resource,
            "schema": ACTION_SCHEMA,
        }
    )


def action_hash(action: Action) -> str:
    """Return the action hash used to bind approvals to an exact action (SPEC-v0.1 §2.3)."""
    return action.action_hash
