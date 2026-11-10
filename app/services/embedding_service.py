from __future__ import annotations

import hashlib
import logging
from collections import OrderedDict
from collections.abc import Iterable
from functools import lru_cache

import httpx

from ..config import settings

logger = logging.getLogger("learning_space")

EMBED_CACHE_MAX = 512
_EMBED_CACHE: OrderedDict[tuple[str, str, str], tuple[float, ...]] = OrderedDict()

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


def _normalized(text: str | None) -> str:
    return (text or "").strip()[:6000] or " "


def content_hash(text: str | None) -> str:
    return hashlib.sha256(_normalized(text).encode("utf-8")).hexdigest()


def _cache_key(digest: str) -> tuple[str, str, str]:
    return (settings.embedding_base_url.rstrip("/"), settings.embedding_model, digest)


def _cache_get(key: tuple[str, str, str]) -> tuple[float, ...] | None:
    vector = _EMBED_CACHE.get(key)
    if vector is not None:
        _EMBED_CACHE.move_to_end(key)
    return vector


def _cache_put(key: tuple[str, str, str], vector: tuple[float, ...]) -> None:
    _EMBED_CACHE[key] = vector
    _EMBED_CACHE.move_to_end(key)
    while len(_EMBED_CACHE) > EMBED_CACHE_MAX:
        _EMBED_CACHE.popitem(last=False)


def clear_embedding_cache() -> None:
    _EMBED_CACHE.clear()


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not enabled():
        raise EmbeddingError("未配置向量模型")
    cleaned = [_normalized(text) for text in texts]
    digests = [content_hash(text) for text in cleaned]

    vectors_by_hash: dict[str, tuple[float, ...]] = {}
    pending: dict[str, str] = {}
    for digest, text in zip(digests, cleaned, strict=False):
        if digest in vectors_by_hash or digest in pending:
            continue
        cached = _cache_get(_cache_key(digest))
        if cached is None:
            pending[digest] = text
        else:
            vectors_by_hash[digest] = cached

    if pending:
        headers = {"Authorization": f"Bearer {settings.embedding_api_key}"}
        url = f"{settings.embedding_base_url.rstrip('/')}/embeddings"
        pending_digests = list(pending)
        offset = 0
        try:
            with httpx.Client(timeout=20) as client:
                for batch in _batches(list(pending.values())):
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
                    batch_digests = pending_digests[offset : offset + len(batch)]
                    for digest, item in zip(batch_digests, ordered, strict=False):
                        vectors_by_hash[digest] = tuple(
                            float(value) for value in item["embedding"]
                        )
                    offset += len(batch)
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise EmbeddingError(f"向量服务调用失败：{exc}") from exc

    for digest in digests:
        vector = vectors_by_hash[digest]
        if len(vector) != settings.embedding_dim:
            raise EmbeddingError(
                f"向量维度应为 {settings.embedding_dim}，实际 {len(vector)}；"
                "请检查 EMBEDDING_MODEL 与 EMBEDDING_DIM"
            )
        _cache_put(_cache_key(digest), vector)
    return [list(vectors_by_hash[digest]) for digest in digests]


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
        return {"embedded": 0, "reused": 0, "model": settings.embedding_model}
    digests = [content_hash(row["content"]) for row in rows]
    unique: dict[str, str] = {}
    for digest, row in zip(digests, rows, strict=False):
        unique.setdefault(digest, row["content"])
    vectors = embed_texts(list(unique.values()))
    by_hash = dict(zip(unique, vectors, strict=False))
    with conn.transaction():
        for row, digest in zip(rows, digests, strict=False):
            conn.execute(
                """
                UPDATE app.messages
                   SET embedding = %s::vector,
                       embedding_model = %s,
                       embedded_at = now()
                 WHERE id = %s
                """,
                (vector_literal(by_hash[digest]), settings.embedding_model, row["id"]),
            )
    return {
        "embedded": len(rows),
        "reused": len(rows) - len(unique),
        "model": settings.embedding_model,
    }


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
