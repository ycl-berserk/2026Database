from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from .auth import ensure_csrf
from .config import settings

templates = Jinja2Templates(directory=str(settings.templates_dir))

ROLE_LABELS = {
    "owner": "所有者",
    "editor": "编辑者",
    "viewer": "只读成员",
    "admin": "管理员",
    "student": "学生",
}
KIND_LABELS = {
    "note": "笔记",
    "question": "疑问",
    "correction": "纠错",
    "summary": "总结",
    "knowledge_point": "知识点",
    "topic": "主题",
    "custom": "自定义",
}
STATUS_LABELS = {
    "open": "待解决",
    "resolved": "已解决",
    "pending": "待复习",
    "reviewing": "复习中",
    "mastered": "已掌握",
    "preview": "预览",
    "imported": "已导入",
    "failed": "失败",
    "expired": "已过期",
    "success": "成功",
    "rejected": "已拒绝",
    "error": "失败",
}
SENDER_LABELS = {"user": "我", "assistant": "助手", "system": "系统"}
PLATFORM_LABELS = {
    "qoder": "Qoder",
    "chatgpt": "ChatGPT",
    "deepseek": "DeepSeek",
    "claude": "Claude",
    "other": "其他",
}


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if isinstance(value, datetime):
        return value
    return None


def format_dt(value: Any, fmt: str = "%Y-%m-%d %H:%M") -> str:
    parsed = _as_datetime(value)
    if parsed is None:
        return "—" if value is None else str(value)
    return parsed.strftime(fmt)


def format_date(value: Any, fmt: str = "%Y-%m-%d") -> str:
    parsed = _as_datetime(value)
    if parsed is None:
        return "—" if value is None else str(value)
    return parsed.strftime(fmt)


def format_number(value: Any) -> str:
    if value is None:
        return "0"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def highlight(text: str, keyword: str | None) -> Markup:
    escaped = str(escape(text or ""))
    if not keyword:
        return Markup(escaped)
    needle = str(escape(keyword))
    if not needle:
        return Markup(escaped)
    lowered = escaped.lower()
    lowered_needle = needle.lower()
    out: list[str] = []
    start = 0
    while True:
        pos = lowered.find(lowered_needle, start)
        if pos < 0:
            out.append(escaped[start:])
            break
        out.append(escaped[start:pos])
        out.append(f"<mark>{escaped[pos:pos + len(needle)]}</mark>")
        start = pos + len(needle)
    return Markup("".join(out))


def add_flash(request: Request, message: str, category: str = "info") -> None:
    flashes = request.session.setdefault("flashes", [])
    flashes.append({"message": message, "category": category})


def pop_flashes(request: Request) -> list[dict]:
    return request.session.pop("flashes", [])


def render(
    request: Request,
    template: str,
    status_code: int = 200,
    **context: Any,
):
    context.setdefault("current_user", getattr(request.state, "user", None))
    context.setdefault("csrf_token", ensure_csrf(request))
    context.setdefault("flashes", pop_flashes(request))
    context.setdefault("active_path", request.url.path)
    context.setdefault("today", date.today())
    return templates.TemplateResponse(
        request, template, context, status_code=status_code
    )


templates.env.filters["dt"] = format_dt
templates.env.filters["date"] = format_date
templates.env.filters["num"] = format_number
templates.env.filters["highlight"] = highlight
templates.env.globals["app_name"] = "学习空间"
templates.env.globals["app_tagline"] = "把散落的问答，变成能复习的课程资料"
templates.env.globals["role_label"] = lambda v: ROLE_LABELS.get(v or "", v or "—")
templates.env.globals["kind_label"] = lambda v: KIND_LABELS.get(v or "", v or "—")
templates.env.globals["status_label"] = lambda v: STATUS_LABELS.get(v or "", v or "—")
templates.env.globals["sender_label"] = lambda v: SENDER_LABELS.get(v or "", v or "—")
templates.env.globals["platform_label"] = lambda v: PLATFORM_LABELS.get(v or "", v or "—")
