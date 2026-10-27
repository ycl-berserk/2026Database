# 演示脚本：个人学习空间（5–8 分钟）

> 目标：在 8 分钟内讲清「聊天记录 → 课程资料 → 检索批注 → 复习统计」的完整闭环，并让数据库设计点可见、可验证。
> 演示数据全部为模拟问答，不含真实个人信息；管理员只展示聚合信息，不读取他人正文。

## 0. 演示前检查（不在视频中出镜）

在项目根目录 `learning-space/` 执行：

```bash
docker compose up -d db
docker compose exec db pg_isready -U postgres

# 仅在演示库损坏或需要完全重置时执行；会删除并重建 learning_space 库
.venv/Scripts/python.exe scripts/init_db.py --reset
.venv/Scripts/python.exe scripts/seed_demo.py

.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
curl -s http://127.0.0.1:8000/healthz
```

确认：

- <http://127.0.0.1:8000> 可打开登录页；
- `alice / Alice123!`、`bob / Bob123!`、`admin / Admin123!` 可登录；
- 导入页「最近导入批次」为 5 条已导入记录；
- 统计页显示 5 门课程、30 个会话、420 条消息、66 条批注、59 个标签；
- Text2SQL 页显示「本地规则模式（未配置大模型 Key）」，无 Key 也能完整演示。

录屏建议：1280×720 或 1440×900，浏览器缩放 100%，关闭无关通知；终端字号 16–18 px。

## 1. 时间轴总览

| 时间 | 场景 | 页面 / 文件 | 要讲清的数据库点 |
| --- | --- | --- | --- |
| 0:00–0:35 | 开场与选题 | 登录页 | 业务问题、模拟数据声明 |
| 0:35–1:15 | 登录与概览 | `/login` → `/dashboard` | Session、PBKDF2、按用户过滤 |
| 1:15–2:00 | 课程与共享 | `/courses`、`/courses/1` | 课程成员 M:N、owner/editor/viewer |
| 2:00–2:50 | 检索与上下文 | `/conversations?q=隔离级别`、会话详情 | trgm + ILIKE、分页、seq_no 上下文 |
| 2:50–3:50 | 导入与查重 | `/imports`、预览页 | 解析校验、JSONB 预览、唯一键、单事务 |
| 3:50–4:40 | 批注与复习 | `/annotations`、`/reviews` | 1:N、UNIQUE(user_id, annotation_id)、间隔重复 |
| 4:40–5:30 | 统计与导出 | `/stats`、CSV / Markdown | JOIN、GROUP BY、FILTER、窗口函数、CTE |
| 5:30–6:40 | Text2SQL | `/text2sql` | 权限视图、只读角色、SQL 守卫、审计 |
| 6:40–7:20 | 权限边界 | bob 403、admin | 应用层 + SQL 作用域 + 数据库权限 |
| 7:20–7:50 | 设计与验收 | E-R 图、`verify.py` | 8 实体、12 联系、26 项验收 |
| 7:50–8:00 | 收尾 | 报告 / README | 可复现命令与限制 |

如果时间紧，优先保留：**检索 → 导入查重 → 批注复习 → Text2SQL → 权限 403**；统计和 E-R 可各用 20 秒带过。

---

## 2. 逐段脚本

### 0:00–0:35 开场

**画面**：登录页（`docs/screenshots/00-login.png` 可作为封面）。

**口播**：

> 这是《数据库系统概论》大作业选题 2「个人学习空间」。平时我们和 Qoder、ChatGPT、DeepSeek 的问答很分散，这个系统把导出记录解析后，按课程、来源、标签整理，支持检索、批注、间隔复习和统计；另外做了一个受只读权限视图保护的 Text2SQL 页面。
>
> 特别说明：演示数据是我围绕课程知识点生成的模拟聊天记录，不含真实个人信息；系统里也会明确标注这一点。

**操作**：不点击，停留在登录页。

---

### 0:35–1:15 登录与概览

**操作**：

1. 输入 `alice` / `Alice123!`，点击登录。
2. 停在 `/dashboard`，鼠标依次划过四个统计卡片。

**口播**：

> 登录使用 Session Cookie，密码只存 PBKDF2-HMAC-SHA256 哈希，并带 CSRF 校验。
>
> 概览上的课程、会话、消息、待复习数量都是按当前用户实时聚合的，没有在表里存冗余计数。alice 是 5 门课程的 owner，能看到 30 个会话、420 条消息。

**可指出的细节**：

- 右上角显示当前用户与「退出」；
- 下方「近 8 个月学习活跃度」用 `date_trunc` + `generate_series` 补零；
- 「今日复习」直接链接到复习队列。

**备用画面**：`docs/screenshots/01-dashboard.png`。

---

### 1:15–2:00 课程与共享

**操作**：

