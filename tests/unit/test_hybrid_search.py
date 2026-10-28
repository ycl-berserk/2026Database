from __future__ import annotations

import pytest

from app.config import settings
from app.services import embedding_service
from app.services.conversation_service import rrf_fuse


def test_rrf_fuse_orders_by_reciprocal_rank():
    assert rrf_fuse([[1, 2, 3], [3, 4]], k=60) == [3, 1, 2, 4]


def test_rrf_fuse_breaks_ties_by_id():
    assert rrf_fuse([[2], [1]], k=60) == [1, 2]


def test_vector_literal_formats_float_array():
    assert embedding_service.vector_literal([0.1, -2, 3]) == (
        "[0.1000000,-2.0000000,3.0000000]"
    )


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _FakeClient:
    def __init__(self, payloads, calls):
        self.payloads = payloads
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def post(self, _url, json, headers):
        self.calls.append((json, headers))
        return _FakeResponse(self.payloads.pop(0))


def _with_embedding(**overrides):
    original = {name: getattr(settings, name) for name in overrides}
    for name, value in overrides.items():
        object.__setattr__(settings, name, value)
    embedding_service._cached_query.cache_clear()
    return original


def _restore(original):
    for name, value in original.items():
        object.__setattr__(settings, name, value)


def test_embedding_disabled_without_key():
    original = _with_embedding(
        embedding_mode="auto", embedding_api_key="", hybrid_search_enabled=True
    )
    try:
        assert embedding_service.mode() == "off"
        assert embedding_service.enabled() is False
        assert embedding_service.embed_query("隔离级别") is None
        with pytest.raises(embedding_service.EmbeddingError):
            embedding_service.embed_texts(["隔离级别"])
    finally:
        _restore(original)


def test_embed_texts_orders_by_index_and_validates_dimension(monkeypatch):
    calls = []
    payload = {
        "data": [
            {"index": 1, "embedding": [0.2, 0.2, 0.2]},
            {"index": 0, "embedding": [0.1, 0.1, 0.1]},
        ]
    }
    monkeypatch.setattr(
        embedding_service.httpx,
        "Client",
        lambda timeout: _FakeClient([payload], calls),
    )
    original = _with_embedding(
        embedding_mode="llm",
        embedding_api_key="test-key",
        embedding_dim=3,
        hybrid_search_enabled=True,
    )
    try:
        vectors = embedding_service.embed_texts(["第一条", "第二条"])
        assert vectors == [[0.1, 0.1, 0.1], [0.2, 0.2, 0.2]]
        body, headers = calls[0]
        assert body["model"] == settings.embedding_model
        assert body["input"] == ["第一条", "第二条"]
        assert headers["Authorization"] == "Bearer test-key"
    finally:
        _restore(original)


def test_embed_texts_rejects_wrong_dimension(monkeypatch):
    payload = {"data": [{"index": 0, "embedding": [0.1, 0.1]}]}
    monkeypatch.setattr(
        embedding_service.httpx,
        "Client",
        lambda timeout: _FakeClient([payload], []),
    )
    original = _with_embedding(
        embedding_mode="llm",
        embedding_api_key="test-key",
        embedding_dim=3,
        hybrid_search_enabled=True,
    )
    try:
        with pytest.raises(embedding_service.EmbeddingError, match="维度"):
            embedding_service.embed_texts(["维度不对"])
    finally:
        _restore(original)


def test_embed_query_is_cached(monkeypatch):
    calls = []
    payload = {"data": [{"index": 0, "embedding": [0.5, 0.5, 0.5]}]}
    monkeypatch.setattr(
        embedding_service.httpx,
        "Client",
        lambda timeout: _FakeClient([payload], calls),
    )
    original = _with_embedding(
        embedding_mode="llm",
        embedding_api_key="test-key",
        embedding_dim=3,
        hybrid_search_enabled=True,
    )
    embedding_service._cached_query.cache_clear()
    try:
        assert embedding_service.embed_query("缓存问题") == [0.5, 0.5, 0.5]
        assert embedding_service.embed_query("缓存问题") == [0.5, 0.5, 0.5]
        assert len(calls) == 1
    finally:
        embedding_service._cached_query.cache_clear()
        _restore(original)
