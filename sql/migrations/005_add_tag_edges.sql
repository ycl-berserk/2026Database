-- 标签先修关系：同一用户的标签之间的有向无环图。
-- 边 a -> b 表示“掌握 a 是学习 b 的前置条件”。
CREATE TABLE app.tag_edges (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    from_tag_id     bigint      NOT NULL REFERENCES app.tags(id) ON DELETE CASCADE,
    to_tag_id       bigint      NOT NULL REFERENCES app.tags(id) ON DELETE CASCADE,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT tag_edges_no_self CHECK (from_tag_id <> to_tag_id),
    CONSTRAINT tag_edges_pair_unique UNIQUE (from_tag_id, to_tag_id)
);

CREATE INDEX tag_edges_to_idx ON app.tag_edges (to_tag_id);

CREATE OR REPLACE FUNCTION app.check_tag_edge_access() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_from_owner bigint;
    v_to_owner   bigint;
    v_cycle      boolean;
BEGIN
    SELECT owner_id INTO v_from_owner FROM app.tags WHERE id = NEW.from_tag_id;
    SELECT owner_id INTO v_to_owner FROM app.tags WHERE id = NEW.to_tag_id;

    IF v_from_owner IS NULL OR v_to_owner IS NULL THEN
        RAISE EXCEPTION 'tag edge references a missing tag'
            USING ERRCODE = '23503';
    END IF;
    IF v_from_owner <> v_to_owner THEN
        RAISE EXCEPTION 'tag edge must connect tags of the same owner'
            USING ERRCODE = '23514';
    END IF;

    WITH RECURSIVE reach(tag_id) AS (
        SELECT NEW.to_tag_id
        UNION
        SELECT e.to_tag_id
          FROM app.tag_edges e
          JOIN reach r ON e.from_tag_id = r.tag_id
    )
    SELECT EXISTS (SELECT 1 FROM reach WHERE tag_id = NEW.from_tag_id) INTO v_cycle;

    IF v_cycle THEN
        RAISE EXCEPTION 'tag edge % -> % would create a cycle', NEW.from_tag_id, NEW.to_tag_id
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_tag_edges_access
    BEFORE INSERT OR UPDATE OF from_tag_id, to_tag_id ON app.tag_edges
    FOR EACH ROW EXECUTE FUNCTION app.check_tag_edge_access();

-- 已有数据库通过 scripts/migrate.py 升级时角色已存在；全新初始化时角色随后由 roles.sql 授权。
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ls_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON app.tag_edges TO ls_app;
        GRANT USAGE, SELECT ON SEQUENCE app.tag_edges_id_seq TO ls_app;
        GRANT EXECUTE ON FUNCTION app.check_tag_edge_access() TO ls_app;
    END IF;
END;
$$;
