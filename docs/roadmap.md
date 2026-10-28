# v2 改进路线图

> 状态：Phase 1、Phase 2、Phase 3、Phase 4 已完成（T1、T2、T5、R1、R6、K2、A1、A2、D1、D2、D3）。当前基线为 334 项测试（204 单元 + 130 集成）与 42 项端到端验收，本路线图的任何后续项都不得破坏这些基线。
>
> 面向对象：课程小组全体成员。每项都有编号，可据此开 Issue、分任务、写周报。

> 优先级（2026-10 小组决定）：**普通用户可用性 > 答辩差异化 > 长期产品化**。阶段顺序按此排列，功能取舍也以此为准。

## 一、四条硬性原则

1. **不新增强制外部依赖**。模型、浏览器扩展、桌面壳、第三方服务全部可选；没装任何东西的用户，功能依然完整。
2. **每一项 AI 能力都要有降级路径**。没有大模型时自动退回规则或词法方案，界面上明确标注当前模式，不报错、不空屏。
3. **先改结构，再加功能**。v2 会新增表和字段，必须先有迁移机制，禁止再用 `init_db.py --reset` 覆盖式改 schema。
4. **每个功能都必须有手机路径**。新页面先按 390px 宽度设计，再向上适配桌面；不允许“桌面能用、手机不可用”的功能合入。

## 二、体验优先：可验收的 UX 标准

“好用”不能靠感觉，以下每条都可在验收时逐项检查：

| 编号 | 标准 | 验收方式 |
| --- | --- | --- |
| UX-1 | 390px 宽度无横向滚动，主要操作单手可达 | 浏览器 390×844 截图检查 |
| UX-2 | 新用户 3 分钟内完成第一次导入 | 计时走查：登录 → 导入样例 → 看到会话 |
| UX-3 | 导入不超过 3 步，错误定位到具体会话/消息 | 用损坏样例走查，检查错误提示 |
| UX-4 | 任意空列表都有“下一步”动作，不出现死胡同 | 遍历课程/会话/批注/复习空态 |
| UX-5 | 未配置模型时，AI 相关入口不显示失败态 | 断开模型配置走查 |
| UX-6 | 所有写操作有明确反馈；可撤销的操作提供 5 秒撤销 | 删除批注、移除标签、复习打卡 |
| UX-7 | 首屏 JS < 100KB，首次请求 < 20 个，本地 LCP < 2.5s | 浏览器 Network / Lighthouse |
| UX-8 | 键盘可完成登录、搜索、批注；焦点始终可见 | Tab 键走查 |
| UX-9 | 错误页说明“为什么被拒”和“下一步怎么做” | 用 bob 访问 alice 课程检查 403 页 |
| UX-10 | 离线时明确显示离线状态，已缓存内容可读 | DevTools 切 Offline 检查 |

## 三、关于 Ollama：可选，不强制

普通用户不应为了使用系统而安装本地模型。模型是**可插拔能力**，不是运行前提。

| 模式 | 触发条件 | 可用能力 | 用户成本 |
| --- | --- | --- | --- |
| `mock`（默认） | 未配置任何 Key | 规则 Text2SQL、词法检索、手动标签 | 零安装，可离线 |
| `openai`（云端） | 部署方配置 OpenAI 兼容 Base URL + Key | 真实 Text2SQL、自动标签、向量检索 | 部署方一个 Key |
| `ollama`（本地） | 部署方或高级用户主动配置 `http://127.0.0.1:11434` | 同上，数据不出本机 | 安装 Ollama + 4–8 GB 模型 |

降级规则：

- 检索：向量不可用 → 退回 `pg_trgm + ILIKE`。
- Text2SQL：无模型 → 规则 mock，示例问题全部可跑。
- 自动标签 / 摘要：无模型 → 隐藏入口，不提示失败。
- 顶栏显示 `AI: 未配置 / 云端 / 本地` 徽章，用户始终知道当前模式。

Ollama 只出现在设置页的“高级 / 隐私模式”，不进入首次使用流程。浏览器内 embedding（transformers.js / WebLLM）作为实验项，不做主线。

## 四、任务清单

图例：价值 = 用户 / 答辩 / 实验；工作量 S ≤2 天，M 3–5 天，L 1–2 周。状态默认“待办”。

### 0. 前置：迁移机制（必须最先做）

