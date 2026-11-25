from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
import psycopg

from ..config import settings as app_settings
from ..errors import ValidationError

SETTINGS_ID = 1
MAX_FIELD = 200
MAX_KEY = 4096


@dataclass(frozen=True)
class AIConfig:
    llm_enabled: bool
    llm_base_url: str
    llm_model: str
    llm_api_key: str
    llm_source: str
    embedding_enabled: bool
    embedding_base_url: str
    embedding_model: str
    embedding_dim: int
    embedding_api_key: str
    embedding_source: str
    secrets_error: bool = False


def _encryption_key() -> str:
    """优先使用 AI_SETTINGS_KEY；否则从 SESSION_SECRET 派生（生产/桌面都已随机化）。"""
    if app_settings.ai_settings_key:
        return app_settings.ai_settings_key
    return hashlib.sha256(
        ("learning-space:ai-settings:" + app_settings.session_secret).encode("utf-8")
    ).hexdigest()


def env_config() -> AIConfig:
    return AIConfig(
        llm_enabled=True,
        llm_base_url=app_settings.llm_base_url,
        llm_model=app_settings.llm_model,
        llm_api_key=app_settings.llm_api_key,
        llm_source="environment" if app_settings.llm_api_key else "none",
        embedding_enabled=True,
        embedding_base_url=app_settings.embedding_base_url,
        embedding_model=app_settings.embedding_model,
        embedding_dim=app_settings.embedding_dim,
        embedding_api_key=app_settings.embedding_api_key,
        embedding_source="environment" if app_settings.embedding_api_key else "none",
    )


_SELECT_DECRYPT = """
SELECT llm_enabled, llm_base_url, llm_model,
       CASE WHEN llm_api_key_enc IS NULL THEN NULL
            ELSE pgp_sym_decrypt(llm_api_key_enc, %(key)s) END AS llm_api_key,
       embedding_enabled, embedding_base_url, embedding_model, embedding_dim,
       CASE WHEN embedding_api_key_enc IS NULL THEN NULL
            ELSE pgp_sym_decrypt(embedding_api_key_enc, %(key)s) END AS embedding_api_key
  FROM app.ai_settings
 WHERE id = %(id)s
"""

_SELECT_MASKED = """
SELECT llm_enabled, llm_base_url, llm_model,
       NULL::text AS llm_api_key,
       embedding_enabled, embedding_base_url, embedding_model, embedding_dim,
       NULL::text AS embedding_api_key
  FROM app.ai_settings
 WHERE id = %(id)s
"""


def _read_row(conn) -> tuple[dict | None, bool]:
    try:
        with conn.transaction():
            row = conn.execute(
                _SELECT_DECRYPT, {"key": _encryption_key(), "id": SETTINGS_ID}
            ).fetchone()
        return row, False
    except psycopg.errors.UndefinedTable:
        return None, False
    except psycopg.Error:
        # 密钥轮换或旧密文无法解密时，不阻塞页面：退回非敏感字段，提示重新保存密钥。
        try:
            with conn.transaction():
                row = conn.execute(_SELECT_MASKED, {"id": SETTINGS_ID}).fetchone()
            return row, True
        except psycopg.errors.UndefinedTable:
            return None, False


def get_config(conn=None) -> AIConfig:
    env = env_config()
    if conn is None:
        return env
    row, secrets_error = _read_row(conn)
    if row is None:
        return env

    llm_key = (row.get("llm_api_key") or "").strip() or env.llm_api_key
    embedding_key = (
        (row.get("embedding_api_key") or "").strip() or env.embedding_api_key
    )
    return AIConfig(
        llm_enabled=bool(row.get("llm_enabled")),
        llm_base_url=(row.get("llm_base_url") or env.llm_base_url).strip(),
        llm_model=(row.get("llm_model") or env.llm_model).strip(),
        llm_api_key=llm_key,
        llm_source=(
            "database" if row.get("llm_api_key") else env.llm_source
        ),
        embedding_enabled=bool(row.get("embedding_enabled")),
        embedding_base_url=(
            row.get("embedding_base_url") or env.embedding_base_url
        ).strip(),
        embedding_model=(row.get("embedding_model") or env.embedding_model).strip(),
        embedding_dim=int(row.get("embedding_dim") or env.embedding_dim),
        embedding_api_key=embedding_key,
        embedding_source=(
            "database" if row.get("embedding_api_key") else env.embedding_source
        ),
        secrets_error=secrets_error,
    )


