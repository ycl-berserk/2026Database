from __future__ import annotations

from app.config import settings
from app.services import embedding_service, retrieval_eval_service


def _vector(index: int = 0) -> list[float]:
    vector = [0.0] * settings.embedding_dim
    vector[index] = 1.0
    return vector


def test_retrieval_eval_compares_lexical_and_hybrid(conn, base, monkeypatch):
    alice = base["users"]["alice"]
    conn.execute(
        """
        UPDATE app.messages
           SET embedding = %s::vector,
               embedding_model = %s,
               embedded_at = now()
         WHERE id = %s
        """,
        (
            embedding_service.vector_literal(_vector()),
            settings.embedding_model,
            base["messages"]["db-1"],
        ),
    )
    monkeypatch.setattr(embedding_service, "embed_query", lambda _query, _config=None: _vector())

    report = retrieval_eval_service.evaluate(
        conn,
        alice,
        [{"id": "db", "query": "事务", "expected": ["db-1"]}],
        k=5,
    )

    assert report["lexical"]["recall_at_k"] == 1.0
    assert report["lexical"]["mrr"] == 1.0
    assert report["hybrid"]["recall_at_k"] == 1.0
    assert report["hybrid"]["mrr"] == 1.0
    assert report["skipped_missing"] == 0
    assert report["cases"][0]["status"] == "ok"


def test_retrieval_eval_skips_unknown_conversation(conn, base):
    report = retrieval_eval_service.evaluate(
        conn,
        base["users"]["alice"],
        [{"id": "missing", "query": "事务", "expected": ["not-in-demo"]}],
        k=5,
    )
    assert report["skipped_missing"] == 1
    assert report["lexical"] is None
    assert report["cases"][0]["status"] == "skipped_missing"
