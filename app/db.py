from __future__ import annotations

from collections.abc import Iterator

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import settings

main_pool = ConnectionPool(
    settings.database_url,
    min_size=1,
    max_size=8,
    open=False,
    timeout=10,
    kwargs={"row_factory": dict_row},
)

t2s_pool = ConnectionPool(
    settings.t2s_database_url,
    min_size=1,
    max_size=4,
    open=False,
    timeout=10,
    kwargs={"row_factory": dict_row},
)


def get_conn() -> Iterator[object]:
    with main_pool.connection() as conn:
        yield conn


def get_t2s_conn() -> Iterator[object]:
    with t2s_pool.connection() as conn:
        yield conn


def check_main_db() -> bool:
    with main_pool.connection() as conn:
        conn.execute("SELECT 1")
    return True


def check_t2s_db() -> bool:
    with t2s_pool.connection() as conn:
        conn.execute("SELECT 1")
    return True
