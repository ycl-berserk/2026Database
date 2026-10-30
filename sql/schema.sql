-- 个人学习空间：核心关系模式
-- 由 scripts/init_db.py --reset 执行；直接 psql 执行前请确认是空库。

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE SCHEMA IF NOT EXISTS app;

-- ---------------------------------------------------------------------------
-- 1. 用户
-- ---------------------------------------------------------------------------
CREATE TABLE app.users (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username        varchar(50)  NOT NULL UNIQUE,
    password_hash   text         NOT NULL,
    display_name    varchar(100) NOT NULL,
    system_role     varchar(10)  NOT NULL DEFAULT 'student',
    is_active       boolean      NOT NULL DEFAULT true,
    created_at      timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT users_username_format CHECK (username ~ '^[A-Za-z0-9_]{3,50}$'),
    CONSTRAINT users_display_name_nonempty CHECK (length(btrim(display_name)) > 0),
    CONSTRAINT users_password_hash_length CHECK (length(password_hash) >= 32),
    CONSTRAINT users_system_role_check CHECK (system_role IN ('student', 'admin'))
);

-- ---------------------------------------------------------------------------
-- 2. 课程
-- ---------------------------------------------------------------------------
CREATE TABLE app.courses (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code            varchar(20)  NOT NULL,
    name            varchar(100) NOT NULL,
    semester        varchar(20)  NOT NULL,
    instructor      varchar(100),
    description     text,
    created_by      bigint       NOT NULL REFERENCES app.users(id) ON DELETE RESTRICT,
    created_at      timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT courses_code_format CHECK (code ~ '^[A-Za-z0-9-]{2,20}$'),
    CONSTRAINT courses_name_nonempty CHECK (length(btrim(name)) > 0),
    CONSTRAINT courses_owner_code_semester_unique UNIQUE (created_by, code, semester)
);

-- ---------------------------------------------------------------------------
-- 3. 课程成员（课程级角色）
-- ---------------------------------------------------------------------------
CREATE TABLE app.course_members (
    course_id       bigint      NOT NULL REFERENCES app.courses(id) ON DELETE CASCADE,
    user_id         bigint      NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
    role            varchar(10) NOT NULL,
    joined_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (course_id, user_id),
    CONSTRAINT course_members_role_check CHECK (role IN ('owner', 'editor', 'viewer'))
);

CREATE UNIQUE INDEX course_members_one_owner
    ON app.course_members (course_id)
    WHERE role = 'owner';
CREATE INDEX course_members_user_idx ON app.course_members (user_id);

-- ---------------------------------------------------------------------------
-- 4. 来源（平台账号）
-- ---------------------------------------------------------------------------
CREATE TABLE app.sources (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_id        bigint       NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
    platform        varchar(20)  NOT NULL,
    account_label   varchar(100) NOT NULL DEFAULT 'default',
    created_at      timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT sources_platform_check CHECK (platform IN ('qoder', 'chatgpt', 'deepseek', 'claude', 'codex', 'opencode', 'other')),
    CONSTRAINT sources_owner_platform_label_unique UNIQUE (owner_id, platform, account_label)
);

