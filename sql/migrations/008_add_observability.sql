-- 观测页依赖 pg_stat_statements 的归一化查询统计。
-- 扩展需要 shared_preload_libraries，未预加载时跳过，页面会显示启用提示。
DO $$
BEGIN
    BEGIN
        CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'pg_stat_statements unavailable: %', SQLERRM;
    END;
END;
$$;

-- 允许应用连接读取全库统计（含归一化 SQL）；页面仍只对 admin 开放。
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ls_app') THEN
        GRANT pg_read_all_stats TO ls_app;
    END IF;
END;
$$;
