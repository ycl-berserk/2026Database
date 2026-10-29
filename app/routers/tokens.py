from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from ..auth import require_learner, require_user, verify_csrf
from ..db import get_conn
from ..errors import AppError
from ..services import token_service
from ..web import add_flash, form_text, render

router = APIRouter(prefix="/tokens")


def _require_student(user: dict) -> None:
    require_learner(user, "管理员不管理采集令牌")


def _render_page(request, conn, user, *, new_token=None, error=None, status_code=200):
    return render(
        request,
        "tokens/list.html",
        status_code=status_code,
        tokens=token_service.list_tokens(conn, user["id"]),
        new_token=new_token,
        error=error,
        rate_limit=token_service.RATE_LIMIT_PER_MINUTE,
    )


@router.get("")
def token_list(request: Request, user=Depends(require_user), conn=Depends(get_conn)):
    _require_student(user)
    return _render_page(request, conn, user)


@router.post("")
async def token_create(
    request: Request, user=Depends(require_user), conn=Depends(get_conn)
):
    _require_student(user)
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    scopes = list(token_service.DEFAULT_SCOPES)
    if form_text(form, "allow_annotate"):
        scopes.append("annotate")
    try:
        created = token_service.create_token(
            conn, user["id"], form_text(form, "name") or "", scopes
        )
    except AppError as exc:
        return _render_page(
            request, conn, user, error=exc.message, status_code=exc.status_code
        )
    return _render_page(
        request, conn, user, new_token=created["token"], status_code=201
    )


@router.post("/{token_id}/revoke")
async def token_revoke(
    token_id: int,
    request: Request,
    user=Depends(require_user),
    conn=Depends(get_conn),
):
    _require_student(user)
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    token_service.revoke_token(conn, token_id, user["id"])
    add_flash(request, "令牌已撤销，正在使用它的脚本或扩展会立即失效", "success")
    return RedirectResponse("/tokens", status_code=303)
