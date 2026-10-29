from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from ..auth import require_learner, require_user, verify_csrf
from ..db import get_conn
from ..services import annotation_service
from ..web import add_flash, form_text, render

router = APIRouter(prefix="/annotations")


def _require_student(user: dict) -> None:
    require_learner(user, "管理员不阅读他人消息正文与批注")


def _back(form) -> str:
    value = form_text(form, "back") or ""
    if value.startswith("/conversations/") and "//" not in value:
        return value
    return "/annotations"


@router.get("")
def annotation_list(
    request: Request, user=Depends(require_user), conn=Depends(get_conn)
):
    _require_student(user)
    params = request.query_params
    page = 1
    try:
        page = int(params.get("page") or 1)
    except ValueError:
        page = 1
    data = annotation_service.list_annotations(
        conn,
        user["id"],
        status=params.get("status"),
        kind=params.get("kind"),
        page=page,
    )
    pages = [
        {
            "n": number,
            "url": _page_url(data, number),
            "active": number == data["page"],
        }
        for number in range(1, data["total_pages"] + 1)
    ]
    return render(
        request,
        "annotations/list.html",
        rows=data["rows"],
        total=data["total"],
        page=data["page"],
        page_size=data["page_size"],
        total_pages=data["total_pages"],
        status_filter=data["status"],
        kind_filter=data["kind"],
        kinds=annotation_service.KINDS,
        statuses=annotation_service.STATUSES,
        pages=pages,
        prev_url=_page_url(data, data["page"] - 1) if data["page"] > 1 else None,
        next_url=(
            _page_url(data, data["page"] + 1)
            if data["page"] < data["total_pages"]
            else None
        ),
    )


def _page_url(data: dict, number: int) -> str:
    parts = []
    if data["status"]:
        parts.append(f"status={data['status']}")
    if data["kind"]:
        parts.append(f"kind={data['kind']}")
    parts.append(f"page={number}")
    return "/annotations?" + "&".join(parts)


@router.post("")
async def annotation_create(
    request: Request, user=Depends(require_user), conn=Depends(get_conn)
):
    _require_student(user)
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    try:
        message_id = int(form_text(form, "message_id") or 0)
    except ValueError:
        message_id = 0
    annotation_service.create_annotation(
        conn,
        message_id,
        user,
        form_text(form, "kind") or "note",
        form_text(form, "content") or "",
    )
    add_flash(request, "批注已添加", "success")
    return RedirectResponse(_back(form), status_code=303)


@router.post("/{annotation_id}/edit")
async def annotation_update(
    annotation_id: int,
    request: Request,
    user=Depends(require_user),
    conn=Depends(get_conn),
):
    _require_student(user)
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    annotation_service.update_annotation(
        conn,
        annotation_id,
        user,
        form_text(form, "kind") or "note",
        form_text(form, "content") or "",
        form_text(form, "status") or "open",
    )
    add_flash(request, "批注已更新", "success")
    return RedirectResponse(_back(form), status_code=303)


@router.post("/{annotation_id}/delete")
async def annotation_delete(
    annotation_id: int,
    request: Request,
    user=Depends(require_user),
    conn=Depends(get_conn),
):
    _require_student(user)
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    annotation_service.delete_annotation(conn, annotation_id, user)
    add_flash(request, "批注已删除，对应复习项一并移除", "success")
    return RedirectResponse(_back(form), status_code=303)


@router.post("/{annotation_id}/review")
async def annotation_review(
    annotation_id: int,
    request: Request,
    user=Depends(require_user),
    conn=Depends(get_conn),
):
    _require_student(user)
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    created = annotation_service.add_to_review(conn, annotation_id, user)
    if created:
        add_flash(request, "已加入复习队列，今天到期", "success")
    else:
        add_flash(request, "该批注已在复习队列中", "info")
    return RedirectResponse(_back(form), status_code=303)
