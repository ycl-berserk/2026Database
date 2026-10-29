from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Settings, env_flag
from app.security import ensure_production_settings


def test_env_flag_accepts_common_truthy_values(monkeypatch):
    monkeypatch.setenv("LS_FLAG_TEST", "YES")
    assert env_flag("LS_FLAG_TEST") is True
    monkeypatch.setenv("LS_FLAG_TEST", "off")
    assert env_flag("LS_FLAG_TEST") is False
    monkeypatch.delenv("LS_FLAG_TEST")
    assert env_flag("LS_FLAG_TEST", default=True) is True


def test_desktop_production_allows_plain_http_only_when_both_flags_set():
    desktop_http = Settings(
        app_env="production",
        desktop_mode=True,
        allow_insecure_http=True,
    )
    assert desktop_http.insecure_http_allowed is True
    assert desktop_http.cookie_secure is False
    assert desktop_http.hsts_enabled is False

    desktop_https = Settings(
        app_env="production",
        desktop_mode=True,
        allow_insecure_http=False,
    )
    assert desktop_https.cookie_secure is True

    plain = Settings(
        app_env="production",
        desktop_mode=False,
        allow_insecure_http=True,
    )
    assert plain.insecure_http_allowed is False
    assert plain.cookie_secure is True


def test_explicit_secure_cookie_wins_over_desktop_exception():
    config = Settings(
        app_env="production",
        desktop_mode=True,
        allow_insecure_http=True,
        session_cookie_secure=True,
    )
    assert config.cookie_secure is True


def test_insecure_http_flag_cannot_bypass_non_desktop_checks():
    config = SimpleNamespace(
        is_dev=True,
        session_secret="x" * 40,
        allow_insecure_http=True,
        desktop_mode=False,
    )
    with pytest.raises(RuntimeError):
        ensure_production_settings(config)


def test_desktop_secret_still_required_in_production_mode():
    config = SimpleNamespace(
        is_dev=False,
        session_secret="short",
        allow_insecure_http=True,
        desktop_mode=True,
    )
    with pytest.raises(RuntimeError):
        ensure_production_settings(config)


def test_runtime_dirs_derive_from_data_dir(tmp_path):
    config = Settings(data_dir=tmp_path)
    assert config.log_dir == tmp_path / "logs"
    assert config.backup_dir == tmp_path / "backups"
    assert config.bind_host == "127.0.0.1"


@pytest.fixture
def restore_environment():
    snapshot = dict(os.environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(snapshot)


def test_configure_environment_generates_secrets_once(tmp_path, restore_environment):
    from app.desktop import configure_environment

    first = configure_environment(tmp_path / "home")
    env_file = Path(first["env_file"])
    assert env_file.is_file()
    assert (tmp_path / "home" / "pgdata").is_dir()
    assert (tmp_path / "home" / "logs").is_dir()
    assert (tmp_path / "home" / "backups").is_dir()
    assert (tmp_path / "home" / "run").is_dir()

    body = env_file.read_text(encoding="utf-8")
    assert "SESSION_SECRET=" in body
    assert "ADMIN_DATABASE_URL=" in body
    assert first["pg_port"] == 55432
    assert first["app_port"] == 8765

    password = first["app_password"]
    second = configure_environment(tmp_path / "home")
    assert second["app_password"] == password
    assert second["admin_url"] == first["admin_url"]
    assert env_file.read_text(encoding="utf-8") == body

    if os.name != "nt":
        assert env_file.stat().st_mode & 0o777 == 0o600


def test_configure_environment_honours_explicit_ports(tmp_path, restore_environment):
    from app.desktop import configure_environment

    cfg = configure_environment(tmp_path / "home2", pg_port=56123, app_port=8899)
    assert cfg["pg_port"] == 56123
    assert cfg["app_port"] == 8899
    assert ":56123/" in str(cfg["database_url"])
    assert os.environ["LS_PORT"] == "8899"
    assert os.environ["LS_DESKTOP_MODE"] == "1"
    assert os.environ["LS_ALLOW_INSECURE_HTTP"] == "1"


def test_backup_due_only_outside_today(tmp_path):
    from app.desktop import backup_due

    assert backup_due(tmp_path) is True
    dump = tmp_path / "learning-space-20260101-000000.dump"
    dump.write_bytes(b"x")
    os.utime(dump, (0, 0))
    assert backup_due(tmp_path) is True

    fresh = tmp_path / "learning-space-20260101-000001.dump"
    fresh.write_bytes(b"x")
    assert backup_due(tmp_path) is False
