from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from ..auth import ensure_csrf, require_user, start_session, verify_csrf
from ..config import settings
from ..db import get_conn
from ..errors import RateLimited
from ..security import login_throttle
from ..services import onboarding_service
from ..services.auth_service import authenticate
from ..web import add_flash, form_text, render

router = APIRouter()


def safe_next(value: str | None) -> str:
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/dashboard"
    if value.startswith("/login"):
        return "/dashboard"
    return value


@router.get("/login")
def login_page(request: Request, conn=Depends(get_conn)):
    if request.session.get("user_id"):
        return RedirectResponse("/dashboard", status_code=303)
    ensure_csrf(request)
    next_url = safe_next(request.query_params.get("next"))
    return render(request, "login.html", next_url=next_url)


@router.post("/login")
async def login_submit(request: Request, conn=Depends(get_conn)):
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    username = (form_text(form, "username") or "").strip()
    password = form_text(form, "password") or ""
    next_url = safe_next(form_text(form, "next"))

    ip = request.client.host if request.client else "unknown"
    wait = login_throttle.retry_after(ip, username)
    if wait:
        raise RateLimited(
            f"登录尝试过于频繁，请在 {wait} 秒后重试。", retry_after=wait
        )

    user = authenticate(conn, username, password)
    if user is None:
        login_throttle.record_failure(ip, username)
        ensure_csrf(request)
        return render(
            request,
            "login.html",
            status_code=401,
            error="用户名或密码错误，或账号已被停用。",
            username=username,
            next_url=next_url,
        )

    login_throttle.reset_user(ip, username)
    start_session(request, user["id"])
    ensure_csrf(request)
    target = next_url
    can_learn = user["system_role"] != "admin" or settings.desktop_mode
    if (
        target == "/dashboard"
        and can_learn
        and onboarding_service.ensure_started(conn, user["id"])
    ):
        target = "/welcome"
    add_flash(request, f"欢迎回来，{user['display_name']}", "success")
    return RedirectResponse(target, status_code=303)


@router.post("/logout")
async def logout(request: Request, user=Depends(require_user)):
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
