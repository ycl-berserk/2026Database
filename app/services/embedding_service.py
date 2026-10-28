from __future__ import annotations

import logging
from collections.abc import Iterable
from functools import lru_cache

import httpx

from ..config import settings

logger = logging.getLogger("learning_space")

VISIBLE_MESSAGES = """
    FROM app.messages m
    JOIN app.conversations v ON v.id = m.conversation_id
    JOIN app.sources s ON s.id = v.source_id
   WHERE (
        s.owner_id = %(uid)s
        OR (v.course_id IS NOT NULL AND EXISTS (
            SELECT 1 FROM app.course_members cm
             WHERE cm.course_id = v.course_id AND cm.user_id = %(uid)s
        ))
   )
"""


class EmbeddingError(RuntimeError):
    pass


def mode() -> str:
    if settings.embedding_mode in ("off", "false", "0"):
        return "off"
    if settings.embedding_mode == "llm":
        return "llm"
    return "llm" if settings.embedding_api_key else "off"


def enabled() -> bool:
    return mode() == "llm" and settings.hybrid_search_enabled


def vector_literal(vector: Iterable[float]) -> str:
    return "[" + ",".join(f"{float(value):.7f}" for value in vector) + "]"


def _batches(items: list[str], size: int = 32) -> Iterable[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not enabled():
        raise EmbeddingError("未配置向量模型")
    cleaned = [(text or "").strip()[:6000] or " " for text in texts]
    vectors: list[list[float]] = []
    headers = {"Authorization": f"Bearer {settings.embedding_api_key}"}
    url = f"{settings.embedding_base_url.rstrip('/')}/embeddings"
    try:
        with httpx.Client(timeout=20) as client:
            for batch in _batches(cleaned):
                response = client.post(
                    url,
                    json={"model": settings.embedding_model, "input": batch},
                    headers=headers,
                )
                response.raise_for_status()
                payload = response.json().get("data") or []
                if len(payload) != len(batch):
                    raise EmbeddingError("向量服务返回数量与请求不一致")
                ordered = sorted(payload, key=lambda item: item.get("index", 0))
                vectors.extend([float(value) for value in item["embedding"]] for item in ordered)
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise EmbeddingError(f"向量服务调用失败：{exc}") from exc
    for vector in vectors:
        if len(vector) != settings.embedding_dim:
            raise EmbeddingError(
                f"向量维度应为 {settings.embedding_dim}，实际 {len(vector)}；"
                "请检查 EMBEDDING_MODEL 与 EMBEDDING_DIM"
            )
    return vectors


@lru_cache(maxsize=128)
def _cached_query(model: str, question: str) -> tuple[float, ...]:
    return tuple(embed_texts([question])[0])


def embed_query(question: str) -> list[float] | None:
    if not enabled() or not (question or "").strip():
        return None
    try:
        return list(_cached_query(settings.embedding_model, question.strip()))
    except EmbeddingError as exc:
        logger.warning("查询向量生成失败，退回词法检索：%s", exc)
        return None


def clear_query_cache() -> None:
    _cached_query.cache_clear()


def coverage(conn, user_id: int) -> dict:
    row = conn.execute(
        f"""
        SELECT count(*) AS total,
               count(*) FILTER (WHERE m.embedding IS NOT NULL
                                  AND m.embedding_model = %(model)s) AS embedded
        {VISIBLE_MESSAGES}
        """,
        {"uid": user_id, "model": settings.embedding_model},
    ).fetchone()
    return {"total": row["total"], "embedded": row["embedded"]}


def pending_count(conn, user_id: int) -> int:
    row = conn.execute(
        f"""
        SELECT count(*) AS n
        {VISIBLE_MESSAGES}
           AND (m.embedding IS NULL
                OR m.embedding_model IS DISTINCT FROM %(model)s)
        """,
        {"uid": user_id, "model": settings.embedding_model},
    ).fetchone()
    return row["n"]


def backfill(conn, user_id: int, limit: int = 64) -> dict:
    limit = max(1, min(int(limit), 512))
    rows = conn.execute(
        f"""
        SELECT m.id, m.content
        {VISIBLE_MESSAGES}
           AND (m.embedding IS NULL
                OR m.embedding_model IS DISTINCT FROM %(model)s)
         ORDER BY m.id
         LIMIT %(limit)s
        """,
        {"uid": user_id, "model": settings.embedding_model, "limit": limit},
    ).fetchall()
    if not rows:
        return {"embedded": 0, "model": settings.embedding_model}
    vectors = embed_texts([row["content"] for row in rows])
    with conn.transaction():
        for row, vector in zip(rows, vectors, strict=False):
            conn.execute(
                """
                UPDATE app.messages
                   SET embedding = %s::vector,
                       embedding_model = %s,
                       embedded_at = now()
                 WHERE id = %s
                """,
                (vector_literal(vector), settings.embedding_model, row["id"]),
            )
    return {"embedded": len(rows), "model": settings.embedding_model}


def status(conn, user_id: int) -> dict:
    result = {
        "mode": mode(),
        "enabled": enabled(),
        "model": settings.embedding_model,
        "coverage": None,
        "pending": None,
    }
    if result["enabled"]:
        result["coverage"] = coverage(conn, user_id)
        result["pending"] = pending_count(conn, user_id)
    return result
