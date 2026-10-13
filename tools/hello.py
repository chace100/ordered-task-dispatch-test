"""Tiny greeting helper (approval-gate demo)."""

from __future__ import annotations


def hello(name: str = "world") -> str:
    """Return a greeting for *name*, defaulting to the literal string world."""
    return f"hello, {name}"
