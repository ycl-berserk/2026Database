# 个人学习空间：把聊天记录变成可复习的课程资料

**课程**：数据库系统概论  
**选题**：选题 2 · 与大模型的聊天记录整理  
**技术栈**：Python 3.12 + FastAPI + Jinja2 + PostgreSQL 16 + psycopg 3  
**演示数据**：全部为模拟问答，不含真实个人信息  
**日期**：2026-10-27

---

## 摘要

本项目实现了一个以“聊天记录”为核心的关系数据库应用系统。系统把 Qoder、ChatGPT、DeepSeek 等平台的问答导出文件解析为统一格式，按课程、来源、标签组织；支持中文检索、会话上下文、批注、间隔复习、统计导出，并额外实现了受数据库只读权限视图保护的 Text2SQL 页面。

数据库包含 12 张业务表、13 个权限视图、5 个触发器、多组唯一/检查/外键约束；应用层使用参数化 SQL 与显式事务，不依赖 ORM。导入流水线采用“解析 → 校验 → 持久化预览 → 单事务确认 → 查重”的结构。系统通过 146 个自动化测试与 29 项端到端验收检查，覆盖 schema、权限、导入幂等、中文检索、统计、批注复习、导出、Text2SQL 安全与事务回滚。

---

## 1. 选题背景与目标

日常使用大模型学习时，有价值的问答散落在不同平台：有的在 Qoder，有的在 ChatGPT，有的在 DeepSeek。它们缺少统一的课程归属、检索入口和复习机制，时间一长就变成“聊过但找不到、看过但记不住”。

本项目要解决三个问题：

1. **归拢**：把不同平台的导出记录解析成统一结构，按课程、来源、标签归档。
2. **复用**：能搜到具体问答、看到上下文、在消息上写批注，并把批注排进复习队列。
3. **可解释**：统计学习轨迹；用 Text2SQL 让自然语言查询自己的数据，同时保证不越权、不写库。

作业要求中的“传统动态网站”和“Text2SQL”两种形态都实现：主界面是服务端渲染的动态网站，Text2SQL 作为独立页面与 JSON API 存在。

## 2. 需求与角色

### 2.1 角色

| 角色 | 说明 | 典型操作 |
| --- | --- | --- |
| 学生（alice） | 课程 owner、数据所有者 | 导入、归类、检索、批注、复习、统计、Text2SQL |
| 共享成员（bob） | 操作系统 editor、数据库 viewer | 阅读共享课程；editor 课程可批注/打标签，viewer 只读 |
| 管理员（admin） | 系统维护者 | 用户启停、系统聚合统计；不读取他人正文 |

### 2.2 功能需求

| 编号 | 需求 | 实现要点 |
| --- | --- | --- |
| R01 | 聊天记录导入 | JSON/Markdown 解析、校验、预览、查重、课程映射、单事务入库 |
| R02 | 课程与知识点 | 课程 CRUD、成员角色、标签 CRUD、会话归类 |
| R03 | 检索与阅读 | 课程/标签/日期/关键词筛选、分页、上下文、关键词高亮 |
| R04 | 批注与复习 | 四类批注、解决状态、复习队列、间隔翻倍、逾期提示 |
| R05 | 统计与导出 | 概览、课程排名、月度补零、标签 TopN、CSV、Markdown |
| R06 | 用户与权限 | 登录/登出、PBKDF2、CSRF、系统角色 + 课程角色 |
| R07 | Text2SQL | 自然语言→SQL、守卫、只读执行、审计历史、mock/LLM |
| R08 | 管理 | 用户启停、系统聚合统计、管理边界 |

更细的输入/输出/异常/代码位置/验收映射见 [requirements.md](requirements.md)。

## 3. 数据来源与模拟数据声明

### 3.1 声明

**本项目没有使用任何真实聊天导出，也没有真实个人信息。** 所有会话、消息、批注、复习内容均为围绕五门课程知识点生成的模拟数据。演示账号密码仅用于本地运行，数据库中只保存 PBKDF2 哈希。页面页脚、Markdown 导出文件均带有“演示数据不含真实个人信息”的声明。

### 3.2 规模

