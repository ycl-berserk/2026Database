from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from ..auth import require_learner, require_user, verify_csrf
from ..db import get_conn
from ..errors import AppError
from ..services import tag_graph_service, tag_service
from ..web import add_flash, form_text, render

router = APIRouter(prefix="/tags")


def _require_student(user: dict) -> None:
    require_learner(user, "管理员不管理课程标签")


@router.get("/graph")
def tag_graph(
    request: Request,
    tag_id: int | None = None,
    user=Depends(require_user),
    conn=Depends(get_conn),
):
    _require_student(user)
    data = tag_graph_service.graph(conn, user["id"], tag_id)
    return render(request, "tags/graph.html", **data, selected_tag_id=tag_id)


@router.post("/graph/edges")
async def edge_create(request: Request, user=Depends(require_user), conn=Depends(get_conn)):
    _require_student(user)
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    try:
        from_tag_id = int(form_text(form, "from_tag_id") or 0)
        to_tag_id = int(form_text(form, "to_tag_id") or 0)
    except ValueError:
        add_flash(request, "请选择两个有效标签", "error")
        return RedirectResponse("/tags/graph", status_code=303)
    try:
        if not from_tag_id or not to_tag_id:
            raise AppError("请选择先修标签和后继标签")
        tag_graph_service.add_edge(conn, user["id"], from_tag_id, to_tag_id)
    except AppError as exc:
        add_flash(request, exc.message, "error")
        return RedirectResponse("/tags/graph", status_code=303)
    add_flash(request, "先修关系已添加", "success")
    return RedirectResponse(f"/tags/graph?tag_id={to_tag_id}", status_code=303)


@router.post("/graph/edges/{edge_id}/delete")
async def edge_delete(
    edge_id: int, request: Request, user=Depends(require_user), conn=Depends(get_conn)
):
    _require_student(user)
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    try:
        tag_graph_service.delete_edge(conn, user["id"], edge_id)
    except AppError as exc:
        add_flash(request, exc.message, "error")
    else:
        add_flash(request, "先修关系已删除", "success")
    return RedirectResponse("/tags/graph", status_code=303)


@router.get("")
def tag_list(request: Request, user=Depends(require_user), conn=Depends(get_conn)):
    _require_student(user)
    tags = tag_service.list_tags(conn, user["id"])
    return render(request, "tags/list.html", tags=tags, kinds=tag_service.KINDS)


@router.post("")
async def tag_create(request: Request, user=Depends(require_user), conn=Depends(get_conn)):
    _require_student(user)
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    tag_service.create_tag(
        conn,
        user["id"],
        form_text(form, "name") or "",
        form_text(form, "kind") or "topic",
        form_text(form, "color") or None,
    )
    add_flash(request, "标签已创建", "success")
    return RedirectResponse("/tags", status_code=303)


@router.post("/{tag_id}/edit")
async def tag_update(
    tag_id: int, request: Request, user=Depends(require_user), conn=Depends(get_conn)
):
    _require_student(user)
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    tag_service.update_tag(
        conn,
        tag_id,
        user["id"],
        form_text(form, "name") or "",
        form_text(form, "kind") or "topic",
        form_text(form, "color") or None,
    )
    add_flash(request, "标签已更新", "success")
    return RedirectResponse("/tags", status_code=303)


@router.post("/{tag_id}/delete")
async def tag_delete(
    tag_id: int, request: Request, user=Depends(require_user), conn=Depends(get_conn)
):
    _require_student(user)
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    tag_service.delete_tag(conn, tag_id, user["id"])
    add_flash(request, "标签已删除，相关会话标签关联同时移除", "success")
    return RedirectResponse("/tags", status_code=303)
