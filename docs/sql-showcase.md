# 关键 SQL 与执行结果

完整可执行脚本见 [../sql/showcase.sql](../sql/showcase.sql)。以下结果来自演示库 `learning_space`（3 用户、5 课程、30 会话、420 消息、59 标签、66 批注、39 复习项）。带 `BEGIN/ROLLBACK` 的示例不会改变演示库。

## 1. 数据范围：本人来源或共享课程

```sql
SELECT s.platform, count(*) AS conversations,
       sum((SELECT count(*) FROM app.messages m WHERE m.conversation_id = c.id)) AS messages
  FROM app.conversations c
  JOIN app.sources s ON s.id = c.source_id
 WHERE s.owner_id = (SELECT id FROM app.users WHERE username = 'alice')
    OR (c.course_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM app.course_members cm
         WHERE cm.course_id = c.course_id
           AND cm.user_id = (SELECT id FROM app.users WHERE username = 'alice')))
 GROUP BY s.platform ORDER BY s.platform;
```

| platform | conversations | messages |
| --- | ---: | ---: |
| chatgpt | 12 | 168 |
| deepseek | 6 | 84 |
| qoder | 12 | 168 |

这个 `s.owner_id = uid OR EXISTS (course_members)` 条件在会话列表、详情、统计、导出和 Text2SQL 视图中反复使用，是全站数据范围的一致口径。

## 2. 课程统计、排名与条件聚合

```sql
WITH ac AS (
    SELECT c.id FROM app.courses c
      JOIN app.course_members cm ON cm.course_id = c.id
     WHERE cm.user_id = :uid
    UNION
    SELECT c.id FROM app.conversations c
      JOIN app.sources s ON s.id = c.source_id
     WHERE s.owner_id = :uid AND c.course_id IS NOT NULL
)
SELECT c.code, c.name, cm.role,
       count(DISTINCT conv.id) AS conversations,
       count(DISTINCT m.id)    AS messages,
       count(DISTINCT m.id) FILTER (WHERE m.sender = 'user')      AS user_messages,
       count(DISTINCT m.id) FILTER (WHERE m.sender = 'assistant') AS assistant_messages,
       count(DISTINCT a.id)    AS annotations,
       RANK() OVER (ORDER BY count(DISTINCT m.id) DESC, c.code)  AS msg_rank
  FROM ac
  JOIN app.courses c ON c.id = ac.id
  JOIN app.course_members cm ON cm.course_id = c.id AND cm.user_id = :uid
  LEFT JOIN app.conversations conv ON conv.course_id = c.id
  LEFT JOIN app.messages m ON m.conversation_id = conv.id
  LEFT JOIN app.annotations a ON a.message_id = m.id
 GROUP BY c.id, c.code, c.name, cm.role
 ORDER BY msg_rank, c.code;
```

| code | name | role | conversations | messages | user_messages | assistant_messages | annotations | msg_rank |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| DB | 数据库系统概论 | owner | 6 | 84 | 42 | 42 | 12 | 1 |
| DS | 数据结构 | owner | 6 | 84 | 42 | 42 | 12 | 2 |
| ML | 机器学习 | owner | 6 | 84 | 42 | 42 | 12 | 3 |
| NET | 计算机网络 | owner | 6 | 84 | 42 | 42 | 12 | 4 |
| OS | 操作系统 | owner | 6 | 84 | 42 | 42 | 18 | 5 |

覆盖知识点：`LEFT JOIN` 保留零数据课程、`COUNT(DISTINCT)` 防止多表连接放大计数、`FILTER` 条件聚合、窗口函数 `RANK()`。

## 3. 月度活跃度：补齐空月份

```sql
WITH bounds AS (
    SELECT date_trunc('month', current_date) - interval '7 months' AS start_month
), months AS (
    SELECT generate_series(start_month, date_trunc('month', current_date),
                           interval '1 month') AS month_start FROM bounds
), buckets AS (
    SELECT date_trunc('month', m.created_at) AS month_start, count(*) AS message_count
      FROM app.messages m
      JOIN app.conversations c ON c.id = m.conversation_id
      JOIN app.sources s ON s.id = c.source_id
     WHERE s.owner_id = :uid AND m.created_at >= (SELECT start_month FROM bounds)
     GROUP BY 1
)
SELECT to_char(mo.month_start, 'YYYY-MM') AS month,
       coalesce(b.message_count, 0) AS messages
  FROM months mo LEFT JOIN buckets b USING (month_start)
 ORDER BY mo.month_start;
```