| 对象 | 数量 |
| --- | ---: |
| 用户 | 3 |
| 课程 | 5（DB / OS / NET / ML / DS） |
| 课程成员 | 7 |
| 来源 | 4（qoder / chatgpt / deepseek / bob 的空来源） |
| 会话 | 30（每门课 6 个） |
| 消息 | 420（每个会话 14 条，7 问 7 答） |
| 标签 | 59 |
| 会话标签 | 60 |
| 批注 | 66 |
| 复习项 | 39（alice 35，bob 4） |
| 导入批次 | 5（全部 imported） |
| Text2SQL 日志 | 种子数据 0；运行时追加 |

> 复习项按 `alice` 统计为 35 条（pending 19 + reviewing 9 + mastered 7）；全库 39 条另含 bob 的 4 条共享课程复习项。统计页面按当前用户过滤，因此 alice 看到 35 条。

### 3.3 导入样例

`data/samples/` 下有 7 个文件：

| 文件 | 用途 |
| --- | --- |
| `qoder_db.json` | 数据库课程，Qoder JSON |
| `chatgpt_os.json` | 操作系统课程，ChatGPT JSON |
| `deepseek_net.md` | 计算机网络课程，DeepSeek Markdown |
| `qoder_ml.json` | 机器学习课程，Qoder JSON |
| `chatgpt_ds.json` | 数据结构课程，ChatGPT JSON |
| `duplicate_db.json` | 与 `qoder_db.json` 内容相同，用于查重演示 |
| `broken.json` | 缺字段/非法角色，用于校验失败演示 |

`scripts/make_samples.py` 可复现生成这些文件；`scripts/seed_demo.py` 先建账号/课程/成员，再**走真实导入服务**写入会话与消息，最后生成批注与复习项。这样演示数据也覆盖了导入路径，而不是直接 `INSERT` 全部业务表。

## 4. 概念设计

### 4.1 E-R 图

教材风格 Chen 记法（8 个核心实体、12 组联系）见下图，矢量版为 [er-chen.svg](er-chen.svg)，Mermaid 版为 [er.mmd](er.mmd)。

![核心 E-R 图](screenshots/16-er-chen.png)

### 4.2 核心链路

```text
用户 ──拥有──> 来源 ──导出──> 会话 ──包含──> 消息 ──批注于──> 批注 ──复习──> 复习项
  │                              │                                  │
  └──创建/加入──> 课程 ──归属────┘                                  └──撰写──> 用户
  └──定义──> 标签 ──打标签──> 会话
```

### 4.3 基数与删除策略

| 联系 | 基数 | 删除行为 |
| --- | --- | --- |
| 用户 — 课程（创建） | 1:N | 创建者 RESTRICT，保留历史 |
| 用户 — 课程（加入） | M:N | 成员关系 CASCADE |
| 来源 — 会话 | 1:N | 有会话的来源 RESTRICT |
| 课程 — 会话 | 1:N 可选 | 删课程 SET NULL，保留学习记录 |
| 会话 — 消息 | 1:N | CASCADE |
| 消息 — 批注 | 1:N | CASCADE |
| 批注 — 复习项 | 1:N | CASCADE |
| 会话 — 标签 | M:N | 关联表 CASCADE |

用户不做硬删除，使用 `is_active=false` 停用，避免破坏历史批注、复习与审计记录。

## 5. 逻辑设计与范式

### 5.1 关系模式

12 张表位于 `app` schema：

1. `users`：账号、PBKDF2 哈希、显示名、系统角色、启停状态。
2. `courses`：课程编号、名称、学期、教师、创建者。
3. `course_members`：`(course_id, user_id)` 复合主键 + 课程角色。
4. `sources`：平台账号，`(owner_id, platform, account_label)` 唯一。
5. `import_batches`：导入预览/审计，JSONB payload 暂存，UUID token，30 分钟 TTL。
6. `conversations`：来源下的外部会话，`(source_id, external_id)` 唯一，课程可空。
7. `messages`：`(conversation_id, seq_no)` 唯一，角色受 CHECK 限制。
8. `tags`：个人标签，`(owner_id, name)` 唯一。
9. `conversation_tags`：M:N 关联，带标注人与时间。
10. `annotations`：消息上的批注，四种类型，open/resolved。
11. `review_items`：`(user_id, annotation_id)` 唯一，间隔与到期日。
12. `text2sql_log`：问题、生成 SQL、状态、耗时、行数、错误。

完整列定义、约束与索引见 [design.md](design.md)。

### 5.2 范式分析

