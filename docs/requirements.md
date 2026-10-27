# 需求拆解与实现映射

本文档按 `Rxx-Fxx` 编号拆解需求，给出输入、输出、异常、设计要点、代码位置与验收点。测试文件位于 `tests/`，端到端检查位于 `scripts/verify.py`。

## R01 聊天记录导入

| ID | 功能 | 输入 | 输出 / 行为 | 异常与边界 | 实现位置 | 验收 |
| --- | --- | --- | --- | --- | --- | --- |
| R01-F01 | 文件识别与解析 | `.json` / `.md` / `.markdown` / `.txt` 文件，可选平台与账号 | 统一 DTO：平台、账号、会话、消息、标签、时间 | 扩展名不支持；空文件；> 5 MB；> 2000 条消息 | `app/services/import_service.py:parse_upload`、`app/importers/*` | `tests/unit/test_importers.py` |
| R01-F02 | 字段校验与清洗 | 解析后的 JSON / Markdown 字段 | 合法记录；错误/警告列表 | 缺标题、缺消息、角色非法、时间格式错误、序号重复 | `json_importer.py`、`markdown_importer.py` | `tests/unit/test_importers.py` |
| R01-F03 | 预览、查重与课程映射 | 合法文件、用户勾选、课程映射 | `import_batches` 预览行 + `preview_token`；新增/可更新/完全重复分类 | token 过期（30 分钟）、token 不属于当前用户、课程无编辑权 | `save_preview`、`preview_context`、`/imports/preview/{token}` | `tests/integration/test_import.py` |
| R01-F04 | 事务确认入库 | token、勾选的会话索引、课程映射 | 新增/更新会话、新增/跳过消息、标签关联计数 | 状态非 preview、已过期、并发确认、来源冲突 | `confirm_import`、`routers/imports.py` | `test_import.py`、`verify.import.confirm` |
| R01-F05 | 重复与损坏导入 | 同一文件 / 缺字段文件 | 重复文件返回 0 新增、全部跳过；损坏文件被拒绝 | 内容冲突计入 `conflicts`；失败批次标记 failed | `confirm_import`、`parse_upload` | `verify.import.duplicate`、`verify.import.broken` |

**设计要点**

- 解析与入库分离：解析器只产出 DTO，不接触数据库；`import_service` 统一持有 SQL 与事务。
- 预览持久化：`import_batches.payload` 保存解析结果，`preview_token` 为 UUID，30 分钟 TTL。
- 幂等：`sources`、`conversations`、`messages`、`conversation_tags` 均有唯一键或 `ON CONFLICT`。
- 并发：确认时 `SELECT ... FOR UPDATE` 锁批次，`pg_advisory_xact_lock(source_id)` 串行化同一来源。

## R02 课程、成员与标签

| ID | 功能 | 输入 | 输出 / 行为 | 异常与边界 | 实现位置 | 验收 |
| --- | --- | --- | --- | --- | --- | --- |
| R02-F01 | 课程 CRUD | 编号、名称、学期、教师、描述 | 课程详情；删除后会话 `course_id` 置空 | 必填缺失、编号格式、重复 `(created_by, code, semester)` | `course_service.py`、`routers/courses.py` | `tests/integration/test_permissions.py` |
| R02-F02 | 成员共享与角色 | 用户名、角色 | 添加/改角色/移除成员 | 每课恰好一个 owner；不能移除最后 owner；非 owner 不可管理 | `course_service.py`、`permissions.py` | `test_permissions.py`、`test_schema.py` |
| R02-F03 | 标签管理 | 名称、类型、颜色 | 新建/改名/改色/删除；删除级联解除会话关联 | 同名冲突、颜色格式、非本人标签不可改 | `routers/tags.py`、`services` | `tests/integration/test_search.py`（筛选） |
| R02-F04 | 会话归类与打标签 | 课程 ID、标签 ID 集合 | 更新会话课程；增删会话标签 | 无编辑权拒绝；标签与会话不同 owner 拒绝 | `conversation_service.organize_conversation`、触发器 | `test_schema.py` 触发器用例、`verify.scope` |

**设计要点**

