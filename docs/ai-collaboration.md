# AI 协作记录与设计分歧

## 1. 协作方式

本项目采用“作者定方向、AI 做实现与检查、作者验收”的方式：

| 阶段 | 作者 | AI 助手 |
| --- | --- | --- |
| 选题与范围 | 选定选题 2；确定同时做动态网站与 Text2SQL；确定 PostgreSQL | 整理需求、给出技术方案与取舍 |
| 数据设计 | 确认以“来源—会话—消息—批注—复习”为核心；确认模拟数据 | 给出 E-R、16 张表关系模式、约束与索引草案 |
| 实现 | 确认关键方案；运行与验收；提出修改 | 编写 schema、服务层、页面、测试、脚本、文档草稿 |
| 验收 | 运行测试与页面；判断是否符合作业要求 | 生成 369 个测试、42 项验收检查、截图与扩展冒烟脚本 |
| 文档 | 确认结构、口径与最终表述 | 生成报告、设计、需求、SQL 展示、演示脚本草稿 |

AI 的输出不作为“正确性证明”。所有结论以测试、验收报告、数据库查询和浏览器实际页面为准。

## 2. 被否决或修正的方案

### 2.1 直接复用参考项目的笔记/向量模型

参考项目 `qoder-reviewmate` 使用“课程笔记 + 向量检索 + seekdb”。最初考虑复用其数据模型，但作业要求是关系数据库、E-R、范式和经典 SQL；向量检索会把重点从关系建模移走。

**决定**：只复用 CSS 视觉 token、FastAPI 生命周期、导入“解析→校验”思路和验收脚本骨架；重新设计以会话/消息/批注/复习为核心的关系模型。向量能力在 Phase 3 作为**可选检索通道**重新评估后引入（pgvector），但核心数据模型、权限与业务写入仍完全由关系表承载，且未配置模型时系统行为与纯词法版本一致。

### 2.2 在 `conversations` 中冗余 `owner_id`

为了让“我的会话”查询少一次 JOIN，曾在设计中出现过在 `conversations` 直接存 `owner_id` 的方案。

**否决原因**：`conversation → source → owner` 形成传递依赖，违反 3NF；来源所有者变更时需要批量更新会话。

**最终**：通过 `source_id` 关联取得所有者；查询使用 `JOIN app.sources s ON s.id = c.source_id`，并为 `sources.owner_id` 建索引。

### 2.3 用生成列保存 `content_hash`

曾考虑 `content_hash char(32) GENERATED ALWAYS AS (md5(content)) STORED`，便于查重。

**否决原因**：引入 `id → content → content_hash` 的传递依赖，破坏 3NF；且会增加行宽。

**最终**：使用表达式索引 `CREATE INDEX ... ON app.messages ((md5(content)))`，查重时 `GROUP BY md5(content)`，既避免冗余列，又能走索引。

### 2.4 `review_items` 只允许一条批注一个复习项

最初把唯一键设计为 `annotation_id`，理由是“一条批注只需要复习一次”。

**修正原因**：共享课程中，同一批注可能被多个有权限的用户各自加入复习队列；若按批注唯一，第二个用户无法创建自己的复习项。

**最终**：`UNIQUE (user_id, annotation_id)`，允许多人复习同一批注，但同一人不能重复入队。

### 2.5 Text2SQL 只做 Prompt 约束

曾考虑“在 Prompt 中要求模型只生成 SELECT”即可。

**否决原因**：Prompt 可被注入，模型也可能幻觉出 `DELETE`、系统表或跨用户查询；仅靠模型自律不构成安全边界。

**最终**：六层防护——Prompt、SQL 守卫、LIMIT 包装、只读角色、行级权限视图、审计日志。即使守卫被绕过，`t2s_reader` 也无法写库或读 `app` 基表。

### 2.6 预览状态放在进程内存

最初想在 `app.state` 中保存上传解析结果，确认时再取。

**否决原因**：多 worker、重启、30 分钟等待都会丢失；也无法审计“谁在什么时候预览了什么文件”。

**最终**：`import_batches` 持久化 JSONB payload、UUID token、TTL、状态与 summary；确认时 `FOR UPDATE` 锁行并清空 payload。

