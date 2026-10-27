from __future__ import annotations

import pytest

from app.auth import ensure_csrf, hash_password, verify_csrf, verify_password
from app.errors import PermissionDenied


class FakeRequest:
    def __init__(self) -> None:
        self.session: dict = {}


def test_hash_and_verify_password():
    stored = hash_password("Passw0rd!")
    assert stored.startswith("pbkdf2_sha256$210000$")
    assert verify_password("Passw0rd!", stored)
    assert not verify_password("passw0rd!", stored)
    assert not verify_password("", stored)


def test_verify_password_rejects_malformed_hash():
    assert not verify_password("x", "plain-text")
    assert not verify_password("x", "md5$1$abc$def")
    assert not verify_password("x", "pbkdf2_sha256$abc$not-b64$also-not")


def test_password_hash_is_salted():
    assert hash_password("same") != hash_password("same")


def test_csrf_roundtrip():
    request = FakeRequest()
    token = ensure_csrf(request)
    assert token == ensure_csrf(request)
    verify_csrf(request, token)
    with pytest.raises(PermissionDenied):
        verify_csrf(request, "wrong")
    with pytest.raises(PermissionDenied):
        verify_csrf(request, None)
    with pytest.raises(PermissionDenied):
        verify_csrf(FakeRequest(), token)
