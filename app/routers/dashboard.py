from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..auth import require_user
from ..config import settings
from ..db import get_conn
from ..services.dashboard_service import admin_dashboard, student_dashboard
from ..web import render

router = APIRouter()


@router.get("/dashboard")
def dashboard(request: Request, user=Depends(require_user), conn=Depends(get_conn)):
    if user["system_role"] == "admin" and not settings.desktop_mode:
        data = admin_dashboard(conn)
    else:
        data = student_dashboard(conn, user["id"])
        data["is_new_user"] = (
            data["stats"]["course_count"] == 0
            and data["stats"]["conversation_count"] == 0
        )
    return render(request, "dashboard.html", **data)
