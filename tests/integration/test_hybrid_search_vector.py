from __future__ import annotations

from app.config import settings
from app.services import conversation_service, embedding_service


def _vector(index: int, value: float = 1.0) -> list[float]:
    vector = [0.0] * settings.embedding_dim
    vector[index] = value
    return vector


def _store(conn, message_id: int, vector: list[float]) -> None:
    conn.execute(
        """
        UPDATE app.messages
           SET embedding = %s::vector,
               embedding_model = %s,
               embedded_at = now()
         WHERE id = %s
        """,
        (embedding_service.vector_literal(vector), settings.embedding_model, message_id),
    )


def test_vector_schema_is_installed(conn):
    extension = conn.execute(
        "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
    ).fetchone()
    assert extension is not None
    column = conn.execute(
        "SELECT udt_name FROM information_schema.columns "
        "WHERE table_schema = 'app' AND table_name = 'messages' "
        "AND column_name = 'embedding'"
    ).fetchone()
    assert column["udt_name"] == "vector"
    index = conn.execute(
        "SELECT indexname FROM pg_indexes "
        "WHERE schemaname = 'app' AND indexname = 'messages_embedding_hnsw'"
    ).fetchone()
    assert index is not None


def test_hybrid_search_is_scoped_and_finds_vector_only_matches(conn, base):
    alice = base["users"]["alice"]
    bob = base["users"]["bob"]
    vector_db = _vector(0)
    vector_os = _vector(1)
    _store(conn, base["messages"]["db-1"], vector_db)
    _store(conn, base["messages"]["os-1"], vector_os)

    bob_source = conn.execute(
        "INSERT INTO app.sources (owner_id, platform, account_label) "
        "VALUES (%s, 'chatgpt', 'bob-private') RETURNING id",
        (bob,),
    ).fetchone()
    bob_conversation = conn.execute(
        "INSERT INTO app.conversations (source_id, external_id, title, started_at) "
        "VALUES (%s, 'bob-private-1', 'Bob 的私有会话', '2026-02-02T10:00:00+08:00') "
        "RETURNING id",
        (bob_source["id"],),
    ).fetchone()
    bob_message = conn.execute(
        "INSERT INTO app.messages (conversation_id, seq_no, sender, content) "
        "VALUES (%s, 1, 'user', 'Bob 的私有消息') RETURNING id",
        (bob_conversation["id"],),
    ).fetchone()
    _store(conn, bob_message["id"], vector_db)

    q = "完全不存在的关键词xyz"
    result = conversation_service.list_conversations(
        conn, alice, q=q, query_vector=vector_db
    )
    assert result["retrieval"]["mode"] == "hybrid"
    assert result["retrieval"]["candidates"] == {
        "lexical": 0,
        "vector": 2,
        "fused": 2,
    }
    assert [row["title"] for row in result["rows"]] == ["事务与隔离级别", "进程调度"]
    assert all(row["hit_mode"] == "vector" for row in result["rows"])

    bob_result = conversation_service.list_conversations(
        conn, bob, q=q, query_vector=vector_db
    )
    titles = {row["title"] for row in bob_result["rows"]}
    assert "Bob 的私有会话" in titles
    assert "Bob 的私有会话" not in {
        row["title"] for row in result["rows"]
    }


def test_hybrid_fuses_lexical_and_vector_candidates(conn, base):
    alice = base["users"]["alice"]
    _store(conn, base["messages"]["db-1"], _vector(0))
    _store(conn, base["messages"]["os-1"], _vector(1))

    result = conversation_service.list_conversations(
        conn, alice, q="事务", query_vector=_vector(1)
    )
    assert result["retrieval"]["mode"] == "hybrid"
    assert result["retrieval"]["candidates"]["lexical"] >= 1
    assert result["retrieval"]["candidates"]["vector"] == 2
    rows = {row["title"]: row for row in result["rows"]}
    assert set(rows) == {"事务与隔离级别", "进程调度"}
    assert rows["事务与隔离级别"]["hit_mode"] == "lexical"
    assert rows["进程调度"]["hit_mode"] == "vector"
    # 两条候选各有一次第 1 名；词法项同时得到向量第 2 名，因此排在最前。
    assert result["rows"][0]["title"] == "事务与隔离级别"


def test_without_query_vector_search_falls_back_to_lexical(conn, base):
    original_mode = settings.embedding_mode
    original_key = settings.embedding_api_key
    try:
        object.__setattr__(settings, "embedding_mode", "auto")
        object.__setattr__(settings, "embedding_api_key", "")
        embedding_service._cached_query.cache_clear()
        result = conversation_service.list_conversations(
            conn, base["users"]["alice"], q="完全不存在的关键词xyz"
        )
    finally:
        object.__setattr__(settings, "embedding_mode", original_mode)
        object.__setattr__(settings, "embedding_api_key", original_key)
        embedding_service._cached_query.cache_clear()

    assert result["retrieval"]["mode"] == "lexical"
    assert result["rows"] == []
    assert result["total"] == 0


def test_embedding_coverage_counts_visible_messages(conn, base):
    alice = base["users"]["alice"]
    _store(conn, base["messages"]["db-1"], _vector(0))
    _store(conn, base["messages"]["os-1"], _vector(1))

    coverage = embedding_service.coverage(conn, alice)
    assert coverage == {"total": 6, "embedded": 2}
    assert embedding_service.pending_count(conn, alice) == 4