def mask_key(key: str | None) -> str:
    value = (key or "").strip()
    if not value:
        return ""
    if len(value) <= 4:
        return "••••"
    return "••••" + value[-4:]


def public_config(conn=None) -> dict:
    config = get_config(conn)
    return {
        "llm_enabled": config.llm_enabled,
        "llm_base_url": config.llm_base_url,
        "llm_model": config.llm_model,
        "llm_key_masked": mask_key(config.llm_api_key),
        "llm_key_source": config.llm_source,
        "llm_key_configured": bool(config.llm_api_key),
        "embedding_enabled": config.embedding_enabled,
        "embedding_base_url": config.embedding_base_url,
        "embedding_model": config.embedding_model,
        "embedding_dim": config.embedding_dim,
        "embedding_key_masked": mask_key(config.embedding_api_key),
        "embedding_key_source": config.embedding_source,
        "embedding_key_configured": bool(config.embedding_api_key),
        "secrets_error": config.secrets_error,
    }


def _clean(value, limit: int = MAX_FIELD) -> str:
    return " ".join(str(value or "").split())[:limit]


def _validate_url(value: str, label: str) -> str:
    text = _clean(value)
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValidationError(f"{label}必须是 http(s) 地址")
    return text.rstrip("/")


def _encrypt(conn, value: str | None) -> bytes | None:
    text = (value or "").strip()
    if not text:
        return None
    row = conn.execute(
        "SELECT pgp_sym_encrypt(%s, %s, 'cipher-algo=aes256') AS enc",
        (text[:MAX_KEY], _encryption_key()),
    ).fetchone()
    return row["enc"]


def save(conn, actor: dict, form) -> dict:
    llm_enabled = bool(form.get("llm_enabled"))
    embedding_enabled = bool(form.get("embedding_enabled"))
    llm_base_url = _validate_url(form.get("llm_base_url") or "", "对话模型 Base URL")
    llm_model = _clean(form.get("llm_model"))
    embedding_base_url = _validate_url(
        form.get("embedding_base_url") or "", "向量模型 Base URL"
    )
    embedding_model = _clean(form.get("embedding_model"))
    if not llm_model:
        raise ValidationError("对话模型名称不能为空")
    if not embedding_model:
        raise ValidationError("向量模型名称不能为空")
    try:
        embedding_dim = int(form.get("embedding_dim") or 1536)
    except (TypeError, ValueError) as exc:
        raise ValidationError("向量维度必须是整数") from exc
    if not 1 <= embedding_dim <= 4096:
        raise ValidationError("向量维度必须在 1–4096 之间")

    llm_new_key = _encrypt(conn, form.get("llm_api_key"))
    embedding_new_key = _encrypt(conn, form.get("embedding_api_key"))
    clear_llm = bool(form.get("clear_llm_key"))
    clear_embedding = bool(form.get("clear_embedding_key"))
    share_key = bool(form.get("share_key"))

    if share_key and not clear_llm and not clear_embedding:
        if embedding_new_key is None and llm_new_key is not None:
            embedding_new_key = llm_new_key
        elif embedding_new_key is None:
            existing = get_config(conn)
            if existing.llm_api_key and not existing.embedding_api_key:
                embedding_new_key = _encrypt(conn, existing.llm_api_key)

    with conn.transaction():
        conn.execute(
            """
            INSERT INTO app.ai_settings (
                id, llm_enabled, llm_base_url, llm_model, llm_api_key_enc,
                embedding_enabled, embedding_base_url, embedding_model,
                embedding_dim, embedding_api_key_enc, updated_by, updated_at
            ) VALUES (
                1, %(llm_enabled)s, %(llm_base_url)s, %(llm_model)s, %(llm_new_key)s,
                %(embedding_enabled)s, %(embedding_base_url)s, %(embedding_model)s,
                %(embedding_dim)s, %(embedding_new_key)s, %(actor)s, now()
            )
            ON CONFLICT (id) DO UPDATE SET
                llm_enabled = EXCLUDED.llm_enabled,
                llm_base_url = EXCLUDED.llm_base_url,
                llm_model = EXCLUDED.llm_model,
                llm_api_key_enc = CASE
                    WHEN %(clear_llm)s THEN NULL
                    WHEN EXCLUDED.llm_api_key_enc IS NOT NULL THEN EXCLUDED.llm_api_key_enc
                    ELSE app.ai_settings.llm_api_key_enc END,
                embedding_enabled = EXCLUDED.embedding_enabled,
                embedding_base_url = EXCLUDED.embedding_base_url,
                embedding_model = EXCLUDED.embedding_model,
                embedding_dim = EXCLUDED.embedding_dim,
                embedding_api_key_enc = CASE
                    WHEN %(clear_embedding)s THEN NULL
                    WHEN EXCLUDED.embedding_api_key_enc IS NOT NULL THEN EXCLUDED.embedding_api_key_enc
                    ELSE app.ai_settings.embedding_api_key_enc END,
                updated_by = EXCLUDED.updated_by,
                updated_at = now()
            """,
            {
                "llm_enabled": llm_enabled,
                "llm_base_url": llm_base_url,
                "llm_model": llm_model,
                "llm_new_key": llm_new_key,
                "embedding_enabled": embedding_enabled,
                "embedding_base_url": embedding_base_url,
                "embedding_model": embedding_model,
                "embedding_dim": embedding_dim,
                "embedding_new_key": embedding_new_key,
                "actor": actor["id"],
                "clear_llm": clear_llm,
                "clear_embedding": clear_embedding,
            },
        )
    return public_config(conn)


