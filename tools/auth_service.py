#!/usr/bin/env python3
"""Auth service: password hashing, token issuance and verification.

This is the surface issue #4 ("Implement auth service") specified -- hashing,
token issuance and verification -- which was closed without delivering any code.
Issue #5 wires the ``POST /login`` and ``POST /logout`` endpoints onto it, so
the minimum that makes those endpoints real lives here.

Stdlib only, like the rest of ``tools/``: no external dependency and no network
call, so the credential and token logic stays unit-testable as pure functions.

Design notes
------------
* Passwords are stored as PBKDF2-HMAC-SHA256 with a per-user random salt and a
  high iteration count, never in plaintext.
* Comparisons of secrets use ``hmac.compare_digest`` so they do not leak timing
  information.
* Tokens are HMAC-SHA256 signed over a compact JSON payload carrying the
  subject, issue time, expiry and a unique id. ``verify_token`` checks the
  signature in constant time and then the expiry; ``revoke_token`` adds the
  token's id to a revocation set, which is what makes ``POST /logout``
  meaningful while the token has not expired yet.
* Failures raise ``AuthError`` with a message that is deliberately generic, so
  a caller cannot tell "no such user" from "wrong password".
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field

# PBKDF2 cost. High enough to be a real hurdle, low enough to keep tests quick.
PBKDF2_ITERATIONS = 200_000
SALT_BYTES = 16
DERIVED_KEY_BYTES = 32
HASH_ALGORITHM = "pbkdf2_sha256"

# Token lifetime, in seconds.
TOKEN_TTL_SECONDS = 3600


class AuthError(Exception):
    """Raised when credentials or a token are rejected.

    The message is intentionally non-specific: callers must not be able to
    distinguish an unknown user from a bad password.
    """


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    """Derive a storable password hash.

    Returns ``pbkdf2_sha256$<iterations>$<salt>$<hash>`` so the parameters used
    at hashing time travel with the value and can be raised later without
    invalidating existing records.
    """
    if not isinstance(password, str) or not password:
        raise AuthError("invalid credentials")
    salt = secrets.token_bytes(SALT_BYTES) if salt is None else salt
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS, DERIVED_KEY_BYTES
    )
    return "$".join(
        (HASH_ALGORITHM, str(PBKDF2_ITERATIONS), _b64encode(salt), _b64encode(derived))
    )


def verify_password(password: str, encoded: str) -> bool:
    """Check a plaintext password against a value from :func:`hash_password`.

    Returns ``False`` rather than raising on a malformed record: a corrupt row
    is a failed login, not a crash in the request path.
    """
    if not isinstance(password, str) or not password or not isinstance(encoded, str):
        return False
    try:
        algorithm, iterations, salt_b64, expected_b64 = encoded.split("$")
        if algorithm != HASH_ALGORITHM:
            return False
        expected = _b64decode(expected_b64)
        derived = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            _b64decode(salt_b64),
            int(iterations),
            len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(derived, expected)


@dataclass
class UserStore:
    """In-memory user records, keyed by username.

    Issue #2 ("Set up database schema") was also closed without delivering the
    users/sessions tables, so there is no persistence layer to use. This keeps
    the endpoints runnable and testable; swapping in a real store means
    implementing the same two methods.
    """

    _passwords: dict[str, str] = field(default_factory=dict)

    def register(self, username: str, password: str) -> None:
        if not isinstance(username, str) or not username:
            raise AuthError("invalid credentials")
        self._passwords[username] = hash_password(password)

    def password_hash(self, username: str) -> str | None:
        return self._passwords.get(username)


@dataclass
class AuthService:
    """Hashing, token issuance and verification.

    ``signing_key`` defaults to a fresh random key per process. Pass an explicit
    key to have tokens survive a restart or be verifiable across processes.
    """

    signing_key: bytes = field(default_factory=lambda: secrets.token_bytes(32))
    token_ttl: int = TOKEN_TTL_SECONDS
    store: UserStore = field(default_factory=UserStore)
    _revoked: set[str] = field(default_factory=set)

    # -- users ------------------------------------------------------------
    def register(self, username: str, password: str) -> None:
        self.store.register(username, password)

    def _authenticate(self, username: str, password: str) -> None:
        """Raise ``AuthError`` unless the credentials are valid.

        The unknown-user path still runs a hash derivation, so response time
        does not reveal whether the account exists.
        """
        encoded = self.store.password_hash(username)
        if encoded is None:
            hash_password(password or "x")
            raise AuthError("invalid credentials")
        if not verify_password(password, encoded):
            raise AuthError("invalid credentials")

    # -- tokens -----------------------------------------------------------
    def _sign(self, payload: str) -> str:
        signature = hmac.new(
            self.signing_key, payload.encode("ascii"), hashlib.sha256
        ).digest()
        return _b64encode(signature)

    def issue_token(self, username: str, *, now: float | None = None) -> str:
        """Issue a signed, expiring token for an authenticated user."""
        issued_at = int(time.time() if now is None else now)
        payload = {
            "sub": username,
            "iat": issued_at,
            "exp": issued_at + int(self.token_ttl),
            "jti": secrets.token_urlsafe(16),
        }
        body = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        return f"{body}.{self._sign(body)}"

    def _decode(self, token: str) -> dict:
        if not isinstance(token, str) or token.count(".") != 1:
            raise AuthError("invalid token")
        body, signature = token.split(".", 1)
        expected = self._sign(body)
        # Constant-time: a byte-by-byte early exit would leak the signature.
        if not hmac.compare_digest(signature, expected):
            raise AuthError("invalid token")
        try:
            payload = json.loads(_b64decode(body))
        except (ValueError, TypeError):
            raise AuthError("invalid token") from None
        if not isinstance(payload, dict) or "sub" not in payload:
            raise AuthError("invalid token")
        return payload

    def verify_token(self, token: str, *, now: float | None = None) -> str:
        """Return the username a token belongs to, or raise ``AuthError``.

        Rejects a bad signature, an expired token, and a token that ``logout``
        has revoked.
        """
        payload = self._decode(token)
        if payload.get("jti") in self._revoked:
            raise AuthError("invalid token")
        expires_at = payload.get("exp")
        if not isinstance(expires_at, int):
            raise AuthError("invalid token")
        if int(time.time() if now is None else now) >= expires_at:
            raise AuthError("invalid token")
        return payload["sub"]

    def revoke_token(self, token: str) -> None:
        """Invalidate a token before its natural expiry (used by logout).

        Revoking an already-invalid token is not an error: logout must be
        idempotent, and telling a caller their token was never valid would be a
        needless information leak.
        """
        try:
            payload = self._decode(token)
        except AuthError:
            return
        jti = payload.get("jti")
        if isinstance(jti, str):
            self._revoked.add(jti)

    # -- login ------------------------------------------------------------
    def login(self, username: str, password: str, *, now: float | None = None) -> str:
        """Authenticate and return a fresh token, or raise ``AuthError``."""
        self._authenticate(username, password)
        return self.issue_token(username, now=now)