| month | messages |
| --- | ---: |
| 2026-03 | 42 |
| 2026-04 | 42 |
| 2026-05 | 56 |
| 2026-06 | 70 |
| 2026-07 | 70 |
| 2026-08 | 56 |
| 2026-09 | 42 |
| 2026-10 | 0 |

`generate_series` 生成月份骨架，`LEFT JOIN` 后 `coalesce` 补零；2026-10 没有模拟消息，因此显示 0，而不是缺行。

## 4. 标签 TopN

```sql
SELECT t.name, t.kind,
       count(DISTINCT ct.conversation_id) AS conversations,
       count(DISTINCT m.id) AS messages
  FROM app.tags t
  JOIN app.conversation_tags ct ON ct.tag_id = t.id
  JOIN app.conversations c ON c.id = ct.conversation_id
  JOIN app.sources s ON s.id = c.source_id
  LEFT JOIN app.messages m ON m.conversation_id = c.id
 WHERE t.owner_id = :uid
   AND (s.owner_id = :uid OR c.course_id IN (
        SELECT course_id FROM app.course_members WHERE user_id = :uid))
 GROUP BY t.id, t.name, t.kind
 ORDER BY conversations DESC, messages DESC, t.name
 LIMIT 8;
```

| name | kind | conversations | messages |
| --- | --- | ---: | ---: |
| TCP | topic | 2 | 28 |
| AVL | topic | 1 | 14 |
| B+树 | topic | 1 | 14 |
| BFS | topic | 1 | 14 |
| DNS | topic | 1 | 14 |
| Dijkstra | topic | 1 | 14 |
| FCFS | topic | 1 | 14 |
| HTTPS | topic | 1 | 14 |

页面按 usage 绘制条形图；Top12 中 TCP 覆盖 2 个会话，其余标签各覆盖 1 个会话。

## 5. 批注解决率

```sql
SELECT kind, count(*) AS total,
       count(*) FILTER (WHERE status = 'resolved') AS resolved,
       round(100.0 * count(*) FILTER (WHERE status = 'resolved') / count(*), 1) AS rate
  FROM app.annotations
 GROUP BY kind ORDER BY kind;
```

| kind | total | resolved | rate |
| --- | ---: | ---: | ---: |
| correction | 15 | 5 | 33.3 |
| note | 18 | 5 | 27.8 |
| question | 18 | 5 | 27.8 |
| summary | 15 | 5 | 33.3 |

## 6. 复习队列：逾期与今日到期

```sql
SELECT status, count(*) AS total,
       count(*) FILTER (WHERE due_date < current_date) AS overdue,
       count(*) FILTER (WHERE due_date = current_date) AS due_today,
       min(due_date) AS earliest
  FROM app.review_items
 WHERE user_id = :uid
 GROUP BY status ORDER BY status;
```

| status | total | overdue | due_today | earliest |
| --- | ---: | ---: | ---: | --- |
| mastered | 7 | 7 | 0 | 2026-10-25 |
| pending | 19 | 5 | 5 | 2026-10-26 |
| reviewing | 9 | 2 | 2 | 2026-10-26 |

`mastered` 项的过期日期只用于历史展示，不进入“待复习”筛选；页面的“今日复习”只取未掌握且 `due_date <= current_date` 的项。

## 7. 每个会话的最新消息：DISTINCT ON

```sql
SELECT DISTINCT ON (m.conversation_id)
       m.conversation_id, c.title, m.seq_no, m.sender,
       left(m.content, 42) AS preview, m.created_at
  FROM app.messages m
  JOIN app.conversations c ON c.id = m.conversation_id
 ORDER BY m.conversation_id, m.seq_no DESC
 LIMIT 5;
```

| conversation_id | title | seq_no | sender | preview |
| ---: | --- | ---: | --- | --- |
| 1 | SQL 执行顺序与 JOIN | 14 | assistant | 报表页的“课程消息量 TopN”就是这一执行顺序的典型应用。 |
| 2 | 索引与 B+ 树 | 14 | assistant | 学习空间的消息检索就使用 trigram 索引支持中文子串查询。 |
| 3 | 事务与隔离级别 | 14 | assistant | 导入批次用单事务保证不会出现“有会话没消息”的半成品。 |
| 4 | 范式与函数依赖 | 14 | assistant | 本系统的会话表不冗余来源所有者，而是通过 source 关联取得。 |
| 5 | 视图与权限 | 14 | assistant | 本系统的 t2s schema 就是这一设计：只读角色只能查询视图。 |

