# 个人学习空间：把聊天记录变成可复习的课程资料

**课程**：数据库系统概论  
**选题**：选题 2 · 与大模型的聊天记录整理  
**技术栈**：Python 3.12 + FastAPI + Jinja2 + PostgreSQL 16 + psycopg 3  
**演示数据**：全部为模拟问答，不含真实个人信息  
**日期**：2026-10-27

---

## 摘要

本项目实现了一个以“聊天记录”为核心的关系数据库应用系统。系统把 Qoder、ChatGPT、DeepSeek 等平台的问答导出文件解析为统一格式，按课程、来源、标签组织；支持中文检索、会话上下文、批注、间隔复习、统计导出，并额外实现了受数据库只读权限视图保护的 Text2SQL 页面。

数据库包含 16 张表（15 张业务表 + 1 张审计表）、15 个权限视图、9 个触发器、多组唯一/检查/外键约束；应用层使用参数化 SQL 与显式事务，不依赖 ORM。导入流水线采用“解析 → 校验 → 持久化预览 → 单事务确认 → 查重”的结构，并在文件导入之外提供平台原生导出适配、采集令牌 + Bearer 接口与 MV3 浏览器扩展。标签之间还维护一张有向无环的先修关系图，用递归 CTE 做环检测、闭包与最短路径查询；另在隔离事务中用 RLS 与应用层 WHERE、`security_barrier` 视图做同规则对比实验。审计触发器只记录对象、主键与变更列名，不保存字段值，消息与批注表不开启审计；管理员观测页只展示 PostgreSQL 统计视图与白名单执行计划，不提供任意 SQL 输入。另提供 Windows / macOS 桌面个人版安装包：内置裁剪后的 PostgreSQL 16 与 PyInstaller sidecar，首次启动自动建库并创建本地管理员，数据只保存在本机；同一 Wi-Fi 下手机可扫码访问同一实例，数据库端口永不对外。系统通过 334 个自动化测试、12 项浏览器 E2E 与 42 项端到端验收检查，覆盖 schema、权限、导入幂等、中文检索、全局搜索、统计、批注复习与 FSRS 调度、整理建议、知识先修图、离线同步、导出、Text2SQL 安全、命令行与 MCP 接口、令牌作用域、RLS 隔离一致性、审计元数据边界、数据库观测与事务回滚。

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
| R03 | 检索与阅读 | 课程/标签/日期/关键词筛选、游标分页、上下文、关键词高亮 |
| R04 | 批注与复习 | 四类批注、解决状态、复习队列、FSRS 四档评分调度、逾期提示 |
| R05 | 统计与导出 | 概览、课程排名、月度补零、标签 TopN、CSV、Markdown |
| R06 | 用户与权限 | 登录/登出、PBKDF2、CSRF、系统角色 + 课程角色 |
| R07 | Text2SQL | 自然语言→SQL、守卫、只读执行、审计历史、mock/LLM |
| R08 | 管理 | 用户启停、系统聚合统计、管理边界 |
| R09 | 快速采集与离线 | PWA 分享、裸文本、平台原生导出适配、采集令牌 + Bearer API、MV3 扩展、离线阅读/批注/复习 |
| R12 | 知识先修图 | 标签间 DAG、触发器防环、递归闭包与最短路径、按课程分组的 SVG 图、Text2SQL 可查 |
| R13 | 命令行客户端 | `ls search` / `ls import`，采集令牌认证、课程/平台覆盖、人类可读与 JSON 输出 |
| R14 | MCP 只读工具 | stdio JSON-RPC、search / get_conversation / list_due_reviews / add_annotation、令牌作用域 |
| R15 | RLS 隔离对比 | 临时 schema/角色内对比 `security_barrier` 视图、应用层 WHERE 与 RLS，验证行集合一致并全部回滚 |

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
| 复习事件 | 76（热力图与离线 outbox 幂等使用） |
| 标签先修边 | 32（覆盖 DFS/BFS、事务、TCP 等知识点） |
| 导入批次 | 5（全部 imported） |
| 采集令牌 | 种子数据 0；演示时可在 `/tokens` 创建 |
| Text2SQL 日志 | 种子数据 0；运行时追加 |

> 复习项按 `alice` 统计为 35 条（pending 19 + reviewing 9 + mastered 7）；全库 39 条另含 bob 的 4 条共享课程复习项。统计页面按当前用户过滤，因此 alice 看到 35 条。

### 3.3 导入样例

`data/samples/` 下有 10 个文件：

| 文件 | 用途 |
| --- | --- |
| `qoder_db.json` | 数据库课程，Qoder JSON |
| `chatgpt_os.json` | 操作系统课程，ChatGPT JSON |
| `deepseek_net.md` | 计算机网络课程，DeepSeek Markdown |
| `qoder_ml.json` | 机器学习课程，Qoder JSON |
| `chatgpt_ds.json` | 数据结构课程，ChatGPT JSON |
| `chatgpt_export_conversations.json` | ChatGPT 原生 `conversations.json`（mapping / current_node） |
| `qoder_sessions_export.json` | Qoder 原生 `sessions` 导出（字段别名） |
| `deepseek_export.md` | DeepSeek 原生 Markdown（`### User/Assistant` + 英文元信息） |
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
| 标签 — 标签（先修） | M:N 有向，同属主，禁止自环与环路 | 任一端标签删除即 CASCADE |

用户不做硬删除，使用 `is_active=false` 停用，避免破坏历史批注、复习与审计记录。

## 5. 逻辑设计与范式

### 5.1 关系模式

16 张表位于 `app` schema（12 张基线表 + 4 张迁移新增表）：

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
11. `review_items`：`(user_id, annotation_id)` 唯一，间隔、到期日与 FSRS 稳定性/难度/遗忘次数。
12. `text2sql_log`：问题、生成 SQL、状态、耗时、行数、错误。
13. `review_logs`：复习事件流水，`(user_id, event_id)` 唯一用于离线重放幂等；`review_item_id` 删除时 SET NULL。
14. `api_tokens`：采集令牌，只存 SHA-256 与可读前缀，含 `scopes` 作用域、固定窗口限流计数、最近使用与撤销时间。
15. `tag_edges`：标签先修关系 DAG，`(from_tag_id, to_tag_id)` 唯一，触发器校验同属主与无环。
16. `audit_log`：审计历史，记录操作者、对象、动作、主键与变更列名；不保存字段值，`changed_columns` 排除 `password_hash`，并按 90 天保留策略清理。

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
- 唯一：用户名、课程 `(created_by, code, semester)`、来源 `(owner_id, platform, label)`、会话 `(source_id, external_id)`、消息 `(conversation_id, seq_no)`、标签 `(owner_id, name)`、复习项 `(user_id, annotation_id)`、令牌哈希、复习事件 `(user_id, event_id)`、先修边 `(from_tag_id, to_tag_id)`。
- CHECK：角色枚举、发送者枚举、批注类型/状态、复习状态、令牌名称长度与非负窗口计数、复习事件动作/间隔、时间顺序、文件大小、SHA-256 格式、颜色格式、先修边两端不同、审计对象/动作枚举与非空主键等。
- 部分唯一索引：`course_members(course_id) WHERE role='owner'`。
- 延迟约束触发器：事务结束时每门课恰好一个 owner。
- 访问触发器：打标签、批注、复习时必须具备会话或课程编辑权限；写先修边时用递归 CTE 检查两端同属主且不形成环。
- 审计触发器：`courses`、`course_members`、`users` 的增删改写入 `audit_log`；应用角色只能读，写入由 `SECURITY DEFINER` 触发器完成。

