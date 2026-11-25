# 项目交接：Learning Space（下一个对话先读这里）

> 更新时间：2026-11-24
> 当前基线：`v0.4.0`（tag `a4dd7d5`，Windows NSIS / macOS DMG 已发布）
> 远程：`https://github.com/ycl-berserk/2026Database`
> 工作区：当前分支 `feat/K4-mastery-tutor`；feat1 已提交 `ebd6ce9`，K5 改动见本次提交；`main` 与 `origin/main` 仍同步。
>
> 给下一个对话的建议顺序：**本文件 → `README.md` 的“答辩重点” → `docs/report.md` → `docs/demo-script.md` → `docs/design.md`**。涉及具体任务时再读对应代码。

---

## 1. 三十秒认识这个项目

这是一个《数据库系统概论》课程大作业，选题 2：**围绕与大模型的聊天记录构建个人学习空间**。

- 技术栈：FastAPI + PostgreSQL 16 + pgvector + Jinja2 服务端渲染；桌面版是 Tauri v2 + PyInstaller sidecar + 内置 PostgreSQL。
- 演示数据全部是模拟的课程问答，**不含真实个人信息**。
- 管理员只查看用户、聚合统计、结构审计元数据与数据库观测指标，**不能读取他人消息正文、批注或复习内容**；桌面个人模式下管理员同时是学习者。
- 课程汇报是 **8 分钟、数据库视角**：重点讲表结构、SQL、索引、权限、事务、触发器、Text2SQL 与 pgvector，不要把主线讲成普通软件工程。
- 报告、PPT、演示脚本都已存在：
  - `docs/report.md`：完整课程报告
  - `docs/汇报.pptx`：9 页汇报 PPT
  - `docs/demo-script.md`：8 分钟逐段口播 + 现场操作
  - `docs/sql-showcase.md`：23 条 showcase SQL

---

## 2. 当前验收基线（不要不小心破坏）

| 指标 | 当前值 |
| --- | --- |
| 表 | 20 张（15 业务 + `audit_log` + `user_onboarding` + `knowledge_mastery` + `quiz_attempts` + `ai_settings`） |
| `t2s` 只读视图 | 18 个，均使用 `security_barrier` |
| 触发器 | 13 个 |
| 索引 | 65 个 |
| 知识先修边 | 32 条，6 个连通分量 |
| pytest | 422 个（247 单元 + 175 集成） |
| Playwright E2E | 12 个带断言的用例 |
| `scripts/verify.py` | 43/43 PASS |
| 演示规模 | 5 门课、30 个会话、420 条消息、66 条批注、59 个标签、5 个导入批次 |

最近一次完整门禁（提交 `6bf030b` 前）：

```bash
.venv/Scripts/python.exe -m ruff check .        # 全绿
.venv/Scripts/python.exe -m pyright app         # 0 errors
.venv/Scripts/python.exe -m pytest -q           # 422 passed
.venv/Scripts/python.exe scripts/verify.py      # 43/43 PASS
```

注：`docs/roadmap.md` 的测试数字与 E2E 数量已于 2026-11-24 对齐到 422 / 12，R1 也补记了三条向量工程机制；继续维护时以本文件第 2 节和 `scripts/verify.py` 输出为准。

---

## 3. 代码地图

```text
app/
  main.py                 FastAPI 应用装配
  config.py               settings（.env / 环境变量）
  db.py                   主库与 t2s 连接池
  auth.py / permissions.py 登录、系统角色、课程角色
  routers/                页面与 JSON API
  services/               业务与数据库逻辑（conversation、import、search、embedding、tutor、verify 等）
  templates/              Jinja2 SSR 页面
  desktop.py              桌面模式入口（initdb / migrate / serve / smoke）
  cli.py                  ls search / ls import
  mcp_server.py           stdio JSON-RPC MCP Server
sql/
  schema.sql              基线 schema
  migrations/             001–014 编号迁移
  views/                  t2s 权限视图
scripts/
  init_db.py              初始化演示库与 _test 库
  migrate.py              应用迁移
  seed_demo.py            播种模拟数据
  verify.py               43 项端到端验收（写入整体回滚）
  evaluate_retrieval.py   Recall@K / MRR 检索评测
  eval_text2sql.py        Text2SQL 金标准评测
  build_ppt.py            生成 docs/汇报.pptx
desktop/                  Tauri v2 桌面壳
packaging/                PyInstaller sidecar 构建与资源打包
tests/unit|integration|e2e/
```

数据库角色分层：

