from __future__ import annotations

from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .config import settings
from .db import check_main_db, check_t2s_db, main_pool, t2s_pool
from .errors import AppError, AuthRequired
from .web import render


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    https_only=not settings.is_dev,
    max_age=60 * 60 * 24 * 7,
)
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
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": exc.message}, status_code=exc.status_code)
    return render(
        request,
        "error.html",
        status_code=exc.status_code,
        error_status=exc.status_code,
        error_title=exc.title,
        error_message=exc.message,
    )


@app.get("/healthz")
def healthz():
    result = {"ok": True, "app": "up", "db": "up", "t2s_db": "up"}
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
    return JSONResponse(result, status_code=200 if result["ok"] else 503)


@app.get("/")
def index():
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
    auth,
    capture,
    conversations,
    courses,
    dashboard,
    imports,
    reviews,
    stats,
    tags,
    text2sql,
)

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(capture.router)
app.include_router(courses.router)
app.include_router(conversations.router)
app.include_router(tags.router)
app.include_router(imports.router)
app.include_router(annotations.router)
app.include_router(reviews.router)
app.include_router(stats.router)
app.include_router(text2sql.router)
app.include_router(admin.router)
