"""Pytest bootstrap: put the pure-Python ``tools/`` scripts on ``sys.path``.

The tools are standalone scripts rather than an installed package, so each test
module used to prepend ``tools/`` itself. Doing it once here keeps that layout
assumption in a single place.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