### 2.7 给应用角色开放 TRUNCATE 以便测试

集成测试需要清库。最初直接使用 `ls_app` 连接测试库执行 `TRUNCATE`，报 `permission denied`。

**否决原因**：`ls_app` 被刻意设计为只有 DML，没有 TRUNCATE/DDL；为了测试方便而放宽生产角色权限，会削弱最小权限设计。

**最终**：测试脚手架使用管理员连接，但强制断言数据库名以 `_test` 结尾；应用运行时仍使用 `ls_app`。这样既保留最小权限，又让测试可清理。

### 2.8 中文全文检索用 `to_tsvector`

最初按常规方案使用 PostgreSQL 全文检索。

**否决原因**：默认配置对中文不分词，整段中文会被当成一个 token，搜索“事务”无法命中“事务与隔离级别”中的词。

**最终**：`pg_trgm` GIN + `ILIKE`；报告中如实说明小表可能走顺序扫描，并用 `SET enable_seqscan=off` 对比证明索引可用。

### 2.9 采集令牌加密存储

最初考虑像密码一样“可逆加密”令牌，方便以后在页面上再次展示。

**否决原因**：令牌只需要验证，不需要还原；可逆加密多一把必须保管的密钥，泄露后风险更大。

**最终**：只存 SHA-256 与可读前缀，明文只在创建响应中显示一次；认证使用常量时间比较，支持撤销与停用账号拒绝。固定窗口计数直接落在 `api_tokens` 行上，避免多 worker 内存计数不一致。

### 2.10 扩展申请常驻站点权限

最初想给扩展声明 `host_permissions` 覆盖 ChatGPT / DeepSeek / Qoder 与学习空间，安装时一次授权。

**否决原因**：普通用户看到“读取所有网站数据”会犹豫；扩展只需要当前标签页和用户配置的学习空间地址。

**最终**：固定权限只有 `activeTab` / `storage`；学习空间地址在首次发送时通过 `chrome.permissions.request` 按需申请。内容脚本只在收到弹窗消息时提取，不轮询、不自动上传；平台 DOM 改版时明确提示改用导出文件。

### 2.11 为每个采集渠道复制一套入库逻辑

曾考虑让扩展直接调用新的“快速入库”接口，绕开 `import_batches` 预览。

**否决原因**：会形成两套去重、课程映射与事务逻辑，重复导入、冲突计数和权限校验容易出现分叉。

**最终**：Bearer 接口与文件导入共用 `parse → save_preview → confirm_import`；扩展、脚本、平台导出都只是统一 JSON 的生产者。平台适配器只做字段归一（ChatGPT `current_node`、Qoder 别名、DeepSeek 标题格式），不改写下游流程。

### 2.12 把复习历史塞进 `review_items`

最初想在 `review_items` 上增加 `history jsonb` 或若干“最近复习时间”列。

**否决原因**：复习历史是一对多事件，塞进 JSON 会破坏 1NF，热力图与离线重放也难以用 SQL 聚合。

**最终**：`review_items` 保留最新聚合状态，新增 `review_logs` 流水；`UNIQUE (user_id, event_id)` 让离线 outbox 重放幂等。热力图用 `generate_series` + `LEFT JOIN` 补零，统计仍可实时计算。

### 2.13 用正则黑名单拦 SQL

最初只靠正则：匹配 `select` 开头、拒绝 DML 关键字和 `app.` 前缀，再扫 `FROM/JOIN` 后的表名。

**否决原因**：正则看不到语法结构。`WITH x AS (DELETE ... RETURNING ...) SELECT * FROM x`、`SELECT ... FOR UPDATE`、`FROM generate_series(...)` 都可能绕过；反过来，正文里的 `delete` 或名为 `"drop"` 的列又会被误伤。

**最终**：用 `sqlglot` 按 PostgreSQL 方言构建 AST，先拒绝非 SELECT/WITH/UNION 根节点与嵌套写入节点，再校验表名与函数名；正则黑名单降为第二层，并在匹配前屏蔽字符串字面量和引用标识符。数据库只读角色与视图仍是最后边界。

### 2.14 只靠 LIMIT 与超时防止慢查询

