from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from starlette.datastructures import FormData, UploadFile

from .auth import ensure_csrf
from .config import settings

templates = Jinja2Templates(directory=str(settings.templates_dir))


def form_text(form: FormData, name: str, default: str | None = None) -> str | None:
    value = form.get(name)
    return value if isinstance(value, str) else default


def form_file(form: FormData, name: str) -> UploadFile | None:
    value = form.get(name)
    return value if isinstance(value, UploadFile) else None


def form_text_list(form: FormData, name: str) -> list[str]:
    return [value for value in form.getlist(name) if isinstance(value, str)]


def _asset_version() -> str:
    files = (settings.static_dir / "app.css", settings.static_dir / "app.js")
    newest = max(int(path.stat().st_mtime) for path in files if path.exists())
    return format(newest, "x")

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
AUDIT_TABLE_LABELS = {
    "courses": "课程",
    "course_members": "课程成员",
    "users": "用户账号",
}
AUDIT_ACTION_LABELS = {"insert": "新增", "update": "修改", "delete": "删除"}
PLATFORM_LABELS = {
    "qoder": "Qoder",
    "chatgpt": "ChatGPT",
    "deepseek": "DeepSeek",
    "claude": "Claude",
    "codex": "Codex",
    "opencode": "opencode",
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


def format_bytes(value: Any) -> str:
    if value is None:
        return "—"
    try:
        size = float(value)
    except (TypeError, ValueError):
        return str(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


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
    current_user = getattr(request.state, "user", None)
    context.setdefault("current_user", current_user)
    context.setdefault(
        "admin_user",
        bool(current_user) and current_user["system_role"] == "admin",
    )
    context.setdefault(
        "can_learn",
        bool(current_user)
        and (current_user["system_role"] != "admin" or settings.desktop_mode),
    )
    context.setdefault("csrf_token", ensure_csrf(request))
    context.setdefault("flashes", pop_flashes(request))
    context.setdefault("active_path", request.url.path)
    context.setdefault("today", date.today())
    context.setdefault("demo_mode", settings.is_dev)
    return templates.TemplateResponse(
        request, template, context, status_code=status_code
    )


templates.env.filters["dt"] = format_dt
templates.env.filters["date"] = format_date
templates.env.filters["num"] = format_number
templates.env.filters["bytes"] = format_bytes
templates.env.filters["highlight"] = highlight
templates.env.globals["app_name"] = "学习空间"
templates.env.globals["app_tagline"] = "把散落的问答，变成能复习的课程资料"
templates.env.globals["asset_version"] = _asset_version()
templates.env.globals["role_label"] = lambda v: ROLE_LABELS.get(v or "", v or "—")
templates.env.globals["kind_label"] = lambda v: KIND_LABELS.get(v or "", v or "—")
templates.env.globals["status_label"] = lambda v: STATUS_LABELS.get(v or "", v or "—")
templates.env.globals["sender_label"] = lambda v: SENDER_LABELS.get(v or "", v or "—")
templates.env.globals["platform_label"] = lambda v: PLATFORM_LABELS.get(v or "", v or "—")
templates.env.globals["audit_table_label"] = lambda v: AUDIT_TABLE_LABELS.get(v or "", v or "—")
templates.env.globals["audit_action_label"] = lambda v: AUDIT_ACTION_LABELS.get(v or "", v or "—")
