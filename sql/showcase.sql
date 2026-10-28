-- 个人学习空间：报告用关键 SQL
-- 用法：docker compose exec -T db psql -U postgres -d learning_space -f /dev/stdin < sql/showcase.sql
-- 或直接复制单条语句执行。除标注 BEGIN/ROLLBACK 的演示外，全部为只读查询。
-- 演示账号 alice 的 id 通过 username 子查询取得，避免硬编码。

\pset footer off

-- ---------------------------------------------------------------------------
-- 1. 数据范围：alice 可访问的会话按来源汇总
--    本人来源 OR 共享课程成员；这是全站权限过滤的公共模式。
-- ---------------------------------------------------------------------------
SELECT s.platform, count(*) AS conversations,
       sum((SELECT count(*) FROM app.messages m WHERE m.conversation_id = c.id)) AS messages
  FROM app.conversations c
  JOIN app.sources s ON s.id = c.source_id
 WHERE s.owner_id = (SELECT id FROM app.users WHERE username = 'alice')
    OR (c.course_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM app.course_members cm
         WHERE cm.course_id = c.course_id
           AND cm.user_id = (SELECT id FROM app.users WHERE username = 'alice')
    ))
 GROUP BY s.platform
 ORDER BY s.platform;

-- ---------------------------------------------------------------------------
-- 2. 课程维度统计 + 排名 + 条件聚合
--    RANK() 按消息数排名；FILTER 分别统计提问人与助手消息。
-- ---------------------------------------------------------------------------
WITH me AS (
    SELECT id FROM app.users WHERE username = 'alice'
), ac AS (
    SELECT c.id
      FROM app.courses c
      JOIN app.course_members cm ON cm.course_id = c.id
      JOIN me ON me.id = cm.user_id
    UNION
    SELECT c.id
      FROM app.conversations c
      JOIN app.sources s ON s.id = c.source_id
      JOIN me ON me.id = s.owner_id
     WHERE c.course_id IS NOT NULL
)
SELECT c.code, c.name, cm.role,
       count(DISTINCT conv.id) AS conversations,
       count(DISTINCT m.id)    AS messages,
       count(DISTINCT m.id) FILTER (WHERE m.sender = 'user')      AS user_messages,
       count(DISTINCT m.id) FILTER (WHERE m.sender = 'assistant') AS assistant_messages,
       count(DISTINCT a.id)    AS annotations,
       RANK() OVER (ORDER BY count(DISTINCT m.id) DESC, c.code) AS msg_rank
  FROM ac
  JOIN app.courses c ON c.id = ac.id
  JOIN app.course_members cm
       ON cm.course_id = c.id
      AND cm.user_id = (SELECT id FROM app.users WHERE username = 'alice')
  LEFT JOIN app.conversations conv ON conv.course_id = c.id
  LEFT JOIN app.messages m ON m.conversation_id = conv.id
  LEFT JOIN app.annotations a ON a.message_id = m.id
 GROUP BY c.id, c.code, c.name, cm.role
 ORDER BY msg_rank, c.code;

-- ---------------------------------------------------------------------------
-- 3. 最近 8 个月活跃度：generate_series 补齐没有消息的月份
-- ---------------------------------------------------------------------------
WITH bounds AS (
    SELECT date_trunc('month', current_date) - interval '7 months' AS start_month
), months AS (
    SELECT generate_series(start_month,
                           date_trunc('month', current_date),
                           interval '1 month') AS month_start
      FROM bounds
), buckets AS (
    SELECT date_trunc('month', m.created_at) AS month_start,
           count(*) AS message_count
      FROM app.messages m
      JOIN app.conversations c ON c.id = m.conversation_id
      JOIN app.sources s ON s.id = c.source_id
     WHERE s.owner_id = (SELECT id FROM app.users WHERE username = 'alice')
       AND m.created_at >= (SELECT start_month FROM bounds)
     GROUP BY 1
)
SELECT to_char(mo.month_start, 'YYYY-MM') AS month,
       coalesce(b.message_count, 0) AS messages
  FROM months mo
  LEFT JOIN buckets b USING (month_start)
 ORDER BY mo.month_start;

