-- 最小权限角色：主应用只做 DML；Text2SQL 只读且只能访问 t2s 视图。
-- 由 scripts/init_db.py 以 postgres 身份执行（每个数据库执行一次）。

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ls_app') THEN
        CREATE ROLE ls_app LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 't2s_reader') THEN
        CREATE ROLE t2s_reader LOGIN;
    END IF;
END;
$$;

ALTER ROLE ls_app WITH LOGIN PASSWORD 'ls_app_dev_pw';
ALTER ROLE t2s_reader WITH LOGIN PASSWORD 't2s_dev_pw';

ALTER ROLE ls_app SET search_path = app, public;
ALTER ROLE t2s_reader SET search_path = t2s, public;
ALTER ROLE t2s_reader SET default_transaction_read_only = on;
ALTER ROLE t2s_reader SET statement_timeout = '3s';
ALTER ROLE t2s_reader SET idle_in_transaction_session_timeout = '5s';
ALTER ROLE t2s_reader SET lock_timeout = '2s';

-- app schema：主应用 DML，无 DDL
REVOKE CREATE ON SCHEMA app FROM PUBLIC;
GRANT USAGE ON SCHEMA app TO ls_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA app TO ls_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA app TO ls_app;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA app TO ls_app;

-- 审计表对应用角色只读：写入由 SECURITY DEFINER 触发器完成，清理走受控函数。
DO $$
BEGIN
    IF to_regclass('app.audit_log') IS NOT NULL THEN
        REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON app.audit_log FROM ls_app;
        GRANT SELECT ON app.audit_log TO ls_app;
        REVOKE ALL ON FUNCTION app.prune_audit_log(integer) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION app.prune_audit_log(integer) TO ls_app;
    END IF;
END;
$$;

-- t2s schema：只读角色只能 SELECT 视图
GRANT USAGE ON SCHEMA t2s TO t2s_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA t2s TO t2s_reader;

-- 明确不授予 t2s_reader 任何 app 基线权限
REVOKE ALL ON SCHEMA app FROM t2s_reader;
REVOKE ALL ON ALL TABLES IN SCHEMA app FROM t2s_reader;

-- 防止普通角色在 public 建对象
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO ls_app, t2s_reader;

-- 数据库连接权限
DO $$
BEGIN
    EXECUTE format('REVOKE CONNECT ON DATABASE %I FROM PUBLIC', current_database());
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO ls_app, t2s_reader', current_database());
END;
$$;

-- 之后由 postgres 新建的 app/t2s 对象自动授权
ALTER DEFAULT PRIVILEGES IN SCHEMA app
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ls_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA app
    GRANT USAGE, SELECT ON SEQUENCES TO ls_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA t2s
    GRANT SELECT ON TABLES TO t2s_reader;
