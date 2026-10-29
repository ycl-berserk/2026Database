from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _default_app_root() -> Path:
    if getattr(sys, "frozen", False):
        # PyInstaller onedir 把数据放在 _internal，也就是 sys._MEIPASS。
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


BASE_DIR = Path(os.getenv("LS_APP_ROOT") or _default_app_root()).resolve()
ENV_FILE = Path(os.getenv("LS_ENV_FILE") or BASE_DIR / ".env")
load_dotenv(ENV_FILE)


@dataclass(frozen=True)
class Settings:
    base_dir: Path = BASE_DIR
    database_url: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL",
            "postgresql://ls_app:ls_app_dev_pw@127.0.0.1:5433/learning_space",
        )
    )
    t2s_database_url: str = field(
        default_factory=lambda: os.getenv(
            "T2S_DATABASE_URL",
            "postgresql://t2s_reader:t2s_dev_pw@127.0.0.1:5433/learning_space",
        )
    )
    admin_database_url: str = field(
        default_factory=lambda: os.getenv(
            "ADMIN_DATABASE_URL",
            "postgresql://postgres:postgres@127.0.0.1:5433/postgres",
        )
    )
    test_database_url: str = field(
        default_factory=lambda: os.getenv(
            "TEST_DATABASE_URL",
            "postgresql://ls_app:ls_app_dev_pw@127.0.0.1:5433/learning_space_test",
        )
    )
    session_secret: str = field(
        default_factory=lambda: os.getenv(
            "SESSION_SECRET", "dev-only-change-me-please-32-chars"
        )
    )
    app_env: str = field(default_factory=lambda: os.getenv("APP_ENV", "dev"))
    desktop_mode: bool = field(default_factory=lambda: env_flag("LS_DESKTOP_MODE"))
    bind_host: str = field(
        default_factory=lambda: os.getenv("LS_BIND_HOST", "127.0.0.1").strip()
        or "127.0.0.1"
    )
    allow_insecure_http: bool = field(
        default_factory=lambda: env_flag("LS_ALLOW_INSECURE_HTTP")
    )
    data_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("LS_DATA_DIR") or BASE_DIR / "data"
        ).resolve()
    )
    session_max_age: int = field(
        default_factory=lambda: int(os.getenv("SESSION_MAX_AGE", str(60 * 60 * 24 * 7)))
    )
    session_cookie_secure: bool = field(
        default_factory=lambda: os.getenv("SESSION_COOKIE_SECURE", "").lower()
        in ("1", "true", "yes", "on")
    )
    login_max_attempts: int = field(
        default_factory=lambda: int(os.getenv("LOGIN_MAX_ATTEMPTS", "5"))
    )
    login_ip_max_attempts: int = field(
        default_factory=lambda: int(os.getenv("LOGIN_IP_MAX_ATTEMPTS", "20"))
    )
    login_window_seconds: int = field(
        default_factory=lambda: int(os.getenv("LOGIN_WINDOW_SECONDS", "900"))
    )

    text2sql_mode: str = field(default_factory=lambda: os.getenv("TEXT2SQL_MODE", "auto"))
    llm_base_url: str = field(
        default_factory=lambda: os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
    )
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "deepseek-chat"))

    import_max_bytes: int = 5 * 1024 * 1024
    import_max_messages: int = 2000
    preview_ttl_minutes: int = 30
    text2sql_row_limit: int = 200
    text2sql_timeout_ms: int = 3000
    text2sql_preflight_enabled: bool = field(
        default_factory=lambda: os.getenv("TEXT2SQL_PREFLIGHT", "1").lower()
        not in ("0", "false", "off")
    )
    text2sql_preflight_timeout_ms: int = 1000
    text2sql_max_cost: float = field(
        default_factory=lambda: float(os.getenv("TEXT2SQL_MAX_COST", "500000"))
    )
    text2sql_max_est_rows: int = field(
        default_factory=lambda: int(os.getenv("TEXT2SQL_MAX_EST_ROWS", "200000"))
    )

    embedding_mode: str = field(
        default_factory=lambda: os.getenv("EMBEDDING_MODE", "auto").lower()
    )
    embedding_base_url: str = field(
        default_factory=lambda: os.getenv("EMBEDDING_BASE_URL")
        or os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
    )
    embedding_api_key: str = field(
        default_factory=lambda: os.getenv("EMBEDDING_API_KEY")
        or os.getenv("LLM_API_KEY", "")
    )
    embedding_model: str = field(
        default_factory=lambda: os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    )
    embedding_dim: int = field(
        default_factory=lambda: int(os.getenv("EMBEDDING_DIM", "1536"))
    )
    hybrid_search_enabled: bool = field(
        default_factory=lambda: os.getenv("HYBRID_SEARCH", "1").lower()
        not in ("0", "false", "off")
    )
    hybrid_rrf_k: int = 60
    hybrid_candidate_limit: int = 100

    suggest_mode: str = field(
        default_factory=lambda: os.getenv("SUGGEST_MODE", "auto").lower()
    )
    review_algorithm: str = field(
        default_factory=lambda: os.getenv("REVIEW_ALGORITHM", "fsrs").lower()
    )
    review_retention: float = field(
        default_factory=lambda: float(os.getenv("REVIEW_RETENTION", "0.9"))
    )

    @property
    def is_dev(self) -> bool:
        return self.app_env != "production"

    @property
    def insecure_http_allowed(self) -> bool:
        # 只有桌面模式显式开启时才允许明文 HTTP；普通部署仍强制 Secure/HSTS。
        return self.desktop_mode and self.allow_insecure_http

    @property
    def cookie_secure(self) -> bool:
        if self.session_cookie_secure:
            return True
        return not self.is_dev and not self.insecure_http_allowed

    @property
    def hsts_enabled(self) -> bool:
        return self.cookie_secure

    @property
    def static_dir(self) -> Path:
        return self.base_dir / "app" / "static"

    @property
    def templates_dir(self) -> Path:
        return self.base_dir / "app" / "templates"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"


settings = Settings()
