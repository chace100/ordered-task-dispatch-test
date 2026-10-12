import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import pytest  # noqa: E402

from auth_service import AuthError, AuthService, hash_password, verify_password  # noqa: E402


def service_with_user(username="alice", password="correct horse"):
    service = AuthService()
    service.register(username, password)
    return service


# -- hashing ---------------------------------------------------------------


def test_password_hash_is_not_the_plaintext_and_verifies():
    encoded = hash_password("hunter2")
    assert "hunter2" not in encoded
    assert encoded.startswith("pbkdf2_sha256$")
    assert verify_password("hunter2", encoded) is True


def test_password_hash_is_salted_per_call():
    # Two hashes of the same password must differ, or the store leaks equality.
    assert hash_password("hunter2") != hash_password("hunter2")


def test_verify_password_rejects_wrong_and_malformed_input():
    encoded = hash_password("hunter2")
    assert verify_password("hunter3", encoded) is False
    assert verify_password("", encoded) is False
    assert verify_password("hunter2", "not-a-valid-record") is False
    assert verify_password("hunter2", "md5$1$c2FsdA$aGFzaA") is False
    assert verify_password("hunter2", None) is False


def test_empty_password_cannot_be_hashed():
    with pytest.raises(AuthError):
        hash_password("")


# -- login -----------------------------------------------------------------


def test_login_issues_a_token_that_verifies_to_the_user():
    service = service_with_user()
    token = service.login("alice", "correct horse")
    assert service.verify_token(token) == "alice"


def test_login_rejects_bad_credentials():
    service = service_with_user()
    with pytest.raises(AuthError):
        service.login("alice", "wrong")
    with pytest.raises(AuthError):
        service.login("nobody", "correct horse")


def test_login_error_message_does_not_reveal_which_field_failed():
    service = service_with_user()
    with pytest.raises(AuthError) as unknown_user:
        service.login("nobody", "correct horse")
    with pytest.raises(AuthError) as wrong_password:
        service.login("alice", "wrong")
    assert str(unknown_user.value) == str(wrong_password.value)


# -- token verification ----------------------------------------------------


def test_verify_token_rejects_tampering_and_foreign_keys():
    service = service_with_user()
    token = service.login("alice", "correct horse")
    body, signature = token.split(".")

    # A flipped signature must not verify.
    with pytest.raises(AuthError):
        service.verify_token(f"{body}.{signature[:-2]}xx")
    # A token from a different signing key must not verify.
    other = service_with_user()
    with pytest.raises(AuthError):
        service.verify_token(other.login("alice", "correct horse"))
    # Structural garbage must not verify.
    for bad in ("", "no-dot", "a.b.c", "!!!.???"):
        with pytest.raises(AuthError):
            service.verify_token(bad)


def test_verify_token_rejects_expired_token():
    service = AuthService(token_ttl=60)
    service.register("alice", "correct horse")
    token = service.login("alice", "correct horse", now=1_000)
    assert service.verify_token(token, now=1_059) == "alice"
    with pytest.raises(AuthError):
        service.verify_token(token, now=1_060)


# -- logout ----------------------------------------------------------------


def test_logout_revokes_the_token():
    service = service_with_user()
    token = service.login("alice", "correct horse")
    assert service.verify_token(token) == "alice"

    service.revoke_token(token)

    # Revoked before expiry, so it must no longer verify.
    with pytest.raises(AuthError):
        service.verify_token(token)


def test_logout_is_idempotent_and_ignores_invalid_tokens():
    service = service_with_user()
    token = service.login("alice", "correct horse")
    service.revoke_token(token)
    service.revoke_token(token)  # second logout must not raise
    service.revoke_token("garbage")  # nor must an invalid one
    with pytest.raises(AuthError):
        service.verify_token(token)


def test_logout_does_not_revoke_other_sessions():
    service = service_with_user()
    first = service.login("alice", "correct horse")
    second = service.login("alice", "correct horse")

    service.revoke_token(first)

    with pytest.raises(AuthError):
        service.verify_token(first)
    assert service.verify_token(second) == "alice"


def test_tokens_for_different_users_are_distinct():
    service = AuthService()
    service.register("alice", "pw-alice")
    service.register("bob", "pw-bob")
    alice = service.login("alice", "pw-alice")
    bob = service.login("bob", "pw-bob")
    assert alice != bob
    assert service.verify_token(alice) == "alice"
    assert service.verify_token(bob) == "bob"