- **1NF**：所有列原子；标签、成员、会话标签均用关联表，不存逗号分隔串。
- **2NF**：实体表用单列代理键；`course_members.role/joined_at` 依赖完整复合键，`conversation_tags.created_by/created_at` 同样依赖完整复合键。
- **3NF / BCNF**：会话不冗余 `owner_id`，通过 `source_id → sources.owner_id` 取得；消息不冗余课程/来源；不保存可派生的消息计数；`md5(content)` 用表达式索引而非生成列，避免 `id → content → hash` 传递依赖。
- **受控冗余**：`import_batches.payload` 是预览期暂存，成功后置 NULL；`summary` 是导入计数快照；`text2sql_log.row_count/latency_ms` 是审计快照。

`courses.created_by`（创建者）与 `course_members(owner)`（当前所有者）语义不同，允许所有权转移，因此不是冗余。

### 5.3 完整性约束

- 主键：所有表均有单列或复合主键。
- 外键：覆盖来源、课程、会话、消息、批注、复习等所有关系；删除行为按语义选择 CASCADE / SET NULL / RESTRICT。
- 唯一：用户名、课程 `(created_by, code, semester)`、来源 `(owner_id, platform, label)`、会话 `(source_id, external_id)`、消息 `(conversation_id, seq_no)`、标签 `(owner_id, name)`、复习项 `(user_id, annotation_id)`。
- CHECK：角色枚举、发送者枚举、批注类型/状态、复习状态、时间顺序、文件大小、SHA-256 格式、颜色格式等。
- 部分唯一索引：`course_members(course_id) WHERE role='owner'`。
- 延迟约束触发器：事务结束时每门课恰好一个 owner。
- 访问触发器：打标签、批注、复习时必须具备会话或课程编辑权限。

### 5.4 索引

重点索引：

- `messages`：`(conversation_id, seq_no)` 唯一 B-tree、`content` GIN trgm、`md5(content)` 表达式索引。
- `conversations`：`(course_id, started_at DESC)`、`source_id`、标题 GIN trgm。
- `review_items`：`(user_id, status, due_date) WHERE status <> 'mastered'` 部分索引。
- `annotations`：`(message_id)`、`(user_id, status)`。
- `import_batches`：`(user_id, created_at DESC)`、`(source_id, file_sha256)`。
- `text2sql_log`：`(user_id, created_at DESC)`。

