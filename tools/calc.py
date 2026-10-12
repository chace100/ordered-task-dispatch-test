"""Arithmetic helpers."""


def divide(a, b):
    return a / b


def divide_safe(a, b):
    """Divide a by b, returning None when the divisor is zero."""
    if b == 0:
        return None
    return a / b
