-- Text2SQL 专用权限视图（schema t2s）
-- 视图以属主（postgres）权限读取 app 基表，t2s_reader 只被授予这些视图的 SELECT。
-- 当前用户来自事务内的 set_config('app.user_id', ..., true)；未设置时返回 0 行。

CREATE SCHEMA IF NOT EXISTS t2s;

-- 当前用户可访问的课程
CREATE OR REPLACE VIEW t2s.v_my_courses WITH (security_barrier = true) AS
WITH me AS (
    SELECT nullif(current_setting('app.user_id', true), '')::bigint AS uid
)
SELECT c.id AS course_id,
       c.code,
       c.name,
       c.semester,
       c.instructor,
       c.description,
       cm.role AS my_role,
       c.created_at,
       (SELECT count(*) FROM app.conversations conv WHERE conv.course_id = c.id) AS conversation_count,
       (SELECT count(*)
          FROM app.messages m
          JOIN app.conversations conv ON conv.id = m.conversation_id
         WHERE conv.course_id = c.id) AS message_count
FROM app.courses c
JOIN app.course_members cm ON cm.course_id = c.id
CROSS JOIN me
WHERE cm.user_id = me.uid;

-- 当前用户可访问的会话
CREATE OR REPLACE VIEW t2s.v_my_conversations WITH (security_barrier = true) AS
WITH me AS (
    SELECT nullif(current_setting('app.user_id', true), '')::bigint AS uid
)
SELECT conv.id AS conversation_id,
       conv.title,
       conv.external_id,
       conv.course_id,
       co.code AS course_code,
       co.name AS course_name,
       s.platform AS source_platform,
       s.account_label,
       conv.started_at,
       conv.ended_at,
       conv.is_archived,
       (SELECT count(*) FROM app.messages m WHERE m.conversation_id = conv.id) AS message_count,
       (SELECT array_agg(t.name ORDER BY t.name)
          FROM app.conversation_tags ct
          JOIN app.tags t ON t.id = ct.tag_id
         WHERE ct.conversation_id = conv.id) AS tag_names
FROM app.conversations conv
JOIN app.sources s ON s.id = conv.source_id
LEFT JOIN app.courses co ON co.id = conv.course_id
CROSS JOIN me
WHERE s.owner_id = me.uid
   OR (conv.course_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM app.course_members cm
         WHERE cm.course_id = conv.course_id AND cm.user_id = me.uid
   ));

-- 当前用户可访问的消息
CREATE OR REPLACE VIEW t2s.v_my_messages WITH (security_barrier = true) AS
WITH me AS (
    SELECT nullif(current_setting('app.user_id', true), '')::bigint AS uid
)
SELECT m.id AS message_id,
       m.conversation_id,
       conv.title AS conversation_title,
       conv.course_id,
       co.name AS course_name,
       m.seq_no,
       m.sender,
       m.content,
       m.created_at
FROM app.messages m
JOIN app.conversations conv ON conv.id = m.conversation_id
JOIN app.sources s ON s.id = conv.source_id
LEFT JOIN app.courses co ON co.id = conv.course_id
CROSS JOIN me
WHERE s.owner_id = me.uid
   OR (conv.course_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM app.course_members cm
         WHERE cm.course_id = conv.course_id AND cm.user_id = me.uid
   ));

-- 当前用户自己的标签
CREATE OR REPLACE VIEW t2s.v_my_tags WITH (security_barrier = true) AS
WITH me AS (
    SELECT nullif(current_setting('app.user_id', true), '')::bigint AS uid
)
SELECT t.id AS tag_id,
       t.name,
       t.kind,
       t.color,
       count(DISTINCT ct.conversation_id) AS conversation_count
FROM app.tags t
CROSS JOIN me
LEFT JOIN app.conversation_tags ct ON ct.tag_id = t.id
WHERE t.owner_id = me.uid
GROUP BY t.id, t.name, t.kind, t.color;

-- 当前用户可访问会话上的标签
CREATE OR REPLACE VIEW t2s.v_my_conversation_tags WITH (security_barrier = true) AS
WITH me AS (
    SELECT nullif(current_setting('app.user_id', true), '')::bigint AS uid
)
SELECT ct.conversation_id,
       conv.title AS conversation_title,
       t.id AS tag_id,
       t.name AS tag_name,
       t.kind AS tag_kind,
       ct.created_by,
       u.display_name AS created_by_name,
       ct.created_at
