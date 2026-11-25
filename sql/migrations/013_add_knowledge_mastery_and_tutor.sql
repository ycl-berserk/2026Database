-- 学习闭环：会话分类（理论 / 出题练习）、知识点掌握度与答题记录。
-- 1. conversations.mode 区分两类对话；应用内 AI 会话写入同一对基表。
-- 2. knowledge_mastery 保存“用户 × 课程 × 知识点”的 1–10 分掌握度。
-- 3. quiz_attempts 保存出题对话中的每道题、参考答案与评分；
--    评分落库后由触发器增量更新掌握度，保证“根据此类对话推出掌握情况”。

-- ---------------------------------------------------------------------------
-- 1. 来源平台与应用内会话分类
-- ---------------------------------------------------------------------------
ALTER TABLE app.sources DROP CONSTRAINT IF EXISTS sources_platform_check;
ALTER TABLE app.sources
    ADD CONSTRAINT sources_platform_check
    CHECK (platform IN ('qoder', 'chatgpt', 'deepseek', 'claude', 'codex', 'opencode', 'learning_space', 'other'));

ALTER TABLE app.conversations
    ADD COLUMN IF NOT EXISTS mode varchar(10) NOT NULL DEFAULT 'theory';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'conversations_mode_check'
           AND conrelid = 'app.conversations'::regclass
    ) THEN
        ALTER TABLE app.conversations
            ADD CONSTRAINT conversations_mode_check
            CHECK (mode IN ('theory', 'quiz'));
    END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS conversations_course_mode_idx
    ON app.conversations (course_id, mode, started_at DESC NULLS LAST);

-- ---------------------------------------------------------------------------
-- 2. 知识点掌握度：用户 × 课程 × 知识点，1–10 分
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app.knowledge_mastery (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id         bigint      NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
    course_id       bigint      NOT NULL REFERENCES app.courses(id) ON DELETE CASCADE,
    tag_id          bigint      NOT NULL REFERENCES app.tags(id) ON DELETE CASCADE,
    score           smallint    NOT NULL DEFAULT 1,
    last_score      smallint,
    sample_count    integer     NOT NULL DEFAULT 0,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT knowledge_mastery_score_check CHECK (score BETWEEN 1 AND 10),
    CONSTRAINT knowledge_mastery_last_score_check
        CHECK (last_score IS NULL OR last_score BETWEEN 1 AND 10),
    CONSTRAINT knowledge_mastery_sample_count_check CHECK (sample_count >= 0),
    CONSTRAINT knowledge_mastery_unique UNIQUE (user_id, course_id, tag_id)
);

CREATE INDEX IF NOT EXISTS knowledge_mastery_course_idx
    ON app.knowledge_mastery (course_id, user_id, score);
CREATE INDEX IF NOT EXISTS knowledge_mastery_tag_idx
    ON app.knowledge_mastery (tag_id);

-- 掌握度只能由本人、针对自己可参与的课程、且该标签确实出现在该课程会话上。
CREATE OR REPLACE FUNCTION app.check_mastery_access() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM app.tags WHERE id = NEW.tag_id) THEN
        RAISE EXCEPTION 'mastery references a missing tag %', NEW.tag_id
            USING ERRCODE = '23503';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM app.course_members
         WHERE course_id = NEW.course_id AND user_id = NEW.user_id
    ) THEN
        RAISE EXCEPTION 'user % is not a member of course %', NEW.user_id, NEW.course_id
            USING ERRCODE = '42501';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM app.conversation_tags ct
          JOIN app.conversations c ON c.id = ct.conversation_id
         WHERE ct.tag_id = NEW.tag_id
           AND c.course_id = NEW.course_id
    ) THEN
        RAISE EXCEPTION 'knowledge point % is not linked to course %',
            NEW.tag_id, NEW.course_id USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_knowledge_mastery_access ON app.knowledge_mastery;
CREATE TRIGGER trg_knowledge_mastery_access
    BEFORE INSERT OR UPDATE OF user_id, course_id, tag_id ON app.knowledge_mastery
    FOR EACH ROW EXECUTE FUNCTION app.check_mastery_access();

-- ---------------------------------------------------------------------------
-- 3. 出题对话的答题记录（一道题一行，先出题、后评分）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app.quiz_attempts (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id             bigint      NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
    course_id           bigint      NOT NULL REFERENCES app.courses(id) ON DELETE CASCADE,
    tag_id              bigint      NOT NULL REFERENCES app.tags(id) ON DELETE CASCADE,
    conversation_id     bigint      NOT NULL REFERENCES app.conversations(id) ON DELETE CASCADE,
    question_message_id bigint      NOT NULL REFERENCES app.messages(id) ON DELETE CASCADE,
    answer_message_id   bigint      REFERENCES app.messages(id) ON DELETE SET NULL,
    reference_answer    text        NOT NULL,
    key_points          text[]      NOT NULL DEFAULT '{}',
    score               smallint,
    feedback            text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    answered_at         timestamptz,
    CONSTRAINT quiz_attempts_score_check CHECK (score IS NULL OR score BETWEEN 1 AND 10),
    CONSTRAINT quiz_attempts_reference_nonempty CHECK (length(btrim(reference_answer)) > 0),
    CONSTRAINT quiz_attempts_state_check CHECK (
        (score IS NULL AND answer_message_id IS NULL AND answered_at IS NULL)
        OR (score IS NOT NULL AND answered_at IS NOT NULL)
    ),
    CONSTRAINT quiz_attempts_question_unique UNIQUE (question_message_id)
);

