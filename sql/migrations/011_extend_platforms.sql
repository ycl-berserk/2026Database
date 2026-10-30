-- 本地 harness（Codex CLI / opencode）作为独立来源平台入库。
ALTER TABLE app.sources DROP CONSTRAINT IF EXISTS sources_platform_check;

ALTER TABLE app.sources
    ADD CONSTRAINT sources_platform_check
    CHECK (platform IN ('qoder', 'chatgpt', 'deepseek', 'claude', 'codex', 'opencode', 'other'));