### 5.4 索引

重点索引：

- `messages`：`(conversation_id, seq_no)` 唯一 B-tree、`content` GIN trgm、`md5(content)` 表达式索引；可选 `embedding vector(1536)` 上的 HNSW cosine 索引（`m=16`、`ef_construction=64`）。
- `conversations`：`(course_id, started_at DESC)`、`source_id`、标题 GIN trgm。
- `review_items`：`(user_id, status, due_date) WHERE status <> 'mastered'` 部分索引。
- `annotations`：`(message_id)`、`(user_id, status)`。
- `import_batches`：`(user_id, created_at DESC)`、`(source_id, file_sha256)`。
- `text2sql_log`：`(user_id, created_at DESC)`。
- `review_logs`：`(user_id, created_at DESC)`、`(review_item_id, created_at DESC)`。
- `api_tokens`：`(user_id, created_at DESC)`；令牌哈希与 `(user_id, event_id)` 由唯一约束提供索引。
- `tag_edges`：`(to_tag_id)` 支持反向“解锁了什么”查询，`(from_tag_id, to_tag_id)` 唯一约束提供正向索引。
- `audit_log`：`(occurred_at DESC, id DESC)` 支撑时间线，`(table_name, action, occurred_at DESC)` 支撑筛选，`(actor_user_id, occurred_at DESC)` 支撑按操作者查询。

中文检索没有使用 PostgreSQL 原生全文检索，因为其默认分词对中文不友好；改用 `pg_trgm` + `ILIKE`，并对用户输入的 `%`、`_`、`\` 做 `ESCAPE` 处理。

向量检索是可选增强：迁移 `004_add_message_embeddings.sql` 增加 `embedding`、`embedding_model`、`embedded_at` 三列与 HNSW 索引，`EMBEDDING_API_KEY` 未配置时不会写入任何向量，检索保持纯词法。启用后，词法候选与向量候选各取 100 条，按 `1/(60+rank)` 做 Reciprocal Rank Fusion；两路候选都复用同一段“本人来源或共享课程”的作用域 SQL，管理员没有任何向量入口。搜索页展示当前模式（混合 / 词法）与向量覆盖率，并允许每次补齐 64 条可见消息。部署方需注意：启用后消息正文会发送到所配置的嵌入服务。

> 运行层面还有一个与镜像有关的完整性细节：从 `postgres:16-alpine` 切换到 `pgvector/pgvector:pg16` 时，基础镜像的 libc 排序规则不同，旧数据目录中的文本唯一索引可能查不到部分中英文混合值。系统通过 `verify.schema.index_health` 对每个标签执行索引查找来发现该问题，修复方式是对演示库与测试库执行 `REINDEX DATABASE`。

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

会话列表支持关键词、课程、标签、日期范围筛选与游标分页；关键词命中标题或消息正文，列表展示命中片段、消息数、平台与标签。会话详情按 `seq_no` 展示消息，支持按发送者过滤、关键词高亮、内联批注、所属课程与标签编辑。

列表默认按“最近一条消息时间、会话 id”倒序排列，与索引顺序一致。首页用窗口函数一并取出总数，并把 `(时间, id)` 与总数编码成不透明游标；后续翻页使用行值比较 `(sort_at, id) < (…)` 取下一页，向前翻页则反转比较方向与排序后再倒回，避免深分页的 OFFSET 扫描。混合检索的排名来自查询期 RRF 分数，无法写成稳定的数据库排序键，因此按融合结果位置生成位置游标；非法游标一律回退首页，收藏/筛选参数在翻页链接中原样保留。

配置向量模型后，关键词检索自动升级为混合检索：词法候选与向量候选分别排序，再用 RRF 融合成统一的会话排名；纯语义命中的会话会显示“语义相近”标记，搜索摘要处标注“混合检索”。未配置模型或嵌入调用失败时，页面标注“词法检索”，结果不受影响。验收时用本地嵌入桩验证了完整 UI 路径：补齐 64/420 条向量、混合检索徽标、5 条纯语义命中，以及默认无 Key 时的词法退化。

![会话列表](screenshots/04-conversations.png)

![会话详情](screenshots/05-conversation-detail.png)

### 6.4 导入流水线

导入页支持 JSON 与 Markdown，单文件 ≤ 5 MB、≤ 2000 条消息。上传后先解析并写入 `import_batches`，生成 30 分钟有效的预览；预览页展示会话数、消息数、新增/可更新/完全重复分类，并允许逐会话选择与课程映射。

![导入页](screenshots/07-imports.png)

![导入预览](screenshots/08-import-preview.png)

确认入库在一个事务内完成：锁批次、按来源加 advisory lock、upsert 来源、按 `(source_id, external_id)` 合并会话、按 `(conversation_id, seq_no)` 跳过重复消息、get-or-create 标签、写关联；成功后清空 payload 并标记 imported。任一步失败整体回滚，批次标记 failed。

重复导入 `qoder_ml.json` 的验收结果：新增 0 会话、0 消息，跳过 84 条消息；`broken.json` 被拒绝并返回 8 条校验错误。

### 6.5 批注与复习

批注支持笔记、疑问、纠错、总结四类；可以标记解决、编辑、删除，或加入复习队列。复习队列按到期日排序，展示批注内容、原消息片段、课程和状态。打卡时按「重来 / 困难 / 良好 / 容易」四档评分，由 FSRS 风格调度器根据稳定性、难度与距上次复习的天数计算下次间隔：新卡片四档初始间隔约为 1 / 1 / 2 / 8 天，回忆成功会增长稳定性，重来则缩短间隔并累加遗忘次数；间隔上限 365 天，也可以标记掌握或重置。`REVIEW_ALGORITHM=simple` 可回退到旧的间隔翻倍，便于对照；评分、算法、间隔与到期日都写入 `review_logs`，离线打卡统一按「良好」补交。

![批注](screenshots/09-annotations.png)

![复习队列](screenshots/10-reviews.png)

### 6.6 统计与导出

统计页实时聚合：概览卡片、课程排名（消息数）、8 个月活跃度（补零）、标签 Top12、批注类型与解决率、复习状态与到期、重复内容检测。所有计数实时 `COUNT`，不保存冗余计数列。

![统计](screenshots/11-stats.png)

导出包括：

- 统计 CSV：UTF-8 BOM，列 `section,item,metric,value`，Excel 直接打开不乱码。
- 单会话 Markdown：标题、课程、平台、账号、标签、消息、内联批注，含模拟数据声明。
- 个人全量 zip：只打包本人来源的会话 Markdown、批注 CSV、复习 CSV 与 manifest 计数，文件名带用户名与日期；共享课程中他人的消息与批注不会进入压缩包，管理员账户不提供该入口。

数据库侧另提供 `scripts/backup.py`：调用 `pg_dump --format=custom` 生成可恢复的 `.dump`，默认保留最近 10 份；恢复使用 `pg_restore --clean --if-exists`，且只允许写入 `*_test` 库，需要覆盖其他库时必须显式 `--force`，避免误伤演示库。

### 6.7 Text2SQL

Text2SQL 页面提供示例问题、结果表格、生成的 SQL、解释和最近查询历史。默认无 API Key 时使用本地规则 mock，界面明确标注“本地规则模式（未配置大模型 Key）”；配置 `LLM_API_KEY` 后走 OpenAI 兼容接口。

![Text2SQL 结果](screenshots/13-text2sql-result.png)

![Text2SQL 历史](screenshots/13b-text2sql-history.png)

### 6.8 管理与越权

管理员只看到用户管理与系统聚合统计，不能进入课程/会话页面。管理页新增只读的审计时间线：可按对象与动作筛选，展示操作者、主键与变更列名；审计表对应用角色只读，消息与批注表未开启审计，因此管理员无法借此读取学习内容。bob 作为数据库课程 viewer，访问课程编辑页返回 403。错误页会说明可能原因，并提供“看看我参与的课程”“回到概览”“返回上一页”等恢复入口。

![管理员](screenshots/14-admin.png)

![管理员审计](screenshots/14b-admin-audit.png)

![越权 403](screenshots/15-permission-denied.png)

### 6.9 采集闭环与体验

除了统一 JSON / Markdown 文件导入，系统提供五条更低摩擦的采集路径：

1. **平台原生导出适配**：ChatGPT `conversations.json` 沿 `current_node` 还原主线并转换 UTC 时间；Qoder `sessions` 导出做字段别名映射；DeepSeek Markdown 识别 `### User/Assistant` 与英文元信息。适配层只做归一，之后复用同一套解析、校验与入库逻辑。
2. **采集令牌 + Bearer API**：`/tokens` 创建的令牌明文只显示一次，服务端仅存 SHA-256；令牌默认 `{read,ingest}`，MCP 写批注需显式勾选 `annotate`；`POST /api/ingest` 按 `course_code` 自动归课，每令牌每分钟 60 次固定窗口限流，可随时撤销。
3. **MV3 浏览器扩展**：`extension/` 固定权限只有 `activeTab` / `storage`，仅当用户点击「发送到学习空间」时读取当前会话并调用 `/api/ingest`；令牌保存在本机浏览器，不写入网页。
4. **PWA Share Target**：手机从任意 App 分享文本/链接，解析结果进入 `import_batches` 预览。
5. **裸文本粘贴**：直接粘贴 `我：… / 助手：…` 对话，自动识别角色、时间与课程/标签。

