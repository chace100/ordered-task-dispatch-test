"""Tiny greeting helper (approval-gate demo)."""

from __future__ import annotations


def hello(name: str = "world") -> str:
    """Return a greeting for *name*, defaulting to the literal string world."""
    if not isinstance(name, str):
        raise TypeError(f"name must be str, got {type(name).__name__}")
    if not name.strip():
        raise ValueError("name must be a non-empty string")
    return f"hello, {name}"
