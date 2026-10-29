"""首次启动向导：只在桌面模式且尚无任何账号时开放。"""

from __future__ import annotations

import re

import psycopg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..auth import start_session, verify_csrf
from ..bootstrap import is_initialized
from ..config import settings
from ..db import get_conn
from ..errors import ValidationError
from ..services import onboarding_service
from ..services.auth_service import create_user
from ..web import add_flash, form_text, render

router = APIRouter()
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{3,50}$")


def setup_available(conn) -> bool:
    return settings.desktop_mode and not is_initialized(conn)


def _render_form(request: Request, status_code: int = 200, **extra):
    return render(request, "setup.html", status_code=status_code, **extra)


@router.get("/setup")
def setup_page(request: Request, conn=Depends(get_conn)):
    if not settings.desktop_mode:
        raise StarletteHTTPException(status_code=404, detail="Not Found")
    if is_initialized(conn):
        return RedirectResponse("/login", status_code=303)
    return _render_form(request)


@router.post("/setup")
async def setup_submit(request: Request, conn=Depends(get_conn)):
    if not settings.desktop_mode:
        raise StarletteHTTPException(status_code=404, detail="Not Found")
    if is_initialized(conn):
        return RedirectResponse("/login", status_code=303)

    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    username = (form_text(form, "username") or "").strip()
    display_name = (form_text(form, "display_name") or "").strip()
    password = form_text(form, "password") or ""
    confirm = form_text(form, "password_confirm") or ""

    values = {"username": username, "display_name": display_name}
    if not USERNAME_PATTERN.fullmatch(username):
        return _render_form(
            request, 400, error="用户名需为 3–50 位字母、数字或下划线。", **values
        )
    if not display_name or len(display_name) > 100:
        return _render_form(request, 400, error="请填写 1–100 字的显示名称。", **values)
    if len(password) < 8:
        return _render_form(request, 400, error="密码至少 8 位。", **values)
    if password != confirm:
        return _render_form(request, 400, error="两次输入的密码不一致。", **values)

    try:
        user = create_user(
            conn, username, password, display_name, system_role="admin"
        )
    except psycopg.errors.UniqueViolation:
        return _render_form(request, 400, error="这个用户名已被占用。", **values)
    except ValidationError as exc:
        return _render_form(request, 400, error=exc.message, **values)

    user_id = int(user["id"])
    start_session(request, user_id)
    onboarding_service.ensure_started(conn, user_id)
    add_flash(request, "本地账号已创建，已设为管理员。", "success")
    return RedirectResponse("/welcome", status_code=303)