所有路径最终都走同一套预览与单事务入库流程；未指定课程的会话进入收件箱，可批量归入课程。

- 导入向导固定为“选择 → 预览 → 结果”三步，支持拖放上传与粘贴双入口。
- 收件箱只允许移动本人且当前未归类的会话，避免编辑者误动共享课程数据。
- 删除批注、复习项或标签后提供 5 秒撤销；离开页面时兜底提交，避免“看起来删了其实没删”。
- 登录页演示账号可一键进入；新账号概览给出建课、导入、收件箱三步引导。
- 静态资源使用 GZip 与版本化长缓存，首屏 JS 约 8KB。

![采集令牌](screenshots/22-tokens.png)

![平台原生导出预览](screenshots/26-platform-import.png)

![浏览器扩展](screenshots/21-extension-popup.png)

![收件箱](screenshots/17-inbox.png)

![分享预览](screenshots/18-share.png)

![导入结果](screenshots/19-import-result.png)

![粘贴导入](screenshots/20-import-paste.png)

### 6.10 全局搜索与离线学习

全局搜索同时检索会话标题、消息正文、本人批注、课程与标签，支持 `/` 与 `Ctrl/Cmd+K` 唤起、最近搜索与最近访问；结果按类型分组并高亮关键词。会话快照可保存到浏览器 IndexedDB，离线页独立于服务端渲染，支持阅读、搜索已保存会话、缓存复习队列、暂存批注草稿与复习打卡；恢复联网后按客户端事件 ID 幂等提交。

![全局搜索](screenshots/23-search.png)

![搜索快捷弹窗](screenshots/23b-search-dialog.png)

![离线已保存会话](screenshots/24-offline-saved.png)

![离线阅读](screenshots/24b-offline-reader.png)

![离线复习](screenshots/25-offline-review.png)

### 6.11 整理建议（AI 标签与摘要）

配置 `LLM_API_KEY` 后，会话详情页的“归类与标签”面板会出现「生成 AI 建议」按钮。点击后服务端读取该会话前 40 条消息（每条最多 800 字、总计 6000 字），调用 OpenAI 兼容接口，要求返回 `{summary, tags}`。服务端复用用户已有同名标签、去重并限制为 3 个标签，摘要清洗后限制 500 字；结果只回填页面表单，用户检查并点击「保存归类与摘要」后才会落库。未配置 Key 或 `SUGGEST_MODE=off` 时按钮隐藏，课程、标签与摘要仍可手动填写。

保存沿用原归类事务：新标签通过 `ON CONFLICT (owner_id, name)` 去重；摘要保存为该用户在此会话的最新“总结”批注（`kind='summary'`），重复保存执行 UPDATE，留空则 DELETE。这样不需要新增表，也不会产生多条重复摘要。viewer 调用接口会得到 403，管理员没有入口。

验收使用本地 LLM 桩验证了完整链路：按钮出现 → 建议填入摘要与标签 → 保存 → 页面显示“总结”批注与新标签；另在无 Key 实例上确认按钮不渲染、表单可用。`verify.ai.organize` 在演示库事务中覆盖标签幂等写入、摘要唯一更新与 viewer 越权拦截。

### 6.12 知识先修图

标签不仅用于筛选，还可以表达“先学什么、后学什么”。`/tags/graph` 把当前用户的标签组织成有向无环图：一条 `from_tag_id → to_tag_id` 的边表示前者是后者的先修知识。演示数据里有 32 条边，覆盖 SQL→索引→B+树、事务→隔离级别、进程→线程、TCP→握手等链路。

3 个设计要点：

1. **环由数据库拦截**。`tag_edges` 上的 BEFORE 触发器先检查两端标签属于同一用户，再用从 `to_tag_id` 出发的递归 CTE 判断 `from_tag_id` 是否已可达；若可达则抛 `check_violation`，服务层再翻译成可读提示。这样即使绕过页面直接写库，也得到同一约束。
2. **递归闭包放在视图里**。`t2s.v_my_tag_prerequisites` 用 `WITH RECURSIVE` 先锁定当前用户的标签集合，再逐层展开先修链，并用 `DISTINCT ON` 只保留每个标签的最短深度。页面上的“完整先修链（含间接）”和 Text2SQL 的“哪些标签有三层以上先修”都读这个视图。
3. **布局在应用层做**。服务层按最长路径给节点分层，同层纵向排列，用三次贝塞尔曲线画边；随后把连通分量按“出现次数最多的课程”合并成课程卡片，孤立标签单独列出。这样页面不会变成一张巨大的全屏图，而是六张可读的课程子图。

