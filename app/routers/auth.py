from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from ..auth import ensure_csrf, require_user, verify_csrf
from ..db import get_conn
from ..services.auth_service import authenticate
from ..web import add_flash, render

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
    verify_csrf(request, form.get("csrf_token"))
    username = (form.get("username") or "").strip()
    password = form.get("password") or ""
    next_url = safe_next(form.get("next"))

    user = authenticate(conn, username, password)
    if user is None:
        ensure_csrf(request)
        return render(
            request,
            "login.html",
            status_code=401,
            error="用户名或密码错误，或账号已被停用。",
            username=username,
            next_url=next_url,
        )

    request.session.clear()
    request.session["user_id"] = user["id"]
    ensure_csrf(request)
    add_flash(request, f"欢迎回来，{user['display_name']}", "success")
    return RedirectResponse(next_url, status_code=303)


@router.post("/logout")
async def logout(request: Request, user=Depends(require_user)):
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