-- ---------------------------------------------------------------------------
-- 4. 标签 TopN：标签 → 会话标签 → 会话 → 来源/课程权限
-- ---------------------------------------------------------------------------
SELECT t.name, t.kind,
       count(DISTINCT ct.conversation_id) AS conversations,
       count(DISTINCT m.id) AS messages
  FROM app.tags t
  JOIN app.conversation_tags ct ON ct.tag_id = t.id
  JOIN app.conversations c ON c.id = ct.conversation_id
  JOIN app.sources s ON s.id = c.source_id
  LEFT JOIN app.messages m ON m.conversation_id = c.id
 WHERE t.owner_id = (SELECT id FROM app.users WHERE username = 'alice')
   AND (s.owner_id = (SELECT id FROM app.users WHERE username = 'alice')
        OR c.course_id IN (SELECT course_id FROM app.course_members
                            WHERE user_id = (SELECT id FROM app.users WHERE username = 'alice')))
 GROUP BY t.id, t.name, t.kind
 ORDER BY conversations DESC, messages DESC, t.name
 LIMIT 8;

-- ---------------------------------------------------------------------------
-- 5. 批注解决率：COUNT(*) FILTER
-- ---------------------------------------------------------------------------
SELECT kind,
       count(*) AS total,
       count(*) FILTER (WHERE status = 'resolved') AS resolved,
       round(100.0 * count(*) FILTER (WHERE status = 'resolved') / count(*), 1) AS rate
  FROM app.annotations
 GROUP BY kind
 ORDER BY kind;

-- ---------------------------------------------------------------------------
-- 6. 复习队列：按状态汇总，区分逾期与今日到期
-- ---------------------------------------------------------------------------
SELECT status,
       count(*) AS total,
       count(*) FILTER (WHERE due_date < current_date) AS overdue,
       count(*) FILTER (WHERE due_date = current_date) AS due_today,
       min(due_date) AS earliest
  FROM app.review_items
 WHERE user_id = (SELECT id FROM app.users WHERE username = 'alice')
 GROUP BY status
 ORDER BY status;

-- ---------------------------------------------------------------------------
-- 7. 每个会话的最新一条消息：DISTINCT ON
-- ---------------------------------------------------------------------------
SELECT DISTINCT ON (m.conversation_id)
       m.conversation_id, c.title, m.seq_no, m.sender,
       left(m.content, 42) AS preview, m.created_at
  FROM app.messages m
  JOIN app.conversations c ON c.id = m.conversation_id
 ORDER BY m.conversation_id, m.seq_no DESC
 LIMIT 5;

-- ---------------------------------------------------------------------------
-- 8. 跨会话重复内容检测：GROUP BY md5(content) HAVING count(*) > 1
-- ---------------------------------------------------------------------------
SELECT md5(content) AS hash,
       count(*) AS occurrences,
       count(DISTINCT conversation_id) AS conversations,
       left(min(content), 36) AS sample
  FROM app.messages
 GROUP BY md5(content)
HAVING count(*) > 1
 ORDER BY occurrences DESC, hash
 LIMIT 5;

-- ---------------------------------------------------------------------------
-- 9. 会话上下文窗口：按 seq_no 取第 6–8 条
-- ---------------------------------------------------------------------------
SELECT m.seq_no, m.sender, left(m.content, 46) AS content
  FROM app.messages m
 WHERE m.conversation_id = 7
   AND m.seq_no BETWEEN 6 AND 8
 ORDER BY m.seq_no;