- 课程角色 `owner > editor > viewer`，以 `ROLE_RANK` 比较。
- 每门课一个 owner：部分唯一索引保证“至多一个”，延迟约束触发器保证“至少一个”。
- 标签属于个人；共享课程中 editor 打标签时，触发器同时校验标签归属和课程权限。

## R03 检索与阅读

| ID | 功能 | 输入 | 输出 / 行为 | 异常与边界 | 实现位置 | 验收 |
| --- | --- | --- | --- | --- | --- | --- |
| R03-F01 | 会话列表与筛选分页 | 关键词、课程、标签、日期、页码 | 每页会话、消息数、标签、命中片段、总数 | 无权限数据不可见；空结果；非法日期忽略 | `conversation_service.list_conversations` | `test_search.py` |
| R03-F02 | 中文关键词检索 | 1–100 字关键词 | 标题或消息命中；命中消息的 `seq_no` 与片段 | `%`/`_`/`\` 转义；超长截断 | 同上，`ILIKE ... ESCAPE '\'` + `pg_trgm` | `verify.search.keyword`、`test_search.py` |
| R03-F03 | 会话上下文 | 会话 ID、发送者筛选 | 消息按 `seq_no` 排序；上下文窗口；批注内联；发送者统计 | 无权限 403；会话不存在 404 | `conversation_service.get_conversation`、`routers/conversations.py` | `verify.search.context`、`test_search.py` |
| R03-F04 | 数据范围过滤 | 当前用户 ID | 只返回本人来源或共享课程下的会话 | 管理员不进入课程页面 | `permissions.can_access_conversation`、SQL 作用域 | `test_permissions.py`、`verify.scope` |
| R03-F05 | 全局搜索 | 关键词（≤ 100 字） | 会话/消息、本人批注、课程、标签分组结果；`/` 或 `Ctrl/Cmd+K` 唤起 | 空查询给出提示；管理员不得检索他人正文 | `search_service.global_search`、`routers/search.py`、`static/app.js` | `tests/integration/test_search.py`、`verify.search.global` |

**设计要点**

- 单条 SQL 完成“会话 + 课程 + 来源 + 标签数组 + 消息数 + 命中片段 + 总数”，`count(*) OVER()` 避免二次计数。
- `LEFT JOIN LATERAL` 取每个会话最早的命中消息作为片段来源。
- 片段函数在 Python 侧围绕关键词截取窗口并做 HTML 转义，避免 XSS。

## R04 批注与复习

| ID | 功能 | 输入 | 输出 / 行为 | 异常与边界 | 实现位置 | 验收 |
| --- | --- | --- | --- | --- | --- | --- |
| R04-F01 | 批注 CRUD 与状态 | 消息 ID、类型、内容、状态 | 新建/编辑/删除/标记解决；批注内联展示 | 无编辑权 403；空内容；非法类型 | `annotation_service.py`、`routers/annotations.py` | `tests/integration/test_annotations_reviews.py` |
| R04-F02 | 加入复习队列 | 批注 ID | 创建 `pending` 复习项，默认 1 天后到期 | 重复加入幂等；无编辑权拒绝；同一用户+批注唯一 | `add_to_review` | `test_annotations_reviews.py`、`verify.annotation.lifecycle` |
| R04-F03 | 复习打卡与间隔 | 复习项 ID | `review_count+1`，间隔 1→2→4→8…，到期日顺延 | 只能操作自己的复习项；上限 60 天 | `review_service.review_once` | `verify.annotation.lifecycle` |
| R04-F04 | 复习筛选与逾期 | active / mastered / all | 到期日排序；逾期标记；掌握/重置/删除 | 非本人 404；掌握后不再进队列 | `review_service.list_reviews`、`summary` | `test_annotations_reviews.py` |

**设计要点**

- `UNIQUE (user_id, annotation_id)` 允许“多人复习同一批注”，但禁止同一人重复入队。
- 复习更新用单条 `UPDATE ... RETURNING`，在事务内完成读改写。
- 部分索引 `review_items_queue_idx` 只覆盖未掌握项，减少队列扫描。

## R05 统计与导出

