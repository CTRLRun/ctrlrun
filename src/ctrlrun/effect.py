"""Effect keys: the identity of a logical effect. Build-list item 5; SPEC-v0.1 §5.1.

The effect key is what duplicate protection is built on (§5.3): two attempts that resolve to
the same key are the same real-world effect, whatever their `action_id`s are. Resolution is
therefore strict — an unresolvable key is never a silent `None`, and a placeholder that
resolves to nothing identifiable is refused rather than rendered.

`EffectState` and the transitions of §5.2 arrive with build-list item 7.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Final

from .action import Action
from .errors import CTRLRunError, EffectKeyError, InvalidArgument

#: The decision reason recorded when an effect template cannot be resolved (SPEC §5.1, §6.1).
UNRESOLVED_EFFECT: Final = "effect_key_error"

#: In an effect template, `{resource}` names the action's `resource` field (SPEC §5.1).
RESOURCE_PLACEHOLDER: Final = "resource"

#: A template is literal text and `{name}` placeholders. Anything else is a typo. `name` is
#: an identifier — a letter or underscore, then letters, digits or underscores — because it
#: names an argument, and an argument name is a Python parameter name.
_TOKEN: Final = re.compile(r"\{(?P<name>[^\W\d]\w*)\}|(?P<text>[^{}]+)|(?P<bad>.)")


def template_placeholders(template: str) -> tuple[str, ...]:
    """Return the placeholder names in `template`, in order. Malformed → `InvalidArgument`.

    The grammar is deliberately smaller than `str.format`: no `{{` escapes, no format specs,
    no attribute or index access. An effect key is an identity, not a formatted string, so a
    brace that is not part of a `{name}` placeholder is a typo — and a typo must not become
    part of an effect identity.
    """
    if not template:
        raise InvalidArgument("a template must be a non-empty string")
    names: list[str] = []
    for token in _TOKEN.finditer(template):
        if token.group("bad") is not None:
            raise InvalidArgument(
                f"{template!r} is not a valid template: expected literal text and "
                f"'{{name}}' placeholders, found {token.group('bad')!r} at position "
                f"{token.start()}"
            )
        name = token.group("name")
        if name is not None:
            names.append(name)
    return tuple(names)


def resolve_effect_key(template: str, action: Action) -> str:
    """Resolve an effect template against a constructed action (SPEC-v0.1 §5.1).

    Placeholders name the action's arguments; `{resource}` names its `resource` field. A
    placeholder with no value raises `EffectKeyError`, and the action is refused: an action
    whose logical effect cannot be identified cannot be protected against duplication.
    """
    names = template_placeholders(template)
    values: dict[str, Any] = action.canonical_arguments
    if RESOURCE_PLACEHOLDER in names:
        if RESOURCE_PLACEHOLDER in values:
            # SPEC: §5.1 — the spec gives `{resource}` to the resource field but does not say
            # what an argument of the same name does. Two candidate values for one key is the
            # fail-closed case: refuse, rather than silently pick the one the author did not
            # mean, because duplicate protection depends on which one it is.
            raise EffectKeyError(
                f"{template!r}: '{{resource}}' is ambiguous — {action.name} has both a "
                "resource field and an argument named 'resource'; rename the argument"
            )
        if action.resource is None:
            raise EffectKeyError(
                f"{template!r}: '{{resource}}' needs a resource on the action, and "
                f"{action.name} has none"
            )
        values[RESOURCE_PLACEHOLDER] = action.resource
    return _render(template, values, EffectKeyError)


def resolve_resource(template: str, arguments: Mapping[str, Any]) -> str:
    """Resolve a `resource=` template against the bound call arguments (SPEC-v0.1 §5.1).

    Same syntax and same resolver as an effect template, but resolved *before* the Action
    exists: `resource` is part of the canonical form and therefore of the action hash (§2.2),
    so a missing placeholder is an `InvalidArgument` at construction time, as in §2.
    """
    return _render(template, arguments, InvalidArgument)


def _render(template: str, values: Mapping[str, Any], error: type[CTRLRunError]) -> str:
    """Substitute `values` into `template`, raising `error` for anything unresolvable."""
    parts: list[str] = []
    for token in _TOKEN.finditer(template):
        text = token.group("text")
        if text is not None:
            parts.append(text)
            continue
        name = token.group("name")
        if name is None:
            raise InvalidArgument(
                f"{template!r} is not a valid template: found {token.group('bad')!r} at "
                f"position {token.start()}"
            )
        if name not in values:
            raise error(
                f"{template!r}: no value for '{{{name}}}' (have: "
                f"{', '.join(sorted(values)) or 'nothing'})"
            )
        parts.append(_rendered(name, values[name], template, error))
    return "".join(parts)


def _rendered(name: str, value: object, template: str, error: type[CTRLRunError]) -> str:
    """Render one placeholder value, or raise: only a non-empty `str` or an `int` will do.

    SPEC: §5.1 — the spec does not restrict placeholder types. This is the fail-closed
    reading: `None` and `""` identify nothing and would collide across unrelated actions,
    `bool` identifies nothing either, and a container has no stable rendering. An effect key
    must be an identity a human can read and two attempts can agree on.
    """
    if isinstance(value, str):
        if not value:
            raise error(f"{template!r}: '{{{name}}}' is empty; an effect key must identify")
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    raise error(
        f"{template!r}: '{{{name}}}' is {type(value).__name__}; a placeholder must resolve "
        "to a non-empty string or an int"
    )
