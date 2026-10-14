import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from hello import hello, hello_many  # noqa: E402


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


def test_hello_many_greets_each_name_in_input_order():
    assert hello_many(["Ada", "Linus"]) == ["hello, Ada", "hello, Linus"]


def test_hello_many_skips_blank_names_without_error():
    assert hello_many(["Ada", "", "   ", "\t\n", "Linus"]) == [
        "hello, Ada",
        "hello, Linus",
    ]


def test_hello_many_keeps_duplicate_names():
    assert hello_many(["Ada", "Ada"]) == ["hello, Ada", "hello, Ada"]


def test_hello_many_returns_empty_list_for_no_names():
    assert hello_many([]) == []


@pytest.mark.parametrize(
    "names", [["Ada", "Linus"], ["Ada", "", "Linus"], ["Ada", "Ada", "  "]]
)
def test_hello_many_matches_a_hand_rolled_loop(names):
    # Each greeting is byte-for-byte hello(n) for the kept names.
    assert hello_many(names) == [hello(n) for n in names if n.strip()]


@pytest.mark.parametrize("bad", [None, 1, True, 3.5, ["chace"]])
def test_hello_many_rejects_a_non_string_entry(bad):
    # Fail closed: a non-string entry propagates hello()'s own TypeError
    # rather than being skipped or coerced.
    with pytest.raises(TypeError, match="name must be str"):
        hello_many(["Ada", bad, "Linus"])


def test_hello_many_rejects_a_bare_string():
    # Fail closed: a bare string would otherwise be greeted letter by letter.
    with pytest.raises(TypeError, match="names must be an iterable of str"):
        hello_many("Ada")
