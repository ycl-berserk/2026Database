from __future__ import annotations

import contextlib
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from ..auth import require_learner, require_user, verify_csrf
from ..db import get_conn
from ..errors import PermissionDenied, ValidationError
from ..services import conversation_service, import_service
from ..web import add_flash, form_text, form_text_list, render

router = APIRouter()
MAX_SHARE_CHARS = 200_000


def _require_student(user: dict) -> None:
    require_learner(user, "管理员不参与聊天记录采集")


def _check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if not origin or origin == "null":
        return
    parsed = urlsplit(origin)
    if parsed.netloc and parsed.netloc != request.url.netloc:
        raise PermissionDenied("分享来源与当前站点不一致")


def _share_text(text: str, url: str) -> str:
    text = (text or "").strip()
    url = (url or "").strip()
    if url and url.lower() not in text.lower():
        text = f"{text}\n\n来源链接：{url}".strip()
    return text


@router.post("/share")
async def share_target(
    request: Request, user=Depends(require_user), conn=Depends(get_conn)
):
    _require_student(user)
    _check_origin(request)
    form = await request.form()
    csrf = form_text(form, "csrf_token")
    if csrf:
        verify_csrf(request, csrf)

    text = _share_text(form_text(form, "text") or "", form_text(form, "url") or "")
    title = (form_text(form, "title") or "").strip()[:255] or None
    platform = (form_text(form, "platform") or "auto").strip().lower()

    def fail(message: str):
        return render(
            request,
            "share/form.html",
            status_code=400,
            error=message,
            draft=text,
            title=title,
            courses=import_service.editable_courses(conn, user["id"]),
            platform=platform,
        )

    if not text:
        return fail("没有收到可保存的文字")
    if len(text) > MAX_SHARE_CHARS:
        return fail(f"分享内容超过 {MAX_SHARE_CHARS:,} 字符上限")

    result, batch = import_service.stage_paste(
        conn,
        user_id=user["id"],
        text=text,
        title=title,
        platform=None if platform in ("", "auto") else platform,
        account_label="share",
        file_name="分享内容",
    )
    if batch is None:
        return fail("；".join(result.errors[:3]) or "没有解析出任何消息")
    return RedirectResponse(f"/share?token={batch['preview_token']}", status_code=303)


@router.get("/share")
def share_page(
    request: Request, user=Depends(require_user), conn=Depends(get_conn)
):
    _require_student(user)
    token = (request.query_params.get("token") or "").strip()
    courses = import_service.editable_courses(conn, user["id"])
    if not token:
        return render(
            request,
            "share/form.html",
            courses=courses,
            platform="auto",
            draft="",
            title="",
        )

    batch = import_service.load_batch(conn, token, user["id"])
    if batch["status"] != "preview":
        return render(
            request,
            "share/form.html",
            status_code=409,
            courses=courses,
            platform="auto",
            draft="",
            title="",
            error="这次分享已经处理或失效，请重新粘贴内容。",
        )
    context = import_service.preview_context(conn, batch, user["id"])
    if not context["rows"]:
        return render(
            request,
            "share/form.html",
            status_code=409,
            courses=courses,
            platform="auto",
            draft="",
            title="",
            error="分享内容已失效，请重新粘贴。",
        )
    row = context["rows"][0]
    return render(
        request,
        "share/form.html",
        courses=courses,
        platform=batch["platform"],
        token=token,
        batch=batch,
        row=row,
        expired=context["expired"],
        warnings=context["warnings"],
    )


@router.post("/share/import")
async def share_import(
    request: Request, user=Depends(require_user), conn=Depends(get_conn)
):
    _require_student(user)
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    token = (form_text(form, "token") or "").strip()
    if not token:
        raise ValidationError("缺少分享预览标识，请重新分享")

    course_id = None
    raw_course = (form_text(form, "course_id") or "").strip()
    if raw_course and raw_course != "none":
        try:
            course_id = int(raw_course)
        except ValueError:
            raise ValidationError("课程选择无效") from None

    batch = import_service.load_batch(conn, token, user["id"])
    payload = batch["payload"] or {}
    conversations = payload.get("conversations") or []
    if len(conversations) != 1:
        raise ValidationError("分享内容不完整，请重新分享")
    external_id = conversations[0]["external_id"]

    result = import_service.confirm_import(
        conn,
        token=token,
        user=user,
        selected={0},
        course_map={0: course_id},
    )
    conversation = conn.execute(
        "SELECT id FROM app.conversations WHERE source_id = %s AND external_id = %s",
        (batch["source_id"], external_id),
    ).fetchone()
    if conversation is None:
        raise ValidationError("内容已导入，但未能定位会话，请到会话列表查看")

    add_flash(
        request,
        f"已收进学习空间：新增 {result['messages_new']} 条消息。",
        "success",
    )
    if course_id is not None:
        return RedirectResponse(
            f"/conversations/{conversation['id']}?captured=1", status_code=303
        )
    return RedirectResponse(
        f"/inbox?highlight={conversation['id']}", status_code=303
    )


@router.get("/inbox")
def inbox(
    request: Request, user=Depends(require_user), conn=Depends(get_conn)
):
    _require_student(user)
    after = request.query_params.get("after")
    before = request.query_params.get("before")
    highlight = None
    with contextlib.suppress(ValueError):
        highlight = int(request.query_params.get("highlight") or 0) or None

    data = conversation_service.list_conversations(
        conn,
        user["id"],
        course_id="none",
        cursor=before or after,
        direction="before" if before else "after",
    )
    courses = conversation_service.editable_courses(conn, user["id"])

    def cursor_url(name: str, value: str | None) -> str | None:
        if not value:
            return None
        pairs = [("highlight", str(highlight))] if highlight else []
        return "/inbox?" + urlencode([*pairs, (name, value)])

    return render(
        request,
        "inbox.html",
        **data,
        courses=courses,
        prev_url=cursor_url("before", data["prev_cursor"]),
        next_url=cursor_url("after", data["next_cursor"]),
        highlight=highlight,
    )


@router.post("/inbox/organize")
async def inbox_organize(
    request: Request, user=Depends(require_user), conn=Depends(get_conn)
):
    _require_student(user)
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    ids: list[int] = []
    for value in form_text_list(form, "selected"):
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    if not ids:
        raise ValidationError("请先选择要归类的会话")
    try:
        course_id = int(form_text(form, "course_id") or "")
    except (TypeError, ValueError):
        raise ValidationError("请选择目标课程") from None

    moved = conversation_service.bulk_organize_inbox(
        conn, ids, user, course_id=course_id
    )
    if moved:
        add_flash(request, f"已把 {moved} 个会话归入课程", "success")
    else:
        add_flash(request, "没有可归类的会话，可能已被整理", "info")
    return RedirectResponse("/inbox", status_code=303)
