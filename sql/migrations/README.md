# 数据库迁移

`schema.sql` 是 v1 基线结构，`migrations/` 存放其后的增量变更。

## 命名

```text
001_add_api_tokens.sql
002_add_review_logs.sql
```

- 三位递增序号 + 小写下划线描述，只允许 `^[0-9]{3}_[a-z0-9_]+$`
- 版本号一旦合入 `main` 不得修改；需要修正时新增下一个版本
- 每个文件必须是可在空库上从基线顺序执行的 SQL

## 执行

```bash
# 对演示库与测试库应用未执行的迁移
.venv/Scripts/python.exe scripts/migrate.py

# 只检查是否有待执行迁移（有待执行时退出码为 1）
.venv/Scripts/python.exe scripts/migrate.py --check

# 初始化时自动应用：schema.sql -> migrations -> views.sql -> roles.sql
.venv/Scripts/python.exe scripts/init_db.py --reset
```

已执行版本记录在 `meta.schema_migrations`。`000_baseline` 表示 `schema.sql` 已应用，它不是迁移文件。

## 约束

- 迁移在单个事务中执行，失败自动回滚，不会写入版本记录
- 迁移只能使用 `ls_app` 之外的初始化连接执行（需要 DDL 权限）
- 修改被 `t2s` 视图引用的表时，需同步评估 `views.sql` 是否需要更新
- 不要删除或重命名已发布的迁移文件
