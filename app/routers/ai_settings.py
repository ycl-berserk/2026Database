from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ..auth import require_user, verify_csrf
from ..config import settings
from ..db import get_conn
from ..permissions import require_system_admin
from ..services import ai_settings_service
from ..web import add_flash, form_text, render

router = APIRouter(prefix="/settings")


def _payload(form) -> dict:
    return {
        "llm_enabled": form_text(form, "llm_enabled") == "on",
        "llm_base_url": form_text(form, "llm_base_url"),
        "llm_model": form_text(form, "llm_model"),
        "llm_api_key": form_text(form, "llm_api_key"),
        "clear_llm_key": form_text(form, "clear_llm_key") == "on",
        "share_key": form_text(form, "share_key") == "on",
        "embedding_enabled": form_text(form, "embedding_enabled") == "on",
        "embedding_base_url": form_text(form, "embedding_base_url"),
        "embedding_model": form_text(form, "embedding_model"),
        "embedding_dim": form_text(form, "embedding_dim"),
        "embedding_api_key": form_text(form, "embedding_api_key"),
        "clear_embedding_key": form_text(form, "clear_embedding_key") == "on",
    }


@router.get("/ai")
def ai_settings_page(
    request: Request, user=Depends(require_user), conn=Depends(get_conn)
):
    require_system_admin(user)
    return render(
        request,
        "settings/ai.html",
        config=ai_settings_service.public_config(conn),
        env={
            "suggest_mode": settings.suggest_mode,
            "tutor_mode": settings.tutor_mode,
            "text2sql_mode": settings.text2sql_mode,
            "embedding_mode": settings.embedding_mode,
            "hybrid_search": settings.hybrid_search_enabled,
            "llm_base_url": settings.llm_base_url,
            "llm_model": settings.llm_model,
            "embedding_base_url": settings.embedding_base_url,
            "embedding_model": settings.embedding_model,
            "embedding_dim": settings.embedding_dim,
        },
    )


@router.post("/ai")
async def ai_settings_save(
    request: Request, user=Depends(require_user), conn=Depends(get_conn)
):
    require_system_admin(user)
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    ai_settings_service.save(conn, user, _payload(form))
    add_flash(request, "AI 设置已保存，新的请求会立即生效", "success")
    return RedirectResponse("/settings/ai", status_code=303)


@router.post("/ai/test")
async def ai_settings_test(
    request: Request, user=Depends(require_user), conn=Depends(get_conn)
):
    require_system_admin(user)
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    payload = _payload(form)
    target = form_text(form, "target") or "llm"
    if target == "embedding":
        result = await ai_settings_service.test_embedding(conn, payload)
    else:
        result = await ai_settings_service.test_llm(conn, payload)
    return JSONResponse(result)
