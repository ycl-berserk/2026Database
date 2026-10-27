from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from starlette.datastructures import UploadFile

from ..auth import require_user, verify_csrf
from ..config import settings
from ..db import get_conn
from ..errors import PermissionDenied, ValidationError
from ..importers.base import PLATFORMS
from ..services import import_service
from ..web import add_flash, render

router = APIRouter(prefix="/imports")


def _require_student(user: dict) -> None:
    if user["system_role"] == "admin":
        raise PermissionDenied("管理员不参与聊天记录导入")


@router.get("")
def import_list(request: Request, user=Depends(require_user), conn=Depends(get_conn)):
    _require_student(user)
    batches = import_service.list_batches(conn, user["id"])
    return render(request, "imports/list.html", batches=batches, platforms=PLATFORMS)


@router.post("/preview")
async def import_preview(
    request: Request, user=Depends(require_user), conn=Depends(get_conn)
):
    _require_student(user)
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))
    upload = form.get("file")
    platform = (form.get("platform") or "auto").strip().lower()
    account_label = (form.get("account_label") or "").strip()

    def fail(message: str, status_code: int = 400):
        batches = import_service.list_batches(conn, user["id"])
        return render(
            request,
            "imports/list.html",
            status_code=status_code,
            batches=batches,
            platforms=PLATFORMS,
            errors=[message],
            form={"platform": platform, "account_label": account_label,
                  "file_name": getattr(upload, "filename", "") or ""},
        )

    if not isinstance(upload, UploadFile):
        return fail("请选择要导入的文件")
    if platform != "auto" and platform not in PLATFORMS:
        return fail("来源平台无效")

    file_name = Path(upload.filename or "upload").name
    raw = await upload.read(settings.import_max_bytes + 1)
    if not raw:
        return fail("文件为空")
    if len(raw) > settings.import_max_bytes:
        return fail(f"文件超过 {settings.import_max_bytes // 1024 // 1024} MB 上限")

    result = import_service.parse_upload(
        file_name,
        raw,
        platform=None if platform == "auto" else platform,
        account_label=account_label or None,
    )
    if result.errors or not result.conversations:
        batches = import_service.list_batches(conn, user["id"])
        return render(
            request,
            "imports/list.html",
            status_code=400,
            batches=batches,
            platforms=PLATFORMS,
            errors=result.errors or ["文件中没有可导入的会话"],
            warnings=result.warnings,
            form={"platform": platform, "account_label": account_label,
                  "file_name": file_name},
        )
    if result.platform not in PLATFORMS:
        return fail("无法确定来源平台：请在表单中选择")

    with conn.transaction():
        source_id = import_service.get_or_create_source(
            conn, user["id"], result.platform, result.account_label
        )
        batch = import_service.save_preview(
            conn,
            user_id=user["id"],
            source_id=source_id,
            file_name=file_name,
            raw=raw,
            result=result,
        )
    add_flash(request, "已解析文件，请确认要导入的会话", "info")
    return RedirectResponse(f"/imports/preview/{batch['preview_token']}", status_code=303)


@router.get("/preview/{token}")
def import_preview_page(
    token: str, request: Request, user=Depends(require_user), conn=Depends(get_conn)
):
    _require_student(user)
    batch = import_service.load_batch(conn, token, user["id"])
    context = import_service.preview_context(conn, batch, user["id"])
    return render(request, "imports/preview.html", **context, token=token)


@router.post("/confirm")
async def import_confirm(
    request: Request, user=Depends(require_user), conn=Depends(get_conn)
):
    _require_student(user)
    form = await request.form()
    verify_csrf(request, form.get("csrf_token"))
    token = (form.get("token") or "").strip()
    if not token:
        raise ValidationError("缺少预览标识，请重新上传")
    try:
        selected = {int(value) for value in form.getlist("selected") if str(value).isdigit()}
    except ValueError:
        raise ValidationError("选择项格式不正确") from None

    course_map: dict[int, int | None] = {}
    for key, value in form.multi_items():
        if key.startswith("course_") and str(value).strip():
            try:
                course_map[int(key[7:])] = int(value)
            except (ValueError, TypeError):
                raise ValidationError("课程映射格式不正确") from None

    result = import_service.confirm_import(
        conn,
        token=token,
        user=user,
        selected=selected,
        course_map=course_map,
    )
    message = (
        f"导入完成：新增会话 {result['conversations_new']} 个、"
        f"更新 {result['conversations_updated']} 个，新增消息 {result['messages_new']} 条，"
        f"跳过重复 {result['messages_skipped']} 条，内容冲突 {result['conflicts']} 条。"
    )
    add_flash(request, message, "success")
    return RedirectResponse("/imports", status_code=303)
