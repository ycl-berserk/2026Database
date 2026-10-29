from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from ..auth import require_learner, require_user, verify_csrf
from ..db import get_conn
from ..services import review_service
from ..web import add_flash, form_text, render

router = APIRouter(prefix="/reviews")

SCOPES = ("active", "mastered", "all")


def _require_student(user: dict) -> None:
    require_learner(user, "管理员不管理个人复习队列")


def _back(scope: str) -> str:
    return f"/reviews?scope={scope}" if scope in SCOPES else "/reviews"


@router.get("")
def review_list(
    request: Request, user=Depends(require_user), conn=Depends(get_conn)
):
    _require_student(user)
    scope = request.query_params.get("scope") or "active"
    if scope not in SCOPES:
        scope = "active"
    rows = review_service.list_reviews(conn, user["id"], scope)
    activity = review_service.activity(conn, user["id"])
    return render(
        request,
        "reviews/list.html",
        rows=rows,
        scope=scope,
        summary=review_service.summary(conn, user["id"]),
        activity=activity,
        activity_total=sum(row["count"] for row in activity),
    )


@router.post("/{review_id}/done")
async def review_done(
    review_id: int,
    request: Request,
    user=Depends(require_user),
    conn=Depends(get_conn),
):
    _require_student(user)
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    row = review_service.review_once(
        conn,
        review_id,
        user["id"],
        event_id=form_text(form, "event_id"),
        rating=form_text(form, "rating"),
    )
    if row["duplicate"]:
        add_flash(request, "这次复习已经记录过，没有重复累加", "info")
    else:
        label = review_service.RATING_LABELS.get(row["rating"], row["rating"])
        add_flash(
            request,
            f"已记录「{label}」，下次到期 {row['due_date']}（间隔 {row['interval_days']} 天）",
            "success",
        )
    return RedirectResponse(_back(form_text(form, "scope") or ""), status_code=303)


@router.post("/{review_id}/mastered")
async def review_mastered(
    review_id: int,
    request: Request,
    user=Depends(require_user),
    conn=Depends(get_conn),
):
    _require_student(user)
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    row = review_service.mark_mastered(
        conn, review_id, user["id"], event_id=form_text(form, "event_id")
    )
    if row["duplicate"]:
        add_flash(request, "这次操作已经记录过，没有重复提交", "info")
    else:
        add_flash(request, "已标记为掌握，转入「已掌握」列表", "success")
    return RedirectResponse(_back(form_text(form, "scope") or ""), status_code=303)


@router.post("/{review_id}/reset")
async def review_reset(
    review_id: int,
    request: Request,
    user=Depends(require_user),
    conn=Depends(get_conn),
):
    _require_student(user)
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    row = review_service.reset_review(
        conn, review_id, user["id"], event_id=form_text(form, "event_id")
    )
    if row["duplicate"]:
        add_flash(request, "这次操作已经记录过，没有重复提交", "info")
    else:
        add_flash(request, "复习进度已重置，今天重新开始", "success")
    return RedirectResponse(_back(form_text(form, "scope") or ""), status_code=303)


@router.post("/{review_id}/delete")
async def review_delete(
    review_id: int,
    request: Request,
    user=Depends(require_user),
    conn=Depends(get_conn),
):
    _require_student(user)
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    review_service.delete_review(conn, review_id, user["id"])
    add_flash(request, "复习项已移出队列，原批注保留", "success")
    return RedirectResponse(_back(form_text(form, "scope") or ""), status_code=303)
