"""Shared pytest setup: put tests/ and tools/ on sys.path.

pytest imports this file before any test module in this directory, so every
test file — run alone or as part of the whole suite — can import fake_github
and the modules from tools/ without carrying its own sys.path boilerplate.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# tools/ is inserted first so tests/ ends up ahead of it in the search order:
# a same-named module in tools/ must never shadow one in tests/.
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