中文检索没有使用 PostgreSQL 原生全文检索，因为其默认分词对中文不友好；改用 `pg_trgm` + `ILIKE`，并对用户输入的 `%`、`_`、`\` 做 `ESCAPE` 处理。

## 6. 功能实现

### 6.1 登录与概览

![登录页](screenshots/00-login.png)

登录使用 Session Cookie `ls_session`，密码以 PBKDF2-HMAC-SHA256（210,000 次迭代）存储；所有 POST 表单校验 CSRF token。登录后的概览展示课程、会话、消息、待复习数量和最近会话、8 个月活跃度、今日复习。

![学习概览](screenshots/01-dashboard.png)

### 6.2 课程与成员

课程列表只显示当前用户作为成员加入的课程；详情页展示角色、成员、会话、标签和统计。owner 可以编辑课程、添加成员、调整角色；每门课必须恰好一个 owner。

![课程列表](screenshots/02-courses.png)

![课程详情](screenshots/03-course-detail.png)

### 6.3 检索与阅读

会话列表支持关键词、课程、标签、日期范围筛选与分页；关键词命中标题或消息正文，列表展示命中片段、消息数、平台与标签。会话详情按 `seq_no` 展示消息，支持按发送者过滤、关键词高亮、内联批注、所属课程与标签编辑。

![会话列表](screenshots/04-conversations.png)

![会话详情](screenshots/05-conversation-detail.png)

### 6.4 导入流水线

导入页支持 JSON 与 Markdown，单文件 ≤ 5 MB、≤ 2000 条消息。上传后先解析并写入 `import_batches`，生成 30 分钟有效的预览；预览页展示会话数、消息数、新增/可更新/完全重复分类，并允许逐会话选择与课程映射。

![导入页](screenshots/07-imports.png)

![导入预览](screenshots/08-import-preview.png)

确认入库在一个事务内完成：锁批次、按来源加 advisory lock、upsert 来源、按 `(source_id, external_id)` 合并会话、按 `(conversation_id, seq_no)` 跳过重复消息、get-or-create 标签、写关联；成功后清空 payload 并标记 imported。任一步失败整体回滚，批次标记 failed。

重复导入 `qoder_ml.json` 的验收结果：新增 0 会话、0 消息，跳过 84 条消息；`broken.json` 被拒绝并返回 8 条校验错误。

### 6.5 批注与复习

批注支持笔记、疑问、纠错、总结四类；可以标记解决、编辑、删除，或加入复习队列。复习队列按到期日排序，展示批注内容、原消息片段、课程和状态；打卡后间隔按 1 → 2 → 4 → 8… 天递增，最长 60 天，也可以标记掌握或重置。

![批注](screenshots/09-annotations.png)

![复习队列](screenshots/10-reviews.png)

### 6.6 统计与导出

统计页实时聚合：概览卡片、课程排名（消息数）、8 个月活跃度（补零）、标签 Top12、批注类型与解决率、复习状态与到期、重复内容检测。所有计数实时 `COUNT`，不保存冗余计数列。

![统计](screenshots/11-stats.png)

导出包括：

- 统计 CSV：UTF-8 BOM，列 `section,item,metric,value`，Excel 直接打开不乱码。
- 单会话 Markdown：标题、课程、平台、账号、标签、消息、内联批注，含模拟数据声明。

### 6.7 Text2SQL

Text2SQL 页面提供示例问题、结果表格、生成的 SQL、解释和最近查询历史。默认无 API Key 时使用本地规则 mock，界面明确标注“本地规则模式（未配置大模型 Key）”；配置 `LLM_API_KEY` 后走 OpenAI 兼容接口。

![Text2SQL 结果](screenshots/13-text2sql-result.png)

![Text2SQL 历史](screenshots/13b-text2sql-history.png)

### 6.8 管理与越权

管理员只看到用户管理与系统聚合统计，不能进入课程/会话页面。bob 作为数据库课程 viewer，访问课程编辑页返回 403。错误页会说明可能原因，并提供“看看我参与的课程”“回到概览”“返回上一页”等恢复入口。

![管理员](screenshots/14-admin.png)

![越权 403](screenshots/15-permission-denied.png)

### 6.9 采集闭环与体验

除了文件导入，系统提供三条更低摩擦的采集路径：手机从任意 App 通过 PWA Share Target 分享文本，粘贴一段问答，或直接上传文件。解析结果统一进入 `import_batches` 暂存，再走同一套预览与单事务入库流程；未指定课程的会话进入收件箱，可批量归入课程。

- 导入向导固定为“选择 → 预览 → 结果”三步，支持拖放上传与粘贴双入口。
- 收件箱只允许移动本人且当前未归类的会话，避免编辑者误动共享课程数据。
- 删除批注、复习项或标签后提供 5 秒撤销；离开页面时兜底提交，避免“看起来删了其实没删”。
- 登录页演示账号可一键进入；新账号概览给出建课、导入、收件箱三步引导。
- 静态资源使用 GZip 与版本化长缓存，首屏 JS 约 8KB。

![收件箱](screenshots/17-inbox.png)

![分享预览](screenshots/18-share.png)

![导入结果](screenshots/19-import-result.png)

![粘贴导入](screenshots/20-import-paste.png)

## 7. 关键 SQL

完整 20 条关键 SQL 及真实结果见 [sql-showcase.md](sql-showcase.md)，可执行脚本见 [../sql/showcase.sql](../sql/showcase.sql)。覆盖：

- 多表 `JOIN`、`LEFT JOIN`、`LATERAL`、`DISTINCT ON`
- `GROUP BY` / `HAVING`、`COUNT(DISTINCT)`、`FILTER`、`CASE`
- `generate_series` + `date_trunc` 补齐月份
- 窗口函数 `RANK()`、`count(*) OVER()`
- CTE、JSONB 取值、`ON CONFLICT`
- `pg_trgm` GIN 索引、表达式索引、部分索引
- `EXPLAIN (ANALYZE, BUFFERS)` 对比顺序扫描与索引扫描

示例：课程统计同时使用条件聚合与窗口函数：

```sql
SELECT c.code, c.name,
       count(DISTINCT m.id) FILTER (WHERE m.sender = 'user')      AS user_messages,
       count(DISTINCT m.id) FILTER (WHERE m.sender = 'assistant') AS assistant_messages,
       RANK() OVER (ORDER BY count(DISTINCT m.id) DESC, c.code)  AS msg_rank
  FROM app.courses c
  LEFT JOIN app.conversations conv ON conv.course_id = c.id
  LEFT JOIN app.messages m ON m.conversation_id = conv.id
 GROUP BY c.id, c.code, c.name
 ORDER BY msg_rank;
