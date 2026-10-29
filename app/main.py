from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

from .config import settings
from .db import check_main_db, check_t2s_db, main_pool, t2s_pool
from .errors import AppError, AuthRequired
from .security import ensure_production_settings
from .web import render

logger = logging.getLogger("learning_space")

ERROR_HINTS = {
    400: "提交的数据没有通过校验。请返回上一页，检查必填项、格式或文件内容后重试。",
    403: "当前账号不是这门课程的成员，或角色是只读成员。可以回到自己参与的课程，或请课程所有者调整成员角色。",
    404: "这个链接可能已经过期，或者内容已被删除或转移。",
    405: "这个地址不接受当前的请求方式。请从页面上的按钮或链接重新进入。",
    409: "数据在提交前已经被修改。刷新页面确认最新状态后再试一次。",
    500: "服务器遇到了意外错误，本次请求没有写入数据。可以稍后重试。",
}
ERROR_TITLES = {404: "页面不存在", 405: "请求方式不支持", 429: "操作太频繁"}


def _render_error(request: Request, status: int, title: str, message: str):
    return render(
        request,
        "error.html",
        status_code=status,
        error_status=status,
        error_title=title,
        error_message=message,
        error_hint=ERROR_HINTS.get(status, "请返回上一页检查操作，或回到概览重新开始。"),
        error_path=request.url.path,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_production_settings(settings)
    main_pool.open()
    t2s_pool.open()
    try:
        yield
    finally:
        main_pool.close()
        t2s_pool.close()


app = FastAPI(title="学习空间 Learning Space", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="ls_session",
    same_site="lax",
    https_only=settings.cookie_secure,
    max_age=settings.session_max_age,
)
app.add_middleware(GZipMiddleware, minimum_size=600)


@app.middleware("http")
async def response_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
    )
    if settings.hsts_enabled:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    if request.url.path.startswith("/static/"):
        if request.query_params.get("v"):
            response.headers.setdefault(
                "Cache-Control", "public, max-age=31536000, immutable"
            )
        else:
            response.headers.setdefault("Cache-Control", "public, max-age=300")
    elif response.headers.get("content-type", "").startswith("text/html"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")


@app.exception_handler(AuthRequired)
async def handle_auth_required(request: Request, exc: AuthRequired):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": exc.message}, status_code=401)
    target = request.url.path
    if request.url.query:
        target = f"{target}?{request.url.query}"
    return RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)


@app.exception_handler(AppError)
async def handle_app_error(request: Request, exc: AppError):
    retry_after = getattr(exc, "retry_after", None)
    headers = {"Retry-After": str(retry_after)} if retry_after else None
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            {"detail": exc.message}, status_code=exc.status_code, headers=headers
        )
    response = _render_error(request, exc.status_code, exc.title, exc.message)
    if headers:
        response.headers.update(headers)
    return response


@app.exception_handler(StarletteHTTPException)
async def handle_http_error(request: Request, exc: StarletteHTTPException):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    if request.url.path.startswith("/static/"):
        return PlainTextResponse("Not Found", status_code=exc.status_code)
    message = {
        404: "没有找到这个页面或资源。",
        405: "这个地址不支持当前的请求方式。",
    }.get(exc.status_code, str(exc.detail))
    title = ERROR_TITLES.get(exc.status_code, "请求无法完成")
    return _render_error(request, exc.status_code, title, message)


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "服务器内部错误"}, status_code=500)
    return _render_error(
        request,
        500,
        "服务器开小差了",
        "这次请求没有完成，数据没有被修改。",
    )


def _initialized() -> bool:
    try:
        from .bootstrap import is_initialized

        with main_pool.connection() as conn:
            return is_initialized(conn)
    except Exception:
        return False


@app.get("/healthz")
def healthz():
    result = {
        "ok": True,
        "app": "up",
        "db": "up",
        "t2s_db": "up",
        "desktop": settings.desktop_mode,
        "bind_host": settings.bind_host,
        "initialized": False,
    }
    try:
        check_main_db()
    except Exception as exc:
        result["ok"] = False
        result["db"] = f"error: {exc.__class__.__name__}"
    try:
        check_t2s_db()
    except Exception as exc:
        result["ok"] = False
        result["t2s_db"] = f"error: {exc.__class__.__name__}"
    if result["db"] == "up":
        result["initialized"] = _initialized()
    return JSONResponse(result, status_code=200 if result["ok"] else 503)


@app.get("/")
def index():
    if settings.desktop_mode and not _initialized():
        return RedirectResponse("/setup", status_code=303)
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    return FileResponse(
        settings.static_dir / "manifest.webmanifest",
        media_type="application/manifest+json",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    # 必须从根路径提供，Service-Worker-Allowed 才能覆盖整个站点。
    return FileResponse(
        settings.static_dir / "sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


@app.get("/offline", include_in_schema=False)
def offline_page():
    return FileResponse(
        settings.static_dir / "offline.html",
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


from .routers import (  # noqa: E402
    admin,
    annotations,
    api,
    auth,
    capture,
    conversations,
    courses,
    dashboard,
    imports,
    mcp,
    onboarding,
    reviews,
    search,
    setup,
    stats,
    tags,
    text2sql,
    tokens,
)

app.include_router(auth.router)
app.include_router(setup.router)
app.include_router(api.router)
app.include_router(mcp.router)
app.include_router(dashboard.router)
app.include_router(onboarding.router)
app.include_router(search.router)
app.include_router(capture.router)
app.include_router(courses.router)
app.include_router(conversations.router)
app.include_router(tags.router)
app.include_router(imports.router)
app.include_router(tokens.router)
app.include_router(annotations.router)
app.include_router(reviews.router)
app.include_router(stats.router)
app.include_router(text2sql.router)
app.include_router(admin.router)
