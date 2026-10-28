-- 采集令牌作用域：read 搜索/读取，ingest 导入，annotate 写入批注。
-- 默认只授予读与导入，MCP 客户端需要显式勾选才能写批注。
ALTER TABLE app.api_tokens
    ADD COLUMN scopes text[] NOT NULL
        DEFAULT ARRAY['read', 'ingest']::text[],
    ADD CONSTRAINT api_tokens_scopes_check CHECK (
        cardinality(scopes) > 0
        AND scopes <@ ARRAY['read', 'ingest', 'annotate']::text[]
    );