最初认为 3 秒 `statement_timeout` 加 200 行 LIMIT 已经足够，不必在执行前判断查询代价。

**否决原因**：超时是在查询开始跑之后才生效，复杂笛卡尔积仍会占用连接与 CPU；LIMIT 只截断返回行，不会减少计划本身要扫描的数据。

**最终**：在执行前用同一只读角色跑 `EXPLAIN (FORMAT JSON)`，按根节点总代价与估算行数设阈值，超限直接拒绝并记 `rejected`；预检有独立 1 秒超时，且不实际取数。阈值做成环境变量，答辩时可现场调低演示拦截。

### 2.15 用金标准评测直接汇报“准确率”

最初打算只展示几条成功问答，用“看起来都能查对”来证明 Text2SQL 可用。

**否决原因**：固定演示会掩盖换问法、聚合、排名类问题的失败；没有分母的“准确率”不可复现，也无法回答答辩时的追问。

**最终**：建立 30 题金标准（含 10 条恶意 SQL、2 条跨用户用例），同时执行金标准与生成 SQL，按列集合与行多重集比较结果。离线 mock 的 17/30（56.7%）如实写入报告，并明确这是规则模式的下限；金标准合法率与越权拦截率单独统计，均要求 100%。评测同时暴露了 mock 与少样本中 `annotation_kind` 列名错误（`v_my_annotations` 实际列名为 `kind`），修正后金标准合法率才达到 100%。

### 2.16 混合检索的融合方式与依赖策略

最初考虑把 `pg_trgm` 相似度与向量余弦距离加权求和，或强制所有部署都配置嵌入模型。

**否决原因**：两种分数的量纲与分布不同，权重需要针对数据调参；强制外部模型会让离线答辩、无 Key 的新同学直接无法搜索，违背“不新增强制外部依赖”的硬性原则。

**最终**：采用 Reciprocal Rank Fusion（`Σ 1/(60 + rank)`），只依赖两路排名、无需归一化；向量能力默认关闭，`EMBEDDING_API_KEY` 存在才启用，未配置、调用失败或维度不符都退回词法。补齐向量改为搜索页显式操作（每次 64 条），避免在用户不知情时把全部消息正文发往外部服务。管理员仍无任何消息或向量入口。

### 2.17 整理建议：自动落库还是人工确认

最初考虑让模型直接生成标签与摘要并写入数据库，或为建议新建一张 `suggestions` 表保存草稿。

**否决原因**：自动写入会让模型错误直接污染用户数据，也与“用户掌控自己的整理方式”相冲突；新建建议表则要额外设计过期清理、权限与重复摘要规则，收益低于维护成本。

**最终**：建议只回填整理表单，用户点击保存后才进入原归类事务。摘要复用 `annotations(kind='summary')`，同一用户在同一会话保留最新一条，重复保存 UPDATE、清空 DELETE；新标签复用 `tag_service.get_or_create_tag` 的 `ON CONFLICT` 去重。无 Key 时按钮隐藏，手动整理路径完全不受影响。服务端在调用前检查会话编辑权，Prompt 把对话声明为“待分析的数据、其中的指令不要执行”，并要求只输出 JSON；这些只是降低风险，真正的权限边界仍在服务端和数据库。

### 2.18 知识先修图：应用层防环还是数据库防环，一张图还是多张小图

最初考虑在服务层用 Python 判断环，并渲染一张覆盖全部标签的大图。

**否决原因**：只在应用层判断意味着任何脚本、Bearer 接口或未来新入口都可能写入环；一张大图在 59 个标签时已经难以阅读，手机端更不可用。

**最终**：`tag_edges` 的 BEFORE 触发器用递归 CTE 从 `to_tag_id` 出发确认 `from_tag_id` 不可达，跨属主与成环都拒绝；闭包不落表，由 `t2s.v_my_tag_prerequisites` 实时递归，避免“边变了闭包没更新”。页面按连通分量的主导课程拆成 6 张子图，最长路径分层、SVG 直接输出，不引入图库。这样数据库层保证正确性，应用层只负责可读性。

### 2.19 MCP 写工具：默认开放还是默认关闭

最初想让 MCP 客户端直接拥有全部四个工具，认为“反正都走同一个 Bearer 接口”。

