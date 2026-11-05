# 演示脚本：个人学习空间（数据库版 · 8 分钟）

> 目标：8 分钟内按「概念模型 → 逻辑设计 → 关键 SQL → 索引与计划 → 访问控制 → 事务 → 触发器与审计」讲清数据库设计。
> 讲法：只讲数据库，不展开桌面打包、扩展、AI 协作和测试数量；每条结论都能用 `sql/showcase.sql` 现场复现。
> 演示数据全部为模拟问答，不含真实个人信息；管理员只展示聚合信息，不读取他人正文。
> PPT 页脚带有每节计时，本脚本的时间轴与之对齐，总计约 7:40。

## 0. 演示前检查（不在视频中出镜）

在项目根目录执行。推荐用 Docker 一条命令起全栈（`app` 容器会自动迁移，空库时播种，以非 root 用户运行）：

```bash
docker compose up -d --build
docker compose exec db pg_isready -U postgres
curl -s http://127.0.0.1:8000/healthz
```

本机 Python 开发方式（便于热重载与调试）：

```bash
docker compose up -d db
docker compose exec db pg_isready -U postgres

# 仅在演示库损坏或需要完全重置时执行；会删除并重建 learning_space 库
.venv/Scripts/python.exe scripts/init_db.py --reset
.venv/Scripts/python.exe scripts/seed_demo.py

.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
curl -s http://127.0.0.1:8000/healthz
```

提前跑一遍关键 SQL，确认终端输出与 `docs/sql-showcase.md` 一致：

```bash
docker compose exec -T db psql -U postgres -d learning_space -f /dev/stdin < sql/showcase.sql
```

确认：

- <http://127.0.0.1:8000> 可打开登录页；
- `alice / Alice123!`、`bob / Bob123!`、`admin / Admin123!` 可登录；
- 统计页显示 5 门课程、30 个会话、420 条消息、66 条批注、59 个标签；
- 导入页「最近导入批次」为 5 条已导入记录；
- bob 直接访问 `/courses/1/edit` 得到 403；
- psql 里 `SELECT count(*) FROM pg_tables WHERE schemaname = 'app';` 返回 17。

> 演示库播种于 2026-10-29。`showcase.sql` 里 `#3` 的月份窗口、`#6/#18` 的逾期标签都依赖 `current_date`，
> 数值随演示日期滚动；口播时不要念死数字，以现场输出为准。

录屏建议：1280×720 或 1440×900，浏览器缩放 100%，关闭无关通知；终端字号 16–18 px。

## 1. 时间轴总览

| 时间 | 场景 | 页面 / 文件 | 要讲清的数据库点 |
| --- | --- | --- | --- |
| 0:00–0:15 | 开场 | 标题页 | 选题、数据声明、讲法（只讲数据库） |
| 0:15–1:00 | 概念模型 | E-R 图 | 8 实体、12 联系、弱实体、三条删除策略 |
| 1:00–2:00 | 逻辑设计 | `docs/design.md` / psql | 17 表、3NF、受控冗余、唯一约束 |
| 2:00–3:15 | 关键 SQL | psql `#2 #3 #21` | 窗口、补零、递归闭包 |
| 3:15–4:15 | 索引与执行计划 | psql `#16` | 54 索引、trgm、部分索引、规划器取舍 |
| 4:15–5:25 | 访问控制 | 浏览器 403 + psql `#12` | 三层防护、`security_barrier` 视图、只读角色 |
| 5:25–6:25 | 事务与并发 | 导入预览 + psql `#14 #15` | 预览不落库、单事务、两个锁、幂等、`FOR UPDATE` |
| 6:25–7:15 | 触发器与审计 | psql `#19 #22` | 防环、恰好一个 owner、只记元数据 |
| 7:15–7:40 | 结论 | 结尾页 | 可复现命令、已知限制 |

时间不够时，优先保留：**#2 排名 → #16 执行计划 → 403/视图过滤 → 导入判重 → owner 约束**；
`#3 补零`、`#21 闭包`、`#22 审计` 可以各用一句带过或直接跳到结论。

## 2. 逐段脚本

### 0:00–0:15 开场

