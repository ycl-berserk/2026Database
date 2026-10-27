from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from ..auth import require_user, verify_csrf
from ..db import get_conn
from ..permissions import require_system_admin
from ..services import admin_service
from ..web import add_flash, render

router = APIRouter(prefix="/admin")


@router.get("")
def admin_page(request: Request, user=Depends(require_user), conn=Depends(get_conn)):
    require_system_admin(user)
    keyword = request.query_params.get("q") or ""
    return render(
        request,
        "admin/index.html",
        stats=admin_service.system_stats(conn),
        users=admin_service.list_users(conn, keyword),
        imports=admin_service.recent_activity(conn),
        q=keyword,
    )


@router.post("/users/{user_id}/toggle")
async def toggle_user(
    user_id: int,
    request: Request,
    user=Depends(require_user),
    conn=Depends(get_conn),
):
    require_system_admin(user)
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))
    active = (form.get("active") or "") == "1"
    row = admin_service.set_active(
        conn, user_id, active, acting_user_id=user["id"]
    )
    add_flash(
        request,
        f"已{'启用' if active else '停用'}账号 {row['username']}",
        "success",
    )
    return RedirectResponse("/admin", status_code=303)
