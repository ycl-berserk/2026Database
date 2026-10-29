from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from ..auth import require_learner, require_user, verify_csrf
from ..db import get_conn
from ..services import embedding_service, search_service
from ..web import add_flash, form_text, render

router = APIRouter(prefix="/search")


@router.get("")
def search_page(request: Request, user=Depends(require_user), conn=Depends(get_conn)):
    require_learner(user, "管理员不检索他人消息正文")
    q = (request.query_params.get("q") or "").strip()[:100]
    data = search_service.global_search(conn, user, q)
    embedding = embedding_service.status(conn, user["id"])
    total = (
        data["conversations"]["total"]
        + len(data["annotations"])
        + len(data["courses"])
        + len(data["tags"])
    )
    return render(
        request,
        "search.html",
        q=q,
        data=data,
        total=total,
        embedding=embedding,
    )


@router.post("/embeddings")
async def build_embeddings(
    request: Request, user=Depends(require_user), conn=Depends(get_conn)
):
    require_learner(user, "管理员不参与向量索引")
    form = await request.form()
    verify_csrf(request, form_text(form, "csrf_token"))
    q = (form_text(form, "q") or "").strip()[:100]
    target = f"/search?q={quote(q)}" if q else "/search"
    if not embedding_service.enabled():
        add_flash(request, "未配置向量模型，继续使用词法检索", "info")
        return RedirectResponse(target, status_code=303)
    try:
        result = embedding_service.backfill(conn, user["id"], limit=64)
    except embedding_service.EmbeddingError as exc:
        add_flash(request, f"向量索引更新失败：{exc}", "error")
    else:
        if result["embedded"]:
            add_flash(request, f"已更新 {result['embedded']} 条消息的向量索引", "success")
        else:
            add_flash(request, "所有可见消息均已有向量索引", "info")
    return RedirectResponse(target, status_code=303)
