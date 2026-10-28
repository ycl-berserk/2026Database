# AI 协作记录与设计分歧

## 1. 协作方式

本项目采用“作者定方向、AI 做实现与检查、作者验收”的方式：

| 阶段 | 作者 | AI 助手 |
| --- | --- | --- |
| 选题与范围 | 选定选题 2；确定同时做动态网站与 Text2SQL；确定 PostgreSQL | 整理需求、给出技术方案与取舍 |
| 数据设计 | 确认以“来源—会话—消息—批注—复习”为核心；确认模拟数据 | 给出 E-R、14 张表关系模式、约束与索引草案 |
| 实现 | 确认关键方案；运行与验收；提出修改 | 编写 schema、服务层、页面、测试、脚本、文档草稿 |
| 验收 | 运行测试与页面；判断是否符合作业要求 | 生成 220 个测试、37 项验收检查、截图与扩展冒烟脚本 |
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

## 3. 实现过程中发现并修复的问题

以下问题不是“设计分歧”，而是实际运行测试/截图时暴露的缺陷，记录了发现方式与修复：

| 问题 | 发现方式 | 原因 | 修复 |
| --- | --- | --- | --- |
| 集成测试 `permission denied for table users` | pytest | `ls_app` 无 TRUNCATE 权限 | 测试脚手架改用管理员连接，并断言 `_test` 后缀 |
| `course 1 must have exactly one owner, found 0` | pytest | 课程与 owner 成员分两条 autocommit 语句插入，延迟触发器提前触发 | fixture 用 `with conn.transaction():` 包住两条插入 |
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

# 2. 端到端验收（37 项，写入 report.json）
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