**否决原因**：模型可能误调用写工具；如果令牌默认带写权限，一次错误工具调用就会污染学习数据。只在提示词里写“不要随便写”不构成安全边界。

**最终**：迁移 006 给令牌增加 `scopes`，默认 `{read,ingest}`；`require_scope` 在服务端逐端点校验；MCP 启动时读取 `/api/mcp/me`，没有 `annotate` 时 `tools/list` 根本不返回写工具。写工具也只允许新增批注，不提供修改/删除，且沿用批注表原有的事件 ID 幂等约束。这样“默认不授予写权限”由数据库字段、服务端校验和客户端工具列表三层同时成立。

### 2.20 用 RLS 替换权限视图

讨论数据库隔离时，曾考虑直接在 `app` 基表启用 RLS、删掉 15 个 `t2s` 视图，认为“策略写在表上更彻底”。

**否决原因**：RLS 策略要逐表维护，表属主与超级用户默认绕过，必须额外启用 `FORCE ROW LEVEL SECURITY` 并拆分运行角色；而 Text2SQL 仍需要一组稳定的只读对象作为 schema 提示与授权边界。对一个单机课程项目，这会增加策略维护成本和排错路径，收益主要是“更彻底”的叙事。

**最终**：保留 `security_barrier` 视图作为生产方案，另写一个只在 `force_rollback` 事务内运行的 RLS 对比实验。实验证明三种方式行集合一致，并暴露了“未限定外层的 `course_id` 被内层子查询解析”的真实 bug；修复后 RLS 计划与应用层 WHERE 完全相同，视图则因 barrier 出现 `Subquery Scan`。这样既能在答辩中讲清两种方案的代价，又不把未经验证的全表策略带进演示库。

### 2.21 审计日志：记录正文还是只记元数据

最初想用通用触发器把每张表的旧值/新值都写进审计表，方便“完整追溯”。

**否决原因**：消息正文、批注与复习内容属于用户隐私，管理员本就不应读取；把这些值复制进审计表等于绕过了管理页的边界。全表审计还会让复习打卡、离线重放等高频写入产生大量冗余行，且 `password_hash` 等敏感字段会被顺带记录下来。

**最终**：只审计 `courses`、`course_members`、`users` 三张“权限与结构”表，只记录对象、动作、主键与**变更列名**，不记录任何列值；`password_hash` 即使发生变化也不出现在变更列里；消息、批注、复习表不建审计触发器。触发器函数用 `SECURITY DEFINER` 写入，`ls_app` 对 `audit_log` 只有 `SELECT`，无法伪造或删除记录；清理函数限定最少保留 90 天，避免“保留策略”变成删证据的捷径。操作者通过事务内 GUC `app.actor_user_id` 传入，系统初始化等没有操作者的写入记为 `NULL`。

### 2.22 观测页：自由 SQL 面板还是白名单指标

最初想让管理员在页面里直接输入任意 SQL 并查看 `EXPLAIN`，认为“管理员本来就该能看数据库”。

**否决原因**：管理员边界是“只读聚合信息”，不是“可读所有数据”。自由 SQL 面板会把管理员变成超级读者：一条 `SELECT content FROM app.messages` 就能绕过课程权限；`EXPLAIN` 也能通过常量/函数调用试探数据。即使加只读角色，管理员仍可借应用连接读到正文。另一个隐式泄漏是 `pg_stat_statements.query`：归一化语句通常安全，但自定义 SQL、DDL 或未参数化的调试语句可能包含字面量。

**最终**：观测页只展示 PostgreSQL 统计视图（`pg_stat_database`、`pg_stat_io`、`pg_stat_user_tables`、`pg_stat_user_indexes`）和 4 个白名单执行计划键；`EXPLAIN (FORMAT JSON)` 只估算、不执行；慢查询仅保留 SELECT/WITH/INSERT/UPDATE/DELETE，并排除 `pg_stat_statements` 与 `EXPLAIN` 自身，页面只显示归一化语句与指标。`pg_stat_statements` 是可选扩展：迁移 008 条件创建，未预加载时页面降级提示，不阻断其他功能；`ls_app` 仅授予 `pg_read_all_stats`。这样“能观察系统”和“能读用户内容”被明确分开。

