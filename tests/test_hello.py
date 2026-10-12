import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from hello import hello  # noqa: E402


def test_hello_defaults_to_world_and_accepts_a_name():
    assert hello() == "hello, world"
    assert hello("chace") == "hello, chace"
