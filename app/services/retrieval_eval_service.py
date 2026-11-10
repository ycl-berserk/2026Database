from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..config import settings
from . import conversation_service, embedding_service


def load_cases(path: str | Path) -> list[dict]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(cases, list):
        raise ValueError("评测文件必须是会话数组，或包含 cases 数组的对象")
    normalized = []
    for index, case in enumerate(cases, start=1):
        query = str(case.get("query") or "").strip()
        expected = [str(item) for item in case.get("expected") or []]
        if not query or not expected:
            raise ValueError(f"第 {index} 个评测用例缺少 query 或 expected")
        normalized.append(
            {
                "id": str(case.get("id") or f"case-{index}"),
                "query": query,
                "expected": expected,
            }
        )
    return normalized


def rank_metrics(ranked_ids: list[int], expected: list[int], k: int = 10) -> dict:
    top = list(ranked_ids)[: max(1, k)]
    targets = set(expected)
    first_rank = next(
        (rank for rank, conversation_id in enumerate(top, start=1)
         if conversation_id in targets),
        None,
    )
    hits = len(targets & set(top))
    return {
        "recall": hits / len(targets) if targets else 0.0,
        "mrr": 1.0 / first_rank if first_rank else 0.0,
        "first_rank": first_rank,
        "hits": hits,
    }


@contextmanager
def _hybrid_flag(value: bool) -> Iterator[None]:
    original = settings.hybrid_search_enabled
    object.__setattr__(settings, "hybrid_search_enabled", value)
    try:
        yield
    finally:
        object.__setattr__(settings, "hybrid_search_enabled", original)


def _conversation_ids(conn, user_id: int, external_ids: list[str]) -> dict[str, int]:
    if not external_ids:
        return {}
    rows = conn.execute(
        """
        SELECT v.id, v.external_id
          FROM app.conversations v
          JOIN app.sources s ON s.id = v.source_id
         WHERE v.external_id = ANY(%(external_ids)s)
           AND (
                s.owner_id = %(uid)s
                OR (v.course_id IS NOT NULL AND EXISTS (
                    SELECT 1 FROM app.course_members cm
                     WHERE cm.course_id = v.course_id AND cm.user_id = %(uid)s
                ))
           )
         ORDER BY v.id
        """,
        {"uid": user_id, "external_ids": external_ids},
    ).fetchall()
    mapping: dict[str, int] = {}
    for row in rows:
        mapping.setdefault(row["external_id"], row["id"])
    return mapping


def _top_ids(conn, user_id: int, query: str, query_vector: list[float] | None) -> list[int]:
    result = conversation_service.list_conversations(
        conn, user_id, q=query, query_vector=query_vector
    )
    return [row["id"] for row in result["rows"]]


def _summary(results: list[dict]) -> dict | None:
    if not results:
        return None
    return {
        "cases": len(results),
        "recall_at_k": sum(item["recall"] for item in results) / len(results),
        "mrr": sum(item["mrr"] for item in results) / len(results),
        "hits": sum(item["hits"] for item in results),
    }


def evaluate(conn, user_id: int, cases: list[dict], k: int = 10) -> dict:
    external_ids = sorted({item for case in cases for item in case["expected"]})
    mapping = _conversation_ids(conn, user_id, external_ids)
    case_results: list[dict] = []

    for case in cases:
        expected_ids = [
            mapping[external_id]
            for external_id in case["expected"]
            if external_id in mapping
        ]
        result = {
            "id": case["id"],
            "query": case["query"],
            "expected": case["expected"],
            "expected_ids": expected_ids,
            "lexical": None,
            "hybrid": None,
            "status": "ok",
        }
        if not expected_ids:
            result["status"] = "skipped_missing"
            case_results.append(result)
            continue

        with _hybrid_flag(False):
            lexical_ids = _top_ids(conn, user_id, case["query"], None)
        result["lexical"] = rank_metrics(lexical_ids, expected_ids, k)

        query_vector = embedding_service.embed_query(case["query"])
        if query_vector is None:
            result["status"] = "hybrid_skipped"
        else:
            with _hybrid_flag(True):
                hybrid_ids = _top_ids(conn, user_id, case["query"], query_vector)
            result["hybrid"] = rank_metrics(hybrid_ids, expected_ids, k)
        case_results.append(result)

    lexical = _summary(
        [item["lexical"] for item in case_results if item["lexical"] is not None]
    )
    hybrid = _summary(
        [item["hybrid"] for item in case_results if item["hybrid"] is not None]
    )
    return {
        "k": max(1, k),
        "model": settings.embedding_model,
        "hybrid_configured": embedding_service.enabled(),
        "lexical": lexical,
        "hybrid": hybrid,
        "cases": case_results,
        "skipped_missing": sum(
            1 for item in case_results if item["status"] == "skipped_missing"
        ),
        "skipped_hybrid": sum(
            1 for item in case_results if item["status"] == "hybrid_skipped"
        ),
    }
