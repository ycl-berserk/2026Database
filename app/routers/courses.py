from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from ..auth import require_user, verify_csrf
from ..db import get_conn
from ..errors import PermissionDenied
from ..services import course_service
from ..web import add_flash, form_text, render

router = APIRouter(prefix="/courses")


def _require_student(user: dict) -> None:
    if user["system_role"] == "admin":
        raise PermissionDenied("管理员只访问系统聚合数据，不查看课程内容")


@router.get("")
def course_list(request: Request, user=Depends(require_user), conn=Depends(get_conn)):
    _require_student(user)
    courses = course_service.list_courses(conn, user["id"])
    return render(request, "courses/list.html", courses=courses)


@router.get("/new")
def course_new(request: Request, user=Depends(require_user)):
    _require_student(user)
    return render(request, "courses/form.html", mode="create", course=None)


@router.post("")
async def course_create(request: Request, user=Depends(require_user), conn=Depends(get_conn)):
    _require_student(user)
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    course_id = course_service.create_course(conn, form, user["id"])
    add_flash(request, "课程已创建", "success")
    return RedirectResponse(f"/courses/{course_id}", status_code=303)


@router.get("/{course_id}")
def course_detail(
    course_id: int, request: Request, user=Depends(require_user), conn=Depends(get_conn)
):
    _require_student(user)
    data = course_service.course_detail(conn, course_id, user)
    candidates = (
        course_service.member_candidates(conn, course_id) if data["role"] == "owner" else []
    )
    return render(request, "courses/detail.html", **data, candidates=candidates)


@router.get("/{course_id}/edit")
def course_edit(
    course_id: int, request: Request, user=Depends(require_user), conn=Depends(get_conn)
):
    _require_student(user)
    course_service.require_owner(conn, course_id, user)
    course = course_service.get_course(conn, course_id)
    return render(request, "courses/form.html", mode="edit", course=course)


@router.post("/{course_id}/edit")
async def course_update(
    course_id: int, request: Request, user=Depends(require_user), conn=Depends(get_conn)
):
    _require_student(user)
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    course_service.update_course(conn, course_id, form, user)
    add_flash(request, "课程信息已更新", "success")
    return RedirectResponse(f"/courses/{course_id}", status_code=303)


@router.post("/{course_id}/delete")
async def course_delete(
    course_id: int, request: Request, user=Depends(require_user), conn=Depends(get_conn)
):
    _require_student(user)
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    course_service.delete_course(conn, course_id, user)
    add_flash(request, "课程已删除，原有会话保留但不再属于该课程", "success")
    return RedirectResponse("/courses", status_code=303)


@router.post("/{course_id}/members")
async def member_add(
    course_id: int, request: Request, user=Depends(require_user), conn=Depends(get_conn)
):
    _require_student(user)
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    course_service.add_member(
        conn, course_id, form_text(form, "username") or "", form_text(form, "role") or "viewer", user
    )
    add_flash(request, "已添加课程成员", "success")
    return RedirectResponse(f"/courses/{course_id}", status_code=303)


@router.post("/{course_id}/members/{member_id}/role")
async def member_role(
    course_id: int,
    member_id: int,
    request: Request,
    user=Depends(require_user),
    conn=Depends(get_conn),
):
    _require_student(user)
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    course_service.set_member_role(conn, course_id, member_id, form_text(form, "role") or "", user)
    add_flash(request, "成员角色已更新", "success")
    return RedirectResponse(f"/courses/{course_id}", status_code=303)


@router.post("/{course_id}/members/{member_id}/remove")
async def member_remove(
    course_id: int,
    member_id: int,
    request: Request,
    user=Depends(require_user),
    conn=Depends(get_conn),
):
    _require_student(user)
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    course_service.remove_member(conn, course_id, member_id, user)
    add_flash(request, "成员已移除", "success")
    return RedirectResponse(f"/courses/{course_id}", status_code=303)