| # | 事项 | 价值 | 量 | 状态 | 说明 |
| --- | --- | --- | --- | --- | --- |
| M1 | 编号 SQL 迁移 + `schema_migrations` 表（或 Alembic） | 答辩 | S | 完成 | 已实现 `meta.schema_migrations`、`scripts/migrate.py` 与 `sql/migrations/`；旧库升级与全新初始化两条路径均已验证 |

### 1. 采集端

| # | 事项 | 价值 | 量 | 状态 | 说明 |
| --- | --- | --- | --- | --- | --- |
| C1 | PWA Share Target：手机从任意 App 分享文本/链接进来 | 用户 | S | 完成 | `manifest.share_target` 指向 `POST /share`，解析结果暂存 `import_batches` 后跳转预览；`/share` 手动粘贴与最终 CSRF 保存已实现 |
| C2 | 剪贴板粘贴导入：自动识别角色与时间分隔 | 用户 | S | 完成 | `importers/plain_text_importer.py` 识别中英文角色前缀、`said/说`、括号时间、标题与课程/标签元信息；输出与 JSON 导入相同的 DTO |
| C3 | 每用户 Ingest Token：哈希存储、可撤销、限流 | 用户 + 答辩 | S/M | 完成 | 新增 `api_tokens` 表（第 14 张）；`/tokens` 管理页明文只显示一次，服务端仅存 SHA-256；`POST /api/ingest` 用 Bearer 令牌写入统一 JSON，按 `course_code` 自动归课，复用预览/确认/查重事务；每令牌每分钟 60 次固定窗口限流 |
| C4 | 浏览器扩展 MV3：一键抓取当前 ChatGPT / Qoder / DeepSeek 会话 | 用户 | M | 完成 | `extension/` 提供 MV3 弹窗：仅在点击发送时提取当前标签页（`activeTab` + 按需站点权限），预览后带 Bearer 令牌调用 `/api/ingest`；令牌只存 `chrome.storage.local`，无后台轮询；平台改版时回退到导出文件。`scripts/extension_smoke.mjs` 用真实浏览器验证提取、按钮清理、预览与发送契约 |
| C5 | 平台导出适配器：ChatGPT `conversations.json`、Qoder、DeepSeek Markdown | 用户 | M | 完成 | 新增 `importers/adapters.py` 字段映射层：ChatGPT `mapping` 沿 `current_node` 取主线并转换 UTC 时间；Qoder `sessions` / 别名字段归一；DeepSeek `### User/Assistant` 与英文元信息归一；统一复用现有 JSON / Markdown 解析与入库逻辑，三份样例随 `make_samples.py` 生成 |
| C6 | Bookmarklet 零安装抓取 | 用户 | S | 待办 | 受 CSP 限制，作为低配方案 |
| C7 | 收件箱 / 待整理区：未归类会话批量打标签、归课程 | 用户 | S/M | 完成 | `/inbox` 列出未归类会话，支持批量归入可编辑课程；仅允许移动本人、当前未归类的会话，避免误动共享课程数据 |

### 2. 离线与端侧体验

| # | 事项 | 价值 | 量 | 状态 | 说明 |
| --- | --- | --- | --- | --- | --- |
| O1 | 可安装 PWA + Service Worker 应用壳缓存 | 用户 | S/M | 完成 | `manifest.webmanifest`、根路径 `/sw.js`、离线页；只缓存静态资源，不缓存登录态 HTML |
| O2 | 离线阅读缓存（IndexedDB，会话/消息只读） | 用户 | M | 完成 | 会话页「保存到离线」显式快照，不缓存登录态 HTML；自包含 `/offline` 阅读页；提供“清除本机离线数据” |
| O3 | 离线批注草稿：本地暂存，联网提交 | 用户 | S | 完成 | 批注表单离线时写入 IndexedDB，`client_event_id` 唯一约束保证重放不重复；联网后自动提交 |
| O4 | 离线复习打卡 outbox + 幂等键 | 用户 | M | 完成 | `/api/reviews/offline` 缓存队列；打卡写入 outbox，`event_id` 重放只生效一次；联网自动同步 |
| O5 | 响应式移动端导航 / 表格 / 表单 | 用户 | S/M | 完成 | 固定顶栏 + 抽屉、底部标签栏、表格堆叠卡片、单列表单与触控尺寸 |
| O6 | Background Sync / Periodic Sync | 实验 | M | 待办 | 浏览器支持参差，不承诺全平台 |

