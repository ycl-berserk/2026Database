from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..auth import require_user
from ..db import get_conn
from ..errors import PermissionDenied
from ..services import search_service
from ..web import render

router = APIRouter(prefix="/search")


@router.get("")
def search_page(request: Request, user=Depends(require_user), conn=Depends(get_conn)):
    if user["system_role"] == "admin":
        raise PermissionDenied("管理员不检索他人消息正文")
    q = (request.query_params.get("q") or "").strip()[:100]
    data = search_service.global_search(conn, user, q)
    total = (
        data["conversations"]["total"]
        + len(data["annotations"])
        + len(data["courses"])
        + len(data["tags"])
    )
    return render(request, "search.html", q=q, data=data, total=total)