```

## 8. Text2SQL 安全设计

Text2SQL 的风险不在“生成得对不对”，而在“生成了危险语句怎么办”。本项目采用多层防护：

1. **Prompt 约束**：只提供 `t2s` 视图与列、关系说明、少样本；要求输出 JSON，只生成单条 SELECT/WITH。
2. **SQL 守卫**：`sqlparse` 拆分必须恰好一条；首关键字必须是 SELECT/WITH；拒绝 DDL/DML/危险关键字/注释；拒绝 `pg_catalog`、`information_schema`、`app.`、`dblink`、`pg_read_file` 等；表名必须在 13 个视图或 CTE 白名单中。
3. **行数包装**：`SELECT * FROM (<sql>) q LIMIT 201`，只返回前 200 行并标记截断。
4. **只读角色**：`t2s_reader` 只能 SELECT `t2s` 视图；对 `app` schema 无任何权限；默认只读事务、3 秒语句超时、5 秒空闲事务超时、2 秒锁超时。
5. **行级权限视图**：13 个 `security_barrier` 视图按事务内 `app.user_id` 过滤；未设置时返回 0 行。
6. **审计**：成功、被拒绝、执行失败都写 `text2sql_log`。

即使 Prompt 被注入、模型生成了恶意 SQL，数据库角色与视图仍然是最后一道边界。测试覆盖了 DROP/DELETE、多语句、系统表、跨用户视图、超时、行数上限与审计。

## 9. 权限与安全

### 9.1 三层防护

1. 服务层：`require_course_role` / `require_conversation_edit` 检查系统角色与课程角色。
2. SQL 作用域：所有列表/详情查询强制带“本人来源或共享课程成员”条件。
3. 数据库层：触发器阻止越权写；约束阻止非法数据；只读角色限制 Text2SQL。

### 9.2 其他措施

- 密码：PBKDF2-HMAC-SHA256，随机 salt，常量时间比较。
- CSRF：Session 中保存 token，所有写表单校验。
- 会话：`itsdangerous` 签名 Cookie，`SameSite=Lax`，生产环境强制 HTTPS。
- XSS：Jinja2 自动转义；JS 渲染表格使用 `textContent`，不拼 `innerHTML`。
- SQL 注入：全部参数化查询；`LIKE` 模式对通配符转义。
- 文件：限制扩展名、大小、消息条数；文件名只取 basename。

## 10. 测试与验收

### 10.1 自动化测试

```text
78 unit tests
68 integration tests
146 total, all passed
```

- 单元：密码哈希/校验、CSRF、JSON/Markdown/裸文本解析、字段校验、去重分类、Text2SQL 守卫、mock 规则、PWA 资产、引导/撤销/错误页/静态缓存与性能预算。
- 集成：schema 约束与删除行为、权限边界、导入预览/确认/幂等/损坏文件、收件箱批量归类、检索筛选与分页、批注复习间隔、统计、管理员、Text2SQL 只读/越权/审计。

集成测试只连接以 `_test` 结尾的数据库，每个用例前 `TRUNCATE app.users RESTART IDENTITY CASCADE`。测试脚手架使用管理员连接执行 TRUNCATE，因为应用角色 `ls_app` 被刻意设计为没有 TRUNCATE 权限。

### 10.2 端到端验收

`scripts/verify.py` 在演示库上执行 29 项检查，所有写操作放在外层事务中，最后主动回滚：

```text
结果：29/29 PASS，0 FAIL，耗时 767 ms
报告：data/verification/report.json
```

检查项包括：

- 12 张表、13 个视图、主外键/唯一/检查约束、索引与 5 个触发器
- 演示数据规模、密码哈希、角色边界
- 重复导入 0 新增 / 84 跳过、损坏文件拒绝
- 关键词检索、课程/标签筛选、上下文与批注内联
- 统计概览、CSV、Markdown
- 批注 → 复习项 → 间隔 2/4 天
- 粘贴收录、重复收录幂等、收件箱批量归类
- Text2SQL mock 查询、用户过滤、恶意 SQL 拒绝、审计、只读角色
- 事务回滚后计数不变、`ls_app` 无 DDL 权限

### 10.3 UI 验证

Playwright 脚本登录 alice / bob / admin，生成桌面 22 张与移动端 10 张截图，检查控制台错误与 390px 横向溢出；bob 的 403 页面是预期行为，脚本单独放行。另外用 Performance API 验证首屏 JS 8KB、请求数 2、移动端 LCP 268ms，并检查 Service Worker 缓存版本与静态资源版本参数。

截图与走查过程中发现并修复了三个问题：

1. 导入预览“可更新”计数显示为 Python 方法对象（Jinja2 属性解析把 `counts.update` 当成 `dict.update`）。
2. 错误页始终显示 400（`render` 的 `status_code` 是 HTTP 参数，模板却把它当上下文变量）。
3. E-R 图标题与顶部属性椭圆重叠。

修复后重新运行测试、验收和截图，全部通过。

## 11. AI 协作说明

本项目由作者与 AI 编程助手协作完成。作者负责选题、需求取舍、数据库方案确认、验收标准与最终检查；AI 助手参与方案整理、代码实现、测试编写、文档草稿与截图脚本。详细的分歧与决策记录见 [ai-collaboration.md](ai-collaboration.md)。

几个关键人工决策：

- 不直接复用参考项目的笔记/向量模型，坚持“来源—会话—消息—批注—复习”的关系模型。
- 不把 `owner_id` 冗余进 `conversations`，保持 3NF。
- 不用生成列存 `content_hash`，改用表达式索引。
- `review_items` 唯一键设为 `(user_id, annotation_id)`，支持共享课程多人各自复习。
- Text2SQL 不信任 Prompt，安全边界放在只读角色与权限视图。
- 测试库脚手架使用管理员连接，而不是给应用角色开放 TRUNCATE。

## 12. 运行与演示

```bash
docker compose up -d db
.venv/Scripts/python.exe scripts/init_db.py --reset
.venv/Scripts/python.exe scripts/seed_demo.py
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe scripts/verify.py --json data/verification/report.json
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

