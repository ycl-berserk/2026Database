# 学习空间桌面版（Tauri v2）

把学习空间打包成“装好即用”的桌面应用：不依赖本机 Python、PostgreSQL 或 Docker，
每个用户的数据只保存在自己的电脑上。

安装包内置：

- PostgreSQL 16（裁剪版，含 `pg_trgm`、`pgvector`，仅监听 `127.0.0.1`）
- FastAPI 服务的 PyInstaller sidecar（`ls-server`）
- Tauri 桌面壳：托盘、全局快捷键、剪贴板提示、启动进度与错误提示

## 普通用户

1. 运行安装包（Windows 首次会被 SmartScreen 提示，选择“更多信息 → 仍要运行”）；
   macOS 打开 DMG 后把“Learning Space”拖进“应用程序”，首次启动若被 Gatekeeper
   拦截，右键选择“打开”并确认，或执行
   `xattr -dr com.apple.quarantine "/Applications/Learning Space.app"`。
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

## 手机访问（同一 Wi-Fi）

1. 先在电脑上完成首次设置，创建本地账号。
2. 托盘菜单 →“手机访问…”，打开开关（Windows 也可运行 `learning-space-desktop.exe --share`）。
3. 窗口会列出本机所有可用地址并标出推荐项（默认路由、真实无线/有线网卡优先，
   VMware/WSL/Docker/VPN 等标记为虚拟）。地址不对时从下拉框手动切换。
4. 手机扫窗口里的二维码，或手动打开 `http://<电脑IP>:8765/`。
5. 手机与电脑使用同一个账号登录，可以同时操作。

手机打不开时，点窗口里的“检查网络”：

- 诊断会显示本机探测、所选局域网地址探测、Windows 当前网络位置、监听地址和防火墙状态，
  并给出可复制的 PowerShell 命令。
- 手机浏览器打开 `http://<电脑IP>:8765/healthz` 可确认服务是否监听在局域网地址
  （响应里的 `bind_host` 应为 `0.0.0.0`）。
- 常见原因：选错了网卡（如 Wi-Fi Direct / 虚拟机网卡）、网络被识别为“公用”、
  防火墙未放行 `ls-server.exe`、访客网络或 AP 客户端隔离。诊断页会逐条提示。

注意事项：

- 默认关闭。开启后只有应用端口监听 `0.0.0.0`，数据库始终只监听 `127.0.0.1`，
  手机无法直接连接 PostgreSQL。
- 仅限可信 Wi-Fi。桌面版是 HTTP 明文，同一网络内的其他人可能看到传输内容；
  离开家庭或办公室后请关闭。访客网络/隔离网络会导致手机连不上电脑。
- 首次开启时 Windows 会弹出防火墙授权，请允许“专用网络”。若误点拒绝，
  可在“Windows 安全中心 → 防火墙 → 允许应用通过防火墙”中为 `ls-server.exe` 放行。
  诊断页给出对应的 `New-NetFirewallRule` 命令，但应用不会自动修改防火墙或提权。
- 手机浏览器无法把页面安装为 PWA 或离线使用（HTTP 限制），导入、批注、复习等功能不受影响。
- 关闭开关或退出应用后，手机会立即失去访问。

## 开发与构建

### 前置条件

| 依赖 | Windows | macOS（arm64） |
| --- | --- | --- |
| Rust 1.77+ | MSVC 工具链 | Xcode Command Line Tools |
| C/C++ 编译器 | Visual Studio Build Tools 2022（“使用 C++ 的桌面开发”） | Xcode CLT 自带 clang |
| WebView2 | Windows 10/11 通常自带 | 系统自带 WKWebView |
| Node.js 18+ | 安装 `@tauri-apps/cli` | 同左 |
| Python 3.12+ 与 `uv` | 项目根目录执行 `uv sync` | 同左 |

macOS 额外依赖 `curl`、`make`、`bzip2`（Xcode CLT 已包含）；PostgreSQL 在打包时从
固定 SHA-256 的源码包本地编译，不需要 Homebrew。

### 构建安装包

```bash
uv sync
uv pip install -r packaging/requirements.txt
uv run python packaging/build.py          # PG + pgvector + sidecar + 资源汇总
cd desktop
npm install
npm run build                             # Windows: tauri build --bundles nsis
```

macOS 上需要先构建 app 再打 DMG（让 ad-hoc 签名覆盖嵌套二进制）：

```bash
cd desktop
npx tauri build --bundles app --target aarch64-apple-darwin
APP="src-tauri/target/aarch64-apple-darwin/release/bundle/macos/Learning Space.app"
find "$APP" -type f \( -perm -111 -o -name '*.dylib' -o -name '*.so' \) -print0 |
  while IFS= read -r -d '' f; do codesign --force --sign - "$f"; done
codesign --force --sign - "$APP"
hdiutil create -volname "Learning Space" -srcfolder "$APP" -ov -format UDZO Learning\ Space.dmg
```

产物：Windows 为 `desktop/src-tauri/target/release/bundle/nsis/*.exe`，
macOS 为 `desktop/src-tauri/target/aarch64-apple-darwin/release/bundle/macos/Learning Space.app`
与自行生成的 DMG。`packaging/build.py` 同时在 `packaging/build/` 输出体积报告
（安装后资源预算 ≤350 MiB；NSIS ≤120 MB、DMG ≤150 MB）。

CI 也会在 `macos-14` 上重复上述过程并上传 DMG；未签名版本只能通过右键“打开”
绕过 Gatekeeper，正式分发仍需 Apple Developer 证书与公证。

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

```bash
LS_HOME="$TMP/ls-smoke-home" \
  "src-tauri/target/aarch64-apple-darwin/release/bundle/macos/Learning Space.app/Contents/MacOS/learning-space-desktop" \
  --smoke
```

`--smoke` 会完成 initdb → 迁移 → 启动服务 → `/healthz` → 备份 → 停止，
并在 `LS_HOME\smoke-report.json` 写入分步耗时；CI 用它验证安装包。

`--share` 启动时直接打开“手机访问”窗口；若应用已在运行，再次执行会把已有窗口切到该页。

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
- 桌面模式是 HTTP（不是 HTTPS）；局域网共享默认关闭，开启后仅应用端口对外，数据库仍只监听 `127.0.0.1`。

## 隐私

- 剪贴板监听只比较文本长度是否达到 20 字，内容仅存在于内存，不会写盘或发往网络。
- 仓库内演示账号与数据均为模拟数据；`.env` 只在本地使用，已被 git 忽略。
