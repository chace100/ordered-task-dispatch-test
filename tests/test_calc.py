import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from calc import divide_safe  # noqa: E402


def test_divide_safe_divides_normally():
    assert divide_safe(6, 3) == 2
    assert divide_safe(1, 2) == 0.5
    assert divide_safe(-9, 3) == -3


def test_divide_safe_returns_none_on_zero_divisor():
    assert divide_safe(1, 0) is None
    assert divide_safe(0, 0) is None
    assert divide_safe(-1, 0.0) is None


def test_divide_safe_returns_none_for_float_zero_divisor():
    # A float zero is not the same object as the cached int 0, so this guards
    # against an identity check (`is`) sneaking back in place of `==`.
    assert divide_safe(1, 0.0) is None
    assert divide_safe(1, -0.0) is None
    assert divide_safe(0.0, 0.0) is None
