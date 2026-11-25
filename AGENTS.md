# Learning Space — Agent 入口

开始任何任务前，先读 **`docs/handoff.md`**（项目是什么、已完成什么、下一步建议、运行与验收命令）。随后按任务读 `README.md` 的“答辩重点”、`docs/report.md`、`docs/demo-script.md` 或对应代码。

当前基线：`v0.4.1`（Windows NSIS / macOS DMG 已发布）；20 张表、18 个视图、13 个触发器、65 个索引、423 个 pytest、43/43 验收。

硬性约定：

- 演示数据只用模拟数据；不提交真实聊天记录、Token、Key。
- `.env` 永不进入仓库，不打印密钥或会话 Cookie。
- 未明确要求时，不 commit / push。
- 一项功能一个分支；动 schema 必须附带编号迁移。
- 每个 AI 能力都必须有无模型降级路径；页面标注当前模式。
- 合入前必须通过：`ruff check .`、`pyright app`、`pytest -q`、`scripts/verify.py`。
- 课程汇报保持数据库视角；检索部分以 README 的“答辩重点”为主线。