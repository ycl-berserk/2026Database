-- 审计历史：记录“谁在何时对哪张表的哪一行做了什么”，只保存主键与变更列名，
-- 不保存字段值、消息正文或批注内容，避免管理员页面成为隐私后门。
CREATE TABLE app.audit_log (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at     timestamptz NOT NULL DEFAULT now(),
    actor_user_id   bigint      REFERENCES app.users(id) ON DELETE SET NULL,
    table_name      text        NOT NULL,
    action          text        NOT NULL,
    row_pk          jsonb       NOT NULL,
    changed_columns text[]      NOT NULL DEFAULT '{}'::text[],
    CONSTRAINT audit_log_table_check CHECK (
        table_name IN ('courses', 'course_members', 'users')
    ),
    CONSTRAINT audit_log_action_check CHECK (
        action IN ('insert', 'update', 'delete')
    ),
    CONSTRAINT audit_log_row_pk_check CHECK (row_pk <> '{}'::jsonb)
);

CREATE INDEX audit_log_occurred_idx ON app.audit_log (occurred_at DESC, id DESC);
CREATE INDEX audit_log_table_action_idx
    ON app.audit_log (table_name, action, occurred_at DESC);
CREATE INDEX audit_log_actor_idx ON app.audit_log (actor_user_id, occurred_at DESC);

-- 通用行变更触发器：表名、动作、主键由触发器参数与 TG_* 提供；
-- 操作者从事务内 GUC app.actor_user_id 读取，未设置时记为系统操作。
CREATE OR REPLACE FUNCTION app.audit_row_change() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = app, pg_temp
AS $$
DECLARE
    v_actor   bigint := nullif(current_setting('app.actor_user_id', true), '')::bigint;
    v_old     jsonb;
    v_new     jsonb;
    v_pk      jsonb := '{}'::jsonb;
    v_changed text[] := '{}'::text[];
    v_key     text;
BEGIN
    IF v_actor IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM app.users WHERE id = v_actor) THEN
        v_actor := NULL;
    END IF;

    IF TG_OP <> 'INSERT' THEN
        v_old := to_jsonb(OLD);
    END IF;
    IF TG_OP <> 'DELETE' THEN
        v_new := to_jsonb(NEW);
    END IF;

    FOREACH v_key IN ARRAY TG_ARGV LOOP
        v_pk := v_pk || jsonb_build_object(
            v_key,
            CASE WHEN TG_OP = 'DELETE' THEN v_old -> v_key ELSE v_new -> v_key END
        );
    END LOOP;

    IF TG_OP = 'UPDATE' THEN
        SELECT coalesce(array_agg(n.key ORDER BY n.key), '{}'::text[])
          INTO v_changed
          FROM jsonb_each(v_new) AS n
         WHERE v_old -> n.key IS DISTINCT FROM n.value
           AND n.key <> 'password_hash';
    ELSIF TG_OP = 'INSERT' THEN
        SELECT coalesce(array_agg(k ORDER BY k), '{}'::text[])
          INTO v_changed
          FROM jsonb_object_keys(v_new) AS k
         WHERE k <> 'password_hash';
    ELSE
        SELECT coalesce(array_agg(k ORDER BY k), '{}'::text[])
          INTO v_changed
          FROM jsonb_object_keys(v_old) AS k
         WHERE k <> 'password_hash';
    END IF;

    INSERT INTO app.audit_log
        (actor_user_id, table_name, action, row_pk, changed_columns)
    VALUES
        (v_actor, TG_TABLE_NAME, lower(TG_OP), v_pk, v_changed);
    RETURN NULL;
END;
$$;

CREATE TRIGGER trg_courses_audit
    AFTER INSERT OR UPDATE OR DELETE ON app.courses
    FOR EACH ROW EXECUTE FUNCTION app.audit_row_change('id');

CREATE TRIGGER trg_course_members_audit
    AFTER INSERT OR UPDATE OR DELETE ON app.course_members
    FOR EACH ROW EXECUTE FUNCTION app.audit_row_change('course_id', 'user_id');

CREATE TRIGGER trg_users_audit
    AFTER INSERT OR UPDATE OR DELETE ON app.users
    FOR EACH ROW EXECUTE FUNCTION app.audit_row_change('id');

-- 保留 90 天，避免审计表无限增长；清理函数以属主身份执行，应用角色只能调用。
CREATE OR REPLACE FUNCTION app.prune_audit_log(retain_days integer DEFAULT 90)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = app, pg_temp
AS $$
DECLARE
    v_deleted integer;
BEGIN
    IF retain_days < 90 OR retain_days > 3650 THEN
        RAISE EXCEPTION 'retain_days must be between 90 and 3650'
            USING ERRCODE = '22023';
    END IF;

    DELETE FROM app.audit_log
     WHERE occurred_at < now() - make_interval(days => retain_days);
    GET DIAGNOSTICS v_deleted = ROW_COUNT;
    RETURN v_deleted;
END;
$$;

-- 已有数据库通过 scripts/migrate.py 升级时角色已存在；全新初始化时 roles.sql 会再执行一次授权。
DO $$
BEGIN
    REVOKE ALL ON FUNCTION app.prune_audit_log(integer) FROM PUBLIC;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ls_app') THEN
        GRANT SELECT ON app.audit_log TO ls_app;
        REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON app.audit_log FROM ls_app;
        GRANT EXECUTE ON FUNCTION app.prune_audit_log(integer) TO ls_app;
    END IF;
END;
$$;
