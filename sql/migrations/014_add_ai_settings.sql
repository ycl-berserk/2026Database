-- AI 供应商配置：部署级单例，API Key 用 pgcrypto 加密后存库。
-- 非敏感字段（enabled / base_url / model / dim）可明文；密钥列只存密文。
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS app.ai_settings (
    id                   smallint    PRIMARY KEY DEFAULT 1,
    llm_enabled          boolean     NOT NULL DEFAULT true,
    llm_base_url         text        NOT NULL DEFAULT 'https://api.deepseek.com/v1',
    llm_model            text        NOT NULL DEFAULT 'deepseek-chat',
    llm_api_key_enc      bytea,
    embedding_enabled    boolean     NOT NULL DEFAULT true,
    embedding_base_url   text        NOT NULL DEFAULT 'https://api.deepseek.com/v1',
    embedding_model      text        NOT NULL DEFAULT 'text-embedding-3-small',
    embedding_dim        integer     NOT NULL DEFAULT 1536,
    embedding_api_key_enc bytea,
    updated_by           bigint      REFERENCES app.users(id) ON DELETE SET NULL,
    updated_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ai_settings_singleton CHECK (id = 1),
    CONSTRAINT ai_settings_llm_base_url_nonempty CHECK (length(btrim(llm_base_url)) > 0),
    CONSTRAINT ai_settings_llm_model_nonempty CHECK (length(btrim(llm_model)) > 0),
    CONSTRAINT ai_settings_embedding_base_url_nonempty CHECK (length(btrim(embedding_base_url)) > 0),
    CONSTRAINT ai_settings_embedding_model_nonempty CHECK (length(btrim(embedding_model)) > 0),
    CONSTRAINT ai_settings_embedding_dim_check CHECK (embedding_dim BETWEEN 1 AND 4096)
);

COMMENT ON TABLE app.ai_settings IS '部署级 AI 供应商配置；API Key 仅存 pgcrypto 密文';
COMMENT ON COLUMN app.ai_settings.llm_api_key_enc IS 'pgp_sym_encrypt 密文，解密密钥来自 AI_SETTINGS_KEY 或 SESSION_SECRET';

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ls_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON app.ai_settings TO ls_app;
    END IF;
END;
$$;
