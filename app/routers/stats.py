from __future__ import annotations

from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from ..auth import require_user
from ..db import get_conn
from ..errors import PermissionDenied
from ..services import export_service, stats_service
from ..web import render

router = APIRouter()


def _require_student(user: dict) -> None:
    if user["system_role"] == "admin":
        raise PermissionDenied("管理员仅查看系统聚合统计，不进入个人统计页")


def _attachment(filename: str) -> str:
    return f"attachment; filename*=UTF-8''{quote(filename)}"


@router.get("/stats")
def stats_page(request: Request, user=Depends(require_user), conn=Depends(get_conn)):
    _require_student(user)
    data = stats_service.load_stats(conn, user["id"])
    return render(request, "stats/index.html", **data)


@router.get("/exports/stats.csv")
def stats_csv(user=Depends(require_user), conn=Depends(get_conn)):
    _require_student(user)
    data = stats_service.load_stats(conn, user["id"])
    body = stats_service.stats_csv(data)
    filename = f"learning-space-stats-{date.today().isoformat()}.csv"
    return Response(
        body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": _attachment(filename)},
    )


@router.get("/exports/conversations/{conversation_id}.md")
def conversation_markdown(
    conversation_id: int, user=Depends(require_user), conn=Depends(get_conn)
):
    _require_student(user)
    filename, body = export_service.conversation_markdown(
        conn, conversation_id, user
    )
    return Response(
        body.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": _attachment(filename)},
    )
