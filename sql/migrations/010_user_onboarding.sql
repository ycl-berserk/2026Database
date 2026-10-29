-- 新用户引导状态：只记录用户主动收起/重新开始，四步进度始终按业务表实时计算。

CREATE TABLE IF NOT EXISTS app.user_onboarding (
    user_id    bigint      PRIMARY KEY REFERENCES app.users(id) ON DELETE CASCADE,
    status     varchar(12) NOT NULL DEFAULT 'pending',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT user_onboarding_status_check
        CHECK (status IN ('pending', 'skipped', 'completed'))
);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ls_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON app.user_onboarding TO ls_app;
    END IF;
END;
$$;