**画面**：PPT 标题页（或登录页）。

**口播**：

> 这是《数据库系统概论》选题 2「个人学习空间」。今天只讲数据库：模型怎么设计、关键 SQL 怎么写、索引和权限怎么落到 PostgreSQL、事务与触发器怎么保证正确性。
>
> 演示数据是围绕课程知识点生成的模拟聊天记录，不含真实个人信息；所有数字都可以用 `sql/showcase.sql` 现场复跑。

---

### 0:15–1:00 概念模型

**画面**：`docs/er-chen.svg`（或 `docs/screenshots/16-er-chen.png`、PPT 第 2 页）。

**操作**：不点击，鼠标依次指「用户—课程」「来源—会话—消息」「标签—先修边」三块。

**口播**：

> 8 个核心实体：用户、课程、来源、会话、消息、标签、批注、复习项；12 组联系。
>
> 用户和课程是多对多，桥表 `course_members` 带角色 owner / editor / viewer；会话和标签也是多对多；批注和复习项按弱实体处理，依附消息存在。
>
> 三条删除策略是有意区分的：消息随会话 CASCADE，因为它是弱实体；课程删除时会话的 `course_id` 置空，保留学习记录；来源下还有会话时 RESTRICT，防止误删整条数据链。
>
> 属主和课程只在来源/会话上存一次，消息不重复存，避免传递依赖。

**可指出的细节**：E-R 图右下角的图例；`docs/design.md` 的删除策略表。

---

### 1:00–2:00 逻辑设计

**画面**：`docs/design.md` 的表清单，或 psql 执行：

```sql
SELECT count(*) FROM pg_tables WHERE schemaname = 'app';
\d app.course_members
```

**口播**：

> 逻辑设计落到 17 张表：15 张业务表，加审计表 `audit_log` 和引导状态表 `user_onboarding`。核心表用单列代理键，关联表用复合主键。
>
> 范式上做到 3NF：会话不冗余属主，消息不冗余课程和来源；表里不存可派生计数，统计全部实时 `count(*)`。
>
> 只有两处受控冗余：`import_batches.payload` 是预览期的 JSONB，确认后清理；`row_count` 是 Text2SQL 审计快照，属于有意的历史记录。
>
> 业务约束尽量交给数据库：会话按 `(source_id, external_id)` 唯一，消息按 `(conversation_id, seq_no)` 唯一，复习项按 `(user_id, annotation_id)` 唯一。
>
> “每门课恰好一个 owner”拆成两半：部分唯一索引保证最多一个；延迟约束触发器在事务结束时检查至少一个，破坏时返回 SQLSTATE 23514。

**可指出的细节**：`course_members` 的复合主键与角色 CHECK；`review_items_queue_idx` 的部分索引条件。

---

### 2:00–3:15 关键 SQL

**画面**：终端 psql，运行 `sql/showcase.sql` 后滚动到对应节（或提前把输出存到文本文件备用）。

**操作**：保持只读，不现场改数据；按 `#2 → #3 → #21` 顺序指结果。

**口播**：

> 这三条查询代表课程里的三类难点。
>
> `#2` 课程排名：`RANK() OVER` 按消息数排名，`FILTER (WHERE sender = 'user')` 同时统计提问和助手消息；窗口函数不会丢明细行。当前输出 5 门课，DB/ML/NET/OS 各 84 条、DS 42 条。
>
> `#3` 活跃度补零：`generate_series` 生成连续 8 个月的骨架，再 `LEFT JOIN` 实际消息桶；没有消息的月份显示 0，而不是少一行。最后两个月是 0，因为种子数据只到 10 月底。
>
> `#21` 先修闭包：`WITH RECURSIVE` 沿 `tag_edges` 展开，`path` 数组防止成环，`DISTINCT ON` 只保留最短深度。32 条边目前得到 10 对间接先修关系，比如「B+树 ← SQL」深度 2、「锁 ← 事务」深度 3。
>
> 这些结果不是截图，都是刚才这条命令现场算出来的。

**可指出的细节**：

