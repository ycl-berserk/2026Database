from __future__ import annotations

from app import config


def _clear(monkeypatch) -> None:
    for name in (
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "MOONSHOT_API_KEY",
        "EMBEDDING_API_KEY",
        "EMBEDDING_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_deepseek_env_key_is_recognized(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-test")
    assert config.llm_key_from_env() == "sk-deepseek-test"
    assert config.llm_base_url_from_env() == "https://api.deepseek.com/v1"
    assert config.llm_model_from_env() == "deepseek-chat"
    # DeepSeek 不提供 embeddings，不应自动把对话 Key 当成向量 Key。
    assert config.embedding_key_from_env() == ""


def test_openai_env_key_sets_matching_defaults(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    assert config.llm_key_from_env() == "sk-openai-test"
    assert config.llm_base_url_from_env() == "https://api.openai.com/v1"
    assert config.llm_model_from_env() == "gpt-4o-mini"
    assert config.embedding_key_from_env() == "sk-openai-test"


def test_explicit_llm_key_wins(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("LLM_API_KEY", "sk-explicit")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
    assert config.llm_key_from_env() == "sk-explicit"