## 8. 重复消息检测

```sql
SELECT md5(content) AS hash, count(*) AS occurrences,
       count(DISTINCT conversation_id) AS conversations,
       left(min(content), 36) AS sample
  FROM app.messages
 GROUP BY md5(content)
HAVING count(*) > 1
 ORDER BY occurrences DESC, hash LIMIT 5;
```

| occurrences | conversations | sample |
| ---: | ---: | --- |
| 30 | 30 | 这里最容易出错的地方是什么？ |
| 30 | 30 | 能结合一个具体场景解释吗？ |
| 30 | 30 | 它和哪个知识点最容易混淆？ |
| 30 | 30 | 如果放到真实项目里，这个知识点的价值在哪？ |
| 30 | 30 | 用一句话帮我记住它。 |

模拟数据刻意复用了通用追问句，因此这 5 句在每个会话中各出现一次。检测本身用于真实数据中的“重复粘贴/重复导出”。

## 9. 会话上下文窗口

```sql
SELECT m.seq_no, m.sender, left(m.content, 46) AS content
  FROM app.messages m
 WHERE m.conversation_id = 7
   AND m.seq_no BETWEEN 6 AND 8
 ORDER BY m.seq_no;
```

| seq_no | sender | content |
| ---: | --- | --- |
| 6 | assistant | 最常见的坑是：认为线程一定更快。线程共享地址空间，错误会互相影响，CPU 密集任务还受核数限制。 |
| 7 | user | 考试里如果换一种问法，我该怎么判断？ |
| 8 | assistant | 先定位题目问的是调度、同步还是内存：调度题画甘特图；同步题写信号量初值与 P/V 顺序；内存题先画页表… |

详情页以 `seq_no` 排序，按需取命中位置前后窗口；批注通过 `annotations.message_id` 内联到对应消息。

## 10. 中文关键词检索与总数

```sql
SELECT v.id, v.title, c.code AS course,
       (SELECT count(*) FROM app.messages m WHERE m.conversation_id = v.id) AS msgs,
       hit.seq_no, left(hit.content, 50) AS hit,
       count(*) OVER () AS total
  FROM app.conversations v
  JOIN app.sources s ON s.id = v.source_id
  LEFT JOIN app.courses c ON c.id = v.course_id
  LEFT JOIN LATERAL (
      SELECT m.seq_no, m.content FROM app.messages m
       WHERE m.conversation_id = v.id AND m.content ILIKE '%事务%'
       ORDER BY m.seq_no LIMIT 1
  ) hit ON true
 WHERE (s.owner_id = :uid OR (v.course_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM app.course_members cm
         WHERE cm.course_id = v.course_id AND cm.user_id = :uid)))
   AND (v.title ILIKE '%事务%' OR EXISTS (
        SELECT 1 FROM app.messages mq
         WHERE mq.conversation_id = v.id AND mq.content ILIKE '%事务%'))
 ORDER BY v.id LIMIT 10;
```

| id | title | course | msgs | seq_no | hit | total |
| ---: | --- | --- | ---: | ---: | --- | ---: |
| 2 | 索引与 B+ 树 | DB | 14 | 4 | 可以。围绕「索引与 B+ 树」，一个直观的场景是：在 messages(content) 上建立 g… | 4 |
| 3 | 事务与隔离级别 | DB | 14 | 4 | 可以。围绕「事务与隔离级别」，一个直观的场景是：导入时一个事务同时写会话、消息和标签… | 4 |
| 6 | 并发控制与锁 | DB | 14 | 2 | 唯一键可以防重复会话与消息，但多条语句仍可能交错。可以用 pg_advisory_xact_lock… | 4 |
| 9 | 死锁 | OS | 14 | 4 | 可以。围绕「死锁」，一个直观的场景是：两个事务都按 id 从小到大加锁，就不会形成交叉等待。 | 4 |

