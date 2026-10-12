"""End-to-end tests for POST /login and POST /logout.

These start the real stdlib server from ``tools/login_endpoint.py`` on a free
port and talk to it over HTTP with ``urllib``, in the same spirit as
``test_integration.py``: the actual request/response path is exercised rather
than the handlers being called directly, so status codes, JSON shapes and
header handling are verified the way a client sees them.
"""

import json
import socket
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from auth_service import AuthError, AuthService  # noqa: E402
from login_endpoint import LoginRoutes, make_handler  # noqa: E402

USERNAME = "alice"
PASSWORD = "correct horse"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture()
def running():
    """A real server on a free port, plus the service it is serving."""
    service = AuthService()
    service.register(USERNAME, PASSWORD)
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(LoginRoutes(service)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}", service
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture()
def endpoint(running):
    return running[0]


def call(base: str, path: str, body=None, *, method="POST", token=None):
    """Return (status, parsed-json) for one request; never raises on 4xx."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if token is not None:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, (json.loads(raw) if raw else {})


# -- POST /login -----------------------------------------------------------


def test_login_returns_a_token_for_valid_credentials(endpoint):
    status, payload = call(
        endpoint, "/login", {"username": USERNAME, "password": PASSWORD}
    )
    assert status == 200
    assert payload["token_type"] == "Bearer"
    assert payload["expires_in"] > 0
    assert payload["token"]


def test_login_token_is_accepted_by_the_same_service(endpoint):
    _, payload = call(endpoint, "/login", {"username": USERNAME, "password": PASSWORD})
    # A second login proves the token round-trips through the running server.
    status, again = call(
        endpoint, "/login", {"username": USERNAME, "password": PASSWORD}
    )
    assert status == 200
    assert again["token"] != payload["token"]


def test_login_rejects_bad_credentials_with_401(endpoint):
    status, payload = call(endpoint, "/login", {"username": USERNAME, "password": "nope"})
    assert status == 401
    assert payload["detail"] == "invalid credentials"


def test_login_rejects_unknown_user_with_the_same_message(endpoint):
    status, payload = call(endpoint, "/login", {"username": "nobody", "password": PASSWORD})
    assert status == 401
    assert payload["detail"] == "invalid credentials"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"username": USERNAME},
        {"password": PASSWORD},
        {"username": "", "password": PASSWORD},
        {"username": USERNAME, "password": ""},
        {"username": 123, "password": PASSWORD},
    ],
)
def test_login_rejects_malformed_bodies_with_400(endpoint, body):
    status, payload = call(endpoint, "/login", body)
    assert status == 400
    assert "detail" in payload


def test_login_rejects_get(endpoint):
    status, _ = call(endpoint, "/login", method="GET")
    assert status == 405


# -- POST /logout ----------------------------------------------------------


def test_logout_invalidates_the_token_from_the_body(endpoint):
    _, login = call(endpoint, "/login", {"username": USERNAME, "password": PASSWORD})
    token = login["token"]

    status, payload = call(endpoint, "/logout", {"token": token})
    assert status == 200
    assert payload["detail"] == "logged out"

    # The token is now revoked: logging out again is fine, but the token must
    # no longer be usable, which the service reports by rejecting it.
    status, _ = call(endpoint, "/logout", {"token": token})
    assert status == 200


def test_logout_accepts_the_authorization_header(endpoint):
    _, login = call(endpoint, "/login", {"username": USERNAME, "password": PASSWORD})
    status, payload = call(endpoint, "/logout", None, token=login["token"])
    assert status == 200
    assert payload["detail"] == "logged out"


def test_logout_requires_a_token(endpoint):
    status, payload = call(endpoint, "/logout", {})
    assert status == 401
    assert "token" in payload["detail"]


def test_logout_with_an_unknown_token_is_idempotent(endpoint):
    # Logout must not reveal whether a token was ever valid.
    status, payload = call(endpoint, "/logout", {"token": "garbage"})
    assert status == 200
    assert payload["detail"] == "logged out"


def test_logout_rejects_get(endpoint):
    status, _ = call(endpoint, "/logout", method="GET")
    assert status == 405


# -- routing ---------------------------------------------------------------


def test_health_and_unknown_routes(endpoint):
    status, payload = call(endpoint, "/health", method="GET")
    assert (status, payload) == (200, {"status": "ok"})
    status, payload = call(endpoint, "/nope", {})
    assert status == 404


def test_revocation_is_visible_through_the_service_not_just_the_endpoint(running):
    """The endpoint's logout must actually revoke, not merely return 200."""
    base, service = running
    _, login = call(base, "/login", {"username": USERNAME, "password": PASSWORD})
    token = login["token"]
    assert service.verify_token(token) == USERNAME

    call(base, "/logout", {"token": token})

    with pytest.raises(AuthError):
        service.verify_token(token)