### 3. 检索与 AI（全部可选模型）

| # | 事项 | 价值 | 量 | 状态 | 说明 |
| --- | --- | --- | --- | --- | --- |
| R1 | pgvector 混合检索：trgm + 向量 + RRF 融合 | 答辩 + 用户 | M | 完成 | 迁移 004 增加 `vector(1536)` 与 HNSW（cosine）；词法候选与向量候选各取 100，RRF(k=60) 融合；无 Key、调用失败、维度不符均退回词法，页面标注模式；搜索页显示向量覆盖率并可分批补齐（每次 64 条）。新增 7 项单元 + 5 项集成测试与 `verify.search.hybrid`；镜像切换后的排序规则问题由 `verify.schema.index_health` 守护 |
| R2 | 本地 embedding（Ollama / sentence-transformers / 浏览器端） | 实验 | M | 待办 | 可选能力，绝不作为依赖 |
| R3 | ParadeDB `pg_search` BM25 + 中文分词 | 实验 | M/L | 待办 | 需更换镜像，扩展可用性有风险 |
| R4 | zhparser / pg_jieba 分词 + tsvector | 答辩 | M | 待办 | 需自建镜像，比 trgm 更贴近课程内容 |
| R5 | 重排 rerank | 实验 | M | 待办 | 需额外模型，个人场景收益有限 |
| R6 | 自动标签 / 摘要建议（人工确认后入库） | 用户 | M | 完成 | `SUGGEST_MODE=auto` 时仅在配置 LLM Key 后显示按钮；发送前 40 条消息（每条 800 字、总计 6000 字），要求返回 `{summary, tags}`；服务端复用同名标签、最多 3 个标签、摘要 500 字，建议只回填表单。保存仍走归类事务：新标签 `ON CONFLICT` 去重，摘要复用该用户在此会话的“总结”批注更新，留空则删除。新增 7 项单元 + 4 项集成测试与 `verify.ai.organize`（标签幂等、摘要唯一更新、viewer 越权拦截）；无 Key 时按钮隐藏，手动整理不受影响 |
| R7 | 语义缓存 | — | S | 不推荐 | 个人场景收益低于维护成本 |

### 4. Text2SQL 硬化

| # | 事项 | 价值 | 量 | 状态 | 说明 |
| --- | --- | --- | --- | --- | --- |
| T1 | AST 校验（pglast / sqlglot）替换正则黑名单 | 答辩 | M | 完成 | `sqlglot` PostgreSQL 方言解析：单条 SELECT/WITH/UNION、拒绝嵌套 DML/`SELECT INTO`/`FOR UPDATE`/表函数/`pg_*` 等函数、15 个视图 + CTE 白名单；字符串与引用标识符先屏蔽再跑正则黑名单；新增 16 项单元测试，`verify.t2sql.guard` 覆盖嵌套 DML 与表函数 |
| T2 | EXPLAIN 预检：估算行数 / 成本超阈值直接拒绝 | 答辩 | S/M | 完成 | `preflight_query` 在只读事务内执行 `EXPLAIN (FORMAT JSON)`，不取数；默认代价上限 500000、估算行数上限 200000、预检超时 1s，可用 `TEXT2SQL_MAX_COST` / `TEXT2SQL_MAX_EST_ROWS` 调整；拒绝写 `rejected` 日志；页面展示预检代价与估算行数；新增 3 项集成测试与 `verify.t2s.preflight` |
| T3 | 结构化输出 / 工具调用 + few-shot | 答辩 | S/M | 待办 | 不同 provider 需适配 |
| T4 | Schema RAG：只向模型提供相关视图与列 | 答辩 | S/M | 待办 | 视图数量少，收益有限但便于讲解 |
| T5 | 评测集：30–50 题金标准，统计执行准确率 / 合法率 / 越权拦截率 | 答辩 | M | 完成 | `data/text2sql_eval.json` 含 30 题 + 10 条恶意 SQL + 2 条跨用户用例；`scripts/eval_text2sql.py` 输出准确率/合法率/拦截率并写 JSON。离线 mock 基线 17/30（56.7%），金标准与生成 SQL 合法率 100%，拦截 12/12；`verify.t2s.eval` 纳入验收 |
| T6 | 结果解释 + 图表建议 | 用户 | S | 待办 | 锦上添花，最后做 |