-- ---------------------------------------------------------------------------
-- 10. 关键词检索：标题或消息命中，LATERAL 取最早命中消息，count(*) OVER() 取总数
-- ---------------------------------------------------------------------------
SELECT v.id, v.title, c.code AS course,
       (SELECT count(*) FROM app.messages m WHERE m.conversation_id = v.id) AS msgs,
       hit.seq_no, left(hit.content, 50) AS hit,
       count(*) OVER () AS total
  FROM app.conversations v
  JOIN app.sources s ON s.id = v.source_id
  LEFT JOIN app.courses c ON c.id = v.course_id
  LEFT JOIN LATERAL (
      SELECT m.seq_no, m.content
        FROM app.messages m
       WHERE m.conversation_id = v.id
         AND m.content ILIKE '%事务%'
       ORDER BY m.seq_no
       LIMIT 1
  ) hit ON true
 WHERE (s.owner_id = (SELECT id FROM app.users WHERE username = 'alice')
        OR (v.course_id IS NOT NULL AND EXISTS (
            SELECT 1 FROM app.course_members cm
             WHERE cm.course_id = v.course_id
               AND cm.user_id = (SELECT id FROM app.users WHERE username = 'alice'))))
   AND (v.title ILIKE '%事务%'
        OR EXISTS (SELECT 1 FROM app.messages mq
                    WHERE mq.conversation_id = v.id
                      AND mq.content ILIKE '%事务%'))
 ORDER BY v.id
 LIMIT 10;

-- ---------------------------------------------------------------------------
-- 11. 共享权限：bob 能看到操作系统（editor）与数据库（viewer）各 6 个会话
-- ---------------------------------------------------------------------------
SELECT c.code, count(*) AS visible_conversations
  FROM app.conversations v
  JOIN app.sources s ON s.id = v.source_id
  LEFT JOIN app.courses c ON c.id = v.course_id
 WHERE s.owner_id = (SELECT id FROM app.users WHERE username = 'bob')
    OR (v.course_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM app.course_members cm
         WHERE cm.course_id = v.course_id
           AND cm.user_id = (SELECT id FROM app.users WHERE username = 'bob')))
 GROUP BY c.code
 ORDER BY c.code;

-- ---------------------------------------------------------------------------
-- 12. Text2SQL 权限视图：同一视图，不同 app.user_id 得到不同行
-- ---------------------------------------------------------------------------
BEGIN;
SELECT set_config('app.user_id',
                  (SELECT id::text FROM app.users WHERE username = 'alice'), true);
SELECT code, name, conversation_count, message_count, annotation_count
  FROM t2s.v_my_course_stats
 ORDER BY message_count DESC, code;
COMMIT;

BEGIN;
SELECT set_config('app.user_id',
                  (SELECT id::text FROM app.users WHERE username = 'bob'), true);
SELECT code, name, my_role, conversation_count, message_count
  FROM t2s.v_my_courses
 ORDER BY code;
COMMIT;

-- ---------------------------------------------------------------------------
-- 13. 导入批次：从 summary JSONB 提取新增/跳过计数
-- ---------------------------------------------------------------------------
SELECT b.id, b.file_name, b.status, s.platform,
       (b.summary ->> 'conversations_new')::int AS conv_new,
       (b.summary ->> 'messages_new')::int AS msg_new,
       (b.summary ->> 'messages_skipped')::int AS skipped,
       b.created_at::date
  FROM app.import_batches b
  JOIN app.sources s ON s.id = b.source_id
 WHERE b.user_id = (SELECT id FROM app.users WHERE username = 'alice')
 ORDER BY b.created_at DESC
 LIMIT 6;

-- ---------------------------------------------------------------------------
-- 14. 导入幂等：会话按 (source_id, external_id) 冲突则更新，消息冲突则跳过
--     BEGIN/ROLLBACK 仅用于演示，实际由 import_service.confirm_import 调用。
-- ---------------------------------------------------------------------------
BEGIN;
WITH target AS (
    SELECT s.id AS source_id, c.external_id
      FROM app.conversations c
      JOIN app.sources s ON s.id = c.source_id
     WHERE s.owner_id = (SELECT id FROM app.users WHERE username = 'alice')
       AND s.platform = 'qoder'
     ORDER BY c.id
     LIMIT 1
)
INSERT INTO app.conversations (source_id, course_id, external_id, title)
SELECT source_id, NULL, external_id, '演示：重复导入标题'
  FROM target
ON CONFLICT (source_id, external_id)
DO UPDATE SET title = EXCLUDED.title
RETURNING id, title;