- `ls_app`：只拥有 `app` schema 的 DML，无 DDL。
- `t2s_reader`：只能 `SELECT` `t2s` 视图，只读事务 + 语句超时。
- 应用层权限检查 → SQL 作用域过滤 → 数据库约束/触发器，三层防护。

---

## 4. 已经完成什么

### 4.1 数据与采集

- JSON / Markdown / 裸文本导入，三步向导、持久化预览、查重、单事务入库、失败回滚。
- ChatGPT / Qoder / DeepSeek 原生导出适配；Codex CLI / opencode / Qoder CLI 本地会话直读。
- PWA Share Target、采集令牌 + Bearer `/api/ingest`、MV3 浏览器扩展。
- 重复导入按唯一键与内容指纹跳过，不重复写正文。

### 4.2 学习闭环

- 课程、来源、标签、收件箱、批量归类。
- 批注四类（笔记/疑问/纠错/总结）、复习队列、FSRS 风格四档调度、5 周热力图。
- 知识先修图：`tag_edges` DAG，递归 CTE 闭包、最短路径、触发器防环、SVG 可视化。
- AI 学习（K4）：`conversations.mode` 区分理论 / 出题两类对话；`knowledge_mastery` 保存用户 × 课程 × 知识点的 1–10 分掌握度；`quiz_attempts` 保存题目、参考答案与评分，评分触发器按 0.6×旧分 + 0.4×本次更新掌握度。应用内会话写入同一对 `conversations` / `messages` 基表，来源平台为 `learning_space`；配置向量模型后，上下文同时使用语义召回与词法兜底。上下文只取该课程**已导入**的对话（`platform <> 'learning_space'`），AI 学习会话永远不会成为自己的上下文，避免自引用与反馈循环；新建理论会话时提示实际载入的片段数。
- AI 设置（K5）：`/settings/ai` 管理端图形化配置对话模型 / 向量模型；`ai_settings` 单例表保存 Base URL、模型、启用开关与 `pgcrypto` 加密后的 Key；页面只显示末四位掩码，支持测试连接、保存与清除。环境变量仍作为默认值：支持 `LLM_API_KEY`、`DEEPSEEK_API_KEY`、`OPENAI_API_KEY`，数据库 Key 优先。
- 单会话 Markdown、统计 CSV、本人全量 zip 导出。

### 4.3 检索与 AI

- `pg_trgm` + `ILIKE` 中文子串检索，`%`/`_`/`\` 转义。
- pgvector `vector(1536)` + HNSW cosine；词法与向量候选 RRF(k=60) 融合；无 Key / 失败 / 维度不符自动退回词法。
- **本次新增的三个向量数据库工程点**（README 已单列“答辩重点”）：
  1. **过滤感知 ANN**：pgvector 0.8+ 启用 `hnsw.iterative_scan = relaxed_order`，消息级 Top-N 后按会话折叠；低版本自动回退。代码在 `app/services/conversation_service.py`，`HYBRID_EF_SEARCH` 可调。
  2. **Recall@K / MRR 评测**：`data/retrieval_eval.json` 16 条金标准片段；`scripts/evaluate_retrieval.py` 对比词法与混合检索；无 Key 时明确跳过混合路径。当前演示库词法基线 16/16、Recall@10=100%、MRR=1.000。
  3. **内容哈希复用**：SHA-256 规范化正文去重 + 512 条进程内 LRU；回填只处理空向量或模型不一致的消息。代码在 `app/services/embedding_service.py`。
- 自动标签/摘要建议：有 Key 才显示按钮，结果只回填表单，人工确认后入库。
- Text2SQL：sqlglot AST 守卫 → 关键字黑名单 → `EXPLAIN` 预检 → `t2s_reader` 只读执行；有本地规则 mock，30 题金标准评测。

### 4.4 数据库与运维

- 编号迁移 + `meta.schema_migrations`。
- RLS vs 应用层 WHERE vs `security_barrier` 视图的隔离实验（临时事务，全部回滚）。
- 审计触发器只记录对象/主键/变更列名，不记值、不记密码列。
- 数据库观测页：大小、缓存命中、连接、`pg_stat_io`、慢查询、白名单 `EXPLAIN JSON`。
- 备份/恢复、登录限流、会话轮换、生产安全头、GZip、游标分页。
- Docker Compose 一条命令起全栈；GitHub Actions 在 `pgvector/pgvector:pg16` 上跑测试 + 验收。
- Windows 桌面个人版已本地构建并安装：内置 PostgreSQL + sidecar + 局域网手机访问；0.4.0 已发布 Windows NSIS 37.5 MiB / macOS DMG 57.1 MiB，macOS 运行时补齐 `pgcrypto`/OpenSSL；0.3.0 的 Text2SQL 500 已修复（PyInstaller 需显式收集 `sqlglot.dialects`）。macOS DMG 由 CI 构建，未在本机人工验收。

### 4.5 最近两次关键提交
| 提交 | 内容 |
| --- | --- |
| `feat/K4-mastery-tutor`（本次提交） | K5：图形化 AI 设置、pgcrypto 密钥加密与测试连接 |
| `feat/K4-mastery-tutor`（已提交 `ebd6ce9`） | feat1：两类对话视图、知识点掌握度与 AI 学习 |
| `6bf030b` | 过滤感知 ANN、Recall@K/MRR 评测、内容哈希复用、README 答辩重点 |
| `d3d29c8` | 修复桌面版 Text2SQL 500：PyInstaller 收集 `sqlglot.dialects` |

再往前：`648b778` 重写 8 分钟数据库版演示脚本与 PPT；`8c4a322` 用延迟约束触发器保证每门课恰好一个 owner；`1e10b01` / `9d3d3c6` 发布 0.3.0 桌面产物。

---

## 5. 怎么跑起来

### 5.1 本机开发（Windows / Git Bash）

```bash
cd /d/projects/learning-space