| 验证项 | 结果 |
| --- | --- |
| 先修边 / 连通分量 | 32 条 / 6 组 |
| “最短路径”的完整先修链 | 图（深度 1）→ 最短路径（深度 2） |
| 试图添加 最短路径→图 | 被触发器拒绝（成环） |
| 添加 BFS→Dijkstra 后闭包 | Dijkstra 新增一层，删除后恢复 |

![知识先修图](screenshots/27-tag-graph.png)

选中标签后，右侧（窄屏为下方）会显示直接先修、递归先修链、解锁的后续标签和关联会话，见 [detail 截图](screenshots/27b-tag-graph-detail.png)。`verify.graph.prerequisites` 在演示库事务中覆盖闭包、防环与增删。管理员没有该页面入口；所有查询天然按属主过滤。

### 6.13 命令行客户端

`app/cli.py` 提供两个子命令，复用现有 JSON API，不复制业务逻辑：

```bash
export LS_BASE_URL=http://127.0.0.1:8000
export LS_TOKEN=ls_xxxxxxxx

ls search "隔离级别" --limit 5
ls import data/samples/qoder_db.json --course-code DB
```

- `ls search` 调用 `GET /api/search?q=...`。该接口与浏览器会话分离，使用 `Authorization: Bearer` 采集令牌，因此脚本和外部只读客户端不需要 Cookie/CSRF；管理员会被拒绝，关键词为空返回 400，结果仍按“本人来源或共享课程成员”过滤。
- `ls import` 读取统一 JSON，先做本地形状检查，再调用 `POST /api/ingest`。`--course-code` 会覆盖每个会话的课程编号，`--platform` 覆盖来源平台；服务端继续复用预览、查重与单事务确认，重复导入返回新增 0 而不是重复写入。
- 两个命令都支持 `--json`，便于管道处理；默认输出是简洁的中文摘要。令牌可放在 `LS_TOKEN`，避免出现在命令历史里；CLI 不会打印令牌。

Markdown 导入仍需走网站上传页，因为 CLI 只发送统一 JSON，避免在客户端再维护一套解析规则。`tests/unit/test_cli.py` 覆盖文档改写与输出格式，`tests/integration/test_api_search.py` 覆盖令牌搜索的可见范围、管理员拒绝与空查询。

### 6.14 MCP 工具接口

`app/mcp_server.py` 通过 stdio 暴露 JSON-RPC 2.0（MCP `2024-11-05`），让 Claude Desktop、Cursor 等客户端把学习空间当作工具使用。服务不直接连接数据库，而是以 `LS_TOKEN` 调用本机 Bearer API，因此沿用同一套权限过滤、限流与审计边界。

- 工具：`search`、`get_conversation`、`list_due_reviews` 为只读；`add_annotation` 为唯一写工具，只能新增批注，不能修改或删除已有批注。
- 作用域：迁移 006 给 `app.api_tokens` 增加 `scopes text[]`，默认 `{read,ingest}`，允许值为 `read` / `ingest` / `annotate`；`require_scope` 在服务端逐端点校验，页面创建令牌时需显式勾选才会写入 `annotate`。
- 端点：`/api/mcp/me` 返回作用域，客户端据此隐藏无权工具；`/api/mcp/search`、`/api/mcp/conversations/{id}`、`/api/mcp/reviews/due`、`/api/mcp/annotations` 分别对应四个工具。管理员在令牌层即被拒绝，MCP 端点也会再次检查。
- 协议：支持 `initialize`、`notifications/initialized`、`ping`、`tools/list`、`tools/call` 与批量请求；JSON 解析失败、未知方法与参数错误分别返回标准 JSON-RPC 错误码，工具执行失败以 `isError` 结果返回，便于模型自行修正。

```json
{
  "mcpServers": {
    "learning-space": {
      "command": "C:/path/to/learning-space/.venv/Scripts/python.exe",
      "args": ["-m", "app.mcp_server"],
      "env": {
        "LS_BASE_URL": "http://127.0.0.1:8000",
        "LS_TOKEN": "ls_你的令牌"
      }
    }
  }
}
```

`tests/unit/test_mcp_server.py` 覆盖工具过滤、输入校验、结果格式、JSON-RPC 错误与批量请求；`tests/integration/test_mcp_api.py` 覆盖作用域拒绝、会话/批注可见范围、到期复习与幂等写入。

### 6.15 RLS 与 security_barrier 视图对比实验

`app/services/rls_experiment.py` 在 `force_rollback` 事务内创建随机 schema 与 NOLOGIN 探针角色，克隆 `sources`、`course_members` 和两张 `conversations`（一张普通表、一张启用 `FORCE ROW LEVEL SECURITY` 的策略表），用三种方式读取“当前用户可见的会话”：现有 `t2s.v_my_conversations`、应用层显式 WHERE、基表 RLS；三者都从 `app.user_id` GUC 取当前用户。事务结束时 schema、角色与克隆表全部回滚。

在演示库上（alice 30 个可见会话、bob 12 个）结果为：

| 方式 | alice | bob | 计划形状 | 未设置 GUC |
| --- | --- | --- | --- | --- |
| `t2s.v_my_conversations` | 30 | 12 | 每次出现 `Subquery Scan`，过滤器被 barrier 包裹 | 0 行 |
| 应用层 WHERE | 30 | 12 | 直接出现 GUC 比较与 hashed SubPlan | — |
| RLS 策略 | 30 | 12 | 与 WHERE 计划相同，策略谓词被内联到基表扫描 | 0 行 |

三种方式行集合完全一致。视图方案的 `security_barrier` 阻止谓词下推到基表，便于把权限规则封装在一个对象里；RLS 方案的计划与应用层 WHERE 基本相同，说明它不会额外引入一层执行节点，但需要为每张表维护策略，且表属主与超级用户默认绕过，必须配合 `FORCE ROW LEVEL SECURITY` 与独立运行角色。演示数据量小，执行时间均在 1 ms 以下、噪声较大，因此报告以计划形状而非耗时作为主要证据。现有个人场景继续使用“只读角色 + `security_barrier` 视图”；RLS 实验用于答辩时展示另一种数据库层隔离方案的代价与收益。

运行 `scripts/rls_experiment.py` 会输出摘要并写入 `data/verification/rls-experiment.json`；`verify.py` 的 `db.rls` 检查确认两种用户的行集合一致、GUC 未设置时返回 0 行，且实验对象全部回滚。

### 6.16 审计历史与通用触发器

`sql/migrations/007_add_audit_log.sql` 新增 `app.audit_log` 与通用触发器函数 `app.audit_row_change()`。只有 `courses`、`course_members`、`users` 三张管理型表开启审计；`messages`、`annotations`、`review_items` 等学习内容表刻意不开启，避免管理员页面成为读取正文或批注的旁路。