| ID | 功能 | 输入 | 输出 / 行为 | 异常与边界 | 实现位置 | 验收 |
| --- | --- | --- | --- | --- | --- | --- |
| R05-F01 | 学习概览 | 当前用户 | 课程、会话、消息、批注、标签、待复习计数 | 全部实时计数，不落冗余列 | `stats_service.load_stats` | `verify.stats.overview` |
| R05-F02 | 课程 / 月度 / 标签统计 | 当前用户 | 课程排名、8 个月活跃补零、标签 Top12 | 无数据课程计 0；空月份补 0 | `stats_service` | `test_stats.py`、`verify.stats.overview` |
| R05-F03 | 批注 / 复习 / 重复检测 | 当前用户 | 批注解决率、复习状态、重复内容 Top5 | 重复检测使用 `md5(content)` | `stats_service` | `test_stats.py` |
| R05-F04 | 统计 CSV 导出 | 当前用户 | UTF-8 BOM CSV，含 section/item/metric/value | 未登录重定向登录 | `stats_csv`、`/exports/stats.csv` | `verify.stats.csv` |
| R05-F05 | 单会话 Markdown 导出 | 会话 ID | 标题、课程、来源、标签、消息、批注、模拟数据声明 | 无权限 403；文件名安全清洗 | `export_service.conversation_markdown` | `verify.export.markdown` |

**设计要点**

- 课程统计使用 `LEFT JOIN` + `COUNT(DISTINCT ...)`，没有消息的课程仍显示 0。
- 月度活跃用 `generate_series` 生成月份骨架，再 `LEFT JOIN` 实际消息。
- 批注解决率用 `COUNT(*) FILTER (WHERE status = 'resolved')`。
- 导出文件名去除 Windows 非法字符并限制 60 字符。

## R06 用户、认证与权限

| ID | 功能 | 输入 | 输出 / 行为 | 异常与边界 | 实现位置 | 验收 |
| --- | --- | --- | --- | --- | --- | --- |
| R06-F01 | 登录与会话 | 用户名、密码、next | 建立 `ls_session`；登录后跳转 next 或概览 | next 只允许站内单斜杠路径；错误 401 | `routers/auth.py`、`auth.py` | `tests/unit/test_auth.py`、手工登录 |
| R06-F02 | 密码与 CSRF | 密码、表单 token | PBKDF2-HMAC-SHA256 210k 哈希；所有 POST 校验 CSRF | 哈希格式错误、token 缺失/不匹配 | `auth.py` | `test_auth.py`、安全审查 |
| R06-F03 | 课程级权限 | 当前用户、课程/会话 ID | owner/editor/viewer 分级；越权 403 | 管理员访问课程页面被拒 | `permissions.py`、触发器 | `test_permissions.py`、`verify.scope` |
| R06-F04 | 登出与停用 | 当前会话 / 用户 ID | 清空 session；`is_active=false` 后不能登录 | 已停用账号登录失败 | `routers/auth.py`、`admin_service.py` | `test_admin.py` |

**设计要点**

- 密码格式 `pbkdf2_sha256$210000$<salt>$<hash>`，使用 `secrets.compare_digest` 常量时间比较。
- Session 使用 `itsdangerous` 签名；生产环境必须替换 `SESSION_SECRET` 并启用 `https_only`。
- CSRF token 存在 session 中，通过隐藏字段或 `meta` 传给前端。

## R07 Text2SQL