FROM app.conversation_tags ct
JOIN app.tags t ON t.id = ct.tag_id
JOIN app.conversations conv ON conv.id = ct.conversation_id
JOIN app.sources s ON s.id = conv.source_id
JOIN app.users u ON u.id = ct.created_by
CROSS JOIN me
WHERE s.owner_id = me.uid
   OR (conv.course_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM app.course_members cm
         WHERE cm.course_id = conv.course_id AND cm.user_id = me.uid
   ));

-- 当前用户可访问会话上的批注
CREATE OR REPLACE VIEW t2s.v_my_annotations WITH (security_barrier = true) AS
WITH me AS (
    SELECT nullif(current_setting('app.user_id', true), '')::bigint AS uid
)
SELECT a.id AS annotation_id,
       a.kind,
       a.status,
       a.content,
       a.created_at,
       a.updated_at,
       m.id AS message_id,
       m.content AS message_content,
       m.sender,
       conv.id AS conversation_id,
       conv.title AS conversation_title,
       co.name AS course_name,
       u.display_name AS author_name,
       (a.user_id = me.uid) AS is_mine
FROM app.annotations a
JOIN app.messages m ON m.id = a.message_id
JOIN app.conversations conv ON conv.id = m.conversation_id
JOIN app.sources s ON s.id = conv.source_id
LEFT JOIN app.courses co ON co.id = conv.course_id
JOIN app.users u ON u.id = a.user_id
CROSS JOIN me
WHERE s.owner_id = me.uid
   OR (conv.course_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM app.course_members cm
         WHERE cm.course_id = conv.course_id AND cm.user_id = me.uid
   ));

-- 当前用户自己的复习项
CREATE OR REPLACE VIEW t2s.v_my_review_items WITH (security_barrier = true) AS
WITH me AS (
    SELECT nullif(current_setting('app.user_id', true), '')::bigint AS uid
)
SELECT ri.id AS review_item_id,
       ri.status,
       ri.due_date,
       ri.interval_days,
       ri.review_count,
       ri.last_reviewed_at,
       a.id AS annotation_id,
       a.kind AS annotation_kind,
       a.content AS annotation_content,
       m.id AS message_id,
       conv.id AS conversation_id,
       conv.title AS conversation_title,
       co.name AS course_name
FROM app.review_items ri
JOIN app.annotations a ON a.id = ri.annotation_id
JOIN app.messages m ON m.id = a.message_id
JOIN app.conversations conv ON conv.id = m.conversation_id
LEFT JOIN app.courses co ON co.id = conv.course_id
CROSS JOIN me
WHERE ri.user_id = me.uid
  AND (
        EXISTS (
            SELECT 1
              FROM app.sources s
             WHERE s.id = conv.source_id AND s.owner_id = me.uid
        )
        OR (conv.course_id IS NOT NULL AND EXISTS (
            SELECT 1 FROM app.course_members cm
             WHERE cm.course_id = conv.course_id AND cm.user_id = me.uid
        ))
  );

-- 课程维度统计
CREATE OR REPLACE VIEW t2s.v_my_course_stats WITH (security_barrier = true) AS
WITH me AS (
    SELECT nullif(current_setting('app.user_id', true), '')::bigint AS uid
),
accessible AS (
    SELECT c.id, c.code, c.name, c.semester
    FROM app.courses c
    JOIN app.course_members cm ON cm.course_id = c.id
    CROSS JOIN me
    WHERE cm.user_id = me.uid
)
SELECT a.id AS course_id,
       a.code,
       a.name,
       a.semester,
       count(DISTINCT conv.id) AS conversation_count,
       count(DISTINCT m.id) AS message_count,
       count(DISTINCT ann.id) AS annotation_count,
       max(m.created_at) AS last_activity
FROM accessible a
LEFT JOIN app.conversations conv ON conv.course_id = a.id
LEFT JOIN app.messages m ON m.conversation_id = conv.id
LEFT JOIN app.annotations ann ON ann.message_id = m.id
GROUP BY a.id, a.code, a.name, a.semester;