每次增删改写入一行：`actor_user_id`（来自事务内 GUC `app.actor_user_id`，未设置记为系统）、`table_name`、`action`、`row_pk`（JSONB，课程/用户为 `{id}`，成员为 `{course_id, user_id}`）与 `changed_columns`（列名数组）。触发器用 `to_jsonb(OLD/NEW)` 做键级比较得到变更列，不保存任何字段值；`password_hash` 即使变更也不会出现在列名中。课程服务与管理员启停操作在各自事务内调用 `set_actor()`，因此审计与业务写入同成同败。

权限边界：`ls_app` 对 `audit_log` 只有 `SELECT`，没有 `INSERT/UPDATE/DELETE/TRUNCATE`；写入由 `SECURITY DEFINER` 触发器完成。清理函数 `app.prune_audit_log(retain_days)` 同样以属主身份执行，只允许清理至少 90 天前的记录，并对应用角色单独授权，`PUBLIC` 无执行权限。管理页 `/admin` 的时间线支持按对象/动作筛选，底部按钮手动清理 90 天前记录；页面只展示元数据，不渲染正文或批注内容。

演示库种子数据留下 15 条系统/课程创建事件（3 个用户、5 门课程、7 条成员关系），用于说明审计链路。`verify.py` 的 `db.audit` 检查在一次课程更新中确认操作者、主键与 `changed_columns = ['description']` 被记录，同时断言应用角色对审计表没有写权限。

### 6.17 数据库观测页

`/admin/observability` 只对系统管理员开放，数据全部来自 PostgreSQL 统计视图，不读取 `app.messages`、`annotations` 或 `review_items` 的正文：

- 概览：`pg_database_size`、`pg_stat_database` 的缓存命中率、连接数、事务提交/回滚、临时文件与死锁。
- I/O：`pg_stat_io` 按 `backend_type` 聚合读写、扩展、命中、驱逐与 fsync。
- 表访问与膨胀：`pg_stat_user_tables` 的顺扫/索引扫描、活元组/死元组、变更计数与总大小。
- 索引使用：`pg_stat_user_indexes` 的扫描次数、读取元组与索引大小，按“最不受欢迎”排序，辅助发现冗余索引。
- 慢查询：可选 `pg_stat_statements`，按平均执行时间排序，只显示归一化语句（不含参数值），并过滤掉 `pg_stat_statements`、`EXPLAIN` 自身与工具类语句。
- 执行计划：只接受 4 个白名单键（消息关键词检索、会话列表、到期复习、课程聚合），用 `EXPLAIN (FORMAT JSON)` 估算，不实际执行；页面把计划树拍平成“节点/关系/代价/估算行/条件”表格。

安全边界是“可观测但不可自由查询”：页面没有 SQL 输入框，管理员不能借它读取他人消息；慢查询即使包含字面量也已在数据库侧归一化。迁移 008 在扩展可用时条件创建 `pg_stat_statements` 并给 `ls_app` 授予 `pg_read_all_stats`；若容器未预加载该库，页面显示降级说明，I/O、表访问、索引与执行计划仍可用。`verify.py` 的 `db.observability` 检查覆盖库大小、缓存率、I/O 行、消息表统计、索引统计、计划根节点、扩展安装状态与慢查询可用性。

![数据库观测](screenshots/28-observability.png)

### 6.18 桌面个人模式（Tauri v2，可选）

同一套站点可以打成“装好即用”的桌面应用：每个用户的数据只保存在本机，不需要服务器、Docker 或本机 Python。安装包内置裁剪后的 PostgreSQL 16（含 `pg_trgm`、`pgvector`）与 PyInstaller onedir sidecar，Rust 壳负责进程编排：

- 首次启动：读取/生成 `config.json` 与 `.env`（随机 `SESSION_SECRET` 及 `postgres`/`ls_app`/`t2s_reader` 密码），执行 `initdb --locale=C --encoding=UTF8`，写入受管 `postgresql.conf` 块（`listen_addresses='127.0.0.1'`、`max_connections=20`、`shared_buffers=128MB`）后 `pg_ctl start`。
- `ls-server bootstrap` 建主库、schema、迁移、视图与角色，迁移前自动 `pg_dump` 备份；随后 `ls-server serve`，轮询 `/healthz` 返回 200 后打开页面。首次进入 `/setup` 创建本地管理员账号（直接设为 admin）。
- 数据目录：Windows `%LOCALAPPDATA%\com.learningspace.desktop`，macOS `~/Library/Application Support/com.learningspace.desktop`；备份保留 7 份，卸载默认保留数据。
- 托盘“手机访问…”把 sidecar 以 `--host 0.0.0.0` 重启并显示 LAN URL 与二维码；关闭开关立即恢复 `127.0.0.1`。PostgreSQL 始终只监听回环地址，局域网是明文 HTTP，仅限可信 Wi-Fi。
- 安装目录或用户名含中文时，不会把非 ASCII 路径直接交给 PostgreSQL：数据目录与安装目录各经一个 `subst` 盘符转发（Windows 版 PG 用 ANSI 代码页解析 junction 目标，junction 在英文系统上会失效）。
- 桌面模式是唯一的明文 HTTP 例外（`LS_DESKTOP_MODE=1` 且 `LS_ALLOW_INSECURE_HTTP=1` 才关闭 Secure Cookie 与 HSTS）；普通生产部署仍强制 Secure/HSTS。

本机实测（Windows 11 x64，0.2.0）：安装后资源约 101 MiB，NSIS 安装包 35.9 MiB；首次启动 `--smoke`（initdb → 迁移 → 启动服务 → `/healthz` → 备份 → 停止）12–17 s，热启动 3.5 s；开启局域网后 `http://10.47.125.30:8765/` 可登录，`55432` 从局域网不可达，关闭开关后立即失效；安装到含中文的目录、`LS_HOME` 也为中文时 smoke 通过。macOS（Apple Silicon）由 CI 构建 DMG 并执行 `.app` 内 smoke，尚未人工验收。完整构建、目录结构、体积预算与安全边界见 [desktop-packaging.md](desktop-packaging.md)。

## 7. 关键 SQL

完整 23 条关键 SQL 及真实结果见 [sql-showcase.md](sql-showcase.md)，可执行脚本见 [../sql/showcase.sql](../sql/showcase.sql)。覆盖：

