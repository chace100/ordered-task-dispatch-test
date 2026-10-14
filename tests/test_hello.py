import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from hello import hello  # noqa: E402


def test_hello_defaults_to_world():
    assert hello() == "hello, world"


def test_hello_accepts_a_name():
    assert hello("chace") == "hello, chace"


def test_hello_accepts_a_name_as_a_keyword():
    assert hello(name="chace") == "hello, chace"


@pytest.mark.parametrize("bad", [None, 1, True, 3.5, ["chace"]])
def test_hello_rejects_a_non_string_name(bad):
    # Fail closed: a non-string must error here, not render a plausible greeting.
    with pytest.raises(TypeError, match="name must be str"):
        hello(bad)


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_hello_rejects_a_blank_name(blank):
    # Same fail-closed rule: "hello, " is not a greeting, it is a silent failure.
    with pytest.raises(ValueError, match="non-empty"):
        hello(blank)
