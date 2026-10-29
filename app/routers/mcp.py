from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from ..auth import require_learner, require_scope, require_token
from ..db import get_conn
from ..errors import ValidationError
from ..services import (
    annotation_service,
    conversation_service,
    review_service,
    search_service,
)

router = APIRouter(prefix="/api/mcp")

ANNOTATION_KINDS = ("note", "question", "correction", "summary")


def _require_student(user: dict) -> None:
    require_learner(user, "管理员不阅读他人消息正文")


def _limit(value: int, maximum: int) -> int:
    return max(1, min(int(value), maximum))


async def _json_body(request: Request) -> dict:
    try:
        payload = await request.json()
    except ValueError as exc:
        raise ValidationError("请求体必须是 JSON") from exc
    if not isinstance(payload, dict):
        raise ValidationError("请求体必须是 JSON 对象")
    return payload


@router.get("/me")
def me(user=Depends(require_token)):
    """MCP 客户端在启动时读取令牌作用域，据此隐藏无权使用的工具。"""
    _require_student(user)
    return {
        "user_id": user["id"],
        "display_name": user["display_name"],
        "scopes": sorted(user.get("scopes") or ()),
    }


@router.get("/search")
def search(
    q: str = "",
    limit: int = 10,
    user=Depends(require_token),
    conn=Depends(get_conn),
):
    _require_student(user)
    require_scope(user, "read")
    query = (q or "").strip()
    if not query:
        raise ValidationError("请输入搜索关键词")
    data = search_service.global_search(conn, user, query)
    rows = data["conversations"]["rows"][: _limit(limit, 50)]
    payload = {
        "query": data["q"],
        "retrieval": data["retrieval"],
        "total": data["conversations"]["total"],
        "conversations": [
            {
                "id": row["id"],
                "title": row["title"],
                "course_code": row.get("course_code"),
                "platform": row["platform"],
                "started_at": row.get("started_at"),
                "message_count": row["message_count"],
                "snippet": row.get("snippet"),
            }
            for row in rows
        ],
        "annotations": [
            {
                "id": row["id"],
                "kind": row["kind"],
                "content": row["content"],
                "snippet": row.get("snippet"),
                "conversation_id": row["conversation_id"],
                "conversation_title": row["conversation_title"],
            }
            for row in data["annotations"]
        ],
        "courses": data["courses"],
        "tags": data["tags"],
    }
    return JSONResponse(jsonable_encoder(payload))


@router.get("/conversations/{conversation_id}")
def conversation(
    conversation_id: int,
    user=Depends(require_token),
    conn=Depends(get_conn),
):
    _require_student(user)
    require_scope(user, "read")
    data = conversation_service.conversation_detail(conn, conversation_id, user)
    conversation_row = data["conversation"]
    payload = {
        "conversation": {
            "id": conversation_row["id"],
            "title": conversation_row["title"],
            "course_id": conversation_row["course_id"],
            "course_code": conversation_row["course_code"],
            "course_name": conversation_row["course_name"],
            "platform": conversation_row["platform"],
            "started_at": conversation_row["started_at"],
            "ended_at": conversation_row["ended_at"],
        },
        "counts": data["counts"],
        "tags": data["tags"],
        "messages": [
            {
                "id": row["id"],
                "seq_no": row["seq_no"],
                "sender": row["sender"],
                "content": row["content"],
                "created_at": row["created_at"],
                "annotations": [
                    {
                        "id": item["id"],
                        "kind": item["kind"],
                        "content": item["content"],
                        "status": item["status"],
                        "author": item["display_name"],
                    }
                    for item in row["annotations"]
                ],
            }
            for row in data["messages"]
        ],
    }
    return JSONResponse(jsonable_encoder(payload))


@router.get("/reviews/due")
def due_reviews(
    limit: int = 20,
    user=Depends(require_token),
    conn=Depends(get_conn),
):
    _require_student(user)
    require_scope(user, "read")
    items = review_service.list_reviews(conn, user["id"], "active")
    due = [item for item in items if item["is_due"]][: _limit(limit, 100)]
    return JSONResponse(
        jsonable_encoder(
            {
                "total": sum(1 for item in items if item["is_due"]),
                "items": [
                    {
                        "id": item["id"],
                        "annotation_id": item["annotation_id"],
                        "annotation_kind": item["kind"],
                        "annotation_content": item["annotation_content"],
                        "message_id": item["message_id"],
                        "seq_no": item["seq_no"],
                        "conversation_id": item["conversation_id"],
                        "conversation_title": item["conversation_title"],
                        "course_code": item["course_code"],
                        "due_date": item["due_date"],
                        "interval_days": item["interval_days"],
                        "review_count": item["review_count"],
                        "is_overdue": item["is_overdue"],
                        "snippet": item["snippet"],
                    }
                    for item in due
                ],
            }
        )
    )


@router.post("/annotations")
async def create_annotation(
    request: Request,
    user=Depends(require_token),
    conn=Depends(get_conn),
):
    """MCP 写工具：需要令牌显式授予 annotate 作用域。"""
    _require_student(user)
    require_scope(user, "annotate")
    payload = await _json_body(request)
    try:
        message_id = int(payload.get("message_id") or 0)
    except (TypeError, ValueError) as exc:
        raise ValidationError("消息标识无效") from exc
    kind = payload.get("kind") or "note"
    if kind not in ANNOTATION_KINDS:
        raise ValidationError("批注类型无效")
    content = str(payload.get("content") or "").strip()
    if not content:
        raise ValidationError("批注内容不能为空")
    row = annotation_service.create_annotation(
        conn,
        message_id,
        user,
        kind,
        content,
        event_id=payload.get("event_id"),
    )
    return JSONResponse(jsonable_encoder({"ok": True, "annotation": row}))