-- 近 12 个月活跃度（补齐空月份）
CREATE OR REPLACE VIEW t2s.v_my_monthly_activity WITH (security_barrier = true) AS
WITH me AS (
    SELECT nullif(current_setting('app.user_id', true), '')::bigint AS uid
),
months AS (
    SELECT generate_series(
        date_trunc('month', current_date) - interval '11 months',
        date_trunc('month', current_date),
        interval '1 month'
    ) AS month_start
)
SELECT to_char(mo.month_start, 'YYYY-MM') AS month,
       count(DISTINCT m.id) AS message_count,
       count(DISTINCT conv.id) AS conversation_count,
       count(DISTINCT ann.id) AS annotation_count
FROM months mo
CROSS JOIN me
LEFT JOIN app.messages m
       ON m.created_at >= mo.month_start
      AND m.created_at < mo.month_start + interval '1 month'
LEFT JOIN app.conversations conv ON conv.id = m.conversation_id
LEFT JOIN app.sources s ON s.id = conv.source_id
LEFT JOIN app.annotations ann ON ann.message_id = m.id
WHERE m.id IS NULL
   OR s.owner_id = me.uid
   OR (conv.course_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM app.course_members cm
         WHERE cm.course_id = conv.course_id AND cm.user_id = me.uid
   ))
GROUP BY mo.month_start
ORDER BY mo.month_start;

-- 标签使用统计
CREATE OR REPLACE VIEW t2s.v_my_tag_stats WITH (security_barrier = true) AS
WITH me AS (
    SELECT nullif(current_setting('app.user_id', true), '')::bigint AS uid
)
SELECT t.id AS tag_id,
       t.name,
       t.kind,
       count(DISTINCT ct.conversation_id) AS conversation_count,
       count(DISTINCT m.id) AS message_count
FROM app.tags t
JOIN app.conversation_tags ct ON ct.tag_id = t.id
JOIN app.conversations conv ON conv.id = ct.conversation_id
JOIN app.sources s ON s.id = conv.source_id
LEFT JOIN app.messages m ON m.conversation_id = conv.id
CROSS JOIN me
WHERE t.owner_id = me.uid
  AND (
        s.owner_id = me.uid
        OR (conv.course_id IS NOT NULL AND EXISTS (
            SELECT 1 FROM app.course_members cm
             WHERE cm.course_id = conv.course_id AND cm.user_id = me.uid
        ))
  )
GROUP BY t.id, t.name, t.kind;

-- 提问人/助手消息比例
CREATE OR REPLACE VIEW t2s.v_my_sender_stats WITH (security_barrier = true) AS
WITH me AS (
    SELECT nullif(current_setting('app.user_id', true), '')::bigint AS uid
)
SELECT co.name AS course_name,
       m.sender,
       count(*) AS message_count
FROM app.messages m
JOIN app.conversations conv ON conv.id = m.conversation_id
JOIN app.sources s ON s.id = conv.source_id
LEFT JOIN app.courses co ON co.id = conv.course_id
CROSS JOIN me
WHERE s.owner_id = me.uid
   OR (conv.course_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM app.course_members cm
         WHERE cm.course_id = conv.course_id AND cm.user_id = me.uid
   ))
GROUP BY co.name, m.sender;

-- 当前用户导入批次
CREATE OR REPLACE VIEW t2s.v_my_import_stats WITH (security_barrier = true) AS
WITH me AS (
    SELECT nullif(current_setting('app.user_id', true), '')::bigint AS uid
)
SELECT b.id AS batch_id,
       b.file_name,
       b.status,
       s.platform,
       b.summary,
       b.created_at,
       b.imported_at
FROM app.import_batches b
JOIN app.sources s ON s.id = b.source_id
CROSS JOIN me
WHERE b.user_id = me.uid;

-- 当前用户 Text2SQL 使用记录
CREATE OR REPLACE VIEW t2s.v_my_t2s_stats WITH (security_barrier = true) AS
WITH me AS (
    SELECT nullif(current_setting('app.user_id', true), '')::bigint AS uid
)
SELECT l.id AS log_id,
       l.question,
       l.status,
       l.latency_ms,
       l.row_count,
       l.created_at
FROM app.text2sql_log l
CROSS JOIN me
WHERE l.user_id = me.uid;