-- ---------------------------------------------------------------------------
-- 5. 导入批次（预览/审计；payload 为暂存 JSONB）
-- ---------------------------------------------------------------------------
CREATE TABLE app.import_batches (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id         bigint       NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
    source_id       bigint       NOT NULL REFERENCES app.sources(id) ON DELETE RESTRICT,
    file_name       varchar(255) NOT NULL,
    file_sha256     char(64)     NOT NULL,
    file_size       integer      NOT NULL,
    status          varchar(10)  NOT NULL DEFAULT 'preview',
    preview_token   uuid         NOT NULL UNIQUE,
    payload         jsonb,
    summary         jsonb        NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz  NOT NULL DEFAULT now(),
    expires_at      timestamptz  NOT NULL,
    imported_at     timestamptz,
    CONSTRAINT import_batches_status_check CHECK (status IN ('preview', 'imported', 'failed', 'expired')),
    CONSTRAINT import_batches_size_check CHECK (file_size > 0 AND file_size <= 5242880),
    CONSTRAINT import_batches_sha256_format CHECK (file_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT import_batches_expiry_check CHECK (expires_at > created_at),
    CONSTRAINT import_batches_payload_object CHECK (payload IS NULL OR jsonb_typeof(payload) = 'object'),
    CONSTRAINT import_batches_summary_object CHECK (jsonb_typeof(summary) = 'object')
);

CREATE INDEX import_batches_user_idx ON app.import_batches (user_id, created_at DESC);
CREATE INDEX import_batches_hash_idx ON app.import_batches (source_id, file_sha256);

-- ---------------------------------------------------------------------------
-- 6. 会话
-- ---------------------------------------------------------------------------
CREATE TABLE app.conversations (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id       bigint       NOT NULL REFERENCES app.sources(id) ON DELETE RESTRICT,
    course_id       bigint       REFERENCES app.courses(id) ON DELETE SET NULL,
    external_id     varchar(200) NOT NULL,
    title           varchar(255) NOT NULL,
    started_at      timestamptz,
    ended_at        timestamptz,
    is_archived     boolean      NOT NULL DEFAULT false,
    created_at      timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT conversations_title_nonempty CHECK (length(btrim(title)) > 0),
    CONSTRAINT conversations_external_id_nonempty CHECK (length(btrim(external_id)) > 0),
    CONSTRAINT conversations_time_check CHECK (ended_at IS NULL OR started_at IS NULL OR ended_at >= started_at),
    CONSTRAINT conversations_source_external_unique UNIQUE (source_id, external_id)
);

CREATE INDEX conversations_course_idx ON app.conversations (course_id, started_at DESC NULLS LAST);
CREATE INDEX conversations_source_idx ON app.conversations (source_id);
CREATE INDEX conversations_title_trgm ON app.conversations USING gin (title gin_trgm_ops);

-- ---------------------------------------------------------------------------
-- 7. 消息
-- ---------------------------------------------------------------------------
CREATE TABLE app.messages (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id bigint      NOT NULL REFERENCES app.conversations(id) ON DELETE CASCADE,
    seq_no          integer     NOT NULL,
    sender          varchar(10) NOT NULL,
    content         text        NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT messages_seq_positive CHECK (seq_no > 0),
    CONSTRAINT messages_sender_check CHECK (sender IN ('user', 'assistant', 'system')),
    CONSTRAINT messages_content_nonempty CHECK (length(btrim(content)) > 0),
    CONSTRAINT messages_conversation_seq_unique UNIQUE (conversation_id, seq_no)
);

CREATE INDEX messages_conversation_seq_idx ON app.messages (conversation_id, seq_no);
CREATE INDEX messages_content_trgm ON app.messages USING gin (content gin_trgm_ops);
-- 内容指纹用于跨会话查重；用表达式索引而不是生成列，避免传递依赖。
CREATE INDEX messages_content_md5_idx ON app.messages ((md5(content)));

-- ---------------------------------------------------------------------------
-- 8. 标签
-- ---------------------------------------------------------------------------
CREATE TABLE app.tags (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_id        bigint      NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
    name            varchar(50) NOT NULL,
    kind            varchar(20) NOT NULL DEFAULT 'topic',
    color           varchar(7)  NOT NULL DEFAULT '#64748b',
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT tags_name_nonempty CHECK (length(btrim(name)) > 0),
    CONSTRAINT tags_kind_check CHECK (kind IN ('knowledge_point', 'topic', 'custom')),
    CONSTRAINT tags_color_check CHECK (color ~ '^#[0-9A-Fa-f]{6}$'),
    CONSTRAINT tags_owner_name_unique UNIQUE (owner_id, name)
);

CREATE INDEX tags_owner_idx ON app.tags (owner_id);

-- ---------------------------------------------------------------------------
-- 9. 会话标签（M:N，带“标注人”）
-- ---------------------------------------------------------------------------
CREATE TABLE app.conversation_tags (
    conversation_id bigint      NOT NULL REFERENCES app.conversations(id) ON DELETE CASCADE,
    tag_id          bigint      NOT NULL REFERENCES app.tags(id) ON DELETE CASCADE,
    created_by      bigint      NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (conversation_id, tag_id)
);

CREATE INDEX conversation_tags_tag_idx ON app.conversation_tags (tag_id);
CREATE INDEX conversation_tags_creator_idx ON app.conversation_tags (created_by);

-- ---------------------------------------------------------------------------
-- 10. 批注
-- ---------------------------------------------------------------------------
CREATE TABLE app.annotations (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    message_id      bigint      NOT NULL REFERENCES app.messages(id) ON DELETE CASCADE,
    user_id         bigint      NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
    kind            varchar(15) NOT NULL DEFAULT 'note',
    content         text        NOT NULL,
    status          varchar(10) NOT NULL DEFAULT 'open',
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT annotations_kind_check CHECK (kind IN ('note', 'question', 'correction', 'summary')),
    CONSTRAINT annotations_status_check CHECK (status IN ('open', 'resolved')),
    CONSTRAINT annotations_content_nonempty CHECK (length(btrim(content)) > 0)
);

CREATE INDEX annotations_message_idx ON app.annotations (message_id);
CREATE INDEX annotations_user_status_idx ON app.annotations (user_id, status);

-- ---------------------------------------------------------------------------
-- 11. 复习项
-- ---------------------------------------------------------------------------
CREATE TABLE app.review_items (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id         bigint      NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
    annotation_id   bigint      NOT NULL REFERENCES app.annotations(id) ON DELETE CASCADE,
    status          varchar(10) NOT NULL DEFAULT 'pending',
    due_date        date        NOT NULL DEFAULT current_date,
    interval_days   integer     NOT NULL DEFAULT 1,
    review_count    integer     NOT NULL DEFAULT 0,
    last_reviewed_at timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT review_items_status_check CHECK (status IN ('pending', 'reviewing', 'mastered')),
    CONSTRAINT review_items_interval_check CHECK (interval_days > 0),
    CONSTRAINT review_items_count_check CHECK (review_count >= 0),
    CONSTRAINT review_items_user_annotation_unique UNIQUE (user_id, annotation_id)
);

CREATE INDEX review_items_queue_idx
    ON app.review_items (user_id, status, due_date)
    WHERE status <> 'mastered';

-- ---------------------------------------------------------------------------
-- 12. Text2SQL 日志
-- ---------------------------------------------------------------------------
CREATE TABLE app.text2sql_log (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id         bigint      NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
    question        text        NOT NULL,
    generated_sql   text,
    status          varchar(10) NOT NULL,
    error_message   text,
    latency_ms      integer,
    row_count       integer,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT text2sql_log_question_nonempty CHECK (length(btrim(question)) > 0),
    CONSTRAINT text2sql_log_status_check CHECK (status IN ('success', 'rejected', 'error')),
    CONSTRAINT text2sql_log_latency_check CHECK (latency_ms IS NULL OR latency_ms >= 0),
    CONSTRAINT text2sql_log_row_count_check CHECK (row_count IS NULL OR row_count >= 0)
);

CREATE INDEX text2sql_log_user_idx ON app.text2sql_log (user_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- 触发器：跨表一致性
-- ---------------------------------------------------------------------------

-- 每个课程在事务结束时必须恰好有一个 owner 成员。
CREATE OR REPLACE FUNCTION app.check_course_has_owner() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    owner_count integer;
BEGIN
    SELECT count(*) INTO owner_count
    FROM app.course_members
    WHERE course_id = NEW.id AND role = 'owner';
    IF owner_count <> 1 THEN
        RAISE EXCEPTION 'course % must have exactly one owner, found %', NEW.id, owner_count
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_courses_owner_required
    AFTER INSERT ON app.courses
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION app.check_course_has_owner();

-- 只有会话所有者或课程 owner/editor 才能给会话打标签；标签必须属于标注人。
CREATE OR REPLACE FUNCTION app.check_conversation_tag_access() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_source_owner bigint;
    v_course_id    bigint;
    v_role         text;
    v_tag_owner    bigint;
BEGIN
    SELECT s.owner_id, c.course_id
      INTO v_source_owner, v_course_id
      FROM app.conversations c
      JOIN app.sources s ON s.id = c.source_id
     WHERE c.id = NEW.conversation_id;

    SELECT owner_id INTO v_tag_owner FROM app.tags WHERE id = NEW.tag_id;

    IF v_tag_owner IS NULL OR v_tag_owner <> NEW.created_by THEN
        RAISE EXCEPTION 'tag % must belong to its creator %', NEW.tag_id, NEW.created_by
            USING ERRCODE = '23514';
    END IF;

    IF v_source_owner = NEW.created_by THEN
        RETURN NEW;
    END IF;

    IF v_course_id IS NOT NULL THEN
        SELECT role INTO v_role
          FROM app.course_members
         WHERE course_id = v_course_id AND user_id = NEW.created_by;
        IF v_role IN ('owner', 'editor') THEN
            RETURN NEW;
        END IF;
    END IF;

    RAISE EXCEPTION 'user % cannot tag conversation %', NEW.created_by, NEW.conversation_id
        USING ERRCODE = '42501';
END;
$$;

CREATE TRIGGER trg_conversation_tags_access
    BEFORE INSERT OR UPDATE OF conversation_id, tag_id, created_by ON app.conversation_tags
    FOR EACH ROW EXECUTE FUNCTION app.check_conversation_tag_access();

-- 只有会话所有者或课程 owner/editor 才能批注。
CREATE OR REPLACE FUNCTION app.check_annotation_access() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_source_owner bigint;
    v_course_id    bigint;
    v_role         text;
BEGIN
    SELECT s.owner_id, c.course_id
      INTO v_source_owner, v_course_id
      FROM app.messages m
      JOIN app.conversations c ON c.id = m.conversation_id
      JOIN app.sources s ON s.id = c.source_id
     WHERE m.id = NEW.message_id;

    IF v_source_owner = NEW.user_id THEN
        RETURN NEW;
    END IF;

    IF v_course_id IS NOT NULL THEN
        SELECT role INTO v_role
          FROM app.course_members
         WHERE course_id = v_course_id AND user_id = NEW.user_id;
        IF v_role IN ('owner', 'editor') THEN
            RETURN NEW;
        END IF;
    END IF;

    RAISE EXCEPTION 'user % cannot annotate message %', NEW.user_id, NEW.message_id
        USING ERRCODE = '42501';
END;
$$;

CREATE TRIGGER trg_annotations_access
    BEFORE INSERT OR UPDATE OF message_id, user_id ON app.annotations
    FOR EACH ROW EXECUTE FUNCTION app.check_annotation_access();

-- 复习项必须由对批注所属会话有编辑权限的用户创建。
CREATE OR REPLACE FUNCTION app.check_review_access() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_source_owner bigint;
    v_course_id    bigint;
    v_role         text;
BEGIN
    SELECT s.owner_id, c.course_id
      INTO v_source_owner, v_course_id
      FROM app.annotations a
      JOIN app.messages m ON m.id = a.message_id
      JOIN app.conversations c ON c.id = m.conversation_id
      JOIN app.sources s ON s.id = c.source_id
     WHERE a.id = NEW.annotation_id;

    IF v_source_owner = NEW.user_id THEN
        RETURN NEW;
    END IF;

    IF v_course_id IS NOT NULL THEN
        SELECT role INTO v_role
          FROM app.course_members
         WHERE course_id = v_course_id AND user_id = NEW.user_id;
        IF v_role IN ('owner', 'editor') THEN
            RETURN NEW;
        END IF;
    END IF;

    RAISE EXCEPTION 'user % cannot review annotation %', NEW.user_id, NEW.annotation_id
        USING ERRCODE = '42501';
END;
$$;

CREATE TRIGGER trg_review_items_access
    BEFORE INSERT OR UPDATE OF annotation_id, user_id ON app.review_items
    FOR EACH ROW EXECUTE FUNCTION app.check_review_access();

-- 批注更新时间
CREATE OR REPLACE FUNCTION app.set_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_annotations_updated_at
    BEFORE UPDATE ON app.annotations
    FOR EACH ROW EXECUTE FUNCTION app.set_updated_at();