### 5. 数据库深度

| # | 事项 | 价值 | 量 | 状态 | 说明 |
| --- | --- | --- | --- | --- | --- |
| D1 | RLS + GUC，与现有 `security_barrier` 视图方案做对比实验 | 答辩 | M | 完成 | `rls_experiment` 在 `force_rollback` 事务内建随机 schema/角色与克隆表，对比视图、应用层 WHERE、RLS 三种读取方式；三种方式行集合一致，GUC 未设置均返回 0 行；视图产生 `Subquery Scan`，RLS 计划与 WHERE 相同；新增 2 项单元 + 2 项集成测试、`verify.db.rls` 与 `scripts/rls_experiment.py`；不修改 `app` 基表 |
| D2 | 审计历史表 + 通用触发器 | 答辩 | M | 完成 | 迁移 007 新增 `audit_log`（第 16 张表）与 `SECURITY DEFINER` 通用触发器，审计 `courses` / `course_members` / `users`；只记对象、动作、主键与变更列名，不记列值、不记密码列，消息/批注/复习表不审计；操作者经事务内 GUC `app.actor_user_id` 传入。`ls_app` 仅可 `SELECT`，清理函数最少保留 90 天；管理页新增筛选时间线与“清理过期记录”，新增 6 项集成测试、`verify.db.audit` 与截图 `14b-admin-audit.png` |
| D3 | 观测页：`pg_stat_statements` / `pg_stat_io` / 慢查询 / EXPLAIN JSON 可视化 | 答辩 | M | 完成 | 迁移 008 条件安装 `pg_stat_statements`（未预加载时页面降级提示）；`/admin/observability` 展示库大小、缓存命中率、连接数、事务、I/O、表访问/死元组、索引使用与慢查询；执行计划只接受 4 个白名单键，`EXPLAIN (FORMAT JSON)` 不执行查询；慢查询只看 SELECT/WITH/INSERT/UPDATE/DELETE，并按 `pg_stat_statements`/`EXPLAIN` 过滤；仅 admin 可见，不提供任意 SQL 输入，不读取正文；新增 5 项集成测试、`verify.db.observability`、桌面截图 `28-observability.png` 与手机截图 `28b-observability-mobile.png` |
| D4 | PG18：`uuidv7()`、虚拟生成列、`RETURNING OLD/NEW`、`NOT NULL NOT VALID`、AIO 基准 | 答辩 | S/M | 待办 | 独立分支与镜像，不改主线 |
| D5 | PG19 Beta：SQL/PGQ 属性图（知识点先修关系）、`ON CONFLICT DO SELECT` | 实验 | M | 待办 | 独立 compose profile，Beta 不稳定 |
| D6 | Apache AGE 图查询替代 | 实验 | M | 待办 | 扩展需编译 |
| D7 | messages 按月分区 | — | M | 不推荐 | 420 行数据下演示价值低于维护成本 |
| D8 | 物化视图 + 定时刷新统计 | 答辩 | S | 完成 | `app/services/stats_experiment.py` + `scripts/stats_experiment.py`：回滚事务内创建 `mv_course_stats`（唯一索引，可改 `CONCURRENTLY`），对比实时聚合与物化的中位耗时、计划形状，插入临时数据验证陈旧与刷新一致性；CI 执行并上传 JSON |

### 6. AI 接口

| # | 事项 | 价值 | 量 | 状态 | 说明 |
| --- | --- | --- | --- | --- | --- |
| A1 | 只读 MCP Server：search / get_conversation / list_due_reviews / add_annotation | 答辩 + 用户 | M | 完成 | 迁移 006 给 `api_tokens` 增加 `scopes`（默认 `{read,ingest}`，可选 `annotate`）；`app/mcp_server.py` 提供 stdio JSON-RPC `initialize` / `tools/list` / `tools/call`，只读工具始终可用，写工具仅在显式授权后出现；新增 `/api/mcp/*` 端点、令牌页作用域展示与 MCP 配置示例；新增 10 项单元 + 7 项集成测试 |
| A2 | CLI：`ls search` / `ls import` | 用户 | S | 完成 | `app/cli.py` 通过采集令牌调用 `GET /api/search` 与 `POST /api/ingest`；`--course-code` 覆盖课程、`--platform` 覆盖平台，支持 `LS_BASE_URL` / `LS_TOKEN` 与 `--json`；`pyproject.toml` 注册 `ls` 入口。新增 6 项单元 + 3 项集成测试，并完成真实服务冒烟 |