def _form_config(conn, form) -> AIConfig:
    config = get_config(conn)
    llm_key = (form.get("llm_api_key") or "").strip()
    embedding_key = (form.get("embedding_api_key") or "").strip()
    if form.get("clear_llm_key"):
        llm_key = ""
    if form.get("clear_embedding_key"):
        embedding_key = ""
    return AIConfig(
        llm_enabled=bool(form.get("llm_enabled", config.llm_enabled)),
        llm_base_url=_clean(form.get("llm_base_url") or config.llm_base_url),
        llm_model=_clean(form.get("llm_model") or config.llm_model),
        llm_api_key=llm_key or config.llm_api_key,
        llm_source="form" if llm_key else config.llm_source,
        embedding_enabled=bool(
            form.get("embedding_enabled", config.embedding_enabled)
        ),
        embedding_base_url=_clean(
            form.get("embedding_base_url") or config.embedding_base_url
        ),
        embedding_model=_clean(form.get("embedding_model") or config.embedding_model),
        embedding_dim=int(form.get("embedding_dim") or config.embedding_dim),
        embedding_api_key=embedding_key or config.embedding_api_key,
        embedding_source="form" if embedding_key else config.embedding_source,
        secrets_error=config.secrets_error,
    )


async def test_embedding(conn, form) -> dict:
    config = _form_config(conn, form)
    if not config.embedding_api_key:
        return {"ok": False, "message": "未配置向量模型 Key，无法测试", "latency_ms": 0}
    base = config.embedding_base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {config.embedding_api_key}"}
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"{base}/embeddings",
                headers=headers,
                json={"model": config.embedding_model, "input": ["ping"]},
            )
            response.raise_for_status()
            payload = response.json()
        vector = (payload.get("data") or [{}])[0].get("embedding") or []
        latency = int((time.perf_counter() - started) * 1000)
        if len(vector) != config.embedding_dim:
            return {
                "ok": False,
                "message": f"连接成功，但返回维度 {len(vector)} 与设置 {config.embedding_dim} 不一致",
                "latency_ms": latency,
            }
        return {
            "ok": True,
            "message": f"连接成功（{config.embedding_model}，{len(vector)} 维，{latency} ms）",
            "latency_ms": latency,
        }
    except httpx.HTTPStatusError as exc:
        return {
            "ok": False,
            "message": f"服务返回 HTTP {exc.response.status_code}，请检查 Base URL / Key / 模型",
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "message": f"连接失败：{exc.__class__.__name__}",
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }


async def test_llm(conn, form) -> dict:
    config = _form_config(conn, form)
    if not config.llm_api_key:
        return {"ok": False, "message": "未配置 API Key，无法测试", "latency_ms": 0}
    base = config.llm_base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {config.llm_api_key}"}
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(f"{base}/models", headers=headers)
            if response.status_code >= 400:
                response = await client.post(
                    f"{base}/chat/completions",
                    headers=headers,
                    json={
                        "model": config.llm_model,
                        "messages": [{"role": "user", "content": "ping"}],
                        "max_tokens": 1,
                    },
                )
            response.raise_for_status()
        latency = int((time.perf_counter() - started) * 1000)
        return {
            "ok": True,
            "message": f"连接成功（{config.llm_model}，{latency} ms）",
            "latency_ms": latency,
        }
    except httpx.HTTPStatusError as exc:
        return {
            "ok": False,
            "message": f"服务返回 HTTP {exc.response.status_code}，请检查 Base URL / Key / 模型",
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "message": f"连接失败：{exc.__class__.__name__}",
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }
