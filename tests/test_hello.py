from hello import hello  # path set up in tests/conftest.py


def test_hello_defaults_to_world():
    assert hello() == "hello, world"


def test_hello_accepts_a_name():
    assert hello("chace") == "hello, chace"


def test_hello_accepts_a_name_as_a_keyword():
    assert hello(name="chace") == "hello, chace"