- `#2` 的 `count(DISTINCT ...)`：多表 JOIN 后避免重复计数；
- `#3` 的 `coalesce(..., 0)`；
- `#21` 的 `NOT from_tag_id = ANY(path)` 是防环关键。

---

### 3:15–4:15 索引与执行计划

**画面**：终端 psql，运行 `#16`（`EXPLAIN (ANALYZE, BUFFERS)`）。

**操作**：先看默认计划，再执行 `SET enable_seqscan = off` 看 Bitmap 计划，最后 `RESET enable_seqscan`。

**口播**：

> 全库 54 个索引，按访问模式设计：消息正文用 `pg_trgm` GIN 支持中文子串 `ILIKE`；`md5(content)` 用表达式索引做跨会话查重；复习队列用 `(user_id, status, due_date)` 的部分索引，掌握后的条目不再进入扫描；唯一和部分唯一索引还承担业务约束。
>
> 执行计划要诚实：420 行的小表上，规划器选择顺序扫描 `messages`，扫了 419 行才留 1 行，耗时约 0.26 ms；强制关闭顺序扫描后走 trigram 位图索引，约 0.03 ms。
>
> 两个计划都是亚毫秒级，耗时随缓存变化。所以我不宣称“所有查询都走索引”——索引是为数据量增长保留的，小表上顺序扫描是代价模型的正常选择。

**可指出的细节**：`Rows Removed by Filter: 419`；`Buffers: shared hit=9` 对 `Heap Blocks: exact=1`；
`docs/sql-showcase.md` 里记录的实测值。

---

### 4:15–5:25 访问控制（现场操作 A）

**画面**：浏览器 bob 会话 + 终端 psql `#12`。

**操作**：见第 3 节「现场操作 A」。若时间紧，只看 403 页面和一条视图对比查询。

**口播**：

> 权限做了三层。
>
> 应用层判断角色，越权直接 403；SQL 查询再按 `sources.owner_id` 和 `course_members` 限制作用域；数据库层用 `security_barrier` 视图和只读角色兜底。
>
> 同一个 `t2s.v_my_course_stats`，设置 `app.user_id = alice` 看到 5 门课，设置成 bob 只看到 2 门：数据库课程 viewer、操作系统课程 editor。过滤在视图内部完成，不依赖前端拼 WHERE。
>
> Text2SQL 用 `t2s_reader` 执行，它对 `app` schema 没有权限，只能读 15 个 `t2s` 视图，所以模型即使生成越权 SQL 也拿不到别人的数据。我们还做了 RLS 对比实验，用同一规则验证数据库层过滤。
>
> 管理员只看到聚合计数和结构变更审计，不读取会话正文；直接访问个人会话页同样 403。

---

### 5:25–6:25 事务与并发（现场操作 B）

**画面**：导入页预览 + 终端 psql `#14 #15`。

**操作**：见第 3 节「现场操作 B」；随后运行 `#15` 的 `FOR UPDATE` 演示（内含 `ROLLBACK`，不改数据）。

**口播**：

> 导入分两阶段。第一阶段只解析和校验，把候选会话写进 `import_batches` 的 JSONB 预览，带 30 分钟有效的 token，此时不碰会话和消息表。
>
> 这个样例是之前导入过的同一批记录，系统按 `(source_id, external_id)` 和 `(conversation_id, seq_no)` 判重，6 个会话全部标成重复；即使确认，也只新增 0 条消息。损坏文件在解析阶段就会被拒绝，进不了预览。
>
> 真正确认时是一个事务：先 `SELECT ... FOR UPDATE` 锁住批次行防重复确认，再对来源加 `pg_advisory_xact_lock` 串行化同一来源；会话 UPSERT，消息 `ON CONFLICT DO NOTHING`，任一步失败整体回滚。
>
> 复习打卡同样是先锁行再写回：应用层用 FSRS 算出新间隔，数据库只保存结果。示例里逾期 8 天后按 good 评分，间隔从 1 天变成 16 天，stability 16.0835；`FOR UPDATE` 避免并发打卡互相覆盖。

**可指出的细节**：预览页的「新增 0 / 完全重复 6」；`#14` 的 `INSERT 0 1` 与 `INSERT 0 0`；
`#15` 的 `BEGIN/ROLLBACK` 说明数据未被修改。

