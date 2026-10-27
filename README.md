# 个人学习空间（Learning Space）

《数据库系统概论》大作业 · 选题 2：围绕“与大模型的聊天记录”构建一个可查阅、可批注、可复习的课程资料库。

系统把 Qoder / ChatGPT / DeepSeek 等平台的问答导出文件解析为统一结构，按课程、来源、标签整理；提供中文检索、会话上下文、批注、间隔复习、统计导出，并额外实现一个受只读权限视图保护的 **Text2SQL** 页面。

> 演示数据全部为围绕课程知识点生成的模拟聊天记录，不含任何真实个人信息。系统管理员只查看用户与聚合统计，不读取他人消息正文或批注。

## 功能一览

| 模块 | 能力 |
| --- | --- |
| 记录导入 | JSON / Markdown 上传、裸文本粘贴、三步导入向导（上传/粘贴 → 预览勾选 → 结果摘要）、解析校验、持久化预览、查重、课程映射、单事务入库、失败回滚 |
| 快速采集 | PWA Share Target：手机从任意 App 分享文本/链接进来；粘贴后直接保存到未归类收件箱或指定课程 |
| 收件箱 | 未归类会话队列、批量归类到课程、分享落地页与高亮定位 |
| 课程与标签 | 课程 CRUD、成员共享（owner / editor / viewer）、标签 CRUD、会话归类与打标签 |
| 检索阅读 | 课程 / 标签 / 日期 / 关键词筛选、分页、消息上下文、关键词高亮、命中片段 |
| 批注与复习 | 笔记 / 疑问 / 纠错 / 总结四类批注、解决状态、加入复习队列、间隔 1→2→4→8… 天、逾期提示、掌握/重置 |
| 统计与导出 | 概览、课程排名、月度活跃补零、标签 TopN、批注解决率、复习状态、重复内容检测；统计 CSV、单会话 Markdown |
| 用户与权限 | Session 登录、PBKDF2 密码哈希、CSRF、系统角色 + 课程角色、应用层与数据库层双重防护 |
| Text2SQL | 自然语言→SQL→校验→只读执行→表格展示、解释、审计历史；无 API Key 时使用本地规则 mock |
| 管理 | 用户启停、系统聚合统计（不读正文） |
| 移动端与安装 | 390px 优先的底部标签栏 / 抽屉导航、列表卡片化、专注阅读模式；PWA manifest + Service Worker 应用壳缓存、离线页、桌面安装、分享快捷方式 |

> PWA 安装与 Service Worker 只在 `localhost` 或 HTTPS 下启用；通过局域网 HTTP 访问手机时仍可使用完整响应式界面，但不会缓存静态资源。

## 技术栈

- Python 3.12+ / FastAPI / Jinja2 服务端渲染
- PostgreSQL 16（Docker Compose，宿主端口 5433）
- psycopg 3 + psycopg_pool，手写参数化 SQL，不使用 ORM
- `pg_trgm` GIN 索引支持中文子串检索
- Playwright（可选）用于页面截图与演示录屏

## 快速开始

前置条件：Docker Desktop 已启动；Python 3.12+；Node.js 20+（仅截图/录屏需要）。

```bash
# 1. 启动数据库（首次会拉取 postgres:16-alpine）
docker compose up -d db

# 2. 创建虚拟环境并安装依赖
py -3.13 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt

# 3. 初始化演示库 + 测试库（--reset 会删除同名库后重建）
.venv/Scripts/python.exe scripts/init_db.py --reset

# 4. 写入演示数据（3 个账号、5 门课程、30 个会话、420 条消息）
.venv/Scripts/python.exe scripts/seed_demo.py

# 5. 启动网站
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000>，使用下方演示账号登录。

### 演示账号

| 账号 | 密码 | 身份 |
| --- | --- | --- |
| `alice` | `Alice123!` | 学生；5 门课程 owner；拥有 3 个平台来源 |
| `bob` | `Bob123!` | 学生；操作系统 editor、数据库 viewer |
| `admin` | `Admin123!` | 管理员；只访问用户管理与系统聚合统计 |

这些密码仅用于本地演示；数据库只保存 PBKDF2-HMAC-SHA256（210,000 次迭代）哈希。

## 常用命令

```bash
# 应用未执行的数据库迁移（演示库 + 测试库），或只检查
.venv/Scripts/python.exe scripts/migrate.py
.venv/Scripts/python.exe scripts/migrate.py --check

# 全量测试（69 单元 + 68 集成，共 137 项）；集成测试需先初始化测试库
.venv/Scripts/python.exe -m pytest -q

# 端到端验收：29 项 PASS/FAIL，写入 data/verification/report.json
.venv/Scripts/python.exe scripts/verify.py --json data/verification/report.json

# 重新生成导入样例（正常 / 重复 / 损坏）
.venv/Scripts/python.exe scripts/make_samples.py

# 生成 Chen 记法 E-R 图（SVG）与 Mermaid ER
.venv/Scripts/python.exe scripts/render_er.py

