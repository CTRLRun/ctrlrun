"""Action model, canonicalization, action_hash. Build-list item 1; SPEC-v0.1 §2."""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
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
    """Who is acting: an agent, optionally on behalf of a human."""

    agent: str
    user: str | None = None

    def __post_init__(self) -> None:
        # SPEC: §2.1 does not say what an empty identity means; treat it as invalid.
        if not self.agent:
            raise InvalidArgument("principal.agent must be a non-empty string")
        if self.user is not None and not self.user:
            raise InvalidArgument("principal.user must be a non-empty string or None")


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


def canonicalize(action: Action) -> bytes:
    """Return the canonical form of an Action: UTF-8 JSON, sorted keys, no whitespace.

    `action_id` and timestamps are excluded; the schema tag is included (SPEC-v0.1 §2.2).
    """
    payload = {
        "arguments": _plain_mapping(action.arguments),
        "environment": action.environment,
        "name": action.name,
        "principal": {"agent": action.principal.agent, "user": action.principal.user},
        "resource": action.resource,
        "schema": ACTION_SCHEMA,
    }
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def action_hash(action: Action) -> str:
    """Return the action hash used to bind approvals to an exact action (SPEC-v0.1 §2.3)."""
    return action.action_hash