INSERT INTO app.messages (conversation_id, seq_no, sender, content)
VALUES (7, 1, 'user', '重复序号演示')
ON CONFLICT (conversation_id, seq_no) DO NOTHING
RETURNING id;
ROLLBACK;

-- ---------------------------------------------------------------------------
-- 15. 复习间隔：单条 UPDATE 完成读改写，间隔 1 → 2 天
-- ---------------------------------------------------------------------------
BEGIN;
SELECT id, interval_days, due_date, review_count, status
  FROM app.review_items WHERE id = 17;
UPDATE app.review_items
   SET review_count = review_count + 1,
       interval_days = LEAST(interval_days * 2, 60),
       due_date = current_date + LEAST(interval_days * 2, 60),
       status = CASE WHEN status = 'pending' THEN 'reviewing' ELSE status END,
       last_reviewed_at = now()
 WHERE id = 17
   AND user_id = (SELECT id FROM app.users WHERE username = 'alice')
 RETURNING interval_days, due_date, review_count, status;
ROLLBACK;

-- ---------------------------------------------------------------------------
-- 16. 中文检索执行计划：默认顺序扫描 vs 强制索引扫描
-- ---------------------------------------------------------------------------
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF, TIMING OFF)
SELECT id FROM app.messages WHERE content ILIKE '%MVCC%';

SET enable_seqscan = off;
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF, TIMING OFF)
SELECT id FROM app.messages WHERE content ILIKE '%MVCC%';
RESET enable_seqscan;

-- ---------------------------------------------------------------------------
-- 17. 提问人与助手消息比例（按课程）
-- ---------------------------------------------------------------------------
SELECT co.name AS course, m.sender, count(*) AS messages
  FROM app.messages m
  JOIN app.conversations c ON c.id = m.conversation_id
  JOIN app.sources s ON s.id = c.source_id
  LEFT JOIN app.courses co ON co.id = c.course_id
 WHERE s.owner_id = (SELECT id FROM app.users WHERE username = 'alice')
 GROUP BY co.name, m.sender
 ORDER BY co.name, m.sender;

-- ---------------------------------------------------------------------------
-- 18. 到期复习清单：CASE 生成“逾期 / 今日到期”
-- ---------------------------------------------------------------------------
SELECT ri.id, co.code AS course, a.kind,
       left(a.content, 24) AS annotation,
       ri.due_date, ri.status,
       CASE WHEN ri.status <> 'mastered' AND ri.due_date < current_date THEN '逾期'
            WHEN ri.status <> 'mastered' AND ri.due_date = current_date THEN '今日到期'
            ELSE '—' END AS urgency
  FROM app.review_items ri
  JOIN app.annotations a ON a.id = ri.annotation_id
  JOIN app.messages m ON m.id = a.message_id
  JOIN app.conversations c ON c.id = m.conversation_id
  LEFT JOIN app.courses co ON co.id = c.course_id
 WHERE ri.user_id = (SELECT id FROM app.users WHERE username = 'alice')
   AND ri.status <> 'mastered'
 ORDER BY ri.due_date, ri.id
 LIMIT 8;

-- ---------------------------------------------------------------------------
-- 19. 每门课恰好一个 owner（若破坏约束会报 23514）
-- ---------------------------------------------------------------------------
SELECT c.id, c.code, count(*) AS owner_count
  FROM app.courses c
  LEFT JOIN app.course_members cm
         ON cm.course_id = c.id AND cm.role = 'owner'
 GROUP BY c.id, c.code
 ORDER BY c.id;

-- ---------------------------------------------------------------------------
-- 20. Text2SQL 审计：成功 / 拒绝 / 执行失败
-- ---------------------------------------------------------------------------
SELECT status, count(*) AS n,
       round(avg(latency_ms)) AS avg_ms,
       sum(row_count) AS rows
  FROM app.text2sql_log
 GROUP BY status
 ORDER BY status;