1. 点击「课程」进入 `/courses`。
2. 打开「DB · 数据库系统概论」。
3. 指向成员区，说明 alice 是 owner，bob 是 viewer。
4. 回到课程详情，展示该课程下的会话、消息和最近活动。

**口播**：

> 课程和用户是多对多，`course_members` 上带角色：owner 可以改课程和共享成员，editor 可以导入、归类、写批注，viewer 只读。
>
> 数据库里用部分唯一索引保证每门课最多一个 owner，并用延迟约束触发器保证不能把最后一个 owner 移除。`courses.created_by` 和当前 owner 是两件事：创建者不等于永久所有者，所有权可以转移。

**如果要展示共享关系**：在课程详情页停留即可，不必现场新增成员，避免改动演示数据。

**备用画面**：`docs/screenshots/02-courses.png`、`03-course-detail.png`。

---

### 2:00–2:50 检索与上下文

**操作**：

1. 点击「会话」，在关键词框输入 `隔离级别`，提交。
2. 结果中打开「事务与隔离级别」。
3. 在会话详情中展示消息序号、发送者、时间、关键词高亮，以及每条消息下的批注。
4. 可选：返回列表搜索 `锁` 再点进结果，或在详情地址后手动加 `?q=锁`，展示高亮效果。

**口播**：

> 检索不是只搜标题：会话标题和消息正文都会匹配。中文没有天然分词，所以这里用 PostgreSQL 的 `pg_trgm` GIN 索引配合 `ILIKE` 做子串检索，查询参数全部参数化。
>
> 打开会话后按 `seq_no` 排序，能围绕命中的消息看上下文；消息和会话是 1:N，`(conversation_id, seq_no)` 唯一，保证同一会话内顺序不重复。

**可补充**：

- 左侧可以按课程、标签、日期过滤；
- 分页使用 `count(*) OVER()` 一次查询拿到总数；
- 命中片段由 SQL 侧截取，避免把整段长回答传到页面。

**备用画面**：`docs/screenshots/04-conversations.png`、`05-conversation-detail.png`。

---

### 2:50–3:50 导入与查重

**操作**：

1. 点击「导入记录」。
2. 选择 `data/samples/duplicate_db.json`，平台保持「跟随文件 / 手动选择」，点击「上传并解析」。
3. 在预览页指出：6 个会话、84 条消息、新增 0、可更新 0、完全重复 6。
4. **不点击确认**，直接返回导入页。
5. 选择 `data/samples/broken.json`，点击「上传并解析」，展示「文件未被接受」和字段级错误。
6. 返回导入页，展示最近 5 条已导入批次。

**口播**：

> 导入分两阶段。第一阶段只解析和校验，把候选会话写进 `import_batches` 的 JSONB 预览，带 30 分钟有效的 token，此时不会碰会话和消息表。
>
> 这个文件是之前已经导入过的同一批记录，系统按 `(source_id, external_id)` 和 `(conversation_id, seq_no)` 判重，所以 6 个会话全部标成重复；即使确认，也只会新增 0 条消息。
>
> 损坏文件在解析阶段就被拒绝：平台枚举不合法、缺少 external_id、消息角色未知、内容为空，都会逐条返回，不会进入预览，更不会入库。
>
> 真正确认时是一个事务：先锁住来源，再 upsert 来源、会话、消息和标签；任何一步失败整体回滚，并把批次标成 failed。

**注意**：这一步会产生一个 preview 批次；录像结束后按第 4 节清理。

**备用画面**：`docs/screenshots/07-imports.png`、`08-import-preview.png`。

---

### 3:50–4:40 批注与复习

**操作**：

1. 点击「批注」进入 `/annotations`。
2. 展示四类批注（笔记 / 疑问 / 纠错 / 总结）和 open / resolved 状态。
3. 找一条还没有复习项的批注（例如网络课程的一条 summary），点击「加入复习」。
4. 进入「复习队列」，指出「今日到期 / 逾期」标记。
5. 对一条到期项点击「复习一次」，展示间隔从 1 天变为 2 天、复习次数加 1。
6. 可选：点击「标记掌握」，展示它从待复习列表消失。

**口播**：

> 批注挂在具体消息上，一条消息可以有多条批注，一个用户也可以批注别人共享课程里的消息。
>
> 复习项和批注是独立的：同一条批注，alice 和 bob 可以各自加入自己的复习队列，所以唯一键是 `(user_id, annotation_id)`，而不是只约束 annotation_id。
>
> 复习一次会在事务里更新到期日、间隔天数和复习次数；间隔按 1、2、4、8 天递增。复习项到期的查询用了 `(user_id, status, due_date)` 的部分索引，掌握后的条目不再进入热点扫描。

**备用画面**：`docs/screenshots/09-annotations.png`、`10-reviews.png`。

