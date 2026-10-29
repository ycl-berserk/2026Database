from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ..auth import require_learner, require_user, verify_csrf
from ..db import get_conn, get_t2s_conn
from ..errors import ValidationError
from ..services import text2sql_service
from ..web import render

router = APIRouter()

EXAMPLES = [
    "每个课程有多少会话和消息？",
    "列举消息最多的课程",
    "哪些复习项今天到期？",
    "列出所有未解决的批注",
    "按类型统计我的批注数量",
    "使用最多的标签有哪些？",
    "按月查看我的学习活跃度",
    "数据库课程下最近有哪些会话？",
    "对比各课程的提问与回答数量",
    "列出最近 10 次导入记录",
]


def _require_student(user: dict) -> None:
    require_learner(user, "管理员可使用系统统计页，不进入个人 Text2SQL")


@router.get("/text2sql")
def text2sql_page(
    request: Request, user=Depends(require_user), conn=Depends(get_conn)
):
    _require_student(user)
    mode = text2sql_service.provider_mode()
    return render(
        request,
        "text2sql/index.html",
        examples=EXAMPLES,
        mode=mode,
        mode_label=(
            "大模型模式"
            if mode == "llm"
            else "本地规则模式（未配置大模型 Key）"
        ),
        history=text2sql_service.history(conn, user["id"]),
    )


@router.post("/api/text2sql")
async def text2sql_query(
    request: Request,
    user=Depends(require_user),
    conn=Depends(get_conn),
    t2s_conn=Depends(get_t2s_conn),
):
    _require_student(user)
    verify_csrf(request, request.headers.get("X-CSRF-Token"))
    try:
        payload = await request.json()
    except ValueError as exc:
        raise ValidationError("请求体必须是 JSON") from exc
    question = str(payload.get("question") or "").strip()
    if not question:
        raise ValidationError("请输入问题")
    if len(question) > 300:
        raise ValidationError("问题不能超过 300 个字符")

    result = await text2sql_service.run(
        question, user["id"], t2s_conn, conn
    )
    return JSONResponse(result)