## 3. 实现过程中发现并修复的问题

以下问题不是“设计分歧”，而是实际运行测试/截图时暴露的缺陷，记录了发现方式与修复：

| 问题 | 发现方式 | 原因 | 修复 |
| --- | --- | --- | --- |
| 集成测试 `permission denied for table users` | pytest | `ls_app` 无 TRUNCATE 权限 | 测试脚手架改用管理员连接，并断言 `_test` 后缀 |
| `course 1 must have exactly one owner, found 0` | pytest | 课程与 owner 成员分两条 autocommit 语句插入，延迟触发器提前触发 | fixture 用 `with conn.transaction():` 包住两条插入 |
| RLS 实验里 bob 多看到 15 个会话 | 运行 `scripts/rls_experiment.py` | 策略子查询中未限定的 `course_id` 被解析为内层 `cm.course_id`，谓词恒真 | 用 `conversations_rls.course_id` 显式限定外层列，并加行集合一致性断言 |
| 唯一用户名测试抛 CheckViolation 而非 UniqueViolation | pytest | 测试密码 `'x'` 先触发哈希长度 CHECK | 改用 40 字符哈希 |
| 导入测试期望 2 个新标签，实际 1 个 | pytest | base fixture 已创建“事务”标签 | 调整期望为 1 |
| 过期预览更新违反 `import_batches_expiry_check` | pytest | 只改 `expires_at` 使其早于 `created_at` | 同时把 `created_at` 回拨 1 小时 |
| 日期筛选返回 3 条而非 1 条 | pytest | base fixture 的会话 `started_at` 都是同一天 | 将会话分散到 2/3/4 月，并加 `since_march` 断言 |
| 复习逾期期望 0，实际 1 | pytest | 服务把 `due_date = current_date` 也算“到期” | 调整语义与断言，页面区分“逾期/今日到期” |
| 片段长度断言 62，实际 67 | pytest | 片段窗口包含省略号与关键词上下文 | 放宽为 `<= 70` |
| `verify.py` 查 t2s 视图数为 0 | 端到端验收 | `information_schema.views` 只显示有权限的视图 | 改用 `pg_views WHERE schemaname='t2s'` |
| 关键词“事务”期望 ≥6，实际 4 | 端到端验收 | 模拟数据中命中会话数少于预期 | 放宽为 ≥4，同时保留片段断言 |
| 批注生命周期检查导致统计批注数 +1 | 端到端验收 | 临时批注在统计检查之前插入 | 把批注检查移到统计/导出之后 |
| Git Bash 验收输出乱码 | 手工运行 | Windows 控制台默认编码 | `sys.stdout.reconfigure(encoding="utf-8")` |
| 导入预览“可更新”显示 `<built-in method update ...>` | Playwright 截图 | Jinja2 把 `counts.update` 解析为 `dict.update` 方法 | 模板改用 `counts["update"]` 等下标访问 |
| 错误页始终显示 400 | Playwright 截图 | `render()` 的 `status_code` 是 HTTP 参数，模板却当上下文变量，`default(400)` 生效 | 额外传 `error_status`，模板读取该变量 |
| E-R 图标题与顶部属性重叠 | 截图目检 | 标题位于 `y=46`，顶部属性椭圆也在 `y=50` | 画布增高到 1250，图内容整体下移 70px |
| Playwright 截 E-R 图超时 | 截图脚本 | 对 SVG 文档做全页截图 + 高 DPR 偶发挂起 | 改为 `locator("svg").screenshot()`，DPR=1 |
| bob 的 403 页面被脚本误报为控制台错误 | 截图脚本 | 浏览器对 403 资源输出 console error | 仅对越权截图放行 `403 (Forbidden)`，其他页面不放过 |
| 评测集 4 题报 `column "annotation_kind" does not exist` | `eval_text2sql.py` | mock、少样本与 3 条金标准把 `v_my_review_items` 的列名误用到 `v_my_annotations`（实际为 `kind`） | 统一改为 `kind`，金标准合法率从 90% 恢复到 100% |
| 验收统计标签数 61（期望 59），重复导入 `tags_new=2` | `verify.py` | 从 Alpine 镜像切到 Debian 版 pgvector 后，旧数据目录的 `tags_owner_name_unique` 索引残留旧 libc 排序，7 个中英混合标签无法被索引查到，`ON CONFLICT` 退化为重复插入 | 对演示库与测试库执行 `REINDEX DATABASE`；amcheck 复核全部 B-tree；新增 `verify.schema.index_health` 逐标签索引查找守护；README 记录镜像切换步骤 |
| R6 首次导入报 `cannot import name 'can_edit_conversation'` | 启动应用 | 把会话编辑权判断误想成 `permissions` 模块函数，实际在 `conversation_service` | 改为调用 `conversation_service.can_edit_conversation`，权限拒绝仍返回 403 |
| R6 浏览器走查等待建议超时 | Playwright | 本地 LLM 桩单次响应慢，4 秒等待不足 | 等待窗口放宽到 30 秒；服务端 25 秒超时并返回可读错误，不影响手动整理 |
| K2 新库初始化时迁移 005 报 `role "ls_app" does not exist` | `init_db.py --reset` | 迁移在 `roles.sql` 之前执行，`GRANT` 目标角色还不存在 | 授权放进 `DO` 块，角色存在时才执行 |
| K2 图页面渲染出 21 张碎片卡片 | 浏览器走查 | 按连通分量直接出图，两节点小组件过多 | 改为先按“主导课程”合并分量，再输出 6 张课程卡 |
| K2 模板里 `loop.parent.loop.index` 报未定义 | 页面渲染 | Jinja2 不支持跨层 `loop` 引用 | 每层用 `{% set component_index = loop.index %}` 保存索引，供箭头 marker ID 使用 |
| K2 单元测试断言分组失败 | pytest | `_components` 返回列表的列表，断言写成列表含集合 | 改为把每组转成 `set` 再比较 |
| D2 审计测试把用户插入当成课程事件 | pytest | 新课程 id 恰好与某个用户 id 相同，按主键过滤时串行 | 事件查询同时限定 `table_name IN ('courses','course_members')` |
| D2 清理测试 SQL 报语法错误 | pytest | 用 f-string 拼 `now() -120 days`，生成了非法区间字面量 | 改为 `now() - %s::interval` 并传参 |
| D2 `verify.db.audit` 报事务已中止 | `verify.py` | 前一项 `db.app_role` 故意触发 `CREATE TABLE` 权限错误，污染了外层事务 | 把预期失败语句包进嵌套 `with conn.transaction():` 保存点 |
| D2 审计行出现 `password_hash` 变更列 | 演示库查询 | 通用触发器按 JSONB 键差异计算列名，未排除密码列 | 函数内显式排除 `password_hash`，清理历史行并加断言 |
| D2 管理页截图拍到的是概览页 | Playwright 截图 | admin 登录后跳转 `/dashboard`，脚本直接截图 | 登录后显式导航 `/admin`，并新增审计面板截图 `14b` |
| D2 清理保留期可设为 7 天 | 代码走查 | 最小保留期最初只校验正数 | 迁移、服务与测试统一收紧为不少于 90 天 |
| D3 `pg_stat_io` 聚合报 `GroupingError` | pytest | `ORDER BY reads + writes` 引用了非分组列 | 改为 `sum(reads + writes)`，并以 `backend_type` 次序兜底 |
| D3 慢查询检查在验收中不可用 | `verify.py` | 带参数的 SQL 里写了字面量 `%`，psycopg 将其当成占位符 | ILIKE 模式改为 `%%pg_stat_statements%%` / `%%EXPLAIN%%`，并收窄异常捕获 |
| D3 慢查询列表混入迁移工具语句 | 浏览器截图 | 未过滤工具语句，出现 `DO $$`、`CREATE TABLE rls_experiment...` | 只保留 SELECT/WITH/INSERT/UPDATE/DELETE 语句，迁移与临时实验不再出现 |
| D3 过滤后慢查询列表为空 | 服务层直连检查 | PostgreSQL ARE 中 `\b` 表示退格而非单词边界，`...delete)\b` 永不匹配 | 改为 `(\s|$)` 边界并验证 224 条语句可匹配 |
| D3 浏览器切换执行计划超时 | Playwright 走查 | `button[type="submit"]` 同时命中侧栏退出登录按钮，页面跳回登录 | 作用域限定为 `form[action="/admin/observability"] button[type="submit"]` |

