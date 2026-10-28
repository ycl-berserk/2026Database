from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from ..auth import ensure_csrf, require_scope, require_token, require_user, verify_csrf
from ..config import settings
from ..db import get_conn
from ..errors import NotFoundError, PermissionDenied, ValidationError
from ..services import (
    ai_service,
    annotation_service,
    conversation_service,
    import_service,
    review_service,
    search_service,
)

router = APIRouter(prefix="/api")

REVIEW_ACTIONS = ("done", "mastered", "reset")


def _require_student(user: dict) -> None:
    if user["system_role"] == "admin":
        raise PermissionDenied("管理员不阅读他人消息正文")


async def _json_body(request: Request) -> dict:
    try:
        payload = await request.json()
    except ValueError as exc:
        raise ValidationError("请求体必须是 JSON") from exc
    if not isinstance(payload, dict):
        raise ValidationError("请求体必须是 JSON 对象")
    return payload


@router.post("/conversations/{conversation_id}/suggest")
async def suggest_conversation(
    conversation_id: int,
    request: Request,
    user=Depends(require_user),
    conn=Depends(get_conn),
):
    _require_student(user)
    verify_csrf(request, request.headers.get("X-CSRF-Token"))
    data = await ai_service.suggest(conn, conversation_id, user)
    return JSONResponse(data)


@router.get("/conversations/{conversation_id}/offline")
def offline_conversation(
    conversation_id: int,
    user=Depends(require_user),
    conn=Depends(get_conn),
):
    """显式导出当前用户可读的会话快照，供浏览器 IndexedDB 离线阅读。"""
    _require_student(user)
    data = conversation_service.conversation_detail(conn, conversation_id, user)
    conversation = data["conversation"]
    payload = {
        "saved_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "conversation": {
            "id": conversation["id"],
            "title": conversation["title"],
            "course_id": conversation["course_id"],
            "course_code": conversation["course_code"],
            "course_name": conversation["course_name"],
            "platform": conversation["platform"],
            "account_label": conversation["account_label"],
            "started_at": conversation["started_at"],
            "ended_at": conversation["ended_at"],
        },
        "counts": data["counts"],
        "tags": data["tags"],
        "messages": data["messages"],
    }
    return JSONResponse(jsonable_encoder(payload))


@router.get("/reviews/offline")
def offline_reviews(
    request: Request,
    user=Depends(require_user),
    conn=Depends(get_conn),
):
    """缓存一份待复习队列，离线时仍可打卡，联网后由 outbox 补交。"""
    _require_student(user)
    items = review_service.list_reviews(conn, user["id"], "active")[:60]
    payload = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "csrf_token": ensure_csrf(request),
        "items": jsonable_encoder(items),
    }
    return JSONResponse(payload)


@router.get("/search")
def search(
    q: str = "",
    user=Depends(require_token),
    conn=Depends(get_conn),
):
    """采集令牌可读的全局搜索，供命令行与外部只读客户端调用。"""
    _require_student(user)
    require_scope(user, "read")
    query = (q or "").strip()
    if not query:
        raise ValidationError("请输入搜索关键词")
    data = search_service.global_search(conn, user, query)
    return JSONResponse(jsonable_encoder(data))


@router.post("/ingest")
async def ingest(
    request: Request,
    user=Depends(require_token),
    conn=Depends(get_conn),
):
    """用采集令牌一次性写入统一 JSON；脚本与浏览器扩展的入口。"""
    _require_student(user)
    require_scope(user, "ingest")
    raw = await request.body()
    if len(raw) > settings.import_max_bytes:
        limit = settings.import_max_bytes // (1024 * 1024)
        raise ValidationError(f"单次导入不能超过 {limit} MB")
    try:
        document = json.loads(raw.decode("utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise ValidationError("文件不是有效的 UTF-8 文本") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(
            f"JSON 解析失败：第 {exc.lineno} 行第 {exc.colno} 列，{exc.msg}"
        ) from exc
    if not isinstance(document, dict):
        raise ValidationError("请求体必须是包含 platform 与 conversations 的 JSON 对象")

    course_id = None
    raw_course = document.get("course_id")
    if raw_course not in (None, ""):
        try:
            course_id = int(raw_course)
        except (TypeError, ValueError) as exc:
            raise ValidationError("课程标识无效") from exc

    result = import_service.ingest_json(
        conn, user=user, document=document, course_id=course_id
    )
    return JSONResponse(jsonable_encoder({"ok": True, **result}))


@router.post("/annotations")
async def create_annotation(
    request: Request,
    user=Depends(require_user),
    conn=Depends(get_conn),
):
    """接收离线批注草稿；同一 event_id 重放不会产生重复批注。"""
    _require_student(user)
    verify_csrf(request, request.headers.get("x-csrf-token"))
    payload = await _json_body(request)
    try:
        message_id = int(payload.get("message_id") or 0)
    except (TypeError, ValueError) as exc:
        raise ValidationError("消息标识无效") from exc
    row = annotation_service.create_annotation(
        conn,
        message_id,
        user,
        payload.get("kind") or "note",
        payload.get("content") or "",
        event_id=payload.get("event_id"),
    )
    return JSONResponse(jsonable_encoder({"ok": True, "annotation": row}))


@router.post("/reviews/{review_id}/{action}")
async def submit_review_action(
    review_id: int,
    action: str,
    request: Request,
    user=Depends(require_user),
    conn=Depends(get_conn),
):
    """复习 outbox 的提交入口；event_id 保证断网重放只生效一次。"""
    _require_student(user)
    if action not in REVIEW_ACTIONS:
        raise NotFoundError("不支持的复习操作")
    verify_csrf(request, request.headers.get("x-csrf-token"))
    payload = await _json_body(request)
    event_id = payload.get("event_id")
    if action == "done":
        row = review_service.review_once(conn, review_id, user["id"], event_id)
    elif action == "mastered":
        row = review_service.mark_mastered(conn, review_id, user["id"], event_id)
    else:
        row = review_service.reset_review(conn, review_id, user["id"], event_id)
    return JSONResponse(jsonable_encoder({"ok": True, "item": row}))