演示账号：`alice / Alice123!`、`bob / Bob123!`、`admin / Admin123!`。完整演示脚本见 [demo-script.md](demo-script.md)。

## 13. 限制与改进方向

1. **单机规模**：未做分布式与高并发压测；并发导入依靠唯一键 + advisory lock 保证正确性。
2. **中文检索**：`pg_trgm` 对 1–2 字短查询选择性差；数据量增大后可引入 `zhparser` 或外部搜索引擎。
3. **模拟数据**：会话模板较规整，重复追问句会命中查重；真实数据需要平台适配层与更复杂的清洗规则。
4. **Text2SQL**：mock 规则只覆盖常见问题；真实 LLM 的 SQL 质量依赖模型与 Prompt，仍需守卫与人工复核。
5. **管理员权限**：当前只做用户启停与聚合统计；若扩展审计功能，需要单独设计“可查看元数据但不可查看正文”的视图。
6. **实时性**：统计全部实时计算，数据量增大后可以增加物化视图，但需要明确刷新策略与冗余一致性。

## 14. 结论

本项目完成了一个从导入、组织、检索、批注、复习到统计导出的完整学习数据闭环，并用 Text2SQL 展示了自然语言查询关系数据的一种可控实现方式。数据库设计覆盖了 E-R 建模、范式分析、完整性约束、索引、触发器、事务与最小权限；应用层保持了 SQL 可见、权限可验证、流程可回滚。自动化测试与端到端验收为演示和答辩提供了可重复的证据。

---

## 附录 A：演示账号

| 账号 | 密码 | 说明 |
| --- | --- | --- |
| alice | Alice123! | 5 门课 owner，30 个可访问会话 |
| bob | Bob123! | OS editor、DB viewer，12 个可访问会话 |
| admin | Admin123! | 管理员，仅聚合数据 |

## 附录 B：主要文件

| 文件 | 说明 |
| --- | --- |
| `sql/schema.sql` | 12 张表、约束、索引、触发器 |
| `sql/views.sql` | 13 个 t2s 权限视图 |
| `sql/roles.sql` | `ls_app` / `t2s_reader` 最小权限 |
| `sql/showcase.sql` | 20 条关键 SQL |
| `app/services/import_service.py` | 导入解析、预览、确认事务 |
| `app/services/text2sql_service.py` | 生成、守卫、只读执行、审计 |
| `app/permissions.py` | 课程角色与会话可见性 |
| `scripts/verify.py` | 29 项端到端验收 |
| `docs/design.md` | 数据库设计详解 |
| `docs/requirements.md` | R01-F01 需求拆解 |
| `docs/sql-showcase.md` | 关键 SQL 与结果 |
| `docs/ai-collaboration.md` | AI 协作与方案分歧 |
| `docs/demo-script.md` | 演示视频脚本 |