- 多表 `JOIN`、`LEFT JOIN`、`LATERAL`、`DISTINCT ON`
- `GROUP BY` / `HAVING`、`COUNT(DISTINCT)`、`FILTER`、`CASE`
- `generate_series` + `date_trunc` 补齐月份
- 窗口函数 `RANK()`、`count(*) OVER()`
- CTE、`WITH RECURSIVE`、JSONB 取值、`ON CONFLICT`
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
2. **SQL 守卫（AST 优先）**：`sqlglot` 以 PostgreSQL 方言解析，要求恰好一条 SELECT/WITH/UNION；遍历语法树拒绝 DDL/DML、`SELECT INTO`、`FOR UPDATE` 锁、表函数与 `pg_*`/`lo_*`/`dblink` 等危险函数，表名必须在 15 个视图或 CTE 白名单中。随后保留 `sqlparse` 单语句拆分、注释禁用和关键字黑名单作为第二层，并在检查前屏蔽字符串字面量与引用标识符，避免把正文里的 `delete` 误判为语句。
3. **EXPLAIN 预检**：在只读事务内用 `t2s_reader` 执行 `EXPLAIN (FORMAT JSON)`，不实际取数；总代价超过 500000 或根节点估算行数超过 200000 时直接拒绝，阈值可用 `TEXT2SQL_MAX_COST` / `TEXT2SQL_MAX_EST_ROWS` 调整，预检本身有 1 秒超时。
4. **行数包装**：`SELECT * FROM (<sql>) q LIMIT 201`，只返回前 200 行并标记截断。
5. **只读角色**：`t2s_reader` 只能 SELECT `t2s` 视图；对 `app` schema 无任何权限；默认只读事务、3 秒语句超时、5 秒空闲事务超时、2 秒锁超时。
6. **行级权限视图**：15 个 `security_barrier` 视图按事务内 `app.user_id` 过滤；未设置时返回 0 行。
7. **审计**：成功、被拒绝、执行失败都写 `text2sql_log`；预检拒绝记为 `rejected`。

即使 Prompt 被注入、模型生成了恶意 SQL，数据库角色与视图仍然是最后一道边界。测试覆盖了 DROP/DELETE、嵌套 DML、表函数、`FOR UPDATE`、系统表、跨用户视图、高成本预检、超时、行数上限与审计。

### 8.1 金标准评测

`data/text2sql_eval.json` 固定 30 个自然语言问题、10 条恶意 SQL 与 2 条跨用户用例。`scripts/eval_text2sql.py` 先执行金标准 SQL，再执行模型生成的 SQL，按“列集合 + 行多重集”比较结果，同时统计合法率与越权拦截率。当前离线 mock 基线：

| 指标 | 结果 |
| --- | --- |
| 执行准确率 | 17/30（56.7%） |
| 金标准 SQL 合法率 | 30/30（100%） |
| 生成 SQL 合法率 | 30/30（100%） |
| 越权拦截 | 12/12（100%） |
| 平均生成 + 执行耗时 | 6.6 ms |

mock 只覆盖固定问法，Q18–Q30 的换问法、聚合与排名问题会落入课程总览兜底，因此 56.7% 是诚实的离线下限，而不是模型能力上限。配置 `LLM_API_KEY` 后运行 `--provider llm` 可对真实模型复测。结果写入 `data/verification/text2sql_eval.json`，`verify.py` 的 `t2s.eval` 检查确保金标准合法率、越权拦截率均为 100%，且 mock 准确率不低于 50%。

## 9. 权限与安全

### 9.1 三层防护

1. 服务层：`require_course_role` / `require_conversation_edit` 检查系统角色与课程角色。
2. SQL 作用域：所有列表/详情查询强制带“本人来源或共享课程成员”条件。
3. 数据库层：触发器阻止越权写；约束阻止非法数据；只读角色限制 Text2SQL。

### 9.2 其他措施

- 密码：PBKDF2-HMAC-SHA256，随机 salt，常量时间比较。
- CSRF：Session 中保存 token，所有写表单校验。
- 会话：`itsdangerous` 签名 Cookie，`SameSite=Lax`；登录时清空旧会话并写入签发时间与随机 nonce，超过 `SESSION_MAX_AGE`（默认 7 天）自动失效；生产环境强制 `Secure` 与 HTTPS，并发送 HSTS。
- 登录限流：滑动窗口同时限制“IP + 账号”（默认 5 次/15 分钟）与单 IP（20 次/15 分钟），超限返回 429 与 `Retry-After`；成功登录只重置该账号计数。计数保存在进程内，单实例演示足够；多副本部署应改用 Redis 等共享存储。
- 生产配置守卫：`APP_ENV=production` 时若 `SESSION_SECRET` 仍为仓库默认值或短于 32 字符，应用拒绝启动；所有响应附带 `X-Content-Type-Options`、`X-Frame-Options`、Referrer 与 Permissions 策略头。
- XSS：Jinja2 自动转义；JS 渲染表格使用 `textContent`，不拼 `innerHTML`。
- SQL 注入：全部参数化查询；`LIKE` 模式对通配符转义。
- 文件：限制扩展名、大小、消息条数；文件名只取 basename。

### 9.3 隔离方案的取舍

数据库层目前采用“只读角色 + `security_barrier` 视图”，而不是在 `app` 基表上启用 RLS。对比实验表明两者能返回同一行集合，计划形状也符合预期；但 RLS 需要为每张表维护策略、处理表属主绕过，并让所有查询路径都承担策略子查询。视图方案把可见性规则集中在一处，Text2SQL 只需暴露视图，个人单机场景的可维护性更高。RLS 的实现与实验保留在 `rls_experiment` 服务中，作为答辩材料和未来多租户部署的参考，不在演示库启用。

### 9.4 审计的可见性与保留

审计的目标是“证明谁改了什么结构”，而不是复制业务数据。因此 `audit_log` 只存主键与列名，不存值；`password_hash` 连列名都不记录；消息、批注、复习内容表不参与审计。管理员可以查看时间线与聚合统计，但看不到任何正文。应用角色对审计表只读，唯一的写路径是 `SECURITY DEFINER` 触发器；清理必须通过至少保留 90 天的受控函数，无法用普通 DML 抹掉近期记录。

## 10. 测试与验收

### 10.1 自动化测试

```text
204 unit tests
130 integration tests
334 total, all passed
```

- 单元：密码哈希/校验、CSRF、登录限流窗口与过期、会话签发时间、生产环境密钥守卫、Cookie 标志与安全响应头、FSRS 初始间隔/评分增长/遗忘与难度边界/保持率与上限、JSON/Markdown/裸文本与三种平台原生导出解析、字段校验、去重分类、Text2SQL AST 守卫（嵌套 DML、表函数、锁、字面量误伤）、EXPLAIN 预检、评测集加载/结果规范化/指标计算、RRF 融合、向量字面量/维度校验、整理建议的模式切换/标签清洗/摘要截断/Prompt 结构、mock 规则、MCP JSON-RPC 协议/工具过滤/输出格式、CLI 文档改写与输出、RLS 执行计划摘要、PWA 资产、MV3 扩展契约、引导/撤销/错误页/静态缓存与性能预算。
- 属性测试：Hypothesis 随机生成 JSON、任意字节/文本与 SQL 片段。解析器不得因畸形输入抛出未捕获异常，成功结果必须满足平台规范化、账号长度、`external_id` 唯一、消息序号连续、发送者合法、内容非空等不变量；SQL 守卫只接受 `SELECT`/`WITH`，危险语句、跨 schema 访问与系统函数必须拒绝，接受后的清洗结果幂等，字符串字面量内的关键字被正确掩码，`wrap_sql` 始终附加行数上限。随机化暴露并修复了真实边界：非字符串 `platform`/`account` 字段、混合类型时间戳排序、非字符串 `current_node`、字符串形式的 `author`，以及词法错误（`TokenError`）未被转换为 `GuardError`。
- 集成：schema 约束与删除行为、权限边界、导入预览/确认/幂等/损坏文件、收件箱批量归类、检索与全局搜索、令牌搜索 API 的可见范围与管理边界、令牌作用域与 MCP 端点（会话详情、到期复习、批注写入及 viewer 越权）、pgvector 可见范围与混合排名、批注复习的 FSRS 四档间隔/评分日志/simple 回退与事件流水、新标签与摘要的创建/更新/清空及 viewer 越权、知识先修图的防环/闭包/级联与视图过滤、RLS 三方式行集合一致与实验对象回滚、物化视图与实时聚合一致及刷新前后陈旧/新鲜、游标分页不重不漏及回退、登录失败锁定与 429/恢复/会话 nonce、备份导出范围与用户隔离（zip 清单、Markdown、CSV）、审计触发器/只读边界/保留清理与内容表不审计、数据库观测指标/白名单执行计划/慢查询降级/学生越权、统计、管理员、采集令牌与 Bearer 入库、Text2SQL 只读/越权/审计与评测编排。

