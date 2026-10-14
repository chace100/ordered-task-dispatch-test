"""Tiny greeting helper (approval-gate demo)."""

from __future__ import annotations

from collections.abc import Iterable


def _is_blank(name: str) -> bool:
    """True when *name* is empty or whitespace-only."""
    return not name.strip()


def hello(name: str = "world") -> str:
    """Return a greeting for *name*, defaulting to the literal string world."""
    if not isinstance(name, str):
        raise TypeError(f"name must be str, got {type(name).__name__}")
    if _is_blank(name):
        raise ValueError("name must be a non-empty string")
    return f"hello, {name}"


def hello_many(names: Iterable[str]) -> list[str]:
    """Return one greeting per non-blank name, in the order given.

    Blank names (empty or whitespace-only) are skipped without error —
    unlike ``hello()`` itself, which raises ValueError on blanks — and
    duplicates are preserved. Raises TypeError for a non-iterable *names*
    argument, for a bare string (which would otherwise be greeted letter
    by letter), and for non-string entries (which propagate ``hello()``'s
    own TypeError).
    """
    if isinstance(names, str):
        # Fail closed: a bare string is iterable, so iter() alone would let
        # it through and greet it letter by letter.
        raise TypeError(f"names must be an iterable of str, got {type(names).__name__}")
    try:
        # Accept exactly what the for loop below accepts: iter() admits
        # old-style sequence objects (__getitem__, no __iter__) that an
        # isinstance check against Iterable would reject.
        names = iter(names)
    except TypeError:
        # Fail closed with the module's own message, not Python's raw
        # "'NoneType' object is not iterable".
        raise TypeError(f"names must be an iterable of str, got {type(names).__name__}") from None
    greetings = []
    for name in names:
        if isinstance(name, str) and _is_blank(name):
            continue
        # Non-strings fall through to hello()'s own TypeError.
        greetings.append(hello(name))
    return greetings