---

### 6:25–7:15 触发器与审计

**画面**：终端 psql `#19`，再演示一次约束拒绝，最后看 `#22`。

**操作**：

```sql
-- 1) 每门课当前恰好一个 owner
SELECT c.code, count(*) AS owner_count
  FROM app.courses c
  JOIN app.course_members cm ON cm.course_id = c.id AND cm.role = 'owner'
 GROUP BY c.code ORDER BY c.code;

-- 2) 尝试删掉唯一的 owner，提交时报 23514（事务整体回滚）
BEGIN;
DELETE FROM app.course_members WHERE course_id = 1 AND role = 'owner';
COMMIT;
```

**口播**：

> 约束不只写在应用里。
>
> 先修边上的触发器在写入前从后继递归展开，能回到先修就 RAISE，所以脚本、接口和页面共享同一个防环边界；32 条边构成 DAG，闭包实时算，不落冗余表。
>
> “恰好一个 owner”的第二半由延迟约束触发器完成：删掉唯一 owner 后事务可以继续，但 COMMIT 时检查失败，返回 23514，整个事务回滚。
>
> 审计只记元数据：课程、课程成员、账号三张表的 AFTER 触发器记录表名、动作、主键和变更列名；列值不保存，`password_hash` 显式排除。应用角色对 `audit_log` 可读不可写。
>
> 所有触发器合计 10 个：课程创建与成员变更两道 owner 约束、标签/批注/复习项的访问校验、`updated_at`、先修边防环，以及三张结构表的审计；
> 管理员还能看 `pg_stat_*` 和白名单 `EXPLAIN (FORMAT JSON)`，但没有任意 SQL 输入框。

**可指出的细节**：`#19` 的 `owner_count = 1`；错误码 `23514`；`#22` 的 `changed_columns` 只有列名；
`has_table_privilege` 返回 `can_read = t`、`can_insert = f`。

---

### 7:15–7:40 结论

**画面**：PPT 结尾页或 README「快速开始」。

**口播**：

> 项目规模：17 张表、15 个 `security_barrier` 视图、10 个触发器、54 个索引、23 条 showcase SQL、42 项验收检查。
>
> 从零复现只要五条命令：起数据库、初始化 schema、写入模拟数据、跑测试、启动网站。关键 SQL 和实测输出都在 `docs/sql-showcase.md`。
>
> 已知限制：单机课程项目，没做分布式与高并发；420 行数据下多数查询走顺序扫描，索引能力用强制计划对比来验证；数据为模拟；AI 能力可选，未配置时走规则模式。谢谢。

---

## 3. 两个现场操作

### A. 越权与视图过滤（4:15–5:25）

**步骤**：

1. 登录 `bob / Bob123!`，打开「数据库系统概论」课程详情，指出 bob 是 viewer，没有编辑入口。
2. 地址栏直接访问 `/courses/1/edit`，展示 403 页面（`docs/screenshots/15-permission-denied.png`）。
3. 终端执行 `#12` 的前半段（alice）和后半段（bob），对比行数：

```bash
docker compose exec -T db psql -U postgres -d learning_space -f /dev/stdin <<'SQL'
BEGIN;
SELECT set_config('app.user_id',
                  (SELECT id::text FROM app.users WHERE username = 'alice'), true);
SELECT count(*) AS alice_courses FROM t2s.v_my_course_stats;
COMMIT;

BEGIN;
SELECT set_config('app.user_id',
                  (SELECT id::text FROM app.users WHERE username = 'bob'), true);
SELECT count(*) AS bob_courses FROM t2s.v_my_course_stats;
COMMIT;
SQL
```

预期：alice 5 门，bob 2 门（DB viewer、OS editor）。

**备用方案**：若浏览器不便切换账号，只跑 psql 对比，口播说明 bob 的 403 截图在 `docs/screenshots/15-permission-denied.png`。

### B. 重复导入预览（5:25–6:25）

**步骤**：