### 7. 工程与部署

| # | 事项 | 价值 | 量 | 状态 | 说明 |
| --- | --- | --- | --- | --- | --- |
| E1 | Dockerfile + compose 增加 app 服务（多阶段、非 root） | 答辩 | S/M | 完成 | 多阶段 `python:3.13-slim` 构建，运行用户 `app`（uid 999）；入口脚本按 `INIT_DB` / `SEED_DEMO` 自动迁移与空库播种，等 db healthy 后启动；`docker compose up -d --build` 一条命令起全栈 |
| E2 | CI：GitHub Actions + Postgres service + pytest + verify | 答辩 | S/M | 完成 | `.github/workflows/ci.yml`：`pgvector/pgvector:pg16` 服务、Python 3.13 缓存依赖、初始化/播种、334 项测试 + 42 项验收，上传 JUnit 与验收 JSON；离线模式与 concurrency 取消旧任务 |
| E3 | uv + lockfile、Ruff、pyright、pre-commit | 答辩 | S | 完成 | `pyproject.toml` 声明运行/开发依赖，`uv.lock` 锁定完整依赖图（当前 57 个包）；Ruff `E/F/I/UP/B/SIM/RUF/C4` 全绿，pyright basic 0 error；pre-commit 校验 YAML/TOML、Ruff 与 pyright；CI 用 `uv sync --frozen` 并检查锁文件 |
| E4 | Hypothesis 属性测试（解析器 / SQL 守卫） | 答辩 | S/M | 完成 | `tests/unit/test_properties.py` 随机生成 JSON/字节/文本与 SQL，验证解析器不抛异常、导入结果不变量、守卫拒绝集合与幂等、字面量掩码与行数上限；新增 28 项测试，并修复随机化暴露的非字符串字段、混合时间戳、非字典节点/作者与词法错误泄漏 |
| E5 | Playwright E2E 进 CI | 答辩 | M | 完成 | `playwright.config.mjs` + `tests/e2e/app.spec.mjs`：12 项带断言的用例（匿名跳转、学生/管理员/只读权限、会话与全局搜索、粘贴预览、Text2SQL、游标翻页、全量导出、四档复习、手机端溢出与 manifest），收集控制台错误、失败保留 trace；CI 安装 Chromium、启动 uvicorn 并上传 JUnit |
| E6 | 备份 / 恢复 + 用户全量导出（zip） | 用户 | S | 完成 | `scripts/backup.py` 封装 `pg_dump`/`pg_restore`（自定义格式、默认保留 10 份、恢复仅允许 `*_test` 库，`--force` 可覆盖）；`GET /exports/all.zip` 导出本人来源的会话 Markdown、批注/复习 CSV 与 manifest，按来源隔离，管理端不提供他人正文；新增 5 项单元 + 2 项集成 + 1 项 E2E |
| E7 | 登录限流、会话轮换、生产 Cookie 标志 | 答辩 | S | 完成 | `app/security.py` 滑动窗口同时限制 IP+账号与单 IP，超限 429 + `Retry-After`；登录清空旧会话并写入签发时间/nonce，超过 `SESSION_MAX_AGE` 失效；生产环境 Cookie `Secure`、HSTS 与安全响应头；`APP_ENV=production` 使用默认/过短 `SESSION_SECRET` 时拒绝启动；新增 6 项单元 + 1 项集成测试 |
| E8 | Gzip / Brotli + 静态缓存 + 字体子集 | 用户 | S | 完成 | GZip 中间件；`?v=` 版本化静态资源一年 immutable 缓存，无版本参数 5 分钟；页面 HTML `no-store`；未引入 Web 字体，使用系统字体栈 |
| E9 | 游标分页（keyset） | 用户 | S | 完成 | 会话列表与收件箱改为 `(最近消息时间, id)` 复合游标，首页仍用 `count(*) OVER()` 取总数并写入游标；后端保留 `page` 参数兼容旧调用，`after`/`before` 分别向后/向前翻页并反转排序；混合检索按 RRF 融合位置生成位置游标；无效游标回退首页；新增 2 项单元 + 1 项集成 + 1 项 E2E |
| E10 | Tauri 桌面壳（全局快捷键、剪贴板监听、托盘） | 用户 | M/L | 待办 | PWA 已覆盖大部分场景，最后评估 |
| E11 | Qoder Sites 静态展示页（报告 / 截图 / PPT） | 答辩 | S | 待办 | 只承载静态内容，不部署应用本体 |