实际代码中的 `ILIKE %(pattern)s ESCAPE '\'` 会转义用户输入的 `%`、`_`、`\`，避免通配符注入。

## 11. 共享课程可见范围：bob

```sql
SELECT c.code, count(*) AS visible_conversations
  FROM app.conversations v
  JOIN app.sources s ON s.id = v.source_id
  LEFT JOIN app.courses c ON c.id = v.course_id
 WHERE s.owner_id = (SELECT id FROM app.users WHERE username = 'bob')
    OR (v.course_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM app.course_members cm
         WHERE cm.course_id = v.course_id
           AND cm.user_id = (SELECT id FROM app.users WHERE username = 'bob')))
 GROUP BY c.code ORDER BY c.code;
```

| code | visible_conversations |
| --- | ---: |
| DB | 6 |
| OS | 6 |

bob 在 DB 是 viewer、在 OS 是 editor；两种角色都能读，但写操作在服务层与触发器层被区分。

## 12. Text2SQL 权限视图：同一 SQL，不同用户不同结果

```sql
BEGIN;
SELECT set_config('app.user_id',
                  (SELECT id::text FROM app.users WHERE username = 'alice'), true);
SELECT code, name, conversation_count, message_count, annotation_count
  FROM t2s.v_my_course_stats ORDER BY message_count DESC, code;
COMMIT;
```

alice 结果：

| code | name | conversation_count | message_count | annotation_count |
| --- | --- | ---: | ---: | ---: |
| DB | 数据库系统概论 | 6 | 84 | 12 |
| DS | 数据结构 | 6 | 84 | 12 |
| ML | 机器学习 | 6 | 84 | 12 |
| NET | 计算机网络 | 6 | 84 | 12 |
| OS | 操作系统 | 6 | 84 | 18 |

bob 结果：

| code | name | my_role | conversation_count | message_count |
| --- | --- | --- | ---: | ---: |
| DB | 数据库系统概论 | viewer | 6 | 84 |
| OS | 操作系统 | editor | 6 | 84 |

同一条 `SELECT` 在 `app.user_id` 不同的情况下返回不同行，说明行级权限在数据库视图内完成，而不是依赖前端拼接 `user_id` 条件。

## 13. 导入批次统计

```sql
SELECT b.id, b.file_name, b.status, s.platform,
       (b.summary ->> 'conversations_new')::int AS conv_new,
       (b.summary ->> 'messages_new')::int AS msg_new,
       (b.summary ->> 'messages_skipped')::int AS skipped,
       b.created_at::date
  FROM app.import_batches b
  JOIN app.sources s ON s.id = b.source_id
 WHERE b.user_id = :uid
 ORDER BY b.created_at DESC LIMIT 6;
```

| id | file_name | status | platform | conv_new | msg_new | skipped |
| ---: | --- | --- | --- | ---: | ---: | ---: |
| 1 | qoder_db.json | imported | qoder | 6 | 84 | 0 |
| 2 | chatgpt_os.json | imported | chatgpt | 6 | 84 | 0 |
| 3 | deepseek_net.md | imported | deepseek | 6 | 84 | 0 |
| 4 | qoder_ml.json | imported | qoder | 6 | 84 | 0 |
| 5 | chatgpt_ds.json | imported | chatgpt | 6 | 84 | 0 |

重复确认同一份 `qoder_ml.json` 时，验收脚本得到：新增 0 会话、0 消息，跳过 84 条消息。

## 14. 导入幂等：ON CONFLICT

```sql
-- 会话冲突：更新标题（不产生重复会话）
INSERT INTO app.conversations (source_id, course_id, external_id, title)
SELECT source_id, NULL, external_id, '演示：重复导入标题'
  FROM (SELECT s.id AS source_id, c.external_id
          FROM app.conversations c JOIN app.sources s ON s.id = c.source_id
         WHERE s.owner_id = :uid AND s.platform = 'qoder'
         ORDER BY c.id LIMIT 1) target
ON CONFLICT (source_id, external_id)
DO UPDATE SET title = EXCLUDED.title
RETURNING id, title;

-- 消息冲突：序号已存在，直接跳过
INSERT INTO app.messages (conversation_id, seq_no, sender, content)
VALUES (7, 1, 'user', '重复序号演示')
ON CONFLICT (conversation_id, seq_no) DO NOTHING
RETURNING id;
```

演示事务中的实际输出：

```text
 id |       title
----+--------------------
  1 | 演示：重复导入标题
INSERT 0 1

 id
