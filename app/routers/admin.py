from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from ..auth import require_user, verify_csrf
from ..db import get_conn
from ..permissions import require_system_admin
from ..services import admin_service, observability_service
from ..web import add_flash, render

router = APIRouter(prefix="/admin")


@router.get("")
def admin_page(request: Request, user=Depends(require_user), conn=Depends(get_conn)):
    require_system_admin(user)
    keyword = request.query_params.get("q") or ""
    audit_table = request.query_params.get("audit_table") or ""
    audit_action = request.query_params.get("audit_action") or ""
    return render(
        request,
        "admin/index.html",
        stats=admin_service.system_stats(conn),
        users=admin_service.list_users(conn, keyword),
        imports=admin_service.recent_activity(conn),
        audit=admin_service.audit_events(
            conn, table=audit_table, action=audit_action
        ),
        audit_summary=admin_service.audit_summary(conn),
        audit_table=audit_table,
        audit_action=audit_action,
        q=keyword,
    )


@router.get("/observability")
def observability_page(
    request: Request,
    user=Depends(require_user),
    conn=Depends(get_conn),
):
    require_system_admin(user)
    plan_key = request.query_params.get("plan") or ""
    return render(
        request,
        "admin/observability.html",
        overview=observability_service.overview(conn),
        io=observability_service.io_stats(conn),
        tables=observability_service.table_activity(conn),
        indexes=observability_service.index_usage(conn),
        slow=observability_service.slow_queries(conn),
        plan=observability_service.explain_plan(conn, plan_key),
        plan_choices=[
            (key, choice["label"])
            for key, choice in observability_service.PLAN_CHOICES.items()
        ],
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


@router.post("/audit/prune")
async def prune_audit(
    request: Request,
    user=Depends(require_user),
    conn=Depends(get_conn),
):
    require_system_admin(user)
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))
    removed = admin_service.prune_audit(conn, 90)
    add_flash(request, f"已清理 {removed} 条超过 90 天的审计记录", "success")
    return RedirectResponse("/admin", status_code=303)