### 8. 学习闭环（知识结构与复习）

| # | 事项 | 价值 | 量 | 状态 | 说明 |
| --- | --- | --- | --- | --- | --- |
| K1 | `review_logs` 复习事件表 + 幂等键 | 用户 + 答辩 | S/M | 完成 | `sql/migrations/001_add_review_logs.sql`；每次复习/掌握/重置写事件，`(user_id,event_id)` 唯一键支持离线重放；复习页展示近 5 周热力图 |
| K2 | 知识先修图：`tag_edges` + 递归 CTE + 可视化 | 用户 + 答辩 | M | 完成 | 迁移 005 新增 `tag_edges`（第 15 张表，同属主、禁自环、触发器递归 CTE 防环）；`/tags/graph` 按课程合并连通分量渲染 SVG，最长路径分层 + 贝塞尔曲线；`t2s.v_my_tag_edges` / `v_my_tag_prerequisites` 暴露给 Text2SQL，闭包用 `DISTINCT ON` 取最短深度；种子 32 条边，新增 2 项布局单元 + 5 项集成测试与 `verify.graph.prerequisites` |
| K3 | FSRS / 可插拔复习算法 | 实验 | M | 完成 | `services/fsrs.py` 实现 FSRS-6 风格长期调度（稳定性/难度/遗忘次数），复习页四档评分，迁移 009 保存状态并在 `review_logs` 记录 rating/algorithm；`REVIEW_ALGORITHM=simple` 保留间隔翻倍回退；新增 8 项单元 + 1 项集成 + 1 项 E2E |

### 9. 体验专项

| # | 事项 | 价值 | 量 | 状态 | 说明 |
| --- | --- | --- | --- | --- | --- |
| U1 | 移动端信息架构：底部/抽屉导航、列表卡片化、阅读模式 | 用户 | S/M | 完成 | 390px 优先；底部标签栏 + 抽屉、表格堆叠、`.msg-body` 阅读排版 |
| U2 | 空状态与首次引导：示例数据一键体验、空列表给出下一步 | 用户 | S | 完成 | 登录页演示账号一键进入；空账号概览展示建课 → 导入 → 收件箱三步引导；各空列表均给出下一步入口 |
| U3 | 导入向导：拖拽上传、步骤条、逐条错误定位、结果摘要 | 用户 | S/M | 完成 | 上传/粘贴双 Tab + 拖放区、三步步骤条（选择 → 预览 → 结果）、预览页逐条错误、结果页新增/跳过/冲突计数与下一步入口 |
| U4 | 全局搜索与最近访问：顶部搜索、`/` 或 `Ctrl+K`、搜索历史 | 用户 | S/M | 完成 | `/search` 聚合会话、批注、课程、标签；顶部搜索框 + 快捷键弹窗；本地记录最近搜索与最近访问 |
| U5 | 反馈与可撤销：骨架屏、Toast、删除 5 秒撤销、失败重试 | 用户 | S/M | 完成 | 可关闭/可播报提示；批注、复习项、标签删除提供 5 秒撤销与离页兜底提交；SSR 页面不需要骨架屏 |
| U6 | 可访问性与性能预算：键盘焦点、对比度、JS < 100KB、LCP < 2.5s | 用户 + 答辩 | S | 完成 | 焦点样式、`aria-current`/`aria-live`、减少动效；首屏 JS 8KB、本地移动端 LCP 268ms、请求数 2 |
| U7 | 错误页与恢复路径：说明被拒原因、提供申请权限或返回入口 | 用户 | S | 完成 | 403/404/400/409/500 分状态提示原因与下一步；未知路由与 405 统一 HTML 错误页；API 保持 JSON |
| U8 | 离线状态提示与缓存管理 | 用户 | S | 完成 | 全站离线横幅 + 恢复联网提示；离线页显示本机保存数量与清除入口 |

## 五、明确不做