| ID | 功能 | 输入 | 输出 / 行为 | 异常与边界 | 实现位置 | 验收 |
| --- | --- | --- | --- | --- | --- | --- |
| R07-F01 | 自然语言生成 SQL | 问题（≤ 300 字） | `{sql, explanation}`；mock 规则或 LLM | 模型超时、返回非 JSON、空问题 | `text2sql_service.generate`、`_mock_sql`、`_llm_sql` | `tests/unit/test_text2sql_guard.py` |
| R07-F02 | SQL 守卫 | 生成的 SQL | AST 校验单条 SELECT/WITH/UNION；无注释；视图与 CTE 白名单；正则黑名单作第二层 | 多语句、DDL/DML、嵌套 DML、`SELECT INTO`、`FOR UPDATE`、表函数、系统表、`app.`、危险函数全部拒绝；字符串字面量不误伤 | `check_sql`、`_validate_ast` | `test_text2sql_guard.py`、`verify.t2sql.guard` |
| R07-F03 | 只读执行 | 合法 SQL、当前用户 ID | 最多 200 行；3 秒超时；`t2s_reader` 执行 | 超时、权限错误、执行错误写日志 | `execute_query`、`db.t2s_pool` | `test_text2sql.py`、`verify.t2sql.role` |
| R07-F04 | 审计历史 | 每次查询 | 问题、SQL、状态、耗时、行数、错误 | 成功/拒绝/异常都记录 | `record_log`、`history` | `verify.t2sql.audit` |
| R07-F05 | 模式切换 | `TEXT2SQL_MODE` / `LLM_API_KEY` | auto / mock / llm | 无 Key 时明确标注“本地规则模式” | `provider_mode`、页面 badge | `test_text2sql_guard.py` |
| R07-F06 | EXPLAIN 预检 | 已通过守卫的 SQL、当前用户 ID | 只读 `EXPLAIN (FORMAT JSON)`；返回总代价与估算行数 | 超过代价/行数阈值拒绝并记 `rejected`；预检异常记 `error`；可配置关闭 | `preflight_query`、`TEXT2SQL_MAX_COST` / `TEXT2SQL_MAX_EST_ROWS` | `test_text2sql.py`、`verify.t2s.preflight` |

**设计要点**

- Prompt 只提供 `t2s` 视图及列、关系说明、少样本；要求输出 JSON。
- 守卫与执行分离；守卫拒绝的 SQL 不会进入数据库。
- 最终权限边界不在 Prompt，而在数据库：视图按 `app.user_id` 过滤，`t2s_reader` 无 `app` schema 权限且默认只读。

## R08 管理

| ID | 功能 | 输入 | 输出 / 行为 | 异常与边界 | 实现位置 | 验收 |
| --- | --- | --- | --- | --- | --- | --- |
| R08-F01 | 用户列表与启停 | 当前管理员、目标用户 ID | 用户状态切换；不能停用自己 | 非管理员 403；目标不存在 404 | `admin_service.py`、`routers/admin.py` | `tests/integration/test_admin.py` |
| R08-F02 | 系统聚合统计 | 管理员 | 用户数、课程数、会话数、消息数、批注数、复习数 | 只返回聚合值，不返回正文 | `admin_service.load_system_stats` | `test_admin.py`、`verify` 管理边界 |
| R08-F03 | 管理边界 | 管理员访问课程/会话/导入 | 统一 403 | 不能通过直接 URL 绕过 | 各 router `_require_student` | `test_permissions.py` |

## R09 快速采集与使用体验

| ID | 功能 | 输入 | 输出 / 行为 | 异常与边界 | 实现位置 | 验收 |
| --- | --- | --- | --- | --- | --- | --- |
| R09-F01 | PWA Share Target | 手机分享的标题/文本/链接 | 解析后暂存 `import_batches`，跳转预览；可保存到课程或收件箱 | 超过 20 万字符拒绝；来源不同源拒绝；最终保存校验 CSRF | `routers/capture.py`、`services/import_service.stage_paste` | `tests/integration/test_capture.py`、`verify.capture.paste` |
| R09-F02 | 裸文本粘贴导入 | 一段问答文本、可选标题/平台 | 识别中英文角色、时间、课程/标签元信息，输出统一 DTO | 无角色前缀时按单条用户消息兜底；空文本拒绝 | `importers/plain_text_importer.py` | `tests/unit/test_plain_text_importer.py`、`test_capture.py` |
| R09-F03 | 收件箱批量归类 | 勾选的未归类会话、目标课程 | 仅移动本人且当前未归类的会话 | 非可编辑课程 403；共享课程他人会话不动 | `conversation_service.bulk_organize_inbox`、`routers/capture.py` | `test_capture.py`、`verify.inbox.organize` |
| R09-F04 | 导入向导与结果页 | 上传/粘贴、勾选、课程映射 | 三步步骤条、逐条错误、新增/跳过/冲突摘要 | 预览 token 过期/越权；失败整体回滚 | `templates/imports/*`、`routers/imports.py` | UI 走查、`test_import.py` |
| R09-F05 | 反馈与恢复 | 删除、加载失败、越权 | 5 秒撤销、可关闭提示、错误页原因与恢复入口 | 离页兜底提交；错误不自动消失 | `static/app.js`、`templates/error.html`、`main.py` | `tests/unit/test_ui_assets.py`、Playwright 走查 |
| R09-F06 | 采集令牌与 Bearer 入库 | 用途名称；`Authorization: Bearer`；统一 JSON | 明文只显示一次；按 `course_code` 归课；每分钟 60 次 | 无效/撤销令牌 401；超限 429；管理员拒绝；跨用户课程拒绝 | `token_service.py`、`routers/tokens.py`、`routers/api.py`、`import_service.ingest_json` | `tests/integration/test_tokens.py`、`test_api_tokens.py`、`verify.token.ingest` |
| R09-F07 | 平台原生导出适配 | ChatGPT `conversations.json`、Qoder `sessions`、DeepSeek Markdown | 归一为统一 DTO；保留警告并复用导入流水线 | 缺平台/缺消息/时间非法仍按原校验拒绝 | `app/importers/adapters.py`、`json_importer.py`、`markdown_importer.py` | `tests/unit/test_platform_adapters.py`、`verify.import.adapters` |
| R09-F08 | MV3 浏览器扩展 | 当前标签页会话、学习空间地址、令牌、课程/标签 | 预览后点击发送；Bearer 调用 `/api/ingest` | 无令牌禁用发送；401/429 提示；无法提取时回退导出文件 | `extension/` | `tests/unit/test_extension_assets.py`、`scripts/extension_smoke.mjs` |