---

### 4:40–5:30 统计与导出

**操作**：

1. 点击「统计」。
2. 从上到下快速指：课程排名、近 8 个月活跃度、标签 Top 12、批注解决率、复习状态。
3. 指向「重复消息检测」，说明这是刻意保留的模板句。
4. 点击右上角「导出统计 CSV」，展示下载。
5. 回到某个会话详情，点击「导出 Markdown」，展示带声明的导出文件。

**口播**：

> 统计页基本覆盖了课程里的聚合知识点：多表 JOIN、GROUP BY、`COUNT(*) FILTER` 算解决率、`RANK() OVER` 做课程排名、`generate_series` 补齐没有消息的月份，以及 `GROUP BY md5(content) HAVING count(*) > 1` 做重复内容检测。
>
> 这里所有计数都是实时查询，不落冗余计数列；Text2SQL 日志里的 `row_count` 是执行结果快照，属于有意的审计字段。
>
> 导出 CSV 和 Markdown 都只导出当前用户有权访问的数据，导出文件里也带模拟数据声明。

**备用画面**：`docs/screenshots/11-stats.png`。

---

### 5:30–6:40 Text2SQL

**操作**：

1. 点击「Text2SQL」。
2. 指出页面顶部的「本地规则模式（未配置大模型 Key）」。
3. 点击示例 chip「每个课程有多少会话和消息？」，提交。
4. 展示：生成的 SQL、解释、结果表格、耗时 / 行数。
5. 再提交「哪些复习项今天到期？」。
6. 刷新页面，向下滚动到历史记录，指出刚才两次查询已写入审计。
7. 口播安全设计，不需要现场输入恶意 SQL；可切到 `tests/integration/test_text2sql.py` 或报告第 8 节展示拒绝用例。

**口播**：

> 没有配置大模型 Key 时，走本地规则映射，保证离线也能演示；配置 Key 后走 OpenAI 兼容接口，失败会自动回退。
>
> 生成 SQL 后不是直接执行：先要求恰好一条 SELECT/WITH，拒绝 DDL/DML、系统表、`pg_` 函数和注释；再包一层 200 行上限，交给 `t2s_reader` 在只读事务里执行，语句超时 3 秒。
>
> 最关键的是权限兜底：`t2s_reader` 对 `app` schema 没有任何权限，只能读 `t2s` 下 13 个 `security_barrier` 视图；视图通过 `current_setting('app.user_id')` 过滤。所以即使模型生成了越权 SQL，数据库层也拿不到别人的数据，更写不进去。
>
> 每次成功、拒绝或异常都会写 `text2sql_log`，包括问题、SQL、状态、耗时和行数。

**备用画面**：`docs/screenshots/12-text2sql.png`、`13-text2sql-result.png`、`13b-text2sql-history.png`。

---

### 6:40–7:20 权限边界

**操作**：

1. 退出 alice，登录 `bob` / `Bob123!`。
2. 打开「数据库系统概论」课程详情，指出 bob 是 viewer，没有编辑入口。
3. 地址栏直接访问 `/courses/1/edit`，展示 403 页面（`docs/screenshots/15-permission-denied.png`）。
4. 可选：退出 bob，登录 `admin` / `Admin123!`，进入 `/admin`，指出只有用户状态和聚合计数，没有会话正文。
5. 可选：admin 直接访问 `/conversations`，同样 403。

**口播**：

> 权限做了三层：页面和服务层先判角色；SQL 查询再按 `course_members` 和 `sources.owner_id` 限制作用域；数据库层还有外键、CHECK、触发器和只读角色兜底。viewer 直接改 URL 也会被服务层拦截。
>
> 管理员只做账号启停和系统聚合统计，不读取他人消息正文、批注或复习内容；管理员访问个人会话页会得到 403。停用账号也不会删除历史数据。

**备用画面**：`docs/screenshots/14-admin.png`、`15-permission-denied.png`。

---

### 7:20–7:50 设计与验收

**操作**：

1. 打开 `docs/er-chen.svg` 或 `docs/screenshots/16-er-chen.png`，指出 8 个核心实体和 12 组联系。
2. 打开 `docs/design.md`，快速展示 12 张表、范式分析和删除策略。
3. 在终端执行验收脚本：

```bash
.venv/Scripts/python.exe scripts/verify.py --json data/verification/report.json
```

4. 指出输出 `26/26 PASS` 与报告路径。

**口播**：

