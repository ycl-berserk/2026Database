from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from ..auth import require_learner, require_user, verify_csrf
from ..db import get_conn
from ..services import tutor_service
from ..web import add_flash, form_text, render

router = APIRouter(prefix="/tutor")


def _require_student(user: dict) -> None:
    require_learner(user, "管理员不使用个人 AI 学习会话")


def _to_int(value) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


@router.get("")
def tutor_index(
    request: Request, user=Depends(require_user), conn=Depends(get_conn)
):
    _require_student(user)
    courses = tutor_service.accessible_courses(conn, user["id"])
    requested = _to_int(request.query_params.get("course_id"))
    course = next((item for item in courses if item["id"] == requested), None)
    if course is None:
        course = courses[0] if courses else None

    points: list[dict] = []
    mastery: list[dict] = []
    recent: list[dict] = []
    if course is not None:
        points = tutor_service.knowledge_points(conn, user["id"], course["id"])
        mastery = tutor_service.course_mastery(conn, user["id"], course["id"])
        recent = tutor_service.recent_sessions(conn, user["id"], course["id"])

    return render(
        request,
        "tutor/index.html",
        courses=courses,
        course=course,
        knowledge_points=points,
        mastery=mastery,
        mastery_summary=tutor_service.mastery_summary(mastery),
        recent_sessions=recent,
        ai_enabled=tutor_service.enabled(conn),
        ai_mode=tutor_service.mode_label(conn),
    )


@router.post("/session")
async def tutor_session(
    request: Request, user=Depends(require_user), conn=Depends(get_conn)
):
    _require_student(user)
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    course_id = _to_int(form_text(form, "course_id"))
    if course_id is None:
        add_flash(request, "请先选择一门课程", "error")
        return RedirectResponse("/tutor", status_code=303)
    session_mode = form_text(form, "mode") or tutor_service.THEORY
    tag_id = _to_int(form_text(form, "tag_id"))
    conversation_id = await tutor_service.create_session(
        conn,
        user,
        course_id=course_id,
        session_mode=session_mode,
        tag_id=tag_id,
    )
    add_flash(
        request,
        "已创建出题练习" if session_mode == tutor_service.QUIZ else "已创建理论答疑",
        "success",
    )
    return RedirectResponse(f"/tutor/{conversation_id}", status_code=303)


@router.get("/{conversation_id}")
def tutor_session_page(
    conversation_id: int,
    request: Request,
    user=Depends(require_user),
    conn=Depends(get_conn),
):
    _require_student(user)
    data = tutor_service.session_detail(conn, user, conversation_id)
    return render(
        request,
        "tutor/session.html",
        **data,
        ai_enabled=tutor_service.enabled(conn),
    )


@router.post("/{conversation_id}/message")
async def tutor_message(
    conversation_id: int,
    request: Request,
    user=Depends(require_user),
    conn=Depends(get_conn),
):
    _require_student(user)
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    result = await tutor_service.send_theory_message(
        conn, user, conversation_id, str(form_text(form, "content") or "")
    )
    add_flash(request, f"已生成回答（{result['mode']}）", "success")
    return RedirectResponse(f"/tutor/{conversation_id}", status_code=303)


@router.post("/{conversation_id}/question")
async def tutor_question(
    conversation_id: int,
    request: Request,
    user=Depends(require_user),
    conn=Depends(get_conn),
):
    _require_student(user)
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    tag_id = _to_int(form_text(form, "tag_id"))
    result = await tutor_service.ask_question(conn, user, conversation_id, tag_id=tag_id)
    add_flash(request, f"已生成「{result['tag']}」的新题目（{result['mode']}）", "success")
    return RedirectResponse(f"/tutor/{conversation_id}", status_code=303)


@router.post("/{conversation_id}/answer")
async def tutor_answer(
    conversation_id: int,
    request: Request,
    user=Depends(require_user),
    conn=Depends(get_conn),
):
    _require_student(user)
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    attempt_id = _to_int(form_text(form, "attempt_id"))
    if attempt_id is None:
        add_flash(request, "没有找到待作答的题目", "error")
        return RedirectResponse(f"/tutor/{conversation_id}", status_code=303)
    result = await tutor_service.answer_question(
        conn,
        user,
        attempt_id,
        str(form_text(form, "answer") or ""),
        conversation_id=conversation_id,
    )
    add_flash(
        request,
        f"本题得分 {result['score']}/10，掌握度已更新",
        "success" if result["score"] >= 6 else "info",
    )
    return RedirectResponse(f"/tutor/{conversation_id}", status_code=303)
