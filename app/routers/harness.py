from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from ..auth import require_learner, require_user, verify_csrf
from ..config import settings
from ..db import get_conn
from ..errors import NotFoundError
from ..services import harness_service
from ..web import add_flash, form_text, form_text_list, render

router = APIRouter(prefix="/imports/local")


def _require_student(user: dict) -> None:
    require_learner(user, "管理员不参与聊天记录导入")


@router.get("")
def local_index(request: Request, user=Depends(require_user), conn=Depends(get_conn)):
    _require_student(user)
    if not settings.harness_import_enabled:
        raise NotFoundError("本地工具导入未启用")
    sources = harness_service.list_sources(conn, user["id"])
    return render(request, "imports/local.html", sources=sources)


@router.post("/stage")
async def local_stage(request: Request, user=Depends(require_user), conn=Depends(get_conn)):
    _require_student(user)
    if not settings.harness_import_enabled:
        raise NotFoundError("本地工具导入未启用")
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    harness_key = (form_text(form, "harness") or "").strip()
    batch = harness_service.stage(
        conn,
        user=user,
        harness_key=harness_key,
        session_ids=form_text_list(form, "session"),
    )
    add_flash(request, "已读取本地会话，请确认要导入的内容", "info")
    return RedirectResponse(f"/imports/preview/{batch['preview_token']}", status_code=303)