## R10 离线学习

| ID | 功能 | 输入 | 输出 / 行为 | 异常与边界 | 实现位置 | 验收 |
| --- | --- | --- | --- | --- | --- | --- |
| R10-F01 | 会话快照 | 会话 ID | 保存到 IndexedDB；离线页可阅读与会话内搜索 | 隐私模式 IndexedDB 不可用时提示；无权限 403 | `routers/api.py`、`static/app.js`、`static/offline.html` | `tests/unit/test_offline_assets.py`、Playwright 截图 |
| R10-F02 | 离线批注草稿 | 消息 ID、类型、正文 | 断网时暂存草稿，联网后提交 | 客户端事件 ID 幂等；权限变化时服务端拒绝 | `static/app.js`、`annotation_service.py` | `test_annotations_reviews.py`、离线走查 |
| R10-F03 | 离线复习与 outbox | 复习项、动作 | 缓存复习队列；断网打卡进入 outbox | 同一事件重复提交只落一次；联网自动 flush | `static/app.js`、`review_service.py`、`review_logs` | `verify.review.activity`、离线走查 |
| R10-F04 | 同步状态提示 | 在线/离线、待同步数 | 页面横幅、导航计数、离线页待同步提示 | 网络恢复自动提交并刷新 | `static/app.js`、`templates/base.html` | `tests/unit/test_offline_assets.py` |

## 非功能需求

| 类别 | 要求 | 实现 |
| --- | --- | --- |
| 安全 | 密码不落明文、所有写操作防 CSRF、越权三层防护 | PBKDF2、CSRF、角色检查 + SQL 作用域 + 触发器 |
| 数据完整性 | 主外键、唯一、CHECK、事务 | `schema.sql`、导入确认事务、复习单语句更新 |
| 性能 | 列表分页、关键查询有索引、统计实时但规模可控 | 索引设计、`LIMIT/OFFSET`、部分索引、trgm |
| 可维护性 | 关键 SQL 可定位、无 ORM 黑盒 | `sql/` + `services/` + 本文件映射 |
| 可演示性 | 离线可演示、模拟数据可复现 | mock Text2SQL、`make_samples.py`、`seed_demo.py` |
| 可验收 | 自动化测试与端到端报告 | 203 个 pytest 用例 + 34 项 `verify.py` 检查 |

## 需求到验收命令的映射

```bash
# R01–R10 的主要回归
.venv/Scripts/python.exe -m pytest -q

# 端到端 PASS/FAIL（含权限、导入、检索、统计、导出、Text2SQL、事务）
.venv/Scripts/python.exe scripts/verify.py --json data/verification/report.json
```

验收输出示例见 `data/verification/report.json`：34/34 PASS，0 FAIL。
