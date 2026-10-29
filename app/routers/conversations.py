from __future__ import annotations

import re
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from ..auth import require_learner, require_user, verify_csrf
from ..db import get_conn
from ..services import ai_service, conversation_service
from ..web import add_flash, form_text, form_text_list, render

router = APIRouter(prefix="/conversations")


def _require_student(user: dict) -> None:
    require_learner(user, "管理员不查看他人会话内容")


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
    after = params.get("after")
    before = params.get("before")
    data = conversation_service.list_conversations(
        conn,
        user["id"],
        q=params.get("q"),
        course_id=params.get("course_id"),
        tag_id=_to_int(params.get("tag_id")),
        date_from=params.get("date_from"),
        date_to=params.get("date_to"),
        page=_to_int(params.get("page")) or 1,
        cursor=before or after,
        direction="before" if before else "after",
    )
    options = conversation_service.filter_options(conn, user["id"])

    filters = [
        (key, value)
        for key, value in params.multi_items()
        if key not in ("page", "after", "before") and value
    ]

    def cursor_url(name: str, value: str | None) -> str | None:
        if not value:
            return None
        return "/conversations?" + urlencode([*filters, (name, value)])

    return render(
        request,
        "conversations/list.html",
        **data,
        **options,
        prev_url=cursor_url("before", data["prev_cursor"]),
        next_url=cursor_url("after", data["next_cursor"]),
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
    return render(
        request,
        "conversations/detail.html",
        **data,
        ai_enabled=ai_service.enabled(),
    )


@router.post("/{conversation_id}/organize")
async def conversation_organize(
    conversation_id: int,
    request: Request,
    user=Depends(require_user),
    conn=Depends(get_conn),
):
    _require_student(user)
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    tag_ids: list[int] = []
    for value in form_text_list(form, "tag_ids"):
        try:
            tag_ids.append(int(value))
        except (TypeError, ValueError):
            continue
    new_tags = [
        part.strip()
        for part in re.split(r"[,，\n]+", str(form_text(form, "new_tags") or ""))
        if part.strip()
    ]
    conversation_service.organize_conversation(
        conn,
        conversation_id,
        user,
        course_id=form_text(form, "course_id"),
        tag_ids=tag_ids,
        new_tag_names=new_tags,
        summary=str(form_text(form, "summary") or ""),
    )
    add_flash(request, "归类、标签与摘要已保存", "success")
    return RedirectResponse(f"/conversations/{conversation_id}", status_code=303)
