-- 每门课在事务结束时必须恰好有一个 owner。
-- 原 schema.sql 中的延迟触发器只挂在 courses 插入上，成员被删除或降级时不会复查；
-- 这里补一个挂在 course_members 上的延迟约束触发器，覆盖 INSERT / UPDATE / DELETE。
CREATE OR REPLACE FUNCTION app.check_course_owner_count() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_course_id   bigint;
    v_owner_count integer;
    v_ids         bigint[];
BEGIN
    IF TG_OP = 'INSERT' THEN
        v_ids := ARRAY[NEW.course_id];
    ELSIF TG_OP = 'DELETE' THEN
        v_ids := ARRAY[OLD.course_id];
    ELSE
        v_ids := ARRAY[OLD.course_id, NEW.course_id];
    END IF;

    FOREACH v_course_id IN ARRAY v_ids LOOP
        CONTINUE WHEN v_course_id IS NULL;

        -- 课程本身已被删除（级联删除成员）时不再检查。
        PERFORM 1 FROM app.courses WHERE id = v_course_id;
        CONTINUE WHEN NOT FOUND;

        SELECT count(*) INTO v_owner_count
          FROM app.course_members
         WHERE course_id = v_course_id AND role = 'owner';

        IF v_owner_count <> 1 THEN
            RAISE EXCEPTION 'course % must have exactly one owner, found %',
                v_course_id, v_owner_count
                USING ERRCODE = '23514';
        END IF;
    END LOOP;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_course_members_owner_required
    AFTER INSERT OR UPDATE OR DELETE ON app.course_members
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION app.check_course_owner_count();

-- 已有数据库升级时角色已存在；全新初始化时 roles.sql 会统一授权。
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ls_app') THEN
        GRANT EXECUTE ON FUNCTION app.check_course_owner_count() TO ls_app;
    END IF;
END;
$$;