# 页面截图（需要先启动网站；可选 --record 录制 docs/demo.webm）
npm install
npx playwright install chromium
node scripts/screenshots.mjs
```

## 目录结构

```text
learning-space/
├── app/
│   ├── main.py              # FastAPI 应用、异常处理、路由挂载
│   ├── config.py            # 环境变量与上限配置
│   ├── db.py                # 连接池、事务、行工厂
│   ├── auth.py              # PBKDF2、Session、CSRF
│   ├── migrations.py        # 迁移发现、应用与版本记录
│   ├── permissions.py       # 课程角色 / 会话可见性判定
│   ├── routers/             # 页面与 JSON API
│   ├── services/            # 业务 SQL、事务与导出
│   ├── importers/           # JSON / Markdown / 裸文本解析与校验
│   ├── templates/           # Jinja2 页面
│   └── static/              # CSS / JS / PWA manifest、Service Worker、离线页与图标
├── sql/
│   ├── schema.sql           # 12 张表、约束、索引、触发器
│   ├── views.sql            # 13 个 t2s 只读权限视图
│   ├── roles.sql            # ls_app / t2s_reader 最小权限
│   ├── migrations/          # v2 增量迁移（编号 SQL，记录到 meta.schema_migrations）
│   └── showcase.sql         # 报告用关键 SQL（带注释）
├── scripts/                 # 初始化、迁移、种子、样例、验收、E-R、截图
├── data/samples/            # 模拟导入样例
├── tests/unit|integration/  # 单元与集成测试
└── docs/                    # 报告、设计、需求、SQL 展示、桌面与移动端截图
```

## 数据与安全说明

- **模拟数据**：`data/samples/*.json|md` 与 `scripts/seed_demo.py` 生成的内容均为模拟问答，不含真实个人信息。每个导出的 Markdown 和页面页脚都带有“演示数据不含真实个人信息”声明。
- **导入格式**：JSON 为主（`platform`、`account_label`、`conversations[]`），Markdown 与裸文本为辅（`# 会话标题` + `- 课程:` / `- 标签:` + `## 用户/助手 [时间]`，或直接粘贴 `我：… / 助手：…` 对话）。详见导入页的“导入格式说明”。
- **分享采集**：`POST /share` 复用 `import_batches` 暂存解析结果（30 分钟有效），不把长文本写入 Cookie；最终保存仍需登录会话与 CSRF Token，并校验批次所有权。
- **权限三层**：页面/服务层角色检查 → SQL 作用域过滤 → 数据库约束与触发器；Text2SQL 额外使用只读角色与 `security_barrier` 视图。
- **数据库角色**：`ls_app` 只有 `app` schema 的 DML 权限，无 DDL；`t2s_reader` 只能 `SELECT` `t2s` 视图，默认只读事务、3 秒语句超时。
- **管理边界**：管理员看不到课程 / 会话页面，也不能读取他人消息正文、批注或复习内容。

## Text2SQL 两种模式

- `TEXT2SQL_MODE=auto`（默认）：配置了 `LLM_API_KEY` 走 OpenAI 兼容接口；未配置则使用本地规则 mock。
- `TEXT2SQL_MODE=mock`：强制离线规则模式，便于无网络演示。
- `TEXT2SQL_MODE=llm`：强制真实模型；失败时返回错误并写审计日志。

SQL 经过单语句、首关键字、关键字黑名单、对象白名单、注释禁用、200 行包装等检查，最终由 `t2s_reader` 在只读事务中执行。完整的表结构说明见 `docs/design.md`，验收与演示说明见 `docs/report.md` 与 `docs/demo-script.md`。

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
| Phase 3 | pgvector 混合检索、自动标签建议、AST 校验、EXPLAIN 预检、Text2SQL 评测集 | 检索更准，Text2SQL 可证明 |
| Phase 4 | 知识先修图、只读 MCP Server、CLI、RLS 对比、审计历史、SQL 观测页 | 从“记录库”变成“学习空间”，并有对外接口 |
| Phase 5 | Dockerfile、CI、Ruff/pyright、E2E，PG18/PG19 实验分支、FSRS | 可部署、可回归、有前沿实验 |

**明确不做**：浏览器内 WASM 数据库、CRDT 多端同步、边缘部署、Redis/Celery/微服务、Electron、messages 分区表、语义缓存。

**分工约定**：一项任务一个分支（如 `feat/C1-share-target`）；动 schema 必须附带可重复执行的迁移；新增 AI 能力必须同时提交无模型降级路径与测试；每个阶段结束都要求 `pytest` 全绿、`verify.py` 全 PASS。

## 已知限制

- 单机课程项目，未做分布式部署与高并发压测；并发导入依靠唯一键与 `pg_advisory_xact_lock` 保证正确性。
- PostgreSQL 原生全文检索对中文分词支持有限，因此使用 `pg_trgm` + `ILIKE`；在 420 条消息的演示规模下，规划器通常选择顺序扫描，索引能力通过 `SET enable_seqscan = off` 的对比计划验证。
- 模拟数据中多个会话共享常用追问句，因此“重复消息检测”会命中这些模板句；这是刻意保留的查重演示素材，不代表真实用户行为。
- Playwright 截图脚本会临时创建一个导入预览批次和一条 Text2SQL 审计记录；演示库验收前可用 `scripts/verify.py` 的事务回滚检查，截图后可按需清理这两类演示痕迹。
