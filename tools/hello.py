"""Tiny greeting helper (approval-gate demo)."""

from __future__ import annotations

from collections.abc import Iterable


def hello(name: str = "world") -> str:
    """Return a greeting for *name*, defaulting to the literal string world."""
    if not isinstance(name, str):
        raise TypeError(f"name must be str, got {type(name).__name__}")
    return f"hello, {name}"


def hello_many(names: Iterable[str]) -> list[str]:
    """Return one greeting per non-blank name, in the order given.

    Blank names (empty or whitespace-only) are skipped without error and
    duplicates are preserved; the result is what a hand-rolled
    ``[hello(n) for n in names]`` loop would produce, minus the blanks.
    """
    if isinstance(names, str):
        # Fail closed: a bare string would otherwise be greeted letter by letter.
        raise TypeError("names must be an iterable of str, not a bare str")
    greetings = []
    for name in names:
        if isinstance(name, str) and not name.strip():
            continue
        # Non-strings fall through to hello()'s own TypeError.
        greetings.append(hello(name))
    return greetings