集成测试只连接以 `_test` 结尾的数据库，每个用例前 `TRUNCATE app.users RESTART IDENTITY CASCADE`。测试脚手架使用管理员连接执行 TRUNCATE，因为应用角色 `ls_app` 被刻意设计为没有 TRUNCATE 权限。

工程工具链同样纳入验收：运行与开发依赖写在 `pyproject.toml`，由 `uv.lock` 锁定完整依赖图（当前 57 个包）；Ruff 以 `E/F/I/UP/B/SIM/RUF/C4` 规则检查 `app`、`scripts`、`tests`，pyright 以 basic 模式检查 `app`，pre-commit 在提交前执行配置校验、Ruff 与 pyright。静态检查还推动了表单访问的收紧：路由不再直接调用 `FormData.get()`（其返回类型可能是 `UploadFile`），而是统一经过 `form_text` / `form_file` / `form_text_list`，避免把二进制字段误当文本处理。

同一套检查在 GitHub Actions 中自动执行：`.github/workflows/ci.yml` 以 `pgvector/pgvector:pg16` 启动 PostgreSQL 服务，并用 `uv sync --frozen` 安装锁定依赖；随后运行 `uv lock --check`、Ruff、pyright、`init_db.py --reset`、`seed_demo.py`、`pytest` 与 `verify.py`。CI 显式关闭大模型调用（`TEXT2SQL_MODE=mock`、`EMBEDDING_MODE=off`、`SUGGEST_MODE=off`），保证离线可重复；测试 JUnit 与验收 JSON 作为构建产物上传，失败时也能回看证据。推送与 PR 都会触发，并用 concurrency 取消同一分支的旧任务。

浏览器 E2E 使用 `@playwright/test`，配置见 `playwright.config.mjs`，用例在 `tests/e2e/app.spec.mjs`：匿名访问跳转登录、学生概览与会话、课程成员、会话阅读与 `Ctrl+K` 全局搜索、会话列表游标翻页与回退、粘贴预览不直接入库、Text2SQL 示例查询、只读成员 403 说明页、管理员观测与执行计划切换、390px 手机端无横向溢出且注册 manifest。每个用例收集控制台错误与页面异常（bob 的 403 资源错误单独放行），失败时保留 trace 与截图。CI 在验收之后安装 Chromium，启动 `uvicorn` 并轮询 `/healthz`，再执行 `npx playwright test`，JUnit 随构件上传；应用日志在失败时输出。

统计的实时聚合与物化视图另做独立实验（`scripts/stats_experiment.py`）：在回滚事务内创建带唯一索引的 `mv_course_stats`，对比两者结果、计划与中位耗时；随后插入一条临时会话消息，确认物化视图保持旧值，`REFRESH` 后重新一致。本地演示库一次运行中，实时聚合 0.47 ms、物化视图 0.24 ms（约 2 倍），全量刷新 5.1 ms；演示规模下差距有限，但计划中消除了多表连接与去重聚合，数据增长后收益会更明显。CI 同样执行该实验并上传 JSON，事务结束后不残留实验对象。

### 10.2 端到端验收

`scripts/verify.py` 在演示库上执行 42 项检查，所有写操作放在外层事务中，最后主动回滚：

```text
结果：42/42 PASS，0 FAIL，耗时约 1–4 秒
报告：data/verification/report.json
```

检查项包括：

- 16 张表、15 个视图、主外键/唯一/检查约束、索引与 9 个触发器；标签唯一索引与数据一致性
- 演示数据规模、密码哈希、角色边界
- 重复导入 0 新增 / 84 跳过、损坏文件拒绝、ChatGPT/Qoder/DeepSeek 原生导出适配
- 关键词检索、课程/标签筛选、上下文与批注内联、全局搜索隔离、pgvector 混合检索与管理员 0 结果
- 统计概览、CSV、Markdown
- 批注 → 复习项 → 间隔 2/4 天、复习事件流水与 5 周热力图
- 粘贴收录、重复收录幂等、收件箱批量归类
- 整理建议：新标签幂等写入、摘要唯一更新、viewer 越权拦截
- 知识先修图：32 条边/6 组图、最短路径闭包、成环拒绝、增删后闭包一致
- 采集令牌哈希存储、作用域、Bearer 认证、限流与幂等入库
- RLS / 视图 / 应用层 WHERE 行集合一致、GUC 未设置返回 0 行、实验对象回滚
- 审计：课程更新记录操作者/主键/变更列名，应用角色对审计表无写权限
- 数据库观测：库大小/缓存率/I/O 行/表与索引统计、白名单计划根节点、扩展安装状态与慢查询可用性
- Text2SQL mock 查询、用户过滤、AST 守卫、EXPLAIN 预检拒绝高成本查询、30 题金标准评测（准确率 / 合法率 / 越权拦截）、恶意 SQL 审计、只读角色
- 事务回滚后计数不变、`ls_app` 无 DDL 权限

### 10.3 UI 验证

Playwright 脚本登录 alice / bob / admin，生成桌面端 36 张（含扩展弹窗、知识先修图、审计与观测页）与移动端 10 张截图，检查控制台错误与 390px 横向溢出；bob 的 403 页面是预期行为，脚本单独放行。知识先修图另用浏览器在 1440px 与 390px 宽度手工截图，确认 6 张课程卡片无横向溢出、控制台无错误。R1 另在第二个实例上串联本地嵌入桩：无 Key 时确认“词法检索”徽标与关键词高亮，启用后确认覆盖率、补齐按钮、“混合检索”徽标和 5 条“语义相近”结果，测试结束清除桩向量并恢复演示库。R6 同样在独立实例上验证：无 Key 时按钮不渲染；配置本地 LLM 桩后点击建议，摘要与新标签填入表单，保存后页面出现“总结”批注与新标签，控制台无错误；测试结束重新播种演示库。`scripts/extension_smoke.mjs` 另用真实浏览器验证扩展提取、按钮文字清理、预览、Bearer 鉴权与发送体结构。另外用 Performance API 验证首屏 JS 8KB、请求数 2、移动端 LCP 268ms，并检查 Service Worker 缓存版本与静态资源版本参数。

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
- 采集令牌只存 SHA-256，不做可逆加密；作用域与限流计数都落在数据库行上，避免多 worker 内存态不一致。
- 浏览器扩展坚持“点击才读取、点击才上传”，不申请常驻 host 权限，也不做后台轮询；DOM 改版时回退到平台导出文件。
- 平台导出适配只做字段归一，不复制一套入库逻辑，所有来源继续共享预览、查重与事务确认。
- 测试库脚手架使用管理员连接，而不是给应用角色开放 TRUNCATE。
- 先修图的防环放在数据库触发器里，而不是只靠应用层判断；页面按课程拆成多张小图，避免一张巨型 SVG。

