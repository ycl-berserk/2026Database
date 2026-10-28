from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.auth import require_user
from app.db import get_conn
from app.errors import AppError
from app.routers import admin as admin_router
from app.services import observability_service


def _client(conn, user: dict) -> TestClient:
    app = FastAPI()
    app.include_router(admin_router.router)
    app.dependency_overrides[get_conn] = lambda: conn
    app.dependency_overrides[require_user] = lambda: user

    @app.exception_handler(AppError)
    async def handle_app_error(_request, exc: AppError):
        return JSONResponse({"detail": exc.message}, status_code=exc.status_code)

    return TestClient(app)


def test_overview_and_io(conn):
    stats = observability_service.overview(conn)
    assert stats["datname"].endswith("_test")
    assert stats["size_bytes"] > 0
    if stats["cache_hit_pct"] is not None:
        assert 0 <= stats["cache_hit_pct"] <= 100
    assert stats["max_connections"] >= stats["numbackends"] >= 1

    io_rows = observability_service.io_stats(conn)
    assert io_rows
    assert {"backend_type", "reads", "writes", "hits"} <= set(io_rows[0])


def test_tables_and_indexes(conn):
    tables = observability_service.table_activity(conn)
    names = {row["relname"] for row in tables}
    assert {"messages", "conversations", "annotations"} <= names

    indexes = observability_service.index_usage(conn)
    assert indexes
    assert {"relname", "indexrelname", "idx_scan", "size_bytes"} <= set(indexes[0])


def test_explain_plan_all_choices(conn):
    for key in observability_service.PLAN_CHOICES:
        plan = observability_service.explain_plan(conn, key)
        assert plan["key"] == key
        assert plan["rows"]
        root = plan["rows"][0]
        assert root["depth"] == 0
        assert root["node_type"]
        assert plan["sql"] == observability_service.PLAN_CHOICES[key]["sql"]

    fallback = observability_service.explain_plan(conn, "not-a-plan")
    assert fallback["key"] == next(iter(observability_service.PLAN_CHOICES))


def test_slow_queries_structure(conn):
    result = observability_service.slow_queries(conn)
    assert set(result) == {"available", "reason", "rows"}
    assert isinstance(result["rows"], list)
    if result["available"]:
        assert result["reason"] is None


def test_observability_requires_admin(conn, base):
    student = {"id": base["users"]["alice"], "system_role": "student"}
    response = _client(conn, student).get("/admin/observability")
    assert response.status_code == 403