# 1. 启动数据库（Docker Desktop 必须先运行；当前测试结束后已停止）
docker compose up -d db

# 2. 首次或需要重建演示库时才执行（会删库，谨慎）
.venv/Scripts/python.exe scripts/init_db.py --reset
.venv/Scripts/python.exe scripts/seed_demo.py --if-empty

# 3. 跑测试与验收
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m pytest -q
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verify.py

# 4. 启动 Web
.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
```

不要执行 `init_db.py --reset` 除非明确想清空演示库/测试库。日常只需要 `docker compose up -d db`。

### 5.2 检索评测

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/evaluate_retrieval.py --user alice --k 10
```

- 未配置 `EMBEDDING_API_KEY` 时混合检索会跳过，这是预期降级。
- 配置后才会同时输出词法与混合的 Recall@K / MRR。
- 报告写入 `data/verification/retrieval_eval.json`（通常被 gitignore）。

### 5.3 Text2SQL 评测

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/eval_text2sql.py --provider mock
```

当前离线 mock 基线：执行准确率 17/30（56.7%）、合法率 100%、越权拦截 12/12。

### 5.4 桌面版（只有改到桌面运行时时才需要）

```bash
cd desktop
npm run build
```

- 常规 Web 改动**不需要**重打桌面包。
- 改了 `app/` 或 sidecar 依赖后，需要重新构建 PyInstaller sidecar + NSIS，再跑 `--smoke`。
- PyInstaller spec 位于 `packaging/sidecar/ls-server.spec`；注意 `collect_submodules("sqlglot.dialects")` 不能删。
- 桌面数据目录：Windows `%LOCALAPPDATA%\com.learningspace.desktop`；安装目录本机为 `D:\Learning Space`。
- `.env` 在桌面数据目录中真实存在，**永远不要提交或打印**。

---

## 6. 下一个对话可以做什么（按建议优先级）

### P0：答辩材料与基线一致性

1. **更新 `docs/roadmap.md` 的旧数字与状态**：已于 2026-11-24 完成（334 → 422 测试、11 → 12 项 E2E、R1 补记三条向量机制）。`docs/report.md` 与 `docs/汇报.pptx` 按用户要求暂未更新，答辩前如需一致再单独同步。
2. **检查报告/PPT 与代码计数一致**：20 表、18 视图、13 触发器、65 索引、422 测试、43 验收；当前 `docs/report.md` 仍是旧基线，重新生成 PPT 前先决定是否同步。
3. **按 `docs/demo-script.md` 完整彩排 8 分钟版本**，重点确认两个现场操作：alice 403 / bob 2 条视图过滤；重复导入预览 6/84/new 0/dup 6。

### P1：直接增强现有差异化

1. **补充自然语言改写版检索评测**：当前 16 条是“金标准片段”，词法基线 100%；可以再维护一组同义改写问题（例如不包含原文连续子串），在配置 `EMBEDDING_API_KEY` 后记录混合检索相对词法的 Recall@K / MRR。注意：现有词法检索是整串 `ILIKE`，改写句如果没有连续原文片段会命中 0，这是预期行为，不要误判为 bug。
2. **E11 静态展示页**：把报告、截图、PPT 放到 Qoder Sites，答辩时手机/浏览器可直接看；只承载静态内容，不部署应用本体。
3. **macOS 桌面验收**：CI 已有 arm64 DMG，但本机未验证；需要在 Mac 上跑安装、`--smoke`、局域网手机访问。签名/公证是加分项，不是课程必需。

### P2：数据库/检索可选实验（先开独立分支）

- **R4 中文分词**：zhparser / pg_jieba + `tsvector`，和现有 `pg_trgm` 做召回对比；需要自建镜像，保持可回退。
- **R5 重排**：对 RRF 候选做 cross-encoder / LLM rerank；个人规模收益有限，先做离线评测再决定。
- **R2 本地 embedding**：Ollama 或 sentence-transformers，作为隐私模式，不进入默认安装流程。
- **D4 PG18 实验**：`uuidv7()`、虚拟生成列、`RETURNING OLD/NEW`、`NOT NULL NOT VALID`、AIO 基准；独立镜像与分支，不改主线。
- **D5 PG19 Beta / D6 Apache AGE**：只作为实验展示，Beta 不稳定，谨慎投入。

### P3：低优先级

- C6 Bookmarklet 零安装抓取（受 CSP 限制）。
- O6 Background Sync / Periodic Sync（浏览器支持参差）。
- T3 结构化输出、T4 Schema RAG、T6 结果解释/图表建议。
- 桌面代码签名与自动更新。

### 明确不建议做

- 为了“用上向量数据库”而引入 Qdrant / Milvus 独立服务；420 条消息、1536 维，pgvector 足够，独立服务只会增加同步与权限复杂度。
- 文档切块、量化压缩、独立向量微服务；当前规模没有收益。
- 给 `messages` 按月分区、语义缓存、Redis/Celery/微服务拆分。
- 在基表上直接启用 RLS 替代现有视图方案；RLS 只保留为对比实验。
- React/Vue 重写 SSR 前端。
- 把管理员做成能看正文的“超级用户”。

---

## 7. 工作约定（重要）

1. **演示数据必须是模拟数据**，不能提交真实聊天记录、真实姓名、Token、Key。
2. **`.env` 永远不进入仓库**；不要在回复里打印密钥或会话 Cookie。
3. **不要主动 commit / push**，除非用户明确说“提交 / 推送”。
4. 一项功能一个分支；动 schema 必须附带可重复执行的编号迁移。
5. 每个 AI 能力必须有降级路径：无模型 → 词法 / mock / 隐藏入口，页面标注当前模式。
6. 新页面先按 390px 设计，保证手机可用。
7. 合入前必须全绿：
   ```bash
   .venv/Scripts/python.exe -m ruff check .
   .venv/Scripts/python.exe -m pyright app
   .venv/Scripts/python.exe -m pytest -q
   .venv/Scripts/python.exe scripts/verify.py
   ```
8. 改桌面运行时才重建 sidecar/安装包；macOS 本机无法验证时要在交付说明里写明。
9. 报告主线保持数据库视角；README 的“答辩重点：从向量数据库借鉴的三个工程思路”是组员汇报检索部分的指定入口。

---

## 8. 如果用户下一句说……

- **“继续做检索”**：先读 `app/services/conversation_service.py`、`embedding_service.py`、`scripts/evaluate_retrieval.py`；确认 pgvector 版本与 `EMBEDDING_API_KEY` 是否配置；不要无评测就调参。
- **“继续做 Text2SQL”**：先读 `app/services/text2sql_service.py`、`app/services/text2sql_eval_service.py`、`data/text2sql_eval.json`；跑 mock 评测再改。
- **“继续做 AI 学习 / 掌握度”**：先读 `app/services/tutor_service.py`、`app/routers/tutor.py`、迁移 013 与 `sql/views.sql` 末尾三个 `t2s` 视图；离线用 `TUTOR_MODE=off` 跑 `tests/integration/test_tutor.py`，不要在没有评测的情况下改评分权重。
- **“继续做 AI 设置 / 模型接入”**：先读 `app/services/ai_settings_service.py`、`app/routers/ai_settings.py`、迁移 014；密钥必须走 `pgcrypto`，页面只能返回掩码，测试连接不得记录 Key。
- **“继续做桌面版”**：先读 `docs/desktop-packaging.md`、`packaging/sidecar/ls-server.spec`、`app/desktop.py`；改 `app/` 后必须重建 sidecar 并 `--smoke`。
- **“准备答辩”**：直接基于 `docs/demo-script.md` 彩排；PPT 用 `scripts/build_ppt.py` 重新生成；所有数字以本文件第 2 节和 `scripts/verify.py` 输出为准。
- **“还能加什么”**：先回答“答辩收益/维护成本”，再从上文 P1/P2 里选；不要为了技术名词引入独立向量库或微服务。