## 12. 运行与演示

一条命令起全栈（推荐演示）：

```bash
docker compose up -d --build
# 容器启动时自动应用 schema/迁移，空库时写入演示数据
curl -s http://127.0.0.1:8000/healthz
```

本机开发与完整验收：

```bash
docker compose up -d db
.venv/Scripts/python.exe scripts/init_db.py --reset
.venv/Scripts/python.exe scripts/seed_demo.py
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe scripts/verify.py --json data/verification/report.json
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
node scripts/extension_smoke.mjs
node scripts/screenshots.mjs
```

桌面个人模式（可选，需 Rust、MSVC Build Tools 2022 与 WebView2）：

```bash
uv pip install -r packaging/requirements.txt
uv run python packaging/build.py      # 内置 PostgreSQL + pgvector + sidecar + SQL
cd desktop
npm install
npm run dev                           # 独立 LS_HOME，首次启动 initdb 并进入 /setup

# 生成安装包（Windows）
npx tauri build --bundles nsis
```

演示账号：`alice / Alice123!`、`bob / Bob123!`、`admin / Admin123!`。完整演示脚本见 [demo-script.md](demo-script.md)。

## 13. 限制与改进方向

1. **单机规模**：未做分布式与高并发压测；并发导入依靠唯一键 + advisory lock 保证正确性。
2. **中文检索**：`pg_trgm` 对 1–2 字短查询选择性差；混合检索可补足语义召回，但需要部署方配置嵌入模型并承担相应调用成本，数据量更大时仍可引入 `zhparser` 或外部搜索引擎。
3. **模拟数据**：会话模板较规整，重复追问句会命中查重；真实数据需要平台适配层与更复杂的清洗规则。
4. **Text2SQL**：mock 规则只覆盖常见问题；真实 LLM 的 SQL 质量依赖模型与 Prompt，仍需守卫与人工复核。
5. **管理员权限**：当前提供用户启停、聚合统计、结构变更审计与数据库观测；观测页刻意不提供自由 SQL，避免管理员越权读取正文。
6. **实时性**：统计全部实时计算，数据量增大后可以增加物化视图，但需要明确刷新策略与冗余一致性。
7. **扩展 DOM 依赖**：ChatGPT / DeepSeek / Qoder 的页面结构随时可能改版；扩展提供预览与回退提示，但长期仍以平台导出文件和适配器为主。
8. **令牌与离线边界**：令牌限流是单机固定窗口，多实例部署需要换成共享计数器；离线数据只保存在当前浏览器，换设备需重新保存。
9. **整理建议的质量与成本**：建议依赖外部模型，输出不保证稳定；服务端只做清洗与长度限制，25 秒超时后返回可读错误。启用前需要部署方提供 Key，并明确该会话节选会被发送到模型服务；结果永远需要用户确认，不会自动写入。
10. **桌面分发**：已提供 Windows NSIS 与 macOS DMG 个人版安装包（内置 PostgreSQL 与 sidecar，不依赖本机 Python/Docker），但安装包未签名，Windows SmartScreen 与 macOS Gatekeeper 会提示；macOS 仅由 CI 构建并通过 `.app` 内 smoke，尚未在真机人工验收；自动更新仍为手动下载覆盖安装。

## 14. 结论

本项目完成了一个从导入、组织、检索、批注、复习到统计导出的完整学习数据闭环，并用 Text2SQL 展示了自然语言查询关系数据的一种可控实现方式。数据库设计覆盖了 E-R 建模、范式分析、完整性约束、索引、触发器、递归查询、事务与最小权限；应用层保持了 SQL 可见、权限可验证、流程可回滚。采集侧的令牌与作用域、Bearer 接口、平台适配与 MV3 扩展，离线阅读/复习能力，把标签连成有向无环先修图的探索，以及可供脚本与 MCP 客户端调用的接口，让系统在真实使用中更顺手。数据库层另用一个全部回滚的 RLS 对比实验验证了三种隔离方式的等价性，并说明了继续使用权限视图的理由。管理员侧的结构变更审计与数据库观测都坚持“只给元数据与聚合指标，不给内容读取后门”。自动化测试与端到端验收为演示和答辩提供了可重复的证据。

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
| `sql/schema.sql` | 12 张基线表、约束、索引、触发器 |
| `sql/migrations/` | 增量迁移：复习事件流水、批注事件 ID、采集令牌、消息向量、标签先修边、令牌作用域、审计历史、观测扩展、FSRS 状态 |
| `sql/views.sql` | 15 个 t2s 权限视图 |
| `sql/roles.sql` | `ls_app` / `t2s_reader` 最小权限 |
| `sql/showcase.sql` | 23 条关键 SQL |
| `app/services/import_service.py` | 导入解析、预览、确认事务、Bearer 入库 |
| `app/importers/adapters.py` | ChatGPT / Qoder / DeepSeek 原生导出归一 |
| `app/services/token_service.py` | 采集令牌哈希、限流、认证与撤销 |
| `app/services/tag_graph_service.py` | 先修图查询、环检测辅助、分层布局与 SVG 生成 |
| `app/cli.py` | `ls search` / `ls import` 命令行客户端 |
| `app/mcp_server.py` | MCP stdio JSON-RPC 服务与四个工具 |
| `app/routers/mcp.py` | 令牌作用域保护的 MCP 查询与批注端点 |
| `app/services/rls_experiment.py` | RLS / 视图 / 应用层 WHERE 隔离对比实验 |
| `app/audit.py` | 事务内操作者 GUC，供审计触发器读取 |
| `app/services/text2sql_service.py` | 生成、守卫、只读执行、审计 |
| `app/permissions.py` | 课程角色与会话可见性 |
| `extension/` | Chrome MV3 扩展、隐私说明与回退指引 |
| `desktop/` | Tauri v2 个人版桌面壳：内置 PostgreSQL/sidecar 编排、托盘、局域网共享、`--smoke` 自检 |
| `packaging/` | PostgreSQL 裁剪/源码构建、pgvector、sidecar 打包、资源汇总与体积报告 |
| `scripts/verify.py` | 42 项端到端验收 |
| `scripts/rls_experiment.py` | 隔离对比实验 CLI 与 JSON 报告 |
| `scripts/extension_smoke.mjs` | 扩展提取与发送契约冒烟 |
| `docs/design.md` | 数据库设计详解 |
| `docs/requirements.md` | R01-F01 需求拆解 |
| `docs/sql-showcase.md` | 关键 SQL 与结果 |
| `docs/ai-collaboration.md` | AI 协作与方案分歧 |
| `docs/demo-script.md` | 演示视频脚本 |