CREATE INDEX IF NOT EXISTS quiz_attempts_user_idx
    ON app.quiz_attempts (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS quiz_attempts_course_tag_idx
    ON app.quiz_attempts (course_id, tag_id, created_at DESC);
CREATE INDEX IF NOT EXISTS quiz_attempts_conversation_idx
    ON app.quiz_attempts (conversation_id);

-- 答题记录必须落在本人拥有的 quiz 会话中，且题目/答案消息属于同一会话。
CREATE OR REPLACE FUNCTION app.check_quiz_attempt_access() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_owner      bigint;
    v_course_id  bigint;
    v_mode       text;
    v_message_cv bigint;
BEGIN
    SELECT s.owner_id, c.course_id, c.mode
      INTO v_owner, v_course_id, v_mode
      FROM app.conversations c
      JOIN app.sources s ON s.id = c.source_id
     WHERE c.id = NEW.conversation_id;

    IF v_owner IS NULL OR v_owner <> NEW.user_id THEN
        RAISE EXCEPTION 'quiz conversation % is not owned by user %',
            NEW.conversation_id, NEW.user_id USING ERRCODE = '42501';
    END IF;
    IF v_course_id IS DISTINCT FROM NEW.course_id THEN
        RAISE EXCEPTION 'quiz conversation % does not belong to course %',
            NEW.conversation_id, NEW.course_id USING ERRCODE = '23514';
    END IF;
    IF v_mode <> 'quiz' THEN
        RAISE EXCEPTION 'conversation % is not a quiz conversation', NEW.conversation_id
            USING ERRCODE = '23514';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM app.tags WHERE id = NEW.tag_id) THEN
        RAISE EXCEPTION 'quiz references a missing tag %', NEW.tag_id
            USING ERRCODE = '23503';
    END IF;
    IF NOT EXISTS (
        SELECT 1
          FROM app.conversation_tags ct
          JOIN app.conversations c ON c.id = ct.conversation_id
         WHERE ct.tag_id = NEW.tag_id
           AND c.course_id = NEW.course_id
    ) THEN
        RAISE EXCEPTION 'quiz tag % is not linked to course %',
            NEW.tag_id, NEW.course_id USING ERRCODE = '23514';
    END IF;

    SELECT conversation_id INTO v_message_cv
      FROM app.messages WHERE id = NEW.question_message_id;
    IF v_message_cv IS DISTINCT FROM NEW.conversation_id THEN
        RAISE EXCEPTION 'question message % is not in conversation %',
            NEW.question_message_id, NEW.conversation_id USING ERRCODE = '23514';
    END IF;

    IF NEW.answer_message_id IS NOT NULL THEN
        SELECT conversation_id INTO v_message_cv
          FROM app.messages WHERE id = NEW.answer_message_id;
        IF v_message_cv IS DISTINCT FROM NEW.conversation_id THEN
            RAISE EXCEPTION 'answer message % is not in conversation %',
                NEW.answer_message_id, NEW.conversation_id USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_quiz_attempts_access ON app.quiz_attempts;
CREATE TRIGGER trg_quiz_attempts_access
    BEFORE INSERT OR UPDATE OF user_id, course_id, tag_id, conversation_id,
        question_message_id, answer_message_id
    ON app.quiz_attempts
    FOR EACH ROW EXECUTE FUNCTION app.check_quiz_attempt_access();

-- 评分写入后增量更新掌握度：首次取本次得分，之后按 0.6×旧分 + 0.4×本次 平滑。
CREATE OR REPLACE FUNCTION app.apply_mastery_from_attempt() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.score IS NULL THEN
        RETURN NEW;
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.score IS NOT DISTINCT FROM NEW.score THEN
        RETURN NEW;
    END IF;

    INSERT INTO app.knowledge_mastery
        (user_id, course_id, tag_id, score, last_score, sample_count, updated_at)
    VALUES
        (NEW.user_id, NEW.course_id, NEW.tag_id, NEW.score, NEW.score, 1, now())
    ON CONFLICT (user_id, course_id, tag_id) DO UPDATE
       SET score = (
               GREATEST(
                   1,
                   LEAST(
                       10,
                       round(0.6 * app.knowledge_mastery.score + 0.4 * EXCLUDED.score)
                   )
               )
           )::smallint,
           last_score = EXCLUDED.last_score,
           sample_count = app.knowledge_mastery.sample_count + 1,
           updated_at = now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_quiz_attempts_mastery ON app.quiz_attempts;
CREATE TRIGGER trg_quiz_attempts_mastery
    AFTER INSERT OR UPDATE OF score ON app.quiz_attempts
    FOR EACH ROW EXECUTE FUNCTION app.apply_mastery_from_attempt();

-- 已有数据库通过 scripts/migrate.py 升级时角色已存在；全新初始化时 roles.sql 会统一授权。
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ls_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON app.knowledge_mastery TO ls_app;
        GRANT USAGE, SELECT ON SEQUENCE app.knowledge_mastery_id_seq TO ls_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON app.quiz_attempts TO ls_app;
        GRANT USAGE, SELECT ON SEQUENCE app.quiz_attempts_id_seq TO ls_app;
        GRANT EXECUTE ON FUNCTION app.check_mastery_access() TO ls_app;
        GRANT EXECUTE ON FUNCTION app.check_quiz_attempt_access() TO ls_app;
        GRANT EXECUTE ON FUNCTION app.apply_mastery_from_attempt() TO ls_app;
    END IF;
END;
$$;
