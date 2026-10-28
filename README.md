# 个人学习空间（Learning Space）

[![CI](https://github.com/ycl-berserk/2026Database/actions/workflows/ci.yml/badge.svg)](https://github.com/ycl-berserk/2026Database/actions/workflows/ci.yml)

《数据库系统概论》大作业 · 选题 2：围绕“与大模型的聊天记录”构建一个可查阅、可批注、可复习的课程资料库。

系统把 Qoder / ChatGPT / DeepSeek 等平台的问答导出文件解析为统一结构，按课程、来源、标签整理；提供中文检索、全局搜索、会话上下文、批注、间隔复习、统计导出，并额外实现一个受只读权限视图保护的 **Text2SQL** 页面。采集侧支持平台原生导出适配、采集令牌 + Bearer 导入接口，以及一个“点击才上传”的 MV3 浏览器扩展；另提供命令行与 MCP 工具接口，方便脚本和 AI 客户端复用同一套权限过滤。

> 演示数据全部为围绕课程知识点生成的模拟聊天记录，不含任何真实个人信息。系统管理员只查看用户、聚合统计、结构变更审计（对象/动作/变更列名，不含内容）与数据库观测指标（归一化 SQL、对象名与代价，不含参数值），不读取他人消息正文或批注。

## 功能一览

| 模块 | 能力 |
| --- | --- |
| 记录导入 | JSON / Markdown 上传、裸文本粘贴、三步导入向导（上传/粘贴 → 预览勾选 → 结果摘要）、解析校验、持久化预览、查重、课程映射、单事务入库、失败回滚 |
| 快速采集 | PWA Share Target、裸文本粘贴、ChatGPT `conversations.json` / Qoder 会话 / DeepSeek Markdown 原生导出适配；采集令牌 + Bearer `POST /api/ingest`；MV3 扩展仅在点击发送时读取当前会话 |
| 收件箱 | 未归类会话队列、批量归类到课程、分享落地页与高亮定位 |
| 课程与标签 | 课程 CRUD、成员共享（owner / editor / viewer）、标签 CRUD、会话归类与打标签 |
| 知识先修图 | 用 `tag_edges` 维护标签之间的先修关系（有向无环图），数据库触发器校验同属主与无环；按课程分组渲染 SVG，支持递归查询全部先修链、查看后继与相关会话 |
| 整理建议 | 配置大模型后可一键生成“会话摘要 + 标签建议”，只填入整理表单；用户检查并点击保存后才写入数据库。新标签按用户去重，摘要复用已有“总结”批注更新而非重复插入；无 Key 时按钮隐藏，表单仍可手动填写 |
| 检索阅读 | 全局搜索（`/` 或 `Ctrl/Cmd+K`）、课程 / 标签 / 日期 / 关键词筛选、分页、消息上下文、关键词高亮、命中片段；配置向量模型后启用 pgvector + RRF 混合检索，否则自动退回词法检索 |
| 批注与复习 | 笔记 / 疑问 / 纠错 / 总结四类批注、解决状态、加入复习队列、间隔 1→2→4→8… 天、逾期提示、掌握/重置、近 5 周复习热力图 |
| 统计与导出 | 概览、课程排名、月度活跃补零、标签 TopN、批注解决率、复习状态、重复内容检测；统计 CSV、单会话 Markdown |
| 用户与权限 | Session 登录、PBKDF2 密码哈希、CSRF、系统角色 + 课程角色、应用层与数据库层双重防护 |
| 数据库隔离实验 | 在只读演示库的临时事务中对比 `security_barrier` 视图、应用层 WHERE 与 RLS；结果与实验对象全部回滚 |
| 数据库观测 | 管理员页展示库大小、缓存命中率、连接与事务、`pg_stat_io`、表访问/死元组、索引使用、慢查询与白名单 `EXPLAIN (FORMAT JSON)`；不提供任意 SQL 输入框 |
| Text2SQL | 自然语言→SQL→AST 守卫→EXPLAIN 预检→只读执行→表格展示、解释、审计历史；无 API Key 时使用本地规则 mock |
| 命令行 | `ls search` / `ls import`：用采集令牌调用 `/api/search` 与 `/api/ingest`，支持环境变量、课程覆盖与 `--json` 输出 |
| MCP 接口 | stdio JSON-RPC 服务：`search`、`get_conversation`、`list_due_reviews` 只读；令牌显式授予 `annotate` 后才开放 `add_annotation` |
| 管理 | 用户启停、系统聚合统计、数据库观测；审计时间线只记录课程/成员/账号的结构变更（对象、动作、主键、变更列名），不读取正文；支持按 90 天保留期清理 |
| 移动端与安装 | 390px 优先的底部标签栏 / 抽屉导航、列表卡片化、专注阅读模式；PWA manifest + Service Worker 应用壳缓存、离线页、桌面安装、分享快捷方式 |
| 离线学习 | 会话快照、离线阅读与搜索、离线批注草稿、复习打卡 outbox，恢复联网后按事件 ID 幂等同步 |
| 体验与可访问性 | 演示账号一键登录、空账号三步引导、删除类操作 5 秒撤销、可关闭且可播报的提示、错误页原因与恢复路径、键盘焦点可见、`prefers-reduced-motion` |
| 性能 | GZip 压缩、静态资源版本化与长缓存、首屏 JS < 10KB、移动端 LCP < 0.5s（本地实测） |

> PWA 安装与 Service Worker 只在 `localhost` 或 HTTPS 下启用；通过局域网 HTTP 访问手机时仍可使用完整响应式界面，但不会缓存静态资源。

## 技术栈

- Python 3.12+ / FastAPI / Jinja2 服务端渲染
- PostgreSQL 16 + pgvector（Docker Compose，`pgvector/pgvector:pg16`，宿主端口 5433）
- psycopg 3 + psycopg_pool，手写参数化 SQL，不使用 ORM
- uv + `uv.lock` 锁定依赖；Ruff、pyright、pre-commit 与 Hypothesis 属性测试覆盖静态检查与随机化边界
- `pg_trgm` GIN 索引支持中文子串检索；`vector(1536)` + HNSW 支持可选的语义召回，RRF 融合两路排名
- GZip 中间件 + 带版本参数的静态资源长缓存
- Chrome MV3 浏览器扩展（原生 JS，点击发送、按需站点权限）
- Playwright 用于带断言的页面 E2E、截图、扩展冒烟与演示录屏

## 快速开始

前置条件：Docker Desktop 已启动；Python 3.12+；Node.js 20+（仅 E2E、截图与录屏需要）。

### 方式 A：Docker 一条命令（推荐演示）

```bash
# 构建并启动数据库 + 网站；首次会拉取镜像并构建
docker compose up -d --build

# 打开 http://127.0.0.1:8000
# 容器启动时自动建库、应用迁移、创建角色；空库时写入演示数据
docker compose logs -f app
```

`docker-compose.yml` 的 `app` 服务以非 root 用户 `app` 运行，入口脚本依次执行 `init_db.py --skip-test` 与 `seed_demo.py --if-empty`。需要接入真实大模型时，在启动前设置 `LLM_API_KEY` 等变量（Compose 会透传）；不想自动初始化或播种时设置 `INIT_DB=0` / `SEED_DEMO=0`。详细信息见 [Dockerfile](Dockerfile) 与 [scripts/docker_entrypoint.sh](scripts/docker_entrypoint.sh)。

### 方式 B：本机 Python 开发

```bash
# 1. 启动数据库（首次会拉取 pgvector/pgvector:pg16）
docker compose up -d db

# 2. 创建虚拟环境并安装依赖（二选一）
uv sync                                        # 推荐：按 uv.lock 安装运行 + 开发依赖
# 或：py -3.13 -m venv .venv && .venv/Scripts/python.exe -m pip install -r requirements-dev.txt

# 3. 初始化演示库 + 测试库（--reset 会删除同名库后重建）
uv run python scripts/init_db.py --reset

# 4. 写入演示数据（3 个账号、5 门课程、30 个会话、420 条消息）
uv run python scripts/seed_demo.py

# 5. 启动网站
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

使用 pip 时把 `uv run python` 换成 `.venv/Scripts/python.exe` 即可。

> `docker-compose.yml` 为 db 服务传入 `shared_preload_libraries=pg_stat_statements`，供管理员观测页读取慢查询；扩展本身由迁移 008 在演示库与测试库中条件创建。若数据库未预加载该库，页面会显示降级提示，其余指标不受影响。

> 如果本地已有旧 `postgres:16-alpine` 数据卷：不同基础镜像的 libc 排序规则不同，切换镜像后需执行 `REINDEX DATABASE learning_space;`（或 `init_db.py --reset` 重建演示库），否则唯一索引可能查不到部分中英文混合标签。`verify.py` 的“索引与数据一致性”检查会提示该问题。

打开 <http://127.0.0.1:8000>，使用下方演示账号登录。

### 演示账号

| 账号 | 密码 | 身份 |
| --- | --- | --- |
| `alice` | `Alice123!` | 学生；5 门课程 owner；拥有 3 个平台来源 |
| `bob` | `Bob123!` | 学生；操作系统 editor、数据库 viewer |
| `admin` | `Admin123!` | 管理员；只访问用户管理、聚合统计、审计与数据库观测 |

这些密码仅用于本地演示；数据库只保存 PBKDF2-HMAC-SHA256（210,000 次迭代）哈希。

### 命令行客户端

在 `/tokens` 创建一次性显示的采集令牌后：

```bash
# 可选：安装入口命令 ls；不安装也可以用 python -m app.cli
.venv/Scripts/python.exe -m pip install -e .

export LS_BASE_URL=http://127.0.0.1:8000
export LS_TOKEN=ls_xxxxxxxx

ls search "隔离级别" --limit 5
ls search "隔离级别" --json
ls import data/samples/qoder_db.json --course-code DB
ls import data/samples/qoder_db.json --course-code DB --json
```

`ls search` 走只读 `/api/search`，只返回令牌所属用户可见的会话、批注、课程与标签；`ls import` 复用 `/api/ingest` 的预览、查重与单事务入库，支持 `--course-code` 覆盖课程、`--platform` 覆盖来源平台。CLI 当前只接受统一 JSON；Markdown 请在网站导入页上传。

令牌默认只有 `read` 与 `ingest` 作用域。若希望 MCP 客户端能写批注，请在 `/tokens` 创建时勾选“允许 MCP 写入批注（annotate）”；页面会展示每个令牌的作用域，可随时撤销。

### MCP 客户端

`app/mcp_server.py` 通过 stdio 提供 MCP 工具，不直接连接数据库，而是用 `LS_TOKEN` 调用本机 Bearer API。先在 `/tokens` 创建令牌，再把下面的配置放进 Claude Desktop / Cursor 的 MCP 设置：

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

- `search`：按关键词检索会话、批注、课程和标签。
- `get_conversation`：读取一个会话的完整消息与批注。
- `list_due_reviews`：列出今天到期或已逾期的复习项。
- `add_annotation`：给消息新增批注；只有 `annotate` 作用域可见，不能修改或删除既有批注。

服务启动时调用 `GET /api/mcp/me` 读取作用域并隐藏无权工具；管理员令牌在认证层即被拒绝。也可以直接运行 `ls-mcp`（执行过 `pip install -e .` 后）。

## 常用命令

```bash
# 应用未执行的数据库迁移（演示库 + 测试库），或只检查
.venv/Scripts/python.exe scripts/migrate.py
.venv/Scripts/python.exe scripts/migrate.py --check

# 全量测试（183 单元 + 125 集成，共 308 项）；集成测试需先初始化测试库
.venv/Scripts/python.exe -m pytest

# 静态检查与提交钩子（uv sync 已安装 Ruff / pyright / pre-commit）
uv run ruff check app scripts tests
uv run pyright app
uv run pre-commit install
uv run pre-commit run --all-files

# 端到端验收：42 项 PASS/FAIL，写入 data/verification/report.json
.venv/Scripts/python.exe scripts/verify.py --json data/verification/report.json

# RLS / 视图 / 应用层 WHERE 隔离对比实验（全部回滚，输出 JSON）
.venv/Scripts/python.exe scripts/rls_experiment.py

# 物化视图统计实验：实时聚合 vs 物化视图耗时、陈旧与刷新（全部回滚）
.venv/Scripts/python.exe scripts/stats_experiment.py

# 为已配置向量模型的用户补齐消息向量（未配置时命令会提示并退出）
.venv/Scripts/python.exe scripts/embed_messages.py --user alice --limit 512

# Text2SQL 金标准评测：30 题 + 10 条恶意 SQL + 2 条跨用户用例
.venv/Scripts/python.exe scripts/eval_text2sql.py

# 重新生成导入样例（正常 / 重复 / 损坏）
.venv/Scripts/python.exe scripts/make_samples.py

# 生成 Chen 记法 E-R 图（SVG）与 Mermaid ER
.venv/Scripts/python.exe scripts/render_er.py

# 安装 Playwright（E2E、截图与扩展冒烟共用）
npm install
npx playwright install chromium

# 页面 E2E（9 项断言用例，需要先启动网站；默认 http://127.0.0.1:8000）
npm run e2e
# 或指定地址：BASE_URL=http://127.0.0.1:8000 npm run e2e

# 页面截图（需要先启动网站；可选 --record 录制 docs/demo.webm）
node scripts/screenshots.mjs

# 浏览器扩展冒烟：内容提取、预览、Bearer 发送（无需启动网站）
node scripts/extension_smoke.mjs
```

> CI 在 GitHub Actions 上用 `pgvector/pgvector:pg16` 服务跑同一套初始化、308 项测试、42 项验收与 9 项 Playwright E2E，结果作为构建产物上传；配置见 [.github/workflows/ci.yml](.github/workflows/ci.yml)。

## 目录结构

```text
learning-space/
├── Dockerfile               # 多阶段构建，非 root 运行
├── docker-compose.yml       # PostgreSQL 16 + app 一条命令启动
├── .dockerignore
├── pyproject.toml           # 依赖、Ruff / pyright / pytest 配置
├── uv.lock                  # uv 锁定的完整依赖图
├── .pre-commit-config.yaml  # 提交前 Ruff + pyright + 配置校验
├── playwright.config.mjs    # Playwright E2E 配置与 JUnit 报告
├── app/
│   ├── main.py              # FastAPI 应用、异常处理、路由挂载
│   ├── config.py            # 环境变量与上限配置
│   ├── db.py                # 连接池、事务、行工厂
│   ├── auth.py              # PBKDF2、Session、CSRF
│   ├── audit.py             # 事务内审计操作者 GUC
│   ├── cli.py               # ls search / ls import 命令行客户端
│   ├── mcp_server.py        # MCP stdio JSON-RPC 服务（四个工具）
│   ├── migrations.py        # 迁移发现、应用与版本记录
│   ├── permissions.py       # 课程角色 / 会话可见性判定
│   ├── routers/             # 页面与 JSON API
│   ├── services/            # 业务 SQL、事务与导出
│   ├── importers/           # JSON / Markdown / 裸文本解析与校验
│   ├── templates/           # Jinja2 页面
│   └── static/              # CSS / JS / PWA manifest、Service Worker、离线页与图标
├── sql/
│   ├── schema.sql           # 12 张基线表、约束、索引、触发器
│   ├── views.sql            # 15 个 t2s 只读权限视图
│   ├── roles.sql            # ls_app / t2s_reader 最小权限
│   ├── migrations/          # 增量迁移（review_logs、api_tokens、tag_edges、audit_log、观测扩展等，记录到 meta.schema_migrations）
│   └── showcase.sql         # 报告用关键 SQL（带注释）
├── extension/               # Chrome MV3 扩展：内容脚本、弹窗、隐私与回退说明
├── scripts/                 # 初始化、迁移、种子、样例、验收、E-R、截图、扩展冒烟
├── data/samples/            # 模拟导入样例（含三种平台原生导出）
├── tests/unit|integration|e2e/  # 单元、集成与浏览器 E2E 测试
└── docs/                    # 报告、设计、需求、SQL 展示、桌面与移动端截图
```

> 数据库共 16 张表：`schema.sql` 的 12 张基线表 + 迁移新增的 `review_logs`（复习事件流水）、`api_tokens`（采集令牌，仅存 SHA-256）、`tag_edges`（知识先修关系）与 `audit_log`（结构变更审计，只记元数据）。

## 数据与安全说明

- **模拟数据**：`data/samples/*.json|md` 与 `scripts/seed_demo.py` 生成的内容均为模拟问答，不含真实个人信息。每个导出的 Markdown 和页面页脚都带有“演示数据不含真实个人信息”声明。
- **导入格式**：JSON 为主（`platform`、`account_label`、`conversations[]`），Markdown 与裸文本为辅（`# 会话标题` + `- 课程:` / `- 标签:` + `## 用户/助手 [时间]`，或直接粘贴 `我：… / 助手：…` 对话）。平台原生导出由 `app/importers/adapters.py` 归一：ChatGPT `conversations.json` 沿 `current_node` 取主线，Qoder 会话导出做字段别名映射，DeepSeek Markdown 识别 `### User/Assistant` 与英文元信息。
- **采集令牌**：`/tokens` 创建的令牌形如 `ls_…`，明文只显示一次，服务端仅存 SHA-256 与可读前缀；每令牌每分钟最多 60 次请求，可随时撤销。管理员无法读取令牌，令牌也应只放在本机脚本或扩展中，不要提交到仓库。
- **浏览器扩展**：`extension/` 使用 MV3，固定权限只有 `activeTab` 与 `storage`；仅当用户在弹窗点击「发送到学习空间」时读取当前标签页并调用 `/api/ingest`，令牌保存在 `chrome.storage.local`。平台 DOM 改版时回退到平台导出文件。
- **分享采集**：`POST /share` 复用 `import_batches` 暂存解析结果（30 分钟有效），不把长文本写入 Cookie；最终保存仍需登录会话与 CSRF Token，并校验批次所有权。
- **离线同步**：会话快照、批注草稿与复习打卡保存在浏览器 IndexedDB；联网后按客户端事件 ID 幂等提交，避免重复写入。
- **权限三层**：页面/服务层角色检查 → SQL 作用域过滤 → 数据库约束与触发器；Text2SQL 额外使用只读角色与 `security_barrier` 视图。
- **数据库角色**：`ls_app` 只有 `app` schema 的 DML 权限，无 DDL；`t2s_reader` 只能 `SELECT` `t2s` 视图，默认只读事务、3 秒语句超时。
- **管理边界**：管理员看不到课程 / 会话页面，也不能读取他人消息正文、批注或复习内容。审计面板只展示课程、课程成员与账号的结构变更元数据（谁、何时、对哪条记录、改了哪些列名），不保存任何列值；密码列即使变更也不出现在审计里。
- **审计与保留**：`audit_log` 由 `SECURITY DEFINER` 触发器写入，`ls_app` 仅可读；清理函数最少保留 90 天，避免保留策略被当作删证据的捷径。
- **数据库观测边界**：`/admin/observability` 只展示 PostgreSQL 统计视图与 4 个白名单执行计划，不接受任意 SQL；慢查询展示归一化语句与指标，不包含参数值，也不关联消息或批注内容。`pg_stat_statements` 由迁移 008 条件安装；若容器未预加载扩展，页面会降级提示，I/O、表访问与执行计划仍可用。
- **混合检索与隐私**：向量能力默认关闭。只有部署方配置 `EMBEDDING_API_KEY` 后，系统才会把可见消息正文发送到该嵌入服务；未配置时检索完全在本地数据库内完成。向量只用于本人可见范围，管理员没有向量召回入口。
- **整理建议与隐私**：`SUGGEST_MODE=auto`（默认）下，只有配置了 `LLM_API_KEY` 且具备会话编辑权的用户点击「生成 AI 建议」时，系统才会把该会话前 40 条消息的节选发送到大模型；建议只回填表单，不自动写入。未配置 Key 时按钮隐藏，可手动填写标签与摘要。管理员没有该入口。

## Text2SQL 两种模式

- `TEXT2SQL_MODE=auto`（默认）：配置了 `LLM_API_KEY` 走 OpenAI 兼容接口；未配置则使用本地规则 mock。
- `TEXT2SQL_MODE=mock`：强制离线规则模式，便于无网络演示。
- `TEXT2SQL_MODE=llm`：强制真实模型；失败时返回错误并写审计日志。

SQL 先经 `sqlglot` PostgreSQL 方言 AST 校验（单条 SELECT/WITH、拒绝嵌套 DML/表函数/锁/危险函数、视图与 CTE 白名单），再经关键字黑名单、注释禁用与 200 行包装，最终由 `t2s_reader` 在只读事务中执行。完整的表结构说明见 `docs/design.md`，验收与演示说明见 `docs/report.md` 与 `docs/demo-script.md`。

### 整理建议模式

- `SUGGEST_MODE=auto`（默认）：配置 `LLM_API_KEY` 时显示「生成 AI 建议」，否则隐藏按钮并保留手动整理。
- `SUGGEST_MODE=off`：强制关闭，适合演示时避免任何外部调用。
- `SUGGEST_MODE=llm`：强制启用（仍需 Key 才能调用成功）。

接口 `POST /api/conversations/{id}/suggest` 会先检查会话编辑权，再把前 40 条消息（每条最多 800 字、总计 6000 字）发给 OpenAI 兼容接口，要求返回 `{summary, tags}`。服务端清洗标签（最多 3 个）、复用同名标签、限制摘要 500 字；结果只回填页面表单，保存动作仍走原有归类事务。

### 金标准评测

`data/text2sql_eval.json` 固定 30 个自然语言问题、10 条恶意 SQL 与 2 条跨用户用例；`scripts/eval_text2sql.py` 对比金标准 SQL 与生成 SQL 的执行结果，统计执行准确率、合法率与越权拦截率。当前离线 mock 基线为 **17/30（56.7%）**，金标准与生成 SQL 合法率均为 **100%**，越权拦截 **12/12**。mock 只覆盖固定问法，换一种说法会落入课程总览兜底，因此这个数字是诚实的离线下限；配置 `LLM_API_KEY` 后可用 `--provider llm` 复测真实模型。结果写入 `data/verification/text2sql_eval.json`。

## v2 改进路线（Roadmap）

v1 已定稿并推送。下一阶段的方向与验收标准统一记录在 [`docs/roadmap.md`](docs/roadmap.md)，每项都有编号，可直接对应 Issue 与分工。

**优先级**：普通用户可用性 > 答辩差异化 > 长期产品化。

**四条硬性原则**：不新增强制外部依赖；每项 AI 能力都有降级路径；动 schema 必须先有迁移机制；每个功能都必须有手机路径（先按 390px 设计）。

**“好用”是可验收的**：路线图定义了 UX-1 至 UX-10 十条标准，包括 390px 无横向滚动、3 分钟完成首次导入、导入不超过 3 步、空列表必有下一步、写操作可撤销、首屏 JS < 100KB / LCP < 2.5s、键盘可完成核心操作、错误页说明原因与恢复路径等。

**关于本地模型**：Ollama **不是必选项**，不会要求普通用户部署本地模型。系统按 `mock`（默认，零安装）/ `openai`（部署方配置）/ `ollama`（主动开启的隐私模式）三种模式运行；没有向量能力时检索自动退回 `pg_trgm`，没有大模型时 Text2SQL 使用规则 mock，界面顶栏始终显示当前 AI 模式。

| 阶段 | 内容 | 用户可感知的结果 |
| --- | --- | --- |
| Phase 1 | 迁移机制、移动端信息架构、空状态引导、导入向导、PWA 安装、Share Target、粘贴导入、收件箱、静态资源优化 | 手机上可安装、可分享导入、导入有向导、列表不空转 |
| Phase 2 | 离线阅读与复习 outbox、复习事件表、Ingest Token、浏览器扩展、平台导出适配器、全局搜索 | 断网可读可复习、采集更省事、找会话更快 |
| Phase 3 | pgvector 混合检索、自动标签/摘要建议、AST 校验、EXPLAIN 预检、Text2SQL 评测集（T1 / T2 / T5 / R1 / R6 已完成） | 检索更准，整理更省事，Text2SQL 可证明 |
| Phase 4 | 知识先修图、只读 MCP Server、CLI、RLS 对比、审计历史、数据库观测页（K2 / A1 / A2 / D1 / D2 / D3 已完成） | 从“记录库”变成“学习空间”，并有对外接口 |
| Phase 5 | Dockerfile、CI、Ruff/pyright、E2E，PG18/PG19 实验分支、FSRS | 可部署、可回归、有前沿实验 |

**明确不做**：浏览器内 WASM 数据库、CRDT 多端同步、边缘部署、Redis/Celery/微服务、Electron、messages 分区表、语义缓存。

**分工约定**：一项任务一个分支（如 `feat/C1-share-target`）；动 schema 必须附带可重复执行的迁移；新增 AI 能力必须同时提交无模型降级路径与测试；每个阶段结束都要求 `pytest` 全绿、`verify.py` 全 PASS。

## 已知限制

- 单机课程项目，未做分布式部署与高并发压测；并发导入依靠唯一键与 `pg_advisory_xact_lock` 保证正确性。
- PostgreSQL 原生全文检索对中文分词支持有限，因此默认使用 `pg_trgm` + `ILIKE`；配置向量模型后叠加 HNSW 语义召回，并用 RRF 融合两路排名。无模型、调用失败或维度不符时都会退回词法检索，页面上会标注当前模式。在 420 条消息的演示规模下，规划器通常选择顺序扫描，索引能力通过 `SET enable_seqscan = off` 的对比计划验证。
- 模拟数据中多个会话共享常用追问句，因此“重复消息检测”会命中这些模板句；这是刻意保留的查重演示素材，不代表真实用户行为。
- Playwright E2E 与截图脚本会临时创建导入预览批次、采集令牌和 Text2SQL 审计记录；演示库验收前可用 `scripts/verify.py` 的事务回滚检查，运行后可按需清理这些演示痕迹。
- 整理建议的输出由外部模型决定，同样的问题不保证每次一致；服务端只做 JSON 解析、去重与长度限制，并在 25 秒超时后返回可读错误。建议永远不会自动落库，必须由用户点击保存。