这些问题说明：**测试和真实浏览器截图是必要的**。仅靠“代码看起来对”无法发现模板变量覆盖、Jinja2 属性解析、触发器延迟时机、权限角色差异等问题。

## 4. 关键人工决策

以下决策由作者确认，AI 只提供备选与后果分析：

1. **两种形态都做**：主站点 + Text2SQL，而不是只做其中一个。
2. **PostgreSQL**：使用 Docker Compose，宿主端口 5433，便于本机演示。
3. **模拟数据**：没有真实导出，定义统一格式并生成可复现数据；报告中明确标注。
4. **不引入 ORM**：关键 SQL 必须在报告中可定位。
5. **管理员边界**：只看用户与聚合统计，不读正文。
6. **单机范围**：不做分布式、消息队列、缓存；把精力放在数据库设计与正确性。
7. **测试库隔离**：`learning_space_test`，强制 `_test` 后缀，每测清理。
8. **知识先修图的环由数据库保证**：触发器递归检查，而不是只依赖页面或服务层；展示按课程拆图。
9. **审计只记结构变更的元数据**：不复制消息、批注、复习正文，也不记录密码列；管理员能看到“谁改了哪条课程/成员/账号”，看不到学习内容。
10. **观测页不做自由 SQL**：只给白名单指标与 4 个执行计划，管理员能判断“库是否健康”，但不能借 SQL 读取他人内容。