> 概念设计有 8 个核心实体：用户、课程、来源、会话、消息、标签、批注、复习项；另外还有课程成员、导入批次和 Text2SQL 日志这些操作型实体。批注和复习项按弱实体处理。
>
> 逻辑设计上，标签、课程成员、会话标签都拆成关联表，满足 1NF；核心表用单列代理键，关联表的非键属性依赖整个复合键；会话不冗余 owner，消息不冗余课程和来源，避免传递依赖。`import_batches.payload` 是预览期的受控冗余，导入完成后清理。
>
> 验收脚本覆盖表与约束、演示数据、登录权限、导入幂等、检索、批注复习、统计、Text2SQL 拒绝与跨用户隔离、导出和事务回滚，共 26 项。

**备用画面**：`docs/screenshots/16-er-chen.png`、`docs/verification/report.json`（实际路径 `data/verification/report.json`）。

---

### 7:50–8:00 收尾

**画面**：README 的「快速开始」或报告结论页。

**口播**：

> 整个项目可以用 README 里的五条命令从零复现：启动数据库、初始化 schema、写入模拟数据、跑测试、启动网站。报告、E-R 图、需求拆解、关键 SQL、测试与 AI 协作记录都在 `docs/` 下。
>
> 已知限制是单机课程项目，没有做高并发；中文检索在 420 条消息的规模下规划器通常选顺序扫描，索引能力通过关闭顺序扫描的执行计划对比来验证。谢谢。

---

## 3. 现场故障与备用方案

| 现象 | 处理 |
| --- | --- |
| 网站打不开 | 检查 uvicorn 是否在 8000 端口；`curl http://127.0.0.1:8000/healthz` |
| 数据库未启动 | `docker compose up -d db`，等待 healthy；必要时 `docker compose logs db` |
| 登录失败 | 确认使用 `Alice123!` 等演示密码；若演示库被改乱，重置后再 seed |
| 文件选择器找不到样例 | 直接把 `data/samples/duplicate_db.json` 拖进文件选择器；或提前复制到桌面 |
| 重复导入不是全重复 | 说明演示库已被重置或样例被改过；重新 seed 后再演示 |
| Text2SQL 没有真实模型 | 页面会明确显示本地规则模式；按示例 chip 提问即可 |
| 大模型调用超时 | 自动回退本地规则，页面仍能出结果；口播说明回退机制 |
| 终端验收脚本失败 | 先跑 `pytest -q` 定位；演示时可直接展示 `data/verification/report.json` 的已有结果 |
| 现场网络不稳定 | 全站不依赖外网；只要本地 PostgreSQL 和 uvicorn 正常即可 |
| 时间不够 | 跳过 admin、Markdown 导出和 E-R 细讲，保留导入查重、复习、Text2SQL、403 |

## 4. 录像后的清理

截图或演示可能留下预览批次和 Text2SQL 审计记录，不影响业务数据，但会让「最近导入批次」多出一条 preview。需要恢复干净演示库时执行：

```bash
docker compose exec -T db psql -U postgres -d learning_space \
  -c "DELETE FROM app.import_batches WHERE status = 'preview';" \
  -c "DELETE FROM app.text2sql_log;"
```

如果演示中点击了「确认导入」「加入复习」「复习一次」或停用了账号，建议直接重置：

```bash
.venv/Scripts/python.exe scripts/init_db.py --reset
.venv/Scripts/python.exe scripts/seed_demo.py
.venv/Scripts/python.exe scripts/verify.py --json data/verification/report.json
```

> `init_db.py --reset` 会删除并重建 `learning_space` 与 `learning_space_test`，只用于本地演示环境。

## 5. 录屏实现（可选）

已有 Playwright 巡览脚本，可自动录制一段无讲解的页面漫游：

```bash
npm install
npx playwright install chromium
node scripts/screenshots.mjs --record
```

产物：`docs/demo.webm`。自动巡览只覆盖页面切换，正式提交建议按本脚本配音；两者可以叠加：先用自动录屏做底片，再按时间轴补口播。

## 6. 页面与截图对照

| 脚本段落 | 页面 | 截图 |
| --- | --- | --- |
| 开场 / 登录 | `/login` | `00-login.png` |
| 概览 | `/dashboard` | `01-dashboard.png` |
| 课程 | `/courses`、`/courses/1` | `02-courses.png`、`03-course-detail.png` |
| 检索 | `/conversations`、`/conversations/{id}` | `04-conversations.png`、`05-conversation-detail.png` |
| 标签 | `/tags` | `06-tags.png` |
| 导入 | `/imports`、预览页 | `07-imports.png`、`08-import-preview.png` |
| 批注 / 复习 | `/annotations`、`/reviews` | `09-annotations.png`、`10-reviews.png` |
| 统计 | `/stats` | `11-stats.png` |
| Text2SQL | `/text2sql` | `12-text2sql.png`、`13-text2sql-result.png`、`13b-text2sql-history.png` |
| 管理 / 越权 | `/admin`、403 页 | `14-admin.png`、`15-permission-denied.png` |
| 概念设计 | E-R 图 | `16-er-chen.png` |
