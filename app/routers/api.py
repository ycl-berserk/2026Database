from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from ..auth import ensure_csrf, require_user, verify_csrf
from ..db import get_conn
from ..errors import NotFoundError, PermissionDenied, ValidationError
from ..services import annotation_service, conversation_service, review_service

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
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
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
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "csrf_token": ensure_csrf(request),
        "items": jsonable_encoder(items),
    }
    return JSONResponse(payload)


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
