"""Placeholder suite. Real acceptance tests arrive with build-list item 1 (SPEC-v0.1 §7)."""

import ctrlrun


def test_package_is_importable() -> None:
    assert ctrlrun.__name__ == "ctrlrun"