## 5. AI 使用的边界与反思

### 5.1 有效的部分

- 快速生成 schema、服务层、页面与测试骨架，减少重复劳动。
- 把需求拆成可验证的检查项，发现遗漏（如删除策略、部分唯一索引、预览 TTL）。
- 在测试失败时定位具体 SQL/约束错误，缩短调试时间。
- 生成文档草稿与截图脚本，保持报告与代码一致。

### 5.2 需要人工把关的部分

- **业务语义**：例如“到期”是否包含今天、“可更新”如何计数、管理员能否看正文，必须由作者定义。
- **安全边界**：AI 容易只做应用层校验；需要人工坚持数据库角色、视图、触发器兜底。
- **数据真实性**：模拟数据容易过于规整；报告必须说明其局限，不能把模板句当成真实行为。
- **验收标准**：AI 可能为了让测试通过而放宽断言；本次在日期筛选、关键词数量、片段长度上都做了“放宽”，但保留了核心语义断言，并在文档中说明。

### 5.3 不足与后续

- 平台原生导出已覆盖 ChatGPT / Qoder / DeepSeek 的当前样例，但平台版本差异仍可能变化；适配器按“字段缺失时给出警告而非崩溃”设计。
- 浏览器扩展依赖页面 DOM，平台改版后需要更新选择器；扩展提供预览、错误提示与导出文件回退。
- 没有做并发压测；advisory lock 与唯一键的正确性只在逻辑和集成测试层面验证。
- Text2SQL 的真实 LLM 模式只用本地 stub 测试，未在报告中声称真实模型效果。
- 若继续迭代，优先补：更多平台适配、Text2SQL 结果可视化、复习提醒、令牌限流的共享计数器。

## 6. 如何复核本文档中的说法

```bash
# 1. 全量测试
.venv/Scripts/python.exe -m pytest -q

# 2. 端到端验收（42 项，写入 report.json）
.venv/Scripts/python.exe scripts/verify.py --json data/verification/report.json

# 3. 关键 SQL（含真实输出）
docker compose exec -T db psql -U postgres -d learning_space -f /dev/stdin < sql/showcase.sql

# 4. 页面截图与控制台检查
node scripts/screenshots.mjs

# 5. 浏览器扩展提取与发送契约
node scripts/extension_smoke.mjs
```

对应证据：

- 测试：`tests/unit/`、`tests/integration/`
- 验收：`data/verification/report.json`
- SQL：`sql/showcase.sql`、`docs/sql-showcase.md`
- 页面：`docs/screenshots/`
- 设计：`docs/design.md`、`docs/requirements.md`
