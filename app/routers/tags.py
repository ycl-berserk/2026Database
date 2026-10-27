from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from ..auth import require_user, verify_csrf
from ..db import get_conn
from ..errors import PermissionDenied
from ..services import tag_service
from ..web import add_flash, render

router = APIRouter(prefix="/tags")


def _require_student(user: dict) -> None:
    if user["system_role"] == "admin":
        raise PermissionDenied("管理员不管理课程标签")


@router.get("")
def tag_list(request: Request, user=Depends(require_user), conn=Depends(get_conn)):
    _require_student(user)
    tags = tag_service.list_tags(conn, user["id"])
    return render(request, "tags/list.html", tags=tags, kinds=tag_service.KINDS)


@router.post("")
async def tag_create(request: Request, user=Depends(require_user), conn=Depends(get_conn)):
    _require_student(user)
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))
    tag_service.create_tag(
        conn,
        user["id"],
        form.get("name") or "",
        form.get("kind") or "topic",
        form.get("color") or None,
    )
    add_flash(request, "标签已创建", "success")
    return RedirectResponse("/tags", status_code=303)


@router.post("/{tag_id}/edit")
async def tag_update(
    tag_id: int, request: Request, user=Depends(require_user), conn=Depends(get_conn)
):
    _require_student(user)
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))
    tag_service.update_tag(
        conn,
        tag_id,
        user["id"],
        form.get("name") or "",
        form.get("kind") or "topic",
        form.get("color") or None,
    )
    add_flash(request, "标签已更新", "success")
    return RedirectResponse("/tags", status_code=303)


@router.post("/{tag_id}/delete")
async def tag_delete(
    tag_id: int, request: Request, user=Depends(require_user), conn=Depends(get_conn)
):
    _require_student(user)
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))
    tag_service.delete_tag(conn, tag_id, user["id"])
    add_flash(request, "标签已删除，相关会话标签关联同时移除", "success")
    return RedirectResponse("/tags", status_code=303)