----
(0 rows)
INSERT 0 0
```

事务结束后 `ROLLBACK`，演示库标题保持不变。实际导入服务还会计数 `messages_skipped` 与内容冲突 `conflicts`。

## 15. 复习间隔：1 → 2 天

```sql
BEGIN;
SELECT id, interval_days, due_date, review_count, status
  FROM app.review_items WHERE id = 17;  -- 1 天，待复习，已复习 1 次
UPDATE app.review_items
   SET review_count = review_count + 1,
       interval_days = LEAST(interval_days * 2, 60),
       due_date = current_date + LEAST(interval_days * 2, 60),
       status = CASE WHEN status = 'pending' THEN 'reviewing' ELSE status END,
       last_reviewed_at = now()
 WHERE id = 17 AND user_id = :uid
 RETURNING interval_days, due_date, review_count, status;
ROLLBACK;
```

| 阶段 | interval_days | due_date | review_count | status |
| --- | ---: | --- | ---: | --- |
| 更新前 | 1 | 2026-10-26 | 1 | pending |
| 更新后 | 2 | 2026-10-29 | 2 | reviewing |

`UPDATE` 右侧使用旧值，所以一条语句同时完成“间隔翻倍、到期日顺延、计数 +1、状态迁移”。

## 16. 中文检索执行计划

演示库只有 420 条消息，规划器默认选择顺序扫描：

```text
Seq Scan on messages (actual rows=1 loops=1)
  Filter: (content ~~* '%MVCC%'::text)
  Rows Removed by Filter: 419
  Buffers: shared hit=9
  Execution Time: 0.236 ms
```

强制关闭顺序扫描后，`pg_trgm` GIN 索引生效：

```text
Bitmap Heap Scan on messages (actual rows=1 loops=1)
  Recheck Cond: (content ~~* '%MVCC%'::text)
  ->  Bitmap Index Scan on messages_content_trgm (actual rows=1 loops=1)
        Index Cond: (content ~~* '%MVCC%'::text)
  Execution Time: 0.160 ms
```

这是数据规模导致的正常现象：小表顺序扫描成本更低；索引为数据量增长保留。报告中应如实说明，而不是声称所有查询都走索引。

## 17. 提问人与助手消息比例

```sql
SELECT co.name AS course, m.sender, count(*) AS messages
  FROM app.messages m
  JOIN app.conversations c ON c.id = m.conversation_id
  JOIN app.sources s ON s.id = c.source_id
  LEFT JOIN app.courses co ON co.id = c.course_id
 WHERE s.owner_id = :uid
 GROUP BY co.name, m.sender ORDER BY co.name, m.sender;
```

五门课程均为 42 条 user + 42 条 assistant；模拟数据按“提问—回答”成对生成，比例 1:1。

## 18. 到期复习清单

```sql
SELECT ri.id, co.code AS course, a.kind, left(a.content, 24) AS annotation,
       ri.due_date, ri.status,
       CASE WHEN ri.status <> 'mastered' AND ri.due_date < current_date THEN '逾期'
            WHEN ri.status <> 'mastered' AND ri.due_date = current_date THEN '今日到期'
            ELSE '—' END AS urgency
  FROM app.review_items ri
  JOIN app.annotations a ON a.id = ri.annotation_id
  JOIN app.messages m ON m.id = a.message_id
  JOIN app.conversations c ON c.id = m.conversation_id
  LEFT JOIN app.courses co ON co.id = c.course_id
 WHERE ri.user_id = :uid AND ri.status <> 'mastered'
 ORDER BY ri.due_date, ri.id LIMIT 8;
```

| id | course | kind | due_date | status | urgency |
| ---: | --- | --- | --- | --- | --- |
| 2 | DB | question | 2026-10-26 | pending | 逾期 |
| 7 | DB | correction | 2026-10-26 | reviewing | 逾期 |
| 12 | DB | summary | 2026-10-26 | pending | 逾期 |
| 17 | OS | note | 2026-10-26 | pending | 逾期 |
| 22 | OS | question | 2026-10-26 | reviewing | 逾期 |
| 27 | NET | correction | 2026-10-26 | pending | 逾期 |
| 32 | NET | summary | 2026-10-26 | pending | 逾期 |
| 3 | DB | correction | 2026-10-27 | pending | 今日到期 |

## 19. 每门课恰好一个 owner

```sql
SELECT c.id, c.code, count(*) AS owner_count
  FROM app.courses c
  LEFT JOIN app.course_members cm
         ON cm.course_id = c.id AND cm.role = 'owner'
 GROUP BY c.id, c.code ORDER BY c.id;
