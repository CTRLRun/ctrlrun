"""`ctrlrun.reporting`: the shared producer behind `stats`, `inspect` and the operator server."""

from __future__ import annotations

import pytest


def test_a_non_ascii_digit_in_since_is_refused_not_crashed():
    """`str.isdigit()` is true for '²' and `int()` refuses it, so `--since ²h` raised a bare
    `ValueError` out of a function whose docstring says it raises `InvalidArgument`."""
    from ctrlrun.errors import InvalidArgument
    from ctrlrun.reporting import since_boundary

    # Written as escapes: these are exactly the characters `str.isdigit()` accepts and `int()`
    # refuses, and a linter is right to flag them as ambiguous in source.
    for text in ("\u00b2h", "\u0661\u0662h", "\u00bdd"):  # superscript 2, Arabic-Indic 12, one half
        with pytest.raises(InvalidArgument):
            since_boundary(text)


def test_an_ascii_relative_window_still_parses():
    """The positive control."""
    from ctrlrun.reporting import since_boundary

    assert since_boundary("24h") is not None
