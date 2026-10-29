# 学习空间桌面壳（Tauri v2）

把本地 FastAPI 服务包成桌面窗口，方便在本机直接使用：

- 启动时自动拉起 `uvicorn app.main:app`，健康检查通过后打开登录页
- 托盘菜单：打开学习空间 / 粘贴导入 / 重启本地服务 / 退出
- 全局快捷键：`Ctrl+Shift+Space` 显示或隐藏窗口，`Ctrl+Shift+V` 打开粘贴导入
- 剪贴板监听：检测到较长的文本时，仅把托盘提示改成“剪贴板有新内容”，不读取、不保存、不上传内容
- 关闭窗口默认收进托盘，退出请用托盘菜单

桌面壳只是“启动器”，页面仍是同一套 FastAPI + Jinja2 站点；数据库、迁移、演示数据与浏览器访问完全一致。

## 前置条件（Windows）

| 依赖 | 说明 |
| --- | --- |
| Rust 1.77+ | `rustup default stable`，需要 MSVC 工具链 |
| Visual Studio Build Tools 2022 | 勾选“使用 C++ 的桌面开发” |
| WebView2 Runtime | Windows 10/11 通常自带 |
| Node.js 18+ | 只用于安装 `@tauri-apps/cli` |
| Python 3.11+ 与 `.venv` | 项目根目录下，已执行 `uv sync` |
| PostgreSQL 16 | `docker compose up -d db`，并完成迁移与演示数据初始化 |

首次准备：

```bash
npm install                 # 在 desktop/ 下
cargo check                 # 在 desktop/src-tauri/ 下，验证 Rust 环境
```

## 运行

在项目根目录先启动数据库：

```bash
docker compose up -d db
uv run python scripts/seed.py        # 首次或重置演示数据时
```

再启动桌面壳：

```bash
cd desktop
npm run dev
```

打包（生成安装包与可执行文件）：

```bash
cd desktop
npm run build
```

产物在 `desktop/src-tauri/target/release/bundle/`。

## 环境变量

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `LS_URL` | `http://127.0.0.1:8000` | 已存在的服务地址；填了就不再自动启动 |
| `LS_AUTOSTART_SERVER` | `1` | 设为 `0` 时只等待外部服务，不自动拉起 uvicorn |
| `LS_PYTHON` | `<项目>/.venv/Scripts/python.exe` | 指定 Python 解释器 |
| `LS_PROJECT_ROOT` | 仓库根目录 | 指定 uvicorn 的工作目录 |
| `LS_CLIPBOARD_MONITOR` | `1` | 设为 `0` 关闭剪贴板监听 |

示例：服务跑到别的端口

```bash
set LS_URL=http://127.0.0.1:8100
npm run dev
```

## 排障

- 窗口一直停在启动页：看 `data/desktop-server.log`，确认数据库、`.env`、依赖和演示数据都正常
- 提示找不到 Python：设置 `LS_PYTHON` 指向实际解释器
- 端口被占用：设置 `LS_URL` 指向已有服务，或换端口后重启
- 快捷键无反应：可能被其他软件占用，托盘菜单仍可操作
- 想完全退出：托盘菜单“退出”；直接关窗口只会隐藏

## 隐私与分发说明

- 剪贴板监听只比较文本长度是否达到 20 字，内容仅存在于内存中的局部变量，不会写盘或发往网络
- 仓库内的演示账号与数据均为模拟数据；`.env` 只在本地使用，已被 git 忽略
- 当前桌面壳依赖本机的 Python 与 PostgreSQL，属于“本地启动器”。若要打包成不依赖环境的安装包，需要把 Python 服务做成 Tauri sidecar，并把数据库改为内嵌或远程方案，这部分尚未实现