```

| id | code | owner_count |
| ---: | --- | ---: |
| 1 | DB | 1 |
| 2 | OS | 1 |
| 3 | NET | 1 |
| 4 | ML | 1 |
| 5 | DS | 1 |

如果事务结束时 owner 数为 0 或多于 1，延迟约束触发器会抛出 `23514`；部分唯一索引会先阻止“多于 1”。

## 20. Text2SQL 审计

```sql
SELECT status, count(*) AS n, round(avg(latency_ms)) AS avg_ms, sum(row_count) AS rows
  FROM app.text2sql_log GROUP BY status ORDER BY status;
```

种子数据不预置查询日志，执行一次 `verify.py` 或页面查询后才有记录。审计表同时记录成功、被拒绝（`rejected`）与执行失败（`error`）三类状态。

## 21. 知识先修闭包：递归 CTE + 最短深度

```sql
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
```

| tag | prerequisite | depth |
| --- | --- | ---: |
| B+树 | SQL | 2 |
| Dijkstra | 图 | 2 |

直接先修的深度是 1（如 `索引 → SQL`、`最短路径 → 图`），这里过滤 `depth >= 2` 以展示递归展开的间接依赖。`path` 数组既用于防止环路重复展开，也保证闭包不会无限递归；`DISTINCT ON` 让同一对“标签—先修”只保留最短路径。生产查询 `t2s.v_my_tag_prerequisites` 就是这一结构，并额外用 `me.uid` 限定当前用户。

## 22. 审计元数据与只读边界

通用触发器只记录“谁、何时、对哪张表的哪条记录、改了哪些列”，不保存列值，也不审计消息、批注与复习内容。操作者通过事务内 GUC 传入：

```sql
SELECT set_config('app.actor_user_id', %(user_id)s, true);

CREATE TRIGGER trg_courses_audit
  AFTER INSERT OR UPDATE OR DELETE ON app.courses
  FOR EACH ROW EXECUTE FUNCTION app.audit_row_change('id');
```

种子数据中的审计行（`audit_row_change` 用 `to_jsonb(NEW)` 与 `to_jsonb(OLD)` 的键差异计算变更列，并显式排除 `password_hash`）：

```sql
SELECT occurred_at::date AS day, coalesce(u.username, 'system') AS actor,
       a.table_name, a.action, a.row_pk, a.changed_columns
  FROM app.audit_log a
  LEFT JOIN app.users u ON u.id = a.actor_user_id
 ORDER BY a.id
 LIMIT 6;
```

| day | actor | table_name | action | row_pk | changed_columns |
| --- | --- | --- | --- | --- | --- |
| 2026-10-28 | system | users | insert | `{"id": 1}` | created_at, display_name, id, is_active, system_role, username |
| 2026-10-28 | alice | courses | insert | `{"id": 1}` | code, created_at, created_by, description, id, instructor, name, semester |
| 2026-10-28 | alice | course_members | insert | `{"user_id": 1, "course_id": 1}` | course_id, joined_at, role, user_id |

应用角色只能读、不能改；清理函数最少保留 90 天：

```sql
SELECT has_table_privilege('ls_app', 'app.audit_log', 'SELECT') AS can_read,
       has_table_privilege('ls_app', 'app.audit_log', 'INSERT') AS can_insert;
-- t | f

SELECT app.prune_audit_log(90);  -- 删除 90 天前的记录，返回删除行数
```

管理员页面通过 `table_name` / `action` 两个可选条件筛选，并用 `count(*) FILTER` 汇总总量与近 7 天数量；审计表本身不在 `t2s` 视图中暴露，Text2SQL 无法查询。

---

### 覆盖的 SQL 知识点

`WHERE` / `ORDER BY` / `LIMIT`、多表 `JOIN`、`LEFT JOIN`、`LATERAL`、`DISTINCT ON`、`GROUP BY` + `HAVING`、`COUNT(DISTINCT)`、`FILTER`、`CASE`、`COALESCE`、`generate_series`、`date_trunc`、窗口函数 `RANK()` 与 `count(*) OVER()`、CTE、`WITH RECURSIVE`、数组包含、JSONB 取值与键差异、`ON CONFLICT`、表达式索引、部分索引、GIN 索引、`EXPLAIN (ANALYZE, BUFFERS)`、事务级 GUC（`set_config`）与 `SECURITY DEFINER` 触发器。
