#!/usr/bin/env python3
"""Small calculator helpers.

Pure functions with no GitHub/network dependency, same as ``depgraph.py``.

The one rule worth stating out loud: division by zero returns ``None`` instead
of raising, so callers can decide what a missing value means for them.
"""

from __future__ import annotations


def divide_safe(a, b):
    """Return ``a / b``, or ``None`` when ``b`` is zero.

    ``ZeroDivisionError`` is swallowed deliberately: a zero divisor is treated
    as "no answer" rather than as a programming error. ``b == 0`` also catches
    ``0.0`` and ``-0.0``.

    Comparison is by value (``==``), not identity (``is``): ``0 is 0`` only
    holds because CPython caches small integers, and a float zero is a
    different object, so an identity check would let ``0.0`` through.

    >>> divide_safe(6, 3)
    2.0
    >>> divide_safe(1, 0) is None
    True
    >>> divide_safe(1, 0.0) is None
    True
    """
    if b == 0:
        return None
    return a / b
