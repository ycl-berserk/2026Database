from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from ..auth import require_user
from ..db import get_conn
from ..errors import PermissionDenied
from ..services import conversation_service

router = APIRouter(prefix="/api")


def _require_student(user: dict) -> None:
    if user["system_role"] == "admin":
        raise PermissionDenied("管理员不阅读他人消息正文")


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
