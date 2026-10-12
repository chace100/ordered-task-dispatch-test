#!/usr/bin/env python3
"""Login endpoints: `POST /login` and `POST /logout` over the auth service.

Issue #5 asks for the two endpoints on top of the auth service from #4. The
service lives in :mod:`auth_service`; this module is the HTTP-shaped seam
between it and a caller.

The handlers are deliberately transport-agnostic: :func:`post_login` and
:func:`post_logout` take a parsed request body and return ``(status, payload)``,
so the endpoint contract is testable without a socket, and wiring them to any
framework is a two-line adapter. :class:`LoginRoutes` is a ``http.server``
adapter for the stdlib, used by the tests and runnable directly:

    python tools/login_endpoint.py --port 8080

``login_endpoint`` is **not** an application server -- there is no application in
this repository, and issues #2/#3 (schema, request logging) delivered no
persistence or logging layer either. It is the endpoint layer the issue asked
for, kept minimal and dependency-free on purpose.

Request/response contract
-------------------------
``POST /login``   body ``{"username": ..., "password": ...}``
    * 200 -> ``{"token": ..., "token_type": "Bearer", "expires_in": <seconds>}``
    * 400 -> malformed/missing fields
    * 401 -> bad credentials (never says which field was wrong)
``POST /logout``  body ``{"token": ...}``, or header ``Authorization: Bearer <t>``
    * 200 -> ``{"detail": "logged out"}``  (idempotent)
    * 401 -> missing token
``GET  /health``  -> 200 ``{"status": "ok"}``
"""

from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from auth_service import AuthError, AuthService

LOGIN_PATH = "/login"
LOGOUT_PATH = "/logout"
HEALTH_PATH = "/health"

_BEARER_RE = re.compile(r"^Bearer\s+(?P<token>\S+)$", re.IGNORECASE)


def _token_from_header(header: str | None) -> str | None:
    """Extract a bearer token from an Authorization header, if present."""
    if not header:
        return None
    match = _BEARER_RE.match(header.strip())
    return match.group("token") if match else None


def post_login(service: AuthService, body) -> tuple[int, dict]:
    """Handle ``POST /login``.

    Returns ``(status, payload)``. A malformed body is a 400; bad credentials
    are a 401 with a generic message.
    """
    if not isinstance(body, dict):
        return 400, {"detail": "request body must be a JSON object"}
    username = body.get("username")
    password = body.get("password")
    if not isinstance(username, str) or not username:
        return 400, {"detail": "username is required"}
    if not isinstance(password, str) or not password:
        return 400, {"detail": "password is required"}

    try:
        token = service.login(username, password)
    except AuthError:
        # Same message for unknown user and wrong password: the caller learns
        # only that the credentials were rejected.
        return 401, {"detail": "invalid credentials"}

    return 200, {
        "token": token,
        "token_type": "Bearer",
        "expires_in": int(service.token_ttl),
    }


def post_logout(service: AuthService, body, authorization: str | None = None) -> tuple[int, dict]:
    """Handle ``POST /logout``.

    The token may come from the body or the ``Authorization`` header. Logout is
    idempotent -- an unknown or already-revoked token still returns 200, so a
    client retrying a logout is not told anything about token validity.
    """
    token = None
    if isinstance(body, dict):
        candidate = body.get("token")
        if isinstance(candidate, str) and candidate:
            token = candidate
    if token is None:
        token = _token_from_header(authorization)

    if not token:
        return 401, {"detail": "a token is required"}

    service.revoke_token(token)
    return 200, {"detail": "logged out"}


class LoginRoutes:
    """Bind the two handlers to an :class:`AuthService`."""

    def __init__(self, service: AuthService):
        self.service = service

    def dispatch(
        self, method: str, path: str, body, authorization: str | None = None
    ) -> tuple[int, dict]:
        """Route one request. Unknown routes are a 404, wrong methods a 405."""
        if path == HEALTH_PATH:
            if method != "GET":
                return 405, {"detail": "method not allowed"}
            return 200, {"status": "ok"}
        if path == LOGIN_PATH:
            if method != "POST":
                return 405, {"detail": "method not allowed"}
            return post_login(self.service, body)
        if path == LOGOUT_PATH:
            if method != "POST":
                return 405, {"detail": "method not allowed"}
            return post_logout(self.service, body, authorization)
        return 404, {"detail": "not found"}


def make_handler(routes: LoginRoutes):
    """Build a ``BaseHTTPRequestHandler`` subclass serving ``routes``."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _read_body(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return None
            raw = self.rfile.read(length)
            try:
                return json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                # A non-JSON body is a bad request, not a server error; let the
                # handler reject it through the normal 400 path.
                return raw.decode("utf-8", "replace")

        def _respond(self, status: int, payload: dict):
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _handle(self, method: str):
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            self._respond(
                *routes.dispatch(
                    method, path, self._read_body(), self.headers.get("Authorization")
                )
            )

        def do_GET(self):  # noqa: N802 - stdlib naming
            self._handle("GET")

        def do_POST(self):  # noqa: N802 - stdlib naming
            self._handle("POST")

        def log_message(self, *args):  # keep the test output quiet
            return

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(argv)

    service = AuthService()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(LoginRoutes(service)))
    print(f"login endpoints on http://{args.host}:{args.port} (POST /login, POST /logout)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
