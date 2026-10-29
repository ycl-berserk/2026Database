# 学习空间桌面版（Tauri v2）

把学习空间打包成“装好即用”的桌面应用：不依赖本机 Python、PostgreSQL 或 Docker，
每个用户的数据只保存在自己的电脑上。

安装包内置：

- PostgreSQL 16（裁剪版，含 `pg_trgm`、`pgvector`，仅监听 `127.0.0.1`）
- FastAPI 服务的 PyInstaller sidecar（`ls-server`）
- Tauri 桌面壳：托盘、全局快捷键、剪贴板提示、启动进度与错误提示

## 普通用户

1. 运行安装包（Windows 首次会被 SmartScreen 提示，选择“更多信息 → 仍要运行”）。
2. 启动“Learning Space”，首次启动会初始化本机数据库，随后进入设置向导。
3. 在 `/setup` 创建本地账号（自动成为管理员）；之后手机或用同一账号登录。
4. 关闭窗口会收进系统托盘；完全退出请用托盘菜单“退出”。

数据位置：

- Windows：`%LOCALAPPDATA%\com.learningspace.desktop`
- macOS：`~/Library/Application Support/com.learningspace.desktop`

其中 `pgdata/` 是数据库，`backups/` 是自动备份（保留 7 份），`logs/` 是日志。
卸载默认保留该目录；需要彻底清除时手动删除。

默认端口：应用 `8765`、PostgreSQL `55432`。首次启动会挑选空闲端口并持久化；
之后若端口被占用，应用会给出可操作的错误，而不是静默换端口。

## 开发与构建

### 前置条件（Windows）

| 依赖 | 说明 |
| --- | --- |
| Rust 1.77+ | `rustup default stable`，MSVC 工具链 |
| Visual Studio Build Tools 2022 | 勾选“使用 C++ 的桌面开发”（构建 pgvector 需要） |
| WebView2 Runtime | Windows 10/11 通常自带 |
| Node.js 18+ | 安装 `@tauri-apps/cli` |
| Python 3.12+ 与 `uv` | 项目根目录执行 `uv sync` |

### 构建安装包

```powershell
uv sync
uv pip install -r packaging/requirements.txt
uv run python packaging/build.py          # PG + pgvector + sidecar + 资源汇总
cd desktop
npm install
npm run build                             # 等价于 tauri build --bundles nsis
```

产物：`desktop/src-tauri/target/release/bundle/nsis/*.exe`。
`packaging/build.py` 同时在 `packaging/build/` 输出体积报告。

### 本地开发运行

```powershell
uv run python packaging/build.py --skip-fetch --skip-pgvector   # 复用已构建的 PG 与 sidecar
cd desktop
npm run dev
```

桌面壳从 `desktop/src-tauri/resources/` 读取 PostgreSQL 与 sidecar；修改 Python
代码后需要重新执行 `packaging/build.py --skip-fetch --skip-pgvector --clean-sidecar`。

建议开发时用独立的 `LS_HOME`，避免污染真实数据：

```powershell
$env:LS_HOME = "D:\build-cache\ls-dev-home"
npm run dev
```

### 自检

```powershell
$env:LS_HOME = "D:\build-cache\ls-smoke-home"
& ".\learning-space-desktop.exe" --smoke    # 在安装目录或 target\release 下执行
```

`--smoke` 会完成 initdb → 迁移 → 启动服务 → `/healthz` → 备份 → 停止，
并在 `LS_HOME\smoke-report.json` 写入分步耗时；CI 用它验证安装包。

## 环境变量（调试用）

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `LS_HOME` | 平台用户数据目录 | 覆盖数据目录 |
| `LS_RESOURCE_DIR` | 安装目录 | 覆盖 `pgsql/`、`sidecar/`、`sql/` 所在目录 |
| `LS_PG_BIN` | 内置 `pgsql/bin` | 覆盖 PostgreSQL 工具目录 |
| `LS_PG_PORT` / `LS_PORT` | 55432 / 8765 | 覆盖端口（仅首次生成配置时生效） |
| `LS_CLIPBOARD_MONITOR` | `1` | 设为 `0` 关闭剪贴板监听 |

## 进程与数据安全

- PostgreSQL 只监听 `127.0.0.1`，使用 `scram-sha-256`；密钥保存在 `LS_HOME/.env`。
- 退出时先停 sidecar，再 `pg_ctl stop -m fast`；异常退出后下次启动会按 `run/state.json`
  清理残留 sidecar，并复用仍在运行的数据库。
- 启动失败时窗口会显示日志目录，也可从托盘选择“打开日志目录”。
- 桌面模式是 HTTP（不是 HTTPS），只允许本机访问；局域网共享在后续版本加入，默认关闭。

## 隐私

- 剪贴板监听只比较文本长度是否达到 20 字，内容仅存在于内存，不会写盘或发往网络。
- 仓库内演示账号与数据均为模拟数据；`.env` 只在本地使用，已被 git 忽略。