- 把 PostgreSQL 搬进浏览器（WASM SQLite）：丢失 SQL 课程价值与事务一致性。
- CRDT / 本地优先多端同步：单用户场景无收益，复杂度极高。
- 边缘部署、多区域部署：没有用户规模支撑。
- Redis / Celery / 微服务拆分：过度设计。
- Electron：有 PWA 与 Tauri 即可。
- messages 分区表、语义缓存：收益低于维护成本。
- 引入 React / Vue 重写前端：当前 SSR + 原生 JS 已满足交互需求，重写只增加包体与维护成本。
- 每用户自带 LLM Key：密钥加密、轮换、审计都是负担；部署级配置 + 可选 Ollama 端点足够。
- 为动画而动画的重型动效：优先保证首屏与低端手机流畅。

## 六、阶段计划（用户可用性优先）

| 阶段 | 内容 | 用户可感知的结果 |
| --- | --- | --- |
| Phase 1「手机能顺手用」 | M1 + U1 U2 U3 U5 U6 U7 + O1 O5 + C1 C2 C7 + E8 | 手机上可安装、可分享导入、导入有向导、列表不空转 |
| Phase 2「碎片时间能学」 | O2 O3 O4 + U8 + K1 + C3 C4 C5 + U4 | 断网可读可复习、扩展与平台导出可采集、有全局搜索 |
| Phase 3「找得到、问得准」 | R1 + R6 + T1 T2 T5 | 检索相关度提升、Text2SQL 有评测数据 |
| Phase 4「学得深、接得广」 | K2 + A1 A2 + D1 D2 D3 | 知识先修图、只读 MCP、数据库观测 |
| Phase 5「工程化与前沿」 | E1–E5 E9 E11 + D4 D5 D8 + K3 + E10 | 可部署、可回归、有实验性亮点 |

**Phase 1 进度**：已完成全部项（M1、O1、O5、U1、U2、U3、U5、U6、U7、C1、C2、C7、E8）。UX-1 至 UX-9 已逐项走查；UX-10 已由 U8 的离线状态横幅与缓存管理覆盖。

**Phase 2 进度**：K1 复习事件流水、O2 离线阅读、O3 离线批注草稿、O4 复习 outbox、U4 全局搜索、U8 离线状态提示、C3 采集令牌 / Bearer 导入接口、C5 平台导出适配器与 C4 MV3 扩展已完成；Phase 2 验收与文档已收尾。

**Phase 3 进度**：T1 AST 校验、T2 EXPLAIN 预检、T5 金标准评测、R1 pgvector 混合检索、R6 自动标签/摘要建议已完成，Phase 3 收尾。

**Phase 4 进度**：K2 知识先修图、A1 只读 MCP、A2 CLI、D1 迁移体系、D2 审计历史、D3 数据库观测均已完成，Phase 4 收尾。

**Phase 5 进度**：E1 容器化、E2 GitHub Actions CI、E3 工程工具链、E4 属性测试、E5 Playwright E2E、E6 备份/恢复与全量导出、D8 物化视图统计实验、E9 游标分页、E7 安全加固、K3 FSRS 复习调度已完成；下一步 E11 静态展示页。

**Phase 4 进度**：K2 知识先修图、A1 MCP 工具接口、A2 命令行、D1 RLS 对比实验、D2 审计历史与 D3 数据库观测页已完成（16 张表、15 个视图、9 个触发器；334 项测试、42 项验收）。Phase 4 收尾。

每个阶段的统一验收：

```bash
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe scripts/verify.py --json data/verification/report.json
```

要求：测试全绿、验收全 PASS、UX-1 至 UX-10 逐项走查、不新增任何强制外部依赖。

## 七、协作约定

- 一项任务一个分支：`feat/C1-share-target`、`docs/roadmap`，合并前跑全量测试。
- 动 schema 必须配迁移文件，迁移必须可重复执行（`IF NOT EXISTS` 或版本表判断）。
- 新增 AI 能力必须同时提交“无模型降级路径”和对应测试。
- 导入格式变更只改解析层，`services/import_service.py` 的入库事务保持单一入口。
- 不把 `.env`、真实聊天导出、个人账号信息提交到仓库；演示数据继续使用模拟内容。
- 报告中“真实数据 vs 模拟数据”的声明在所有新增功能里保持不变。