-- ---------------------------------------------------------------------------
-- 21. 知识先修闭包：递归 CTE + 最短深度
-- ---------------------------------------------------------------------------
WITH RECURSIVE mine AS (
    SELECT id FROM app.tags
     WHERE owner_id = (SELECT id FROM app.users WHERE username = 'alice')
), walk AS (
    SELECT t.id AS tag_id, t.id AS prereq_id, 0 AS depth, ARRAY[t.id] AS path
      FROM app.tags t JOIN mine ON mine.id = t.id
    UNION ALL
    SELECT w.tag_id, e.from_tag_id, w.depth + 1, w.path || e.from_tag_id
      FROM walk w
      JOIN app.tag_edges e ON e.to_tag_id = w.prereq_id
     WHERE NOT e.from_tag_id = ANY (w.path)
), best AS (
    SELECT DISTINCT ON (tag_id, prereq_id) tag_id, prereq_id, depth
      FROM walk WHERE depth >= 2
     ORDER BY tag_id, prereq_id, depth
)
SELECT rt.name AS tag, pt.name AS prerequisite, b.depth
  FROM best b
  JOIN app.tags rt ON rt.id = b.tag_id
  JOIN app.tags pt ON pt.id = b.prereq_id
 ORDER BY rt.name, b.depth;

-- ---------------------------------------------------------------------------
-- 22. 审计元数据：谁、何时、对哪张表的哪条记录、改了哪些列
--     只记录列名，不记录列值；password_hash 被触发器显式排除。
-- ---------------------------------------------------------------------------
SELECT occurred_at::date AS day,
       coalesce(u.username, 'system') AS actor,
       a.table_name, a.action, a.row_pk, a.changed_columns
  FROM app.audit_log a
  LEFT JOIN app.users u ON u.id = a.actor_user_id
 ORDER BY a.id
 LIMIT 6;

-- 应用角色只能读，不能改；清理函数最少保留 90 天
SELECT has_table_privilege('ls_app', 'app.audit_log', 'SELECT') AS can_read,
       has_table_privilege('ls_app', 'app.audit_log', 'INSERT') AS can_insert;

-- ---------------------------------------------------------------------------
-- 23. 数据库观测：统计视图与白名单执行计划
--     观测页只读这些视图，不读取消息/批注正文，也不接受任意 SQL。
-- ---------------------------------------------------------------------------
SELECT pg_size_pretty(pg_database_size(current_database())) AS size,
       round(100.0 * blks_hit / nullif(blks_hit + blks_read, 0), 2) AS hit_pct,
       numbackends, xact_commit, xact_rollback
  FROM pg_stat_database
 WHERE datname = current_database();

SELECT relname, seq_scan, idx_scan, n_live_tup, n_dead_tup,
       pg_size_pretty(pg_total_relation_size(relid)) AS size
  FROM pg_stat_user_tables
 WHERE schemaname = 'app'
 ORDER BY pg_total_relation_size(relid) DESC
 LIMIT 5;

SELECT relname, indexrelname, idx_scan,
       pg_size_pretty(pg_relation_size(indexrelid)) AS size
  FROM pg_stat_user_indexes
 WHERE schemaname = 'app'
 ORDER BY idx_scan, pg_relation_size(indexrelid) DESC
 LIMIT 5;

-- 扩展未预加载时可跳过；页面会显示降级提示
SELECT left(regexp_replace(query, '\s+', ' ', 'g'), 120) AS query,
       calls, round(mean_exec_time::numeric, 2) AS mean_ms
  FROM pg_stat_statements
 WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
   AND query NOT ILIKE '%pg_stat_statements%'
   AND query NOT ILIKE '%EXPLAIN%'
   AND query ~* '^\s*(select|with|insert|update|delete)(\s|$)'
 ORDER BY mean_exec_time DESC NULLS LAST, calls DESC
 LIMIT 5;

EXPLAIN (FORMAT JSON)
SELECT r.id, r.due_date, r.interval_days
  FROM app.review_items r
 WHERE r.user_id = (SELECT id FROM app.users WHERE username = 'alice')
   AND r.status <> 'mastered'
   AND r.due_date <= current_date
 ORDER BY r.due_date, r.id
 LIMIT 20;
