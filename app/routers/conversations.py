from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from ..auth import require_user, verify_csrf
from ..db import get_conn
from ..errors import PermissionDenied
from ..services import conversation_service
from ..web import add_flash, render

router = APIRouter(prefix="/conversations")


def _require_student(user: dict) -> None:
    if user["system_role"] == "admin":
        raise PermissionDenied("管理员不查看他人会话内容")


def _to_int(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except (TypeError, ValueError):
        return None


@router.get("")
def conversation_list(
    request: Request, user=Depends(require_user), conn=Depends(get_conn)
):
    _require_student(user)
    params = request.query_params
    data = conversation_service.list_conversations(
        conn,
        user["id"],
        q=params.get("q"),
        course_id=params.get("course_id"),
        tag_id=_to_int(params.get("tag_id")),
        date_from=params.get("date_from"),
        date_to=params.get("date_to"),
        page=_to_int(params.get("page")) or 1,
    )
    options = conversation_service.filter_options(conn, user["id"])

    query = [(key, value) for key, value in params.multi_items() if key != "page" and value]
    base = urlencode(query)

    def page_url(number: int) -> str:
        return f"/conversations?{base}&page={number}" if base else f"/conversations?page={number}"

    pages = [
        {"n": number, "url": page_url(number), "active": number == data["page"]}
        for number in range(1, data["total_pages"] + 1)
    ]
    prev_url = page_url(data["page"] - 1) if data["page"] > 1 else None
    next_url = (
        page_url(data["page"] + 1)
        if data["page"] < data["total_pages"]
        else None
    )
    return render(
        request,
        "conversations/list.html",
        **data,
        **options,
        pages=pages,
        prev_url=prev_url,
        next_url=next_url,
    )


@router.get("/{conversation_id}")
def conversation_detail(
    conversation_id: int,
    request: Request,
    user=Depends(require_user),
    conn=Depends(get_conn),
):
    _require_student(user)
    data = conversation_service.conversation_detail(
        conn,
        conversation_id,
        user,
        sender=request.query_params.get("sender"),
        q=request.query_params.get("q"),
    )
    return render(request, "conversations/detail.html", **data)


@router.post("/{conversation_id}/organize")
async def conversation_organize(
    conversation_id: int,
    request: Request,
    user=Depends(require_user),
    conn=Depends(get_conn),
):
    _require_student(user)
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))
    tag_ids: list[int] = []
    for value in form.getlist("tag_ids"):
        try:
            tag_ids.append(int(value))
        except (TypeError, ValueError):
            continue
    conversation_service.organize_conversation(
        conn,
        conversation_id,
        user,
        course_id=form.get("course_id"),
        tag_ids=tag_ids,
    )
    add_flash(request, "会话归类与标签已更新", "success")
    return RedirectResponse(f"/conversations/{conversation_id}", status_code=303)
