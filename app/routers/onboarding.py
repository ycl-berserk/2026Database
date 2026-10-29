from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from ..auth import require_learner, require_user, verify_csrf
from ..db import get_conn
from ..services import onboarding_service
from ..web import add_flash, form_text, render

router = APIRouter()


def _require_learner(user: dict) -> None:
    require_learner(user, "管理员不参与学习引导")


@router.get("/welcome")
def welcome_page(
    request: Request, user=Depends(require_user), conn=Depends(get_conn)
):
    _require_learner(user)
    return render(
        request,
        "welcome.html",
        onboarding=onboarding_service.progress(conn, user["id"]),
    )


@router.post("/welcome/skip")
async def skip_onboarding(
    request: Request, user=Depends(require_user), conn=Depends(get_conn)
):
    _require_learner(user)
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    onboarding_service.mark_skipped(conn, user["id"])
    add_flash(request, "已收起新手指南，可随时从概览继续。", "info")
    return RedirectResponse("/dashboard", status_code=303)


@router.post("/welcome/restart")
async def restart_onboarding(
    request: Request, user=Depends(require_user), conn=Depends(get_conn)
):
    _require_learner(user)
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    onboarding_service.restart(conn, user["id"])
    return RedirectResponse("/welcome", status_code=303)