1. 用 alice 登录，点击「导入记录」。
2. 选择 `data/samples/duplicate_db.json`，平台保持「跟随文件 / 手动选择」，点击「上传并解析」。
3. 在预览页指出：6 个会话、84 条消息、新增 0、完全重复 6。
4. **不点击确认**，直接返回导入页；展示最近 5 条已导入批次。
5. 可选：上传 `data/samples/broken.json`，展示字段级错误与「文件未被接受」。

**备用方案**：若文件选择器不方便，直接展示 `docs/screenshots/08-import-preview.png`，并在 psql 跑 `#13` 看历史批次的 `messages_skipped`。

> 这一操作会留下一个 preview 批次，录像结束后按第 5 节清理。

## 4. 现场故障与备用方案

| 现象 | 处理 |
| --- | --- |
| 网站打不开 | 检查 uvicorn 是否在 8000 端口；`curl http://127.0.0.1:8000/healthz` |
| 数据库未启动 | `docker compose up -d db`，等待 healthy；必要时 `docker compose logs db` |
| 登录失败 | 确认使用 `Alice123!` 等演示密码；若演示库被改乱，重置后再 seed |
| psql 找不到命令 | 一律用 `docker compose exec -T db psql ...`，不依赖宿主机安装 |
| showcase 输出太长 | 只用 `#2 #3 #16 #21` 四段；其余展示 `docs/sql-showcase.md` |
| 月份/逾期数字与文档不同 | `#3 #6 #18` 依赖 `current_date`；以现场输出为准，口播说“窗口随日期滚动” |
| 重复导入不是全重复 | 演示库被重置或样例被改过；重新 seed 后再演示 |
| owner 约束演示报错后会话中断 | 属正常：事务整体回滚；新开一个 psql 会话继续 |
| Text2SQL 没有真实模型 | 页面明确显示本地规则模式；按示例 chip 提问即可 |
| 终端验收脚本失败 | 先跑 `pytest -q` 定位；演示时可直接展示已有 `data/verification/report.json` |
| 时间不够 | 跳过 `#3`、`#21`、`#22` 细讲，保留 `#2`、`#16`、403、导入判重、owner 约束 |

## 5. 录像后的清理

现场操作 B 会留下 preview 批次，可能还有 Text2SQL 审计。清理命令：

```bash
docker compose exec -T db psql -U postgres -d learning_space \
  -c "DELETE FROM app.import_batches WHERE status = 'preview';" \
  -c "DELETE FROM app.text2sql_log;"
```

如果演示中点击了「确认导入」「加入复习」「复习一次」或改了先修边，建议直接重置：

```bash
.venv/Scripts/python.exe scripts/init_db.py --reset
.venv/Scripts/python.exe scripts/seed_demo.py
.venv/Scripts/python.exe scripts/verify.py --json data/verification/report.json
```

> `init_db.py --reset` 会删除并重建 `learning_space` 与 `learning_space_test`，只用于本地演示环境。

## 6. 录屏实现（可选）

已有 Playwright 巡览脚本，可自动录制一段无讲解的页面漫游：

```bash
npm install
npx playwright install chromium
node scripts/screenshots.mjs --record
```

产物：`docs/demo.webm`。自动巡览只覆盖页面切换，正式提交建议按本脚本配音；
也可以先用自动录屏做底片，再按时间轴补口播。

## 7. 页面与截图对照

| 脚本段落 | 页面 / 文件 | 截图 |
| --- | --- | --- |
| 开场 / 登录 | `/login` | `00-login.png` |
| 概念模型 | E-R 图 | `16-er-chen.png` |
| 逻辑设计 | `docs/design.md`、psql | — |
| 关键 SQL | psql `#2 #3 #21` | `docs/sql-showcase.md` |
| 索引与计划 | psql `#16` | `docs/sql-showcase.md` |
| 越权 | `/courses/1/edit` 403 | `15-permission-denied.png` |
| 视图过滤 | psql `#12` | `docs/sql-showcase.md` |
| 导入判重 | `/imports`、预览页 | `07-imports.png`、`08-import-preview.png` |
| 复习写回 | psql `#15` | `docs/sql-showcase.md` |
| owner 约束 / 审计 | psql `#19 #22` | `docs/sql-showcase.md` |
