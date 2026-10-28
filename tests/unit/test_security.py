from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import Settings
from app.security import (
    AttemptWindow,
    LoginThrottle,
    ensure_production_settings,
    session_expired,
)


def test_attempt_window_locks_until_oldest_failure_expires():
    window = AttemptWindow(limit=2, window_seconds=60)
    assert window.retry_after("k", now=100) == 0
    window.record("k", now=100)
    window.record("k", now=110)
    assert window.retry_after("k", now=110) == 50
    assert window.retry_after("k", now=161) == 0
    window.reset("k")
    assert window.retry_after("k", now=110) == 0


def test_login_throttle_separates_user_and_ip_limits():
    throttle = LoginThrottle(per_user=2, per_ip=3, window_seconds=60)
    throttle.record_failure("1.2.3.4", "Alice", now=0)
    throttle.record_failure("1.2.3.4", "alice", now=1)
    assert throttle.retry_after("1.2.3.4", "ALICE", now=1) == 59
    assert throttle.retry_after("1.2.3.4", "bob", now=1) == 0

    throttle.reset_user("1.2.3.4", "alice")
    assert throttle.retry_after("1.2.3.4", "alice", now=1) == 0

    for index, name in enumerate(("u1", "u2", "u3")):
        throttle.record_failure("5.6.7.8", name, now=index)
    assert throttle.retry_after("5.6.7.8", "other", now=2) == 58
    throttle.reset()
    assert throttle.retry_after("5.6.7.8", "other", now=2) == 0


def test_session_expiry_requires_issued_at():
    assert session_expired({}, 100, now=10)
    assert not session_expired({"issued_at": 10}, 100, now=50)
    assert session_expired({"issued_at": 10}, 100, now=111)


def test_production_requires_random_session_secret():
    default = SimpleNamespace(
        is_dev=False, session_secret="dev-only-change-me-please-32-chars"
    )
    short = SimpleNamespace(is_dev=False, session_secret="short")
    strong = SimpleNamespace(is_dev=False, session_secret="x" * 40)
    dev = SimpleNamespace(is_dev=True, session_secret="dev-only-change-me-please-32-chars")

    for config in (default, short):
        with pytest.raises(RuntimeError):
            ensure_production_settings(config)
    ensure_production_settings(strong)
    ensure_production_settings(dev)


def test_cookie_secure_defaults_to_production_only():
    assert Settings(app_env="dev", session_cookie_secure=False).cookie_secure is False
    assert Settings(app_env="production", session_cookie_secure=False).cookie_secure is True
    assert Settings(app_env="dev", session_cookie_secure=True).cookie_secure is True


def test_security_headers_on_public_response():
    from fastapi.testclient import TestClient

    from app.main import app

    response = TestClient(app).get("/offline")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "camera=()" in response.headers["permissions-policy"]
