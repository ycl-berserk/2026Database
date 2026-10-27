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

    @property
    def is_dev(self) -> bool:
        return self.app_env != "production"

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
