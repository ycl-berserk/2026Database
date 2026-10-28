from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


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
    def cookie_secure(self) -> bool:
        return self.session_cookie_secure or not self.is_dev

    @property
    def static_dir(self) -> Path:
        return self.base_dir / "app" / "static"

    @property
    def templates_dir(self) -> Path:
        return self.base_dir / "app" / "templates"

    @property
    def data_dir(self) -> Path:
        return self.base_dir / "data"


settings = Settings()
